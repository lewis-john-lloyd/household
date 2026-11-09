"""Investment accounts' activity (investment_activity), summed for the views and the log.

flows: each account's money in and out each year by kind (contributions, conversions, transfers,
dividends, withdrawals, fees), from the rows whose amount crosses the account's edge. Buying, selling and
exchanging inside an account move no money in or out and are left out.
"""

import pandas as pd

FLOW_COLUMNS = ["year", "account_id", "kind", "amount", "rows"]


def flows(activity):
    """FLOW_COLUMNS, newest year first, then by account and kind."""
    if activity is None or activity.empty:
        return pd.DataFrame(columns=FLOW_COLUMNS)
    df = activity.copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df = df[df["amount"] != 0]
    df["year"] = df["date"].astype(str).str[:4]
    out = df.groupby(["year", "account_id", "kind"], as_index=False).agg(
        amount=("amount", "sum"), rows=("amount", "size")
    )
    out["amount"] = out["amount"].round(2)
    return out.sort_values(["year", "account_id", "kind"], ascending=[False, True, True]).reset_index(drop=True)[
        FLOW_COLUMNS
    ]
