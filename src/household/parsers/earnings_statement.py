"""Pay stubs in the "Earnings Statement" layout: a direct-deposit voucher on top,
then earnings (rate, hours, amount, YTD) beside deductions (amount, YTD), then
taxes with a total row.

Checks before anything is written: earnings add to the gross row, taxes to the
taxes total, deductions to the deductions total, gross minus taxes minus
deductions equals net pay, and gross minus pre-tax deductions equals the
printed federal taxable income.
"""

import re

import pandas as pd

from .common import ParseError, Result, nearest, require, tokens, us_date

NAME = "earnings_statement"

PRE_TAX = {"401k", "Medical", "Dental", "Vision", "HSA", "FSA", "Dependent Care FSA"}
EMPLOYER = {"401k Match"}  # shown with earnings, but paid into the plan, not to the employee
LONG_DATE = "%B %d, %Y"
DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"


def sniff(text):
    return "Earnings Statement" in text and "Fed Taxable Income" in text


def _date(text, label):
    m = re.search(rf"{label}\s{{2,}}{DATE}", text)
    if not m:
        raise ParseError(f"{NAME}: no {label}")
    return us_date(m.group(1), LONG_DATE)


def _number(text, label):
    m = re.search(rf"{label}\s+(-?[\d,]+\.\d\d)", text)
    if not m:
        raise ParseError(f"{NAME}: no {label}")
    return float(m.group(1).replace(",", ""))


def parse(text):
    lines = text.splitlines()
    pay_date = _date(text, "Check Date")
    period_start = _date(text, "Period Beginning")
    period_end = _date(text, "Period Ending")
    fed_taxable = _number(text, "Fed Taxable Income")
    net = _number(text, "Net Pay")

    title = next((l for l in lines if "Earnings Statement" in l), None)
    footer = next((l for l in lines if "| FEIN" in l or "FEIN:" in l), "")
    if title is None:
        raise ParseError(f"{NAME}: no title line")
    person = tokens(title)[0].text.split()[0].lower()
    employer = footer.split("|")[0].strip()

    head = next((i for i, l in enumerate(lines)
                 if l.strip().startswith("Earnings") and "Deductions" in l and "YTD" in l), None)
    if head is None:
        raise ParseError(f"{NAME}: no earnings header")
    header = lines[head]
    ded_col = header.find("Deductions")
    labels = ["Rate", "Hours", "Amount", "YTD"]
    earn_edges = [header.find(lab) + len(lab) for lab in labels]
    right = header[ded_col:]
    ded_edges = [ded_col + right.find("Amount") + 6, ded_col + right.find("YTD") + 3]

    items, gross, deductions_total, taxes_total = [], None, None, None
    earn_done = ded_done = False
    i = head + 1
    while i < len(lines) and not (earn_done and ded_done):
        line = lines[i]
        i += 1
        toks = tokens(line)
        if not toks:
            continue
        left = [t for t in toks if t.start < ded_col - 2]
        rt = [t for t in toks if t.start >= ded_col - 2]
        if not earn_done and left and not left[0].numeric:
            name = left[0].text
            cols = {nearest(t, earn_edges): t.value for t in left[1:] if t.numeric}
            if name == "Gross Earnings":
                gross = (cols.get(2, 0.0), cols.get(3, 0.0))
                earn_done = True
            elif name == "Taxes":
                earn_done = True
            else:
                kind = "employer" if name in EMPLOYER else "income"
                items.append((name, kind, cols.get(2, 0.0), cols.get(3, 0.0)))
        if not ded_done and rt and not rt[0].numeric:
            name = rt[0].text
            cols = {nearest(t, ded_edges): t.value for t in rt[1:] if t.numeric}
            if name == "Deductions":
                deductions_total = (cols.get(0, 0.0), cols.get(1, 0.0))
                ded_done = True
            else:
                kind = "pre_tax" if name in PRE_TAX else "post_tax"
                items.append((name, kind, cols.get(0, 0.0), cols.get(1, 0.0)))

    tax_head = next((k for k in range(i - 1, len(lines))
                     if lines[k].strip().startswith("Taxes") and "Amount" in lines[k]), None)
    if tax_head is None:
        raise ParseError(f"{NAME}: no taxes header")
    for line in lines[tax_head + 1:]:
        toks = [t for t in tokens(line) if t.start < ded_col - 2]
        if not toks or toks[0].numeric:
            continue
        nums = [t.value for t in toks[1:] if t.numeric]
        if len(nums) != 2:
            continue
        if toks[0].text == "Taxes":
            taxes_total = tuple(nums)
            break
        items.append((toks[0].text, "tax", nums[0], nums[1]))

    if gross is None or deductions_total is None or taxes_total is None:
        raise ParseError(f"{NAME}: missing a total row")
    df = pd.DataFrame(items, columns=["line_item", "kind", "amount", "ytd_amount"])
    checks = []
    for n, (col, period) in enumerate((("amount", "Current"), ("ytd_amount", "YTD"))):
        by_kind = df.groupby("kind")[col].sum()
        deductions = by_kind.get("pre_tax", 0.0) + by_kind.get("post_tax", 0.0)
        checks += [
            (f"{period} earnings", gross[n], by_kind.get("income", 0.0)),
            (f"{period} taxes", taxes_total[n], by_kind.get("tax", 0.0)),
            (f"{period} deductions", deductions_total[n], deductions),
        ]
    current = df.groupby("kind")["amount"].sum()
    checks += [
        ("net", net, gross[0] - taxes_total[0] - deductions_total[0]),
        ("fed taxable", fed_taxable, gross[0] - current.get("pre_tax", 0.0)),
    ]
    require(checks, f"{NAME} {pay_date}")

    memo = pd.DataFrame([
        ("Gross", "memo", gross[0], gross[1]),
        ("Fed Taxable Income", "memo", fed_taxable, 0.0),
        ("Taxes", "memo", taxes_total[0], taxes_total[1]),
        ("Deductions", "memo", deductions_total[0], deductions_total[1]),
        ("Net Pay", "memo", net, 0.0),
    ], columns=df.columns)
    rows = pd.concat([df, memo], ignore_index=True)
    for col, value in (("employer", employer), ("person", person), ("period_end", period_end),
                       ("period_start", period_start), ("pay_date", pay_date)):
        rows.insert(0, col, value)
    return Result(kind="paystub", parser=NAME, label=f"paystub_{person}_{pay_date}",
                  covers_from=period_start, covers_to=period_end,
                  tables={"paystubs": rows},
                  keys={"paystubs": ["pay_date", "person", "employer"]})
