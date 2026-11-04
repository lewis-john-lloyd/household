"""Engine: the two stages, ingest then compute, called from the engine notebook.

Stores are found by name inside the household folder, so a notebook needs only
the folder ID (and, for migration, the legacy workbook ID).
"""

import json
import time
from datetime import datetime, timezone

import pandas as pd

from . import (backup, budget, categorize, checks, compute, explain, flows, inbox, ingest, legacy, mortgage,
               pennsylvania, projection, recurring, schema, sheets, tax, taxsources, transfers, wisconsin, withholding)


def versions():
    """The libraries this run uses and their versions ("pandas 2.2.2, ..."): the tested range is in
    pyproject.toml, and Colab's image decides where in it a run lands, so each run says."""
    import gspread
    import numpy
    import pypdf
    import sklearn
    out = []
    for name, module in (("pandas", pd), ("numpy", numpy), ("scikit-learn", sklearn), ("gspread", gspread),
                         ("pypdf", pypdf)):
        out.append(f"{name} {getattr(module, '__version__', '?')}")
    return ", ".join(out)


def new_run_id(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def bootstrap(session, folder_id):
    """Open or create the ledger and Reports spreadsheets and their declared tables."""
    ledger, _ = sheets.open_or_create(session.sheets, sheets.LEDGER_TITLE, folder_id)
    reports, _ = sheets.open_or_create(session.sheets, sheets.REPORTS_TITLE, folder_id)
    created = {
        "ledger": sheets.ensure_tables(ledger, schema.LEDGER),
        "reports": sheets.ensure_tables(reports, schema.REPORTS),
    }
    return ledger, reports, created


def ingest_legacy_year(session, folder_id, legacy_id, year, run_id=None):
    """Stage 1 for the migration: back up the ledger, then append one legacy year's transactions."""
    run_id = run_id or new_run_id()
    ledger, _, _ = bootstrap(session, folder_id)
    backup.snapshot_ledger(ledger, session.drive(), folder_id, run_id)
    rows = session.sheets.open_by_key(legacy_id).worksheet(f"{year}-data").get_values(
        value_render_option=sheets.UNFORMATTED)
    incoming = ingest.legacy_to_transactions(legacy.parse_data_rows(rows))
    appended, skipped = ingest.append_transactions(ledger, incoming)
    return {"run_id": run_id, "read": len(incoming), "appended": appended, "skipped": skipped}


def _say(log, message):
    if log:
        log(message)


def _recording(log):
    """(say, lines): say prints through log as before and keeps every line, for the views' status page."""
    lines = []

    def say(message):
        lines.append(str(message))
        if log:
            log(message)
    return say, lines


STAGES = {"ingest", "compute"}


def _record(reports, stage, run_id, at, summary, lines):
    """Keep this run of this stage in Reports (run_log: its summary as JSON; run_notes: every line it
    printed), replacing that stage's last run and leaving the other stages' alone."""
    for name, rows in (
            ("run_log", [{"run_id": run_id, "computed_at": at, "stage": stage,
                          "detail": json.dumps(summary, default=str, sort_keys=True)}]),
            ("run_notes", [{"run_id": run_id, "stage": stage, "at": at, "n": n, "message": m}
                           for n, m in enumerate(lines)])):
        t = schema.table(schema.REPORTS, name)
        kept = sheets.read_table(reports, t)
        kept = kept[kept["stage"].astype(str).isin(STAGES - {stage})] if len(kept) else kept
        sheets.replace_table(reports, t, pd.concat([kept, pd.DataFrame(rows, columns=t.columns)], ignore_index=True))


def ingest_inbox(session, folder_id, run_id=None, log=print):
    """Stage 1: back up the ledger, then ingest every file in the inbox (see inbox.py).

    Progress goes to log (print by default; None for silence).
    """
    run_id = run_id or new_run_id()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log, said = _recording(log)
    _say(log, f"run {run_id}: opening the ledger")
    _say(log, f"versions: {versions()}")
    ledger, reports, _ = bootstrap(session, folder_id)
    drive = session.drive()
    _say(log, "backing up every ledger tab")
    started = time.perf_counter()
    manifest = backup.snapshot_ledger(ledger, drive, folder_id, run_id)
    timing = {"backup": round(time.perf_counter() - started, 2)}
    _say(log, f"backup: {len(manifest['tables'])} tabs to backups/ledger_{run_id}.zip "
              f"({timing['backup']:.1f}s)")
    files = inbox.process(ledger, drive, folder_id, timing=timing, log=log)
    counts = {}
    for f in files:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    started = time.perf_counter()
    answered = _review(ledger, log)
    timing["review"] = round(time.perf_counter() - started, 2)
    for f in files:  # a parser's notes, kept with the run (the notebook prints them itself)
        said += [f"note: {f['file']} - {note}" for note in f.get("notes") or []]
    result = {"run_id": run_id, "files": files, "counts": counts, "review": answered, "seconds": timing}
    _record(reports, "ingest", run_id, started_at, {k: v for k, v in result.items() if k != "files"}
            | {"files": [{k: f.get(k) for k in ("file", "status", "reason", "appended")} for f in files]}, said)
    return result


def _read(ledger, name):
    return sheets.read_table(ledger, schema.table(schema.LEDGER, name))


def _books(ledger):
    """The ledger's transactions as they now stand, with what categorization needs.

    effective: corrections applied, transfer pairs marked, old category names
    aliased (the one transactions table both stages reason about).
    """
    txns = _read(ledger, "transactions")
    effective = compute.effective_transactions(txns, _read(ledger, "txn_corrections"))
    accounts = _read(ledger, "accounts")
    parameters = _read(ledger, "parameters")
    paired = transfers.pairs(effective, accounts)
    lonely = transfers.unmatched(effective, accounts, paired)
    household = parameters[parameters["jurisdiction"] == "household"] if len(parameters) else parameters
    payees = {str(k)[len(transfers.PAYEE_PREFIX):]: str(v) for k, v in zip(household.get("key", []), household.get("value", []))
              if str(k).startswith(transfers.PAYEE_PREFIX)}
    assumed, lonely = transfers.assume_before_history(
        lonely, accounts, transfers.history_starts(effective),
        categorize.household_parameter(parameters, transfers.MAIN_ACCOUNT), payees)
    if len(assumed):
        paired = pd.concat([paired, assumed], ignore_index=True)
    effective = compute.mark_transfers(effective, transfers.paired_ids(paired))
    aliases = _read(ledger, "category_aliases")
    effective = compute.apply_aliases(effective, aliases)
    return {"txns": txns, "effective": effective, "accounts": accounts, "paired": paired, "lonely": lonely,
            "rules": _read(ledger, "payee_rules"), "decisions": _read(ledger, "proposal_decisions"),
            "categories": _read(ledger, "categories"), "parameters": parameters,
            "details": _read(ledger, "payment_details"), "items": _read(ledger, "order_items"),
            "aliases": aliases}


def _categorized(books):
    guess = categorize.household_parameter(books["parameters"], categorize.UNPAIRED_PARAMETER)
    effective = books["effective"]
    cards = {ingest._last4(c) for c in books["accounts"]["last4"] if str(c).strip()}
    explained = explain.explain(effective[effective["era"] != "legacy"], books["details"], books["items"], cards,
                                explain.order_services(books["parameters"]))
    books["coverage"] = explained["coverage"]
    out = categorize.categorize(effective, books["rules"], books["decisions"],
                                unpaired=books["lonely"]["txn_id"], unpaired_guess=guess,
                                explanations=explained, categories=books["categories"]["category"])
    return categorize.rename(out, books["aliases"])


def review(session, folder_id, log=print, now=None):
    """Read the answers typed in the review tab into the ledger, then rewrite the tab with what is open.

    Runs at the end of every ingest (ingest_inbox); call it alone to turn answers
    around without touching the inbox. A payee answer is stored as a
    proposal_decisions row, a single line's answer as a txn_corrections row; an
    answer that is not a category stays on its row with the reason.
    """
    ledger, _, _ = bootstrap(session, folder_id)
    return _review(ledger, log, now)


def _review(ledger, log=print, now=None):
    review_table = schema.table(schema.LEDGER, "review")
    typed = _read(ledger, "review")
    categories = _read(ledger, "categories")["category"].astype(str).str.strip()
    decisions, corrections, left = categorize.harvest(typed, set(categories), now=now)
    sheets.append_rows(ledger, schema.table(schema.LEDGER, "proposal_decisions"), decisions)
    sheets.append_rows(ledger, schema.table(schema.LEDGER, "txn_corrections"), corrections)
    if len(decisions) or len(corrections):
        _say(log, f"review: read {len(decisions)} payee answers and {len(corrections)} line answers")
    for row in left.to_dict("records"):
        _say(log, f"review: {row['key']}: {row['note']}")

    books = _books(ledger)
    categorized = _categorized(books)
    rows = categorize.queue(categorized, carry=left)
    answers = ["y"] + sorted(c for c in categories if c and c != "transfer") + ["transfer"]
    sheets.rewrite_review(ledger, review_table, rows, answers=answers)
    open_lines = int(pd.to_numeric(rows["lines"]).sum()) if len(rows) else 0
    _say(log, f"review: {len(rows)} questions open ({open_lines} lines): answer them in the ledger's "
              "review tab, then run ingest again")
    return {"answered": len(decisions) + len(corrections), "unusable": len(left), "questions": len(rows),
            "open_lines": open_lines}


def compute_all(session, folder_id, legacy_id=None, legacy_year=None, run_id=None, log=print):
    """Stage 2: read the ledger, rebuild Reports, and optionally check one year against the legacy rollup."""
    run_id = run_id or new_run_id()
    log, said = _recording(log)
    _say(log, f"run {run_id}: reading the ledger")
    _say(log, f"versions: {versions()}")
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger, reports, _ = bootstrap(session, folder_id)

    books = _books(ledger)
    txns, effective, paired, lonely = books["txns"], books["effective"], books["paired"], books["lonely"]
    for name, df in (("transfers", paired), ("transfers_unmatched", lonely)):
        sheets.replace_table(reports, schema.table(schema.REPORTS, name), df.assign(run_id=run_id))
    assumed = int((paired["kind"] == transfers.ASSUMED).sum())
    answered = set(effective.loc[(effective["category"].astype(str).str.strip() != "")
                                 | (effective["split"].astype(str).str.strip() != ""), "txn_id"])
    waiting = lonely[~lonely["txn_id"].isin(answered)]  # a person's category or split answers one
    _say(log, f"transfers: {len(paired) - assumed} pairs between your accounts, {len(lonely)} transfer lines "
              f"with no partner ({len(waiting)} not yet answered)"
              + (f", {assumed} more from before the other account's downloaded history, "
                 "taken as transfers with it" if assumed else ""))
    legacy_checked, legacy_blank = compute.legacy_rows(txns)
    legacy_actuals = compute.monthly_actuals(legacy_checked)  # as ingested, for the legacy check

    categorized = _categorized(books)
    recent = categorized[categorized["era"] != "legacy"]
    sheets.replace_table(reports, schema.table(schema.REPORTS, "categorized"), recent.assign(run_id=run_id))
    counts = categorize.summary(categorized)
    _say(log, f"categories since {categorize.REVIEW_FROM}: decided {counts['decided']}; "
              f"{counts['open_lines']} lines open in review; waiting {counts['waiting']}")
    actuals = compute.monthly_actuals(categorize.actual_rows(categorized))
    actuals["run_id"], actuals["computed_at"] = run_id, computed_at
    sheets.replace_table(reports, schema.table(schema.REPORTS, "monthly_actuals"), actuals)
    _say(log, f"monthly_actuals: {len(actuals)} rows from {len(effective)} transactions")

    today = computed_at[:10]
    rules = _read(ledger, "budget_rules")
    if not rules.empty:
        planned = budget.targets(rules, actuals, today[:7])
        sheets.replace_table(reports, schema.table(schema.REPORTS, "budget_targets"), planned.assign(run_id=run_id))
        compared = budget.versus(planned, actuals, books["categories"])
        sheets.replace_table(reports, schema.table(schema.REPORTS, "budget_vs_actual"),
                             compared.assign(run_id=run_id))
        funds = budget.sinking_funds(planned, actuals)
        sheets.replace_table(reports, schema.table(schema.REPORTS, "sinking_funds"), funds.assign(run_id=run_id))
        now = compared[compared["month"] == today[:7]]
        over = now[now["remaining"] < 0]
        _say(log, f"budget: {rules['line'].nunique()} lines; this month {now['target'].sum():,.0f} budgeted, "
                  f"{now['actual'].sum():,.0f} spent, {len(over)} categories over")
        warnings = planned[planned["basis"].str.contains("no usable|unknown|no fallback", regex=True)]
        for line in sorted(set(warnings["line"])):
            _say(log, f"  budget line {line}: {warnings.loc[warnings['line'] == line, 'basis'].iloc[-1]}")
    else:
        _say(log, "budget: no budget_rules yet")
    due = checks.coverage(books["accounts"], effective, _read(ledger, "balances"),
                          _read(ledger, "source_documents"), categorized, books["coverage"], today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "coverage"), due.assign(run_id=run_id))
    to_get = due[due["status"].isin(["due", "never downloaded"])]
    _say(log, f"coverage: {len(to_get)} downloads due" + (": " + ", ".join(to_get["source"]) if len(to_get) else ""))
    balanced = checks.balance_check(_read(ledger, "balances"), effective)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "balance_check"), balanced.assign(run_id=run_id))
    off = balanced[balanced["status"].str.startswith("off")]
    _say(log, f"balance_check: {len(balanced)} balances, {len(off)} that disagree with the ledger")
    for row in off.to_dict("records"):
        _say(log, f"  {row['account_id']} {row['as_of']}: {row['status']}")
    unpaired = checks.paychecks(_read(ledger, "paystubs"), categorized)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "paychecks"), unpaired.assign(run_id=run_id))
    forgot = unpaired[unpaired["status"].str.startswith("missing stub")]
    lost = unpaired[unpaired["status"] == "no deposit"]
    _say(log, f"paychecks: {len(forgot)} pay deposits with no stub, {len(lost)} stubs with no deposit")
    for who, g in forgot.groupby("person"):
        _say(log, f"  did you forget {who}'s stubs? {len(g)} missing, the latest paid {g['date'].max()}")
    for row in lost.to_dict("records"):
        _say(log, f"  {row['date']} {row['person']} ({row['employer']}): net pay {row['amount']:,.2f} "
                  "never arrived in an account")
    loans = _read(ledger, "loans")
    if not loans.empty:
        sched, notes = mortgage.schedule(loans, _read(ledger, "loan_events"), categorized, today[:7])
        sheets.replace_table(reports, schema.table(schema.REPORTS, "mortgage"), sched.assign(run_id=run_id))
        for line in notes + mortgage.summary(sched):
            _say(log, line)
    subscriptions = recurring.discover(categorized, today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "recurring"), subscriptions.assign(run_id=run_id))
    _say(log, f"recurring: {len(subscriptions)} recurring charges, "
              f"{int((subscriptions['status'] == 'price changed').sum())} changed price, "
              f"{int((subscriptions['status'] == 'stopped').sum())} stopped")
    money = flows.build(_read(ledger, "paystubs"), categorized, books["categories"])
    sheets.replace_table(reports, schema.table(schema.REPORTS, "flows"), money.assign(run_id=run_id))
    _say(log, f"flows: {money['period'].nunique()} months and years for the Sankey view")

    decisions = books["decisions"]
    dismissed = decisions.loc[(decisions["kind"] == "duplicate")
                              & (decisions["decision"] == "reject"), "proposal_key"]
    dupes = compute.possible_duplicates(effective, dismissed=set(dismissed))
    dupes["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "possible_duplicates"), dupes)
    _say(log, f"possible_duplicates: {len(dupes)}")

    result = {"run_id": run_id, "months": actuals["month"].nunique(), "rows": len(actuals),
              "review_open_lines": counts["open_lines"], "downloads_due": len(to_get),
              "balances_off": len(off), "stubs_missing": len(forgot), "pay_not_deposited": len(lost),
              "possible_duplicates": len(dupes), "transfer_pairs": len(paired),
              "transfers_unmatched": len(waiting), "transfers_unmatched_answered": len(lonely) - len(waiting)}

    facts = sheets.read_table(ledger, schema.table(schema.LEDGER, "return_facts"))
    if not facts.empty:
        check = tax.check_against_return(facts)
        check["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_check"), check)
        result["tax_check_mismatches"] = int((~check["match"]).sum())
        _say(log, f"tax_check: {len(check)} filed lines, {result['tax_check_mismatches']} off by more "
                  "than 1 dollar")

    documents = sheets.read_table(ledger, schema.table(schema.LEDGER, "source_documents"))
    sources = taxsources.report(documents)
    sources["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_sources"), sources)
    unfiled = int((sources["record"] == "not in record store").sum())
    result["tax_sources_unfiled"] = unfiled
    _say(log, f"tax_sources: {len(sources)} cited values, {unfiled} whose source PDF is not in the "
              "record store")

    stubs = sheets.read_table(ledger, schema.table(schema.LEDGER, "paystubs"))
    if not stubs.empty:
        backtest = withholding.backtest(stubs, withholding.explained_by_people(decisions, "federal"))
        backtest["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "withholding_backtest"), backtest)
        unexplained = int(backtest["explanation"].str.startswith("unexplained").sum())
        by_you = int(backtest["explanation"].str.startswith("explained by you").sum())
        result["withholding_unexplained"] = unexplained
        _say(log, f"withholding_backtest: {len(backtest)} stubs, {unexplained} unexplained, "
                  f"{by_you} explained by you")

        wi = wisconsin.backtest(stubs, withholding.explained_by_people(decisions, "wi"))
        wi["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "wi_withholding_backtest"), wi)
        wi_unexplained = wi[wi["explanation"].str.startswith("unexplained")]
        result["wi_withholding_unexplained"] = len(wi_unexplained)
        wi_by_you = int(wi["explanation"].str.startswith("explained by you").sum())
        _say(log, f"wi_withholding_backtest: {len(wi)} stubs, {len(wi_unexplained)} unexplained, "
                  f"{wi_by_you} explained by you")
        for row in wi_unexplained.to_dict("records"):
            _say(log, f"  {row['pay_date']} {row['person']}: {row['explanation']}")

        pa = pennsylvania.backtest(stubs)
        pa["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "pa_withholding_backtest"), pa)
        pa_unexplained = int(pa["explanation"].str.startswith("unexplained").sum())
        result["pa_withholding_unexplained"] = pa_unexplained
        _say(log, f"pa_withholding_backtest: {len(pa)} stubs, {pa_unexplained} unexplained")

        year = int(stubs["pay_date"].astype(str).max()[:4])
        sales = sheets.read_table(ledger, schema.table(schema.LEDGER, "equity_sales"))
        if year in tax.taxdata.PARAMS:
            lines, notes = projection.project(year, stubs, sales, facts)
            lines["year"], lines["run_id"] = year, run_id
            sheets.replace_table(reports, schema.table(schema.REPORTS, "projection"), lines)
            for note in notes:
                _say(log, f"projection {year}: {note}")
            for row in lines.to_dict("records"):
                verdict = "refund" if row["owe_or_refund"] < 0 else "owe"
                even = row["extra_to_break_even"]
                _say(log, f"projection {year}, {row['scenario']}: {verdict} "
                          f"{abs(row['owe_or_refund']):,.0f} (tax {row['total_tax']:,.0f}, "
                          f"paid {row['withheld']:,.0f})"
                          + ("" if even is None or pd.isna(even) else
                             f"; extra withholding to break even {even:,.0f} a paycheck"))
            wi_row = lines.iloc[0]
            wi_verdict = "refund" if wi_row["wi_owe_or_refund"] < 0 else "owe"
            _say(log, f"projection {year}, Wisconsin: {wi_verdict} "
                      f"{abs(wi_row['wi_owe_or_refund']):,.0f} (tax {wi_row['wi_tax']:,.0f}, "
                      f"paid {wi_row['wi_withheld']:,.0f})")
            result["wi_projection"] = int(wi_row["wi_owe_or_refund"])
            plans = projection.elections(year, stubs)
            plans["run_id"] = run_id
            sheets.replace_table(reports, schema.table(schema.REPORTS, "elections"), plans)
            for row in plans[plans["status"] != "at limit"].to_dict("records"):
                _say(log, f"elections {year}: {row['person']} {row['plan']}: {row['status']} "
                          f"({row['gap']:+,.2f})")
            result["projection"] = dict(zip(lines["scenario"], lines["owe_or_refund"]))
    if legacy_id and legacy_year:
        rows = session.sheets.open_by_key(legacy_id).worksheet(str(legacy_year)).get_values(
            value_render_option=sheets.UNFORMATTED)
        year_actuals = legacy_actuals[legacy_actuals["month"].str.startswith(str(legacy_year))]
        check = compute.compare_to_legacy(year_actuals, legacy.parse_rollup_rows(rows))
        check["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "legacy_check"), check)
        result.update({"checked": len(check), "mismatches": int((~check["match"]).sum())})
        _say(log, f"legacy check {legacy_year}: {result['mismatches']} mismatches of {len(check)}")
        blank = legacy_blank[legacy_blank["date"].astype(str).str.startswith(str(legacy_year))]
        result["legacy_uncategorized"] = len(blank)
        for row in blank.to_dict("records"):
            _say(log, f"  not in the old rollup (no category): {row['date']} {row['payee_raw']} "
                      f"{-float(row['amount']):.2f}")

    _record(reports, "compute", run_id, computed_at, result | {"versions": versions()}, said)
    return result
