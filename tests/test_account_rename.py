"""An account renamed by a setting: the ledger keeps its old id, compute and the views read the new one, and a file
named with either one finds it."""

import pandas as pd
import pytest

from household import engine, ingest, ledgerio, settings
from household.parsers.common import ParseError
from test_engine import session  # noqa: F401  (the fixture)


def params(**rows):
    return pd.DataFrame(
        [{"year": "", "jurisdiction": "household", "key": k, "value": v, "source": ""} for k, v in rows.items()]
    )


ACCOUNTS = pd.DataFrame([{"account_id": "a_plan_old", "last4": ""}, {"account_id": "acme_brokerage", "last4": ""}])


def test_the_setting_gives_old_and_new_ids_and_a_bad_one_is_noted():
    got, notes = engine.renamed_accounts(params(**{"account_renamed:a_plan_old": "a_plan_acme"}))
    assert got == {"a_plan_old": "a_plan_acme"} and notes == []
    got, notes = engine.renamed_accounts(params(**{"account_renamed:a_plan_old": "Acme Plan"}))
    assert got == {} and "isn't an account id" in notes[0]
    got, notes = engine.renamed_accounts(params(**{"account_renamed:x": "same", "account_renamed:y": "same"}))
    assert got == {} and "same new name" in notes[0]
    assert settings.check(params(**{"account_renamed:a_plan_old": "Acme Plan"}))


def test_compute_reads_every_account_column_under_the_new_name(monkeypatch):
    holdings = pd.DataFrame(
        [
            {"as_of": "2001-01-31", "account_id": "a_plan_old", "symbol": "VFIAX", "value": 10},
            {"as_of": "2001-01-31", "account_id": "acme_brokerage", "symbol": "VFIAX", "value": 20},
        ]
    )
    monkeypatch.setattr(ledgerio.sheets, "read_table", lambda ledger, t: holdings.copy())
    view = ledgerio.LedgerView(None, ledgerio.LedgerReading(renamed={"a_plan_old": "a_plan_acme"}))
    assert list(engine.read_ledger(view, "holdings")["account_id"]) == ["a_plan_acme", "acme_brokerage"]
    assert list(engine.read_ledger(None, "holdings")["account_id"]) == ["a_plan_old", "acme_brokerage"]  # as written


def test_a_file_named_with_the_new_name_finds_the_account_under_its_old_id():
    named = ingest.named_accounts(ACCOUNTS, params(**{"account_renamed:a_plan_old": "a_plan_acme"}))
    assert ingest.resolve_account("", "a_plan_acme_q1_2001.pdf", named)["account_id"] == "a_plan_old"
    assert ingest.resolve_account("", "a_plan_old_q1_2001.pdf", named)["account_id"] == "a_plan_old"
    with pytest.raises(ParseError, match="no account"):
        ingest.resolve_account("", "a_plan_acme_q1_2001.pdf", ACCOUNTS)  # without the setting it is unknown


def test_the_old_workbooks_readings_of_an_account_can_belong_to_another():
    accounts = {"a_ira", "a_ira_rollover"}
    got, notes = engine.legacy_moves(params(**{"legacy_account:a_ira": "a_ira_rollover"}), accounts)
    assert got == {"a_ira": "a_ira_rollover"} and notes == []
    got, notes = engine.legacy_moves(params(**{"legacy_account:a_ira": "a_ira_old"}), accounts)
    assert got == {} and "no account a_ira_old" in notes[0]
    assert settings.check(params(**{"legacy_account:a_ira": "Old IRA"}))


def test_only_the_old_workbooks_readings_move_and_the_rest_take_the_accounts_new_name(monkeypatch):
    holdings = pd.DataFrame(
        [
            {
                "as_of": "2001-01-31",
                "account_id": "a_ira",
                "symbol": "VFIAX",
                "value": 90,
                "doc_id": "legacy:investments",
            },
            {"as_of": "2005-01-31", "account_id": "a_ira", "symbol": "VFIAX", "value": 5, "doc_id": "positions_2005"},
        ]
    )
    monkeypatch.setattr(ledgerio.sheets, "read_table", lambda ledger, t: holdings.copy())
    reading = ledgerio.LedgerReading(renamed={"a_ira": "a_ira_now"}, legacy_moved={"a_ira": "a_ira_rollover"})
    view = ledgerio.LedgerView(None, reading)
    assert list(engine.read_ledger(view, "holdings")["account_id"]) == ["a_ira_rollover", "a_ira_now"]
    assert list(engine.read_ledger(view, "accounts")["account_id"]) == ["a_ira_now", "a_ira_now"]  # only holdings move


