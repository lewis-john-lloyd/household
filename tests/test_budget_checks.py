"""Budget checks: what looks off, said where the number is, on made-up lines."""

import pandas as pd

from household import budget_checks, recurring
from household.parsers import changes


def line(n, day, amount, category="hobby", key="PAGE TURNER BOOKS", one_off=""):
    return {
        "txn_id": f"t{n}",
        "date": day,
        "amount": -amount,
        "key": key,
        "era": "ledger",
        "category": category,
        "payee_raw": key,
        "payee_text": key,
        "splits": "",
        "one_off": one_off,
    }


def decision(kind, key, yes=True, note="", at="2026-10-01T00:00:00Z"):
    return {
        "proposal_key": f"{kind}:{key}",
        "kind": kind,
        "decided_at": at,
        "decision": "approve" if yes else "reject",
        "note": note,
    }


MONTH = "2026-11"
# hobby spends 20 a month, except one 90 purchase in September; its trailing line averages the four
# months before November, so the one purchase lifts its target well above a usual month
HOBBY = [line(i, f"2025-{m:02d}-10", 20, key="QUILL & INK") for i, m in enumerate(range(11, 13))] + [
    line(10 + m, f"2026-{m:02d}-10", 20, key="QUILL & INK") for m in range(1, 11)
]
SPIKE = line(99, "2026-09-20", 90, key="WILLOW ST")
PLANNED = pd.DataFrame(
    [
        {"month": MONTH, "line": "hobby", "category": "hobby", "account": "", "priority": "low"}
        | {"rule": "trailing", "target": 55.0, "basis": "trailing"},
        {"month": MONTH, "line": "food", "category": "food", "account": "", "priority": "high"}
        | {"rule": "fixed", "target": 600.0, "basis": "fixed"},
    ]
)


def test_a_floating_target_lifted_by_one_big_line_names_that_line_and_opens_it():
    found = budget_checks.high_targets(PLANNED, pd.DataFrame(HOBBY + [SPIKE]), MONTH)
    assert [(c["kind"], c["about"], c["category"], c["open"]) for c in found] == [
        ("target_high", "hobby", "hobby", "txn:t99")
    ]
    c = found[0]
    assert c["check"] == "target_high|hobby|t99"  # the evidence: settling it holds while that line is behind it
    assert c["label"] == "Budget $55.00, usually $20.00"  # leak-guard: allow (made up)
    assert (
        "September 2026, which spent $110.00, the most from one line: WILLOW ST, $90.00 on Sep 20, 2026"  # leak-guard: allow (made up)
        in c["detail"]  # leak-guard: allow (made up)
    )


def test_once_the_big_line_is_marked_one_off_there_is_nothing_to_point_at():
    marked = dict(SPIKE, one_off="TRUE")
    # even before a run brings the target down, no month in its average stands out, so no line is blamed
    assert budget_checks.high_targets(PLANNED, pd.DataFrame(HOBBY + [marked]), MONTH) == []


def test_a_target_near_a_usual_month_or_a_small_one_is_left_alone():
    near = PLANNED.assign(target=[28.0, 600.0])
    assert budget_checks.high_targets(near, pd.DataFrame(HOBBY + [SPIKE]), MONTH) == []
    small = [dict(r, amount=r["amount"] / 10) for r in HOBBY]  # 2 a month: 5.5 is well above, but not HIGH_MIN more
    assert budget_checks.high_targets(PLANNED.assign(target=[5.5, 600.0]), pd.DataFrame(small), MONTH) == []


# a tracked charge whose latest charge is a one-time purchase from the same merchant
BOOKS = [line(i, f"2026-{m:02d}-03", 15, category="media") for i, m in enumerate(range(5, 11))] + [
    line(50, "2026-11-01", 80, category="media")
]
TRACKED = [
    decision("subscription", "PAGE TURNER BOOKS"),
    decision("subscription_name", "PAGE TURNER BOOKS", note="Books"),
]


def test_a_charge_that_changed_price_is_a_check_under_the_line_item_it_moves():
    subs = recurring.subscriptions(pd.DataFrame(BOOKS), pd.DataFrame(TRACKED), "2026-11-02")
    found = budget_checks.price_changes(subs, pd.DataFrame(TRACKED))
    assert [(c["kind"], c["about"], c["category"], c["label"], c["open"]) for c in found] == [
        (
            "price_changed",
            "PAGE TURNER BOOKS",
            "media",
            "Books $15.00 → $80.00",  # leak-guard: allow (made up)
            "charge:PAGE TURNER BOOKS",
        )  # leak-guard: allow (made up)
    ]
    assert found[0]["check"] == "price_changed|PAGE TURNER BOOKS|2026-11-01|80.00"
    # paying a budget line in another category, it sits under that line's category
    paid = TRACKED + [decision("subscription_line", "PAGE TURNER BOOKS", note="reading")]
    rules = pd.DataFrame([{"line": "reading", "effective": "2026-01-01", "category": "hobby"}])
    assert budget_checks.price_changes(subs, pd.DataFrame(paid), rules)[0]["category"] == "hobby"


def test_looks_right_settles_one_fingerprint_and_a_new_one_comes_back():
    lines = pd.DataFrame(BOOKS + HOBBY + [SPIKE])
    subs = recurring.subscriptions(lines, pd.DataFrame(TRACKED), "2026-11-02")
    both = budget_checks.build(subs, PLANNED, lines, pd.DataFrame(TRACKED), MONTH)
    assert list(both["kind"]) == ["target_high", "price_changed"]  # by line item: hobby, then media
    ok = TRACKED + [
        decision("looks_right", "price_changed|PAGE TURNER BOOKS|2026-11-01|80.00", at="2026-11-02T00:00:00Z")
    ]
    left = budget_checks.build(subs, PLANNED, lines, pd.DataFrame(ok), MONTH)
    assert list(left["kind"]) == ["target_high"]
    # a later charge at another price is new evidence: the check is back
    later = pd.DataFrame(BOOKS + [line(51, "2026-11-20", 95, category="media")] + HOBBY + [SPIKE])
    subs2 = recurring.subscriptions(later, pd.DataFrame(ok), "2026-11-21")
    again = budget_checks.build(subs2, PLANNED, later, pd.DataFrame(ok), MONTH)
    assert "price_changed" in set(again["kind"])
    # and "check again" takes a settled one back
    undo = ok + [
        decision(
            "looks_right", "price_changed|PAGE TURNER BOOKS|2026-11-01|80.00", yes=False, at="2026-11-03T00:00:00Z"
        )
    ]
    assert len(budget_checks.build(subs, PLANNED, lines, pd.DataFrame(undo), MONTH)) == 2


def test_a_looks_right_edit_is_a_decision_and_is_checked_alone():
    edit = {"kind": "looks_right", "check": "target_high|hobby|t99", "looks_right": "yes"}
    assert changes._values("looks_right", edit) == []
    assert changes._values("looks_right", dict(edit, looks_right="maybe")) == ["looks_right 'maybe' is not yes or no"]
    assert changes._values("looks_right", dict(edit, check=" ")) == ["check is empty"]
    assert changes._describe(edit) == "check target_high|hobby|t99: looks right"
