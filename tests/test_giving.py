"""Giving by recipient: gifts per month and recipient, and whether each recipient is deductible."""

import json

import pandas as pd

from household import giving
from household.parsers import changes

CATEGORIES = pd.DataFrame(
    [["gifts", "giving", "low"], ["food", "food", "high"]], columns=["category", "group", "priority"]
)


def line(txn_id, date, amount, payee, category, key="", splits=""):
    return {
        "txn_id": txn_id,
        "date": date,
        "amount": amount,
        "payee_raw": payee,
        "payee_text": payee,
        "key": key,
        "category": category,
        "splits": splits,
    }


CATEGORIZED = pd.DataFrame(
    [
        line("t1", "2026-03-02", -50.0, "QUILL & INK DONATION", "gifts", "QUILL & INK"),
        line("t2", "2026-03-20", -25.0, "QUILL & INK DONATION", "gifts", "QUILL & INK"),
        line("t3", "2026-03-05", -40.0, "WILLOW ST FUND", "gifts", "WILLOW ST"),
        line("t4", "2026-03-06", -30.0, "ACME GROCER", "food", "ACME GROCER"),
        line("t5", "2026-04-01", -90.0, "ACME GROCER", "food", "ACME GROCER", "food -80; gifts -10"),
        line("t6", "2025-12-30", -60.0, "WILLOW ST FUND", "gifts", "WILLOW ST"),
    ]
)


def decisions(*answers):
    return pd.DataFrame(
        [
            {"proposal_key": giving.PREFIX + k, "kind": giving.KIND, "decided_at": at, "decision": d, "note": ""}
            for k, d, at in answers
        ],
        columns=["proposal_key", "kind", "decided_at", "decision", "note"],
    )


def test_gifts_are_summed_per_month_and_recipient_with_the_latest_answer():
    said = decisions(("QUILL & INK", "reject", "2026-01-01"), ("QUILL & INK", "approve", "2026-02-01"))
    out = giving.by_recipient(CATEGORIZED, CATEGORIES, said)
    rows = out[["month", "key", "given", "gifts", "deductible"]].values.tolist()
    assert rows == [
        ["2025-12", "WILLOW ST", 60.0, 1, ""],
        ["2026-03", "QUILL & INK", 75.0, 2, "yes"],
        ["2026-03", "WILLOW ST", 40.0, 1, ""],
        ["2026-04", "ACME GROCER", 10.0, 1, ""],  # the gift part of a split line
    ]
    assert out.loc[1, "name"] == "QUILL & INK DONATION"  # the payee seen most, when no name was given
    assert giving.deductible_total(out, 2026) == 75.0
    assert giving.deductible_total(out, 2025) == 0.0


def test_no_giving_group_means_no_gifts():
    out = giving.by_recipient(CATEGORIZED, CATEGORIES.assign(group="food"), None)
    assert out.empty and list(out.columns) == giving.COLUMNS


def test_a_recipient_edit_becomes_a_deductible_answer():
    text = json.dumps(
        {
            "format": changes.FORMAT,
            "version": 1,
            "created_at": "2026-01-31T12:00:00Z",
            "base_run": "r1",
            "edits": [
                {"kind": "recipient", "key": "QUILL & INK", "deductible": "yes"},
                {"kind": "recipient", "key": "WILLOW ST", "deductible": "no"},
            ],
        }
    )
    result = changes.parse(text)
    rows = result.tables["proposal_decisions"][["proposal_key", "kind", "decision"]].values.tolist()
    assert rows == [
        ["deductible:QUILL & INK", "deductible", "approve"],
        ["deductible:WILLOW ST", "deductible", "reject"],
    ]
    assert "recipient QUILL & INK: deductible" in result.notes
    said = giving.deductible(result.tables["proposal_decisions"])
    assert said == {"QUILL & INK": "yes", "WILLOW ST": "no"}


def test_a_recipient_edit_must_say_yes_or_no():
    text = json.dumps(
        {
            "format": changes.FORMAT,
            "version": 1,
            "created_at": "2026-01-31T12:00:00Z",
            "base_run": "r1",
            "edits": [{"kind": "recipient", "key": "WILLOW ST", "deductible": "maybe"}],
        }
    )
    try:
        changes.parse(text)
    except Exception as e:  # ParseError
        assert "deductible 'maybe' is not yes or no" in str(e)
    else:
        raise AssertionError("refused")


def test_a_gift_with_no_payee_is_kept_under_no_payee():
    blank = pd.DataFrame([line("t9", "2026-05-01", -20.0, "", "gifts")])
    out = giving.by_recipient(blank, CATEGORIES, None)
    assert out[["key", "name", "given"]].values.tolist() == [["", "No payee", 20.0]]
