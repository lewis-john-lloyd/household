# household

Automation for household finances: ingest, compute and views over a ledger kept in Google Sheets.

The package holds logic only. All data lives in the owner's Google Drive. Notebooks in Drive install a pinned release of this package and run it under the user's own Google sign-in; nothing here stores data or credentials.

## Rules for this public repo

- No household data: no account details, amounts, names, Drive or Sheet IDs, or exports.
- Test fixtures under `tests/fixtures/` are synthetic.
- Notebooks are never committed; they hold real output.
- `tools/leak_guard.py` enforces this in CI and as a pre-commit hook. A deliberate exception is marked on its line with `leak-guard: allow`.

## Inbox

Files dropped in the Drive `inbox` folder are identified, checked and filed by `engine.ingest_inbox`: pay stubs in two layouts, the Schwab equity export, the accounts seed, and tax returns and W-4s (filed for the record, no rows). Every parser checks the document against its own printed totals; a file that fails stays in the inbox with the reason, and nothing is written. See `src/household/inbox.py`.

## Development

```
pip install -e ".[dev]"
pytest
python tools/leak_guard.py
```

Optional local hook: `pip install pre-commit && pre-commit install`.

## Releases

Notebooks pin a tag, for example `pip install git+https://github.com/lewis-john-lloyd/household@v0.0.1`. Upgrading is a deliberate edit of that tag; rolling back is reverting it.
