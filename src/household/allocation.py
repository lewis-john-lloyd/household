"""The household's asset allocation against its targets, and a rebalance that moves toward them.

Targets (allocation_targets) are a tree: each node has a parent ("" for the top) and a weight among its siblings
(weights are normalized, so 82/18 and 0.82/0.18 read the same). A leaf is an asset class code from securities
(SDT, BT, ...). The rows with the latest effective date on or before today are the tree in force.

A security's asset_class is one code, or a blend for a fund that holds several ("SDT 60, SIT 30, BT 10", parts in
any units, normalized). Policy for what counts:
  - 529s are left out (each follows its own age-based portfolio);
  - I-Bonds count as US bonds (IBOND_CLASS);
  - everything else counts by its class, and a holding with no class shows as unclassified.

The rebalance proposal (rebalance) moves money only inside tax-advantaged accounts, and only among funds the
account holds or has held (no new tickers). Taxable accounts, I-Bonds and anything with no class stay as they are
(selling in a taxable account is a separate decision, tax and all). A linear program finds the account balances
that come closest to each class's target and, among those, trade least. A class held only where nothing can trade
and already over its target (cash in a taxable account, say) keeps what it has, and the other targets share what
is left in proportion. A class no tradable account can hold is reported: reaching it would need a new ticker.
"""

import re

import numpy as np
import pandas as pd

IBOND_CLASS = "BT"
UNCLASSIFIED = "unclassified"
TREE_COLUMNS = ["node", "parent", "depth", "order", "label", "leaf", "target_share", "target_value", "current_value",
                "current_share", "delta_value", "proposed_value", "proposed_share"]
TRADE_COLUMNS = ["account_id", "bucket", "symbol", "asset_class", "current", "proposed", "trade"]
FIXED_WRAPPERS = ("taxable", "ibonds")
LEFT_OUT = ("529",)
MIN_TRADE = 50.0  # trades smaller than this are left out of the proposal

# a node's label in the views; a leaf class with no label here shows its code
LABELS = {"stocks": "Stocks", "us_stocks": "US", "intl_stocks": "International", "bonds": "Bonds",
          "SDT": "US total market", "SDS": "US small value", "REIT": "REIT", "SIT": "International total",
          "SIE": "Emerging markets", "BT": "US bonds", "BI": "International bonds", "BA": "Cash",
          UNCLASSIFIED: "Unclassified"}

# The targets the old investments workbook used, as allocation_targets rows (effective, node, parent, weight)
SEED = [("stocks", "", 82), ("us_stocks", "stocks", 75), ("SDT", "us_stocks", 85), ("SDS", "us_stocks", 5),
        ("REIT", "us_stocks", 10), ("intl_stocks", "stocks", 25), ("SIT", "intl_stocks", 75),
        ("SIE", "intl_stocks", 25), ("bonds", "", 18), ("BT", "bonds", 80), ("BI", "bonds", 20), ("BA", "", 0)]

PART = re.compile(r"([A-Za-z_]+)\s*[: ]\s*([0-9.]+)")


def blend(asset_class):
    """{class: fraction} for a securities asset_class: "SDT", or "SDT 60, SIT 30, BT 10"."""
    text = str(asset_class).strip()
    if not text:
        return {}
    parts = PART.findall(text)
    if not parts:
        return {text: 1.0}
    total = sum(float(v) for _, v in parts)
    return {k: float(v) / total for k, v in parts} if total > 0 else {}


def class_mix(row):
    """The class fractions of one valuation row, by the policy above."""
    if row["wrapper"] == "ibonds":
        return {IBOND_CLASS: 1.0}
    if str(row["symbol"]).strip().lower() == "cash":
        return {"BA": 1.0}
    return blend(row["asset_class"]) or {UNCLASSIFIED: 1.0}


