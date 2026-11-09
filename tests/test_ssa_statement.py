"""The Social Security statement's XML download: who, when born, the estimates by claiming age, the earnings."""

import pytest

from household.parsers import identify, ssa_statement
from household.parsers.common import ParseError


def statement(name="ALEX Q. SAMPLE", born="1980-06-01", ages=range(62, 71), years=(2001, 2002, 2003)):
    est = "".join(f"<osss:Age{a}RetirementEstimate><osss:RetirementAge><osss:Years>{a}</osss:Years>"
                  f"</osss:RetirementAge><osss:Estimate>{1000 + 50 * (a - 62)}</osss:Estimate>"
                  f"</osss:Age{a}RetirementEstimate>" for a in ages)
    earn = "".join(f'<osss:Earnings startYear="{y}" endYear="{y}"><osss:FicaEarnings>{10 * (y - 2000)}'
                   f"</osss:FicaEarnings><osss:MedicareEarnings>{20 * (y - 2000)}</osss:MedicareEarnings>"
                   "</osss:Earnings>" for y in years)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<osss:OnlineSocialSecurityStatementData xmlns:osss="http://ssa.gov/osss/schemas/2.0">\n'
            "<osss:FileCreationDate>2001-02-03T00:00:00.000-05:00</osss:FileCreationDate>\n"
            f"<osss:UserInformation><osss:Name>{name}</osss:Name><osss:DateOfBirth>{born}</osss:DateOfBirth>"
            "</osss:UserInformation>\n"
            f"<osss:EstimatedBenefits>{est}<osss:DisabilityEstimate>90</osss:DisabilityEstimate>"
            "</osss:EstimatedBenefits>\n"
            f"<osss:EarningsRecord>{earn}</osss:EarningsRecord>\n"
            "</osss:OnlineSocialSecurityStatementData>\n")


def test_a_statement_gives_the_person_their_estimates_and_earnings():
    text = statement()
    assert identify(text) is ssa_statement
    r = ssa_statement.parse(text)
    who = r.tables["ss_statements"].iloc[0]
    assert (who["person"], who["birth_date"], who["as_of"]) == ("alex", "1980-06-01", "2001-02-03")
    est = r.tables["ss_estimates"]
    retire = est[est["kind"] == "retirement"]
    assert list(retire["age"]) == [str(a) for a in range(62, 71)] and list(retire["monthly"])[:2] == [1000, 1050]
    assert est[est["kind"] == "disability"]["monthly"].tolist() == [90]
    earn = r.tables["ss_earnings"]
    assert earn[["year", "fica_earnings", "medicare_earnings"]].values.tolist() == [
        ["2001", 10, 20], ["2002", 20, 40], ["2003", 30, 60]]
    assert set(earn["as_of"]) == {"2001-02-03"} and r.label == "ss_statement_alex_2001-02-03"


@pytest.mark.parametrize("text, message", [
    (statement(name=""), "first name"),
    (statement(born="06/01/1980"), "YYYY-MM-DD"),
    (statement(ages=range(63, 71)), "from 62 to 70"),
    (statement(years=()), "no earnings"),
    (statement(years=(2001, 2001)), "twice"),
], ids=["name", "birth", "ages", "earnings", "repeat"])
def test_a_statement_missing_a_part_is_refused(text, message):
    with pytest.raises(ParseError, match=message):
        ssa_statement.parse(text)


def test_other_xml_is_not_a_statement():
    assert not ssa_statement.sniff('<?xml version="1.0"?><OFX></OFX>')
