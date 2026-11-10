"""Store receipts from email, as the receipt exporter (exporter/Code.js) writes them into the inbox.

A file holds one or more messages, each starting with a line "===== RECEIPT <message id> =====",
then From, Subject and Date lines, a blank line and the body. Each receipt layout has a reader
(READERS, recognized by the body, not the sender, so no local store's name lives in this code; the
app stores, like Amazon and Google elsewhere, are named); a message in a layout with no reader yet is
counted and left out.

Co-op point-of-sale receipts (service "co_op"): the receipt lists each item with its price and its
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

Bookseller point-of-sale receipts (service "bookseller", the receipt emailed from the register):
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

App store receipts (Apple's and Google Play's) name what a charge bought rather than a store's
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

Google Play receipts (service "google", the same as the Takeout purchase list): "Order number:",
"Order date:", the items each ending in its price (per month or year for a subscription), the tax
lines and "Total:". One order is one row at its total, keyed as the Takeout reader keys a transaction
("google|<order number>|item"), so a purchase in both is read once. An item reads as the Takeout
reader names it: the app first ("Claude by Anthropic: Claude Pro Subscription").

Farm store register receipts (service "farm_store", the receipt printed one line at a time): each item
is its name, its price after any discount and its tax code (T taxed, N not), then a line with its
barcode (or NOT ON FILE) and quantity, then any discount and regular-price lines. The items add to
Subtotal, Subtotal and Total Sales Tax to Total, and the card was charged the Credit (or Debit) lines.
Tax goes to the taxed items by amount, then everything is scaled to what the cards were charged (a
reward or gift card that paid part). An untaxed item is food (groceries go untaxed where the store
is), a donation is charity, anything else is left to the default. A receipt not marked SALE (a return)
or paid without a card is left out.

Outfitter receipts (service "outfitter"), in two layouts: the store eReceipt ("Items purchased",
"Transaction type: Sale") and the online order confirmation ("Order number:", "Order date:"). Each
item is its name (and for an online order its color or size, in brackets), "Item #", "Qty:", the
price each and what it came to. Payment details list Subtotal, shipping, tax and the total (label then
value, or all the labels then all the values), then the member rewards used and what each card paid
("(ending in ...)"). A store sale is one charge, so tax and rewards are shared by amount; an online
order is charged as its parts ship, and the rewards come off the first items listed, as charged, so
each item reads at what its part cost the card and matching can take the parts one by one. A store
return, or an order the rewards paid in full, is left out.

PayPal emails (service "paypal", rows for payment_details, as the QIF download's are): a merchant's
receipt ("You paid" or "You authorized ... to", with the merchant's name and the transaction date),
PayPal's own receipt ("Payment to", the items in a table, Total), a refund ("Your Refund of ...
from") and money a person sent ("... sent you", with their note). Money out is negative. A payment is
keyed by PayPal's transaction id, so its receipt and its authorization are read once; a refund, which
prints none, by the message id.

Venmo emails (service "venmo", rows for payment_details): a payment sent ("You paid ..."), one received
("... paid you"), each with its note and transaction id, and a transfer to the bank ("Transfer Amount"), which
explain.match_venmo names after the payments received before it.

Audible order emails (service "audible", "Thanks, your order is complete" and "Your order is confirmed"),
in two layouts that read alike once a table is reduced to lines: "Order number:", then each title with what
it cost (a price, or "1 credit"), on its own line or the line below the title, its author and narrator,
and "Summary" with the Subtotal, Tax and "Total paid". A title paid with a credit cost nothing on the card,
so only priced titles are rows; the tax goes to them by amount. An order paid wholly in credits charged
nothing and is left out. The order is keyed by its order number, since Audible may send the same order
twice. A membership's monthly renewal sends no email, so its card lines are left to the payee's answer.

A receipt in one of these layouts that doesn't add up (a layout Apple or Google has since changed, say)
is left out with its id in the notes, never refusing the rest of the file.

A body still in HTML (a store that sends HTML in its plain-text part) is turned into text first,
the way the exporter does it.

The rows are order_items, one order per receipt, keyed by the message id, so a receipt exported
twice is read once. The household names each store's card lines with a parameter
(order_payee:<service>, see explain.order_services).
"""

import hashlib
import re
from datetime import datetime

import pandas as pd

from .amazon import COLUMNS
from .common import ParseError, Result
from .google_takeout import _item as _app_item

NAME = "email_receipts"
KIND = "email_receipts"
START = "===== RECEIPT "
MESSAGE = re.compile(r"^===== RECEIPT (\S+) =====$", re.M)
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
TOY_NAMES = re.compile(
    r"\b(puzzles?|jigsaw|stickers?|games?|toys?|lego|playmobil|plush|crayons?|coloring|"
    r"playing cards|kite|figures?|building set)\b",
    re.I,
)
MEMBERSHIP = re.compile(r"\bmembership\b", re.I)
HTML = re.compile(r"<(table|td|tr|div|br|pre)\b", re.I)
ENTITIES = {
    "nbsp": " ",
    "amp": "&",
    "quot": '"',
    "apos": "'",
    "rsquo": "'",
    "lsquo": "'",
    "lt": "<",
    "gt": ">",
    "zwnj": "",
    "shy": "",
    "copy": "",
}


