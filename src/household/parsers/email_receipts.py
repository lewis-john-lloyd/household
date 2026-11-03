"""Store receipts from email, as the receipt exporter (exporter/Code.js) writes them into the inbox.

A file holds one or more messages, each starting with a line "===== RECEIPT <message id> =====",
then From, Subject and Date lines, a blank line and the body. Each receipt layout has a reader
(READERS, recognized by the body, not the sender, so no store's name lives in this code); a
message in a layout with no reader yet is counted and left out.

Co-op point-of-sale receipts (service "co_op"): the receipt lists each item with its price and its
tax flags (F: food, W: weighed, T1: taxed). The items add to Net Sales, the taxed ones to the base printed beside the
tax, and the card was charged SUB TOTAL (after any vendor coupon). Each item's amount is its
price plus its share of the tax (taxed items only), scaled so the items add to what was charged.
Food (an F flag) is grocery; a taxed item that is not food is alcohol when its name says so
(ALCOHOL), toys and games when its name says so (TOYS), and otherwise left to the default
(candles, medicine, soap). Weight and quantity lines, coupon lines and the store's own
contributions (not charged) are skipped. Checks: the items add to Net Sales and the taxed ones to
the tax base.

The rows are order_items, one order per receipt, keyed by the message id, so a receipt exported
twice is read once. The household names the store's card lines with a parameter
(order_payee:co_op, see explain.order_services).
"""

import hashlib
import re

import pandas as pd

from .amazon import COLUMNS
from .common import ParseError, Result

NAME = "email_receipts"
KIND = "email_receipts"
START = "===== RECEIPT "
MESSAGE = re.compile(r"^===== RECEIPT (\S+) =====$", re.M)
ITEM = re.compile(r"^(?P<name>.*?)\s*(?P<minus>-?)\$(?P<price>\d+\.\d\d)\s*(?P<flags>[A-Z0-9]*)\s*$")
SKIP = re.compile(r"^(\d+(\.\d+)? lb @|\d+ @ \$|\s*Ref:|CPN:|-Vendor coupon|[^$]*contribution: \$)")
ALCOHOL = re.compile(r"\b(beers?|ale|ipa|lager|stout|porter|pils(ner)?|quadrupel|tripel|dubbel|saison|kolsch|"
                     r"wine|sauvignon|cabernet|merlot|pinot|chardonnay|riesling|ros[eé]|prosecco|champagne|cava|"
                     r"malbec|zinfandel|syrah|shiraz|grenache|tempranillo|sangiovese|chianti|rioja|moscato|"
                     r"vermouth|cider|mead|sake|vodka|gin|whiske?y|bourbon|rum|tequila|mezcal|brandy|liqueur|"
                     r"hard seltzer|red blend|white blend)\b", re.I)
TOYS = re.compile(r"\b(puzzles?|stickers?|games?|toys?|lego|crayons?|coloring|playing cards|cards?|kite|"
                  r"books?|activity)\b", re.I)


def sniff(text):
    return text.lstrip("﻿").startswith(START)


def _messages(text):
    """[(message id, {header: value}, body)] in the file."""
    out = []
    marks = list(MESSAGE.finditer(text))
    for n, m in enumerate(marks):
        chunk = text[m.end():marks[n + 1].start() if n + 1 < len(marks) else len(text)].lstrip("\n")
        head, _, body = chunk.partition("\n\n")
        headers = {}
        for line in head.splitlines():
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        out.append((m.group(1), headers, body))
    return out


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
    items = []
    for line in sections[1].splitlines():
        if not line.strip() or SKIP.match(line):
            continue
        m = ITEM.match(line)
        if not m:
            continue  # a line with no price (a free item)
        cents = round(float(m["price"]) * 100) * (-1 if m["minus"] else 1)
        items.append((" ".join(m["name"].split()), cents, m["flags"]))
    totals = "\n".join(sections[2:])
    net = _money(totals, r"Net Sales \$(-?[\d,]+\.\d\d)", "Net Sales")
    charged = _money(totals, r"SUB TOTAL \$(-?[\d,]+\.\d\d)", "SUB TOTAL")
    taxes = [(round(float(b) * 100), round(float(t) * 100))
             for b, t in re.findall(r"Tax \d+ \[\$(-?[\d.]+)\] \$(-?[\d.]+)", totals)]
    if not items or charged <= 0:
        return []
    if sum(c for _, c, _ in items) != net:
        raise ParseError(f"{NAME}: co-op receipt {message_id}: items add to {sum(c for _, c, _ in items) / 100:.2f}, "
                         f"Net Sales is {net / 100:.2f}")
    base = sum(c for _, c, f in items if "T" in f)
    if taxes and sum(b for b, _ in taxes) != base:
        raise ParseError(f"{NAME}: co-op receipt {message_id}: taxed items add to {base / 100:.2f}, "
                         f"the tax base is {sum(b for b, _ in taxes) / 100:.2f}")
    tax = sum(t for _, t in taxes)
    with_tax = [c + (round(tax * c / base) if base and "T" in f else 0) for _, c, f in items]
    amounts = _scale(with_tax, charged)
    day = re.search(r"^#\S+ (\d{4}-\d{2}-\d{2}) ", body, re.M)
    day = day.group(1) if day else headers.get("date", "")[:10]
    store = re.search(r"^\s*\*([^*\n]+)\*\s*$", body, re.M)
    rows = []
    for n, ((name, _, flags), amount) in enumerate(zip(items, amounts)):
        rows.append({"item_id": hashlib.sha256(f"{CO_OP}|{message_id}|{n}".encode()).hexdigest()[:16],
                     "service": CO_OP, "order_id": message_id, "kind": "item", "order_date": day,
                     "date": day, "amount": amount / 100, "card": "", "department": _department(name, flags),
                     "item": name, "website": store.group(1).strip() if store else CO_OP})
    return rows


CO_OP = "co_op"


def _is_co_op(body):
    return all(s in body for s in ("EMAIL RECEIPT", "Net Sales", "SUB TOTAL"))


READERS = ((_is_co_op, co_op),)


def parse(text):
    rows, skipped = [], {}
    for message_id, headers, body in _messages(text):
        reader = next((r for recognizes, r in READERS if recognizes(body)), None)
        if reader is None:
            key = re.sub(r".*@", "", headers.get("from", "").lower()).strip("> ") or "unknown"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        rows += reader(message_id, headers, body)
    df = pd.DataFrame(rows, columns=COLUMNS)
    dates = sorted(d for d in df["date"] if d)
    notes = [f"receipts: {df['order_id'].nunique()} read, {len(df)} items"]
    notes += [f"receipts: {n} from {k} left for a reader not written yet" for k, n in sorted(skipped.items())]
    return Result(kind=KIND, parser=NAME, label=f"receipts_{dates[-1] if dates else 'none'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"order_items": df} if len(df) else {}, keys={"order_items": ["item_id"]}, notes=notes)
