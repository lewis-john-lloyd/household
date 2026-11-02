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

- `tools/import_guard.py` (CI and pre-commit) allows only a short list of imports: pure standard-library modules, pandas, numpy, scikit-learn (the categorization model), pypdf, and Google's Sheets, Drive and Colab clients. No sockets, HTTP libraries, subprocesses, `os`, `eval`/`exec` or dynamic imports. Widening the list is a visible change to that file.
- pypdf, the one library a release may download, is pinned to an exact version. pandas, numpy, scikit-learn and Google's clients come from the Colab image.
- Only the owner creates release tags, and notebooks install a tag, so nothing reaches Colab without the owner reviewing the diff (`/compare/<old tag>...<new tag>` on GitHub).

## Inbox

Files dropped in the Drive `inbox` folder are identified, checked and filed by `engine.ingest_inbox`: pay stubs in three layouts (one of them ADP's earnings statement, whose PDF is read piece by piece because its layout text is unusable), the Schwab equity export, the accounts seed, tax returns and W-4s (filed for the record, no rows), and the IRS publications, revenue procedures, notices and public laws the tax code cites (filed under stable names such as `publication-15-t-2026`, no rows). Every parser checks the document against its own printed totals; a file that fails stays in the inbox with the reason, and nothing is written. Re-sent files, and re-downloads whose rows are all already present, go to `record_store/duplicates`; a partially overlapping export adds only its new rows and names the earlier file it overlapped. Compute lists likely double entries (same account and amount, a few days apart, from different files) in the `possible_duplicates` report; a `void` correction takes a confirmed one off the books. See `src/household/inbox.py` and `compute.possible_duplicates`.

Bank and card downloads come in as OFX or QFX (preferred: each transaction carries the bank's own id, and the file gives the balance) or as USAA's CSV, or as Bank of America's one-statement CSVs (named with the card's last four). An OFX file is matched to its account by the last four of the account number against the `last4` column of `accounts`; a CSV names no account, so its file name must contain the `account_id` (`expenses_usaa.csv`). Transactions get stable ids (the bank's FITID, or account, date, amount and payee with a count for identical rows), so overlapping downloads add each transaction once. Pending rows are left out, card CSV amounts are flipped so money out is negative everywhere, and an OFX ledger balance goes to `balances`. See `parsers/ofx.py`, `parsers/usaa_csv.py` and `ingest.finish_bank_rows`. PayPal's QIF downloads are not counted as an account (each purchase is already on the card or bank that funded it); their payments, with counterparty and item, go to `payment_details` for naming those lines.

Amazon's order history export (the zip from Request your data, or its `Order History.csv` and `Refund Details.csv` on their own) goes to `order_items`: order, ship date, the item's total, department, website and the card's last four, nothing else. The inbox files that trimmed copy in `record_store` and moves the original to Drive's trash, since the export also holds addresses, tracking numbers and the like; the same export sent again goes straight to the trash. See `parsers/amazon.py`.

Google Takeout (Google Pay and Google Play Store selected; it may arrive as several zips) goes to `order_items` too: Google Pay's list of Google purchases (Play apps and subscriptions, Google One, the Google Store), service `google`, app and item, amount and card's last four. The zips go to the trash and only those rows are filed; a part holding nothing read (the HTML index) is trashed as well. See `parsers/google_takeout.py`.

`explain.py` matches these explanation downloads to the lines they explain, by amount and date, one to one: a PAYPAL line takes PayPal's counterparty as its payee, and a GOOGLE line the app and item bought; an AMAZON charge matches a shipment (or a whole order), a credit a refund, and takes its category from the items (Whole Foods is food, a few departments map, the rest is household), split by item amount when they differ. Orders paid with a card that is not in `accounts` are left out. A line inside an export's dates that matched nothing is categorized like any other, except an AMAZON line: that one is asked about on its own in review, with the items shipped around then (closest in price first) as hints, since a gift card or a split payment can hide the order it paid for. A line after the latest export waits for the next.

## Categories and the review tab

Every transaction gets a category and a record of what decided it (`categorize.py`), first match wins: a category a person set on the line; a payee rule (`payee_rules`, a pattern with an effective date, seeded from the inbox like the accounts); an answer given in review for that payee; an explanation matched to the line (see the Inbox section; an Amazon match decides the category, a PayPal match names the payee and categorization carries on), or one still to come (Amazon, PayPal, Venmo, Apple and Google lines after their latest export wait, with a provisional guess); a scikit-learn model retrained every run on the old books and every answer since, which decides only when it is confident; and the bank's own category, as a guess. A transfer with no partner is asked about every time (money out is guessed as the household parameter `unpaired_transfer_guess`), and paper checks are asked about one by one. Lines before `REVIEW_FROM` are never asked about.

Everything else lands in the ledger's `review` tab, one row per merchant (`key`, the payee with store numbers, places and processor prefixes stripped) or per line. Type a category in `answer`, or `y` to take the guess, and run ingest: the answers are stored (`proposal_decisions` for a merchant, `txn_corrections` for a line) and the tab is rewritten with what is still open. A merchant answer covers every line from that merchant, past and future, however a card spells it: spaces and apostrophes don't count, a key covers every key that starts with it, and names a card cut short match on their first ten letters (`categorize.same_merchant`); `example` shows up to three spellings that were joined. Shorten the key before answering to widen it (`AMAZON MKTPL` to `AMAZON`). Old category names in `category_aliases` apply to rules and answers too, so renaming a category is one alias row. `engine.review` turns answers around without touching the inbox.

## Budget

`budget_rules` holds one row per budget line per change: the category it pays for, the account that funds it, a priority, and a rule (`budget.py`). `fixed` is an amount per period (monthly, biweekly, annually and so on) normalized to a month; `lookback` is the mean plus a quarter standard deviation of the category's spending in the same three months a year earlier, the old budget-reports formula, with the amount as a fallback until there is a year of history; `sinking_fund` sets an amount aside each month and keeps a balance against the category's spending; `scheduled` puts one amount in one month (several rows for installments); `end` stops a line. A change is a new row with a later `effective`, so past targets never move. Compute writes `budget_targets` (each line each month), `budget_vs_actual` (each category each month, with its group and priority) and `sinking_funds`. The rules arrive as a seed file and are edited in the ledger afterwards.

## The monthly checklist

Compute writes four reports for the monthly routine (`checks.py`, `recurring.py`). `coverage` lists each bank and card account that sends monthly statements with the newest date the ledger has for it, and marks it due after `max_gap_days` (45 when blank); then each explanation download with the last date it covers and how many lines wait for the next one. Set an account's `expected_docs` to `when_used` for an open card kept unused (listed, never due), or to `none` (or fill `closed`) to take it off the list. `balance_check` turns each stored balance into the balance the ledger implies before its first transaction; every snapshot of an account must imply the same one, so a later download that disagrees points at missing or doubled transactions. `paychecks` pairs each stub's net pay with its pay deposit (same amount, a few days around payday, the deposit naming the employer) and lists what doesn't pair: a deposit with no stub is "missing stub: did you forget it?", and a stub whose pay never arrived is "no deposit". `recurring` lists payees charged at a steady cadence for a steady amount, marking the ones that changed price or stopped.

## Mortgage

`mortgage.py` amortizes each loan in `loans` (principal, annual rate, term in months, origination date and the monthly escrow, if the payment includes it) month by month from the month after origination, applying `loan_events`: recorded payments (escrow comes off first; anything above the scheduled payment is extra principal), prepayments, recasts (a lump sum, then the same term at a lower payment) and rate changes. A month with no recorded payment takes the scheduled one. Compute writes the schedule (interest, principal, escrow, extra principal, balance) to the `mortgage` report beside what the household's own transactions categorized as mortgage paid that month. Loans arrive as a seed file (`loan_id,lender,originated,principal,rate,term_months,escrow`); events are typed into `loan_events`. A loan missing a field is skipped with a note.

## Views

Compute writes each view's data as a Reports table, and `web/` draws it: a TypeScript Apps Script web app that reads Reports under the viewer's own Google sign-in and draws with Google Charts. The first view is the `flows` table (`flows.py`) as a Sankey: pay through taxes, deductions and net pay into the accounts (vested shares go straight to savings), then out to savings and each category (savings categories also flow back in, as shares sold; a category that got more back than it spent shows as money back; whatever the period didn't cover came from or went to cash balances), for any month, any year, or a year's monthly average (the year divided by the months it has data for), by category or by group. A year takes each pay line's year-to-date amount from the year's last stub, so a missing stub doesn't shrink the year, and pay deposits no stub accounts for show as "pay without a stub (person)", matched to a person by their employer's name on the deposit. `web/src/shape.ts` turns rows into chart data and is tested under Node; no IDs or data live in `web/`.

To deploy (Node 22 and `npm i -g @google/clasp`):

1. `cd web && npm ci && npm run build`
2. `clasp login`, then `clasp create --type webapp --title "Household views" --rootDir dist` (or copy `.clasp.json.example` to `.clasp.json` with an existing script id)
3. `clasp push`
4. In the script editor, Project Settings, Script properties: add `REPORTS_ID` with the Reports spreadsheet's id
5. Deploy, New deployment, Web app. It runs as the person viewing it, so they see only what they can open; `access` in `appsscript.json` is `MYSELF` (only the owner). To let another household member open it, set `ANYONE` (any signed-in Google account; Reports' own sharing still decides who can read it) and redeploy.

## Development

```
pip install -e ".[dev]"
pytest
python tools/leak_guard.py
```

Optional local hook: `pip install pre-commit && pre-commit install`.

## Tax law

Federal, Wisconsin and Pennsylvania rates, limits and withholding tables live in `src/household/taxdata.py`; the Wisconsin return (Form 1 and part-year Form 1NPR) and Pub W-166 withholding are in `src/household/wisconsin.py`. Pennsylvania (PA-40, Schedule G-L and flat-rate withholding) is in `src/household/pennsylvania.py`. Every value cites the page of a source document that states it (`CITATIONS`), and each source is pinned by the SHA-256 of the PDF it was checked against (`DOCUMENTS`). The `tax_sources` report lists every value with its citation and a link to the PDF in the record store.

A release that changes tax law (each January) adds the new year's values and citations, then checks them against the PDFs:

```
python tools/verify_tax_law.py <folder of source PDFs>
```

It checks that each quote is on its cited page and states the value the code uses, that every bracket threshold appears, and that every row of Pub 15-T's annual married-filing-jointly table equals the brackets shifted by the table floor. Wisconsin prints no rate schedule or standard deduction formula, so those are proved by reproducing every row of the Form 1 tax table, standard deduction table and homeowner's credit table, and the amounts printed in the tax computation worksheet and Pub W-166. It exits nonzero on any failure, missing document, or uncited value.

## Before pushing

This repo is public, so a pre-push hook checks what is about to leave the machine. Turn it on once per clone with `git config core.hooksPath .githooks`. `tools/push_guard.py` checks every added line and commit message against the leak guard's patterns and against a private denylist of the household's real values (names, places, employers, account digits, and every amount with cents in the pay stubs and transactions, however it is written), kept outside the repo at `~/.config/household/denylist.txt`; then it runs the import guard and the tests. `tools/denylist.py` builds the denylist from ledger exports and a words file. Without a denylist the push stops (`HOUSEHOLD_NO_DENYLIST=1` skips that check; `HOUSEHOLD_PUSH_QUICK=1` skips the tests).

## Releases

Notebooks pin a tag, for example `pip install git+https://github.com/lewis-john-lloyd/household@v0.0.1`. Upgrading is a deliberate edit of that tag; rolling back is reverting it.

Ordinary commits leave the version number alone. When work is ready to run, one release commit sets the version in `pyproject.toml` and `household/__init__.py`, and the owner tags that commit.
