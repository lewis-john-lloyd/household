"""End-to-end check that a notebook can write to and read from Google Sheets."""

from datetime import datetime, timezone

import gspread

SMOKE_TITLE = "household_smoke_test"


def roundtrip(client, folder_id, title=SMOKE_TITLE, now=None):
    """Write a timestamped row to a test Sheet in `folder_id`, read it back, and compare.

    Reuses the test Sheet if it already exists in the folder. Returns a dict with
    `ok`, the values written and read, and the Sheet URL.
    """
    try:
        sheet = client.open(title, folder_id=folder_id)
    except gspread.SpreadsheetNotFound:
        sheet = client.create(title, folder_id=folder_id)

    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    written = [["household smoke test", stamp]]
    worksheet = sheet.sheet1
    worksheet.update(values=written, range_name="A1:B1")
    read = worksheet.get("A1:B1")

    return {"ok": read == written, "written": written, "read": read, "url": sheet.url}
