"""Store receipts from email, as the receipt exporter (exporter/Code.js) writes them into the inbox.

A file holds one or more messages, each starting with a line "===== RECEIPT <message id> =====",
then From, Subject and Date lines, a blank line and the body. Each receipt layout has a reader
(READERS, recognized by the body, not the sender, so no local store's name lives in this code; the
app stores, like Amazon and Google elsewhere, are named); a message in a layout with no reader yet is
counted and left out.

Each layout's reader is its own module in parsers/receipts (co_op, bookseller, apple, google_play, farm_store, outfitter, paypal, venmo, audible), with the helpers they share in receipts/common; this module splits a file into its messages, picks each one's reader and gathers what they read.

A receipt in one of these layouts that doesn't add up (a layout Apple or Google has since changed, say)
is left out with its id in the notes, never refusing the rest of the file.

A body still in HTML (a store that sends HTML in its plain-text part) is turned into text first,
the way the exporter does it.

The rows are order_items, one order per receipt, keyed by the message id, so a receipt exported
twice is read once. The household names each store's card lines with a parameter
(order_payee:<service>, see explain.order_services).
"""

import re

import pandas as pd

from ..explain import REWARD
from .amazon import COLUMNS
from .common import Result
from .receipts.apple import _is_apple_itunes, _is_apple_receipt, apple_itunes, apple_receipt
from .receipts.audible import _is_audible, audible
from .receipts.bookseller import _is_bookseller, _is_bookseller_online, bookseller, bookseller_online
from .receipts.co_op import _is_co_op, co_op
from .receipts.common import NAME, PAYPAL, Unpriced, Unread
from .receipts.farm_store import _is_farm_store, farm_store
from .receipts.google_play import _is_google_play, google_play
from .receipts.outfitter import _is_outfitter, outfitter
from .receipts.paypal import _is_paypal, paypal
from .receipts.venmo import _is_venmo, venmo


KIND = "email_receipts"


START = "===== RECEIPT "


MESSAGE = re.compile(r"^===== RECEIPT (\S+) =====$", re.M)


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
    rewards = df["item_id"].astype(str).str.endswith(REWARD)
    notes = [f"receipts: {df['order_id'].nunique()} read, {int((~rewards).sum())} items"]
    if rewards.any():
        notes.append(f"receipts: rewards paid {-df.loc[rewards, 'amount'].sum():,.2f} of what they bought")
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
