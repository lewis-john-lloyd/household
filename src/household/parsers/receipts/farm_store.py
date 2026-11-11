"""Farm store register receipts (service "farm_store", the receipt printed one line at a time): each item
is its name, its price after any discount and its tax code (T taxed, N not), then a line with its
barcode (or NOT ON FILE) and quantity, then any discount and regular-price lines. The items add to
Subtotal, Subtotal and Total Sales Tax to Total, and the card was charged the Credit (or Debit) lines.
Tax goes to the taxed items by amount, then everything is scaled to what the cards were charged (a
reward or gift card that paid part). An untaxed item is food (groceries go untaxed where the store
is), a donation is charity, anything else is left to the default. A receipt not marked SALE (a return)
or paid without a card is left out.
"""

import re

from .common import forgiving, item_row, money_in, scale_to, to_cents, Unread

FARM_STORE = "farm_store"


FARM_ITEM = re.compile(r"^(?P<name>.*\S)\s+(?P<price>-?[\d,]+\.\d\d)\s+(?P<tax>[A-Z])$")


FARM_CODE = re.compile(r"^(?:\d{6,14}|NOT ON FILE) x \d+\b")


FARM_DAY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})\s+\d{1,2}:\d{2}\s?[AP]M$")


FARM_TENDER = re.compile(r"^(?:Credit|Debit)\s+([\d,]+\.\d\d)$")


DONATION = re.compile(r"\bdonation\b", re.I)


@forgiving(FARM_STORE)
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
            items.append((" ".join(m["name"].split()), to_cents(m["price"]), m["tax"]))
    totals = lines[end:]
    subtotal = money_in("\n".join(totals), r"^Subtotal\s+(-?[\d,]+\.\d\d)$", "Subtotal")
    tax = money_in("\n".join(totals), r"^Total Sales Tax\s+(-?[\d,]+\.\d\d)$", "tax", required=False) or 0
    total = money_in("\n".join(totals), r"^Total\s+(-?[\d,]+\.\d\d)$", "Total")
    if not items or sum(c for _, c, _ in items) != subtotal or subtotal + tax != total:
        raise Unread(f"{FARM_STORE} {message_id}: the items, tax and Total don't add up")
    charged = sum(to_cents(m.group(1)) for m in map(FARM_TENDER.match, totals) if m)
    if charged <= 0:
        return []
    base = sum(c for _, c, t in items if t == "T")
    with_tax = [c + (round(tax * c / base) if base and t == "T" else 0) for _, c, t in items]
    amounts = scale_to(with_tax, charged)  # a reward or gift card that paid part comes off every item alike
    when = next((FARM_DAY.match(line) for line in lines[rules[0] : rules[1]] if FARM_DAY.match(line)), None)
    day = f"20{when[3]}-{int(when[1]):02d}-{int(when[2]):02d}" if when else headers.get("date", "")[:10]
    store = lines[lines.index("YOUR RECEIPT") + 1] if "YOUR RECEIPT" in lines[: rules[0]] else FARM_STORE
    rows = []
    for n, ((name, _, tax_code), amount) in enumerate(zip(items, amounts)):
        department = "charity" if DONATION.search(name) else "grocery" if tax_code == "N" else ""
        rows.append(item_row(FARM_STORE, message_id, n, day, amount, department, name, store))
    return rows


def _is_farm_store(body):
    return all(s in body for s in ("Cashier #:", "Total Sales Tax", "Subtotal")) and bool(
        re.search(r"^\s*(?:\d{6,14}|NOT ON FILE) x \d+\b", body, re.M)
    )
