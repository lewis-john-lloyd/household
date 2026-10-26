"""Engine: the two stages, ingest then compute, called from the engine notebook.

Stores are found by name inside the household folder, so a notebook needs only
the folder ID (and, for migration, the legacy workbook ID).
"""

import time
from datetime import datetime, timezone

import pandas as pd

from . import (backup, compute, inbox, ingest, legacy, pennsylvania, projection, schema, sheets, tax,
               taxsources, transfers, wisconsin, withholding)


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
    accounts = sheets.read_table(ledger, schema.table(schema.LEDGER, "accounts"))
    paired = transfers.pairs(effective, accounts)
    lonely = transfers.unmatched(effective, accounts, paired)
    effective = compute.mark_transfers(effective, transfers.paired_ids(paired))
    for name, df in (("transfers", paired), ("transfers_unmatched", lonely)):
        sheets.replace_table(reports, schema.table(schema.REPORTS, name), df.assign(run_id=run_id))
    _say(log, f"transfers: {len(paired)} pairs between your accounts, {len(lonely)} transfer lines "
              "with no partner")
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
              "possible_duplicates": len(dupes), "transfer_pairs": len(paired),
              "transfers_unmatched": len(lonely)}

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
