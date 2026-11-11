"""Audible order emails (service "audible", "Thanks, your order is complete" and "Your order is confirmed"),
in two layouts that read alike once a table is reduced to lines: "Order number:", then each title with what
it cost (a price, or "1 credit"), on its own line or the line below the title, its author and narrator,
and "Summary" with the Subtotal, Tax and "Total paid". A title paid with a credit cost nothing on the card,
so only priced titles are rows; the tax goes to them by amount. An order paid wholly in credits charged
nothing and is left out. The order is keyed by its order number, since Audible may send the same order
twice. A membership's monthly renewal sends no email, so its card lines are left to the payee's answer.
"""

import re

from .common import to_cents, money_in, item_row, scale_to, Unread

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
                items.append((" ".join(name.split()), to_cents(priced["price"]) if priced["price"] else 0))
            title = ""
        elif line and not line.startswith(AUDIBLE_ABOUT) and line != "Title Amount":
            title = line
    summary = "\n".join(lines[end:])
    subtotal = money_in(summary, r"^Subtotal:\s*\$([\d,]+\.\d\d)", "Subtotal")
    tax = money_in(summary, r"^Tax:\s*\$([\d,]+\.\d\d)", "Tax")
    total = money_in(summary, r"^Total paid:\s*\$([\d,]+\.\d\d)", "Total paid")
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
    amounts = scale_to([c for _, c in paid], total)
    day = headers.get("date", "")[:10]
    return [
        item_row(AUDIBLE, order[1], n, day, amount, "audiobooks", name, "audible.com")
        for n, ((name, _), amount) in enumerate(zip(paid, amounts))
    ]


def _is_audible(body):
    return "Audible, Inc." in body and "Order number:" in body and "Total paid:" in body
