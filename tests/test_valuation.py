"""Month-end values of investment accounts from readings, activity and prices, on made-up accounts."""

import pandas as pd

from household import valuation as v

ACCOUNTS = pd.DataFrame([
    {"account_id": "a_plan", "owner": "alex", "type": "workplace_plan", "tax_treatment": "pre_tax", "closed": ""},
    {"account_id": "s_ira", "owner": "sam", "type": "retirement", "tax_treatment": "roth", "closed": ""},
    {"account_id": "j_tax", "owner": "joint", "type": "brokerage", "tax_treatment": "taxable", "closed": ""},
    {"account_id": "k_529", "owner": "kid", "type": "education", "tax_treatment": "education", "closed": ""},
    {"account_id": "gone", "owner": "joint", "type": "education", "tax_treatment": "education", "closed": "2001-03-15"},
    {"account_id": "j_chk", "owner": "joint", "type": "checking", "tax_treatment": "none", "closed": ""}])


def row(as_of, account, symbol, shares, value, doc="d1", source=""):
    return {"as_of": as_of, "account_id": account, "symbol": symbol, "source": source, "shares": shares,
            "value": value, "doc_id": doc}


HOLDINGS = pd.DataFrame([
    row("2001-01-12", "a_plan", "ACMEX", 10, 100),
    row("2001-02-28", "a_plan", "", "", 90, "s1", "pretax"),
    row("2001-02-28", "a_plan", "", "", 30, "s1", "roth_in_plan_conversion"),
    row("2001-01-12", "s_ira", "WILLX", 5, 50, "legacy:investments"),
    row("2001-01-12", "s_ira", "cash", "", 7, "legacy:investments"),
    row("2001-03-03", "s_ira", "cash", "", 9, "c1"),
    row("2001-01-12", "j_tax", "ACMEX", 2, 20, "d2"),
    row("2001-01-12", "k_529", "", "", 40, "legacy:investments"),
    row("2001-01-12", "gone", "", "", 40, "legacy:investments")])
ACTIVITY = pd.DataFrame([{"account_id": "j_tax", "date": "2001-02-10", "symbol": "ACMEX", "source": "", "shares": 1},
                         {"account_id": "j_tax", "date": "2001-03-10", "symbol": "PAGEX", "source": "", "shares": 4}])
PRICES = pd.DataFrame(
    [{"symbol": "ACMEX", "date": d, "priced_date": d, "close": c}
     for d, c in [("2001-01-31", 11), ("2001-02-28", 12), ("2001-03-31", 13), ("2001-04-09", 14)]]
    + [{"symbol": "WILLX", "date": "2001-03-31", "priced_date": "2001-03-30", "close": 10.5},
       {"symbol": "PAGEX", "date": "2001-03-31", "priced_date": "2001-03-30", "close": 5}])
SECURITIES = pd.DataFrame([{"symbol": "ACMEX", "asset_class": "SDT"}, {"symbol": "WILLX", "asset_class": "BT"},
                           {"symbol": "cash", "asset_class": "BA"}])


def values():
    out, notes = v.values(ACCOUNTS, HOLDINGS, ACTIVITY, PRICES, SECURITIES, "2001-04-10", partial={"c1"})  # a cash history
    return out, notes


def on(out, day, account):
    return out[(out["date"] == day) & (out["account_id"] == account)]


def test_dates_are_each_month_end_from_the_first_reading_then_today():
    assert v.valuation_dates(HOLDINGS, "2001-04-10") == ["2001-01-31", "2001-02-28", "2001-03-31", "2001-04-10"]


def test_shares_roll_forward_with_activity_and_a_new_fund_joins_from_activity():
    out, _ = values()
    assert on(out, "2001-02-28", "j_tax")[["symbol", "shares", "value", "basis"]].values.tolist() == [
        ["ACMEX", 3, 36, "rolled"]]
    later = on(out, "2001-03-31", "j_tax")
    assert later[["symbol", "value"]].values.tolist() == [["ACMEX", 39], ["PAGEX", 20]]
    assert set(later["bucket"]) == {"taxable"} and set(later["owner"]) == {"joint"}


def test_cash_updates_apart_from_funds_and_a_fund_with_no_price_since_its_reading_is_carried():
    out, _ = values()
    feb = on(out, "2001-02-28", "s_ira")
    assert feb[["symbol", "value", "basis"]].values.tolist() == [["cash", 7, "carried"], ["WILLX", 50, "carried"]]
    mar = on(out, "2001-03-31", "s_ira")
    assert mar[["symbol", "value", "basis"]].values.tolist() == [["cash", 9, "carried"], ["WILLX", 52.5, "priced"]]
    assert set(mar["bucket"]) == {"ira_roth"} and set(mar["wrapper"]) == {"ira"}


def test_a_newer_plan_statement_sets_the_total_then_the_funds_move_it_and_split_it_by_source():
    out, _ = values()
    feb = on(out, "2001-02-28", "a_plan").set_index("bucket")
    assert (feb.at["401k_pretax", "value"], feb.at["401k_roth", "value"]) == (90, 30)
    apr = on(out, "2001-04-10", "a_plan").set_index("bucket")
    assert (apr.at["401k_pretax", "value"], apr.at["401k_roth", "value"]) == (105, 35)  # 120 times 14/12
    jan = on(out, "2001-01-31", "a_plan")
    assert jan["value"].sum() == 110 and set(jan["bucket"]) == {"401k_pretax", "401k_roth"}  # split by the nearest


