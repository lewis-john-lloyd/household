"""The allocation tree against its targets, and the rebalance proposal, on made-up holdings."""

import pandas as pd
import pytest

from household import allocation as al

NODES = [(n, p, float(w)) for n, p, w in al.SEED]


def rows(*items, day="2001-04-10"):
    return pd.DataFrame([{"date": day, "account_id": a, "owner": "alex", "wrapper": w, "bucket": b, "symbol": s,
                          "asset_class": c, "value": v} for a, w, b, s, c, v in items])


def test_a_blend_reads_its_parts_in_any_units_and_one_code_is_the_whole():
    assert al.blend("SDT 60, SIT 30, BT 10") == pytest.approx({"SDT": 0.6, "SIT": 0.3, "BT": 0.1})
    assert al.blend("SDT:3; BT:1") == {"SDT": 0.75, "BT": 0.25}
    assert al.blend("REIT") == {"REIT": 1.0} and al.blend("") == {}


def test_leaf_shares_multiply_down_the_tree_with_weights_normalized_among_siblings():
    _, leaves = al.leaf_shares(NODES)
    assert leaves["SDT"] == pytest.approx(0.82 * 0.75 * 0.85) and leaves["BI"] == pytest.approx(0.18 * 0.2)
    assert leaves["BA"] == 0 and sum(leaves.values()) == pytest.approx(1)
    targets = pd.DataFrame([{"effective": "2001-01-01", "node": "SDT", "parent": "", "weight": 3},
                            {"effective": "2001-01-01", "node": "BT", "parent": "", "weight": 1},
                            {"effective": "2009-01-01", "node": "BT", "parent": "", "weight": 1}])
    assert al.tree(targets, "2001-06-30") == [("SDT", "", 3.0), ("BT", "", 1.0)]
    assert al.tree(targets.iloc[0:0], "2001-06-30") == NODES  # the old workbook's targets until set


def test_529s_are_left_out_ibonds_count_as_us_bonds_and_a_fund_with_no_class_shows_as_unclassified():
    vals = rows(("p", "401k", "401k_pretax", "ACMEX", "SDT", 60), ("b", "ibonds", "ibonds", "IB1", "", 20),
                ("k", "529", "529", "QUILX", "SDT", 90), ("p", "401k", "401k_pretax", "WILLX", "", 20))
    scope = al.in_scope(pd.concat([vals, rows(("p", "401k", "401k_pretax", "ACMEX", "SDT", 1), day="2001-03-31")]))
    assert al.by_class(scope) == {"SDT": 60, "BT": 20, al.UNCLASSIFIED: 20}
    report = al.report(NODES, al.by_class(scope)).set_index("node")
    assert report.at["stocks", "current_value"] == 60 and report.at["bonds", "target_value"] == 18
    assert report.at[al.UNCLASSIFIED, "target_share"] == 0 and report.at[al.UNCLASSIFIED, "current_value"] == 20


VALS = rows(
    ("plan", "401k", "401k_pretax", "ACMEX", "SDT", 60),
    ("plan", "401k", "401k_pretax", "WILLX", "BT", 5),
    ("ira", "ira", "ira_roth", "QUILX", "SIT", 10),
    ("ira", "ira", "ira_roth", "PAGEX", "SDT 50, SIT 50", 10),
    ("tax", "taxable", "taxable", "BLUEX", "SDT", 10),
    ("tax", "taxable", "taxable", "cash", "BA", 5))


def test_the_rebalance_trades_only_in_tax_advantaged_accounts_among_funds_they_hold():
    trades, proposed, notes = al.rebalance(VALS, [("SDT", "", 70), ("SIT", "", 15), ("BT", "", 15)])
    assert set(trades["account_id"]) <= {"plan", "ira"}
    assert trades["trade"].sum() == pytest.approx(0, abs=0.01)  # each account's money stays in it
    after = {c: round(v, 2) for c, v in proposed.items()}
    # cash held in the taxable account can't move and is over its target of nothing, so it keeps its 5 and the
    # others share the remaining 95 in proportion: SDT 66.5, SIT 14.25, BT 14.25
    assert after == {"SDT": 66.5, "SIT": 14.25, "BT": 14.25, "BA": 5}
    assert "rebalance: Cash held where nothing trades (5) is over its target of 0; the other targets share the rest" in notes


def test_a_fund_the_account_once_held_may_be_bought_and_a_class_no_account_can_hold_is_reported():
    tree = [("SDT", "", 50), ("BI", "", 25), ("BT", "", 25)]
    vals = rows(("plan", "401k", "401k_pretax", "ACMEX", "SDT", 80), ("plan", "401k", "401k_pretax", "WILLX", "BT", 20))
    _, proposed, notes = al.rebalance(vals, tree)
    assert round(proposed["BT"], 2) == 33.33 and proposed.get("BI", 0) == 0  # BI's quarter shared 2 to 1
    assert any("no tradable account holds a fund for International bonds" in n for n in notes)
    lineup = pd.concat([vals, rows(("other", "401k", "401k_pretax", "INTBX", "BI", 0))])
    _, proposed, _ = al.rebalance(lineup, tree, {"plan": {"INTBX"}})
    assert round(proposed["BI"], 2) == 25  # once held in the plan, so it may be bought there


