"""Tax lots and wash sales, derived every run from vests, sales and purchases across every account.

Lots. Each vest of shares (equity_vests: the net shares deposited, at the vest's market value) and each purchase
or reinvested dividend (investment_activity) is a lot. A sale of shares from a stock plan (equity_sales) names
its lot by grant and vest date, as the plan's records do; any other sale takes the account's oldest shares first.
Stock splits (stock_splits) restate every share count and price in today's shares, so a lot bought before a
split and its replacement bought after it compare share for share; Form 8949 shows shares as of each sale.

Wash sales (IRC 1091, IRS Publication 550), for the symbols they are checked for (the stock plan's, unless
the wash_sale_symbols setting names others). A sale at a loss is a wash to the extent that substantially
identical shares (the same symbol, or the same `securities.wash_group`) were acquired within 30 days before or
after it. For each lot sold at a loss, in the order the shares are sold:

  - replacement shares come from other lots (never the lot being sold) acquired in the 61-day window, earliest
    first; each share replaces at most once, and a lot acquired before the sale counts only for the shares it
    still holds when the sale is matched (shares sold later in the same sale still count, as the old sheet had);
  - the matched part of the loss is disallowed (Form 8949 code W, column (g)) and added to the replacement
    shares' basis, and their holding period starts earlier by the days the sold shares were held. A lot that
    takes part of an adjustment splits in two, so each part keeps its own basis and dates;
  - when the replacement is in an IRA, a 401(k), an HSA or a 529, the disallowed loss is lost for good
    (Rev. Rul. 2008-5): nothing is added to any basis.

Shares held more than a year from the start of their holding period are long term.

Outputs: form_8949 (each lot part sold: proceeds, adjusted basis, the adjustment and the gain as reported),
open lots (what each lot still holds, its basis and holding period), and notes. Nothing here reads a name,
account or symbol: they come from the ledger.
"""

from datetime import date, timedelta

import pandas as pd

from . import logs, valuation
from .legacy import parse_date

WINDOW_DAYS = 30
SHARE_EPS = 1e-6
SHELTERED = ("401k", "ira", "hsa", "529")

FORM_8949 = [
    "year",
    "sale_date",
    "account_id",
    "symbol",
    "shares",
    "acquired",
    "holding_from",
    "term",
    "proceeds",
    "basis",
    "wash_carried",
    "code",
    "adjustment",
    "gain",
    "lot_id",
    "piece",
    "replaced_by",
    "reported_basis",
    "note",
]
OPEN_LOTS = [
    "lot_id",
    "piece",
    "account_id",
    "symbol",
    "acquired",
    "holding_from",
    "shares",
    "cost_per_share",
    "basis",
    "wash_added",
    "kind",
    "grant_id",
]
ACQUIRED = ["lot_id", "account_id", "sheltered", "symbol", "group", "acquired", "shares", "cost", "kind", "grant_id"]
SOLD = ["sale_date", "account_id", "symbol", "group", "shares", "proceeds", "lot_id", "reported_basis", "order"]


