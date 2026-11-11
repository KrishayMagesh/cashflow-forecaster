import pandas as pd
import pytest

import cashflow_core as core
from conftest import SAMPLE_BYTES


@pytest.fixture(scope="module")
def tx():
    return core.parse_transactions(SAMPLE_BYTES)[0]


def make_csv(rows, header="Date,Description,Amount"):
    return (header + "\n" + "\n".join(rows)).encode()


def weekly_rows(weeks=12, extra=None):
    rows = []
    for i in range(weeks * 7):
        day = (pd.Timestamp("2026-01-01") + pd.Timedelta(days=i)).date()
        rows.append(f"{day},SQUARE DEPOSIT,500")
        if i % 7 == 0:
            rows.append(f"{day},SUPPLIER INC,-300")
    return rows + (extra or [])


def test_parses_sample_and_categorizes(tx):
    assert len(tx) > 900
    cats = set(tx["category"])
    assert {"Payroll", "Rent", "Card sales", "Taxes", "Supplies & inventory"} <= cats


def test_parses_debit_credit_columns_and_money_formats():
    data = make_csv([f"01/{d:02d}/2026,Deposit,,\"$1,200.00\",{1000 + d}" for d in range(1, 29)]
                    + ["02/01/2026,Rent payment,(2500.00),,", "03/15/2026,Supplies,85.50,,"],
                    header="Posted Date,Memo,Debit,Credit,Balance")
    tx, balance = core.parse_transactions(data)
    assert tx["amount"].iloc[0] == 1200
    assert -2500 in set(tx["amount"]) and -85.5 in set(tx["amount"])
    assert balance == 1028


def test_rejects_bad_files():
    with pytest.raises(core.ForecastError, match="Date column"):
        core.parse_transactions(b"foo,bar\n1,2\n")
    with pytest.raises(core.ForecastError, match="at least 20"):
        core.parse_transactions(make_csv(["2026-01-01,A,10"] * 5))
    with pytest.raises(core.ForecastError, match="8 weeks"):
        core.parse_transactions(make_csv([f"2026-01-{d:02d},A{d},10" for d in range(1, 29)]))


def test_detects_recurring_payments(tx):
    found = {r["payee"]: r for r in core.detect_recurring(tx, tx["date"].max())}
    assert found["GUSTO PAYROLL"]["cadence"] == "Every 2 weeks"
    assert found["ACH RENT MAPLE ST PROPERTIES"]["cadence"] == "Monthly"
    assert found["ACH RENT MAPLE ST PROPERTIES"]["amount"] == -6500
    assert found["RESTAURANT DEPOT SUPPLIES"]["cadence"] == "Weekly"
    assert not any("SQUARE" in p for p in found)          # daily sales are not "recurring"


def test_forecast_shape_and_balance_math(tx):
    f = core.forecast(tx, 15000, {"threshold": 10000})
    assert len(f["weeks"]) == core.HORIZON_WEEKS
    running = 15000
    for w in f["weeks"]:
        running += w["net"]
        assert w["ending_balance"] == pytest.approx(running, abs=0.05)
        assert w["low"] <= w["ending_balance"] <= w["high"]
    assert f["seasonal_used"] is True
    assert any(y["payee"].startswith("IRS") for y in f["yearly_items"])   # Jan 15 tax


def test_scenarios_change_the_forecast_by_the_right_amount(tx):
    base = core.forecast(tx, 15000)
    one_time = core.forecast(tx, 15000, {"one_time_expense": 8000, "one_time_week": 3})
    assert one_time["weeks"][1]["ending_balance"] == base["weeks"][1]["ending_balance"]
    assert one_time["ending_balance"] == pytest.approx(base["ending_balance"] - 8000, abs=0.05)
    hire = core.forecast(tx, 15000, {"new_monthly_cost": 4000, "new_monthly_start_week": 1})
    assert hire["ending_balance"] == pytest.approx(base["ending_balance"] - 12000, abs=0.05)
    slower = core.forecast(tx, 15000, {"revenue_change_pct": -20})
    base_in = sum(w["inflows"] for w in base["weeks"])
    assert sum(w["inflows"] for w in slower["weeks"]) == pytest.approx(base_in * 0.8, rel=1e-6)


def test_threshold_warning(tx):
    f = core.forecast(tx, 2000, {"threshold": 10000})
    assert f["first_below_threshold"] == f["weeks"][0]["week_start"]


def test_backtest_uses_only_past_data(tx):
    result = core.backtest(tx)
    assert result["period_end"] == tx["date"].max().date().isoformat()
    assert result["weekly_mae"] < result["naive_weekly_mae"]
    short = core.parse_transactions(make_csv(weekly_rows(12)))[0]
    assert core.backtest(short) is None


def test_works_without_much_history():
    short = core.parse_transactions(make_csv(weekly_rows(10)))[0]
    f = core.forecast(short, 1000)
    assert f["seasonal_used"] is False
    assert f["weeks"][0]["inflows"] == pytest.approx(3500)
