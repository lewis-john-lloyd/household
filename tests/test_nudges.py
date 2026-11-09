from datetime import date

import pandas as pd

from household import nudges, settings


def params(**values):
    return pd.DataFrame(
        [
            {"year": "", "jurisdiction": "household", "key": k.replace("__", ":"), "value": v, "source": "t"}
            for k, v in values.items()
        ],
        columns=["year", "jurisdiction", "key", "value", "source"],
    )


def stub(pay_date, line, amount, ytd, person="alex"):
    return {
        "pay_date": pay_date,
        "person": person,
        "employer": "Acme Widgets",
        "stub_id": pay_date,
        "line_item": line,
        "kind": "pre_tax",
        "amount": amount,
        "ytd_amount": ytd,
    }


STUBS = pd.DataFrame(
    [
        stub("2025-12-12", "Limited Purpose FSA", 50, 1250),
        stub("2025-12-26", "Limited Purpose FSA", 50, 1300),
        stub("2025-12-26", "Dependent Care FSA", 100, 5000),
        stub("2026-10-30", "Dependent Care FSA", 100, 2200),
    ]
)

SET = dict(
    fsa_payee_pattern="WILLOW ST",
    lpfsa_categories="family, gifts",
    dcfsa_categories="kids",
    lpfsa_claim_deadline="03-31",
    dcfsa_claim_deadline__2025="2026-04-15",
    fsa_claims_url="https://claims.example",
    views_url="https://views.example/exec",
)


def txn(day, amount, category, payee="WILLOW ST BENEFITS", note=""):
    return {"date": day, "amount": amount, "payee_raw": payee, "category": category, "note": note}


def test_settings_say_which_values_cant_be_used():
    p = params(
        payroll_url="http://payroll.example",
        target_refund="lots",
        lpfsa_claim_deadline="3/31",
        dcfsa_claim_deadline__2025="2026-02-30",
        fsa_payee_pattern="(",
        benefits_url="https://ok.example",
    )
    problems = {k: why for k, _, why in settings.check(p)}
    assert problems == {
        "payroll_url": "is not an https link",
        "target_refund": "is not a number",
        "lpfsa_claim_deadline": "is not a month and day (MM-DD)",
        "dcfsa_claim_deadline:2025": "is not a date (YYYY-MM-DD)",
        "fsa_payee_pattern": problems["fsa_payee_pattern"],
    }
    assert problems["fsa_payee_pattern"].startswith("is not a pattern")
    assert settings.link(p, "payroll_url") == "" and settings.link(p, "benefits_url") == "https://ok.example"


def test_a_claim_deadline_is_the_month_and_day_of_the_next_year_or_one_plan_years_date():
    p = params(**SET)
    assert settings.claim_deadline(p, "lpfsa_claim_deadline", 2025) == date(2026, 3, 31)
    assert settings.claim_deadline(p, "dcfsa_claim_deadline", 2025) == date(2026, 4, 15)
    assert settings.claim_deadline(p, "dcfsa_claim_deadline", 2026) is None


def test_fsa_balances_count_deposits_in_each_accounts_categories_last_years_money_first():
    categorized = pd.DataFrame(
        [
            txn("2025-10-16", 90, "family"),  # 2025 limited-purpose
            txn("2025-11-02", 60, "media"),  # not an FSA category
            txn("2025-11-03", 40, "family", "ACME GROCER"),  # not the administrator: a refund, not a reimbursement
            txn("2026-01-09", 4000, "kids"),  # in the claim window: 2025's dependent care first
            txn("2026-02-18", 1210, "gifts"),  # 2025 limited-purpose, in the claim window: fills it
            txn("2026-03-02", 1500, "kids"),  # 1000 left of 2025's dependent care, then 500 for 2026
            txn("2026-03-20", 70, "kids", note="fsa 2026"),  # the note puts it in 2026
            txn("2026-05-01", -50, "kids"),  # money out is never a reimbursement
        ]
    )
    out = nudges.fsa_balances(STUBS, categorized, params(**SET), date(2026, 11, 6))
    got = {
        (r["year"], r["account"]): (r["elected"], r["reimbursed"], r["left"], r["claim_deadline"])
        for r in out.to_dict("records")
    }
    assert got[(2025, "lpfsa")] == (1300, 1300, 0, "2026-03-31")
    assert got[(2025, "dcfsa")] == (5000, 5000, 0, "2026-04-15")
    # 2026's dependent care: 2200 so far and 100 on each of the paychecks left this year
    elected_2026 = 2200 + 100 * 4
    assert got[(2026, "dcfsa")] == (elected_2026, 570, elected_2026 - 570, "")
    assert (2026, "lpfsa") not in got
    # this year's election, when compute has the elections report, is its projection
    elections = pd.DataFrame([{"year": 2026, "plan": "dependent-care FSA", "projected": 2500}])
    out = nudges.fsa_balances(STUBS, categorized, params(**SET), date(2026, 11, 6), elections)
    assert out[(out["year"] == 2026)]["elected"].tolist() == [2500]


