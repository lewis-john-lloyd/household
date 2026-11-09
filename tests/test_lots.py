"""Tax lots and wash sales on a made-up stock plan, a made-up brokerage and an IRA."""

import pandas as pd
import pytest

from household import legacy_equity, lots
from household.parsers import identify, seed
from test_engine import session  # noqa: F401  (the fixture)

def usd(amount):
    """Money as the old sheet shows it, with its currency sign."""
    return chr(36) + " " + amount


SCHEDULE = pd.DataFrame([{"grant_id": "G1", "symbol": "ACME"}, {"grant_id": "G2", "symbol": "ACME"}])


def vest(day, shares, fmv, grant="G1"):
    return {"vest_date": day, "grant_id": grant, "award_date": "", "net_shares": shares, "fmv": fmv,
            "deposit_date": "", "doc_id": ""}


def sale(day, vested, shares, proceeds, basis="", grant="G1"):
    return {"sale_date": day, "grant_id": grant, "vest_date": vested, "shares": shares, "sale_price": "",
            "proceeds": proceeds, "basis": basis, "gain": "", "term": "", "doc_id": ""}


def run(vests, sales, splits=None, activity=None, accounts=None):
    acq, sold, notes = lots.equity_lots(pd.DataFrame(vests), pd.DataFrame(sales), SCHEDULE, {"ACME": "brk"})
    if activity is not None:
        more_acq, more_sold = lots.activity_lots(pd.DataFrame(activity), accounts, leave_out={"ACME"})
        acq, sold = pd.concat([acq, more_acq], ignore_index=True), pd.concat([sold, more_sold], ignore_index=True)
    form, still, more = lots.build(acq, sold, splits)
    return form, still, notes + more


def test_a_loss_is_disallowed_for_the_shares_replaced_and_moves_into_their_basis_and_holding_period():
    form, still, notes = run([vest("2001-01-10", 2, 50), vest("2001-02-05", 1, 40)],
                             [sale("2001-02-01", "2001-01-10", 2, 60), sale("2001-03-01", "2001-02-05", 1, 50)])
    first = form[form["sale_date"] == "2001-02-01"].iloc[0]
    # 2 shares sold at 30 that cost 50: a 40 loss; the vest 4 days later replaces one share, so half is a wash
    assert (first["proceeds"], first["basis"], first["code"], first["adjustment"], first["gain"]) == (60, 100, "W", 20, -20)
    assert first["replaced_by"] == "vest:G1:2001-02-05#1"
    later = form[form["sale_date"] == "2001-03-01"].iloc[0]
    # the replacement's basis is 40 plus the 20 disallowed, and it is held from 22 days (the sold shares' time)
    # before its vest
    assert (later["basis"], later["wash_carried"], later["gain"], later["code"]) == (60, 20, -10, "")
    assert (later["acquired"], later["holding_from"], later["term"]) == ("2001-02-05", "2001-01-14", "short_term")
    assert still.empty and notes == []


def test_the_replacement_is_another_lot_earliest_first_and_each_share_replaces_once():
    vests = [vest("2001-01-02", 1, 90), vest("2001-01-20", 1, 90), vest("2001-02-10", 1, 90), vest("2001-02-20", 1, 90)]
    sales = [sale("2001-02-01", "2001-01-02", 1, 80), sale("2001-02-01", "2001-01-20", 1, 80)]
    form, still, _ = run(vests, sales)
    # the first lot's loss goes to the lot sold beside it (acquired 12 days before, and still held when matched);
    # that lot's own loss, 20 now, goes to the next vest; the last vest replaces nothing
    assert form["replaced_by"].tolist() == ["vest:G1:2001-01-20#1", "vest:G1:2001-02-10#1"]
    assert form["adjustment"].tolist() == [10, 20] and form["gain"].tolist() == [0, 0]
    assert still.set_index("lot_id")["wash_added"].to_dict() == {"vest:G1:2001-02-10": 20, "vest:G1:2001-02-20": 0}


def test_a_split_restates_shares_so_a_lot_and_its_replacement_compare_and_8949_shows_shares_at_sale():
    splits = pd.DataFrame([{"symbol": "ACME", "date": "2001-01-20", "ratio": 2, "note": ""}])
    form, still, _ = run([vest("2001-01-10", 1, 80), vest("2001-01-25", 2, 40)], [sale("2001-01-15", "2001-01-10", 1, 60)],
                         splits)
    row = form.iloc[0]
    assert (row["shares"], row["adjustment"], row["gain"]) == (1, 20, 0)  # 2 shares after the split replace the 1
    assert still[["shares", "cost_per_share", "basis"]].values.tolist() == [[2, 50, 100]]


