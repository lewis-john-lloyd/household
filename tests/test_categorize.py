import pandas as pd
import pytest

from household import categorize, schema, sheets
from household.parsers import ParseError, seed

COLUMNS = ["txn_id", "date", "account_id", "amount", "payee_raw", "category", "note", "era", "source"]


def txn(i, date, payee, amount=-10.0, category="", era="ledger", account="card", note=""):
    return {"txn_id": f"t{i}", "date": date, "account_id": account, "amount": amount, "payee_raw": payee,
            "category": category, "note": note, "era": era, "source": "ofx"}


def history():
    """Old-books rows the model learns from: three merchants, three categories."""
    rows, i = [], 0
    for payee, category, amount in (("ACME GROCER", "food", -80.0), ("BLUE PUMP FUEL", "petrol", -40.0),
                                    ("PAGE TURNER BOOKS", "household", -25.0)):
        for n in range(60):
            rows.append(txn(f"h{i}", f"2023-{n % 12 + 1:02d}-10", f"{payee} {n}", amount, category, "legacy",
                            "legacy"))
            i += 1
    return rows


def frame(rows):
    return pd.DataFrame(rows, columns=COLUMNS)


def rules(*rows):
    return pd.DataFrame(rows, columns=["pattern", "category", "precedence", "effective", "ask"])


def decisions(*pairs):
    return pd.DataFrame([{"proposal_key": f"payee:{k}", "kind": "category", "decided_at": f"2026-01-0{n + 1}",
                          "decision": "approve", "note": c} for n, (k, c) in enumerate(pairs)],
                        columns=["proposal_key", "kind", "decided_at", "decision", "note"])


def by_id(df):
    return df.set_index("txn_id")


@pytest.mark.parametrize("raw,key", [
    ("APLPAY WILLOW ST CO-OP SPRINGFIELD WI", "WILLOW ST SPRINGFIELD"),
    ("WILLOW ST COOP #2", "WILLOW ST COOP"),
    ("TST* LANTERN CAFE", "LANTERN CAFE"),
    ("SQ *RED KITE BAKERY", "RED KITE BAKERY"),
    ("FUEL STOP 4471", "FUEL STOP"),
    ("City of Springfield", "CITY OF SPRINGFIELD"),
])
def test_payee_key(raw, key):
    assert categorize.payee_key(raw) == key


def test_rules_follow_effective_dates_and_precedence():
    r = categorize._rules(rules(["OLD CARD", "subscriptions", "10", "", "FALSE"],
                                ["OLD CARD", "transfer", "10", "2025-11-01", "FALSE"],
                                ["CARD", "misc", "50", "", "FALSE"]))
    assert categorize.rule_for("OLD CARD ONLINE PMT", "2025-10-31", r)["category"] == "subscriptions"
    assert categorize.rule_for("OLD CARD ONLINE PMT", "2025-11-01", r)["category"] == "transfer"
    assert categorize.rule_for("NEW CARD", "2026-01-01", r)["category"] == "misc"
    assert categorize.rule_for("GROCER", "2026-01-01", r) is None