def test_without_the_fsa_settings_nothing_counts_as_reimbursed():
    out = nudges.fsa_balances(STUBS, pd.DataFrame([txn("2026-01-09", 4000, "kids")]), params(), date(2026, 11, 6))
    assert out[out["year"] == 2025]["reimbursed"].tolist() == [0, 0]


def fsa(year, account, elected, reimbursed):
    return {
        "year": year,
        "account": account,
        "elected": elected,
        "reimbursed": reimbursed,
        "left": elected - reimbursed,
        "claim_deadline": "",
    }


def shown(rows, day):
    d = day.isoformat()
    return rows[(rows["show_from"] <= d) & ((rows["show_until"] == "") | (rows["show_until"] >= d))]


def test_last_years_fsa_money_is_in_every_email_from_january_until_its_deadline():
    balances = pd.DataFrame(
        [fsa(2025, "lpfsa", 1300, 1000), fsa(2025, "dcfsa", 5000, 5000), fsa(2026, "dcfsa", 2600, 0)]
    )
    for day, items in (
        (date(2025, 12, 31), []),
        (date(2026, 1, 1), ["Limited-purpose FSA 2025"]),
        (date(2026, 3, 31), ["Limited-purpose FSA 2025"]),
    ):
        rows = nudges.build(day, params(**SET), fsa=balances)
        assert shown(rows, day)["item"].tolist() == items
    after = nudges.build(date(2026, 4, 1), params(**SET), fsa=balances)
    assert after.empty  # past the deadline there is nothing left to do about it
    row = nudges.build(date(2026, 1, 3), params(**SET), fsa=balances).iloc[0]
    assert row["detail"] == "300 of 1,300 not reimbursed yet; claims due March 31"
    assert (row["group"], row["link"]) == ("Claim FSA reimbursements", "https://claims.example")


def test_an_unset_deadline_is_taken_as_march_31_and_the_email_says_to_set_it():
    balances = pd.DataFrame([fsa(2025, "dcfsa", 5000, 4000)])
    p = params(fsa_payee_pattern="WILLOW ST", lpfsa_categories="family", dcfsa_categories="kids")
    row = nudges.build(date(2026, 1, 3), p, fsa=balances).iloc[0]
    assert row["show_until"] == "2026-03-31" and "set dcfsa_claim_deadline" in row["detail"]


def test_fsa_tracking_without_its_settings_asks_for_them_rather_than_nagging_about_every_balance():
    rows = nudges.build(date(2026, 1, 3), params(), fsa=pd.DataFrame([fsa(2025, "dcfsa", 5000, 0)]))
    assert rows[rows["group"] == "Fix a setting"]["item"].tolist() == [
        "fsa_payee_pattern, lpfsa_categories, dcfsa_categories"
    ]


def plan_row(ret, projected, suggested, now=0.0):
    return {
        "return": ret,
        "person": "alex",
        "employer": "Acme Widgets",
        "paychecks_left": 4,
        "vest_rate": 0.35,
        "projected": projected,
        "target": 2000.0,
        "current_extra": now,
        "suggested_extra": suggested,
        "lands_at": 2000.0,
        "penalty_free": None,
        "note": "",
    }


def test_withholding_far_from_the_target_is_due_in_the_30_days_before_the_last_paycheck():
    plan = pd.DataFrame([plan_row("federal", -1000.0, 750.0), plan_row("wisconsin", 1800.0, 0.0)])
    stubs = pd.DataFrame([stub("2026-10-30", "Dependent Care FSA", 100, 2200)])  # last paycheck: December 25
    rows = nudges.build(date(2026, 11, 6), params(payroll_url="https://payroll.example"), plan=plan, stubs=stubs)
    assert rows["item"].tolist() == ["Federal (W-4 Step 4(c)) withholding"]  # Wisconsin is within 500 of the target
    row = rows.iloc[0]
    assert (row["show_from"], row["show_until"], row["link"]) == ("2026-11-25", "2026-12-25", "https://payroll.example")
    assert row["detail"] == (
        "projected owing 1,000 against a target refund of 2,000: set the extra withholding to "
        "750.00 a paycheck (now 0.00) for the last 4 paychecks"
    )
    tight = nudges.build(date(2026, 11, 6), params(withholding_tolerance="5000"), plan=plan, stubs=stubs)
    assert tight.empty


