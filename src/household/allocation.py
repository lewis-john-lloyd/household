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
from collections.abc import Iterable, Sequence
from typing import Any, Optional

import numpy as np
import pandas as pd
from pandas import DataFrame

IBOND_CLASS = "BT"
UNCLASSIFIED = "unclassified"
TREE_COLUMNS = [
    "node",
    "parent",
    "depth",
    "order",
    "label",
    "leaf",
    "target_share",
    "target_value",
    "current_value",
    "current_share",
    "delta_value",
    "proposed_value",
    "proposed_share",
]
TRADE_COLUMNS = ["account_id", "bucket", "symbol", "asset_class", "current", "proposed", "trade"]
FIXED_WRAPPERS = ("taxable", "ibonds")
LEFT_OUT = ("529",)
MIN_TRADE = 50.0  # trades smaller than this are left out of the proposal

# Asset location: how poorly each class suits money taxed each way (0 suits it; 1 and 2 less so). Bonds and cash,
# whose returns are income and grow least, belong in pre-tax money, which is taxed as income on the way out
# anyway and so costs the least growth; the stocks expected to grow most belong in Roth and HSA money, which is
# never taxed again. REITs pay income taxed as ordinary, so any sheltered account suits them. A taxable account
# is never traded (its sales are taxable), so it has no row here.
LOCATION = {
    "BT": {"pretax": 0, "hsa": 1, "roth": 2},
    "BI": {"pretax": 0, "hsa": 1, "roth": 2},
    "BA": {"pretax": 0, "hsa": 1, "roth": 2},
    "REIT": {"pretax": 0, "hsa": 0, "roth": 0},
    "SDT": {"pretax": 1, "hsa": 0, "roth": 0},
    "SIT": {"pretax": 1, "hsa": 0, "roth": 0},
    "SDS": {"pretax": 2, "hsa": 0, "roth": 0},
    "SIE": {"pretax": 2, "hsa": 0, "roth": 0},
}
# a placement level costs this many times a dollar traded: moving a bond out of a Roth account and a stock into
# it (four trades) is worth it, a reshuffle to gain a little is not; reaching the targets always comes first
LOCATION_WEIGHT = 3.0

# a node's label in the views; a leaf class with no label here shows its code
LABELS = {
    "stocks": "Stocks",
    "us_stocks": "US",
    "intl_stocks": "International",
    "bonds": "Bonds",
    "SDT": "US total market",
    "SDS": "US small value",
    "REIT": "REIT",
    "SIT": "International total",
    "SIE": "Emerging markets",
    "BT": "US bonds",
    "BI": "International bonds",
    "BA": "Cash",
    UNCLASSIFIED: "Unclassified",
}

# The targets the old investments workbook used, as allocation_targets rows (effective, node, parent, weight)
SEED = [
    ("stocks", "", 82),
    ("us_stocks", "stocks", 75),
    ("SDT", "us_stocks", 85),
    ("SDS", "us_stocks", 5),
    ("REIT", "us_stocks", 10),
    ("intl_stocks", "stocks", 25),
    ("SIT", "intl_stocks", 75),
    ("SIE", "intl_stocks", 25),
    ("bonds", "", 18),
    ("BT", "bonds", 80),
    ("BI", "bonds", 20),
    ("BA", "", 0),
]

PART = re.compile(r"([A-Za-z_]+)\s*[: ]\s*([0-9.]+)")


def blend(asset_class: str) -> dict[str, float]:
    """{class: fraction} for a securities asset_class: "SDT", or "SDT 60, SIT 30, BT 10"."""
    text = str(asset_class).strip()
    if not text:
        return {}
    parts = PART.findall(text)
    if not parts:
        return {text: 1.0}
    total = sum(float(v) for _, v in parts)
    return {k: float(v) / total for k, v in parts} if total > 0 else {}


Node = tuple[str, str, float]  # (node, parent, weight) in the targets tree


def class_mix(row: dict[str, Any]) -> dict[str, float]:
    """The class fractions of one valuation row, by the policy above."""
    if row["wrapper"] == "ibonds":
        return {IBOND_CLASS: 1.0}
    if str(row["symbol"]).strip().lower() == "cash":
        return {"BA": 1.0}
    return blend(str(row["asset_class"])) or {UNCLASSIFIED: 1.0}


