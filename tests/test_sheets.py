"""Sheet I/O against an in-memory fake of the gspread objects used."""

import re
from collections import Counter

import pandas as pd
import pytest

from household import schema, sheets

CALLS: Counter = Counter()  # every request the fakes were asked for, by what it was: a test clears it, then counts


def as_sheets_reads(rows):
    """Rows as the Sheets API gives them back: without the empty cells at a row's end, or the empty rows at
    the tab's. (A tab keeps its grid; a read gives only what is in it.)"""
    out = [list(r) for r in rows]
    for r in out:
        while r and r[-1] in ("", None):
            r.pop()
    while out and not out[-1]:
        out.pop()
    return out


class FakeWorksheet:
    _next_id = 1

    def __init__(self, title, rows=None):
        self.title = title
        self.rows = rows or []
        self.id = FakeWorksheet._next_id
        FakeWorksheet._next_id += 1

    def update(self, values, range_name="A1", value_input_option="RAW", **_):
        """Write values from a cell in row 1 (A1, D1), noting which cells Sheets would read as typed in."""
        CALLS["update"] += 1
        assert range_name[1:] == "1" and range_name[0].isalpha(), range_name
        col = ord(range_name[0].upper()) - ord("A")
        for i, row in enumerate(values):
            while len(self.rows) <= i:
                self.rows.append([])
            line = self.rows[i] + [""] * (col + len(row) - len(self.rows[i]))
            line[col : col + len(row)] = list(row)
            self.rows[i] = line
            if value_input_option == "USER_ENTERED":
                self.entered = getattr(self, "entered", set()) | {(i, col + j) for j in range(len(row))}

    def freeze(self, rows):
        pass

    def get_values(self, **_):
        CALLS["get_values"] += 1
        return as_sheets_reads(self.rows)

    get_all_values = get_values

    def append_rows(self, values, **_):
        CALLS["append_rows"] += 1
        self.rows += [list(r) for r in values]

    def clear(self):
        CALLS["clear"] += 1
        self.rows = []

    def resize(self, rows=None, cols=None):
        CALLS["resize"] += 1


def _value(cell):
    """What a cell written by appendCells reads back as."""
    v = cell.get("userEnteredValue", {})
    return v.get("stringValue", v.get("numberValue", v.get("boolValue", "")))


class FakeSpreadsheet:
    def __init__(self, title):
        self.title = title
        self.tabs = {"Sheet1": FakeWorksheet("Sheet1")}
        self.batch_updates = []

    def worksheets(self):
        CALLS["worksheets"] += 1
        return list(self.tabs.values())

    def worksheet(self, name):
        CALLS["worksheet"] += 1  # gspread asks Sheets for the list of tabs on every lookup
        return self.tabs[name]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title)
        return self.tabs[title]

    def del_worksheet(self, ws):
        del self.tabs[ws.title]

    def batch_update(self, body):
        CALLS["batch_update"] += 1
        # Sheets applies a batchUpdate atomically: a failure (fail_batch, or a sheet that isn't there) writes nothing
        if getattr(self, "fail_batch", None) and any("appendCells" in r for r in body["requests"]):
            error, self.fail_batch = self.fail_batch, None
            raise error
        by_id = {ws.id: ws for ws in self.tabs.values()}
        appends = [r["appendCells"] for r in body["requests"] if "appendCells" in r]
        for a in appends:
            if a["sheetId"] not in by_id:
                raise ValueError(f"no sheet {a['sheetId']}")
        for a in appends:
            by_id[a["sheetId"]].rows += [[_value(c) for c in row["values"]] for row in a["rows"]]
        for request in body["requests"]:  # a tab made smaller loses the rows and columns past its new size
            props = request.get("updateSheetProperties", {}).get("properties", {})
            grid = props.get("gridProperties", {})
            if props.get("sheetId") in by_id and "rowCount" in grid:
                ws = by_id[props["sheetId"]]
                ws.rows = [r[: grid.get("columnCount", len(r))] for r in ws.rows[: grid["rowCount"]]]
        self.batch_updates.append(body)
        replies = []
        for request in body["requests"]:
            if "addSheet" in request:
                ws = self.add_worksheet(request["addSheet"]["properties"]["title"], 0, 0)
                replies.append({"addSheet": {"properties": {"sheetId": ws.id}}})
            else:
                replies.append({})
        return {"replies": replies}

    def values_batch_update(self, body):
        """Each range is a tab and the row its values start at ('name'!A1): the cells written are replaced and
        the rest of the tab is left as it was, as Sheets does."""
        CALLS["values_batch_update"] += 1
        if getattr(self, "fail_values", None):
            error, self.fail_values = self.fail_values, None
            raise error
        for item in body["data"]:
            name = item["range"].split("'")[1]
            start = int(re.search(r"!A(\d+)$", item["range"]).group(1)) - 1
            rows = self.tabs[name].rows
            while len(rows) < start + len(item["values"]):
                rows.append([])
            for i, values in enumerate(item["values"]):
                rows[start + i] = list(values) + rows[start + i][len(values) :]

    def values_batch_get(self, ranges, params=None):
        CALLS["values_batch_get"] += 1
        out = []
        for r in ranges:
            name = r.split("'")[1]
            rows = as_sheets_reads(self.tabs[name].rows if "!" not in r else self.tabs[name].rows[:2])
            out.append({"values": rows} if rows else {})
        return {"valueRanges": out}


