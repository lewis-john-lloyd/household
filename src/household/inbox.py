"""The inbox: recognize each file, write its rows, register it, and file it in record_store.

A run has three phases.

  1. Read: for every file in household_finances/inbox, download and hash it.
     A file already registered is a duplicate. Otherwise identify and parse it
     (a parser's self-checks must pass), and stage the rows whose key is not
     already in the ledger or staged earlier in this run, so an overlapping
     export adds nothing twice. A file whose rows are all present already is
     treated as a duplicate. Unrecognized or failing files stay in the inbox.
  2. Write: one append per ledger table for everything staged, then one append
     registering the documents in source_documents. Batching keeps a run to a
     handful of Sheets writes however many files there are.
  3. File: move each ingested file to record_store under a name that says what
     it is, and duplicates to record_store/duplicates.

The order makes an interrupted run safe to repeat: rows are recognized by key,
and a registered file still sitting in the inbox (its move failed) is filed on
the next run rather than treated as a duplicate.
"""

import hashlib
import io
import logging
import time
from datetime import datetime, timezone

import pandas as pd

from . import backup, pdftext, schema, sheets
from .backup import RETRIES
from .parsers import ParseError, identify

PDF = "application/pdf"
DOCS = schema.table(schema.LEDGER, "source_documents")


def list_files(drive, folder_id):
    query = (f"'{folder_id}' in parents and trashed = false and "
             f"mimeType != '{backup.FOLDER_MIME}'")
    response = drive.files().list(q=query, fields="files(id,name,mimeType)",
                                  pageSize=1000).execute(num_retries=RETRIES)
    return response.get("files", [])


def download(drive, file_id):
    from googleapiclient.http import MediaIoBaseDownload

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, drive.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk(num_retries=RETRIES)
    return buffer.getvalue()


def move(drive, file_id, from_id, to_id, name):
    drive.files().update(fileId=file_id, addParents=to_id, removeParents=from_id,
                         body={"name": name}, fields="id").execute(num_retries=RETRIES)


def is_pdf(name, mime):
    return mime == PDF or name.lower().endswith(".pdf")


def to_text(name, mime, data, all_pages=False):
    if is_pdf(name, mime):
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        return pdftext.extract(data, max_pages=None if all_pages else pdftext.MAX_PAGES)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ""


def _key(df, keys):
    return df[keys].astype(str).apply(
        lambda c: c.str.replace(r"\.0+$", "", regex=True)).agg("|".join, axis=1)


def fresh_rows(incoming, existing, keys):
    """Rows of incoming whose key columns do not match any existing row."""
    if incoming.empty or existing.empty:
        return incoming
    return incoming[~_key(incoming, keys).isin(set(_key(existing, keys)))]


def overlapping_docs(incoming, existing, keys):
    """doc_ids of existing rows that incoming repeats."""
    if incoming.empty or existing.empty or "doc_id" not in existing:
        return set()
    hit = _key(existing, keys).isin(set(_key(incoming, keys)))
    return set(existing.loc[hit, "doc_id"].astype(str)) - {""}


def canonical_name(result, sha, original):
    extension = original.rsplit(".", 1)[-1].lower() if "." in original else "bin"
    return f"{result.label}_{sha[:8]}.{extension}"


class _Tables:
    """Ledger tables read once per run; staged rows are visible to later files at once."""

    def __init__(self, ledger):
        self.ledger, self.cache, self.pending = ledger, {}, {}
        self.spec = {t.name: t for t in schema.LEDGER}

    def read(self, name):
        if name not in self.cache:
            self.cache[name] = sheets.read_table(self.ledger, self.spec[name])
        return self.cache[name]

    def stage(self, name, rows):
        if rows.empty:
            return
        rows = rows[list(self.spec[name].columns)]
        self.cache[name] = pd.concat([self.read(name), rows], ignore_index=True)
        self.pending[name] = pd.concat([self.pending.get(name, rows.iloc[:0]), rows],
                                       ignore_index=True)

    def flush(self):
        """Write staged rows: one append per table, source_documents last."""
        order = sorted(self.pending, key=lambda n: n == "source_documents")
        return {name: sheets.append_rows(self.ledger, self.spec[name], self.pending.pop(name))
                for name in order}


class _Clock:
    def __init__(self):
        self.seconds = {}

    def add(self, stage, started):
        self.seconds[stage] = round(self.seconds.get(stage, 0.0) + time.perf_counter() - started, 2)


