"""Change sets from the views: shape, checks against the ledger, conflicts, and the inbox run."""

import json

import pandas as pd
import pytest

from household import budget, categorize, compute, inbox, schema, sheets
from household.parsers import ParseError, changes, identify
from test_inbox import FakeDrive, rows
from test_sheets import FakeSpreadsheet

AT = "2026-01-31T12:00:00Z"


def doc(*edits, **top):
    return json.dumps({"format": changes.FORMAT, "version": 1, "created_at": AT, "base_run": "r1",
                       "edits": list(edits)} | top)


def table(name, *records):
    return pd.DataFrame(list(records), columns=list(schema.table(schema.LEDGER, name).columns)).fillna("")


def ledger_reader(**tables):
    """read(name) over a few made-up tables; the rest are empty."""
    def read(name):
        return tables.get(name, table(name))
    return read


LEDGER = dict(
    categories=table("categories", {"category": "food", "group": "living", "priority": "high"},
                     {"category": "kids", "group": "family", "priority": "high"},
                     {"category": "hobby", "group": "fun", "priority": "low"}),
    transactions=table("transactions", {"txn_id": "t1", "date": "2026-01-02", "account_id": "checking",
                                        "amount": "-40", "payee_raw": "ACME GROCER"},
                       {"txn_id": "t2", "date": "2026-01-03", "account_id": "checking", "amount": "-40",
                        "payee_raw": "ACME GROCER #2"}),
    paystubs=table("paystubs", {"stub_id": "s1", "person": "alex", "employer": "Acme Widgets"}),
    budget_rules=table("budget_rules", {"line": "groceries", "effective": "2025-01-01", "category": "food",
                                        "priority": "high", "rule": "fixed", "amount": 500, "per": "monthly"}),
    payee_rules=table("payee_rules", {"pattern": "ACME GROCER", "category": "food", "precedence": "10",
                                      "effective": "2025-01-01", "ask": "FALSE"}),
    parameters=table("parameters", {"jurisdiction": "household", "key": "transfer_account", "value": "checking"}),
    order_items=table("order_items", {"item_id": "i1", "service": "amazon", "order_id": "o1", "kind": "item",
                                      "item": "Jigsaw Puzzle", "amount": "20", "department": "toys"}),
)


GROCERIES_WAS = {"effective": "2025-01-01", "category": "food", "account": "", "priority": "high", "rule": "fixed",
                 "amount": "500.00", "per": "monthly", "note": ""}
RULE_WAS = {"category": "food", "precedence": "10", "effective": "2025-01-01", "ask": "FALSE"}

EVERY_KIND = [
    {"kind": "payee", "key": "ACME GROCER", "category": "food"},
    {"kind": "line", "txn_id": "t1", "field": "split", "value": "food 30; kids"},
    {"kind": "budget_line", "line": "groceries", "effective": "2026-02-01", "rule": "fixed", "category": "food",
     "priority": "high", "amount": "600", "per": "monthly",
     "was": GROCERIES_WAS},
    {"kind": "budget_line", "line": "camp", "effective": "2026-06-01", "rule": "scheduled", "category": "kids",
     "amount": "90"},
    {"kind": "payee_rule", "pattern": "ACME GROCER", "category": "", "effective": "2026-02-01",
     "was": RULE_WAS},
    {"kind": "category", "category": "gifts", "group": "family", "priority": "medium", "was": None},
    {"kind": "alias", "alias": "presents", "category": "gifts", "was": None},
    {"kind": "parameter", "key": "transfer_account", "value": "savings", "was": {"value": "checking"}},
    {"kind": "explanation", "jurisdiction": "federal", "stub_id": "s1", "note": "extra withholding asked for"},
    {"kind": "not_duplicate", "txn_a": "t2", "txn_b": "t1"},
]


