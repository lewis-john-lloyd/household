"""Investment accounts in OFX or QFX (Quicken's Web Connect): a brokerage, an IRA or a workplace plan,
one or several accounts to a file (Vanguard puts every account in one download).

Each account's statement gives three things:

  investment_activity  each transaction in the one vocabulary of parsers/fidelity.py (kind, shares and their
                       value, and the money crossing the account's edge), with a plan's money source
                       (INV401KSOURCE: pretax, match, aftertax, rollover...) in `source`
  holdings             the positions on the statement's date: shares and market value per fund (and source,
                       when the plan gives one)
  prices               the price of each fund on each date the file states one (positions, the security
                       list, and every trade), source "ofx"; a plan-only fund has no other price

How each transaction reads:

  BUYMF, BUYSTOCK      a buy, paid from the account's cash (amount 0); in a plan, money from payroll, so a
                       contribution (amount in)
  SELLMF, SELLSTOCK    a sale into the account's cash (amount 0)
  REINVEST             a dividend that buys shares: income in, shares bought
  INCOME               a dividend (DIV, CGLONG, CGSHORT) or interest (INTEREST), in cash
  TRANSFER             in a plan, shares moved between funds or sources (an exchange, amount 0); elsewhere
                       shares in or out of the account
  INVBANKTRAN          money in or out of the account (a transfer; ingest names a transfer from a pre-tax
                       account to a Roth one in the same file a conversion)
  INVEXPENSE           a fee
  SPLIT                shares added (an adjustment)

A sweep into or out of the settlement fund (memo "Settlement Fund") is the account's cash moving into or out
of its money market: a buy or sale of that fund. Money deposited by check or from a bank often appears only
as such a sweep, so the reader keeps a running count of the account's uninvested cash (dividends, sales and
transfers add to it; buys, fees and sweeps in take from it) and a sweep in that the cash on hand can't
cover is a deposit: a contribution row for the difference, marked "deposit (inferred)".

The account is named by its number only (<ACCTID>); each statement's rows carry "acct:<last four>" until
ingest matches it to the accounts table's last4 (ingest.finish_investment_rows), then hashes each row's
key with the account. A transaction is keyed on its FITID and what it moved, with a count for identical rows.
A transaction listed twice, identical to its FITID (Vanguard repeats every transaction in its trust funds),
is read once.
"""

import re

import pandas as pd

from . import ofx
from .common import ParseError, Result
from .fidelity import COLUMNS

NAME = "ofx_investments"
KIND = "investment_activity"
HOLDINGS = ["as_of", "account_id", "symbol", "source", "shares", "value"]
PRICES = ["symbol", "date", "priced_date", "close", "source"]
BUYS, SELLS = ("BUYMF", "BUYSTOCK", "BUYOTHER", "BUYDEBT"), ("SELLMF", "SELLSTOCK", "SELLOTHER", "SELLDEBT")
POSITIONS = ("POSMF", "POSSTOCK", "POSOTHER", "POSDEBT")
INFOS = ("MFINFO", "STOCKINFO", "OTHERINFO", "DEBTINFO")
INCOME_KIND = {"DIV": "dividend", "CGLONG": "dividend", "CGSHORT": "dividend", "INTEREST": "interest", "MISC": "other"}


def sniff(text):
    head = text[:4000]
    return ("<OFX>" in head or "OFXHEADER" in head) and "<INVSTMTRS>" in text


def _num(text, what):
    try:
        return float(str(text if text is not None else "").replace(",", "").strip() or 0)
    except ValueError as e:
        raise ParseError(f"{NAME}: {what} is not a number: {text!r}") from e


def _memo(text):
    return " ".join(re.sub(r"[^\x20-\x7e]", " ", text or "").split())


def securities(root):
    """{CUSIP or other id: (symbol, name)} from the file's security list; the id is the symbol with no ticker."""
    out = {}
    for tag in INFOS:
        for info in ofx.find(root, tag):
            sec = (info.get("SECINFO") or [{}])[0]
            uid = ofx.first(sec, "SECID", "UNIQUEID")
            if uid:
                out[uid] = (ofx.first(sec, "TICKER") or uid, _memo(ofx.first(sec, "SECNAME")))
    return out


def _rows(st, secs):
    """One statement's transactions as (sort key, row) pairs, before deposits are inferred."""
    # a workplace plan: its 401(k) details, its balance by source, or a source on its transactions (Empower's
    # download has only the balance)
    plan = bool(st.get("INV401K") or st.get("INV401KBAL")) or "INV401KSOURCE" in str(st.get("INVTRANLIST", ""))
    out, seen, repeats = [], set(), 0
    for tranlist in st.get("INVTRANLIST", []):
        for tag, items in tranlist.items():
            if not isinstance(items, list):
                continue
            for item in items:
                whole = (tag, repr(item))
                if whole in seen:  # the same transaction, FITID and all, listed again (Vanguard's trust funds)
                    repeats += 1
                    continue
                seen.add(whole)
                out.append(_row(tag, item, secs, plan))
    return [r for r in out if r is not None], plan, repeats


