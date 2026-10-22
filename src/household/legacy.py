"""Read the legacy budget-reports workbook: per-year `YYYY-data` tabs and `YYYY` rollups.

Legacy conventions: costs are positive for money spent; dates are either real
date cells (serial numbers when read unformatted) or text in a few formats;
cells often carry stray spaces.
"""

import re
from datetime import date, datetime, timedelta

import pandas as pd

SHEETS_EPOCH = date(1899, 12, 30)
TOTAL = "TOTAL"
TEXT_DATE_FORMATS = ("%Y-%b-%d", "%Y-%m-%d", "%b %d, %Y", "%m/%d/%Y")


def parse_date(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return SHEETS_EPOCH + timedelta(days=int(value))
    text = str(value).strip()
    for fmt in TEXT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {value!r}")


def parse_money(value):
    """Return a float for a numeric cell or text with a currency sign, thousands commas,
    parentheses or a leading minus for negatives, or a lone dash for zero."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    negative = ("(" in text and ")" in text) or bool(re.match(r"^[^\d]*-\s*\$?\s*\d", text))
    digits = re.sub(r"[^\d.]", "", text)
    if not digits or digits == ".":
        return 0.0
    amount = float(digits)
    return -amount if negative else amount


def parse_data_rows(rows):
    """Rows of a `YYYY-data` tab (header first) to a DataFrame of date, category, payee_raw, cost, note."""
    header = [h.strip().lower() for h in rows[0]]
    idx = {name: header.index(name) for name in ("category", "date", "transaction", "cost")}
    note_idx = header.index("notes") if "notes" in header else None
    records = []
    for row in rows[1:]:
        row = list(row) + [""] * (len(header) - len(row))
        category = str(row[idx["category"]]).strip()
        if not category and str(row[idx["cost"]]).strip() == "":
            continue  # blank row
        records.append({
            "date": parse_date(row[idx["date"]]),
            "category": category,
            "payee_raw": str(row[idx["transaction"]]).strip(),
            "cost": round(parse_money(row[idx["cost"]]), 2),
            "note": str(row[note_idx]).strip() if note_idx is not None else "",
        })
    return pd.DataFrame(records, columns=["date", "category", "payee_raw", "cost", "note"])


def parse_rollup_rows(rows):
    """Rows of a `YYYY` rollup tab to long form: month (YYYY-MM), category, spend.

    The rollup's `sum` row is the monthly total; it is returned as category TOTAL.
    """
    starts = rows[0][1:]
    months = [parse_date(v).strftime("%Y-%m") if str(v).strip() else None for v in starts]
    records = []
    for row in rows[2:]:  # rows 0 and 1 are start and end dates
        category = str(row[0]).strip() if row else ""
        if not category:
            continue
        if category.lower() == "sum":
            category = TOTAL
        for month, cell in zip(months, row[1:]):
            if month is None:
                continue
            records.append({"month": month, "category": category, "spend": round(parse_money(cell), 2)})
    return pd.DataFrame(records, columns=["month", "category", "spend"])
