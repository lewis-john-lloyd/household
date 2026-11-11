"""Venmo emails (service "venmo", rows for payment_details): a payment sent ("You paid ..."), one received
("... paid you"), each with its note and transaction id, and a transfer to the bank ("Transfer Amount"), which
explain.match_venmo names after the payments received before it.
"""

import hashlib
import re

from .common import line_after, to_cents, parse_day, Unread

VENMO = "venmo"


VENMO_PAID = re.compile(r"^You paid ([^$]+)$")


VENMO_RECEIVED = re.compile(r"^([^$]+) paid you$")


VENMO_AMOUNT = re.compile(r"\$([\d,]+\.\d\d)")


def venmo(message_id, headers, body):
    """One payment_details row for one Venmo email: a payment sent ("You paid ..."), one received ("... paid
    you"), each with its note, or a transfer to the bank ("Transfer Amount"). Money out is negative; a transfer
    is positive, as it lands in the bank. Keyed by Venmo's transaction id."""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if "Transfer Amount" in lines:
        amount = VENMO_AMOUNT.search(line_after(lines, "Transfer Amount"))
        txn = line_after(lines, "Transfer transaction ID")
        if not amount or not txn:
            raise Unread(f"{VENMO} {message_id}: a transfer with no amount or id")
        day = parse_day(line_after(lines, "Initiated on"), ("%A, %B %d, %Y",)) or headers.get("date", "")[:10]
        return [
            {
                "detail_id": hashlib.sha256(f"{VENMO}|transfer|{txn}".encode()).hexdigest()[:16],
                "date": day,
                "amount": to_cents(amount[1]) / 100,
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
    txn = line_after(lines, "Transaction ID")
    if not amount or not txn:
        raise Unread(f"{VENMO} {message_id}: no amount or transaction id")
    # the amount is printed as "$", "12", ". 50" after the sentence; the note follows it, up to "See transaction"
    end = next((i for i in range(at, len(lines)) if lines[i].startswith("See transaction")), at)
    note = [line for line in lines[at + 1 : end] if not re.fullmatch(r"\$|[\d,]+|\.\s?\d\d", line)]
    cents = to_cents(amount[1])
    return [
        {
            "detail_id": hashlib.sha256(f"{VENMO}|{txn}".encode()).hexdigest()[:16],
            "date": parse_day(line_after(lines, "Date")) or headers.get("date", "")[:10],
            "amount": (-cents if paid else cents) / 100,
            "service": VENMO,
            "kind": "Payment" if paid else "Payment Received",
            "counterparty": " ".join(who.split()),
            "memo": " ".join(" ".join(note).split()),
        }
    ]


def _is_venmo(body):
    return "Venmo is a service of PayPal" in body and ("Transaction ID" in body or "Transfer Amount" in body)
