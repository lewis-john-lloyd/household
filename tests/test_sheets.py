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

    def update(self, values, range_name="A1", **_):
        assert range_name == "A1"
        self.rows = [list(r) for r in values] + self.rows[len(values):]

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
    sheets.replace_table(reports, t, pd.DataFrame([{"run_id": "r1", "computed_at": "x",
                                                    "stage": "compute", "detail": "d"}]))
    sheets.replace_table(reports, t, pd.DataFrame([{"run_id": "r2", "computed_at": "x",
                                                    "stage": "compute", "detail": "d"}]))
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