def sniff(text: str) -> bool:
    return text.lstrip("﻿").startswith(START)


def _messages(text):
    """[(message id, {header: value}, body)] in the file."""
    out = []
    marks = list(MESSAGE.finditer(text))
    for n, m in enumerate(marks):
        chunk = text[m.end() : marks[n + 1].start() if n + 1 < len(marks) else len(text)].lstrip("\n")
        head, _, body = chunk.partition("\n\n")
        headers = {}
        for line in head.splitlines():
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        out.append((m.group(1), headers, body))
    return out


def _text(body):
    """The body as text: HTML turned into lines the way the exporter does it, anything else as is."""
    if not HTML.search(body):
        return body
    body = re.sub(r"<(style|script)[\s\S]*?</\1>", " ", body, flags=re.I)
    body = re.sub(r"<br\s*/?>|</(p|div|tr|li|h\d|table|pre)>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), body)
    body = re.sub(r"&#x([0-9a-f]+);", lambda m: chr(int(m.group(1), 16)), body, flags=re.I)
    body = re.sub(r"&([a-z]+);", lambda m: ENTITIES.get(m.group(1), m.group(0)), body)
    body = re.sub(r"[ \t\u00a0]+", " ", body.replace("\r", ""))
    return re.sub(r"\n\s*\n+", "\n", body)


class Unpriced(Exception):
    """A receipt that lists several items without their prices: it stays one charge."""


class Unread(Exception):
    """A receipt in a known layout that doesn't add up: left out, with why, in the notes."""


def _soft(service):
    """An app store reader whose surprises (a line missing, a number that won't read) leave the one receipt
    out with a note, as Unread, rather than refusing the file: those layouts change without notice."""

    def wrap(reader):
        def read(message_id, headers, body):
            try:
                return reader(message_id, headers, body)
            except Unread:
                raise
            except (StopIteration, IndexError, ValueError) as e:
                raise Unread(f"{service} {message_id}: {type(e).__name__} {e}".strip()) from e

        read.__name__ = reader.__name__
        return read

    return wrap


def _cents(text):
    return round(float(text.replace(",", "")) * 100)


def _money(text, pattern, what, required=True):
    found = re.search(pattern, text, re.M)
    if not found:
        if required:
            raise ParseError(f"{NAME}: no {what}")
        return None
    return round(float(found.group(1).replace(",", "")) * 100)


def _department(name, flags):
    if "F" in flags:
        return "grocery"
    if "T" in flags and ALCOHOL.search(name) and not re.search(r"\b(NA|non-?alcoholic)\b", name, re.I):
        return "alcohol"
    if TOYS.search(name):
        return "toys & games"
    return ""


def _scale(cents, total):
    """These amounts scaled so they add to total exactly, the cents left over on the biggest."""
    whole = sum(cents)
    if whole <= 0:
        raise ParseError(f"{NAME}: nothing priced")
    out = [round(c * total / whole) for c in cents]
    out[max(range(len(out)), key=lambda i: abs(out[i]))] += total - sum(out)
    return out


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
    net = _money(totals, r"Net Sales \$(-?[\d,]+\.\d\d)", "Net Sales")
    charged = _money(totals, r"SUB TOTAL \$(-?[\d,]+\.\d\d)", "SUB TOTAL")
    taxes = [
        (round(float(b) * 100), round(float(t) * 100))
        for b, t in re.findall(r"Tax \d+ \[\$(-?[\d.]+)\] \$(-?[\d.]+)", totals)
    ]
    coupons = sum(_cents(c) for c in re.findall(COUPONS, totals, re.M))
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
    amounts = _scale(with_tax + [c for _, c, _ in charity], charged)
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
BOOKSELLER = "bookseller"
REGISTER = re.compile(r"^\s*STR:\d+ REG:\d+ TRN:\d+", re.M)
SUBTOTAL = re.compile(r"^\s*Subtotal\s+-?[\d,]+\.\d\d", re.M)
BARCODE = re.compile(r"^(?P<code>\d{6,14})\s*(?P<tax>[A-Z]\d?)?$")
PRICED = re.compile(r"^\((?P<qty>-?\d+) @ (?P<each>[\d,]+\.\d\d)\)\s*(?P<rest>.*)$")
NET = re.compile(r"^(-?[\d,]+\.\d\d)$")
POSTED = re.compile(r"(\d{2})/(\d{2})/(\d{4})\s+\d{1,2}:\d{2}\s?[AP]M")


def _row(service, message_id, n, day, amount, department, name, website):
    return {
        "item_id": hashlib.sha256(f"{service}|{message_id}|{n}".encode()).hexdigest()[:16],
        "service": service,
        "order_id": message_id,
        "kind": "item",
        "order_date": day,
        "date": day,
        "amount": amount / 100,
        "card": "",
        "department": department,
        "item": name,
        "website": website,
    }


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
                items.append((" ".join(name.split()), _cents(net.group(1)), barcode, tax))
                current = None
            continue
        text = line
    totals = "\n".join(lines[end:])
    subtotal = _money(totals, r"^Subtotal\s+(-?[\d,]+\.\d\d)", "Subtotal")
    total = _money(totals, r"^TOTAL\s+(-?[\d,]+\.\d\d)", "TOTAL")
    taxes = {}
    for code, tax in re.findall(r"^Sales Tax (\S+) \([\d.]+%\)\s+(-?[\d,]+\.\d\d)", totals, re.M):
        taxes[code] = taxes.get(code, 0) + _cents(tax)
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
    amounts = _scale(with_tax, total)
    posted = POSTED.search(totals)
    day = f"{posted[3]}-{posted[1]}-{posted[2]}" if posted else headers.get("date", "")[:10]
    store = [line for line in lines[:start] if line][-4:]
    website = store[0] if store else BOOKSELLER
    rows = []
    for n, ((name, _, _, _), amount) in enumerate(zip(items, amounts)):
        department = _book_department(name)
        rows.append(_row(BOOKSELLER, message_id, n, day, amount, department, name, website))
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
    charged = sum(_cents(c) for c in re.findall(r"\(ending in\)\s*\d+:\s*\$([\d,]+\.\d\d)", body))
    if not items or charged <= 0:
        return []
    if len(items) > 1:
        raise Unpriced(BOOKSELLER)
    name = items[0]
    department = _book_department(name)
    return [_row(BOOKSELLER, message_id, 0, headers.get("date", "")[:10], charged, department, name, BOOKSELLER)]


APPLE = "apple"
GOOGLE = "google"
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


def _day(text, formats=("%B %d, %Y", "%b %d, %Y")):
    for f in formats:
        try:
            return datetime.strptime(text.strip(), f).date().isoformat()
        except ValueError:
            pass
    return ""


def _after(lines, label, start=0):
    """The line after the first line equal to label, from start; "" when there is none."""
    i = next((i for i in range(start, len(lines)) if lines[i] == label), None)
    return lines[i + 1] if i is not None and i + 1 < len(lines) else ""


def _blocks(lines):
    """[(lines, cents)]: runs of lines each ended by a price line."""
    out, block = [], []
    for line in lines:
        price = PRICE.match(line)
        if price:
            out.append((block, (-1 if price[1] else 1) * _cents(price[2])))
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
    amounts = _scale([c for _, c in items], total)
    return [
        {**_row(APPLE, order or message_id, n, day, amount, "", name, "apple.com"), "card": card}
        for n, ((name, _), amount) in enumerate(zip(items, amounts))
    ]


@_soft(APPLE)
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
    subtotal = _cents(_after(totals, "Subtotal").lstrip("$")) if _after(totals, "Subtotal").startswith("$") else None
    tax = _after(totals, "Tax")
    total = _after(totals, "TOTAL")
    if subtotal is None or not total.startswith("$"):
        raise Unread(f"{APPLE} {message_id}: no Subtotal or TOTAL")
    total = _cents(total.lstrip("$"))
    if (
        sum(c for _, c in items) != subtotal
        or subtotal + (_cents(tax.lstrip("$")) if tax.startswith("$") else 0) != total
    ):
        raise Unread(f"{APPLE} {message_id}: the items, tax and TOTAL don't add up")
    billed = CARD_LAST4.search(" ".join(lines[:doc]))
    return _apple_rows(
        _after(lines, "ORDER ID"),
        message_id,
        _day(_after(lines, "DATE")) or headers.get("date", "")[:10],
        billed.group(1) if billed else "",
        items,
        total,
    )


@_soft(APPLE)
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
    sub = _after(totals, "Subtotal")
    tax = _after(totals, "Tax")
    if not sub.startswith("$"):
        raise Unread(f"{APPLE} {message_id}: no Subtotal")
    subtotal = _cents(sub.lstrip("$"))
    total = subtotal + (_cents(tax.lstrip("$")) if tax.startswith("$") else 0)
    if sum(c for _, c in items) != subtotal:
        raise Unread(f"{APPLE} {message_id}: the items don't add to the Subtotal")
    billed = CARD_LAST4.search(" ".join(totals))
    day = _day(lines[lines.index("Receipt") + 1]) if "Receipt" in lines[:start] else ""
    return _apple_rows(
        _after(lines, "Order ID:"),
        message_id,
        day or headers.get("date", "")[:10],
        billed.group(1) if billed else "",
        items,
        total,
    )


GOOGLE_ITEM = re.compile(r"^(?P<name>.*\S)\s+\$(?P<price>[\d,]+\.\d\d)(?:/\w+)?$")
GOOGLE_TAX = re.compile(r"^(?:[A-Za-z ]*\s)?tax:\s*\$([\d,]+\.\d\d)$", re.I)
GOOGLE_TOTAL = re.compile(r"^Total:\s*\$([\d,]+\.\d\d)(?:/\w+)?$")


@_soft(GOOGLE)
def google_play(message_id, headers, body):
    """One order_items row for one Google Play receipt, at what was charged, keyed as Takeout keys it."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    number = next(line for line in lines if line.startswith("Order number:")).split(":", 1)[1].strip()
    when = next((line for line in lines if line.startswith("Order date:")), "Order date:").split(":", 1)[1]
    day = _day(" ".join(when.split()[:3])) or headers.get("date", "")[:10]
    head = next((i for i, line in enumerate(lines) if line in ("Item Price", "Price")), None)
    total_at = next((i for i, line in enumerate(lines) if GOOGLE_TOTAL.match(line)), None)
    if head is None or total_at is None:
        raise Unread(f"{GOOGLE} {message_id}: no items or no Total")
    items, taxes, text = [], 0, ""
    for line in lines[head + 1 : total_at]:
        tax = GOOGLE_TAX.match(line)
        if tax:
            taxes += _cents(tax.group(1))
            continue
        if line == "Auto-renewing subscription":
            continue
        priced = GOOGLE_ITEM.match(f"{text} {line}".strip())
        if priced:
            items.append((re.sub(r"\s*\(by [^()]*\)$", "", priced["name"]), _cents(priced["price"])))
            text = ""
        else:
            text = f"{text} {line}".strip()  # a long name wraps onto the next line
    total = _cents(GOOGLE_TOTAL.match(lines[total_at]).group(1))
    if not items or sum(c for _, c in items) + taxes != total:
        raise Unread(f"{GOOGLE} {message_id}: the items and tax don't add to the Total")
    if total <= 0:
        return []
    paid = _after(lines, "Payment method:")
    card = re.search(r"(\d{4})\s*$", paid)
    name = "; ".join(_app_item(n, "") for n, _ in items)
    return [
        {
            "item_id": hashlib.sha256(f"{GOOGLE}|{number}|item".encode()).hexdigest()[:16],
            "service": GOOGLE,
            "order_id": number,
            "kind": "item",
            "order_date": day,
            "date": day,
            "amount": total / 100,
            "card": card.group(1) if card else "",
            "department": "Google Play",
            "item": name,
            "website": "",
        }
    ]


FARM_STORE = "farm_store"
FARM_ITEM = re.compile(r"^(?P<name>.*\S)\s+(?P<price>-?[\d,]+\.\d\d)\s+(?P<tax>[A-Z])$")
FARM_CODE = re.compile(r"^(?:\d{6,14}|NOT ON FILE) x \d+\b")
FARM_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})\s+\d{1,2}:\d{2}\s?[AP]M$")
FARM_TENDER = re.compile(r"^(?:Credit|Debit)\s+([\d,]+\.\d\d)$")
DONATION = re.compile(r"\bdonation\b", re.I)


@_soft(FARM_STORE)
def farm_store(message_id, headers, body):
    """order_items rows for one farm store register receipt, or [] for a return or one paid in cash."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    rules = [i for i, line in enumerate(lines) if re.fullmatch(r"_{20,}", line)]
    if len(rules) < 2:
        raise Unread(f"{FARM_STORE} {message_id}: no item list")
    if "SALE" not in lines[rules[0] : rules[1]]:
        return []
    end = next((i for i in range(rules[1], len(lines)) if lines[i].startswith("Subtotal ")), None)
    if end is None:
        raise Unread(f"{FARM_STORE} {message_id}: no Subtotal")
    items = []
    for n in range(rules[1] + 1, end):
        m = FARM_ITEM.match(lines[n])
        if m and n + 1 < end and FARM_CODE.match(lines[n + 1]):  # the price line is the item's, after discounts
            items.append((" ".join(m["name"].split()), _cents(m["price"]), m["tax"]))
    totals = lines[end:]
    subtotal = _money("\n".join(totals), r"^Subtotal\s+(-?[\d,]+\.\d\d)$", "Subtotal")
    tax = _money("\n".join(totals), r"^Total Sales Tax\s+(-?[\d,]+\.\d\d)$", "tax", required=False) or 0
    total = _money("\n".join(totals), r"^Total\s+(-?[\d,]+\.\d\d)$", "Total")
    if not items or sum(c for _, c, _ in items) != subtotal or subtotal + tax != total:
        raise Unread(f"{FARM_STORE} {message_id}: the items, tax and Total don't add up")
    charged = sum(_cents(m.group(1)) for m in map(FARM_TENDER.match, totals) if m)
    if charged <= 0:
        return []
    base = sum(c for _, c, t in items if t == "T")
    with_tax = [c + (round(tax * c / base) if base and t == "T" else 0) for _, c, t in items]
    amounts = _scale(with_tax, charged)  # a reward or gift card that paid part comes off every item alike
    when = next((FARM_DAY.match(line) for line in lines[rules[0] : rules[1]] if FARM_DAY.match(line)), None)
    day = f"20{when[3]}-{int(when[1]):02d}-{int(when[2]):02d}" if when else headers.get("date", "")[:10]
    store = lines[lines.index("YOUR RECEIPT") + 1] if "YOUR RECEIPT" in lines[: rules[0]] else FARM_STORE
    rows = []
    for n, ((name, _, tax_code), amount) in enumerate(zip(items, amounts)):
        department = "charity" if DONATION.search(name) else "grocery" if tax_code == "N" else ""
        rows.append(_row(FARM_STORE, message_id, n, day, amount, department, name, store))
    return rows


OUTFITTER = "outfitter"
DOLLARS = re.compile(r"^(-?)\$([\d,]+\.\d\d)$")
PAID_WITH = re.compile(r"\(ending in \d{4}\)")
SHIP_NOISE = re.compile(
    r"^(Estimated arrival|Standard shipping|Expedited|Ship to|Items to ship|"
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \w{3} \d{1,2}$)"
)


