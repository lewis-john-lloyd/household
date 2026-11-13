"""Household settings: the parameters table's jurisdiction "household" rows, kept in the ledger and edited in the
views' Settings tab like any other edit, so nothing about the household lives in the code or a script.

SETTINGS lists the settings the engine and the weekly email read, with what each is for; the views show them
even before they are set. check() says which values can't be used (a link that isn't https, a date that isn't
a date), so compute can say so rather than send a broken link.

A setting can be narrowed with one ":" part, as order_payee:books already is; a claim deadline for one plan
year is lpfsa_claim_deadline:2026.
"""

import re
from datetime import date

import pandas as pd

from . import categorize, mortgage

SETTINGS = {
    "target_refund": "The federal and Wisconsin refund to aim withholding at, in dollars (default 2000)",
    "planned_supplemental_rate": "The federal rate vests and bonuses are withheld at from now on (default 0.35)",
    "after_tax_401k_converts_to_roth": "TRUE when the 401(k) plan converts after-tax money to Roth on its own",
    "withholding_tolerance": "How far, in dollars, a projected refund may miss target_refund before the weekly "
    "email says so (default 500)",
    "views_url": "The views' web app link, for links in the weekly email",
    "payroll_url": "Where W-4, WT-4 and supplemental withholding are changed",
    "benefits_url": "Where 401(k) contributions are changed",
    "fsa_claims_url": "Where FSA claims are filed",
    "fsa_payee_pattern": "A pattern matching the FSA administrator on reimbursement deposits",
    "lpfsa_categories": "Categories whose reimbursements come from the limited-purpose FSA, separated by commas",
    "dcfsa_categories": "Categories whose reimbursements come from the dependent care FSA, separated by commas",
    "lpfsa_claim_deadline": "The last day to claim limited-purpose FSA money: MM-DD of the year after the plan "
    "year (lpfsa_claim_deadline:2026 with a full date for one plan year)",
    "dcfsa_claim_deadline": "The last day to claim dependent care FSA money: MM-DD of the year after the plan "
    "year (dcfsa_claim_deadline:2026 with a full date for one plan year)",
    "household_adults": "The two adults' owner names in accounts, separated by commas: a joint account splits evenly "
    "between them, and accounts of any other owner count as the kids' (default: the people "
    "on pay stubs)",
    "home_value_basis": "Which value of a home counts toward net worth: purchase_price (until sold), median, "
    "lowest, or a source in property_values such as zillow or county_fair_market "
    "(default purchase_price)",
    "home_sale_cost_rate": "What selling a home would cost, as a fraction of the price (commission and closing), "
    "for what a sale would net (default 0.06)",
    "wash_sale_symbols": "The symbols whose losses are checked for wash sales, separated by commas (default: the "
    "stock plan's, from the vesting schedule); a loss in any other is taken as it is",
    "loan_split": "What a loan's payment splits into on the budget (loan_split:<loan id>): category=interest and "
    "category=principal, from the amortization schedule, and each escrow part as category=its monthly amount, "
    "sharing what the payment leaves after principal and interest (mortgage_interest=interest, "
    "mortgage_principal=principal, pmi=50, property_tax=200, home_insurance=50)",
    "account_renamed": "An account's new name (account_renamed:<its id> = the new id): the views and reports show "
    "the new one, and a file named with either one finds the account",
    "legacy_account": "The old investments workbook's readings kept under one account that belong to another "
    "(legacy_account:<the id they were read under> = the account they belong to), such as an old "
    "rollover IRA whose tab was read into the IRA that took its place",
    "price_proxy": "A public fund a plan's fund tracks (price_proxy:<the fund's symbol, lower case, other characters "
    "as _> = its ticker, such as VFIAX), so the fund's value between statements moves with it",
    "birth_date": "A person's birth date, YYYY-MM-DD (birth_date:<person>), for the retirement plan; their Social "
    "Security statement gives it too",
    "social_security_claim_age": "The age a person claims Social Security at, 62 to 70 "
    "(social_security_claim_age:<person>; default 67)",
    "social_security_monthly": "A person's monthly benefit at full retirement age in today's dollars, when there is no "
    "statement for them yet (social_security_monthly:<person>)",
    "social_security_cut": "A cut to every Social Security benefit to assume, as a fraction, from "
    "social_security_cut_from (default 0; the 2025 Trustees Report has benefits payable at "
    "about 0.81 once the reserves run out)",
    "social_security_cut_from": "The year social_security_cut starts (default 2034)",
    "retirement_year": "The year the household retires in the plan (default: the earliest whose Monte Carlo success "
    "reaches retirement_success_target)",
    "retirement_strategy": "The plan's strategy, as the Strategies tab names it, such as bracket/to22/constant "
    "(default: the one that ends with the most after-tax wealth among the most successful)",
    "retirement_success_target": "The share of Monte Carlo runs that must never run short for a year to be one the "
    "household can retire in (default 0.9)",
    "retirement_plan_to_age": "The age the plan runs to, for the younger adult (default 95)",
    "retirement_stock_share": "The share of the portfolio in stocks for the plan's returns (default: the allocation "
    "targets')",
    "retirement_cagrs": "The fixed nominal yearly returns to show, separated by commas (default 0.05, 0.07, 0.09)",
    "retirement_inflation": "Inflation for the fixed returns and for amounts the law doesn't index (default 0.03)",
    "retirement_taxable_yield": "The taxable accounts' yearly dividends as a share of their value (default 0.02)",
    "retirement_taxable_savings": "What the household adds to taxable accounts a year while working, in today's "
    "dollars (default: the last 12 months, as the Assumptions tab shows)",
    "retirement_pre_tax_contributions": "A person's pre-tax plan contributions a year while working, employer's "
    "included (retirement_pre_tax_contributions:<person>; default: the last 12 "
    "months' activity)",
    "retirement_roth_contributions": "A person's Roth and after-tax contributions a year while working "
    "(retirement_roth_contributions:<person>; default: the last 12 months' activity)",
    "retirement_spending": "A category or group's spending in retirement, a year in today's dollars, from a year on: "
    "retirement_spending:<category or group> = 0 from 2032, or 18000; 9000 from 2040. A name "
    "that isn't a category or group adds that spending",
    "retirement_leave_out": "Categories or groups that aren't spending in retirement, separated by commas (default "
    "taxes: the plan works out its own)",
    "retirement_health_pre65": "Health cover a year for the household before 65, once retired (default 24000)",
    "retirement_health_supplement": "Medigap and Part D premiums a year a person from 65 (default 3000)",
    "hsa_spending_categories": "Categories or groups the HSA pays for in retirement, separated by commas (default "
    "medical); it pays Medicare premiums too",
    "retirement_monte_carlo_runs": "How many Monte Carlo runs (default 1000)",
    "grant_price": "A grant's price per share, for the Equity page (grant_price:<grant id>, the id lower case with "
    "anything but letters and digits as _; default: the price on the award page)",
    "equity_price_steps": "The dollars a share the Equity page moves the price up and down by, separated by commas "
    "(default 5, 10, 20)",
    "receipt_sender": "The addresses a store's receipts come from, comma-separated, one setting per store "
    "(receipt_sender:<store>); the receipt exporter reads only mail from these",
    "receipt_subject": "Optional, with a store's receipt_sender: words its receipts' subjects contain, separated "
    "by | (receipt_subject:<store>); the exporter then reads only mail whose subject has one",
}

