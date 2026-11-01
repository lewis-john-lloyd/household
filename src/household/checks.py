"""The monthly checklist: what is due to download, and whether balances agree with the ledger.

coverage       one row per bank or card account that sends monthly statements
               (accounts.expected_docs = monthly_statement, not closed, of type
               checking, savings or credit_card): the newest date the ledger has
               for it, from a transaction, a balance or a download's covers_to,
               and "due" once that is more than max_gap_days old (45 when blank).
               An account whose expected_docs is when_used (open but unused)
               is listed and never due.
               Then one row per explanation download (PayPal, Amazon, Venmo,
               Apple, Google): the last date its exports cover and how many
               lines are waiting for the next one; "due" when any are.
balance_check  one row per stored balance (an OFX download's ledger balance,
               or one typed in by hand): the balance less the sum of the
               account's transactions up to that date is the balance the
               ledger implies before its first transaction. Every snapshot of
               an account must imply the same opening; when one disagrees,
               transactions between them are missing or doubled.
paychecks      each pay stub's net pay against the pay deposit it should be
               (category pay, naming the stub's employer, same amount, posted
               PAY_WINDOW days around the pay date), one to one. A deposit
               with no stub is "missing stub: did you forget it?"; a stub with
               no deposit is "no deposit". Stubs are checked from the first
               days the ledger has the account their deposits land in, and
               deposits from the employer's first stub, so history from
               before either began is not flagged.
"""

from datetime import date

import pandas as pd

SPENDING_TYPES = ("checking", "savings", "credit_card")
DEFAULT_GAP = 45
WHEN_USED = "when_used"  # an open card kept unused: listed, never due
SERVICES = ("paypal", "amazon", "venmo", "apple", "google")
TOLERANCE = 0.005
PAY_WINDOW = (-3, 5)  # days from a stub's pay date to its deposit's posting date
PAYCHECK_COLUMNS = ["date", "person", "employer", "amount", "txn_id", "account_id", "payee_raw", "status"]


def _gap(value):
    text = str(value).strip()
    return int(float(text)) if text else DEFAULT_GAP


def coverage(accounts, transactions, balances, documents, categorized, explained_coverage, today):
    today = pd.Timestamp(today or date.today())
    rows = []
    live = accounts[accounts["expected_docs"].isin(["monthly_statement", WHEN_USED])
                    & accounts["type"].isin(SPENDING_TYPES)
                    & (accounts["closed"].astype(str).str.strip() == "")]
    for a in live.to_dict("records"):
        acct = a["account_id"]
        dates = [d for d in (
            transactions.loc[transactions["account_id"] == acct, "date"].astype(str).str[:10].max(),
            balances.loc[balances["account_id"] == acct, "as_of"].astype(str).str[:10].max(),
            documents.loc[documents["account_id"] == acct, "covers_to"].astype(str).str[:10].max(),
        ) if isinstance(d, str) and d]
        newest = max(dates) if dates else ""
        gap = _gap(a.get("max_gap_days", ""))
        age = (today - pd.Timestamp(newest)).days if newest else None
        status = "never downloaded" if not newest else "due" if age > gap else "ok"
        if a["expected_docs"] == WHEN_USED and status != "ok":
            status = "download when used"
        rows.append({"source": acct, "kind": "account", "newest": newest, "age_days": "" if age is None else age,
                     "due_after_days": gap, "waiting_lines": "", "status": status})
    waiting = categorized[categorized["status"] == "waiting"]["decided_by"].value_counts()
    for service in SERVICES:
        span = explained_coverage.get(service)
        count = int(waiting.get(f"waiting for {service}", 0))
        newest = span[1] if span else ""
        status = ("due" if count else "ok") if span else ("never downloaded" if count else "not used")
        rows.append({"source": service, "kind": "explanation", "newest": newest,
                     "age_days": (today - pd.Timestamp(newest)).days if newest else "",
                     "due_after_days": "", "waiting_lines": count, "status": status})
    return pd.DataFrame(rows, columns=["source", "kind", "newest", "age_days", "due_after_days", "waiting_lines",
                                       "status"])


def balance_check(balances, transactions):
    """Each stored balance against the ledger's transactions for its account."""
    out = []
    txns = transactions[transactions["era"] != "legacy"].copy()
    txns["amount"] = pd.to_numeric(txns["amount"])
    txns["date"] = txns["date"].astype(str).str[:10]
    snaps = balances.copy()
    snaps["as_of"] = snaps["as_of"].astype(str).str[:10]
    snaps["balance"] = pd.to_numeric(snaps["balance"])
    for acct, g in snaps.sort_values(["account_id", "as_of"]).groupby("account_id"):
        mine = txns[txns["account_id"] == acct]
        first = None
        for s in g.to_dict("records"):
            through = round(float(mine.loc[mine["date"] <= s["as_of"], "amount"].sum()), 2)
            opening = round(s["balance"] - through, 2)
            if first is None:
                first, status = opening, "baseline"
            else:
                status = "ok" if abs(opening - first) < TOLERANCE else f"off by {opening - first:+.2f}"
            out.append({"account_id": acct, "as_of": s["as_of"], "balance": s["balance"],
                        "ledger_through": through, "implied_opening": opening,
                        "first_transaction": mine["date"].min() if len(mine) else "", "status": status})
    return pd.DataFrame(out, columns=["account_id", "as_of", "balance", "ledger_through", "implied_opening",
                                      "first_transaction", "status"])


