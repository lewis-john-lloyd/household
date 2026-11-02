# Working in this repo

This repo is public. Household data never goes in it: no account numbers (not even last four digits), amounts,
names, addresses, employers, Drive or Sheet IDs. Fixtures and examples are made up.

## Made-up vocabulary

Examples, tests, docstrings and commit messages use only these, even when the example is about the
household's real situation. Never copy a name, category, merchant or amount from the ledger, a report, a
PDF, a log or the conversation.

- People: `alex`, `sam` (and `partner`).
- Categories: the generic ones (`food`, `household`, `media`, `petrol`, `family`, `misc`, `transfer`) and
  `kids`, `hobby`, `gifts`. Never a category named for a person, and never the household's own category
  list.
- Employers and merchants: `Acme Widgets`, `ACME GROCER`, `BLUE PUMP FUEL`, `PAGE TURNER BOOKS`,
  `QUILL & INK`, `WILLOW ST`.
- Amounts: under 100, or round.

## Before every push

1. The hooks must be on: `git config core.hooksPath .githooks` (pre-commit and commit-msg check each commit,
   pre-push checks the push). Never commit or push with `--no-verify`.
2. The denylist must exist at `~/.config/household/denylist.txt`. In a fresh session, build it before the
   first push:
   - download `push-denylist-words.txt` from the household Drive folder (names, places, employers,
     account digits);
   - read the ledger's `paystubs`, `transactions` and `categories` tabs through the Sheets connector into
     JSON files (the shape the connector returns, `{"values": [...]}`), outside the repo;
   - `python tools/denylist.py --words push-denylist-words.txt --categories categories.json paystubs.json transactions.json > ~/.config/household/denylist.txt`.
3. When the push guard reports a household value, change the value to a made-up one and amend the commit.
   A real value is never marked `leak-guard: allow`. When it finds a word that belongs on the list and
   isn't there, add it to the Drive words file.
4. Real figures seen while debugging (from a PDF, a Sheet, a log) stay out of comments, docstrings, tests
   and commit messages.

## Releases

Ordinary commits leave the version alone. A release is its own commit that sets the version in
`pyproject.toml` and `src/household/__init__.py`; the owner tags it.

## Style

No em dashes in code, docs or messages.
