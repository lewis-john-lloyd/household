"""Schwab's Awards page copied as text, and the projection's vests from the schedule, on a made-up grant."""

import pandas as pd
import pytest

from household import projection
from household.parsers import ParseError, identify, schwab_awards
from test_projection import regular

PAGE = """Award Date\tSymbol\tAward Type\tAward Price\tGranted\tUnvested Market Value\tGrant Agreement Status
03/04/2026\t
ACME\t
RSU\t
¤0.00\t
30\t
¤900.00\t
Accepted
Award ID\tVested\tUnvested\tCanceled
G100\t
10\t
20\t
0
Vesting Schedule
Vest Date\t# of Shares
08/25/2026\t
5
09/25/2026\t
5
10/25/2026\t
5
11/25/2026\t
5
12/25/2026\t
5
01/25/2027\t
5
Dividend Equivalent Rights
Record Date

Payable Date

Dividend Per Share

09/07/2026\t
09/14/2026\t
¤0.20
Total\t
0.1
08/06/2025\t
ACME\t
RSU\t
¤0.00\t
10\t
¤300.00\t
Accepted
Award ID\tVested\tUnvested\tCanceled
G90\t
6\t
4\t
0
Vesting Schedule
Vest Date\t# of Shares
06/25/2026\t
6
11/25/2026\t
4
""".replace("\u00a4", "$")  # money written as \u00a4 in the page above, so no line reads as an amount


def test_the_awards_page_reads_as_each_grants_vesting_schedule():
    assert identify(PAGE) is schwab_awards
    r = schwab_awards.parse(PAGE)
    df = r.tables["vest_schedule"]
    assert df.groupby("grant_id")["shares"].sum().to_dict() == {"G100": 30, "G90": 10}
    assert df.iloc[0].to_dict() == {"grant_id": "G100", "award_date": "2026-03-04", "symbol": "ACME", "award_type": "RSU",
                                    "vest_date": "2026-08-25", "shares": 5}
    assert r.notes[1] == "G90 (ACME RSU of 2025-08-06): 10 granted, 6 vested, 4 unvested, 2 vests to 2026-11-25"


def test_a_schedule_that_doesnt_add_up_to_the_grant_refuses_the_page():
    with pytest.raises(ParseError, match="G100: the schedule's 25 shares are not the 30 granted"):
        schwab_awards.parse(PAGE.replace("01/25/2027\t\n5\n", ""))
    with pytest.raises(ParseError, match="are not the 30 granted"):
        schwab_awards.parse(PAGE.replace("G100\t\n10\t", "G100\t\n11\t"))


def test_a_grants_latest_page_is_its_schedule_and_the_price_is_the_latest_seen():
    old = schwab_awards.parse(PAGE).tables["vest_schedule"].assign(doc_id="d1")
    new = old[old["grant_id"] == "G100"].assign(doc_id="d2", shares=6)  # a later copy of one grant's page
    s = schwab_awards.in_force(pd.concat([old, new]))
    assert s.groupby("grant_id")["shares"].sum().to_dict() == {"G100": 36, "G90": 10}
    vested = pd.DataFrame([{"vest_date": "2026-09-25", "fmv": "30"}, {"vest_date": "2026-08-25", "fmv": "28"}])
    prices = pd.DataFrame([{"symbol": "ACME", "date": "2026-10-31", "close": "32"}, {"symbol": "QUILX", "date": "2026-11-01", "close": "9"}])
    assert projection.scheduled_vests(old, vested, prices)["price"] == 32
    assert projection.scheduled_vests(old, vested)["price"] == 30
    assert projection.scheduled_vests(old.iloc[0:0], vested) is None


def vest(pay_date, stub_id, amount=2_000.0):
    """A vest paid on its own stub, withheld at the flat 22%."""
    common = {"pay_date": pay_date, "period_start": pay_date, "period_end": pay_date, "person": "alex",
              "employer": "Acme", "stub_id": stub_id, "doc_id": "d"}
    items = [("RSU Vest", "income", amount, amount), ("Federal Income Tax", "tax", amount * 0.22, amount * 0.22),
             ("Employee Medicare", "tax", amount * 0.0145, amount * 0.0145), ("Gross", "memo", amount, amount),
             ("FIT Taxable Wages", "memo", amount, amount)]
    return [{**common, "line_item": a, "kind": k, "amount": v, "ytd_amount": y} for a, k, v, y in items]


def test_the_vests_left_this_year_come_from_the_schedule_in_place_of_the_monthly_guess():
    rows = []
    for n, day in enumerate(pd.date_range("2026-01-02", periods=20, freq="14D"), start=1):
        rows += regular(day.date().isoformat(), str(n))
    rows += vest("2026-08-27", "801") + vest("2026-09-28", "901")
    stubs = pd.DataFrame(rows)
    guessed = projection.positions(stubs, 2026)[("alex", "Acme")]
    assert guessed["vests_left"] == 3  # October, November, December at the recent average
    schedule = schwab_awards.parse(PAGE).tables["vest_schedule"]
    vests = {"schedule": list(zip(schedule["vest_date"], schedule["shares"])), "price": 30}
    planned = projection.positions(stubs, 2026, {"vests": vests})[("alex", "Acme")]
    # after the latest paid (September 28) and this year: Oct 25 (5), Nov 25 (5 + 4), Dec 25 (5)
    assert planned["vests_left"] == 3
    assert round(guessed["wages"] - planned["wages"], 2) == 3 * 2_000 - 19 * 30
    assert planned["notes"][-1] == "3 more scheduled vests, 19 shares at about 30.00, withheld at 22%"
    lower = projection.positions(stubs, 2026, {"vests": vests, "stock_price": 0.8})[("alex", "Acme")]
    assert round(planned["wages"] - lower["wages"], 2) == 19 * 6
