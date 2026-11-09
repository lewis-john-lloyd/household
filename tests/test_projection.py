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
    common = {
        "pay_date": pay_date,
        "period_start": "2026-01-01",
        "period_end": "2026-01-14",
        "person": "alex",
        "employer": "Acme",
        "stub_id": stub_id,
        "doc_id": "d",
    }
    n = int(stub_id)
    items = [
        ("Regular Pay", "income", gross, gross * n),
        ("401k", "pre_tax", k401, k401 * n),
        ("HSA Employee", "pre_tax", hsa, hsa * n),
        ("Medical", "pre_tax", pretax - k401 - hsa, (pretax - k401 - hsa) * n),
        ("Federal Income Tax", "tax", fit, fit * n),
        ("Employee Medicare", "tax", gross * 0.0145, gross * 0.0145 * n),
        ("Gross", "memo", gross, gross * n),
        ("FIT Taxable Wages", "memo", taxable, taxable * n),
    ]
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
    facts = pd.DataFrame(
        [
            {"year": 2026, "kind": "filed", "key": "24", "person": "", "value": 9000},
            {"year": 2026, "kind": "filed", "key": "11a", "person": "", "value": 120000},
        ]
    )
    sales = pd.DataFrame(columns=["sale_date", "gain", "term"])
    _, _, detail = projection.project(2026, stubs, sales, facts)
    federal = detail[(detail["scenario"] == "as is") & (detail["form"] == "Form 1040")]
    assert federal["line"].tolist()[:4] == ["1z", "7", "9", "11"]  # no interest or dividends: left out
    assert federal.set_index("key").loc["24", "filed"] == 9000
    assert federal.set_index("key").loc["15", "filed"] is None or pd.isna(federal.set_index("key").loc["15", "filed"])
    assert set(detail["scenario"]) == {"as is"}  # the forms show the year as it stands
    assert federal["label"].iloc[-1] in ("Refund", "Amount owed")


def test_the_withholding_plan_gives_a_refund_no_balance_and_owing_for_each_return():
    stubs = stubs_through(20)  # last paid Sep 25: seven paychecks left, Oct 9 to Dec 31
    facts = pd.DataFrame(columns=["year", "kind", "key", "person", "value"])
    sales = pd.DataFrame(columns=["sale_date", "gain", "term"])
    plan = projection.withholding_plan(2026, stubs, sales, facts, target=2000)
    assert [(r["return"], r["target"]) for r in plan.to_dict("records")] == [
        ("federal", 2000),
        ("federal", 0),
        ("federal", -2000),
        ("wisconsin", 2000),
        ("wisconsin", 0),
        ("wisconsin", -2000),
    ]
    federal = plan[plan["return"] == "federal"].set_index("target")
    assert (federal["paychecks_left"] == 7).all() and (federal["current_extra"] == 200).all()
    # the stubs over-withhold: even none extra leaves more than a 2,000 refund, so each target lands where none does
    assert federal["projected"].iloc[0] - 200 * 7 > 2000
    assert (federal["suggested_extra"] == 0).all() and (federal["lands_at"] == federal["projected"] - 200 * 7).all()
    assert federal["penalty_free"].all()  # a refund owes no penalty
    # stubs that under-withhold reach every target, and owing 2,000 is checked for the penalty
    under = stubs.copy()
    fit = under["line_item"] == "Federal Income Tax"
    under.loc[fit, "amount"] = under.loc[fit, "amount"] * 0.5
    under.loc[fit, "ytd_amount"] = under.loc[fit, "ytd_amount"] * 0.5
    short = projection.withholding_plan(2026, under, sales, facts, target=2000)
    short = short[short["return"] == "federal"].set_index("target")
    assert all(abs(short.loc[goal, "lands_at"] - goal) < 1 for goal in (2000, 0))
    assert short.loc[0, "suggested_extra"] < short.loc[2000, "suggested_extra"]
    # owing 2,000 would take less than no extra withholding: it lands as close as none gets, and says if a
    # penalty is possible
    assert short.loc[-2000, "suggested_extra"] == 0 and short.loc[-2000, "lands_at"] > -2000
    assert short.loc[0, "penalty_free"] and short.loc[-2000, "penalty_free"] in (True, False)
    assert plan[plan["return"] == "wisconsin"]["note"].iloc[0].startswith("no Wisconsin settings")


