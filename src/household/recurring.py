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
    if df.empty:  # no transactions yet (a fresh ledger): nothing recurs
        return pd.DataFrame(columns=COLUMNS)
    df["amount"] = pd.to_numeric(df["amount"])
    df = df[(df["amount"] < 0) & (df["key"] != "") & ~df["key"].astype(str).str.startswith("CHECK")]
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


# --- subscriptions you marked ---------------------------------------------------------------------
# The Subscriptions editor marks a merchant key (a payee answer's key: APPLE ONE, ACME GROCER) yes or no
# (a "subscription" edit, kept in proposal_decisions). A marked key covers the lines whose key is it or
# starts with it and a space. Each subscription gets its cadence (or "irregular"), latest and previous
# charge, a yearly cost (the latest charge times the charges a year, or the last 365 days' charges when
# irregular), the next charge expected, and a status:
#
#   price changed  the latest charge differs from the one before by more than PRICE_STEP (and RAISE_MIN)
#   stopped        not charged for 1.5 cadences (400 days when irregular)
#   active         otherwise
#
# A price change is news for FRESH_DAYS after the charge: compute counts those for the status page and
# the weekly email.

PRICE_STEP = 0.03
RAISE_MIN = 0.5
FRESH_DAYS = 45
PER_YEAR = {"weekly": 52, "biweekly": 26, "monthly": 12, "quarterly": 4, "yearly": 1}
SUB_COLUMNS = ["key", "category", "cadence", "charges", "first", "last", "latest", "previous", "yearly",
               "next_expected", "status", "change", "example", "note"]
EDITOR_COLUMNS = ["key", "marked", "cadence", "charges", "last", "latest", "yearly", "category", "example", "note"]


def marks(decisions):
    """{key: (True or False, note)} from proposal_decisions: the latest subscription answer per key."""
    if decisions is None or decisions.empty:
        return {}
    d = decisions[(decisions["kind"] == "subscription")
                  & decisions["proposal_key"].astype(str).str.startswith("subscription:")]
    d = d.sort_values("decided_at", kind="stable")
    return {str(k)[len("subscription:"):]: (v == "approve", str(n or "")) for k, v, n in
            zip(d["proposal_key"], d["decision"], d.get("note", pd.Series([""] * len(d), index=d.index)))}


def _charges(categorized):
    df = categorized[categorized["era"] != "legacy"].copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    return df[(df["amount"] < 0) & (df["key"].astype(str) != "")]


def _covers(key, line_key):
    return line_key == key or line_key.startswith(key + " ")


def _example(g):
    text = g["payee_text"] if "payee_text" in g else g["payee_raw"]
    return str(text.astype(str).value_counts().index[0]) if len(g) else ""


def subscriptions(categorized, decisions, today=None):
    """One row per marked subscription (SUB_COLUMNS), newest price change first, then by yearly cost."""
    today = pd.Timestamp(today or date.today())
    yes = {k: note for k, (on, note) in marks(decisions).items() if on}
    df = _charges(categorized)
    rows = []
    for key, note in yes.items():
        g = df[df["key"].astype(str).map(lambda k: _covers(key, k))].sort_values("date", kind="stable")
        if g.empty:
            rows.append({"key": key, "category": "", "cadence": "", "charges": 0, "first": "", "last": "",
                         "latest": None, "previous": None, "yearly": None, "next_expected": "", "status": "no charges",
                         "change": "", "example": "", "note": note})
            continue
        days = pd.to_datetime(g["date"])
        spend = -g["amount"].to_numpy()
        name, cadence = _cadence(days.diff().dt.days.dropna().to_numpy()) if len(g) >= 2 else (None, None)
        last, latest = days.iloc[-1], float(spend[-1])
        previous = float(spend[-2]) if len(spend) >= 2 else None
        if name:
            yearly = latest * PER_YEAR[name]
            following = (last + pd.Timedelta(days=round(cadence))).date().isoformat()
        else:
            yearly = float(spend[(days > today - pd.Timedelta(days=365)).to_numpy()].sum())
            following = ""
        changed = previous is not None and abs(latest - previous) > max(PRICE_STEP * previous, RAISE_MIN)
        if (today - last).days > (1.5 * cadence if cadence else 400):
            status = "stopped"
        elif changed:
            status = "price changed"
        else:
            status = "active"
        change = (f"{latest:,.2f}, was {previous:,.2f} ({latest / previous - 1:+.0%}) from {g['date'].iloc[-1]}"
                  if changed else "")
        rows.append({"key": key, "category": g["category"].mode().iloc[0], "cadence": name or "irregular",
                     "charges": len(g), "first": g["date"].iloc[0], "last": g["date"].iloc[-1],
                     "latest": round(latest, 2), "previous": None if previous is None else round(previous, 2),
                     "yearly": round(yearly, 2), "next_expected": following, "status": status, "change": change,
                     "example": _example(g), "note": note})
    out = pd.DataFrame(rows, columns=SUB_COLUMNS)
    order = {"price changed": 0, "active": 1, "no charges": 2, "stopped": 3}
    out["_o"] = out["status"].map(order)
    out["_y"] = -pd.to_numeric(out["yearly"], errors="coerce").fillna(0)
    return out.sort_values(["_o", "_y", "key"], kind="stable").drop(columns=["_o", "_y"]).reset_index(drop=True)


def fresh_changes(subs, today=None):
    """The subscriptions whose price changed at a charge in the last FRESH_DAYS."""
    today = pd.Timestamp(today or date.today())
    if subs.empty:
        return subs
    recent = pd.to_datetime(subs["last"], errors="coerce") >= today - pd.Timedelta(days=FRESH_DAYS)
    return subs[(subs["status"] == "price changed") & recent]


def editor_rows(subs, found, categorized, decisions):
    """The Subscriptions editor's rows (EDITOR_COLUMNS): your subscriptions, the recurring charges discover
    found that you have not marked either way, and the ones you said are not subscriptions."""
    marked = marks(decisions)
    rows = [{"key": r["key"], "marked": "yes", "cadence": r["cadence"], "charges": r["charges"], "last": r["last"],
             "latest": r["latest"], "yearly": r["yearly"], "category": r["category"], "example": r["example"],
             "note": r["note"]} for r in subs.to_dict("records")]
    df = _charges(categorized) if len(found) else None
    for r in found.to_dict("records"):
        on = marked.get(r["key"])
        if on and on[0]:
            continue
        g = df[df["key"] == r["key"]]
        rows.append({"key": r["key"], "marked": "no" if on else "", "cadence": r["cadence"], "charges": r["charges"],
                     "last": r["last"], "latest": r["latest"], "yearly": round(r["latest"] * PER_YEAR[r["cadence"]], 2),
                     "category": r["category"], "example": _example(g), "note": on[1] if on else ""})
    seen = {r["key"] for r in rows}
    rows += [{"key": k, "marked": "no", "cadence": "", "charges": "", "last": "", "latest": None, "yearly": None,
              "category": "", "example": "", "note": note} for k, (on, note) in marked.items() if not on and k not in seen]
    return pd.DataFrame(rows, columns=EDITOR_COLUMNS)
