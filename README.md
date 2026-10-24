# household

Automation for household finances: ingest, compute and views over a ledger kept in Google Sheets.

The package holds logic only. All data lives in the owner's Google Drive. Notebooks in Drive install a pinned release of this package and run it under the user's own Google sign-in; nothing here stores data or credentials.

## Rules for this public repo

- No household data: no account details, amounts, names, Drive or Sheet IDs, or exports.
- Test fixtures under `tests/fixtures/` are synthetic.
- Notebooks are never committed; they hold real output.
- `tools/leak_guard.py` enforces this in CI and as a pre-commit hook. A deliberate exception is marked on its line with `leak-guard: allow`.

## What the package can do

It runs in Colab under the owner's Google sign-in, so its reach is limited by construction:

- `tools/import_guard.py` (CI and pre-commit) allows only a short list of imports: pure standard-library modules, pandas, pypdf, and Google's Sheets, Drive and Colab clients. No sockets, HTTP libraries, subprocesses, `os`, `eval`/`exec` or dynamic imports. Widening the list is a visible change to that file.
- pypdf, the one library a release may download, is pinned to an exact version. pandas and Google's clients come from the Colab image.
- Only the owner creates release tags, and notebooks install a tag, so nothing reaches Colab without the owner reviewing the diff (`/compare/<old tag>...<new tag>` on GitHub).

## Inbox

Files dropped in the Drive `inbox` folder are identified, checked and filed by `engine.ingest_inbox`: pay stubs in two layouts, the Schwab equity export, the accounts seed, tax returns and W-4s (filed for the record, no rows), and the IRS publications, revenue procedures, notices and public laws the tax code cites (filed under stable names such as `publication-15-t-2026`, no rows). Every parser checks the document against its own printed totals; a file that fails stays in the inbox with the reason, and nothing is written. Re-sent files, and re-downloads whose rows are all already present, go to `record_store/duplicates`; a partially overlapping export adds only its new rows and names the earlier file it overlapped. Compute lists likely double entries (same account and amount, a few days apart, from different files) in the `possible_duplicates` report; a `void` correction takes a confirmed one off the books. See `src/household/inbox.py` and `compute.possible_duplicates`.

## Development

```
pip install -e ".[dev]"
pytest
python tools/leak_guard.py
```

Optional local hook: `pip install pre-commit && pre-commit install`.

## Tax law

Federal and Wisconsin brackets, limits and withholding tables live in `src/household/taxdata.py`; the Wisconsin return (Form 1 and part-year Form 1NPR) and Pub W-166 withholding are in `src/household/wisconsin.py`. Every value cites the page of a source document that states it (`CITATIONS`), and each source is pinned by the SHA-256 of the PDF it was checked against (`DOCUMENTS`). The `tax_sources` report lists every value with its citation and a link to the PDF in the record store.

A release that changes tax law (each January) adds the new year's values and citations, then checks them against the PDFs:

```
python tools/verify_tax_law.py <folder of source PDFs>
```

It checks that each quote is on its cited page and states the value the code uses, that every bracket threshold appears, and that every row of Pub 15-T's annual married-filing-jointly table equals the brackets shifted by the table floor. Wisconsin prints no rate schedule or standard deduction formula, so those are proved by reproducing every row of the Form 1 tax table, standard deduction table and homeowner's credit table, and the amounts printed in the tax computation worksheet and Pub W-166. It exits nonzero on any failure, missing document, or uncited value.

## Releases

Notebooks pin a tag, for example `pip install git+https://github.com/lewis-john-lloyd/household@v0.0.1`. Upgrading is a deliberate edit of that tag; rolling back is reverting it.
