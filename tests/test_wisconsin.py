"""Wisconsin tax and withholding on synthetic numbers and the publications' own examples."""

import pandas as pd
import pytest

from household import projection, tax, wisconsin, withholding


def test_tax_table_and_worksheet():
    assert wisconsin.tax(28_653, 2025) == 1_084  # the Form 1 instructions' tax table example
    assert wisconsin.tax(30, 2025) == 1 and wisconsin.tax(70, 2025) == 2  # the 20- and 60-wide rows
    assert wisconsin.tax(150_000, 2025) == round(0.053 * 150_000 - 781.92)  # worksheet, 5.3% row
    assert wisconsin.tax(500_000, 2025) == round(0.0765 * 500_000 - 10_911.83)  # worksheet, 7.65% row


def test_standard_deduction_slides_to_zero():
    assert wisconsin.standard_deduction(10_000, 2025) == 25_110
    assert wisconsin.standard_deduction(30_100, 2025) == 24_707  # the 30,000-30,500 row
    assert wisconsin.standard_deduction(155_100, 2025) == 17  # the short last row
    assert wisconsin.standard_deduction(200_000, 2025) == 0


def test_credits():
    assert wisconsin.school_property_credit(30, 2025) == 5
    assert wisconsin.school_property_credit(9_000, 2025) == 300
    assert wisconsin.married_couple_credit([50_000, 10_000], 2025) == 300
    assert wisconsin.married_couple_credit([50_000, 20_000], 2025) == 480
    assert wisconsin.married_couple_credit([50_000, 0], 2025) == 0


@pytest.mark.parametrize("base, periods, married, exemptions, withheld", [
    (350, 52, False, 1, 7.59),     # Pub W-166 example 1
    (500, 52, False, 3, 14.34),    # example 2
    (1_000, 26, True, 3, 22.08),   # example 3
])
def test_withholding_matches_w166_examples(base, periods, married, exemptions, withheld):
    assert wisconsin.regular_withholding(base, periods, 2026, married, exemptions) == withheld


def test_flat_rate_band_follows_the_annualized_payment():
    assert [wisconsin.flat_rate(b, 26) for b in (30, 704, 2_000, 50_000)] == [0.0354, 0.0465, 0.053, 0.0765]


PART_YEAR = {"federal_agi": 200_000, "wi_income": 100_000, "wi_exemptions": 2, "wi_itemized_interest": 20_000,
             "wi_property_tax": 5_000, "wi_withheld": 4_000,
             "wi_earned_income_by_person": [("a", 120_000), ("b", 30_000)]}


def test_part_year_return_prorates_tax_and_credits():
    r = wisconsin.wisconsin_return(PART_YEAR, 2025)
    tax_all = round(0.053 * 198_600 - 781.92)
    assert (r["wi1npr_32"], r["wi1npr_37"], r["wi1npr_38"]) == (0.5, 198_600, tax_all)
    assert (r["wi1npr_39"], r["wi1npr_40"], r["wi1npr_43c"]) == (round(tax_all * 0.5), 500, 150)
    assert r["wi1npr_48"] == 480 and r["wi1npr_52"] == round(tax_all * 0.5) - 500 - 150 - 480
    assert r["wi1npr_70"] == 4_000 - r["wi1npr_52"] and r["wi_owe_or_refund"] == -r["wi1npr_70"]


def test_full_year_return_uses_wisconsin_income_and_federal_fallbacks():
    i = {**{k: v for k, v in PART_YEAR.items() if k not in ("wi_income", "wi_itemized_interest",
                                                            "wi_property_tax")},
         "wi_subtractions": 10_000, "mortgage_interest": 20_000, "real_estate_tax": 5_000}
    r = wisconsin.wisconsin_return(i, 2026)
    assert (r["wi_income"], r["wi_taxable"]) == (190_000, 188_600)
    assert r["wi_net_tax"] == round(0.053 * 188_600 - 781.92) - 1_000 - 300 - 480
    assert "wi1npr_38" not in r


def test_return_check_includes_wisconsin_lines():
    rows = [("input", "wages", "a", 120_000), ("input", "wages", "b", 80_000),
            ("input", "wi_income", "", 100_000), ("input", "wi_exemptions", "", 2),
            ("input", "wi_earned_income", "a", 120_000), ("input", "wi_earned_income", "b", 30_000),
            ("filed", "wi1npr_32", "", 0.5), ("filed", "wi1npr_48", "", 480), ("filed", "wi1npr_38", "", 1)]
    facts = pd.DataFrame([{"year": "2025", "kind": k, "key": key, "person": p, "value": str(v)}
                          for k, key, p, v in rows])
    check = tax.check_against_return(facts).set_index("line")
    assert check.loc["wi1npr_32", "match"] and check.loc["wi1npr_48", "match"]
    assert not check.loc["wi1npr_38", "match"]


def stub(pay_date, stub_id, base, wi, person="alex", lines=("Regular Pay",)):
    common = {"pay_date": pay_date, "period_start": "2026-01-01", "period_end": "2026-01-14",
              "person": person, "employer": "Acme", "stub_id": stub_id}
    rows = [(lines[0], "income", base), ("FIT Taxable Wages", "memo", base), ("Gross", "memo", base),
            ("Federal Income Tax", "tax", 0.0), ("WI State Income Tax", "tax", wi)]
    return [{**common, "line_item": a, "kind": k, "amount": v, "ytd_amount": v} for a, k, v in rows]


