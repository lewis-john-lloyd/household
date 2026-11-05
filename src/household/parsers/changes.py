"""Change sets: the edits a person made in the views, saved as one JSON file the inbox reads.

The views never write the ledger. Save writes a change set into the views' own Drive folder (moved into
the inbox once, like the receipt exporter's), and ingest turns each edit into one ledger row, the same
row a person would otherwise have typed:

  payee            a review answer for a merchant      -> proposal_decisions (kind category)
  line             one line's category, split, note,   -> txn_corrections
                   one_off or void
  budget_line      a budget line's new state, a        -> budget_rules
                   scheduled amount, or its end
  payee_rule       a payee rule's new state; no        -> payee_rules
                   category (and no ask) stops it
  category         a category's group and priority     -> categories
  alias            an old name and what it counts as   -> category_aliases
  parameter        a household setting                 -> parameters (jurisdiction household)
  explanation      why a stub's withholding is off     -> proposal_decisions (kind withholding)
  not_duplicate    two lines that are both real        -> proposal_decisions (kind duplicate)

A change set fails closed: parse checks its shape and every value it can check alone (strict JSON,
no unknown fields, sizes, enums, dates, regexes), and check then holds each edit against the ledger
(categories that exist, lines and stubs that exist, splits that add up to the line). One bad edit
refuses the whole file, which stays in the inbox with every reason, so nothing is half applied.

An edit that replaces a setting (a budget line, a payee rule, a category, an alias, a parameter) says
in `was` what it replaced: every field of the row in force that the edit can change (WAS_FIELDS), as
it was when the person edited it, or null for a new one; a `was` missing any of them is refused. If the ledger no longer agrees (another change set or a seed got there first), the file is
refused rather than written over what the person never saw.

Rows are recorded at the change set's created_at, so a later edit supersedes an earlier one by the
ledger's usual rule, and a change set read twice adds nothing.
"""

import json
import re
from datetime import date, datetime, timezone

import pandas as pd

from .common import ParseError, Result
from .. import categorize, compute, schema

NAME = "changes"
FORMAT = "household_changes"
VERSION = 1
MAX_BYTES = 1024 * 1024
MAX_EDITS = 1000
MAX_TEXT = 500      # any one value
MAX_PATTERN = 200   # a payee pattern or a parameter used as one
MAX_ERRORS = 25     # reasons listed when a change set is refused
SOURCE = "web"

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")  # a category, the shape a split can name
PARAMETER_RE = re.compile(r"^[a-z][a-z0-9_]*(:[a-z0-9_]+)?$")
PATTERN_PARAMETERS = ("order_payee:", "transfer_payee:")  # parameters whose value is a payee pattern
TOP = {"format", "version", "created_at", "base_run", "edits"}
BOOL = ("TRUE", "FALSE")

# kind -> (required fields, optional fields); every field is a string, `was` an object or null
KINDS = {
    "payee": ({"key", "category"}, set()),
    "line": ({"txn_id", "field", "value"}, set()),
    "budget_line": ({"line", "effective", "rule"},
                    {"category", "account", "priority", "amount", "per", "note", "was"}),
    "payee_rule": ({"pattern", "category", "effective"}, {"precedence", "ask", "was"}),
    "category": ({"category"}, {"group", "priority", "was"}),
    "alias": ({"alias", "category"}, {"note", "was"}),
    "parameter": ({"key", "value"}, {"was"}),
    "explanation": ({"jurisdiction", "stub_id", "note"}, set()),
    "not_duplicate": ({"txn_a", "txn_b"}, set()),
}
LINE_FIELDS = ("category", "split", "note", "one_off", "void")
JURISDICTIONS = ("federal", "wi")
REPLACES = {"budget_line", "payee_rule", "category", "alias", "parameter"}  # edits that carry `was`
# every field such an edit can change: `was` gives them all, so a change made since to any of them is seen
WAS_FIELDS = {
    "budget_line": ("effective", "category", "account", "priority", "rule", "amount", "per", "note"),
    "payee_rule": ("category", "precedence", "effective", "ask"),
    "category": ("group", "priority"),
    "alias": ("category", "note"),
    "parameter": ("value",),
}

BUDGET = schema.table(schema.LEDGER, "budget_rules")
RULES = schema.table(schema.LEDGER, "payee_rules")
AMOUNT_RULES = ("fixed", "sinking_fund", "scheduled")  # rules that need an amount (the others fall back to one)


