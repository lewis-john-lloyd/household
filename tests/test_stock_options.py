"""A stock option grant's vesting schedule copied from the plan's page, on a made-up grant."""

import pytest

from household.parsers import ParseError, identify, stock_options

PAGE = """STOCK OPTIONS
(1 GRANT)
All values are before taxes
Grant ID\tGrant price\tTotal
granted\tVesting date\tStatus\tQuantity\tExercisable
quantity\tExercisable
value\tOutstanding
value\tActions
OPT1
Granted:
Jan-15-2001
Expires:
Jan-15-2011
¤2.50\t40.000\tMar-15-2002\tUnvested\t10.000\t0.000\t--\t¤25.00\t
•••
Feb-15-2002\tUnvested\t10.000\t¤25.00
Jan-15-2002\tVested\t20.000\t¤50.00
Total\t\t40.000\t\t\t40.000\t0.000\t--\t¤100.00
""".replace("¤", "$")  # money written as ¤ above, so no line of this file reads as an amount


def test_each_tranche_is_a_vesting_date_of_the_option_with_its_price_and_expiry():
    assert identify(PAGE) is stock_options
    r = stock_options.parse(PAGE)
    df = r.tables["vest_schedule"]
    assert df[["vest_date", "shares"]].values.tolist() == [["2002-01-15", 20], ["2002-02-15", 10], ["2002-03-15", 10]]
    assert set(df["award_type"]) == {"OPTION"} and set(df["grant_price"]) == {2.5} and set(df["expires"]) == {"2011-01-15"}
    assert r.notes == ["OPT1 (options at 2.50, granted 2001-01-15, expire 2011-01-15): 40 granted, 20 unvested, 3 tranches to 2002-03-15"]


def test_tranches_that_dont_add_up_to_the_grant_refuse_the_page():
    with pytest.raises(ParseError, match="OPT1: the tranches' 30 shares are not the 40 granted"):
        stock_options.parse(PAGE.replace("Feb-15-2002\tUnvested\t10.000", "Feb-15-2002\tUnvested\t0.000"))