LINKS = ("views_url", "payroll_url", "benefits_url", "fsa_claims_url")
RENAME_PREFIX = "account_renamed:"
LEGACY_ACCOUNT_PREFIX = "legacy_account:"
ACCOUNT_ID = re.compile(r"[a-z][a-z0-9_]{0,40}")
PROXY_PREFIX = "price_proxy:"


def symbol_key(symbol):
    """A symbol as a setting can name it: lower case, every other character _ ("S&P 500 Index" is s_p_500_index)."""
    return re.sub(r"[^a-z0-9]+", "_", str(symbol).lower()).strip("_")


NUMBERS = (
    "target_refund",
    "planned_supplemental_rate",
    "withholding_tolerance",
    "social_security_claim_age",
    "social_security_monthly",
    "social_security_cut",
    "social_security_cut_from",
    "retirement_year",
    "retirement_success_target",
    "retirement_plan_to_age",
    "retirement_stock_share",
    "retirement_inflation",
    "retirement_taxable_yield",
    "retirement_taxable_savings",
    "retirement_health_pre65",
    "retirement_health_supplement",
    "retirement_monte_carlo_runs",
    "retirement_pre_tax_contributions",
    "retirement_roth_contributions",
    "grant_price",
)
DEADLINES = ("lpfsa_claim_deadline", "dcfsa_claim_deadline")
ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
PHRASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 &'.,!#-]{0,59}")
MAX_PARTS = 10


