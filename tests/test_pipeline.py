"""Legacy parsing, ingest identity and compute, end to end on synthetic fixtures."""

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from household import compute, ingest, legacy, schema

FIXTURES = Path(__file__).parent / "fixtures"


def rows(name):
    """Read a fixture as Sheets would return it unformatted: date serials as ints."""
    with open(FIXTURES / name, newline="") as f:
        out = list(csv.reader(f))
    return [[int(c) if c.strip().isdigit() and len(c.strip()) == 5 else c for c in r] for r in out]


@pytest.fixture
def legacy_df():
    return legacy.parse_data_rows(rows("legacy_data.csv"))


def test_parse_dates_in_every_format():
    assert legacy.parse_date(" 2001-Jan-05 ") == date(2001, 1, 5)
    assert legacy.parse_date("2001-01-17") == date(2001, 1, 17)
    assert legacy.parse_date("Jan 30, 2001") == date(2001, 1, 30)
    assert legacy.parse_date(36925) == date(2001, 2, 3)


def test_parse_money():
    assert legacy.parse_money("$ 1,057.39") == 1057.39  # leak-guard: allow (synthetic)
    assert legacy.parse_money("($ 5.00)") == -5.0  # leak-guard: allow (synthetic)
    assert legacy.parse_money("-12.50") == -12.5
    assert legacy.parse_money("$ -") == 0.0
    assert legacy.parse_money(42.1) == 42.1


def test_parse_data_rows_strips_and_skips_blanks(legacy_df):
    assert len(legacy_df) == 6
    assert legacy_df.loc[0, "category"] == "food"
    assert legacy_df.loc[0, "payee_raw"] == "GROCER ONE"
    assert legacy_df["cost"].sum() == pytest.approx(1161.49)


def test_txn_ids_are_stable_and_distinguish_duplicates(legacy_df):
    a = ingest.legacy_to_transactions(legacy_df, recorded_at="t")
    b = ingest.legacy_to_transactions(legacy_df, recorded_at="t")
    assert list(a["txn_id"]) == list(b["txn_id"])
    assert a["txn_id"].is_unique  # the two identical fuel rows get different ids
    assert (a["amount"] < 0).sum() == 5  # outflows are negative


def test_reingest_appends_nothing(legacy_df):
    txns = ingest.legacy_to_transactions(legacy_df, recorded_at="t")
    assert ingest.new_rows(txns, txns["txn_id"]).empty
    assert len(ingest.new_rows(txns, txns["txn_id"].iloc[:2])) == len(txns) - 2


def test_ledger_rows_match_declared_columns(legacy_df):
    txns = ingest.legacy_to_transactions(legacy_df, recorded_at="t")
    assert set(schema.table(schema.LEDGER, "transactions").columns) <= set(txns.columns)


def test_monthly_actuals_match_legacy_rollup(legacy_df):
    txns = ingest.legacy_to_transactions(legacy_df, recorded_at="t")
    actuals = compute.monthly_actuals(txns)
    check = compute.compare_to_legacy(actuals, legacy.parse_rollup_rows(rows("legacy_rollup.csv")))
    assert check["match"].all(), check


def test_compare_reports_a_mismatch(legacy_df):
    txns = ingest.legacy_to_transactions(legacy_df.iloc[1:], recorded_at="t")
    check = compute.compare_to_legacy(compute.monthly_actuals(txns),
                                      legacy.parse_rollup_rows(rows("legacy_rollup.csv")))
    bad = check[~check["match"]]
    assert list(bad["category"]) == ["TOTAL", "food"]
    assert list(bad["diff"]) == [pytest.approx(-42.10)] * 2


def test_uncategorized_spend_shows_only_in_the_total(legacy_df):
    df = legacy_df.copy()
    df.loc[0, "category"] = ""
    check = compute.compare_to_legacy(
        compute.monthly_actuals(ingest.legacy_to_transactions(df, recorded_at="t")),
        legacy.parse_rollup_rows(rows("legacy_rollup.csv")))
    bad = check[~check["match"]]
    assert set(bad["category"]) == {"", "food"}  # TOTAL still matches


def test_corrections_supersede_latest_wins(legacy_df):
    txns = ingest.legacy_to_transactions(legacy_df, recorded_at="t")
    first = txns["txn_id"].iloc[0]
    corrections = pd.DataFrame([
        {"txn_id": first, "field": "category", "value": "dining", "recorded_at": "2026-01-01"},
        {"txn_id": first, "field": "category", "value": "groceries", "recorded_at": "2026-02-01"},
    ])
    effective = compute.effective_transactions(txns, corrections)
    assert effective.loc[effective["txn_id"] == first, "category"].item() == "groceries"
    assert txns.loc[txns["txn_id"] == first, "category"].item() == "food"  # input untouched


def test_schema_tables_are_well_formed():
    for t in schema.LEDGER + schema.REPORTS:
        assert len(set(t.columns)) == len(t.columns), t.name


def _txns(rows, doc):
    records = pd.DataFrame(rows, columns=["date", "amount", "payee_raw"])
    return ingest.records_to_transactions(records, "card", "csv", doc_id=doc, recorded_at="t")


COFFEE = ("2001-03-02", -4.5, "CAFE ONE")


def test_overlapping_files_keep_true_repeats_and_drop_shared_rows():
    jan = _txns([("2001-03-01", -20.0, "GROCER"), COFFEE, COFFEE], "doc1")
    feb = _txns([COFFEE, COFFEE, ("2001-03-03", -9.0, "BOOKS")], "doc2")
    ledger = jan
    added = ingest.new_rows(feb, ledger["txn_id"])
    assert list(added["payee_raw"]) == ["BOOKS"]  # both coffees already there
    third = _txns([COFFEE, COFFEE, COFFEE], "doc3")
    assert len(ingest.new_rows(third, pd.concat([jan, added])["txn_id"])) == 1  # a real third


def test_possible_duplicates_pairs_pending_and_posted_across_files():
    a = _txns([("2001-03-01", -42.0, "HARDWARE STORE #12"), ("2001-03-05", -3.0, "PARKING")], "doc1")
    b = _txns([("2001-03-03", -42.0, "HARDWARE STORE 12 SPRINGFIELD"), ("2001-03-20", -3.0, "PARKING")], "doc2")
    found = compute.possible_duplicates(pd.concat([a, b]))
    assert len(found) == 1 and found.loc[0, "amount"] == -42.0
    assert {found.loc[0, "doc_a"], found.loc[0, "doc_b"]} == {"doc1", "doc2"}
    key = found.loc[0, "txn_a"] + "|" + found.loc[0, "txn_b"]
    assert compute.possible_duplicates(pd.concat([a, b]), dismissed={key}).empty


def test_same_file_repeats_are_not_flagged():
    assert compute.possible_duplicates(_txns([COFFEE, COFFEE], "doc1")).empty


def test_void_correction_removes_and_restores(legacy_df):
    txns = ingest.legacy_to_transactions(legacy_df, recorded_at="t")
    first = txns["txn_id"].iloc[0]
    void = pd.DataFrame([{"txn_id": first, "field": "void", "value": "TRUE", "recorded_at": "1"}])
    assert first not in set(compute.effective_transactions(txns, void)["txn_id"])
    undo = pd.concat([void, pd.DataFrame([{"txn_id": first, "field": "void", "value": "FALSE",
                                           "recorded_at": "2"}])])
    assert first in set(compute.effective_transactions(txns, undo)["txn_id"])
