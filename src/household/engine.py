"""Engine: the two stages, ingest then compute, called from the engine notebook.

Stores are found by name inside the household folder, so a notebook needs only
the folder ID (and, for migration, the legacy workbook ID).
"""

import json
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from . import (allocation, backup, budget, categorize, checks, compute, editor, explain, flows, inbox, ingest, investments, legacy, legacy_investments, mortgage, networth, house, valuation,
               paycheck, nudges, prices, pennsylvania, projection, recurring, schema, settings, sheets, tax, taxhistory, taxsources, transfers,
               wisconsin, withholding)


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
    known = set(_read(ledger, "accounts")["account_id"].astype(str).str.strip())
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
    have_funds = set(_read(ledger, "securities")["symbol"].astype(str).str.strip())
    funds = funds[~funds["symbol"].isin(have_funds)]
    have = _read(ledger, "holdings")
    def key(df):
        return zip(*(df[c].astype(str) for c in ("as_of", "account_id", "symbol", "source")))
    keys = set(key(have))
    new = held[[k not in keys for k in key(held)]]
    for note in notes:
        _say(log, note)
    if funds.empty and new.empty:  # the usual case once it has run: nothing to back up or write
        _say(log, f"legacy investments: nothing new ({len(held)} readings already held)")
        return {"run_id": run_id, "securities": 0, "holdings": 0, "already_held": len(held),
                "dates": int(held["as_of"].nunique()), "notes": notes}
    backup.snapshot_ledger(ledger, session.drive(), folder_id, run_id)
    counts = sheets.append_tables(ledger, [(schema.table(schema.LEDGER, "securities"), funds),
                                           (schema.table(schema.LEDGER, "holdings"), new)])
    _say(log, f"legacy investments: {counts['securities']} funds and {counts['holdings']} readings added "
              f"({len(held) - len(new)} already held), {held['as_of'].nunique()} dates")
    return {"run_id": run_id, "securities": counts["securities"], "holdings": counts["holdings"],
            "already_held": len(held) - len(new), "dates": int(held["as_of"].nunique()), "notes": notes}


def ingest_legacy_net_worth(session, folder_id, workbook_id, columns, tab="Net Worth", run_id=None, log=print):
    """Add the legacy workbook's home values to `property_values` and its loan balances to `loan_balances`
    (legacy_investments.net_worth), backing up the ledger first. Safe to run every time: a value or balance the
    ledger already holds (same date and home or loan) is not added again, and with nothing new it writes nothing.

    columns maps a column header of the tab to ("value", property_id) or ("loan", loan_id). The homes must be in
    `properties` first, so their loans and dates are known; until they are, it says so and adds nothing.
    """
    run_id = run_id or new_run_id()
    ledger, _, _ = bootstrap(session, folder_id)
    homes = set(_read(ledger, "properties")["property_id"].astype(str).str.strip())
    unknown = sorted({t for k, t in columns.values() if k == "value" and t not in homes})
    if unknown:  # a properties seed in the inbox adds them; until it has, there is nothing to attach these to
        _say(log, f"legacy net worth: skipped, no such homes in properties yet: {', '.join(unknown)}")
        return {"run_id": run_id, "property_values": 0, "loan_balances": 0, "notes": [], "skipped": unknown}
    workbook = session.sheets.open_by_key(workbook_id)
    got = workbook.values_batch_get([f"'{tab}'"], params={"valueRenderOption": sheets.UNFORMATTED})
    rows = (got.get("valueRanges") or [{}])[0].get("values", [])
    values, loans, notes = legacy_investments.net_worth(rows, columns)
    for note in notes:
        _say(log, note)
    have_v, have_l = _read(ledger, "property_values"), _read(ledger, "loan_balances")
    seen_v = set(zip(have_v["as_of"].astype(str).str[:10], have_v["property_id"].astype(str), have_v["source"].astype(str)))
    seen_l = set(zip(have_l["as_of"].astype(str).str[:10], have_l["loan_id"].astype(str)))
    new_v = values[[k not in seen_v for k in zip(values["as_of"], values["property_id"], values["source"])]]
    new_l = loans[[k not in seen_l for k in zip(loans["as_of"], loans["loan_id"])]]
    if new_v.empty and new_l.empty:
        _say(log, f"legacy net worth: nothing new ({len(values)} home values and {len(loans)} loan balances held)")
        return {"run_id": run_id, "property_values": 0, "loan_balances": 0, "notes": notes}
    backup.snapshot_ledger(ledger, session.drive(), folder_id, run_id)
    counts = sheets.append_tables(ledger, [(schema.table(schema.LEDGER, "property_values"), new_v),
                                           (schema.table(schema.LEDGER, "loan_balances"), new_l)])
    _say(log, f"legacy net worth: {counts['property_values']} home values and {counts['loan_balances']} loan "
              "balances added")
    return {"run_id": run_id, "property_values": counts["property_values"], "loan_balances": counts["loan_balances"],
            "notes": notes}


