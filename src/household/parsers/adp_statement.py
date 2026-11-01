"""Pay stubs in ADP's "Earnings Statement" layout (an employer's PDF with a voucher at the bottom).

The PDF's text comes through pdftext's piece-by-piece drawing (its layout text
is unusable), so numbers arrive as groups with no decimal point: "-1 234 56"
is -1,234.56, and "2 000 00 12 345 67" is two amounts, 2,000.00 this period
and 12,345.67 year to date. A group of one to three digits starts an amount,
three-digit groups follow, and a two-digit group (the cents, with * when the
deduction is excluded from federal taxable wages) ends it. The cents are set
at a fixed column per amount column, which says whether an amount is this
period's or the year's.

Earnings come first (rate and hours for hourly lines, then this period and
year to date), then Gross Pay; under Statutory come the taxes (labels with
"Tax") and the deductions (pre-tax when starred or named in PRE_TAX), then Net
Pay. Group Term Life (under Other Benefits, outside gross) is imputed income:
it is written as an income line with a matching post-tax line, so gross
includes it and net pay does not change, as other stubs print it.

Checks before anything is written: earnings add to Gross Pay for the period
and the year; gross less taxes and deductions is net pay; gross plus group term
life less pre-tax deductions is the printed federal taxable wages; and for the
year, gross less taxes and deductions is what was deposited ("Checking" lines).
"""

import re

import pandas as pd

from ..pdftext import POINTS
from .common import ParseError, Result, require, us_date

NAME = "adp_statement"
PRE_TAX = ("401k", "dental", "vision", "medical", "hsa", "fsa", "dep care", "pretax", "pre-tax")
RIGHT_PANEL = 350 / POINTS  # Other Benefits and the federal taxable wages sit right of this column
YEAR_COLUMN = 285 / POINTS  # left panel: cents right of this are year to date
TOTAL_COLUMN = 535 / POINTS  # right panel: cents right of this are total to date
WORD = re.compile(r"\S+")
GROUP = re.compile(r"^([-$]*)(\d{1,3})$")


def sniff(text):
    return all(s in text for s in ("Earnings Statement", "Statutory", "Net Check", "Pay Date:")) or (
        all(s in text for s in ("Statutory", "Net Check", "Pay Date:", "Period Beginning:")))


def _words(line):
    return [(m.start(), m.group()) for m in WORD.finditer(line)]


def _is_number(word):
    return bool(re.fullmatch(r"[-$]*\d+\*?", word))


def _amounts(words, what):
    """[(cents column, value, starred)] from number groups, left to right."""
    out, i = [], 0
    while i < len(words):
        m = GROUP.match(words[i][1])
        if not m:
            raise ParseError(f"{NAME}: {what}: can't read {words[i][1]!r}")
        sign, digits = m.group(1), m.group(2)
        i += 1
        while i + 1 < len(words) and re.fullmatch(r"\d{3}", words[i][1]):
            digits += words[i][1]
            i += 1
        if i >= len(words) or not re.fullmatch(r"\d\d\*?", words[i][1]):
            raise ParseError(f"{NAME}: {what}: an amount with no cents")
        col, cents = words[i]
        i += 1
        value = float(f"{digits}.{cents[:2]}") * (-1 if "-" in sign else 1)
        out.append((col, value, cents.endswith("*")))
    return out


def _row(line, panel):
    """(label, rate words dropped, [(column, value, starred)]) for the left or right panel of a line."""
    words = [w for w in _words(line) if (w[0] < RIGHT_PANEL) == (panel == "left")]
    first = next((i for i, (_, w) in enumerate(words) if _is_number(w)), len(words))
    label = " ".join(w for _, w in words[:first])
    numbers = words[first:]
    if label in ("Checking", "Savings") and numbers:  # "Checking 01": the account's number on the stub
        label, numbers = f"{label} {numbers[0][1]}", numbers[1:]
    if len(numbers) >= 4 and re.fullmatch(r"\d{4}", numbers[1][1]):  # rate (50 0000) and hours (40 00)
        numbers = numbers[4:]
    return label, _amounts(numbers, label or "a line")


def _split(amounts, boundary):
    """(this period, year to date) from amounts by their cents column; a missing one is 0."""
    period = sum(v for c, v, _ in amounts if c < boundary)
    year = sum(v for c, v, _ in amounts if c >= boundary)
    return round(period, 2), round(year, 2)


def _find(lines, text):
    i = next((k for k, l in enumerate(lines) if text in l), None)
    if i is None:
        raise ParseError(f"{NAME}: no {text!r}")
    return i


def _date(text, label):
    m = re.search(rf"{label}:\s*(\d\d/\d\d/\d{{4}})", text)
    if not m:
        raise ParseError(f"{NAME}: no {label}")
    return us_date(m.group(1))


