"""Co-op point-of-sale receipts (service "co_op"): the receipt lists each item with its price and its
tax flags (F: food, W: weighed, T1: taxed). The items add to Net Sales, the taxed ones to the base printed beside the
tax, and the card was charged SUB TOTAL (after any vendor coupon). Each item's amount is its
price plus its share of the tax (taxed items only), scaled so the items add to what was charged.
Food (an F flag) is grocery; a taxed item that is not food is alcohol when its name says so
(ALCOHOL), toys and games when its name says so (TOYS), and otherwise left to the default
(candles, medicine, soap). Weight and quantity lines, coupon lines and the store's own
contributions (not charged) are skipped. A "** Voided" line cancels the next priced entry (a re-rung
item, a bag credit, a contribution), passing over the weight, quantity and Ref lines that go with it. A charity round-up is its own item (charity). Checks: the items add to Net
Sales and the taxed ones to the tax base, or to more by no more than the coupons the totals take off
(a vendor or store coupon that no item line shows).
"""

import hashlib
import re

from ..common import ParseError
from .common import money_in, NAME, scale_to, to_cents

ITEM = re.compile(r"^(?P<name>.*?)\s*(?P<minus>-?)\$(?P<price>\d+\.\d\d)\s*(?P<flags>[A-Z0-9]*)\s*$")


SKIP = re.compile(r"^(\d+(\.\d+)? lb @|\d+ @ \$|\s*Ref:|CPN:|-Vendor coupon|[^$]*contribution: \$)")


ALCOHOL = re.compile(
    r"\b(beers?|ale|ipa|lager|stout|porter|pils(ner)?|quadrupel|tripel|dubbel|saison|kolsch|"
    r"wine|sauvignon|cabernet|merlot|pinot|chardonnay|riesling|ros[eé]|prosecco|champagne|cava|"
    r"malbec|zinfandel|syrah|shiraz|grenache|tempranillo|sangiovese|chianti|rioja|moscato|"
    r"vermouth|cider|mead|sake|vodka|gin|whiske?y|bourbon|rum|tequila|mezcal|brandy|liqueur|"
    r"hard seltzer|red blend|white blend)\b",
    re.I,
)


TOYS = re.compile(
    r"\b(puzzles?|stickers?|games?|toys?|lego|crayons?|coloring|playing cards|cards?|kite|"
    r"books?|activity)\b",
    re.I,
)


def _department(name, flags):
    if "F" in flags:
        return "grocery"
    if "T" in flags and ALCOHOL.search(name) and not re.search(r"\b(NA|non-?alcoholic)\b", name, re.I):
        return "alcohol"
    if TOYS.search(name):
        return "toys & games"
    return ""


def co_op(message_id, headers, body):
    """order_items rows for one co-op point-of-sale receipt, or [] for one that charged nothing."""
    receipt = body.split("EMAIL RECEIPT", 1)[-1]
    sections = re.split(r"^-{20,}", receipt, flags=re.M)
    if len(sections) < 3:
        raise ParseError(f"{NAME}: co-op receipt {message_id}: no item list")
    items, charity, voided = [], [], False
    for line in sections[1].splitlines():
        if not line.strip():
            continue
        if line.strip() in VOID_MARKS:
            voided = True  # the line right after is cancelled, whatever it is
            continue
        if voided:
            if not ITEM_INFO.match(line):  # a weight, quantity or Ref line belongs to the entry after it
                voided = False
            continue
        if SKIP.match(line):
            continue
        m = ITEM.match(line)
        if not m:
            continue  # a line with no price (a free item)
        name = " ".join(m["name"].split())
        cents = round(float(m["price"]) * 100) * (-1 if m["minus"] else 1)
        if CHARITY.match(name):
            charity.append((name.rstrip(":"), cents, ""))
            continue
        items.append((name, cents, m["flags"]))
    totals = "\n".join(sections[2:])
    net = money_in(totals, r"Net Sales \$(-?[\d,]+\.\d\d)", "Net Sales")
    charged = money_in(totals, r"SUB TOTAL \$(-?[\d,]+\.\d\d)", "SUB TOTAL")
    taxes = [
        (round(float(b) * 100), round(float(t) * 100))
        for b, t in re.findall(r"Tax \d+ \[\$(-?[\d.]+)\] \$(-?[\d.]+)", totals)
    ]
    coupons = sum(to_cents(c) for c in re.findall(COUPONS, totals, re.M))
    if not items or charged <= 0:
        return []
    over = sum(c for _, c, _ in items) - net
    if not 0 <= over <= coupons:  # a coupon in the totals can take its amount off Net Sales, never more
        raise ParseError(
            f"{NAME}: co-op receipt {message_id}: items add to {sum(c for _, c, _ in items) / 100:.2f}, "
            f"Net Sales is {net / 100:.2f}" + (f" (coupons {coupons / 100:.2f})" if coupons else "")
        )
    base = sum(c for _, c, f in items if "T" in f)
    if taxes and not 0 <= base - sum(b for b, _ in taxes) <= coupons:
        raise ParseError(
            f"{NAME}: co-op receipt {message_id}: taxed items add to {base / 100:.2f}, "
            f"the tax base is {sum(b for b, _ in taxes) / 100:.2f}"
        )
    tax = sum(t for _, t in taxes)
    with_tax = [c + (round(tax * c / base) if base and "T" in f else 0) for _, c, f in items]
    lines = items + charity
    amounts = scale_to(with_tax + [c for _, c, _ in charity], charged)
    day = re.search(r"^#\S+ (\d{4}-\d{2}-\d{2}) ", body, re.M)
    day = day.group(1) if day else headers.get("date", "")[:10]
    store = re.search(r"^\s*\*([^*\n]+)\*\s*$", body, re.M)
    rows = []
    for n, ((name, _, flags), amount) in enumerate(zip(lines, amounts)):
        department = "charity" if (name, _, flags) in charity else _department(name, flags)
        rows.append(
            {
                "item_id": hashlib.sha256(f"{CO_OP}|{message_id}|{n}".encode()).hexdigest()[:16],
                "service": CO_OP,
                "order_id": message_id,
                "kind": "item",
                "order_date": day,
                "date": day,
                "amount": amount / 100,
                "card": "",
                "department": department,
                "item": name,
                "website": store.group(1).strip() if store else CO_OP,
            }
        )
    return rows


CO_OP = "co_op"


VOID_MARKS = ("** Voided", "** Corrected")


ITEM_INFO = re.compile(r"^(\s*\d+(\.\d+)? lb @|\s*\d+ @ \$|\s*Ref:)")


CHARITY = re.compile(r"^charity:?$", re.I)


COUPONS = r"^\s*(?:Vendor coupon|Electronic store coupon)\s+\$(-?[\d,]+\.\d\d)"


def _is_co_op(body):
    return all(s in body for s in ("EMAIL RECEIPT", "Net Sales", "SUB TOTAL"))
