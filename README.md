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
python tools/leak_guard.py
git config core.hooksPath .githooks
```

The web app's tests run with `npm test`, `npm run test:server` and `npm run test:browser` in `web/`.

## Documentation

- [Safety and privacy](docs/safety.md): the repo rules, what the package may import, where the data lives and for how long, the push checks
- [The inbox](docs/inbox.md): how each kind of file is identified, checked and filed
- [Investments](docs/investments.md): investment downloads and statements, holdings and prices, filling history between readings, tax lots and wash sales
- [Categories, budget and the monthly checklist](docs/budget.md)
- [Mortgage, retirement and pay history](docs/planning.md)
- [Receipts, the weekly email and the county check](docs/apps-scripts.md): the Apps Scripts and how to set them up
- [Views](docs/views.md): the web app, editing through change sets, and how to deploy it
- [Development, tax law and releases](docs/development.md)
