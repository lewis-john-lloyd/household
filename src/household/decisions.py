"""Answers a person gave, kept in proposal_decisions: one reader for every kind.

A decision row is (proposal_key, kind, decided_at, decision, note). The key is "<prefix>:<what it is about>"
(a merchant key, a stub id), the decision approve or reject, and the note what was said (a category, a name,
a budget line). The latest row per key is the answer in force, so a later answer replaces an earlier one and
the ledger keeps both. Each kind's meaning stays with the module that uses it:

  category             categorize.py   a review answer: the category for a merchant key
  subscription         recurring.py    track a merchant key's charges as recurring, or not
  subscription_merge   recurring.py    a key's charges count as another key's
  subscription_name    recurring.py    the name to show a recurring charge by
  subscription_line    recurring.py    the budget line a recurring charge pays
  deductible           giving.py       whether gifts to a recipient count toward the deduction
  looks_right          budget_checks   a budget check looked at and right, by its fingerprint
  withholding          withholding.py  why a stub's withholding is off
  duplicate            checks          two lines that are both real
"""

import pandas as pd


def latest(decisions, kind, prefix=None):
    """{key: (approved, note)}: the latest answer per proposal key of one kind, oldest first so a later one
    wins, the key without its "<prefix>:" (prefix defaults to the kind). Rows whose key lacks the prefix are
    left out."""
    if decisions is None or decisions.empty:
        return {}
    head = (kind if prefix is None else prefix) + ":"
    d = decisions[(decisions["kind"] == kind) & decisions["proposal_key"].astype(str).str.startswith(head)]
    d = d.sort_values("decided_at", kind="stable")
    notes = d["note"] if "note" in d else pd.Series([""] * len(d), index=d.index)
    return {
        str(k)[len(head) :]: (v == "approve", "" if n is None or (isinstance(n, float) and pd.isna(n)) else str(n))
        for k, v, n in zip(d["proposal_key"], d["decision"], notes)
    }


def approved_notes(decisions, kind, prefix=None, upper=False):
    """{key: note} for the keys whose latest answer approves with a note that is not blank (a reject, or a
    blank note, takes the key back out)."""
    out = {}
    for key, (approved, note) in latest(decisions, kind, prefix).items():
        note = note.strip().upper() if upper else note.strip()
        if approved and note:
            out[key] = note
    return out
