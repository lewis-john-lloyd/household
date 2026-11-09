"""The ledger and Reports as every stage opens them, and the ledger as compute reads it.

Each stage opens the two spreadsheets with bootstrap, which also forgets whatever an earlier compute in the
same session read as renamed or moved, so ingest and the legacy imports always see the ledger as written.
Compute then reads accounts under their new names (account_renamed), the old investments workbook's readings
under the account they belong to (legacy_account), and investment activity as corrected
(activity_corrections), all through read_ledger.
"""

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import legacy_investments, schema, settings, sheets
from .kinds import Frame, Session, Spreadsheet


def versions() -> str:
    """The libraries this run uses and their versions ("pandas 2.2.2, ..."): the tested range is in
    pyproject.toml, and Colab's image decides where in it a run lands, so each run says."""
    import gspread
    import numpy
    import pypdf
    import sklearn

    out = []
    for name, module in (
        ("pandas", pd),
        ("numpy", numpy),
        ("scikit-learn", sklearn),
        ("gspread", gspread),
        ("pypdf", pypdf),
    ):
        out.append(f"{name} {getattr(module, '__version__', '?')}")
    return ", ".join(out)


def new_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def bootstrap(session: Session, folder_id: str) -> tuple[Spreadsheet, Spreadsheet, dict[str, Any]]:
    """Open or create the ledger and Reports spreadsheets and their declared tables."""
    # every stage reads the ledger as written; compute alone reads accounts renamed or moved (compute_all sets
    # them after this), so a stage run after compute in the same session doesn't see stale names
    _RENAMED.clear()
    _LEGACY_MOVED.clear()
    _ACTIVITY_FIXES.clear()
    ledger, _ = sheets.open_or_create(session.sheets, sheets.LEDGER_TITLE, folder_id)
    reports, _ = sheets.open_or_create(session.sheets, sheets.REPORTS_TITLE, folder_id)
    created = {
        "ledger": sheets.ensure_tables(ledger, schema.LEDGER),
        "reports": sheets.ensure_tables(reports, schema.REPORTS),
    }
    return ledger, reports, created


_RENAMED: dict[str, str] = {}
_LEGACY_MOVED: dict[
    str, str
] = {}  # {id the old investments workbook's readings were read under: the account they belong to}
_ACTIVITY_FIXES: dict[
    str, dict[str, str]
] = {}  # {activity_id: {field: value}} from activity_corrections, the last row in force
ACCOUNT_COLUMNS = ("account_id", "account")


def legacy_moves(parameters: Frame, accounts: set[str]) -> tuple[dict[str, str], list[str]]:
    """{id: account} from the legacy_account:<id> settings, and a note for each one that can't be used: both
    must be accounts in the ledger."""
    out: dict = {}
    notes: list[str] = []
    if parameters is None or not len(parameters):
        return out, notes
    p = parameters[parameters["jurisdiction"].astype(str) == "household"]
    for key in sorted(set(p["key"].astype(str))):
        if not key.startswith(settings.LEGACY_ACCOUNT_PREFIX):
            continue
        old, new = key[len(settings.LEGACY_ACCOUNT_PREFIX) :], settings.value(parameters, key).strip()
        if not new:
            continue
        missing = [a for a in (old, new) if a not in accounts]
        if missing:
            notes.append(f"accounts: {key} = {new}: no account {' or '.join(missing)} in the ledger")
            continue
        out[old] = new
    return out, notes


def renamed_accounts(parameters: Frame) -> tuple[dict[str, str], list[str]]:
    """{old id: new id} from the account_renamed:<old> settings, and a note for each one that can't be used."""
    out: dict = {}
    notes: list[str] = []
    if parameters is None or not len(parameters):
        return out, notes
    p = parameters[parameters["jurisdiction"].astype(str) == "household"]
    for key in sorted(set(p["key"].astype(str))):
        if not key.startswith(settings.RENAME_PREFIX):
            continue
        old, new = key[len(settings.RENAME_PREFIX) :], settings.value(parameters, key).strip()
        if not new:
            continue
        if not settings.ACCOUNT_ID.fullmatch(new):
            notes.append(f"accounts: {key} = {new!r} isn't an account id (lower case, digits and _)")
            continue
        out[old] = new
    if len(set(out.values())) < len(out):
        notes.append("accounts: two account_renamed settings give the same new name: neither is used")
        return {}, notes
    return out, notes


def activity_fixes(corrections: Frame, activity: Frame) -> tuple[dict[str, dict[str, str]], list[str]]:
    """{activity_id: {field: value}} from activity_corrections (the last row for an activity and field wins),
    and a note for each that can't be used: an activity the ledger doesn't have, or a kind that isn't one."""
    out: dict = {}
    notes: list[str] = []
    if corrections is None or not len(corrections):
        return out, notes
    known = set(activity["activity_id"].astype(str)) if activity is not None and len(activity) else set()
    kinds = schema.table(schema.LEDGER, "investment_activity").enums["kind"]
    for r in corrections.to_dict("records"):
        aid, f, v = str(r["activity_id"]).strip(), str(r["field"]).strip(), str(r["value"]).strip()
        if aid not in known:
            notes.append(f"investment activity: a correction names {aid}, which the ledger doesn't have")
        elif f == "kind" and v not in kinds:
            notes.append(f"investment activity: {aid}: {v!r} isn't a kind ({', '.join(kinds)})")
        else:
            out.setdefault(aid, {})[f] = v
    return out, notes


def read_ledger(ledger: Spreadsheet, name: str) -> Frame:
    df = sheets.read_table(ledger, schema.table(schema.LEDGER, name))
    if _ACTIVITY_FIXES and name == "investment_activity" and len(df):
        for i in df.index[df["activity_id"].astype(str).isin(_ACTIVITY_FIXES)]:
            for f, v in _ACTIVITY_FIXES[str(df.at[i, "activity_id"])].items():
                df.at[i, f] = v
    if _LEGACY_MOVED and name == "holdings" and len(df):
        legacy = df["doc_id"].astype(str) == legacy_investments.LEGACY_DOC
        df.loc[legacy, "account_id"] = df.loc[legacy, "account_id"].map(lambda a: _LEGACY_MOVED.get(str(a), a))
    if _RENAMED and len(df):
        for column in ACCOUNT_COLUMNS:
            if column in df.columns:
                df[column] = df[column].map(lambda a: _RENAMED.get(str(a), a))
    return df
