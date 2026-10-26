"""Compute: derive tables from ledger facts. Pure functions; writing happens in the engine."""

import re

import pandas as pd

DUPLICATE_WINDOW_DAYS = 3


def effective_transactions(transactions, corrections=None):
    """Apply superseding corrections (latest recorded_at per txn_id and field wins).

    A `void` correction with value TRUE removes the transaction, which is how a
    confirmed duplicate leaves the books without deleting a ledger row; a later
    `void` FALSE restores it.
    """
    df = transactions.copy()
    if corrections is not None and not corrections.empty:
        latest = (corrections.sort_values("recorded_at")
                  .groupby(["txn_id", "field"], as_index=False).last())
        for field in ("category", "one_off", "note"):
            patch = latest[latest["field"] == field].set_index("txn_id")["value"]
            mask = df["txn_id"].isin(patch.index)
            df.loc[mask, field] = df.loc[mask, "txn_id"].map(patch)
        void = latest[(latest["field"] == "void")
                      & (latest["value"].astype(str).str.upper() == "TRUE")]["txn_id"]
        df = df[~df["txn_id"].isin(set(void))]
    return df.reset_index(drop=True)


TRANSFER = "transfer"


def mark_transfers(transactions, ids):
    """Give paired transfer lines the category "transfer", unless a person already categorized them."""
    df = transactions.copy()
    hit = df["txn_id"].isin(ids) & (df["category"].astype(str).str.strip() == "")
    df.loc[hit, "category"] = TRANSFER
    return df


def apply_aliases(transactions, aliases):
    """Rename old categories to the categories they now count as (category_aliases)."""
    df = transactions.copy()
    if aliases is None or aliases.empty:
        return df
    names = dict(zip(aliases["alias"].astype(str).str.strip(), aliases["category"].astype(str).str.strip()))
    df["category"] = df["category"].map(lambda c: names.get(str(c).strip(), c))
    return df


def _payee_key(payee):
    words = re.sub(r"[^A-Z ]", " ", str(payee).upper()).split()
    return words[0] if words else ""


def possible_duplicates(transactions, dismissed=(), window_days=DUPLICATE_WINDOW_DAYS):
    """Pairs that look like one charge recorded twice: same account and amount, dates within
    a few days, payees starting with the same word, from different source files.

    Rows from one file are never paired (a file listing two identical coffees means
    two coffees), and exact repeats across files never reach the ledger, so what
    remains is mostly a pending charge and its posted version with a shifted date or
    reworded payee. `dismissed` holds "txn_a|txn_b" keys already judged distinct.
    """
    cols = ["account_id", "amount", "txn_a", "date_a", "payee_a", "doc_a",
            "txn_b", "date_b", "payee_b", "doc_b"]
    if transactions.empty:
        return pd.DataFrame(columns=cols)
    df = transactions.copy()
    df["amount"] = pd.to_numeric(df["amount"]).round(2)
    df["day"] = pd.to_datetime(df["date"].astype(str).str[:10])
    df["doc"] = df["doc_id"].astype(str).replace("", "(none)")
    df["payee_key"] = df["payee_raw"].map(_payee_key)
    pairs = df.merge(df, on=["account_id", "amount", "payee_key"], suffixes=("_a", "_b"))
    pairs = pairs[(pairs["txn_id_a"] < pairs["txn_id_b"]) & (pairs["doc_a"] != pairs["doc_b"])
                  & ((pairs["day_a"] - pairs["day_b"]).abs().dt.days <= window_days)]
    keys = pairs["txn_id_a"] + "|" + pairs["txn_id_b"]
    pairs = pairs[~keys.isin(set(dismissed))]
    out = pairs.rename(columns={"txn_id_a": "txn_a", "txn_id_b": "txn_b",
                                "payee_raw_a": "payee_a", "payee_raw_b": "payee_b"})
    return out[cols].sort_values(["account_id", "date_a"]).reset_index(drop=True)


def monthly_actuals(transactions):
    """Spend per category per month. Spend is positive for outflows."""
    df = transactions.copy()
    df["amount"] = pd.to_numeric(df["amount"])
    df["month"] = df["date"].astype(str).str[:7]
    out = (df.groupby(["month", "category"], as_index=False)
             .agg(spend=("amount", lambda s: round(-s.sum(), 2)), txn_count=("amount", "size")))
    return out.sort_values(["month", "category"]).reset_index(drop=True)


TOTAL = "TOTAL"


def legacy_rows(transactions):
    """The ledger rows the legacy check compares, and the legacy rows it leaves out.

    The check asks whether ingest copied the old books faithfully, so it reads
    legacy rows as they were ingested: no corrections, no aliases, and nothing
    from a bank download (downloads reach past where the old books stopped).
    A legacy row the old books never categorized is in no category row of the
    old rollup, so it is in none of its totals either; it is left out and
    returned separately, to be categorized in review like any other.
    """
    legacy = transactions[transactions["era"] == "legacy"]
    blank = legacy["category"].astype(str).str.strip() == ""
    return legacy[~blank], legacy[blank]


def compare_to_legacy(computed, legacy_rollup, tolerance=0.005):
    """Join computed monthly spend to the legacy rollup; returns rows with legacy, computed, diff.

    Monthly totals (all transactions, uncategorized included) are compared as category TOTAL.
    """
    totals = (computed.groupby("month", as_index=False)["spend"].sum()
              .assign(category=TOTAL, spend=lambda d: d["spend"].round(2)))
    left = legacy_rollup.rename(columns={"spend": "legacy"})
    right = (pd.concat([computed[["month", "category", "spend"]], totals[["month", "category", "spend"]]])
             .rename(columns={"spend": "computed"}))
    joined = left.merge(right, on=["month", "category"], how="outer").fillna(0.0)
    joined["diff"] = (joined["computed"] - joined["legacy"]).round(2)
    joined["match"] = joined["diff"].abs() < tolerance
    return joined.sort_values(["month", "category"]).reset_index(drop=True)
