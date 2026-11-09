# Safety and privacy

How the public repo stays free of household data, what the package is allowed to do, and where the data lives.

## Rules for this public repo

- No household data: no account details, amounts, names, Drive or Sheet IDs, or exports.
- Test fixtures under `tests/fixtures/` are synthetic.
- Notebooks are never committed; they hold real output.
- `tools/leak_guard.py` enforces this in CI and as a pre-commit hook. A deliberate exception is marked on its line with `leak-guard: allow`.
- `tools/push_guard.py` runs before every push (see [Before pushing](#before-pushing)) and also checks against a private list of the household's real values.

## What the package can do

It runs in Colab under the owner's Google sign-in, so its reach is limited by construction:

- `tools/import_guard.py` (CI and pre-commit) allows only a short list of imports: pure standard-library modules, pandas, numpy, scikit-learn (the categorization model), pypdf, and Google's Sheets, Drive and Colab clients. No sockets, HTTP libraries, subprocesses, `os`, `eval`/`exec` or dynamic imports. Widening the list is a visible change to that file. It is an import policy that keeps changes reviewable, not a sandbox: the allowed libraries (and the Google clients' access, which is the signed-in owner's) can do whatever they do, so the boundary is reviewing a release before the notebook pins it.
- pypdf, the one library a release may download, is pinned to an exact version. pandas, numpy, scikit-learn and Google's clients come from the Colab image, bounded in `pyproject.toml` below the next major version the code hasn't been tested with (pip installs a version inside the bound if the image moves past it), and every ingest and compute logs the versions it ran with (`engine.versions`, also a `versions` row in `run_log`).
- Only the owner creates release tags, and notebooks install a tag (the newest unless one is pinned), so nothing reaches Colab without the owner reviewing the diff (`/compare/<old tag>...<new tag>` on GitHub).
- CI's token can only read the repo, and every action it uses is pinned to a commit, not a tag that could be moved (`tests/test_ci.py` holds both). To update an action, look up the commit its new tag points to (`git ls-remote --tags https://github.com/actions/<name>`).

## Where the data lives, and for how long

Everything is in the owner's Google Drive, under the household folder, and nothing about the household is in this repo or the scripts. Who can see it is decided by Drive sharing, not by this code: share that folder only with the people who keep the books, and look at its sharing now and then. Each part, and how long it stays:

- `inbox`: files waiting to be read. Read files leave it; a refused one stays, with its reason, until it is fixed or deleted.
- `record_store`: every file ingest read, filed under a descriptive name and kept for good, so the ledger can be rebuilt from it.
- The ledger and Reports: the ledger keeps every row; a correction is a new row, so an earlier value stays in the sheet. Reports is rebuilt by every compute.
- `backups`: one zip of every ledger tab per ingest, kept for good. Each holds everything the ledger held then, so it is as sensitive as the ledger, and a value corrected since is still in the older zips.
- Drive's trash: the Amazon and Google exports go there once their trimmed copies are filed (the originals hold addresses and more). Drive empties its trash after 30 days; until then a file can be restored. Empty the trash to remove them sooner.
- Gmail: the receipts the exporter reads stay in Gmail as they were, and the weekly email puts the household's to-dos (balances, withholding, FSA money left) in the inbox it is sent to.

To remove something for good, it has to go from the ledger, from `record_store` and from every backup that holds it, by hand.

## Before pushing

This repo is public, so hooks check for household data before it can leave the machine. Binary files (PDFs, images, office files, archives) are refused outright unless `tests/fixtures/BINARIES` lists them as made-up fixtures, and a listed PDF's text is checked like any other file: real statements and exports are binary, and line checks can't see inside them. Turn them on once per clone with `git config core.hooksPath .githooks`. The pre-commit and commit-msg hooks run the text checks below on the staged changes and the message (`push_guard.py --staged`, `--message`), so a value is caught before it is in a commit; the pre-push hook runs everything again as the backstop.

`tools/push_guard.py` checks every added line and commit message against the leak guard's patterns and against a private denylist of the household's real values (names, places, employers, account digits, and every amount with cents in the pay stubs and transactions, however it is written), kept outside the repo at `~/.config/household/denylist.txt`; then it runs the import guard, Ruff, mypy and the tests. `tools/denylist.py` builds the denylist from ledger exports and a words file, and adds every category in a personal group (`personal`, `family_support`), since those are named for people. Examples and tests use made-up names and categories only (CLAUDE.md lists them); a GitHub address is not checked, since it is public. Without a denylist the push stops (`HOUSEHOLD_NO_DENYLIST=1` skips that check; `HOUSEHOLD_PUSH_QUICK=1` skips the tests).