TXNS = schema.table(schema.LEDGER, "transactions")


def test_ensure_tables_creates_headers_validation_and_drops_default():
    ledger = FakeSpreadsheet("ledger")
    created = sheets.ensure_tables(ledger, schema.LEDGER)
    assert created == [t.name for t in schema.LEDGER]
    assert "Sheet1" not in ledger.tabs
    assert ledger.tabs["transactions"].rows == [list(TXNS.columns)]
    assert len(ledger.batch_updates) == 2  # one to add every tab, one for every dropdown
    assert any("setDataValidation" in r for r in ledger.batch_updates[1]["requests"])


def test_ensure_tables_is_idempotent_and_never_touches_data():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    ledger.tabs["transactions"].rows.append(["id1"] + [""] * (len(TXNS.columns) - 1))
    assert sheets.ensure_tables(ledger, schema.LEDGER) == []
    assert len(ledger.tabs["transactions"].rows) == 2


def test_header_drift_is_an_error():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    ledger.tabs["transactions"].rows[0][3] = "amt"
    ledger.tabs["transactions"].rows.append(["id1"] + [""] * (len(TXNS.columns) - 1))
    with pytest.raises(sheets.SchemaMismatch):
        sheets.ensure_tables(ledger, schema.LEDGER)


def test_append_then_read_round_trip():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    df = pd.DataFrame([{c: "" for c in TXNS.columns} | {"txn_id": "a", "amount": -1.5}])
    assert sheets.append_rows(ledger, TXNS, df) == 1
    back = sheets.read_table(ledger, TXNS)
    assert back.loc[0, "txn_id"] == "a" and back.loc[0, "amount"] == -1.5


def test_replace_table_refuses_the_ledger():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    with pytest.raises(PermissionError):
        sheets.replace_table(ledger, TXNS, pd.DataFrame(columns=list(TXNS.columns)))


def test_replace_table_rewrites_reports():
    reports = FakeSpreadsheet("reports")
    sheets.ensure_tables(reports, schema.REPORTS)
    t = schema.table(schema.REPORTS, "run_log")
    sheets.replace_table(
        reports, t, pd.DataFrame([{"run_id": "r1", "computed_at": "x", "stage": "compute", "detail": "d"}])
    )
    sheets.replace_table(
        reports, t, pd.DataFrame([{"run_id": "r2", "computed_at": "x", "stage": "compute", "detail": "d"}])
    )
    assert reports.tabs["run_log"].rows == [list(t.columns), ["r2", "x", "compute", "d"]]


def test_empty_table_with_old_header_is_migrated():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    ledger.tabs["equity_sales"].rows = [["lot_id", "sale_date", "shares", "proceeds", "doc_id"]]
    sheets.ensure_tables(ledger, schema.LEDGER)
    sales = schema.table(schema.LEDGER, "equity_sales")
    assert ledger.tabs["equity_sales"].rows[0] == list(sales.columns)


def test_table_with_data_and_old_header_is_an_error():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    ledger.tabs["equity_sales"].rows = [["lot_id", "sale_date"], ["x", "y"]]
    with pytest.raises(sheets.SchemaMismatch):
        sheets.ensure_tables(ledger, schema.LEDGER)


