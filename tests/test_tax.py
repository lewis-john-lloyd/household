"""Federal tax lines on synthetic households, checked against hand calculations."""

import pandas as pd
import pytest

from household import tax, taxdata, taxsources


def test_bracket_tax_at_thresholds():
    assert tax.bracket_tax(23_850, 2025) == pytest.approx(2_385)
    assert tax.bracket_tax(96_950, 2025) == pytest.approx(2_385 + 0.12 * 73_100)
    assert tax.bracket_tax(0, 2026) == 0


def test_simple_wage_earners_take_the_standard_deduction():
    r = tax.federal_return({"wages": 100_000, "federal_withheld": 8_000}, 2025)
    assert r["12e"] == 31_500 and r["15"] == 68_500
    assert r["16"] == tax.whole(2_385 + 0.12 * (68_500 - 23_850))  # hand: 7,743
    assert r["24"] == 7_743 and r["37"] == -257


def test_qualified_dividends_fill_the_zero_band_first():
    r = tax.federal_return({"wages": 60_000, "ordinary_dividends": 10_000,
                            "qualified_dividends": 10_000}, 2025)
    # taxable 38,500 is all below the 0% top, so the dividends are untaxed
    assert r["16"] == tax.whole(tax.bracket_tax(28_500, 2025))


def test_salt_cap_phases_down_to_the_floor():
    assert tax.salt_deduction(30_000, 15_000, 400_000, 2025) == 40_000
    assert tax.salt_deduction(30_000, 15_000, 550_000, 2025) == 25_000  # 40,000 - 0.3 x 50,000
    assert tax.salt_deduction(30_000, 15_000, 700_000, 2025) == 10_000
    assert tax.salt_deduction(30_000, 15_000, 400_000, 2024) == 10_000


def test_2026_charity_floor_and_itemized_cap():
    base = {"wages": 900_000, "state_income_tax": 50_000, "mortgage_interest": 40_000,
            "charity": 20_000}
    r = tax.federal_return(base, 2026)
    charity = 20_000 - 0.005 * 900_000
    itemized = 10_000 + 40_000 + charity
    itemized -= 2 / 37 * min(itemized, 900_000 - 768_700)
    assert r["12e"] == tax.whole(itemized)


def test_additional_medicare_niit_and_excess_social_security():
    r = tax.federal_return({"wages": 400_000, "medicare_wages": 420_000, "medicare_withheld": 7_000,
                            "capital_gain_short": 20_000, "state_income_tax": 0,
                            "ss_withheld_by_person": [(12_000, 2), (9_000, 1)]}, 2025)
    assert r["s2_11"] == tax.whole(0.009 * 170_000)
    assert r["s2_12"] == tax.whole(0.038 * 20_000)
    assert r["25c"] == 7_000 - tax.whole(0.0145 * 420_000)
    assert r["31"] == 12_000 - tax.whole(176_100 * 0.062)  # only the two-employer person


def test_check_against_return_matches_a_hand_computed_year():
    facts = pd.DataFrame([
        {"year": "2025", "kind": "input", "key": "wages", "person": "a", "value": "60000"},
        {"year": "2025", "kind": "input", "key": "wages", "person": "b", "value": "40000"},
        {"year": "2025", "kind": "input", "key": "federal_withheld", "person": "", "value": "8000"},
        {"year": "2025", "kind": "filed", "key": "24", "person": "", "value": "7743"},
        {"year": "2025", "kind": "filed", "key": "37", "person": "", "value": "-257"},
        {"year": "2025", "kind": "filed", "key": "15", "person": "", "value": "68000"},
    ])
    check = tax.check_against_return(facts).set_index("line")
    assert check.loc["24", "match"] and check.loc["37", "match"]
    assert not check.loc["15", "match"] and check.loc["15", "diff"] == 500


def test_every_year_has_complete_law():
    keys = set(taxdata.PARAMS[2025])
    for year in taxdata.PARAMS:
        assert set(taxdata.PARAMS[year]) == keys and year in taxdata.MFJ_BRACKETS


def test_every_cited_document_is_pinned_and_every_value_is_covered():
    for year, item, document, page, quote in taxsources.citations():
        assert document in taxdata.DOCUMENTS and page >= 1 and quote
    assert taxsources.uncovered() == []


def test_citation_check_catches_a_wrong_value_page_or_table(monkeypatch):
    floor = taxdata.params(2026)["withholding_mfj_table_floor"]
    table = " ".join(f"{t + floor:,} 1 {tax.bracket_tax(t, 2026):,.2f} {r * 100:g}% {t + floor:,}"
                     for t, r in taxdata.brackets(2026))
    page = f"0 {floor:,} 0.00 0% 0 " + table
    monkeypatch.setattr(taxdata, "CITATIONS", {
        (2026, "hsa_family"): ("doc-a", 1, "family coverage is 8,750"),
        (2026, "withholding_mfj_table_floor"): ("doc-b", 1, f"0 {floor:,} 0.00 0%")})
    monkeypatch.setattr(taxdata, "METHODS", {})
    dollar = "$"  # sources print dollar signs; quotes are written without them
    pages = {"doc-a": [f"the   family coverage is {dollar}8,750."], "doc-b": [page]}
    assert taxsources.check(pages)["ok"].all()

    monkeypatch.setitem(taxdata.PARAMS[2026], "hsa_family", 8_751)
    result = taxsources.check(pages).set_index("item")
    assert "does not state" in result.loc["hsa_family", "detail"]
    monkeypatch.setitem(taxdata.PARAMS[2026], "hsa_family", 8_750)

    pages["doc-b"] = [page.replace("24%", "25%")]
    assert "table rows differ" in taxsources.check(pages).set_index("item").loc[
        "withholding_mfj_table_floor", "detail"]
    assert taxsources.check({"doc-a": ["blank"]}).set_index("item").loc["hsa_family", "detail"] == \
        "quote not on page"


def test_tax_sources_report_links_filed_documents():
    title, sha = taxdata.DOCUMENTS["rev-proc-2025-19"]
    docs = pd.DataFrame([{"sha256": sha + "0" * 48, "drive_link": "link-to-pdf"}])
    report = taxsources.report(docs)
    hsa = report[(report["year"] == 2026) & (report["item"] == "hsa_family")].iloc[0]
    assert (hsa["value"], hsa["document"], hsa["record"]) == (8_750, title, "link-to-pdf")
    assert (report["record"] == "no source on file").sum() == len(taxdata.UNCITED)
    assert set(report.loc[report["document"] != title, "record"]) <= {
        "not in record store", "no source on file"}