def test_every_kind_becomes_its_ledger_row():
    result = changes.parse(doc(*EVERY_KIND))
    result.check(ledger_reader(**LEDGER))
    t = result.tables
    assert t["proposal_decisions"][["proposal_key", "kind", "decision", "note"]].values.tolist() == [
        ["payee:ACME GROCER", "category", "approve", "food"],
        ["federal:s1", "withholding", "approve", "extra withholding asked for"],
        ["t1|t2", "duplicate", "reject", "web"]]
    assert t["txn_corrections"][["txn_id", "field", "value", "reason"]].values.tolist() == [
        ["t1", "split", "food 30; kids", "web"]]
    assert t["budget_rules"][["line", "rule", "amount"]].values.tolist() == [["groceries", "fixed", "600"],
                                                                              ["camp", "scheduled", "90"]]
    assert t["payee_rules"]["category"].tolist() == [""]
    assert t["categories"].values.tolist() == [["gifts", "family", "medium"]]
    assert t["category_aliases"].values.tolist() == [["presents", "gifts", ""]]
    assert t["parameters"].values.tolist() == [["", "household", "transfer_account", "savings", "web"]]
    assert set(t["proposal_decisions"]["decided_at"]) == {"2026-01-31T12:00:00.000+00:00"}
    assert result.label == "changes_2026-01-31T120000.000"
    assert "payee rule 'ACME GROCER' stopped from 2026-02-01" in result.notes
    assert len(result.notes) == len(EVERY_KIND) + 1


def test_sniff_knows_a_change_set_and_nothing_else():
    assert identify(doc(EVERY_KIND[0])) is changes
    assert not changes.sniff('{"format": "something_else"}')
    assert not changes.sniff("category,group,priority\nfood,living,high\n")


def refused(text, read=None):
    with pytest.raises(ParseError) as e:
        result = changes.parse(text)
        result.check(read or ledger_reader(**LEDGER))
    return str(e.value)


def test_shape_problems_are_all_listed_and_nothing_is_written():
    why = refused(doc({"kind": "budget_line", "line": "camp", "effective": "June", "rule": "scheduled",
                       "category": "kids", "was": {"amount": "1"}},
                      {"kind": "payee_rule", "pattern": "ACME (", "category": "food", "effective": "2026-01-01"},
                      {"kind": "line", "txn_id": "t1", "field": "payee_raw", "value": "x"},
                      {"kind": "category", "category": "Big Spend"},
                      {"kind": "explode"},
                      {"kind": "payee", "key": "ACME", "category": "food", "extra": "1"}))
    for reason in ("edit 1 (budget_line camp): effective 'June' is not a date",
                   "a scheduled line needs an amount", "was must give every field the edit replaces",
                   "edit 2 (payee_rule ACME (): pattern", "field must be one of",
                   "category 'Big Spend': use lower case", "unknown kind 'explode'", "unknown field extra"):
        assert reason in why
    assert why.startswith("changes: refused, nothing written")


@pytest.mark.parametrize("text, reason", [
    ('{"format": "household_changes", "format": "household_changes"}', "given twice"),
    (doc(EVERY_KIND[0], version=2), "this engine reads version 1"),
    (doc(EVERY_KIND[0], created_at="2026-01-31 12:00"), "created_at must be a date and time with its zone"),
    (doc(), "at least one edit"),
    (doc(EVERY_KIND[0], author="alex"), "unknown field author"),
    (doc({"kind": "payee", "key": "ACME", "category": 3}), "category must be a string"),
    (doc({"kind": "payee", "key": "ACME", "category": "food "}), "spaces at its ends"),
    (doc({"kind": "payee", "key": "x" * 501, "category": "food"}), "longer than 500"),
    (doc({"kind": "payee_rule", "pattern": "A" * 201, "category": "food", "effective": "2026-01-01"}),
     "longer than 200"),
    (doc({"kind": "parameter", "key": "order_payee:books", "value": "PAGE TURNER ("}), "value 'PAGE TURNER ('"),
    (doc({"kind": "line", "txn_id": "t1", "field": "void", "value": "yes"}), "void must be TRUE or FALSE"),
])
def test_a_malformed_change_set_is_refused(text, reason):
    assert reason in refused(text)


def test_a_change_set_too_big_or_too_long_is_refused(monkeypatch):
    monkeypatch.setattr(changes, "MAX_EDITS", 2)
    assert "3 edits, more than 2" in refused(doc(*EVERY_KIND[:3]))
    monkeypatch.setattr(changes, "MAX_BYTES", 100)
    assert "over 100 bytes" in refused(doc(EVERY_KIND[0]))


