import pandas as pd
import pytest

from household import budget

RULES = ["line", "effective", "category", "account", "priority", "rule", "amount", "per", "note"]


def rules(*rows):
    return pd.DataFrame(rows, columns=RULES)


def actuals(rows):
    return pd.DataFrame(rows, columns=["month", "category", "spend"])


def by(df, *keys):
    return df.set_index(list(keys))


def test_fixed_amounts_normalize_to_a_month():
    out = by(budget.targets(rules(
        ["phone", "2026-01-01", "phone", "bills", "high", "fixed", "60", "monthly", ""],
        ["cleaning", "2026-01-01", "cleaning", "bills", "low", "fixed", "120", "biweekly", ""],
        ["registration", "2026-01-01", "car_upkeep", "yearly", "high", "fixed", "36", "annually", ""],
        ["sewer", "2026-01-01", "sewer", "bills", "high", "fixed", "170", "semi_annually", ""]),
        None, "2026-01"), "line")
    assert out["target"].to_dict() == {"cleaning": 260.0, "phone": 60.0, "registration": 3.0, "sewer": 28.33}


def test_changes_take_effect_from_their_month_and_end_stops_a_line():
    out = budget.targets(rules(
        ["phone", "2026-01-01", "phone", "bills", "high", "fixed", "60", "monthly", ""],
        ["phone", "2026-03-15", "phone", "bills", "high", "fixed", "75", "monthly", "new plan"],
        ["phone", "2026-05-01", "phone", "bills", "high", "end", "", "", "cancelled"]), None, "2026-06")
    assert out[["month", "target"]].values.tolist() == [["2026-01", 60.0], ["2026-02", 60.0], ["2026-03", 75.0],
                                                       ["2026-04", 75.0]]


def test_seasonal_uses_the_three_months_centered_a_year_earlier():
    history = actuals([["2025-09", "food", 900.0], ["2025-10", "food", 1000.0], ["2025-11", "food", 1100.0],
                       ["2025-12", "food", 5000.0]])
    out = by(budget.targets(rules(["food", "2026-10-01", "food", "bills", "high", "seasonal", "1500", "monthly", ""]),
                            history, "2026-10"), "month")
    assert out.loc["2026-10", "target"] == pytest.approx(1000 + 0.25 * 100)  # sd of 900, 1000, 1100 is 100
    assert "2025-09 to 2025-11" in out.loc["2026-10", "basis"]


def test_lookback_uses_the_twelve_months_before():
    months = [f"{y}-{m:02d}" for y, m in [(2025, 10), (2025, 11), (2025, 12)] + [(2026, m) for m in range(1, 10)]]
    history = actuals([[m, "food", 1000.0 + 10 * i] for i, m in enumerate(months)] + [["2026-10", "food", 9999.0]])
    out = budget.targets(rules(["food", "2026-10-01", "food", "bills", "high", "lookback", "1500", "monthly", ""]),
                         history, "2026-10")
    values = pd.Series([1000.0 + 10 * i for i in range(12)])
    assert out["target"].tolist() == [pytest.approx(round(values.mean() + 0.25 * values.std(), 2))]
    assert "2025-10 to 2026-09" in out["basis"].iloc[0]


def test_trailing_uses_the_four_months_before():
    history = actuals([["2026-05", "petrol", 9999.0], ["2026-06", "petrol", 100.0], ["2026-07", "petrol", 200.0],
                       ["2026-08", "petrol", 300.0], ["2026-10", "petrol", 9999.0]])  # September spent nothing
    out = budget.targets(rules(["petrol", "2026-10-01", "petrol", "bills", "high", "trailing", "500", "monthly", ""]),
                         history, "2026-10")
    values = [100.0, 200.0, 300.0, 0.0]
    mean, sd = sum(values) / 4, pd.Series(values).std()
    assert out["target"].tolist() == [pytest.approx(round(mean + 0.25 * sd, 2))]
    assert "2026-06 to 2026-09" in out["basis"].iloc[0]


