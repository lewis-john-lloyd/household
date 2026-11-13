"""The views and ingest agree on what an edit is: its kinds and fields, the fields `was` must carry, and which
edits replace each other.

A change set is written by the views (web/src/edits.ts) and read by ingest (parsers/changes.py), two copies of
one contract in two languages. A kind added on one side and not the other is refused only when someone saves
one, so this test holds the two side by side. It needs Node and the built web app, like test_end_to_end.
"""

import json
import shutil
import subprocess
from itertools import combinations
from pathlib import Path

import pytest

from household.parsers import changes

EDITS_JS = Path(__file__).parent.parent / "web" / "dist" / "edits.js"
pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and EDITS_JS.exists()), reason="needs Node and the built web app"
)

# one made-up edit per kind, and a second that edits the same thing, or a different one, for each
SAMPLES = {
    "payee": [{"key": "ACME GROCER", "category": "food"}, {"key": "acme grocer", "category": "kids"}],
    "line": [
        {"txn_id": "t1", "field": "split", "value": "food -40; kids"},
        {"txn_id": "t1", "field": "category", "value": "food"},
        {"txn_id": "t1", "field": "note", "value": "x"},
    ],
    "budget_line": [
        {"line": "camp", "effective": "2026-01-01", "rule": "fixed", "category": "kids", "amount": "50"},
        {"line": "camp", "effective": "2026-02-01", "rule": "end"},
        {"line": "camp", "effective": "2026-06-01", "rule": "scheduled", "category": "kids", "amount": "90"},
        {"line": "camp", "effective": "2026-07-01", "rule": "scheduled", "category": "kids", "amount": "90"},
    ],
    "payee_rule": [{"pattern": "ACME", "category": "food", "effective": "2026-01-01"}],
    "category": [{"category": "gifts", "group": "family"}],
    "alias": [{"alias": "presents", "category": "gifts"}],
    "parameter": [{"key": "transfer_account", "value": "checking"}],
    "explanation": [{"jurisdiction": "federal", "stub_id": "s1", "note": "x"}],
    "not_duplicate": [{"txn_a": "t1", "txn_b": "t2"}, {"txn_a": "t2", "txn_b": "t1"}],
    "item": [{"item_id": "i1", "category": "kids"}],
    "job": [
        {"effective": "2026-01-01", "person": "alex", "employer": "Acme Widgets", "salary": "70000"},
        {"effective": "2026-01-01", "person": "alex", "employer": "Acme Widgets", "salary": "80000"},
    ],
    "subscription": [{"key": "PAGE TURNER BOOKS", "subscription": "yes"}],
    "recurring_merge": [{"key": "PAGE TURNER BOOKS", "same_as": "QUILL & INK"}],
    "recurring_name": [{"key": "PAGE TURNER BOOKS", "name": "books"}],
    "recurring_line": [{"key": "PAGE TURNER BOOKS", "line": "books"}],
    "recipient": [{"key": "WILLOW ST", "deductible": "yes"}, {"key": "willow st", "deductible": "no"}],
    "looks_right": [
        {"check": "target_high|hobby|t9", "looks_right": "yes"},
        {"check": "target_high|hobby|t9", "looks_right": "no"},
        {"check": "price_changed|WILLOW ST|2001-01-03|9.00", "looks_right": "yes"},
    ],
    "estimate": [
        {"year": "2026", "key": "charity", "value": "60"},
        {"year": "2026", "key": "charity", "value": "70", "note": "x"},
        {"year": "2026", "key": "wi_earned_income", "person": "alex", "value": "70"},
        {"year": "2026", "key": "wi_earned_income", "person": "sam", "value": "70"},
    ],
    "security": [
        {"symbol": "ACME", "asset_class": "SDT"},
        {"symbol": "ACME", "asset_class": "SDT 60, BT 40"},
        {"symbol": "WILLOW", "asset_class": "BT"},
    ],
}


def views():
    """The views' side of the contract, from the built edits.js."""
    script = (
        EDITS_JS.read_text(encoding="utf-8")
        + "\nconst samples = JSON.parse(process.argv[1]);\n"
        + "const targets = {};\n"
        + "for (const [kind, list] of Object.entries(samples))\n"
        + "  targets[kind] = list.map((e) => editTarget({ kind, ...e }));\n"
        + "process.stdout.write(JSON.stringify({ kinds: EDIT_KINDS, was: WAS_FIELDS, targets }));\n"
    )
    done = subprocess.run(
        ["node", "-e", script, json.dumps(SAMPLES)], capture_output=True, text=True, check=True, timeout=60
    )
    return json.loads(done.stdout)


def test_both_sides_know_the_same_kinds_and_fields():
    side = views()
    ingest = {k: [sorted(required), sorted(optional)] for k, (required, optional) in changes.KINDS.items()}
    page = {k: [sorted(required), sorted(optional)] for k, (required, optional) in side["kinds"].items()}
    assert page == ingest
    assert {k: list(v) for k, v in changes.WAS_FIELDS.items()} == side["was"]
    assert set(SAMPLES) == set(changes.KINDS), "add a sample for the new kind"


def test_both_sides_agree_on_which_edits_replace_each_other():
    side = views()
    for kind, samples in SAMPLES.items():
        for a in samples:  # every kind has a target on both sides, even one with a single sample
            assert changes._target({"kind": kind, **a}), (kind, a)
        for (i, a), (j, b) in combinations(enumerate(samples), 2):
            same_here = side["targets"][kind][i] == side["targets"][kind][j]
            same_there = changes._target({"kind": kind, **a}) == changes._target({"kind": kind, **b})
            assert same_here == same_there, (kind, a, b)
