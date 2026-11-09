"""Homes, their values, loans, improvements and basis, and net worth, on a made-up household."""

import pandas as pd
import pytest

from household import house, legacy_investments as li, mortgage, networth
from household.parsers import identify, seed

PROPS = pd.DataFrame([
    {"property_id": "willow", "address": "1 Willow St", "parcel": "", "owner": "joint", "purchased": "2001-05-01",
     "purchase_price": "300", "basis_costs": "6", "loan_ids": "acme_loan", "sold": "", "sale_price": "", "note": ""},
    {"property_id": "old", "address": "", "parcel": "", "owner": "joint", "purchased": "", "purchase_price": "",
     "basis_costs": "", "loan_ids": "old_loan", "sold": "", "sale_price": "", "note": ""}])
VALUES = pd.DataFrame([
    {"as_of": "2001-01-12", "property_id": "old", "source": "legacy", "value": "90", "note": "", "doc_id": ""},
    {"as_of": "2001-04-01", "property_id": "old", "source": "legacy", "value": "95", "note": "", "doc_id": ""},
    {"as_of": "2001-06-01", "property_id": "willow", "source": "zillow", "value": "280", "note": "", "doc_id": ""},
    {"as_of": "2001-06-05", "property_id": "willow", "source": "county_fair_market", "value": "320", "note": "",
     "doc_id": ""},
    {"as_of": "2001-06-09", "property_id": "willow", "source": "redfin", "value": "310", "note": "", "doc_id": ""}])
LOANS = pd.DataFrame([{"loan_id": "acme_loan", "lender": "Acme Widgets", "originated": "2001-05-01",
                       "principal": "240", "rate": "6", "term_months": "360", "escrow": ""}])
BALANCES = pd.DataFrame([{"as_of": "2001-01-12", "loan_id": "old_loan", "balance": "70", "doc_id": ""},
                         {"as_of": "2001-04-01", "loan_id": "old_loan", "balance": "60", "doc_id": ""},
                         {"as_of": "2001-02-01", "loan_id": "side_loan", "balance": "5", "doc_id": ""}])
IMPROVED = house.improvements(pd.DataFrame([
    {"date": "2001-07-01", "property_id": "willow", "description": "new well pump", "kind": "capital", "cost": "8",
     "credit": "", "receipt": "", "note": ""},
    {"date": "2001-07-02", "property_id": "willow", "description": "heat pump", "kind": "energy_credit", "cost": "10",
     "credit": "3", "receipt": "", "note": ""},
    {"date": "2001-07-03", "property_id": "willow", "description": "fix a leak", "kind": "repair", "cost": "2",
     "credit": "", "receipt": "", "note": ""}]))
SCHEDULE, _ = mortgage.schedule(LOANS, through_month="2001-09")
PARAMS = pd.DataFrame(columns=["year", "jurisdiction", "key", "value", "source"])


def setting(key, value):
    return pd.DataFrame([{"year": "", "jurisdiction": "household", "key": key, "value": value, "source": ""}])


def test_improvements_add_to_basis_by_kind():
    assert IMPROVED["basis_added"].tolist() == [8, 7, 0]


def test_the_summary_has_basis_estimates_loans_equity_and_what_a_sale_would_net():
    out, notes = house.summary(PROPS.iloc[:1], VALUES, IMPROVED, LOANS, SCHEDULE, BALANCES, PARAMS, "2001-09-15")
    got = dict(zip(out["key"], out["value"]))
    owed = float(SCHEDULE["balance"].iloc[-1])
    assert got["basis"] == 300 + 6 + 8 + 7 and got["repairs"] == 2 and got["status"] == "owned"
    assert got["estimate_median"] == 310 and got["loan:acme_loan"] == owed
    assert got["value_used"] == 300 and got["equity"] == pytest.approx(300 - owed)  # the purchase price by default
    assert got["sale_net"] == pytest.approx(310 * 0.94 - owed) and got["sale_gain"] == pytest.approx(310 * 0.94 - 321)
    assert notes == []


