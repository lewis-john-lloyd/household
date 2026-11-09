# Development, tax law and releases

Working on the code.

## Development

```
pip install -e ".[dev]"
pytest
python tools/leak_guard.py
```

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
