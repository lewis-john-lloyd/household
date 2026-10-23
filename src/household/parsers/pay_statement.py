"""Pay stubs in the "Pay Statement" layout: earnings and deductions side by side,
then taxes, then a pay summary with gross, FIT taxable wages, taxes, deductions
and net pay for the period and year to date.

Every stub is checked against its own summary before anything is written:
line items must add to the printed totals, gross minus taxes minus deductions
must equal net, and gross minus pre-tax deductions must equal FIT taxable wages.
The last check is what catches a deduction filed under the wrong kind.

These checks are strict for the period's figures. Year-to-date figures are
checked too, but a mismatch there is reported rather than fatal: on days with
two stubs (an RSU vest beside another payment) payroll prints line-item YTDs
that leave out the sibling stub, while the summary includes it.

A check history (one stub per page) parses into every stub it holds. Stubs
are identified by their document number; vest stubs print none, so they get
an id derived from their own figures, which is stable across downloads.
"""

import hashlib
import re

import pandas as pd

from .common import ParseError, Result, nearest, require, tokens, us_date

NAME = "pay_statement"
ALL_PAGES = True  # check histories run to many pages

# Deductions taken before federal income tax. Anything else is post-tax; the
# FIT taxable wages check fails loudly if a new pre-tax line appears.
PRE_TAX = {
    "401K Pretax", "Bonus 401K Pre", "HSA Employee", "HSA ER Update", "FSA Dependent",
    "Ltd Purpose FSA", "Medical", "Dental", "Vision",
}


def sniff(text):
    return "Pay Statement" in text and "Pay Summary" in text and "FIT Taxable Wages" in text


def _field(text, label):
    m = re.search(rf"{label}\s+(\d\d/\d\d/\d{{4}})", text)
    if not m:
        raise ParseError(f"{NAME}: no {label}")
    return us_date(m.group(1))


def _index(lines, predicate, start=0, what="line"):
    for i in range(start, len(lines)):
        if predicate(lines[i]):
            return i
    raise ParseError(f"{NAME}: no {what}")


def _header_edges(line, labels):
    edges = []
    for label in labels:
        at = line.find(label)
        if at < 0:
            raise ParseError(f"{NAME}: header has no '{label}'")
        edges.append(at + len(label))
    return edges


def _stubs(text):
    """Split text into one chunk per stub, each starting at its "Pay Statement" title."""
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if "Pay Statement" in l]
    if not starts:
        raise ParseError(f"{NAME}: no title")
    bounds = starts + [len(lines)]
    return ["\n".join(lines[a:b]) for a, b in zip(bounds, bounds[1:])]


def parse(text):
    stubs = [_parse_one(chunk) for chunk in _stubs(text)]
    rows = pd.concat([r for r, _ in stubs], ignore_index=True)
    notes = [n for _, ns in stubs for n in ns]
    person = rows["person"].iloc[0]
    dates = sorted(rows["pay_date"].unique())
    label = (f"paystub_{person}_{dates[0]}" if len(stubs) == 1
             else f"paystubs_{person}_{dates[0]}_{dates[-1]}")
    return Result(kind="paystub", parser=NAME, label=label,
                  covers_from=rows["period_start"].min(), covers_to=rows["period_end"].max(),
                  tables={"paystubs": rows},
                  keys={"paystubs": ["pay_date", "person", "employer", "stub_id"]},
                  notes=notes)


