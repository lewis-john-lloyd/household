"""Inbox processing against in-memory fakes of Drive and the ledger spreadsheet."""

import re
from pathlib import Path

import pytest

from household import inbox, schema, sheets
from test_sheets import FakeSpreadsheet

FIXTURES = Path(__file__).parent / "fixtures"
FOLDER = "application/vnd.google-apps.folder"


class Request:
    def __init__(self, result):
        self.result = result

    def execute(self, num_retries=0):
        return self.result


class FakeFiles:
    def __init__(self, drive):
        self.drive = drive

    def list(self, q, fields, pageSize=None):
        parent = re.search(r"'([^']+)' in parents", q).group(1)
        name = re.search(r"name = '([^']+)'", q)
        found = [f for f in self.drive.items.values() if f["parent"] == parent
                 and (name is None or f["name"] == name.group(1))
                 and (("mimeType != '" + FOLDER) not in q or f["mimeType"] != FOLDER)
                 and (("mimeType = '" + FOLDER) not in q or f["mimeType"] == FOLDER)]
        return Request({"files": [dict(f) for f in found]})

    def create(self, body, fields, media_body=None):
        return Request({"id": self.drive.add(body["name"], body["parents"][0],
                                             body.get("mimeType", "text/csv"), b"")})

    def update(self, fileId, addParents, removeParents, body, fields):
        f = self.drive.items[fileId]
        assert f["parent"] == removeParents
        f.update(parent=addParents, name=body["name"])
        return Request({"id": fileId})


class FakeDrive:
    def __init__(self):
        self.items, self.content, self._n = {}, {}, 0
        self._files = FakeFiles(self)

    def files(self):
        return self._files

    def add(self, name, parent, mime, data):
        self._n += 1
        fid = f"f{self._n}"
        self.items[fid] = {"id": fid, "name": name, "parent": parent, "mimeType": mime}
        self.content[fid] = data
        return fid

    def folder(self, name, parent="root"):
        return self.add(name, parent, FOLDER, b"")

    def names_in(self, parent):
        return sorted(f["name"] for f in self.items.values() if f["parent"] == parent)


@pytest.fixture
def setup(monkeypatch):
    drive, ledger = FakeDrive(), FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    box = drive.folder("inbox")
    record = drive.folder("record_store")
    monkeypatch.setattr(inbox, "download", lambda d, fid: d.content[fid])
    return drive, ledger, box, record


def drop(drive, box, name):
    return drive.add(name, box, "text/plain", (FIXTURES / name).read_bytes())


def rows(ledger, name):
    return sheets.read_table(ledger, schema.table(schema.LEDGER, name))


def test_ingests_files_registers_and_files_them(setup):
    drive, ledger, box, record = setup
    for name in ("pay_statement.txt", "schwab_equity.csv", "accounts_seed.csv"):
        drop(drive, box, name)
    drive.add("notes.txt", box, "text/plain", b"nothing to see")

    out = {s["file"]: s for s in inbox.process(ledger, drive, "root")}

    assert out["notes.txt"]["status"] == "unrecognized"
    assert out["pay_statement.txt"]["appended"] == {"paystubs": 18}
    assert out["schwab_equity.csv"]["appended"] == {"equity_vests": 2, "equity_sales": 2}
    assert out["accounts_seed.csv"]["appended"] == {"accounts": 2}
    assert len(rows(ledger, "source_documents")) == 3
    assert set(rows(ledger, "paystubs")["doc_id"]) == {rows(ledger, "source_documents")["doc_id"][0]}
    assert drive.names_in(box) == ["notes.txt"]
    filed = drive.names_in(record)
    assert any(n.startswith("paystub_alex_2001-01-19_") and n.endswith(".txt") for n in filed)


def test_same_file_again_is_a_duplicate(setup):
    drive, ledger, box, record = setup
    drop(drive, box, "pay_statement.txt")
    inbox.process(ledger, drive, "root")
    drop(drive, box, "pay_statement.txt")
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["status"] == "duplicate"
    assert len(rows(ledger, "paystubs")) == 18
    dup = next(f["id"] for f in drive.items.values() if f["name"] == "duplicates")
    assert drive.names_in(dup) == ["pay_statement.txt"]


