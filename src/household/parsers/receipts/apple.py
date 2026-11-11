"""App store receipts (Apple's and Google Play's) name what a charge bought rather than a store's
shelf, so their rows are read for matching as Google's purchase list is (explain.match_named): the
card line is named after its order's items, and review asks about that name, once for every renewal
of the same subscription.

Apple receipts (service "apple"), in two layouts, both in HTML: the older one ("ORDER ID", "DOCUMENT
NO.", each item under its store's section, such as Apple TV, then its name, a detail, its kind and the
device, "Report a Problem" and its price; the whole receipt printed twice, for wide screens and narrow,
so only the first copy is read) and the newer one ("Order ID:", "Document:", each item its app, its
plan, when it renews and the device, then its price, and "Billing and Payment" with the Subtotal and
Tax; an Apple Card offer above the items is skipped). Each item is "<section or app>: <name or plan>";
tax is shared by amount, so the items add to
what was charged; the order is keyed by its order id.
"""

import re

from .common import forgiving, item_row, line_after, parse_day, scale_to, to_cents, Unread

APPLE = "apple"


PRICE = re.compile(r"^(-?)\$([\d,]+\.\d\d)$")


CARD_LAST4 = re.compile(r"(?:Visa|Mastercard|MasterCard|Amex|American Express|Discover)\D{0,12}?(\d{4})")


APPLE_SECTIONS = {
    "Apple TV",
    "iTunes Store",
    "App Store",
    "Apple Books",
    "Apple Music",
    "Apple Arcade",
    "Apple Podcasts",
    "Apple Fitness+",
    "Apple News+",
    "Apple One",
    "iCloud",
}


APPLE_NOISE = {"Report a Problem", "Write a Review"}


APPLE_PROMO = re.compile(r"^Save \d+% on all your Apple purchases|Apple Card")


def _blocks(lines):
    """[(lines, cents)]: runs of lines each ended by a price line."""
    out, block = [], []
    for line in lines:
        price = PRICE.match(line)
        if price:
            out.append((block, (-1 if price[1] else 1) * to_cents(price[2])))
            block = []
        elif line not in APPLE_NOISE:
            block.append(line)
    return out


def _apple_rows(order, message_id, day, card, items, total):
    """Rows for one Apple order: the tax shared by amount so the items add to total."""
    if not items:
        return []
    if total <= 0:
        return []
    amounts = scale_to([c for _, c in items], total)
    return [
        {**item_row(APPLE, order or message_id, n, day, amount, "", name, "apple.com"), "card": card}
        for n, ((name, _), amount) in enumerate(zip(items, amounts))
    ]


@forgiving(APPLE)
def apple_itunes(message_id, headers, body):
    """order_items rows for one Apple receipt in the older layout (ORDER ID, DOCUMENT NO.)."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    doc = next(i for i, line in enumerate(lines) if line == "DOCUMENT NO.")
    end = next((i for i in range(doc, len(lines)) if lines[i] == "Subtotal"), None)
    if end is None:
        raise Unread(f"{APPLE} {message_id}: no Subtotal")
    items, section = [], ""
    for block, cents in _blocks(lines[doc + 2 : end]):
        if block and block[0] in APPLE_SECTIONS:
            section, block = block[0], block[1:]
        if not block:
            raise Unread(f"{APPLE} {message_id}: a price with no item")
        items.append(((f"{section}: " if section else "") + block[0], cents))
    totals = lines[end : end + 8]
    subtotal = (
        to_cents(line_after(totals, "Subtotal").lstrip("$")) if line_after(totals, "Subtotal").startswith("$") else None
    )
    tax = line_after(totals, "Tax")
    total = line_after(totals, "TOTAL")
    if subtotal is None or not total.startswith("$"):
        raise Unread(f"{APPLE} {message_id}: no Subtotal or TOTAL")
    total = to_cents(total.lstrip("$"))
    if (
        sum(c for _, c in items) != subtotal
        or subtotal + (to_cents(tax.lstrip("$")) if tax.startswith("$") else 0) != total
    ):
        raise Unread(f"{APPLE} {message_id}: the items, tax and TOTAL don't add up")
    billed = CARD_LAST4.search(" ".join(lines[:doc]))
    return _apple_rows(
        line_after(lines, "ORDER ID"),
        message_id,
        parse_day(line_after(lines, "DATE")) or headers.get("date", "")[:10],
        billed.group(1) if billed else "",
        items,
        total,
    )


@forgiving(APPLE)
def apple_receipt(message_id, headers, body):
    """order_items rows for one Apple receipt in the newer layout (Order ID:, Billing and Payment)."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    start = next(i for i, line in enumerate(lines) if line == "Document:") + 2
    if start < len(lines) and lines[start] == "Apple Account:":
        start += 2
    while start < len(lines) and APPLE_PROMO.search(lines[start]):  # an Apple Card offer above the items
        start += 1
    end = next((i for i in range(start, len(lines)) if lines[i] == "Billing and Payment"), None)
    if end is None:
        raise Unread(f"{APPLE} {message_id}: no Billing and Payment")
    items = []
    for block, cents in _blocks(lines[start:end]):
        if not block:
            raise Unread(f"{APPLE} {message_id}: a price with no item")
        plan = block[1] if len(block) > 1 and not block[1].startswith("Renews ") else ""
        items.append((f"{block[0]}: {plan}" if plan and plan != block[0] else block[0], cents))
    totals = lines[end:]
    sub = line_after(totals, "Subtotal")
    tax = line_after(totals, "Tax")
    if not sub.startswith("$"):
        raise Unread(f"{APPLE} {message_id}: no Subtotal")
    subtotal = to_cents(sub.lstrip("$"))
    total = subtotal + (to_cents(tax.lstrip("$")) if tax.startswith("$") else 0)
    if sum(c for _, c in items) != subtotal:
        raise Unread(f"{APPLE} {message_id}: the items don't add to the Subtotal")
    billed = CARD_LAST4.search(" ".join(totals))
    day = parse_day(lines[lines.index("Receipt") + 1]) if "Receipt" in lines[:start] else ""
    return _apple_rows(
        line_after(lines, "Order ID:"),
        message_id,
        day or headers.get("date", "")[:10],
        billed.group(1) if billed else "",
        items,
        total,
    )


def _is_apple_itunes(body):
    return all(s in body for s in ("ORDER ID", "DOCUMENT NO.", "TOTAL", "Apple"))


def _is_apple_receipt(body):
    return all(s in body for s in ("Order ID:", "Document:", "Billing and Payment", "Apple"))
