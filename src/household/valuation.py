"""What each investment account held and was worth on a date: month-ends back to the first reading, and today.

Readings (holdings) are anchors; activity (investment_activity) and prices fill in between them. For an account on
a date:

  1. Positions come from its latest reading on or before the date. A reading is complete (what it doesn't
     list, the account no longer holds) unless it comes from a partial document: a history that gives only the
     cash or only the funds (PARTIAL_PARSERS). Past the latest complete reading, partial ones count cash and funds
     apart: a cash history updates the cash and leaves the funds, a fund history leaves the cash, and within one
     such document a fund's latest row counts even when another fund's is later (a history that ends each fund
     on its own last trade).
     The legacy workbook's readings are complete across the whole household: an account it stops listing held
     nothing from then until its next reading. Its readings within LEGACY_SNAPSHOT_DAYS of each other are one
     snapshot (the workbook was sometimes updated an account at a time).
  2. Fund shares roll forward with the activity after the reading (buys, sales, reinvested dividends,
     exchanges), and a fund bought since the reading joins in. Cash is carried as read.
  3. Shares are priced at the last close on or before the date. A fund with no close since its reading keeps
     the value it was read at.
  4. A workplace plan's statement gives balances per money source with no funds (symbol blank, source set).
     When that statement is newer than the fund reading, it sets the account's total and the funds give the
     mix; either way it splits the funds between pre-tax and Roth in proportion to its sources.

An account closed on or before the date holds nothing. Each row says how its value was found (basis): read,
priced, rolled (activity since the reading), carried (the value as read), scaled (to a newer statement).

Every row carries the account's wrapper (401k, ira, hsa, 529, ibonds, taxable), its tax bucket and its owner.
"""

from datetime import date, timedelta

import pandas as pd

from . import logs, settings
from .legacy_investments import LEGACY_DOC
from .prices import month_ends

COLUMNS = ["date", "account_id", "owner", "wrapper", "bucket", "symbol", "source", "asset_class", "shares", "price",
           "value", "basis"]

# accounts.type -> wrapper; "retirement" is the older word for both kinds of retirement account
WRAPPERS = {"workplace_plan": "401k", "ira": "ira", "hsa": "hsa", "education": "529", "savings_bonds": "ibonds",
            "brokerage": "taxable"}
INVESTED_TYPES = set(WRAPPERS) | {"retirement"}
BUCKETS = ("401k_pretax", "401k_roth", "ira_pretax", "ira_roth", "hsa", "529", "ibonds", "taxable", "other")
BUCKET_NAMES = {"401k_pretax": "401(k) pre-tax", "401k_roth": "401(k) Roth", "ira_pretax": "IRA pre-tax",
                "ira_roth": "IRA Roth", "hsa": "HSA", "529": "529", "ibonds": "I-Bonds", "taxable": "Taxable",
                "other": "Other"}
# parsers whose readings give only part of an account: HealthEquity's cash history and its fund history
PARTIAL_PARSERS = ("healthequity_cash", "healthequity")
LEGACY_SNAPSHOT_DAYS = 30
ROTH_WORDS = ("roth", "aftertax", "after_tax", "after-tax", "posttax", "post_tax")