def test_sources_in_order():
    rows = history() + [
        txn(1, "2025-08-01", "SPRINGFIELD CLINIC MD", -900.0),           # rule
        txn(2, "2025-08-02", "CHECK # 1001", -300.0),                     # rule that asks
        txn(3, "2025-08-03", "WILLOW ST CO-OP SPRINGFIELD WI", -50.0),    # your decision (prefix)
        txn(4, "2025-08-04", "AMAZON MKTPL*AB12", -20.0),                 # waiting
        txn(5, "2025-08-05", "FUNDS TRANSFER DB", -400.0),                # unpaired
        txn(6, "2025-08-06", "ODD NEW PLACE", -15.0),                     # open
        txn(7, "2025-08-07", "LANTERN CAFE", -16.0, category="restaurant"),  # a person set it
    ]
    out = by_id(categorize.categorize(
        frame(rows), rules(["CLINIC MD", "concierge", "10", "", "FALSE"], ["^CHECK\\b", "", "5", "", "TRUE"]),
        decisions(("WILLOW ST", "food")), unpaired={"t5"}, unpaired_guess="partner"))
    assert out.loc["t1", ["category", "decided_by", "status"]].tolist() == ["concierge", "rule", "decided"]
    assert out.loc["t2", ["decided_by", "status", "ask"]].tolist() == ["rule, asks each time", "open", "line"]
    assert out.loc["t3", ["category", "decided_by"]].tolist() == ["food", "your decision"]
    assert out.loc["t4", ["decided_by", "status"]].tolist() == ["waiting for amazon", "waiting"]
    assert out.loc["t4", "category"] != ""  # provisional guess keeps totals whole
    assert out.loc["t5", ["guess", "ask", "status"]].tolist() == ["partner", "line", "open"]
    assert out.loc["t6", ["ask", "status"]].tolist() == ["payee", "open"]
    assert out.loc["t7", ["category", "decided_by"]].tolist() == ["restaurant", "you"]


def test_unpaired_inflow_is_guessed_a_transfer():
    out = by_id(categorize.categorize(frame(history() + [txn(1, "2025-08-01", "FUNDS TRANSFER CR", 400.0)]),
                                      unpaired={"t1"}))
    assert out.loc["t1", "guess"] == "transfer"


def test_longest_answered_key_wins():
    out = by_id(categorize.categorize(
        frame(history() + [txn(1, "2025-09-01", "CITY OF ELMWOOD RAMP", -5.0),
                           txn(2, "2025-09-02", "CITY OF SPRINGFIELD", -60.0)]),
        decisions=decisions(("CITY OF", "sewer"), ("CITY OF ELMWOOD", "parking_tolls"))))
    assert out.loc["t1", "category"] == "parking_tolls"
    assert out.loc["t2", "category"] == "sewer"


def test_a_sure_model_decides_and_an_unsure_one_asks():
    rows = history() + [txn(1, "2025-09-01", "ACME GROCER 7", -82.0), txn(2, "2025-09-08", "ACME GROCER 9", -79.0),
                        txn(3, "2025-09-03", "QUILL & INK", -12.0)]
    out = by_id(categorize.categorize(frame(rows)))
    assert out.loc["t1", ["category", "decided_by", "status"]].tolist() == ["food", "model", "decided"]
    assert out.loc["t3", "status"] == "open"


def test_nothing_before_review_from_is_asked():
    out = by_id(categorize.categorize(frame(history() + [txn(1, "2025-03-01", "QUILL & INK", -12.0)])))
    assert out.loc["t1", "status"] == "decided"
    assert out.loc["t1", "decided_by"].endswith("before review")


def test_bank_category_fills_a_weak_guess():
    out = by_id(categorize.categorize(frame(history() + [
        txn(1, "2025-09-01", "XQZ", -9.0, note="XQZ | Groceries")])))
    assert out.loc["t1", ["guess", "decided_by"]].tolist() == ["food", "bank"]


def test_queue_groups_payees_and_lists_lines():
    rows = history() + [txn(1, "2025-08-01", "WILLOW ST CO-OP", -50.0),
                        txn(2, "2025-08-09", "APLPAY WILLOW ST COOP SPRINGFIELD WI", -30.0, account="other"),
                        txn(3, "2025-08-15", "QUILL & INK", -12.0),
                        txn(4, "2025-08-20", "FUNDS TRANSFER DB", -400.0)]
    q = categorize.queue(categorize.categorize(frame(rows), unpaired={"t4"}, unpaired_guess="partner"))
    assert list(q["kind"]) == ["payee", "payee", "line"]
    willow = q[q["key"] == "WILLOW ST"].iloc[0]
    assert (willow["lines"], willow["total"], willow["accounts"]) == (2, -80.0, "card, other")
    assert (willow["first"], willow["last"]) == ("2025-08-01", "2025-08-09")
    assert q.iloc[-1]["key"] == "t4" and q.iloc[-1]["guess"] == "partner"