def test_backtest_decodes_wt4_settings_and_flat_rates():
    regular = wisconsin.regular_withholding(4_000, 26, 2026, True, 4)
    rows = (stub("2026-01-16", "1", 4_000, regular) + stub("2026-01-30", "2", 4_000, regular + 25)
            + stub("2026-02-03", "3", 50_000, round(0.0765 * 50_000, 2), lines=("Stock Unit",))
            + stub("2026-02-13", "4", 4_000, 99.0))
    bt = wisconsin.backtest(pd.DataFrame(rows)).set_index("stub_id")
    assert bt.loc["1", "explanation"] == "WT-4: 4 exemptions"
    assert bt.loc["2", "explanation"] == "WT-4: 4 exemptions, 25 additional"
    assert bt.loc["3", "kind"] == "supplemental" and bt.loc["3", "explanation"].startswith("flat 7.65%")
    assert bt.loc["4", "explanation"].startswith("unexplained")
    assert "4 exemptions" in bt.loc["4", "explanation"]


def test_projection_adds_wisconsin_withholding_for_paychecks_and_vests():
    regular = wisconsin.regular_withholding(4_000, 26, 2026, True, 0)
    federal = withholding.regular_withholding(4_000, 26, 2026)
    vest = round(0.0765 * 20_000, 2)
    paid = [("2026-08-27", "v0", 20_000, vest, 0.22 * 20_000, "Stock Unit"),
            ("2026-09-28", "v1", 20_000, vest, 0.22 * 20_000, "Stock Unit"),
            ("2026-10-02", "r0", 4_000, regular, federal, "Regular Pay"),
            ("2026-10-16", "r1", 4_000, regular, federal, "Regular Pay"),
            ("2026-10-28", "v2", 20_000, vest, 0.22 * 20_000, "Stock Unit")]
    rows, ytd = [], {}
    for day, stub_id, base, wi, fit, line in paid:
        common = {"pay_date": day, "period_start": "2026-01-01", "period_end": "2026-01-14",
                  "person": "alex", "employer": "Acme", "stub_id": stub_id}
        for name, kind, amount in ((line, "income", base), ("FIT Taxable Wages", "memo", base),
                                   ("Gross", "memo", base), ("Federal Income Tax", "tax", fit),
                                   ("WI State Income Tax", "tax", wi)):
            ytd[name] = ytd.get(name, 0.0) + amount
            rows.append({**common, "line_item": name, "kind": kind, "amount": amount,
                         "ytd_amount": ytd[name]})
    pos = projection.positions(pd.DataFrame(rows), 2026)[("alex", "Acme")]
    assert (pos["paychecks_left"], pos["vests_left"]) == (5, 2)
    expected = 2 * regular + 3 * vest + 5 * regular + 2 * 0.0765 * 20_000
    assert pos["wi_withheld"] == pytest.approx(expected, abs=0.05)
    lower = projection.positions(pd.DataFrame(rows), 2026, {"stock_price": 0.8})[("alex", "Acme")]
    assert lower["wages"] == pytest.approx(pos["wages"] - 2 * 0.2 * 20_000)
    assert lower["wi_withheld"] == pytest.approx(pos["wi_withheld"] - 2 * 0.0765 * 0.2 * 20_000, abs=0.05)


def test_people_can_explain_a_residual_the_rules_cannot():
    decisions = pd.DataFrame([
        {"proposal_key": "wi:4", "kind": "withholding", "decided_at": "", "decision": "approve",
         "note": "lived in another state, employer here"},
        {"proposal_key": "wi:9", "kind": "withholding", "decided_at": "", "decision": "reject", "note": "x"},
        {"proposal_key": "federal:4", "kind": "withholding", "decided_at": "", "decision": "approve",
         "note": "y"}])
    explained = withholding.explained_by_people(decisions, "wi")
    assert explained == {"4": "lived in another state, employer here"}
    regular = wisconsin.regular_withholding(4_000, 26, 2026, True, 4)
    rows = stub("2026-01-16", "1", 4_000, regular) + stub("2026-02-13", "4", 4_000, 99.0)
    bt = wisconsin.backtest(pd.DataFrame(rows), explained).set_index("stub_id")
    assert bt.loc["4", "explanation"] == "explained by you: lived in another state, employer here"
    assert bt.loc["4", "residual"] != 0  # the number stays visible


def test_break_even_extra_withholding_uses_the_longest_paycheck_run_and_floors_at_zero():
    pos = {("a", "X"): {"paychecks_left": 5, "extra_withholding": 1_000.0},
           ("b", "Y"): {"paychecks_left": 2, "extra_withholding": 0.0}}
    assert projection.break_even_extra(pos, 2_500) == 1_500  # owe 2,500: 500 more on each of 5
    assert projection.break_even_extra(pos, -2_500) == 500
    assert projection.break_even_extra(pos, -9_000) == 0
    none_left = {("a", "X"): {"paychecks_left": 0, "extra_withholding": 0.0}}
    assert projection.break_even_extra(none_left, 1) is None
