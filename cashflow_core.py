"""Cash flow forecasting for a small business, from a bank transaction export.

Method (kept simple and explainable on purpose):
1. Clean the transactions and sort them into categories with keyword rules.
2. Find recurring payments and deposits (rent, payroll, loans, subscriptions...) by
   looking for the same payee at a steady interval and a steady amount. Tax payments
   (which follow an uneven calendar, like US estimated taxes) are expected on the same
   dates as last year.
3. Forecast the next 13 weeks:
   - recurring items are placed on their expected dates
   - everything else uses the median of the last 12 weeks
   - with at least a year of history, inflows and inventory/supply costs follow last
     year's seasonal pattern
4. Add a rough 80% range, what-if scenarios, and a backtest that checks how the method
   would have done on the most recent 13 weeks.

All numbers are estimates for planning, not financial advice.
"""
import io
import re
from datetime import timedelta

import numpy as np
import pandas as pd

HORIZON_WEEKS = 13
BASELINE_WEEKS = 12
SEASONAL_COST_CATEGORY = "Supplies & inventory"

CATEGORY_RULES = [
    ("Payroll", ["payroll", "gusto", "adp", "paychex", "salary", "wages"]),
    ("Rent", ["rent", "lease", "properties", "property mgmt"]),
    ("Taxes", ["irs", "usataxpymt", "tax", "franchise tax", "edd"]),
    ("Loan payments", ["loan", "sba", "financing"]),
    ("Utilities", ["pg&e", "pge", "electric", "water", "utility", "comcast", "at&t",
                   "verizon", "internet"]),
    ("Software", ["software", "subscription", "quickbooks", "intuit", "adobe",
                  "google workspace", "microsoft"]),
    ("Insurance", ["insurance"]),
    ("Marketing", ["meta ads", "facebook", "google ads", "yelp", "marketing"]),
    ("Supplies & inventory", ["supplies", "supply", "depot", "wholesale", "costco", "sysco",
                              "inventory", "flour", "vendor"]),
    ("Card sales", ["square", "stripe", "shopify", "toast", "clover", "paypal"]),
    ("Customer payments", ["invoice", "catering", "payment from", "customer"]),
]
CADENCES = [  # name, typical gap in days, allowed wobble in days
    ("Weekly", 7, 1), ("Every 2 weeks", 14, 2), ("Monthly", 30.4, 4), ("Quarterly", 91, 10),
]


class ForecastError(ValueError):
    """The uploaded file can't be used for a forecast (message is shown to the user)."""


# ---------- 1. reading and cleaning ----------
def _money(series):
    text = series.astype(str).str.strip()
    negative = text.str.startswith("(") & text.str.endswith(")")
    cleaned = text.str.replace(r"[$,()\s]", "", regex=True).replace({"": None, "nan": None})
    values = pd.to_numeric(cleaned, errors="coerce")
    return values.where(~negative, -values.abs())


def _find(columns, names):
    for name in names:
        if name in columns:
            return columns[name]
    return None


def categorize(description, amount):
    text = description.lower()
    for category, words in CATEGORY_RULES:
        if any(w in text for w in words):
            if category in ("Card sales", "Customer payments") and amount < 0:
                continue
            if category not in ("Card sales", "Customer payments") and amount > 0:
                continue
            return category
    return "Other income" if amount > 0 else "Other expenses"