def _parse_one(text):
    lines = text.splitlines()
    pay_date = _field(text, "Pay Date")
    period_start = _field(text, "Period Start Date")
    period_end = _field(text, "Period End Date")

    top = _index(lines, lambda l: "Pay Statement" in l, what="title")
    details = _index(lines, lambda l: l.strip().startswith("Pay Details"), top, "Pay Details")
    employer = next((t.text for l in lines[top + 1:details] for t in tokens(l)[:1]
                     if t.start < 5), "")
    person_line = next(l for l in lines[details + 1:] if l.strip())
    person = tokens(person_line)[0].text.split()[0].lower()

    head = _index(lines, lambda l: "Pay Type" in l and "Deduction" in l, details, "earnings header")
    taxes_at = _index(lines, lambda l: l.strip() == "Taxes", head, "Taxes section")
    ded_col = lines[head].find("Deduction")
    earn_edges = _header_edges(lines[head], ["Hours", "Pay Rate", "Current", "YTD"])
    ded_edges = _header_edges(lines[head][ded_col:], ["Employee Current", "Employee YTD",
                                                       "Employer Current", "Employer YTD"])
    ded_edges = [e + ded_col for e in ded_edges]

    items = []
    for line in lines[head + 1:taxes_at]:
        toks = tokens(line)
        left = [t for t in toks if t.start < ded_col - 2]
        right = [t for t in toks if t.start >= ded_col - 2]
        names = [t for t in left if not t.numeric]
        if names and not names[0].text.startswith("Total Hours"):
            cols = {nearest(t, earn_edges): t.value for t in left if t.numeric}
            items.append((names[0].text, "income", cols.get(2, 0.0), cols.get(3, 0.0)))
        rnames = [t for t in right if not t.numeric]
        if rnames:
            name = rnames[0].text
            cols = {nearest(t, ded_edges): t.value for t in right if t.numeric}
            kind = "pre_tax" if name in PRE_TAX else "post_tax"
            items.append((name, kind, cols.get(0, 0.0), cols.get(1, 0.0)))
            if cols.get(2, 0.0) or cols.get(3, 0.0):
                items.append((name, "employer", cols.get(2, 0.0), cols.get(3, 0.0)))

    summary_at = _index(lines, lambda l: l.strip() == "Pay Summary", taxes_at, "Pay Summary")
    tax_head = _index(lines, lambda l: "Based On" in l, taxes_at, "taxes header")
    for line in lines[tax_head + 1:summary_at]:
        toks = tokens(line)
        if line.strip().startswith(("Paid Time Off", "Net Pay Distribution")):
            break
        nums = [t.value for t in toks if t.numeric]
        if toks and not toks[0].numeric and len(nums) == 3:
            items.append((toks[0].text, "tax", nums[1], nums[2]))

    summary = {}
    for line in lines[summary_at + 1:]:
        toks = tokens(line)
        if toks and toks[0].text in ("Current", "YTD"):
            summary[toks[0].text] = [t.value for t in toks[1:] if t.numeric]
    if set(summary) != {"Current", "YTD"} or any(len(v) != 5 for v in summary.values()):
        raise ParseError(f"{NAME}: unreadable pay summary")

    df = pd.DataFrame(items, columns=["line_item", "kind", "amount", "ytd_amount"])
    notes = []
    for col, period in (("amount", "Current"), ("ytd_amount", "YTD")):
        gross, fit, taxes, deductions, net = summary[period]
        by_kind = df.groupby("kind")[col].sum()
        income, pre = by_kind.get("income", 0.0), by_kind.get("pre_tax", 0.0)
        checks = [
            (f"{period} earnings", gross, income),
            (f"{period} taxes", taxes, by_kind.get("tax", 0.0)),
            (f"{period} deductions", deductions, pre + by_kind.get("post_tax", 0.0)),
            (f"{period} net", net, gross - taxes - deductions),
            (f"{period} FIT taxable", fit, gross - pre),
        ]
        if period == "Current":
            require(checks, f"{NAME} {pay_date}")
        else:
            try:
                require(checks, f"{NAME} {pay_date}")
            except ParseError as e:
                notes.append(str(e).replace("failed self-checks", "YTD differs from its lines"))

    memo = pd.DataFrame(
        [(label, "memo", c, y) for label, c, y in zip(
            ("Gross", "FIT Taxable Wages", "Taxes", "Deductions", "Net Pay"),
            summary["Current"], summary["YTD"])],
        columns=df.columns)
    rows = pd.concat([df, memo], ignore_index=True)
    stub = re.search(r"Document\s+(\d+)", text)
    if stub:
        stub_id = stub.group(1)
    else:
        figures = "|".join(f"{v:.2f}" for v in summary["Current"] + summary["YTD"])
        stub_id = "s" + hashlib.sha256(f"{pay_date}|{figures}".encode()).hexdigest()[:10]
    rows.insert(0, "stub_id", stub_id)
    rows.insert(0, "employer", employer)
    rows.insert(0, "person", person)
    rows.insert(0, "period_end", period_end)
    rows.insert(0, "period_start", period_start)
    rows.insert(0, "pay_date", pay_date)
    return rows, notes