def _num(value):
    try:
        text = str(value).replace(",", "").replace("$", "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def wrapper(account):
    """The kind of account an accounts row is: 401k, ira, hsa, 529, ibonds, taxable, or "" for one that isn't
    an investment account. A "retirement" account is an IRA when its tax treatment or id says so, else a plan."""
    kind = str(account.get("type", "")).strip()
    if kind == "retirement":
        return "ira" if "ira" in str(account.get("account_id", "")).lower() else "401k"
    return WRAPPERS.get(kind, "")


def is_roth_source(source):
    s = str(source).strip().lower()
    return any(w in s for w in ROTH_WORDS)


def bucket(wrap, tax_treatment, source=""):
    roth = str(tax_treatment).strip() == "roth" or is_roth_source(source)
    if wrap == "401k":
        return "401k_roth" if roth else "401k_pretax"
    if wrap == "ira":
        return "ira_roth" if roth else "ira_pretax"
    return wrap if wrap in BUCKETS else "other"


def owner_group(owner, adults):
    """An account owner as one of the adults, joint, or kids (anyone else: the children's own accounts)."""
    o = str(owner).strip().lower()
    return o if o in adults or o == "joint" else "kids"


def adults(params=None, paystubs=None):
    """The household's adults, lower case: the household_adults setting (names separated by commas), else
    the people on pay stubs."""
    if params is not None and len(params):
        names = [n.strip().lower() for n in str(settings.value(params, "household_adults")).split(",") if n.strip()]
        if names:
            return tuple(names)
    if paystubs is not None and len(paystubs):
        return tuple(sorted({str(p).strip().lower() for p in paystubs["person"] if str(p).strip()}))
    return ()


def classes(securities):
    """{symbol: asset_class} from securities, the last row for a symbol winning."""
    if securities is None or securities.empty:
        return {}
    out = {}
    for s, c in zip(securities["symbol"].astype(str).str.strip(), securities["asset_class"].astype(str).str.strip()):
        if s:
            out[s] = c
    return out


def is_cash(symbol, klass):
    return str(symbol).strip().lower() == "cash" or str(klass).strip() == "BA"


class Prices:
    """Closes by symbol, for the close on or just before a date."""

    def __init__(self, prices):
        self.by = {}
        if prices is not None and len(prices):
            p = prices.copy()
            p["close"] = pd.to_numeric(p["close"], errors="coerce")
            p = p.dropna(subset=["close"])
            p["day"] = [str(pd_ or d)[:10] for pd_, d in zip(p["priced_date"].astype(str).str.strip(), p["date"])]
            for s, g in p.groupby(p["symbol"].astype(str).str.strip()):
                self.by[s] = sorted(zip(g["day"], g["close"]))

    def on(self, symbol, day, since=""):
        """The last close on or before day, or None when there is none or it is older than since."""
        rows = self.by.get(str(symbol).strip())
        best = None
        for d, c in rows or ():
            if d > day:
                break
            best = (d, c)
        return best[1] if best and best[0] >= since else None


def valuation_dates(holdings, today):
    """Each month-end from the first reading's month to the last month-end passed, then today."""
    seen = [str(d)[:10] for d in holdings["as_of"] if str(d).strip()] if holdings is not None and len(holdings) else []
    if not seen:
        return [today]
    first = date.fromisoformat(min(seen))
    start = date(first.year, first.month, 1) - timedelta(days=1)
    t = date.fromisoformat(today)
    ends = [d.isoformat() for d in month_ends(start, t - timedelta(days=1))]
    return ends + ([today] if today not in ends else [])


def _readings(rows, day, klass, partial=frozenset(), gone=()):
    """The position rows in force on day: those from the latest complete reading on (a date the legacy
    workbook left the account out, in gone, is a complete reading of nothing), and of those, per kind (cash,
    fund), the latest reading, plus a partial document's rows of funds it last saw earlier."""
    rows = rows[rows["as_of"] <= day]
    if rows.empty:
        return rows
    ended = [g for g in gone if g <= day]
    if ended:  # a legacy snapshot that left the account out is an empty reading
        rows = rows[rows["as_of"] >= max(ended)]
        if rows.empty:
            return rows
    complete = rows[~rows["doc_id"].isin(partial)]
    if len(complete):
        rows = rows[rows["as_of"] >= complete["as_of"].max()]
    kind = ["cash" if is_cash(s, klass.get(s, "")) else "fund" for s in rows["symbol"]]
    rows = rows.assign(kind=kind)
    keep = []
    for _, g in rows.groupby("kind"):
        latest = g["as_of"].max()
        now = g[g["as_of"] == latest]
        keep.append(now)
        docs = set(now["doc_id"]) & set(partial)
        if docs:
            more = g[g["doc_id"].isin(docs) & (g["as_of"] < latest)
                     & ~g["symbol"].isin(set(now["symbol"]))]
            more = more.sort_values("as_of").drop_duplicates(["symbol", "source"], keep="last")
            keep.append(more)
    return pd.concat(keep, ignore_index=True)


def legacy_gone(holdings):
    """{account_id: [dates]}: each legacy snapshot's first date for an account the snapshot leaves out after
    listing it before (holdings with as_of and doc_id cleaned)."""
    legacy = holdings[holdings["doc_id"] == LEGACY_DOC]
    if legacy.empty:
        return {}
    snapshots, last = [], None
    for d in sorted(set(legacy["as_of"])):
        if last is None or (date.fromisoformat(d) - date.fromisoformat(last)).days > LEGACY_SNAPSHOT_DAYS:
            snapshots.append([d])
        else:
            snapshots[-1].append(d)
        last = d
    listed = legacy.groupby("as_of")["account_id"].agg(set).to_dict()
    first = holdings.groupby("account_id")["as_of"].min().to_dict()
    out = {}
    for snap in snapshots:
        there = set().union(*(listed[d] for d in snap))
        for account, since in first.items():
            if account not in there and since < snap[0]:
                out.setdefault(account, []).append(snap[0])
    return out


def _positions(positions, activity, prices, klass, day, base, partial=frozenset(), gone=()):
    """The fund and cash rows on day from readings and activity (no statement applied), and the fund reading's
    date ("" when no fund has been read by then)."""
    held = _readings(positions, day, klass, partial, gone)
    out = []
    for r in held.to_dict("records"):
        symbol, cls = r["symbol"], klass.get(r["symbol"], "")
        shares, value = _num(r["shares"]), _num(r["value"]) or 0.0
        basis, price = ("read" if r["as_of"] == day else "carried"), ""
        if r["kind"] == "fund" and shares is not None and symbol:
            moves = activity[(activity["symbol"] == symbol) & (activity["date"] > r["as_of"]) & (activity["date"] <= day)]
            if r["source"]:
                moves = moves[moves["source"] == r["source"]]
            delta = pd.to_numeric(moves["shares"], errors="coerce").fillna(0.0).sum()
            if abs(delta) > 1e-9:
                shares, basis = shares + delta, "rolled"
            close = prices.on(symbol, day, since=r["as_of"])
            if close is not None:
                price, value = close, shares * close
                if basis == "carried":
                    basis = "priced"
        elif shares is None and r["kind"] == "cash":
            shares = value
        out.append({**base, "symbol": symbol, "source": r["source"], "asset_class": cls,
                    "shares": round(shares, 6) if shares is not None else "", "price": price,
                    "value": round(value, 2), "basis": basis})
    funds = held[held["kind"] == "fund"] if len(held) else held
    fund_at = funds["as_of"].max() if len(funds) else ""
    if fund_at:  # funds bought since the fund reading join in from activity alone
        seen = {r["symbol"] for r in out}
        later = activity[(activity["date"] > fund_at) & (activity["date"] <= day) & (activity["symbol"] != "")
                         & ~activity["symbol"].isin(seen)]
        for symbol, g in later.groupby("symbol"):
            if is_cash(symbol, klass.get(symbol, "")):
                continue
            shares = pd.to_numeric(g["shares"], errors="coerce").fillna(0.0).sum()
            close = prices.on(symbol, day)
            if shares > 1e-6 and close is not None:
                out.append({**base, "symbol": symbol, "source": "", "asset_class": klass.get(symbol, ""),
                            "shares": round(shares, 6), "price": close, "value": round(shares * close, 2),
                            "basis": "rolled"})
    return out, fund_at


def account_on(account, holdings, activity, prices, klass, day, partial=frozenset(), gone=()):
    """The rows for one account (an accounts record) on day. holdings and activity are that account's rows;
    partial is the ids of partial documents and gone the dates the legacy workbook stopped listing it."""
    wrap, treatment = wrapper(account), account.get("tax_treatment", "")
    base = {"date": day, "account_id": account["account_id"], "owner": str(account.get("owner", "")).strip(),
            "wrapper": wrap}
    closed = str(account.get("closed", "")).strip()
    if holdings.empty or (closed and closed[:10] <= day):
        return []
    totals = holdings[(holdings["symbol"] == "") & (holdings["source"] != "")]
    positions = holdings.drop(totals.index)
    out, fund_at = _positions(positions, activity, prices, klass, day, base, partial, gone)
    if len(totals):
        out = _with_statement(out, totals, fund_at, day, base,
                              lambda then: _positions(positions, activity, prices, klass, then, base, partial, gone)[0])
    for r in out:
        r["bucket"] = bucket(wrap, treatment, r["source"])
    return [r for r in out if abs(r["value"]) >= 0.005]


def _with_statement(out, totals, fund_at, day, base, positions_on):
    """Apply a plan's money-source statements: the latest one on or before day sets the account's value when it
    is newer than the fund reading (the funds then move with the market from there and give the mix), and the
    nearest one splits funds of no stated source between pre-tax and Roth."""
    before = totals[totals["as_of"] <= day]
    pick = before if len(before) else totals[totals["as_of"] == totals["as_of"].min()]
    stated_on = pick["as_of"].max()
    stated = pick[pick["as_of"] == stated_on]
    amounts = pd.to_numeric(stated["value"], errors="coerce").fillna(0.0)
    total = float(amounts.sum())
    if total <= 0:
        return out
    roth = float(amounts[[is_roth_source(s) for s in stated["source"]]].sum()) / total
    if len(before) and stated_on > (fund_at or ""):
        then = sum(r["value"] for r in positions_on(stated_on))
        if not out or then <= 0:  # no funds known: the statement's sources are the account, as stated
            return [{**base, "symbol": "", "source": src, "asset_class": "", "shares": "", "price": "",
                     "value": round(float(v), 2), "basis": "read" if stated_on == day else "carried"}
                    for src, v in zip(stated["source"], amounts) if v]
        # the funds give only the mix: one row per fund (an older reading's sources aside), scaled to the statement
        merged = {}
        for r in out:
            m = merged.setdefault(r["symbol"], {**r, "source": "", "value": 0.0, "shares": 0.0})
            m["value"] += r["value"]
            m["shares"] = m["shares"] + r["shares"] if r["shares"] != "" and m["shares"] != "" else ""
        out = list(merged.values())
        for r in out:
            r["value"], r["basis"] = round(r["value"] * total / then, 2), "scaled"
    split = []
    for r in out:
        if r["source"]:
            split.append(r)
            continue
        for part, share in (("roth", roth), ("pretax", 1 - roth)):
            if share > 1e-9:
                shares = r["shares"]
                split.append({**r, "source": part, "value": round(r["value"] * share, 2),
                              "shares": round(shares * share, 6) if shares != "" else ""})
    return split


def partial_docs(documents):
    """The ids of documents whose readings give only part of an account, from source_documents."""
    if documents is None or documents.empty:
        return frozenset()
    parser = documents["parser"].astype(str).str.strip()
    return frozenset(documents.loc[parser.isin(PARTIAL_PARSERS), "doc_id"].astype(str).str.strip())


def values(accounts, holdings, activity, prices, securities, today, dates=None, partial=frozenset()):
    """COLUMNS for every investment account on each valuation date, and notes. partial: the ids of partial
    documents (partial_docs)."""
    notes = []
    if holdings is None or holdings.empty or accounts is None or accounts.empty:
        return pd.DataFrame(columns=COLUMNS), ["valuation: no holdings yet"]
    h = holdings.copy()
    for c in ("as_of", "account_id", "symbol", "source", "doc_id"):
        h[c] = h[c].astype(str).str.strip()
    h["as_of"] = h["as_of"].str[:10]
    a = activity.copy() if activity is not None and len(activity) else pd.DataFrame(
        columns=["account_id", "date", "symbol", "source", "shares"])
    for c in ("account_id", "date", "symbol", "source"):
        a[c] = a[c].astype(str).str.strip()
    a["date"] = a["date"].str[:10]
    klass, priced = classes(securities), Prices(prices)
    dates = dates or valuation_dates(h, today)
    gone = legacy_gone(h)
    rows = []
    for acct in accounts.to_dict("records"):
        acct = {k: str(v).strip() for k, v in acct.items()}
        if str(acct.get("type", "")) not in INVESTED_TYPES:
            continue
        mine = h[h["account_id"] == acct["account_id"]]
        if mine.empty:
            continue
        acts = a[a["account_id"] == acct["account_id"]]
        for day in dates:
            rows += account_on(acct, mine, acts, priced, klass, day, partial, gone.get(acct["account_id"], ()))
    unknown = sorted(set(h["account_id"]) - set(accounts["account_id"].astype(str).str.strip()))
    if unknown:
        notes.append(logs.warn("valuation: holdings for accounts the ledger doesn't list: " + ", ".join(unknown)))
    out = pd.DataFrame(rows, columns=COLUMNS)
    latest = out[out["date"] == out["date"].max()] if len(out) else out
    blank = sorted(set(latest.loc[(latest["asset_class"] == "") & (latest["symbol"] != "")
                                  & ~latest["wrapper"].isin(("529", "ibonds")), "symbol"]))
    if blank:
        notes.append(logs.warn(f"valuation: {len(blank)} held funds have no asset class in securities: " + ", ".join(blank)))
    return out, notes


def split_by_owner(df, people, value="value"):
    """Each row's value as each adult's share and the kids': a joint account splits evenly between the adults.
    Returns a frame with one column per adult and "kids", on df's index."""
    groups = df["owner"].map(lambda o: owner_group(o, people))
    v = pd.to_numeric(df[value], errors="coerce").fillna(0.0)
    joint = v.where(groups == "joint", 0.0) / max(len(people), 1)
    out = {p: v.where(groups == p, 0.0) + joint for p in people}
    out["kids"] = v.where(groups == "kids", 0.0)
    return pd.DataFrame(out, index=df.index)