def test_overlapping_export_adds_only_new_rows(setup):
    drive, ledger, box, record = setup
    drop(drive, box, "schwab_equity.csv")
    inbox.process(ledger, drive, "root")
    newer = (FIXTURES / "schwab_equity.csv").read_text().replace(
        '"02/27/2001","Sale"',
        '"03/29/2001","Deposit","XYZ","RS","4","","","","","","","","","","","","","","","","","","","","",""\n'
        '"","","","","","","","","12/01/2000","G1","03/25/2001","$90.00","","","","","","","","","","","","","",""\n'  # leak-guard: allow (synthetic)
        '"02/27/2001","Sale"', 1)
    drive.add("export2.csv", box, "text/csv", newer.encode())
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["appended"] == {"equity_vests": 1, "equity_sales": 0}
    assert summary["skipped"] == {"equity_vests": 2, "equity_sales": 2}


def test_failed_self_check_leaves_file_and_writes_nothing(setup):
    drive, ledger, box, record = setup
    broken = (FIXTURES / "pay_statement.txt").read_text().replace("$4,000.00", "$4,100.00", 1)  # leak-guard: allow (synthetic)
    drive.add("stub.txt", box, "text/plain", broken.encode())
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["status"] == "failed" and "self-checks" in summary["reason"]
    assert rows(ledger, "paystubs").empty and rows(ledger, "source_documents").empty
    assert drive.names_in(box) == ["stub.txt"]


def test_redownload_with_different_bytes_is_already_present(setup):
    drive, ledger, box, record = setup
    drop(drive, box, "schwab_equity.csv")
    inbox.process(ledger, drive, "root")
    same_rows = (FIXTURES / "schwab_equity.csv").read_text() + "\n"  # different bytes, same data
    drive.add("export_again.csv", box, "text/csv", same_rows.encode())
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["status"] == "already_present"
    assert summary["overlaps"] == ["schwab_equity.csv"]
    assert len(rows(ledger, "source_documents")) == 1


def test_partial_overlap_names_the_earlier_file(setup):
    drive, ledger, box, record = setup
    drop(drive, box, "schwab_equity.csv")
    inbox.process(ledger, drive, "root")
    extra = ('"03/29/2001","Deposit","XYZ","RS","4","","","","","","","","","","","","","","","","","","","","",""\n'
             '"","","","","","","","","12/01/2000","G1","03/25/2001","$90.00","","","","","","","","","","","","","",""\n')  # leak-guard: allow (synthetic)
    newer = (FIXTURES / "schwab_equity.csv").read_text() + extra
    drive.add("export2.csv", box, "text/csv", newer.encode())
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["status"] == "ingested" and summary["overlaps"] == ["schwab_equity.csv"]


def test_network_error_on_one_file_does_not_stop_the_run(setup, monkeypatch):
    drive, ledger, box, record = setup
    bad = drop(drive, box, "pay_statement.txt")
    drop(drive, box, "accounts_seed.csv")

    def flaky(d, fid):
        if fid == bad:
            raise TimeoutError("The read operation timed out")
        return d.content[fid]

    monkeypatch.setattr(inbox, "download", flaky)
    out = {s["file"]: s for s in inbox.process(ledger, drive, "root")}
    assert out["pay_statement.txt"]["status"] == "error"
    assert "TimeoutError" in out["pay_statement.txt"]["reason"]
    assert out["accounts_seed.csv"]["status"] == "ingested"
    assert drive.names_in(box) == ["pay_statement.txt"]  # left for the next run

    monkeypatch.setattr(inbox, "download", lambda d, fid: d.content[fid])
    [again] = inbox.process(ledger, drive, "root")
    assert again["status"] == "ingested" and again["appended"] == {"paystubs": 18}


def test_tables_are_read_once_per_run(setup, monkeypatch):
    drive, ledger, box, record = setup
    for name in ("pay_statement.txt", "earnings_statement.txt"):
        drop(drive, box, name)
    reads = []
    real = sheets.read_table
    monkeypatch.setattr(sheets, "read_table", lambda s, t: reads.append(t.name) or real(s, t))
    inbox.process(ledger, drive, "root")
    assert reads.count("paystubs") == 1 and reads.count("source_documents") == 1
