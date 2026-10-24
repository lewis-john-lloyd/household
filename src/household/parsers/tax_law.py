"""Public tax law sources: IRS publications and form instructions, revenue procedures,
notices, public laws, and Wisconsin's income tax instructions and withholding guide.
Filed for the record with no rows to extract.

These are the documents taxdata.CITATIONS points at, so each gets a stable label
(for example "publication-15-t-2026" or "rev-proc-2025-32") that names it in the
record store. The markers are strict and checked before every other parser,
because these documents quote the phrases other recognizers look for ("Form
1040 ... Individual Income Tax Return", "W-4 ... Withholding Certificate").
"""

import re

from .common import Result

NAME = "tax_law"
KIND = "tax_law"

# Matching runs on the text with all whitespace removed: some of these PDFs extract
# letter-spaced ("Ad min is tra tiv e"), and the markers survive either way.
PUBLICATION = re.compile(r"Publication(\d+(?:-[A-Z]+)?)\((20\d\d)\)CatalogNumber")
INSTRUCTIONS = re.compile(r"Instructionsfor(Form|Schedule)([0-9A-Z-]+?)(?:\(Form1040\))?\((20\d\d)\)"
                          r"CatalogNumber")
REV_PROC = re.compile(r"Rev\.Proc\.(20\d\d)-(\d+)")
NOTICE = re.compile(r"Notice(20\d\d)-(\d+)")
PUBLIC_LAW = re.compile(r"PUBLICLAW(\d+)[\u2013-](\d+)\u2014")
WI_INSTRUCTIONS = re.compile(r"WisconsinIncomeTax.{0,40}?Form(1|1NPR)Instructions(20\d\d)")
WI_W166 = re.compile(r"PublicationW-166\((\d{1,2})/(\d\d)\)")
WI_FORM = re.compile(r"^Wisconsin(20\d\d)(1|1NPR)incometax")
# A filed return carries a social security number and dollar amounts; the blank form has neither.
# (Amounts need four or more digits: the blank form cites statutes such as "sec. 157.06".)
FILLED = re.compile(r"\b\d{9}\b|\b\d{3}-\d{2}-\d{4}\b|\b\d{4,}\.\d\d\b|\b\d{1,3}(?:,\d{3})+\.\d\d\b")
IRB_HEADER = "Administrative,Procedural,andMiscellaneous"
TITLE_WINDOW = 1200  # characters (without whitespace) in which a title must appear
TITLE_LINES = 40


def _identify(text):
    """(label, title, year) for a recognized source, else None."""
    tight = re.sub(r"\s+", "", text)
    m = PUBLICATION.search(tight)
    if m:
        number, year = m.groups()
        return f"publication-{number.lower()}-{year}", f"Publication {number} ({year})", year
    m = INSTRUCTIONS.search(tight)
    if m:
        what, number, year = m.groups()
        return (f"instructions-for-{what.lower()}-{number.lower()}-{year}",
                f"Instructions for {what} {number} ({year})", year)
    m = WI_INSTRUCTIONS.search(tight[:TITLE_WINDOW])
    if m:
        form, year = m.groups()
        return (f"wi-form-{form.lower()}-instructions-{year}",
                f"Wisconsin Form {form} Instructions ({year})", year)
    m = WI_W166.search(tight[:TITLE_WINDOW])
    if m:
        month, yy = int(m.group(1)), m.group(2)
        return (f"wi-publication-w-166-20{yy}-{month:02d}", f"Wisconsin Publication W-166 ({m.group(1)}/{yy})",
                f"20{yy}")
    m = WI_FORM.search(tight)
    if m and not FILLED.search(text):
        year, form = m.groups()
        return (f"wi-form-{form.lower()}-{year}", f"Wisconsin Form {form} ({year}, blank)", year)
    m = PUBLIC_LAW.search(tight[:TITLE_WINDOW])
    if m:
        return f"public-law-{m.group(1)}-{m.group(2)}", f"Public Law {m.group(1)}-{m.group(2)}", ""
    if IRB_HEADER in tight[:TITLE_WINDOW]:
        m = REV_PROC.search(tight)
        if m:
            return f"rev-proc-{m.group(1)}-{m.group(2)}", f"Rev. Proc. {m.group(1)}-{m.group(2)}", ""
    for line in text.splitlines()[:TITLE_LINES]:  # a notice's number is a title line of its own
        m = NOTICE.fullmatch(re.sub(r"\s+", "", line))
        if m and "InternalRevenueCode" in tight:
            return f"notice-{m.group(1)}-{m.group(2)}", f"Notice {m.group(1)}-{m.group(2)}", ""
    return None


def sniff(text):
    return _identify(text) is not None


def parse(text):
    label, title, year = _identify(text)
    return Result(kind=KIND, parser=NAME, label=label,
                  covers_from=f"{year}-01-01" if year else "",
                  covers_to=f"{year}-12-31" if year else "",
                  notes=[title])