def test_a_replacement_in_an_ira_loses_the_loss_for_good():
    accounts = pd.DataFrame([{"account_id": "ira", "owner": "alex", "type": "ira", "tax_treatment": "pre_tax"},
                             {"account_id": "brk", "owner": "alex", "type": "brokerage", "tax_treatment": "taxable"}])
    activity = [{"account_id": "ira", "date": "2001-02-10", "kind": "buy", "symbol": "WILLX", "shares": 1, "price": 30,
                 "value": 30, "amount": -30},
                {"account_id": "brk", "date": "2001-01-02", "kind": "buy", "symbol": "WILLX", "shares": 1, "price": 50,
                 "value": 50, "amount": -50},
                {"account_id": "brk", "date": "2001-02-01", "kind": "sell", "symbol": "WILLX", "shares": -1, "price": 40,
                 "value": -40, "amount": 40}]
    form, still, _ = run([], [], activity=activity, accounts=accounts)
    row = form.iloc[0]
    assert (row["account_id"], row["code"], row["adjustment"], row["gain"]) == ("brk", "W", 10, 0)
    assert "lost" in row["note"] and still.empty  # nothing is added to the IRA's shares, which aren't lots here


def test_shares_held_more_than_a_year_are_long_term_counting_a_washed_lots_time():
    assert lots.term("2001-03-01", "2002-03-01") == "short_term" and lots.term("2001-03-01", "2002-03-02") == "long_term"
    form, _, _ = run([vest("2001-01-02", 1, 90), vest("2001-01-20", 1, 80)],
                     [sale("2001-01-10", "2001-01-02", 1, 70), sale("2002-01-15", "2001-01-20", 1, 99)])
    later = form.iloc[1]
    assert (later["holding_from"], later["term"]) == ("2001-01-12", "long_term")  # 8 days earlier than its vest


def test_a_sale_with_no_shares_left_is_noted_and_the_reported_basis_is_checked():
    form, _, notes = run([vest("2001-01-10", 1, 50)], [sale("2001-01-15", "2001-01-10", 2, 120, basis=90)])
    assert notes == ["lots: 2001-01-15 sale of ACME: 1.0000 shares with no lot to sell (vest:G1:2001-01-10)"]
    assert form["reported_basis"].tolist() == [45] and len(lots.check_reported(form)) == 1  # its lot cost 50


def test_each_year_beside_the_filed_return_and_the_open_lots_beside_the_latest_reading():
    form, still, _ = run([vest("2001-01-10", 2, 50), vest("2001-02-05", 1, 40)], [sale("2001-02-01", "2001-01-10", 2, 60)])
    facts = pd.DataFrame([{"year": "2001", "key": "capital_gain_short", "value": "-40"}])
    assert lots.year_lines(form, facts) == [
        "lots 2001: short-term gain -20.00, long-term 0.00; wash sales disallowed 20.00 (filed: short -40, long 0)"]
    holdings = pd.DataFrame([{"as_of": "2001-02-06", "account_id": "brk", "symbol": "ACME", "shares": "2"}])
    assert lots.holding_check(still, holdings, {"ACME"}) == [
        "lots: ACME open lots hold 1.000 shares but the 2001-02-06 reading has 2.000: a vest or sale is missing"]
    accounts = pd.DataFrame([{"account_id": "brk", "type": "brokerage", "tax_treatment": "taxable"}])
    assert lots.holders(holdings, accounts, {"ACME"}) == {"ACME": "brk"}


def test_the_old_sheets_vests_and_sales_become_the_plans_rows_with_its_lot_parts_added_back():
    grants = [["date", " fmv_vest ", "shares_gross", "shares_net", "shares_tax", "2000", "2001"],
              [36901, 50, 10, 6, 4, 10, ""], [36932, usd("40.00"), 20, 12, 8, 10, 10]]
    vested, notes = legacy_equity.vests(grants, {"2000": "G1", "2001": "G2"})
    assert vested[["vest_date", "grant_id", "net_shares", "fmv"]].values.tolist() == [
        ["2001-01-10", "G1", 6, 50], ["2001-02-10", "G1", 6, 40], ["2001-02-10", "G2", 6, 40]]
    header = ["(a)\nDescription", "(b)\nDate acquired", "Lot #", "Shares", " FMV ", "(c)\nDate Sold", "Sale $/share",
              " Unadjusted Basis ", "(d)\nProceeds"]
    rows = [header, ["", "", "1", "6", "", "", "", usd("300.00"), usd("270.00")],
            ["RS 1", "2001-01-10", "1a", "4", usd("50.00"), "2001-01-20", usd("45.00"), usd("200.00"), usd("180.00")],
            ["RS 1", "2001-01-10", "1b", "2", usd("50.00"), "2001-01-20", usd("45.00"), usd("100.00"), usd("90.00")],
            ["RS 2", "2001-02-10", "2a", "12", usd("40.00"), "2001-02-12", usd("41.00"), usd("480.00"), usd("492.00")]]
    sold, more = legacy_equity.sales(rows, vested)
    assert sold[["sale_date", "grant_id", "vest_date", "shares", "proceeds", "basis", "gain", "term"]].values.tolist() == [
        ["2001-01-20", "G1", "2001-01-10", 6, 270, 300, -30, "short_term"],
        ["2001-02-12", "G1", "2001-02-10", 6, 246, 240, 6, "short_term"],
        ["2001-02-12", "G2", "2001-02-10", 6, 246, 240, 6, "short_term"]]
    assert notes == [] and more == []
    have = pd.DataFrame([{"vest_date": "2001-02-10"}])
    assert legacy_equity.before(vested, have, "vest_date")["vest_date"].tolist() == ["2001-01-10"]


