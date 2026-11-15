"""Thin I/O over gspread: create declared tables, read them, append, replace.

Ledger tables are only ever appended to. `replace_table` refuses to touch a
spreadsheet unless it is the Reports spreadsheet, so a bug in compute cannot
overwrite stored facts.

A stage opens each spreadsheet as a Book (ledgerio.bootstrap): the same spreadsheet, with what was read from
it kept for the rest of the run and Reports written in a few calls rather than several per table. Every
function here takes a Book or the spreadsheet itself; given the spreadsheet, each read and write is its own
call, as it always was.
"""

import hashlib
import json
import re
from datetime import date, timedelta
from typing import Any, Iterator

import pandas as pd

from .kinds import Frame, Spreadsheet
from .schema import Table

REPORTS_TITLE = "reports"
LEDGER_TITLE = "ledger"
UNFORMATTED = "UNFORMATTED_VALUE"
REVIEW = "review"
PRICE_REQUESTS = "price_requests"
# tabs read from Sheets every time they are asked for: a person types answers into review, and Sheets itself
# fills in price_requests, so neither may be answered from what was read earlier in the run
VOLATILE = frozenset({REVIEW, PRICE_REQUESTS})
WRITE_BYTES = 2_000_000  # Google suggests keeping a request under 2 MB


class SchemaMismatch(Exception):
    pass


class Book:
    """A spreadsheet as one run uses it: a tab is read once and remembered, a write through this module forgets
    what it changed (the next read asks Sheets again, along with every other tab written since), and Reports
    tables can be queued and written together (batched).

    It stands in for the spreadsheet in this module: title, worksheet and worksheets answer from what it
    knows, and the batch calls are the spreadsheet's own. calls counts the requests made through it, for the
    run's log.
    """

    def __init__(self, spreadsheet: Spreadsheet):
        self.spreadsheet = spreadsheet
        self.calls = 0
        self.deferred = False
        self._rows: dict[str, list[list[Any]]] = {}
        self._written: set[str] = set()  # tabs read, then written: read again with the next tab asked for
        self._sheets: dict[str, Any] | None = None
        self._queue: dict[str, tuple[Table, list[list[Any]]]] = {}

    @property
    def title(self) -> str:
        return self.spreadsheet.title

    def batch_update(self, body: dict[str, Any]) -> Any:
        self.calls += 1
        return self.spreadsheet.batch_update(body)

    def values_batch_update(self, body: dict[str, Any]) -> Any:
        self.calls += 1
        return self.spreadsheet.values_batch_update(body)

    def values_batch_get(self, ranges: list[str], params: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        return self.spreadsheet.values_batch_get(ranges, params=params)

    def worksheets(self) -> list[Any]:
        if self._sheets is None:
            self.calls += 1
            self._sheets = {ws.title: ws for ws in self.spreadsheet.worksheets()}
        return list(self._sheets.values())

    def worksheet(self, name: str) -> Any:
        """The tab, from one listing of the spreadsheet's tabs (gspread asks for the listing on every lookup)."""
        if name not in {ws.title for ws in self.worksheets()}:
            self._sheets = None  # a tab made since the listing
            self.worksheets()
        assert self._sheets is not None
        if name not in self._sheets:
            return self.spreadsheet.worksheet(name)  # raises gspread's own error for a tab that is not there
        return self._sheets[name]

    def _fetch(self, names: list[str]) -> dict[str, list[list[Any]]]:
        got = self.values_batch_get([f"'{n}'" for n in names], params={"valueRenderOption": UNFORMATTED})
        return {n: vr.get("values", []) for n, vr in zip(names, got.get("valueRanges", []))}

    def rows(self, name: str) -> list[list[Any]]:
        """A tab's rows as Sheets holds them (unformatted). Not to be changed: they are what the next read gets."""
        queued = self._queue.get(name)
        if queued is not None:  # replaced in this run and not yet written: what it will hold
            return _as_read(queued[1])
        if name in VOLATILE:
            return self._fetch([name])[name]
        if name not in self._rows:
            wanted = [name] + sorted(self._written - {name})
            self._rows.update(self._fetch(wanted))
            self._written -= set(wanted)
        return self._rows[name]

    def prefetch(self, names: list[str] | None = None) -> None:
        """Read every tab named (every tab there is, when none is) that is not already held, in one call."""
        names = [n for n in (names or [ws.title for ws in self.worksheets()]) if n not in VOLATILE]
        wanted = [n for n in names if n not in self._rows and n not in self._queue]
        if wanted:
            self._rows.update(self._fetch(wanted))
            self._written -= set(wanted)

    def snapshot(self) -> dict[str, list[list[Any]]]:
        """Every tab as it is now, in one call, for the backup; what it read is kept for the rest of the run."""
        got = self._fetch([ws.title for ws in self.worksheets()])
        self._rows.update({n: rows for n, rows in got.items() if n not in VOLATILE})
        self._written.clear()
        return got

    def digest(self, names: list[str]) -> str:
        """A fingerprint of the tabs named, as held: the same rows give the same one."""
        self.prefetch(names)
        h = hashlib.sha256()
        for n in names:
            h.update(json.dumps([n, self.rows(n)], default=str, separators=(",", ":")).encode())
        return h.hexdigest()[:16]

    def touch(self, *names: str) -> None:
        """These tabs were written: forget what was read of them."""
        for n in names:
            if self._rows.pop(n, None) is not None:
                self._written.add(n)

    def queue(self, t: Table, values: list[list[Any]]) -> None:
        self._queue[t.name] = (t, values)

    def write(self, tables: list[tuple[Table, list[list[Any]]]]) -> int:
        """Replace each table's tab with its values: one call sizes every tab to its table, then the values go
        in as few calls as keep each under WRITE_BYTES. A tab sized to its table and written in full holds
        nothing else, so nothing has to be cleared first. Returns the calls made."""
        if not tables:
            return 0
        # a tab keeps at least two rows, so a table with no rows writes a blank second one over what was there
        tables = [(t, values if len(values) > 1 else values + [[""] * len(t.columns)]) for t, values in tables]
        before = self.calls
        ids = {ws.title: ws.id for ws in self.worksheets()}
        self.batch_update(
            {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": ids[t.name],
                                "gridProperties": {"rowCount": max(len(values), 2), "columnCount": len(t.columns)},
                            },
                            "fields": "gridProperties.rowCount,gridProperties.columnCount",
                        }
                    }
                    for t, values in tables
                ]
            }
        )
        done: list[str] = []
        try:
            for chunk in _chunks(tables):
                self.values_batch_update({"valueInputOption": "RAW", "data": chunk})
                done += [item["range"] for item in chunk]
        except Exception as e:
            raise RuntimeError(
                f"{self.title}: written in part ({len(done)} of its ranges went in before "
                f"{type(e).__name__}: {e}); run again to write the rest"
            ) from e
        finally:
            self.touch(*[t.name for t, _ in tables])
        return self.calls - before

    def flush(self) -> int:
        """Write every queued table (write). Returns the calls made."""
        tables, self._queue = list(self._queue.values()), {}
        return self.write(tables)

    def discard(self) -> None:
        self._queue = {}


