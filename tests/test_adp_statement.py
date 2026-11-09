"""ADP earnings statements, drawn from text pieces the way pdftext draws a page whose layout text fails."""

import pytest

from household import pdftext
from household.parsers import ParseError, adp_statement, identify

# (y, x, text) as pypdf's visitor reports an ADP stub: numbers in groups, cents at a fixed x per column.
# leak-guard: allow (synthetic amounts and advice number)
PIECES = [
    (735.8, 99.6, "ACME MARINE, LLC"),
    (735.8, 353.8, "Period Beginning: 01/01/2026"),
    (723.8, 353.8, "Period Ending: 01/14/2026"),
    (711.8, 353.8, "Pay Date: 01/23/2026"),
    (669.8, 99.6, "Taxable Marital Status: Married"),
    (669.4, 371.8, "ALEX Q SAMPLE"),
    (600.0, 131.0, "rate hours this period year to date"),
    (587.3, 20.9, "Regular 50"),
    (587.3, 124.8, " 0000 40"),
    (587.3, 180.2, " 00 2"),
    (587.3, 225.6, " 000"),
    (587.3, 245.8, " 00 2"),
    (587.3, 306.2, " 000"),
    (587.3, 326.4, " 00"),
    (581.3, 363.8, "Your federal taxable wages this period are"),
    (575.3, 20.9, "Holiday 400"),
    (575.3, 245.8, " 00 400"),
    (575.3, 326.4, " 00"),
    (569.3, 363.8, "$1"),  # leak-guard: allow (made up)
    (569.3, 379.0, " 922"),
    (569.3, 399.1, " 40"),  # leak-guard: allow
    (527.3, 354.0, "Group Term Life 2"),
    (527.3, 495.1, " 40 2"),
    (527.3, 575.8, " 40"),
    (515.3, 94.6, "Gross Pay"),
    (515.3, 210.5, "$2"),  # leak-guard: allow (made up)
    (515.3, 225.6, " 400"),
    (515.3, 245.8, " 00"),  # leak-guard: allow
    (515.3, 291.1, "2"),
    (515.3, 306.2, " 400"),
    (515.3, 326.4, " 00"),
    (485.3, 94.6, "Statutory"),
    (473.3, 94.6, "Federal Income Tax"),
    (473.3, 220.6, "-150"),
    (473.3, 245.8, " 00 150"),
    (473.3, 326.4, " 00"),
    (461.3, 94.6, "Social Security Tax"),
    (461.3, 220.6, "-148"),
    (461.3, 245.8, " 80 148"),
    (461.3, 326.4, " 80"),
    (449.3, 94.6, "Medicare Tax"),
    (449.3, 225.6, "-34"),
    (449.3, 245.8, " 80 34"),
    (449.3, 326.4, " 80"),
    (437.3, 94.6, "PA State Income Tax"),
    (437.3, 225.6, "-73"),
    (437.3, 245.8, " 68 73"),
    (437.3, 326.4, " 68"),
    (425.3, 94.6, "401K-Pretax"),
    (425.3, 220.6, "-480"),
    (425.3, 245.8, " 00* 480"),
    (425.3, 326.4, " 00"),
    (413.3, 94.6, "Union Dues"),
    (413.3, 225.6, "-20"),
    (413.3, 245.8, " 00 20"),
    (413.3, 326.4, " 00"),
    (329.3, 94.6, "Net Pay"),
    (329.3, 210.5, "$1"),  # leak-guard: allow (made up)
    (329.3, 225.6, " 492"),
    (329.3, 245.8, " 72"),  # leak-guard: allow
    (317.3, 94.6, "Checking 01"),
    (317.3, 210.5, "-1"),
    (317.3, 225.6, " 492"),
    (317.3, 245.8, " 72 1"),
    (317.3, 306.2, " 492"),
    (317.3, 326.4, " 72"),
    (299.3, 94.6, "Net Check"),
    (299.3, 230.6, "$0"),  # leak-guard: allow (made up)
    (299.3, 245.8, " 00"),  # leak-guard: allow
    (200.0, 353.8, "Advice number:"),
    (200.0, 470.0, "00000012345"),  # leak-guard: allow
]


def text(pieces=PIECES):
    return pdftext.render(pieces)


def test_render_puts_each_piece_at_its_column_on_its_line():
    out = pdftext.render([(700.0, 20.0, "left"), (700.4, 100.0, " right"), (690.0, 40.0, "below")])
    first, second = out.splitlines()
    assert (first.index("left"), first.index("right"), second.index("below")) == (10, 50, 20)


def test_adp_lines_kinds_and_year_to_date():
    t = text()
    assert identify(t) is adp_statement
    rows = adp_statement.parse(t).tables["paystubs"]
    got = {(r["line_item"], r["kind"]): (r["amount"], r["ytd_amount"]) for r in rows.to_dict("records")}
    assert got[("Regular", "income")] == (2000.0, 2000.0)  # rate and hours are not amounts
    assert got[("Holiday", "income")] == (400.0, 400.0)
    assert got[("Federal Income Tax", "tax")] == (150.0, 150.0)
    assert got[("401K-Pretax", "pre_tax")] == (480.0, 480.0)  # starred: excluded from federal wages
    assert got[("Union Dues", "post_tax")] == (20.0, 20.0)
    assert got[("Group Term Life", "income")] == got[("Group Term Life", "post_tax")] == (2.4, 2.4)
    assert got[("Gross", "memo")] == (2402.4, 2402.4)
    assert got[("Fed Taxable Income", "memo")][0] == 1922.4
    assert got[("Net Pay", "memo")][0] == 1492.72
    first = rows.iloc[0]
    assert (first["pay_date"], first["person"], first["employer"], first["stub_id"]) == (
        "2026-01-23",
        "alex",
        "ACME MARINE, LLC",
        "12345",
    )


def test_adp_amounts_read_groups_into_numbers():
    words = [(0, "-1"), (2, "234"), (6, "56"), (10, "12"), (13, "345"), (17, "67*")]
    assert adp_statement._amounts(words, "x") == [(6, -1234.56, False), (17, 12345.67, True)]


def test_adp_rejects_a_misread_number():
    bad = [p if p != (461.3, 220.6, "-148") else (461.3, 220.6, "-146") for p in PIECES]
    with pytest.raises(ParseError, match="self-checks"):
        adp_statement.parse(text(bad))