def in_scope(vals):
    """The valuation rows the allocation counts: the latest date, 529s left out."""
    if vals is None or vals.empty:
        return vals
    latest = vals[vals["date"] == vals["date"].max()]
    return latest[~latest["wrapper"].isin(LEFT_OUT)].copy()


def by_class(rows):
    """{class: dollars} for valuation rows."""
    out = {}
    for r in rows.to_dict("records"):
        v = float(r["value"])
        for c, f in class_mix(r).items():
            out[c] = out.get(c, 0.0) + v * f
    return out


def tree(targets, today):
    """The tree in force: a list of (node, parent, weight) in the order given, or the seed when there is none."""
    if targets is None or targets.empty:
        return [(n, p, float(w)) for n, p, w in SEED]
    t = targets.copy()
    t["effective"] = t["effective"].astype(str).str[:10]
    t = t[t["effective"] <= today]
    if t.empty:
        return [(n, p, float(w)) for n, p, w in SEED]
    t = t[t["effective"] == t["effective"].max()]
    return [(str(n).strip(), str(p).strip(), float(pd.to_numeric(w, errors="coerce") or 0.0))
            for n, p, w in zip(t["node"], t["parent"], t["weight"]) if str(n).strip()]


def leaf_shares(nodes):
    """{leaf: share of the whole} from a tree: each node's weight normalized among its siblings, times its
    parent's share."""
    kids = {}
    for n, p, w in nodes:
        kids.setdefault(p, []).append((n, w))
    shares = {}

    def walk(parent, share):
        children = kids.get(parent, [])
        total = sum(w for _, w in children)
        for n, w in children:
            s = share * (w / total if total > 0 else 0.0)
            shares[n] = s
            walk(n, s)
    walk("", 1.0)
    leaves = {n: s for n, s in shares.items() if n not in kids}
    return shares, leaves


def report(nodes, current, proposed=None):
    """TREE_COLUMNS: each node's target and current value and share, depth-first in the tree's order, then any
    class held that the tree doesn't name."""
    shares, leaves = leaf_shares(nodes)
    kids = {}
    for n, p, _ in nodes:
        kids.setdefault(p, []).append(n)
    total = sum(current.values())
    proposed = proposed or {}
    rows, order = [], [0]

    def under(node):
        if node not in kids:
            return [node]
        return [x for k in kids[node] for x in under(k)]

    def walk(parent, depth):
        for n in kids.get(parent, []):
            mine = under(n)
            cur = sum(current.get(c, 0.0) for c in mine)
            prop = sum(proposed.get(c, 0.0) for c in mine)
            order[0] += 1
            rows.append({"node": n, "parent": parent, "depth": depth, "order": order[0], "label": LABELS.get(n, n),
                         "leaf": n not in kids, "target_share": round(shares[n], 6),
                         "target_value": round(shares[n] * total, 2), "current_value": round(cur, 2),
                         "current_share": round(cur / total, 6) if total else 0.0,
                         "delta_value": round(cur - shares[n] * total, 2),
                         "proposed_value": round(prop, 2) if proposed else "",
                         "proposed_share": round(prop / total, 6) if proposed and total else ""})
            walk(n, depth + 1)
    walk("", 0)
    for c in sorted(set(current) - set(leaves)):
        order[0] += 1
        cur = current[c]
        rows.append({"node": c, "parent": "", "depth": 0, "order": order[0], "label": LABELS.get(c, c), "leaf": True,
                     "target_share": 0.0, "target_value": 0.0, "current_value": round(cur, 2),
                     "current_share": round(cur / total, 6) if total else 0.0, "delta_value": round(cur, 2),
                     "proposed_value": round(proposed.get(c, 0.0), 2) if proposed else "",
                     "proposed_share": round(proposed.get(c, 0.0) / total, 6) if proposed and total else ""})
    return pd.DataFrame(rows, columns=TREE_COLUMNS)


