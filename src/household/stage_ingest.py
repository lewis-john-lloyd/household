"""The ingest stage: every file in the inbox into the ledger, then the review tab's answers and the prices
GOOGLEFINANCE answered. Ingest is the only stage that writes the ledger.
"""

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from . import backup, categorize, compute, inbox, prices, schema, sheets, valuation
from .books import categorize_books, read_books
from .kinds import Log, Session, StageResult
from .ledgerio import bootstrap, new_run_id, read_ledger, versions
from .runlog import record_stage, recording, say


def fetch_prices(session: Session, folder_id: str, rounds: int = 8, log: Log = print) -> StageResult:
    """Ask GOOGLEFINANCE for the prices still wanted, round after round (each as ingest asks, prices.py), until
    none is wanted, a round adds nothing, or rounds run out: a backfill in one call. Safe to run every time."""
    ledger, _, _ = bootstrap(session, folder_id)
    out: dict[str, Any] = {"added": 0, "wanted": None, "rounds": 0}
    for _ in range(rounds):
        got = _price_requests(ledger, log)
        out["rounds"] += 1
        out["added"] += got["added"]
        out["wanted"] = got["wanted"]
        if not got["wanted"] or (out["rounds"] > 1 and not got["added"]):
            break
    say(log, f"prices: {out['added']} added in {out['rounds']} rounds, {out['wanted']} still wanted")
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
    securities = read_ledger(ledger, "securities")
    usable, refused = prices.quotes(securities)
    for symbol in refused:
        say(log, f"prices: {symbol}'s quote can't be used (capitals and digits, as VEMAX or MUTF:VEMAX)", "WARN")
    holdings, activity = read_ledger(ledger, "holdings"), read_ledger(ledger, "investment_activity")
    stand_ins = sorted(set(valuation.proxies(read_ledger(ledger, "parameters")).values()))
    added = 0

    def collect():
        nonlocal added
        held = read_ledger(ledger, "prices")
        got, waiting = prices.harvest(read_ledger(ledger, "price_requests"))
        if len(held) and len(got):
            have = set(zip(held["symbol"].astype(str), held["date"].astype(str)))
            got = got[[k not in have for k in zip(got["symbol"], got["date"])]]
        added += sheets.append_tables(ledger, [(table, got)])["prices"]
        return held if got.empty else pd.concat([held, got], ignore_index=True), len(waiting)

    held, _ = collect()
    ask, wanted = prices.requests(securities, holdings, activity, held, today, stand_ins=stand_ins)
    sheets.rewrite_price_requests(ledger, requests_table, ask)
    if len(ask):
        say(
            log,
            f"prices: asked GOOGLEFINANCE for {len(ask)} prices in price_requests; waiting up to {wait}s "
            "for Sheets to answer",
        )
    waited = 0
    while len(ask) and waited < wait:
        sleep(PRICE_POLL_EVERY)
        waited += PRICE_POLL_EVERY
        held, waiting = collect()
        if not waiting:
            break
    ask, wanted = prices.requests(securities, holdings, activity, held, today, stand_ins=stand_ins)
    sheets.rewrite_price_requests(ledger, requests_table, ask)
    if usable or stand_ins or added:
        say(
            log,
            f"prices: {added} read from GOOGLEFINANCE, {wanted} still wanted"
            + (f" ({len(ask)} asked in price_requests; run ingest again for the rest)" if wanted else ""),
        )
    return {"added": added, "wanted": wanted, "asked": len(ask)}


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
    s = s[at >= since].assign(
        file_id=s["drive_link"].astype(str).str.rstrip("/").str.rsplit("/", n=1).str[-1],
        file=s["original_name"].astype(str),
        written_at=s["received_at"].astype(str),
    )
    return s.drop_duplicates("file_id", keep="first").sort_values("file")[columns].reset_index(drop=True)


