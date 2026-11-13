"""Subscriptions you mark, their yearly cost and price changes, on made-up charges."""

import json
from pathlib import Path

import pandas as pd
import pytest

from household import nudges, recurring
from household.parsers import changes
from household.parsers.common import ParseError


def charge(n, day, key, amount, category="media", text=None):
    return {
        "txn_id": f"t{n}",
        "date": day,
        "amount": -amount,
        "key": key,
        "era": "ledger",
        "category": category,
        "payee_raw": key,
        "payee_text": text or key,
    }


def answer(key, yes, at="2026-10-01T00:00:00Z", note=""):
    return {
        "proposal_key": f"subscription:{key}",
        "kind": "subscription",
        "decided_at": at,
        "decision": "approve" if yes else "reject",
        "note": note,
    }


# PAGE TURNER BOOKS monthly, up from 9.99 to 12.99 in October; QUILL & INK yearly; ACME GROCER monthly-ish
# but marked not a subscription; WILLOW ST monthly and not marked either way
LINES = pd.DataFrame(
    [
        charge(i, f"2026-{m:02d}-05", "PAGE TURNER BOOKS", 9.99 if m < 10 else 12.99, text="Page Turner Books: Monthly")
        for i, m in enumerate(range(1, 11))
    ]
    + [charge(20, "2025-03-01", "QUILL & INK", 60), charge(21, "2026-03-02", "QUILL & INK ANNUAL", 60)]
    + [charge(30 + m, f"2026-{m:02d}-10", "ACME GROCER", 40) for m in range(4, 10)]
    + [charge(40 + m, f"2026-{m:02d}-15", "WILLOW ST", 20, category="family") for m in range(5, 11)]
)
DECISIONS = pd.DataFrame(
    [
        answer("PAGE TURNER BOOKS", True),
        answer("QUILL & INK", True, note="drawing app"),
        answer("ACME GROCER", True, at="2026-09-01T00:00:00Z"),
        answer("ACME GROCER", False, at="2026-09-02T00:00:00Z"),
    ]
)
TODAY = "2026-10-20"


def test_marked_subscriptions_have_a_cadence_a_yearly_cost_and_a_price_change():
    subs = recurring.subscriptions(LINES, DECISIONS, TODAY).set_index("key")
    assert list(subs.index) == ["PAGE TURNER BOOKS", "QUILL & INK"]  # the latest answer for ACME GROCER is no
    books = subs.loc["PAGE TURNER BOOKS"]
    assert (books["cadence"], books["latest"], books["previous"], books["yearly"]) == ("monthly", 12.99, 9.99, 155.88)
    assert (books["status"], books["next_expected"]) == ("price changed", "2026-11-04")
    assert books["change"] == "12.99, was 9.99 (+30%) from 2026-10-05"
    assert books["example"] == "Page Turner Books: Monthly"
    # a key covers the keys that start with it and a space
    yearly = subs.loc["QUILL & INK"]
    assert (yearly["cadence"], yearly["charges"], yearly["yearly"], yearly["note"]) == (
        "yearly",
        2,
        60.0,
        "drawing app",
    )
    assert list(recurring.fresh_changes(recurring.subscriptions(LINES, DECISIONS, TODAY), TODAY)["key"]) == [
        "PAGE TURNER BOOKS"
    ]
    assert recurring.fresh_changes(recurring.subscriptions(LINES, DECISIONS, "2026-12-01"), "2026-12-01").empty


def test_the_editor_lists_yours_then_what_recurs_unmarked_then_what_you_said_no_to():
    subs = recurring.subscriptions(LINES, DECISIONS, TODAY)
    found = recurring.discover(LINES, TODAY)
    rows = recurring.editor_rows(subs, found, LINES, DECISIONS)
    assert rows[["key", "marked"]].values.tolist() == [
        ["PAGE TURNER BOOKS", "yes"],
        ["QUILL & INK", "yes"],
        ["WILLOW ST", ""],
        ["ACME GROCER", "no"],
    ]
    assert rows.set_index("key").at["WILLOW ST", "yearly"] == 240.0