def test_a_reports_table_with_rows_takes_a_new_header_but_the_ledger_never_does():
    reports = FakeSpreadsheet(sheets.REPORTS_TITLE)
    old = schema.Table("projection", ("year", "scenario", "run_id"))
    sheets.ensure_tables(reports, (old,))
    sheets.replace_table(reports, old, pd.DataFrame([{"year": 2026, "scenario": "as is", "run_id": "r"}]))
    new = schema.Table("projection", ("year", "scenario", "extra", "run_id"))
    sheets.ensure_tables(reports, (new,))
    values = reports.worksheet("projection").get_values()
    assert values[0][:4] == ["year", "scenario", "extra", "run_id"]
    assert not any(any(str(c).strip() for c in row) for row in values[1:])

    ledger = FakeSpreadsheet(sheets.LEDGER_TITLE)
    sheets.ensure_tables(ledger, (old,))
    sheets.append_rows(ledger, old, pd.DataFrame([{"year": 2026, "scenario": "as is", "run_id": "r"}]))
    with pytest.raises(sheets.SchemaMismatch):
        sheets.ensure_tables(ledger, (new,))


def test_dropdowns_are_cleared_then_set_so_an_old_layout_leaves_none_behind():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    ledger.batch_updates.clear()
    sheets.ensure_tables(ledger, schema.LEDGER)
    requests = [
        r["setDataValidation"] for body in ledger.batch_updates for r in body["requests"] if "setDataValidation" in r
    ]
    ids = {name: tab.id for name, tab in ledger.tabs.items()}
    budget = [r for r in requests if r["range"]["sheetId"] == ids["budget_rules"]]
    assert "rule" not in budget[0] and "startColumnIndex" not in budget[0]["range"]  # a clear, first
    assert len(budget) == 1 + len(schema.table(schema.LEDGER, "budget_rules").enums)
    review = [r for r in requests if r["range"]["sheetId"] == ids["review"]]
    assert all("rule" in r for r in review)  # the review tab's answer dropdown is left alone


def test_a_header_padded_by_its_cell_format_still_matches():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    t = schema.table(schema.LEDGER, "budget_rules")
    ledger.tabs["budget_rules"].rows[0] = [f" {c} " if c == "amount" else c for c in t.columns]
    ledger.tabs["budget_rules"].rows.append(["food"] + [""] * (len(t.columns) - 1))
    assert sheets.ensure_tables(ledger, schema.LEDGER) == []
    assert list(sheets.read_table(ledger, t).columns) == list(t.columns)


def test_dates_typed_into_sheets_read_back_as_iso():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    t = schema.table(schema.LEDGER, "budget_rules")
    blank = [""] * (len(t.columns) - 2)
    ledger.tabs["budget_rules"].rows += [
        ["food", 45839] + blank,
        ["fuel", "7/1/2025"] + blank,
        ["rent", "2025-07-01"] + blank,
    ]
    assert sheets.read_table(ledger, t)["effective"].tolist() == ["2025-07-01"] * 3
    assert sheets.iso_date("not a date") == "not a date" and sheets.iso_date(True) is True


def test_the_review_tab_takes_a_new_layout_without_losing_typed_answers():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    t = schema.table(schema.LEDGER, "review")
    old = [c for c in t.columns if c != "split"]  # the layout before the split column
    ledger.tabs["review"].rows = [old, ["t1", "family"] + [""] * (len(old) - 2)]
    assert sheets.ensure_tables(ledger, schema.LEDGER) == []  # no SchemaMismatch, nothing cleared
    typed = sheets.read_table(ledger, t)
    assert list(typed.columns) == list(t.columns)
    assert typed.iloc[0][["key", "answer", "split"]].tolist() == ["t1", "family", ""]
    ledger.batch_updates.clear()
    sheets.rewrite_review(ledger, t, pd.DataFrame(columns=list(t.columns)), answers=["y", "food"])
    assert ledger.tabs["review"].rows[0] == list(t.columns)
    requests = [r["setDataValidation"] for r in ledger.batch_updates[0]["requests"]]
    assert "rule" not in requests[0]  # every old dropdown cleared first
    columns = {r["range"]["startColumnIndex"]: r["rule"].get("strict") for r in requests[1:]}
    assert columns == {t.columns.index("kind"): True, t.columns.index("answer"): False}


def test_append_tables_is_one_atomic_write_with_values_typed_as_raw():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    params = schema.table(schema.LEDGER, "parameters")
    sources = schema.table(schema.LEDGER, "source_documents")
    before = len(ledger.batch_updates)
    counts = sheets.append_tables(
        ledger,
        [
            (
                params,
                pd.DataFrame([{"jurisdiction": "household", "key": "target_refund", "value": "50"}]).reindex(
                    columns=params.columns, fill_value=""
                ),
            ),
            (
                sources,
                pd.DataFrame([{"doc_id": "d1", "kind": "changes", "original_name": "c.json", "precedence": 2}]).reindex(
                    columns=sources.columns, fill_value=""
                ),
            ),
            (TXNS, pd.DataFrame(columns=TXNS.columns)),
        ],
    )
    assert counts == {"parameters": 1, "source_documents": 1, "transactions": 0}
    assert len(ledger.batch_updates) == before + 1
    assert ledger.worksheet("parameters").rows[1][: list(params.columns).index("value") + 1][-1] == "50"
    row = ledger.worksheet("source_documents").rows[1]
    assert row[0] == "d1" and row[list(sources.columns).index("precedence")] == 2
    assert row[list(sources.columns).index("sha256")] == ""


