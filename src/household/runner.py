"""One call runs the engine: every stage in order, with the household's legacy workbooks named in a settings
file in Drive rather than in the notebook.

The settings file is `engine_settings.json`, directly in the household folder (beside the ledger). It names the
old workbooks and how their tabs map onto the ledger; every part is optional, and a part left out skips its
stage. Nothing about the household lives in the package or the notebook:

    {
      "version": 1,
      "legacy_investments": {
        "workbook_id": "<id>",
        "tabs": {"<account tab>": "<account_id>", "<plan tab>": "<account_id>:<money source>"},
        "net_worth_columns": {"<Net Worth column>": ["value", "<property_id>"], "<column>": ["loan", "<loan_id>"]}
      },
      "legacy_budget": {"workbook_id": "<id>", "check_year": 2023},
      "legacy_equity": {"workbook_id": "<id>", "grant_columns": {"<award year>": "<grant id>"},
                        "grants_tab": "<tab>", "sales_tab": "<tab>"}
    }

Every line printed carries the time since the run began ([m:ss]); each stage says when it starts, what it
did and how long it took; a failure says where, with the end of its traceback; and the run ends with a table
of the stages. The run's own lines (not ingest's or compute's, which keep theirs) go to Reports as stage
"run", for the views' Engine log.

The stages, each safe to run every time: the legacy investments workbook (funds and readings), ingest (the
inbox), the legacy Net Worth tab (home values and loans, once the homes are in), the legacy stock plan tabs
(vests and sales from before the plan's records), prices, then compute (with the legacy budget check). A
legacy stage that fails says why and the run goes on; ingest, prices and compute stop the run if they fail.
"""

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from . import __version__, engine, inbox, logs
from .kinds import Log, Session, StageResult

SETTINGS_NAME = "engine_settings.json"
SETTINGS_VERSION = 1
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
TEXT = re.compile(r"^[^\x00-\x1f]{1,100}$")


class SettingsError(ValueError):
    pass


def _id(value, where):
    if not isinstance(value, str) or not FILE_ID.match(value):
        raise SettingsError(f"{where}: not a Drive file id")
    return value


def _names(value, where):
    if not isinstance(value, dict) or not value:
        raise SettingsError(f"{where}: expected a non-empty object")
    for k, v in value.items():
        if not (isinstance(k, str) and TEXT.match(k) and isinstance(v, str) and TEXT.match(v)):
            raise SettingsError(f"{where}: names and values must be short text")
    return dict(value)


def parse_settings(text):
    """The settings file's text, checked: {part: {...}} with only the parts and fields it knows."""
    try:
        got = json.loads(text)
    except ValueError as e:
        raise SettingsError(f"{SETTINGS_NAME} isn't JSON: {e}") from e
    if not isinstance(got, dict) or got.get("version") != SETTINGS_VERSION:
        raise SettingsError(f"{SETTINGS_NAME}: expected version {SETTINGS_VERSION}")
    unknown = set(got) - {"version", "legacy_investments", "legacy_budget", "legacy_equity"}
    if unknown:
        raise SettingsError(f"{SETTINGS_NAME}: unknown parts {sorted(unknown)}")
    out = {}
    if "legacy_investments" in got:
        part, where = got["legacy_investments"], "legacy_investments"
        columns = {}
        for k, v in (part.get("net_worth_columns") or {}).items():
            if not (
                isinstance(k, str)
                and TEXT.match(k)
                and isinstance(v, list)
                and len(v) == 2
                and v[0] in ("value", "loan")
                and isinstance(v[1], str)
                and TEXT.match(v[1])
            ):
                raise SettingsError(f'{where}.net_worth_columns: each is ["value" or "loan", an id]')
            columns[k] = (v[0], v[1])
        out[where] = {
            "workbook_id": _id(part.get("workbook_id"), where),
            "tabs": _names(part.get("tabs"), where + ".tabs"),
            "net_worth_columns": columns,
        }
    if "legacy_budget" in got:
        part, where = got["legacy_budget"], "legacy_budget"
        year = part.get("check_year")
        if year is not None and not (isinstance(year, int) and 2000 <= year <= 2100):
            raise SettingsError(f"{where}.check_year: a year")
        out[where] = {"workbook_id": _id(part.get("workbook_id"), where), "check_year": year}
    if "legacy_equity" in got:
        part, where = got["legacy_equity"], "legacy_equity"
        tabs = {k: part.get(k) for k in ("grants_tab", "sales_tab")}
        if not all(isinstance(v, str) and TEXT.match(v) for v in tabs.values()):
            raise SettingsError(f"{where}: grants_tab and sales_tab name the tabs")
        out[where] = {
            "workbook_id": _id(part.get("workbook_id"), where),
            "grant_columns": _names(part.get("grant_columns"), where + ".grant_columns"),
            **tabs,
        }
    return out