def ingest_inbox(session: Session, folder_id: str, run_id: str | None = None, log: Log = print) -> StageResult:
    """Stage 1: back up the ledger, then ingest every file in the inbox (see inbox.py).

    Progress goes to log (print by default; None for silence).
    """
    run_id = run_id or new_run_id()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log, said = recording(log)
    say(log, f"run {run_id}: opening the ledger")
    say(log, f"versions: {versions()}")
    ledger, reports, _ = bootstrap(session, folder_id)
    drive = session.drive()
    say(log, "backing up every ledger tab")
    started = time.perf_counter()
    manifest = backup.snapshot_ledger(ledger, drive, folder_id, run_id)
    timing = {"backup": round(time.perf_counter() - started, 2)}
    say(log, f"backup: {len(manifest['tables'])} tabs to backups/ledger_{run_id}.zip ({timing['backup']:.1f}s)")
    files = inbox.process(ledger, drive, folder_id, timing=timing, log=log)
    counts: dict[str, int] = {}
    for f in files:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    say(log, "review: reading the answers in the review tab, then rebuilding its questions (often a minute)")
    started = time.perf_counter()
    answered = _review(ledger, log)
    timing["review"] = round(time.perf_counter() - started, 2)
    started = time.perf_counter()
    priced = _price_requests(ledger, log)
    timing["prices"] = round(time.perf_counter() - started, 2)
    written = changes_written(read_ledger(ledger, "source_documents"))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "changes_written"), written)
    say(log, f"change sets written in the last {CHANGES_WRITTEN_DAYS} days: {len(written)}")
    for f in files:  # a parser's notes, kept with the run (the notebook prints them; a change set's are printed above)
        if f.get("parser") != "changes":
            said += [f"note: {f['file']} - {note}" for note in f.get("notes") or []]
    result = {
        "run_id": run_id,
        "files": files,
        "counts": counts,
        "review": answered,
        "prices": priced,
        "seconds": timing,
        # where the inbox is, so the weekly email can list what waits in it with nothing to set up
        "inbox": backup.find_or_create_folder(drive, "inbox", folder_id),
    }
    record_stage(
        reports,
        "ingest",
        run_id,
        started_at,
        {k: v for k, v in result.items() if k != "files"}
        | {"files": [{k: f.get(k) for k in ("file", "status", "reason", "appended", "written")} for f in files]},
        said,
    )
    return result


# accounts renamed by the account_renamed:<old id> setting: the ledger keeps the id each row was written with
# (so a document read again matches what it wrote before), and compute reads every account column under the new
# name. Set by compute_all from the parameters before anything else is read.


def review(session: Session, folder_id: str, log: Log = print, now: datetime | None = None) -> StageResult:
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
    typed = read_ledger(ledger, "review")
    categories = compute.latest(read_ledger(ledger, "categories"), ["category"])["category"].astype(str).str.strip()
    decisions, corrections, left = categorize.harvest(typed, set(categories), now=now)
    # an answer the ledger already holds as its latest is not written again: if the review tab's rewrite
    # below failed after these were written, the next run reads the same answers and must not repeat them
    decisions = _not_yet(
        decisions, read_ledger(ledger, "proposal_decisions"), ["proposal_key"], ["kind", "decision", "note"]
    )
    corrections = _not_yet(
        corrections, read_ledger(ledger, "txn_corrections"), ["txn_id", "field"], ["value", "reason", "source"]
    )
    sheets.append_tables(
        ledger,
        [
            (schema.table(schema.LEDGER, "proposal_decisions"), decisions),
            (schema.table(schema.LEDGER, "txn_corrections"), corrections),
        ],
    )
    if len(decisions) or len(corrections):
        say(log, f"review: read {len(decisions)} payee answers and {len(corrections)} line answers")
    for row in left.to_dict("records"):
        say(log, f"review: {row['key']}: {row['note']}")

    books = read_books(ledger)
    categorized = categorize_books(books)
    rows = categorize.queue(categorized, carry=left)
    answers = ["y"] + sorted(c for c in categories if c and c != "transfer") + ["transfer"]
    sheets.rewrite_review(ledger, review_table, rows, answers=answers)
    open_lines = int(pd.to_numeric(rows["lines"]).sum()) if len(rows) else 0
    say(
        log,
        f"review: {len(rows)} questions open ({open_lines} lines): answer them in the ledger's "
        "review tab, then run ingest again",
    )
    return {
        "answered": len(decisions) + len(corrections),
        "unusable": len(left),
        "questions": len(rows),
        "open_lines": open_lines,
    }