def test_the_penalty_rule():
    from household import tax

    assert tax.penalty_free(owe=999, withheld=0, total_tax=50_000)  # under 1,000 owed
    assert tax.penalty_free(owe=5_000, withheld=45_000, total_tax=50_000)  # 90% of this year's tax
    assert not tax.penalty_free(owe=5_000, withheld=40_000, total_tax=50_000)
    assert tax.penalty_free(owe=5_000, withheld=40_000, total_tax=50_000, harbor=39_000)  # last year's safe harbor


def test_elections_show_the_year_so_far_and_say_a_backdoor_roth_is_not_tracked():
    plans = projection.elections(2026, stubs_through(24)).set_index("plan")
    assert plans.loc["401(k) elective deferral", "ytd"] == 300 * 24
    assert plans.loc["Backdoor Roth IRA", "status"].startswith("not tracked")


def test_the_mega_backdoor_row_says_whether_the_plan_converts_after_tax_money():
    rows = stubs_through(10).to_dict("records")
    for stub in {r["stub_id"]: r for r in rows}.values():  # an after-tax 401(k) contribution on every stub
        rows.append(
            {
                **stub,
                "line_item": "401k After-Tax",
                "kind": "post_tax",
                "amount": 50.0,
                "ytd_amount": 50.0 * int(stub["stub_id"]),
            }
        )
    stubs = pd.DataFrame(rows)
    plain = projection.elections(2026, stubs).set_index("plan").loc["Mega backdoor Roth (after-tax 401(k))"]
    assert plain["ytd"] == 500 and plain["status"].endswith("; Roth conversion not tracked")
    auto = projection.elections(2026, stubs, after_tax_converts=True).set_index("plan")
    assert auto.loc["Mega backdoor Roth (after-tax 401(k))", "status"].endswith("converts it to Roth automatically")


def test_wisconsin_lines_carry_the_form_s_line_numbers_and_say_when_they_are_last_year_s():
    stubs = stubs_through(24)
    facts = pd.DataFrame(columns=["year", "kind", "key", "person", "value"])
    sales = pd.DataFrame(columns=["sale_date", "gain", "term"])
    _, notes, detail = projection.project(2026, stubs, sales, facts)
    wi = detail[(detail["scenario"] == "as is") & (detail["form"] == "Wisconsin Form 1")].set_index("key")
    assert wi.loc["wi_income", "line"] == "7" and wi.loc["wi_net_tax", "line"] == "22"
    assert wi.loc["wi_owe_or_refund", "line"] == ("40" if wi.loc["wi_owe_or_refund", "label"] == "Refund" else "45")
    assert "Wisconsin 2026: Form 1 line numbers are the 2025 form's until the 2026 form is on file" in notes


def test_a_limited_purpose_fsa_is_projected_against_the_health_fsa_limit():
    rows = stubs_through(24).to_dict("records")
    for stub in {r["stub_id"]: r for r in rows}.values():
        rows.append(
            {
                **stub,
                "line_item": "Ltd Purpose FSA",
                "kind": "pre_tax",
                "amount": 50.0,
                "ytd_amount": 50.0 * int(stub["stub_id"]),
            }
        )
    fsa = projection.elections(2026, pd.DataFrame(rows)).set_index("plan").loc["Limited-purpose FSA"]
    assert (fsa["person"], fsa["ytd"], fsa["projected"], fsa["limit"]) == ("alex", 50 * 24, 50 * 26, 3_400)
    assert fsa["status"] == "room left"


def jobs(*rows):
    return pd.DataFrame(
        [
            {"effective": e, "person": p, "employer": m, "salary": "", "band": "", "rating": "", "fte": "", "note": ""}
            for e, p, m in rows
        ]
    )


