"""Closing prices from GOOGLEFINANCE, asked for through the ledger's price_requests tab.

The engine has no network of its own (tools/import_guard.py), so it asks Sheets: ingest writes one
GOOGLEFINANCE formula per security and date still wanted into price_requests, Sheets evaluates them, and
ingest reads the answers back into the ledger's prices table, then rewrites the tab with what is left.

Which prices are wanted: every security with a quote in `securities` and every price_proxy ticker, on every date the legacy investments
workbook recorded holdings (so each old reading can be priced), then at each month-end after the last of
those dates up to the last month-end that has passed, and its latest close (asked each day ingest runs). With no legacy readings, month-ends start from the
first holdings reading or investment activity. A date is asked once: an answer of "none" (the fund did not
exist yet, or GOOGLEFINANCE doesn't carry it) is kept as a prices row with no close, so it isn't asked
again; delete that row to ask again.

Each answer is the last close on or before the date, from the week before it, with the trading day it came
from (priced_date). A mutual fund's close is its NAV, unadjusted, which is what shares are worth.
"""

import re
from datetime import date, timedelta

import pandas as pd

from .legacy_investments import LEGACY_DOC
from .sheets import QUOTE

SOURCE = "googlefinance"
MAX_REQUESTS = 600  # per run; a backfill finishes over a few runs rather than one slow sheet
ANSWER = re.compile(r"^(\d{4}-\d{2}-\d{2}) (-?[0-9.]+(?:[eE][-+]?\d+)?)$")


def month_ends(after, through):
    """The last day of each month that ends after `after` and on or before `through` (dates)."""
    out = []
    y, m = after.year, after.month
    while True:
        first_next = date(y + (m == 12), m % 12 + 1, 1)
        end = first_next - timedelta(days=1)
        if end > through:
            return out
        if end > after:
            out.append(end)
        y, m = first_next.year, first_next.month


def quotes(securities):
    """{symbol: quote} for each security with a usable quote, and the symbols whose quote can't be used."""
    usable, refused = {}, []
    if securities is None or securities.empty:
        return usable, refused
    for symbol, quote in zip(securities["symbol"].astype(str).str.strip(), securities["quote"].astype(str).str.strip()):
        if not symbol or not quote or symbol in usable:
            continue
        if QUOTE.match(quote):
            usable[symbol] = quote
        else:
            refused.append(symbol)
    return usable, refused


def wanted_dates(holdings, activity, today):
    """The dates prices are wanted on: the legacy readings' dates, then month-ends after the last of them."""
    holdings = holdings if holdings is not None else pd.DataFrame(columns=["as_of", "doc_id"])
    legacy = sorted({str(d) for d in holdings.loc[holdings["doc_id"].astype(str) == LEGACY_DOC, "as_of"] if str(d).strip()})
    if legacy:
        start = date.fromisoformat(legacy[-1])
    else:
        seen = [str(d) for d in holdings["as_of"] if str(d).strip()]
        if activity is not None and len(activity):
            seen += [str(d) for d in activity["date"] if str(d).strip()]
        if not seen:
            return []
        first = date.fromisoformat(min(seen))
        start = date(first.year, first.month, 1) - timedelta(days=1)  # the month-end before the first date
    ends = [d.isoformat() for d in month_ends(start, today - timedelta(days=1))]
    return sorted(set(legacy) | set(ends))


def requests(securities, holdings, activity, prices, today, limit=MAX_REQUESTS, stand_ins=()):
    """The price_requests rows still wanted (symbol, quote, date), newest dates first, at most limit.

    stand_ins are the price_proxy tickers (valuation.proxies): each is asked like a security, under its
    ticker as both symbol and quote, so a plan fund with no price of its own can move with it.

    Besides the wanted dates, each security is asked for its latest close (on yesterday's date, the last close
    on or before it), so the projection and the views price with the market as it is, not a month old. A
    security GOOGLEFINANCE has never priced is not asked for its latest close, so it adds no empty rows.
    """
    usable, _ = quotes(securities)
    for ticker in stand_ins:
        if QUOTE.match(ticker) and ticker not in usable:
            usable[ticker] = ticker
    dates = wanted_dates(holdings, activity, today)
    have, priced = set(), set()
    if prices is not None and len(prices):
        sym, day = prices["symbol"].astype(str).str.strip(), prices["date"].astype(str).str.strip()
        have = set(zip(sym, day))
        if "close" in prices:
            priced = set(sym[pd.to_numeric(prices["close"], errors="coerce").notna()])
        tried = set(sym)
    else:
        tried = set()
    latest = (today - timedelta(days=1)).isoformat()
    rows = [{"symbol": s, "quote": q, "date": d} for d in sorted(dates, reverse=True) for s, q in sorted(usable.items())
            if (s, d) not in have]
    rows = [{"symbol": s, "quote": q, "date": latest} for s, q in sorted(usable.items())
            if (s, latest) not in have and latest not in dates and (s in priced or s not in tried)] + rows
    return pd.DataFrame(rows[:limit], columns=["symbol", "quote", "date"]), len(rows)


def harvest(typed):
    """(prices rows, rows still waiting) from the price_requests tab as Sheets evaluated it.

    "YYYY-MM-DD close" is a price; "none" is a date with no price; anything else ("Loading...", an error, a
    formula not evaluated yet) waits for the next read.
    """
    done, waiting = [], []
    if typed is None or typed.empty:
        return pd.DataFrame(columns=["symbol", "date", "priced_date", "close", "source"]), typed
    for r in typed.to_dict("records"):
        result = str(r.get("result", "")).strip()
        m = ANSWER.match(result)
        symbol, day = str(r["symbol"]).strip(), str(r["date"]).strip()
        if m and m.group(1) <= day:
            done.append({"symbol": symbol, "date": day, "priced_date": m.group(1), "close": float(m.group(2)),
                         "source": SOURCE})
        elif result.lower() == "none" or result.startswith("Date "):  # no rows in the week, or only a header
            done.append({"symbol": symbol, "date": day, "priced_date": "", "close": "", "source": SOURCE})
        else:
            waiting.append(r)
    out = pd.DataFrame(done, columns=["symbol", "date", "priced_date", "close", "source"])
    return out.drop_duplicates(["symbol", "date"]), pd.DataFrame(waiting, columns=list(typed.columns))


def latest(prices):
    """Each (symbol, date) with a close, as numbers: the prices compute can use."""
    if prices is None or prices.empty:
        return pd.DataFrame(columns=["symbol", "date", "priced_date", "close"])
    p = prices.copy()
    p["close"] = pd.to_numeric(p["close"], errors="coerce")
    p = p.dropna(subset=["close"])
    return p.drop_duplicates(["symbol", "date"], keep="last")[["symbol", "date", "priced_date", "close"]]
