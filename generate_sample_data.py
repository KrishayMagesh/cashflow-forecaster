"""Create a realistic, made-up bank export for a fictional bakery.

"Sunrise Bakery" is not a real business. The file lets anyone try the forecaster without
uploading private financial data.  Run:  python generate_sample_data.py
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd

START, END = date(2025, 5, 1), date(2026, 10, 31)
rng = np.random.default_rng(7)
rows = []


def add(day, description, amount):
    rows.append({"Date": day.isoformat(), "Description": description,
                 "Amount": round(float(amount), 2)})


def season(day):
    """Bakery sales: busy in December, slow in January and February."""
    return {12: 1.35, 1: 0.82, 2: 0.86, 6: 1.05, 7: 1.05, 8: 1.03, 11: 1.12}.get(day.month, 1.0)


day = START
first_friday = START + timedelta(days=(4 - START.weekday()) % 7)
while day <= END:
    weekend = day.weekday() >= 5
    base = 2150 if weekend else 1380
    add(day, f"SQUARE INC DEPOSIT {rng.integers(1000, 9999)}",
        base * season(day) * rng.normal(1, 0.12))
    if day.day == 1:
        add(day, "ACH RENT MAPLE ST PROPERTIES", -6500)
        add(day, "INTUIT QUICKBOOKS ONLINE", -90)
        add(day, "NEXT INSURANCE PREMIUM", -318.40)
    if day.day == 5:
        add(day, "COMCAST BUSINESS INTERNET", -179.99)
    if day.day == 15:
        add(day, "SBA LOAN PAYMENT", -1450)
    if day.day == 20:
        winter = day.month in (12, 1, 2)
        add(day, "PG&E UTILITY PAYMENT", -(1050 if winter else 820) * rng.normal(1, 0.08))
    if day.day == 25:
        add(day, "META ADS", -rng.uniform(250, 600))
    if day.weekday() == 0:
        add(day, "RESTAURANT DEPOT SUPPLIES", -1800 * season(day) * rng.normal(1, 0.15))
    if (day - first_friday).days % 14 == 0 and day >= first_friday:
        add(day, "GUSTO PAYROLL", -10400 * rng.normal(1, 0.03))
        add(day, "GUSTO TAX", -2100 * rng.normal(1, 0.03))
    if (day - first_friday).days % 14 == 7 and day >= first_friday:
        add(day, "GOLDEN MILL FLOUR CO", -1100 * rng.normal(1, 0.06))
    if (day.month, day.day) in [(1, 15), (4, 15), (6, 15), (9, 15)]:
        add(day, "IRS USATAXPYMT EST TAX", -4500)
    if rng.random() < 0.09:
        client = rng.choice(["NORTHSIDE OFFICE", "LAKE SCHOOL PTA", "RIVERA WEDDING"])
        add(day, f"CATERING PAYMENT {client}", rng.uniform(600, 2400))
    if rng.random() < 0.006:
        add(day, "ABC OVEN REPAIR", -rng.uniform(700, 1800))
    day += timedelta(days=1)

df = pd.DataFrame(rows).sort_values("Date", kind="stable")
out = "sample_data/sunrise_bakery_transactions.csv"
df.to_csv(out, index=False)
print(f"Wrote {len(df)} transactions to {out}")
