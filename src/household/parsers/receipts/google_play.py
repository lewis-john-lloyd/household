"""Google Play receipts (service "google", the same as the Takeout purchase list): "Order number:",
"Order date:", the items each ending in its price (per month or year for a subscription), the tax
lines and "Total:". One order is one row at its total, keyed as the Takeout reader keys a transaction
("google|<order number>|item"), so a purchase in both is read once. An item reads as the Takeout
reader names it: the app first ("Claude by Anthropic: Claude Pro Subscription").
"""

import hashlib
import re

from ..google_takeout import _item as _app_item
from .common import forgiving, line_after, parse_day, to_cents, Unread

GOOGLE = "google"


GOOGLE_ITEM = re.compile(r"^(?P<name>.*\S)\s+\$(?P<price>[\d,]+\.\d\d)(?:/\w+)?$")


GOOGLE_TAX = re.compile(r"^(?:[A-Za-z ]*\s)?tax:\s*\$([\d,]+\.\d\d)$", re.I)


GOOGLE_TOTAL = re.compile(r"^Total:\s*\$([\d,]+\.\d\d)(?:/\w+)?$")


@forgiving(GOOGLE)
def google_play(message_id, headers, body):
    """One order_items row for one Google Play receipt, at what was charged, keyed as Takeout keys it."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    number = next(line for line in lines if line.startswith("Order number:")).split(":", 1)[1].strip()
    when = next((line for line in lines if line.startswith("Order date:")), "Order date:").split(":", 1)[1]
    day = parse_day(" ".join(when.split()[:3])) or headers.get("date", "")[:10]
    head = next((i for i, line in enumerate(lines) if line in ("Item Price", "Price")), None)
    total_at = next((i for i, line in enumerate(lines) if GOOGLE_TOTAL.match(line)), None)
    if head is None or total_at is None:
        raise Unread(f"{GOOGLE} {message_id}: no items or no Total")
    items, taxes, text = [], 0, ""
    for line in lines[head + 1 : total_at]:
        tax = GOOGLE_TAX.match(line)
        if tax:
            taxes += to_cents(tax.group(1))
            continue
        if line == "Auto-renewing subscription":
            continue
        priced = GOOGLE_ITEM.match(f"{text} {line}".strip())
        if priced:
            items.append((re.sub(r"\s*\(by [^()]*\)$", "", priced["name"]), to_cents(priced["price"])))
            text = ""
        else:
            text = f"{text} {line}".strip()  # a long name wraps onto the next line
    total = to_cents(GOOGLE_TOTAL.match(lines[total_at]).group(1))
    if not items or sum(c for _, c in items) + taxes != total:
        raise Unread(f"{GOOGLE} {message_id}: the items and tax don't add to the Total")
    if total <= 0:
        return []
    paid = line_after(lines, "Payment method:")
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


def _is_google_play(body):
    return all(s in body for s in ("Google Play", "Order number:", "Total:"))
