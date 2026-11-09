"""Prices asked of GOOGLEFINANCE through the ledger's price_requests tab, on made-up funds."""

from datetime import date

import pandas as pd
import pytest

from household import engine, prices, schema, sheets
from household.legacy_investments import LEGACY_DOC
from test_engine import session  # noqa: F401  (the fixture)

SECURITIES = pd.DataFrame([
    {"symbol": "ACMEX", "quote": "ACMEX"}, {"symbol": "WILLX", "quote": "MUTF:WILLX"},
    {"symbol": "cash", "quote": ""}, {"symbol": "QUILX", "quote": '=IMPORTXML("x")'}])
HOLDINGS = pd.DataFrame([
    {"as_of": "2001-01-12", "account_id": "a_ira", "symbol": "ACMEX", "doc_id": LEGACY_DOC},
    {"as_of": "2001-03-03", "account_id": "a_ira", "symbol": "ACMEX", "doc_id": LEGACY_DOC},
    {"as_of": "2001-05-20", "account_id": "a_ira", "symbol": "ACMEX", "doc_id": "positions_2001"}])
NO_ACTIVITY = pd.DataFrame(columns=["date"])
TODAY = date(2001, 6, 15)


def test_month_ends_come_after_a_date_up_to_another():
    assert [d.isoformat() for d in prices.month_ends(date(2000, 12, 20), date(2001, 3, 31))] == [
        "2000-12-31", "2001-01-31", "2001-02-28", "2001-03-31"]
    assert prices.month_ends(date(2001, 1, 31), date(2001, 2, 27)) == []


def test_prices_are_wanted_on_each_legacy_date_then_each_month_end_after_the_last():
    assert prices.wanted_dates(HOLDINGS, NO_ACTIVITY, TODAY) == [
        "2001-01-12", "2001-03-03", "2001-03-31", "2001-04-30", "2001-05-31"]


def test_with_no_legacy_readings_month_ends_start_with_the_first_holding_or_activity():
    activity = pd.DataFrame([{"date": "2001-04-10"}])
    assert prices.wanted_dates(HOLDINGS[HOLDINGS["doc_id"] != LEGACY_DOC], activity, TODAY) == ["2001-04-30", "2001-05-31"]
    assert prices.wanted_dates(HOLDINGS.iloc[0:0], NO_ACTIVITY, TODAY) == []


def test_requests_skip_what_is_held_and_ask_newest_first_up_to_a_limit():
    held = pd.DataFrame([{"symbol": "ACMEX", "date": "2001-05-31"}, {"symbol": "WILLX", "date": "2001-01-12"}])
    ask, wanted = prices.requests(SECURITIES, HOLDINGS, NO_ACTIVITY, held, TODAY)
    assert wanted == 8 and ask.values.tolist()[:3] == [
        ["WILLX", "MUTF:WILLX", "2001-05-31"], ["ACMEX", "ACMEX", "2001-04-30"], ["WILLX", "MUTF:WILLX", "2001-04-30"]]
    assert "QUILX" not in set(ask["symbol"]) and prices.quotes(SECURITIES)[1] == ["QUILX"]
    few, wanted = prices.requests(SECURITIES, HOLDINGS, NO_ACTIVITY, held, TODAY, limit=2)
    assert len(few) == 2 and wanted == 8


def test_answers_are_read_as_prices_a_none_is_kept_and_the_rest_wait():
    typed = pd.DataFrame([
        {"symbol": "ACMEX", "quote": "ACMEX", "date": "2001-03-03", "result": "2001-03-02 12.5"},
        {"symbol": "ACMEX", "quote": "ACMEX", "date": "2001-01-12", "result": "none"},
        {"symbol": "WILLX", "quote": "WILLX", "date": "2001-03-03", "result": "Loading..."},
        {"symbol": "WILLX", "quote": "WILLX", "date": "2001-01-12", "result": "#N/A"},
        {"symbol": "WILLX", "quote": "WILLX", "date": "2001-03-31", "result": "Date Close"}])
    done, waiting = prices.harvest(typed)
    assert done.values.tolist() == [["ACMEX", "2001-03-03", "2001-03-02", 12.5, "googlefinance"],
                                    ["ACMEX", "2001-01-12", "", "", "googlefinance"],
                                    ["WILLX", "2001-03-31", "", "", "googlefinance"]]
    assert waiting["result"].tolist() == ["Loading...", "#N/A"]
    assert prices.latest(done).values.tolist() == [["ACMEX", "2001-03-03", "2001-03-02", 12.5]]


