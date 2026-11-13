"""Equity: the share awards still to vest and the shares held, for the Equity page.

Each grant's schedule in force (parsers/schwab_awards.in_force: the latest document's rows) gives what it
granted, what has vested by today and what is still to vest, month by month for the grant's whole life. A
grant's price is the grant_price:<grant id> setting when it is set, else the price on the award page; an
option's shares are worth what the price is over its grant (exercise) price, a share award's the price.

The price everything is valued at is the symbol's latest close (prices), or a vest's fair market value when
that is newer. A month already vested is valued at its vest's fair market value (equity_vests) when the
ledger has it, else at the latest price; a month to come at the latest price. The shares held are the open
lots of the grants' symbols (lots.py), with their basis, so the gain is what selling them all at the price
would make.

The Equity page moves the price by the equity_price_steps setting (default 5, 10 and 20 dollars a share, up
and down) and shows the gain on the shares held and the value still to vest at each; the arithmetic is the
page's, from these tables.
"""

import pandas as pd

from . import settings
from .parsers import schwab_awards

OPTIONS = ("OPTION", "ISO", "NSO", "NQ")  # award types worth only what the price is over their grant price
GRANT_COLUMNS = [
    "grant_id",
    "symbol",
    "award_type",
    "award_date",
    "grant_price",
    "price_from",  # setting (grant_price:<grant id>), award page, or blank when neither has one
    "granted",
    "vested",
    "unvested",
    "next_vest",
    "last_vest",
]
VEST_COLUMNS = ["month", "grant_id", "symbol", "shares", "price", "value", "basis"]  # basis: vested or latest
SUMMARY_COLUMNS = ["symbol", "price", "price_date", "held", "basis", "value", "gain", "unvested", "unvested_value"]
STEPS_DEFAULT = "5, 10, 20"


def _num(s):
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def grant_key(grant_id):
    """A grant as a setting names it: grant_price:<this>."""
    return settings.symbol_key(grant_id)


def per_share(award_type, price, grant_price):
    """What one share of a grant is worth at a price: an option's spread over its grant price, else the price."""
    if str(award_type).upper() in OPTIONS:
        return max(price - (grant_price or 0.0), 0.0)
    return price


def latest_price(prices, vested, symbol):
    """(price, date) the symbol is valued at: its latest close, or a vest's fair market value when newer; (None,
    "") with neither."""
    seen = []
    if prices is not None and len(prices):
        p = prices[prices["symbol"].astype(str).str.strip() == symbol]
        close = pd.to_numeric(p["close"], errors="coerce")
        seen += [(str(d)[:10], float(c)) for d, c in zip(p["date"], close) if pd.notna(c) and c > 0]
    if vested is not None and len(vested) and "symbol" in vested:
        v = vested[vested["symbol"] == symbol]
        seen += [(str(d)[:10], float(f)) for d, f in zip(v["vest_date"], _num(v["fmv"])) if f > 0]
    if not seen:
        return None, ""
    day, price = max(seen)
    return round(price, 4), day


def _schedule(schedule):
    s = schwab_awards.in_force(schedule).copy()
    if s.empty:
        return s
    for c in ("grant_id", "symbol", "award_type", "award_date", "vest_date"):
        s[c] = s[c].astype(str).str.strip()
    s["vest_date"] = s["vest_date"].str[:10]
    s["shares"] = _num(s["shares"])
    return s


def grants(schedule, params, today):
    """Each grant in force (GRANT_COLUMNS): its shares granted, vested by today and still to vest, its next and
    last vest, and its grant price with where it comes from."""
    s = _schedule(schedule)
    if s.empty:
        return pd.DataFrame(columns=GRANT_COLUMNS)
    out = []
    for gid, g in s.groupby("grant_id", sort=False):
        first = g.iloc[0]
        done, left = g[g["vest_date"] <= today], g[g["vest_date"] > today]
        said = settings.value(params, f"grant_price:{grant_key(gid)}").strip()
        page = pd.to_numeric(pd.Series([first.get("grant_price", "")]), errors="coerce").iloc[0]
        price, origin = None, ""
        try:
            price, origin = (float(said.replace(",", "").replace("$", "")), "setting") if said else (None, "")
        except ValueError:
            price, origin = None, ""
        if price is None and pd.notna(page) and page > 0:
            price, origin = float(page), "award page"
        out.append(
            {
                "grant_id": gid,
                "symbol": first["symbol"],
                "award_type": first["award_type"].upper(),
                "award_date": str(first["award_date"])[:10],
                "grant_price": "" if price is None else round(price, 4),
                "price_from": origin,
                "granted": round(float(g["shares"].sum()), 4),
                "vested": round(float(done["shares"].sum()), 4),
                "unvested": round(float(left["shares"].sum()), 4),
                "next_vest": left["vest_date"].min() if len(left) else "",
                "last_vest": g["vest_date"].max(),
            }
        )
    return pd.DataFrame(out, columns=GRANT_COLUMNS).sort_values(["award_date", "grant_id"]).reset_index(drop=True)


