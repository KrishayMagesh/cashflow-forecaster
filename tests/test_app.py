import io
import sqlite3

from conftest import SAMPLE_BYTES, new_client, register, token


def upload(client, data=SAMPLE_BYTES, name="bank.csv", balance="15000", threshold="10000"):
    return client.post("/forecasts", content_type="multipart/form-data", data={
        "file": (io.BytesIO(data), name), "balance": balance, "threshold": threshold,
        "csrf_token": token(client)})


def test_upload_and_view_forecast(logged_in):
    resp = upload(logged_in)
    assert resp.status_code == 302
    page = logged_in.get(resp.headers["Location"])
    assert page.status_code == 200
    for text in [b"Week by week", b"What the forecast assumes", b"GUSTO PAYROLL",
                 b"How accurate is it?", b"not financial, tax or accounting advice"]:
        assert text in page.data
    for chart in ["balance", "spending"]:
        img = logged_in.get(f"/forecasts/1/{chart}.png")
        assert img.mimetype == "image/png" and img.data.startswith(b"\x89PNG")
    csv = logged_in.get("/forecasts/1/forecast.csv").data.decode()
    assert csv.startswith("week,week_start") and len(csv.strip().splitlines()) == 14


def test_sample_data_button(logged_in):
    resp = logged_in.post("/forecasts/sample", data={"csrf_token": token(logged_in)})
    assert b"Sunrise Bakery" in logged_in.get(resp.headers["Location"]).data


def test_scenario_via_query_string(logged_in):
    upload(logged_in)
    page = logged_in.get("/forecasts/1?revenue_change_pct=-20&one_time_expense=8000"
                         "&one_time_week=3")
    assert b"Showing the what-if scenario" in page.data and b"Clear scenario" in page.data
    assert logged_in.get("/forecasts/1/balance.png?revenue_change_pct=-20").status_code == 200
    base = logged_in.get("/forecasts/1/forecast.csv").data.decode().splitlines()[-1]
    what_if = logged_in.get("/forecasts/1/forecast.csv?one_time_expense=8000").data.decode()
    assert float(what_if.splitlines()[-1].split(",")[5]) == round(float(base.split(",")[5])
                                                                  - 8000, 2)
    # nonsense values are ignored instead of crashing
    assert logged_in.get("/forecasts/1?revenue_change_pct=abc&one_time_week=99").status_code == 200


def test_low_balance_alert(logged_in):
    upload(logged_in, balance="500", threshold="10000")
    assert b"projected to fall below your alert level" in logged_in.get("/forecasts/1").data


def test_balance_required_without_balance_column(logged_in):
    page = logged_in.get(upload(logged_in, balance="").headers["Location"])
    assert b"enter your current cash balance" in page.data


def test_bad_files_rejected(logged_in):
    page = logged_in.get(upload(logged_in, name="notes.txt").headers["Location"])
    assert b"choose a .csv file" in page.data
    page = logged_in.get(upload(logged_in, data=b"a,b\n1,2\n").headers["Location"])
    assert b"needs a Date column" in page.data
    assert b"No forecasts yet" in page.data


def test_users_cannot_see_each_others_forecasts(app, logged_in):
    upload(logged_in)
    other = new_client(app)
    register(other, "mallory")
    for path in ["/forecasts/1", "/forecasts/1/balance.png", "/forecasts/1/forecast.csv"]:
        assert other.get(path).status_code == 404
    assert other.post("/forecasts/1/delete", data={"csrf_token": token(other)}).status_code == 404


def test_delete(app, logged_in):
    upload(logged_in)
    logged_in.post("/forecasts/1/delete", data={"csrf_token": token(logged_in)})
    assert logged_in.get("/forecasts/1").status_code == 404
    count = sqlite3.connect(app.config["DATABASE"]).execute(
        "SELECT COUNT(*) FROM forecasts").fetchone()[0]
    assert count == 0
