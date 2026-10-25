"""OFX and QFX bank and card downloads (Quicken / Money / Web Connect files).

Two dialects reach the inbox: OFX 1.x, which is SGML (a header of KEY:VALUE
lines, and leaf elements such as <TRNAMT>-12.50 with no closing tag), and OFX
2.x, which is XML. Aggregates (<STMTTRN> ... </STMTTRN>) are closed in both, so
one reader handles both: an element with text is a leaf, one without is an
aggregate that runs to its closing tag (or, never closed, an empty SGML leaf).

From one statement the file gives:
  transactions  each <STMTTRN>: posted date, signed amount (OFX's convention is
                the household's: money out is negative, on a card too), FITID,
                and payee (<NAME>, else <MEMO>)
  balances      <LEDGERBAL> with its as-of date (a card's balance owed is negative)
  account       the last four characters of <ACCTID>, which the inbox matches to
                the accounts table

A transaction is keyed on its FITID, the bank's own id, which stays the same
in every download that includes it, so overlapping downloads add nothing twice.
The inbox turns "fitid:<FITID>" into a txn_id once it knows the account.

Checks: the server status is 0, the currency is US dollars, every transaction
has a date, amount and FITID, no FITID repeats, and a DEBIT never adds money
nor a CREDIT take it away.
"""

import re

import pandas as pd

from .common import ParseError, Result

NAME = "ofx"
KIND = "bank_statement"
TAG = re.compile(r"<(/?)([A-Za-z0-9.]+)>([^<]*)")
STATEMENTS = ("STMTRS", "CCSTMTRS")
ACCOUNT = ("BANKACCTFROM", "CCACCTFROM")


def sniff(text):
    head = text[:4000]
    return ("<OFX>" in head or "OFXHEADER" in head) and any(f"<{s}>" in text for s in STATEMENTS)


def read(text):
    """The OFX body as nested dicts: {tag: [child, ...]} for aggregates, text for leaves."""
    body_text = text[text.find("<OFX>"):] if "<OFX>" in text else text
    closed = {m.group(1).upper() for m in re.finditer(r"</([A-Za-z0-9.]+)>", body_text)}
    root = {}
    stack = [("", root)]
    for m in TAG.finditer(body_text):
        closing, tag, body = m.group(1), m.group(2).upper(), m.group(3).strip()
        if closing:
            names = [name for name, _ in stack]
            if tag in names:  # closes an aggregate (a leaf's closing tag in XML is ignored)
                while stack[-1][0] != tag:
                    stack.pop()
                stack.pop()
            continue
        if body:
            stack[-1][1].setdefault(tag, body)
        elif tag in closed:
            node = {}
            stack[-1][1].setdefault(tag, []).append(node)
            stack.append((tag, node))
    return root


def find(node, tag):
    """Every aggregate named tag anywhere under node, in document order."""
    out = []
    for key, value in node.items():
        if isinstance(value, list):
            for child in value:
                if key == tag:
                    out.append(child)
                out.extend(find(child, tag))
    return out


def first(node, *path):
    for tag in path[:-1]:
        children = node.get(tag) if isinstance(node, dict) else None
        if not children:
            return ""
        node = children[0]
    value = node.get(path[-1], "") if isinstance(node, dict) else ""
    return value if isinstance(value, str) else ""


def day(value):
    digits = re.match(r"\d{8}", value or "")
    if not digits:
        raise ParseError(f"{NAME}: bad date {value!r}")
    d = digits.group()
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def parse(text):
    root = read(text)
    for status in find(root, "SONRS") + find(root, "STMTTRNRS") + find(root, "CCSTMTTRNRS"):
        code = first(status, "STATUS", "CODE")
        if code and code != "0":
            raise ParseError(f"{NAME}: server status {code}")
    statements = [s for tag in STATEMENTS for s in find(root, tag)]
    if len(statements) != 1:
        raise ParseError(f"{NAME}: expected one account statement, found {len(statements)}")
    st = statements[0]
    currency = first(st, "CURDEF") or "USD"
    if currency != "USD":
        raise ParseError(f"{NAME}: currency {currency}")
    account = next((first(st, a, "ACCTID") for a in ACCOUNT if st.get(a)), "")
    if not account:
        raise ParseError(f"{NAME}: no account id")
    last4 = re.sub(r"\W", "", account)[-4:]

    rows = []
    for t in find(st, "STMTTRN"):
        fitid, posted, amount = first(t, "FITID"), first(t, "DTPOSTED"), first(t, "TRNAMT")
        if not (fitid and posted and amount):
            raise ParseError(f"{NAME}: transaction without date, amount or FITID ({fitid or posted})")
        value = round(float(amount), 2)
        kind = first(t, "TRNTYPE").upper()
        if (kind == "DEBIT" and value > 0) or (kind == "CREDIT" and value < 0):
            raise ParseError(f"{NAME}: {kind} of {value:+.2f} (FITID {fitid})")
        name, memo = first(t, "NAME"), first(t, "MEMO")
        note = []
        if name and memo and memo != name:
            note.append(memo)
        card = first(t, "CCACCTTO", "ACCTID")
        if card:
            note.append(f"card {card[-4:]}")
        rows.append({"txn_id": f"fitid:{fitid}", "date": day(posted), "account_id": "",
                     "amount": value, "payee_raw": " ".join((name or memo).split()),
                     "category": "", "one_off": "FALSE", "note": "; ".join(note), "source": "ofx",
                     "era": "ledger", "recorded_at": ""})
    txns = pd.DataFrame(rows, columns=["txn_id", "date", "account_id", "amount", "payee_raw",
                                       "category", "one_off", "note", "source", "era", "recorded_at"])
    if txns["txn_id"].duplicated().any():
        raise ParseError(f"{NAME}: repeated FITID {txns.loc[txns['txn_id'].duplicated(), 'txn_id'].iloc[0]}")

    tables, keys = {"transactions": txns}, {"transactions": ["txn_id"]}
    balance, as_of = first(st, "LEDGERBAL", "BALAMT"), first(st, "LEDGERBAL", "DTASOF")
    if balance and as_of:
        tables["balances"] = pd.DataFrame([{"as_of": day(as_of), "account_id": "",
                                            "balance": round(float(balance), 2)}])
        keys["balances"] = ["as_of", "account_id"]
    start, end = first(st, "BANKTRANLIST", "DTSTART"), first(st, "BANKTRANLIST", "DTEND")
    dates = sorted(txns["date"])
    covers_from = day(start) if start else (dates[0] if dates else "")
    covers_to = day(end) if end else (dates[-1] if dates else "")
    org = next((first(fi, "ORG") for fi in find(root, "FI")), "")
    return Result(kind=KIND, parser=NAME, label=f"statement_{last4}_{covers_to}",
                  covers_from=covers_from, covers_to=covers_to, account_hint=last4,
                  tables=tables, keys=keys,
                  notes=[f"{org or 'bank'} ..{last4}: {len(txns)} transactions"
                         + (f", balance {float(balance):.2f} on {day(as_of)}" if balance and as_of else "")])