def _row(tag, item, secs, plan):
    if tag == "INVBANKTRAN":
        st = (item.get("STMTTRN") or [{}])[0]
        amount = round(_num(st.get("TRNAMT"), "TRNAMT"), 2)
        return {"fitid": st.get("FITID", ""), "date": ofx.day(st.get("DTPOSTED")), "kind": "transfer_in" if amount > 0 else "transfer_out",
                "action": "INVBANKTRAN " + _memo(st.get("NAME") or st.get("MEMO") or st.get("TRNTYPE", "")), "symbol": "",
                "description": "", "shares": 0.0, "price": 0.0, "value": 0.0, "amount": amount, "source": "", "cash": amount,
                "sweep": ""}
    body = item
    for inner in ("INVBUY", "INVSELL"):
        if item.get(inner):
            body = item[inner][0]
    tran = (body.get("INVTRAN") or [{}])[0]
    if not tran.get("DTTRADE"):
        raise ParseError(f"{NAME}: a {tag} with no trade date")
    uid = ofx.first(body, "SECID", "UNIQUEID")
    symbol, name = secs.get(uid, (uid, ""))
    memo = _memo(tran.get("MEMO"))
    units = _num(body.get("UNITS"), "UNITS")
    price = _num(body.get("UNITPRICE"), "UNITPRICE")
    total = abs(_num(body.get("TOTAL"), "TOTAL"))
    source = (body.get("INV401KSOURCE") or "").lower()
    row = {"fitid": tran.get("FITID", ""), "date": ofx.day(tran["DTTRADE"]), "action": f"{tag} {memo}".strip(), "symbol": symbol,
           "description": name, "price": round(price, 6), "source": source, "sweep": ""}
    value = round(total or abs(units * price), 2)
    if tag in BUYS:
        sweep = "Settlement Fund" in memo
        kind = "contribution" if plan else "buy"
        row.update(kind=kind, shares=round(abs(units), 6), value=value, amount=value if plan else 0.0,
                   cash=0.0 if plan or sweep else -value, sweep="in" if sweep and not plan else "")
    elif tag in SELLS:
        sweep = "Settlement Fund" in memo
        row.update(kind="withdrawal" if plan else "sell", shares=-round(abs(units), 6), value=-value,
                   amount=-value if plan else 0.0, cash=0.0 if plan or sweep else value, sweep="out" if sweep and not plan else "")
    elif tag == "REINVEST":
        row.update(kind="reinvest", shares=round(abs(units), 6), value=value, amount=value, cash=0.0)
    elif tag == "INCOME":
        kind = INCOME_KIND.get((body.get("INCOMETYPE") or "").upper(), "other")
        row.update(kind=kind, shares=0.0, value=0.0, amount=round(total, 2), cash=round(total, 2))
    elif tag == "TRANSFER":
        into = (body.get("TFERACTION") or "").upper() == "IN"
        sign = 1 if into else -1
        moved = round(abs(units * price), 2)
        if plan:
            row.update(kind="exchange", shares=sign * round(abs(units), 6), value=sign * moved, amount=0.0, cash=0.0)
        else:
            row.update(kind="transfer_in" if into else "transfer_out", shares=sign * round(abs(units), 6),
                       value=sign * moved, amount=sign * moved, cash=0.0)
    elif tag in ("INVEXPENSE", "MARGININTEREST"):
        row.update(kind="fee", shares=0.0, value=0.0, amount=-round(total, 2), cash=0.0 if plan else -round(total, 2))
    elif tag == "SPLIT":
        added = _num(body.get("NEWUNITS"), "NEWUNITS") - _num(body.get("OLDUNITS"), "OLDUNITS")
        row.update(kind="adjustment", shares=round(added, 6), value=0.0, amount=0.0, cash=0.0)
    else:
        row.update(kind="other", shares=round(units, 6), value=0.0, amount=0.0, cash=0.0)
    return row


ORDER = {"cash_in": 0, "sweep_out": 1, "cash_out": 2, "sweep_in": 3}


def infer_deposits(rows):
    """Contribution rows for the deposits seen only as a sweep into the settlement fund (see the module doc)."""
    def phase(r):
        if r["sweep"] == "in":
            return ORDER["sweep_in"]
        if r["sweep"] == "out":
            return ORDER["sweep_out"]
        return ORDER["cash_in"] if r["cash"] >= 0 else ORDER["cash_out"]
    pool, out = 0.0, []
    for r in sorted(rows, key=lambda r: (r["date"], phase(r))):
        if r["sweep"] == "in":
            short = round(r["value"] - max(pool, 0.0), 2)
            if short > 0.005:
                out.append({**r, "fitid": f"deposit:{r['fitid']}", "kind": "contribution", "action": "deposit (inferred)",
                            "symbol": "", "description": "", "shares": 0.0, "price": 0.0, "value": 0.0, "amount": short,
                            "sweep": ""})
                pool += short
            pool -= r["value"]
        elif r["sweep"] == "out":
            pool += -r["value"]  # a sale of the settlement fund: its value is negative
        else:
            pool += r["cash"]
    return out


