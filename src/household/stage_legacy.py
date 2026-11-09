"""The legacy stages: the old budget, investments, equity and net worth workbooks into the ledger, once each,
safe to run every time.
"""

from . import backup, ingest, legacy, legacy_equity, legacy_investments, schema, sheets
from .ledgerio import bootstrap, new_run_id, read_ledger
from .runlog import say


def ingest_legacy_year(session, folder_id, legacy_id, year, run_id=None):
    """Stage 1 for the migration: back up the ledger, then append one legacy year's transactions."""
    run_id = run_id or new_run_id()
    ledger, _, _ = bootstrap(session, folder_id)
    backup.snapshot_ledger(ledger, session.drive(), folder_id, run_id)
    rows = (
        session.sheets.open_by_key(legacy_id)
        .worksheet(f"{year}-data")
        .get_values(value_render_option=sheets.UNFORMATTED)
    )
    incoming = ingest.legacy_to_transactions(legacy.parse_data_rows(rows))
    appended, skipped = ingest.append_transactions(ledger, incoming)
    return {"run_id": run_id, "read": len(incoming), "appended": appended, "skipped": skipped}


def ingest_legacy_investments(session, folder_id, workbook_id, tabs, funds_tab="Equities", run_id=None, log=print):
    """Add the legacy investments workbook's funds to `securities` and its account tabs' readings to `holdings`
    (legacy_investments.py), backing up the ledger first. Safe to run every time: with nothing new it reads,
    says so, and writes nothing (no backup either).

    tabs maps each account tab's name to the ledger account_id it belongs to ({"tab": "account_id"}), or to an
    account_id and the plan money source the tab holds ({"tab": "account_id:roth"}); several tabs can name one
    account. Only the tabs named are read. A fund the ledger already lists, or a reading it
    already holds (same date, account and fund), is not added again, so running it twice adds nothing.
    """
    run_id = run_id or new_run_id()
    ledger, _, _ = bootstrap(session, folder_id)
    known = set(read_ledger(ledger, "accounts")["account_id"].astype(str).str.strip())
    where = {tab: (target.split(":", 1) + [""])[:2] for tab, target in tabs.items()}
    unknown = sorted({a for a, _ in where.values() if a not in known})
    if unknown:
        raise ValueError(f"no such accounts in the ledger: {', '.join(unknown)} (add them first)")
    workbook = session.sheets.open_by_key(workbook_id)
    names = [funds_tab] + list(tabs)
    got = workbook.values_batch_get([f"'{n}'" for n in names], params={"valueRenderOption": sheets.UNFORMATTED})
    read = {n: vr.get("values", []) for n, vr in zip(names, got.get("valueRanges", []))}
    funds, notes = legacy_investments.securities(read[funds_tab])
    frames = []
    for tab, (account, source) in where.items():
        df, more = legacy_investments.holdings(read[tab], account, source)
        frames.append(df)
        notes += more
    held = legacy_investments.merge(frames)
    have_funds = set(read_ledger(ledger, "securities")["symbol"].astype(str).str.strip())
    funds = funds[~funds["symbol"].isin(have_funds)]
    have = read_ledger(ledger, "holdings")

    def key(df):
        return zip(*(df[c].astype(str) for c in ("as_of", "account_id", "symbol", "source")))

    keys = set(key(have))
    new = held[[k not in keys for k in key(held)]]
    for note in notes:
        say(log, note)
    if funds.empty and new.empty:  # the usual case once it has run: nothing to back up or write
        say(log, f"legacy investments: nothing new ({len(held)} readings already held)")
        return {
            "run_id": run_id,
            "securities": 0,
            "holdings": 0,
            "already_held": len(held),
            "dates": int(held["as_of"].nunique()),
            "notes": notes,
        }
    backup.snapshot_ledger(ledger, session.drive(), folder_id, run_id)
    counts = sheets.append_tables(
        ledger, [(schema.table(schema.LEDGER, "securities"), funds), (schema.table(schema.LEDGER, "holdings"), new)]
    )
    say(
        log,
        f"legacy investments: {counts['securities']} funds and {counts['holdings']} readings added "
        f"({len(held) - len(new)} already held), {held['as_of'].nunique()} dates",
    )
    return {
        "run_id": run_id,
        "securities": counts["securities"],
        "holdings": counts["holdings"],
        "already_held": len(held) - len(new),
        "dates": int(held["as_of"].nunique()),
        "notes": notes,
    }