def _priced_lines(section):
    """{label: cents} for a totals section printed label then value, or all the labels then all the values."""
    labels = [line for line in section if not DOLLARS.match(line) and line != "FREE"]
    values = [
        0 if line == "FREE" else (-1 if DOLLARS.match(line)[1] else 1) * _cents(DOLLARS.match(line)[2])
        for line in section
        if DOLLARS.match(line) or line == "FREE"
    ]
    if len(labels) != len(values):
        raise ValueError("the totals' labels and values don't pair up")
    return dict(zip(labels, values))


@_soft(OUTFITTER)
def outfitter(message_id, headers, body):
    """order_items rows for one outfitter store eReceipt or online order confirmation, or [] for a return or one
    paid in full with rewards."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    online = "Order number:" in lines
    kind = next((line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("transaction type:")), "")
    if not online and kind.lower() != "sale":
        return []
    start = (
        lines.index("Items purchased") + 1
        if not online
        else next(i for i, line in enumerate(lines) if line.startswith("Estimated arrival")) + 1
    )
    end = lines.index("Payment details")
    items, text, n = [], [], start
    while n < end:
        line = lines[n]
        if line.startswith("Item #"):
            qty = next(i for i in range(n, end) if lines[i].startswith("Qty:"))
            prices = [i for i in range(qty + 1, end) if DOLLARS.match(lines[i])]
            last = prices[0]
            while last + 1 < end and DOLLARS.match(lines[last + 1]):
                last += 1
            name = [t for t in text if not SHIP_NOISE.match(t)]
            if not name:
                raise ValueError("an item with no name")
            m = DOLLARS.match(lines[last])
            items.append(
                (name[0] + (f" ({', '.join(name[1:])})" if len(name) > 1 else ""), (-1 if m[1] else 1) * _cents(m[2]))
            )
            text, n = [], last + 1
            continue
        text.append(line)
        n += 1
    totals = lines[end + 1 :]
    upto = next(i for i, line in enumerate(totals) if line in ("Total", "Order total"))
    priced = _priced_lines(totals[: upto + 2])
    subtotal, total = priced["Subtotal"], priced.get("Total", priced.get("Order total"))
    if not items or sum(c for _, c in items) != subtotal or sum(priced.values()) - total != total:
        raise Unread(f"{OUTFITTER} {message_id}: the items, tax and total don't add up")
    charged = 0
    for i, line in enumerate(totals[:-1]):
        if PAID_WITH.search(line) and DOLLARS.match(totals[i + 1]):
            charged += _cents(DOLLARS.match(totals[i + 1])[2])
    if charged <= 0:
        return []
    if online:  # shipped in parts, charged as each part ships: rewards come off the first items, as charged
        amounts, left = [], total - charged
        for amount in _scale([c for _, c in items], total):
            off = min(amount, left)
            amounts.append(amount - off)
            left -= off
    else:  # one charge: tax and any rewards used shared by amount
        amounts = _scale([c for _, c in items], charged)
    if online:
        order = lines[lines.index("Order number:") + 1]
        when = re.search(r"Order date:\s*(\d{2})/(\d{2})/(\d{4})", body)
        day = f"{when[3]}-{when[1]}-{when[2]}" if when else headers.get("date", "")[:10]
    else:
        order = message_id
        when = re.search(r"Date:\s*(\d{2})/(\d{2})/(\d{2})\s", body)
        day = f"20{when[3]}-{when[1]}-{when[2]}" if when else headers.get("date", "")[:10]
    website = (
        "online"
        if online
        else next((lines[i + 1] for i, line in enumerate(lines[:-1]) if line == "Purchased at:"), OUTFITTER)
    )
    return [
        _row(OUTFITTER, order, n, day, amount, "", name, website)
        for n, ((name, _), amount) in enumerate(zip(items, amounts))
        if amount > 0
    ]


PAYPAL = "paypal"
PAYPAL_ID = re.compile(r"Transaction ID:?\s*\n?\s*([0-9A-Z]{17})\b")
PAYPAL_PAID = re.compile(r"^You (paid|authorized) \$([\d,]+\.\d\d) USD (?:payment )?to (.+?)\.?$", re.M)
PAYPAL_SENT = re.compile(r"^(.+?) sent you \$([\d,]+\.\d\d) USD$", re.M)
PAYPAL_REFUND = re.compile(r"^Your Refund of \$([\d,]+\.\d\d) USD from (.+?)\.?$", re.M)
PAYPAL_TOTAL = re.compile(r"^\$([\d,]+\.\d\d) USD$")


@_soft(PAYPAL)
def paypal(message_id, headers, body):
    """One payment_details row for one PayPal email: a payment to a merchant (its receipt, or the merchant's own
    layout), money a person sent, or a refund. The row is shaped as the QIF download's are, keyed by PayPal's
    transaction id (a refund, which shows none, by the message id)."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    text = "\n".join(lines)
    txn = PAYPAL_ID.search(text)
    memo = ""
    paid, sent, refund = PAYPAL_PAID.search(text), PAYPAL_SENT.search(text), PAYPAL_REFUND.search(text)
    if refund:
        kind, cents, who = "Payment Refund", _cents(refund[1]), _after(lines, "Refund from") or refund[2]
    elif paid:  # the merchant's name in full where it is printed (the sentence drops a last ".")
        kind, cents, who = "Payment", -_cents(paid[2]), _after(lines, "Merchant") or paid[3]
    elif sent:
        kind, cents, who = "Payment Received", _cents(sent[2]), sent[1]
        note = _after(lines, f"Note from {who}")
        memo = "" if note in ("Transaction date", "Amount") else note
    elif "Payment to" in lines:  # the receipt layout: the items in a table, then Total
        kind, who = "Payment", _after(lines, "Payment to")
        at = lines.index("Total", lines.index("Payment to"))
        cents = -_cents(PAYPAL_TOTAL.match(lines[at + 1])[1])
        if "Description" in lines:
            head = lines.index("Amount", lines.index("Description")) + 1
            names, row = [], []
            for line in lines[head:at]:
                if line in ("Shipping and handling", "Sales Tax", "Discount", "Insurance", "Handling"):
                    break
                row.append(line)
                if PAYPAL_TOTAL.match(line) and len(row) >= 4:  # name, unit price, quantity, amount
                    names.append(" ".join(row[:-3]))
                    row = []
            memo = "; ".join(names)
    else:
        raise Unread(f"{PAYPAL} {message_id}: no payment, refund or money received")
    when = _after(lines, "Transaction date") or _after(lines, "Date")
    day = _day(when) or headers.get("date", "")[:10]
    key = txn.group(1) if txn and not refund else message_id
    return [
        {
            "detail_id": hashlib.sha256(f"{PAYPAL}|{key}".encode()).hexdigest()[:16],
            "date": day,
            "amount": cents / 100,
            "service": PAYPAL,
            "kind": kind,
            "counterparty": " ".join(who.split()),
            "memo": " ".join(memo.split()),
        }
    ]


