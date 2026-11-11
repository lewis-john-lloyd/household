"""PayPal emails (service "paypal", rows for payment_details, as the QIF download's are): a merchant's
receipt ("You paid" or "You authorized ... to", with the merchant's name and the transaction date),
PayPal's own receipt ("Payment to", the items in a table, Total), a refund ("Your Refund of ...
from") and money a person sent ("... sent you", with their note). Money out is negative. A payment is
keyed by PayPal's transaction id, so its receipt and its authorization are read once; a refund, which
prints none, by the message id.
"""

import hashlib
import re

from .common import forgiving, line_after, parse_day, PAYPAL, to_cents, Unread

PAYPAL_ID = re.compile(r"Transaction ID:?\s*\n?\s*([0-9A-Z]{17})\b")


PAYPAL_PAID = re.compile(r"^You (paid|authorized) \$([\d,]+\.\d\d) USD (?:payment )?to (.+?)\.?$", re.M)


PAYPAL_SENT = re.compile(r"^(.+?) sent you \$([\d,]+\.\d\d) USD$", re.M)


PAYPAL_REFUND = re.compile(r"^Your Refund of \$([\d,]+\.\d\d) USD from (.+?)\.?$", re.M)


PAYPAL_TOTAL = re.compile(r"^\$([\d,]+\.\d\d) USD$")


@forgiving(PAYPAL)
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
        kind, cents, who = "Payment Refund", to_cents(refund[1]), line_after(lines, "Refund from") or refund[2]
    elif paid:  # the merchant's name in full where it is printed (the sentence drops a last ".")
        kind, cents, who = "Payment", -to_cents(paid[2]), line_after(lines, "Merchant") or paid[3]
    elif sent:
        kind, cents, who = "Payment Received", to_cents(sent[2]), sent[1]
        note = line_after(lines, f"Note from {who}")
        memo = "" if note in ("Transaction date", "Amount") else note
    elif "Payment to" in lines:  # the receipt layout: the items in a table, then Total
        kind, who = "Payment", line_after(lines, "Payment to")
        at = lines.index("Total", lines.index("Payment to"))
        cents = -to_cents(PAYPAL_TOTAL.match(lines[at + 1])[1])
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
    when = line_after(lines, "Transaction date") or line_after(lines, "Date")
    day = parse_day(when) or headers.get("date", "")[:10]
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