def parse_transactions(data):
    """Bank CSV bytes -> DataFrame[date, description, amount, category] (+ last balance or None).

    Accepts either one signed 'Amount' column or separate debit/credit (withdrawal/deposit)
    columns. Money like "$1,234.50" and "(85.00)" is understood.
    """
    df = None
    for encoding in ("utf-8-sig", "latin1"):
        try:
            df = pd.read_csv(io.BytesIO(data), encoding=encoding, dtype=str)
            break
        except UnicodeDecodeError:
            continue
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            raise ForecastError("That file could not be read as a CSV.") from exc
    if df is None or df.empty:
        raise ForecastError("That file has no rows.")

    cols = {c.strip().lower(): c for c in df.columns}
    date_col = _find(cols, ["date", "transaction date", "posted date", "posting date"])
    desc_col = _find(cols, ["description", "memo", "name", "payee", "details"])
    amount_col = _find(cols, ["amount", "transaction amount"])
    debit_col = _find(cols, ["debit", "withdrawal", "withdrawals", "money out"])
    credit_col = _find(cols, ["credit", "deposit", "deposits", "money in"])
    no_money = amount_col is None and debit_col is None and credit_col is None
    if date_col is None or desc_col is None or no_money:
        raise ForecastError("The CSV needs a Date column, a Description column, and either an "
                            "Amount column or Debit/Credit columns.")

    out = pd.DataFrame({"date": pd.to_datetime(df[date_col], errors="coerce", format="mixed"),
                        "description": df[desc_col].fillna("").astype(str).str.strip()})
    if amount_col is not None:
        out["amount"] = _money(df[amount_col])
    else:
        debit = _money(df[debit_col]).abs().fillna(0) if debit_col else 0
        credit = _money(df[credit_col]).abs().fillna(0) if credit_col else 0
        out["amount"] = credit - debit
    cat_col = _find(cols, ["category"])
    given = df[cat_col].fillna("").astype(str).str.strip() if cat_col else None

    keep = out["date"].notna() & out["amount"].notna() & (out["amount"] != 0)
    out, df = out[keep], df[keep]
    if given is not None:
        given = given[keep]
    out["category"] = [
        g if (given is not None and g) else categorize(d, a)
        for g, d, a in zip(given if given is not None else [None] * len(out),
                           out["description"], out["amount"])]
    out = out.drop_duplicates().sort_values("date", kind="stable").reset_index(drop=True)

    if len(out) < 20:
        raise ForecastError("Please upload at least 20 transactions.")
    if (out["date"].max() - out["date"].min()).days < 56:
        raise ForecastError("Please upload at least 8 weeks of transactions "
                            "(6 months or more gives better forecasts).")
    balance = None
    bal_col = _find(cols, ["balance", "running balance", "available balance"])
    if bal_col:
        last = _money(df[bal_col]).dropna()
        balance = float(last.iloc[-1]) if len(last) else None
    return out, balance


# ---------- 2. recurring items ----------
def _payee_key(description):
    words = re.sub(r"[^a-z& ]", " ", description.lower()).split()
    return " ".join(words[:4])


def detect_recurring(tx, as_of):
    """Find payees with a steady interval and amount that are still active."""
    found = []
    tx = tx.assign(key=tx["description"].map(_payee_key), sign=np.sign(tx["amount"]))
    for (key, sign), group in tx.groupby(["key", "sign"]):
        if len(group) < 3 or not key:
            continue
        dates = group["date"].sort_values()
        gaps = dates.diff().dt.days.dropna()
        median_gap = float(gaps.median())
        for name, typical, wobble in CADENCES:
            if abs(median_gap - typical) > wobble:
                continue
            if (abs(gaps - typical) <= wobble).mean() < 0.7:
                break
            amounts = group["amount"]
            median_amount = float(amounts.median())
            if amounts.std(ddof=0) / abs(median_amount) > 0.35:
                break
            last = dates.iloc[-1]
            if (as_of - last).days > typical * 1.5:
                break                                  # stopped happening
            found.append({"payee": group["description"].mode().iloc[0], "key": key,
                          "category": group["category"].mode().iloc[0], "cadence": name,
                          "gap_days": typical, "amount": round(median_amount, 2),
                          "last_date": last.date().isoformat(), "count": int(len(group))})
            break
    found.sort(key=lambda r: r["amount"])
    return found


def _future_dates(item, as_of, end):
    last = pd.Timestamp(item["last_date"])
    dates = []
    step = 1
    while True:
        if item["cadence"] == "Monthly":
            nxt = last + pd.DateOffset(months=step)
        elif item["cadence"] == "Quarterly":
            nxt = last + pd.DateOffset(months=3 * step)
        else:
            nxt = last + timedelta(days=item["gap_days"] * step)
        if nxt > end:
            return dates
        if nxt > as_of:
            dates.append(nxt)
        step += 1


