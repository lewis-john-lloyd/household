import pandas as pd
import pytest

from household import categorize, compute, schema, sheets
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
    ("FUELCO#6602379EXPRESS", "FUELCO EXPRESS"),  # every word has a digit: keep the letters
    ("7-QUICK 34719", "QUICK"),
    ("12345", ""),
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


@pytest.mark.parametrize("key,answered,same", [
    ("LANTERN CAFE WESTSIDE", "LANTERN CAFE", True),       # starts with the answer
    ("LANTERNS CAFE", "LANTERN S CAFE", True),             # spaces don't count
    ("LANTERN CAFEWESTSIDE", "LANTERN CAFE DOWNTOWN", True),  # cut-off name, same first letters
    ("UNIVERSITY SQUARE", "UNIVERSITY BOOK STORE", False),  # shared letters are one word only
    ("CITY OF ELMWOOD", "CITY OF SPRINGFIELD", False),
    ("ODD", "ODD", True),
    ("ODDITY SHOP", "ODD", False),                          # too short to cover others
])
def test_same_merchant(key, answered, same):
    assert categorize.same_merchant(key, answered) is same


def test_one_row_per_merchant_whatever_the_card_calls_it():
    rows = history() + [txn(1, "2025-08-01", "LANTERN'S CAFE", -9.0, account="a"),
                        txn(2, "2025-08-02", "LANTERNS CAFE 4471 SPRINGFIELD WI", -8.0, account="b"),
                        txn(3, "2025-08-03", "AplPay LANTERNS CAFESPRINGFIELD WI", -7.0, account="c")]
    q = categorize.queue(categorize.categorize(frame(rows)))
    assert len(q) == 1 and q.iloc[0]["lines"] == 3
    assert q.iloc[0]["example"].count(" | ") == 2  # the three spellings, so you can see what was joined


def test_renamed_categories_follow_their_alias():
    out = categorize.categorize(frame(history() + [txn(1, "2025-08-01", "STREAMER", -9.0)]),
                                rules(["STREAMER", "subscriptions", "10", "", "FALSE"]))
    aliases = pd.DataFrame([{"alias": "subscriptions", "category": "media", "note": ""}])
    assert by_id(categorize.rename(out, aliases)).loc["t1", "category"] == "media"


def test_a_rule_can_match_the_banks_own_category():
    out = by_id(categorize.categorize(
        frame(history() + [txn(1, "2025-08-01", "SOME CREDIT UNION", -203.0, note="Some Credit Union | Cash"),
                           txn(2, "2025-08-01", "SOME CREDIT UNION", -50.0, note="Some Credit Union | Groceries")]),
        rules(["\\| Cash$", "cash", "10", "", "FALSE"])))
    assert out.loc["t1", ["category", "decided_by"]].tolist() == ["cash", "rule"]
    assert out.loc["t2", "decided_by"] != "rule"


def test_a_payee_without_letters_is_asked_about_alone():
    out = by_id(categorize.categorize(frame(history() + [txn(1, "2025-08-01", "12345", -9.0),
                                                         txn(2, "2025-08-02", "67890", -9.0)])))
    assert out.loc[["t1", "t2"], "ask"].tolist() == ["line", "line"]


# --- splits ---------------------------------------------------------------------------------------

KNOWN = {"kids", "hobby", "gifts", "family", "media", "savings_529"}


def test_a_split_takes_the_lines_sign_and_the_rest():
    text = "Kids 20; hobby 15.50; gifts $40; family"  # leak-guard: allow (made up)
    parts = categorize.parse_split(text, -87.37, KNOWN)
    assert parts == [("kids", -20.0), ("hobby", -15.5), ("gifts", -40.0), ("family", -11.87)]
    assert categorize.parse_split("kids 5, family 7.25", 12.25) == [("kids", 5.0), ("family", 7.25)]
    assert categorize.parse_split("kids 1,005.00; family", -1010.0) == [("kids", -1005.0), ("family", -5.0)]
    assert categorize.split_text(parts) == "kids 20.00; hobby 15.50; gifts 40.00; family 11.87"


@pytest.mark.parametrize("text, reason", [
    ("kids 20; hobby 15", "the parts come to 35.00, the line is 36.00"),
    ("kids 40; family", "leaving nothing of the line's 36.00 for family"),
    ("kids; family", "only one part can leave its amount out"),
    ("kids 10; kids 26", "kids is named twice"),
    ("davd 36", "not a category: davd"),
    ("20 kids", "can't read"),
    ("kids 0; family", "nothing for kids"),
])
def test_a_split_that_does_not_add_up_says_why(text, reason):
    with pytest.raises(ValueError, match=reason):
        categorize.parse_split(text, -36.0, KNOWN)