def sniff(text):
    head = text.lstrip("﻿").lstrip()[:200]
    return head.startswith("{") and re.search(r'"format"\s*:\s*"' + FORMAT + '"', head) is not None


def _unique_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"field {k!r} given twice")
        seen[k] = v
    return seen


def _date(text):
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def _number(text):
    try:
        float(text.replace(",", ""))
        return True
    except ValueError:
        return False


def _instant(text):
    """created_at as UTC to the second ("2026-11-04T19:41:00+00:00"), or None."""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        return None
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _label(n, edit):
    kind = edit.get("kind", "?") if isinstance(edit, dict) else "?"
    name = ""
    if isinstance(edit, dict):
        name = next((str(edit[f]) for f in ("line", "pattern", "alias", "key", "txn_id", "stub_id", "txn_a",
                                            "category") if isinstance(edit.get(f), str)), "")
    return f"edit {n} ({kind}{' ' + name if name else ''})"


def _shape(edit):
    """What is wrong with one edit's shape and values, judged alone ([] when nothing)."""
    if not isinstance(edit, dict):
        return ["not an object"]
    kind = edit.get("kind")
    if kind not in KINDS:
        return [f"unknown kind {kind!r}"]
    required, optional = KINDS[kind]
    fields = set(edit) - {"kind"}
    problems = [f"missing {f}" for f in sorted(required - fields)]
    problems += [f"unknown field {f}" for f in sorted(fields - required - optional)]
    for f in sorted(fields & (required | optional)):
        value = edit[f]
        if f == "was":
            if value is not None and not (isinstance(value, dict)
                                          and all(isinstance(v, str) for v in value.values())):
                problems.append("was must be null or an object of strings")
            continue
        if not isinstance(value, str):
            problems.append(f"{f} must be a string")
        elif len(value) > MAX_TEXT:
            problems.append(f"{f} is longer than {MAX_TEXT} characters")
        elif value != value.strip():
            problems.append(f"{f} has spaces at its ends")
    if problems:
        return problems
    return problems + _values(kind, edit)


def _pattern(text, what):
    if not text:
        return [f"{what} is empty"]
    if len(text) > MAX_PATTERN:
        return [f"{what} is longer than {MAX_PATTERN} characters"]
    try:
        re.compile(text)
    except re.error as e:
        return [f"{what} {text!r}: {e}"]
    return []


def _values(kind, e):
    """Per-kind checks of values that need no ledger."""
    out = []
    if kind == "payee" and not e["key"]:
        out.append("key is empty")
    if kind == "line":
        if e["field"] not in LINE_FIELDS:
            out.append(f"field must be one of {', '.join(LINE_FIELDS)}")
        elif e["field"] in ("one_off", "void") and e["value"] not in BOOL:
            out.append(f"{e['field']} must be TRUE or FALSE")
        elif e["field"] == "split" and e["value"]:
            try:
                categorize.parse_split(e["value"])
            except ValueError as err:
                out.append(f"split: {err}")
    if kind == "budget_line":
        if not e["line"]:
            out.append("line is empty")
        if not _date(e["effective"]):
            out.append(f"effective {e['effective']!r} is not a date (YYYY-MM-DD)")
        for f in ("rule", "priority", "per"):
            allowed = BUDGET.enums[f]
            if e.get(f, "") and e[f] not in allowed:
                out.append(f"{f} {e[f]!r} is not one of {', '.join(allowed)}")
        if e["rule"] != "end" and not e.get("category", ""):
            out.append("a budget line needs a category")
        amount = e.get("amount", "")
        if amount and not _number(amount):
            out.append(f"amount {amount!r} is not a number")
        if e["rule"] in AMOUNT_RULES and not amount:
            out.append(f"a {e['rule']} line needs an amount")
        if e["rule"] == "scheduled" and e.get("was") is not None:
            out.append("a scheduled amount adds to its month and replaces nothing: was must be null")
    if kind == "payee_rule":
        out += _pattern(e["pattern"], "pattern")
        if not _date(e["effective"]):
            out.append(f"effective {e['effective']!r} is not a date (YYYY-MM-DD)")
        if e.get("precedence", "") and not _number(e["precedence"]):
            out.append(f"precedence {e['precedence']!r} is not a number")
        if e.get("ask", "") not in BOOL + ("",):
            out.append("ask must be TRUE, FALSE or empty")
    if kind == "category":
        if not NAME_RE.match(e["category"]):
            out.append(f"category {e['category']!r}: use lower case letters, digits and _")
        if e.get("priority", "") not in schema.table(schema.LEDGER, "categories").enums["priority"] + ("",):
            out.append(f"priority {e['priority']!r} is not high, medium or low")
    if kind == "alias" and not e["alias"]:
        out.append("alias is empty")
    if kind == "parameter":
        if not PARAMETER_RE.match(e["key"]):
            out.append(f"key {e['key']!r}: use lower case letters, digits, _ and one ':'")
        elif e["key"].startswith(PATTERN_PARAMETERS) and e["value"]:
            out += _pattern(e["value"], "value")
    if kind == "explanation":
        if e["jurisdiction"] not in JURISDICTIONS:
            out.append(f"jurisdiction must be one of {', '.join(JURISDICTIONS)}")
        if not e["note"]:
            out.append("an explanation needs a note")
    if kind == "not_duplicate" and e["txn_a"] == e["txn_b"]:
        out.append("a line is not a duplicate of itself")
    if kind in WAS_FIELDS and isinstance(e.get("was"), dict):
        missing = [f for f in WAS_FIELDS[kind] if f not in e["was"]]
        extra = sorted(set(e["was"]) - set(WAS_FIELDS[kind]))
        if missing:
            out.append(f"was must give every field the edit replaces; missing {', '.join(missing)}")
        if extra:
            out.append(f"was has fields this edit does not replace: {', '.join(extra)}")
    return out


