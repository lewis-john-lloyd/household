"""Building the plan's inputs from the ledger and Reports, and the spending budget it uses."""

import re
from datetime import date

import pandas as pd

from .. import markets, retirelaw as law, settings, valuation
from .model import DEFAULTS, Household, LAW_YEAR, Person, TRUSTEES_CUT_YEAR
from .simulation import central

# --- building the inputs from the ledger ------------------------------------------------------------------

BUCKET_MAP = {
    "401k_pretax": "pre_plan",
    "ira_pretax": "pre_ira",
    "401k_roth": "roth",
    "ira_roth": "roth",
    "hsa": "hsa",
    "taxable": "taxable",
    "ibonds": "taxable",
    "other": "taxable",
}
STOCK_CLASSES = ("SDT", "SDS", "SIT", "SIE", "REIT")


def _num(s):
    return pd.to_numeric(s if isinstance(s, pd.Series) else pd.Series(s, dtype=object), errors="coerce").fillna(0.0)


def _owners(owner, adults):
    o = str(owner).strip().lower()
    if o in adults:
        return {o: 1.0}
    if o in ("joint", "") and adults:
        return {a: 1 / len(adults) for a in adults}
    return {}


def spending_steps(text):
    """[(from year or None, amount)] from a retirement_spending value: "12000", "0 from 2032",
    "18000; 9000 from 2040; 0 from 2050". None when it can't be read."""
    out = []
    for part in str(text).split(";"):
        m = re.fullmatch(r"\s*(-?[\d,]+(?:\.\d+)?)(?:\s+from\s+(\d{4}))?\s*", part)
        if not m:
            return None
        out.append((int(m.group(2)) if m.group(2) else None, float(m.group(1).replace(",", ""))))
    return out


LUMPY_MIN, LUMPY_SHARE = 5_000, 0.6


def baseline(actuals, categories, today, leave_out=("taxes",)):
    """(spending by category over the last 12 full months, {category: group}, the months used, {category: the
    share of its spending in its two biggest months} for the lumpy ones)."""
    month = today[:7]
    groups = dict(zip(categories["category"].astype(str), categories["group"].astype(str))) if len(categories) else {}
    if actuals is None or actuals.empty:
        return {}, groups, [], {}
    a = actuals.assign(spend=_num(actuals["spend"]), month=actuals["month"].astype(str))
    months = sorted(m for m in a["month"].unique() if m < month)[-12:]
    a = a[a["month"].isin(months)]
    skip = {"", "transfer"} | set(leave_out)
    keep = [
        c
        for c in a["category"].astype(str).unique()
        if c not in skip and groups.get(c, "") not in ({"income", "savings", "transfer"} | set(leave_out))
    ]
    kept = a[a["category"].isin(keep)]
    by = kept.groupby("category")["spend"].sum()
    scale = 12 / len(months) if months else 1
    # a category whose year came mostly in a month or two (a renovation, a year's tuition) may not recur
    lumpy = {}
    for c, g in kept.groupby("category"):
        total = g["spend"].sum()
        top = g["spend"].nlargest(2).sum()
        if total > LUMPY_MIN and top > LUMPY_SHARE * total:
            lumpy[c] = top / total
    return {c: float(v) * scale for c, v in by.items()}, groups, months, lumpy


def spending_path(base, groups, years, changes, loans):
    """({year: spending}, the adjustment rows): each category's last-12-months amount, a category or group
    named in changes replaced by its steps from their years, a new name added, each loan's payments taken
    off after its payoff year."""
    rows = []
    path = {}
    by_group = {}
    for c in base:
        by_group.setdefault(groups.get(c, c), []).append(c)
    for y in years:
        total = 0.0
        handled = set()
        for name, steps in changes.items():
            members = [name] if name in base else by_group.get(name, [])
            current = sum(base[c] for c in members) if members else 0.0
            amount = current
            for start, value in steps:
                if start is None or y >= start:
                    amount = value
            total += amount
            handled |= set(members)
        total += sum(v for c, v in base.items() if c not in handled)
        for loan_id, (payoff_year, yearly) in loans.items():
            if y > payoff_year and loan_id not in changes:
                total -= yearly
        path[y] = max(total, 0.0)
    for name, steps in changes.items():
        members = [name] if name in base else by_group.get(name, [])
        now = sum(base[c] for c in members)
        rows.append(
            {
                "key": name,
                "now": now,
                "steps": "; ".join(f"{v:,.0f}" + (f" from {s}" if s else "") for s, v in steps),
                "basis": "a category" if name in base else ("a group" if members else "added"),
            }
        )
    for loan_id, (payoff_year, yearly) in loans.items():
        if loan_id not in changes:
            rows.append(
                {
                    "key": loan_id,
                    "now": yearly,
                    "steps": f"0 from {payoff_year + 1}",
                    "basis": "the loan's payments end when it is paid off",
                }
            )
    return path, rows


