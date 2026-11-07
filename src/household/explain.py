"""Explanation downloads matched to the card and bank lines they explain.

A PayPal line on a card says only PAYPAL; PayPal's own download says who was
paid. An Amazon line says only AMAZON; Amazon's order history says what was in
the shipment. Neither export adds money: it names a line already in the
ledger, matched by amount and date.

  App stores  order_items rows of service google (Google Pay's purchase list, or Google Play
           receipts) and apple (Apple's emailed receipts), match_named: a GOOGLE or APPLE.COM
           line takes what its order bought (the app first) as its payee, so review asks once
           about a subscription that renews.
  PayPal   payment_details rows of service paypal: same signed amount, the
           line posted WINDOW days around the payment. The line takes the
           counterparty as its payee, and categorization goes on from there
           (rules, your answers for that payee, the model).
  Venmo    payment_details rows of service venmo (Venmo's emails), match_venmo: a VENMO line that
           is one payment takes who it went to or came from and its note, as PayPal's; a cash-out
           to the bank matches the transfer's email and takes the payments received since the
           transfer before it, which it carried.
  Orders   order_items of a service in ORDER_SERVICES (Amazon, Target, and any a household
           parameter order_payee:<service> adds, so a local store's name stays out of this code; any
           store whose order history lists items joins by a parser writing
           order_items rows and an entry there): a card charge is one shipment
           (the items shipped together, summed) or, failing that, a whole
           order or, failing both, one item of a shipment that held several (a
           store that charges each part as it ships); a credit is one refund, of the item that cost exactly that
           much if there is one. The line takes its category from the items:
           Whole Foods orders are food, departments map through DEPARTMENTS,
           anything else is household, and a charge whose items fall in several
           categories is split by item amount. The items themselves go along
           (contents), so a view can show what a charge bought.

Each match is one to one, closest dates first. Orders paid with a card that is
not one of the household's (a corporate card) are left out of matching.

Coverage: a service's export covers the dates from its first to its last row.
A line from that service dated inside the coverage that matched nothing is not
waiting any more; categorization handles it like any other line.
"""

import json
import re

import pandas as pd

WINDOW = (-2, 6)  # days from the explanation's date to the line's posting date
DEPARTMENTS = {
    "grocery": "food", "grocery & gourmet food": "food",
    "apparel": "clothing", "shoes": "clothing", "watch": "clothing",
    "pet products": "pets", "pet supplies": "pets",
    "lawn & patio": "garden", "lawn and garden": "garden",
    "tools & home improvement": "maintenance", "tools & hardware": "maintenance",
    "alcohol": "booze", "beer, wine & spirits": "booze",
    "toys & games": "family", "books": "media", "charity": "charity", "membership": "subscriptions",
}
DEFAULT = "household"
WHOLE_FOODS = "wholefoods.com"
ITEM_CHARS = 80  # an item's name as shown with its charge, cut to this
# stores whose order history itemizes a charge: service -> the pattern of its lines' payee
ORDER_SERVICES = {"amazon": "AMAZON|AMZN", "target": r"\bTARGET\b|TARGET\.?COM"}
ORDER_PAYEE = "order_payee:"  # a household parameter "order_payee:<service>" adds a store, its value the payee pattern


def coverage(details=None, items=None):
    """{service: (first date, last date)} over the explanation rows on file."""
    out = {}
    for df in (details, items):
        if df is None or df.empty:
            continue
        for service, g in df.groupby("service"):
            dates = g["date"].astype(str).str[:10]
            dates = dates[dates != ""]
            if len(dates):
                lo, hi = out.get(service, (dates.min(), dates.max()))
                out[service] = (min(lo, dates.min()), max(hi, dates.max()))
    return out


def _greedy(lines, candidates, key, group_cols=("group",), exclusive=None):
    """One-to-one matches between lines and candidate groups with the same amount, closest dates first.

    exclusive(group_row) -> ids that become unusable once that group is used.
    """
    if lines.empty or candidates.empty:
        return []
    merged = lines.assign(_k=lines[key]).merge(candidates.assign(_k=candidates["amount"]), on="_k",
                                               suffixes=("", "_c"))
    gap = (pd.to_datetime(merged["date"]) - pd.to_datetime(merged["date_c"])).dt.days
    merged = merged[gap.between(*WINDOW)].assign(_gap=gap.abs())
    merged = merged.sort_values(["_gap", "rank", "date", "txn_id"])
    used_lines, used_groups, out = set(), set(), []
    for r in merged.to_dict("records"):
        if r["txn_id"] in used_lines or r["group"] in used_groups:
            continue
        used_lines.add(r["txn_id"])
        used_groups.update(exclusive(r) if exclusive else {r["group"]})
        out.append(r)
    return out


