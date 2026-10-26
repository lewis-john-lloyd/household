"""Transfers between the household's own accounts.

Money moved from one account to another, a card payment included, shows up
twice: out of one account and into another, for the same amount, a few days
apart. Neither line is spending. This module pairs them.

Two lines pair when they are in different household accounts, their amounts
cancel exactly, they posted within WINDOW_DAYS of each other, and both read
like a transfer or payment (TRANSFER_WORDS). Candidates are taken closest in
date first, and each line pairs at most once, so a run of identical amounts
(a 500 every month) pairs month by month.

A payment that bounces shows up as the payment and its return in the same
account; those pair too, as a reversal.

A line that reads like a move between the household's own accounts but finds
no partner is reported: usually the other side lies
outside the downloaded dates, sometimes a download is missing. Payments to
people and services (NOT_OWN: Venmo, PayPal, Zelle, a mortgage, taxes) are not
reported; categorization handles them.
"""

import re

import pandas as pd

WINDOW_DAYS = 6
TRANSFER_WORDS = re.compile(
    r"TRANSFER|TRNSFER|TRNSFR|\bTFR\b|XFER|PAYMENT THANK YOU|PAYMENT - THANK YOU|PYMT|\bPMT\b|EPAY|"
    r"ELECTRONIC PAYMENT|CREDIT CARD PAYMENT|WITHDRAWAL TO|WITHDRAWAL FROM|DEPOSIT FROM")
NOT_OWN = re.compile(r"PAYPAL|VENMO|ZELLE|CASH APP|WIRE|LOAN|MTG|MORTGAGE|\bIRS\b|TAX")
REVERSAL = re.compile(r"RETURN|REVERS")
COLUMNS = ["txn_a", "date_a", "account_a", "payee_a", "txn_b", "date_b", "account_b", "payee_b",
           "amount", "kind"]


def _prepare(transactions, accounts):
    df = transactions.copy()
    df = df[df["account_id"].isin(set(accounts["account_id"]))]
    df["amount"] = pd.to_numeric(df["amount"]).round(2)
    df["day"] = pd.to_datetime(df["date"].astype(str).str[:10])
    df["text"] = df["payee_raw"].astype(str).str.upper()
    df["transferish"] = df["text"].str.contains(TRANSFER_WORDS)
    kinds = dict(zip(accounts["account_id"], accounts["type"]))
    df["card"] = df["account_id"].map(kinds).eq("credit_card")
    return df


def pairs(transactions, accounts):
    """Matched transfer pairs: one row per pair, the outflow as side a."""
    df = _prepare(transactions, accounts)
    df = df[(df["transferish"] | df["text"].str.contains(REVERSAL)) & (df["amount"] != 0)]
    out, inn = df[df["amount"] < 0], df[df["amount"] > 0]
    if out.empty or inn.empty:
        return pd.DataFrame(columns=COLUMNS)
    cand = out.assign(key=-out["amount"]).merge(inn.assign(key=inn["amount"]), on="key", suffixes=("_a", "_b"))
    # different accounts, or one account where the second line returns the first (a bounced payment)
    same = cand["account_id_a"] == cand["account_id_b"]
    returned = cand["text_a"].str.contains(REVERSAL) | cand["text_b"].str.contains(REVERSAL)
    cand = cand[~same | returned]
    cand["gap"] = (cand["day_b"] - cand["day_a"]).dt.days.abs()
    cand = cand[cand["gap"] <= WINDOW_DAYS].sort_values(["gap", "day_a", "txn_id_a", "txn_id_b"])
    used, rows = set(), []
    for c in cand.itertuples(index=False):
        if c.txn_id_a in used or c.txn_id_b in used:
            continue
        used.update((c.txn_id_a, c.txn_id_b))
        rows.append({"txn_a": c.txn_id_a, "date_a": str(c.date_a), "account_a": c.account_id_a,
                     "payee_a": c.payee_raw_a, "txn_b": c.txn_id_b, "date_b": str(c.date_b),
                     "account_b": c.account_id_b, "payee_b": c.payee_raw_b, "amount": c.key,
                     "kind": ("reversal" if c.account_id_a == c.account_id_b
                              else "card_payment" if c.card_a or c.card_b else "transfer")})
    return pd.DataFrame(rows, columns=COLUMNS).sort_values(["date_a", "txn_a"]).reset_index(drop=True)


def unmatched(transactions, accounts, paired):
    """Lines that read like a move between own accounts but found no partner."""
    df = _prepare(transactions, accounts)
    done = set(paired["txn_a"]) | set(paired["txn_b"])
    left = df[df["transferish"] & ~df["text"].str.contains(NOT_OWN) & ~df["txn_id"].isin(done)]
    return left[["txn_id", "date", "account_id", "amount", "payee_raw"]].sort_values(
        ["date", "account_id"]).reset_index(drop=True)


def paired_ids(paired):
    return set(paired["txn_a"]) | set(paired["txn_b"])
