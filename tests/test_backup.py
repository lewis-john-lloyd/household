import json

from household import backup


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeFiles:
    def __init__(self):
        self.created = []

    def list(self, q, fields):
        return FakeRequest({"files": []})

    def create(self, body, fields, media_body=None):
        content = media_body.getbytes(0, media_body.size()).decode() if media_body else None
        self.created.append((body["name"], body.get("mimeType"), content))
        return FakeRequest({"id": f"id{len(self.created)}"})


class FakeDrive:
    def __init__(self):
        self._files = FakeFiles()

    def files(self):
        return self._files


class FakeWs:
    def __init__(self, title):
        self.title = title


class FakeLedger:
    def __init__(self, tabs):
        self.tabs = tabs

    def worksheets(self):
        return [FakeWs(t) for t in self.tabs]

    def values_batch_get(self, ranges, params=None):
        assert params == {"valueRenderOption": "UNFORMATTED_VALUE"}
        return {"valueRanges": [{"values": self.tabs[r.strip("'")]} for r in ranges]}


def test_snapshot_writes_each_tab_and_a_manifest():
    ledger = FakeLedger({"accounts": [["account_id"]], "transactions": [["txn_id", "amount"], ["a", -1.5]]})
    drive = FakeDrive()
    manifest = backup.snapshot_ledger(ledger, drive, "root", "run-1")
    names = [name for name, _, _ in drive.files().created]
    assert names == ["backups", "run-1", "accounts.csv", "transactions.csv", "manifest.json"]
    assert manifest["tables"]["transactions"]["rows"] == 1
    assert dict((n, c) for n, _, c in drive.files().created)["transactions.csv"] == "txn_id,amount\na,-1.5\n"
    assert json.loads(drive.files().created[-1][2])["run_id"] == "run-1"
