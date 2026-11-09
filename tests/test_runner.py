"""One call runs every stage, the legacy workbooks named in a settings file in Drive."""

import json

import pytest

from household import __version__, engine, logs, runner

SETTINGS = {
    "version": 1,
    "legacy_investments": {
        "workbook_id": "a" * 30,
        "tabs": {"alex-ira": "a_ira", "alex-plan": "a_plan:roth"},
        "net_worth_columns": {"Willow - V": ["value", "willow"], "Willow - M": ["loan", "acme_loan"]},
    },
    "legacy_budget": {"workbook_id": "b" * 30, "check_year": 2001},
    "legacy_equity": {
        "workbook_id": "c" * 30,
        "grant_columns": {"2000": "G1"},
        "grants_tab": "grants",
        "sales_tab": "sales",
    },
}


def test_the_settings_are_checked_part_by_part():
    got = runner.parse_settings(json.dumps(SETTINGS))
    assert got["legacy_investments"]["net_worth_columns"]["Willow - M"] == ("loan", "acme_loan")
    assert got["legacy_budget"] == {"workbook_id": "b" * 30, "check_year": 2001}
    assert runner.parse_settings(json.dumps({"version": 1})) == {}
    for bad, message in [
        ({"version": 2}, "version 1"),
        ({**SETTINGS, "extra": {}}, "unknown parts"),
        ({"version": 1, "legacy_budget": {"workbook_id": "../x"}}, "Drive file id"),
        ({"version": 1, "legacy_equity": {**SETTINGS["legacy_equity"], "grants_tab": ""}}, "name the tabs"),
        (
            {
                "version": 1,
                "legacy_investments": {**SETTINGS["legacy_investments"], "net_worth_columns": {"x": ["equity", "y"]}},
            },
            "value",
        ),
    ]:
        with pytest.raises(runner.SettingsError, match=message):
            runner.parse_settings(json.dumps(bad))
    with pytest.raises(runner.SettingsError, match="isn't JSON"):
        runner.parse_settings("{")


class Session:
    def drive(self):
        return None


def stages(monkeypatch, files, fail=()):
    calls = []

    def stage(name, result=None):
        def call(*args, **kwargs):
            calls.append((name, args[2:]))
            if name in fail:
                raise ValueError("no such accounts in the ledger: a_ira (add them first)")
            return result if result is not None else {}

        return call

    monkeypatch.setattr(
        runner.inbox, "list_files", lambda drive, folder: [{"id": str(i), "name": n} for i, n in enumerate(files)]
    )
    monkeypatch.setattr(runner.inbox, "download", lambda drive, file_id: json.dumps(SETTINGS).encode())
    for name in (
        "ingest_legacy_investments",
        "ingest_inbox",
        "ingest_legacy_net_worth",
        "ingest_legacy_equity",
        "fetch_prices",
    ):
        monkeypatch.setattr(engine, name, stage(name))
    monkeypatch.setattr(engine, "compute_all", stage("compute_all", {"checked": 3, "mismatches": 0}))
    return calls


def recorded(monkeypatch):
    kept = {}
    monkeypatch.setattr(engine, "bootstrap", lambda session, folder: (None, "reports", None))
    monkeypatch.setattr(
        engine,
        "_record",
        lambda reports, stage, run_id, at, summary, lines: kept.update(stage=stage, summary=summary, lines=list(lines)),
    )
    return kept


def test_a_run_goes_through_every_stage_in_order_with_the_settings(monkeypatch):
    calls, kept = stages(monkeypatch, ["ledger", runner.SETTINGS_NAME]), recorded(monkeypatch)
    said = []
    runner.run(Session(), "folder", log=said.append, run_id="r1")
    assert [c[0] for c in calls] == [
        "ingest_legacy_investments",
        "ingest_inbox",
        "ingest_legacy_net_worth",
        "ingest_legacy_equity",
        "fetch_prices",
        "compute_all",
    ]
    assert calls[3][1] == ("c" * 30, {"2000": "G1"}, "grants", "sales") and calls[5][1] == ("b" * 30, 2001)
    # every line is [elapsed][stage][topic][LEVEL] message
    assert said[0] == f"[0:00][run][run][INFO] r1, household {__version__}"
    assert "[0:00][legacy_equity][legacy_equity][INFO] stage 4 of 6, the old stock plan tabs' vests and sales" in said
    assert "[0:00][compute][compute][INFO] done in 0:00: legacy check PASS; 0 lines open in review" in said
    assert "[0:00][run][summary][INFO] 6 of 6 stages done in 0:00" in said
    # the run's own lines, kept for the views, are plain text with their levels, and its table of stages
    assert kept["stage"] == "run" and kept["lines"][0] == "run: r1, household " + __version__
    assert [st["stage"] for st in kept["summary"]["stages"]] == [n for n, _, _ in runner.STAGES]


def test_a_legacy_stage_that_fails_says_why_where_and_the_rest_still_run(monkeypatch):
    calls, kept = (
        stages(monkeypatch, [runner.SETTINGS_NAME], fail=("ingest_legacy_investments",)),
        recorded(monkeypatch),
    )
    said = []
    runner.run(Session(), "folder", log=said.append, run_id="r1")
    failed = [line for line in said if "[ERROR]" in line]
    assert failed[0] == (
        "[0:00][legacy_investments][legacy_investments][ERROR] failed after 0:00: ValueError: "
        "no such accounts in the ledger: a_ira (add them first)"
    )
    at = [line for line in failed if "][ERROR]   at " in line]
    assert at and at[-1].endswith(" in call")  # the innermost place it passed through, last
    assert "[0:00][run][summary][ERROR] 5 of 6 stages done in 0:00; failed: legacy investments" in said
    assert calls[-1][0] == "compute_all" and kept["summary"]["failed"] == ["legacy investments"]
    assert [logs.level_of(line) for line in kept["lines"] if "failed after" in line] == ["ERROR"]


def test_with_no_settings_file_only_the_inbox_prices_and_compute_run(monkeypatch):
    calls, _ = stages(monkeypatch, ["ledger"]), recorded(monkeypatch)
    said = []
    runner.run(Session(), "folder", log=said.append, run_id="r1")
    assert [c[0] for c in calls] == ["ingest_inbox", "fetch_prices", "compute_all"]
    assert (
        said[1]
        == "[0:00][run][run][WARN] no engine_settings.json in the household folder: the legacy stages are skipped"
    )


def test_a_line_names_its_topic_and_keeps_its_level():
    assert logs.topic_of("prices: 3 added") == ("prices", "3 added")
    assert logs.topic_of("lots 2025: short-term gain 9") == ("lots", "2025: short-term gain 9")
    assert logs.topic_of("  by tax bucket: 401(k)") == ("", "  by tax bucket: 401(k)")
    line = logs.warn("lots: a vest is missing")
    assert line == "lots: a vest is missing" and logs.level_of(line) == "WARN" and logs.level_of("plain") == "INFO"
    said = []
    log = runner.RunLog(said.append, clock=iter([0, 75, 76]).__next__)
    log.at("ingest")
    log("inbox: 2 files")
    log("[1/2] a.csv: failed", "WARN")
    assert said == ["[1:15][ingest][inbox][INFO] 2 files", "[1:16][ingest][inbox][WARN] [1/2] a.csv: failed"]
