"""Money flows for the Sankey view: where each month's and each year's money came from and went.

Pay comes from the pay stubs (one gross-pay node per person): gross pay splits
into taxes (federal, social security, Medicare, state), pre-tax deductions,
post-tax deductions and net pay, and net pay goes into "accounts". A month
counts the stubs paid in it. A year counts each line's year-to-date amount on
the year's last stub that carries it (per person and employer), so a year with
stubs missing still shows the whole year's pay, taxes and deductions up to its
last stub. A stub without year-to-date amounts, or a year the ledger starts
partway through, falls back to adding up the stubs.

Pay deposits (category pay) that the stubs' net pay does not account for (a
missing stub) flow into accounts as "pay without a stub (person)". A deposit
is a person's when it names one of their stubs' employers (the first word of
the employer's name); a pay deposit naming no employer on the stubs is "pay
without a stub".

Shares that vest are pay too: the stub counts the vest's value as income, takes
the taxes on it from the shares, and backs the shares kept out of the check
with an "offset" line (post-tax). Those shares went to the brokerage, not to
an account, so an offset line flows to VESTED, in the savings group.

Every other category comes from the ledger's transactions (categorize.
actual_rows, so a split charge counts in each of its categories), netted over
the period, so money back in one month offsets spending in another:
  income group (other than pay)  money in: category -> accounts ("other income")
  savings group                  money out: accounts -> category (a 529, the
                                 brokerage); money in: "from savings: category"
                                 -> accounts (shares sold, a withdrawal)
  everything else                money out: accounts -> category; money in, when
                                 more came back than went out: "category (money
                                 back)" -> accounts
Transfers between the household's own accounts are not flows. Selling shares
is drawing on savings, not income: the shares were income when they vested, at
their value then, which is their basis. Only what a sale made or lost against
that basis is new: each period's net realized gain on share sales (equity_sales,
by sale date) flows from "capital gains" into savings, and a net loss flows
from savings to "capital losses". Neither touches the accounts, so a sale whose
cash reaches the bank in a later month still balances, and the proceeds that
do arrive come "from savings" as before.

Each period is balanced: when more went out of accounts than came in, the
difference came from cash already in the accounts ("from cash balances"); when
less, it stayed there ("to cash balances").

Rows come at two levels: "category" (every tax line, deduction and category)
and "group" (taxes, deductions and the category groups from the categories
table), for the same periods: every month with ledger transactions, every
calendar year made of those months, and each year's monthly average (the
year's flows divided by the months it has data for, so a partial year
compares with a full one). The old books (era legacy) are left out:
they have no pay stubs to balance against.
"""

import re

import pandas as pd

FROM_BALANCES = "from cash balances"
UNSPENT = "to cash balances"
SAVINGS, FROM_SAVINGS, MONEY_BACK = "savings", "from savings", "money back"
VESTED = "vested shares (to savings)"
ACCOUNTS = "accounts"
NET_PAY = "net pay"
TAXES, PRE_TAX, POST_TAX, OTHER_INCOME = "taxes", "pre-tax deductions", "post-tax deductions", "other income"
NO_STUB = "pay without a stub"
GAINS, GAINS_KEPT = "capital gains", "capital gains (to savings)"
LOSSES_FROM, LOSSES = f"{FROM_SAVINGS}: capital losses", "capital losses"
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


AVERAGE = "monthly average"


def _periods(month):
    """The periods a month's flows count in: the month, its year, and its year's monthly average."""
    return [month, month[:4], f"{month[:4]} {AVERAGE}"]


def _check_flows(period, person, g, rows):
    """Flows for one person's pay in one period, from lines with kind, line_item and amount.

    Returns the net pay."""
    gross_node = f"gross pay ({person})"
    by_kind = {k: g[g["kind"] == k] for k in ("income", "tax", "pre_tax", "post_tax")}
    gross = float(by_kind["income"]["amount"].sum())
    out = 0.0
    for kind, group, name in (("tax", TAXES, tax_node), ("pre_tax", PRE_TAX, lambda n: f"pre-tax: {n}"),
                              ("post_tax", POST_TAX, lambda n: f"post-tax: {n}")):
        items = by_kind[kind].groupby(by_kind[kind]["line_item"].map(name))["amount"].sum()
        for node, amount in items.items():
            vested = kind == "post_tax" and "offset" in node.lower()  # shares kept, sent to the brokerage
            if amount > 0:
                rows.append((period, gross_node, VESTED if vested else node, amount, gross_node,
                             SAVINGS if vested else group))
                out += amount
            elif amount < 0:  # a reimbursement paid with the check adds to it
                gross -= amount
    net = round(gross - out, 2)
    if net > 0:
        rows.append((period, gross_node, NET_PAY, net, gross_node, NET_PAY))
        rows.append((period, NET_PAY, ACCOUNTS, net, NET_PAY, ACCOUNTS))
    return max(net, 0.0)


