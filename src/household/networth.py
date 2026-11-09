"""Net worth on each valuation date: investments by tax bucket, home equity, and other debt, by owner.

Investments come from valuation (every investment account, 529s included). Home equity is each home's counted
value less its loans (house.py). Other debt is any loan in loan_balances that no home lists. Owners are the
household's adults (valuation.adults) and the kids; a joint account or home splits evenly between the adults.
"""

import pandas as pd

from . import house, valuation

COLUMNS = ["date", "part", "owner", "value"]


def build(vals, people, properties, values, loans, schedule, balances, rule):
    """COLUMNS, one row per date, part and owner with a value."""
    rows = []
    if vals is not None and len(vals):
        v = vals.copy()
        v["value"] = pd.to_numeric(v["value"], errors="coerce").fillna(0.0)
        shares = valuation.split_by_owner(v, people)
        for owner in shares.columns:
            v[owner + "\0"] = shares[owner]
        for (day, part), g in v.groupby(["date", "bucket"]):
            for owner in shares.columns:
                amount = float(g[owner + "\0"].sum())
                if abs(amount) >= 0.005:
                    rows.append({"date": day, "part": part, "owner": owner, "value": round(amount, 2)})
        dates = sorted(set(v["date"]))
    else:
        dates = []
    attached = {i for p in properties.to_dict("records") for i in house.loan_ids(p)} if len(properties) else set()
    loose = sorted(set(balances["loan_id"]) - attached) if len(balances) else []
    for day in dates:
        homes = house.equity_on(properties, values, loans, schedule, balances, day, rule) if len(properties) else []
        debts = [("joint", -(house.loan_balance(i, day, loans, schedule, balances) or 0.0)) for i in loose]
        for part, items in (("home_equity", [(owner, equity) for _, owner, _, _, equity in homes]),
                            ("other_debt", debts)):
            for owner, amount in items:
                if abs(amount) < 0.005:
                    continue
                split = valuation.split_by_owner(pd.DataFrame({"owner": [owner], "value": [amount]}), people)
                for who in split.columns:
                    share = float(split[who].iloc[0])
                    if abs(share) >= 0.005:
                        rows.append({"date": day, "part": part, "owner": who, "value": round(share, 2)})
    out = pd.DataFrame(rows, columns=COLUMNS)
    if len(out):
        out = out.groupby(["date", "part", "owner"], as_index=False)["value"].sum()
        out["value"] = out["value"].round(2)
    return out[COLUMNS]


def totals(net):
    """{date: total} for the log."""
    if net is None or net.empty:
        return {}
    return net.groupby("date")["value"].sum().round(2).to_dict()
