"""An account renamed by a setting: the ledger keeps its old id, compute and the views read the new one, and a file
named with either one finds it."""

import pandas as pd
import pytest

from household import engine, ingest, settings
from household.parsers.common import ParseError


def params(**rows):
    return pd.DataFrame([{"year": "", "jurisdiction": "household", "key": k, "value": v, "source": ""}
                         for k, v in rows.items()])


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
    holdings = pd.DataFrame([{"as_of": "2001-01-31", "account_id": "a_plan_old", "symbol": "VFIAX", "value": 10},
                             {"as_of": "2001-01-31", "account_id": "acme_brokerage", "symbol": "VFIAX", "value": 20}])
    monkeypatch.setattr(engine.sheets, "read_table", lambda ledger, t: holdings.copy())
    monkeypatch.setattr(engine, "_RENAMED", {"a_plan_old": "a_plan_acme"})
    assert list(engine._read(None, "holdings")["account_id"]) == ["a_plan_acme", "acme_brokerage"]


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
    holdings = pd.DataFrame([
        {"as_of": "2001-01-31", "account_id": "a_ira", "symbol": "VFIAX", "value": 90, "doc_id": "legacy:investments"},
        {"as_of": "2005-01-31", "account_id": "a_ira", "symbol": "VFIAX", "value": 5, "doc_id": "positions_2005"}])
    monkeypatch.setattr(engine.sheets, "read_table", lambda ledger, t: holdings.copy())
    monkeypatch.setattr(engine, "_LEGACY_MOVED", {"a_ira": "a_ira_rollover"})
    monkeypatch.setattr(engine, "_RENAMED", {"a_ira": "a_ira_now"})
    assert list(engine._read(None, "holdings")["account_id"]) == ["a_ira_rollover", "a_ira_now"]
    assert list(engine._read(None, "accounts")["account_id"]) == ["a_ira_now", "a_ira_now"]  # only holdings move


def test_every_stage_starts_from_the_ledger_as_written(monkeypatch):
    monkeypatch.setattr(engine, "_RENAMED", {"a_ira": "a_ira_now"})
    monkeypatch.setattr(engine, "_LEGACY_MOVED", {"a_ira": "a_ira_rollover"})
    monkeypatch.setattr(engine.sheets, "open_or_create", lambda *a: (None, False))
    monkeypatch.setattr(engine.sheets, "ensure_tables", lambda *a: [])
    session = type("S", (), {"sheets": None})()
    engine.bootstrap(session, "folder")
    assert engine._RENAMED == {} and engine._LEGACY_MOVED == {}