def test_harvest_turns_answers_into_ledger_rows():
    review = pd.DataFrame([
        {"key": "WILLOW ST", "answer": "y", "guess": "food", "kind": "payee", "note": ""},
        {"key": "quill", "answer": "Household", "guess": "misc", "kind": "payee", "note": ""},
        {"key": "t4", "answer": "partner", "guess": "partner", "kind": "line", "note": ""},
        {"key": "ODD", "answer": "fod", "guess": "food", "kind": "payee", "note": ""},
        {"key": "NEXT", "answer": "", "guess": "food", "kind": "payee", "note": ""},
    ], columns=list(categorize.REVIEW_COLUMNS))
    made, fixed, left = categorize.harvest(review, {"food", "household", "partner"}, now="2026-11-01T00:00:00")
    assert made[["proposal_key", "note"]].values.tolist() == [["payee:WILLOW ST", "food"],
                                                             ["payee:QUILL", "household"]]
    assert fixed[["txn_id", "field", "value"]].values.tolist() == [["t4", "category", "partner"]]
    assert left[["key", "note"]].values.tolist() == [["ODD", "not a category: fod"]]


def test_answers_settle_the_queue_on_the_next_run():
    rows = history() + [txn(1, "2025-08-01", "WILLOW ST CO-OP", -50.0), txn(2, "2025-08-20", "FUNDS TRANSFER DB", -9.0)]
    first = categorize.queue(categorize.categorize(frame(rows), unpaired={"t2"}, unpaired_guess="partner"))
    first["answer"] = ["food", "y"]
    made, fixed, _ = categorize.harvest(first, {"food", "partner"})
    df = frame(rows)
    df.loc[df["txn_id"] == "t2", "category"] = fixed["value"].iloc[0]  # what effective_transactions does
    out = categorize.categorize(df, decisions=made, unpaired={"t2"}, unpaired_guess="partner")
    assert categorize.queue(out).empty
    assert by_id(out).loc["t1", "decided_by"] == "your decision"


def test_carried_answers_survive_a_rewrite():
    rows = history() + [txn(1, "2025-08-01", "QUILL & INK", -12.0)]
    carry = pd.DataFrame([{"key": "QUILL & INK", "answer": "fod", "note": "not a category: fod"}])
    q = categorize.queue(categorize.categorize(frame(rows)), carry=carry)
    assert q.iloc[0][["answer", "note"]].tolist() == ["fod", "not a category: fod"]


def test_payee_rules_seed_keys_on_pattern_and_date():
    text = ("pattern,category,precedence,effective,ask\n"
            "OLD CARD,subscriptions,10,,FALSE\nOLD CARD,transfer,10,2025-11-01,FALSE\n")
    result = seed.parse(text)
    assert result.keys == {"payee_rules": ["pattern", "effective"]}
    with pytest.raises(ParseError, match="duplicate"):
        seed.parse(text + "OLD CARD,misc,10,2025-11-01,FALSE\n")
    with pytest.raises(ParseError, match="pattern"):
        seed.parse("pattern,category,precedence,effective,ask\n(unclosed,misc,10,,FALSE\n")


def test_only_the_review_tab_is_rewritten():
    with pytest.raises(PermissionError):
        sheets.rewrite_review(object(), schema.table(schema.LEDGER, "transactions"), pd.DataFrame())


def test_household_parameter():
    params = pd.DataFrame([["", "household", "unpaired_transfer_guess", "partner", ""],
                           ["2026", "federal", "unpaired_transfer_guess", "x", ""]],
                          columns=["year", "jurisdiction", "key", "value", "source"])
    assert categorize.household_parameter(params, "unpaired_transfer_guess") == "partner"
    assert categorize.household_parameter(params, "missing", "d") == "d"
