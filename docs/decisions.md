# Decisions

Why the system is built the way it is, for the choices the code alone doesn't explain. Each says what was
decided, why, and what it costs. A new decision goes at the end; one that is reversed stays, marked as
replaced, with a pointer to the one that replaced it.

## 1. The ledger is a Google Sheet

**Decided.** The household's records live in a Google Sheet (the ledger), and every derived table in a second
one (Reports), both in the owner's Drive.

**Why.** The data stays in an account the household already controls and backs up, under its own sign-in
and Drive sharing; nothing runs a server or holds credentials. A person can open the ledger and read any row,
or fix a seed file, without this code. Sheets also evaluates GOOGLEFINANCE, which is how the engine gets
prices without network access of its own.

**Cost.** Sheets is slow and rate-limited (the views batch their reads and wait out quota errors), has no
transactions beyond one batch write, and keeps everything as text, so every reader parses columns
(`schema.py` declares them, and every table is read through it).

## 2. Only ingest writes the ledger

**Decided.** Ingest is the one writer of the ledger, and it only appends. Compute only reads the ledger and
replaces Reports (`sheets.replace_table` refuses any other spreadsheet). The views never write either.

**Why.** One writer means one place to check what goes in, one backup before each write, and a ledger that
can always be rebuilt from `record_store`. Append-only means a correction never erases what it corrects: the
earlier value stays as a row, and the later one is in force.

**Cost.** Every correction needs a way to be expressed as a new row: `txn_corrections`,
`activity_corrections`, settings that rename or move accounts. A wrong row can't simply be fixed in place.

## 3. The views edit through change sets

**Decided.** An edit made in the views is saved as a change set, a JSON file, into a Drive folder in the
inbox. Ingest reads it like any other file: checked, then appended, or refused whole with its reasons.

**Why.** It keeps decision 2: one writer, one set of checks, one record (`source_documents`) of where every
row came from. The web app needs only `drive.file` permission, not access to the ledger. A change set that
fails stays visible with every reason instead of half-applying.

