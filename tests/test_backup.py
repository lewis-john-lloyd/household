import io
import json
import zipfile

from household import backup


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self, num_retries=0):
        return self.result


class FakeFiles:
    def __init__(self):
        self.created = []

    def list(self, q, fields):
        return FakeRequest({"files": []})

    def create(self, body, fields, media_body=None):
        content = media_body.getbytes(0, media_body.size()) if media_body else None
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


def test_snapshot_is_one_zip_with_each_tab_and_a_manifest():
    ledger = FakeLedger({"accounts": [["account_id"]], "transactions": [["txn_id", "amount"], ["a", -1.5]]})
    drive = FakeDrive()
    manifest = backup.snapshot_ledger(ledger, drive, "root", "run-1")
    created = drive.files().created
    assert [name for name, _, _ in created] == ["backups", "ledger_run-1.zip"]
    bundle = zipfile.ZipFile(io.BytesIO(created[-1][2]))
    assert sorted(bundle.namelist()) == ["accounts.csv", "manifest.json", "transactions.csv"]
    assert bundle.read("transactions.csv").decode() == "txn_id,amount\na,-1.5\n"
    assert json.loads(bundle.read("manifest.json"))["run_id"] == "run-1"
    assert manifest["tables"]["transactions"]["rows"] == 1
