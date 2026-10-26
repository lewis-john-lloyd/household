"""Pairing transfers and card payments between the household's own accounts (synthetic)."""

import pandas as pd

from household import compute, transfers

ACCOUNTS = pd.DataFrame([{"account_id": a, "type": t} for a, t in
                         [("chk", "checking"), ("sav", "savings"), ("card", "credit_card")]])


def txns(rows):
    return pd.DataFrame([{"txn_id": f"t{i}", "date": d, "account_id": a, "amount": v, "payee_raw": p,
                          "category": c} for i, (d, a, v, p, c) in enumerate(rows)])


ROWS = [
    ("2001-01-02", "chk", -500.0, "BANK FUNDS TRANSFER DB", ""),
    ("2001-01-02", "sav", 500.0, "BANK FUNDS TRANSFER CR", ""),
    ("2001-02-02", "chk", -500.0, "BANK FUNDS TRANSFER DB", ""),
    ("2001-02-03", "sav", 500.0, "BANK FUNDS TRANSFER CR", ""),
    ("2001-01-10", "chk", -812.34, "EXAMPLE CARD EPAY", ""),
    ("2001-01-12", "card", 812.34, "Payment Thank You-Mobile", ""),
    ("2001-01-15", "card", -812.34, "GROCER", ""),  # same amount, but a purchase: never pairs
    ("2001-01-20", "card", 99.0, "MOBILE PYMT", ""),
    ("2001-01-23", "card", -99.0, "Returned Mobile ACH Payme", ""),
    ("2001-01-25", "chk", -40.0, "VENMO PAYMENT", ""),
    ("2001-01-26", "chk", -300.0, "BANK FUNDS TRANSFER DB", ""),  # partner outside the downloads
    ("2001-03-01", "chk", -700.0, "BANK FUNDS TRANSFER DB", ""),
    ("2001-03-20", "sav", 700.0, "BANK FUNDS TRANSFER CR", ""),  # too far apart to be the same move
]


def test_pairs_month_by_month_and_names_card_payments_and_reversals():
    p = transfers.pairs(txns(ROWS), ACCOUNTS).set_index("txn_a")
    assert p.loc["t0", "txn_b"] == "t1" and p.loc["t2", "txn_b"] == "t3"
    assert p.loc["t4", "txn_b"] == "t5" and p.loc["t4", "kind"] == "card_payment"
    assert p.loc["t8", "txn_b"] == "t7" and p.loc["t8", "kind"] == "reversal"
    assert "t6" not in set(p["txn_b"]) | set(p.index) and "t11" not in p.index


def test_unmatched_lists_own_account_moves_but_not_payments_to_people():
    t = txns(ROWS)
    left = transfers.unmatched(t, ACCOUNTS, transfers.pairs(t, ACCOUNTS))
    assert set(left["txn_id"]) == {"t10", "t11", "t12"}


def test_paired_lines_become_transfers_unless_already_categorized():
    t = txns(ROWS)
    t.loc[t["txn_id"] == "t1", "category"] = "gift"
    ids = transfers.paired_ids(transfers.pairs(t, ACCOUNTS))
    marked = compute.mark_transfers(t, ids).set_index("txn_id")["category"]
    assert marked["t0"] == "transfer" and marked["t1"] == "gift" and marked["t6"] == ""
