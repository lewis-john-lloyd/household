"""The editor tables compute publishes for the views."""

import pandas as pd

from household import budget, editor, schema


def table(name, *records):
    return pd.DataFrame(list(records), columns=list(schema.table(schema.LEDGER, name).columns)).fillna("")


def actuals(*rows):
    return pd.DataFrame(list(rows), columns=["month", "category", "spend"])


def test_each_budget_line_is_its_row_in_force_in_words_with_this_month_and_a_year_of_spending():
    rules = table(
        "budget_rules",
        {
            "line": "groceries",
            "effective": "2025-01-01",
            "category": "food",
            "rule": "fixed",
            "amount": "500",
            "per": "monthly",
        },
        {
            "line": "groceries",
            "effective": "2026-01-01",
            "category": "food",
            "rule": "fixed",
            "amount": "600",
            "per": "monthly",
        },
        {"line": "books", "effective": "2025-01-01", "category": "media", "rule": "lookback", "amount": "20"},
        {"line": "books", "effective": "2026-01-01", "rule": "end"},
        {"line": "camp", "effective": "2026-06-01", "category": "kids", "rule": "scheduled", "amount": "90"},
    )
    spent = actuals(
        ("2025-12", "food", 70.0), ("2025-02", "food", 30.0), ("2024-12", "food", 999.0), ("2025-11", "media", 12.0)
    )
    targets = budget.targets(rules, spent, "2026-01")
    out = editor.budget_lines(rules, targets, spent, "2026-01").set_index("line")
    assert out.at["groceries", "amount"] == "600" and out.at["groceries", "in_words"] == "600.00 a month"
    assert out.at["groceries", "this_month"] == 600 and out.at["groceries", "last_12_months"] == 100
    assert (out.at["books", "in_force"], out.at["books", "in_words"]) == ("ended", "ended 2026-01-01")
    assert out.at["books", "spends_from"] == "media" and out.at["books", "last_12_months"] == 12
    assert out.at["camp", "in_force"] == "scheduled only" and out.at["camp", "scheduled"] == "2026-06-01 90.00"


def test_rules_in_words():
    assert editor.in_words({"rule": "seasonal", "amount": "40", "per": "monthly", "effective": ""}) == (
        "what the same three months a year earlier spent, plus 0.25 standard deviations; 40.00 a month until "
        "there is history"
    )
    assert editor.in_words({"rule": "sinking_fund", "amount": "1200", "per": "annually", "effective": ""}) == (
        "set aside 1,200.00 a year, spent from a running balance"
    )


def test_settings_in_force_are_the_last_row_per_key():
    rules = table(
        "payee_rules",
        {"pattern": "ACME", "category": "food", "effective": "2025-01-01"},
        {"pattern": "ACME", "category": "", "effective": "2026-01-01"},
        {"pattern": "^CHECK", "category": "", "effective": "2025-01-01", "ask": "TRUE"},
    )
    out = editor.payee_rules(rules).set_index("pattern")
    assert out.at["ACME", "status"] == "stopped" and out.at["^CHECK", "status"] == "in force"
    params = table(
        "parameters",
        {"jurisdiction": "household", "key": "transfer_account", "value": "checking"},
        {"jurisdiction": "federal", "key": "rate", "value": "1"},
        {"jurisdiction": "household", "key": "transfer_account", "value": "savings"},
    )
    out = editor.parameters(params).set_index("key")
    assert out.at["transfer_account", "value"] == "savings" and "rate" not in out.index
    # every known setting shows, set or not, with what it is for
    assert out.at["payroll_url", "source"] == "not set" and "W-4" in out.at["payroll_url", "about"]
    assert out.at["transfer_account", "about"] == ""


def test_no_settings_yet_publishes_empty_tables_with_their_columns():
    books = {
        "rules": table("payee_rules"),
        "categories": table("categories"),
        "aliases": table("category_aliases"),
        "parameters": table("parameters"),
    }
    out = editor.publish(
        books, None, table("budget_rules"), pd.DataFrame(columns=budget.TARGET_COLUMNS), actuals(), "2026-01"
    )
    for name, df in out.items():
        assert list(df.columns) == [c for c in schema.table(schema.REPORTS, name).columns if c != "run_id"]


def test_each_scheduled_amount_in_force_is_published_once_per_line_and_date():
    rules = pd.DataFrame(
        [
            {"line": "camp", "effective": "2026-06-01", "category": "kids", "rule": "scheduled", "amount": "90"},
            {"line": "camp ", "effective": "2026-06-01", "category": "kids", "rule": "scheduled", "amount": "0"},
            {"line": "camp", "effective": "2026-07-01", "category": "kids", "rule": "scheduled", "amount": "40"},
            {"line": "groceries", "effective": "2026-01-01", "category": "food", "rule": "fixed", "amount": "500"},
        ]
    )
    out = editor.scheduled(rules)
    assert out[["line", "effective", "amount"]].values.tolist() == [
        ["camp", "2026-06-01", "0"],
        ["camp", "2026-07-01", "40"],
    ]
    assert list(out.columns) == [c for c in schema.table(schema.REPORTS, "editor_scheduled").columns if c != "run_id"]


def test_each_category_names_the_loan_whose_payment_splits_into_it():
    cats = pd.DataFrame(
        [{"category": c, "group": "home", "priority": "high"} for c in ("interest", "principal", "hobby")]
    )
    params = pd.DataFrame(
        [
            {
                "jurisdiction": "household",
                "key": "loan_split:acme_loan",
                "value": "interest=interest, principal=principal, hobby=20",
                "source": "web",
            }
        ]
    )
    out = editor.categories(cats, params)
    assert dict(zip(out["category"], out["loan"])) == {
        "hobby": "acme_loan",
        "interest": "acme_loan",
        "principal": "acme_loan",
    }
    assert set(editor.categories(cats, None)["loan"]) == {""}