def _target(e):
    """The setting an edit replaces, so one change set cannot edit it twice."""
    kind = e["kind"]
    if kind == "budget_line":
        return None if e["rule"] == "scheduled" else ("budget_line", e["line"])
    if kind == "line":
        field = "category" if e["field"] == "split" else e["field"]  # a category and a split replace each other
        return ("line", e["txn_id"], field)
    if kind == "payee":
        return ("payee", categorize.payee_key(e["key"]) or e["key"].upper())
    key = {"payee_rule": "pattern", "category": "category", "alias": "alias", "parameter": "key",
           "explanation": "stub_id", "not_duplicate": "txn_a"}[kind]
    extra = (e["jurisdiction"],) if kind == "explanation" else (e["txn_b"],) if kind == "not_duplicate" else ()
    return (kind, e[key]) + extra


def parse(text):
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ParseError(f"{NAME}: the change set is over {MAX_BYTES} bytes")
    try:
        doc = json.loads(text.lstrip("﻿"), object_pairs_hook=_unique_keys)
    except ValueError as e:
        raise ParseError(f"{NAME}: not valid JSON: {e}") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ParseError(f"{NAME}: not a change set (format must be {FORMAT!r})")
    problems = [f"unknown field {f}" for f in sorted(set(doc) - TOP)]
    problems += [f"missing {f}" for f in sorted(TOP - set(doc))]
    if doc.get("version") != VERSION:
        problems.append(f"version {doc.get('version')!r}: this engine reads version {VERSION}")
    at = _instant(doc["created_at"]) if isinstance(doc.get("created_at"), str) else None
    if at is None:
        problems.append("created_at must be a date and time with its zone (2026-01-31T12:00:00Z)")
    if not isinstance(doc.get("base_run", ""), str):
        problems.append("base_run must be a string")
    edits = doc.get("edits")
    if not isinstance(edits, list) or not edits:
        problems.append("edits must be a list with at least one edit")
        edits = []
    elif len(edits) > MAX_EDITS:
        problems.append(f"{len(edits)} edits, more than {MAX_EDITS}")
        edits = []
    seen = {}
    for n, edit in enumerate(edits, start=1):
        wrong = _shape(edit)
        problems += [f"{_label(n, edit)}: {w}" for w in wrong]
        if wrong:
            continue
        target = _target(edit)
        if target is not None and target in seen:
            problems.append(f"{_label(n, edit)}: edits the same thing as edit {seen[target]}")
        elif target is not None:
            seen[target] = n
    _refuse(problems)

    tables, notes = _rows(edits, at)
    keys = {name: list(schema.table(schema.LEDGER, name).columns) for name in tables}  # an exact repeat adds nothing
    stamp = at[:19].replace(":", "")
    notes.insert(0, f"change set from {at}, {len(edits)} edits, based on run {doc.get('base_run') or '(none)'}")
    return Result(kind="changes", parser=NAME, label=f"changes_{stamp}", covers_from=at[:10], covers_to=at[:10],
                  tables=tables, keys=keys, notes=notes, check=lambda read: check(edits, read))