def _stubs(paystubs):
    stubs = paystubs.copy()
    stubs["amount"] = pd.to_numeric(stubs["amount"], errors="coerce").fillna(0.0)
    ytd = stubs["ytd_amount"] if "ytd_amount" in stubs else pd.Series("", index=stubs.index)
    stubs["ytd"] = pd.to_numeric(ytd.astype(str).str.replace(",", "").str.strip(), errors="coerce")
    stubs["pay_date"] = stubs["pay_date"].astype(str).str[:10]
    stubs["month"], stubs["year"] = stubs["pay_date"].str[:7], stubs["pay_date"].str[:4]
    if "employer" not in stubs:
        stubs["employer"] = ""
    return stubs[stubs["kind"].isin(["income", "tax", "pre_tax", "post_tax"])]


def _stub_flows(paystubs, months):
    """Month flows from the stubs paid in each month: (rows, {(month, person): net pay})."""
    rows, net = [], {}
    if paystubs is None or paystubs.empty:
        return rows, net
    stubs = _stubs(paystubs)
    stubs = stubs[stubs["month"].isin(months)]
    for (month, person), g in stubs.groupby(["month", "person"]):
        net[(month, person)] = _check_flows(month, person, g, rows)
    return rows, net


def year_lines(stubs):
    """A person's lines for one year at one employer: each line's year-to-date amount on the last stub
    that carries it, or, when any line lacks a year-to-date amount, the sum of the year's stubs."""
    if stubs["ytd"].isna().any():
        return stubs.groupby(["kind", "line_item"], as_index=False)["amount"].sum()
    s = stubs.assign(size=stubs["ytd"].abs()).sort_values(["pay_date", "size"])
    last = s.groupby(["kind", "line_item"], as_index=False).last()
    return last[["kind", "line_item"]].assign(amount=last["ytd"])


def _year_flows(paystubs, months):
    """Year flows: (rows, {(year, person): net pay}). A year whose ledger starts in January takes the year-to-date
    amounts; a year the ledger starts partway through adds up the stubs paid in its months."""
    rows, net = [], {}
    if paystubs is None or paystubs.empty:
        return rows, net
    stubs = _stubs(paystubs)
    whole = {m[:4] for m in months if m.endswith("-01")}
    stubs = stubs[stubs["month"].isin(months) | stubs["year"].isin(whole)]
    for (year, person), g in stubs.groupby(["year", "person"]):
        if year in whole:
            lines = pd.concat([year_lines(e) for _, e in g.groupby("employer")], ignore_index=True)
        else:
            lines = g.groupby(["kind", "line_item"], as_index=False)["amount"].sum()
        net[(year, person)] = _check_flows(year, person, lines, rows)
    return rows, net


def employer_word(employer):
    """The word that names an employer on its deposits: "Acme Widgets, LLC" -> "ACME"."""
    words = re.sub(r"[^A-Z ]", " ", str(employer).upper()).split()
    return next((w for w in words if len(w) >= 3), "")


def payers(paystubs):
    """{employer word: person} from the stubs, to tell whose pay a deposit is."""
    if paystubs is None or paystubs.empty or "employer" not in paystubs:
        return {}
    pairs = paystubs[["employer", "person"]].drop_duplicates()
    return {w: p for w, p in zip(pairs["employer"].map(employer_word), pairs["person"]) if w}


def _whose(pay, words):
    text = pay["payee_raw"].astype(str).str.upper() if "payee_raw" in pay else pd.Series("", index=pay.index)
    return text.map(lambda t: next((p for w, p in words.items() if re.search(rf"\b{w}\b", t)), ""))


def _no_stub(pay, period, net):
    """Pay deposits beyond the stubs' net pay, per period and person: a missing stub. A deposit from a
    payer with no stubs at all counts in full."""
    rows = []
    for (p, person), amount in pay.groupby([period, "person"])["amount"].sum().items():
        gap = round(amount - net.get((p, person), 0.0), 2) if person else round(amount, 2)
        node = f"{NO_STUB} ({person})" if person else NO_STUB
        if gap > 0:
            rows.append((p, node, ACCOUNTS, gap, node, ACCOUNTS))
    return rows