def test_a_failed_append_tables_writes_nothing():
    ledger = FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    ledger.fail_batch = RuntimeError("503: the service is unavailable")
    with pytest.raises(RuntimeError):
        sheets.append_tables(
            ledger,
            [
                (
                    schema.table(schema.LEDGER, "parameters"),
                    pd.DataFrame([{"jurisdiction": "household", "key": "k", "value": "v"}]).reindex(
                        columns=schema.table(schema.LEDGER, "parameters").columns, fill_value=""
                    ),
                ),
                (
                    schema.table(schema.LEDGER, "source_documents"),
                    pd.DataFrame([{"doc_id": "d1"}]).reindex(
                        columns=schema.table(schema.LEDGER, "source_documents").columns, fill_value=""
                    ),
                ),
            ],
        )
    assert len(ledger.worksheet("parameters").rows) == 1 and len(ledger.worksheet("source_documents").rows) == 1


def test_cells_are_typed_as_a_raw_append_would_write_them():
    import numpy as np

    assert sheets._cell("") == {} and sheets._cell(None) == {}
    assert sheets._cell(True) == {"userEnteredValue": {"boolValue": True}}
    assert sheets._cell(np.int64(5)) == {"userEnteredValue": {"numberValue": 5}}
    assert sheets._cell(np.float64(2.5)) == {"userEnteredValue": {"numberValue": 2.5}}
    assert sheets._cell("500.00") == {"userEnteredValue": {"stringValue": "500.00"}}  # text stays text
    assert sheets._cell("=1+1") == {"userEnteredValue": {"stringValue": "=1+1"}}  # never a formula


# --- a Book: tabs read once, Reports written together ---------------------------------------------------------


def book(title="reports", tables=schema.REPORTS):
    raw = FakeSpreadsheet(title)
    sheets.ensure_tables(raw, tables)
    return raw, sheets.Book(raw)


RUN_LOG, RUN_NOTES = schema.table(schema.REPORTS, "run_log"), schema.table(schema.REPORTS, "run_notes")


def test_a_book_reads_a_tab_once_and_again_only_after_it_is_written():
    raw, ledger = book("ledger", schema.LEDGER)
    CALLS.clear()
    sheets.read_table(ledger, TXNS)
    sheets.read_table(ledger, TXNS)
    assert CALLS["values_batch_get"] == 1 and CALLS["worksheet"] == 0  # no listing of the tabs per read either
    row = pd.DataFrame([{c: "x" for c in TXNS.columns}])
    sheets.append_tables(ledger, [(TXNS, row)])
    accounts = schema.table(schema.LEDGER, "accounts")
    sheets.read_table(ledger, accounts)  # a tab not held: asked for along with the one written since it was read
    assert CALLS["values_batch_get"] == 2
    assert len(sheets.read_table(ledger, TXNS)) == 1 and CALLS["values_batch_get"] == 2


def test_a_book_reads_every_tab_in_one_call_and_the_tabs_people_and_sheets_fill_in_every_time():
    raw, ledger = book("ledger", schema.LEDGER)
    CALLS.clear()
    ledger.prefetch()
    for t in schema.LEDGER:
        if t.name not in sheets.VOLATILE:
            sheets.read_table(ledger, t)
    assert CALLS["values_batch_get"] == 1
    review = schema.table(schema.LEDGER, "review")
    assert sheets.read_table(ledger, review).empty
    raw.tabs["review"].rows.append(["QUILL & INK"] + [""] * (len(review.columns) - 1))  # typed while a run is on
    assert len(sheets.read_table(ledger, review)) == 1