def ingest_legacy_equity(session, folder_id, workbook_id, grant_columns, grants_tab, sales_tab, run_id=None, log=print):
    """Add the legacy tax workbook's stock-plan vests to `equity_vests` and its sales to `equity_sales`
    (legacy_equity.py), for the dates before the plan's own records begin, backing up the ledger first. Safe to
    run every time: what the ledger already holds is not added again, and with nothing new it writes nothing.

    grant_columns maps each grant column of the grants tab (headed by the year it was awarded) to the plan's
    grant id, as vest_schedule names it ({"<year>": "<grant id>"}).
    """
    run_id = run_id or new_run_id()
    ledger, _, _ = bootstrap(session, folder_id)
    workbook = session.sheets.open_by_key(workbook_id)
    got = workbook.values_batch_get(
        [f"'{grants_tab}'", f"'{sales_tab}'"], params={"valueRenderOption": sheets.UNFORMATTED}
    )
    grants_rows, sales_rows = (vr.get("values", []) for vr in got.get("valueRanges", []))
    vested, notes = legacy_equity.vests(grants_rows, grant_columns)
    sold, more = legacy_equity.sales(sales_rows, vested)
    notes += more
    have_vests, have_sales = read_ledger(ledger, "equity_vests"), read_ledger(ledger, "equity_sales")
    new_vests = legacy_equity.before(vested, have_vests[have_vests["doc_id"] != legacy_equity.LEGACY_DOC], "vest_date")
    new_sales = legacy_equity.before(sold, have_sales[have_sales["doc_id"] != legacy_equity.LEGACY_DOC], "sale_date")

    def fresh(df, have, keys):
        seen = set(zip(*(have[k].astype(str) for k in keys))) if len(have) else set()
        return df[[k not in seen for k in zip(*(df[k].astype(str) for k in keys))]] if len(df) else df

    new_vests = fresh(new_vests, have_vests, ["vest_date", "grant_id"])
    new_sales = fresh(new_sales, have_sales, ["sale_date", "grant_id", "vest_date"])
    for note in notes:
        say(log, note)
    if new_vests.empty and new_sales.empty:
        say(log, f"legacy equity: nothing new ({len(vested)} vests and {len(sold)} sales read)")
        return {"run_id": run_id, "equity_vests": 0, "equity_sales": 0, "notes": notes}
    backup.snapshot_ledger(ledger, session.drive(), folder_id, run_id)
    counts = sheets.append_tables(
        ledger,
        [
            (schema.table(schema.LEDGER, "equity_vests"), new_vests),
            (schema.table(schema.LEDGER, "equity_sales"), new_sales),
        ],
    )
    say(
        log,
        f"legacy equity: {counts['equity_vests']} vests and {counts['equity_sales']} sales added, from "
        f"before the plan's own records",
    )
    return {
        "run_id": run_id,
        "equity_vests": counts["equity_vests"],
        "equity_sales": counts["equity_sales"],
        "notes": notes,
    }


def ingest_legacy_net_worth(session, folder_id, workbook_id, columns, tab="Net Worth", run_id=None, log=print):
    """Add the legacy workbook's home values to `property_values` and its loan balances to `loan_balances`
    (legacy_investments.net_worth), backing up the ledger first. Safe to run every time: a value or balance the
    ledger already holds (same date and home or loan) is not added again, and with nothing new it writes nothing.

    columns maps a column header of the tab to ("value", property_id) or ("loan", loan_id). The homes must be in
    `properties` first, so their loans and dates are known; until they are, it says so and adds nothing.
    """
    run_id = run_id or new_run_id()
    ledger, _, _ = bootstrap(session, folder_id)
    homes = set(read_ledger(ledger, "properties")["property_id"].astype(str).str.strip())
    unknown = sorted({t for k, t in columns.values() if k == "value" and t not in homes})
    if unknown:  # a properties seed in the inbox adds them; until it has, there is nothing to attach these to
        say(log, f"legacy net worth: skipped, no such homes in properties yet: {', '.join(unknown)}", "WARN")
        return {"run_id": run_id, "property_values": 0, "loan_balances": 0, "notes": [], "skipped": unknown}
    workbook = session.sheets.open_by_key(workbook_id)
    got = workbook.values_batch_get([f"'{tab}'"], params={"valueRenderOption": sheets.UNFORMATTED})
    rows = (got.get("valueRanges") or [{}])[0].get("values", [])
    values, loans, notes = legacy_investments.net_worth(rows, columns)
    for note in notes:
        say(log, note)
    have_v, have_l = read_ledger(ledger, "property_values"), read_ledger(ledger, "loan_balances")
    seen_v = set(
        zip(have_v["as_of"].astype(str).str[:10], have_v["property_id"].astype(str), have_v["source"].astype(str))
    )
    seen_l = set(zip(have_l["as_of"].astype(str).str[:10], have_l["loan_id"].astype(str)))
    new_v = values[[k not in seen_v for k in zip(values["as_of"], values["property_id"], values["source"])]]
    new_l = loans[[k not in seen_l for k in zip(loans["as_of"], loans["loan_id"])]]
    if new_v.empty and new_l.empty:
        say(log, f"legacy net worth: nothing new ({len(values)} home values and {len(loans)} loan balances held)")
        return {"run_id": run_id, "property_values": 0, "loan_balances": 0, "notes": notes}
    backup.snapshot_ledger(ledger, session.drive(), folder_id, run_id)
    counts = sheets.append_tables(
        ledger,
        [
            (schema.table(schema.LEDGER, "property_values"), new_v),
            (schema.table(schema.LEDGER, "loan_balances"), new_l),
        ],
    )
    say(
        log,
        f"legacy net worth: {counts['property_values']} home values and {counts['loan_balances']} loan balances added",
    )
    return {
        "run_id": run_id,
        "property_values": counts["property_values"],
        "loan_balances": counts["loan_balances"],
        "notes": notes,
    }
