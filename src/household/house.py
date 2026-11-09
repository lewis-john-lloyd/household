"""Homes: what each is worth, what is owed on it, its equity, and its basis for a sale.

properties lists each home with its purchase, loans (loan_ids) and sale. A loan's balance on a date comes from the
amortization (mortgage.schedule, for a loan in loans) or from loan_balances (a loan with recorded balances only).

The value that counts toward net worth follows the home_value_basis setting:
  purchase_price      the price paid, from purchase until sale (the default); a home with no purchase price uses
                      its latest value as below
  median, lowest      of the latest value from each source in the year before the date
  <source>            the latest value from that source (zillow, county_fair_market, legacy, ...)
A home with no purchased date is owned from its first value; with no sold date, until today (or, when it has
only legacy values, through the last of them).

Basis: purchase price, plus basis_costs (closing costs that add to basis), plus each capital improvement's cost,
plus each energy-credit improvement's cost less its credit. Repairs add nothing. What a sale would net is the
estimate less the home_sale_cost_rate share of it and the loans; the gain is that estimate, less sale costs, over
the basis (before any exclusion for a main home).
"""

from datetime import date

import numpy as np
import pandas as pd

from . import settings

SUMMARY_COLUMNS = ["property_id", "order", "key", "label", "value", "as_of", "note"]
VALUE_COLUMNS = ["property_id", "as_of", "source", "value", "note"]
IMPROVEMENT_COLUMNS = ["property_id", "date", "description", "kind", "cost", "credit", "basis_added", "receipt", "note"]
DEFAULT_SALE_COST = 0.06
ESTIMATE_DAYS = 366  # a source's value older than this before the date doesn't count toward median or lowest