def test_trailing_falls_back_without_recent_history():
    out = budget.targets(rules(["petrol", "2026-10-01", "petrol", "bills", "high", "trailing", "500", "monthly", ""]),
                         actuals([]), "2026-10")
    assert out["target"].tolist() == [500.0] and "fallback" in out["basis"].iloc[0]


def test_lookback_falls_back_without_history():
    out = budget.targets(rules(["food", "2026-10-01", "food", "bills", "high", "lookback", "1500", "monthly", ""]),
                         actuals([]), "2026-10")
    assert out["target"].tolist() == [1500.0] and "fallback" in out["basis"].iloc[0]


def test_scheduled_amounts_land_in_their_month():
    out = budget.targets(rules(
        ["tuition", "2026-08-01", "tuition", "school", "high", "scheduled", "7500", "", "fall"],
        ["tuition", "2027-01-01", "tuition", "school", "high", "scheduled", "7500", "", "spring"]), None, "2027-01")
    assert out[["month", "target"]].values.tolist() == [["2026-08", 7500.0], ["2027-01", 7500.0]]


def test_a_bad_amount_is_a_warning_not_an_error():
    out = budget.targets(rules(["gym", "2026-01-01", "alex", "alex", "low", "fixed", "abc", "monthly", ""]),
                         None, "2026-01")
    assert out["target"].tolist() == [0.0] and "no usable amount" in out["basis"].iloc[0]


def test_versus_and_sinking_funds():
    planned = budget.targets(rules(
        ["food", "2026-01-01", "food", "bills", "high", "fixed", "1000", "monthly", ""],
        ["car repairs", "2026-01-01", "car_upkeep", "emergency", "high", "sinking_fund", "1200", "annually", ""]),
        None, "2026-03")
    spent = actuals([["2026-01", "food", 900.0], ["2026-02", "food", 1100.0], ["2026-02", "car_upkeep", 250.0],
                     ["2026-02", "booze", 40.0], ["2026-02", "transfer", 500.0], ["2026-02", "pay", -4000.0],
                     ["2026-02", "stock_sales", -9000.0]])
    cats = pd.DataFrame({"category": ["food", "car_upkeep", "pay"], "group": ["food", "car", "income"],
                         "priority": ["high", "high", ""]})
    v = by(budget.versus(planned, spent, cats), "month", "category")
    assert v.loc[("2026-02", "food"), ["target", "actual", "remaining"]].tolist() == [1000.0, 1100.0, -100.0]
    assert v.loc[("2026-02", "booze"), ["target", "actual", "priority"]].tolist() == [0.0, 40.0, ""]
    assert v.loc[("2026-01", "car_upkeep"), "group"] == "car"
    assert not {"transfer", "pay", "stock_sales"} & set(v.index.get_level_values("category"))
    s = budget.sinking_funds(planned, spent)
    assert s["balance"].tolist() == [100.0, -50.0, 50.0]


def test_budget_rules_seed_keys_on_line_and_date():
    from household.parsers import seed
    text = ("line,effective,category,account,priority,rule,amount,per,note\n"
            "tuition,2026-08-01,tuition,school,high,scheduled,7500,,fall\n"
            "tuition,2027-01-01,tuition,school,high,scheduled,7500,,spring\n")
    assert seed.parse(text).keys == {"budget_rules": ["line", "effective"]}


def test_a_line_name_with_stray_spaces_or_symbols_is_still_one_line():
    out = budget.targets(rules(
        ["Kids' food & snacks", "2026-01-01", "food", "bills", "high", "fixed", "100", "monthly", ""],
        ["Kids' food & snacks ", "2026-03-01", "food", "bills", "high", "end", "", "", ""]), None, "2026-04")
    assert out["line"].unique().tolist() == ["Kids' food & snacks"]
    assert out["month"].tolist() == ["2026-01", "2026-02"]
