"""The ledger's transactions as both stages reason about them: corrections applied, transfer pairs marked,
old category names aliased, and each line's category as categorize decides it.
"""

from typing import Any

import pandas as pd

from . import __version__, categorize, compute, explain, fingerprints, ingest, mortgage, sheets, transfers
from .kinds import Frame, Spreadsheet
from .ledgerio import LedgerView, read_ledger, shared

# every ledger table read_books and categorize_books read: what a categorization rests on (key)
BOOK_TABLES = (
    "transactions",
    "txn_corrections",
    "accounts",
    "parameters",
    "category_aliases",
    "payee_rules",
    "proposal_decisions",
    "categories",
    "payment_details",
    "order_items",
    "item_categories",
    "loans",
    "loan_events",
)


def key(ledger: "Spreadsheet | LedgerView") -> str | None:
    """A fingerprint of everything the books and their categories rest on: the tables as the ledger holds
    them, how they are read (a LedgerView's reading) and the package's version. None for a ledger opened
    without a Book, which has nothing held to fingerprint."""
    book = ledger.ledger if isinstance(ledger, LedgerView) else ledger
    if not isinstance(book, sheets.Book):
        return None
    reading = ledger.reading if isinstance(ledger, LedgerView) else None
    return fingerprints.of("books", __version__, book.digest(list(BOOK_TABLES)), reading)


def _copies(books: dict[str, Any], categorized: Frame) -> tuple[dict[str, Any], Frame]:
    return {k: v.copy() if isinstance(v, pd.DataFrame) else v for k, v in books.items()}, categorized.copy()


def read_and_categorize(ledger: "Spreadsheet | LedgerView") -> tuple[dict[str, Any], Frame]:
    """(read_books, categorize_books) for this ledger. Inside one run (ledgerio.one_run) the two are worked
    out once for the same ledger read the same way: ingest's review and compute both ask, and the model is
    trained for the first; the second is given copies, unless a table they rest on was written in between."""
    run, rests_on = shared(), key(ledger)
    if run is not None and rests_on and run.get("books", (None,))[0] == rests_on:
        return _copies(*run["books"][1:])
    books = read_books(ledger)
    categorized = categorize_books(books)
    if run is not None and rests_on:
        run["books"] = (rests_on, *_copies(books, categorized))
    return books, categorized


def read_books(ledger: Spreadsheet) -> dict[str, Any]:
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
    waiting = transfers.waiting_for(lonely, effective, accounts, payees)  # their other side isn't downloaded yet
    effective = compute.mark_transfers(effective, transfers.paired_ids(paired))
    aliases = compute.latest(read_ledger(ledger, "category_aliases"), ["alias"])
    effective = compute.apply_aliases(effective, aliases)
    return {
        "txns": txns,
        "effective": effective,
        "accounts": accounts,
        "paired": paired,
        "lonely": lonely,
        "waiting_transfers": waiting,
        "rules": read_ledger(ledger, "payee_rules"),
        "decisions": read_ledger(ledger, "proposal_decisions"),
        "categories": compute.latest(read_ledger(ledger, "categories"), ["category"]),
        "parameters": parameters,
        "details": read_ledger(ledger, "payment_details"),
        "items": with_item_categories(
            explain.settle_rewards(read_ledger(ledger, "order_items")), read_ledger(ledger, "item_categories")
        ),
        "aliases": aliases,
        "loans": read_ledger(ledger, "loans"),
        "loan_events": read_ledger(ledger, "loan_events"),
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
        unpaired_waiting=books.get("waiting_transfers") or {},
        unpaired_guess=guess,
        explanations=explained,
        categories=books["categories"]["category"],
    )
    out = categorize.rename(out, books["aliases"])
    splits = mortgage.split_settings(books["parameters"])
    loans = books.get("loans")
    if splits and loans is not None and not loans.empty:  # each loan payment into its parts (mortgage.py)
        sched, _ = mortgage.schedule(
            loans, books.get("loan_events"), None, str(effective["date"].astype(str).max())[:7]
        )
        out = mortgage.split_payments(out, sched, splits)
    return out
