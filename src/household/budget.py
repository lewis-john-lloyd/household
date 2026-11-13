"""The budget: effective-dated rules turned into a monthly target per line, set against actual spending.

Each budget line (budget_rules, one row per line per change) carries the old
budget's fields: the category it pays for, the account that funds it, a
priority, and a rule:

  fixed         amount per `per` (monthly, biweekly, weekly, semi_monthly,
                quarterly, semi_annually, annually), normalized to a month
  seasonal      mean plus LOOKBACK_SIGMA standard deviations of the category's
                spending in the three calendar months centered on the same
                month a year earlier (for October 2026: September to November
                2025), the old budget-reports formula; amount per `per` is the
                fallback while there is no year-earlier history
  lookback      the same formula over the twelve months before this one (for
                October 2026: October 2025 to September 2026), a year-round
                average; amount per `per` is the fallback while there is no
                history
  trailing      the same formula over the four months before this one (for
                October 2026: June to September 2026), for spending that
                follows recent habits more than the season; amount per `per`
                is the fallback while there is no recent history
  billed        amount per `per` (quarterly, semi_annually, annually) in the months a bill comes, nothing
                between, so a quarterly bill's month, quarter and year all compare with what was paid; the
                months follow the category's own spending (the last month it spent in, and every period
                after), or `effective` until there is any
  sinking_fund  amount per `per` (usually annually) set aside each month, with a
                balance: what was set aside since the line began less what the
                category spent
  scheduled     an explicit amount in the month of `effective`; a line can have
                any number of these rows (tuition installments), and a later
                row on the same date replaces the amount (0 cancels it)
  recurring     the recurring charges linked to the line, else those you track in its category (recurring.py),
                each at its latest price on a monthly footing, plus an allowance
                for the rest of the category's spending: amount per `per`
                (blank for none); a price change moves the target with it
  end           the line stops from the month of `effective` on

A line's state in a month is its latest non-scheduled row whose effective date
falls in that month or before, so a change takes effect for the whole month it
is dated in (an end dated 2026-05-15 leaves May without a target) and the
history of targets stays reproducible. Of two rows in one month, the later in
the sheet is in force, so a correction is a new row, never an edit. A missing or malformed amount is a
warning in the report's basis column, never an error.
"""

import numpy as np
import pandas as pd

PER_MONTH = {
    "monthly": 1.0,
    "biweekly": 26 / 12,
    "weekly": 52 / 12,
    "semi_monthly": 2.0,
    "quarterly": 1 / 3,
    "semi_annually": 1 / 6,
    "annually": 1 / 12,
    "": 1.0,
}
LOOKBACK_SIGMA = 0.25
SEASONAL_WINDOW = (-13, -12, -11)  # months from the budget month: the same month a year earlier, centered
TRAILING_WINDOW = (-4, -3, -2, -1)  # the four months before the budget month
LOOKBACK_WINDOW = tuple(range(-12, 0))  # the twelve months before the budget month
TARGET_COLUMNS = ["month", "line", "category", "account", "priority", "rule", "target", "basis"]
FLOATING = ("trailing", "lookback", "seasonal")  # the rules that average the category's own months
WINDOWS = {"trailing": TRAILING_WINDOW, "lookback": LOOKBACK_WINDOW, "seasonal": SEASONAL_WINDOW}
# a floating target's working: per budget month and line, each month its average takes in
INPUT_COLUMNS = ["month", "line", "category", "rule", "input", "spend", "left_out", "mean", "spread", "sigma", "value"]
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "": 3}


def _amount(row):
    try:
        return float(str(row.get("amount", "")).replace(",", "").replace("$", "").strip() or "nan")
    except ValueError:
        return float("nan")


def _months(first, last):
    return [p.strftime("%Y-%m") for p in pd.period_range(first, last, freq="M")]


def shift_month(month, n):
    """The month n months from month ("2026-01" and -1 is "2025-12")."""
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


