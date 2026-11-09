"""Whole outputs pinned on made-up data, so a refactor that should change nothing changes nothing.

Each test runs a whole stage and compares a digest of every table it writes with the one in tests/golden.json.
Digests rather than the tables themselves: the made-up figures stay out of the repo's history, and no amount
in them can look like a household value to the push guard. A change that is meant to change an output updates
the digests: run `HOUSEHOLD_GOLDEN=update pytest tests/test_golden.py`, then say in the commit what changed.

Tables are compared as text: numbers rounded to the cent, rows sorted, and the columns that record when or how
fast a run went (run ids, timings, times of day) left out.
"""

import hashlib
import json
import os
import string
from datetime import date
from pathlib import Path

import pandas as pd
from freezegun import freeze_time

from household import engine
from household import retirement as R
from test_engine import FakeSession, drop, session  # noqa: F401  (the fixture)
from test_inbox import FakeDrive  # noqa: F401
from test_retirement import household, params, person

GOLDEN = Path(__file__).parent / "golden.json"
VOLATILE = {
    "run_id",
    "computed_at",
    "seconds",
    "at",
    "started_at",
    "finished_at",
    "recorded_at",
    "received_at",
    "elapsed",
    "took",
    "drive_link",
    "doc_id",
    "sha256",
}


def digest(df):
    """A table as stable text: volatile columns out, numbers to the cent, rows sorted."""
    df = pd.DataFrame(df).copy()
    df.columns = [str(c) for c in df.columns]
    df = df[[c for c in df.columns if c not in VOLATILE]]
    for c in df.columns:
        n = pd.to_numeric(df[c], errors="coerce")
        if n.notna().all() and len(df):
            df[c] = n.round(2).map(lambda v: f"{v:.2f}")
        else:
            df[c] = df[c].astype(str)
    lines = sorted("\t".join(r) for r in df.astype(str).values.tolist())
    text = "\t".join(df.columns) + "\n" + "\n".join(lines)
    # letters only (hex with its digits moved to g to p), so no digest reads as an account number to the guards
    return hashlib.sha256(text.encode()).hexdigest()[:16].translate(str.maketrans(string.digits, "ghijklmnop"))


def check(name, tables):
    got = {k: digest(v) for k, v in sorted(tables.items())}
    pinned = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if os.environ.get("HOUSEHOLD_GOLDEN") == "update":
        pinned[name] = got
        GOLDEN.write_text(json.dumps(pinned, indent=1, sort_keys=True) + "\n")
        return
    assert name in pinned, f"no digests for {name}: run with HOUSEHOLD_GOLDEN=update"
    changed = sorted(k for k in set(got) | set(pinned[name]) if got.get(k) != pinned[name].get(k))
    assert not changed, f"{name}: these tables changed: {', '.join(changed)}"


def test_the_retirement_plan_is_unchanged():
    h = household(
        monte_carlo_runs=100,
        plan_to_age=85,
        people=[person(born=date(1966, 6, 1), first=1990), person("sam", date(1968, 2, 1), 30_000, 1992)],
    )
    years, table, plan, ss, earned, notes = R.run(h, params(), "2026-11-01")
    path, rows = R.spending_path(
        {"food": 10.0, "camps": 2.0},
        {"food": "food", "camps": "kids"},
        range(2030, 2041),
        {"kids": [(2035, 0.0)]},
        {"acme_loan": (2037, 3.0)},
    )
    check(
        "retirement",
        {
            "years": years,
            "strategies": table,
            "plan": plan,
            "social_security": ss,
            "earnings": earned,
            "notes": pd.DataFrame({"note": notes}),
            "spending_path": pd.DataFrame(sorted(path.items())),
            "spending_rows": pd.DataFrame(rows),
        },
    )


@freeze_time("2026-11-09 12:00:00")
def test_every_report_from_ingest_and_compute_is_unchanged(session):  # noqa: F811
    drop(session, "pay_statement.pdf", "application/pdf")
    drop(session, "schwab_equity.csv", "text/csv")
    drop(session, "accounts_seed.csv", "text/csv")
    drop(session, "return_facts.csv", "text/csv")
    engine.ingest_inbox(session, session.root, run_id="golden-1")
    engine.ingest_legacy_year(session, session.root, "legacy-key", 2001, run_id="golden-2")
    engine.compute_all(session, session.root, "legacy-key", legacy_year=2001, run_id="golden-3")
    files = session.sheets.files
    tables = {}
    for book in ("ledger", "reports"):
        for name, tab in files[book].tabs.items():
            values = tab.rows
            if name in ("run_log", "run_notes", "run_lines"):
                continue  # what a run printed: its wording is free to change
            if not values:
                tables[f"{book}.{name}"] = pd.DataFrame()
                continue
            tables[f"{book}.{name}"] = pd.DataFrame(values[1:], columns=values[0])
    check("ingest_and_compute", tables)


