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
# A key can be merged into another (a "subscription_merge" edit): a merchant whose name on the statement
# changed, or a loan that was sold, is then one charge with one history.
#
# A price change is news for FRESH_DAYS after the charge: compute counts those for the status page and
# the weekly email.

PRICE_STEP = 0.03
RAISE_MIN = 0.5
FRESH_DAYS = 45
PER_YEAR = {"weekly": 52, "biweekly": 26, "monthly": 12, "quarterly": 4, "yearly": 1}
SUB_COLUMNS = ["key", "category", "cadence", "charges", "first", "last", "latest", "previous", "yearly",
               "next_expected", "status", "change", "example", "note", "includes", "merged"]
EDITOR_COLUMNS = ["key", "marked", "status", "cadence", "charges", "last", "latest", "yearly", "category", "example",
                  "note", "includes", "merged"]


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


def merges(decisions):
    """{key: the key it is part of} from proposal_decisions: the latest merge answer per key (a
    "subscription_merge" edit, the key it joins in its note; a reject separates it again), a chain followed
    to its end. A key in a cycle stays on its own."""
    if decisions is None or decisions.empty:
        return {}
    d = decisions[(decisions["kind"] == "subscription_merge")
                  & decisions["proposal_key"].astype(str).str.startswith("subscription_merge:")]
    d = d.sort_values("decided_at", kind="stable")
    raw = {}
    for k, v, n in zip(d["proposal_key"], d["decision"], d.get("note", pd.Series([""] * len(d), index=d.index))):
        key, into = str(k)[len("subscription_merge:"):], str(n if n is not None else "").strip().upper()
        if v == "approve" and into and into != key:
            raw[key] = into
        else:
            raw.pop(key, None)
    out = {}
    for key, into in raw.items():
        seen = [key]
        while into in raw and into not in seen:
            seen.append(into)
            into = raw[into]
        if into not in seen:
            out[key] = into
    return out


def members(key, merged):
    """The keys whose charges count as key's: itself and the keys merged into it."""
    return [key] + sorted(k for k, into in merged.items() if into == key)


def group_lines(df, key, merged):
    """The charges in df that count as key's, oldest first: their key is a member or starts with one and a space."""
    keys = members(key, merged)
    return df[df["key"].astype(str).map(lambda k: any(_covers(m, k) for m in keys))].sort_values("date", kind="stable")


def _empty(key, note):
    return {"key": key, "category": "", "cadence": "", "charges": 0, "first": "", "last": "", "latest": None,
            "previous": None, "yearly": None, "next_expected": "", "status": "no charges", "change": "", "example": "",
            "note": note, "includes": "", "merged": ""}


def _row(key, g, today, note, merged):
    """A SUB_COLUMNS row from a group's charges g (oldest first, at least one)."""
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
    others = sorted(set(g["key"].astype(str)) - {key})
    return {"key": key, "category": g["category"].mode().iloc[0], "cadence": name or "irregular",
            "charges": len(g), "first": g["date"].iloc[0], "last": g["date"].iloc[-1],
            "latest": round(latest, 2), "previous": None if previous is None else round(previous, 2),
            "yearly": round(yearly, 2), "next_expected": following, "status": status, "change": change,
            "example": _example(g), "note": note, "includes": ", ".join(others),
            "merged": ", ".join(members(key, merged)[1:])}


def tracked(decisions):
    """{key: note} for each group you track: its own answer is yes, or it has none and a key merged into it
    does. A key merged into another is never a group of its own."""
    marked, merged = marks(decisions), merges(decisions)
    out = {}
    for key in list(marked) + list(merged.values()):
        if key in merged or key in out:
            continue
        own = marked.get(key)
        yes = own[0] if own else any(marked.get(m, (False, ""))[0] for m in members(key, merged))
        if yes:
            out[key] = own[1] if own else ""
    return out


def subscriptions(categorized, decisions, today=None):
    """One row per recurring charge you track (SUB_COLUMNS), newest price change first, then by yearly cost.
    A key merged into another adds its charges to that one's, so a merchant that changed its name on the
    statement (or a lender that sold the loan) is one charge with one history."""
    today = pd.Timestamp(today or date.today())
    merged = merges(decisions)
    df = _charges(categorized)
    rows = []
    for key, note in tracked(decisions).items():
        g = group_lines(df, key, merged)
        rows.append({**_empty(key, note), "merged": ", ".join(members(key, merged)[1:])} if g.empty
                    else _row(key, g, today, note, merged))
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