def _as_read(values: list[list[Any]]) -> list[list[Any]]:
    """Values about to be written, as Sheets would give them back: a whole number comes back without its
    decimal point."""
    return [[int(v) if isinstance(v, float) and v.is_integer() else v for v in row] for row in values]


def _chunks(tables: list[tuple[Table, list[list[Any]]]]) -> Iterator[list[dict[str, Any]]]:
    """values.batchUpdate bodies' data, each under WRITE_BYTES: whole tables together, and a table bigger than
    that in runs of rows."""
    chunk: list[dict[str, Any]] = []
    size = 0
    for t, values in tables:
        start = 0
        while start < len(values):
            end, took = start, 0
            while end < len(values):
                row = len(json.dumps(values[end], default=str)) + 1
                if end > start and size + took + row > WRITE_BYTES:
                    break
                took += row
                end += 1
            if chunk and size + took > WRITE_BYTES:
                yield chunk
                chunk, size = [], 0
                continue  # measure this table again against an empty request
            chunk.append({"range": f"'{t.name}'!A{start + 1}", "values": values[start:end]})
            size += took
            start = end
    if chunk:
        yield chunk


class batched:
    """with batched(reports): inside, replace_table on a Book queues its table; leaving writes them all
    together (Book.flush), and leaving on an error writes none, so Reports stays as the last full run left it.
    Given the spreadsheet itself, every table is written as it is replaced."""

    def __init__(self, spreadsheet: Spreadsheet):
        self.book = spreadsheet if isinstance(spreadsheet, Book) else None

    def __enter__(self) -> "batched":
        if self.book is not None:
            self.book.deferred = True
        return self

    def __exit__(self, kind: Any, error: Any, trace: Any) -> None:
        if self.book is None:
            return
        self.book.deferred = False
        if kind is None:
            self.book.flush()
        else:
            self.book.discard()


def _wrote(spreadsheet: Spreadsheet, *names: str) -> None:
    if isinstance(spreadsheet, Book):
        spreadsheet.touch(*names)


def prefetch(spreadsheet: Spreadsheet, names: list[str] | None = None) -> None:
    """Read the tabs named (all of them, when none is) in one call, if this is a Book; otherwise each is read
    when it is asked for."""
    if isinstance(spreadsheet, Book):
        spreadsheet.prefetch(names)