def test_one_thing_edited_twice_in_one_change_set_is_refused():
    why = refused(doc({"kind": "line", "txn_id": "t1", "field": "category", "value": "food"},
                      {"kind": "line", "txn_id": "t1", "field": "split", "value": "food 20; kids"}))
    assert "edit 2 (line t1): edits the same thing as edit 1" in why


def test_a_pair_either_way_round_is_one_target():
    why = refused(doc({"kind": "not_duplicate", "txn_a": "t1", "txn_b": "t2"},
                      {"kind": "not_duplicate", "txn_a": "t2", "txn_b": "t1"}))
    assert "edit 2 (not_duplicate" in why and "edits the same thing as edit 1" in why


def test_a_scheduled_amount_is_one_per_line_and_date():
    def scheduled(effective, amount):
        return {"kind": "budget_line", "line": "camp", "effective": effective, "rule": "scheduled",
                "category": "kids", "amount": amount, "was": None}
    why = refused(doc(scheduled("2026-06-01", "90"), scheduled("2026-06-01", "50")))
    assert "edits the same thing as edit 1" in why
    assert len(changes.parse(doc(scheduled("2026-06-01", "90"), scheduled("2026-07-01", "90"))).tables) >= 1


def test_edits_that_do_not_fit_the_ledger_are_refused_with_every_reason():
    why = refused(doc({"kind": "line", "txn_id": "t9", "field": "category", "value": "food"},
                      {"kind": "line", "txn_id": "t1", "field": "split", "value": "food 30; kids 20"},
                      {"kind": "line", "txn_id": "t2", "field": "category", "value": "petrol"},
                      {"kind": "payee", "key": "BLUE PUMP FUEL", "category": "petrol"},
                      {"kind": "explanation", "jurisdiction": "wi", "stub_id": "s9", "note": "moved"},
                      {"kind": "not_duplicate", "txn_a": "t1", "txn_b": "t9"}))
    for reason in ("edit 1 (line t9): no such transaction", "the parts come to 50.00, the line is 40.00",
                   "edit 3 (line t2): not a category: petrol", "edit 4 (payee BLUE PUMP FUEL): not a category",
                   "edit 5 (explanation s9): no such pay stub", "no such transaction t9"):
        assert reason in why


def test_a_category_added_in_the_change_set_can_be_used_in_it():
    result = changes.parse(doc({"kind": "category", "category": "petrol", "group": "car", "was": None},
                               {"kind": "line", "txn_id": "t2", "field": "category", "value": "petrol"}))
    result.check(ledger_reader(**LEDGER))


def test_an_edit_made_on_an_older_view_of_a_setting_is_refused():
    stale = {"kind": "budget_line", "line": "groceries", "effective": "2026-02-01", "rule": "fixed",
             "category": "food", "amount": "600", "was": GROCERIES_WAS | {"amount": "450"}}
    assert "changed in the ledger since you edited it (amount is '500')" in refused(doc(stale))
    new_but_exists = {"kind": "category", "category": "food", "group": "living", "was": None}
    assert "the ledger already has it" in refused(doc(new_but_exists))
    gone = {"kind": "alias", "alias": "nosh", "category": "food", "was": {"category": "food", "note": ""}}
    assert "replaces a row the ledger does not have" in refused(doc(gone))
    # a scheduled amount on a date with none yet is new, even on a line that exists
    changes.parse(doc({"kind": "budget_line", "line": "groceries", "effective": "2026-12-01", "rule": "scheduled",
                       "category": "food", "amount": "80"})).check(ledger_reader(**LEDGER))


def test_the_setting_in_force_is_the_last_row_for_it():
    later = LEDGER | {"budget_rules": pd.concat([LEDGER["budget_rules"], table(
        "budget_rules", {"line": "groceries", "effective": "2025-01-15", "category": "food", "rule": "fixed",
                         "amount": "550", "per": "monthly"})], ignore_index=True)}
    edit = {"kind": "budget_line", "line": "groceries", "effective": "2026-02-01", "rule": "end",
            "was": GROCERIES_WAS | {"effective": "2025-01-15", "priority": "", "amount": "550"}}
    changes.parse(doc(edit)).check(ledger_reader(**later))
    assert "amount is '550'" in refused(doc(edit | {"was": edit["was"] | {"amount": "500"}}), ledger_reader(**later))