def senders(v):
    return [a.strip() for a in str(v).split(",") if a.strip()]


def subjects(v):
    return [w.strip() for w in str(v).split("|") if w.strip()]


def sender_problem(v):
    """Why a receipt_sender value can't be used, or "": email addresses only, so each search is exactly
    those senders and a setting can never widen the exporter to the rest of the mailbox."""
    found = senders(v)
    if not found or len(found) > MAX_PARTS:
        return f"needs 1 to {MAX_PARTS} addresses"
    bad = [a for a in found if not ADDRESS.fullmatch(a)]
    return f"has {bad[0]!r}, which is not an email address" if bad else ""


def subject_problem(v):
    """Why a receipt_subject value can't be used, or "": plain words and phrases, no search syntax."""
    found = subjects(v)
    if not found or len(found) > MAX_PARTS:
        return f"needs 1 to {MAX_PARTS} phrases"
    bad = [w for w in found if not PHRASE.fullmatch(w)]
    return f"has {bad[0]!r}: letters, digits, spaces and & ' . , ! # - only" if bad else ""


def gmail_search(sender_list, subject_list):
    """The Gmail search for one store, as the exporter builds it: from those senders, and if there are
    subjects, with one of them. Every branch keeps the sender."""
    search = "from:(" + " OR ".join(sender_list) + ")"
    if subject_list:
        search += " subject:(" + " OR ".join(f'"{w}"' for w in subject_list) + ")"
    return search


def receipt_searches(params):
    """The receipt exporter's searches, one row per store (store, senders, subjects, search): each
    receipt_sender:<store> that is set and usable, with its receipt_subject:<store> if that is set and
    usable. A store whose subject setting can't be used is left out rather than read without it."""
    columns = ["store", "senders", "subjects", "search"]
    if params is None or params.empty:
        return pd.DataFrame(columns=columns)
    household = params[params["jurisdiction"].astype(str) == "household"]
    latest = household.drop_duplicates("key", keep="last")
    value = dict(zip(latest["key"].astype(str), latest["value"].astype(str).str.strip()))
    rows = []
    for key, v in value.items():
        base, _, store = key.partition(":")
        if base != "receipt_sender" or not store or not v or sender_problem(v):
            continue
        subject = value.get(f"receipt_subject:{store}", "")
        if subject and subject_problem(subject):
            continue
        rows.append(
            {
                "store": store,
                "senders": ", ".join(senders(v)),
                "subjects": " | ".join(subjects(subject)),
                "search": gmail_search(senders(v), subjects(subject)),
            }
        )
    return pd.DataFrame(rows, columns=columns).sort_values("store").reset_index(drop=True)


def value(params, key, default=""):
    """A household setting's value, or default when it is not set."""
    return categorize.household_parameter(params, key, default) or default


def number(params, key, default):
    """A numeric setting, or default when it is unset or not a number."""
    try:
        return float(value(params, key, default))
    except ValueError:
        return float(default)


def link(params, key):
    """An https link from the settings, or "" when it is unset or not one."""
    url = value(params, key)
    return url if url.startswith("https://") else ""