**Cost.** An edit appears on the pages only after the next ingest and compute. Saving safely over an
unreliable connection takes care (an id per save, `findSave`, remembered discards; see
[views.md](views.md#saving-safely)).

## 4. Every file read is kept

**Decided.** `record_store` keeps every file ingest read, for good, under a descriptive name, and a backup
zip of the ledger is made before every ingest.

**Why.** The ledger can be rebuilt from the documents, a figure can be traced to the page it came from, and
a parser fixed later can read old files again.

**Cost.** The documents are as sensitive as the ledger, and removing something for good means removing it
from the ledger, `record_store` and every backup by hand ([safety.md](safety.md)).

## 5. Filed and computed taxes are kept apart

**Decided.** A tax year has the return as filed (`return_facts`, kind `filed`) and the return as computed
from pay stubs, vests and sales, shown side by side. Tax law values each cite the page of a source PDF,
pinned by its hash.

**Why.** The filed return is what the IRS has; the computed one shows whether the data explains it.
Keeping them apart turns every difference into a question to answer rather than a number silently
overwritten. Citations make each rate and limit checkable against the law, every year.

**Cost.** Two numbers for one line, and a yearly round of adding the new year's values and checking them
(`tools/verify_tax_law.py`).

## 6. The package's imports are an allowlist

**Decided.** `tools/import_guard.py` allows only pure standard-library modules, pandas, numpy,
scikit-learn, scipy's optimizer, pypdf and Google's clients. No network, subprocess or `os`.

**Why.** The engine runs with the owner's Google access, so what it may do should be visible at a glance;
widening the list is a reviewed change to one file. It is a policy that keeps changes reviewable, not a
sandbox.

**Cost.** Some conveniences are out of reach (no HTTP: prices come through Sheets).

## 7. Formatting and linting by tool

**Decided.** Ruff checks Python for errors and likely bugs (pyflakes and bugbear) and formats it at 120
columns; Prettier formats the web app's TypeScript and tests at 130. CI and the push guard check both.
Formatting-only commits are listed in `.git-blame-ignore-revs`. The page's HTML is left as written; its script
lives in `web/src/page` as TypeScript, formatted like the rest.

**Why.** Reviews look at logic, not layout, and a formatter settles every layout question the same way.

**Cost.** One large commit per formatter, which blame is told to look past.

## 8. Gradual typing: the boundaries, and the money calculations in full

**Decided.** The functions where one stage hands work to another are annotated, with the shared types in
`kinds.py`; `tests/test_typed.py` keeps them annotated, and mypy checks every annotated function. The modules
that do the money arithmetic (`lots`, `allocation`, `tax` and `retirement`) are typed in full: mypy refuses an
unannotated function there and checks every body (`[[tool.mypy.overrides]]` in pyproject). Their state has
named shapes rather than dicts of mixed values: the simulation's `Accounts`, `Year`, `Lifetime` and
`Outcome`, and the retirement inputs' `_Held`, `_Saved` and `_Spent`. Elsewhere, annotations are welcome but
not required. pandas frames are typed as frames, not by their columns.

**Why.** The contracts between stages are where a mistake spreads furthest, and they are few. The money
modules are where a wrong type turns into a wrong number without an error. Annotating every helper elsewhere
would add more noise than safety, since most of the code passes frames whose columns `schema.py` already
declares.

**Cost.** A frame's columns are not checked by mypy; the schema, the tests and the pinned outputs check them.
A new helper in a money module needs its types before it passes.

## 9. TypeScript's unchecked index access stays off

**Decided.** The web app compiles with `strict` and `exactOptionalPropertyTypes`, but not
`noUncheckedIndexedAccess`. That holds for the page's own code too (`web/src/page`, `tsconfig.page.json`),
which is checked against the server's types.

**Why.** Turning it on gave 190 errors, nearly all from reading a Reports row by its column name, where the
code already falls back for a missing cell (an empty string, zero, or null). Fixing them would add a fallback
to almost every line that reads a row without catching a real mistake.

**Cost.** A misspelled column name reads as missing rather than failing to compile. The end-to-end test
(`tests/test_end_to_end.py`) runs the pages on Reports that compute really wrote, which catches a renamed
column.

## 10. Whole outputs are pinned before a refactor

**Decided.** `tests/test_golden.py` runs the retirement plan, and ingest plus compute for two made-up
households, and compares a digest of every table with `tests/golden.json`. Digests are letters only.

**Why.** A refactor should change nothing, and module tests alone can miss a change in a table nobody
asserted on. Digests rather than the tables keep made-up figures out of the history, where one could look
like a household value to the push guard.

**Cost.** A change meant to change an output re-pins the digests (`HOUSEHOLD_GOLDEN=update`) and says in its
commit what changed; a failure names the table, not the row.

## 11. A budget is groups of line items, and a line item is a category

**Decided.** The Budget page has three levels. A group (`categories.group`) is a high-level description:
home, media, giving. A line item is one category, the level spending is categorized at. A budget line
(`budget_rules`) says how a line item's budget is set; most line items have one, and one with several (a
camp per child) shows each as its own row. A line item's kind comes from its budget lines' rules: Recurring
(`recurring`, `billed`, `loan`), Annual (`sinking_fund`, judged on the year so far), Floating (`trailing`,
`lookback`, `seasonal`) or Fixed; a line item with no budget line in force is Unplanned and folds into one
row at its group's foot. Recurring charges, a giving line's recipients and a category's budget lines are a
line item's breakdown, never line items of their own.

**Why.** A subscription is a line item of the media group, not the other way round, and spending is only
ever categorized one level deep, so the budget can't be finer than a category without guessing. Keeping the
kind derived from the rule means there is nothing new to keep in step.

**Cost.** Anything that wants its own line item needs its own category (each streaming service, each part
of the mortgage), so categories multiply; a group is a label, with no budget of its own beyond the sum.

## 12. A loan payment is split as it is categorized, by the schedule

**Decided.** A loan's monthly payment is one bank line. With a `loan_split:<loan id>` setting, categorizing
the books splits it: interest and principal from the amortization schedule that month, and what is left,
the escrow, shared across the escrow parts by their amounts (`mortgage.split_payments`). The ledger keeps the
payment as it came; the split is derived every compute. A `loan` budget rule budgets a part from the same
schedule.

**Why.** The schedule already knows each month's interest and principal, and the escrow moves at each
review: deriving the split means a recast, an extra payment or an escrow change is right everywhere at the
next compute, with nothing to re-enter. A split kept in the ledger would go stale.

**Cost.** A month with two running loans that both have the setting, or a payment that doesn't cover the
schedule's principal and interest, is left whole. The escrow parts' amounts are estimates, so their shares
are only as good as the setting.

## 13. Answers about merchants are proposal_decisions, read one way

**Decided.** An answer a person gives about a merchant key or a stub (its category, whether its charges
recur, its name, the budget line it pays, whether gifts to it are deductible, why a stub's withholding is
off) is a `proposal_decisions` row of its own kind, keyed `<kind>:<key>`. The latest row per key is in force.
Every reader goes through `decisions.latest`; the module that uses a kind says what it means.

**Why.** One table and one rule (latest wins, the ledger keeps both) make a new kind of answer a few lines in
the change set reader and one reader call, with no schema change, and the history of every answer is kept.

**Cost.** The table is untyped: a kind's note means different things for different kinds, and nothing but
the reader enforces it. A kind with more than one value to keep would need its own table.

## 14. The views and ingest share the edit contract by test

**Decided.** A change set's edit kinds, their fields, the fields `was` carries and which edits replace each
other are written twice: in `web/src/edits.ts` for the views and in `parsers/changes.py` for ingest.
`tests/test_edit_contract.py` runs the built `edits.js` under Node beside the Python and fails when they
differ.

**Why.** Apps Script can't import Python and the engine can't run the page's code, so one of them would have
to be generated from the other; a test is simpler, and a drift is caught when it is made rather than when
someone saves.

**Cost.** A new kind is written in both places. The test needs Node and the built web app, so it runs in
CI's end-to-end job, not the Python job.
