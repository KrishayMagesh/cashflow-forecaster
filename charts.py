"""Charts for the forecast page (PNG bytes)."""
import io

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

DOLLARS = FuncFormatter(lambda v, _: f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}")


def _png(fig):
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    return buf.getvalue()


def balance_chart(history, base, scenario=None):
    """Past balances, the 13-week forecast with its rough range, and an optional scenario."""
    fig, ax = plt.subplots(figsize=(10, 4.8))
    hist = pd.DataFrame(history)
    hist["date"] = pd.to_datetime(hist["date"])
    ax.plot(hist["date"], hist["balance"], color="#555555", linewidth=2, label="Past balance")

    weeks = pd.DataFrame(base["weeks"])
    ends = pd.to_datetime(weeks["week_start"]) + pd.Timedelta(days=6)
    start = pd.Timestamp(base["as_of"])
    x = [start] + list(ends)
    ax.plot(x, [base["start_balance"]] + list(weeks["ending_balance"]), color="#2a6fdb",
            linewidth=2.5, label="Forecast")
    ax.fill_between(ends, weeks["low"], weeks["high"], color="#2a6fdb", alpha=0.15,
                    label="Rough 80% range")
    if scenario is not None:
        sw = pd.DataFrame(scenario["weeks"])
        ax.plot(x, [scenario["start_balance"]] + list(sw["ending_balance"]), color="#e8743b",
                linewidth=2.5, linestyle="--", label="What-if scenario")
    if base["threshold"]:
        ax.axhline(base["threshold"], color="#c0392b", linewidth=1.2, linestyle=":",
                   label=f"Alert level (${base['threshold']:,.0f})")
    ax.axhline(0, color="black", linewidth=0.8)
    ax.axvline(start, color="#999999", linewidth=1, linestyle="--")
    ax.set_title("Cash balance: past 13 weeks and next 13 weeks", fontsize=13, fontweight="bold")
    ax.yaxis.set_major_formatter(DOLLARS)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9, loc="upper left")
    return _png(fig)


def spending_chart(spending):
    fig, ax = plt.subplots(figsize=(8, 0.45 * max(len(spending), 3) + 1))
    names = [s["category"] for s in spending][::-1]
    values = [s["amount"] for s in spending][::-1]
    ax.barh(names, values, color="#2a6fdb")
    ax.xaxis.set_major_formatter(DOLLARS)
    ax.set_title("Spending by category, last 90 days", fontsize=13, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3)
    return _png(fig)