def process(ledger, drive, folder_id, now=None, timing=None):
    """Ingest every file in the inbox. Returns one summary dict per file.

    A file that hits an unexpected error (a network timeout, say) is reported
    with status "error" and left in the inbox; the run carries on, and the next
    run picks it up. Pass a dict as timing to receive seconds spent per stage.
    """
    clock = _Clock()
    received_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    inbox = backup.find_or_create_folder(drive, "inbox", folder_id)
    record = backup.find_or_create_folder(drive, "record_store", folder_id)
    started = time.perf_counter()
    ledger_tables = _Tables(ledger)
    registered = ledger_tables.read("source_documents")
    clock.add("read ledger", started)
    by_sha = dict(zip(registered["sha256"], registered["drive_link"]))
    names = dict(zip(registered["doc_id"], registered["original_name"]))
    summaries, moves = [], []

    for f in list_files(drive, inbox):
        summary = {"file": f["name"], "status": None}
        summaries.append(summary)
        try:
            move_to = _read_one(f, drive, ledger_tables, by_sha, names, received_at, summary, clock)
            if move_to:
                moves.append((f, summary, move_to))
        except Exception as e:  # keep going; the file stays in the inbox for the next run
            summary.update(status="error", reason=f"{type(e).__name__}: {e}")

    started = time.perf_counter()
    ledger_tables.flush()
    clock.add("write ledger", started)

    started = time.perf_counter()
    duplicates = None
    for f, summary, (where, name) in moves:
        try:
            if where == "duplicates":
                duplicates = duplicates or backup.find_or_create_folder(drive, "duplicates", record)
                move(drive, f["id"], inbox, duplicates, name)
            else:
                move(drive, f["id"], inbox, record, name)
        except Exception as e:  # registered but not moved: the next run files it
            summary.update(status="error", reason=f"not moved: {type(e).__name__}: {e}")
    clock.add("file in Drive", started)
    if timing is not None:
        timing.update(clock.seconds)
    return summaries


def _link(file_id):
    return f"https://drive.google.com/file/d/{file_id}"  # leak-guard: allow (template, no ID)


def _read_one(f, drive, ledger_tables, by_sha, names, received_at, summary, clock):
    """Phase 1 for one file: returns (folder, name) to move it to, or None to leave it."""
    tables = ledger_tables.spec
    started = time.perf_counter()
    data = download(drive, f["id"])
    clock.add("download", started)
    sha = hashlib.sha256(data).hexdigest()
    unfiled = sha in by_sha and by_sha[sha] == _link(f["id"])  # registered, move had failed
    if sha in by_sha and not unfiled:
        summary["status"] = "duplicate"
        return ("duplicates", f["name"])

    started = time.perf_counter()
    text = to_text(f["name"], f.get("mimeType", ""), data)
    parser = identify(text)
    if parser is None:
        clock.add("parse", started)
        summary["status"] = "unrecognized"
        return None
    if getattr(parser, "ALL_PAGES", False) and is_pdf(f["name"], f.get("mimeType", "")):
        text = to_text(f["name"], f.get("mimeType", ""), data, all_pages=True)
    try:
        result = parser.parse(text)
    except ParseError as e:
        summary.update(status="failed", reason=str(e))
        return None
    finally:
        clock.add("parse", started)
    new_name = canonical_name(result, sha, f["name"])
    if unfiled:
        summary.update(status="ingested", parser=result.parser, filed_as=new_name,
                       appended={}, notes=["registered on an earlier run; filed now"])
        return ("record_store", new_name)

    doc_id = sha[:16]
    staged, overlaps = {}, set()
    for name, rows in result.tables.items():
        if rows.empty:
            continue
        rows = rows.copy()
        if "doc_id" in tables[name].columns:
            rows["doc_id"] = doc_id
        existing = ledger_tables.read(name)
        staged[name] = (rows, fresh_rows(rows, existing, result.keys[name]))
        overlaps |= overlapping_docs(rows, existing, result.keys[name])
    if staged and all(new.empty for _, new in staged.values()):
        summary.update(status="already_present", parser=result.parser,
                       overlaps=sorted(names.get(d, d) for d in overlaps))
        return ("duplicates", f["name"])

    appended = {}
    for name, (rows, new) in staged.items():
        ledger_tables.stage(name, new)
        appended[name] = len(new)
        summary.setdefault("skipped", {})[name] = len(rows) - len(new)
    if overlaps:
        summary["overlaps"] = sorted(names.get(d, d) for d in overlaps)
    ledger_tables.stage("source_documents", pd.DataFrame([{
        "doc_id": doc_id, "kind": result.kind, "sha256": sha, "original_name": f["name"],
        "drive_link": _link(f["id"]), "account_id": result.account_id,
        "covers_from": result.covers_from, "covers_to": result.covers_to,
        "parser": result.parser, "received_at": received_at, "precedence": "",
    }]))
    by_sha[sha] = _link(f["id"])
    names[doc_id] = f["name"]
    summary.update(status="ingested", parser=result.parser, filed_as=new_name,
                   appended=appended, notes=result.notes)
    return ("record_store", new_name)
