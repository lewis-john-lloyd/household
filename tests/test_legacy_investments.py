"""The legacy investments workbook: its funds tab and account tabs, on made-up rows."""

from datetime import date

import pandas as pd
import pytest

from household import engine, legacy_investments as li, schema, sheets
from test_engine import session  # noqa: F401  (the fixture)
from test_sheets import FakeSpreadsheet, FakeWorksheet


def serial(y, m, d):
    return (date(y, m, d) - date(1899, 12, 30)).days


FUNDS = [["Symbol", "Title", "ER", "Type"],
         ["cash", "", "", "BA"],
         ["ACMEX", "Acme Widgets Index Fund", 0.0011, "SDT"],
         ["WILLX", "Willow St Bond Fund", "#N/A", "BT"],
         ["ACMEX", "Acme Widgets Index Fund", 0.0011, "SDS"],
         ["Quill & Ink plan fund", "", "", "SIT"]]

# shares and value per fund, cash at the end; the first row has only a value, before shares were kept
IRA = [["", "", "ACMEX", " SDT ", "WILLX", " BT ", "cash", " BA "],
       ["Date", " Value ", "Shares", " Val ", "Shares", " Val ", "Shares", " Val "],
       [serial(2001, 1, 12), 80, "", "", "", 80, "", ""],
       [serial(2001, 3, 3), 95, 2.5, 50, 1.25, 40, 5, 5],
       [serial(2001, 4, 1), "", "", "", "", "", "", ""],
       ["", ""]]

# a plan's target-date fund, kept as its share of each underlying fund's value
PLAN = [["", "", "", "ACMEX", " SDT ", "WILLX", " BT "],
        ["Date", " Value ", "Fund", "Per", " Val ", "Per", " Val "],
        [serial(2001, 3, 3), 100, "TR50", 0.6, 60, 0.4, 40],
        [serial(2001, 4, 1), 90, "", 5.5, 55, 3.5, 35]]  # later, the funds held directly: shares in the Per column

# a balance under a label, and one value per child
RESERVE = [["", "", "Cash", " BA "], ["Date", " Value ", "Reserve", " Val "], [serial(2001, 3, 3), 70, "", 70]]
COLLEGE = [["", "", " alex ", " sam "], ["Date", " Value ", " Val ", " Val "], [serial(2001, 3, 3), 90, 50, 40]]


def test_the_funds_tab_becomes_securities_quoted_by_their_own_symbol():
    df, notes = li.securities(FUNDS)
    assert df[["symbol", "asset_class", "expense_ratio", "quote"]].values.tolist() == [
        ["cash", "BA", "", ""], ["ACMEX", "SDT", 0.0011, "ACMEX"], ["WILLX", "BT", "", "WILLX"]]
    assert notes == ["funds: ACMEX listed twice, the first kept", "funds: 'Quill & Ink plan fund' is not a symbol, left out"]


def test_an_account_tab_reads_shares_and_value_per_fund_on_each_date():
    df, notes = li.holdings(IRA, "a_ira")
    assert df.drop(columns="source").values.tolist() == [
        ["2001-01-12", "a_ira", "WILLX", "", 80],  # a value with no shares yet
        ["2001-03-03", "a_ira", "ACMEX", 2.5, 50], ["2001-03-03", "a_ira", "WILLX", 1.25, 40],
        ["2001-03-03", "a_ira", "cash", 5, 5]]
    assert notes == [] and set(df["source"]) == {""}


def test_a_tab_that_is_one_money_source_of_a_plan_keeps_its_readings_apart():
    pre, _ = li.holdings(IRA, "a_401k", "pretax")
    roth, _ = li.holdings(IRA, "a_401k", "roth")
    df = li.merge([pre, roth])
    on = df[(df["as_of"] == "2001-03-03") & (df["symbol"] == "ACMEX")]
    assert on[["source", "shares", "value"]].values.tolist() == [["pretax", 2.5, 50], ["roth", 2.5, 50]]


def test_a_plan_fund_a_label_and_a_value_per_name_read_as_values():
    plan, _ = li.holdings(PLAN, "a_plan")
    assert plan[["as_of", "symbol", "shares", "value"]].values.tolist() == [
        ["2001-03-03", "ACMEX", "", 60], ["2001-03-03", "WILLX", "", 40],
        ["2001-04-01", "ACMEX", 5.5, 55], ["2001-04-01", "WILLX", 3.5, 35]]
    reserve, _ = li.holdings(RESERVE, "a_cash")
    assert reserve[["symbol", "value"]].values.tolist() == [["cash", 70]]
    college, _ = li.holdings(COLLEGE, "a_529")
    assert college[["symbol", "value"]].values.tolist() == [["", 50], ["", 40]]  # names are not symbols


