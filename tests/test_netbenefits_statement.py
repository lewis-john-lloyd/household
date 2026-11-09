"""A NetBenefits plan statement: each fund's shares and value at the period's start and end."""

import pytest

from household.parsers import identify, netbenefits_statement as nb
from household.parsers.common import ParseError

HEAD = """Statement Details

ACME WIDGETS SAVINGS PLAN                                  Retirement Savings Statement

ALEX SAMPLE                                                   Customer Service: (800) 555-0100

Your Account Summary                                    Statement Period: 04/01/2001 to 06/30/2001
"""

TABLE = """Market Value of Your Account                              Statement Period: 04/01/2001 to 06/30/2001

                                               CORE OPTIONS
              Shares as of    Shares as of     Price as of    Price as of    Market Value    Market Value as of
Investment       03/31/2001     06/30/2001     03/31/2001      06/30/2001             as of           06/30/2001
Stock                                                                               ¤40.00        ¤55.00
Large Cap
S&P 500                2.000      2.500            ¤20.00         ¤22.00             ¤40.00          ¤55.00
Index
Bond                                                                                  ¤30.00         ¤33.00

Income
US Bond                3.000     3.300            ¤10.00         ¤10.00             ¤30.00          ¤33.00
Index

                                        ASSET ALLOCATION FUNDS
Target Ret              0.000            0.000           ¤0.00         ¤11.00              ¤0.00               ¤0.00
2060
Target Ret              0.000            1.000           ¤0.00         ¤12.00              ¤0.00              ¤12.00
2065

Account Totals                                                                            {then}     {now}
"""


def statement(then="¤70.00", now="¤100.00", table=TABLE):
    # money written as ¤ in this file, so no line of it reads as an amount
    return (HEAD + "\n" + table.replace("{then}", then).replace("{now}", now)).replace("¤", "$")


def test_each_fund_is_read_at_the_start_and_the_end_with_wrapped_names_joined():
    text = statement()
    assert identify(text) is nb
    held = nb.parse(text).tables["holdings"]
    assert held[["as_of", "symbol", "shares", "value"]].values.tolist() == [
        ["2001-03-31", "S&P 500 Index", 2.0, 40.0],
        ["2001-06-30", "S&P 500 Index", 2.5, 55.0],
        ["2001-03-31", "US Bond Index", 3.0, 30.0],
        ["2001-06-30", "US Bond Index", 3.3, 33.0],
        ["2001-06-30", "Target Ret 2065", 1.0, 12.0],
    ]  # a fund held at neither date is left out


def test_a_statement_that_doesnt_add_up_is_refused():
    with pytest.raises(ParseError, match="Account Totals"):
        nb.parse(statement(now="¤90.00"))
    with pytest.raises(ParseError, match="shares at"):
        nb.parse(statement(table=TABLE.replace("¤55.00", "¤65.00")))
    with pytest.raises(ParseError, match="no fund rows"):
        nb.parse(
            statement(
                table=TABLE.replace("S&P 500 ", "")
                .replace("US Bond ", "")
                .replace("Target Ret ", "")
                .replace("2.000", "x")
                .replace("3.000", "x")
                .replace("0.000", "x")
            )
        )


def test_a_statement_printed_with_the_browser_title_first_is_still_one():
    assert identify("Fidelity NetBenefits - Statement Details\n\n" + statement()) is nb


def test_a_price_cut_to_the_cent_still_checks_but_a_misread_value_does_not():
    # 40 shares worth 49.98 is 1.2495 each, which the statement shows cut to 1.24
    row = "S&P 500                0.000     40.000            ¤0.00         ¤1.24             ¤0.00          ¤49.98\nIndex\n"
    table = "Market Value of Your Account\n" + row + "\nAccount Totals   {then}     {now}\n"
    held = nb.parse(statement(then="¤0.00", now="¤49.98", table=table)).tables["holdings"]
    assert held[["shares", "value"]].values.tolist() == [[40.0, 49.98]]
    with pytest.raises(ParseError):
        nb.parse(statement(then="¤0.00", now="¤50.98", table=table.replace("49.98", "50.98")))
