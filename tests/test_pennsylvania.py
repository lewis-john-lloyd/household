"""Pennsylvania return, Schedule G-L and withholding on synthetic numbers."""

import pandas as pd

from household import pennsylvania, tax

RETURN = {"pa_compensation": 100_000, "pa_interest": 50, "pa_dividends": 200, "pa_gains": -3_000,
          "pa_other_deductions": 10_000, "pa_withheld": 2_900,
          "pa_gl_compensation_pa": 20_000, "pa_gl_compensation_other": 10_000,
          "pa_gl_dividends_other": 500, "pa_gl_other_tax_due": 600, "pa_gl_other_tax_paid": 650}


def test_classes_do_not_offset_and_tax_is_flat():
    r = pennsylvania.pa_return(RETURN, 2025)
    assert r["pa40_9"] == 100_250  # the loss in gains does not offset other classes
    assert (r["pa40_11"], r["pa40_12"]) == (90_250, round(0.0307 * 90_250))


def test_schedule_gl_limits_the_credit_three_ways():
    gl = pennsylvania.schedule_gl(RETURN, 2025)
    assert gl["gl_3"] == 10_000  # only income both states taxed
    assert gl["gl_4c"] == 600  # the lesser of tax due and tax paid
    # the share of other-state income Pennsylvania did not tax comes off the credit
    assert gl["gl_4d"] == round((1 - round(10_000 / 10_500, 6)) * 600)
    assert gl["gl_5"] == 307 and gl["gl_6"] == min(600 - gl["gl_4d"], 307)
    r = pennsylvania.pa_return(RETURN, 2025)
    assert r["pa40_22"] == gl["gl_6"] and r["pa40_24"] == 2_900 + gl["gl_6"]
    assert r["pa40_29"] == max(r["pa40_24"] - r["pa40_12"], 0)


def test_return_check_includes_pennsylvania_lines():
    rows = [("input", "pa_compensation", 50_000), ("filed", "pa40_12", 1_535), ("filed", "pa40_11", 1)]
    facts = pd.DataFrame([{"year": "2025", "kind": k, "key": key, "person": "", "value": str(v)}
                          for k, key, v in rows])
    check = tax.check_against_return(facts).set_index("line")
    assert check.loc["pa40_12", "match"] and not check.loc["pa40_11", "match"]


def stub(stub_id, pa, gross=5_000.0, gtl=10.0, medical=100.0, k401=500.0, fsa=200.0):
    common = {"pay_date": "2025-03-07", "period_start": "2025-02-16", "period_end": "2025-03-01",
              "person": "alex", "employer": "Acme", "stub_id": stub_id}
    rows = [("Salary", "income", gross - gtl), ("Group Term Life", "income", gtl),
            ("Gross", "memo", gross), ("Medical", "pre_tax", medical), ("401K Pretax", "pre_tax", k401),
            ("FSA Dependent", "pre_tax", fsa), ("PA State Income Tax", "tax", pa)]
    return [{**common, "line_item": a, "kind": k, "amount": v, "ytd_amount": v} for a, k, v in rows]


def test_withholding_taxes_401k_but_not_section_125_benefits():
    base = 5_000 - 10 - 100 - 200  # gross less group-term life, medical and FSA; 401(k) stays in
    rows = (stub("1", round(0.0307 * base, 2)) + stub("2", 0.0)
            + stub("3", round(0.0307 * (base + 200), 2)) + stub("4", 99.0))
    bt = pennsylvania.backtest(pd.DataFrame(rows)).set_index("stub_id")
    assert bt.loc["1", "base"] == base and bt.loc["1", "residual"] == 0
    assert bt.loc["2", "kind"] == "none"
    assert bt.loc["3", "explanation"].endswith("with FSA Dependent taxed on this stub")
    assert bt.loc["4", "explanation"].startswith("unexplained")
