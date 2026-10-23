"""The inbox: recognize each file, write its rows, register it, and file it in record_store.

For every file in household_finances/inbox:
  1. Hash it. A file already registered goes to record_store/duplicates untouched.
  2. Identify it (see parsers). Unrecognized files stay in the inbox and are reported.
  3. Parse it. A parser's self-checks must pass, or the file stays in the inbox
     with the reason, and nothing is written.
  4. Append its rows, skipping any whose key is already in the ledger, so an
     overlapping export or a re-run adds nothing twice.
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


def to_text(name, mime, data):
    if mime == PDF or name.lower().endswith(".pdf"):
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        return pdftext.extract(data)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ""


def fresh_rows(incoming, existing, keys):
    """Rows of incoming whose key columns do not match any existing row."""
    if incoming.empty or existing.empty:
        return incoming

    def key(df):
        return df[keys].astype(str).apply(
            lambda c: c.str.replace(r"\.0+$", "", regex=True)).agg("|".join, axis=1)

    return incoming[~key(incoming).isin(set(key(existing)))]


def canonical_name(result, sha, original):
    extension = original.rsplit(".", 1)[-1].lower() if "." in original else "bin"
    return f"{result.label}_{sha[:8]}.{extension}"


def process(ledger, drive, folder_id, now=None):
    """Ingest every file in the inbox. Returns one summary dict per file."""
    received_at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    inbox = backup.find_or_create_folder(drive, "inbox", folder_id)
    record = backup.find_or_create_folder(drive, "record_store", folder_id)
    known = set(sheets.read_table(ledger, DOCS)["sha256"])
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
        try:
            result = parser.parse(text)
        except ParseError as e:
            summary.update(status="failed", reason=str(e))
            continue

        doc_id = sha[:16]
        written = {}
        for name, rows in result.tables.items():
            if rows.empty:
                continue
            rows = rows.copy()
            if "doc_id" in tables[name].columns:
                rows["doc_id"] = doc_id
            existing = sheets.read_table(ledger, tables[name])
            new = fresh_rows(rows, existing, result.keys[name])
            written[name] = sheets.append_rows(ledger, tables[name], new)
            summary.setdefault("skipped", {})[name] = len(rows) - len(new)

        new_name = canonical_name(result, sha, f["name"])
        sheets.append_rows(ledger, DOCS, pd.DataFrame([{
            "doc_id": doc_id, "kind": result.kind, "sha256": sha, "original_name": f["name"],
            "drive_link": f"https://drive.google.com/file/d/{f['id']}",  # leak-guard: allow (template, no ID)
            "account_id": result.account_id, "covers_from": result.covers_from,
            "covers_to": result.covers_to, "parser": result.parser, "received_at": received_at,
            "precedence": "",
        }]))
        known.add(sha)
        move(drive, f["id"], inbox, record, new_name)
        summary.update(status="ingested", parser=result.parser, filed_as=new_name,
                       appended=written, notes=result.notes)
    return summaries