def fetch_prices(session, folder_id, rounds=8, log=print):
    """Ask GOOGLEFINANCE for the prices still wanted, round after round (each as ingest asks, prices.py), until
    none is wanted, a round adds nothing, or rounds run out: a backfill in one call. Safe to run every time."""
    ledger, _, _ = bootstrap(session, folder_id)
    out = {"added": 0, "wanted": None, "rounds": 0}
    for _ in range(rounds):
        got = _price_requests(ledger, log)
        out["rounds"] += 1
        out["added"] += got["added"]
        out["wanted"] = got["wanted"]
        if not got["wanted"] or (out["rounds"] > 1 and not got["added"]):
            break
    _say(log, f"prices: {out['added']} added in {out['rounds']} rounds, {out['wanted']} still wanted")
    return out


PRICE_POLL_SECONDS = 60  # how long ingest waits for Sheets to answer the price requests it just wrote
PRICE_POLL_EVERY = 10


def _price_requests(ledger, log=print, today=None, wait=PRICE_POLL_SECONDS, sleep=time.sleep):
    """Read the prices Sheets has answered into the ledger, then ask for the ones still wanted (prices.py).

    Ingest waits up to `wait` seconds for the new requests' answers; whatever isn't answered by then is read
    by the next ingest.
    """
    today = today or date.today()
    table, requests_table = schema.table(schema.LEDGER, "prices"), schema.table(schema.LEDGER, "price_requests")
    securities = _read(ledger, "securities")
    usable, refused = prices.quotes(securities)
    for symbol in refused:
        _say(log, f"prices: {symbol}'s quote can't be used (capitals and digits, as VEMAX or MUTF:VEMAX)")
    holdings, activity = _read(ledger, "holdings"), _read(ledger, "investment_activity")
    added = 0

    def collect():
        nonlocal added
        held = _read(ledger, "prices")
        got, waiting = prices.harvest(_read(ledger, "price_requests"))
        if len(held) and len(got):
            have = set(zip(held["symbol"].astype(str), held["date"].astype(str)))
            got = got[[k not in have for k in zip(got["symbol"], got["date"])]]
        added += sheets.append_tables(ledger, [(table, got)])["prices"]
        return held if got.empty else pd.concat([held, got], ignore_index=True), len(waiting)

    held, _ = collect()
    ask, wanted = prices.requests(securities, holdings, activity, held, today)
    sheets.rewrite_price_requests(ledger, requests_table, ask)
    waited = 0
    while len(ask) and waited < wait:
        sleep(PRICE_POLL_EVERY)
        waited += PRICE_POLL_EVERY
        held, waiting = collect()
        if not waiting:
            break
    ask, wanted = prices.requests(securities, holdings, activity, held, today)
    sheets.rewrite_price_requests(ledger, requests_table, ask)
    if usable or added:
        _say(log, f"prices: {added} read from GOOGLEFINANCE, {wanted} still wanted"
                  + (f" ({len(ask)} asked in price_requests; run ingest again for the rest)" if wanted else ""))
    return {"added": added, "wanted": wanted, "asked": len(ask)}


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


CHANGES_WRITTEN_DAYS = 45  # longer than the views remember a discard (30 days)


