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

The stages, each safe to run every time: the legacy investments workbook (funds and readings), ingest (the
inbox), the legacy Net Worth tab (home values and loans, once the homes are in), the legacy stock plan tabs
(vests and sales from before the plan's records), prices, then compute (with the legacy budget check). A
legacy stage that fails says why and the run goes on; ingest, prices and compute stop the run if they fail.
"""

import json
import re

from . import engine, inbox

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
            if not (isinstance(k, str) and TEXT.match(k) and isinstance(v, list) and len(v) == 2
                    and v[0] in ("value", "loan") and isinstance(v[1], str) and TEXT.match(v[1])):
                raise SettingsError(f"{where}.net_worth_columns: each is [\"value\" or \"loan\", an id]")
            columns[k] = (v[0], v[1])
        out[where] = {"workbook_id": _id(part.get("workbook_id"), where),
                      "tabs": _names(part.get("tabs"), where + ".tabs"), "net_worth_columns": columns}
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
        out[where] = {"workbook_id": _id(part.get("workbook_id"), where),
                      "grant_columns": _names(part.get("grant_columns"), where + ".grant_columns"), **tabs}
    return out


def read_settings(drive, folder_id):
    """The settings file in the household folder, checked, or None when there is none."""
    found = [f for f in inbox.list_files(drive, folder_id) if f["name"] == SETTINGS_NAME]
    if not found:
        return None
    if len(found) > 1:
        raise SettingsError(f"{len(found)} files named {SETTINGS_NAME} in the household folder: keep one")
    return parse_settings(inbox.download(drive, found[0]["id"]).decode("utf-8"))


def run(session, folder_id, log=print):
    """Every stage in order (see the module docstring). Returns each stage's result by name."""
    settings = read_settings(session.drive(), folder_id)
    if settings is None:
        engine._say(log, f"no {SETTINGS_NAME} in the household folder: the legacy stages are skipped")
        settings = {}
    out, failed = {}, []

    def legacy(name, call):
        try:
            out[name] = call()
        except Exception as e:  # a legacy import is history: say why and go on
            failed.append(name)
            engine._say(log, f"{name.replace('_', ' ')}: not run: {e}")

    inv, budget, equity = (settings.get(k) for k in ("legacy_investments", "legacy_budget", "legacy_equity"))
    if inv:
        legacy("legacy_investments", lambda: engine.ingest_legacy_investments(
            session, folder_id, inv["workbook_id"], inv["tabs"], log=log))
    out["ingest"] = engine.ingest_inbox(session, folder_id, log=log)
    if inv and inv["net_worth_columns"]:
        legacy("legacy_net_worth", lambda: engine.ingest_legacy_net_worth(
            session, folder_id, inv["workbook_id"], inv["net_worth_columns"], log=log))
    if equity:
        legacy("legacy_equity", lambda: engine.ingest_legacy_equity(
            session, folder_id, equity["workbook_id"], equity["grant_columns"], equity["grants_tab"],
            equity["sales_tab"], log=log))
    out["prices"] = engine.fetch_prices(session, folder_id, log=log)
    out["compute"] = engine.compute_all(session, folder_id, budget["workbook_id"] if budget else None,
                                        budget["check_year"] if budget else None, log=log)
    checked = out["compute"].get("checked")
    verdict = ("" if not checked else
               f"; legacy check {'PASS' if out['compute'].get('mismatches') == 0 else 'FAIL'}")
    engine._say(log, f"done: ingest, prices and compute ran{verdict}"
                     + (f"; not run: {', '.join(failed)}" if failed else "")
                     + ". The status page in the views says what needs you.")
    return out