def _targets(leaves, total, fixed):
    """{class: target dollars}: leaf shares of the total, where a class whose fixed holdings already exceed its
    target keeps them and the rest share what remains in proportion to their weights."""
    leaves = {**{c: 0.0 for c in fixed}, **leaves}  # a class held with no node in the tree has a target of nothing
    pinned, targets = set(), {}
    while True:
        free = {c: s for c, s in leaves.items() if c not in pinned}
        left = total - sum(fixed.get(c, 0.0) for c in pinned)
        weight = sum(free.values())
        targets = {c: (s / weight * left if weight > 0 else 0.0) for c, s in free.items()}
        targets.update({c: fixed.get(c, 0.0) for c in pinned})
        over = {c for c in free if fixed.get(c, 0.0) > targets[c] + 0.005}
        if not over:
            return targets
        pinned |= over


def rebalance(vals, nodes, ever_held=None):
    """(TRADE_COLUMNS, {class: proposed dollars}, notes). vals are the in-scope valuation rows; ever_held is
    {account_id: set of symbols} the account has held (holdings and activity), which the proposal may buy."""
    from scipy.optimize import linprog

    notes = []
    rows = vals.copy()
    rows["value"] = pd.to_numeric(rows["value"], errors="coerce").fillna(0.0)
    rows = rows[rows["value"].abs() >= 0.005]
    _, leaves = leaf_shares(nodes)
    total = float(rows["value"].sum())
    mixes = {(r["account_id"], r["bucket"], r["symbol"]): class_mix(r) for r in rows.to_dict("records")}

    def tradable(r):
        if r["wrapper"] in FIXED_WRAPPERS or not str(r["symbol"]).strip():
            return False
        return UNCLASSIFIED not in mixes[(r["account_id"], r["bucket"], r["symbol"])]
    rows["tradable"] = [tradable(r) for r in rows.to_dict("records")]
    fixed_rows = rows[~rows["tradable"]]
    fixed = by_class(fixed_rows)
    # the tradable money: per (account, bucket), its value and the funds it may hold
    accounts = rows[rows["tradable"]].groupby(["account_id", "bucket"], as_index=False)["value"].sum()
    funds = {}  # symbol -> class mix, for funds that can be bought
    for r in rows[rows["tradable"]].to_dict("records"):
        funds[r["symbol"]] = mixes[(r["account_id"], r["bucket"], r["symbol"])]
    klass = {}
    for r in vals.to_dict("records"):
        if str(r["symbol"]).strip() and r["symbol"] not in klass:
            klass[r["symbol"]] = class_mix(r)
    variables = []  # (account, bucket, symbol, current)
    for a in accounts.to_dict("records"):
        mine = rows[(rows["account_id"] == a["account_id"]) & (rows["bucket"] == a["bucket"]) & rows["tradable"]]
        held = dict(zip(mine["symbol"], mine["value"]))
        may = set(held) | {s for s in (ever_held or {}).get(a["account_id"], set())
                           if UNCLASSIFIED not in klass.get(s, {UNCLASSIFIED: 1}) and s in klass}
        for s in sorted(may):
            variables.append((a["account_id"], a["bucket"], s, float(held.get(s, 0.0))))
            funds.setdefault(s, klass[s])
    classes = sorted(set(leaves) | set(fixed) | {c for m in funds.values() for c in m})
    targets = _targets(leaves, total, fixed)
    reachable = {c for _, _, s, _ in variables for c, f in funds[s].items() if f > 0}
    for c in sorted(leaves):
        if leaves[c] > 0 and c not in reachable and fixed.get(c, 0.0) + 0.005 < targets.get(c, 0.0):
            notes.append(f"rebalance: no tradable account holds a fund for {LABELS.get(c, c)}, so it stays "
                         f"{targets[c] - fixed.get(c, 0.0):,.0f} under target without a new ticker")
    pinned = [c for c in sorted(set(leaves) | set(fixed)) if fixed.get(c, 0.0) > leaves.get(c, 0.0) * total + 0.005]
    for c in pinned:
        notes.append(f"rebalance: {LABELS.get(c, c)} held where nothing trades ({fixed[c]:,.0f}) is over its "
                     f"target of {leaves.get(c, 0.0) * total:,.0f}; the other targets share the rest")
    if not variables:
        return pd.DataFrame(columns=TRADE_COLUMNS), dict(fixed), notes + ["rebalance: nothing tradable"]
    n, m = len(variables), len(classes)
    # x (n), then over/under per class (2m), then buy/sell per variable (2n)
    size = n + 2 * m + 2 * n
    cost = np.zeros(size)
    scale = max(total, 1.0)
    cost[n:n + 2 * m] = 1000.0 / scale
    cost[n + 2 * m:] = 1.0 / scale
    a_eq, b_eq = [], []
    for c_i, c in enumerate(classes):  # fixed + sum x*f - over + under = target
        row = np.zeros(size)
        for v_i, (_, _, s, _) in enumerate(variables):
            row[v_i] = funds[s].get(c, 0.0)
        row[n + c_i], row[n + m + c_i] = -1.0, 1.0
        a_eq.append(row)
        b_eq.append(targets.get(c, 0.0) - fixed.get(c, 0.0))
    for key, g in pd.DataFrame(variables, columns=["a", "b", "s", "cur"]).groupby(["a", "b"]):
        row = np.zeros(size)
        row[list(g.index)] = 1.0
        a_eq.append(row)
        b_eq.append(float(g["cur"].sum()))
    for v_i, (_, _, _, cur) in enumerate(variables):  # x - buy + sell = current
        row = np.zeros(size)
        row[v_i], row[n + 2 * m + v_i], row[n + 2 * m + n + v_i] = 1.0, -1.0, 1.0
        a_eq.append(row)
        b_eq.append(cur)
    result = linprog(cost, A_eq=np.array(a_eq), b_eq=np.array(b_eq), bounds=[(0, None)] * size, method="highs")
    if not result.success:
        return pd.DataFrame(columns=TRADE_COLUMNS), dict(fixed), notes + [f"rebalance: {result.message}"]
    x = result.x[:n]
    trades, proposed = [], dict(fixed)
    for (acct, bkt, s, cur), new in zip(variables, x):
        for c, f in funds[s].items():
            proposed[c] = proposed.get(c, 0.0) + new * f
        trade = new - cur
        if abs(trade) >= MIN_TRADE or (cur > 0 and new < 0.5):
            mix = funds[s]
            label = ", ".join(f"{c} {f:.0%}" for c, f in mix.items()) if len(mix) > 1 else next(iter(mix))
            trades.append({"account_id": acct, "bucket": bkt, "symbol": s, "asset_class": label,
                           "current": round(cur, 2), "proposed": round(new, 2), "trade": round(trade, 2)})
    out = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    if len(out):
        out = out.sort_values(["account_id", "bucket", "trade"]).reset_index(drop=True)
        moved = out["trade"].clip(lower=0).sum()
        notes.append(f"rebalance: {len(out)} trades in {out['account_id'].nunique()} accounts, {moved:,.0f} moved")
    else:
        notes.append("rebalance: on target, nothing to trade")
    return out, proposed, notes


def ever_held(holdings, activity, since=""):
    """{account_id: symbols} each account has held or traded on or after since (a plan's lineup changes, so a
    fund last seen years ago may no longer be offered)."""
    out = {}
    for df, day in ((holdings, "as_of"), (activity, "date")):
        if df is None or df.empty:
            continue
        recent = df[df[day].astype(str).str[:10] >= since]
        for a, s in zip(recent["account_id"].astype(str).str.strip(), recent["symbol"].astype(str).str.strip()):
            if s and s.lower() != "cash":
                out.setdefault(a, set()).add(s)
    return out
