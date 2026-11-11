"""Outfitter receipts (service "outfitter"), in two layouts: the store eReceipt ("Items purchased",
"Transaction type: Sale") and the online order confirmation ("Order number:", "Order date:"). Each
item is its name (and for an online order its color or size, in brackets), "Item #", "Qty:", the
price each and what it came to. Payment details list Subtotal, shipping, tax and the total (label then
value, or all the labels then all the values), then the member rewards used and what each card paid
("(ending in ...)"). Each item reads at what it cost, its share of the tax with it; what rewards paid
of it is a row of its own (department "rewards", item id with REWARD after it), so the two add to what
the card paid for that item and the card line splits into the item and the rewards earned. A store
sale is one charge, so the rewards are shared by amount; an online order is charged as its parts ship,
and the rewards come off the first items listed, as charged, so matching can take the parts one by one.
A store return, or an order the rewards paid in full, is left out: no card line paid for it.
"""

import re

from ...explain import REWARD
from .common import forgiving, item_row, scale_to, to_cents, Unread


def _reward(item, cents):
    """The rewards row for an item rewards paid for (cents, negative): the item's own row reads at what it
    cost, and this one, under the item's id with REWARD after it, takes off what the rewards paid, so the
    two add to what the card was charged and the line splits into the item and the rewards earned."""
    return {
        **item,
        "item_id": item["item_id"] + REWARD,
        "amount": cents / 100,
        "department": "rewards",
        "item": "rewards used",
    }


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
        0 if line == "FREE" else (-1 if DOLLARS.match(line)[1] else 1) * to_cents(DOLLARS.match(line)[2])
        for line in section
        if DOLLARS.match(line) or line == "FREE"
    ]
    if len(labels) != len(values):
        raise ValueError("the totals' labels and values don't pair up")
    return dict(zip(labels, values))


@forgiving(OUTFITTER)
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
                (name[0] + (f" ({', '.join(name[1:])})" if len(name) > 1 else ""), (-1 if m[1] else 1) * to_cents(m[2]))
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
            charged += to_cents(DOLLARS.match(totals[i + 1])[2])
    if charged <= 0:
        return []
    full = scale_to([c for _, c in items], total)  # each item at what it cost, its share of the tax with it
    if online:  # shipped in parts, charged as each part ships: rewards come off the first items, as charged
        amounts, left = [], total - charged
        for amount in full:
            off = min(amount, left)
            amounts.append(amount - off)
            left -= off
    else:  # one charge: tax and any rewards used shared by amount
        amounts = scale_to([c for _, c in items], charged)
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
    rows = []
    for n, ((name, _), cost, amount) in enumerate(zip(items, full, amounts)):
        rows.append(item_row(OUTFITTER, order, n, day, cost, "", name, website))
        if cost != amount:  # rewards paid part or all of it: their own row, so the item reads at what it cost
            rows.append(_reward(rows[-1], amount - cost))
    return rows


def _is_outfitter(body):
    return (
        "Payment details" in body
        and "Item #" in body
        and "Qty:" in body
        and ("Items purchased" in body or "Order number:" in body)
    )
