"""Subscription discovery: charges that come back at a regular interval for a similar amount.

A payee key with at least MIN_CHARGES outgoing charges is recurring when most of
the gaps between charges fall in one cadence (weekly to yearly) and most
amounts sit near the typical one. Each gets a status:

  active         charged within 1.5 cadences of today
  stopped        not charged for longer than that
  price changed  the latest charge differs from the typical amount by more
                 than PRICE_CHANGE (and at least a dollar)

Transfers, paper checks and the old books are left out. This is a report; a
recurring charge with no budget line becomes a proposal once budgets exist.
"""

from datetime import date

import numpy as np
import pandas as pd

MIN_CHARGES = 3
CADENCES = (("weekly", 7), ("biweekly", 14), ("monthly", 30.4), ("quarterly", 91.3), ("yearly", 365.25))
GAP_TOLERANCE = 0.2  # a gap within 20% of the cadence counts as on time
AMOUNT_TOLERANCE = 0.15
SHARE = 0.7  # of gaps on time, and of amounts near typical
PRICE_CHANGE = 0.05
COLUMNS = ["key", "category", "cadence", "typical", "latest", "charges", "first", "last", "next_expected", "status"]


def _cadence(gaps):
    for name, days in CADENCES:
        on_time = np.abs(gaps - days) <= GAP_TOLERANCE * days
        if on_time.mean() >= SHARE:
            return name, days
    return None, None


def discover(categorized, today=None):
    today = pd.Timestamp(today or date.today())
    df = categorized[(categorized["era"] != "legacy") & (categorized["category"] != "transfer")].copy()
    df["amount"] = pd.to_numeric(df["amount"])
    df = df[(df["amount"] < 0) & (df["key"] != "") & ~df["key"].str.startswith("CHECK")]
    rows = []
    for key, g in df.groupby("key"):
        if len(g) < MIN_CHARGES:
            continue
        g = g.sort_values("date")
        days = pd.to_datetime(g["date"])
        name, cadence = _cadence(days.diff().dt.days.dropna().to_numpy())
        if name is None:
            continue
        spend = -g["amount"].to_numpy()
        typical = float(np.median(spend))
        if (np.abs(spend - typical) <= AMOUNT_TOLERANCE * typical).mean() < SHARE:
            continue
        last, latest = days.iloc[-1], float(spend[-1])
        earlier = float(np.median(spend[:-1]))
        if (today - last).days > 1.5 * cadence:
            status = "stopped"
        elif abs(latest - earlier) > max(PRICE_CHANGE * earlier, 1.0):
            status = "price changed"
        else:
            status = "active"
        rows.append({"key": key, "category": g["category"].mode().iloc[0], "cadence": name,
                     "typical": round(typical, 2), "latest": round(latest, 2), "charges": len(g),
                     "first": g["date"].iloc[0], "last": g["date"].iloc[-1],
                     "next_expected": (last + pd.Timedelta(days=round(cadence))).date().isoformat(),
                     "status": status})
    out = pd.DataFrame(rows, columns=COLUMNS)
    order = {"price changed": 0, "active": 1, "stopped": 2}
    return out.sort_values(["status", "typical"], key=lambda c: c.map(order) if c.name == "status" else -c,
                           ).reset_index(drop=True)