def _gain_flows(equity_sales, months):
    """Net realized gain or loss on share sales: (month rows, year rows), for the periods the ledger has."""
    if equity_sales is None or equity_sales.empty or "gain" not in equity_sales:
        return [], []
    sales = equity_sales.assign(gain=pd.to_numeric(equity_sales["gain"], errors="coerce").fillna(0.0),
                                month=equity_sales["sale_date"].astype(str).str[:7])
    years = {m[:4] for m in months}
    out = []
    for kept, key in ((sales[sales["month"].isin(months)], sales["month"]),
                      (sales[sales["month"].str[:4].isin(years)], sales["month"].str[:4])):
        rows = []
        for period, net in kept.groupby(key.loc[kept.index])["gain"].sum().items():
            net = round(float(net), 2)
            if net > 0:
                rows.append((period, GAINS, GAINS_KEPT, net, GAINS, SAVINGS))
            elif net < 0:
                rows.append((period, LOSSES_FROM, LOSSES, -net, FROM_SAVINGS, LOSSES))
        out.append(rows)
    return out[0], out[1]


def _category_flows(by_period, groups):
    """Flow rows (period, source, target, amount, group source, group target) for each category's net."""
    rows = []
    for period, category, amount in by_period[["period", "category", "amount"]].itertuples(index=False):
        group = groups.get(category) or category
        if abs(amount) < 0.005:
            continue
        if group == "income":
            if amount > 0:
                rows.append((period, category, ACCOUNTS, amount, OTHER_INCOME, ACCOUNTS))
            else:
                rows.append((period, ACCOUNTS, category, -amount, ACCOUNTS, OTHER_INCOME))
        elif amount < 0:
            rows.append((period, ACCOUNTS, category, -amount, ACCOUNTS, group))
        elif group == SAVINGS:
            rows.append((period, f"{FROM_SAVINGS}: {category}", ACCOUNTS, amount, FROM_SAVINGS, ACCOUNTS))
        else:
            rows.append((period, f"{category} ({MONEY_BACK})", ACCOUNTS, amount, MONEY_BACK, ACCOUNTS))
    return rows


def build(paystubs, categorized, categories, equity_sales=None):
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
    months_in_year = pd.Series([m[:4] for m in months]).value_counts().to_dict()
    txns = txns[~txns["category"].astype(str).str.strip().isin(["", "transfer"])]
    pay = txns[txns["category"].astype(str).str.strip() == "pay"]
    pay = pay.assign(person=_whose(pay, payers(paystubs)), year=pay["month"].str[:4])
    month_rows, month_net = _stub_flows(paystubs, set(months))
    year_rows, year_net = _year_flows(paystubs, set(months))
    month_rows += _no_stub(pay, "month", month_net)
    year_rows += _no_stub(pay, "year", year_net)
    month_gains, year_gains = _gain_flows(equity_sales, set(months))
    month_rows += month_gains
    year_rows += year_gains
    signed = txns[txns["category"].astype(str).str.strip() != "pay"]
    signed = signed.groupby(["month", "category"], as_index=False)["amount"].sum()

    cols = ["month", "source", "target", "amount", "gsource", "gtarget"]
    pay_detail = {0: pd.DataFrame(month_rows, columns=cols), 1: pd.DataFrame(year_rows, columns=cols)}
    out = []
    for level, src, dst in (("category", "source", "target"), ("group", "gsource", "gtarget")):
        for period_kind in (0, 1, 2):
            by_period = signed.assign(period=signed["month"].map(lambda m, k=period_kind: _periods(m)[min(k, 1)]))
            by_period = by_period.groupby(["period", "category"], as_index=False)["amount"].sum()
            detail = pd.DataFrame(_category_flows(by_period, groups), columns=cols)
            d = pd.concat([detail, pay_detail[min(period_kind, 1)]], ignore_index=True)
            d = d.assign(period=d["month"].map(lambda m, k=period_kind: _periods(m)[k] if len(m) == 7 else
                                               [m, m, f"{m} {AVERAGE}"][k]))
            sums = d.groupby(["period", src, dst], as_index=False)["amount"].sum()
            sums.columns = ["period", "source", "target", "amount"]
            if period_kind == 2:  # the year's total spread over the months it has data for
                sums["amount"] = sums["amount"] / sums["period"].str[:4].map(months_in_year)
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
