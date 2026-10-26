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

Files dropped in the Drive `inbox` folder are identified, checked and filed by `engine.ingest_inbox`: pay stubs in two layouts, the Schwab equity export, the accounts seed, tax returns and W-4s (filed for the record, no rows), and the IRS publications, revenue procedures, notices and public laws the tax code cites (filed under stable names such as `publication-15-t-2026`, no rows). Every parser checks the document against its own printed totals; a file that fails stays in the inbox with the reason, and nothing is written. Re-sent files, and re-downloads whose rows are all already present, go to `record_store/duplicates`; a partially overlapping export adds only its new rows and names the earlier file it overlapped. Compute lists likely double entries (same account and amount, a few days apart, from different files) in the `possible_duplicates` report; a `void` correction takes a confirmed one off the books. See `src/household/inbox.py` and `compute.possible_duplicates`.

Bank and card downloads come in as OFX or QFX (preferred: each transaction carries the bank's own id, and the file gives the balance) or as USAA's CSV, or as Bank of America's one-statement CSVs (named with the card's last four). An OFX file is matched to its account by the last four of the account number against the `last4` column of `accounts`; a CSV names no account, so its file name must contain the `account_id` (`expenses_usaa.csv`). Transactions get stable ids (the bank's FITID, or account, date, amount and payee with a count for identical rows), so overlapping downloads add each transaction once. Pending rows are left out, card CSV amounts are flipped so money out is negative everywhere, and an OFX ledger balance goes to `balances`. See `parsers/ofx.py`, `parsers/usaa_csv.py` and `ingest.finish_bank_rows`. PayPal's QIF downloads are not counted as an account (each purchase is already on the card or bank that funded it); their payments, with counterparty and item, go to `payment_details` for naming those lines.

Amazon's order history export (the zip from Request your data, or its `Order History.csv` and `Refund Details.csv` on their own) goes to `order_items`: order, ship date, the item's total, department, website and the card's last four, nothing else. The inbox files that trimmed copy in `record_store` and moves the original to Drive's trash, since the export also holds addresses, tracking numbers and the like; the same export sent again goes straight to the trash. See `parsers/amazon.py`.

`explain.py` matches these explanation downloads to the lines they explain, by amount and date, one to one: a PAYPAL line takes PayPal's counterparty as its payee; an AMAZON charge matches a shipment (or a whole order), a credit a refund, and takes its category from the items (Whole Foods is food, a few departments map, the rest is household), split by item amount when they differ. Orders paid with a card that is not in `accounts` are left out. A line inside an export's dates that matched nothing is categorized like any other; one after the latest export waits for the next.

## Categories and the review tab

Every transaction gets a category and a record of what decided it (`categorize.py`), first match wins: a category a person set on the line; a payee rule (`payee_rules`, a pattern with an effective date, seeded from the inbox like the accounts); an answer given in review for that payee; an explanation matched to the line (see the Inbox section; an Amazon match decides the category, a PayPal match names the payee and categorization carries on), or one still to come (Amazon, PayPal, Venmo, Apple and Google lines after their latest export wait, with a provisional guess); a scikit-learn model retrained every run on the old books and every answer since, which decides only when it is confident; and the bank's own category, as a guess. A transfer with no partner is asked about every time (money out is guessed as the household parameter `unpaired_transfer_guess`), and paper checks are asked about one by one. Lines before `REVIEW_FROM` are never asked about.

Everything else lands in the ledger's `review` tab, one row per merchant (`key`, the payee with store numbers, places and processor prefixes stripped) or per line. Type a category in `answer`, or `y` to take the guess, and run ingest: the answers are stored (`proposal_decisions` for a merchant, `txn_corrections` for a line) and the tab is rewritten with what is still open. A merchant answer covers every line whose key starts with that key, past and future; shorten the key before answering to widen it (`AMAZON MKTPL` to `AMAZON`). `engine.review` turns answers around without touching the inbox.

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

## Releases

Notebooks pin a tag, for example `pip install git+https://github.com/lewis-john-lloyd/household@v0.0.1`. Upgrading is a deliberate edit of that tag; rolling back is reverting it.

Ordinary commits leave the version number alone. When work is ready to run, one release commit sets the version in `pyproject.toml` and `household/__init__.py`, and the owner tags that commit.
