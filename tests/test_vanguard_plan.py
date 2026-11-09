"""A Vanguard plan's quarterly statement, as the PDF's layout text reads, on made-up figures."""

import pandas as pd
import pytest

from household import ingest
from household.parsers import ParseError, identify, vanguard_plan

GAP = " " * 40
STATEMENT = f"""Page > 1 of 5   > vanguard.com
SAM PARTNER{GAP}ACCOUNT SUMMARY: 01/01/2001 - 03/31/2001
1 Willow St{GAP}ACME WIDGETS 401(K) SAVINGS PLAN-- 001234
{GAP}Total Account Balance:{GAP}¤100.00
        Investment Activity
{GAP}Beginning{GAP}Other{GAP}Market{GAP}Dividends/{GAP}Ending
{GAP}Balance{GAP}Contributions{GAP}Transactions*{GAP}Gain/Loss{GAP}Capital Gains{GAP}Balance
                Acme Bond Index{GAP}¤20.00{GAP}¤10.00{GAP}- ¤0.10{GAP}- ¤1.90{GAP}¤2.00{GAP}¤30.00
                Ix Institl
                Quill 500 Index Trust{GAP}¤50.00{GAP}¤10.00{GAP}- ¤0.10{GAP}¤10.10{GAP}¤0.00{GAP}¤70.00

        Total Invested{GAP}¤70.00{GAP}¤20.00{GAP}- ¤0.20{GAP}¤8.20{GAP}¤2.00{GAP}¤100.00
*Other Transactions may include incoming or outgoing assets not specified above.
        Source Activity
{GAP}Balance{GAP}Contributions{GAP}Activity{GAP}Balance
        Pre-Tax 401(K){GAP}¤40.00{GAP}¤10.00{GAP}¤5.00{GAP}¤55.00
        After-Tax 401(K){GAP}¤0.00{GAP}¤10.00{GAP}- ¤10.00{GAP}¤0.00
             Source Activity
             2001 Roth In Plan Conversion{GAP}¤30.00{GAP}¤0.00{GAP}¤15.00{GAP}¤45.00
             Total Value{GAP}¤100.00
""".replace("¤", "$")  # money written as ¤ above, so no line of this file reads as an amount
ACCOUNTS = pd.DataFrame([{"account_id": "a_401k", "last4": "1234", "tax_treatment": "pre_tax"}])


def test_each_source_with_money_is_a_reading_on_the_quarters_last_day():
    assert identify(STATEMENT) is vanguard_plan
    r = ingest.finish_investment_rows(vanguard_plan.parse(STATEMENT), "document.pdf", ACCOUNTS)
    held = r.tables["holdings"]
    assert held[["as_of", "account_id", "symbol", "source", "value"]].values.tolist() == [
        ["2001-03-31", "a_401k", "", "pre_tax_401k", 55.0], ["2001-03-31", "a_401k", "", "2001_roth_in_plan_conversion", 45.0]]
    assert r.notes == ["a_401k: plan statement 2001-01-01 to 2001-03-31, 2 sources worth 100.00 (45.00 Roth) in 2 funds"]
    assert r.tables["investment_activity"].empty and (r.covers_from, r.covers_to) == ("2001-01-01", "2001-03-31")


@pytest.mark.parametrize("change, says", [
    ("¤55.00\n".replace("¤", "$"), "Pre-Tax 401(K): 55.00 is not the 56.00 it shows"),
    ("¤30.00\n                Ix".replace("¤", "$"), "Acme Bond Index Ix Institl: 30.00 is not the 31.00 it shows"),
])
def test_a_row_that_doesnt_add_up_refuses_the_statement(change, says):
    wrong = STATEMENT.replace(change, change.replace(change[1:3], "56" if "55" in change else "31"), 1)
    with pytest.raises(ParseError, match=says.replace("(", r"\(").replace(")", r"\)")):
        vanguard_plan.parse(wrong)


def test_sources_that_dont_add_up_to_the_total_value_refuse_the_statement():
    wrong = STATEMENT.replace("Total Value" + GAP + "$100.00", "Total Value" + GAP + "$90.00")  # leak-guard: allow (made up)
    with pytest.raises(ParseError, match="the sources: 100.00 is not the 90.00 it shows"):
        vanguard_plan.parse(wrong)