def test_tabs_that_map_to_one_account_add_up_by_date_and_fund():
    ira, _ = li.holdings(IRA, "a_401k")
    plan, _ = li.holdings(PLAN, "a_401k")
    college, _ = li.holdings(COLLEGE, "a_529")
    df = li.merge([ira, plan, college])
    on = df[df["as_of"] == "2001-03-03"].set_index(["account_id", "symbol"])
    assert (on.at[("a_401k", "ACMEX"), "shares"], on.at[("a_401k", "ACMEX"), "value"]) == (2.5, 110)
    assert on.at[("a_401k", "WILLX"), "value"] == 80 and on.at[("a_529", ""), "value"] == 90
    assert set(df["doc_id"]) == {li.LEGACY_DOC} and list(df.columns) == list(schema.table(schema.LEDGER, "holdings").columns)


def test_a_row_whose_date_cant_be_read_is_noted_and_left_out():
    rows = IRA[:2] + [["someday", 10, 1, 10]]
    df, notes = li.holdings(rows, "a_ira")
    assert df.empty and notes == ["a_ira: row 3: 'someday' is not a date, left out"]


def _workbook():
    wb = FakeSpreadsheet("investments")
    wb.tabs = {"Equities": FakeWorksheet("Equities", FUNDS), "ira": FakeWorksheet("ira", IRA),
               "plan": FakeWorksheet("plan", PLAN), "other": FakeWorksheet("other", RESERVE)}
    return wb


def _ledger_with_accounts(session, ids):  # noqa: F811  (the session fixture, by name)
    ledger, _, _ = engine.bootstrap(session, session.root)
    accounts = pd.DataFrame([{"account_id": a, "institution": "Acme Widgets", "owner": "alex", "type": "retirement",
                              "tax_treatment": "pre_tax"} for a in ids])
    sheets.append_tables(ledger, [(schema.table(schema.LEDGER, "accounts"), accounts.reindex(
        columns=schema.table(schema.LEDGER, "accounts").columns, fill_value=""))])
    return ledger


def test_the_migration_adds_funds_and_readings_once(session):  # noqa: F811
    session.sheets.legacy = _workbook()
    ledger = _ledger_with_accounts(session, ["a_ira", "a_401k"])
    out = engine.ingest_legacy_investments(session, session.root, "workbook", {"ira": "a_ira", "plan": "a_401k"}, log=None)
    assert (out["securities"], out["holdings"], out["dates"]) == (3, 8, 3)
    held = sheets.read_table(ledger, schema.table(schema.LEDGER, "holdings"))
    assert set(held["account_id"]) == {"a_ira", "a_401k"} and "other" not in set(held["account_id"])
    again = engine.ingest_legacy_investments(session, session.root, "workbook", {"ira": "a_ira", "plan": "a_401k"}, log=None)
    assert (again["securities"], again["holdings"], again["already_held"]) == (0, 0, 8)


def test_the_migration_refuses_an_account_the_ledger_doesnt_have(session):  # noqa: F811
    session.sheets.legacy = _workbook()
    _ledger_with_accounts(session, ["a_ira"])
    with pytest.raises(ValueError, match="no such accounts in the ledger: a_roth"):
        engine.ingest_legacy_investments(session, session.root, "workbook", {"ira": "a_roth"}, log=None)


def test_a_tab_that_skipped_a_date_another_tab_of_its_account_recorded_carries_its_last_reading():
    pre = [IRA[0], IRA[1], [serial(2001, 1, 12), 50, 1, 50, "", "", "", ""], [serial(2001, 2, 9), 55, 1, 55, "", "", "", ""],
           [serial(2001, 3, 3), 60, 1, 60, "", "", "", ""]]
    old = [IRA[0], IRA[1], [serial(2001, 1, 12), 20, "", "", 2, 20, "", ""], [serial(2001, 3, 3), 30, "", "", 2, 30, "", ""],
           [serial(2001, 4, 1), 0, "", "", "", "", "", ""]]
    a, _ = li.holdings(pre, "a_401k")
    b, _ = li.holdings(old, "a_401k")
    df = li.merge([a, b])
    totals = df.groupby("as_of")["value"].sum().to_dict()
    assert totals == {"2001-01-12": 70, "2001-02-09": 75, "2001-03-03": 90}  # 2001-02-09 carries the old tab's 20



def test_with_nothing_new_the_migration_writes_nothing_and_backs_nothing_up(session, monkeypatch):  # noqa: F811
    session.sheets.legacy = _workbook()
    _ledger_with_accounts(session, ["a_ira"])
    engine.ingest_legacy_investments(session, session.root, "workbook", {"ira": "a_ira"}, log=None)
    snapshots = []
    monkeypatch.setattr(engine.backup, "snapshot_ledger", lambda *a, **k: snapshots.append(a))
    again = engine.ingest_legacy_investments(session, session.root, "workbook", {"ira": "a_ira"}, log=None)
    assert (again["holdings"], again["securities"], snapshots) == (0, 0, [])


def test_a_row_dated_before_the_one_above_it_is_noted():
    emptied = IRA[:4] + [[serial(2000, 4, 2), 0, 0, 0, 0, 0, 0, 0]]  # a year typed one too low
    df, notes = li.holdings(emptied, "a_ira")
    assert notes == ["a_ira: row 5: 2000-04-02 comes before the row above it (2001-03-03): is its year a typo?"]