def parse(text):
    lines = text.splitlines()
    pay_date, start, end = _date(text, "Pay Date"), _date(text, "Period Beginning"), _date(text, "Period Ending")
    employer = lines[_find(lines, "Period Beginning:")].split("Period Beginning:")[0].strip()
    owner = [w for c, w in _words(lines[_find(lines, "Taxable Marital Status")]) if c >= RIGHT_PANEL]
    if not employer or not owner:
        raise ParseError(f"{NAME}: no employer or employee name")
    person = owner[0].lower()
    advice = re.search(r"Advice number:\s*0*(\d+)", text)

    head = next((k for k, l in enumerate(lines) if "rate" in l and "hours" in l and "this period" in l), None)
    if head is None:
        raise ParseError(f"{NAME}: no earnings header")
    items, gross, net, deposited, section = [], None, None, (0.0, 0.0), "earnings"
    gtl = (0.0, 0.0)
    for line in lines[head + 1:]:
        label, amounts = _row(line, "left")
        if label == "Statutory":
            section = "statutory"
        elif label == "Gross Pay":
            gross = _split(amounts, YEAR_COLUMN)
        elif label == "Net Pay":
            net = _split(amounts, YEAR_COLUMN)[0]
        elif label.startswith("Checking"):
            d = _split(amounts, YEAR_COLUMN)
            deposited = (deposited[0] - d[0], deposited[1] + d[1])
        elif label == "Net Check":
            break
        elif label and amounts:
            period, year = _split(amounts, YEAR_COLUMN)
            if section == "earnings":
                items.append((label, "income", period, year))
            elif "tax" in label.lower().split()[-1:] or label.lower().endswith(" tax"):
                items.append((label, "tax", -period, year))
            else:
                pre = any(s for _, _, s in amounts) or any(k in label.lower() for k in PRE_TAX)
                items.append((label, "pre_tax" if pre else "post_tax", -period, year))
        right, right_amounts = _row(line, "right")
        if right.startswith("Group Term Life"):
            gtl = _split(right_amounts, TOTAL_COLUMN)
    taxable_at = _find(lines, "Your federal taxable wages")
    taxable = []
    for following in lines[taxable_at + 1:taxable_at + 4]:  # the first dollar amount below
        words = _words(following)
        dollar = next((i for i, (_, w) in enumerate(words) if w.startswith("$")), None)
        if dollar is not None:
            cents = next((i for i in range(dollar + 1, len(words)) if re.fullmatch(r"\d\d", words[i][1])), dollar)
            taxable = _amounts(words[dollar:cents + 1], "federal taxable wages")
            break
    if gross is None or net is None or not taxable:
        raise ParseError(f"{NAME}: missing Gross Pay, Net Pay or federal taxable wages")
    fed_taxable = taxable[0][1]

    df = pd.DataFrame(items, columns=["line_item", "kind", "amount", "ytd_amount"])
    by = {col: df.groupby("kind")[col].sum() for col in ("amount", "ytd_amount")}
    taxes = tuple(round(by[c].get("tax", 0.0), 2) for c in by)
    deductions = tuple(round(by[c].get("pre_tax", 0.0) + by[c].get("post_tax", 0.0), 2) for c in by)
    checks = [("earnings", gross[0], by["amount"].get("income", 0.0)),
              ("YTD earnings", gross[1], by["ytd_amount"].get("income", 0.0)),
              ("net", net, gross[0] - taxes[0] - deductions[0]),
              ("fed taxable", fed_taxable, gross[0] + gtl[0] - by["amount"].get("pre_tax", 0.0))]
    if deposited != (0.0, 0.0):
        checks.append(("YTD deposited", deposited[1], gross[1] - taxes[1] - deductions[1]))
    require(checks, f"{NAME} {pay_date}")

    extra = [("Group Term Life", "income", gtl[0], gtl[1]), ("Group Term Life", "post_tax", gtl[0], gtl[1])] \
        if gtl != (0.0, 0.0) else []
    memo = [("Gross", "memo", gross[0] + gtl[0], gross[1] + gtl[1]),
            ("Fed Taxable Income", "memo", fed_taxable, 0.0),
            ("Taxes", "memo", taxes[0], taxes[1]),
            ("Deductions", "memo", deductions[0] + gtl[0], deductions[1] + gtl[1]),
            ("Net Pay", "memo", net, 0.0)]
    rows = pd.concat([df, pd.DataFrame(extra + memo, columns=df.columns)], ignore_index=True)
    rows["amount"], rows["ytd_amount"] = rows["amount"].round(2), rows["ytd_amount"].round(2)
    for col, value in (("stub_id", advice.group(1) if advice else ""), ("employer", employer), ("person", person),
                       ("period_end", end), ("period_start", start), ("pay_date", pay_date)):
        rows.insert(0, col, value)
    return Result(kind="paystub", parser=NAME, label=f"paystub_{person}_{pay_date}",
                  covers_from=start, covers_to=end, tables={"paystubs": rows},
                  keys={"paystubs": ["pay_date", "person", "employer", "stub_id"]})