CAMP = {"line": "camp", "effective": "2026-06-01", "category": "kids", "rule": "scheduled", "amount": "90"}
CAMP_WAS = {"effective": "2026-06-01", "category": "kids", "account": "", "priority": "", "rule": "scheduled",
            "amount": "90", "per": "", "note": ""}


def test_a_scheduled_amount_replaces_the_one_on_its_date_and_says_what_it_replaced():
    # the budget keeps the last row on a date (budget.py), so a new amount there replaces the old one
    camp = LEDGER | {"budget_rules": pd.concat([LEDGER["budget_rules"], table("budget_rules", CAMP)], ignore_index=True)}
    edit = {"kind": "budget_line", "line": "camp", "effective": "2026-06-01", "rule": "scheduled", "category": "kids",
            "amount": "100", "was": CAMP_WAS}
    changes.parse(doc(edit)).check(ledger_reader(**camp))
    assert "replacing 90" in changes.parse(doc(edit)).notes[1]
    assert "the ledger already has it" in refused(doc(edit | {"was": None}), ledger_reader(**camp))
    assert "amount is '90'" in refused(doc(edit | {"was": CAMP_WAS | {"amount": "120"}}), ledger_reader(**camp))
    assert "replaces a row the ledger does not have" in refused(doc(edit | {"effective": "2026-07-01"}),
                                                                ledger_reader(**camp))


# --- the ledger's rules for later rows ----------------------------------------------------------------

def test_a_later_budget_row_in_the_same_month_wins_and_a_scheduled_zero_cancels():
    rules = table("budget_rules",
                  {"line": "groceries", "effective": "2026-01-01", "category": "food", "rule": "fixed",
                   "amount": "500", "per": "monthly"},
                  {"line": "groceries", "effective": "2026-01-01", "category": "food", "rule": "fixed",
                   "amount": "600", "per": "monthly"},
                  {"line": "camp", "effective": "2026-01-10", "category": "kids", "rule": "scheduled", "amount": "90"},
                  {"line": "camp", "effective": "2026-01-10", "category": "kids", "rule": "scheduled", "amount": "0"},
                  {"line": "camp", "effective": "2026-01-20", "category": "kids", "rule": "scheduled", "amount": "20"})
    out = budget.targets(rules, pd.DataFrame(columns=["month", "category", "spend"]), "2026-01").set_index("line")
    assert out.at["groceries", "target"] == 600
    assert out.at["camp", "target"] == 20


def test_a_payee_rule_row_with_no_category_stops_it_and_a_later_row_on_one_date_wins():
    rules = categorize._rules(table("payee_rules",
                                    {"pattern": "ACME", "category": "food", "effective": "2025-01-01"},
                                    {"pattern": "ACME", "category": "", "effective": "2026-01-01"},
                                    {"pattern": "QUILL", "category": "hobby", "effective": "2025-01-01"},
                                    {"pattern": "QUILL", "category": "gifts", "effective": "2025-01-01"}))
    assert categorize.rule_for("ACME GROCER", "2025-06-01", rules)["category"] == "food"
    assert categorize.rule_for("ACME GROCER", "2026-02-01", rules) is None
    assert categorize.rule_for("QUILL & INK", "2025-06-01", rules)["category"] == "gifts"


def test_latest_keeps_the_last_row_per_key():
    df = table("categories", {"category": "food", "group": "living"}, {"category": "kids", "group": "family"},
               {"category": "food ", "group": "groceries"})
    assert compute.latest(df, ["category"])[["category", "group"]].values.tolist() == [
        ["kids", "family"], ["food ", "groceries"]]


# --- through the inbox ------------------------------------------------------------------------------

@pytest.fixture
def box(monkeypatch):
    drive, ledger = FakeDrive(), FakeSpreadsheet("ledger")
    sheets.ensure_tables(ledger, schema.LEDGER)
    for name in ("categories", "transactions", "paystubs", "budget_rules", "payee_rules", "parameters"):
        sheets.append_rows(ledger, schema.table(schema.LEDGER, name), LEDGER[name])
    inbox_id = drive.folder("inbox")
    record = drive.folder("record_store")
    edits = drive.folder("web_edits", inbox_id)
    monkeypatch.setattr(inbox, "download", lambda d, fid: d.content[fid])
    return drive, ledger, edits, record


def save(drive, folder, name, text):
    return drive.add(name, folder, "application/json", text.encode())


