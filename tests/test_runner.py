"""One call runs every stage, the legacy workbooks named in a settings file in Drive."""

import json

import pytest

from household import engine, runner

SETTINGS = {
    "version": 1,
    "legacy_investments": {"workbook_id": "a" * 30, "tabs": {"alex-ira": "a_ira", "alex-plan": "a_plan:roth"},
                           "net_worth_columns": {"Willow - V": ["value", "willow"], "Willow - M": ["loan", "acme_loan"]}},
    "legacy_budget": {"workbook_id": "b" * 30, "check_year": 2001},
    "legacy_equity": {"workbook_id": "c" * 30, "grant_columns": {"2000": "G1"}, "grants_tab": "grants",
                      "sales_tab": "sales"},
}


def test_the_settings_are_checked_part_by_part():
    got = runner.parse_settings(json.dumps(SETTINGS))
    assert got["legacy_investments"]["net_worth_columns"]["Willow - M"] == ("loan", "acme_loan")
    assert got["legacy_budget"] == {"workbook_id": "b" * 30, "check_year": 2001}
    assert runner.parse_settings(json.dumps({"version": 1})) == {}
    for bad, message in [({"version": 2}, "version 1"), ({**SETTINGS, "extra": {}}, "unknown parts"),
                         ({"version": 1, "legacy_budget": {"workbook_id": "../x"}}, "Drive file id"),
                         ({"version": 1, "legacy_equity": {**SETTINGS["legacy_equity"], "grants_tab": ""}}, "name the tabs"),
                         ({"version": 1, "legacy_investments": {**SETTINGS["legacy_investments"],
                                                                "net_worth_columns": {"x": ["equity", "y"]}}}, "value")]:
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

    monkeypatch.setattr(runner.inbox, "list_files", lambda drive, folder: [
        {"id": str(i), "name": n} for i, n in enumerate(files)])
    monkeypatch.setattr(runner.inbox, "download", lambda drive, file_id: json.dumps(SETTINGS).encode())
    for name in ("ingest_legacy_investments", "ingest_inbox", "ingest_legacy_net_worth", "ingest_legacy_equity",
                 "fetch_prices"):
        monkeypatch.setattr(engine, name, stage(name))
    monkeypatch.setattr(engine, "compute_all", stage("compute_all", {"checked": 3, "mismatches": 0}))
    return calls


def test_a_run_goes_through_every_stage_in_order_with_the_settings(monkeypatch):
    calls = stages(monkeypatch, ["ledger", runner.SETTINGS_NAME])
    said = []
    runner.run(Session(), "folder", log=said.append)
    assert [c[0] for c in calls] == ["ingest_legacy_investments", "ingest_inbox", "ingest_legacy_net_worth",
                                     "ingest_legacy_equity", "fetch_prices", "compute_all"]
    assert calls[3][1] == ("c" * 30, {"2000": "G1"}, "grants", "sales") and calls[5][1] == ("b" * 30, 2001)
    assert said[-1] == "done: ingest, prices and compute ran; legacy check PASS. The status page in the views says what needs you."


def test_a_legacy_stage_that_fails_says_why_and_the_rest_still_run(monkeypatch):
    calls = stages(monkeypatch, [runner.SETTINGS_NAME], fail=("ingest_legacy_investments",))
    said = []
    runner.run(Session(), "folder", log=said.append)
    assert said[0] == "legacy investments: not run: no such accounts in the ledger: a_ira (add them first)"
    assert "not run: legacy_investments" in said[-1] and calls[-1][0] == "compute_all"


def test_with_no_settings_file_only_the_inbox_prices_and_compute_run(monkeypatch):
    calls = stages(monkeypatch, ["ledger"])
    said = []
    runner.run(Session(), "folder", log=said.append)
    assert [c[0] for c in calls] == ["ingest_inbox", "fetch_prices", "compute_all"]
    assert said[0].startswith("no engine_settings.json in the household folder")