def in_scope(vals: DataFrame) -> DataFrame:
    """The valuation rows the allocation counts: the latest date, 529s left out."""
    if vals is None or vals.empty:
        return vals
    latest = vals[vals["date"] == vals["date"].max()]
    return latest[~latest["wrapper"].isin(LEFT_OUT)].copy()


def by_class(rows: DataFrame) -> dict[str, float]:
    """{class: dollars} for valuation rows."""
    out: dict[str, float] = {}
    for r in rows.to_dict("records"):
        v = float(r["value"])
        for c, f in class_mix(r).items():
            out[c] = out.get(c, 0.0) + v * f
    return out


def tree(targets: DataFrame, today: str) -> list[Node]:
    """The tree in force: a list of (node, parent, weight) in the order given, or the seed when there is none."""
    if targets is None or targets.empty:
        return [(n, p, float(w)) for n, p, w in SEED]
    t = targets.copy()
    t["effective"] = t["effective"].astype(str).str[:10]
    t = t[t["effective"] <= today]
    if t.empty:
        return [(n, p, float(w)) for n, p, w in SEED]
    t = t[t["effective"] == t["effective"].max()]
    return [
        (str(n).strip(), str(p).strip(), float(pd.to_numeric(w, errors="coerce") or 0.0))
        for n, p, w in zip(t["node"], t["parent"], t["weight"])
        if str(n).strip()
    ]


def leaf_shares(nodes: Sequence[Node]) -> tuple[dict[str, float], dict[str, float]]:
    """{leaf: share of the whole} from a tree: each node's weight normalized among its siblings, times its
    parent's share."""
    kids: dict[str, list[tuple[str, float]]] = {}
    for n, p, w in nodes:
        kids.setdefault(p, []).append((n, w))
    shares: dict[str, float] = {}

    def walk(parent: str, share: float) -> None:
        children = kids.get(parent, [])
        total = sum(w for _, w in children)
        for n, w in children:
            s = share * (w / total if total > 0 else 0.0)
            shares[n] = s
            walk(n, s)

    walk("", 1.0)
    leaves = {n: s for n, s in shares.items() if n not in kids}
    return shares, leaves


def report(nodes: Sequence[Node], current: dict[str, float], proposed: Optional[dict[str, float]] = None) -> DataFrame:
    """TREE_COLUMNS: each node's target and current value and share, depth-first in the tree's order, then any
    class held that the tree doesn't name."""
    shares, leaves = leaf_shares(nodes)
    kids: dict[str, list[str]] = {}
    for n, p, _ in nodes:
        kids.setdefault(p, []).append(n)
    total = sum(current.values())
    proposed = proposed or {}
    rows, order = [], [0]

    def under(node: str) -> list[str]:
        if node not in kids:
            return [node]
        return [x for k in kids[node] for x in under(k)]

    def walk(parent: str, depth: int) -> None:
        for n in kids.get(parent, []):
            mine = under(n)
            cur = sum(current.get(c, 0.0) for c in mine)
            prop = sum(proposed.get(c, 0.0) for c in mine)
            order[0] += 1
            rows.append(
                {
                    "node": n,
                    "parent": parent,
                    "depth": depth,
                    "order": order[0],
                    "label": LABELS.get(n, n),
                    "leaf": n not in kids,
                    "target_share": round(shares[n], 6),
                    "target_value": round(shares[n] * total, 2),
                    "current_value": round(cur, 2),
                    "current_share": round(cur / total, 6) if total else 0.0,
                    "delta_value": round(cur - shares[n] * total, 2),
                    "proposed_value": round(prop, 2) if proposed else "",
                    "proposed_share": round(prop / total, 6) if proposed and total else "",
                }
            )
            walk(n, depth + 1)

    walk("", 0)
    for c in sorted(set(current) - set(leaves)):
        order[0] += 1
        cur = current[c]
        rows.append(
            {
                "node": c,
                "parent": "",
                "depth": 0,
                "order": order[0],
                "label": LABELS.get(c, c),
                "leaf": True,
                "target_share": 0.0,
                "target_value": 0.0,
                "current_value": round(cur, 2),
                "current_share": round(cur / total, 6) if total else 0.0,
                "delta_value": round(cur, 2),
                "proposed_value": round(proposed.get(c, 0.0), 2) if proposed else "",
                "proposed_share": round(proposed.get(c, 0.0) / total, 6) if proposed and total else "",
            }
        )
    return pd.DataFrame(rows, columns=TREE_COLUMNS)