VENMO = "venmo"
VENMO_PAID = re.compile(r"^You paid ([^$]+)$")
VENMO_RECEIVED = re.compile(r"^([^$]+) paid you$")
VENMO_AMOUNT = re.compile(r"\$([\d,]+\.\d\d)")


@_soft(VENMO)
def venmo(message_id, headers, body):
    """One payment_details row for one Venmo email: a payment sent ("You paid ..."), one received ("... paid
    you"), each with its note, or a transfer to the bank ("Transfer Amount"). Money out is negative; a transfer
    is positive, as it lands in the bank. Keyed by Venmo's transaction id."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if "Transfer Amount" in lines:
        amount = VENMO_AMOUNT.search(_after(lines, "Transfer Amount"))
        txn = _after(lines, "Transfer transaction ID")
        if not amount or not txn:
            raise Unread(f"{VENMO} {message_id}: a transfer with no amount or id")
        day = _day(_after(lines, "Initiated on"), ("%A, %B %d, %Y",)) or headers.get("date", "")[:10]
        return [
            {
                "detail_id": hashlib.sha256(f"{VENMO}|transfer|{txn}".encode()).hexdigest()[:16],
                "date": day,
                "amount": _cents(amount[1]) / 100,
                "service": VENMO,
                "kind": "Transfer",
                "counterparty": "",
                "memo": "",
            }
        ]
    at = next((i for i, line in enumerate(lines) if VENMO_PAID.match(line) or VENMO_RECEIVED.match(line)), None)
    if at is None:
        raise Unread(f"{VENMO} {message_id}: no payment sent or received")
    paid = VENMO_PAID.match(lines[at])
    who = paid[1] if paid else VENMO_RECEIVED.match(lines[at])[1]
    amount = VENMO_AMOUNT.search(lines[0]) or VENMO_AMOUNT.search(headers.get("subject", ""))
    txn = _after(lines, "Transaction ID")
    if not amount or not txn:
        raise Unread(f"{VENMO} {message_id}: no amount or transaction id")
    # the amount is printed as "$", "12", ". 50" after the sentence; the note follows it, up to "See transaction"
    end = next((i for i in range(at, len(lines)) if lines[i].startswith("See transaction")), at)
    note = [line for line in lines[at + 1 : end] if not re.fullmatch(r"\$|[\d,]+|\.\s?\d\d", line)]
    cents = _cents(amount[1])
    return [
        {
            "detail_id": hashlib.sha256(f"{VENMO}|{txn}".encode()).hexdigest()[:16],
            "date": _day(_after(lines, "Date")) or headers.get("date", "")[:10],
            "amount": (-cents if paid else cents) / 100,
            "service": VENMO,
            "kind": "Payment" if paid else "Payment Received",
            "counterparty": " ".join(who.split()),
            "memo": " ".join(" ".join(note).split()),
        }
    ]


AUDIBLE = "audible"
AUDIBLE_PRICED = re.compile(r"^(?P<name>.*?)\s*(?:\$(?P<price>[\d,]+\.\d\d)|(?P<credits>\d+) credits?)$", re.I)
AUDIBLE_ORDER = re.compile(r"Order number:\s*(D\d{2}-\d{7}-\d{7})")
AUDIBLE_ABOUT = ("Author:", "Narrated by:", "Written", "Visit library", "http", "Apple In-App Purchase")


def _audible_lines(body):
    """The body's lines with a table's cell bars and a heading's marks taken out."""
    out = []
    for line in body.splitlines():
        line = " ".join(line.replace("|", " ").split()).strip("#* ")
        if line:
            out.append(line)
    return out


