"""Taxes over the years, from made-up return facts: each split of a year adds up to its whole."""

import pandas as pd

from household import pennsylvania, tax, taxhistory


def facts(rows):
    return pd.DataFrame([{"year": y, "kind": k, "key": key, "person": p, "value": str(v), "source_doc": "d", "note": ""}
                         for y, k, key, p, v in rows])


ROWS = [
    # an older year: the law is not on file, so it is shown and not recomputed
    ("2019", "input", "wages", "alex", 90_000), ("2019", "input", "wages", "sam", 60_000),
    ("2019", "input", "capital_gain_short", "", -5_000), ("2019", "input", "pension_taxable", "", 20),
    ("2019", "input", "pa_compensation", "", 150_000), ("2019", "input", "pa_gains", "", -5_000),
    ("2019", "filed", "1z", "", 150_000), ("2019", "filed", "11a", "", 147_020), ("2019", "filed", "12e", "", 24_000),
    ("2019", "filed", "15", "", 123_020), ("2019", "filed", "16", "", 20_000), ("2019", "filed", "20", "", 4_000),
    ("2019", "filed", "23", "", 0), ("2019", "filed", "24", "", 16_000),
    ("2019", "filed", "pa40_9", "", 150_000), ("2019", "filed", "pa40_11", "", 150_000), ("2019", "filed", "pa40_12", "", 4_605),
    ("2019", "filed", "pa40_23", "", 60),
]


def test_each_split_of_each_year_adds_up_to_its_whole():
    h = taxhistory.history(facts(ROWS))
    sums = h.groupby(["year", "jurisdiction", "split"]).agg(parts=("value", "sum"), whole=("total", "first"))
    assert ((sums["parts"] - sums["whole"]).abs() < 0.01).all()
    fed = h[(h["jurisdiction"] == "federal")].set_index(["split", "part"])["value"]
    assert fed[("came", "Wages, alex")] == 90_000 and fed[("came", "Capital gains")] == -3_000  # the most deducted
    assert fed[("came", "Other income")] == 20  # the pension
    assert (fed[("taxed", "Taxable income")], fed[("taxed", "Deducted")]) == (123_020, 24_000)
    # federal "went" carries the state's tax after its credits; credits are part of what was kept
    assert (fed[("went", "Federal income tax")], fed[("went", "State income tax")], fed[("went", taxhistory.CREDITS)]) == (16_000, 4_545, 4_000)
    assert fed[("went", taxhistory.KEPT)] == 147_020 - 16_000 - 4_545 - 4_000
    pa = h[h["jurisdiction"] == "pa"].set_index(["split", "part"])["value"]
    assert pa[("went", "State income tax")] == 4_545 and ("came", "Capital gains") not in pa.index  # a loss adds nothing


def test_a_year_without_its_tax_law_is_not_recomputed_and_pa_counts_its_other_credits():
    assert tax.check_against_return(facts(ROWS)).empty
    pa = pennsylvania.pa_return({"pa_compensation": 100_000, "pa_withheld": 3_000, "pa_other_credits": 60}, 2025)
    assert (pa["pa40_23"], pa["pa40_24"], pa["pa40_29"]) == (60, 3_060, 0)
    assert pa["pa40_26"] == 10
