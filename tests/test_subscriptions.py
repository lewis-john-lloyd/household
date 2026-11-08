"""Subscriptions you mark, their yearly cost and price changes, on made-up charges."""

import pandas as pd
import pytest

from household import nudges, recurring
from household.parsers import changes
from household.parsers.common import ParseError


def charge(n, day, key, amount, category="media", text=None):
    return {"txn_id": f"t{n}", "date": day, "amount": -amount, "key": key, "era": "ledger", "category": category,
            "payee_raw": key, "payee_text": text or key}


def answer(key, yes, at="2026-10-01T00:00:00Z", note=""):
    return {"proposal_key": f"subscription:{key}", "kind": "subscription", "decided_at": at,
            "decision": "approve" if yes else "reject", "note": note}


# PAGE TURNER BOOKS monthly, up from 9.99 to 12.99 in October; QUILL & INK yearly; ACME GROCER monthly-ish
# but marked not a subscription; WILLOW ST monthly and not marked either way
LINES = pd.DataFrame(
    [charge(i, f"2026-{m:02d}-05", "PAGE TURNER BOOKS", 9.99 if m < 10 else 12.99, text="Page Turner Books: Monthly")
     for i, m in enumerate(range(1, 11))]
    + [charge(20, "2025-03-01", "QUILL & INK", 60), charge(21, "2026-03-02", "QUILL & INK ANNUAL", 60)]
    + [charge(30 + m, f"2026-{m:02d}-10", "ACME GROCER", 40) for m in range(4, 10)]
    + [charge(40 + m, f"2026-{m:02d}-15", "WILLOW ST", 20, category="family") for m in range(5, 11)])
DECISIONS = pd.DataFrame([answer("PAGE TURNER BOOKS", True), answer("QUILL & INK", True, note="drawing app"),
                          answer("ACME GROCER", True, at="2026-09-01T00:00:00Z"),
                          answer("ACME GROCER", False, at="2026-09-02T00:00:00Z")])
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
    assert (yearly["cadence"], yearly["charges"], yearly["yearly"], yearly["note"]) == ("yearly", 2, 60.0, "drawing app")
    assert list(recurring.fresh_changes(recurring.subscriptions(LINES, DECISIONS, TODAY), TODAY)["key"]) == [
        "PAGE TURNER BOOKS"]
    assert recurring.fresh_changes(recurring.subscriptions(LINES, DECISIONS, "2026-12-01"), "2026-12-01").empty


def test_the_editor_lists_yours_then_what_recurs_unmarked_then_what_you_said_no_to():
    subs = recurring.subscriptions(LINES, DECISIONS, TODAY)
    found = recurring.discover(LINES, TODAY)
    rows = recurring.editor_rows(subs, found, LINES, DECISIONS)
    assert rows[["key", "marked"]].values.tolist() == [
        ["PAGE TURNER BOOKS", "yes"], ["QUILL & INK", "yes"], ["WILLOW ST", ""], ["ACME GROCER", "no"]]
    assert rows.set_index("key").at["WILLOW ST", "yearly"] == 240.0


def test_a_subscription_edit_is_an_answer_and_a_fresh_change_is_in_the_weekly_email():
    result = changes.parse(
        '{"format": "household_changes", "version": 1, "created_at": "2026-10-20T00:00:00Z", "base_run": "r1", '
        '"edits": [{"kind": "subscription", "key": "willow st", "subscription": "yes"}]}')
    row = result.tables["proposal_decisions"].iloc[0]
    assert (row["proposal_key"], row["kind"], row["decision"]) == ("subscription:WILLOW ST", "subscription", "approve")
    subs = recurring.subscriptions(LINES, DECISIONS, TODAY)
    todo = nudges.build(pd.Timestamp(TODAY).date(), pd.DataFrame(columns=["key", "value"]),
                        changed=recurring.fresh_changes(subs, TODAY))
    mine = todo[todo["group"] == "Recurring charges that changed price"]
    assert mine[["item", "show_from", "show_until"]].values.tolist() == [["PAGE TURNER BOOKS", "2026-10-05", "2026-11-19"]]


def test_each_subscription_is_its_own_entry_each_month_with_what_its_cadence_expected():
    groups = pd.DataFrame([{"category": "media", "group": "fun"}, {"category": "hobby", "group": "fun"}])
    months = recurring.subscription_months(LINES, DECISIONS, groups, TODAY)
    books = months[months["key"] == "PAGE TURNER BOOKS"].set_index("month")
    assert list(books.index) == [f"2026-{m:02d}" for m in range(1, 11)]
    assert pd.isna(books.at["2026-01", "expected"])  # nothing before its first charge to go by
    assert (books.at["2026-10", "expected"], books.at["2026-10", "actual"], books.at["2026-10", "group"]) == (9.99, 12.99, "fun")
    yearly = months[months["key"] == "QUILL & INK"].set_index("month")
    assert yearly.at["2026-03", "actual"] == 60.0 and yearly.at["2026-03", "expected"] == 60.0  # due a year on
    assert yearly.at["2026-04", "expected"] == 0.0 and yearly.at["2025-04", "actual"] == 0.0


def merge(key, into, at="2026-10-02T00:00:00Z"):
    return {"proposal_key": f"subscription_merge:{key}", "kind": "subscription_merge", "decided_at": at,
            "decision": "approve" if into else "reject", "note": into}


