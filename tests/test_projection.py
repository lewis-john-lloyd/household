"""Projection of a year in progress on synthetic stubs."""

from datetime import date

import pandas as pd

from household import projection, withholding


def test_remaining_pay_dates_move_new_years_day_to_december_31():
    dates = projection.remaining_pay_dates("2026-12-04", 2026)
    assert dates == [date(2026, 12, 18), date(2026, 12, 31)]
    assert projection.remaining_pay_dates("2026-12-18", 2026) == [date(2026, 12, 31)]
    assert projection.remaining_pay_dates("2025-12-19", 2025) == []


def regular(pay_date, stub_id, gross=5_000.0, pretax=500.0, fit=None, k401=300.0, hsa=100.0):
    taxable = gross - pretax
    fit = withholding.regular_withholding(taxable, 26, 2026) + 200 if fit is None else fit
    common = {"pay_date": pay_date, "period_start": "2026-01-01", "period_end": "2026-01-14",
              "person": "alex", "employer": "Acme", "stub_id": stub_id, "doc_id": "d"}
    n = int(stub_id)
    items = [("Regular Pay", "income", gross, gross * n), ("401k", "pre_tax", k401, k401 * n),
             ("HSA Employee", "pre_tax", hsa, hsa * n),
             ("Medical", "pre_tax", pretax - k401 - hsa, (pretax - k401 - hsa) * n),
             ("Federal Income Tax", "tax", fit, fit * n), ("Employee Medicare", "tax", gross * 0.0145,
                                                           gross * 0.0145 * n),
             ("Gross", "memo", gross, gross * n), ("FIT Taxable Wages", "memo", taxable, taxable * n)]
    return [{**common, "line_item": a, "kind": k, "amount": v, "ytd_amount": y} for a, k, v, y in items]


def stubs_through(last_index):
    rows = []
    d = date(2026, 1, 2)
    for n in range(1, last_index + 1):
        rows += regular(d.isoformat(), str(n))
        d = date.fromordinal(d.toordinal() + 14)
    return pd.DataFrame(rows)


def test_elections_count_only_paychecks_with_deductions():
    stubs = stubs_through(24)  # last pay date 2026-11-20; three more: Dec 4, Dec 18, Dec 31
    plans = projection.elections(2026, stubs).set_index("plan")
    assert plans.loc["401(k) elective deferral", "projected"] == 300 * 26  # the 27th has none
    assert plans.loc["HSA, family (employee + employer)", "projected"] == 100 * 26


def test_scenarios_change_withholding_not_tax():
    stubs = stubs_through(24)
    facts = pd.DataFrame(columns=["year", "kind", "key", "person", "value"])
    sales = pd.DataFrame(columns=["sale_date", "gain", "term"])
    lines, notes = projection.project(2026, stubs, sales, facts)
    lines = lines.set_index("scenario")
    assert lines["total_tax"].nunique() == 1
    assert lines.loc["no extra withholding", "withheld"] < lines.loc["as is", "withheld"]
    assert any("27 paychecks" in n for n in notes)