def editor_rows(subs, found, categorized, decisions, today=None):
    """The Recurring editor's rows (EDITOR_COLUMNS): what you track, then the recurring charges discover found
    that you have not answered either way (ongoing ones before the ones that seem to have stopped), then the
    ones you said not to track. A found key merged into another, or whose charges a tracked one already
    covers, is listed only as part of that one; a merged group is judged on all its charges."""
    today = pd.Timestamp(today or date.today())
    marked, merged = marks(decisions), merges(decisions)
    pick = ["key", "status", "cadence", "charges", "last", "latest", "yearly", "category", "example", "note",
            "includes", "merged"]
    rows = [{**{c: r[c] for c in pick}, "marked": "yes"} for r in subs.to_dict("records")]
    df = _charges(categorized)
    covered = set()
    for r in subs.to_dict("records"):
        covered |= set(group_lines(df, r["key"], merged)["key"].astype(str))
    seen = {r["key"] for r in rows}
    found_rows = []
    for r in found.to_dict("records"):
        key = merged.get(r["key"], r["key"])
        if key in seen or r["key"] in covered:
            continue
        seen.add(key)
        on = marked.get(key)
        g = group_lines(df, key, merged)
        if key != r["key"] or len(members(key, merged)) > 1:  # a merged group: judged on all its charges
            base = _row(key, g, today, on[1] if on else "", merged)
        else:
            base = {"key": key, "status": r["status"], "cadence": r["cadence"], "charges": r["charges"],
                    "last": r["last"], "latest": r["latest"], "yearly": round(r["latest"] * PER_YEAR[r["cadence"]], 2),
                    "category": r["category"], "example": _example(g), "note": on[1] if on else "",
                    "includes": "", "merged": ""}
        found_rows.append({**{c: base[c] for c in pick}, "marked": "no" if on else ""})
    later = {"": 0, "no": 1}
    found_rows.sort(key=lambda r: (later[r["marked"]], r["status"] == "stopped"))
    rows += found_rows
    rows += [{**_empty(k, note), "marked": "no", "status": ""} for k, (on, note) in marked.items()
             if not on and k not in seen and k not in merged]
    out = pd.DataFrame(rows, columns=EDITOR_COLUMNS)
    return out[[c for c in EDITOR_COLUMNS]]


MONTH_COLUMNS = ["month", "key", "category", "group", "expected", "actual"]


def subscription_months(categorized, decisions, categories=None, today=None):
    """Each marked subscription's spending each month from its first charge to this month (MONTH_COLUMNS),
    for the budget view to show it as its own entry under its category. expected is what its cadence says
    was due that month at the latest price before it: the last charge for a monthly one, that times the
    charges a month for a weekly or biweekly one, and for a quarterly or yearly one the last charge only in
    the month its next charge fell due. An irregular one expects nothing."""
    today = pd.Timestamp(today or date.today())
    subs = subscriptions(categorized, decisions, today)
    merged = merges(decisions)
    groups = {} if categories is None or categories.empty else dict(zip(categories["category"], categories["group"]))
    df = _charges(categorized)
    rows = []
    for sub in subs[subs["charges"] > 0].to_dict("records"):
        g = group_lines(df, sub["key"], merged)
        g = g.assign(month=g["date"].astype(str).str[:7], spend=-g["amount"])
        spent = g.groupby("month")["spend"].sum()
        cadence = dict(CADENCES).get(sub["cadence"])
        for month in pd.period_range(g["month"].iloc[0], today.strftime("%Y-%m"), freq="M").astype(str):
            before = g[g["month"] < month]
            expected = None
            if cadence and len(before):
                last = float(before["spend"].iloc[-1])
                if sub["cadence"] == "monthly":
                    expected = last
                elif cadence < 28:
                    expected = last * PER_YEAR[sub["cadence"]] / 12
                else:
                    due = pd.Timestamp(before["date"].iloc[-1]) + pd.Timedelta(days=round(cadence))
                    expected = last if due.strftime("%Y-%m") == month else 0.0
            rows.append({"month": month, "key": sub["key"], "category": sub["category"],
                         "group": groups.get(sub["category"], ""),
                         "expected": None if expected is None else round(expected, 2),
                         "actual": round(float(spent.get(month, 0.0)), 2)})
    return pd.DataFrame(rows, columns=MONTH_COLUMNS)


def monthly_expected(categorized, decisions, through, today=None):
    """{(category, month): (expected, charges)}: what the recurring charges you track were expected to cost in
    each month from their first charge through `through` (YYYY-MM), on a monthly footing, for the budget's
    `recurring` rule. A charge counts at its latest price on or before the month's end times its charges a
    year over twelve (a yearly one is a twelfth of its price every month), and only from its first charge
    until it has missed 1.5 cadences; an irregular one counts the twelve months' spending to the month's end
    over twelve, until it has gone 400 days without a charge. Its category is where its charges mostly go."""
    merged = merges(decisions)
    df = _charges(categorized)
    out = {}
    for key in tracked(decisions):
        g = group_lines(df, key, merged)
        if g.empty:
            continue
        days = pd.to_datetime(g["date"])
        spend = -g["amount"].to_numpy()
        name, cadence = _cadence(days.diff().dt.days.dropna().to_numpy()) if len(g) >= 2 else (None, None)
        category = str(g["category"].mode().iloc[0])
        for month in pd.period_range(days.iloc[0].strftime("%Y-%m"), through, freq="M"):
            start, end = month.start_time, month.end_time
            before = (days <= end).to_numpy()
            last = days[before].iloc[-1]
            if name:
                if (start - last).days > 1.5 * cadence:
                    continue
                cost = float(spend[before][-1]) * PER_YEAR[name] / 12
            else:
                if (start - last).days > 400:
                    continue
                cost = float(spend[before & (days > end - pd.Timedelta(days=365)).to_numpy()].sum()) / 12
            month = month.strftime("%Y-%m")
            total, n = out.get((category, month), (0.0, 0))
            out[(category, month)] = (round(total + cost, 2), n + 1)
    return out
