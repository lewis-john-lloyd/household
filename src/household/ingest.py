"""Ingest: turn source records into ledger rows and append only what is new.

Ingest is the only code that writes the ledger. Every row gets a stable
txn_id, so re-running an ingest appends nothing the second time.
"""

import hashlib
import re
from datetime import datetime, timezone

import pandas as pd

from . import schema, sheets
from .parsers import ParseError

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


# --- bank downloads -----------------------------------------------------------------------------

def _last4(value):
    v = re.sub(r"\.0+$", "", str(value).strip())  # a Sheet may hand back 713.0 for "0713"
    return (v.zfill(4) if v.isdigit() else v)[-4:]  # Amex shows five digits; four decide


def _named(word, file_name):
    return bool(word) and re.search(rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])",
                                    file_name.lower()) is not None


def resolve_account(hint, file_name, accounts):
    """The accounts row a bank download belongs to.

    First the last four of the account number in the file (hint) against the
    last4 column; failing that, an account_id or last4 that appears in the file
    name as a word of its own ("expenses_usaa.csv", "card_1234.qfx"). Exactly one
    account must match, or the file stays in the inbox with the reason.
    """
    rows = accounts.to_dict("records")
    by_hint = [a for a in rows if hint and _last4(a.get("last4", "")) == _last4(hint)]
    by_name = [a for a in rows if _named(str(a["account_id"]), file_name)
               or _named(_last4(a.get("last4", "")), file_name)]
    if len(by_hint) == 1:
        return by_hint[0]
    if len(by_hint) > 1:
        both = [a for a in by_hint if a in by_name]
        if len(both) == 1:
            return both[0]
        ids = ", ".join(a["account_id"] for a in by_hint)
        raise ParseError(f"accounts {ids} all end {hint}: put the account_id in the file name")
    # "usaa_card.csv" names usaa_card, not also card: a shorter id inside a longer match doesn't count
    by_name = [a for a in by_name if not any(str(a["account_id"]) in str(b["account_id"])
                                             and a is not b for b in by_name)]
    if len(by_name) == 1:
        return by_name[0]
    if by_name:
        ids = ", ".join(a["account_id"] for a in by_name)
        raise ParseError(f"file name matches several accounts ({ids}): rename it")
    where = f"account ..{hint}" if hint else "this file"
    raise ParseError(f"no account for {where}: put {hint} in that account's last4 column"
                     if hint else f"no account for {where}: rename it to include its account_id")


def finish_bank_rows(result, file_name, accounts, recorded_at):
    """Fill in a bank download's account and txn_ids (in place on result).

    The bank's own id (FITID, reference number) becomes the txn_id hashed with the account; a file without ids gets
    assign_txn_ids. A card download that shows charges as positive is flipped.
    """
    account = resolve_account(result.account_hint, file_name, accounts)
    account_id = str(account["account_id"])
    for name, rows in result.tables.items():
        if "account_id" in rows:
            rows = rows.copy()
            rows["account_id"] = account_id
            result.tables[name] = rows
    txns = result.tables.get("transactions")
    if txns is not None and not txns.empty:
        if result.card_charges_positive and account.get("type") == "credit_card":
            txns["amount"] = -txns["amount"]
            result.notes.append("card amounts flipped: charges are money out")
        txns["recorded_at"] = recorded_at
        bank_id = txns["txn_id"].astype(str).str.match(r"(fitid|ref):")
        if bank_id.all():  # the bank's own id (OFX FITID, a reference number), with the account
            txns["txn_id"] = [hashlib.sha256(f"{account_id}|{t.split(':', 1)[1]}".encode()).hexdigest()[:16]
                              for t in txns["txn_id"]]
        else:
            txns = assign_txn_ids(txns)
        result.tables["transactions"] = txns
    result.account_id = account_id
    result.label = f"{account_id}_{result.covers_to or 'undated'}"
    return result
