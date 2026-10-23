"""Thin I/O over gspread: create declared tables, read them, append, replace.

Ledger tables are only ever appended to. `replace_table` refuses to touch a
spreadsheet unless it is the Reports spreadsheet, so a bug in compute cannot
overwrite stored facts.
"""

import pandas as pd

REPORTS_TITLE = "reports"
LEDGER_TITLE = "ledger"
UNFORMATTED = "UNFORMATTED_VALUE"


class SchemaMismatch(Exception):
    pass


def open_or_create(client, title, folder_id):
    import gspread

    try:
        return client.open(title, folder_id=folder_id), False
    except gspread.SpreadsheetNotFound:
        return client.create(title, folder_id=folder_id), True


def ensure_tables(spreadsheet, tables):
    """Create missing tabs with headers and validation; check existing headers. Never edits data.

    A tab whose header differs from the schema is an error, unless the tab has no
    data rows yet: then its header is rewritten, which is how a table that has
    never held data picks up a schema change.
    """
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}
    present = [t for t in tables if t.name in existing]
    migrate = []
    if present:  # one batched read for every header, to stay well inside the Sheets quota
        ranges = [f"'{t.name}'!1:2" for t in present]
        result = spreadsheet.values_batch_get(ranges)
        for t, value_range in zip(present, result.get("valueRanges", [])):
            values = value_range.get("values") or [[]]
            header = values[0]
            if tuple(header) == t.columns:
                continue
            if len(values) > 1 and any(str(c).strip() for c in values[1]):
                raise SchemaMismatch(
                    f"{spreadsheet.title}/{t.name}: header {header} != {list(t.columns)}")
            migrate.append((t, existing[t.name], len(header)))
    if migrate:
        _migrate_empty_tables(spreadsheet, migrate)
    missing = [t for t in tables if t.name not in existing]
    if missing:
        _create_tables(spreadsheet, missing)
    _drop_default_sheet(spreadsheet, existing, tables)
    return [t.name for t in missing]


def _migrate_empty_tables(spreadsheet, migrate):
    """Rewrite the header of tables that hold no data; two API calls (three with dropdowns)."""
    spreadsheet.batch_update({"requests": [
        {"updateSheetProperties": {
            "properties": {"sheetId": ws.id, "gridProperties": {
                "columnCount": max(len(t.columns), old_width), "frozenRowCount": 1}},
            "fields": "gridProperties.columnCount,gridProperties.frozenRowCount"}}
        for t, ws, old_width in migrate]})
    spreadsheet.values_batch_update({"valueInputOption": "RAW", "data": [
        {"range": f"'{t.name}'!A1",
         "values": [list(t.columns) + [""] * max(0, old_width - len(t.columns))]}
        for t, ws, old_width in migrate]})
    validations = [_validation(ws.id, t, column, values)
                   for t, ws, _ in migrate for column, values in t.enums.items()]
    if validations:
        spreadsheet.batch_update({"requests": validations})


def _create_tables(spreadsheet, tables):
    """Three API calls regardless of table count: add tabs, write headers, add dropdowns."""
    reply = spreadsheet.batch_update({"requests": [
        {"addSheet": {"properties": {"title": t.name, "gridProperties": {
            "rowCount": 1000, "columnCount": len(t.columns), "frozenRowCount": 1}}}}
        for t in tables]})
    sheet_ids = [r["addSheet"]["properties"]["sheetId"] for r in reply["replies"]]
    spreadsheet.values_batch_update({"valueInputOption": "RAW", "data": [
        {"range": f"'{t.name}'!A1", "values": [list(t.columns)]} for t in tables]})
    validations = [_validation(sheet_id, t, column, values)
                   for sheet_id, t in zip(sheet_ids, tables)
                   for column, values in t.enums.items()]
    if validations:
        spreadsheet.batch_update({"requests": validations})


def _validation(sheet_id, t, column, values):
    index = t.columns.index(column)
    return {"setDataValidation": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1,
                  "startColumnIndex": index, "endColumnIndex": index + 1},
        "rule": {"condition": {"type": "ONE_OF_LIST",
                               "values": [{"userEnteredValue": v} for v in values]},
                 "strict": True, "showCustomUi": True},
    }}


def _drop_default_sheet(spreadsheet, existing, tables):
    names = {t.name for t in tables}
    for title, ws in existing.items():
        if title in ("Sheet1",) and title not in names and not any(ws.get_all_values()):
            spreadsheet.del_worksheet(ws)


def read_table(spreadsheet, t):
    """Read a declared table as a DataFrame of raw (unformatted) values."""
    rows = spreadsheet.worksheet(t.name).get_values(value_render_option=UNFORMATTED)
    if not rows:
        return pd.DataFrame(columns=list(t.columns))
    header, body = rows[0], rows[1:]
    if tuple(header) != t.columns:
        raise SchemaMismatch(f"{t.name}: header {header} != {list(t.columns)}")
    width = len(header)
    return pd.DataFrame([r + [""] * (width - len(r)) for r in body], columns=header)


def append_rows(spreadsheet, t, df):
    """Append records in declared column order. The only write path into the ledger."""
    if df.empty:
        return 0
    values = df[list(t.columns)].astype(object).where(df.notna(), "").values.tolist()
    spreadsheet.worksheet(t.name).append_rows(values, value_input_option="RAW")
    return len(values)


def replace_table(spreadsheet, t, df):
    """Overwrite a derived table. Allowed only on the Reports spreadsheet."""
    if spreadsheet.title != REPORTS_TITLE:
        raise PermissionError(f"replace_table refused on '{spreadsheet.title}': only Reports is rebuilt")
    ws = spreadsheet.worksheet(t.name)
    ws.clear()
    values = [list(t.columns)] + df[list(t.columns)].astype(object).where(df.notna(), "").values.tolist()
    ws.resize(rows=max(len(values), 2), cols=len(t.columns))  # values.update does not grow the grid
    ws.update(values=values, range_name="A1", value_input_option="RAW")
    return len(values) - 1