def _refuse(problems):
    if problems:
        more = len(problems) - MAX_ERRORS
        raise ParseError(f"{NAME}: refused, nothing written: " + "; ".join(problems[:MAX_ERRORS])
                         + (f"; and {more} more" if more > 0 else ""))


def _describe(e):
    kind = e["kind"]
    if kind == "payee":
        return f"payee {e['key']}: {e['category']}"
    if kind == "line":
        return f"line {e['txn_id']}: {e['field']} {e['value'] or '(cleared)'}"
    if kind == "budget_line":
        if e["rule"] == "end":
            return f"budget line {e['line']}: ends {e['effective']}"
        if e["rule"] == "scheduled":
            return f"budget line {e['line']}: {e.get('amount')} scheduled for {e['effective']} ({e.get('category')})"
        amount = f" {e.get('amount')} {e.get('per') or 'monthly'}" if e.get("amount") else ""
        return f"budget line {e['line']}: {e['rule']}{amount} for {e.get('category')} from {e['effective']}"
    if kind == "payee_rule":
        asks = e.get("ask") == "TRUE"
        what = (f"-> {e['category']}" if e["category"] else "") + (" (asks each line)" if asks else "")
        what = what.strip() or "stopped"
        return f"payee rule {e['pattern']!r} {what} from {e['effective']}"
    if kind == "category":
        return f"category {e['category']}: group {e.get('group', '') or '(none)'}, priority {e.get('priority', '') or '(none)'}"
    if kind == "alias":
        return f"alias {e['alias']} counts as {e['category']}"
    if kind == "parameter":
        return f"parameter {e['key']} = {e['value']!r}"
    if kind == "explanation":
        return f"{e['jurisdiction']} withholding on stub {e['stub_id']} explained"
    return f"{e['txn_a']} and {e['txn_b']} are not duplicates"


def _rows(edits, at):
    """The ledger rows the edits become, and one note per edit."""
    rows, notes = {}, []

    def add(table, row):
        rows.setdefault(table, []).append(row)

    for e in edits:
        kind = e["kind"]
        if kind == "payee":
            add("proposal_decisions", {"proposal_key": f"payee:{categorize.payee_key(e['key']) or e['key'].upper()}",
                                       "kind": categorize.DECISION_KIND, "decided_at": at, "decision": "approve",
                                       "note": e["category"]})
        elif kind == "line":
            add("txn_corrections", {"txn_id": e["txn_id"], "field": e["field"], "value": e["value"],
                                    "recorded_at": at, "reason": SOURCE, "source": "hand"})
        elif kind == "budget_line":
            add("budget_rules", {c: e.get(c, "") for c in BUDGET.columns})
        elif kind == "payee_rule":
            add("payee_rules", {c: e.get(c, "") for c in RULES.columns})
        elif kind == "category":
            add("categories", {"category": e["category"], "group": e.get("group", ""), "priority": e.get("priority", "")})
        elif kind == "alias":
            add("category_aliases", {"alias": e["alias"], "category": e["category"], "note": e.get("note", "")})
        elif kind == "parameter":
            add("parameters", {"year": "", "jurisdiction": "household", "key": e["key"], "value": e["value"],
                               "source": SOURCE})
        elif kind == "explanation":
            add("proposal_decisions", {"proposal_key": f"{e['jurisdiction']}:{e['stub_id']}", "kind": "withholding",
                                       "decided_at": at, "decision": "approve", "note": e["note"]})
        else:
            a, b = sorted((e["txn_a"], e["txn_b"]))  # the order possible_duplicates names a pair in
            add("proposal_decisions", {"proposal_key": f"{a}|{b}", "kind": "duplicate", "decided_at": at,
                                       "decision": "reject", "note": SOURCE})
        notes.append(_describe(e))
    tables = {name: pd.DataFrame(r, columns=list(schema.table(schema.LEDGER, name).columns)).astype(str)
              for name, r in rows.items()}
    return tables, notes


# --- against the ledger -------------------------------------------------------------------------