# ---------- 3. forecast ----------
def _week_index(dates, as_of):
    """0 = the 7 days ending on as_of, 1 = the 7 days before that, ..."""
    return ((as_of - dates).dt.days // 7).astype(int)


def _weekly_history(tx, as_of, weeks):
    idx = _week_index(tx["date"], as_of)
    tx = tx[(idx >= 0) & (idx < weeks)].assign(week=idx[(idx >= 0) & (idx < weeks)])
    weekly = tx.groupby(["week", tx["amount"] > 0])["amount"].sum().unstack(fill_value=0)
    weekly = weekly.reindex(index=range(weeks), columns=[True, False], fill_value=0)
    inflow, outflow = weekly[True].rename("amount"), weekly[False].rename("amount")
    return inflow, outflow


def _seasonal_factors(other, as_of):
    """Inflow seasonality from last year, if there is a year of history (else all 1.0)."""
    if (as_of - other["date"].min()).days < 364 + 7 * (BASELINE_WEEKS + HORIZON_WEEKS):
        return np.ones(HORIZON_WEEKS), False
    year_ago = as_of - timedelta(days=364)
    inflow, _ = _weekly_history(other, year_ago + timedelta(days=7 * HORIZON_WEEKS),
                                BASELINE_WEEKS + HORIZON_WEEKS)
    # inflow[0] is the week ending 13 weeks after year_ago; reorder oldest -> newest
    series = inflow.iloc[::-1].to_numpy()
    base = series[:BASELINE_WEEKS].mean()
    future = pd.Series(series[BASELINE_WEEKS:]).rolling(3, center=True, min_periods=1).mean()
    if base <= 0:
        return np.ones(HORIZON_WEEKS), False
    return np.clip(future.to_numpy() / base, 0.5, 2.0), True


def forecast(tx, start_balance, scenario=None, as_of=None):
    """Weekly forecast for the next 13 weeks. Returns a dict of plain Python values."""
    scenario = scenario or {}
    as_of = pd.Timestamp(as_of) if as_of is not None else tx["date"].max()
    tx = tx[tx["date"] <= as_of]
    end = as_of + timedelta(days=7 * HORIZON_WEEKS)
    recurring = detect_recurring(tx, as_of)
    recurring_keys = {(r["key"], np.sign(r["amount"])) for r in recurring}
    is_recurring = np.array([(k, np.sign(a)) in recurring_keys
                             for k, a in zip(tx["description"].map(_payee_key), tx["amount"])],
                            dtype=bool)
    # Taxes follow an uneven yearly calendar: expect them on the same dates as last year.
    is_tax = ((tx["category"] == "Taxes") & (tx["amount"] < 0)).to_numpy() & ~is_recurring
    year = timedelta(days=364)
    yearly = tx[is_tax & (tx["date"] > as_of - year).to_numpy()
                & (tx["date"] <= end - year).to_numpy()]
    other = tx[~is_recurring & ~is_tax]

    hist_in, hist_out = _weekly_history(other, as_of, BASELINE_WEEKS)
    base_in, base_out = float(hist_in.median()), float(hist_out.median())
    season, seasonal_used = _seasonal_factors(other, as_of)
    net_hist = hist_in + hist_out
    spread = float(net_hist.std(ddof=1)) if len(net_hist) > 1 else 0.0

    week_starts = [as_of + timedelta(days=1 + 7 * k) for k in range(HORIZON_WEEKS)]
    inflow = base_in * season
    outflow = np.full(HORIZON_WEEKS, base_out)
    scheduled = []
    for item in recurring:
        for d in _future_dates(item, as_of, end):
            k = min((d - as_of).days - 1, 7 * HORIZON_WEEKS - 1) // 7
            amount = item["amount"]
            if item["category"] == SEASONAL_COST_CATEGORY:
                amount *= season[k]          # inventory spending rises and falls with sales
            if amount > 0:
                inflow[k] += amount
            else:
                outflow[k] += amount
            scheduled.append({"date": d.date().isoformat(), "payee": item["payee"],
                              "amount": round(float(amount), 2)})

    for _, row in yearly.iterrows():
        d = row["date"] + year
        k = min((d - as_of).days - 1, 7 * HORIZON_WEEKS - 1) // 7
        outflow[k] += row["amount"]
        scheduled.append({"date": d.date().isoformat(), "payee": row["description"],
                          "amount": round(float(row["amount"]), 2),
                          "note": "same date as last year"})
    yearly_items = [{"payee": r["description"], "amount": round(float(r["amount"]), 2),
                     "last_year": r["date"].date().isoformat()} for _, r in yearly.iterrows()]

    # what-if scenario
    change = float(scenario.get("revenue_change_pct") or 0) / 100
    inflow = inflow * (1 + change)
    one_time = abs(float(scenario.get("one_time_expense") or 0))
    if one_time:
        week = int(scenario.get("one_time_week") or 1)
        outflow[min(max(week, 1), HORIZON_WEEKS) - 1] -= one_time
    monthly = abs(float(scenario.get("new_monthly_cost") or 0))
    if monthly:
        first = int(scenario.get("new_monthly_start_week") or 1)
        start = week_starts[min(max(first, 1), HORIZON_WEEKS) - 1]
        step = 0
        while (d := start + pd.DateOffset(months=step)) <= end:
            outflow[min((d - as_of).days - 1, 7 * HORIZON_WEEKS - 1) // 7] -= monthly
            step += 1

    net = inflow + outflow
    balance = float(start_balance) + np.cumsum(net)
    band = 1.28 * spread * np.sqrt(np.arange(1, HORIZON_WEEKS + 1))
    threshold = float(scenario.get("threshold") or 0)
    weeks = [{"week": k + 1, "week_start": week_starts[k].date().isoformat(),
              "inflows": round(float(inflow[k]), 2), "outflows": round(float(outflow[k]), 2),
              "net": round(float(net[k]), 2), "ending_balance": round(float(balance[k]), 2),
              "low": round(float(balance[k] - band[k]), 2),
              "high": round(float(balance[k] + band[k]), 2)} for k in range(HORIZON_WEEKS)]
    low_week = int(np.argmin(balance))
    below = [w for w in weeks if w["ending_balance"] < threshold]
    return {
        "as_of": as_of.date().isoformat(), "start_balance": round(float(start_balance), 2),
        "threshold": threshold, "weeks": weeks, "recurring": recurring,
        "yearly_items": yearly_items,
        "scheduled": sorted(scheduled, key=lambda s: s["date"]),
        "baseline_weekly_inflow": round(base_in, 2), "baseline_weekly_outflow": round(base_out, 2),
        "seasonal_used": seasonal_used,
        "lowest_balance": round(float(balance[low_week]), 2),
        "lowest_week_start": weeks[low_week]["week_start"],
        "first_below_threshold": below[0]["week_start"] if below else None,
        "ending_balance": round(float(balance[-1]), 2),
        "total_net": round(float(net.sum()), 2),
    }


# ---------- 4. backtest and summaries ----------
def backtest(tx):
    """Forecast the last 13 weeks using only earlier data and compare with what happened.

    Compared against a simple baseline: 'each week nets the same as the average of the
    previous 12 weeks'. Returns None when there isn't enough history.
    """
    as_of = tx["date"].max()
    cutoff = as_of - timedelta(days=7 * HORIZON_WEEKS)
    if (cutoff - tx["date"].min()).days < 7 * 26:
        return None
    predicted = np.array([w["net"] for w in forecast(tx, 0, as_of=cutoff)["weeks"]])
    actual_in, actual_out = _weekly_history(tx, as_of, HORIZON_WEEKS)
    actual = (actual_in + actual_out).iloc[::-1].to_numpy()       # oldest -> newest
    past_in, past_out = _weekly_history(tx[tx["date"] <= cutoff], cutoff, BASELINE_WEEKS)
    naive = np.full(HORIZON_WEEKS, float((past_in + past_out).mean()))
    mae = float(np.abs(predicted - actual).mean())
    naive_mae = float(np.abs(naive - actual).mean())
    return {
        "period_start": (cutoff + timedelta(days=1)).date().isoformat(),
        "period_end": as_of.date().isoformat(),
        "weekly_mae": round(mae, 2), "naive_weekly_mae": round(naive_mae, 2),
        "improvement_pct": round((naive_mae - mae) / naive_mae * 100, 1) if naive_mae else 0.0,
        "total_error": round(float(predicted.sum() - actual.sum()), 2),
        "actual_total": round(float(actual.sum()), 2),
    }


def spending_by_category(tx, days=90):
    recent = tx[(tx["date"] > tx["date"].max() - timedelta(days=days)) & (tx["amount"] < 0)]
    totals = (-recent.groupby("category")["amount"].sum()).sort_values(ascending=False)
    return [{"category": c, "amount": round(float(v), 2)} for c, v in totals.items()]


def history_balance(tx, current_balance, weeks=13):
    """Approximate past week-end balances by working backwards from today's balance."""
    as_of = tx["date"].max()
    points = []
    for k in range(weeks, 0, -1):
        day = as_of - timedelta(days=7 * k)
        later = tx.loc[tx["date"] > day, "amount"].sum()
        points.append({"date": day.date().isoformat(),
                       "balance": round(float(current_balance - later), 2)})
    points.append({"date": as_of.date().isoformat(), "balance": round(float(current_balance), 2)})
    return points
