# Working in this repo

This repo is public. Household data never goes in it: no account numbers (not even last four digits), amounts,
names, addresses, employers, Drive or Sheet IDs. Fixtures and examples are made up.

## Before every push

1. The pre-push hook must be on: `git config core.hooksPath .githooks`. Never push with `--no-verify`.
2. The denylist must exist at `~/.config/household/denylist.txt`. In a fresh session, build it before the
   first push:
   - download `push-denylist-words.txt` from the household Drive folder (names, places, employers,
     account digits);
   - read the ledger's `paystubs` and `transactions` tabs through the Sheets connector into JSON files
     (the shape the connector returns, `{"values": [...]}`), outside the repo;
   - `python tools/denylist.py --words push-denylist-words.txt paystubs.json transactions.json > ~/.config/household/denylist.txt`.
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
