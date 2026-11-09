# Development, tax law and releases

Working on the code.

## Development

```
pip install -e ".[dev]"
pytest
python -m ruff check src tests tools
python -m ruff format src tests tools
python -m mypy
python tools/leak_guard.py
```

Type annotations are gradual: the functions where one stage hands work to another are annotated (`tests/test_typed.py` keeps them so, with the types they share in `kinds.py`), and mypy checks every annotated function (`[tool.mypy]` in pyproject). The money modules (`lots`, `allocation`, `tax`, `retirement`) are typed in full: mypy rejects an unannotated function there (decision 8). Ruff checks for real errors and likely bugs (pyflakes and bugbear) and keeps every file in one format (120 columns); CI and the push guard run both. A formatting-only commit goes in `.git-blame-ignore-revs` (`git config blame.ignoreRevsFile .git-blame-ignore-revs` once per clone). `tests/test_golden.py` pins whole outputs on made-up data (digests in `tests/golden.json`): a refactor must leave them alone, and a change meant to change an output re-pins them with `HOUSEHOLD_GOLDEN=update pytest tests/test_golden.py`.

The engine notebook calls `runner.run`, which calls the stages through `engine`: `stage_legacy` (the old workbooks into the ledger), `stage_ingest` (the inbox, review answers and prices; the only writer of the ledger) and `stage_compute` (Reports from the ledger). `ledgerio` opens the two spreadsheets and reads ledger tables; compute reads through a `LedgerView`, the ledger with a `LedgerReading` attached (renamed accounts, moved readings, corrected activity), worked out once from the ledger and passed along explicitly, `books` gives both stages the transactions as they now stand, and `runlog` keeps what each stage prints for the Status page.

Optional local hooks: `pip install pre-commit && pre-commit install`, and `git config core.hooksPath .githooks` for the push guard.

## Tax law

Federal and state rates, limits and withholding tables live in `src/household/taxdata.py`; each state's return and withholding method is in a module of its own. Every value cites the page of a source document that states it (`CITATIONS`), and each source is pinned by the SHA-256 of the PDF it was checked against (`DOCUMENTS`). The `tax_sources` report lists every value with its citation and a link to the PDF in the record store.

A release that changes tax law (each January) adds the new year's values and citations, then checks them against the PDFs:

```
python tools/verify_tax_law.py <folder of source PDFs>
```

It checks that each quote is on its cited page and states the value the code uses, that every bracket threshold appears, and that every row of Pub 15-T's annual married-filing-jointly table equals the brackets shifted by the table floor. Where a state prints no rate schedule or deduction formula, those are proved by reproducing every row of its printed tables and the amounts in its worksheets. It exits nonzero on any failure, missing document, or uncited value.

## Releases

The engine notebook installs a release tag: by default the newest one (`git ls-remote --tags`), so tagging a release is what upgrades it, and only the owner tags. Setting its `RELEASE` to a tag (`"v0.0.1"`) pins that one instead, which is how to roll back. Either way it installs `git+https://github.com/lewis-john-lloyd/household@<tag>` and prints the tag it installed.

The notebook holds only the household folder's id. It calls `runner.run(session, folder_id)`, which runs every stage in order: the legacy investments workbook, ingest, the legacy Net Worth tab, the legacy stock plan tabs, prices and compute. The old workbooks and how their tabs map onto the ledger come from `engine_settings.json` in the household folder (see `runner.py` for its parts; each is optional, and a part left out skips its stage), so a new legacy import needs a settings change, not a notebook change. A legacy stage that fails says why and the run goes on.

Ordinary commits leave the version number alone. When work is ready to run, one release commit sets the version in `pyproject.toml` and `household/__init__.py`, and the owner tags that commit.