def test_stock_splits_arrive_as_a_seed_file():
    text = "symbol,date,ratio,note\nACME,2001-01-20,2,two for one\n"
    assert identify(text) is seed
    assert seed.parse(text).keys["stock_splits"] == ["symbol", "date"]
    assert lots.factor(seed.parse(text).tables["stock_splits"], "ACME", "2001-01-19") == pytest.approx(2)


def test_the_old_sheets_history_is_added_once_and_only_before_the_plans_own_records(session):  # noqa: F811
    from household import engine, schema, sheets
    from test_sheets import FakeSpreadsheet, FakeWorksheet
    wb = FakeSpreadsheet("taxes")
    wb.tabs = {"grants": FakeWorksheet("grants", [["date", "fmv_vest", "shares_net", "2000"], [36901, 50, 6, 10],
                                                  [36932, 40, 6, 10]]),
               "sales": FakeWorksheet("sales", [["(b) Date acquired", "Shares", "(c) Date Sold", "Sale $/share",
                                                 "Unadjusted Basis", "(d) Proceeds"],
                                                [36901, 6, 36911, 45, 300, 270]])}
    session.sheets.legacy = wb
    ledger, _, _ = engine.bootstrap(session, session.root)
    sheets.append_tables(ledger, [(schema.table(schema.LEDGER, "equity_vests"), pd.DataFrame([vest("2001-02-10", 6, 40)]))])
    first = engine.ingest_legacy_equity(session, session.root, "workbook", {"2000": "G1"}, "grants", "sales", log=None)
    again = engine.ingest_legacy_equity(session, session.root, "workbook", {"2000": "G1"}, "grants", "sales", log=None)
    assert (first["equity_vests"], first["equity_sales"], again["equity_vests"], again["equity_sales"]) == (1, 1, 0, 0)


def test_compute_writes_form_8949_and_the_open_lots(session):  # noqa: F811
    from household import engine, schema, sheets
    ledger, reports, _ = engine.bootstrap(session, session.root)
    accounts = pd.DataFrame([{"account_id": "brk", "institution": "Acme Widgets", "owner": "alex", "type": "brokerage",
                              "tax_treatment": "taxable", "opened": "", "closed": "", "last4": "", "expected_docs": "",
                              "max_gap_days": ""}])
    rows = [("accounts", accounts),
            ("vest_schedule", pd.DataFrame([{"grant_id": "G1", "award_date": "", "symbol": "ACME", "award_type": "",
                                             "grant_price": "", "expires": "", "vest_date": "", "shares": "", "doc_id": ""}])),
            ("equity_vests", pd.DataFrame([vest("2001-01-10", 2, 50), vest("2001-02-05", 1, 40)])),
            ("equity_sales", pd.DataFrame([sale("2001-02-01", "2001-01-10", 2, 60, basis=100)])),
            ("holdings", pd.DataFrame([{"as_of": "2001-02-06", "account_id": "brk", "symbol": "ACME", "source": "",
                                        "shares": "1", "value": "40", "doc_id": "d1"}]))]
    sheets.append_tables(ledger, [(schema.table(schema.LEDGER, n), df) for n, df in rows])
    said = []
    form, result = engine._lots(ledger, reports, engine._books(ledger), "2001-03-01", "r1", said.append)
    assert result == {"lot_parts_sold": 1, "wash_sales": 1, "open_lot_parts": 1} and form["code"].tolist() == ["W"]
    written = sheets.read_table(reports, schema.table(schema.REPORTS, "open_lots"))
    assert written[["lot_id", "account_id", "shares", "basis", "wash_added"]].values.tolist() == [
        ["vest:G1:2001-02-05", "brk", 1, 60, 20]]
    assert "lots 2001: short-term gain -20.00, long-term 0.00; wash sales disallowed 20.00" in said


def test_wash_sales_are_checked_only_for_the_symbols_named():
    accounts = pd.DataFrame([{"account_id": "brk", "owner": "alex", "type": "brokerage", "tax_treatment": "taxable"}])
    activity = [{"account_id": "brk", "date": "2001-01-02", "kind": "buy", "symbol": "WILLX", "shares": 1, "price": 50,
                 "value": 50, "amount": -50},
                {"account_id": "brk", "date": "2001-02-01", "kind": "sell", "symbol": "WILLX", "shares": -1, "price": 40,
                 "value": -40, "amount": 40},
                {"account_id": "brk", "date": "2001-02-10", "kind": "buy", "symbol": "WILLX", "shares": 1, "price": 30,
                 "value": 30, "amount": -30}]
    acq, sold = lots.activity_lots(pd.DataFrame(activity), accounts)
    form, _, _ = lots.build(acq, sold, wash_symbols=["ACME"])
    assert (form.iloc[0]["code"], form.iloc[0]["gain"]) == ("", -10)  # WILLX isn't checked: the loss is taken
    form, _, _ = lots.build(acq, sold)
    assert (form.iloc[0]["code"], form.iloc[0]["gain"]) == ("W", 0)