def names(params, key):
    """A comma-separated setting as a set of lower-case names."""
    return {n.strip().lower() for n in value(params, key).split(",") if n.strip()}


def claim_deadline(params, key, plan_year):
    """The claim deadline for a plan year: key:plan_year as a full date, else key as MM-DD of the next year.
    None when neither is set or usable."""
    specific = value(params, f"{key}:{plan_year}")
    try:
        if specific:
            return date.fromisoformat(specific)
        every = value(params, key)
        if re.fullmatch(r"\d{2}-\d{2}", every):
            return date.fromisoformat(f"{plan_year + 1}-{every}")
    except ValueError:
        return None
    return None


def check(params):
    """[(key, value, problem)] for each setting that is set but can't be used."""
    if params is None or params.empty:
        return []
    household = params[params["jurisdiction"].astype(str) == "household"]
    latest = household.drop_duplicates("key", keep="last")
    out = []
    for key, v in zip(latest["key"].astype(str), latest["value"].astype(str).str.strip()):
        base = key.split(":")[0]
        if not v:
            continue
        if base in LINKS and not v.startswith("https://"):
            out.append((key, v, "is not an https link"))
        elif base in NUMBERS:
            try:
                float(v)
            except ValueError:
                out.append((key, v, "is not a number"))
        elif base == "fsa_payee_pattern":
            try:
                re.compile(v)
            except re.error as e:
                out.append((key, v, f"is not a pattern ({e})"))
        elif base == "receipt_sender" and sender_problem(v):
            out.append((key, v, sender_problem(v)))
        elif base == "receipt_subject" and subject_problem(v):
            out.append((key, v, subject_problem(v)))
        elif base in ("account_renamed", "legacy_account") and not ACCOUNT_ID.fullmatch(v):
            out.append((key, v, "is not an account id (lower case letters, digits and _)"))
        elif base == "price_proxy" and not re.fullmatch(r"[A-Z0-9.:^-]{1,20}", v):
            out.append((key, v, "is not a ticker (VFIAX, NYSEARCA:BND)"))
        elif base == "loan_split" and not mortgage.split_parts(v):
            out.append((key, v, "is not category=interest, category=principal and category=amount parts"))
        elif base == "birth_date":
            try:
                date.fromisoformat(v)
            except ValueError:
                out.append((key, v, "is not a date (YYYY-MM-DD)"))
        elif base == "retirement_spending" and not re.fullmatch(
            r"\s*-?[\d,]+(\.\d+)?(\s+from\s+\d{4})?\s*(;\s*-?[\d,]+(\.\d+)?(\s+from\s+\d{4})?\s*)*", v
        ):
            out.append((key, v, "is not an amount, or amounts from years (0 from 2032; 9000 from 2040)"))
        elif base == "retirement_cagrs" and not re.fullmatch(r"\s*-?\d*\.?\d+(\s*,\s*-?\d*\.?\d+)*\s*", v):
            out.append((key, v, "is not numbers separated by commas"))
        elif base in DEADLINES:
            full = ":" in key
            ok = re.fullmatch(r"\d{4}-\d{2}-\d{2}" if full else r"\d{2}-\d{2}", v)
            try:
                date.fromisoformat(v if full else f"2001-{v}")
            except ValueError:
                ok = False
            if not ok:
                out.append((key, v, "is not a date (YYYY-MM-DD)" if full else "is not a month and day (MM-DD)"))
    return out


def known(editor_rows):
    """The editor's settings plus every setting in SETTINGS that isn't set yet, each with what it is for."""
    rows = editor_rows.copy()
    have = set(rows["key"].astype(str))
    missing = [{"key": k, "value": "", "source": "not set"} for k in SETTINGS if k not in have]
    if missing:
        rows = pd.concat([rows, pd.DataFrame(missing)], ignore_index=True)
    rows["about"] = rows["key"].astype(str).str.split(":").str[0].map(SETTINGS).fillna("")
    return rows.sort_values("key").reset_index(drop=True)
