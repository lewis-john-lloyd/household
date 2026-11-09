"""The ledger's transactions as both stages reason about them: corrections applied, transfer pairs marked,
old category names aliased, and each line's category as categorize decides it.
"""

import pandas as pd

from . import categorize, compute, explain, ingest, transfers
from .ledgerio import read_ledger


def read_books(ledger):
    """The ledger's transactions as they now stand, with what categorization needs.

    effective: corrections applied, transfer pairs marked, old category names
    aliased (the one transactions table both stages reason about).
    """
    txns = read_ledger(ledger, "transactions")
    effective = compute.effective_transactions(txns, read_ledger(ledger, "txn_corrections"))
    accounts = read_ledger(ledger, "accounts")
    parameters = read_ledger(ledger, "parameters")
    paired = transfers.pairs(effective, accounts)
    lonely = transfers.unmatched(effective, accounts, paired)
    household = parameters[parameters["jurisdiction"] == "household"] if len(parameters) else parameters
    payees = {
        str(k)[len(transfers.PAYEE_PREFIX) :]: str(v)
        for k, v in zip(household.get("key", []), household.get("value", []))
        if str(k).startswith(transfers.PAYEE_PREFIX)
    }
    assumed, lonely = transfers.assume_before_history(
        lonely,
        accounts,
        transfers.history_starts(effective),
        categorize.household_parameter(parameters, transfers.MAIN_ACCOUNT),
        payees,
    )
    if len(assumed):
        paired = pd.concat([paired, assumed], ignore_index=True)
    effective = compute.mark_transfers(effective, transfers.paired_ids(paired))
    aliases = compute.latest(read_ledger(ledger, "category_aliases"), ["alias"])
    effective = compute.apply_aliases(effective, aliases)
    return {
        "txns": txns,
        "effective": effective,
        "accounts": accounts,
        "paired": paired,
        "lonely": lonely,
        "rules": read_ledger(ledger, "payee_rules"),
        "decisions": read_ledger(ledger, "proposal_decisions"),
        "categories": compute.latest(read_ledger(ledger, "categories"), ["category"]),
        "parameters": parameters,
        "details": read_ledger(ledger, "payment_details"),
        "items": with_item_categories(read_ledger(ledger, "order_items"), read_ledger(ledger, "item_categories")),
        "aliases": aliases,
    }


def with_item_categories(items, chosen):
    """order_items with a category column: a person's category for an item (the latest row per item in
    item_categories), else blank, which leaves the item to its department (explain._category)."""
    items = items.copy()
    items["category"] = ""
    if chosen is None or chosen.empty or items.empty:
        return items
    latest = chosen.sort_values("recorded_at", kind="stable").groupby("item_id")["category"].last()
    items["category"] = items["item_id"].astype(str).map(latest).fillna("")
    return items


def categorize_books(books):
    guess = categorize.household_parameter(books["parameters"], categorize.UNPAIRED_PARAMETER)
    effective = books["effective"]
    cards = {ingest._last4(c) for c in books["accounts"]["last4"] if str(c).strip()}
    explained = explain.explain(
        effective[effective["era"] != "legacy"],
        books["details"],
        books["items"],
        cards,
        explain.order_services(books["parameters"]),
    )
    books["coverage"] = explained["coverage"]
    out = categorize.categorize(
        effective,
        books["rules"],
        books["decisions"],
        unpaired=books["lonely"]["txn_id"],
        unpaired_guess=guess,
        explanations=explained,
        categories=books["categories"]["category"],
    )
    return categorize.rename(out, books["aliases"])