def targets(rules, actuals, through, recurring=None, by_line=None, loan=None):
    """One row per line per month, from each line's first effective month through `through` (YYYY-MM).
    recurring: {(category, month): (expected, charges)} from recurring.monthly_expected, for `recurring` lines;
    by_line: {line: {month: (expected, charges)}} from recurring.line_expected, for the lines charges are
    linked to, which count those charges alone; loan: {(category, month): (amount, part)} from loan_parts, for
    `loan` lines, whose target is their category's part of that month's scheduled loan payment."""
    if rules is None or rules.empty:
        return pd.DataFrame(columns=TARGET_COLUMNS)
    r = rules.copy()
    r["day"] = r["effective"].astype(str).str[:10]
    r["effective"] = r["effective"].astype(str).str[:7]
    r["rule"] = r["rule"].astype(str).str.strip()
    r["line"] = r["line"].astype(str).str.strip()  # "Groceries " and "Groceries" are one line
    spend = _spend_lookup(actuals)
    out = []
    for line, rows in r.groupby("line"):
        rows = rows.sort_values("effective", kind="stable")  # within a month, the later row in the sheet wins
        states = rows[rows["rule"] != "scheduled"]
        scheduled = rows[rows["rule"] == "scheduled"].drop_duplicates("day", keep="last")
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
                value, why = _rule_target(state, month, spend, recurring or {}, by_line or {}, loan or {})
                target += value or 0.0
                basis.append(why)
            src = state if active else due.iloc[-1]
            out.append(
                {
                    "month": month,
                    "line": line,
                    "category": str(src["category"]).strip(),
                    "account": str(src.get("account", "")).strip(),
                    "priority": str(src.get("priority", "")).strip(),
                    "rule": state["rule"] if active else "scheduled",
                    "target": round(target, 2),
                    "basis": "; ".join(basis),
                }
            )
    return pd.DataFrame(out, columns=TARGET_COLUMNS).sort_values(["month", "category", "line"]).reset_index(drop=True)


BILL_MONTHS = {"monthly": 1, "quarterly": 3, "semi_annually": 6, "annually": 12}


def _billed(state, month, spend=None):
    """A billed line's target in month: the whole amount in a bill's month, else nothing. The bills' months
    follow the category's own spending: the latest month on or before this one that the category spent in is
    a bill's month, and so is every period after it. With no spending yet, `effective` is the first bill's."""
    per = str(state.get("per", "")).strip()
    amount = _amount(state)
    if np.isnan(amount):
        return None, "billed: no amount"
    if per not in BILL_MONTHS:
        value, why = _per_month(state)
        return value, f"billed: {why} (a {per or 'blank'} bill has no month of its own, so it is spread)"
    category = str(state["category"]).strip()
    paid = sorted(m for (c, m), v in (spend or {}).items() if c == category and m <= month and (v or 0) > 0)
    anchor, seen = (paid[-1], "the last bill") if paid else (str(state.get("effective", ""))[:7], "the line's start")
    gap = (pd.Period(month, freq="M") - pd.Period(anchor, freq="M")).n
    if gap % BILL_MONTHS[per] == 0:
        return amount, f"billed: {amount:.2f} {per}, due this month ({seen} {anchor})"
    return 0.0, f"billed: {amount:.2f} {per}, not due this month ({seen} {anchor})"


def loan_parts(sched, splits):
    """{(category, month): (amount, part)}: each month's scheduled interest and principal (extra principal
    with it), under the categories a loan's loan_split setting names for them (mortgage.split_settings)."""
    out = {}
    if sched is None or sched.empty or not splits:
        return out
    for row in sched.to_dict("records"):
        for category, part in splits.get(str(row["loan_id"]), []):
            if part == "interest":
                amount = float(row["interest"])
            elif part == "principal":
                amount = float(row["principal"]) + float(row["extra_principal"] or 0)
            else:
                continue
            had, _ = out.get((category, row["month"]), (0.0, part))
            out[(category, row["month"])] = (round(had + amount, 2), part)
    return out


def _rule_target(state, month, spend, recurring=None, by_line=None, loan=None):
    rule = state["rule"]
    if rule == "loan":
        amount, part = (loan or {}).get((str(state["category"]).strip(), month), (None, ""))
        if amount is None:
            return 0.0, "loan: no scheduled part for this category (set loan_split for the loan)"
        return amount, f"loan: scheduled {part} {amount:.2f}"
    if rule == "billed":
        return _billed(state, month, spend)
    if rule == "recurring":
        line = str(state.get("line", "")).strip()
        if line in (by_line or {}):
            expected, n = by_line[line].get(month, (0.0, 0))
            whose = "linked "
        else:
            expected, n = (recurring or {}).get((str(state["category"]).strip(), month), (0.0, 0))
            whose = ""
        allowance, _ = _per_month(state) if str(state.get("amount", "")).strip() else (0.0, "")
        allowance = allowance or 0.0
        return expected + allowance, (
            f"recurring: {n} {whose}charge{'' if n == 1 else 's'} {expected:.2f} + allowance {allowance:.2f}"
        )
    if rule in ("fixed", "sinking_fund"):
        value, why = _per_month(state)
        return (None, why) if value is None else (value, f"{rule}: {why}")
    if rule in FLOATING:
        return _from_history(state, month, spend, rule, window_months(rule, month))
    return None, f"unknown rule {rule!r}"


