"""Sheet I/O against an in-memory fake of the gspread objects used."""

import pandas as pd
import pytest

from household import schema, sheets


class FakeWorksheet:
    _next_id = 1

    def __init__(self, title, rows=None):
        self.title = title
        self.rows = rows or []
        self.id = FakeWorksheet._next_id
        FakeWorksheet._next_id += 1

    def update(self, values, range_name="A1", value_input_option="RAW", **_):
        """Write values from a cell in row 1 (A1, D1), noting which cells Sheets would read as typed in."""
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
        return [list(r) for r in self.rows]

    get_all_values = get_values

    def append_rows(self, values, **_):
        self.rows += [list(r) for r in values]

    def clear(self):
        self.rows = []

    def resize(self, rows=None, cols=None):
        pass


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
        return list(self.tabs.values())

    def worksheet(self, name):
        return self.tabs[name]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title)
        return self.tabs[title]

    def del_worksheet(self, ws):
        del self.tabs[ws.title]

    def batch_update(self, body):
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
        for item in body["data"]:
            name = item["range"].split("'")[1]
            self.tabs[name].rows = [list(r) for r in item["values"]] + self.tabs[name].rows[1:]

    def values_batch_get(self, ranges, params=None):
        out = []
        for r in ranges:
            name = r.split("'")[1]
            rows = self.tabs[name].rows if "!" not in r else self.tabs[name].rows[:2]
            out.append({"values": [list(x) for x in rows]} if rows else {})
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