def match_paypal(lines, details):
    """{txn_id: payee} for PAYPAL lines named by PayPal's download."""
    if details is None or details.empty:
        return {}
    paypal = lines[lines["payee_raw"].astype(str).str.upper().str.contains("PAYPAL")]
    d = details[details["service"] == "paypal"].copy()
    d["amount"] = pd.to_numeric(d["amount"]).round(2)
    d = d.assign(group=d["detail_id"], rank=0)
    found = _greedy(paypal.assign(amt=pd.to_numeric(paypal["amount"]).round(2)), d, "amt")
    return {r["txn_id"]: " ".join(f"{r['counterparty']} {r['memo']}".split()) for r in found}


VENMO_TRANSFER = "Venmo transfer"  # how a cash-out is named, before what it carried


def match_venmo(lines, details):
    """{txn_id: payee} for VENMO lines named by Venmo's emails: a payment by who it went to or came from and its
    note, as PayPal's are; a transfer to the bank (a cash-out) by the payments received since the transfer
    before it, which are what it carried."""
    if details is None or details.empty:
        return {}
    venmo = lines[lines["payee_raw"].astype(str).str.upper().str.contains("VENMO")]
    d = details[details["service"] == "venmo"].copy()
    if venmo.empty or d.empty:
        return {}
    d["amount"] = pd.to_numeric(d["amount"]).round(2)
    d["date"] = d["date"].astype(str).str[:10]
    d = d.assign(group=d["detail_id"], rank=0)
    found = _greedy(venmo.assign(amt=pd.to_numeric(venmo["amount"]).round(2)), d, "amt")
    received = d[d["kind"] == "Payment Received"].sort_values("date")
    transfers = sorted(d.loc[d["kind"] == "Transfer", "date"])
    out = {}
    for r in found:
        if r["kind"] != "Transfer":
            out[r["txn_id"]] = " ".join(f"{r['counterparty']} {r['memo']}".split())
            continue
        before = max((t for t in transfers if t < r["date_c"]), default="")
        carried = received[(received["date"] > before) & (received["date"] <= r["date_c"])]
        names = [f"{c} ({m})" if m else c for c, m in zip(carried["counterparty"], carried["memo"])]
        out[r["txn_id"]] = VENMO_TRANSFER + (": " + "; ".join(names) if names else "")
    return out


def _category(item):
    chosen = str(item.get("category", "") or "").strip()  # a person's category for this item (item_categories)
    if chosen and chosen != "nan":
        return chosen
    if str(item["website"]).strip().lower() == WHOLE_FOODS:
        return "food"
    return DEPARTMENTS.get(str(item["department"]).strip().lower(), DEFAULT)


def order_groups(items, cards, service="amazon"):
    """A service's shipments, whole orders and refunds, each with its amount and date, ready to match.

    cards: the household's cards' last four digits; an order paid with any other
    card is left out (an order with no card on file is kept).
    """
    if items is None or items.empty:
        return pd.DataFrame(columns=["group", "order_id", "level", "date", "amount", "rank"])
    df = items[items["service"] == service].copy()
    df["amount"] = pd.to_numeric(df["amount"])
    bought = df[df["kind"] == "item"]
    foreign = bought[~bought["card"].isin(set(cards) | {"", "gift"})]["order_id"]
    bought = bought[~bought["order_id"].isin(set(foreign))]
    shipments = (bought.groupby(["order_id", "date"], as_index=False)["amount"].sum()
                 .assign(level="shipment", rank=0))
    orders = (bought.groupby("order_id", as_index=False).agg(date=("order_date", "first"), amount=("amount", "sum"))
              .assign(level="order", rank=1))
    refunds = df[df["kind"] == "refund"][["order_id", "date", "amount", "item_id"]].assign(level="refund", rank=0)
    # an item on its own, last: a store that charges each part of a shipment separately (an order history with
    # one date for the whole order cannot say which items went together)
    several = bought.groupby(["order_id", "date"])["item_id"].transform("size") > 1
    singles = bought[several][["order_id", "date", "amount", "item_id"]].assign(level="item", rank=2)
    groups = pd.concat([shipments, orders, refunds, singles], ignore_index=True)
    groups["amount"] = groups["amount"].round(2)
    groups["group"] = (groups["level"] + ":" + groups["order_id"] + ":" + groups["date"].astype(str)
                       + ":" + groups.get("item_id", pd.Series("", index=groups.index)).fillna("").astype(str))
    return groups


def amazon_groups(items, cards):
    return order_groups(items, cards, "amazon")