def test_the_value_counted_follows_the_setting():
    prop = PROPS.iloc[0].to_dict()
    assert house.value_on(prop, VALUES, "2001-09-15", "median")[0] == 310
    assert house.value_on(prop, VALUES, "2001-09-15", "lowest")[0] == 280
    assert house.value_on(prop, VALUES, "2001-09-15", "county_fair_market") == (320, "county_fair_market on 2001-06-05")
    assert house.value_on(PROPS.iloc[1].to_dict(), VALUES, "2001-05-01", "purchase_price") == (95, "legacy on 2001-04-01")


def test_a_home_with_only_legacy_values_is_owned_through_the_last_of_them():
    old = PROPS.iloc[1].to_dict()
    assert [house.owned(old, VALUES, d) for d in ("2001-01-01", "2001-02-28", "2001-04-01", "2001-04-30")] == [
        False, True, True, False]


def test_a_loans_balance_comes_from_its_schedule_or_its_recorded_balances():
    assert house.loan_balance("acme_loan", "2001-04-30", LOANS, SCHEDULE, BALANCES) == 0
    assert house.loan_balance("acme_loan", "2001-05-31", LOANS, SCHEDULE, BALANCES) == 240  # first payment in July
    assert house.loan_balance("old_loan", "2001-03-31", LOANS, SCHEDULE, BALANCES) == 70
    assert house.loan_balance("nothing", "2001-03-31", LOANS, SCHEDULE, BALANCES) is None


def test_net_worth_adds_investments_home_equity_and_other_debt_by_owner():
    vals = pd.DataFrame([{"date": "2001-02-28", "owner": "alex", "bucket": "ira_roth", "value": 40},
                         {"date": "2001-02-28", "owner": "joint", "bucket": "taxable", "value": 10},
                         {"date": "2001-02-28", "owner": "kid", "bucket": "529", "value": 30}])
    net = networth.build(vals, ("alex", "sam"), PROPS, VALUES, LOANS, SCHEDULE, BALANCES, "purchase_price")
    got = {(p, o): v for p, o, v in zip(net["part"], net["owner"], net["value"])}
    assert got == {("ira_roth", "alex"): 40, ("taxable", "alex"): 5, ("taxable", "sam"): 5, ("529", "kids"): 30,
                   ("home_equity", "alex"): 10, ("home_equity", "sam"): 10,  # 90 less 70, halved
                   ("other_debt", "alex"): -2.5, ("other_debt", "sam"): -2.5}
    assert networth.totals(net) == {"2001-02-28": 95}


def test_the_legacy_net_worth_tab_gives_home_values_and_loans_owed():
    rows = [["Date", "Value", " Old - M ", " Old - V "], [36903, 10, -70, 90], [36980, 10, "", 0], ["", ""]]
    values, loans, notes = li.net_worth(rows, {"Old - V": ("value", "old"), "Old - M": ("loan", "old_loan"),
                                               "Gone": ("loan", "gone")})
    assert values[["as_of", "property_id", "source", "value"]].values.tolist() == [["2001-01-12", "old", "legacy", 90]]
    assert loans[["as_of", "loan_id", "balance"]].values.tolist() == [["2001-01-12", "old_loan", 70]]
    assert notes == ["net worth: no column 'Gone'"]


def test_homes_values_and_improvements_arrive_as_seed_files():
    text = ("as_of,property_id,source,value,note,doc_id\n"
            "2001-06-01,willow,zillow,280,,\n2001-06-05,willow,county_assessed,250,,\n")
    assert identify(text) is seed
    r = seed.parse(text)
    assert r.keys["property_values"] == ["as_of", "property_id", "source"] and len(r.tables["property_values"]) == 2
    bad = text.replace("zillow", "a_guess")
    with pytest.raises(Exception, match="property_values.source"):
        seed.parse(bad)
    work = "date,property_id,description,kind,cost,credit,receipt,note,doc_id\n2001-07-01,willow,new pump,capital,8,,,,\n"
    assert seed.parse(work).tables["improvements"]["kind"].tolist() == ["capital"]