@_soft(AUDIBLE)
def audible(message_id, headers, body):
    """order_items rows for one Audible order, or [] for one paid wholly in credits."""
    order = AUDIBLE_ORDER.search(body)
    if not order:
        raise Unread(f"{AUDIBLE} {message_id}: no order number")
    lines = _audible_lines(body)
    start = next(i for i, line in enumerate(lines) if AUDIBLE_ORDER.search(line))
    end = next(i for i in range(start, len(lines)) if lines[i] == "Summary")
    items, title = [], ""
    first = AUDIBLE_ORDER.split(lines[start], maxsplit=1)[-1]  # an item glued to the order number's line
    for line in [first] + lines[start + 1 : end]:
        priced = AUDIBLE_PRICED.match(line)
        if priced and (priced["price"] or not line.lower().startswith("title")):
            name = priced["name"].strip() or title
            if name:
                items.append((" ".join(name.split()), _cents(priced["price"]) if priced["price"] else 0))
            title = ""
        elif line and not line.startswith(AUDIBLE_ABOUT) and line != "Title Amount":
            title = line
    summary = "\n".join(lines[end:])
    subtotal = _money(summary, r"^Subtotal:\s*\$([\d,]+\.\d\d)", "Subtotal")
    tax = _money(summary, r"^Tax:\s*\$([\d,]+\.\d\d)", "Tax")
    total = _money(summary, r"^Total paid:\s*\$([\d,]+\.\d\d)", "Total paid")
    paid = [(name, cents) for name, cents in items if cents > 0]
    if not items:
        raise Unread(f"{AUDIBLE} {message_id}: no titles")
    if sum(c for _, c in paid) != subtotal:
        raise Unread(
            f"{AUDIBLE} {message_id}: the titles add to {sum(c for _, c in paid) / 100:.2f}, Subtotal is {subtotal / 100:.2f}"
        )
    if subtotal + tax != total:
        raise Unread(f"{AUDIBLE} {message_id}: Subtotal and Tax don't add to Total paid")
    if total <= 0:
        return []
    amounts = _scale([c for _, c in paid], total)
    day = headers.get("date", "")[:10]
    return [
        _row(AUDIBLE, order[1], n, day, amount, "audiobooks", name, "audible.com")
        for n, ((name, _), amount) in enumerate(zip(paid, amounts))
    ]