def _num(value):
    try:
        text = str(value).replace(",", "").replace("$", "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def _day(value):
    """An ISO date from a date cell (text or a sheet's serial day count), "" for a blank or unreadable one."""
    if value is None or str(value).strip() == "":
        return ""
    try:
        return parse_date(value).isoformat()
    except ValueError:
        return ""


def groups(securities):
    """{symbol: wash group}: a security's wash_group when set, else its own symbol (the last row wins)."""
    out = {}
    if securities is not None and len(securities) and "wash_group" in securities:
        for s, g in zip(securities["symbol"].astype(str).str.strip(), securities["wash_group"].astype(str).str.strip()):
            if s:
                out[s] = g or s
    return out


def factor(splits, symbol, day):
    """How many of today's shares one share held on day is: the product of the ratios of the symbol's splits
    after day."""
    f = 1.0
    if splits is None or splits.empty:
        return f
    for s, d, r in zip(splits["symbol"].astype(str).str.strip(), splits["date"], splits["ratio"]):
        ratio = _num(r)
        if s == symbol and ratio and _day(d) > day:
            f *= ratio
    return f


def grant_symbols(schedule):
    """{grant_id: symbol} from vest_schedule."""
    out = {}
    if schedule is not None and len(schedule):
        for g, s in zip(schedule["grant_id"].astype(str).str.strip(), schedule["symbol"].astype(str).str.strip()):
            if g and s:
                out.setdefault(g, s)
    return out


def equity_lots(vests, sales, schedule, accounts=None, group_of=None):
    """(acquired, sold, notes) from equity_vests and equity_sales: each vest's net shares at its market value,
    and each sale naming its lot. accounts: {symbol: the taxable account its shares are held in} (holders)."""
    symbols, group_of, accounts = grant_symbols(schedule), group_of or {}, accounts or {}
    acq, sold, notes = [], [], []
    for r in vests.to_dict("records") if vests is not None and len(vests) else []:
        grant, day = str(r["grant_id"]).strip(), _day(r["vest_date"])
        shares, fmv = _num(r["net_shares"]), _num(r["fmv"])
        symbol = symbols.get(grant, "")
        if not (day and shares and fmv is not None):
            continue
        if not symbol:
            notes.append(logs.warn(f"lots: grant {grant} has no symbol in vest_schedule; its vests are left out"))
            continue
        acq.append(
            {
                "lot_id": f"vest:{grant}:{day}",
                "account_id": accounts.get(symbol, ""),
                "sheltered": False,
                "symbol": symbol,
                "group": group_of.get(symbol, symbol),
                "acquired": day,
                "shares": shares,
                "cost": fmv,
                "kind": "vest",
                "grant_id": grant,
            }
        )
    for n, r in enumerate(sales.to_dict("records") if sales is not None and len(sales) else []):
        grant, day, vested = str(r["grant_id"]).strip(), _day(r["sale_date"]), _day(r["vest_date"])
        shares = _num(r["shares"])
        proceeds = _num(r["proceeds"])
        if proceeds is None and shares is not None and _num(r["sale_price"]) is not None:
            proceeds = shares * _num(r["sale_price"])
        symbol = symbols.get(grant, "")
        if not (day and shares and proceeds is not None and symbol):
            continue
        sold.append(
            {
                "sale_date": day,
                "account_id": accounts.get(symbol, ""),
                "symbol": symbol,
                "group": group_of.get(symbol, symbol),
                "shares": shares,
                "proceeds": proceeds,
                "lot_id": f"vest:{grant}:{vested}" if vested else "",
                "reported_basis": _num(r["basis"]),
                "order": n,
            }
        )
    return pd.DataFrame(acq, columns=ACQUIRED), pd.DataFrame(sold, columns=SOLD), sorted(set(notes))


def holders(holdings, accounts, symbols):
    """{symbol: account_id}: the taxable account whose latest reading holds each symbol (a stock plan's shares
    land in a brokerage account)."""
    if holdings is None or holdings.empty or not symbols:
        return {}
    taxable = set()
    for a in accounts.to_dict("records") if accounts is not None and len(accounts) else []:
        if valuation.wrapper({k: str(v).strip() for k, v in a.items()}) == "taxable":
            taxable.add(str(a["account_id"]).strip())
    h = holdings.assign(
        symbol=holdings["symbol"].astype(str).str.strip(),
        account_id=holdings["account_id"].astype(str).str.strip(),
        as_of=holdings["as_of"].astype(str),
    )
    h = h[h["symbol"].isin(symbols) & h["account_id"].isin(taxable)].sort_values("as_of")
    return {s: g["account_id"].iloc[-1] for s, g in h.groupby("symbol")}


def activity_lots(activity, accounts, group_of=None, leave_out=()):
    """(acquired, sold) frames from investment_activity: each purchase or reinvested dividend of a fund (cash
    left out) is a lot in any account; each sale in a taxable account is a sale. Symbols in leave_out (a stock
    plan's, read from its own records) are skipped."""
    group_of = group_of or {}
    if activity is None or activity.empty:
        return pd.DataFrame(columns=ACQUIRED), pd.DataFrame(columns=SOLD)
    wraps = {}
    for a in accounts.to_dict("records") if accounts is not None and len(accounts) else []:
        wraps[str(a["account_id"]).strip()] = valuation.wrapper({k: str(v).strip() for k, v in a.items()})
    acq, sold = [], []
    for n, r in enumerate(activity.to_dict("records")):
        symbol, kind = str(r.get("symbol", "")).strip(), str(r.get("kind", "")).strip()
        account = str(r.get("account_id", "")).strip()
        shares, day = _num(r.get("shares")), _day(r.get("date"))
        wrap = wraps.get(account, "")
        if not (symbol and shares and day and wrap) or symbol in leave_out or valuation.is_cash(symbol, ""):
            continue
        price = _num(r.get("price"))
        value = _num(r.get("value"))
        if value is None:
            value = _num(r.get("amount"))
        each = price if price else (abs(value) / abs(shares) if value else None)
        buying = kind in ("buy", "reinvest") or (kind == "exchange" and shares > 0)
        selling = kind == "sell" or (kind == "exchange" and shares < 0)
        if buying and shares > 0 and each is not None:
            acq.append(
                {
                    "lot_id": f"{kind}:{account}:{symbol}:{day}:{n}",
                    "account_id": account,
                    "sheltered": wrap in SHELTERED,
                    "symbol": symbol,
                    "group": group_of.get(symbol, symbol),
                    "acquired": day,
                    "shares": shares,
                    "cost": each,
                    "kind": kind,
                    "grant_id": "",
                }
            )
        elif selling and wrap == "taxable" and each is not None:
            sold.append(
                {
                    "sale_date": day,
                    "account_id": account,
                    "symbol": symbol,
                    "group": group_of.get(symbol, symbol),
                    "shares": abs(shares),
                    "proceeds": abs(shares) * each,
                    "lot_id": "",
                    "reported_basis": None,
                    "order": n,
                }
            )
    return pd.DataFrame(acq, columns=ACQUIRED), pd.DataFrame(sold, columns=SOLD)


def _plus_year(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year + 1, day=28)


def term(holding_from, sale_date):
    """long_term when the shares were held more than a year, else short_term."""
    start, end = date.fromisoformat(holding_from), date.fromisoformat(sale_date)
    return "long_term" if end > _plus_year(start) else "short_term"


def build(acquired, sold, splits=None, wash_symbols=None):
    """(form_8949, open_lots, notes) from acquired and sold frames (equity_lots, activity_lots). wash_symbols:
    the symbols whose losses are checked for wash sales (their wash groups, that is); None checks every
    symbol. A loss in any other symbol is taken as it is."""
    watched = None if wash_symbols is None else set(wash_symbols)
    pieces, notes = [], []
    for r in acquired.sort_values(["acquired", "lot_id"], kind="stable").to_dict("records"):
        f = factor(splits, r["symbol"], r["acquired"])
        pieces.append(
            {
                "lot_id": r["lot_id"],
                "piece": 1,
                "account_id": r["account_id"],
                "sheltered": bool(r["sheltered"]),
                "symbol": r["symbol"],
                "group": r["group"],
                "acquired": r["acquired"],
                "holding_from": r["acquired"],
                "open": r["shares"] * f,
                "cost": r["cost"] / f,
                "added": 0.0,
                "used": False,
                "kind": r["kind"],
                "grant_id": r["grant_id"],
            }
        )
    count = {}
    for p in pieces:
        count[p["lot_id"]] = 1

    def split(p, shares):
        """Carve shares off piece p into a new piece just after it; p keeps the rest. Returns the new piece."""
        count[p["lot_id"]] += 1
        moved = p["added"] * shares / p["open"]
        q = {**p, "open": shares, "piece": count[p["lot_id"]], "added": moved}
        p["open"] -= shares
        p["added"] -= moved
        pieces.insert(pieces.index(p) + 1, q)
        return q

    acquired_on = {p["lot_id"]: p["acquired"] for p in pieces}
    rows, unmatched = [], 0
    order = (
        sold.assign(_acq=[acquired_on.get(x, "") for x in sold["lot_id"]]).sort_values(
            ["sale_date", "_acq", "order"], kind="stable"
        )
        if len(sold)
        else sold
    )
    for s in order.to_dict("records"):
        day, f = s["sale_date"], factor(splits, s["symbol"], s["sale_date"])
        need = total = s["shares"] * f
        each = s["proceeds"] / need
        reported = None if s["reported_basis"] is None or pd.isna(s["reported_basis"]) else float(s["reported_basis"])
        if s["lot_id"]:
            mine = [p for p in pieces if p["lot_id"] == s["lot_id"] and p["open"] > SHARE_EPS]
        else:
            mine = sorted(
                (
                    p
                    for p in pieces
                    if p["symbol"] == s["symbol"]
                    and p["account_id"] == s["account_id"]
                    and p["acquired"] <= day
                    and p["open"] > SHARE_EPS
                    and not p["sheltered"]
                ),
                key=lambda p: (p["acquired"], pieces.index(p)),
            )
        for p in mine:
            if need <= SHARE_EPS:
                break
            if p["open"] - need > SHARE_EPS:
                split(p, p["open"] - need)  # the rest stays open as a new piece; p is what is sold
            shares, carried = p["open"], p["added"]
            need -= shares
            p["open"] = 0.0
            proceeds, basis = shares * each, shares * p["cost"]
            gain = proceeds - basis
            adjustment, replaced, lost = 0.0, [], False
            if gain < -0.005 and (watched is None or p["symbol"] in watched or p["group"] in watched):
                held = (date.fromisoformat(day) - date.fromisoformat(p["holding_from"])).days
                lo = (date.fromisoformat(day) - timedelta(days=WINDOW_DAYS)).isoformat()
                hi = (date.fromisoformat(day) + timedelta(days=WINDOW_DAYS)).isoformat()
                want = shares
                for c in sorted(
                    (
                        c
                        for c in pieces
                        if c["group"] == p["group"]
                        and c["lot_id"] != p["lot_id"]
                        and not c["used"]
                        and c["open"] > SHARE_EPS
                        and lo <= c["acquired"] <= hi
                    ),
                    key=lambda c: (c["acquired"], pieces.index(c)),
                ):
                    if want <= SHARE_EPS:
                        break
                    take = min(c["open"], want)
                    if c["open"] - take > SHARE_EPS:
                        split(c, c["open"] - take)
                    part = -gain * take / shares
                    c["used"] = True
                    if c["sheltered"]:
                        lost = True
                    else:
                        c["cost"] += part / take
                        c["added"] += part
                        start = date.fromisoformat(c["acquired"]) - timedelta(days=held)
                        c["holding_from"] = min(c["holding_from"], start.isoformat())
                    adjustment += part
                    replaced.append(f"{c['lot_id']}#{c['piece']}")
                    want -= take
            note = "the replacement is in a tax-advantaged account: the disallowed loss is lost" if lost else ""
            rows.append(
                {
                    "year": day[:4],
                    "sale_date": day,
                    "account_id": s["account_id"],
                    "symbol": s["symbol"],
                    "shares": round(shares / f, 6),
                    "acquired": p["acquired"],
                    "holding_from": p["holding_from"],
                    "term": term(p["holding_from"], day),
                    "proceeds": round(proceeds, 2),
                    "basis": round(basis, 2),
                    "wash_carried": round(carried, 2),
                    "code": "W" if adjustment > 0.005 else "",
                    "adjustment": round(adjustment, 2),
                    "gain": round(gain + adjustment, 2),
                    "lot_id": p["lot_id"],
                    "piece": p["piece"],
                    "replaced_by": " ".join(replaced),
                    "reported_basis": "" if reported is None else round(reported * shares / total, 2),
                    "note": note,
                }
            )
        if need > 1e-4:
            unmatched += 1
            short = need / f
            notes.append(
                logs.warn(
                    f"lots: {day} sale of {s['symbol']}: {short:,.4f} shares with no lot to sell"
                    + (f" ({s['lot_id']})" if s["lot_id"] else "")
                )
            )
    form = pd.DataFrame(rows, columns=FORM_8949)
    still = []
    for p in pieces:  # parts of a lot with the same basis and dates read as one
        if p["open"] <= SHARE_EPS or p["sheltered"]:
            continue
        last = still[-1] if still else None
        if (
            last
            and last["lot_id"] == p["lot_id"]
            and last["holding_from"] == p["holding_from"]
            and abs(last["cost"] - p["cost"]) < 1e-4
        ):
            last["open"] += p["open"]
            last["added"] += p["added"]
        else:
            still.append(dict(p))
    lots = pd.DataFrame(
        [
            {
                "lot_id": p["lot_id"],
                "piece": p["piece"],
                "account_id": p["account_id"],
                "symbol": p["symbol"],
                "acquired": p["acquired"],
                "holding_from": p["holding_from"],
                "shares": round(p["open"], 6),
                "cost_per_share": round(p["cost"], 6),
                "basis": round(p["open"] * p["cost"], 2),
                "wash_added": round(p["added"], 2),
                "kind": p["kind"],
                "grant_id": p["grant_id"],
            }
            for p in still
        ],
        columns=OPEN_LOTS,
    )
    return form, lots, notes


def priced(open_lots, prices, today):
    """open_lots with today's price, value, unrealized gain and the term a sale today would be."""
    out = open_lots.copy()
    close = [prices.on(sym, today) for sym in out["symbol"]]
    out["price"] = [c if c is not None else "" for c in close]
    out["value"] = [round(c * n, 2) if c is not None else "" for c, n in zip(close, out["shares"])]
    out["gain"] = [round(c * n - b, 2) if c is not None else "" for c, n, b in zip(close, out["shares"], out["basis"])]
    out["term_if_sold"] = [term(h, today) for h in out["holding_from"]] if len(out) else []
    return out


def year_lines(form, facts=None):
    """A note per tax year: the gains Form 8949 gives, beside the filed return's when it is on file."""
    filed = {}
    if facts is not None and len(facts):
        for y, k, v in zip(facts["year"].astype(str), facts["key"].astype(str), facts["value"]):
            if k in ("capital_gain_short", "capital_gain_long"):
                filed[(y, k)] = _num(v)
    lines = []
    for y, t in sorted(by_year(form).items()):
        line = (
            f"lots {y}: short-term gain {t['short_term']:,.2f}, long-term {t['long_term']:,.2f}; "
            f"wash sales disallowed {t['disallowed']:,.2f}"
        )
        short, long = filed.get((y, "capital_gain_short")), filed.get((y, "capital_gain_long"))
        if short is not None or long is not None:
            line += f" (filed: short {short or 0:,.0f}, long {long or 0:,.0f})"
        lines.append(line)
    return lines


def holding_check(open_lots, holdings, symbols):
    """A note for each stock plan symbol whose open lots disagree with its latest reading's shares."""
    if holdings is None or holdings.empty:
        return []
    h = holdings.assign(symbol=holdings["symbol"].astype(str).str.strip(), as_of=holdings["as_of"].astype(str))
    lines = []
    for sym in sorted(symbols):
        mine = h[h["symbol"] == sym]
        if mine.empty:
            continue
        latest = mine[mine["as_of"] == mine["as_of"].max()]
        read = pd.to_numeric(latest["shares"], errors="coerce").sum()
        on = latest["as_of"].iloc[0][:10]
        have = (
            open_lots.loc[(open_lots["symbol"] == sym) & (open_lots["acquired"] <= on), "shares"].sum()
            if len(open_lots)
            else 0.0
        )
        if abs(read - have) > 0.01:
            lines.append(
                logs.warn(
                    f"lots: {sym} open lots hold {have:,.3f} shares but the {on} reading "
                    f"has {read:,.3f}: a vest or sale is missing"
                )
            )
    return lines


def check_reported(form, tolerance=0.02):
    """Lot parts whose basis before wash sales (basis less wash_carried) disagrees with the plan's reported
    basis for them (its sale row's, shared by shares)."""
    if form.empty:
        return form
    reported = pd.to_numeric(form["reported_basis"], errors="coerce")
    return form[reported.notna() & ((reported - (form["basis"] - form["wash_carried"])).abs() > tolerance)]


def by_year(form):
    """{year: {"short_term": gain, "long_term": gain, "disallowed": adjustment}} from form_8949."""
    out = {}
    for r in form.to_dict("records"):
        y = out.setdefault(str(r["year"]), {"short_term": 0.0, "long_term": 0.0, "disallowed": 0.0})
        y[r["term"]] += r["gain"]
        y["disallowed"] += r["adjustment"]
    return out


def adjusted_sales(form):
    """form_8949 as equity_sales-like rows (sale_date, gain, term) for the tax projection."""
    return form[["sale_date", "gain", "term"]].copy()
