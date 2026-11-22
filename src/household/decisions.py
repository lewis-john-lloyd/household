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


LINE_NAME = "budget_line_name"


def line_names(decisions):
    """{a budget line's name in the ledger: its name now} from proposal_decisions' "budget_line_name" answers
    (a rename from the views, the new name in its note), applied in the order they were made, so a line renamed
    twice takes the second name and renaming it back undoes the first. Rows written after a rename carry the
    new name already; only names that changed are listed."""
    if decisions is None or decisions.empty:
        return {}
    head = LINE_NAME + ":"
    d = decisions[(decisions["kind"] == LINE_NAME) & decisions["proposal_key"].astype(str).str.startswith(head)]
    d = d[d["decision"] == "approve"].sort_values("decided_at", kind="stable")
    now = {}  # each name ever seen: its name now
    for key, note in zip(d["proposal_key"], d["note"] if "note" in d else [""] * len(d)):
        old, new = str(key)[len(head) :].strip(), str(note or "").strip()
        if not new or new == old:
            continue
        now.setdefault(old, old)
        now.setdefault(new, new)
        for name, current in list(now.items()):
            if current == old:
                now[name] = new
    return {name: current for name, current in now.items() if name != current}


def renamed_lines(rules, decisions):
    """Budget rules with each line under its name now (line_names)."""
    names = line_names(decisions)
    if rules is None or rules.empty or not names:
        return rules
    line = rules["line"].astype(str).str.strip()
    return rules.assign(line=line.map(lambda n: names.get(n, n)))