def test_a_subscription_edit_is_an_answer_and_a_fresh_change_is_in_the_weekly_email():
    result = changes.parse(
        '{"format": "household_changes", "version": 1, "created_at": "2026-10-20T00:00:00Z", "base_run": "r1", '
        '"edits": [{"kind": "subscription", "key": "willow st", "subscription": "yes"}]}'
    )
    row = result.tables["proposal_decisions"].iloc[0]
    assert (row["proposal_key"], row["kind"], row["decision"]) == ("subscription:WILLOW ST", "subscription", "approve")
    subs = recurring.subscriptions(LINES, DECISIONS, TODAY)
    todo = nudges.build(
        pd.Timestamp(TODAY).date(), pd.DataFrame(columns=["key", "value"]), changed=recurring.fresh_changes(subs, TODAY)
    )
    mine = todo[todo["group"] == "Recurring charges that changed price"]
    assert mine[["item", "show_from", "show_until"]].values.tolist() == [
        ["PAGE TURNER BOOKS", "2026-10-05", "2026-11-19"]
    ]


def test_each_subscription_is_its_own_entry_each_month_with_what_its_cadence_expected():
    groups = pd.DataFrame([{"category": "media", "group": "fun"}, {"category": "hobby", "group": "fun"}])
    months = recurring.subscription_months(LINES, DECISIONS, groups, TODAY)
    books = months[months["key"] == "PAGE TURNER BOOKS"].set_index("month")
    assert list(books.index) == [f"2026-{m:02d}" for m in range(1, 11)]
    assert pd.isna(books.at["2026-01", "expected"])  # nothing before its first charge to go by
    assert (books.at["2026-10", "expected"], books.at["2026-10", "actual"], books.at["2026-10", "group"]) == (
        9.99,
        12.99,
        "fun",
    )
    yearly = months[months["key"] == "QUILL & INK"].set_index("month")
    assert yearly.at["2026-03", "actual"] == 60.0 and yearly.at["2026-03", "expected"] == 60.0  # due a year on
    assert yearly.at["2026-04", "expected"] == 0.0 and yearly.at["2025-04", "actual"] == 0.0


def merge(key, into, at="2026-10-02T00:00:00Z"):
    return {
        "proposal_key": f"subscription_merge:{key}",
        "kind": "subscription_merge",
        "decided_at": at,
        "decision": "approve" if into else "reject",
        "note": into,
    }


# BLUE PUMP FUEL monthly at 30 until April, then the same charge as WILLOW ST from May (32 in October); ACME GROCER monthly
# at 40 in 2025 and nothing since
RENAMED = pd.DataFrame(
    [charge(i, f"2026-{m:02d}-03", "BLUE PUMP FUEL", 30) for i, m in enumerate(range(1, 5))]
    + [charge(10 + m, f"2026-{m:02d}-03", "WILLOW ST", 30 if m < 10 else 32) for m in range(5, 11)]
    + [charge(30 + m, f"2025-{m:02d}-10", "ACME GROCER", 40) for m in range(1, 7)]
)


def test_a_merged_key_adds_its_charges_to_the_one_it_joins():
    decisions = pd.DataFrame([answer("WILLOW ST", True), merge("BLUE PUMP FUEL", "WILLOW ST")])
    subs = recurring.subscriptions(RENAMED, decisions, TODAY).set_index("key")
    assert list(subs.index) == ["WILLOW ST"]
    one = subs.loc["WILLOW ST"]
    assert (one["charges"], one["first"], one["cadence"], one["status"]) == (
        10,
        "2026-01-03",
        "monthly",
        "price changed",
    )
    assert (one["includes"], one["merged"]) == ("BLUE PUMP FUEL", "BLUE PUMP FUEL")
    months = recurring.subscription_months(RENAMED, decisions, today=TODAY)
    assert months[months["month"] == "2026-02"]["actual"].tolist() == [30.0]
    # separated again, it is its own entry in the editor
    apart = pd.concat([decisions, pd.DataFrame([merge("BLUE PUMP FUEL", "", at="2026-10-03T00:00:00Z")])])
    rows = recurring.editor_rows(
        recurring.subscriptions(RENAMED, apart, TODAY), recurring.discover(RENAMED, TODAY), RENAMED, apart, TODAY
    )
    assert rows[["key", "marked", "status"]].values.tolist() == [
        ["WILLOW ST", "yes", "price changed"],
        ["ACME GROCER", "", "stopped"],
        ["BLUE PUMP FUEL", "", "stopped"],
    ]


