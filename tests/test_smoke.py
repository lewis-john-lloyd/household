from datetime import datetime, timezone

import gspread

from household import smoke


class FakeWorksheet:
    def __init__(self):
        self.cells = {}

    def update(self, values, range_name):
        self.cells[range_name] = [list(row) for row in values]

    def get(self, range_name):
        return self.cells.get(range_name, [])


class FakeSheet:
    def __init__(self, title):
        self.title = title
        self.sheet1 = FakeWorksheet()
        self.url = f"https://example.invalid/{title}"


class FakeClient:
    def __init__(self):
        self.sheets = {}
        self.created = 0

    def open(self, title, folder_id=None):
        key = (title, folder_id)
        if key not in self.sheets:
            raise gspread.SpreadsheetNotFound
        return self.sheets[key]

    def create(self, title, folder_id=None):
        self.created += 1
        sheet = FakeSheet(title)
        self.sheets[(title, folder_id)] = sheet
        return sheet


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_roundtrip_creates_sheet_and_reads_back():
    client = FakeClient()
    result = smoke.roundtrip(client, folder_id="folder", now=NOW)
    assert result["ok"]
    assert result["read"] == [["household smoke test", "2026-01-01T00:00:00+00:00"]]
    assert client.created == 1


def test_roundtrip_reuses_existing_sheet():
    client = FakeClient()
    smoke.roundtrip(client, folder_id="folder", now=NOW)
    smoke.roundtrip(client, folder_id="folder", now=NOW)
    assert client.created == 1


def test_roundtrip_detects_mismatch():
    client = FakeClient()
    sheet = client.create(smoke.SMOKE_TITLE, folder_id="folder")
    sheet.sheet1.get = lambda range_name: [["something else"]]
    assert not smoke.roundtrip(client, folder_id="folder", now=NOW)["ok"]