# a fuller household, made up (no fund has a quote, so ingest asks Sheets for no prices and doesn't wait): a bank account with a month of lines, an IRA and a Roth with activity and
# positions, a workplace plan, categories, payee rules, a budget line and the settings the retirement plan needs
GOLDEN_FILES = {
    "accounts_seed_golden.csv": """account_id,institution,owner,type,tax_treatment,opened,closed,last4,expected_docs,max_gap_days
checking,Example Bank,joint,checking,none,,,1234,monthly_statement,45
a_ira,Example Broker,alex,retirement,pre_tax,,,3333,quarterly_statement,100
a_roth,Example Broker,alex,retirement,roth,,,1111,quarterly_statement,100
s_plan,Example Broker,sam,workplace_plan,pre_tax,,,2222,quarterly_statement,100
""",
    "categories_seed_golden.csv": """category,group,priority
food,living,high
household,living,medium
hobby,fun,low
pay,income,high
""",
    "payee_rules_seed_golden.csv": """pattern,category,precedence,effective,ask
ACME GROCER,food,,2000-01-01,FALSE
PAGE TURNER BOOKS,hobby,,2000-01-01,FALSE
ACME WIDGETS PAYROLL,pay,,2000-01-01,FALSE
""",
    "budget_rules_seed_golden.csv": """line,effective,category,account,priority,rule,amount,per,note
food,2001-01-01,food,checking,high,fixed,90,monthly,
""",
    "parameters_seed_golden.csv": """year,jurisdiction,key,value,source
,household,household_adults,"alex,sam",seed
,household,birth_date:alex,1970-06-01,seed
,household,birth_date:sam,1972-02-01,seed
,household,retirement_monte_carlo_runs,40,seed
,household,retirement_plan_to_age,80,seed
""",
    "securities_seed_golden.csv": """symbol,name,asset_class,expense_ratio,quote,wash_group
ACMEX,Acme Widgets Index Fund,SDT,0.001,,
SPAXX,Money market,BA,,,
OJU1,S&P 500 index (plan),SDT,,,
""",
}


def golden_ofx():
    from test_bank import sgml

    return sgml(
        [
            ("DEBIT", (1, 5), "-12.50", "G1", "ACME GROCER"),
            ("CREDIT", (1, 9), "90.00", "G2", "ACME WIDGETS PAYROLL"),
            ("DEBIT", (1, 12), "-20.00", "G3", "PAGE TURNER BOOKS"),
            ("DEBIT", (1, 20), "-7.50", "G4", "WILLOW ST"),
        ],
        acct="99-1234",
    )


@freeze_time("2026-11-09 12:00:00")
def test_a_fuller_household_through_ingest_and_compute_is_unchanged(session):  # noqa: F811
    from test_fidelity import IRA
    from test_fidelity_positions import FILE as POSITIONS

    for name, text in GOLDEN_FILES.items():
        session._drive.add(name, session.box, "text/csv", text.encode())
    session._drive.add("history_a_ira.csv", session.box, "text/csv", IRA.encode("utf-8"))
    session._drive.add("portfolio_positions.csv", session.box, "text/csv", POSITIONS.encode())
    session._drive.add("checking.qfx", session.box, "application/octet-stream", golden_ofx().encode())
    first = engine.ingest_inbox(session, session.root, run_id="golden-1")
    assert (
        "refused" not in first["counts"] and "failed" not in first["counts"] and "unrecognized" not in first["counts"]
    ), first
    engine.compute_all(session, session.root, run_id="golden-2")
    tables = {}
    for book in ("ledger", "reports"):
        for name, tab in session.sheets.files[book].tabs.items():
            if name in ("run_log", "run_notes", "run_lines"):
                continue
            values = tab.rows
            tables[f"{book}.{name}"] = pd.DataFrame(values[1:], columns=values[0]) if values else pd.DataFrame()
    filled = sum(1 for v in tables.values() if len(v))
    assert filled >= 30, f"only {filled} tables have rows: the scenario no longer reaches most of compute"
    check("fuller_household", tables)


@freeze_time("2026-11-09 12:00:00")
def test_a_year_of_pay_stubs_through_the_projection_is_unchanged(session):  # noqa: F811
    """This year's stubs reach the tax projection, the withholding plan, the elections and the backtests."""
    from household import schema, sheets
    from test_projection import stubs_through

    for name in ("accounts_seed_golden.csv", "categories_seed_golden.csv", "parameters_seed_golden.csv"):
        session._drive.add(name, session.box, "text/csv", GOLDEN_FILES[name].encode())
    session._drive.add("checking.qfx", session.box, "application/octet-stream", golden_ofx().encode())
    engine.ingest_inbox(session, session.root, run_id="golden-1")
    ledger = session.sheets.files["ledger"]
    stubs = stubs_through(20).astype(str)
    sheets.append_tables(ledger, [(schema.table(schema.LEDGER, "paystubs"), stubs)])
    engine.compute_all(session, session.root, run_id="golden-2")
    tables = {}
    for name, tab in session.sheets.files["reports"].tabs.items():
        if name in ("run_log", "run_notes", "run_lines"):
            continue
        values = tab.rows
        tables[f"reports.{name}"] = pd.DataFrame(values[1:], columns=values[0]) if values else pd.DataFrame()
    for name in ("projection", "tax_lines", "withholding_plan", "elections", "withholding_backtest"):
        assert len(tables[f"reports.{name}"]), f"{name} is empty: the stubs no longer reach it"
    check("stubs_and_projection", tables)