def _targets(
    leaves: dict[str, float], total: float, fixed: dict[str, float], unreachable: Iterable[str] = ()
) -> dict[str, float]:
    """{class: target dollars}: leaf shares of the total, where a class no trade can change (held only where
    nothing trades and already over its target, or with no fund a tradable account could buy) keeps what it has
    and the rest share what remains in proportion to their weights."""
    leaves = {**{c: 0.0 for c in fixed}, **leaves}  # a class held with no node in the tree has a target of nothing
    pinned, targets = set(unreachable), {}
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


def location_kind(bucket: str) -> str:
    """How a bucket's money is taxed, for asset location: pretax, roth or hsa."""
    b = str(bucket)
    return "hsa" if b == "hsa" else "roth" if b.endswith("_roth") else "pretax"


def placement(mix: dict[str, float], kinds: dict[str, float]) -> float:
    """How poorly a fund (its class mix) suits an account (its share of money by kind): 0 where it belongs."""
    return sum(f * share * LOCATION.get(c, {}).get(kind, 0) for c, f in mix.items() for kind, share in kinds.items())


OWNER_WEIGHT = 30.0  # a dollar of one adult's portfolio off the target mix, beside 1000 for the household's


def _owner_shares(owner: str, people: Sequence[str]) -> dict[str, float]:
    """{person: share of an account's money}: all of it to its owner, a joint account's split evenly, nothing
    for the kids' accounts."""
    o = str(owner).strip().lower()
    if o in people:
        return {o: 1.0}
    if o == "joint" and people:
        return {p: 1.0 / len(people) for p in people}
    return {}


def _off(held: dict[str, float], targets_share: dict[str, float], total: float) -> float:
    """The share of a portfolio in the wrong classes: half the sum of each class's distance from its target."""
    if total <= 0:
        return 0.0
    keys = set(held) | set(targets_share)
    return sum(abs(held.get(c, 0.0) - targets_share.get(c, 0.0) * total) for c in keys) / 2 / total


