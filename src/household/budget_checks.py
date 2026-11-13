"""What looks off in the budget, said where the number is: the views mark the line item Check (budget_checks).

A check is a number that may rest on a wrong input, with the input to look at and the fix one click away.
Each kind says what it rests on:

  price_changed  a tracked charge's latest charge is not the one before it (recurring.py's "price changed").
                 A one-time purchase from the same merchant reads as a new price, and a line that takes its
                 target from its charges follows it. Opens the charge's lines, where one can be marked one-off.
  target_high    a floating budget line (trailing, lookback, seasonal) wants well more this month than a
                 usual month spends: HIGH_RATIO times the median of the twelve months before, and HIGH_MIN
                 more, with a month in its average at least HIGH_MIN over a usual one. The check names that
                 month and its biggest line, and opens that line's page, where it can be marked one-off.

Each check has a fingerprint (`check`): its kind, what it is about, and its evidence (the charge's latest day
and price; the line behind the big month). "Looks right" (a looks_right edit, kept in proposal_decisions as
looks_right:<check>) settles that fingerprint only, so a check comes back when its evidence changes.
"""

import numpy as np
import pandas as pd

from . import budget, categorize, compute, recurring
from . import decisions as decisions_

COLUMNS = ["check", "kind", "about", "category", "label", "detail", "open"]
KIND = "looks_right"  # proposal_decisions kind: a check that was looked at and is right
HIGH_RATIO = 1.5  # a floating target this many times a usual month's spending is worth a look
HIGH_MIN = 25.0  # and at least this much more, so small lines are left alone


def _money(x):
    return f"${x:,.2f}"


def _month_words(month):
    return pd.Timestamp(month + "-01").strftime("%B %Y")


def _day_words(day):
    d = pd.Timestamp(str(day)[:10])
    return f"{d:%b} {d.day}, {d.year}"


def settled(decisions):
    """The checks someone said look right: their fingerprints, from the latest looks_right answer per check."""
    return {k for k, (approved, _) in decisions_.latest(decisions, KIND).items() if approved}


def price_changes(subs, decisions, rules=None):
    """A check per tracked charge whose latest charge changed price, under the line item it moves: the
    category of the budget line it pays, else its own."""
    if subs is None or subs.empty:
        return []
    links, names = recurring.links(decisions), recurring.names(decisions)
    line_category = {}
    if rules is not None and not rules.empty:
        for r in rules.sort_values("effective", kind="stable").to_dict("records"):
            line_category[str(r["line"]).strip()] = str(r.get("category", "") or "").strip()
    out = []
    for r in subs[subs["status"] == "price changed"].to_dict("records"):
        key = str(r["key"])
        name = names.get(key) or key
        latest, previous = float(r["latest"]), float(r["previous"])
        category = line_category.get(links.get(key, ""), "") or str(r["category"])
        out.append(
            {
                "check": f"price_changed|{key}|{str(r['last'])[:10]}|{latest:.2f}",
                "kind": "price_changed",
                "about": key,
                "category": category,
                "label": f"{name} {_money(previous)} → {_money(latest)}",
                "detail": (
                    f"{name}'s latest charge, on {_day_words(r['last'])}, was {_money(latest)}; the one before was "
                    f"{_money(previous)}. A one-time purchase from the same merchant reads as a new price: if that "
                    "charge isn't part of it, mark it so in its charges."
                ),
                "open": f"charge:{key}",
            }
        )
    return out


def high_targets(planned, categorized, month):
    """A check per floating budget line whose target this month is well above a usual month's spending,
    naming the biggest line in the biggest month its average takes in."""
    if planned is None or planned.empty or categorized is None or categorized.empty:
        return []
    now = planned[(planned["month"] == month) & planned["rule"].isin(budget.FLOATING)]
    if now.empty:
        return []
    rows = categorize.actual_rows(categorized[~recurring.one_off(categorized)]).copy()
    rows["amount"] = pd.to_numeric(rows["amount"], errors="coerce").fillna(0.0)
    rows["month"] = rows["date"].astype(str).str[:7]
    spend = {(r["category"], r["month"]): float(r["spend"]) for r in compute.monthly_actuals(rows).to_dict("records")}
    out = []
    for r in now.to_dict("records"):
        category, target = str(r["category"]).strip(), float(r["target"])
        past = [spend.get((category, budget.shift_month(month, n)), 0.0) for n in budget.LOOKBACK_WINDOW]
        usual = float(np.median(past))
        if usual <= 0 or target < HIGH_RATIO * usual or target - usual < HIGH_MIN:
            continue
        window = budget.window_months(r["rule"], month)
        big = max(window, key=lambda m: spend.get((category, m), 0.0))
        if spend.get((category, big), 0.0) - usual < HIGH_MIN:
            continue  # no month in its average stands out, so there is no line to point at
        lines = rows[(rows["category"].astype(str).str.strip() == category) & (rows["month"] == big)]
        if lines.empty:
            continue
        top = lines.sort_values("amount", kind="stable").iloc[0]  # the most spent: amounts out are negative
        payee = str(top.get("payee_text", "") or top.get("payee_raw", "") or "a line").strip()
        out.append(
            {
                "check": f"target_high|{r['line']}|{top['txn_id']}",
                "kind": "target_high",
                "about": str(r["line"]),
                "category": category,
                "label": f"Budget {_money(target)}, usually {_money(usual)}",
                "detail": (
                    f"Its budget this month, {_money(target)}, is well above a usual month ({_money(usual)}, the "
                    f"middle of the last twelve). It averages months that include {_month_words(big)}, which spent "
                    f"{_money(spend.get((category, big), 0.0))}, the most from one line: {payee}, "
                    f"{_money(-float(top['amount']))} on {_day_words(top['date'])}. If that was a one-off, mark it "
                    "so on its page and the budget comes back down."
                ),
                "open": f"txn:{top['txn_id']}",
            }
        )
    return out


def build(subs, planned, categorized, decisions, month, rules=None):
    """Every check in force this month (COLUMNS), less the ones someone said look right."""
    found = price_changes(subs, decisions, rules) + high_targets(planned, categorized, month)
    done = settled(decisions)
    out = pd.DataFrame([c for c in found if c["check"] not in done], columns=COLUMNS)
    return out.sort_values(["category", "kind", "about"], kind="stable").reset_index(drop=True)
