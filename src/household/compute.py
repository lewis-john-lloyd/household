"""Compute: derive tables from ledger facts. Pure functions; writing happens in the engine."""

import pandas as pd


def effective_transactions(transactions, corrections=None):
    """Apply superseding corrections (latest recorded_at per txn_id and field wins)."""
    df = transactions.copy()
    if corrections is not None and not corrections.empty:
        latest = (corrections.sort_values("recorded_at")
                  .groupby(["txn_id", "field"], as_index=False).last())
        for field in ("category", "one_off", "note"):
            patch = latest[latest["field"] == field].set_index("txn_id")["value"]
            mask = df["txn_id"].isin(patch.index)
            df.loc[mask, field] = df.loc[mask, "txn_id"].map(patch)
    return df


def monthly_actuals(transactions):
    """Spend per category per month. Spend is positive for outflows."""
    df = transactions.copy()
    df["amount"] = pd.to_numeric(df["amount"])
    df["month"] = df["date"].astype(str).str[:7]
    out = (df.groupby(["month", "category"], as_index=False)
             .agg(spend=("amount", lambda s: round(-s.sum(), 2)), txn_count=("amount", "size")))
    return out.sort_values(["month", "category"]).reset_index(drop=True)


TOTAL = "TOTAL"


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
