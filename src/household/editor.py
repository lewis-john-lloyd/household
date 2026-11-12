"""What the views' editors need, as Reports tables compute rebuilds every run.

The views read only Reports, so compute publishes the settings in force (the last row per budget line,
payee rule, category, alias and household parameter), the review queue, and for each budget line what it
means now: the rule in words, this month's target and the last twelve months' spending. An edit made
in the views sends back the fields of the row it replaces (a change set's `was`, parsers/changes.py),
so these tables carry each row's fields exactly as the ledger holds them.
"""

import pandas as pd

from . import budget, compute, mortgage, schema, settings

PER_WORDS = {
    "monthly": "a month",
    "biweekly": "every two weeks",
    "weekly": "a week",
    "semi_monthly": "twice a month",
    "quarterly": "a quarter",
    "semi_annually": "every six months",
    "annually": "a year",
    "": "a month",
}
HISTORY_WORDS = {
    "seasonal": "the same three months a year earlier",
    "lookback": "the last twelve months",
    "trailing": "the last four months",
}


def _columns(name):
    return [c for c in schema.table(schema.REPORTS, name).columns if c != "run_id"]


def _money(text):
    try:
        return f"{float(str(text).replace(',', '').replace('$', '')):,.2f}"
    except ValueError:
        return str(text)


def in_words(row):
    """A budget line's rule as a sentence: "600.00 a month", "what the last twelve months spent ..."."""
    rule, amount, per = str(row["rule"]), str(row.get("amount", "")).strip(), str(row.get("per", "")).strip()
    each = f"{_money(amount)} {PER_WORDS.get(per, per)}" if amount else ""
    if rule == "fixed":
        return each or "fixed, but no amount"
    if rule == "billed":
        return (
            f"a bill of {_money(amount)} {PER_WORDS.get(per, per)}, in the months it comes"
            if amount
            else "billed, but no amount"
        )
    if rule == "sinking_fund":
        return f"set aside {each}, spent from a running balance" if each else "sinking fund, but no amount"
    if rule in HISTORY_WORDS:
        words = f"what {HISTORY_WORDS[rule]} spent, plus {budget.LOOKBACK_SIGMA:g} standard deviations"
        return words + (f"; {each} until there is history" if each else "")
    if rule == "recurring":
        return "its recurring charges" + (f", plus {each}" if each else "")
    if rule == "loan":
        return "its part of the loan payment, from the amortization schedule"
    if rule == "end":
        return f"ended {row['effective']}"
    return rule


def budget_lines(rules, targets, actuals, month):
    """One row per budget line: its row in force (or its end), in words, this month's target, the
    last twelve months' spending in its category, and its scheduled amounts from this month on."""
    columns = _columns("editor_budget_lines")
    if rules is None or rules.empty:
        return pd.DataFrame(columns=columns)
    r = rules.copy()
    r["line"] = r["line"].astype(str).str.strip()
    r["rule"] = r["rule"].astype(str).str.strip()
    r["month"] = r["effective"].astype(str).str[:7]
    spend = budget._spend_lookup(actuals)
    window = [budget.shift_month(month, n) for n in budget.LOOKBACK_WINDOW]
    now = targets[targets["month"] == month].set_index("line")["target"] if len(targets) else pd.Series(dtype=float)
    out = []
    for line, rows in r.sort_values("month", kind="stable").groupby("line", sort=True):
        states = rows[rows["rule"] != "scheduled"]
        ahead = rows[(rows["rule"] == "scheduled") & (rows["month"] >= month)]
        row = (states if len(states) else rows).iloc[-1].to_dict()
        category = str(row.get("category", "")).strip()
        if row["rule"] == "end" and len(states) > 1:
            category = str(states.iloc[-2]["category"]).strip()  # what it paid for before it ended
        out.append(
            {c: row.get(c, "") for c in schema.table(schema.LEDGER, "budget_rules").columns}
            | {
                "in_force": "scheduled only" if not len(states) else ("ended" if row["rule"] == "end" else "yes"),
                "in_words": in_words(row) if len(states) else "scheduled amounts only",
                "this_month": round(float(now.get(line, 0.0)), 2),
                "last_12_months": round(sum(float(spend.get((category, m), 0.0)) for m in window), 2),
                "scheduled": "; ".join(f"{d} {_money(a)}" for d, a in zip(ahead["effective"], ahead["amount"])),
                "spends_from": category,
            }
        )
    return pd.DataFrame(out, columns=columns)


