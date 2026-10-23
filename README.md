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

Files dropped in the Drive `inbox` folder are identified, checked and filed by `engine.ingest_inbox`: pay stubs in two layouts, the Schwab equity export, the accounts seed, and tax returns and W-4s (filed for the record, no rows). Every parser checks the document against its own printed totals; a file that fails stays in the inbox with the reason, and nothing is written. Re-sent files, and re-downloads whose rows are all already present, go to `record_store/duplicates`; a partially overlapping export adds only its new rows and names the earlier file it overlapped. Compute lists likely double entries (same account and amount, a few days apart, from different files) in the `possible_duplicates` report; a `void` correction takes a confirmed one off the books. See `src/household/inbox.py` and `compute.possible_duplicates`.

## Development

```
pip install -e ".[dev]"
pytest
python tools/leak_guard.py
```

Optional local hook: `pip install pre-commit && pre-commit install`.

## Releases

Notebooks pin a tag, for example `pip install git+https://github.com/lewis-john-lloyd/household@v0.0.1`. Upgrading is a deliberate edit of that tag; rolling back is reverting it.
