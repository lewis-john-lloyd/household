"""Explanation downloads matched to the card and bank lines they explain.

A PayPal line on a card says only PAYPAL; PayPal's own download says who was
paid. An Amazon line says only AMAZON; Amazon's order history says what was in
the shipment. Neither export adds money: it names a line already in the
ledger, matched by amount and date.

  Google   order_items rows of service google (Google Pay's purchase list): a
           GOOGLE line takes the app and item bought as its payee.
  PayPal   payment_details rows of service paypal: same signed amount, the
           line posted WINDOW days around the payment. The line takes the
           counterparty as its payee, and categorization goes on from there
           (rules, your answers for that payee, the model).
  Amazon   order_items: a card charge is one shipment (the items shipped
           together, summed) or, failing that, a whole order; a credit is one
           refund, of the item that cost exactly that much if there is one. The line takes its category from the items: Whole Foods
           orders are food, departments map through DEPARTMENTS, anything else
           is household, and a charge whose items fall in several categories
           is split by item amount. The items themselves go along (contents),
           so a view can show what an Amazon charge bought.

Each match is one to one, closest dates first. Orders paid with a card that is
not one of the household's (a corporate card) are left out of matching.

Coverage: a service's export covers the dates from its first to its last row.
A line from that service dated inside the coverage that matched nothing is not
waiting any more; categorization handles it like any other line.
"""

import json

import pandas as pd

WINDOW = (-2, 6)  # days from the explanation's date to the line's posting date
DEPARTMENTS = {
    "grocery": "food", "grocery & gourmet food": "food",
    "apparel": "clothing", "shoes": "clothing", "watch": "clothing",
    "pet products": "pets", "pet supplies": "pets",
    "lawn & patio": "garden", "lawn and garden": "garden",
    "tools & home improvement": "maintenance", "tools & hardware": "maintenance",
}
DEFAULT = "household"
WHOLE_FOODS = "wholefoods.com"
ITEM_CHARS = 80  # an item's name as shown with its charge, cut to this


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


def _category(item):
    if str(item["website"]).strip().lower() == WHOLE_FOODS:
        return "food"
    return DEPARTMENTS.get(str(item["department"]).strip().lower(), DEFAULT)


def amazon_groups(items, cards):
    """Shipments, whole orders and refunds, each with its amount and date, ready to match.

    cards: the household's cards' last four digits; an order paid with any other
    card is left out.
    """
    if items is None or items.empty:
        return pd.DataFrame(columns=["group", "order_id", "level", "date", "amount", "rank"])
    df = items[items["service"] == "amazon"].copy()
    df["amount"] = pd.to_numeric(df["amount"])
    bought = df[df["kind"] == "item"]
    foreign = bought[~bought["card"].isin(set(cards) | {"", "gift"})]["order_id"]
    bought = bought[~bought["order_id"].isin(set(foreign))]
    shipments = (bought.groupby(["order_id", "date"], as_index=False)["amount"].sum()
                 .assign(level="shipment", rank=0))
    orders = (bought.groupby("order_id", as_index=False).agg(date=("order_date", "first"), amount=("amount", "sum"))
              .assign(level="order", rank=1))
    refunds = df[df["kind"] == "refund"][["order_id", "date", "amount", "item_id"]].assign(level="refund", rank=0)
    groups = pd.concat([shipments, orders, refunds], ignore_index=True)
    groups["amount"] = groups["amount"].round(2)
    groups["group"] = (groups["level"] + ":" + groups["order_id"] + ":" + groups["date"].astype(str)
                       + ":" + groups.get("item_id", pd.Series("", index=groups.index)).fillna("").astype(str))
    return groups