def vesting(schedule, granted, vested, prices, today):
    """Each grant's shares vesting each month of its life (VEST_COLUMNS) and their value: a month vested at its
    vest's fair market value when the ledger has it, else (and every month to come) at the latest price."""
    s = _schedule(schedule)
    if s.empty or granted is None or granted.empty:
        return pd.DataFrame(columns=VEST_COLUMNS)
    fmv = {}
    if vested is not None and len(vested):
        for gid, day, f in zip(vested["grant_id"].astype(str).str.strip(), vested["vest_date"], _num(vested["fmv"])):
            if f > 0:
                fmv[(gid, str(day)[:10])] = float(f)
    info = granted.set_index("grant_id")
    latest = {sym: latest_price(prices, _with_symbol(vested, s), sym)[0] for sym in set(s["symbol"])}
    rows = []
    for r in s.to_dict("records"):
        gid, day, symbol = r["grant_id"], r["vest_date"], r["symbol"]
        price, basis = fmv.get((gid, day)), "vested"
        if price is None or day > today:
            price, basis = latest.get(symbol), "latest"
        if price is None:
            continue
        g = info.loc[gid]
        strike = float(g["grant_price"]) if g["grant_price"] != "" else 0.0
        value = r["shares"] * per_share(g["award_type"], price, strike)
        rows.append(
            {
                "month": day[:7],
                "grant_id": gid,
                "symbol": symbol,
                "shares": r["shares"],
                "price": round(price, 4),
                "value": value,
                "basis": basis,
            }
        )
    if not rows:
        return pd.DataFrame(columns=VEST_COLUMNS)
    out = pd.DataFrame(rows)
    out = out.groupby(["month", "grant_id", "symbol", "basis"], as_index=False).agg(
        shares=("shares", "sum"), price=("price", "last"), value=("value", "sum")
    )
    out["value"] = out["value"].round(2)
    return out[VEST_COLUMNS].sort_values(["month", "grant_id"]).reset_index(drop=True)


def _with_symbol(vested, schedule):
    """equity_vests with each vest's symbol, from its grant's schedule."""
    if vested is None or vested.empty:
        return vested
    symbol = dict(zip(schedule["grant_id"], schedule["symbol"]))
    return vested.assign(symbol=vested["grant_id"].astype(str).str.strip().map(symbol))


def summary(schedule, granted, open_lots, vested, prices):
    """Per symbol of the grants (SUMMARY_COLUMNS): the price and its date, the shares held (open lots) with their
    basis, value and gain at the price, and the shares still to vest with their value at it."""
    if granted is None or granted.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    s = _schedule(schedule)
    with_symbol = _with_symbol(vested, s)
    rows = []
    for symbol, g in granted.groupby("symbol", sort=True):
        price, day = latest_price(prices, with_symbol, symbol)
        lots = open_lots[open_lots["symbol"].astype(str).str.strip() == symbol] if open_lots is not None else None
        held = float(_num(lots["shares"]).sum()) if lots is not None and len(lots) else 0.0
        basis = float(_num(lots["basis"]).sum()) if lots is not None and len(lots) else 0.0
        unvested_value = 0.0
        if price is not None:
            for r in g.to_dict("records"):
                strike = float(r["grant_price"]) if r["grant_price"] != "" else 0.0
                unvested_value += r["unvested"] * per_share(r["award_type"], price, strike)
        value = held * price if price is not None else None
        rows.append(
            {
                "symbol": symbol,
                "price": "" if price is None else price,
                "price_date": day,
                "held": round(held, 4),
                "basis": round(basis, 2),
                "value": "" if value is None else round(value, 2),
                "gain": "" if value is None else round(value - basis, 2),
                "unvested": round(float(g["unvested"].sum()), 4),
                "unvested_value": "" if price is None else round(unvested_value, 2),
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