def read_settings(drive, folder_id):
    """The settings file in the household folder, checked, or None when there is none."""
    found = [f for f in inbox.list_files(drive, folder_id) if f["name"] == SETTINGS_NAME]
    if not found:
        return None
    if len(found) > 1:
        raise SettingsError(f"{len(found)} files named {SETTINGS_NAME} in the household folder: keep one")
    return parse_settings(inbox.download(drive, found[0]["id"]).decode("utf-8"))


STAGES = (  # name, what it does, whether it is a legacy import (a failure is said and the run goes on)
    ("legacy investments", "the old investments workbook's funds and readings", True),
    ("ingest", "every file in the inbox, then review answers and prices", False),
    ("legacy net worth", "the old Net Worth tab's home values and loans", True),
    ("legacy equity", "the old stock plan tabs' vests and sales", True),
    ("prices", "GOOGLEFINANCE, round after round, for the prices still wanted", False),
    ("compute", "every report, rebuilt from the ledger", False),
)


def where(error, frames=5):
    """The last few places the error passed through, innermost last: "at household/inbox.py:466 in _read_one"."""
    out, tb = [], error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        path = code.co_filename.replace("\\", "/")
        short = path[path.rindex("household/") :] if "household/" in path else path.rsplit("/", 1)[-1]
        out.append(f"at {short}:{tb.tb_lineno} in {code.co_name}")
        tb = tb.tb_next
    return out[-frames:]


def _clock(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


class RunLog:
    """Prints every line as [elapsed][LEVEL][stage][topic] message (logs.py), and keeps the run's own lines (not
    ingest's or compute's, which keep theirs) for Reports."""

    def __init__(self, log, clock=time.perf_counter):
        self.log, self.clock, self.started, self.lines, self.inside = log, clock, clock(), [], False
        self.stage, self.topic = "run", "run"

    def elapsed(self):
        return self.clock() - self.started

    def at(self, stage):
        self.stage, self.topic = stage, stage

    def __call__(self, message, level=None):
        line = logs.Line(str(message), level or logs.level_of(message))
        if not self.inside:
            self.lines.append(line)
        if not self.log:
            return
        topic, text = logs.topic_of(line)
        if topic:
            self.topic = topic
        else:
            text = line
        for part in text.splitlines() or [""]:
            self.log(
                logs.structured(
                    part, self.stage.replace(" ", "_"), self.topic.replace(" ", "_"), line.level, _clock(self.elapsed())
                )
            )

    def gap(self):
        """A blank line in the notebook between stages (not kept)."""
        if self.log:
            self.log("")


def brief(name, result):
    """One line saying what a stage did, from what it returned."""
    r = result or {}
    if name == "legacy investments":
        added = r.get("securities", 0) + r.get("holdings", 0)
        return (
            f"{r.get('securities', 0)} funds and {r.get('holdings', 0)} readings added"
            if added
            else f"nothing new ({r.get('already_held', 0)} readings already held)"
        )
    if name == "ingest":
        counts = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted((r.get("counts") or {}).items()))
        review = r.get("review") or {}
        return (
            f"{len(r.get('files') or [])} files"
            + (f" ({counts})" if counts else "")
            + f"; {review.get('questions', 0)} review questions open"
        )
    if name == "legacy net worth":
        if r.get("skipped"):
            return "waiting for homes: " + ", ".join(r["skipped"])
        return f"{r.get('property_values', 0)} home values and {r.get('loan_balances', 0)} loan balances added"
    if name == "legacy equity":
        return f"{r.get('equity_vests', 0)} vests and {r.get('equity_sales', 0)} sales added"
    if name == "prices":
        return f"{r.get('added', 0)} added in {r.get('rounds', 0)} rounds, {r.get('wanted') or 0} still wanted"
    if name == "compute":
        bits = []
        if r.get("checked"):
            bits.append(f"legacy check {'PASS' if r.get('mismatches') == 0 else 'FAIL'}")
        bits.append(f"{r.get('review_open_lines', 0)} lines open in review")
        if r.get("wash_sales") is not None:
            bits.append(f"{r.get('lot_parts_sold', 0)} lot parts sold, {r.get('wash_sales', 0)} wash sales")
        return "; ".join(bits)
    return ""


def describe(settings):
    """What the settings file sets, in words."""
    if not settings:
        return f"no {SETTINGS_NAME} in the household folder: the legacy stages are skipped"
    parts = []
    inv = settings.get("legacy_investments")
    if inv:
        parts.append(f"legacy investments ({len(inv['tabs'])} tabs, {len(inv['net_worth_columns'])} Net Worth columns)")
    if settings.get("legacy_budget"):
        year = settings["legacy_budget"].get("check_year")
        parts.append("legacy budget" + (f" (check {year})" if year else ""))
    if settings.get("legacy_equity"):
        parts.append(f"legacy equity ({len(settings['legacy_equity']['grant_columns'])} grant columns)")
    return f"settings: {SETTINGS_NAME} sets " + (", ".join(parts) if parts else "nothing")


