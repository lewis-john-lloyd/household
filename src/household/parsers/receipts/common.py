"""Helpers the receipt readers share: amounts and dates in a receipt's text, one order_items row, and the
exceptions a reader raises for a receipt it can't price or that doesn't add up.
"""

import hashlib
import re
from datetime import datetime

from ..common import ParseError

NAME = "email_receipts"  # the parser's name, in the errors its readers raise


class Unpriced(Exception):
    """A receipt that lists several items without their prices: it stays one charge."""


class Unread(Exception):
    """A receipt in a known layout that doesn't add up: left out, with why, in the notes."""


def forgiving(service):
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


def to_cents(text):
    return round(float(text.replace(",", "")) * 100)


def money_in(text, pattern, what, required=True):
    found = re.search(pattern, text, re.M)
    if not found:
        if required:
            raise ParseError(f"{NAME}: no {what}")
        return None
    return round(float(found.group(1).replace(",", "")) * 100)


def scale_to(cents, total):
    """These amounts scaled so they add to total exactly, the cents left over on the biggest."""
    whole = sum(cents)
    if whole <= 0:
        raise ParseError(f"{NAME}: nothing priced")
    out = [round(c * total / whole) for c in cents]
    out[max(range(len(out)), key=lambda i: abs(out[i]))] += total - sum(out)
    return out


def item_row(service, message_id, n, day, amount, department, name, website):
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


def parse_day(text, formats=("%B %d, %Y", "%b %d, %Y")):
    for f in formats:
        try:
            return datetime.strptime(text.strip(), f).date().isoformat()
        except ValueError:
            pass
    return ""


def line_after(lines, label, start=0):
    """The line after the first line equal to label, from start; "" when there is none."""
    i = next((i for i in range(start, len(lines)) if lines[i] == label), None)
    return lines[i + 1] if i is not None and i + 1 < len(lines) else ""


PAYPAL = "paypal"
