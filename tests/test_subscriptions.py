"""Subscriptions you mark, their yearly cost and price changes, on made-up charges."""

import pandas as pd

from household import nudges, recurring
from household.parsers import changes


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
        ["PAGE TURNER BOOKS", "yes"], ["QUILL & INK", "yes"], ["ACME GROCER", "no"], ["WILLOW ST", ""]]
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
    mine = todo[todo["group"] == "Subscriptions that changed price"]
    assert mine[["item", "show_from", "show_until"]].values.tolist() == [["PAGE TURNER BOOKS", "2026-10-05", "2026-11-19"]]