def window_months(rule, month):
    """The months a floating rule's target for `month` averages, oldest first."""
    return [shift_month(month, n) for n in WINDOWS[rule]]


def history(category, window, spend):
    """A floating target's working over the window's months: (values, mean, spread, value), each month's
    spending (a month with no spending counts as zero) and mean plus LOOKBACK_SIGMA standard deviations;
    None while none of the months has any spending. The one place the formula lives (targets, target_inputs)."""
    seen = [spend.get((category, m)) for m in window]
    if all(h is None for h in seen):
        return None
    values = np.array([h or 0.0 for h in seen])
    spread = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    return values, float(values.mean()), spread, float(values.mean()) + LOOKBACK_SIGMA * spread


def _from_history(state, month, spend, rule, window):
    """The working's value, or the line's amount while none of the window's months has any spending."""
    category = str(state["category"]).strip()
    worked = history(category, window, spend)
    if worked is None:
        value, why = _per_month(state)
        if value is None:
            return None, f"{rule}: no history for {window[0]} to {window[-1]} and no fallback amount"
        return value, f"{rule}: no history for {window[0]} to {window[-1]}, fallback {why}"
    _, mean, _, value = worked
    return value, f"{rule} {window[0]} to {window[-1]}: mean {mean:.2f} + {LOOKBACK_SIGMA} sd"


def target_inputs(planned, usual, actuals, since):
    """How each floating target from `since` (YYYY-MM) on was worked out, for the views to show its work: per
    budget month and line, a row for each month its average takes in, with what the category spent there
    (`usual`, the actuals the targets learn from), what lines marked one-off left out (`actuals` less `usual`),
    and the working's mean, spread, sigma and value. A target that fell back to the line's amount has none."""
    if planned is None or planned.empty:
        return pd.DataFrame(columns=INPUT_COLUMNS)
    spend, every = _spend_lookup(usual), _spend_lookup(actuals)
    out = []
    rows = planned[planned["rule"].isin(FLOATING) & (planned["month"] >= since)]
    for r in rows.to_dict("records"):
        category = str(r["category"]).strip()
        window = window_months(r["rule"], r["month"])
        worked = history(category, window, spend)
        if worked is None:
            continue
        values, mean, spread, value = worked
        for m, v in zip(window, values):
            out.append(
                {
                    "month": r["month"],
                    "line": r["line"],
                    "category": category,
                    "rule": r["rule"],
                    "input": m,
                    "spend": round(float(v), 2),
                    "left_out": round(float(every.get((category, m), v)) - float(v), 2),  # none known: none
                    "mean": round(mean, 2),
                    "spread": round(spread, 2),
                    "sigma": LOOKBACK_SIGMA,
                    "value": round(value, 2),
                }
            )
    return pd.DataFrame(out, columns=INPUT_COLUMNS)


def _not_spending(categories):
    """Categories that are not spending: transfers, uncategorized, and the income group (pay, refunds...)."""
    out = {"", "transfer"}
    if categories is not None and not categories.empty:
        out |= set(categories.loc[categories["group"].astype(str).str.strip() == "income", "category"].astype(str))
    return out


def versus(targets_df, actuals, categories=None):
    """Budget against actuals per month and category: target, actual, remaining, and the highest priority.

    Transfers and income are not spending, so they are left out, and so is money coming in to a category
    with no budget (a savings category's shares sold, say).
    """
    t = targets_df.copy()
    if t.empty:
        return pd.DataFrame(columns=["month", "category", "group", "priority", "target", "actual", "remaining"])
    t["rank"] = t["priority"].map(lambda p: PRIORITY_ORDER.get(p, 3))
    by = t.groupby(["month", "category"], as_index=False).agg(target=("target", "sum"), rank=("rank", "min"))
    a = actuals.copy()
    a["spend"] = pd.to_numeric(a["spend"])
    a = a[~a["category"].astype(str).str.strip().isin(_not_spending(categories))]
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
    out = out[~((out["target"] == 0) & (out["actual"] < 0))]  # money in to an unbudgeted category (shares sold)
    return (
        out[["month", "category", "group", "priority", "target", "actual", "remaining"]]
        .sort_values(["month", "category"])
        .reset_index(drop=True)
    )


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
            out.append(
                {
                    "month": row["month"],
                    "line": line,
                    "category": row["category"],
                    "set_aside": row["target"],
                    "spent": round(spent, 2),
                    "balance": round(balance, 2),
                }
            )
    return pd.DataFrame(out)