def test_a_job_goes_on_until_comp_events_records_the_next_one():
    left = stubs_through(10)  # alex's last paycheck from Acme Widgets: 2026-05-08
    left["employer"] = "Acme Widgets, LLC"
    stubs = pd.concat([left, stubs_through(24)], ignore_index=True)  # Acme pays through 2026-11-20
    # no job change recorded: both jobs go on, whatever the gap in stubs
    pos = projection.positions(stubs, 2026)
    assert pos[("alex", "Acme Widgets, LLC")]["paychecks_left"] == 17 and pos[("alex", "Acme")]["paychecks_left"] == 3
    # a raise at Acme Widgets ends nothing; the move to Willow St on May 18 ends it
    events = jobs(
        ("2025-01-01", "alex", "Acme Widgets"),
        ("2026-03-01", "alex", "Acme Widgets"),
        ("2026-05-18", "alex", "Willow St"),
        ("2026-06-01", "sam", "Quill & Ink"),
    )
    gone = projection.positions(stubs, 2026, comp_events=events)[("alex", "Acme Widgets, LLC")]
    assert gone["paychecks_left"] == 0 and gone["wages"] == 10 * 4_500
    assert any("ends 2026-05-18" in n for n in gone["notes"])
    # a change later in the year stops the paychecks from then
    later = projection.positions(stubs, 2026, comp_events=jobs(("2026-12-10", "alex", "Willow St")))
    assert later[("alex", "Acme")]["paychecks_left"] == 1  # Dec 4, not Dec 18 or 31
    # a job the person went back to goes on: its stint starts at the latest row naming it
    back = jobs(("2025-01-01", "alex", "Acme"), ("2025-06-01", "alex", "Willow St"), ("2026-01-01", "alex", "Acme"))
    assert projection.positions(stubs_through(24), 2026, comp_events=back)[("alex", "Acme")]["paychecks_left"] == 3


def test_a_year_that_is_over_is_not_projected():
    stubs = pd.concat([stubs_through(24), pd.DataFrame(regular("2027-01-08", "1"))], ignore_index=True)
    over = projection.positions(stubs, 2026)[("alex", "Acme")]
    assert over["paychecks_left"] == 0 and over["wages"] == 24 * 4_500 and over["notes"] == []


def facts_of(rows, year="2025"):
    return pd.DataFrame([{"year": year, "kind": k, "key": key, "person": p, "value": str(v)} for k, key, p, v in rows])


def test_a_filed_year_is_laid_out_from_its_own_figures_beside_every_filed_line():
    rows = [
        ("input", "wages", "alex", 90_000),
        ("input", "wages", "sam", 60_000),
        ("input", "federal_withheld", "alex", 15_000),
        ("input", "federal_withheld", "sam", 8_000),
        ("input", "taxable_interest", "", 20),
        ("input", "capital_gain_short", "", 1_000),
        ("input", "wi_income", "", 75_510),
        ("input", "wi_exemptions", "", 2),
        ("input", "wi_withheld", "", 4_000),
        ("input", "wi_earned_income", "alex", 50_000),
        ("input", "wi_earned_income", "sam", 25_000),
        ("input", "pa_compensation", "", 50_000),
        ("input", "pa_withheld", "", 1_500),
    ]
    facts = facts_of(rows)
    r = projection.tax.federal_return(projection.tax.inputs_from_facts(facts, 2025), 2025)
    filed = [
        ("filed", "11a", "", r["11a"]),
        ("filed", "24", "", r["24"]),
        ("filed", "wi1npr_32", "", 0.5),
        ("filed", "pa40_12", "", 50),
    ]  # the last one off
    detail = projection.as_filed(2025, pd.concat([facts, facts_of(filed)], ignore_index=True))
    assert list(dict.fromkeys(detail["form"])) == ["Form 1040", "Wisconsin Form 1NPR", "Pennsylvania PA-40"]
    by = detail.set_index("key")
    assert by.loc["1z", "value"] == 150_000 and by.loc["taxable_interest", "line"] == "2b"
    assert by.loc["11a", "filed"] == by.loc["11a", "value"] and pd.isna(by.loc["1z", "filed"])
    assert (by.loc["wi1npr_32", "value"], by.loc["wi1npr_32", "line"]) == (0.5, "32")
    assert by.loc["pa40_12", "value"] == round(0.0307 * 50_000) != by.loc["pa40_12", "filed"]
    assert detail["label"].iloc[-1] in ("Refund", "Amount owed") and set(detail["scenario"]) == {"as is"}
    assert projection.has_return(facts, 2025) and not projection.has_return(facts, 2026)