def test_the_reading_is_worked_out_from_the_ledger_and_a_bad_setting_is_noted(monkeypatch):
    tables = {
        "parameters": params(
            **{
                "account_renamed:a_ira": "a_ira_now",
                "account_renamed:gone": "x",
                "account_renamed:a_plan": "a_ira_rollover",
                "legacy_account:a_ira": "a_ira_rollover",
            }
        ),
        "accounts": pd.DataFrame({"account_id": ["a_ira", "a_ira_rollover", "a_plan"]}),
        "activity_corrections": pd.DataFrame(columns=["activity_id", "field", "value"]),
        "investment_activity": pd.DataFrame(columns=["activity_id"]),
    }
    monkeypatch.setattr(ledgerio.sheets, "read_table", lambda ledger, t: tables[t.name].copy())
    reading, problems = ledgerio.LedgerReading.of(None)
    assert reading.renamed == {"a_ira": "a_ira_now"} and reading.legacy_moved == {"a_ira": "a_ira_rollover"}
    assert any("account_renamed:gone names no account" in p for p in problems)
    assert any("a_ira_rollover is already an account" in p for p in problems)
    assert reading.notes()[0] == "accounts: read as renamed: a_ira as a_ira_now"


def test_a_view_reads_its_spreadsheet():
    sheet = type("Sheet", (), {"title": "ledger", "worksheet": lambda self, name: f"tab {name}"})()
    view = ledgerio.LedgerView(sheet, ledgerio.LedgerReading())
    assert view.title == "ledger" and view.worksheet("holdings") == "tab holdings"


def test_a_correction_changes_an_activity_rows_kind_as_compute_reads_it(monkeypatch):
    activity = pd.DataFrame(
        [
            {"activity_id": "a1", "account_id": "a_plan", "kind": "contribution", "amount": 90},
            {"activity_id": "a2", "account_id": "a_plan", "kind": "contribution", "amount": 5},
        ]
    )
    fixes = pd.DataFrame(
        [
            {"activity_id": "a1", "field": "kind", "value": "contribution"},
            {"activity_id": "a1", "field": "kind", "value": "transfer_in"},  # the later one is in force
            {"activity_id": "zz", "field": "kind", "value": "transfer_in"},
            {"activity_id": "a2", "field": "kind", "value": "gift"},
        ]
    )
    got, notes = engine.activity_fixes(fixes, activity)
    assert got == {"a1": {"kind": "transfer_in"}}
    assert "zz, which the ledger doesn't have" in notes[0] and "'gift' isn't a kind" in notes[1]
    monkeypatch.setattr(ledgerio.sheets, "read_table", lambda ledger, t: activity.copy())
    view = ledgerio.LedgerView(None, ledgerio.LedgerReading(activity_fixes=got))
    assert list(engine.read_ledger(view, "investment_activity")["kind"]) == ["transfer_in", "contribution"]


def test_compute_reads_through_the_reading_and_ingest_as_written(session):  # noqa: F811
    from freezegun import freeze_time
    from test_fidelity_positions import FILE as POSITIONS
    from test_golden import GOLDEN_FILES

    for name in ("accounts_seed_golden.csv", "securities_seed_golden.csv"):
        session._drive.add(name, session.box, "text/csv", GOLDEN_FILES[name].encode())
    session._drive.add(
        "parameters_seed_renamed.csv",
        session.box,
        "text/csv",
        b"year,jurisdiction,key,value,source\n,household,account_renamed:a_roth,a_roth_new,seed\n",
    )
    session._drive.add("portfolio_positions.csv", session.box, "text/csv", POSITIONS.encode())
    with freeze_time("2026-11-09 12:00:00"):
        engine.ingest_inbox(session, session.root, run_id="r1")
        engine.compute_all(session, session.root, run_id="r2")
    files = session.sheets.files
    reports = {n: t.rows for n, t in files["reports"].tabs.items()}
    values = reports["investment_values"]
    accounts = {r[values[0].index("account_id")] for r in values[1:]}
    assert "a_roth_new" in accounts and "a_roth" not in accounts
    holdings = files["ledger"].tabs["holdings"].rows
    assert "a_roth" in {r[holdings[0].index("account_id")] for r in holdings[1:]}  # the ledger keeps the old id
