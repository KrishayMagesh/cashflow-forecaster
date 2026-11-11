"""Cash Flow Forecaster: upload a bank export, see the next 13 weeks of cash, try what-ifs."""
import csv
import io
import json
from pathlib import Path

import pandas as pd
from flask import (Flask, abort, flash, g, redirect, render_template, request, send_file,
                   url_for)

import cashflow_core as core
import charts
from auth_db import configure, get_db, login_required, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    start_balance REAL NOT NULL,
    threshold REAL NOT NULL,
    n_transactions INTEGER NOT NULL,
    first_date TEXT NOT NULL,
    as_of TEXT NOT NULL,
    transactions_json TEXT NOT NULL
);
"""
SAMPLE = Path(__file__).parent / "sample_data" / "sunrise_bakery_transactions.csv"
SCENARIO_FIELDS = {  # name: (min, max, integer?)
    "revenue_change_pct": (-90, 200, False),
    "one_time_expense": (0, 100_000_000, False),
    "one_time_week": (1, core.HORIZON_WEEKS, True),
    "new_monthly_cost": (0, 100_000_000, False),
    "new_monthly_start_week": (1, core.HORIZON_WEEKS, True),
}


def parse_number(text, low, high, integer=False):
    """Return a number within [low, high], or None if blank/invalid."""
    text = (text or "").strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        value = int(float(text)) if integer else float(text)
    except ValueError:
        return None
    return value if low <= value <= high else None


def read_scenario(args):
    scenario = {}
    for name, (low, high, integer) in SCENARIO_FIELDS.items():
        value = parse_number(args.get(name), low, high, integer)
        if value is not None:
            scenario[name] = value
    active = any(scenario.get(k) for k in ("revenue_change_pct", "one_time_expense",
                                           "new_monthly_cost"))
    return scenario, active


def create_app(test_config=None):
    app = Flask(__name__)
    app.config["APP_NAME"] = "Cash Flow Forecaster"
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    configure(app, test_config, SCHEMA)

    @app.template_filter("money")
    def money(value):
        if value is None:
            return "-"
        return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"

    def owned(fid):
        row = get_db(app).execute("SELECT * FROM forecasts WHERE id = ? AND user_id = ?",
                                  (fid, g.user["id"])).fetchone()
        if row is None:
            abort(404)
        return row

    def load(row):
        tx = pd.DataFrame(json.loads(row["transactions_json"]),
                          columns=["date", "description", "amount", "category"])
        tx["date"] = pd.to_datetime(tx["date"])
        return tx

    def run(row, args):
        tx = load(row)
        scenario, active = read_scenario(args)
        base = core.forecast(tx, row["start_balance"], {"threshold": row["threshold"]})
        what_if = (core.forecast(tx, row["start_balance"],
                                 dict(scenario, threshold=row["threshold"])) if active else None)
        return tx, base, what_if, scenario

    def save(tx, name, balance, threshold):
        records = [[d.date().isoformat(), desc, float(a), cat] for d, desc, a, cat in
                   tx[["date", "description", "amount", "category"]].itertuples(index=False)]
        db = get_db(app)
        cur = db.execute(
            "INSERT INTO forecasts (user_id, name, created_at, start_balance, threshold, "
            "n_transactions, first_date, as_of, transactions_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (g.user["id"], name, now(), balance, threshold, len(tx),
             tx["date"].min().date().isoformat(), tx["date"].max().date().isoformat(),
             json.dumps(records)))
        db.commit()
        return redirect(url_for("forecast", fid=cur.lastrowid))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        rows = get_db(app).execute(
            "SELECT id, name, created_at, start_balance, threshold, n_transactions, first_date, "
            "as_of FROM forecasts WHERE user_id = ? ORDER BY id DESC", (g.user["id"],)).fetchall()
        return render_template("dashboard.html", forecasts=rows)

    @app.post("/forecasts")
    @login_required
    def upload():
        file = request.files.get("file")
        if file is None or not file.filename.lower().endswith(".csv"):
            flash("Please choose a .csv file exported from your bank or accounting software.",
                  "error")
            return redirect(url_for("dashboard"))
        try:
            tx, csv_balance = core.parse_transactions(file.read())
        except core.ForecastError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))
        balance = parse_number(request.form.get("balance"), -1e9, 1e9)
        if balance is None:
            balance = csv_balance
        if balance is None:
            flash("Please enter your current cash balance (your file has no Balance column).",
                  "error")
            return redirect(url_for("dashboard"))
        threshold = parse_number(request.form.get("threshold"), 0, 1e9) or 0.0
        return save(tx, Path(file.filename).name[:120], balance, threshold)

    @app.post("/forecasts/sample")
    @login_required
    def sample():
        tx, _ = core.parse_transactions(SAMPLE.read_bytes())
        return save(tx, "Sample: Sunrise Bakery (fictional)", 25000.0, 15000.0)

    @app.get("/forecasts/<int:fid>")
    @login_required
    def forecast(fid):
        row = owned(fid)
        tx, base, what_if, scenario = run(row, request.args)
        return render_template("forecast.html", f=row, base=base, what_if=what_if,
                               scenario=scenario, backtest=core.backtest(tx),
                               spending=core.spending_by_category(tx),
                               query=request.query_string.decode(),
                               horizon=core.HORIZON_WEEKS)

    @app.get("/forecasts/<int:fid>/balance.png")
    @login_required
    def balance_png(fid):
        row = owned(fid)
        tx, base, what_if, _ = run(row, request.args)
        png = charts.balance_chart(core.history_balance(tx, row["start_balance"]), base, what_if)
        return app.response_class(png, mimetype="image/png")

    @app.get("/forecasts/<int:fid>/spending.png")
    @login_required
    def spending_png(fid):
        png = charts.spending_chart(core.spending_by_category(load(owned(fid))))
        return app.response_class(png, mimetype="image/png")

    @app.get("/forecasts/<int:fid>/forecast.csv")
    @login_required
    def download(fid):
        row = owned(fid)
        _, base, what_if, _ = run(row, request.args)
        result = what_if or base
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=["week", "week_start", "inflows", "outflows",
                                                 "net", "ending_balance", "low", "high"])
        writer.writeheader()
        writer.writerows(result["weeks"])
        return send_file(io.BytesIO(out.getvalue().encode()), as_attachment=True,
                         download_name=f"cash_flow_forecast_{fid}.csv", mimetype="text/csv")

    @app.post("/forecasts/<int:fid>/delete")
    @login_required
    def delete(fid):
        owned(fid)
        db = get_db(app)
        db.execute("DELETE FROM forecasts WHERE id = ? AND user_id = ?", (fid, g.user["id"]))
        db.commit()
        flash("Forecast deleted.", "ok")
        return redirect(url_for("dashboard"))

    @app.errorhandler(413)
    def too_large(_err):
        return "File too large (5 MB maximum).", 413

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5001)