def test_a_split_without_the_line_amount_keeps_the_rest_for_later():
    assert categorize.parse_split("kids 20; family") == [("kids", 20.0), ("family", None)]
    assert categorize.split_text([("kids", 20.0), ("family", None)]) == "kids 20.00; family"


def test_a_persons_split_decides_the_line_and_counts_each_part():
    rows = history() + [txn(1, "2025-08-01", "PAGE TURNER BOOKS 7", -87.37), txn(2, "2025-08-02", "QUILL", -9.0)]
    df = frame(rows)
    df["split"] = ""
    df.loc[df["txn_id"] == "t1", "split"] = "kids 20; hobby 15.50; gifts 40; family"
    df.loc[df["txn_id"] == "t2", "split"] = "kids 5; family 5"
    out = by_id(categorize.categorize(df, categories=KNOWN | {"food", "petrol", "household"}))
    assert out.loc["t1", ["category", "decided_by", "status"]].tolist() == ["gifts", "you, split", "decided"]
    assert out.loc["t1", "splits"] == "kids -20.00; hobby -15.50; gifts -40.00; family -11.87"
    assert out.loc["t2", "status"] == "open"
    assert out.loc["t2", "decided_by"] == "your split: the parts come to 10.00, the line is 9.00"
    parts = categorize.actual_rows(out.reset_index())
    assert parts[parts["txn_id"] == "t1"].set_index("category")["amount"].to_dict() == {
        "kids": -20.0, "hobby": -15.5, "gifts": -40.0, "family": -11.87}


def test_the_later_of_a_category_and_a_split_stands():
    txns = frame([txn(1, "2025-08-01", "PAGE TURNER BOOKS", -30.0, "media")])
    corrections = pd.DataFrame([
        {"txn_id": "t1", "field": "split", "value": "kids 10; family", "recorded_at": "2026-11-02T10:00:00"},
        {"txn_id": "t1", "field": "category", "value": "family", "recorded_at": "2026-11-01T10:00:00"}])
    effective = compute.effective_transactions(txns, corrections)
    assert effective.iloc[0][["category", "split"]].tolist() == ["media", "kids 10; family"]
    corrections.loc[1, "recorded_at"] = "2026-11-03T10:00:00"
    effective = compute.effective_transactions(txns, corrections)
    assert effective.iloc[0][["category", "split"]].tolist() == ["family", ""]


def test_harvest_reads_a_split_answer_for_a_line():
    review = pd.DataFrame([
        {"key": "t1", "answer": "kids 20; hobby 15.50; gifts 40; family", "guess": "media", "total": -87.37,
         "kind": "line", "note": ""},
        {"key": "t2", "answer": "kids 20; family 5", "guess": "media", "total": -30.0, "kind": "line", "note": ""},
        {"key": "QUILL", "answer": "kids 5; family", "guess": "media", "total": -9.0, "kind": "payee", "note": ""},
        {"key": "t3", "answer": "kids 20; family", "guess": "", "total": "", "kind": "line", "note": ""},
        {"key": "t4", "answer": "savings_529", "guess": "", "total": -50.0, "kind": "line", "note": ""},
    ], columns=list(categorize.REVIEW_COLUMNS))
    _, fixed, left = categorize.harvest(review, KNOWN, now="2026-11-02T00:00:00")
    assert fixed[["txn_id", "field", "value"]].values.tolist() == [
        ["t1", "split", "kids 20.00; hobby 15.50; gifts 40.00; family 11.87"],
        ["t3", "split", "kids 20.00; family"],
        ["t4", "category", "savings_529"]]
    assert left[["key", "note"]].values.tolist() == [
        ["t2", "split: the parts come to 25.00, the line is 30.00"],
        ["QUILL", "a split answers one line: answer a merchant with one category"]]


def test_a_line_typed_into_review_by_hand_stays_until_it_is_fixed():
    rows = history() + [txn(1, "2025-08-01", "QUILL & INK", -12.0)]
    carry = pd.DataFrame([{"key": "t9", "answer": "kids 20; family 5", "kind": "line", "total": "",
                           "note": "split: the parts come to 25.00, the line is 30.00"},
                          {"key": "GONE", "answer": "fod", "kind": "payee", "note": "not a category: fod"}])
    q = categorize.queue(categorize.categorize(frame(rows)), carry=carry)
    assert q["key"].tolist() == ["QUILL & INK", "t9"]
    assert q.iloc[1]["note"] == "split: the parts come to 25.00, the line is 30.00"