def _is_audible(body):
    return "Audible, Inc." in body and "Order number:" in body and "Total paid:" in body


def _is_venmo(body):
    return "Venmo is a service of PayPal" in body and ("Transaction ID" in body or "Transfer Amount" in body)


def _is_farm_store(body):
    return all(s in body for s in ("Cashier #:", "Total Sales Tax", "Subtotal")) and bool(
        re.search(r"^\s*(?:\d{6,14}|NOT ON FILE) x \d+\b", body, re.M)
    )


def _is_outfitter(body):
    return (
        "Payment details" in body
        and "Item #" in body
        and "Qty:" in body
        and ("Items purchased" in body or "Order number:" in body)
    )


def _is_paypal(body):
    return "PayPal, Inc." in body and any(
        s in body
        for s in (
            "You paid $",
            "You authorized $",
            " sent you $",
            "Your Refund of $",
            "Receipt for your PayPal payment",
        )
    )


def _is_apple_itunes(body):
    return all(s in body for s in ("ORDER ID", "DOCUMENT NO.", "TOTAL", "Apple"))


def _is_apple_receipt(body):
    return all(s in body for s in ("Order ID:", "Document:", "Billing and Payment", "Apple"))


def _is_google_play(body):
    return all(s in body for s in ("Google Play", "Order number:", "Total:"))