def test_withholding_says_what_still_moves_it_and_skips_a_return_nothing_can_move():
    # too large a refund: dropping the extra to nothing still leaves too much, so the supplemental rate is next;
    # Wisconsin is withheld at nothing extra already and has no supplemental lever, so there is nothing to do
    fed = plan_row("federal", 9000.0, 0.0, now=80.0) | {"lands_at": 8680.0}
    wi = plan_row("wisconsin", 6000.0, 0.0, now=0.0) | {"lands_at": 6000.0}
    stubs = pd.DataFrame([stub("2026-10-30", "Dependent Care FSA", 100, 2200)])
    rows = nudges.build(date(2026, 11, 6), params(), plan=pd.DataFrame([fed, wi]), stubs=stubs)
    assert rows["detail"].tolist() == [
        "projected a refund of 9,000 against a target refund of 2,000: set the extra withholding to 0.00 a paycheck "
        "(now 80.00) for the last 4 paychecks, which gets to a refund of 8,680; then lower the supplemental rate for "
        "vests and bonuses (now 35%) to close the rest"
    ]
    already = plan_row("federal", 9000.0, 0.0, now=0.0) | {"lands_at": 9000.0}
    rows = nudges.build(date(2026, 11, 6), params(), plan=pd.DataFrame([already]), stubs=stubs)
    assert rows["detail"].tolist() == [
        "projected a refund of 9,000 against a target refund of 2,000: lower the "
        "supplemental rate for vests and bonuses (now 35%) to close the rest"
    ]


def test_a_401k_off_its_limit_is_due_before_the_last_paycheck():
    elections = pd.DataFrame(
        [
            {
                "year": 2026,
                "person": "alex",
                "employer": "Acme Widgets",
                "plan": "401(k) elective deferral",
                "ytd": 20000,
                "projected": 22000,
                "limit": 24500,
                "gap": 2500,
                "status": "room left",
            },
            {
                "year": 2026,
                "person": "alex",
                "employer": "Acme Widgets",
                "plan": "Limited-purpose FSA",
                "ytd": 1000,
                "projected": 1300,
                "limit": 3400,
                "gap": 2100,
                "status": "room left",
            },
        ]
    )  # elections are out of scope
    stubs = pd.DataFrame([stub("2026-10-30", "Dependent Care FSA", 100, 2200)])
    rows = nudges.build(
        date(2026, 11, 6), params(benefits_url="https://benefits.example"), elections=elections, stubs=stubs
    )
    assert rows["item"].tolist() == ["alex 401(k) elective deferral"]
    assert "2,500 short of the 24,500 limit" in rows.iloc[0]["detail"]
    assert rows.iloc[0]["show_until"] == "2026-12-25"


def test_downloads_and_review_are_due_once_stale_for_30_days():
    coverage = pd.DataFrame(
        [
            {"source": "BLUE PUMP FUEL card", "newest": "2026-09-30", "waiting_lines": 0, "status": "due"},
            {"source": "PAGE TURNER BOOKS orders", "newest": "", "waiting_lines": 12, "status": "never downloaded"},
            {"source": "QUILL & INK orders", "newest": "", "waiting_lines": 0, "status": "never downloaded"},
            {"source": "checking", "newest": "2026-11-01", "waiting_lines": 0, "status": "current"},
        ]
    )
    review = pd.DataFrame(
        [
            {"key": "ACME GROCER", "lines": 3, "first": "2026-10-20"},
            {"key": "WILLOW ST", "lines": 2, "first": "2026-09-01"},
        ]
    )
    rows = nudges.build(
        date(2026, 11, 6), params(views_url="https://views.example/exec"), coverage=coverage, review=review
    ).set_index("item")
    assert list(rows.index) == ["BLUE PUMP FUEL card", "PAGE TURNER BOOKS orders", "2 questions"]
    assert rows.at["BLUE PUMP FUEL card", "show_from"] == "2026-10-30"
    assert rows.at["PAGE TURNER BOOKS orders", "show_from"] == "2026-11-06"
    assert rows.at["PAGE TURNER BOOKS orders", "detail"] == "never downloaded; 12 lines wait for it"
    one = nudges.build(
        date(2026, 11, 6),
        params(),
        coverage=pd.DataFrame(
            [{"source": "BLUE PUMP FUEL card", "newest": "2026-09-30", "waiting_lines": 1, "status": "due"}]
        ),
    )
    assert one.iloc[0]["detail"] == "has everything through September 30, 2026; 1 line waits for it"
    assert rows.at["2 questions", "show_from"] == "2026-10-01"
    assert rows.at["2 questions", "detail"] == "covering 5 lines, the oldest from September 1, 2026"
    assert rows.at["2 questions", "link"] == "https://views.example/exec?view=edit&tab=review"


def test_a_setting_that_cant_be_used_is_in_the_next_email():
    rows = nudges.build(date(2026, 11, 6), params(payroll_url="payroll.example"))
    assert rows[["group", "item", "show_from"]].values.tolist() == [["Fix a setting", "payroll_url", "2026-11-06"]]
