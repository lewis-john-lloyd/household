"""What a slow part of a run rested on the last time it ran, so a run can tell that nothing it rests on has
changed and keep what it made.

of(...) is a fingerprint of anything a part is given: the same things give the same fingerprint, whatever
order a dict or a set was filled in, with numbers taken to six decimal places (a sum added up in another
order is the same sum). Each caller puts the package's version among them, so a release never keeps what an
older one made.

Reports' `fingerprints` table holds one row per part: its name, the fingerprint, the run that made what is
kept, when, and what that run found and said (detail, as JSON), for a later run to say again. A part that
finds its fingerprint there, and its tables still as that run wrote them (holds), skips its work.
"""

import dataclasses
import hashlib
import json
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from . import schema, sheets
from .kinds import Spreadsheet

TABLE = "fingerprints"
PLACES = 6


def canonical(x: Any) -> Any:
    """x as plain JSON values that do not depend on the order it was built in."""
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return canonical(vars(x))
    if isinstance(x, pd.DataFrame):
        return {
            "columns": [str(c) for c in x.columns],
            "rows": canonical(x.astype(object).where(x.notna(), None).values.tolist()),
        }
    if isinstance(x, pd.Series):
        return canonical(x.astype(object).where(x.notna(), None).tolist())
    if isinstance(x, np.ndarray):
        return canonical(x.tolist())
    if isinstance(x, dict):
        return {str(k): canonical(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (set, frozenset)):
        return sorted((canonical(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(x, (list, tuple)):
        return [canonical(v) for v in x]
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, np.generic):
        return canonical(x.item())
    if isinstance(x, bool) or x is None or isinstance(x, (int, str)):
        return x
    if isinstance(x, float):
        if x != x or x in (float("inf"), float("-inf")):
            return str(x)
        return round(x, PLACES) + 0.0  # -0.0 is 0.0
    return str(x)


def of(*parts: Any) -> str:
    """The fingerprint of everything given."""
    text = json.dumps(canonical(parts), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def read(reports: Spreadsheet) -> dict[str, dict[str, Any]]:
    """{name: {"fingerprint", "run_id", "at", "detail"}} as Reports holds them."""
    df = sheets.read_table(reports, schema.table(schema.REPORTS, TABLE))
    out: dict[str, dict[str, Any]] = {}
    for r in df.to_dict("records"):
        try:
            detail = json.loads(str(r["detail"]) or "{}")
        except ValueError:
            detail = {}
        out[str(r["name"])] = {
            "fingerprint": str(r["fingerprint"]),
            "run_id": str(r["run_id"]),
            "at": str(r["at"]),
            "detail": detail if isinstance(detail, dict) else {},
        }
    return out


def write(reports: Spreadsheet, name: str, fingerprint: str, run_id: str, at: str, detail: dict[str, Any]) -> None:
    """Keep name's fingerprint (replacing its last one, leaving the others)."""
    t = schema.table(schema.REPORTS, TABLE)
    kept = sheets.read_table(reports, t)
    kept = kept[kept["name"].astype(str) != name] if len(kept) else kept
    row = {
        "name": name,
        "fingerprint": fingerprint,
        "run_id": run_id,
        "at": at,
        "detail": json.dumps(canonical(detail), sort_keys=True),
    }
    sheets.replace_table(reports, t, pd.concat([kept, pd.DataFrame([row], columns=t.columns)], ignore_index=True))


def forget(reports: Spreadsheet, name: str) -> None:
    """Drop name's fingerprint: what it kept is no longer what Reports holds."""
    t = schema.table(schema.REPORTS, TABLE)
    kept = sheets.read_table(reports, t)
    if len(kept) and (kept["name"].astype(str) == name).any():
        sheets.replace_table(reports, t, kept[kept["name"].astype(str) != name].reset_index(drop=True))


def holds(reports: Spreadsheet, tables: dict[str, int], run_id: str) -> bool:
    """Whether each table in Reports is still as run_id wrote it: its first row carries that run's id, or it
    has no rows where that run wrote none (tables: {name: rows written}). One read of each tab's first two
    rows."""
    names = sorted(tables)
    if not names:
        return True
    got = reports.values_batch_get([f"'{n}'!1:2" for n in names], params={"valueRenderOption": sheets.UNFORMATTED})
    for name, value_range in zip(names, got.get("valueRanges", [])):
        rows = value_range.get("values", [])
        first = rows[1] if len(rows) > 1 else []
        if not tables[name]:
            if any(str(c).strip() for c in first):
                return False
            continue
        header = [str(h).strip() for h in rows[0]] if rows else []
        if "run_id" not in header or header.index("run_id") >= len(first):
            return False
        if str(first[header.index("run_id")]) != run_id:
            return False
    return True
