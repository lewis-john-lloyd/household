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
    lines, notes, detail = projection.project(2026, stubs, sales, facts)
    lines = lines.set_index("scenario")
    as_is = detail[detail["scenario"] == "as is"].set_index("key")
    assert as_is.loc["11a", "value"] == lines.loc["as is", "agi"]
    assert set(detail["form"]) == {"Form 1040", "Wisconsin Form 1"}
    assert lines["total_tax"].nunique() == 1
    assert lines.loc["no extra withholding", "withheld"] < lines.loc["as is", "withheld"]
    assert any("27 paychecks" in n for n in notes)


def test_the_return_lines_read_like_the_forms_and_sit_beside_a_filed_return():
    stubs = stubs_through(24)
    facts = pd.DataFrame([{"year": 2026, "kind": "filed", "key": "24", "person": "", "value": 9000},
                          {"year": 2026, "kind": "filed", "key": "11a", "person": "", "value": 120000}])
    sales = pd.DataFrame(columns=["sale_date", "gain", "term"])
    _, _, detail = projection.project(2026, stubs, sales, facts)
    federal = detail[(detail["scenario"] == "as is") & (detail["form"] == "Form 1040")]
    assert federal["line"].tolist()[:4] == ["1z", "7", "9", "11"]  # no interest or dividends: left out
    assert federal.set_index("key").loc["24", "filed"] == 9000
    assert federal.set_index("key").loc["15", "filed"] is None or pd.isna(federal.set_index("key").loc["15", "filed"])
    assert detail[detail["scenario"] != "as is"]["filed"].isna().all()
    assert federal["label"].iloc[-1] in ("Refund", "Amount owed")


def test_the_withholding_plan_lands_the_year_on_the_target_refund():
    stubs = stubs_through(20)
    facts = pd.DataFrame(columns=["year", "kind", "key", "person", "value"])
    sales = pd.DataFrame(columns=["sale_date", "gain", "term"])
    plan = projection.withholding_plan(2026, stubs, sales, facts, target=2000, wi_target=0).set_index("return")
    federal = plan.loc["federal"]
    assert federal["paychecks_left"] == 7 and federal["current_extra"] == 200  # Oct 9 to Dec 31
    # withholding that much more on the paychecks left moves the refund to the target
    # more refund than the target even with no extra: the plan says where it lands instead
    assert federal["projected"] > 2000 and federal["suggested_extra"] == 0
    assert federal["lands_at"] == federal["projected"] - 200 * 7  # the 200 extra a paycheck stops
    assert "still" in federal["note"]
    owing = projection.withholding_plan(2026, stubs, sales, facts, target=9000).set_index("return").loc["federal"]
    assert abs(owing["lands_at"] - 9000) < 1  # reachable: lands on it
    assert plan.loc["wisconsin", "note"].startswith("no Wisconsin settings")  # the stubs have no Wisconsin line


def test_elections_show_the_year_so_far_and_say_a_backdoor_roth_is_not_tracked():
    plans = projection.elections(2026, stubs_through(24)).set_index("plan")
    assert plans.loc["401(k) elective deferral", "ytd"] == 300 * 24
    assert plans.loc["Backdoor Roth IRA", "status"].startswith("not tracked")


def test_the_mega_backdoor_row_says_whether_the_plan_converts_after_tax_money():
    rows = stubs_through(10).to_dict("records")
    for stub in {r["stub_id"]: r for r in rows}.values():  # an after-tax 401(k) contribution on every stub
        rows.append({**stub, "line_item": "401k After-Tax", "kind": "post_tax", "amount": 50.0,
                     "ytd_amount": 50.0 * int(stub["stub_id"])})
    stubs = pd.DataFrame(rows)
    plain = projection.elections(2026, stubs).set_index("plan").loc["Mega backdoor Roth (after-tax 401(k))"]
    assert plain["ytd"] == 500 and plain["status"].endswith("(set after_tax_401k_converts_to_roth if the plan does it)")
    auto = projection.elections(2026, stubs, after_tax_converts=True).set_index("plan")
    assert auto.loc["Mega backdoor Roth (after-tax 401(k))", "status"].endswith("converts it to Roth automatically")
