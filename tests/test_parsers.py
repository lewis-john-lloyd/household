"""Parsers on synthetic documents laid out like the real formats."""

from pathlib import Path

import pytest

from household.parsers import ParseError, accounts_seed, documents, identify
from household.parsers import earnings_statement, pay_statement, schwab_equity

FIXTURES = Path(__file__).parent / "fixtures"


def text(name):
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize("name, parser", [
    ("pay_statement.txt", pay_statement), ("earnings_statement.txt", earnings_statement),
    ("schwab_equity.csv", schwab_equity), ("accounts_seed.csv", accounts_seed),
])
def test_each_fixture_is_identified_by_its_parser(name, parser):
    assert identify(text(name)) is parser


def test_unknown_text_is_not_identified():
    assert identify("hello\nworld\n") is None


def test_pay_statement_lines_and_kinds():
    r = pay_statement.parse(text("pay_statement.txt"))
    rows = r.tables["paystubs"]
    assert r.label == "paystub_alex_2001-01-19"
    assert set(rows["pay_date"]) == {"2001-01-19"} and set(rows["employer"]) == {"Acme Corp"}
    by = rows.set_index(["line_item", "kind"])
    assert by.loc[("401K Pretax", "pre_tax"), "amount"] == 400
    assert by.loc[("401K Pretax", "employer"), "ytd_amount"] == 2000
    assert by.loc[("Internet Reim", "post_tax"), "amount"] == -12.5
    assert by.loc[("Federal Income Tax", "tax"), "ytd_amount"] == 5000
    assert by.loc[("FIT Taxable Wages", "memo"), "amount"] == 3510


def test_pay_statement_rejects_a_misread_number():
    broken = text("pay_statement.txt").replace("$100.00", "$900.00", 1)  # Medical current; leak-guard: allow (synthetic)
    with pytest.raises(ParseError, match="self-checks"):
        pay_statement.parse(broken)


def test_pay_statement_catches_an_unlisted_pre_tax_deduction(monkeypatch):
    monkeypatch.setattr(pay_statement, "PRE_TAX", pay_statement.PRE_TAX - {"Medical"})
    with pytest.raises(ParseError, match="FIT taxable"):
        pay_statement.parse(text("pay_statement.txt"))


def test_earnings_statement_lines_and_kinds():
    r = earnings_statement.parse(text("earnings_statement.txt"))
    rows = r.tables["paystubs"].set_index(["line_item", "kind"])
    assert r.label == "paystub_pat_2001-01-19"
    assert rows.loc[("401k Match", "employer"), "ytd_amount"] == 300
    assert rows.loc[("401k", "pre_tax"), "amount"] == 105
    assert rows.loc[("Regular", "income"), "amount"] == 2000
    assert rows.loc[("GROUP TE", "income"), "amount"] == 5
    assert rows.loc[("Phone Stipend", "post_tax"), "ytd_amount"] == -60
    assert set(r.tables["paystubs"]["employer"]) == {"Example Employer, LLC"}


def test_earnings_statement_rejects_a_misread_number():
    broken = text("earnings_statement.txt").replace("124.31", "124.13")
    with pytest.raises(ParseError):
        earnings_statement.parse(broken)


def test_schwab_vests_and_sale_lots():
    r = schwab_equity.parse(text("schwab_equity.csv"))
    vests, sales = r.tables["equity_vests"], r.tables["equity_sales"]
    assert list(vests["vest_date"]) == ["2001-02-25", "2001-01-25"]
    assert list(vests["net_shares"]) == [5, 10]
    assert sales["gain"].sum() == 125 and sales["proceeds"].sum() == 1650
    assert set(sales["term"]) == {"short_term"}
    assert r.notes == ["1 dividend rows not ingested"]


def test_schwab_sale_must_match_its_lots():
    broken = text("schwab_equity.csv").replace('"Share Sale","15"', '"Share Sale","16"')
    with pytest.raises(ParseError):
        schwab_equity.parse(broken)


def test_accounts_seed_validates_enums():
    r = accounts_seed.parse(text("accounts_seed.csv"))
    assert list(r.tables["accounts"]["account_id"]) == ["bills", "card"]
    with pytest.raises(ParseError, match="type"):
        accounts_seed.parse(text("accounts_seed.csv").replace("credit_card", "plastic"))


def test_documents_recognized_without_rows():
    r = documents.parse("Electronic Filing Instructions for your 2001 Federal Tax Return\nForm 1040")
    assert (r.kind, r.label, r.tables) == ("tax_return", "tax_return_2001", {})
    w4 = identify("Form W-4  Employee's Withholding Certificate  2001")
    assert w4 is documents and documents.parse("Form W-4 Withholding Certificate 2001").label == "w4_2001"


def test_stub_ids_distinguish_two_stubs_on_one_date():
    a = pay_statement.parse(text("pay_statement.txt")).tables["paystubs"]
    b = earnings_statement.parse(text("earnings_statement.txt")).tables["paystubs"]
    assert set(a["stub_id"]) == {"4242"} and set(b["stub_id"]) == {"77"}


def test_earnings_statement_accepts_an_unlabeled_deductions_total():
    lines = text("earnings_statement.txt").splitlines()
    i = next(k for k, l in enumerate(lines) if "GROUP TE" in l)
    at = lines[i].index("Deductions", 30)
    lines[i] = lines[i][:at] + " " * len("Deductions") + lines[i][at + len("Deductions"):]
    r = earnings_statement.parse("\n".join(lines))
    assert r.tables["paystubs"].set_index("line_item").loc["Deductions", "amount"] == 90
