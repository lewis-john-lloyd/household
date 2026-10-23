"""Where every tax number comes from.

check() holds each citation in taxdata against the text of its source document:
the quote must be on the cited page, and the quote must say the value the code
uses. Two citations check more than a quote: "brackets" requires every
threshold on the page, and "withholding_mfj_table_floor" requires every row of
Pub 15-T's annual married-filing-jointly table to equal the year's brackets
shifted up by the floor, which is the identity withholding.py relies on.

report() is the Reports view of the same citations, with a link to each source
PDF in the record store, so a number in a report can be traced to the page
that says it.
"""

import re

import pandas as pd

from . import tax, taxdata

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
    if item == "brackets" or year is None:
        return None
    value = taxdata.params(year)[item]
    if value in (0, False) or value is True:  # an effective date, not a number
        return None
    spellings = _spellings(value)
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


def _extra_checks(year, item, page_text):
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
            problems += [p for p in (_says_value(year, item, quote), _extra_checks(year, item, text)) if p]
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
        for item in ["brackets", *taxdata.PARAMS[year]]:
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
        value = "" if year is None or item == "brackets" else taxdata.params(year)[item]
        rows.append({"year": "" if year is None else year, "item": item, "value": value,
                     "document": title, "page": page, "quote": quote,
                     "record": links.get(sha) or "not in record store"})
    for key, reason in taxdata.UNCITED.items():
        year, item = (key, "all") if isinstance(key, int) else key
        value = "" if item == "all" else taxdata.params(year)[item]
        rows.append({"year": year, "item": item, "value": value, "document": "", "page": "",
                     "quote": reason, "record": "no source on file"})
    return pd.DataFrame(rows, columns=REPORT_COLUMNS)
