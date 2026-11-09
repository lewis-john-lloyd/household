# household

Automation for household finances: ingest, compute and views over a ledger kept in Google Sheets.

The package holds logic only. All data lives in the owner's Google Drive. Notebooks in Drive install a pinned release of this package and run it under the user's own Google sign-in; nothing here stores data or credentials.

## How it works

1. **Ingest** reads what is dropped in the Drive `inbox` (bank and card downloads, pay stubs, investment downloads and statements, order histories, receipts, the views' change sets) and appends it to the **ledger**, a Google Sheet that only ingest writes.
2. **Compute** rebuilds **Reports** from the ledger: categorized spending, the budget, investments and tax lots, the tax projection, the mortgage, net worth and the retirement plan.
3. The **views**, an Apps Script web app, draw Reports and edit the ledger by writing change sets for ingest to read.
4. Small Apps Scripts beside it export receipts from Gmail, send a weekly email of what needs attention, and check the county's assessments.

## Rules for this public repo

- No household data: no account details, amounts, names, Drive or Sheet IDs, or exports. Fixtures are made up.
- Notebooks are never committed; they hold real output.
- `tools/leak_guard.py` (CI and pre-commit) and `tools/push_guard.py` (before every push, against a private denylist) enforce this. See [docs/safety.md](docs/safety.md).

## Development

```
pip install -e ".[dev]"
pytest
python -m ruff check src tests tools && python -m ruff format --check src tests tools
python -m mypy
python tools/leak_guard.py
git config core.hooksPath .githooks
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

The web app's checks run in `web/`: `npm run format:check`, `npm run typecheck`, `npm test`, `npm run test:server` and `npm run test:browser`. `tests/test_end_to_end.py` needs the web app built (`npm run build`).

## Documentation

[docs/](docs/README.md), grouped by reader:

- **Using it:** [the inbox](docs/inbox.md), [investments](docs/investments.md), [categories, budget and the monthly checklist](docs/budget.md), [mortgage, retirement and pay history](docs/planning.md), [the views](docs/views.md), [receipts, the weekly email and the county check](docs/apps-scripts.md)
- **How it works:** [how it fits together](docs/architecture.md) (with diagrams), [decisions](docs/decisions.md), [safety and privacy](docs/safety.md)
- **Working on it:** [development, tax law and releases](docs/development.md)
