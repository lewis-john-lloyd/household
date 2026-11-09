"""From the inbox to the page: made-up files go through ingest and compute, and the views' own page code
(web/dist, the built TypeScript) reads the Reports that come out, the way the web app does.

Each stage is tested on its own elsewhere; this catches a seam between them: a column renamed in Reports that
a page still reads by its old name, a table compute stops writing, a stage that no longer reaches the next.
It needs Node and the built web app (cd web && npm ci && npm run build), and is skipped without them; CI's
end-to-end job has both.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from freezegun import freeze_time

from household import engine
from test_engine import session  # noqa: F401  (the fixture)
from test_golden import GOLDEN_FILES, golden_ofx

ROOT = Path(__file__).parent.parent
DIST = ROOT / "web" / "dist"
pytestmark = pytest.mark.skipif(
    not (shutil.which("node") and (DIST / "wealth.js").exists()), reason="needs Node and the built web app"
)


def household(session):  # noqa: F811
    from test_fidelity import IRA
    from test_fidelity_positions import FILE as POSITIONS

    for name, text in GOLDEN_FILES.items():
        session._drive.add(name, session.box, "text/csv", text.encode())
    session._drive.add("history_a_ira.csv", session.box, "text/csv", IRA.encode("utf-8"))
    session._drive.add("portfolio_positions.csv", session.box, "text/csv", POSITIONS.encode())
    session._drive.add("checking.qfx", session.box, "application/octet-stream", golden_ofx().encode())
    with freeze_time("2026-11-09 12:00:00"):
        engine.ingest_inbox(session, session.root, run_id="e2e-1")
        engine.compute_all(session, session.root, run_id="e2e-2")
    return {name: tab.rows for name, tab in session.sheets.files["reports"].tabs.items()}


def pages(tabs):
    done = subprocess.run(
        ["node", str(ROOT / "tests" / "views_from_reports.js")],
        input=json.dumps(tabs),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_inbox_reaches_every_page(session):  # noqa: F811
    p = pages(household(session))

    # Cashflow: the month's money from pay into food and the hobby
    assert p["flows"]["periods"] and p["flows"]["rows"]
    names = {n for r in p["flows"]["rows"] for n in (r[0], r[1])}
    assert any("fun" in n for n in names) and any("living" in n for n in names)

    # every line from the bank download, categorized by the payee rules
    assert len(p["transactions"]) == 4
    assert {t["category"] for t in p["transactions"]} >= {"food", "hobby", "pay"}

    # Budget: the food line from the seed
    assert any(t["category"] == "food" and t["target"] == 90 for t in p["budget"]["targets"])

    # Investments: the positions, the IRA's activity, and a value today
    inv = p["investments"]
    assert round(inv["total"]) == 104
    assert {a["account"] for a in inv["accounts"]} >= {"a_roth", "s_plan"}
    assert inv["lines"] and inv["history"]

    # Retirement: a plan for both adults, and the budget it spends from
    ret = p["retirement"]
    assert ret["chosenYear"] and ret["strategies"] and ret["years"]
    assert {b["line"] for b in ret["budget"]} >= {"living", "fun"}

    # Status: the latest ingest and compute runs
    assert {s["stage"] for s in p["status"]["stages"]} >= {"ingest", "compute"}

    # Taxes and Form 8949 read without error (no pay stubs or sales in this household)
    assert p["form8949"] == {"years": []}
    assert "years" in p["taxes"]
