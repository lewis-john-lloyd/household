"""Where every tax number comes from.

check() holds each citation in taxdata against the text of its source document:
the quote must be on the cited page, and the quote must say the value the code
uses. Two citations check more than a quote: "brackets" requires every
threshold on the page, and "withholding_mfj_table_floor" requires every row of
Pub 15-T's annual married-filing-jointly table to equal the year's brackets
shifted up by the floor, which is the identity withholding.py relies on. Wisconsin prints no rate schedule or
standard deduction formula, so those are proved the same way: every row of the
Form 1 tax table, standard deduction table and homeowner's school property tax
credit table must come out of wisconsin.py, as must the worksheet's and Pub
W-166's printed amounts.

report() is the Reports view of the same citations, with a link to each source
PDF in the record store, so a number in a report can be traced to the page
that says it.
"""

import re

import pandas as pd

from . import tax, taxdata, wisconsin

CHECK_COLUMNS = ["year", "item", "document", "page", "quote", "ok", "detail"]
REPORT_COLUMNS = ["year", "item", "value", "document", "page", "quote", "record"]


def normalize(text):
    """Collapse whitespace and drop dollar signs, so quotes need neither."""
    return re.sub(r"\s+", " ", text.replace("$", "")).strip()


def _spellings(value):
    """Ways a source might print a value: 8,550; 6.2%; 30 percent; 1 million."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, float) and 0 < value < 1:
        pct = f"{value * 100:.4f}".rstrip("0").rstrip(".")
        return {f"{pct}%", f"{pct} percent"}
    if isinstance(value, (int, float)) and value >= 1 and float(value).is_integer():
        out = {f"{int(value):,}"}
        if value % 1_000_000 == 0:
            out.add(f"{int(value) // 1_000_000} million")
        return out
    return set()


def _says_value(year, item, quote):
    """None when the quote states the value the code uses (or there is no single value to state)."""
    if year is None or item not in taxdata.params(year):
        return None
    value = taxdata.params(year)[item]
    if value in (0, False) or value is True:  # an effective date, not a number
        return None
    spellings = _spellings(value)
    if item in taxdata.TABLE_CHECKED and not any(s in quote for s in spellings):
        return None  # proved by the table check instead
    if spellings and not any(s in quote for s in spellings):
        return f"quote does not state {value!r}"
    return None


def _table_rows(year, floor):
    """Each row of the annual MFJ table as normalized text: start, [end,] base tax, rate, start."""
    rows = [rf"0 {floor:,} 0\.00 0% 0\b"]
    for threshold, rate in taxdata.brackets(year):
        start = threshold + floor
        base = f"{tax.bracket_tax(threshold, year):,.2f}"
        rows.append(rf"\b{start:,} (?:[\d,]+ )?{re.escape(base)} {rate * 100:g}% {start:,}\b")
    return rows


def _numbers(line):
    """The integers on a table line, or None if the line holds anything else."""
    tokens = line.split()
    if not tokens or not all(re.fullmatch(r"\d{1,3}(?:,\d{3})*", t) for t in tokens):
        return None
    return [int(t.replace(",", "")) for t in tokens]


def _table(pages, first, last, width, columns):
    """(at least, less than, values...) rows from table lines with this many numbers."""
    out = []
    for text in pages[first - 1:last]:
        for line in text.splitlines():
            nums = _numbers(line)
            if nums and len(nums) == columns and 0 < nums[1] - nums[0] <= width:
                out.append(nums)
    return out


def _table_result(rows, expected, minimum, what):
    if len(rows) < minimum:
        return f"only {len(rows)} {what} rows found"
    bad = [r for r in rows if expected(r) != r[-1] if r]
    return f"{len(bad)} of {len(rows)} {what} rows differ, first {bad[0]}" if bad else None


def _wisconsin_checks(year, item, pages, page, text):
    if item == "wi_brackets":
        # The worksheet taxes income of 100,000 or more as rate x income - subtraction, one row
        # per bracket in reach; each subtraction amount and row boundary must be printed.
        steps = taxdata.wi_brackets(year)
        printed = []
        for i, (start, rate) in enumerate(steps):
            top = steps[i + 1][0] if i + 1 < len(steps) else float("inf")
            if top > wisconsin.TABLE_LIMIT:
                printed.append(f"{rate * start - wisconsin.bracket_tax(start, year):,.2f}")
                if start > wisconsin.TABLE_LIMIT:
                    printed.append(f"{start:,}")
        missing = [p for p in printed if p not in text]
        if missing:
            return f"worksheet amounts not on page: {', '.join(missing)}"
        rows = [r[:2] + [r[3]] for r in _table(pages, 1, len(pages), 100, 5)]  # MFJ is the 2nd tax column
        return _table_result(rows, lambda r: wisconsin.tax(r[0], year), 900, "tax table")
    if item == "wi_withholding_brackets":
        steps = taxdata.wi_withholding_brackets()
        amounts = [f"{wisconsin._schedule(start, steps):,.2f}" for start, _ in steps[1:]]
        missing = [a for a in amounts if a not in text]
        missing += [f"{t:,}" for t, _ in steps[1:] if f"{t:,}" not in text]
        return f"withholding schedule amounts not on page: {', '.join(missing)}" if missing else None
    if item.startswith("wi_standard_deduction"):
        rows = [r[:2] + [r[3]] for r in _table(pages, page, page + 2, 500, 6)]  # MFJ is the 2nd column
        return _table_result(rows, lambda r: wisconsin.standard_deduction(r[0], year), 250,
                             "standard deduction table")
    if item.startswith("wi_school_property_credit"):
        rows = _table(pages, page, page, 25, 3)
        return _table_result(rows, lambda r: wisconsin.school_property_credit(r[0], year), 50,
                             "homeowner's credit table")
    return None


def _extra_checks(year, item, page_text, pages=None, page=None):
    if item.startswith("wi_") and pages is not None:
        return _wisconsin_checks(year, item, pages, page, page_text)
    if item == "brackets":
        missing = [f"{t:,}" for t, _ in taxdata.brackets(year)[1:] if f"{t:,}" not in page_text]
        return f"thresholds not on page: {', '.join(missing)}" if missing else None
    if item == "withholding_mfj_table_floor":
        floor = taxdata.params(year)[item]
        bad = [r for r in _table_rows(year, floor) if not re.search(r, page_text)]
        return f"{len(bad)} table rows differ from the brackets shifted by {floor:,}" if bad else None
    return None


def citations():
    """Every citation as (year, item, document, page, quote); methods have year None."""
    out = [(year, item, *c) for (year, item), c in sorted(taxdata.CITATIONS.items())]
    out += [(None, item, *c) for item, c in taxdata.METHODS.items()]
    return out


def check(pages_by_document):
    """Check every citation against {document label: [text of page 1, page 2, ...]}."""
    rows = []
    for year, item, document, page, quote in citations():
        pages = pages_by_document.get(document)
        problems = []
        if pages is None:
            problems.append("document not supplied")
        elif not 1 <= page <= len(pages):
            problems.append(f"no page {page} (document has {len(pages)})")
        else:
            text = normalize(pages[page - 1])
            if normalize(quote) not in text:
                problems.append("quote not on page")
            problems += [p for p in (_says_value(year, item, quote),
                                     _extra_checks(year, item, text, pages, page)) if p]
        rows.append({"year": "" if year is None else year, "item": item, "document": document,
                     "page": page, "quote": quote,
                     "ok": not problems, "detail": "; ".join(problems)})
    return pd.DataFrame(rows, columns=CHECK_COLUMNS)


def uncovered():
    """(year, item) pairs in the tax tables with neither a citation nor an UNCITED reason."""
    out = []
    for year in sorted(taxdata.PARAMS):
        if year in taxdata.UNCITED:
            continue
        for item in ["brackets", "wi_brackets", "wi_withholding_brackets", *taxdata.PARAMS[year]]:
            if (year, item) not in taxdata.CITATIONS and (year, item) not in taxdata.UNCITED:
                out.append((year, item))
    return out


def report(source_documents):
    """One row per cited value, linked to the source PDF in the record store when it is there."""
    links = {}
    if not source_documents.empty:
        for r in source_documents.to_dict("records"):
            links[str(r.get("sha256", ""))[:16]] = r.get("drive_link", "")
    rows = []
    for year, item, document, page, quote in citations():
        title, sha = taxdata.DOCUMENTS[document]
        value = "" if year is None else taxdata.params(year).get(item, "")
        rows.append({"year": "" if year is None else year, "item": item, "value": value,
                     "document": title, "page": page, "quote": quote,
                     "record": links.get(sha) or "not in record store"})
    for key, reason in taxdata.UNCITED.items():
        year, item = (key, "all") if isinstance(key, int) else key
        value = "" if item == "all" else taxdata.params(year).get(item, "")
        rows.append({"year": year, "item": item, "value": value, "document": "", "page": "",
                     "quote": reason, "record": "no source on file"})
    return pd.DataFrame(rows, columns=REPORT_COLUMNS)