BUDGET_COLUMNS = [
    "line",
    "group",
    "kind",
    "now",
    "first_year",
    "at_retirement",
    "retire_year",
    "setting",
    "is_set",
    "lumpy",
    "note",
]


def _step(steps, year, current):
    amount = current
    for start, value in steps:
        if start is None or year >= start:
            amount = value
    return amount


def budget_table(h, retire_year):
    """The Retirement page's Budget: each group, each category in it, each added line and each loan's end, with
    what it is now (the last 12 months, a year), in the plan's first year and in the retirement year, and its
    retirement_spending setting (is_set when the ledger has one, blank or not, so an edit knows what it replaces).
    A category whose group has a setting is counted in the group's line (its
    first_year and at_retirement are blank). The lines' amounts add up to h.spending in those years."""
    b = h.budget or {}
    base, groups, changes = b.get("base", {}), b.get("groups", {}), b.get("changes", {})
    loans, lumpy, raw = b.get("loans", {}), b.get("lumpy", {}), b.get("settings", {})
    first = h.start
    rows = []

    def row(line, group, kind, now, at, note=""):
        rows.append(
            {
                "line": line,
                "group": group,
                "kind": kind,
                "now": round(now, 2),
                "first_year": "" if at is None else round(at(first), 2),
                "at_retirement": "" if at is None else round(at(retire_year), 2),
                "retire_year": retire_year,
                "setting": raw.get(line, ""),
                "is_set": "yes" if line in raw else "",
                "lumpy": round(lumpy[line], 3) if line in lumpy else "",
                "note": note,
            }
        )

    by_group = {}
    for c in base:
        by_group.setdefault(groups.get(c, c), []).append(c)
    for g in sorted(by_group):
        members = sorted(by_group[g])
        now = sum(base[c] for c in members)
        if g in changes and g not in base:
            row(g, g, "group", now, lambda y, g=g, now=now: _step(changes[g], y, now))
        else:
            row(
                g,
                g,
                "group",
                now,
                lambda y, ms=members: sum(_step(changes[c], y, base[c]) if c in changes else base[c] for c in ms),
            )
        for c in members:
            if members == [g]:
                continue  # a group of one category of its name is one line
            covered = g in changes and c not in changes and g not in base
            row(
                c,
                g,
                "category",
                base[c],
                None if covered else (lambda y, c=c: _step(changes.get(c, []), y, base[c])),
                "counted in its group's setting" if covered else "",
            )
    for name in sorted(changes):
        if name not in base and name not in by_group:
            row(name, "", "added", 0.0, lambda y, n=name: _step(changes[n], y, 0.0))
    for loan_id, (payoff_year, yearly) in sorted(loans.items()):
        if loan_id not in changes:
            row(
                loan_id,
                "",
                "loan",
                0.0,
                lambda y, p=payoff_year, v=yearly: -v if y > p else 0.0,
                f"its payments, {yearly:,.0f} a year, end after {payoff_year}",
            )
    return pd.DataFrame(rows, columns=BUDGET_COLUMNS)


