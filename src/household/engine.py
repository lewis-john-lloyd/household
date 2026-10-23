"""Engine: the two stages, ingest then compute, called from the engine notebook.

Stores are found by name inside the household folder, so a notebook needs only
the folder ID (and, for migration, the legacy workbook ID).
"""

import time
from datetime import datetime, timezone

import pandas as pd

from . import (backup, compute, inbox, ingest, legacy, projection, schema, sheets, tax,
               withholding)


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


def ingest_inbox(session, folder_id, run_id=None, log=print):
    """Stage 1: back up the ledger, then ingest every file in the inbox (see inbox.py).

    Progress goes to log (print by default; None for silence).
    """
    run_id = run_id or new_run_id()
    _say(log, f"run {run_id}: opening the ledger")
    ledger, _, _ = bootstrap(session, folder_id)
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
    return {"run_id": run_id, "files": files, "counts": counts, "seconds": timing}


def compute_all(session, folder_id, legacy_id=None, legacy_year=None, run_id=None, log=print):
    """Stage 2: read the ledger, rebuild Reports, and optionally check one year against the legacy rollup."""
    run_id = run_id or new_run_id()
    _say(log, f"run {run_id}: reading the ledger")
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger, reports, _ = bootstrap(session, folder_id)

    txns = sheets.read_table(ledger, schema.table(schema.LEDGER, "transactions"))
    corrections = sheets.read_table(ledger, schema.table(schema.LEDGER, "txn_corrections"))
    effective = compute.effective_transactions(txns, corrections)
    actuals = compute.monthly_actuals(effective)
    actuals["run_id"], actuals["computed_at"] = run_id, computed_at
    sheets.replace_table(reports, schema.table(schema.REPORTS, "monthly_actuals"), actuals)
    _say(log, f"monthly_actuals: {len(actuals)} rows from {len(effective)} transactions")

    decisions = sheets.read_table(ledger, schema.table(schema.LEDGER, "proposal_decisions"))
    dismissed = decisions.loc[(decisions["kind"] == "duplicate")
                              & (decisions["decision"] == "reject"), "proposal_key"]
    dupes = compute.possible_duplicates(effective, dismissed=set(dismissed))
    dupes["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "possible_duplicates"), dupes)
    _say(log, f"possible_duplicates: {len(dupes)}")

    result = {"run_id": run_id, "months": actuals["month"].nunique(), "rows": len(actuals),
              "possible_duplicates": len(dupes)}

    facts = sheets.read_table(ledger, schema.table(schema.LEDGER, "return_facts"))
    if not facts.empty:
        check = tax.check_against_return(facts)
        check["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_check"), check)
        result["tax_check_mismatches"] = int((~check["match"]).sum())
        _say(log, f"tax_check: {len(check)} filed lines, {result['tax_check_mismatches']} off by more "
                  "than 1 dollar")

    stubs = sheets.read_table(ledger, schema.table(schema.LEDGER, "paystubs"))
    if not stubs.empty:
        backtest = withholding.backtest(stubs)
        backtest["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "withholding_backtest"), backtest)
        unexplained = int(backtest["explanation"].str.startswith("unexplained").sum())
        result["withholding_unexplained"] = unexplained
        _say(log, f"withholding_backtest: {len(backtest)} stubs, {unexplained} unexplained")

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
                _say(log, f"projection {year}, {row['scenario']}: {verdict} "
                          f"{abs(row['owe_or_refund']):,.0f} (tax {row['total_tax']:,.0f}, "
                          f"paid {row['withheld']:,.0f})")
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
        year_actuals = actuals[actuals["month"].str.startswith(str(legacy_year))]
        check = compute.compare_to_legacy(year_actuals, legacy.parse_rollup_rows(rows))
        check["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "legacy_check"), check)
        result.update({"checked": len(check), "mismatches": int((~check["match"]).sum())})
        _say(log, f"legacy check {legacy_year}: {result['mismatches']} mismatches of {len(check)}")

    log = pd.DataFrame([{"run_id": run_id, "computed_at": computed_at, "stage": "compute",
                         "detail": str(result)}])
    sheets.replace_table(reports, schema.table(schema.REPORTS, "run_log"), log)
    return result
