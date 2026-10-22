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
    """Create missing tabs with headers and validation; check existing headers. Never edits data."""
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}
    present = [t for t in tables if t.name in existing]
    if present:  # one batched read for every header, to stay well inside the Sheets quota
        ranges = [f"'{t.name}'!1:1" for t in present]
        result = spreadsheet.values_batch_get(ranges)
        for t, value_range in zip(present, result.get("valueRanges", [])):
            header = (value_range.get("values") or [[]])[0]
            if tuple(header) != t.columns:
                raise SchemaMismatch(
                    f"{spreadsheet.title}/{t.name}: header {header} != {list(t.columns)}")
    created = []
    for t in tables:
        if t.name in existing:
            continue
        ws = spreadsheet.add_worksheet(title=t.name, rows=1000, cols=len(t.columns))
        ws.update(values=[list(t.columns)], range_name="A1")
        ws.freeze(rows=1)
        _add_validation(spreadsheet, ws, t)
        created.append(t.name)
    _drop_default_sheet(spreadsheet, existing, tables)
    return created


def _add_validation(spreadsheet, ws, t):
    requests = []
    for column, values in t.enums.items():
        index = t.columns.index(column)
        requests.append({"setDataValidation": {
            "range": {"sheetId": ws.id, "startRowIndex": 1,
                      "startColumnIndex": index, "endColumnIndex": index + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": v} for v in values]},
                     "strict": True, "showCustomUi": True},
        }})
    if requests:
        spreadsheet.batch_update({"requests": requests})


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
