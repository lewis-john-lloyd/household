"""Documents filed for the record with no rows to extract: tax returns and W-4s.

Their few needed facts are entered by hand, tagged with the document's doc_id.
Long documents are identified from their first pages, so the markers include
the cover letter some preparers put in front of the return.
"""

import re

from .common import Result

NAME = "documents"
RETURN_YEAR = (r"(20\d\d) Federal Tax Return", r"Form 1040 \((20\d\d)\)", r"(20\d\d) Form 1040")


def sniff(text):
    return _kind(text) is not None


def _kind(text):
    if "Form 1040" in text and ("Individual Income Tax Return" in text
                                or "Federal Tax Return" in text):
        return "tax_return"
    if "W-4" in text and "Withholding Certificate" in text:
        return "w4"
    return None


def _year(kind, text):
    patterns = RETURN_YEAR if kind == "tax_return" else (r"\b(20\d\d)\b",)
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return ""


def parse(text):
    kind = _kind(text)
    year = _year(kind, text)
    return Result(kind=kind, parser=NAME, label=f"{kind}_{year}" if year else kind,
                  covers_from=f"{year}-01-01" if year else "",
                  covers_to=f"{year}-12-31" if year else "")