def match_amazon(lines, items, cards, contents=None):
    """{txn_id: [(category, amount), ...]} for AMAZON lines matched to order history.

    The split amounts carry the line's sign and sum to the line's amount. contents,
    when a dict, is filled with {txn_id: [(category, item, amount), ...]}: the
    items each matched line paid for (or the one refunded), as the order history
    prices them.
    """
    groups = amazon_groups(items, cards)
    if groups.empty:
        return {}
    amazon = lines[lines["payee_raw"].astype(str).str.upper().str.contains("AMAZON|AMZN")].copy()
    amazon["amt"] = pd.to_numeric(amazon["amount"]).round(2)
    charges = amazon[amazon["amt"] < 0].assign(abs_amt=lambda d: -d["amt"])
    credits = amazon[amazon["amt"] > 0].assign(abs_amt=lambda d: d["amt"])

    def exclusive(r):  # a shipment used rules out its order, and the reverse
        if r["level"] == "refund":
            return {r["group"]}
        shipments = set(groups.loc[(groups["order_id"] == r["order_id"]) & (groups["level"] == "shipment"), "group"])
        orders = set(groups.loc[(groups["order_id"] == r["order_id"]) & (groups["level"] == "order"), "group"])
        return {r["group"]} | (orders if r["level"] == "shipment" else shipments)

    found = (_greedy(charges, groups[groups["level"] != "refund"], "abs_amt", exclusive=exclusive)
             + _greedy(credits, groups[groups["level"] == "refund"], "abs_amt"))
    bought = items[(items["service"] == "amazon") & (items["kind"] == "item")].copy()
    bought["amount"] = pd.to_numeric(bought["amount"])
    out = {}
    for r in found:
        order = bought[bought["order_id"] == r["order_id"]]
        if r["level"] == "shipment":
            order = order[order["date"] == r["date_c"]]
        elif r["level"] == "refund":  # an item that cost exactly the refund is the one returned
            same = order[(order["amount"].round(2) - abs(float(r["amt"]))).abs() < 0.005]
            order = same.head(1) if len(same) else order
        if contents is not None:
            contents[r["txn_id"]] = [(_category(i), " ".join(str(i["item"]).split())[:ITEM_CHARS],
                                      round(float(i["amount"]), 2)) for i in order.to_dict("records")]
        weights = order.assign(category=order.apply(_category, axis=1)).groupby("category")["amount"].sum()
        weights = weights[weights > 0]
        if weights.empty:
            weights = pd.Series({DEFAULT: 1.0})
        amount = float(r["amt"])
        shares = (weights / weights.sum() * amount).round(2)
        shares.iloc[shares.abs().argmax()] += round(amount - shares.sum(), 2)  # cents land on the biggest
        out[r["txn_id"]] = sorted((c, round(float(v), 2)) for c, v in shares.items())
    return out


def match_google(lines, items):
    """{txn_id: payee} for GOOGLE lines named by Google's purchase list: the app and item bought.

    A charge matches a purchase, a credit a refund, by amount and date, one to one.
    """
    if items is None or items.empty:
        return {}
    google = items[items["service"] == "google"].copy()
    if google.empty:
        return {}
    google["amount"] = pd.to_numeric(google["amount"]).round(2)
    google = google.assign(group=google["item_id"], rank=0)
    upper = lines["payee_raw"].astype(str).str.upper()
    named = lines[upper.str.contains("GOOGLE") & ~upper.str.contains("PAYROLL")].copy()
    named["amt"] = pd.to_numeric(named["amount"]).round(2)
    named["abs_amt"] = named["amt"].abs()
    found = (_greedy(named[named["amt"] < 0], google[google["kind"] == "item"], "abs_amt")
             + _greedy(named[named["amt"] > 0], google[google["kind"] == "refund"], "abs_amt"))
    return {r["txn_id"]: str(r["item"]) for r in found}


HINT_WINDOW = (-2, 10)  # days from a shipment to an unmatched charge that might be part of it
HINTS = 3


def amazon_hints(lines, items, matched):
    """{txn_id: "maybe: ..."} for AMAZON charges that matched nothing: the items shipped around then,
    closest in price first, to answer the line by hand (a gift card or a split payment hides the total)."""
    if items is None or items.empty:
        return {}
    bought = items[(items["service"] == "amazon") & (items["kind"] == "item")].copy()
    if bought.empty:
        return {}
    bought["amount"] = pd.to_numeric(bought["amount"])
    bought["day"] = pd.to_datetime(bought["date"], errors="coerce")
    amazon = lines[lines["payee_raw"].astype(str).str.upper().str.contains("AMAZON|AMZN")
                   & ~lines["txn_id"].isin(set(matched))]
    out = {}
    for r in amazon.to_dict("records"):
        day, amount = pd.Timestamp(str(r["date"])[:10]), abs(float(r["amount"]))
        gap = (day - bought["day"]).dt.days
        near = bought[gap.between(*HINT_WINDOW)]
        if near.empty:
            continue
        near = near.assign(off=(near["amount"] - amount).abs()).sort_values("off").head(HINTS)
        out[r["txn_id"]] = "maybe: " + "; ".join(
            f"{str(i)[:40]} ({a:.2f}, {d})" for i, a, d in zip(near["item"], near["amount"], near["date"]))
    return out


def explain(lines, details=None, items=None, cards=()):
    """Everything categorization needs from the explanation downloads."""
    named = {**match_paypal(lines, details), **match_google(lines, items)}
    contents = {}
    split = match_amazon(lines, items, cards, contents)
    return {"payee": named, "split": split, "coverage": coverage(details, items),
            "hints": amazon_hints(lines, items, split),
            "items": {t: json.dumps([list(i) for i in parts]) for t, parts in contents.items()}}