def _is_co_op(body):
    return all(s in body for s in ("EMAIL RECEIPT", "Net Sales", "SUB TOTAL"))


def _is_bookseller(body):
    return bool(REGISTER.search(body) and SUBTOTAL.search(body))


def _is_bookseller_online(body):
    return "Item(s) Shipped/Billed" in body and "Order Subtotal:" in body


READERS = (
    (_is_co_op, co_op),
    (_is_bookseller, bookseller),
    (_is_bookseller_online, bookseller_online),
    (_is_apple_itunes, apple_itunes),
    (_is_apple_receipt, apple_receipt),
    (_is_google_play, google_play),
    (_is_farm_store, farm_store),
    (_is_outfitter, outfitter),
    (_is_audible, audible),
    (_is_venmo, venmo),
    (_is_paypal, paypal),
)
DETAIL_COLUMNS = ["detail_id", "date", "amount", "service", "kind", "counterparty", "memo"]


def parse(text: str) -> Result:
    rows: list = []
    details: list = []
    skipped: dict = {}
    unpriced: dict = {}
    empty, unreadable, unread = 0, [], []
    for message_id, headers, body in _messages(text):
        if "unreadable" in headers:  # the exporter could not read it; it tries again every run
            unreadable.append(f"{message_id} ({headers['unreadable']})")
            continue
        body = _text(body)
        reader = next((r for recognizes, r in READERS if recognizes(body)), None)
        if reader is None:
            key = re.sub(r".*@", "", headers.get("from", "").lower()).strip("> ") or "unknown"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        try:
            found = reader(message_id, headers, body)
        except Unpriced as e:
            unpriced[str(e)] = unpriced.get(str(e), 0) + 1
            continue
        except Unread as e:
            unread.append(str(e))
            continue
        rows += [r for r in found if "item_id" in r]
        details += [r for r in found if "detail_id" in r]
        if not found:
            empty += 1
    df = pd.DataFrame(rows, columns=COLUMNS)
    paid = pd.DataFrame(details, columns=DETAIL_COLUMNS).drop_duplicates("detail_id")
    dates = sorted(d for d in list(df["date"]) + list(paid["date"]) if d)
    notes = [f"receipts: {df['order_id'].nunique()} read, {len(df)} items"]
    if len(paid):
        for service, n in paid["service"].value_counts().sort_index().items():
            notes.append(f"{'PayPal' if service == PAYPAL else 'Venmo'}: {n} payments and transfers")
    notes += [f"receipts: {n} from {k} left for a reader not written yet" for k, n in sorted(skipped.items())]
    notes += [
        f"receipts: {n} {k} charges for several items without prices left as one charge"
        for k, n in sorted(unpriced.items())
    ]
    if empty:
        notes.append(f"receipts: {empty} that charged nothing (a return, a free order) left out")
    if unread:
        notes.append(f"receipts: {len(unread)} in a known layout that didn't add up, left out: " + "; ".join(unread))
    if unreadable:
        notes.append(
            f"receipts: {len(unreadable)} the exporter could not read, tried again on its every run "
            "(look in Gmail by id): " + "; ".join(unreadable)
        )
    return Result(
        kind=KIND,
        parser=NAME,
        label=f"receipts_{dates[-1] if dates else 'none'}",
        covers_from=dates[0] if dates else "",
        covers_to=dates[-1] if dates else "",
        tables={k: v for k, v in (("order_items", df), ("payment_details", paid)) if len(v)},
        keys={"order_items": ["item_id"], "payment_details": ["detail_id"]},
        notes=notes,
    )