def build(
    params,
    people_names,
    statements,
    estimates,
    earnings,
    accounts,
    values,
    activity,
    lots,
    actuals,
    categories,
    schedule,
    vests,
    allocation_share,
    today,
):
    """(Household, notes, assumption rows) from the ledger and Reports frames; Household None when an adult's
    birth date is missing."""
    today_year = int(today[:4])
    notes, assume = [], []
    value = lambda key, default="": settings.value(params, key, default)  # noqa: E731
    number = lambda key, default: settings.number(params, key, default)  # noqa: E731

    def note(section, key, label, val, source, setting=""):
        assume.append(
            {"section": section, "key": key, "label": label, "value": val, "source": source, "setting": setting}
        )

    if not people_names:
        return None, ["retirement: no adults (household_adults, or the people on pay stubs)"], pd.DataFrame(assume)
    st = statements.copy() if statements is not None else pd.DataFrame(columns=["person", "birth_date", "as_of"])
    people = []
    for name in people_names:
        mine = st[st["person"].astype(str) == name].sort_values("as_of")
        born = value(f"birth_date:{name}") or (mine["birth_date"].iloc[-1] if len(mine) else "")
        try:
            born_d = date.fromisoformat(str(born).strip())
        except ValueError:
            notes.append(
                f"retirement: no birth date for {name}: set birth_date:{name} (YYYY-MM-DD) or add their "
                "Social Security statement"
            )
            return None, notes, pd.DataFrame(assume)
        as_of = str(mine["as_of"].iloc[-1]) if len(mine) else ""
        e = (
            earnings[(earnings["person"].astype(str) == name) & (earnings["as_of"].astype(str) == as_of)]
            if len(mine) and earnings is not None and len(earnings)
            else pd.DataFrame()
        )
        est = (
            estimates[
                (estimates["person"].astype(str) == name)
                & (estimates["as_of"].astype(str) == as_of)
                & (estimates["kind"].astype(str) == "retirement")
            ]
            if len(mine) and estimates is not None and len(estimates)
            else pd.DataFrame()
        )
        claim = int(number(f"social_security_claim_age:{name}", DEFAULTS["claim_age"]))
        claim = min(max(claim, 62), 70)
        person = Person(
            name,
            born_d,
            claim,
            {int(y): float(v) for y, v in zip(e.get("year", []), _num(e.get("fica_earnings", [])))},
            {int(a): float(v) for a, v in zip(est.get("age", []), _num(est.get("monthly", [])))},
            int(as_of[:4]) if as_of else 0,
            number(f"social_security_monthly:{name}", 0.0),
            float(_num(e.get("medicare_earnings", pd.Series(dtype=float))).iloc[-1]) if len(e) else 0.0,
        )
        people.append(person)
        note(
            "people",
            f"born:{name}",
            f"{name} born",
            born_d.isoformat(),
            "the setting" if value(f"birth_date:{name}") else "the Social Security statement",
            f"birth_date:{name}",
        )
        note(
            "people",
            f"claim:{name}",
            f"{name} claims Social Security at",
            claim,
            "default (full retirement age)" if not value(f"social_security_claim_age:{name}") else "the setting",
            f"social_security_claim_age:{name}",
        )
        if not person.earnings and not person.fra_monthly:
            notes.append(
                f"retirement: no Social Security statement for {name}; their benefit counts as none until "
                f"one is added (or social_security_monthly:{name} is set)"
            )

    adults = [p.name for p in people]
    accts = accounts.set_index("account_id") if len(accounts) else pd.DataFrame()
    v = values[values["date"].astype(str) == today] if len(values) else values
    bal = {k: {a: 0.0 for a in adults} for k in ("pre_plan", "pre_ira", "roth")}
    taxable = hsa = 0.0
    taxable_accounts = set()
    for r in v.to_dict("records"):
        kind = BUCKET_MAP.get(str(r["bucket"]))
        amount = float(pd.to_numeric(r["value"], errors="coerce") or 0.0)
        if not kind:
            continue
        shares = _owners(r["owner"], adults)
        if not shares:
            continue
        if kind == "taxable":
            taxable += amount
            taxable_accounts.add(r["account_id"])
        elif kind == "hsa":
            hsa += amount
        else:
            for a, s in shares.items():
                bal[kind][a] += amount * s
    lot_rows = lots[lots["account_id"].isin(taxable_accounts)] if lots is not None and len(lots) else pd.DataFrame()
    lot_value, lot_basis = (
        (float(_num(lot_rows["value"]).sum()), float(_num(lot_rows["basis"]).sum())) if len(lot_rows) else (0.0, 0.0)
    )
    basis = lot_basis + max(taxable - lot_value, 0.0)

    since = (pd.Timestamp(today) - pd.DateOffset(years=1)).date().isoformat()
    contributions = {a: {"pre_plan": 0.0, "roth": 0.0} for a in adults}
    roth_basis = {a: 0.0 for a in adults}
    pending = {a: {} for a in adults}
    hsa_in = into_ira_529 = 0.0
    if activity is not None and len(activity):
        act = activity.assign(amount=_num(activity["amount"]), date=activity["date"].astype(str))
        for r in act.to_dict("records"):
            if r["account_id"] not in accts.index:
                continue
            acct = accts.loc[r["account_id"]]
            kind = BUCKET_MAP.get(
                valuation.bucket(
                    valuation.wrapper({**acct.to_dict(), "account_id": r["account_id"]}),
                    acct["tax_treatment"],
                    r.get("source", ""),
                )
            )
            shares = _owners(acct["owner"], adults)
            recent = r["date"] > since
            if str(acct["type"]) in ("ira", "education") and recent and r["kind"] in ("contribution", "withdrawal"):
                into_ira_529 += r["amount"]
            if kind == "roth" and r["kind"] in ("contribution", "conversion_in"):
                for a, s in shares.items():
                    roth_basis[a] += r["amount"] * s
            if not recent or not shares:
                continue
            if r["kind"] in ("contribution", "conversion_in", "conversion_out") and kind in (
                "pre_plan",
                "pre_ira",
                "roth",
            ):
                for a, s in shares.items():
                    key = "roth" if kind == "roth" else ("pre_plan" if kind == "pre_plan" else "pre_ira")
                    if key == "pre_ira":
                        continue  # money through an IRA on its way to a Roth: the Roth row counts it
                    contributions[a][key] += r["amount"] * s
            if r["kind"] == "contribution" and kind == "hsa":
                hsa_in += r["amount"]

    base, groups, months, lumpy = baseline(
        actuals, categories, today, [x.strip() for x in value("retirement_leave_out", "taxes").split(",") if x.strip()]
    )
    leave_out = [x.strip() for x in value("retirement_leave_out", "taxes").split(",") if x.strip()]
    came_in = left_out = 0.0
    if actuals is not None and len(actuals) and months:
        a = actuals.assign(spend=_num(actuals["spend"]))
        a = a[a["month"].astype(str).isin(months)]
        scale = 12 / len(months)
        came_in = -float(a[a["category"].map(lambda c: groups.get(str(c)) == "income")]["spend"].sum()) * scale
        left_out = (
            float(a[a["category"].map(lambda c: str(c) in leave_out or groups.get(str(c)) in leave_out)]["spend"].sum())
            * scale
        )
    vested = 0.0
    if vests is not None and len(vests):
        vv = vests[vests["vest_date"].astype(str) > since]
        vested = float((_num(vv["net_shares"]) * _num(vv["fmv"])).sum())

    changes = {}
    for key in (
        set(params.loc[params["jurisdiction"].astype(str) == "household", "key"].astype(str)) if len(params) else ()
    ):
        base_key, _, name = key.partition(":")
        if base_key == "retirement_spending" and name and str(value(key)).strip():
            steps = spending_steps(value(key))
            if steps is None:
                notes.append(
                    f"retirement: {key} can't be read ({value(key)!r}); write it like 12000 or "
                    "0 from 2032 or 18000; 9000 from 2040"
                )
            elif steps:
                changes[name] = steps
    loans = {}
    if schedule is not None and len(schedule):
        s = schedule.assign(
            interest=_num(schedule["interest"]),
            principal=_num(schedule["principal"]),
            extra=_num(schedule.get("extra_principal", 0)),
            month=schedule["month"].astype(str),
        )
        for loan_id, g in s.groupby("loan_id"):
            last12 = g[(g["month"] < today[:7])].tail(12)
            if len(last12) and g["month"].max() >= today[:7]:
                loans[str(loan_id)] = (
                    int(g["month"].max()[:4]),
                    float((last12["interest"] + last12["principal"] - last12["extra"]).sum()),
                )
    start = today_year + 1
    end = max(p.born.year for p in people) + int(number("retirement_plan_to_age", DEFAULTS["plan_to_age"]))
    path, adjust = spending_path(base, groups, range(start, end + 1), changes, loans)
    # what working leaves for taxable accounts: pay and other income into the bank and the shares that vest,
    # less spending as the settings have it from next year, the taxes paid and what goes to IRAs and 529s
    derived = came_in + vested - path.get(start, 0.0) - left_out - into_ira_529
    taxable_savings = number("retirement_taxable_savings", derived) if value("retirement_taxable_savings") else derived
    taxable_savings = max(taxable_savings, 0.0)
    for a in adults:
        for kind, key in (("pre_plan", "retirement_pre_tax_contributions"), ("roth", "retirement_roth_contributions")):
            if value(f"{key}:{a}"):
                contributions[a][kind] = number(f"{key}:{a}", contributions[a][kind])
    hsa_names = [x.strip() for x in value("hsa_spending_categories", "medical").split(",") if x.strip()]
    hsa_spending = sum(v for c, v in base.items() if c in hsa_names or groups.get(c) in hsa_names)

    cagr_text = value("retirement_cagrs")
    try:
        cagrs = tuple(float(x) for x in cagr_text.split(",")) if cagr_text else DEFAULTS["cagrs"]
    except ValueError:
        cagrs = DEFAULTS["cagrs"]
        notes.append(
            f"retirement: retirement_cagrs isn't numbers separated by commas ({cagr_text!r}); using the default"
        )
    stock = number("retirement_stock_share", allocation_share if allocation_share is not None else 0.7)
    h = Household(
        start=start,
        people=people,
        pre_plan=bal["pre_plan"],
        pre_ira=bal["pre_ira"],
        roth=bal["roth"],
        roth_basis=roth_basis,
        taxable=taxable,
        basis=basis,
        hsa=hsa,
        contributions=contributions,
        hsa_contribution=hsa_in,
        taxable_savings=taxable_savings,
        spending=path,
        hsa_spending=hsa_spending,
        health_pre65=number("retirement_health_pre65", DEFAULTS["health_pre65"]),
        health_supplement=number("retirement_health_supplement", DEFAULTS["health_supplement"]),
        stock_share=min(max(stock, 0.0), 1.0),
        taxable_yield=number("retirement_taxable_yield", DEFAULTS["taxable_yield"]),
        inflation=number("retirement_inflation", DEFAULTS["inflation"]),
        cagrs=cagrs,
        plan_to_age=int(number("retirement_plan_to_age", DEFAULTS["plan_to_age"])),
        success_target=number("retirement_success_target", DEFAULTS["success_target"]),
        monte_carlo_runs=int(number("retirement_monte_carlo_runs", DEFAULTS["monte_carlo_runs"])),
        ss_cut=number("social_security_cut", 0.0),
        ss_cut_from=int(number("social_security_cut_from", TRUSTEES_CUT_YEAR)),
        pending=pending,
        budget={
            "base": base,
            "groups": groups,
            "changes": changes,
            "loans": loans,
            "lumpy": lumpy,
            "months": months,
            "settings": {
                k.partition(":")[2]: str(value(k))
                for k in (
                    set(params.loc[params["jurisdiction"].astype(str) == "household", "key"].astype(str))
                    if len(params)
                    else ()
                )
                if k.startswith("retirement_spending:")
            },
        },
    )

    for a in adults:
        for kind, label in (("pre_plan", "pre-tax in workplace plans"), ("pre_ira", "pre-tax IRAs"), ("roth", "Roth")):
            note("today", f"{kind}:{a}", f"{a}: {label}", round(bal[kind][a], 2), "investment values today")
        note(
            "today",
            f"roth_basis:{a}",
            f"{a}: Roth contributions and conversions (free before 59 1/2)",
            round(roth_basis[a], 2),
            "investment activity (conversions taken as backdoor ones, already taxed)",
        )
    note("today", "taxable", "Taxable accounts", round(taxable, 2), "investment values today")
    note("today", "basis", "Their cost basis", round(basis, 2), "open lots, and cash at its value")
    note("today", "hsa", "HSA", round(hsa, 2), "investment values today")
    for a in adults:
        for kind, label in (("pre_plan", "pre-tax"), ("roth", "Roth")):
            note(
                "saving",
                f"{kind}:{a}",
                f"{a}: {label} contributions a year",
                round(contributions[a][kind], 2),
                "investment activity, last 12 months",
            )
    note("saving", "hsa", "HSA contributions a year", round(hsa_in, 2), "investment activity, last 12 months")
    for a in adults:
        for kind, key in (("pre_plan", "retirement_pre_tax_contributions"), ("roth", "retirement_roth_contributions")):
            assume[[i for i, x in enumerate(assume) if x["key"] == f"{kind}:{a}" and x["section"] == "saving"][0]][
                "setting"
            ] = f"{key}:{a}"
    note(
        "saving",
        "taxable",
        "Taxable savings a year",
        round(taxable_savings, 2),
        "the setting"
        if value("retirement_taxable_savings")
        else f"last 12 months: income {came_in:,.0f} and shares vested {vested:,.0f}, less spending as set for "
        f"{start} {path.get(start, 0.0):,.0f}, {', '.join(leave_out) or 'nothing left out'} {left_out:,.0f}, "
        f"IRAs and 529s {into_ira_529:,.0f}" + (" (below zero: none)" if derived < 0 else ""),
        "retirement_taxable_savings",
    )
    for c in sorted(base, key=lambda c: (groups.get(c, ""), c)):
        note(
            "spending",
            c,
            f"{groups.get(c, '')}: {c}",
            round(base[c], 2),
            (f"actual, {months[0]} to {months[-1]}" if months else "")
            + (f"; {lumpy[c]:.0%} of it in two months, so it may not recur" if c in lumpy else ""),
            f"retirement_spending:{c}",
        )
    note("spending", "total", "Spending now, a year", round(sum(base.values()), 2), "the categories above")
    for r in adjust:
        note(
            "changes",
            r["key"],
            f"{r['key']} ({r['basis']})",
            r["steps"],
            f"now {r['now']:,.0f} a year",
            f"retirement_spending:{r['key']}",
        )
    note(
        "health",
        "pre65",
        "Health cover before 65, a year for the household",
        h.health_pre65,
        "setting or default",
        "retirement_health_pre65",
    )
    note(
        "health",
        "supplement",
        "Medigap and Part D, a year a person from 65",
        h.health_supplement,
        "setting or default",
        "retirement_health_supplement",
    )
    note(
        "health",
        "hsa_spending",
        "Spending the HSA pays (medical)",
        round(hsa_spending, 2),
        "categories or groups named in hsa_spending_categories (default medical)",
        "hsa_spending_categories",
    )
    note(
        "returns",
        "stock_share",
        "Stocks in the portfolio",
        round(h.stock_share, 4),
        "setting" if value("retirement_stock_share") else "the allocation targets",
        "retirement_stock_share",
    )
    note(
        "returns",
        "cagrs",
        "Fixed yearly returns (nominal)",
        ", ".join(f"{c:.1%}" for c in cagrs),
        "setting or default",
        "retirement_cagrs",
    )
    note(
        "returns",
        "inflation",
        "Inflation for the fixed returns",
        h.inflation,
        "setting or default",
        "retirement_inflation",
    )
    mean_r, mean_i = central(h)
    note(
        "returns",
        "history",
        "History's real return for this mix, and inflation, a year (geometric)",
        f"{mean_r:.2%}, {mean_i:.2%}",
        f"markets.py, {markets.FIRST} to {markets.LAST}",
    )
    note(
        "returns",
        "yield",
        "Taxable accounts' dividends",
        h.taxable_yield,
        "setting or default",
        "retirement_taxable_yield",
    )
    note("returns", "runs", "Monte Carlo runs", h.monte_carlo_runs, "setting or default", "retirement_monte_carlo_runs")
    note(
        "plan",
        "to_age",
        "The plan runs until the younger is",
        h.plan_to_age,
        "setting or default",
        "retirement_plan_to_age",
    )
    note(
        "plan",
        "target",
        "Success needed to retire",
        h.success_target,
        "setting or default",
        "retirement_success_target",
    )
    note(
        "plan",
        "ss_cut",
        "Social Security cut assumed",
        f"{h.ss_cut:.0%} from {h.ss_cut_from}",
        "setting or none",
        "social_security_cut",
    )
    for p in people:
        note("law", f"rmd:{p.name}", f"{p.name}: RMDs from age", law.rmd_age(p.born.year), "IRC 401(a)(9), SECURE 2.0")
        note(
            "law",
            f"fra:{p.name}",
            f"{p.name}: full retirement age (months)",
            law.fra_months(p.born.year),
            "Social Security Act 216(l)",
        )
    note("law", "tax_year", "Tax law used (held in today's dollars)", LAW_YEAR, "taxdata.py")
    return h, notes, pd.DataFrame(assume)


def allocation_stock_share(allocation):
    """The stocks' share of the targets (the allocation report's stocks node), or None."""
    if allocation is None or allocation.empty:
        return None
    a = allocation.assign(target_share=_num(allocation["target_share"]))
    row = a[a["node"].astype(str) == "stocks"]
    return float(row["target_share"].iloc[0]) if len(row) and row["target_share"].iloc[0] > 0 else None
