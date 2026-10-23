"""Federal tax lines on synthetic households, checked against hand calculations."""

import pandas as pd
import pytest

from household import tax, taxdata


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
        assert year in taxdata.SOURCES
