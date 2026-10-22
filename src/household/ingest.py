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
    """Hash account, date, amount and payee, plus an occurrence index so identical rows stay distinct."""
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


def new_rows(incoming, existing_ids):
    return incoming[~incoming["txn_id"].isin(set(existing_ids))]


def append_transactions(ledger, incoming):
    """Append transactions whose txn_id is not already in the ledger. Returns (appended, skipped)."""
    existing = sheets.read_table(ledger, TRANSACTIONS)
    fresh = new_rows(incoming, existing["txn_id"])
    appended = sheets.append_rows(ledger, TRANSACTIONS, fresh)
    return appended, len(incoming) - appended