def _norm(value):
    """A value as text to compare: the Sheet's 600 and the editor's "600.00" are the same amount."""
    text = "" if value is None else str(value).strip()
    if text == "nan":
        return ""
    if text and _number(text):
        return ("%.10f" % float(text.replace(",", ""))).rstrip("0").rstrip(".")
    return text


def _in_force(df, key_columns, values, order=None):
    """The row now in force for a setting (the last in the sheet, after ordering by `order`), or None."""
    if df is None or df.empty:
        return None
    hit = df
    for column, value in zip(key_columns, values):
        hit = hit[hit[column].astype(str).str.strip() == value]
    if order is not None:
        hit = hit.sort_values(order, kind="stable")
    return None if hit.empty else hit.iloc[-1].to_dict()


def _current(e, read):
    """The row an edit that carries `was` replaces, or None when there is none."""
    kind = e["kind"]
    if kind == "budget_line":
        rules = read("budget_rules")
        if not rules.empty:
            rules = rules[rules["rule"].astype(str).str.strip() != "scheduled"].assign(
                month=rules["effective"].astype(str).str[:7])
        return _in_force(rules, ["line"], [e["line"]], order="month")
    if kind == "payee_rule":
        return _in_force(read("payee_rules"), ["pattern"], [e["pattern"]], order="effective")
    if kind == "category":
        return _in_force(read("categories"), ["category"], [e["category"]])
    if kind == "alias":
        return _in_force(read("category_aliases"), ["alias"], [e["alias"]])
    parameters = read("parameters")
    return _in_force(parameters, ["jurisdiction", "key"], ["household", e["key"]])


def _conflict(e, read):
    """Why the ledger no longer matches what the edit says it replaced, or ""."""
    if e["kind"] == "budget_line" and e["rule"] == "scheduled":
        return ""  # adds an amount to its month, replacing nothing
    was = e.get("was")
    current = _current(e, read)
    if was is None:
        if current is not None:
            return "is new in the change set, but the ledger already has it: reload the views and edit again"
        return ""
    if current is None:
        return "replaces a row the ledger does not have: reload the views and edit again"
    changed = [f for f, v in was.items() if f not in current or _norm(current[f]) != _norm(v)]
    if changed:
        now = ", ".join(f"{f} is {_norm(current.get(f, ''))!r}" for f in changed)
        return f"changed in the ledger since you edited it ({now}): reload the views and edit again"
    return ""


def check(edits, read):
    """Hold each edit against the ledger as it stands (rows staged earlier in this run included).

    read(name) returns a ledger table. Raises ParseError listing every edit that does not fit.
    """
    categories = compute.latest(read("categories"), ["category"])
    known = set(categories["category"].astype(str).str.strip()) if len(categories) else set()
    known |= {e["category"] for e in edits if e["kind"] == "category"}  # added by this change set
    txns = read("transactions")
    amounts = dict(zip(txns["txn_id"].astype(str), txns["amount"])) if len(txns) else {}
    stubs = read("paystubs")
    stub_ids = set(stubs["stub_id"].astype(str)) if len(stubs) else set()
    problems = []
    for n, e in enumerate(edits, start=1):
        kind, wrong = e["kind"], []
        category = e.get("category", "")
        if kind == "line":
            if e["txn_id"] not in amounts:
                wrong.append("no such transaction")
            elif e["field"] == "category" and e["value"] and e["value"] not in known:
                wrong.append(f"not a category: {e['value']}")
            elif e["field"] == "split" and e["value"]:
                try:
                    categorize.parse_split(e["value"], amounts[e["txn_id"]], known)
                except ValueError as err:
                    wrong.append(f"split: {err}")
        elif kind in ("payee", "alias") and category not in known:
            wrong.append(f"not a category: {category}")
        elif kind in ("budget_line", "payee_rule") and category and category not in known:
            wrong.append(f"not a category: {category}")
        elif kind == "explanation" and e["stub_id"] not in stub_ids:
            wrong.append("no such pay stub")
        elif kind == "not_duplicate":
            wrong += [f"no such transaction {t}" for t in (e["txn_a"], e["txn_b"]) if t not in amounts]
        if kind in REPLACES:
            why = _conflict(e, read)
            if why:
                wrong.append(why)
        problems += [f"{_label(n, e)}: {w}" for w in wrong]
    _refuse(problems)