def calls(spreadsheet: Spreadsheet) -> int:
    """How many requests went to Sheets through this Book so far (0 for a spreadsheet opened without one)."""
    return spreadsheet.calls if isinstance(spreadsheet, Book) else 0


def open_or_create(client, title, folder_id):
    import gspread

    try:
        return client.open(title, folder_id=folder_id), False
    except gspread.SpreadsheetNotFound:
        return client.create(title, folder_id=folder_id), True


def ensure_tables(spreadsheet: Spreadsheet, tables: tuple[Table, ...] | list[Table]) -> list[str]:
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
                    raise SchemaMismatch(f"{spreadsheet.title}/{t.name}: header {header} != {list(t.columns)}")
                existing[t.name].clear()  # derived rows; this run rebuilds them
            migrate.append((t, existing[t.name], len(header)))
    if migrate:
        _migrate_empty_tables(spreadsheet, migrate)
    refresh = [r for t in present if t not in [m[0] for m in migrate] for r in _dropdowns(existing[t.name].id, t)]
    if refresh:  # keeps dropdowns in step with the schema, stale ones cleared; one call
        spreadsheet.batch_update({"requests": refresh})
    missing = [t for t in tables if t.name not in existing]
    if missing:
        _create_tables(spreadsheet, missing)
    _drop_default_sheet(spreadsheet, existing, tables)
    return [t.name for t in missing]


def _migrate_empty_tables(spreadsheet, migrate):
    """Rewrite the header of tables that hold no data; two API calls (three with dropdowns)."""
    spreadsheet.batch_update(
        {
            "requests": [
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": ws.id,
                            "gridProperties": {"columnCount": max(len(t.columns), old_width), "frozenRowCount": 1},
                        },
                        "fields": "gridProperties.columnCount,gridProperties.frozenRowCount",
                    }
                }
                for t, ws, old_width in migrate
            ]
        }
    )
    spreadsheet.values_batch_update(
        {
            "valueInputOption": "RAW",
            "data": [
                {"range": f"'{t.name}'!A1", "values": [list(t.columns) + [""] * max(0, old_width - len(t.columns))]}
                for t, ws, old_width in migrate
            ],
        }
    )
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
    reply = spreadsheet.batch_update(
        {
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": t.name,
                            "gridProperties": {"rowCount": 1000, "columnCount": len(t.columns), "frozenRowCount": 1},
                        }
                    }
                }
                for t in tables
            ]
        }
    )
    sheet_ids = [r["addSheet"]["properties"]["sheetId"] for r in reply["replies"]]
    spreadsheet.values_batch_update(
        {"valueInputOption": "RAW", "data": [{"range": f"'{t.name}'!A1", "values": [list(t.columns)]} for t in tables]}
    )
    validations = [
        _validation(sheet_id, t, column, values)
        for sheet_id, t in zip(sheet_ids, tables)
        for column, values in t.enums.items()
    ]
    if validations:
        spreadsheet.batch_update({"requests": validations})


def _validation(sheet_id, t, column, values):
    index = t.columns.index(column)
    return {
        "setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": index, "endColumnIndex": index + 1},
            "rule": {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in values]},
                "strict": True,
                "showCustomUi": True,
            },
        }
    }


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


def read_table(spreadsheet: Spreadsheet, t: Table) -> Frame:
    """Read a declared table as a DataFrame of raw (unformatted) values, its date columns as YYYY-MM-DD.

    The review tab is read by column name (a column it doesn't have yet reads as blank), so
    answers typed under an older layout still count; every other table must match the schema.
    """
    held = getattr(spreadsheet, "rows", None)  # a Book, or a view of one (ledgerio.LedgerView)
    rows = held(t.name) if held else spreadsheet.worksheet(t.name).get_values(value_render_option=UNFORMATTED)
    if not rows:
        return pd.DataFrame(columns=list(t.columns))
    header, body = [str(h).strip() for h in rows[0]], rows[1:]
    if tuple(header) != t.columns and t.name != REVIEW:
        raise SchemaMismatch(f"{t.name}: header {header} != {list(t.columns)}")
    width = len(header)
    df = pd.DataFrame([list(r) + [""] * (width - len(r)) for r in body], columns=header)
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
    _wrote(spreadsheet, t.name)
    return len(values)