def _num(value):
    try:
        text = str(value).replace(",", "").replace("$", "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def _clean(df, columns):
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    out = df.copy()
    for c in out.columns:
        out[c] = out[c].astype(str).str.strip()
    return out


def loan_ids(prop):
    return [x.strip() for x in str(prop.get("loan_ids", "")).split(",") if x.strip()]


def loan_balance(loan_id, day, loans, schedule, balances):
    """What was owed on a loan on day (0 before it began or after it was paid off), or None when nothing says."""
    if loan_id in set(loans["loan_id"]):
        loan = loans[loans["loan_id"] == loan_id].iloc[-1]
        if day < str(loan["originated"])[:10]:
            return 0.0
        rows = schedule[(schedule["loan_id"] == loan_id) & (schedule["month"] <= day[:7])]
        if rows.empty:
            return _num(loan["principal"])
        return float(rows["balance"].iloc[-1])
    mine = balances[(balances["loan_id"] == loan_id) & (balances["as_of"] <= day)]
    if mine.empty:
        return None
    return abs(_num(mine.sort_values("as_of")["balance"].iloc[-1]) or 0.0)


def _latest_by_source(values, property_id, day):
    mine = values[(values["property_id"] == property_id) & (values["as_of"] <= day)]
    mine = mine[pd.to_numeric(mine["value"], errors="coerce").notna()]
    return mine.sort_values("as_of").drop_duplicates("source", keep="last")


def owned(prop, values, day):
    """Whether the home was the household's on day."""
    pid = prop["property_id"]
    mine = values[values["property_id"] == pid]
    start = str(prop.get("purchased", ""))[:10] or (mine["as_of"].min() if len(mine) else "")
    end = str(prop.get("sold", ""))[:10]
    if not end and len(mine) and set(mine["source"]) == {"legacy"} and not _num(prop.get("purchase_price")):
        end = mine["as_of"].max()
        return bool(start) and start <= day <= end
    return bool(start) and start <= day and (not end or day < end)


def value_on(prop, values, day, rule):
    """(value, how) the home counts at on day by the rule, or (None, why not)."""
    price = _num(prop.get("purchase_price"))
    if rule == "purchase_price" and price:
        return price, "purchase price"
    latest = _latest_by_source(values, prop["property_id"], day)
    if latest.empty:
        return (price, "purchase price") if price else (None, "no values")
    if rule in ("median", "lowest"):
        cutoff = (date.fromisoformat(day) - pd.Timedelta(days=ESTIMATE_DAYS)).isoformat()
        recent = latest[latest["as_of"] >= cutoff]
        recent = recent if len(recent) else latest.tail(1)
        amounts = [float(v) for v in recent["value"]]
        pick = float(np.median(amounts)) if rule == "median" else min(amounts)
        return pick, f"{rule} of {', '.join(recent['source'])}"
    if rule not in ("purchase_price",):
        hit = latest[latest["source"] == rule]
        if len(hit):
            return float(hit["value"].iloc[-1]), f"{rule} on {hit['as_of'].iloc[-1]}"
    last = latest.sort_values("as_of").iloc[-1]
    return float(last["value"]), f"{last['source']} on {last['as_of']}"


def equity_on(properties, values, loans, schedule, balances, day, rule):
    """[(property_id, owner, value, owed, equity)] for the homes owned on day."""
    out = []
    for prop in properties.to_dict("records"):
        if not owned(prop, values, day):
            continue
        value, _ = value_on(prop, values, day, rule)
        owed = sum(b for b in (loan_balance(i, day, loans, schedule, balances) for i in loan_ids(prop)) if b)
        if value is None:
            continue
        out.append((prop["property_id"], prop.get("owner", "") or "joint", value, owed, value - owed))
    return out


def improvements(rows):
    """IMPROVEMENT_COLUMNS with what each adds to basis."""
    if rows is None or rows.empty:
        return pd.DataFrame(columns=IMPROVEMENT_COLUMNS)
    out = []
    for r in rows.to_dict("records"):
        cost, credit = _num(r["cost"]) or 0.0, _num(r.get("credit")) or 0.0
        kind = str(r["kind"]).strip()
        added = cost if kind == "capital" else cost - credit if kind == "energy_credit" else 0.0
        out.append({"property_id": r["property_id"], "date": r["date"], "description": r["description"], "kind": kind,
                    "cost": round(cost, 2), "credit": round(credit, 2) if credit else "",
                    "basis_added": round(added, 2), "receipt": r.get("receipt", ""), "note": r.get("note", "")})
    return pd.DataFrame(out, columns=IMPROVEMENT_COLUMNS).sort_values(["property_id", "date"]).reset_index(drop=True)


def summary(properties, values, improved, loans, schedule, balances, params, today):
    """SUMMARY_COLUMNS for each home, and notes."""
    rule = settings.value(params, "home_value_basis", "purchase_price").strip() or "purchase_price"
    rate = settings.number(params, "home_sale_cost_rate", DEFAULT_SALE_COST)
    rows, notes = [], []
    for prop in properties.to_dict("records"):
        pid = prop["property_id"]
        out = []

        def add(key, label, value, as_of="", note=""):
            out.append({"property_id": pid, "order": len(out) + 1, "key": key, "label": label,
                        "value": round(value, 2) if isinstance(value, float) else value, "as_of": as_of, "note": note})
        price, costs = _num(prop.get("purchase_price")) or 0.0, _num(prop.get("basis_costs")) or 0.0
        mine = improved[improved["property_id"] == pid] if len(improved) else improved
        capital = float(mine.loc[mine["kind"] == "capital", "basis_added"].sum()) if len(mine) else 0.0
        energy = float(mine.loc[mine["kind"] == "energy_credit", "basis_added"].sum()) if len(mine) else 0.0
        repairs = float(mine.loc[mine["kind"] == "repair", "cost"].sum()) if len(mine) else 0.0
        basis = price + costs + capital + energy
        sold = str(prop.get("sold", ""))[:10]
        on = sold if sold and sold < today else today
        add("status", "Status", "owned" if owned(prop, values, today) else "past", on)
        add("purchase_price", "Purchase price", price, str(prop.get("purchased", ""))[:10])
        add("basis_costs", "Closing costs in basis", costs)
        count = int((mine["kind"] == "capital").sum()) if len(mine) else 0
        add("capital_improvements", "Capital improvements", capital,
            note=f"{count} item{'s' if count != 1 else ''}" if count else "")
        add("energy_improvements", "Energy improvements, less credits", energy)
        add("repairs", "Repairs (not in basis)", repairs)
        add("basis", "Adjusted basis", basis)
        latest = _latest_by_source(values, pid, on)
        for r in latest.sort_values("source").to_dict("records"):
            add(f"estimate:{r['source']}", f"Estimate: {r['source'].replace('_', ' ')}", float(r["value"]), r["as_of"],
                r.get("note", ""))
        estimates = [float(v) for v in latest.loc[latest["source"] != "legacy", "value"]] or \
                    [float(v) for v in latest["value"]]
        middle = float(np.median(estimates)) if estimates else None
        if middle is not None:
            add("estimate_median", "Median estimate", middle, on)
        owed = 0.0
        for loan in loan_ids(prop):
            b = loan_balance(loan, on, loans, schedule, balances)
            if b is None:
                notes.append(f"house: {pid}: no balance known for loan {loan}")
                continue
            owed += b
            add(f"loan:{loan}", f"Owed on {loan}", b, on)
        value, how = value_on(prop, values, on, rule)
        if value is not None:
            add("value_used", "Value counted in net worth", value, on, how)
            add("equity", "Equity (value counted, less loans)", value - owed, on)
        if middle is not None:
            add("equity_at_estimate", "Equity at the median estimate", middle - owed, on)
            net = middle * (1 - rate) - owed
            add("sale_net", "A sale at the median estimate would net", net, on,
                f"after {rate:.0%} sale costs and the loans")
            add("sale_gain", "Gain over basis at the median estimate", middle * (1 - rate) - basis, on,
                "before any main-home exclusion")
        rows += out
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS), notes