def test_the_editor_lists_ongoing_charges_before_the_ones_that_seem_to_have_stopped():
    merged = pd.DataFrame([merge("BLUE PUMP FUEL", "WILLOW ST")])  # merged, neither tracked yet
    rows = recurring.editor_rows(
        recurring.subscriptions(RENAMED, merged, TODAY), recurring.discover(RENAMED, TODAY), RENAMED, merged, TODAY
    )
    assert rows[["key", "marked", "status", "charges", "merged"]].values.tolist() == [
        ["WILLOW ST", "", "price changed", 10, "BLUE PUMP FUEL"],
        ["ACME GROCER", "", "stopped", 6, ""],
    ]
    # tracking a key that was merged tracks the group it joined
    tracked = pd.concat([merged, pd.DataFrame([answer("BLUE PUMP FUEL", True)])])
    assert list(recurring.subscriptions(RENAMED, tracked, TODAY)["key"]) == ["WILLOW ST"]


def test_a_chain_of_merges_ends_at_one_key_and_a_cycle_merges_nothing():
    chain = pd.DataFrame([merge("ACME GROCER", "BLUE PUMP FUEL"), merge("BLUE PUMP FUEL", "WILLOW ST")])
    assert recurring.merges(chain) == {"ACME GROCER": "WILLOW ST", "BLUE PUMP FUEL": "WILLOW ST"}
    cycle = pd.DataFrame([merge("ACME GROCER", "WILLOW ST"), merge("WILLOW ST", "ACME GROCER")])
    assert recurring.merges(cycle) == {}


def test_a_recurring_merge_edit_is_an_answer_and_cannot_join_itself():
    doc = (
        '{"format": "household_changes", "version": 1, "created_at": "2026-10-20T00:00:00Z", "base_run": "r1", '
        '"edits": [%s]}'
    )
    result = changes.parse(doc % '{"kind": "recurring_merge", "key": "blue pump fuel", "same_as": "willow st"}')
    row = result.tables["proposal_decisions"].iloc[0]
    assert (row["proposal_key"], row["kind"], row["decision"], row["note"]) == (
        "subscription_merge:BLUE PUMP FUEL",
        "subscription_merge",
        "approve",
        "WILLOW ST",
    )
    apart = changes.parse(doc % '{"kind": "recurring_merge", "key": "BLUE PUMP FUEL", "same_as": ""}')
    assert apart.tables["proposal_decisions"].iloc[0]["decision"] == "reject"
    with pytest.raises(ParseError, match="same_as must be another key"):
        changes.parse(doc % '{"kind": "recurring_merge", "key": "WILLOW ST", "same_as": "willow st"}')


def test_tracked_charges_set_a_recurring_budget_line_with_its_allowance():
    from household import budget

    # PAGE TURNER BOOKS monthly in media, 9.99 then 12.99 from October; QUILL & INK yearly at 60, also media
    expected = recurring.monthly_expected(LINES, DECISIONS, "2026-10", TODAY)
    assert expected[("media", "2025-03")] == (5.0, 1)  # a yearly charge counts a twelfth a month, from its first
    assert expected[("media", "2026-01")] == (14.99, 2)
    assert expected[("media", "2026-10")] == (17.99, 2)  # the new price from the month it was charged
    rules = pd.DataFrame(
        [["media", "2026-09-01", "media", "", "", "recurring", "20", "monthly", ""]],
        columns=["line", "effective", "category", "account", "priority", "rule", "amount", "per", "note"],
    )
    out = budget.targets(rules, None, "2026-10", expected).set_index("month")
    assert out["target"].to_dict() == {"2026-09": 34.99, "2026-10": 37.99}
    assert out.at["2026-10", "basis"] == "recurring: 2 charges 17.99 + allowance 20.00"
    none = rules.assign(amount="")  # no allowance: the charges alone
    assert budget.targets(none, None, "2026-10", expected).set_index("month").at["2026-10", "target"] == 17.99


def test_daily_spending_leaves_out_what_is_not_spending():
    from household import compute

    lines = pd.DataFrame(
        [
            {"date": "2026-10-05", "category": "media", "amount": -10},
            {"date": "2026-10-05", "category": "media", "amount": -2.5},
            {"date": "2026-10-06", "category": "transfer", "amount": -50},
            {"date": "2026-10-07", "category": "hobby", "amount": 4},
        ]
    )
    out = compute.daily_actuals(lines, {"transfer"})
    assert out.values.tolist() == [["2026-10-05", "media", 12.5], ["2026-10-07", "hobby", -4.0]]


def named(key, name, at="2026-10-02T00:00:00Z"):
    return {
        "proposal_key": f"subscription_name:{key}",
        "kind": "subscription_name",
        "decided_at": at,
        "decision": "approve" if name else "reject",
        "note": name,
    }


