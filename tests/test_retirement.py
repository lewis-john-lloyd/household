"""The retirement model: Social Security's formula, the taxes of a retired year, the age rules, the strategies,
and reading the household from the ledger. People, amounts and earnings here are made up."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from household import markets, retirelaw as law, retirement as R


def person(name="alex", born=date(1980, 6, 1), earnings=60_000, first=2002, claim=67):
    return R.Person(name, born, claim, {y: float(earnings) for y in range(first, 2026)}, {}, 2026)


def household(**over):
    people = over.pop("people", [person(), person("sam", date(1982, 2, 1), 30_000, 2004)])
    names = [p.name for p in people]
    base = dict(
        start=2027,
        people=people,
        pre_plan={n: 300_000.0 for n in names},
        pre_ira={n: 0.0 for n in names},
        roth={n: 50_000.0 for n in names},
        roth_basis={n: 30_000.0 for n in names},
        taxable=400_000.0,
        basis=300_000.0,
        hsa=20_000.0,
        contributions={n: {"pre_plan": 20_000.0, "roth": 7_000.0} for n in names},
        hsa_contribution=8_000.0,
        taxable_savings=30_000.0,
        spending={y: 80_000.0 for y in range(2027, 2090)},
        monte_carlo_runs=200,
    )
    base.update(over)
    return R.Household(**base)


def fixed(h, real_return, inflation=0.0):
    shape = (len(h.years), 1)
    return np.full(shape, real_return), np.full(shape, inflation)


# --- Social Security ----------------------------------------------------------------------------------------


def test_the_bend_points_and_claiming_factors_follow_the_published_ones():
    assert law.bend_points(2026) == (1286, 7749) and law.bend_points(2027) == (1340, 8077)
    assert law.claim_factor(1980, 62 * 12) == pytest.approx(0.70)
    assert law.claim_factor(1980, 70 * 12) == pytest.approx(1.24)
    assert law.spouse_factor(1980, 62 * 12) == pytest.approx(0.65)
    assert law.fra_months(1957) == 66 * 12 + 6


def test_the_pia_is_cut_to_the_dime_and_retiring_early_lowers_it():
    assert law.pia(2000, (1286, 7749)) == pytest.approx(int((0.9 * 1286 + 0.32 * 714) * 10) / 10)
    p = person(earnings=90_000, first=2000)
    aime_late, late = R.pia(p, 2047, 2026)
    aime_early, early = R.pia(p, 2030, 2026)
    assert late > early and aime_late > aime_early  # fewer years of earnings among the 35 counted


def test_a_spouse_gets_half_the_others_benefit_when_that_is_more():
    h = household(people=[person(earnings=150_000, first=1995), person("sam", date(1980, 6, 1), 1, 2024)])
    got = R.benefits(h, 2045, {}, 2026)
    high = R.pia(h.people[0], 2045, 2026)[1]
    assert got["sam"][2048] == pytest.approx(0.5 * high * 12, rel=0.01)
    assert got["alex"][2047] == pytest.approx(high * 12 * 7 / 12)  # the claiming year counts from the birthday


# --- the year's taxes ---------------------------------------------------------------------------------------


def test_social_security_is_taxed_in_steps_on_a_joint_return():
    ss = np.array([30_000.0, 30_000.0, 30_000.0])
    other = np.array([10_000.0, 25_000.0, 100_000.0])
    got = R.taxable_social_security(ss, other, 1.0)
    assert got[0] == 0 and got[1] == pytest.approx(0.5 * (25_000 + 15_000 - 32_000))
    assert got[2] == pytest.approx(0.85 * 30_000)


def test_a_retired_years_taxes_count_gains_wisconsin_exclusions_and_the_early_penalty():
    one = np.ones(1)
    fed, wi, agi, _ = R.taxes(50_000 * one, 0 * one, 0 * one, 0 * one, 0 * one, 0 * one, 0, 2, 1.0, 2040)
    assert fed[0] == pytest.approx(0.10 * 17_800) and agi[0] == 50_000
    _, _, _, _ = R.taxes(50_000 * one, 0 * one, 0 * one, 0 * one, 0 * one, 0 * one, 2, 2, 1.0, 2040)
    gains, _, _, _ = R.taxes(0 * one, 80_000 * one, 0 * one, 0 * one, 0 * one, 0 * one, 0, 2, 1.0, 2040)
    assert gains[0] == 0  # long-term gains inside the 0% band
    fed_pen, wi_pen, _, _ = R.taxes(50_000 * one, 0 * one, 0 * one, 0 * one, 10_000 * one, 0 * one, 0, 2, 1.0, 2040)
    assert fed_pen[0] - fed[0] == pytest.approx(1_000) and wi_pen[0] - wi[0] == pytest.approx(330)
    _, wi_ex, _, _ = R.taxes(50_000 * one, 0 * one, 0 * one, 0 * one, 0 * one, 48_000 * one, 2, 2, 1.0, 2040)
    assert wi_ex[0] < wi[0]


def test_irmaa_reads_the_tier_for_the_income():
    total, extra = R.irmaa(np.array([100_000.0, 300_000.0]), 2)
    assert extra[0] == 0 and total[0] == pytest.approx(202.90 * 24)
    assert extra[1] == pytest.approx((405.80 - 202.90 + 37.50) * 24)


# --- the simulation -----------------------------------------------------------------------------------------


def test_every_year_balances_spending_health_and_tax_against_money_in():
    h = household()
    r, i = fixed(h, 0.04)
    res = R.simulate(h, R.Strategy("conventional", 0.0), 2040, r, i, 2026, record=True)
    for row in [x for x in res["rows"] if x["phase"] == "retired"]:
        money_in = (
            row["social_security"]
            + row["rmd"]
            + row["from_taxable"]
            + row["from_pre_tax"]
            + row["from_roth"]
            + row["from_hsa"]
        )
        out = row["spending"] + row["health"] + row["federal_tax"] + row["state_tax"]
        assert money_in == pytest.approx(out, abs=2) or money_in > out  # extra (RMDs) goes back to taxable


def test_conventional_order_spends_taxable_first_and_rmds_start_at_75():
    h = household()
    r, i = fixed(h, 0.04)
    rows = R.simulate(h, R.Strategy("conventional", 0.0), 2045, r, i, 2026, record=True)["rows"]
    first = next(x for x in rows if x["year"] == 2045)
    assert first["from_pre_tax"] == 0 and first["from_taxable"] > 0
    assert next(x for x in rows if x["year"] == 2054)["rmd"] == 0  # alex is 74
    assert next(x for x in rows if x["year"] == 2055)["rmd"] > 0


def test_conversions_fill_the_bracket_and_move_pre_tax_to_roth():
    h = household()
    r, i = fixed(h, 0.04)
    none = R.simulate(h, R.Strategy("conventional", 0.0), 2045, r, i, 2026, record=True)["rows"]
    conv = R.simulate(h, R.Strategy("conventional", 0.12), 2045, r, i, 2026, record=True)["rows"]
    year = next(x for x in conv if x["year"] == 2046)
    assert year["converted"] > 0
    # to the top of the 12% bracket, over the standard deduction and the 65-and-over one (alex is 66)
    assert year["ordinary_income"] == pytest.approx(R.bracket_top(0.12) + 32_200 + 1_650, abs=1)
    assert conv[-1]["roth"] > none[-1]["roth"] and conv[-1]["pre_tax"] < none[-1]["pre_tax"]


def test_before_59_and_a_half_the_ten_percent_falls_on_pre_tax_money_unless_a_rule_frees_it():
    only_pre = dict(
        taxable=0.0,
        basis=0.0,
        roth={"alex": 0.0},
        roth_basis={"alex": 0.0},
        hsa=0.0,
        people=[person(born=date(1985, 6, 1))],
        pre_plan={"alex": 0.0},
        pre_ira={"alex": 1_500_000.0},
        contributions={},
        hsa_contribution=0.0,
        taxable_savings=0.0,
    )
    h = household(**only_pre)
    r, i = fixed(h, 0.03)
    plain = R.simulate(h, R.Strategy("conventional", 0.0), 2030, r, i, 2026)
    sepp = R.simulate(h, R.Strategy("conventional", 0.0, sepp=True), 2030, r, i, 2026)
    assert plain["penalty"][0] > 0 and sepp["penalty"][0] < plain["penalty"][0]
    # the plan of an employer left in the year turning 55 is free of it
    h55 = household(**{**only_pre, "pre_plan": {"alex": 1_500_000.0}, "pre_ira": {"alex": 0.0}})
    assert R.simulate(h55, R.Strategy("conventional", 0.0), 2040, r, i, 2026)["penalty"][0] == 0


def test_a_household_without_enough_runs_short_and_one_with_plenty_does_not():
    rich, poor = (
        household(),
        household(
            taxable=0.0,
            basis=0.0,
            pre_plan={"alex": 0.0, "sam": 0.0},
            roth={"alex": 0.0, "sam": 0.0},
            hsa=0.0,
            contributions={},
            hsa_contribution=0.0,
            taxable_savings=0.0,
        ),
    )
    r, i = fixed(rich, 0.04)
    assert not R.simulate(rich, R.Strategy(), 2045, r, i, 2026)["short"][0]
    assert R.simulate(poor, R.Strategy(), 2030, r, i, 2026)["short"][0]


def test_guardrails_cut_spending_in_a_bad_sequence_and_vpw_never_runs_short():
    h = household(taxable=900_000.0, contributions={}, taxable_savings=0.0)
    r, i = fixed(h, -0.03)
    guard = R.simulate(h, R.Strategy("conventional", 0.0, "guardrails"), 2027, r, i, 2026)
    vpw = R.simulate(h, R.Strategy("conventional", 0.0, "vpw"), 2027, r, i, 2026)
    assert guard["lowest"][0] < 1 and not vpw["short"][0]


def test_the_three_return_models_run_side_by_side():
    h = household()
    got = R.run_all(h, R.Strategy(), 2045, 2026)
    assert set(got) == {"fixed 5%", "fixed 7%", "fixed 9%", "historical", "monte carlo"}
    assert len(got["historical"]["short"]) == len(got["historical"]["starts"]) >= 30
    assert len(got["monte carlo"]["short"]) == 200
    years, _, starts = R.historical_paths(h, 2045)
    assert starts[0] == markets.FIRST


def test_strategy_keys_read_back():
    for s in (R.Strategy(), R.Strategy("proportional", 0.0, "vpw", True)):
        assert R.parse_strategy(s.key) == s
    assert R.parse_strategy("bracket/to30/constant") is None and R.parse_strategy("nonsense") is None


# --- from the ledger ----------------------------------------------------------------------------------------


def test_spending_settings_read_as_steps():
    assert R.spending_steps("0 from 2032") == [(2032, 0.0)]
    assert R.spending_steps("18,000; 9000 from 2040") == [(None, 18_000.0), (2040, 9_000.0)]
    assert R.spending_steps("soon") is None


def test_the_spending_path_replaces_categories_and_groups_and_drops_a_paid_off_loan():
    base = {"food": 10_000.0, "camps": 2_000.0, "tuition": 8_000.0, "household": 5_000.0}
    groups = {"food": "food", "camps": "kids", "tuition": "kids", "household": "household"}
    path, rows = R.spending_path(
        base,
        groups,
        range(2030, 2041),
        {"kids": [(2035, 0.0)], "hobby": [(None, 1_000.0)]},
        {"acme_loan": (2037, 3_000.0)},
    )
    assert path[2030] == 26_000 and path[2035] == 16_000 and path[2038] == 13_000
    assert {r["key"] for r in rows} == {"kids", "hobby", "acme_loan"}


def test_the_budget_lists_each_line_and_adds_up_to_the_spending_path():
    base = {"food": 10_000.0, "camps": 2_000.0, "tuition": 8_000.0, "household": 5_000.0, "hobby": 3_000.0}
    groups = {"food": "food", "camps": "kids", "tuition": "kids", "household": "home", "hobby": "fun"}
    changes = {"kids": [(2035, 0.0)], "hobby": [(None, 1_000.0)], "gifts": [(2032, 500.0)]}
    loans = {"acme_loan": (2037, 3_000.0)}
    path, _ = R.spending_path(base, groups, range(2030, 2041), changes, loans)
    h = household(
        start=2030,
        spending=path,
        budget={
            "base": base,
            "groups": groups,
            "changes": changes,
            "loans": loans,
            "lumpy": {"tuition": 0.9},
            "settings": {"kids": "0 from 2035", "hobby": "1000", "gifts": "500 from 2032"},
        },
    )
    t = R.budget_table(h, 2038)
    lines = t.set_index("line")
    assert list(t["kind"]) == [
        "group",
        "group",
        "category",
        "group",
        "category",
        "group",
        "category",
        "category",
        "added",
        "loan",
    ]
    assert lines.loc["kids", "setting"] == "0 from 2035" and lines.loc["kids", "at_retirement"] == 0
    assert lines.loc["kids", "is_set"] == "yes" and lines.loc["food", "is_set"] == ""
    assert lines.loc["tuition", "at_retirement"] == "" and lines.loc["tuition", "lumpy"] == 0.9
    assert lines.loc["hobby", "now"] == 3_000 and lines.loc["hobby", "at_retirement"] == 1_000
    counted = lambda col: sum(v for k, v in zip(t["kind"], t[col]) if k != "category" and v != "")  # noqa: E731
    assert counted("first_year") == path[2030] and counted("at_retirement") == path[2038]


def ledger_frames():
    accounts = pd.DataFrame(
        [
            {"account_id": "a_plan", "type": "workplace_plan", "tax_treatment": "pre_tax", "owner": "alex"},
            {"account_id": "s_roth", "type": "ira", "tax_treatment": "roth", "owner": "sam"},
            {"account_id": "brok", "type": "brokerage", "tax_treatment": "taxable", "owner": "joint"},
            {"account_id": "k529", "type": "education", "tax_treatment": "education", "owner": "kid"},
        ]
    )
    values = pd.DataFrame(
        [
            {"date": "2026-11-01", "account_id": "a_plan", "owner": "alex", "bucket": "401k_pretax", "value": 50},
            {"date": "2026-11-01", "account_id": "s_roth", "owner": "sam", "bucket": "ira_roth", "value": 40},
            {"date": "2026-11-01", "account_id": "brok", "owner": "joint", "bucket": "taxable", "value": 90},
            {"date": "2026-11-01", "account_id": "k529", "owner": "kid", "bucket": "529", "value": 30},
        ]
    )
    activity = pd.DataFrame(
        [
            {"account_id": "a_plan", "date": "2026-03-01", "kind": "contribution", "amount": 20, "source": "pretax"},
            {"account_id": "a_plan", "date": "2026-03-01", "kind": "contribution", "amount": 5, "source": "aftertax"},
            {"account_id": "s_roth", "date": "2026-01-10", "kind": "conversion_in", "amount": 7, "source": ""},
            {"account_id": "k529", "date": "2026-02-01", "kind": "contribution", "amount": 10, "source": ""},
        ]
    )
    lots = pd.DataFrame([{"account_id": "brok", "value": 60, "basis": 20}])
    actuals = pd.DataFrame(
        [
            {"month": f"2026-{m:02d}", "category": c, "spend": s}
            for m in range(1, 12)
            for c, s in (("food", 10), ("brokerage", 20), ("pay", -90), ("savings_529", 1))
        ]
    )
    categories = pd.DataFrame(
        [
            {"category": "food", "group": "food"},
            {"category": "brokerage", "group": "savings"},
            {"category": "pay", "group": "income"},
            {"category": "savings_529", "group": "savings"},
        ]
    )
    statements = pd.DataFrame([{"person": "alex", "birth_date": "1980-06-01", "as_of": "2026-01-01"}])
    estimates = pd.DataFrame(
        [{"person": "alex", "as_of": "2026-01-01", "kind": "retirement", "age": "67", "monthly": 20}]
    )
    earnings = pd.DataFrame(
        [{"person": "alex", "as_of": "2026-01-01", "year": "2025", "fica_earnings": 50, "medicare_earnings": 60}]
    )
    return accounts, values, activity, lots, actuals, categories, statements, estimates, earnings


def params(**rows):
    return pd.DataFrame(
        [{"year": "", "jurisdiction": "household", "key": k, "value": v, "source": ""} for k, v in rows.items()]
    )


def test_the_household_is_read_from_the_ledger():
    accounts, values, activity, lots, actuals, categories, statements, estimates, earnings = ledger_frames()
    p = params(**{"birth_date:sam": "1982-02-01", "retirement_spending:food": "5 from 2040"})
    h, notes, assume = R.build(
        p,
        ("alex", "sam"),
        statements,
        estimates,
        earnings,
        accounts,
        values,
        activity,
        lots,
        actuals,
        categories,
        None,
        None,
        0.6,
        "2026-11-01",
    )
    assert [x.name for x in h.people] == ["alex", "sam"] and h.people[1].born == date(1982, 2, 1)
    assert h.pre_plan["alex"] == 50 and h.roth["sam"] == 40 and h.taxable == 90 and h.basis == 20 + 30
    assert h.contributions["alex"] == {"pre_plan": 20, "roth": 5} and h.contributions["sam"]["roth"] == 7
    assert h.roth_basis["sam"] == 7 and h.stock_share == 0.6
    assert h.spending[2030] == pytest.approx(10 * 12) and h.spending[2040] == 5  # food alone: savings aren't spending
    # taxable savings: pay (90 a month) less next year's spending (food, 120 a year) and the 529's 10
    assert h.taxable_savings == pytest.approx(90 * 12 - 120 - 10)
    assert any("no Social Security statement for sam" in n for n in notes)
    assert {"people", "today", "saving", "spending", "changes", "returns", "law"} <= set(assume["section"])


def test_without_a_birth_date_there_is_no_plan():
    accounts, values, activity, lots, actuals, categories, statements, estimates, earnings = ledger_frames()
    h, notes, _ = R.build(
        params(),
        ("alex", "sam"),
        statements,
        estimates,
        earnings,
        accounts,
        values,
        activity,
        lots,
        actuals,
        categories,
        None,
        None,
        None,
        "2026-11-01",
    )
    assert h is None and "birth_date:sam" in notes[0]


def test_a_run_finds_the_year_picks_a_strategy_and_lays_out_every_table():
    h = household(
        monte_carlo_runs=100,
        plan_to_age=85,
        people=[person(born=date(1966, 6, 1), first=1990), person("sam", date(1968, 2, 1), 30_000, 1992)],
    )
    years, table, plan, ss, earned, notes = R.run(h, params(), "2026-11-01")
    chosen = years[years["chosen"] == "yes"]
    assert chosen["retire_year"].nunique() == 1 and set(years["model"]) == {"fixed", "historical", "monte carlo"}
    year = int(chosen["retire_year"].iloc[0])
    mc = years[(years["model"] == "monte carlo")].set_index("retire_year")["success"]
    assert mc[year] >= h.success_target and (
        year == h.start
        or mc[year - 1] < h.success_target
        or table[table["chosen"] == "yes"]["strategy"].iloc[0] != R.Strategy().key
    )
    picked = table[table["chosen"] == "yes"]
    assert picked["strategy"].nunique() == 1 and set(picked["model"]) == {"historical", "monte carlo"}
    assert set(plan["strategy"]) == set(table["strategy"])
    assert set(ss["person"]) == {"alex", "sam"} and set(earned["basis"]) == {"posted", "projected"}
    assert notes[0].startswith(f"retirement: {year} (")
