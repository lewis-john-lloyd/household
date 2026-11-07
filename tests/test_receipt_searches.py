"""The receipt exporter's searches: plain senders and subject phrases in the settings, the search built from them,
and the file compute writes them into for the exporter (exporter/Code.js builds the same search)."""

import json
import re

import pandas as pd
import pytest

from household import inbox, settings


def params(**values):
    return pd.DataFrame([{"year": "", "jurisdiction": "household", "key": k.replace("__", ":"), "value": v}
                         for k, v in values.items()])


def test_a_search_is_its_senders_and_one_of_its_subjects_and_syntax_is_left_out():
    p = params(receipt_sender__co_op="receipts@example.coop",
               receipt_sender__books="a@example.com, b@example.com",
               receipt_subject__books="Thank you for shopping | Billing Summary",
               receipt_sender__wide="x@example.com OR in:anywhere",
               receipt_sender__sneaky="y@example.com", receipt_subject__sneaky='x") OR (in:anywhere',
               receipt_sender__gone="")
    found = settings.receipt_searches(p).set_index("store")
    assert list(found.index) == ["books", "co_op"]
    assert found.at["co_op", "search"] == "from:(receipts@example.coop)"
    assert found.at["books", "search"] == ('from:(a@example.com OR b@example.com) '
                                           'subject:("Thank you for shopping" OR "Billing Summary")')
    assert [(k, problem) for k, _, problem in settings.check(p)] == [
        ("receipt_sender:wide", "has 'x@example.com OR in:anywhere', which is not an email address"),
        ("receipt_subject:sneaky", "has 'x\") OR (in:anywhere': letters, digits, spaces and & ' . , ! # - only")]
    about = settings.known(pd.DataFrame(columns=["key", "value", "source"])).set_index("key")["about"]
    assert "receipt_sender" in about and "receipt_subject" in about


class Files:
    def __init__(self, items):
        self.items, self.updates = items, []

    def list(self, q, fields, pageSize=None):
        if "properties has" in q:
            key, value = re.search(r"key='([^']+)' and value='([^']+)'", q).groups()
            found = [f for f in self.items if (f.get("properties") or {}).get(key) == value and not f.get("trashed")]
        else:
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            found = [f for f in self.items if f["parent"] == parent]
        return Done({"files": [dict(f) for f in found]})

    def update(self, fileId, body, fields):
        self.updates.append((fileId, body))
        return Done({"id": fileId})


class Done:
    def __init__(self, result):
        self.result = result

    def execute(self, num_retries=0):
        return self.result


class Drive:
    def __init__(self, items):
        self._files = Files(items)

    def files(self):
        return self._files


MARK = {"household": "receipt_searches"}


def test_ingest_passes_over_the_searches_file_and_compute_writes_into_it():
    drive = Drive([{"id": "r1", "name": "receipts_1.txt", "parent": "box", "mimeType": "text/plain"},
                   {"id": "s1", "name": "receipt searches.json", "parent": "box", "mimeType": "application/json",
                    "properties": MARK},
                   {"id": "s0", "name": "receipt searches.json", "parent": "box", "properties": MARK, "trashed": True}])
    assert [f["id"] for f in inbox.list_files(drive, "box")] == ["r1"]
    searches = settings.receipt_searches(params(receipt_sender__books="a@example.com, b@example.com",
                                                receipt_subject__books="Thank you"))
    assert inbox.write_receipt_searches(drive, searches) == 1
    (file_id, body), = drive.files().updates
    assert file_id == "s1" and json.loads(body["description"]) == {
        "version": 1, "searches": [{"store": "books", "senders": ["a@example.com", "b@example.com"],
                                    "subjects": ["Thank you"]}]}


def test_no_searches_file_yet_writes_nothing_and_too_many_searches_are_refused_not_cut():
    assert inbox.write_receipt_searches(Drive([]), settings.receipt_searches(params(receipt_sender__a="a@example.com"))) == 0
    many = pd.DataFrame([{"store": f"s{i}", "senders": ", ".join(f"someone{j}@example.com" for j in range(10)),
                          "subjects": ""} for i in range(20)])
    with pytest.raises(ValueError, match="over 4000"):
        inbox.write_receipt_searches(Drive([]), many)