def match_orders(lines, items, cards, contents=None, service="amazon", services=None):
    """{txn_id: [(category, amount), ...]} for a service's lines matched to its order history.

    The split amounts carry the line's sign and sum to the line's amount. contents,
    when a dict, is filled with {txn_id: [(category, item, amount), ...]}: the
    items each matched line paid for (or the one refunded), as the order history
    prices them.
    """
    groups = order_groups(items, cards, service)
    if groups.empty:
        return {}
    pattern = (services or ORDER_SERVICES).get(service, service.upper())
    store = lines[lines["payee_raw"].astype(str).str.upper().str.contains(pattern)].copy()
    store["amt"] = pd.to_numeric(store["amount"]).round(2)
    charges = store[store["amt"] < 0].assign(abs_amt=lambda d: -d["amt"])
    credits = store[store["amt"] > 0].assign(abs_amt=lambda d: d["amt"])

    def exclusive(r):  # a part used rules out what contains it and what it contains
        if r["level"] == "refund":
            return {r["group"]}
        same = groups[groups["order_id"] == r["order_id"]]
        orders = set(same.loc[same["level"] == "order", "group"])
        if r["level"] == "order":
            return set(same.loc[same["level"] != "refund", "group"])
        if r["level"] == "shipment":
            inside = same[(same["level"] == "item") & (same["date"] == r["date_c"])]
            return {r["group"]} | orders | set(inside["group"])
        shipment = same[(same["level"] == "shipment") & (same["date"] == r["date_c"])]
        return {r["group"]} | orders | set(shipment["group"])

    found = (_greedy(charges, groups[groups["level"] != "refund"], "abs_amt", exclusive=exclusive)
             + _greedy(credits, groups[groups["level"] == "refund"], "abs_amt"))
    bought = items[(items["service"] == service) & (items["kind"] == "item")].copy()
    bought["amount"] = pd.to_numeric(bought["amount"])
    out = {}
    for r in found:
        order = bought[bought["order_id"] == r["order_id"]]
        if r["level"] == "shipment":
            order = order[order["date"] == r["date_c"]]
        elif r["level"] == "item":
            order = order[order["item_id"] == r["item_id"]]
        elif r["level"] == "refund":  # an item that cost exactly the refund is the one returned
            same = order[(order["amount"].round(2) - abs(float(r["amt"]))).abs() < 0.005]
            order = same.head(1) if len(same) else order
        if contents is not None:
            contents[r["txn_id"]] = [(_category(i), " ".join(str(i["item"]).split())[:ITEM_CHARS],
                                      round(float(i["amount"]), 2), str(i.get("item_id", "")))
                                     for i in order.to_dict("records")]
        weights = order.assign(category=order.apply(_category, axis=1)).groupby("category")["amount"].sum()
        weights = weights[weights > 0]
        if weights.empty:
            weights = pd.Series({DEFAULT: 1.0})
        amount = float(r["amt"])
        shares = (weights / weights.sum() * amount).round(2)
        shares.iloc[shares.abs().argmax()] += round(amount - shares.sum(), 2)  # cents land on the biggest
        out[r["txn_id"]] = sorted((c, round(float(v), 2)) for c, v in shares.items())
    return out


def match_amazon(lines, items, cards, contents=None):
    return match_orders(lines, items, cards, contents, "amazon")


def order_service(payee, services=None):
    """The store whose card lines look like this payee (services: ORDER_SERVICES by default), or ""."""
    text = str(payee).upper()
    return next((s for s, pattern in (services or ORDER_SERVICES).items() if re.search(pattern, text, re.I)), "")


def order_services(parameters=None):
    """ORDER_SERVICES and the stores the household's parameters add (order_payee:<service> = pattern)."""
    out = dict(ORDER_SERVICES)
    if parameters is not None and not parameters.empty:
        rows = parameters[(parameters["jurisdiction"] == "household")
                          & parameters["key"].astype(str).str.startswith(ORDER_PAYEE)]
        for key, value in zip(rows["key"], rows["value"]):
            if str(value).strip():
                out[str(key)[len(ORDER_PAYEE):].strip()] = str(value).strip()
    return out


def match_all_orders(lines, items, cards, contents=None, services=None):
    """match_orders over every store, each line matched by its own store's history."""
    out = {}
    for service in (services or ORDER_SERVICES):
        out.update(match_orders(lines, items, cards, contents, service, services))
    return out


# app stores whose purchases name a card line (Google's purchase list or receipts, Apple's receipts):
# service -> the pattern of its lines' payee
NAMED_SERVICES = {"google": r"GOOGLE", "apple": r"APPLE\.COM|APPLE COM|ITUNES"}


