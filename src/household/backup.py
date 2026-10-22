"""Pre-write backups: every ledger tab to CSV in backups/<run_id>/ before ingest writes."""

import csv
import hashlib
import io
import json

FOLDER_MIME = "application/vnd.google-apps.folder"


def find_or_create_folder(drive, name, parent_id):
    query = (f"name = '{name}' and '{parent_id}' in parents and mimeType = '{FOLDER_MIME}' "
             "and trashed = false")
    found = drive.files().list(q=query, fields="files(id)").execute().get("files", [])
    if found:
        return found[0]["id"]
    body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    return drive.files().create(body=body, fields="id").execute()["id"]


def upload_text(drive, name, parent_id, text, mime="text/csv"):
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype=mime)
    body = {"name": name, "parents": [parent_id]}
    return drive.files().create(body=body, media_body=media, fields="id").execute()["id"]


def to_csv(rows):
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def snapshot_ledger(ledger, drive, root_folder_id, run_id):
    """Write each ledger tab as CSV plus a manifest of row counts and hashes. Returns the manifest."""
    backups = find_or_create_folder(drive, "backups", root_folder_id)
    target = find_or_create_folder(drive, run_id, backups)
    manifest = {"run_id": run_id, "tables": {}}
    titles = [ws.title for ws in ledger.worksheets()]
    result = ledger.values_batch_get([f"'{t}'" for t in titles],
                                     params={"valueRenderOption": "UNFORMATTED_VALUE"})
    for title, value_range in zip(titles, result.get("valueRanges", [])):
        text = to_csv(value_range.get("values", []))
        upload_text(drive, f"{title}.csv", target, text)
        manifest["tables"][title] = {
            "rows": max(text.count("\n") - 1, 0),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    upload_text(drive, "manifest.json", target, json.dumps(manifest, indent=1), "application/json")
    return manifest