def changes_written(sources, now=None):
    """The change sets the ledger holds, received in the last CHANGES_WRITTEN_DAYS, by Drive file id (the
    id the views keep for a discard) and name.

    The ledger is the record of what was written: a run_log row says only what its own run did, and the
    next ingest replaces it, so the views check a discarded change set against this instead.
    """
    columns = list(schema.table(schema.REPORTS, "changes_written").columns)
    if sources is None or sources.empty:
        return pd.DataFrame(columns=columns)
    since = (now or datetime.now(timezone.utc)) - timedelta(days=CHANGES_WRITTEN_DAYS)
    s = sources[sources["kind"].astype(str) == "changes"]
    at = pd.to_datetime(s["received_at"], utc=True, errors="coerce")
    s = s[at >= since].assign(file_id=s["drive_link"].astype(str).str.rstrip("/").str.rsplit("/", n=1).str[-1],
                              file=s["original_name"].astype(str), written_at=s["received_at"].astype(str))
    return s.drop_duplicates("file_id", keep="first").sort_values("file")[columns].reset_index(drop=True)


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
    started = time.perf_counter()
    priced = _price_requests(ledger, log)
    timing["prices"] = round(time.perf_counter() - started, 2)
    written = changes_written(_read(ledger, "source_documents"))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "changes_written"), written)
    _say(log, f"change sets written in the last {CHANGES_WRITTEN_DAYS} days: {len(written)}")
    for f in files:  # a parser's notes, kept with the run (the notebook prints them; a change set's are printed above)
        if f.get("parser") != "changes":
            said += [f"note: {f['file']} - {note}" for note in f.get("notes") or []]
    result = {"run_id": run_id, "files": files, "counts": counts, "review": answered, "prices": priced, "seconds": timing,
              # where the inbox is, so the weekly email can list what waits in it with nothing to set up
              "inbox": backup.find_or_create_folder(drive, "inbox", folder_id)}
    _record(reports, "ingest", run_id, started_at, {k: v for k, v in result.items() if k != "files"}
            | {"files": [{k: f.get(k) for k in ("file", "status", "reason", "appended", "written")} for f in files]}, said)
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
    aliases = compute.latest(_read(ledger, "category_aliases"), ["alias"])
    effective = compute.apply_aliases(effective, aliases)
    return {"txns": txns, "effective": effective, "accounts": accounts, "paired": paired, "lonely": lonely,
            "rules": _read(ledger, "payee_rules"), "decisions": _read(ledger, "proposal_decisions"),
            "categories": compute.latest(_read(ledger, "categories"), ["category"]), "parameters": parameters,
            "details": _read(ledger, "payment_details"),
            "items": with_item_categories(_read(ledger, "order_items"), _read(ledger, "item_categories")),
            "aliases": aliases}


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


def _not_yet(new, held, keys, fields):
    """The rows of new that differ from the latest row the ledger holds for their key."""
    if new is None or new.empty or held is None or held.empty:
        return new
    def text(df):
        return df[keys + fields].astype(str).apply(lambda c: c.str.strip())
    last = text(compute.latest(held, keys))
    seen = set(map(tuple, last.values.tolist()))
    return new[[tuple(r) not in seen for r in text(new).values.tolist()]].reset_index(drop=True)


def _review(ledger, log=print, now=None):
    review_table = schema.table(schema.LEDGER, "review")
    typed = _read(ledger, "review")
    categories = compute.latest(_read(ledger, "categories"), ["category"])["category"].astype(str).str.strip()
    decisions, corrections, left = categorize.harvest(typed, set(categories), now=now)
    # an answer the ledger already holds as its latest is not written again: if the review tab's rewrite
    # below failed after these were written, the next run reads the same answers and must not repeat them
    decisions = _not_yet(decisions, _read(ledger, "proposal_decisions"), ["proposal_key"], ["kind", "decision", "note"])
    corrections = _not_yet(corrections, _read(ledger, "txn_corrections"), ["txn_id", "field"], ["value", "reason", "source"])
    sheets.append_tables(ledger, [(schema.table(schema.LEDGER, "proposal_decisions"), decisions),
                                  (schema.table(schema.LEDGER, "txn_corrections"), corrections)])
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