# BLUE PUMP FUEL monthly at 30 until April, then the same charge as WILLOW ST from May (32 in October); ACME GROCER monthly
# at 40 in 2025 and nothing since
RENAMED = pd.DataFrame(
    [charge(i, f"2026-{m:02d}-03", "BLUE PUMP FUEL", 30) for i, m in enumerate(range(1, 5))]
    + [charge(10 + m, f"2026-{m:02d}-03", "WILLOW ST", 30 if m < 10 else 32) for m in range(5, 11)]
    + [charge(30 + m, f"2025-{m:02d}-10", "ACME GROCER", 40) for m in range(1, 7)])


def test_a_merged_key_adds_its_charges_to_the_one_it_joins():
    decisions = pd.DataFrame([answer("WILLOW ST", True), merge("BLUE PUMP FUEL", "WILLOW ST")])
    subs = recurring.subscriptions(RENAMED, decisions, TODAY).set_index("key")
    assert list(subs.index) == ["WILLOW ST"]
    one = subs.loc["WILLOW ST"]
    assert (one["charges"], one["first"], one["cadence"], one["status"]) == (10, "2026-01-03", "monthly", "price changed")
    assert (one["includes"], one["merged"]) == ("BLUE PUMP FUEL", "BLUE PUMP FUEL")
    months = recurring.subscription_months(RENAMED, decisions, today=TODAY)
    assert months[months["month"] == "2026-02"]["actual"].tolist() == [30.0]
    # separated again, it is its own entry in the editor
    apart = pd.concat([decisions, pd.DataFrame([merge("BLUE PUMP FUEL", "", at="2026-10-03T00:00:00Z")])])
    rows = recurring.editor_rows(recurring.subscriptions(RENAMED, apart, TODAY), recurring.discover(RENAMED, TODAY),
                                 RENAMED, apart, TODAY)
    assert rows[["key", "marked", "status"]].values.tolist() == [
        ["WILLOW ST", "yes", "price changed"], ["ACME GROCER", "", "stopped"], ["BLUE PUMP FUEL", "", "stopped"]]


def test_the_editor_lists_ongoing_charges_before_the_ones_that_seem_to_have_stopped():
    merged = pd.DataFrame([merge("BLUE PUMP FUEL", "WILLOW ST")])  # merged, neither tracked yet
    rows = recurring.editor_rows(recurring.subscriptions(RENAMED, merged, TODAY), recurring.discover(RENAMED, TODAY),
                                 RENAMED, merged, TODAY)
    assert rows[["key", "marked", "status", "charges", "merged"]].values.tolist() == [
        ["WILLOW ST", "", "price changed", 10, "BLUE PUMP FUEL"], ["ACME GROCER", "", "stopped", 6, ""]]
    # tracking a key that was merged tracks the group it joined
    tracked = pd.concat([merged, pd.DataFrame([answer("BLUE PUMP FUEL", True)])])
    assert list(recurring.subscriptions(RENAMED, tracked, TODAY)["key"]) == ["WILLOW ST"]


def test_a_chain_of_merges_ends_at_one_key_and_a_cycle_merges_nothing():
    chain = pd.DataFrame([merge("ACME GROCER", "BLUE PUMP FUEL"), merge("BLUE PUMP FUEL", "WILLOW ST")])
    assert recurring.merges(chain) == {"ACME GROCER": "WILLOW ST", "BLUE PUMP FUEL": "WILLOW ST"}
    cycle = pd.DataFrame([merge("ACME GROCER", "WILLOW ST"), merge("WILLOW ST", "ACME GROCER")])
    assert recurring.merges(cycle) == {}


def test_a_recurring_merge_edit_is_an_answer_and_cannot_join_itself():
    doc = ('{"format": "household_changes", "version": 1, "created_at": "2026-10-20T00:00:00Z", "base_run": "r1", '
           '"edits": [%s]}')
    result = changes.parse(doc % '{"kind": "recurring_merge", "key": "blue pump fuel", "same_as": "willow st"}')
    row = result.tables["proposal_decisions"].iloc[0]
    assert (row["proposal_key"], row["kind"], row["decision"], row["note"]) == (
        "subscription_merge:BLUE PUMP FUEL", "subscription_merge", "approve", "WILLOW ST")
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
    rules = pd.DataFrame([["media", "2026-09-01", "media", "", "", "recurring", "20", "monthly", ""]],
                         columns=["line", "effective", "category", "account", "priority", "rule", "amount", "per", "note"])
    out = budget.targets(rules, None, "2026-10", expected).set_index("month")
    assert out["target"].to_dict() == {"2026-09": 34.99, "2026-10": 37.99}
    assert out.at["2026-10", "basis"] == "recurring: 2 charges 17.99 + allowance 20.00"
    none = rules.assign(amount="")  # no allowance: the charges alone
    assert budget.targets(none, None, "2026-10", expected).set_index("month").at["2026-10", "target"] == 17.99


def test_daily_spending_leaves_out_what_is_not_spending():
    from household import compute
    lines = pd.DataFrame([{"date": "2026-10-05", "category": "media", "amount": -10},
                          {"date": "2026-10-05", "category": "media", "amount": -2.5},
                          {"date": "2026-10-06", "category": "transfer", "amount": -50},
                          {"date": "2026-10-07", "category": "hobby", "amount": 4}])
    out = compute.daily_actuals(lines, {"transfer"})
    assert out.values.tolist() == [["2026-10-05", "media", 12.5], ["2026-10-07", "hobby", -4.0]]