def scheduled(rules):
    """Each scheduled amount in force: the last row per line and date, as the budget reads them (budget.py),
    with its fields exactly as the ledger holds them, since a new amount on that date replaces it and sends
    this row back as its `was`."""
    columns = _columns("editor_scheduled")
    if rules is None or rules.empty:
        return pd.DataFrame(columns=columns)
    r = rules[rules["rule"].astype(str).str.strip() == "scheduled"].copy()
    r["line"] = r["line"].astype(str).str.strip()
    r["day"] = r["effective"].astype(str).str[:10]
    out = r.drop_duplicates(["line", "day"], keep="last").sort_values(["line", "day"], kind="stable")
    return out.reindex(columns=columns, fill_value="").reset_index(drop=True)


def in_force(df, keys, name):
    """The last row per key of a settings table, in the Reports table's columns."""
    columns = _columns(name)
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    return compute.latest(df, keys).reindex(columns=columns, fill_value="")


def categories(cats, params):
    """Each category in force, with the loan whose payment splits into it (mortgage.split_settings), so the
    views can show a loan's parts together without reading the setting themselves."""
    out = in_force(cats, ["category"], "editor_categories")
    loan_of = {c: loan for loan, parts in mortgage.split_settings(params).items() for c, _ in parts}
    return out.assign(loan=[loan_of.get(str(c).strip(), "") for c in out["category"]]) if len(out) else out


def payee_rules(rules):
    """The row in force per pattern (the latest effective, then the later row), stopped rules included
    so they can be started again."""
    columns = _columns("editor_payee_rules")
    if rules is None or rules.empty:
        return pd.DataFrame(columns=columns)
    r = rules.sort_values("effective", kind="stable")
    out = compute.latest(r, ["pattern"]).reindex(columns=columns, fill_value="")
    stopped = (out["category"].astype(str).str.strip() == "") & (out["ask"].astype(str).str.upper() != "TRUE")
    out["status"] = stopped.map({True: "stopped", False: "in force"})
    return out.sort_values("pattern").reset_index(drop=True)


def parameters(params):
    """Household settings (jurisdiction household): the last row per key, and every known setting not set yet,
    each with what it is for (settings.SETTINGS)."""
    columns = _columns("editor_parameters")
    if params is None or params.empty:
        rows = pd.DataFrame(columns=["key", "value", "source"])
    else:
        household = params[params["jurisdiction"].astype(str) == "household"]
        rows = compute.latest(household, ["key"]).reindex(columns=["key", "value", "source"], fill_value="")
    return settings.known(rows).reindex(columns=columns, fill_value="")


def review_queue(review):
    """The review tab as it stands after ingest, for answering in the views."""
    columns = _columns("editor_review")
    if review is None or review.empty:
        return pd.DataFrame(columns=columns)
    return review.reindex(columns=columns, fill_value="")


def publish(books, review, rules, targets, actuals, month):
    """Every editor table, by Reports table name."""
    return {
        "editor_budget_lines": budget_lines(rules, targets, actuals, month),
        "editor_scheduled": scheduled(rules),
        "editor_payee_rules": payee_rules(books["rules"]),
        "editor_categories": categories(books["categories"], books["parameters"]),
        "editor_aliases": in_force(books["aliases"], ["alias"], "editor_aliases"),
        "editor_parameters": parameters(books["parameters"]),
        "editor_review": review_queue(review),
    }