def _together(names):
    """Item names as one: "Apple TV: First; Second" when they share an app, else joined with "; "."""
    names = list(dict.fromkeys(str(n) for n in names))
    apps = {n.split(": ", 1)[0] for n in names if ": " in n}
    if len(names) > 1 and len(apps) == 1 and all(": " in n for n in names):
        return f"{apps.pop()}: " + "; ".join(n.split(": ", 1)[1] for n in names)
    return "; ".join(names)


# an Apple Card offer that receipts read before 0.17.4 took as the app: "Save 3% on all ... 2: Apple One"
APPLE_PROMO = re.compile(r"^Save \d+% on all your Apple purchases.*?\d: ")


def match_named(lines, items, service):
    """{txn_id: payee} for an app store's card lines named by what each charge bought.

    An order is one charge: its items (an Apple receipt can hold several) are summed and named together.
    A charge matches an order, a credit a refund, by amount and date, one to one.
    """
    if items is None or items.empty:
        return {}
    store = items[items["service"] == service].copy()
    if store.empty:
        return {}
    store["amount"] = pd.to_numeric(store["amount"])
    store["item"] = store["item"].astype(str).str.replace(APPLE_PROMO, "", regex=True)
    orders = (store.groupby(["order_id", "kind"], sort=False)
              .agg(amount=("amount", "sum"), date=("date", "min"), item=("item", _together)).reset_index())
    orders["amount"] = orders["amount"].round(2)
    orders = orders.assign(group=orders["order_id"] + "|" + orders["kind"], rank=0)
    upper = lines["payee_raw"].astype(str).str.upper()
    named = lines[upper.str.contains(NAMED_SERVICES[service]) & ~upper.str.contains("PAYROLL")].copy()
    named["amt"] = pd.to_numeric(named["amount"]).round(2)
    named["abs_amt"] = named["amt"].abs()
    found = (_greedy(named[named["amt"] < 0], orders[orders["kind"] == "item"], "abs_amt")
             + _greedy(named[named["amt"] > 0], orders[orders["kind"] == "refund"], "abs_amt"))
    return {r["txn_id"]: str(r["item"]) for r in found}


def match_google(lines, items):
    """{txn_id: payee} for GOOGLE lines named by Google's purchases: the app and item bought."""
    return match_named(lines, items, "google")


HINT_WINDOW = (-2, 10)  # days from a shipment to an unmatched charge that might be part of it
HINTS = 3


def amazon_hints(lines, items, matched, service="amazon", services=None):
    """{txn_id: "maybe: ..."} for a store's charges that matched nothing (AMAZON by default): the items
    shipped around then, closest in price first, to answer the line by hand (a gift card or a split
    payment hides the total)."""
    if items is None or items.empty:
        return {}
    bought = items[(items["service"] == service) & (items["kind"] == "item")].copy()
    if bought.empty:
        return {}
    bought["amount"] = pd.to_numeric(bought["amount"])
    bought["day"] = pd.to_datetime(bought["date"], errors="coerce")
    pattern = (services or ORDER_SERVICES).get(service, service.upper())
    store = lines[lines["payee_raw"].astype(str).str.upper().str.contains(pattern)
                  & ~lines["txn_id"].isin(set(matched))]
    out = {}
    for r in store.to_dict("records"):
        day, amount = pd.Timestamp(str(r["date"])[:10]), abs(float(r["amount"]))
        gap = (day - bought["day"]).dt.days
        near = bought[gap.between(*HINT_WINDOW)]
        if near.empty:
            continue
        near = near.assign(off=(near["amount"] - amount).abs()).sort_values("off").head(HINTS)
        out[r["txn_id"]] = "maybe: " + "; ".join(
            f"{str(i)[:40]} ({a:.2f}, {d})" for i, a, d in zip(near["item"], near["amount"], near["date"]))
    return out


def explain(lines, details=None, items=None, cards=(), services=None):
    """Everything categorization needs from the explanation downloads. services: the stores with an
    order history and their payee patterns (order_services; ORDER_SERVICES by default)."""
    services = services or ORDER_SERVICES
    named = {**match_paypal(lines, details), **match_venmo(lines, details), **match_named(lines, items, "google"),
             **match_named(lines, items, "apple")}
    contents = {}
    split = match_all_orders(lines, items, cards, contents, services)
    hints = {}
    for service in services:
        hints.update(amazon_hints(lines, items, split, service, services))
    return {"payee": named, "split": split, "coverage": coverage(details, items), "hints": hints, "services": services,
            "items": {t: json.dumps([list(i) for i in parts]) for t, parts in contents.items()}}