def test_reports_tables_replaced_inside_batched_are_written_together_in_three_calls():
    raw, reports = book()
    one = pd.DataFrame([{"run_id": "r1", "computed_at": "t", "stage": "ingest", "detail": "{}"}])
    notes = pd.DataFrame(
        [
            {"run_id": "r1", "stage": "ingest", "at": "t", "n": n, "message": f"line {n}", "level": "INFO"}
            for n in range(3)
        ]
    )
    raw.tabs["run_notes"].rows += [["old"] * len(RUN_NOTES.columns) for _ in range(9)]  # a longer table before
    CALLS.clear()
    with sheets.batched(reports):
        sheets.replace_table(reports, RUN_LOG, one)
        sheets.replace_table(reports, RUN_NOTES, notes)
        assert raw.tabs["run_log"].rows == [list(RUN_LOG.columns)]  # nothing written yet
        assert sheets.read_table(reports, RUN_NOTES)["n"].tolist() == [0, 1, 2]  # but read as it will be
    # the tabs listed once, every tab sized in one call, every value in one more
    assert {k: v for k, v in CALLS.items() if v} == {"worksheets": 1, "batch_update": 1, "values_batch_update": 1}
    assert raw.tabs["run_log"].rows[1][:3] == ["r1", "t", "ingest"]
    assert len(raw.tabs["run_notes"].rows) == 4  # the nine old rows are gone: the tab was sized to the table
    assert sheets.read_table(reports, RUN_NOTES)["message"].tolist() == ["line 0", "line 1", "line 2"]


def test_an_error_inside_batched_writes_nothing():
    raw, reports = book()
    before = [list(r) for r in raw.tabs["run_log"].rows]
    with pytest.raises(ValueError, match="went wrong"):
        with sheets.batched(reports):
            sheets.replace_table(reports, RUN_LOG, pd.DataFrame([{c: "x" for c in RUN_LOG.columns}]))
            raise ValueError("went wrong")
    assert raw.tabs["run_log"].rows == before and not reports.deferred
    sheets.replace_table(reports, RUN_LOG, pd.DataFrame([{c: "y" for c in RUN_LOG.columns}]))  # at once again
    assert raw.tabs["run_log"].rows[1] == ["y"] * len(RUN_LOG.columns)


def test_a_table_bigger_than_a_request_is_written_in_runs_of_rows(monkeypatch):
    raw, reports = book()
    monkeypatch.setattr(sheets, "WRITE_BYTES", 400)
    notes = pd.DataFrame(
        [
            {"run_id": "r1", "stage": "ingest", "at": "t", "n": n, "message": "m" * 40, "level": "INFO"}
            for n in range(30)
        ]
    )
    CALLS.clear()
    with sheets.batched(reports):
        sheets.replace_table(reports, RUN_NOTES, notes)
        sheets.replace_table(reports, RUN_LOG, pd.DataFrame([{c: "x" for c in RUN_LOG.columns}]))
    assert CALLS["values_batch_update"] > 3
    got = sheets.read_table(reports, RUN_NOTES)
    assert got["n"].tolist() == list(range(30)) and (got["message"] == "m" * 40).all()
    assert raw.tabs["run_log"].rows[1] == ["x"] * len(RUN_LOG.columns)


def test_a_write_that_fails_part_way_says_reports_is_written_in_part():
    raw, reports = book()
    raw.fail_values = TimeoutError("no answer")
    with pytest.raises(RuntimeError, match="written in part .* run again"):
        with sheets.batched(reports):
            sheets.replace_table(reports, RUN_LOG, pd.DataFrame([{c: "x" for c in RUN_LOG.columns}]))


def test_a_spreadsheet_given_as_it_is_reads_and_writes_as_before():
    raw = FakeSpreadsheet("reports")
    sheets.ensure_tables(raw, schema.REPORTS)
    CALLS.clear()
    with sheets.batched(raw):
        sheets.replace_table(raw, RUN_LOG, pd.DataFrame([{c: "x" for c in RUN_LOG.columns}]))
        assert raw.tabs["run_log"].rows[1] == ["x"] * len(RUN_LOG.columns)  # written at once
    assert (CALLS["clear"], CALLS["resize"], CALLS["update"]) == (1, 1, 1)
    assert sheets.calls(raw) == 0


def test_a_table_that_empties_leaves_no_row_behind():
    reports = FakeSpreadsheet("reports")
    t = schema.table(schema.REPORTS, "possible_duplicates")
    reports.add_worksheet(t.name, 2, len(t.columns))
    book = sheets.Book(reports)
    full = pd.DataFrame([{c: f"{c}-{i}" for c in t.columns} for i in range(3)], columns=t.columns)
    sheets.replace_table(book, t, full)
    assert len(sheets.read_table(book, t)) == 3
    sheets.replace_table(book, t, full.iloc[:0])
    assert sheets.read_table(book, t).empty
    assert sheets.read_table(reports, t).empty  # as Sheets itself holds it