def test_a_charge_can_be_shown_by_a_name_you_give_it_and_back_by_its_key():
    decisions = pd.concat(
        [DECISIONS, pd.DataFrame([named("PAGE TURNER BOOKS", "Book club"), named("WILLOW ST", "Lessons")])]
    )
    subs = recurring.subscriptions(LINES, decisions, TODAY).set_index("key")
    assert subs["name"].to_dict() == {"PAGE TURNER BOOKS": "Book club", "QUILL & INK": ""}
    rows = recurring.editor_rows(
        recurring.subscriptions(LINES, decisions, TODAY), recurring.discover(LINES, TODAY), LINES, decisions, TODAY
    ).set_index("key")
    assert rows.at["WILLOW ST", "name"] == "Lessons"  # found but not tracked: named all the same
    months = recurring.subscription_months(LINES, decisions, today=TODAY)
    assert set(months.loc[months["key"] == "PAGE TURNER BOOKS", "name"]) == {"Book club"}
    cleared = pd.concat([decisions, pd.DataFrame([named("PAGE TURNER BOOKS", "", at="2026-10-03T00:00:00Z")])])
    assert recurring.names(cleared) == {"WILLOW ST": "Lessons"}


def test_a_recurring_name_edit_is_an_answer_on_one_line():
    doc = (
        '{"format": "household_changes", "version": 1, "created_at": "2026-10-20T00:00:00Z", "base_run": "r1", '
        '"edits": [%s]}'
    )
    row = (
        changes.parse(doc % '{"kind": "recurring_name", "key": "page turner books", "name": "Book club"}')
        .tables["proposal_decisions"]
        .iloc[0]
    )
    assert (row["proposal_key"], row["kind"], row["decision"], row["note"]) == (
        "subscription_name:PAGE TURNER BOOKS",
        "subscription_name",
        "approve",
        "Book club",
    )
    back = changes.parse(doc % '{"kind": "recurring_name", "key": "PAGE TURNER BOOKS", "name": ""}')
    assert back.tables["proposal_decisions"].iloc[0]["decision"] == "reject"
    with pytest.raises(ParseError, match="at most 60 characters"):
        changes.parse(doc % ('{"kind": "recurring_name", "key": "WILLOW ST", "name": "%s"}' % ("x" * 61)))


def linked(key, line, at="2026-10-02T00:00:00Z"):
    return {
        "proposal_key": f"subscription_line:{key}",
        "kind": "subscription_line",
        "decided_at": at,
        "decision": "approve" if line else "reject",
        "note": line,
    }


def test_a_charge_linked_to_a_budget_line_sets_that_lines_target_alone():
    from household import budget

    # PAGE TURNER BOOKS (12.99 a month by October) pays the books line, so the other media line has only QUILL &
    # INK's twelfth (5.00); WILLOW ST pays lessons though it isn't tracked
    decisions = pd.concat(
        [DECISIONS, pd.DataFrame([linked("PAGE TURNER BOOKS", "books"), linked("WILLOW ST", "lessons")])]
    )
    assert recurring.links(decisions) == {"PAGE TURNER BOOKS": "books", "WILLOW ST": "lessons"}
    expected = recurring.monthly_expected(LINES, decisions, "2026-10", TODAY)
    assert expected[("media", "2026-10")] == (5.0, 1)
    by_line = recurring.line_expected(LINES, decisions, "2026-10")
    assert by_line["books"]["2026-10"] == (12.99, 1) and by_line["lessons"]["2026-10"] == (20.0, 1)
    rules = pd.DataFrame(
        [
            ["books", "2026-10-01", "media", "", "", "recurring", "", "monthly", ""],
            ["other media", "2026-10-01", "media", "", "", "recurring", "20", "monthly", ""],
        ],
        columns=["line", "effective", "category", "account", "priority", "rule", "amount", "per", "note"],
    )
    out = budget.targets(rules, None, "2026-10", expected, by_line).set_index("line")
    assert out["target"].to_dict() == {"books": 12.99, "other media": 25.0}
    assert out.at["books", "basis"] == "recurring: 1 linked charge 12.99 + allowance 0.00"
    rows = recurring.editor_rows(
        recurring.subscriptions(LINES, decisions, TODAY), recurring.discover(LINES, TODAY), LINES, decisions, TODAY
    ).set_index("key")
    assert rows.at["PAGE TURNER BOOKS", "line"] == "books" and rows.at["QUILL & INK", "line"] == ""
    unlinked = pd.concat([decisions, pd.DataFrame([linked("PAGE TURNER BOOKS", "", at="2026-10-03T00:00:00Z")])])
    assert recurring.links(unlinked) == {"WILLOW ST": "lessons"}
    assert recurring.monthly_expected(LINES, unlinked, "2026-10", TODAY)[("media", "2026-10")] == (17.99, 2)