def test_a_closed_account_holds_nothing_and_a_bank_account_isnt_an_investment():
    out, _ = values()
    assert set(out.loc[out["account_id"] == "gone", "date"]) == {"2001-01-31", "2001-02-28"}
    assert "j_chk" not in set(out["account_id"])


def test_a_held_fund_with_no_asset_class_is_noted():
    _, notes = values()
    assert notes == ["valuation: 1 held funds have no asset class in securities: PAGEX"]


def test_joint_splits_evenly_between_the_adults_and_anyone_elses_is_the_kids():
    out, _ = values()
    today = out[out["date"] == "2001-04-10"]
    split = v.split_by_owner(today, ("alex", "sam")).sum().round(2).to_dict()
    assert split == {"alex": 140 + 31, "sam": 9 + 52.5 + 31, "kids": 40}


def test_the_adults_come_from_the_setting_else_the_pay_stubs():
    params = pd.DataFrame([{"year": "", "jurisdiction": "household", "key": "household_adults", "value": "Alex, Sam",
                            "source": ""}])
    assert v.adults(params) == ("alex", "sam")
    assert v.adults(params.iloc[0:0], pd.DataFrame([{"person": "sam"}, {"person": "alex"}])) == ("alex", "sam")


def test_wrappers_and_buckets():
    assert [v.wrapper({"type": t, "account_id": i}) for t, i in [
        ("retirement", "x_ira"), ("retirement", "x_plan"), ("savings_bonds", "b"), ("checking", "c")]] == [
        "ira", "401k", "ibonds", ""]
    assert v.bucket("401k", "pre_tax", "after_tax") == "401k_roth" and v.bucket("ira", "pre_tax") == "ira_pretax"


def test_a_statement_newer_than_an_older_reading_by_source_sets_the_total_and_the_split():
    held = pd.DataFrame([row("2001-01-12", "a_plan", "ACMEX", 6, 60, "legacy:investments", "pretax"),
                         row("2001-01-12", "a_plan", "ACMEX", 2, 20, "legacy:investments", "roth"),
                         row("2001-03-31", "a_plan", "", "", 75, "s1", "pre_tax_401k"),
                         row("2001-03-31", "a_plan", "", "", 25, "s1", "roth_in_plan_conversion")])
    out, _ = v.values(ACCOUNTS.iloc[:1], held, ACTIVITY.iloc[0:0], PRICES, SECURITIES, "2001-04-10")
    jan = on(out, "2001-01-31", "a_plan")
    assert jan[["bucket", "value"]].values.tolist() == [["401k_pretax", 66], ["401k_roth", 22]]
    apr = on(out, "2001-04-10", "a_plan").set_index("bucket")["value"]
    assert (apr["401k_pretax"], apr["401k_roth"]) == (round(75 * 14 / 13, 2), round(25 * 14 / 13, 2))


def test_a_complete_reading_ends_what_it_doesnt_list_and_a_partial_one_leaves_the_other_kind():
    held = pd.DataFrame([row("2001-01-12", "s_ira", "WILLX", 5, 50, "d1"), row("2001-01-12", "s_ira", "cash", "", 7, "d1"),
                         row("2001-03-03", "s_ira", "cash", "", 9, "c1")])
    complete, _ = v.values(ACCOUNTS, held, ACTIVITY, PRICES, SECURITIES, "2001-04-10")
    assert on(complete, "2001-03-31", "s_ira")[["symbol", "value"]].values.tolist() == [["cash", 9]]
    partial, _ = v.values(ACCOUNTS, held, ACTIVITY, PRICES, SECURITIES, "2001-04-10", partial={"c1"})
    assert on(partial, "2001-03-31", "s_ira")[["symbol", "value"]].values.tolist() == [["cash", 9], ["WILLX", 52.5]]
    docs = pd.DataFrame([{"doc_id": "c1", "parser": "healthequity_cash"}, {"doc_id": "d1", "parser": "ofx_invest"}])
    assert v.partial_docs(docs) == {"c1"}


def test_an_account_the_legacy_workbook_stops_listing_holds_nothing_until_its_next_reading():
    legacy = "legacy:investments"
    held = pd.DataFrame([
        row("2001-01-12", "s_ira", "WILLX", 5, 50, legacy), row("2001-01-12", "s_ira", "cash", "", 7, legacy),
        row("2001-01-12", "j_tax", "ACMEX", 2, 20, legacy),
        row("2001-02-20", "a_plan", "ACMEX", 1, 10, legacy),  # one snapshot updated an account at a time:
        row("2001-03-05", "j_tax", "ACMEX", 2, 20, legacy),   # s_ira is left out of both its dates
        row("2001-04-02", "s_ira", "cash", "", 3, "c1")])
    assert v.legacy_gone(held) == {"s_ira": ["2001-02-20"]}
    out, _ = v.values(ACCOUNTS, held, ACTIVITY, PRICES, SECURITIES, "2001-04-10", partial={"c1"})
    assert on(out, "2001-02-28", "s_ira").empty and on(out, "2001-03-31", "s_ira").empty
    assert on(out, "2001-04-10", "s_ira")[["symbol", "value"]].values.tolist() == [["cash", 3]]  # its funds stay ended
    assert len(on(out, "2001-03-31", "j_tax"))  # listed in the snapshot's second date
