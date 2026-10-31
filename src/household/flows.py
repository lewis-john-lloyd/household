"""Money flows for the Sankey view: where each month's and each year's money came from and went.

Pay comes from the pay stubs (one gross-pay node per person): gross pay splits
into taxes (federal, social security, Medicare, state), pre-tax deductions,
post-tax deductions and net pay, and net pay goes into "accounts". Other income
the ledger categorized (categories in the income group other than pay, whose
deposits the stubs already count) flows into accounts too. Out of accounts go
savings and every spending category, from categorize.actual_rows, so a split
charge counts in each of its categories. Transfers between the household's own
accounts are not flows.

Each period is balanced: when more went out than came in, the difference comes
from "savings and balances"; when less, it goes to "unspent".

Rows come at two levels: "category" (every tax line, deduction and category)
and "group" (taxes, deductions and the category groups from the categories
table), for the same periods: every month with ledger transactions, and every
calendar year made of those months. The old books (era legacy) are left out:
they have no pay stubs to balance against.
"""

import pandas as pd

FROM_BALANCES = "savings and balances"
UNSPENT = "unspent"
ACCOUNTS = "accounts"
NET_PAY = "net pay"
TAXES, PRE_TAX, POST_TAX, OTHER_INCOME = "taxes", "pre-tax deductions", "post-tax deductions", "other income"
COLUMNS = ["period", "level", "source", "target", "amount"]


def tax_node(line_item):
    name = str(line_item).lower()
    if "social" in name or name.strip() in ("ss", "oasdi") or "oasdi" in name:
        return "social security"
    if "medicare" in name or name.strip() in ("med", "medi"):
        return "medicare"
    if "federal" in name or name.strip() in ("fitw", "fit", "fed"):
        return "federal tax"
    return "state tax"


def _periods(month):
    return [month, month[:4]]


def _stub_flows(paystubs, months):
    """(period, source, target, amount, group-level source, group-level target) from the pay stubs."""
    rows = []
    if paystubs is None or paystubs.empty:
        return rows
    stubs = paystubs.copy()
    stubs["amount"] = pd.to_numeric(stubs["amount"], errors="coerce").fillna(0.0)
    stubs["month"] = stubs["pay_date"].astype(str).str[:7]
    stubs = stubs[stubs["month"].isin(months)]
    for (month, person), g in stubs.groupby(["month", "person"]):
        gross_node = f"gross pay ({person})"
        by_kind = {k: g[g["kind"] == k] for k in ("income", "tax", "pre_tax", "post_tax")}
        gross = float(by_kind["income"]["amount"].sum())
        out = 0.0
        for kind, group, name in (("tax", TAXES, tax_node), ("pre_tax", PRE_TAX, lambda n: f"pre-tax: {n}"),
                                  ("post_tax", POST_TAX, lambda n: f"post-tax: {n}")):
            items = by_kind[kind].groupby(by_kind[kind]["line_item"].map(name))["amount"].sum()
            for node, amount in items.items():
                if amount > 0:
                    rows.append((month, gross_node, node, amount, gross_node, group))
                    out += amount
                elif amount < 0:  # a reimbursement paid with the check adds to it
                    gross -= amount
        net = round(gross - out, 2)
        if net > 0:
            rows.append((month, gross_node, NET_PAY, net, gross_node, NET_PAY))
            rows.append((month, NET_PAY, ACCOUNTS, net, NET_PAY, ACCOUNTS))
    return rows


def build(paystubs, categorized, categories):
    """The flows table: one row per period, level, source and target."""
    from .categorize import actual_rows

    txns = categorized[(categorized["era"] != "legacy")]
    txns = actual_rows(txns).copy()
    txns["amount"] = pd.to_numeric(txns["amount"], errors="coerce").fillna(0.0)
    txns["month"] = txns["date"].astype(str).str[:7]
    months = sorted(set(txns["month"]) - {""})
    groups = dict(zip(categories["category"].astype(str).str.strip(), categories["group"].astype(str).str.strip())) \
        if categories is not None and not categories.empty else {}

    if not months:
        return pd.DataFrame(columns=COLUMNS)
    flows = _stub_flows(paystubs, set(months))
    txns = txns[~txns["category"].astype(str).str.strip().isin(["", "transfer"])]
    income = txns["category"].map(lambda c: groups.get(c) == "income" and c != "pay").astype(bool)
    for (month, category), amount in txns[income].groupby(["month", "category"])["amount"].sum().items():
        if amount > 0:
            flows.append((month, category, ACCOUNTS, amount, OTHER_INCOME, ACCOUNTS))
    spend = txns[~txns["category"].map(lambda c: groups.get(c) == "income").astype(bool)]
    for (month, category), amount in spend.groupby(["month", "category"])["amount"].sum().items():
        if amount < 0:
            flows.append((month, ACCOUNTS, category, -amount, ACCOUNTS, groups.get(category) or category))

    detail = pd.DataFrame(flows, columns=["month", "source", "target", "amount", "gsource", "gtarget"])
    out = []
    for level, src, dst in (("category", "source", "target"), ("group", "gsource", "gtarget")):
        for period_kind in (0, 1):
            d = detail.assign(period=detail["month"].map(lambda m: _periods(m)[period_kind]))
            sums = d.groupby(["period", src, dst], as_index=False)["amount"].sum()
            sums.columns = ["period", "source", "target", "amount"]
            for period, p in sums.groupby("period"):
                into = p.loc[p["target"] == ACCOUNTS, "amount"].sum()
                out_of = p.loc[p["source"] == ACCOUNTS, "amount"].sum()
                gap = round(out_of - into, 2)
                extra = ([(period, FROM_BALANCES, ACCOUNTS, gap)] if gap > 0 else
                         [(period, ACCOUNTS, UNSPENT, -gap)] if gap < 0 else [])
                p = pd.concat([p, pd.DataFrame(extra, columns=["period", "source", "target", "amount"])],
                              ignore_index=True)
                out.append(p.assign(level=level))
    if not out:
        return pd.DataFrame(columns=COLUMNS)
    result = pd.concat(out, ignore_index=True)
    result["amount"] = result["amount"].round(2)
    result = result[result["amount"] > 0]
    return result[COLUMNS].sort_values(["period", "level", "source", "target"]).reset_index(drop=True)
