from conftest import TOKEN, register, token


def test_health(client):
    assert client.get("/health").json == {"status": "ok"}


def test_pages_require_login(client):
    for path in ["/dashboard", "/forecasts/1", "/forecasts/1/forecast.csv"]:
        resp = client.get(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_register_login_logout(client):
    assert register(client, "bob").status_code == 302
    assert client.get("/dashboard").status_code == 200
    client.post("/logout", data={"csrf_token": token(client)})
    assert client.get("/dashboard").status_code == 302
    bad = client.post("/login", data={"username": "bob", "password": "wrong",
                                      "csrf_token": token(client)})
    assert b"Incorrect username or password" in bad.data
    ok = client.post("/login", data={"username": "bob", "password": "password123",
                                     "csrf_token": token(client)})
    assert ok.status_code == 302


def test_register_validation(client):
    short = client.post("/register", data={"username": "carol", "password": "short",
                                           "csrf_token": TOKEN})
    assert b"at least 8 characters" in short.data
    register(client, "dave")
    client.post("/logout", data={"csrf_token": token(client)})
    dup = client.post("/register", data={"username": "DAVE", "password": "password123",
                                         "csrf_token": token(client)})
    assert b"already taken" in dup.data


def test_password_is_hashed(app, logged_in):
    import sqlite3
    row = sqlite3.connect(app.config["DATABASE"]).execute(
        "SELECT password_hash FROM users").fetchone()
    assert "password123" not in row[0]


def test_csrf_required(client):
    assert client.post("/login", data={"username": "x", "password": "y"}).status_code == 400