def values_report(values):
    if values is None or values.empty:
        return pd.DataFrame(columns=VALUE_COLUMNS)
    out = values[VALUE_COLUMNS].copy()
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out.dropna(subset=["value"]).sort_values(["property_id", "as_of", "source"]).reset_index(drop=True)


def frames(ledger_tables):
    """The house tables cleaned for the functions above: properties, property_values, improvements, loans,
    loan_balances."""
    t = ledger_tables
    props = _clean(t.get("properties"), ["property_id"])
    vals = _clean(t.get("property_values"), ["as_of", "property_id", "source", "value", "note", "doc_id"])
    if len(vals):
        vals["as_of"] = vals["as_of"].str[:10]
    loans = _clean(t.get("loans"), ["loan_id", "originated", "principal"])
    bal = _clean(t.get("loan_balances"), ["as_of", "loan_id", "balance", "doc_id"])
    if len(bal):
        bal["as_of"] = bal["as_of"].str[:10]
    return props, vals, _clean(t.get("improvements"), ["date", "property_id", "kind", "cost"]), loans, bal


TAX_COLUMNS = ["property_id", "tax_year", "assessed", "mill_rate", "total_taxes", "credits", "specials", "amount_due",
               "basis"]


def taxes(properties, values, bills, today):
    """TAX_COLUMNS for each home: each tax year billed (property_taxes), then each year the county has assessed
    but not yet billed, estimated at the latest bill's mill rate, credits and specials. Property taxes are
    billed in December for the year's January assessment, so the year's estimate stands until then."""
    rows = []
    if properties is None or properties.empty:
        return pd.DataFrame(columns=TAX_COLUMNS)
    b = bills.copy() if bills is not None and len(bills) else pd.DataFrame(columns=["tax_year", "property_id"])
    for prop in properties.to_dict("records"):
        pid = prop["property_id"]
        mine = b[b["property_id"].astype(str).str.strip() == pid].copy()
        mine["tax_year"] = mine["tax_year"].astype(str).str[:4]
        mine = mine.sort_values("tax_year").drop_duplicates("tax_year", keep="last")
        for r in mine.to_dict("records"):
            credits = sum(_num(r.get(k)) or 0.0 for k in ("lottery_credit", "first_dollar_credit"))
            rows.append({"property_id": pid, "tax_year": r["tax_year"], "assessed": _num(r.get("assessed")),
                         "mill_rate": _num(r.get("mill_rate")), "total_taxes": _num(r.get("total_taxes")),
                         "credits": round(credits, 2), "specials": _num(r.get("specials")) or 0.0,
                         "amount_due": _num(r.get("amount_due")), "basis": "billed"})
        if mine.empty or not owned(prop, values, today):
            continue
        last = rows[-1]
        if not last["mill_rate"]:
            continue
        assessed = values[(values["property_id"] == pid) & (values["source"] == "county_assessed")].copy()
        assessed["year"] = assessed["as_of"].str[:4]
        for year, g in assessed.sort_values("as_of").groupby("year"):
            if year <= last["tax_year"]:
                continue
            value = float(g["value"].iloc[-1])
            total = round(value * last["mill_rate"], 2)
            rows.append({"property_id": pid, "tax_year": year, "assessed": value, "mill_rate": last["mill_rate"],
                         "total_taxes": total, "credits": last["credits"], "specials": last["specials"],
                         "amount_due": round(total - last["credits"] + last["specials"], 2), "basis": "estimated"})
    return pd.DataFrame(rows, columns=TAX_COLUMNS)


def parcels(properties, values, today):
    """[{property_id, parcel, since}] for the homes owned today that have a parcel number: what the county
    check (assessor/Code.js) looks up, from the year each was bought."""
    out = []
    if properties is None or properties.empty:
        return out
    for prop in properties.to_dict("records"):
        parcel = str(prop.get("parcel", "")).strip()
        if parcel and owned(prop, values, today):
            out.append({"property_id": prop["property_id"], "parcel": parcel,
                        "since": str(prop.get("purchased", ""))[:4] or today[:4]})
    return out
