"""The functions where one stage hands work to another are annotated, so their contracts are explicit and
mypy checks them (pyproject's [tool.mypy]; CI runs it). Inside a module, annotations are welcome, not required."""

import inspect

from household import books, engine, ledgerio, lots, parsers, runlog, runner, sheets, valuation
from household import retirement as R

BOUNDARIES = [
    runner.run,
    engine.bootstrap,
    engine.ingest_inbox,
    engine.compute_all,
    engine.review,
    engine.fetch_prices,
    engine.ingest_legacy_year,
    engine.ingest_legacy_investments,
    engine.ingest_legacy_equity,
    engine.ingest_legacy_net_worth,
    ledgerio.read_ledger,
    ledgerio.renamed_accounts,
    ledgerio.legacy_moves,
    ledgerio.activity_fixes,
    runlog.say,
    runlog.record_stage,
    books.read_books,
    sheets.read_table,
    sheets.append_tables,
    sheets.replace_table,
    sheets.ensure_tables,
    lots.build,
    valuation.values,
    R.build,
    R.run,
    R.budget_table,
    R.allocation_stock_share,
]


def unannotated(fn):
    sig = inspect.signature(fn)
    missing = [p for p, v in sig.parameters.items() if v.annotation is inspect.Parameter.empty]
    if sig.return_annotation is inspect.Signature.empty:
        missing.append("return")
    return missing


def test_every_stage_boundary_says_what_it_takes_and_returns():
    gaps = {f"{fn.__module__}.{fn.__name__}": unannotated(fn) for fn in BOUNDARIES}
    assert {k: v for k, v in gaps.items() if v} == {}


def test_every_parser_says_it_reads_text_and_returns_a_result():
    gaps = {p.NAME: unannotated(p.sniff) + unannotated(p.parse) for p in parsers.PARSERS}
    assert {k: v for k, v in gaps.items() if v} == {}
