"""Bookseller point-of-sale receipts (service "bookseller", the receipt emailed from the register):
each item is its name, a line with its barcode and tax code (T1), then one line per discount and a
last "(qty @ price)  net" line with what it came to. The items add to Subtotal, the tax lines (by
tax code) and Subtotal add to TOTAL, and the card was charged TOTAL. Tax goes to the items with that
tax code, by amount. An item whose name says toys, games, puzzles or the like is toys and games, a
membership is a membership (subscriptions), and anything else is books (the store prints book-like
codes on everything, snacks included, so the barcode cannot tell them apart).

Bookseller online billing summaries (the same service): the charge for what shipped, with item
names and quantities but no prices. A summary for one item reads as that item, at what the cards
were charged; one for several items cannot be shared out by price, so it is counted and left as one
charge.
"""

import re

from ..common import ParseError
from .common import item_row, money_in, NAME, scale_to, to_cents, Unpriced

TOY_NAMES = re.compile(
    r"\b(puzzles?|jigsaw|stickers?|games?|toys?|lego|playmobil|plush|crayons?|coloring|"
    r"playing cards|kite|figures?|building set)\b",
    re.I,
)


MEMBERSHIP = re.compile(r"\bmembership\b", re.I)


BOOKSELLER = "bookseller"


REGISTER = re.compile(r"^\s*STR:\d+ REG:\d+ TRN:\d+", re.M)


SUBTOTAL = re.compile(r"^\s*Subtotal\s+-?[\d,]+\.\d\d", re.M)


BARCODE = re.compile(r"^(?P<code>\d{6,14})\s*(?P<tax>[A-Z]\d?)?$")


PRICED = re.compile(r"^\((?P<qty>-?\d+) @ (?P<each>[\d,]+\.\d\d)\)\s*(?P<rest>.*)$")


NET = re.compile(r"^(-?[\d,]+\.\d\d)$")


POSTED = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+\d{1,2}:\d{2}\s?[AP]M")


def _book_department(name):
    if MEMBERSHIP.search(name):
        return "membership"
    return "toys & games" if TOY_NAMES.search(name) else "books"


def bookseller(message_id, headers, body):
    """order_items rows for one bookseller register receipt, or [] for a return or a free receipt."""
    lines = [line.strip() for line in body.splitlines()]
    start = next(i for i, line in enumerate(lines) if REGISTER.match(line))
    end = next((i for i in range(start, len(lines)) if SUBTOTAL.match(lines[i])), None)
    if end is None:
        raise ParseError(f"{NAME}: bookseller receipt {message_id}: no Subtotal")
    items, text, current = [], "", None
    for line in lines[start + 1 : end]:
        if not line or line.startswith("#"):
            continue
        code = BARCODE.match(line)
        if code:
            current = (text, code["code"], code["tax"] or "")
            continue
        priced = PRICED.match(line)
        if priced:
            net = NET.match(priced["rest"].strip())
            if net:  # the item's last line: what it came to after its discounts
                name, barcode, tax = current or (text, "", "")
                items.append((" ".join(name.split()), to_cents(net.group(1)), barcode, tax))
                current = None
            continue
        text = line
    totals = "\n".join(lines[end:])
    subtotal = money_in(totals, r"^Subtotal\s+(-?[\d,]+\.\d\d)", "Subtotal")
    total = money_in(totals, r"^TOTAL\s+(-?[\d,]+\.\d\d)", "TOTAL")
    taxes = {}
    for code, tax in re.findall(r"^Sales Tax (\S+) \([\d.]+%\)\s+(-?[\d,]+\.\d\d)", totals, re.M):
        taxes[code] = taxes.get(code, 0) + to_cents(tax)
    if not items or total <= 0:
        return []
    if sum(c for _, c, _, _ in items) != subtotal:
        raise ParseError(
            f"{NAME}: bookseller receipt {message_id}: items add to "
            f"{sum(c for _, c, _, _ in items) / 100:.2f}, Subtotal is {subtotal / 100:.2f}"
        )
    if subtotal + sum(taxes.values()) != total:
        raise ParseError(
            f"{NAME}: bookseller receipt {message_id}: Subtotal and tax add to "
            f"{(subtotal + sum(taxes.values())) / 100:.2f}, TOTAL is {total / 100:.2f}"
        )
    bases = {code: sum(c for _, c, _, t in items if t == code) for code in taxes}
    with_tax = [c + (round(taxes[t] * c / bases[t]) if bases.get(t) else 0) for _, c, _, t in items]
    amounts = scale_to(with_tax, total)
    posted = POSTED.search(totals)
    day = f"{posted[3]}-{posted[1]}-{posted[2]}" if posted else headers.get("date", "")[:10]
    store = [line for line in lines[:start] if line][-4:]
    website = store[0] if store else BOOKSELLER
    rows = []
    for n, ((name, _, _, _), amount) in enumerate(zip(items, amounts)):
        department = _book_department(name)
        rows.append(item_row(BOOKSELLER, message_id, n, day, amount, department, name, website))
    return rows


def bookseller_online(message_id, headers, body):
    """order_items rows for one bookseller billing summary that bills one item, or [] for none charged."""
    lines = [line.strip() for line in body.splitlines()]
    start = next(i for i, line in enumerate(lines) if line.startswith("Item(s) Shipped/Billed"))
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("Order Subtotal:")), None)
    if end is None:
        raise ParseError(f"{NAME}: bookseller billing summary {message_id}: no Order Subtotal")
    items, name = [], ""
    for line in lines[start + 1 : end]:  # a name and its quantity, on one line or two
        if not line or line == "Qty":
            continue
        if line.isdigit() and name:
            items.append(name)
            name = ""
            continue
        m = re.match(r"^(?P<name>.*\S)\s+(?P<qty>\d+)$", line)
        if m and not name:
            items.append(" ".join(m["name"].split()))
        else:
            name = " ".join(line.split())
    charged = sum(to_cents(c) for c in re.findall(r"\(ending in\)\s*\d+:\s*\$([\d,]+\.\d\d)", body))
    if not items or charged <= 0:
        return []
    if len(items) > 1:
        raise Unpriced(BOOKSELLER)
    name = items[0]
    department = _book_department(name)
    return [item_row(BOOKSELLER, message_id, 0, headers.get("date", "")[:10], charged, department, name, BOOKSELLER)]


def _is_bookseller(body):
    return bool(REGISTER.search(body) and SUBTOTAL.search(body))


def _is_bookseller_online(body):
    return "Item(s) Shipped/Billed" in body and "Order Subtotal:" in body