def test_a_change_set_in_the_views_folder_is_written_and_filed(box):
    drive, ledger, edits, record = box
    save(drive, edits, "changes_2026-01-31T120000Z.json", doc(*EVERY_KIND))
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["status"] == "ingested", summary
    assert summary["appended"]["budget_rules"] == 2
    assert rows(ledger, "parameters")["value"].tolist() == ["checking", "savings"]
    assert drive.names_in(edits) == []
    assert any(n.startswith("changes_2026-01-31T120000.000_") for n in drive.names_in(record))
    assert rows(ledger, "source_documents")["kind"].tolist() == ["changes"]


def test_a_refused_change_set_stays_with_its_reason_and_writes_nothing(box):
    drive, ledger, edits, _ = box
    before = len(rows(ledger, "proposal_decisions"))
    save(drive, edits, "changes_2026-01-31T120000Z.json",
         doc(EVERY_KIND[0], {"kind": "line", "txn_id": "t9", "field": "category", "value": "food"}))
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["status"] == "failed"
    assert "edit 2 (line t9): no such transaction" in summary["reason"]
    assert drive.names_in(edits) == ["changes_2026-01-31T120000Z.json"]
    assert len(rows(ledger, "proposal_decisions")) == before


def test_change_sets_are_read_oldest_first_and_each_sees_the_last(box):
    drive, ledger, edits, _ = box
    first = {"kind": "parameter", "key": "transfer_account", "value": "savings", "was": {"value": "checking"}}
    second = {"kind": "parameter", "key": "transfer_account", "value": "brokerage", "was": {"value": "savings"}}
    save(drive, edits, "changes_2026-02-01T090000Z.json", doc(second, created_at="2026-02-01T09:00:00Z"))
    save(drive, edits, "changes_2026-01-31T120000Z.json", doc(first))
    out = inbox.process(ledger, drive, "root")
    assert [s["status"] for s in out] == ["ingested", "ingested"]
    assert rows(ledger, "parameters")["value"].tolist() == ["checking", "savings", "brokerage"]


def test_two_people_editing_the_same_line_the_second_is_refused(box):
    drive, ledger, edits, _ = box
    mine = {"kind": "budget_line", "line": "groceries", "effective": "2026-02-01", "rule": "fixed",
            "category": "food", "amount": "600", "per": "monthly", "priority": "high", "was": GROCERIES_WAS}
    save(drive, edits, "changes_2026-01-31T120000Z.json", doc(mine))
    save(drive, edits, "changes_2026-01-31T120500Z.json", doc(mine | {"amount": "650"},
                                                             created_at="2026-01-31T12:05:00Z"))
    first, second = inbox.process(ledger, drive, "root")
    assert first["status"] == "ingested"
    assert second["status"] == "failed" and "amount is '600'" in second["reason"]


def test_a_was_must_give_every_field_the_edit_replaces():
    partial = {"kind": "budget_line", "line": "groceries", "effective": "2026-02-01", "rule": "fixed", "category": "food",
               "amount": "600", "was": {"effective": "2025-01-01", "rule": "fixed", "amount": "500", "per": "monthly",
                                        "category": "food"}}
    assert "was must give every field the edit replaces; missing account, priority, note" in refused(doc(partial))
    assert "was has fields this edit does not replace: line" in refused(doc(
        partial | {"was": GROCERIES_WAS | {"line": "groceries"}}))
    alias = {"kind": "alias", "alias": "presents", "category": "gifts", "was": {"category": "hobby"}}
    assert "missing note" in refused(doc(alias))


def test_a_change_to_any_field_since_the_page_loaded_makes_the_edit_stale():
    # the reviewer's case: someone changed only the account (or priority, or note) after the page loaded
    moved = LEDGER | {"budget_rules": pd.concat([LEDGER["budget_rules"], table(
        "budget_rules", {"line": "groceries", "effective": "2025-01-01", "category": "food", "priority": "high",
                         "rule": "fixed", "amount": "500", "per": "monthly", "account": "savings"})], ignore_index=True)}
    edit = {"kind": "budget_line", "line": "groceries", "effective": "2026-02-01", "rule": "fixed", "category": "food",
            "amount": "600", "per": "monthly", "was": GROCERIES_WAS}
    assert "account is 'savings'" in refused(doc(edit), ledger_reader(**moved))


