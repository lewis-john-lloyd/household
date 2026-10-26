"""The monthly checklist: what is due to download, and whether balances agree with the ledger.

coverage       one row per bank or card account that sends monthly statements
               (accounts.expected_docs = monthly_statement, not closed, of type
               checking, savings or credit_card): the newest date the ledger has
               for it, from a transaction, a balance or a download's covers_to,
               and "due" once that is more than max_gap_days old (45 when blank).
               Then one row per explanation download (PayPal, Amazon, Venmo,
               Apple, Google): the last date its exports cover and how many
               lines are waiting for the next one; "due" when any are.
balance_check  one row per stored balance (an OFX download's ledger balance,
               or one typed in by hand): the balance less the sum of the
               account's transactions up to that date is the balance the
               ledger implies before its first transaction. Every snapshot of
               an account must imply the same opening; when one disagrees,
               transactions between them are missing or doubled.
"""

from datetime import date

import pandas as pd

SPENDING_TYPES = ("checking", "savings", "credit_card")
DEFAULT_GAP = 45
SERVICES = ("paypal", "amazon", "venmo", "apple", "google")
TOLERANCE = 0.005


def _gap(value):
    text = str(value).strip()
    return int(float(text)) if text else DEFAULT_GAP


def coverage(accounts, transactions, balances, documents, categorized, explained_coverage, today):
    today = pd.Timestamp(today or date.today())
    rows = []
    live = accounts[(accounts["expected_docs"] == "monthly_statement")
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
