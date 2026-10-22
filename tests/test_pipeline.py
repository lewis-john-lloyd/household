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