def test_ever_held_looks_back_only_to_a_date():
    holdings = pd.DataFrame([{"as_of": "2001-01-01", "account_id": "p", "symbol": "OLD"},
                             {"as_of": "2003-01-01", "account_id": "p", "symbol": "NEW"},
                             {"as_of": "2003-01-01", "account_id": "p", "symbol": "cash"}])
    activity = pd.DataFrame([{"date": "2003-02-01", "account_id": "p", "symbol": "MID"}])
    assert al.ever_held(holdings, activity, "2002-01-01") == {"p": {"NEW", "MID"}}


def test_asset_location_moves_bonds_to_pre_tax_money_and_stocks_to_roth_without_moving_off_target():
    vals = rows(("pre", "ira", "ira_pretax", "ACMEX", "SDT", 600), ("roth", "ira", "ira_roth", "WILLX", "BT", 400))
    tree = [("SDT", "", 60), ("BT", "", 40)]
    held = {"pre": {"ACMEX", "WILLX"}, "roth": {"ACMEX", "WILLX"}}  # each has held both
    trades, proposed, _ = al.rebalance(vals, tree, held, classes={"ACMEX": "SDT", "WILLX": "BT"})
    after = {(t["account_id"], t["symbol"]): t["proposed"] for t in trades.to_dict("records")}
    assert after == {("pre", "WILLX"): 400, ("pre", "ACMEX"): 200, ("roth", "WILLX"): 0, ("roth", "ACMEX"): 400}
    assert {c: round(v, 2) for c, v in proposed.items()} == {"SDT": 600, "BT": 400}  # the targets still hold
    unmoved, _, notes = al.rebalance(vals, tree, held, location=False, classes={"ACMEX": "SDT", "WILLX": "BT"})
    assert unmoved.empty and notes == ["rebalance: on target, nothing to trade"]


def test_a_plans_pre_tax_and_roth_parts_hold_one_mix():
    vals = rows(("plan", "401k", "401k_pretax", "ACMEX", "SDT", 750), ("plan", "401k", "401k_roth", "ACMEX", "SDT", 250))
    trades, _, _ = al.rebalance(vals, [("SDT", "", 50), ("BT", "", 50)], {"plan": {"WILLX"}}, classes={"WILLX": "BT"})
    assert set(trades["bucket"]) == {"401k_pretax+401k_roth"}
    assert trades.set_index("symbol")["proposed"].to_dict() == {"WILLX": 500, "ACMEX": 500}


def test_each_adult_is_kept_near_the_target_mix_and_not_only_the_household():
    vals = pd.DataFrame([
        {"date": "2001-04-10", "account_id": "a_ira", "owner": "alex", "wrapper": "ira", "bucket": "ira_roth",
         "symbol": "ACMEX", "asset_class": "SDT", "value": 1000},
        {"date": "2001-04-10", "account_id": "s_ira", "owner": "sam", "wrapper": "ira", "bucket": "ira_roth",
         "symbol": "WILLX", "asset_class": "BT", "value": 1000},
        {"date": "2001-04-10", "account_id": "j_tax", "owner": "joint", "wrapper": "taxable", "bucket": "taxable",
         "symbol": "QUILX", "asset_class": "SDT", "value": 400}])
    tree = [("SDT", "", 50), ("BT", "", 50)]
    held = {"a_ira": {"ACMEX", "WILLX"}, "s_ira": {"ACMEX", "WILLX"}}
    klass = {"ACMEX": "SDT", "WILLX": "BT"}
    trades, _, notes = al.rebalance(vals, tree, held, classes=klass)
    # the household is 1400 stocks to 1000 bonds: 200 moves from stocks to bonds, all of it in one account
    assert round(trades["trade"].clip(lower=0).sum(), 2) == 200 and trades["account_id"].nunique() == 1
    trades, proposed, notes = al.rebalance(vals, tree, held, classes=klass, people=("alex", "sam"))
    after = {(t["account_id"], t["symbol"]): t["proposed"] for t in trades.to_dict("records")}
    # each has half of the joint 400 in stocks, so each IRA holds 400 of stocks and 600 of bonds
    assert after == {("a_ira", "ACMEX"): 400, ("a_ira", "WILLX"): 600, ("s_ira", "ACMEX"): 400, ("s_ira", "WILLX"): 600}
    assert {c: round(v, 2) for c, v in proposed.items()} == {"SDT": 1200, "BT": 1200}
    assert "rebalance: alex's own money is 50% off the target mix now, 0% after the trades" in notes
    assert "rebalance: sam's own money is 33% off the target mix now, 0% after the trades" in notes
