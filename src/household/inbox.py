"""The inbox: recognize each file, write its rows, register it, and file it in record_store.

For every file in household_finances/inbox:
  1. Hash it. A file already registered goes to record_store/duplicates untouched.
  2. Identify it (see parsers). Unrecognized files stay in the inbox and are reported.
  3. Parse it. A parser's self-checks must pass, or the file stays in the inbox
     with the reason, and nothing is written.
  4. Append its rows, skipping any whose key is already in the ledger, so an
     overlapping export or a re-run adds nothing twice. The summary names the
     earlier documents a partial overlap shared rows with. A file whose rows are
     all already present (a re-download of the same data, different bytes) is
     treated as a duplicate.
  5. Register it in source_documents, then move it to record_store under a
     name that says what it is.

Steps 4 and 5 are ordered so a run that dies part way is safe to repeat.
"""

import hashlib
import io
import logging
from datetime import datetime, timezone

import pandas as pd

from . import backup, pdftext, schema, sheets
from .parsers import ParseError, identify

PDF = "application/pdf"
DOCS = schema.table(schema.LEDGER, "source_documents")


def list_files(drive, folder_id):
    query = (f"'{folder_id}' in parents and trashed = false and "
             f"mimeType != '{backup.FOLDER_MIME}'")
    response = drive.files().list(q=query, fields="files(id,name,mimeType)",
                                  pageSize=1000).execute()
    return response.get("files", [])


def download(drive, file_id):
    from googleapiclient.http import MediaIoBaseDownload

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, drive.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def move(drive, file_id, from_id, to_id, name):
    drive.files().update(fileId=file_id, addParents=to_id, removeParents=from_id,
                         body={"name": name}, fields="id").execute()


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


def process(ledger, drive, folder_id, now=None):
    """Ingest every file in the inbox. Returns one summary dict per file."""
    received_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    inbox = backup.find_or_create_folder(drive, "inbox", folder_id)
    record = backup.find_or_create_folder(drive, "record_store", folder_id)
    registered = sheets.read_table(ledger, DOCS)
    known = set(registered["sha256"])
    names = dict(zip(registered["doc_id"], registered["original_name"]))
    tables = {t.name: t for t in schema.LEDGER}
    summaries = []

    for f in list_files(drive, inbox):
        data = download(drive, f["id"])
        sha = hashlib.sha256(data).hexdigest()
        summary = {"file": f["name"], "status": None}
        summaries.append(summary)
        if sha in known:
            duplicates = backup.find_or_create_folder(drive, "duplicates", record)
            move(drive, f["id"], inbox, duplicates, f["name"])
            summary["status"] = "duplicate"
            continue
        text = to_text(f["name"], f.get("mimeType", ""), data)
        parser = identify(text)
        if parser is None:
            summary["status"] = "unrecognized"
            continue
        if getattr(parser, "ALL_PAGES", False) and is_pdf(f["name"], f.get("mimeType", "")):
            text = to_text(f["name"], f.get("mimeType", ""), data, all_pages=True)
        try:
            result = parser.parse(text)
        except ParseError as e:
            summary.update(status="failed", reason=str(e))
            continue

        doc_id = sha[:16]
        staged, overlaps = {}, set()
        for name, rows in result.tables.items():
            if rows.empty:
                continue
            rows = rows.copy()
            if "doc_id" in tables[name].columns:
                rows["doc_id"] = doc_id
            existing = sheets.read_table(ledger, tables[name])
            staged[name] = (rows, fresh_rows(rows, existing, result.keys[name]))
            overlaps |= overlapping_docs(rows, existing, result.keys[name])
        if staged and all(new.empty for _, new in staged.values()):
            duplicates = backup.find_or_create_folder(drive, "duplicates", record)
            move(drive, f["id"], inbox, duplicates, f["name"])
            summary.update(status="already_present", parser=result.parser,
                           overlaps=sorted(names.get(d, d) for d in overlaps))
            continue
        written = {}
        for name, (rows, new) in staged.items():
            written[name] = sheets.append_rows(ledger, tables[name], new)
            summary.setdefault("skipped", {})[name] = len(rows) - len(new)
        if overlaps:
            summary["overlaps"] = sorted(names.get(d, d) for d in overlaps)

        new_name = canonical_name(result, sha, f["name"])
        sheets.append_rows(ledger, DOCS, pd.DataFrame([{
            "doc_id": doc_id, "kind": result.kind, "sha256": sha, "original_name": f["name"],
            "drive_link": f"https://drive.google.com/file/d/{f['id']}",  # leak-guard: allow (template, no ID)
            "account_id": result.account_id, "covers_from": result.covers_from,
            "covers_to": result.covers_to, "parser": result.parser, "received_at": received_at,
            "precedence": "",
        }]))
        known.add(sha)
        names[doc_id] = f["name"]
        move(drive, f["id"], inbox, record, new_name)
        summary.update(status="ingested", parser=result.parser, filed_as=new_name,
                       appended=written, notes=result.notes)
    return summaries