def test_a_recurring_line_edit_names_a_budget_line_that_exists():
    from test_changes import LEDGER, ledger_reader

    doc = (
        '{"format": "household_changes", "version": 1, "created_at": "2026-10-20T00:00:00Z", "base_run": "r1", '
        '"edits": [%s]}'
    )
    result = changes.parse(doc % '{"kind": "recurring_line", "key": "acme grocer", "line": "groceries"}')
    row = result.tables["proposal_decisions"].iloc[0]
    assert (row["proposal_key"], row["kind"], row["decision"], row["note"]) == (
        "subscription_line:ACME GROCER",
        "subscription_line",
        "approve",
        "groceries",
    )
    result.check(ledger_reader(**LEDGER))
    off = changes.parse(doc % '{"kind": "recurring_line", "key": "ACME GROCER", "line": ""}')
    assert off.tables["proposal_decisions"].iloc[0]["decision"] == "reject"
    off.check(ledger_reader(**LEDGER))
    with pytest.raises(ParseError, match="no budget line hobby"):
        changes.parse(doc % '{"kind": "recurring_line", "key": "ACME GROCER", "line": "hobby"}').check(
            ledger_reader(**LEDGER)
        )


# a monthly charge with a cheap one-time purchase from the same merchant just before its latest renewal
ONCE = pd.DataFrame(
    [charge(i, f"2026-{m:02d}-12", "WILLOW ST", 20.0) for i, m in enumerate(range(1, 11))]
    + [charge(50, "2026-10-01", "WILLOW ST", 4.0, text="WILLOW ST one book")]
)
TRACK_WILLOW = pd.DataFrame([answer("WILLOW ST", True)])


def test_a_line_marked_one_off_leaves_the_charge_and_its_history_says_so():
    # counted, the purchase is the price before the renewal: a +400% "price change" that isn't one
    before = recurring.subscriptions(ONCE.sort_values("date"), TRACK_WILLOW, TODAY).iloc[0]
    assert before["status"] == "price changed" and before["previous"] == 4.0
    marked = ONCE.assign(one_off=["TRUE" if t == "t50" else "FALSE" for t in ONCE["txn_id"]])
    after = recurring.subscriptions(marked, TRACK_WILLOW, TODAY).iloc[0]
    assert after["status"] == "active" and after["previous"] == 20.0 and after["charges"] == 10
    history = recurring.charge_history(marked, TRACK_WILLOW, ["WILLOW ST"])
    assert history[["txn_id", "counts", "why"]].head(3).values.tolist() == [
        ["t9", "yes", "sets the latest price"],  # newest first
        ["t50", "no", "marked one-off"],
        ["t8", "yes", ""],
    ]
    assert len(history) == 11 and (history["spend"] > 0).all()


def test_budget_averages_learn_from_months_without_one_off_lines():
    from household import budget, categorize, compute

    lines = pd.DataFrame(
        [
            {**charge(i, f"2026-0{m}-03", "ACME GROCER", 50.0, category="food"), "splits": "", "one_off": "FALSE"}
            for i, m in enumerate(range(1, 5))
        ]
        + [{**charge(9, "2026-04-20", "ACME GROCER", 90.0, category="food"), "splits": "", "one_off": "TRUE"}]
    )
    rules = pd.DataFrame(
        [
            {
                "line": "food",
                "effective": "2026-01-01",
                "category": "food",
                "priority": "high",
                "rule": "trailing",
                "amount": "50",
                "per": "monthly",
                "note": "",
            }
        ]
    )
    usual = compute.monthly_actuals(categorize.actual_rows(lines[~recurring.one_off(lines)]))
    every = compute.monthly_actuals(categorize.actual_rows(lines))
    may = lambda actuals: budget.targets(rules, actuals, "2026-05").query("month == '2026-05'")["target"].iloc[0]  # noqa: E731
    assert may(usual) < may(every)  # the one-off raised April's spending, so it would have raised the average
    assert every.query("month == '2026-04'")["spend"].iloc[0] == 140.0  # and it still counts as spent


def test_a_price_change_follows_the_fixture_the_views_preview_checks_too():
    # the same file is read by web/test/shape.test.ts for the page's preview (priceChanged)
    doc = json.loads((Path(__file__).parent / "fixtures" / "charge_prices.json").read_text())
    for case in doc["cases"]:
        assert recurring.price_changed(case["latest"], case["previous"]) == case["changed"], case["why"]
