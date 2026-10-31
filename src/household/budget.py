"""The budget: effective-dated rules turned into a monthly target per line, set against actual spending.

Each budget line (budget_rules, one row per line per change) carries the old
budget's fields: the category it pays for, the account that funds it, a
priority, and a rule:

  fixed         amount per `per` (monthly, biweekly, weekly, semi_monthly,
                quarterly, semi_annually, annually), normalized to a month
  lookback      mean plus LOOKBACK_SIGMA standard deviations of the category's
                spending in the same three calendar months a year earlier
                (for October 2026: September to November 2025), the old
                budget-reports formula; amount per `per` is the fallback while
                there is no year-earlier history
  sinking_fund  amount per `per` (usually annually) set aside each month, with a
                balance: what was set aside since the line began less what the
                category spent
  scheduled     an explicit amount in the month of `effective`; a line can have
                any number of these rows (tuition installments)
  end           the line stops from `effective` on

A line's state in a month is its latest non-scheduled row effective on or
before the month's first day, so a change takes effect from its month and the
history of targets stays reproducible. A missing or malformed amount is a
warning in the report's basis column, never an error.
"""

import numpy as np
import pandas as pd

PER_MONTH = {"monthly": 1.0, "biweekly": 26 / 12, "weekly": 52 / 12, "semi_monthly": 2.0, "quarterly": 1 / 3,
             "semi_annually": 1 / 6, "annually": 1 / 12, "": 1.0}
LOOKBACK_SIGMA = 0.25
TARGET_COLUMNS = ["month", "line", "category", "account", "priority", "rule", "target", "basis"]
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}


def _amount(row):
    try:
        return float(str(row.get("amount", "")).replace(",", "").replace("$", "").strip() or "nan")
    except ValueError:
        return float("nan")


def _months(first, last):
    return [p.strftime("%Y-%m") for p in pd.period_range(first, last, freq="M")]


def _shift(month, n):
    return (pd.Period(month, freq="M") + n).strftime("%Y-%m")


def _per_month(row):
    per = str(row.get("per", "")).strip().lower().replace("-", "_")
    amount = _amount(row)
    if per not in PER_MONTH or np.isnan(amount):
        return None, f"no usable amount/per ({row.get('amount', '')!r} per {row.get('per', '')!r})"
    return amount * PER_MONTH[per], f"{amount:.2f} {per or 'monthly'}"


def _spend_lookup(actuals):
    if actuals is None or actuals.empty:
        return {}
    a = actuals.copy()
    a["spend"] = pd.to_numeric(a["spend"])
    return a.groupby(["category", "month"])["spend"].sum().to_dict()


def targets(rules, actuals, through):
    """One row per line per month, from each line's first effective month through `through` (YYYY-MM)."""
    if rules is None or rules.empty:
        return pd.DataFrame(columns=TARGET_COLUMNS)
    r = rules.copy()
    r["effective"] = r["effective"].astype(str).str[:7]
    r["rule"] = r["rule"].astype(str).str.strip()
    spend = _spend_lookup(actuals)
    out = []
    for line, rows in r.groupby("line"):
        rows = rows.sort_values("effective")
        states = rows[rows["rule"] != "scheduled"]
        scheduled = rows[rows["rule"] == "scheduled"]
        for month in _months(rows["effective"].min(), through):
            current = states[states["effective"] <= month]
            state = current.iloc[-1] if len(current) else None
            active = state is not None and state["rule"] != "end"
            due = scheduled[scheduled["effective"] == month]
            if not active and not len(due):
                continue
            target, basis = 0.0, []
            if len(due):
                amounts = [_amount(x) for x in due.to_dict("records")]
                target += float(sum(a for a in amounts if not np.isnan(a)))
                basis.append("scheduled")
            if active:
                value, why = _rule_target(state, month, spend)
                target += value or 0.0
                basis.append(why)
            src = state if active else due.iloc[-1]
            out.append({"month": month, "line": line, "category": str(src["category"]).strip(),
                        "account": str(src.get("account", "")).strip(),
                        "priority": str(src.get("priority", "")).strip(),
                        "rule": state["rule"] if active else "scheduled",
                        "target": round(target, 2), "basis": "; ".join(basis)})
    return pd.DataFrame(out, columns=TARGET_COLUMNS).sort_values(["month", "category", "line"]).reset_index(drop=True)


def _rule_target(state, month, spend):
    rule = state["rule"]
    if rule in ("fixed", "sinking_fund"):
        value, why = _per_month(state)
        return (None, why) if value is None else (value, f"{rule}: {why}")
    if rule == "lookback":
        category = str(state["category"]).strip()
        window = [_shift(month, n) for n in (-13, -12, -11)]
        history = [spend.get((category, m)) for m in window]
        if all(h is None for h in history):
            value, why = _per_month(state)
            if value is None:
                return None, "lookback: no history a year earlier and no fallback amount"
            return value, f"lookback: no history a year earlier, fallback {why}"
        values = np.array([h or 0.0 for h in history])
        value = values.mean() + LOOKBACK_SIGMA * values.std(ddof=1)
        return value, f"lookback {window[0]} to {window[-1]}: mean {values.mean():.2f} + {LOOKBACK_SIGMA} sd"
    return None, f"unknown rule {rule!r}"


def versus(targets_df, actuals, categories=None):
    """Budget against actuals per month and category: target, actual, remaining, and the highest priority."""
    t = targets_df.copy()
    if t.empty:
        return pd.DataFrame(columns=["month", "category", "group", "priority", "target", "actual", "remaining"])
    t["rank"] = t["priority"].map(lambda p: PRIORITY_ORDER.get(p, 3))
    by = t.groupby(["month", "category"], as_index=False).agg(target=("target", "sum"), rank=("rank", "min"))
    a = actuals.copy()
    a["spend"] = pd.to_numeric(a["spend"])
    a = a.groupby(["month", "category"], as_index=False)["spend"].sum().rename(columns={"spend": "actual"})
    months = set(by["month"])
    a = a[a["month"].isin(months)]
    out = by.merge(a, on=["month", "category"], how="outer").fillna({"target": 0.0, "actual": 0.0, "rank": 3})
    names = {v: k for k, v in PRIORITY_ORDER.items()}
    out["priority"] = out["rank"].astype(int).map(names)
    groups = {} if categories is None or categories.empty else dict(zip(categories["category"], categories["group"]))
    out["group"] = out["category"].map(lambda c: groups.get(c, ""))
    out["target"], out["actual"] = out["target"].round(2), out["actual"].round(2)
    out["remaining"] = (out["target"] - out["actual"]).round(2)
    return (out[["month", "category", "group", "priority", "target", "actual", "remaining"]]
            .sort_values(["month", "category"]).reset_index(drop=True))


def sinking_funds(targets_df, actuals):
    """Each sinking-fund line's running balance: set aside since it began, less what its category spent.

    The category's whole spending counts against the line, so a category should have one sinking fund.
    """
    t = targets_df[targets_df["rule"] == "sinking_fund"]
    if t.empty:
        return pd.DataFrame(columns=["month", "line", "category", "set_aside", "spent", "balance"])
    spend = _spend_lookup(actuals)
    out = []
    for line, g in t.sort_values("month").groupby("line"):
        balance = 0.0
        for row in g.to_dict("records"):
            spent = float(spend.get((row["category"], row["month"]), 0.0))
            balance += row["target"] - spent
            out.append({"month": row["month"], "line": line, "category": row["category"],
                        "set_aside": row["target"], "spent": round(spent, 2), "balance": round(balance, 2)})
    return pd.DataFrame(out)