def rebalance(
    vals: DataFrame,
    nodes: Sequence[Node],
    ever_held: Optional[dict[str, set[str]]] = None,
    location: bool = True,
    classes: Optional[dict[str, str]] = None,
    people: Sequence[str] = (),
) -> tuple[DataFrame, dict[str, float], list[str]]:
    """(TRADE_COLUMNS, {class: proposed dollars}, notes). vals are the in-scope valuation rows; ever_held is
    {account_id: set of symbols} the account has held (holdings and activity), which the proposal may buy.
    With location, among mixes equally near the targets it prefers each class where it is taxed best (LOCATION),
    when that is worth the trades it takes. classes is {symbol: asset_class} from securities, for the funds an
    account held before but holds none of now. people are the household's adults: among mixes equally near
    the household's targets it then keeps each adult's own money (theirs, and half of what is joint) near the
    target mix too, so neither carries a class the other holds none of, where the accounts allow."""
    from scipy.optimize import linprog

    notes: list[str] = []
    rows = vals.copy()
    rows["value"] = pd.to_numeric(rows["value"], errors="coerce").fillna(0.0)
    rows = rows[rows["value"].abs() >= 0.005]
    _, leaves = leaf_shares(nodes)
    total = float(rows["value"].sum())
    mixes = {(r["account_id"], r["bucket"], r["symbol"]): class_mix(r) for r in rows.to_dict("records")}

    def tradable(r: dict[str, Any]) -> bool:
        if r["wrapper"] in FIXED_WRAPPERS or not str(r["symbol"]).strip():
            return False
        return UNCLASSIFIED not in mixes[(r["account_id"], r["bucket"], r["symbol"])]

    rows["tradable"] = [tradable(r) for r in rows.to_dict("records")]
    fixed_rows = rows[~rows["tradable"]]
    fixed = by_class(fixed_rows)
    # the tradable money: per account (a plan's pre-tax and Roth parts hold one fund mix), its value, the funds
    # it may hold, and how its money is taxed, for asset location
    tradable_rows = rows[rows["tradable"]]
    accounts = tradable_rows.groupby("account_id", as_index=False)["value"].sum()
    funds: dict[str, dict[str, float]] = {}  # symbol -> class mix, for funds that can be bought
    for r in tradable_rows.to_dict("records"):
        funds[r["symbol"]] = mixes[(r["account_id"], r["bucket"], r["symbol"])]
    klass = {s: blend(c) for s, c in (classes or {}).items() if blend(c)}
    for r in vals.to_dict("records"):
        if str(r["symbol"]).strip():
            klass[r["symbol"]] = class_mix(r)
    variables: list[tuple[str, str, float]] = []  # (account, symbol, current)
    kinds: dict[str, dict[str, float]] = {}
    labels: dict[str, str] = {}
    for a in accounts.to_dict("records"):
        mine = tradable_rows[tradable_rows["account_id"] == a["account_id"]]
        held = mine.groupby("symbol")["value"].sum().to_dict()
        by_bucket = mine.groupby("bucket")["value"].sum()
        kinds[a["account_id"]] = {
            location_kind(b): float(v) / float(by_bucket.sum()) for b, v in by_bucket.items() if by_bucket.sum() > 0
        }
        labels[a["account_id"]] = "+".join(sorted(by_bucket.index))
        may = set(held) | {
            s
            for s in (ever_held or {}).get(a["account_id"], set())
            if UNCLASSIFIED not in klass.get(s, {UNCLASSIFIED: 1}) and s in klass
        }
        for s in sorted(may):
            variables.append((a["account_id"], s, float(held.get(s, 0.0))))
            funds.setdefault(s, klass[s])
    all_classes = sorted(set(leaves) | set(fixed) | {c for m in funds.values() for c in m})
    reachable = {c for _, s, _ in variables for c, f in funds[s].items() if f > 0}
    unreachable = [c for c in sorted(leaves) if c not in reachable]
    for c in unreachable:
        if leaves[c] * total > fixed.get(c, 0.0) + 0.5:
            notes.append(
                f"rebalance: no tradable account holds a fund for {LABELS.get(c, c)}, so it stays "
                f"{leaves[c] * total - fixed.get(c, 0.0):,.0f} under target without a new ticker; "
                "the other targets share its part"
            )
    targets = _targets(leaves, total, fixed, unreachable)
    pinned = [c for c in sorted(set(leaves) | set(fixed)) if fixed.get(c, 0.0) > leaves.get(c, 0.0) * total + 0.005]
    for c in pinned:
        notes.append(
            f"rebalance: {LABELS.get(c, c)} held where nothing trades ({fixed[c]:,.0f}) is over its "
            f"target of {leaves.get(c, 0.0) * total:,.0f}; the other targets share the rest"
        )
    if not variables:
        return pd.DataFrame(columns=TRADE_COLUMNS), dict(fixed), notes + ["rebalance: nothing tradable"]
    n, m = len(variables), len(all_classes)
    people = tuple(people or ())
    owner_of = {r["account_id"]: _owner_shares(r.get("owner", ""), people) for r in rows.to_dict("records")}
    balance = [p for p in people if any(p in owner_of.get(a, {}) for a, _, _ in variables)] if len(people) > 1 else []
    k = len(balance)
    share = {c: targets.get(c, 0.0) / total if total else 0.0 for c in all_classes}
    owner_fixed: dict[str, dict[str, float]] = {p: {} for p in balance}
    owner_total = {p: 0.0 for p in balance}
    for r in rows.to_dict("records"):
        mix = mixes[(r["account_id"], r["bucket"], r["symbol"])]
        for p, w in owner_of.get(r["account_id"], {}).items():
            if p not in owner_total:
                continue
            owner_total[p] += w * r["value"]
            if not r["tradable"]:
                for c, f in mix.items():
                    owner_fixed[p][c] = owner_fixed[p].get(c, 0.0) + w * r["value"] * f
    # x (n), then over/under per class (2m), then buy/sell per variable (2n), then over/under per adult and class (2km)
    owner_at = n + 2 * m + 2 * n
    size = owner_at + 2 * k * m
    cost = np.zeros(size)
    scale = max(total, 1.0)
    cost[n : n + 2 * m] = 1000.0 / scale
    cost[n + 2 * m : owner_at] = 1.0 / scale
    cost[owner_at:] = OWNER_WEIGHT / scale
    if location:  # each dollar of a class in an account that suits it less costs a little more
        for v_i, (acct, sym, _) in enumerate(variables):
            cost[v_i] = LOCATION_WEIGHT / scale * placement(funds[sym], kinds[acct])
    a_eq: list[np.ndarray] = []
    b_eq: list[float] = []
    for c_i, c in enumerate(all_classes):  # fixed + sum x*f - over + under = target
        row = np.zeros(size)
        for v_i, (_, s, _) in enumerate(variables):
            row[v_i] = funds[s].get(c, 0.0)
        row[n + c_i], row[n + m + c_i] = -1.0, 1.0
        a_eq.append(row)
        b_eq.append(targets.get(c, 0.0) - fixed.get(c, 0.0))
    for p_i, p in enumerate(balance):  # an adult's fixed + their part of sum x*f - over + under = their target
        for c_i, c in enumerate(all_classes):
            row = np.zeros(size)
            for v_i, (acct, s_, _) in enumerate(variables):
                row[v_i] = owner_of.get(acct, {}).get(p, 0.0) * funds[s_].get(c, 0.0)
            at = owner_at + 2 * (p_i * m + c_i)
            row[at], row[at + 1] = -1.0, 1.0
            a_eq.append(row)
            b_eq.append(share[c] * owner_total[p] - owner_fixed[p].get(c, 0.0))
    for _key, g in pd.DataFrame(variables, columns=["a", "s", "cur"]).groupby("a"):
        row = np.zeros(size)
        row[list(g.index)] = 1.0
        a_eq.append(row)
        b_eq.append(float(g["cur"].sum()))
    for v_i, (_, _, cur) in enumerate(variables):  # x - buy + sell = current
        row = np.zeros(size)
        row[v_i], row[n + 2 * m + v_i], row[n + 2 * m + n + v_i] = 1.0, -1.0, 1.0
        a_eq.append(row)
        b_eq.append(cur)
    result = linprog(cost, A_eq=np.array(a_eq), b_eq=np.array(b_eq), bounds=[(0, None)] * size, method="highs")
    if not result.success:
        return pd.DataFrame(columns=TRADE_COLUMNS), dict(fixed), notes + [f"rebalance: {result.message}"]
    x = result.x[:n]
    trades: list[dict[str, Any]] = []
    proposed = dict(fixed)
    for (acct, s, cur), new in zip(variables, x):
        for c, f in funds[s].items():
            proposed[c] = proposed.get(c, 0.0) + new * f
        trade = new - cur
        if abs(trade) >= MIN_TRADE or (cur > 0 and new < 0.5):
            mix = funds[s]
            label = ", ".join(f"{c} {f:.0%}" for c, f in mix.items()) if len(mix) > 1 else next(iter(mix))
            trades.append(
                {
                    "account_id": acct,
                    "bucket": labels[acct],
                    "symbol": s,
                    "asset_class": label,
                    "current": round(cur, 2),
                    "proposed": round(new, 2),
                    "trade": round(trade, 2),
                }
            )
    for p in balance:  # how far each adult's own money is from the target mix, now and after the trades
        now, after = dict(owner_fixed[p]), dict(owner_fixed[p])
        for (acct, s_, cur), new in zip(variables, x):
            w = owner_of.get(acct, {}).get(p, 0.0)
            for c, f in funds[s_].items():
                now[c] = now.get(c, 0.0) + w * cur * f
                after[c] = after.get(c, 0.0) + w * new * f
        notes.append(
            f"rebalance: {p}'s own money is {_off(now, share, owner_total[p]):.0%} off the target mix now, "
            f"{_off(after, share, owner_total[p]):.0%} after the trades"
        )
    out = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    if len(out):
        out = out.sort_values(["account_id", "bucket", "trade"]).reset_index(drop=True)
        moved = out["trade"].clip(lower=0).sum()
        notes.append(f"rebalance: {len(out)} trades in {out['account_id'].nunique()} accounts, {moved:,.0f} moved")
    else:
        notes.append("rebalance: on target, nothing to trade")
    return out, proposed, notes


def ever_held(holdings: DataFrame, activity: DataFrame, since: str = "") -> dict[str, set[str]]:
    """{account_id: symbols} each account has held or traded on or after since (a plan's lineup changes, so a
    fund last seen years ago may no longer be offered)."""
    out: dict[str, set[str]] = {}
    for df, day in ((holdings, "as_of"), (activity, "date")):
        if df is None or df.empty:
            continue
        recent = df[df[day].astype(str).str[:10] >= since]
        for a, s in zip(recent["account_id"].astype(str).str.strip(), recent["symbol"].astype(str).str.strip()):
            if s and s.lower() != "cash":
                out.setdefault(a, set()).add(s)
    return out