def _cell(value):
    """One cell for appendCells, typed as values.append with RAW would write it."""
    if value is None or (isinstance(value, str) and value == ""):
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if pd.api.types.is_number(value):  # numpy's too
        return {"userEnteredValue": {"numberValue": int(value) if pd.api.types.is_integer(value) else float(value)}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def append_tables(
    spreadsheet: Spreadsheet, tables: list[tuple[Table, Frame]], size: dict[str, Any] | None = None
) -> dict[str, int]:
    """Append records to several tables as one write: every table's rows land, or none do.

    tables is a list of (Table, DataFrame). One spreadsheets.batchUpdate of appendCells requests, which
    Sheets applies atomically, so a file's rows and its source_documents row can't be split by a failure
    between them (an ingest that writes the rows but not the record of them would read the file again).
    Returns rows appended by table name; size, a dict when given, gets the request's "bytes".
    """
    requests, counts = [], {}
    for t, df in tables:
        counts[t.name] = 0 if df is None else len(df)
        if df is None or df.empty:
            continue
        values = df[list(t.columns)].astype(object).where(df.notna(), "").values.tolist()
        requests.append(
            {
                "appendCells": {
                    "sheetId": spreadsheet.worksheet(t.name).id,
                    "fields": "userEnteredValue",
                    "rows": [{"values": [_cell(v) for v in row]} for row in values],
                }
            }
        )
    if requests:
        body = {"requests": requests}
        if size is not None:
            size["bytes"] = len(json.dumps(body))  # Google suggests keeping a request under 2 MB
        try:
            spreadsheet.batch_update(body)
        finally:  # written or not, what was read of them is no longer known to be what they hold
            _wrote(spreadsheet, *[t.name for t, df in tables if df is not None and not df.empty])
    return counts


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
    _wrote(spreadsheet, t.name)
    return len(values) - 1


QUOTE = re.compile(r"^[A-Z0-9]{1,10}(?:[:.][A-Z0-9]{1,10})?$")  # VEMAX, MUTF:VEMAX, BRK.B


def rewrite_price_requests(spreadsheet, t, df):
    """Rewrite the ledger's price_requests tab, the one place a formula is written: each row's result is a
    GOOGLEFINANCE formula Sheets evaluates, which ingest reads back as prices (prices.py).

    The formula is built here from the row's quote and date alone, after both are checked (a quote of capitals
    and digits, a YYYY-MM-DD date), so nothing typed into the ledger can become a formula of its own.
    """
    if t.name != PRICE_REQUESTS:
        raise PermissionError(f"rewrite_price_requests refused on '{t.name}'")
    rows = []
    for r in df[["symbol", "quote", "date"]].astype(str).values.tolist():
        symbol, quote, day = (c.strip() for c in r)
        if not QUOTE.match(quote) or not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            raise ValueError(f"price request refused: quote {quote!r}, date {day!r}")
        rows.append([symbol, quote, day, price_formula(quote, day)])
    ws = spreadsheet.worksheet(t.name)
    ws.clear()
    values = [list(t.columns)] + rows
    ws.resize(rows=max(len(values), 2), cols=len(t.columns))
    # everything but the formula column is written as it is: a symbol is the ledger's text, never a formula
    ws.update(values=[r[:-1] for r in values], range_name="A1", value_input_option="RAW")
    last = chr(ord("A") + len(t.columns) - 1)
    ws.update(values=[[r[-1]] for r in values], range_name=f"{last}1", value_input_option="USER_ENTERED")
    _wrote(spreadsheet, t.name)
    return len(rows)


def price_formula(quote, day):
    """The last close on or before day, in the week before it, as "YYYY-MM-DD close"; "none" when there is none."""
    y, m, d = (int(p) for p in day.split("-"))
    when = f"DATE({y},{m},{d})"
    return (
        f'=IFERROR(LET(r,GOOGLEFINANCE("{quote}","close",{when}-7,{when}),'
        f'TEXT(INDEX(r,ROWS(r),1),"yyyy-mm-dd")&" "&INDEX(r,ROWS(r),2)),"none")'
    )


def replace_table(spreadsheet: Spreadsheet, t: Table, df: Frame) -> int:
    """Overwrite a derived table. Allowed only on the Reports spreadsheet. On a Book inside batched() the
    table waits to be written with the others."""
    if spreadsheet.title != REPORTS_TITLE:
        raise PermissionError(f"replace_table refused on '{spreadsheet.title}': only Reports is rebuilt")
    values = [list(t.columns)] + df[list(t.columns)].astype(object).where(df.notna(), "").values.tolist()
    if isinstance(spreadsheet, Book):
        if spreadsheet.deferred:
            spreadsheet.queue(t, values)
        else:
            spreadsheet.write([(t, values)])
        return len(values) - 1
    ws = spreadsheet.worksheet(t.name)
    ws.clear()
    ws.resize(rows=max(len(values), 2), cols=len(t.columns))  # values.update does not grow the grid
    ws.update(values=values, range_name="A1", value_input_option="RAW")
    return len(values) - 1