def net_pays(paystubs):
    """One row per stub with net pay: pay_date, person, employer, stub_id, net. The stub's own Net Pay line
    when it has one, else gross less taxes and deductions."""
    cols = ["pay_date", "person", "employer", "stub_id", "net"]
    if paystubs is None or paystubs.empty:
        return pd.DataFrame(columns=cols)
    s = paystubs.copy()
    s["amount"] = pd.to_numeric(s["amount"], errors="coerce").fillna(0.0)
    s["pay_date"] = s["pay_date"].astype(str).str[:10]
    sign = s["kind"].map({"income": 1.0, "tax": -1.0, "pre_tax": -1.0, "post_tax": -1.0}).fillna(0.0)
    s["computed"] = s["amount"] * sign
    s["stated"] = s["amount"].where((s["kind"] == "memo") & (s["line_item"].str.strip().str.lower() == "net pay"))
    keys = ["pay_date", "person", "employer", "stub_id"]
    out = s.groupby(keys, as_index=False).agg(computed=("computed", "sum"), stated=("stated", "max"))
    out["net"] = out["stated"].fillna(out["computed"]).round(2)
    return out[out["net"] > 0][cols].reset_index(drop=True)


def paychecks(paystubs, categorized):
    """Stubs and pay deposits that don't pair up: a missing stub, or pay that never arrived."""
    from .flows import employer_word

    stubs = net_pays(paystubs)
    if stubs.empty or categorized is None or categorized.empty:
        return pd.DataFrame(columns=PAYCHECK_COLUMNS)
    txns = categorized[categorized["era"] != "legacy"].copy()
    txns["date"] = txns["date"].astype(str).str[:10]
    txns["amount"] = pd.to_numeric(txns["amount"], errors="coerce").round(2)
    first_day = txns.groupby("account_id")["date"].min().to_dict()
    pay = txns[(txns["category"].astype(str).str.strip() == "pay") & (txns["amount"] > 0)]
    upper = pay["payee_raw"].astype(str).str.upper()
    stubs["word"] = stubs["employer"].map(employer_word)
    out = []
    for (word, employer), g in stubs[stubs["word"] != ""].groupby(["word", "employer"]):
        person = g["person"].iloc[0]
        deposits = pay[upper.str.contains(rf"\b{word}\b", regex=True)]
        deposits = deposits[deposits["date"] >= g["pay_date"].min()]
        starts = [first_day[a] for a in set(deposits["account_id"]) if a in first_day]
        # a deposit can post before its pay date, so a stub paid in the account's first days may predate it
        start = (pd.Timestamp(min(starts)) - pd.Timedelta(days=PAY_WINDOW[0])).strftime("%Y-%m-%d") if starts else ""
        checked = g[g["pay_date"] >= start] if starts else g.iloc[0:0]
        pairs = g.merge(deposits, how="cross")
        pairs = pairs[(pairs["net"] - pairs["amount"]).abs() < TOLERANCE]
        gap = (pd.to_datetime(pairs["date"]) - pd.to_datetime(pairs["pay_date"])).dt.days
        pairs = pairs[gap.between(*PAY_WINDOW)].assign(_gap=gap.abs()).sort_values(["_gap", "pay_date"])
        used_stubs, used_txns = set(), set()
        for r in pairs.to_dict("records"):
            if r["stub_id"] not in used_stubs and r["txn_id"] not in used_txns:
                used_stubs.add(r["stub_id"])
                used_txns.add(r["txn_id"])
        for r in checked[~checked["stub_id"].isin(used_stubs)].to_dict("records"):
            out.append({"date": r["pay_date"], "person": person, "employer": employer, "amount": r["net"],
                        "txn_id": "", "account_id": "", "payee_raw": "", "status": "no deposit"})
        for r in deposits[~deposits["txn_id"].isin(used_txns)].to_dict("records"):
            out.append({"date": r["date"], "person": person, "employer": employer, "amount": r["amount"],
                        "txn_id": r["txn_id"], "account_id": r["account_id"], "payee_raw": r["payee_raw"],
                        "status": "missing stub: did you forget it?"})
    return pd.DataFrame(out, columns=PAYCHECK_COLUMNS).sort_values(["date", "person"]).reset_index(drop=True)