def test_a_request_is_written_as_a_formula_only_from_a_checked_quote_and_date():
    assert sheets.price_formula("MUTF:WILLX", "2001-03-03") == (
        '=IFERROR(LET(r,GOOGLEFINANCE("MUTF:WILLX","close",DATE(2001,3,3)-7,DATE(2001,3,3)),'
        'TEXT(INDEX(r,ROWS(r),1),"yyyy-mm-dd")&" "&INDEX(r,ROWS(r),2)),"none")')
    t = schema.table(schema.LEDGER, "price_requests")
    with pytest.raises(PermissionError):
        sheets.rewrite_price_requests(None, schema.table(schema.LEDGER, "prices"), pd.DataFrame())
    for quote, day in (('ACMEX")&IMPORTXML("x', "2001-03-03"), ("ACMEX", "=NOW()")):
        with pytest.raises(ValueError, match="price request refused"):
            sheets.rewrite_price_requests(None, t, pd.DataFrame([{"symbol": "ACMEX", "quote": quote, "date": day}]))


def test_ingest_asks_then_reads_the_answers_into_the_ledger_and_asks_for_the_rest(session):  # noqa: F811
    ledger, _, _ = engine.bootstrap(session, session.root)
    table = lambda name: schema.table(schema.LEDGER, name)  # noqa: E731
    sheets.append_tables(ledger, [
        (table("securities"), SECURITIES.iloc[:3].reindex(columns=table("securities").columns, fill_value="")),
        (table("holdings"), HOLDINGS.reindex(columns=table("holdings").columns, fill_value=""))])
    first = engine._price_requests(ledger, log=None, today=TODAY, wait=0)
    assert (first["added"], first["wanted"], first["asked"]) == (0, 12, 12)  # 10 dates and each one's latest close
    tab = ledger.worksheet("price_requests").rows
    assert tab[0] == list(table("price_requests").columns) and tab[1][3].startswith("=IFERROR(LET(r,GOOGLEFINANCE(")
    # Sheets evaluates two of the formulas before the next ingest; the rest are still loading
    assert [r[2] for r in tab[1:3]] == ["2001-06-14", "2001-06-14"]  # the latest closes are asked first
    tab[1][3], tab[2][3] = "2001-06-14 12.5", "none"
    for row in tab[3:]:
        row[3] = "Loading..."
    second = engine._price_requests(ledger, log=None, today=TODAY, wait=0)
    assert (second["added"], second["wanted"]) == (2, 10)
    held = sheets.read_table(ledger, table("prices"))
    assert held[["symbol", "date", "priced_date", "close"]].values.tolist() == [
        ["ACMEX", "2001-06-14", "2001-06-14", 12.5], ["WILLX", "2001-06-14", "", ""]]
    assert len(ledger.worksheet("price_requests").rows) == 11  # header and the ten still wanted


def test_ingest_waits_for_answers_while_sheets_works_them_out(session):  # noqa: F811
    ledger, _, _ = engine.bootstrap(session, session.root)
    table = lambda name: schema.table(schema.LEDGER, name)  # noqa: E731
    sheets.append_tables(ledger, [
        (table("securities"), SECURITIES.iloc[:1].reindex(columns=table("securities").columns, fill_value="")),
        (table("holdings"), HOLDINGS.iloc[:1].reindex(columns=table("holdings").columns, fill_value=""))])
    naps = []

    def sleep(seconds):  # Sheets answers during the first wait
        naps.append(seconds)
        for row in ledger.worksheet("price_requests").rows[1:]:
            row[3] = f"{row[2]} 10"
    out = engine._price_requests(ledger, log=None, today=TODAY, wait=60, sleep=sleep)
    assert naps == [engine.PRICE_POLL_EVERY] and (out["added"], out["wanted"], out["asked"]) == (7, 0, 0)  # 6 dates, 1 latest


def test_fetch_prices_asks_round_after_round_until_none_is_wanted(session, monkeypatch):  # noqa: F811
    answers = iter([{"added": 0, "wanted": 10, "asked": 10}, {"added": 6, "wanted": 4, "asked": 4},
                    {"added": 4, "wanted": 0, "asked": 0}, {"added": 9, "wanted": 0, "asked": 0}])
    monkeypatch.setattr(engine, "_price_requests", lambda ledger, log: next(answers))
    out = engine.fetch_prices(session, session.root, log=None)
    assert out == {"added": 10, "wanted": 0, "rounds": 3}
    stuck = iter([{"added": 0, "wanted": 5, "asked": 5}] * 9)
    monkeypatch.setattr(engine, "_price_requests", lambda ledger, log: next(stuck))
    assert engine.fetch_prices(session, session.root, log=None)["rounds"] == 2  # a round that adds nothing ends it


def test_the_latest_close_is_asked_each_day_for_what_googlefinance_prices_and_not_for_what_it_never_has():
    held = pd.DataFrame([{"symbol": "ACMEX", "date": "2001-05-31", "close": "12"},
                         {"symbol": "WILLX", "date": "2001-05-31", "close": ""}])
    ask, _ = prices.requests(SECURITIES, HOLDINGS, NO_ACTIVITY, held, TODAY)
    latest = ask[ask["date"] == "2001-06-14"]
    assert latest["symbol"].tolist() == ["ACMEX"]  # WILLX was tried and never priced