def parse(text):
    root = ofx.read(text)
    for status in ofx.find(root, "SONRS") + ofx.find(root, "INVSTMTTRNRS"):
        code = ofx.first(status, "STATUS", "CODE")
        if code and code != "0":
            raise ParseError(f"{NAME}: server status {code}")
    statements = ofx.find(root, "INVSTMTRS")
    if not statements:
        raise ParseError(f"{NAME}: no investment statement")
    secs = securities(root)
    acts, held, prices, notes, spans = [], [], [], [], []
    for st in statements:
        if (ofx.first(st, "CURDEF") or "USD") != "USD":
            raise ParseError(f"{NAME}: currency {ofx.first(st, 'CURDEF')}")
        number = ofx.first(st, "INVACCTFROM", "ACCTID")
        if len(re.sub(r"\D", "", number)) < 4:
            raise ParseError(f"{NAME}: a statement with no account number")
        account = f"acct:{re.sub(r'[^0-9A-Za-z]', '', number)[-4:]}"
        as_of = ofx.day(ofx.first(st, "DTASOF"))
        rows, plan, repeats = _rows(st, secs)
        rows += infer_deposits(rows)
        tl = (st.get("INVTRANLIST") or [{}])[0]
        if tl.get("DTSTART"):
            spans += [ofx.day(tl["DTSTART"]), ofx.day(tl.get("DTEND") or tl["DTSTART"])]
        for r in rows:
            r["account_id"] = account
            if r["price"] and r["symbol"]:
                prices.append({"symbol": r["symbol"], "date": r["date"], "priced_date": r["date"], "close": r["price"],
                               "source": "ofx"})
        acts += rows
        for tag in POSITIONS:
            for pos in ofx.find(st, tag):
                p = (pos.get("INVPOS") or [{}])[0]
                uid = ofx.first(p, "SECID", "UNIQUEID")
                symbol = secs.get(uid, (uid, ""))[0]
                units = _num(p.get("UNITS"), "UNITS")
                price, when = _num(p.get("UNITPRICE"), "UNITPRICE"), ofx.day(p.get("DTPRICEASOF") or ofx.first(st, "DTASOF"))
                if price:
                    prices.append({"symbol": symbol, "date": when, "priced_date": when, "close": round(price, 6), "source": "ofx"})
                if units:
                    held.append({"as_of": as_of, "account_id": account, "source": (p.get("INV401KSOURCE") or "").lower(),
                                 "symbol": symbol, "shares": round(units, 6), "value": round(_num(p.get("MKTVAL"), "MKTVAL"), 2)})
        counts = pd.Series([r["kind"] for r in rows]).value_counts() if rows else pd.Series(dtype=int)
        what = ", ".join(f"{n} {k}" for k, n in counts.items())
        inferred = sum(1 for r in rows if r["action"] == "deposit (inferred)")
        notes.append(f"account ..{account[5:]}{' (plan)' if plan else ''}: {what or 'no activity'}; "
                     f"{sum(1 for h in held if h['account_id'] == account)} positions on {as_of}"
                     + (f"; {inferred} deposit(s) inferred from sweeps" if inferred else "")
                     + (f"; {repeats} transaction(s) listed twice, read once" if repeats else ""))
    for info_tag in INFOS:
        for info in ofx.find(root, info_tag):
            sec = (info.get("SECINFO") or [{}])[0]
            uid, price = ofx.first(sec, "SECID", "UNIQUEID"), ofx.first(sec, "UNITPRICE")
            if uid and price and ofx.first(sec, "DTASOF"):
                when = ofx.day(ofx.first(sec, "DTASOF"))
                prices.append({"symbol": secs[uid][0], "date": when, "priced_date": when, "close": round(_num(price, "UNITPRICE"), 6),
                               "source": "ofx"})
    if not acts and not held:
        raise ParseError(f"{NAME}: no activity or positions in the file")
    df = pd.DataFrame(acts)
    if len(df):
        key = df[["account_id", "fitid", "action", "source", "symbol", "shares", "amount"]].astype(str).agg("|".join, axis=1)
        n = key.groupby(key).cumcount()
        df["activity_id"] = [f"key:{k}|{i}" for k, i in zip(key, n)]
        df["cash_balance"] = ""
    df = df.reindex(columns=COLUMNS)
    dates = sorted(set(spans) | set(df["date"].dropna()) | {h["as_of"] for h in held})
    price_df = pd.DataFrame(prices, columns=PRICES).drop_duplicates(["symbol", "date"], keep="last")
    return Result(kind=KIND, parser=NAME, label=f"ofx_investments_{dates[-1]}", covers_from=dates[0], covers_to=dates[-1],
                  tables={KIND: df, "holdings": pd.DataFrame(held, columns=HOLDINGS), "prices": price_df},
                  keys={KIND: ["activity_id"], "holdings": ["as_of", "account_id", "source", "symbol"],
                        "prices": ["symbol", "date"]},
                  notes=notes)
