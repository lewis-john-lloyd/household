"""Giving by recipient: what each one was given each month, and whether gifts to it are deductible.

A gift is spending in a category of the giving group (GROUP). Its recipient is the line's merchant key, the
key review asks under, shown by the name given it in the Recurring editor (recurring.names) or else by the
payee text seen most. Whether gifts to a recipient count toward the itemized deduction is a "recipient" edit
(kept in proposal_decisions, kind deductible): yes, no, or not said yet. The Budget page sums a period's
gifts by recipient under the giving line items, and the year's deductible gifts.

The budget itself stays with the budget lines: a giving line's amount is the year's estimate, and the
recipients under it show where it went.
"""

import pandas as pd

from . import categorize, recurring
from . import decisions as decisions_

GROUP = "giving"
KIND = "deductible"
PREFIX = KIND + ":"  # proposal_decisions keys: deductible:<merchant key>
COLUMNS = ["month", "key", "name", "category", "given", "gifts", "deductible"]


def deductible(decisions):
    """{key: "yes" or "no"} from proposal_decisions: the latest "deductible" answer per key (approve is yes)."""
    return {k: "yes" if approved else "no" for k, (approved, _) in decisions_.latest(decisions, KIND).items()}


def _key(row):
    key = str(row.get("key", "") or "").strip()
    if key:
        return key
    text = row.get("payee_text", "") or row.get("payee_raw", "")
    return categorize.payee_key(text) or str(text).strip().upper()


def by_recipient(categorized, categories, decisions):
    """Each month's gifts per recipient and category: what was given (positive), how many gifts, and
    whether the recipient is deductible ("yes", "no" or "" for not said)."""
    if categorized is None or categorized.empty or categories is None or categories.empty:
        return pd.DataFrame(columns=COLUMNS)
    giving = set(
        categories.loc[categories["group"].astype(str).str.strip() == GROUP, "category"].astype(str).str.strip()
    )
    rows = categorize.actual_rows(categorized)
    rows = rows[rows["category"].astype(str).str.strip().isin(giving)].copy()
    if rows.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows["amount"] = pd.to_numeric(rows["amount"], errors="coerce").fillna(0.0)
    rows["month"] = rows["date"].astype(str).str[:7]
    rows["key"] = [_key(r) for r in rows.to_dict("records")]
    text = rows["payee_text"] if "payee_text" in rows else rows["payee_raw"]
    rows["text"] = text.astype(str).str.strip()
    seen = rows[rows["text"] != ""].groupby("key")["text"].agg(lambda s: s.value_counts().index[0]).to_dict()
    given = recurring.names(decisions)
    said = deductible(decisions)
    out = rows.groupby(["month", "key", "category"], as_index=False).agg(
        given=("amount", lambda s: round(-s.sum(), 2)), gifts=("amount", lambda s: int((s < 0).sum()))
    )
    out = out[out["given"] != 0]
    out["name"] = [given.get(k) or seen.get(k, k) or "No payee" for k in out["key"]]
    out["deductible"] = [said.get(k, "") for k in out["key"]]
    return out[COLUMNS].sort_values(["month", "given"], ascending=[True, False]).reset_index(drop=True)


def deductible_total(gifts, year):
    """What was given in a year to recipients said to be deductible."""
    if gifts is None or gifts.empty:
        return 0.0
    hit = gifts[(gifts["month"].astype(str).str[:4] == str(year)) & (gifts["deductible"] == "yes")]
    return round(float(pd.to_numeric(hit["given"]).sum()), 2)