def run(session: Session, folder_id: str, log: Log = print, run_id: str | None = None) -> StageResult:
    """Every stage in order (see the module docstring). Each line printed carries the time since the run began;
    each stage says when it starts, what it did and how long it took, a failure with where it happened; the run
    ends with a table of the stages. The run's own lines are kept in Reports (run_log and run_notes, stage
    "run") for the views' Engine log. Returns each stage's result by name."""
    run_id = run_id or engine.new_run_id()
    say = RunLog(log)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    say(f"run: {run_id}, household {__version__}")
    settings = read_settings(session.drive(), folder_id) or {}
    say(describe(settings), "INFO" if settings else "WARN")
    inv, budget, equity = (settings.get(k) for k in ("legacy_investments", "legacy_budget", "legacy_equity"))
    calls: dict[str, Any] = {
        "legacy investments": inv
        and (lambda: engine.ingest_legacy_investments(session, folder_id, inv["workbook_id"], inv["tabs"], log=say)),
        "ingest": lambda: engine.ingest_inbox(session, folder_id, log=say),
        "legacy net worth": inv
        and inv["net_worth_columns"]
        and (
            lambda: engine.ingest_legacy_net_worth(
                session, folder_id, inv["workbook_id"], inv["net_worth_columns"], log=say
            )
        ),
        "legacy equity": equity
        and (
            lambda: engine.ingest_legacy_equity(
                session,
                folder_id,
                equity["workbook_id"],
                equity["grant_columns"],
                equity["grants_tab"],
                equity["sales_tab"],
                log=say,
            )
        ),
        "prices": lambda: engine.fetch_prices(session, folder_id, log=say),
        "compute": lambda: engine.compute_all(
            session,
            folder_id,
            budget["workbook_id"] if budget else None,
            budget["check_year"] if budget else None,
            log=say,
        ),
    }
    todo = [(name, what, legacy) for name, what, legacy in STAGES if calls[name]]
    skipped = [name for name, _, _ in STAGES if not calls[name]]
    say(
        f"run: {len(todo)} stages: {', '.join(n for n, _, _ in todo)}"
        + (f" (not set up: {', '.join(skipped)})" if skipped else "")
    )
    out, table, failed = {}, [], []
    try:
        for n, (name, what, legacy) in enumerate(todo, 1):
            say.gap()
            say.at(name)
            say(f"{name}: stage {n} of {len(todo)}, {what}")
            began = say.elapsed()
            say.inside = name in ("ingest", "compute")
            try:
                out[name.replace(" ", "_")] = result = calls[name]()
            except Exception as e:
                say.inside = False
                took = say.elapsed() - began
                failed.append(name)
                table.append((name, "failed", took, f"{type(e).__name__}: {e}"))
                say(f"{name}: failed after {_clock(took)}: {type(e).__name__}: {e}", "ERROR")
                for line in where(e):
                    say("  " + line, "ERROR")
                if not legacy:
                    raise
                say(f"{name}: the run goes on without it", "WARN")
                continue
            say.inside = False
            took = say.elapsed() - began
            table.append((name, "done", took, brief(name, result)))
            say(f"{name}: done in {_clock(took)}: {table[-1][3]}")
    finally:
        say.inside = False
        say.gap()
        say.at("run")
        say(
            f"summary: {len(table) - len(failed)} of {len(todo)} stages done in {_clock(say.elapsed())}"
            + (f"; failed: {', '.join(failed)}" if failed else ""),
            "ERROR" if failed else "INFO",
        )
        width = max([len(n) for n, _, _ in STAGES] + [5])
        say(f"  {'stage':<{width}}  {'status':<6}  {'time':>5}  result")
        for name, status, took, what in table:
            say(f"  {name:<{width}}  {status:<6}  {_clock(took):>5}  {what}", "ERROR" if status == "failed" else "INFO")
        for name in [n for n, _, _ in todo if n not in {t[0] for t in table}]:
            say(f"  {name:<{width}}  {'not run':<6}", "WARN")
        summary = {
            "run_id": run_id,
            "version": __version__,
            "seconds": round(say.elapsed(), 1),
            "failed": failed,
            "stages": [{"stage": n, "status": st, "seconds": round(t, 1), "result": w} for n, st, t, w in table],
        }
        try:
            _, reports, _ = engine.bootstrap(session, folder_id)
            engine.record_stage(reports, "run", run_id, started_at, summary, say.lines)
        except Exception as e:  # the notebook has the log either way
            say(f"run: could not keep this run's log in Reports: {e}", "WARN")
    say("done: the Status page in the views says what needs you; its Engine log tab has this run")
    return out
