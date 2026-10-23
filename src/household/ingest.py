"""Ingest: turn source records into ledger rows and append only what is new.

Ingest is the only code that writes the ledger. Every row gets a stable
txn_id, so re-running an ingest appends nothing the second time.
"""

import hashlib
from datetime import datetime, timezone

import pandas as pd

from . import schema, sheets

TRANSACTIONS = schema.table(schema.LEDGER, "transactions")


def assign_txn_ids(df):
    """Hash account, date, amount and payee, plus an occurrence index so identical rows stay distinct.

    The occurrence index counts identical rows within one batch (one file). Two
    files that overlap give the same ids for the days they share, so the second
    adds only what the first lacked: two identical coffees on a day in both files
    stay two, and a third file showing three that day adds one. Exports cover
    whole days, which is what makes this exact.
    """
    df = df.copy()
    key = (df["account_id"].astype(str) + "|" + df["date"].astype(str) + "|"
           + df["amount"].map(lambda a: f"{a:.2f}") + "|" + df["payee_raw"].astype(str))
    occurrence = key.groupby(key).cumcount()
    df["txn_id"] = [hashlib.sha256(f"{k}|{n}".encode()).hexdigest()[:16]
                    for k, n in zip(key, occurrence)]
    return df


def legacy_to_transactions(legacy, recorded_at=None):
    """Legacy spend (positive cost) to ledger transactions (outflows negative)."""
    recorded_at = recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    df = pd.DataFrame({
        "date": legacy["date"].map(lambda d: d.isoformat()),
        "account_id": "legacy",
        "amount": -legacy["cost"],
        "payee_raw": legacy["payee_raw"],
        "category": legacy["category"],
        "one_off": "FALSE",
        "note": legacy["note"],
        "source": "legacy",
        "era": "legacy",
        "doc_id": "",
        "recorded_at": recorded_at,
    })
    return assign_txn_ids(df)


def records_to_transactions(records, account_id, source, doc_id="", recorded_at=None):
    """Parsed records (date, amount, payee_raw[, note]) from one file to ledger transactions."""
    recorded_at = recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    df = pd.DataFrame({
        "date": records["date"].astype(str),
        "account_id": account_id,
        "amount": records["amount"].astype(float).round(2),
        "payee_raw": records["payee_raw"].astype(str).str.strip(),
        "category": "",
        "one_off": "FALSE",
        "note": records["note"] if "note" in records else "",
        "source": source,
        "era": "ledger",
        "doc_id": doc_id,
        "recorded_at": recorded_at,
    })
    return assign_txn_ids(df)


def new_rows(incoming, existing_ids):
    return incoming[~incoming["txn_id"].isin(set(existing_ids))]


def append_transactions(ledger, incoming):
    """Append transactions whose txn_id is not already in the ledger. Returns (appended, skipped)."""
    existing = sheets.read_table(ledger, TRANSACTIONS)
    fresh = new_rows(incoming, existing["txn_id"])
    appended = sheets.append_rows(ledger, TRANSACTIONS, fresh)
    return appended, len(incoming) - appended
