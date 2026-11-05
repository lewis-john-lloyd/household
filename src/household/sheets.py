"""Thin I/O over gspread: create declared tables, read them, append, replace.

Ledger tables are only ever appended to. `replace_table` refuses to touch a
spreadsheet unless it is the Reports spreadsheet, so a bug in compute cannot
overwrite stored facts.
"""

import re
from datetime import date, timedelta

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
    never held data picks up a schema change. On the Reports spreadsheet every
    table is derived and rebuilt each run, so a changed header clears that tab
    and takes the new one; ledger data is never cleared. The ledger's review tab is
    a work surface rewritten by every review: its header follows on that rewrite,
    and it is read by column name until then (read_table).
    """
    existing = {ws.title: ws for ws in spreadsheet.worksheets()}
    present = [t for t in tables if t.name in existing]
    migrate = []
    if present:  # one batched read for every header, to stay well inside the Sheets quota
        ranges = [f"'{t.name}'!1:2" for t in present]
        # unformatted, so a column's number format (accounting pads text with spaces) can't change the header
        result = spreadsheet.values_batch_get(ranges, params={"valueRenderOption": "UNFORMATTED_VALUE"})
        for t, value_range in zip(present, result.get("valueRanges", [])):
            values = value_range.get("values") or [[]]
            header = [str(h).strip() for h in values[0]]
            if tuple(header) == t.columns or t.name == REVIEW:
                continue
            if len(values) > 1 and any(str(c).strip() for c in values[1]):
                if spreadsheet.title != REPORTS_TITLE:
                    raise SchemaMismatch(
                        f"{spreadsheet.title}/{t.name}: header {header} != {list(t.columns)}")
                existing[t.name].clear()  # derived rows; this run rebuilds them
            migrate.append((t, existing[t.name], len(header)))
    if migrate:
        _migrate_empty_tables(spreadsheet, migrate)
    refresh = [r for t in present if t not in [m[0] for m in migrate]
               for r in _dropdowns(existing[t.name].id, t)]
    if refresh:  # keeps dropdowns in step with the schema, stale ones cleared; one call
        spreadsheet.batch_update({"requests": refresh})
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
    validations = [r for t, ws, _ in migrate for r in _dropdowns(ws.id, t)]
    if validations:
        spreadsheet.batch_update({"requests": validations})


def _dropdowns(sheet_id, t):
    """Requests that clear a tab's dropdowns below the header, then set the schema's.

    Clearing first drops a dropdown a column kept from an older layout of the tab (a "per" list
    left on the column that now holds categories). The review tab keeps its own: its answer
    dropdown is set when the tab is rewritten.
    """
    clear = [] if t.name == REVIEW else [{"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": 1}}}]
    return clear + [_validation(sheet_id, t, column, values) for column, values in t.enums.items()]


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


DATE_COLUMNS = {"date", "effective", "originated", "as_of", "period_start", "period_end", "covers_from", "covers_to"}
SHEETS_EPOCH = date(1899, 12, 30)  # day 0 of a Sheets date serial
US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def is_date_column(name):
    return name in DATE_COLUMNS or name.endswith("_date")


def iso_date(value):
    """A date cell as YYYY-MM-DD: a date typed into Sheets reads back as a serial day count (45839) or,
    formatted, as 7/1/2025; text already in ISO form, or anything else, is left as it is."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= value < 200000:
        return (SHEETS_EPOCH + timedelta(days=int(value))).isoformat()
    m = US_DATE.match(str(value).strip())
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2))).isoformat()
    return value


def read_table(spreadsheet, t):
    """Read a declared table as a DataFrame of raw (unformatted) values, its date columns as YYYY-MM-DD.

    The review tab is read by column name (a column it doesn't have yet reads as blank), so
    answers typed under an older layout still count; every other table must match the schema.
    """
    rows = spreadsheet.worksheet(t.name).get_values(value_render_option=UNFORMATTED)
    if not rows:
        return pd.DataFrame(columns=list(t.columns))
    header, body = [str(h).strip() for h in rows[0]], rows[1:]
    if tuple(header) != t.columns and t.name != REVIEW:
        raise SchemaMismatch(f"{t.name}: header {header} != {list(t.columns)}")
    width = len(header)
    df = pd.DataFrame([r + [""] * (width - len(r)) for r in body], columns=header)
    if tuple(header) != t.columns:
        df = df.loc[:, ~df.columns.duplicated()].reindex(columns=list(t.columns), fill_value="")
    for column in [c for c in header if is_date_column(c)]:
        df[column] = df[column].map(iso_date)
    return df


def append_rows(spreadsheet, t, df):
    """Append records in declared column order. The only write path into the ledger."""
    if df.empty:
        return 0
    values = df[list(t.columns)].astype(object).where(df.notna(), "").values.tolist()
    spreadsheet.worksheet(t.name).append_rows(values, value_input_option="RAW")
    return len(values)


def _cell(value):
    """One cell for appendCells, typed as values.append with RAW would write it."""
    if value is None or (isinstance(value, str) and value == ""):
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def append_tables(spreadsheet, tables):
    """Append records to several tables as one write: every table's rows land, or none do.

    tables is a list of (Table, DataFrame). One spreadsheets.batchUpdate of appendCells requests, which
    Sheets applies atomically, so a file's rows and its source_documents row can't be split by a failure
    between them (an ingest that writes the rows but not the record of them would read the file again).
    Returns rows appended by table name.
    """
    requests, counts = [], {}
    for t, df in tables:
        counts[t.name] = 0 if df is None else len(df)
        if df is None or df.empty:
            continue
        values = df[list(t.columns)].astype(object).where(df.notna(), "").values.tolist()
        requests.append({"appendCells": {"sheetId": spreadsheet.worksheet(t.name).id, "fields": "userEnteredValue",
                                         "rows": [{"values": [_cell(v) for v in row]} for row in values]}})
    if requests:
        spreadsheet.batch_update({"requests": requests})
    return counts


REVIEW = "review"


def rewrite_review(spreadsheet, t, df, answers=()):
    """Rewrite the ledger's review tab: the one ledger tab that is a work surface, not a record.

    Its answers are read into the ledger before every rewrite (see engine.review),
    so rewriting loses nothing. answers fill the answer column's dropdown; typing
    something else is allowed and is reported back in note. The split column has
    no dropdown, so a split can be typed. Every dropdown below the header is
    cleared first, so one left by an older layout of the tab does not linger.
    """
    if t.name != REVIEW:
        raise PermissionError(f"rewrite_review refused on '{t.name}': only the review tab is rewritten")
    ws = spreadsheet.worksheet(t.name)
    ws.clear()
    values = [list(t.columns)] + df[list(t.columns)].astype(object).where(df.notna(), "").values.tolist()
    ws.resize(rows=max(len(values), 2), cols=len(t.columns))
    ws.update(values=values, range_name="A1", value_input_option="RAW")
    requests = [{"setDataValidation": {"range": {"sheetId": ws.id, "startRowIndex": 1}}}]
    requests += [_validation(ws.id, t, column, values) for column, values in t.enums.items()]
    if answers:
        rule = _validation(ws.id, t, "answer", list(answers))
        rule["setDataValidation"]["rule"]["strict"] = False
        requests.append(rule)
    spreadsheet.batch_update({"requests": requests})
    return len(values) - 1


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