def _investments_and_homes(ledger, reports, books, sched, today, run_id, log):
    """Investment values, the allocation and a rebalance, homes, and net worth (valuation.py, allocation.py,
    house.py, networth.py) into Reports. Returns figures for the run's result."""
    accounts, params = books["accounts"], books["parameters"]
    holdings, activity = _read(ledger, "holdings"), _read(ledger, "investment_activity")
    securities = _read(ledger, "securities")
    vals, notes = valuation.values(accounts, holdings, activity, _read(ledger, "prices"), securities, today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "investment_values"), vals.assign(run_id=run_id))
    now = vals[vals["date"] == today] if len(vals) else vals
    _say(log, f"investments: {len(now)} positions in {now['account_id'].nunique() if len(now) else 0} accounts worth "
              f"{pd.to_numeric(now['value'], errors='coerce').sum():,.0f} today; {vals['date'].nunique() if len(vals) else 0}"
              " dates valued")
    for line in notes:
        _say(log, line)
    if len(now):
        by = now.assign(v=pd.to_numeric(now["value"], errors="coerce")).groupby("bucket")["v"].sum()
        _say(log, "  by tax bucket: " + ", ".join(f"{valuation.BUCKET_NAMES.get(b, b)} {v:,.0f}" for b, v in by.items()))
        stale = now[now["basis"] == "carried"]
        if len(stale):
            _say(log, f"  {len(stale)} positions carried at a reading's value (no price since): "
                      + ", ".join(sorted(set(stale["account_id"] + ":" + stale["symbol"].replace("", "(total)")))))
    nodes = allocation.tree(_read(ledger, "allocation_targets"), today)
    scope = allocation.in_scope(vals)
    current = allocation.by_class(scope) if scope is not None and len(scope) else {}
    since = (pd.Timestamp(today) - pd.DateOffset(years=2)).date().isoformat()
    trades, proposed, more = (allocation.rebalance(scope, nodes, allocation.ever_held(holdings, activity, since))
                              if current else (pd.DataFrame(columns=allocation.TRADE_COLUMNS), {}, []))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "allocation"),
                         allocation.report(nodes, current, proposed).assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "rebalance"), trades.assign(run_id=run_id))
    for line in more:
        _say(log, line)
    tables = {n: _read(ledger, n) for n in ("properties", "property_values", "improvements", "loans", "loan_balances")}
    props, pvals, improved, loans, balances = house.frames(tables)
    improved = house.improvements(improved)
    summary, more = house.summary(props, pvals, improved, loans, sched, balances, params, today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "property_summary"), summary.assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "home_values"),
                         house.values_report(pvals).assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "home_improvements"), improved.assign(run_id=run_id))
    for line in more:
        _say(log, line)
    for r in summary[summary["key"].isin(["value_used", "equity"])].to_dict("records"):
        _say(log, f"house: {r['property_id']} {r['label'].lower()} {float(r['value']):,.0f}"
                  + (f" ({r['note']})" if r["note"] else ""))
    people = valuation.adults(params, _read(ledger, "paystubs"))
    rule = settings.value(params, "home_value_basis", "purchase_price").strip() or "purchase_price"
    net = networth.build(vals, people, props, pvals, loans, sched, balances, rule)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "net_worth"), net.assign(run_id=run_id))
    totals = networth.totals(net)
    if totals:
        first, last = min(totals), max(totals)
        _say(log, f"net worth: {totals[last]:,.0f} on {last} ({len(totals)} dates from {first}); owners "
                  + ", ".join(people or ("none set",)))
    return {"investments_value": round(float(pd.to_numeric(now["value"], errors="coerce").sum()), 2) if len(now) else 0.0,
            "rebalance_trades": len(trades), "net_worth": totals.get(max(totals), 0.0) if totals else 0.0}


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
    daily = compute.daily_actuals(categorize.actual_rows(categorized), budget._not_spending(books["categories"]))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "daily_actuals"), daily.assign(run_id=run_id))
    activity = _read(ledger, "investment_activity")
    invested = investments.flows(activity)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "investment_flows"), invested.assign(run_id=run_id))
    if len(activity):
        _say(log, f"investments: {len(activity)} activity rows in {activity['account_id'].nunique()} accounts")
        for r in invested[invested["kind"].isin(["contribution", "conversion_in", "conversion_out", "withdrawal"])].head(12).to_dict("records"):
            _say(log, f"  {r['year']} {r['account_id']}: {r['kind']} {r['amount']:,.2f}")

    today = computed_at[:10]
    rules = _read(ledger, "budget_rules")
    planned = pd.DataFrame(columns=budget.TARGET_COLUMNS)
    if not rules.empty:
        expected = recurring.monthly_expected(categorized, books["decisions"], today[:7], today)
        planned = budget.targets(rules, actuals, today[:7], expected)
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
    published = editor.publish(books, _read(ledger, "review"), rules, planned, actuals, today[:7])
    for name, df in published.items():
        sheets.replace_table(reports, schema.table(schema.REPORTS, name), df.assign(run_id=run_id))
    _say(log, "editor: " + ", ".join(f"{len(df)} {name[len('editor_'):].replace('_', ' ')}"
                                     for name, df in published.items()) + " for the views")
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
    sched = pd.DataFrame(columns=mortgage.COLUMNS)
    if not loans.empty:
        # to the end of each loan's term: the months ahead are the schedule as it stands, for the Home page
        sched, notes = mortgage.schedule(loans, _read(ledger, "loan_events"), categorized, "9999-12")
        sched.loc[sched["month"] > today[:7], "paid_from_ledger"] = ""
        sheets.replace_table(reports, schema.table(schema.REPORTS, "mortgage"), sched.assign(run_id=run_id))
        for line in notes + mortgage.summary(sched[sched["month"] <= today[:7]]):
            _say(log, line)
        ahead = sched[sched["month"] > today[:7]]
        for loan_id, g in ahead.groupby("loan_id"):
            _say(log, f"mortgage: {loan_id} paid off {g['month'].iloc[-1]} as scheduled, "
                      f"{g['interest'].sum():,.0f} interest still to pay")
    worth = _investments_and_homes(ledger, reports, books, sched, today, run_id, log)
    subscriptions = recurring.discover(categorized, today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "recurring"), subscriptions.assign(run_id=run_id))
    _say(log, f"recurring: {len(subscriptions)} recurring charges, "
              f"{int((subscriptions['status'] == 'price changed').sum())} changed price, "
              f"{int((subscriptions['status'] == 'stopped').sum())} stopped")
    subs = recurring.subscriptions(categorized, books["decisions"], today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "subscriptions"), subs.assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "editor_subscriptions"),
                         recurring.editor_rows(subs, subscriptions, categorized, books["decisions"], today)
                         .assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "subscription_months"),
                         recurring.subscription_months(categorized, books["decisions"], books["categories"], today)
                         .assign(run_id=run_id))
    changed = recurring.fresh_changes(subs, today)
    yearly = pd.to_numeric(subs["yearly"], errors="coerce")[subs["status"] != "stopped"].sum()
    _say(log, f"tracked recurring charges: {len(subs)}, {yearly:,.0f} a year, {len(changed)} changed price lately")
    for r in changed.to_dict("records"):
        _say(log, f"recurring charge: {r['key']} now {r['change']}")
    money = flows.build(_read(ledger, "paystubs"), categorized, books["categories"], _read(ledger, "equity_sales"))
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
              "subscription_price_changes": len(changed),
              "review_open_lines": counts["open_lines"], "downloads_due": len(to_get),
              "balances_off": len(off), "stubs_missing": len(forgot), "pay_not_deposited": len(lost),
              "possible_duplicates": len(dupes), "transfer_pairs": len(paired),
              "transfers_unmatched": len(waiting), "transfers_unmatched_answered": len(lonely) - len(waiting),
              # how lines since REVIEW_FROM were categorized, for the status page's breakdown
              "categories": {"since": categorize.REVIEW_FROM, "decided": counts["decided"], "waiting": counts["waiting"],
                             "open_lines": counts["open_lines"]}, **worth}

    facts = sheets.read_table(ledger, schema.table(schema.LEDGER, "return_facts"))
    if not facts.empty:
        check = tax.check_against_return(facts)
        check["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_check"), check)
        result["tax_check_mismatches"] = int((~check["match"]).sum())
        _say(log, f"tax_check: {len(check)} filed lines, {result['tax_check_mismatches']} off by more "
                  "than 1 dollar")
        unchecked = sorted(set(facts.loc[facts["kind"] == "filed", "year"].astype(int)) - set(tax.taxdata.PARAMS))
        if unchecked:
            _say(log, f"tax_check: {', '.join(map(str, unchecked))} shown as filed; their tax law is not on file, "
                      "so their lines are not recomputed")
        past = taxhistory.history(facts).assign(basis="filed")
        sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_history"), past.assign(run_id=run_id))
        _say(log, f"tax_history: {past['year'].nunique()} filed years")
        sheets.replace_table(reports, schema.table(schema.REPORTS, "filed_lines"),
                             taxhistory.filed_lines(facts).assign(run_id=run_id))

    documents = sheets.read_table(ledger, schema.table(schema.LEDGER, "source_documents"))
    sources = taxsources.report(documents)
    sources["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_sources"), sources)
    unfiled = int((sources["record"] == "not in record store").sum())
    result["tax_sources_unfiled"] = unfiled
    _say(log, f"tax_sources: {len(sources)} cited values, {unfiled} whose source PDF is not in the "
              "record store")

    stubs = sheets.read_table(ledger, schema.table(schema.LEDGER, "paystubs"))
    jobs = sheets.read_table(ledger, schema.table(schema.LEDGER, "comp_events"))
    pay = paycheck.check(stubs, jobs)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "pay_check"), pay.assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "editor_jobs"),
                         paycheck.jobs(jobs, pay).assign(run_id=run_id))
    wrong_pay = pay[pay["status"] != "agrees"]
    result["pay_mismatches"] = len(wrong_pay)
    _say(log, f"pay_check: {len(pay)} runs of paychecks beside comp_events, {len(wrong_pay)} disagree")
    for row in wrong_pay.to_dict("records"):
        _say(log, f"  {row['person']} at {row['employer']}: {row['finding']}")
    tax_plan = tax_elections = None  # this year's, for the weekly email
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
            vests = projection.scheduled_vests(*(sheets.read_table(ledger, schema.table(schema.LEDGER, t))
                                                  for t in ("vest_schedule", "equity_vests", "prices")))
            if vests:
                _say(log, f"projection: vests from the vesting schedule at {vests['price']:,.2f} a share, the "
                          f"{vests['months']}-month average (latest {vests['latest']:,.2f} on {vests['price_date']}; "
                          "a month-old date means prices need fetching)")
            lines, notes, detail = projection.project(year, stubs, sales, facts, jobs, vests)
            lines["year"], lines["run_id"] = year, run_id
            sheets.replace_table(reports, schema.table(schema.REPORTS, "projection"), lines)
            # last year too: as filed once its return is in return_facts, until then the year its stubs show
            details = [detail.assign(year=year)]
            filed_years = set(facts.loc[facts["kind"] == "filed", "year"].astype(str)) if not facts.empty else set()
            if str(year) not in filed_years:  # this year joins the history as projected, until its return is in
                now = taxhistory.projected(year, detail[detail["scenario"] == "as is"]).assign(basis="projected")
                before = taxhistory.history(facts).assign(basis="filed") if not facts.empty else now.iloc[0:0]
                sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_history"),
                                     pd.concat([before, now], ignore_index=True).assign(run_id=run_id))
            last = year - 1
            if last in tax.taxdata.PARAMS and projection.has_return(facts, last):
                details.append(projection.as_filed(last, facts).assign(year=last))
                filed_lines = details[-1][details[-1]["filed"].notna()]
                off = filed_lines[(filed_lines["value"] - filed_lines["filed"]).abs() > 1]
                _say(log, f"tax_lines: {last} as filed, {len(filed_lines)} lines beside the filed return, "
                          f"{len(off)} off by more than 1")
            elif last in tax.taxdata.PARAMS and (stubs["pay_date"].astype(str).str[:4] == str(last)).any():
                details.append(projection.project(last, stubs, sales, facts, jobs, vests)[2].assign(year=last))
                _say(log, f"tax_lines: {last} from its pay stubs until its return's figures are in return_facts")
            every = pd.concat(details, ignore_index=True)
            sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_lines"), every.assign(run_id=run_id))
            params = books["parameters"]
            setting = lambda key, default: float(categorize.household_parameter(params, key, default) or default)  # noqa: E731
            target, rate = setting("target_refund", 2000), setting("planned_supplemental_rate", 0.35)
            plan = projection.withholding_plan(year, stubs, sales, facts, target, rate, jobs, vests)
            tax_plan = plan if year == int(today[:4]) else None
            sheets.replace_table(reports, schema.table(schema.REPORTS, "withholding_plan"),
                                 plan.assign(year=year, run_id=run_id))
            for row in plan.to_dict("records"):
                if pd.isna(row["suggested_extra"]):
                    _say(log, f"withholding plan {year}, {row['return']}: {row['note']}")
                    continue
                goal = ("a refund of " if row["target"] > 0 else "owing ") + format(abs(row["target"]), ",.0f") \
                    if row["target"] else "no balance"
                _say(log, f"withholding plan {year}, {row['return']}: with vests at {rate:.0%}, "
                          f"{'refund' if row['projected'] >= 0 else 'owe'} {abs(row['projected']):,.0f}; for {goal}, "
                          f"{row['person']} ({row['employer']}) withholds {row['suggested_extra']:,.2f} extra a "
                          f"paycheck (now {row['current_extra']:,.2f}) for {row['paychecks_left']} paychecks"
                          + ("" if abs(row["lands_at"] - row["target"]) <= 1 else
                             f", which can only get to {row['lands_at']:,.0f}")
                          + (" (an underpayment penalty is possible)" if row["penalty_free"] is False else ""))
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
            converts = categorize.household_parameter(books["parameters"], "after_tax_401k_converts_to_roth").upper() == "TRUE"
            plans = projection.elections(year, stubs, after_tax_converts=converts, comp_events=jobs)
            plans["run_id"] = run_id
            sheets.replace_table(reports, schema.table(schema.REPORTS, "elections"), plans)
            tax_elections = plans if year == int(today[:4]) else None
            for row in plans[plans["status"] != "at limit"].to_dict("records"):
                _say(log, f"elections {year}: {row['person']} {row['plan']}: {row['status']} "
                          f"({row['gap']:+,.2f})")
            result["projection"] = dict(zip(lines["scenario"], lines["owe_or_refund"]))
            result["projection_year"] = year
    day = date.fromisoformat(today)
    params = books["parameters"]
    for key, v, problem in settings.check(params):
        _say(log, f"settings: {key} {v!r} {problem}")
    searches = settings.receipt_searches(params)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "receipt_searches"), searches.assign(run_id=run_id))
    _say(log, f"receipt_searches: {len(searches)} for the receipt exporter"
              + (f" ({', '.join(searches['store'])})" if len(searches) else ""))
    try:
        written = inbox.write_receipt_searches(session.drive(), searches)
        if not written:
            _say(log, "receipt_searches: the exporter has no searches file yet; run exportReceipts once, then compute")
    except Exception as e:  # the rest of compute goes on: the exporter keeps the searches it was last given
        _say(log, f"receipt_searches: could not write the exporter's searches file: {e}")
    fsa = nudges.fsa_balances(stubs, categorized, params, day, tax_elections)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "fsa_balances"), fsa.assign(run_id=run_id))
    for r in fsa.to_dict("records"):
        _say(log, f"fsa_balances {r['year']} {r['account']}: {r['reimbursed']:,.2f} of {r['elected']:,.2f} "
                  f"reimbursed, {r['left']:,.2f} left" + (f", claims due {r['claim_deadline']}" if r["claim_deadline"] else ""))
    todo = nudges.build(day, params, due, published["editor_review"], tax_plan, tax_elections, stubs, fsa, changed)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "nudges"), todo.assign(run_id=run_id))
    showing = todo[(todo["show_from"] <= today) & ((todo["show_until"] == "") | (todo["show_until"] >= today))]
    _say(log, f"nudges: {len(todo)} for the weekly email, {len(showing)} of them due in it as of today")

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
