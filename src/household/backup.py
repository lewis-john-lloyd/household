"""Pre-write backups: every ledger tab to CSV, zipped into backups/ledger_<run_id>.zip.

One zip per run keeps a backup to a single Drive upload however many tabs the
ledger has. It holds <tab>.csv for every tab plus manifest.json with each tab's
row count and SHA-256, so a restore can be checked.
"""

import csv
import hashlib
import io
import json
import zipfile

FOLDER_MIME = "application/vnd.google-apps.folder"
RETRIES = 5  # Google's client retries timeouts, dropped connections and 5xx with backoff


def find_or_create_folder(drive, name, parent_id):
    query = (f"name = '{name}' and '{parent_id}' in parents and mimeType = '{FOLDER_MIME}' "
             "and trashed = false")
    found = drive.files().list(q=query, fields="files(id)").execute(num_retries=RETRIES).get("files", [])
    if found:
        return found[0]["id"]
    body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    return drive.files().create(body=body, fields="id").execute(num_retries=RETRIES)["id"]


def upload_text(drive, name, parent_id, text, mime="text/csv"):
    return upload_bytes(drive, name, parent_id, text.encode("utf-8"), mime)


def upload_bytes(drive, name, parent_id, data, mime):
    from googleapiclient.http import MediaIoBaseUpload

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    body = {"name": name, "parents": [parent_id]}
    return drive.files().create(body=body, media_body=media, fields="id").execute(num_retries=RETRIES)["id"]


def to_csv(rows):
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue()


def snapshot_ledger(ledger, drive, root_folder_id, run_id):
    """Zip each ledger tab as CSV, plus a manifest of row counts and hashes, and upload it once.

    Returns the manifest.
    """
    backups = find_or_create_folder(drive, "backups", root_folder_id)
    manifest = {"run_id": run_id, "tables": {}}
    titles = [ws.title for ws in ledger.worksheets()]
    result = ledger.values_batch_get([f"'{t}'" for t in titles],
                                     params={"valueRenderOption": "UNFORMATTED_VALUE"})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for title, value_range in zip(titles, result.get("valueRanges", [])):
            text = to_csv(value_range.get("values", []))
            bundle.writestr(f"{title}.csv", text)
            manifest["tables"][title] = {
                "rows": max(text.count("\n") - 1, 0),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        bundle.writestr("manifest.json", json.dumps(manifest, indent=1))
    upload_bytes(drive, f"ledger_{run_id}.zip", backups, buffer.getvalue(), "application/zip")
    return manifest