def test_an_item_s_category_becomes_an_item_categories_row_and_is_checked():
    result = changes.parse(doc({"kind": "item", "item_id": "i1", "category": "kids"}))
    result.check(ledger_reader(**LEDGER))
    assert result.tables["item_categories"][["item_id", "category", "source"]].values.tolist() == [["i1", "kids", "web"]]
    why = refused(doc({"kind": "item", "item_id": "i9", "category": "kids"}, {"kind": "item", "item_id": "i1", "category": "petrol"}))
    assert "edit 1 (item i9): no such item" in why and "edit 2 (item i1): not a category: petrol" in why


def test_two_saves_in_one_second_stay_in_order(box):
    # milliseconds keep them apart: the later one's rows supersede the earlier's, and it is read second
    drive, ledger, edits, _ = box
    first = {"kind": "parameter", "key": "transfer_account", "value": "savings", "was": {"value": "checking"}}
    second = {"kind": "parameter", "key": "transfer_account", "value": "brokerage", "was": {"value": "savings"}}
    save(drive, edits, "changes_2026-01-31T120000.250Z_b2.json", doc(second, created_at="2026-01-31T12:00:00.250Z"))
    save(drive, edits, "changes_2026-01-31T120000.125Z_a1.json", doc(first, created_at="2026-01-31T12:00:00.125Z"))
    out = inbox.process(ledger, drive, "root")
    assert [s["status"] for s in out] == ["ingested", "ingested"]
    rows = sheets.read_table(ledger, schema.table(schema.LEDGER, "parameters"))
    assert rows["value"].tolist() == ["checking", "savings", "brokerage"]
    times = rows["source"].tolist()  # parameters carry no time; the decisions table shows the recorded order
    assert times[1:] == ["web", "web"]
    assert changes.parse(doc(first, created_at="2026-01-31T12:00:00.125Z")).label != \
        changes.parse(doc(first, created_at="2026-01-31T12:00:00.250Z")).label


def test_two_people_scheduling_the_same_date_from_one_view_the_second_is_refused(box):
    # both pages showed 90 on June 1; one moves it to 120, the other, saved later, to 100
    drive, ledger, edits, _ = box
    sheets.append_rows(ledger, schema.table(schema.LEDGER, "budget_rules"), table("budget_rules", CAMP))
    mine = {"kind": "budget_line", "line": "camp", "effective": "2026-06-01", "rule": "scheduled", "category": "kids",
            "amount": "120", "was": CAMP_WAS}
    save(drive, edits, "changes_2026-01-31T120000Z.json", doc(mine))
    save(drive, edits, "changes_2026-01-31T120500Z.json", doc(mine | {"amount": "100"},
                                                             created_at="2026-01-31T12:05:00Z"))
    first, second = inbox.process(ledger, drive, "root")
    assert first["status"] == "ingested"
    assert second["status"] == "failed" and "amount is '120'" in second["reason"]
    target = budget.targets(rows(ledger, "budget_rules"), pd.DataFrame(columns=["month", "category", "spend"]),
                            "2026-06")
    assert target.set_index(["month", "line"]).at[("2026-06", "camp"), "target"] == 120


def test_a_change_set_discarded_after_ingest_read_it_is_still_applied_and_logged_as_ingested(box, monkeypatch):
    # The views trash a discarded file, but an ingest that has already downloaded it holds its own copy and
    # writes it. Nothing here can stop that; what the views rely on is that the run says so: the file is
    # listed by name as ingested, and the views tell a discard the run_log lists as applied anyway.
    drive, ledger, edits, _ = box
    fid = save(drive, edits, "changes_2026-01-31T120000.000Z_e5.json", doc(EVERY_KIND[7]))

    def download_then_discard(d, file_id):
        data = d.content[file_id]
        if file_id == fid:
            d.items[file_id]["trashed"] = True  # Discard lands between the download and the ledger write
        return data
    monkeypatch.setattr(inbox, "download", download_then_discard)
    [summary] = inbox.process(ledger, drive, "root")
    assert summary["file"] == "changes_2026-01-31T120000.000Z_e5.json"
    assert summary["status"] == "ingested"
    assert rows(ledger, "parameters")["value"].tolist() == ["checking", "savings"]
