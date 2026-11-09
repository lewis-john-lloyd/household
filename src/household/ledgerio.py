"""The ledger and Reports as every stage opens them, and the ledger as compute reads it.

read_ledger(ledger, name) reads a table as written. Compute reads it through a LedgerView instead: the same
spreadsheet with a LedgerReading attached, which reads accounts under their new names (account_renamed), the
old investments workbook's readings under the account they belong to (legacy_account), and investment
activity as corrected (activity_corrections). The reading is worked out once from the ledger
(LedgerReading.of) and travels with the view, so what a read returns depends only on what it is given.
"""

from dataclasses import dataclass, field
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
    ledger, _ = sheets.open_or_create(session.sheets, sheets.LEDGER_TITLE, folder_id)
    reports, _ = sheets.open_or_create(session.sheets, sheets.REPORTS_TITLE, folder_id)
    created = {
        "ledger": sheets.ensure_tables(ledger, schema.LEDGER),
        "reports": sheets.ensure_tables(reports, schema.REPORTS),
    }
    return ledger, reports, created


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


@dataclass(frozen=True)
class LedgerReading:
    """How compute reads the ledger: accounts renamed, old readings moved, activity corrected. Empty, it reads
    the ledger as written."""

    renamed: dict[str, str] = field(default_factory=dict)  # {old account id: new id}
    legacy_moved: dict[str, str] = field(
        default_factory=dict
    )  # {id the old workbook's readings were read under: the account}
    activity_fixes: dict[str, dict[str, str]] = field(default_factory=dict)  # {activity_id: {field: value}}

    @classmethod
    def of(cls, ledger: Spreadsheet) -> tuple["LedgerReading", list[str]]:
        """The reading the ledger's settings and corrections ask for, and a note for each one that can't be used."""
        parameters = read_ledger(ledger, "parameters")
        accounts = set(read_ledger(ledger, "accounts")["account_id"].astype(str))
        renames, problems = renamed_accounts(parameters)
        moves, more = legacy_moves(parameters, accounts)
        problems += more
        fixes, more = activity_fixes(
            read_ledger(ledger, "activity_corrections"), read_ledger(ledger, "investment_activity")
        )
        problems += more
        for old, new in list(renames.items()):
            if old not in accounts:
                problems.append(f"accounts: account_renamed:{old} names no account")
                del renames[old]
            elif new in accounts:
                problems.append(f"accounts: account_renamed:{old} = {new}, but {new} is already an account")
                del renames[old]
        return cls(renames, moves, fixes), problems

    def notes(self) -> list[str]:
        """What this reading changes, for the run log."""
        out = []
        if self.renamed:
            out.append(
                "accounts: read as renamed: " + ", ".join(f"{o} as {n}" for o, n in sorted(self.renamed.items()))
            )
        if self.activity_fixes:
            out.append(f"investment activity: {len(self.activity_fixes)} rows read as corrected (activity_corrections)")
        if self.legacy_moved:
            out.append(
                "accounts: the old investments workbook's readings of "
                + ", ".join(f"{o} read as {n}'s" for o, n in sorted(self.legacy_moved.items()))
            )
        return out

    def apply(self, name: str, df: Frame) -> Frame:
        """A table as read, changed the way this reading asks."""
        if not len(df):
            return df
        if self.activity_fixes and name == "investment_activity":
            for i in df.index[df["activity_id"].astype(str).isin(self.activity_fixes)]:
                for f, v in self.activity_fixes[str(df.at[i, "activity_id"])].items():
                    df.at[i, f] = v
        if self.legacy_moved and name == "holdings":
            legacy = df["doc_id"].astype(str) == legacy_investments.LEGACY_DOC
            df.loc[legacy, "account_id"] = df.loc[legacy, "account_id"].map(lambda a: self.legacy_moved.get(str(a), a))
        if self.renamed:
            for column in ACCOUNT_COLUMNS:
                if column in df.columns:
                    df[column] = df[column].map(lambda a: self.renamed.get(str(a), a))
        return df


class LedgerView:
    """The ledger spreadsheet with a LedgerReading attached: read_ledger on it reads the way the reading asks.
    Compute only reads the ledger, so a view offers what reading needs (its title and its worksheets); a table
    read with sheets.read_table directly comes back as written."""

    def __init__(self, ledger: Spreadsheet, reading: LedgerReading):
        self.ledger, self.reading = ledger, reading

    @property
    def title(self) -> str:
        return self.ledger.title

    def worksheet(self, name: str) -> Any:
        return self.ledger.worksheet(name)


def read_ledger(ledger: "Spreadsheet | LedgerView", name: str) -> Frame:
    """A ledger table: as written, or, read through a LedgerView, the way its reading asks."""
    if isinstance(ledger, LedgerView):
        return ledger.reading.apply(name, sheets.read_table(ledger.ledger, schema.table(schema.LEDGER, name)))
    return sheets.read_table(ledger, schema.table(schema.LEDGER, name))
