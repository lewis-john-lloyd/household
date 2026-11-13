"""Declared tables for the ledger and Reports spreadsheets.

The ledger holds facts and is append-only; Reports holds derived tables that
compute rebuilds every run. A table is a header row plus records; enumerated
columns get dropdown validation in the Sheet.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple
    enums: dict = field(default_factory=dict)

    def __post_init__(self):
        unknown = set(self.enums) - set(self.columns)
        if unknown:
            raise ValueError(f"{self.name}: enum for unknown columns {sorted(unknown)}")


LEDGER = (
    Table(
        "accounts",
        (
            "account_id",
            "institution",
            "owner",
            "type",
            "tax_treatment",
            "opened",
            "closed",
            "last4",
            "expected_docs",
            "max_gap_days",
        ),
        {
            "type": (
                "checking",
                "savings",
                "credit_card",
                "brokerage",
                "retirement",
                "workplace_plan",
                "ira",
                "hsa",
                "education",
                "savings_bonds",
                "loan",
                "other",
            ),
            "tax_treatment": ("taxable", "pre_tax", "roth", "hsa", "education", "none"),
        },
    ),
    Table(
        "transactions",
        (
            "txn_id",
            "date",
            "account_id",
            "amount",
            "payee_raw",
            "category",
            "one_off",
            "note",
            "source",
            "era",
            "doc_id",
            "recorded_at",
        ),
        {"source": ("legacy", "csv", "ofx", "pdf", "feed", "hand"), "one_off": ("TRUE", "FALSE")},
    ),
    Table(
        "txn_corrections",
        ("txn_id", "field", "value", "recorded_at", "reason", "source"),
        {"field": ("category", "one_off", "note", "split", "void")},
    ),
    Table("balances", ("as_of", "account_id", "balance", "doc_id")),
    # who a payment service says a payment went to or came from, for naming the bank or card
    # line it funded ("PAYPAL *XYZ" on a card is a payment to a named merchant for an item)
    Table("payment_details", ("detail_id", "date", "amount", "service", "kind", "counterparty", "memo", "doc_id")),
    # what an Amazon order history export says was bought (or refunded), trimmed to what matching needs
    Table(
        "order_items",
        (
            "item_id",
            "service",
            "order_id",
            "kind",
            "order_date",
            "date",
            "amount",
            "card",
            "department",
            "item",
            "website",
            "doc_id",
        ),
        {"kind": ("item", "refund")},
    ),
    # a person's category for one item an order history or receipt listed (the latest per item wins), so a
    # line those items explain splits by it
    Table("item_categories", ("item_id", "category", "recorded_at", "source")),
    Table("categories", ("category", "group", "priority"), {"priority": ("high", "medium", "low")}),
    # old category names (the 2017 to 2024 books) and the category each now counts as
    Table("category_aliases", ("alias", "category", "note")),
    # a pattern on the payee and the category it means from `effective` on; ask TRUE only pre-fills the
    # guess and still asks about each line (paper checks)
    Table("payee_rules", ("pattern", "category", "precedence", "effective", "ask"), {"ask": ("TRUE", "FALSE")}),
    # one row per budget line per change (see budget.py); scheduled rows are one-off amounts in their month
    Table(
        "budget_rules",
        ("line", "effective", "category", "account", "priority", "rule", "amount", "per", "note"),
        {
            "rule": (
                "fixed",
                "billed",
                "seasonal",
                "trailing",
                "lookback",
                "sinking_fund",
                "recurring",
                "loan",
                "scheduled",
                "end",
            ),
            "priority": ("high", "medium", "low"),
            "per": ("monthly", "biweekly", "weekly", "semi_monthly", "quarterly", "semi_annually", "annually"),
        },
    ),
    Table("allocation_targets", ("effective", "node", "parent", "weight")),
    Table("parameters", ("year", "jurisdiction", "key", "value", "source")),
    Table(
        "return_facts",
        ("year", "kind", "key", "person", "value", "source_doc", "note", "doc_id"),
        {"kind": ("input", "filed", "estimate")},
    ),
    # the household's history of pay changes: each raise, promotion, new job or schedule change
    Table("comp_events", ("effective", "person", "employer", "salary", "band", "rating", "fte", "note")),
    Table(
        "paystubs",
        (
            "pay_date",
            "period_start",
            "period_end",
            "person",
            "employer",
            "stub_id",
            "line_item",
            "kind",
            "amount",
            "ytd_amount",
            "doc_id",
        ),
        {"kind": ("income", "pre_tax", "tax", "post_tax", "employer", "memo")},
    ),
    Table("equity_vests", ("vest_date", "grant_id", "award_date", "net_shares", "fmv", "deposit_date", "doc_id")),
    # each grant's vesting schedule (parsers/schwab_awards.py for share awards, parsers/stock_options.py for
    # options, with their exercise price and expiry); a grant's rows from its latest document are its schedule
    Table(
        "vest_schedule",
        ("grant_id", "award_date", "symbol", "award_type", "grant_price", "expires", "vest_date", "shares", "doc_id"),
    ),
    Table(
        "equity_sales",
        ("sale_date", "grant_id", "vest_date", "shares", "sale_price", "proceeds", "basis", "gain", "term", "doc_id"),
        {"term": ("short_term", "long_term")},
    ),
    # a reading of what an account held on a date: shares and value per fund (shares blank for a value alone).
    # source is the part of a workplace plan the reading covers (pretax, match, aftertax, rollover, roth), blank
    # for the whole account
    Table("holdings", ("as_of", "account_id", "symbol", "source", "shares", "value", "doc_id")),
    # an investment account's activity (parsers/fidelity.py, parsers/ofx_invest.py): shares and their value, and
    # money in or out; source is a workplace plan's money source for the row, as its custodian names it
    Table(
        "investment_activity",
        (
            "activity_id",
            "account_id",
            "date",
            "kind",
            "action",
            "symbol",
            "description",
            "shares",
            "price",
            "value",
            "amount",
            "cash_balance",
            "source",
            "doc_id",
        ),
        {
            "kind": (
                "contribution",
                "buy",
                "sell",
                "exchange",
                "reinvest",
                "dividend",
                "interest",
                "conversion_in",
                "conversion_out",
                "transfer_in",
                "transfer_out",
                "withdrawal",
                "fee",
                "adjustment",
                "other",
            )
        },
    ),
    # a person's correction to one row of investment_activity, which stays as it was read (a plan's download
    # that calls a rollover a contribution, say); the last row for an activity and field is in force
    Table(
        "activity_corrections",
        ("activity_id", "field", "value", "recorded_at", "reason", "source"),
        {"field": ("kind",)},
    ),
    # each fund or stock the household holds: its name, asset class and expense ratio, and the symbol
    # GOOGLEFINANCE knows it by (quote; blank for a fund with no public price, such as a plan-only trust)
    Table("securities", ("symbol", "name", "asset_class", "expense_ratio", "quote", "wash_group")),
    # a stock split: from date on, one share is ratio shares (lots.py restates lots and sales in today's shares)
    Table("stock_splits", ("symbol", "date", "ratio", "note")),
    # a security's closing price on or before a date (priced_date is the trading day it came from); a row with
    # no close records that GOOGLEFINANCE had none, so the date is not asked again (prices.py)
    Table("prices", ("symbol", "date", "priced_date", "close", "source")),
    # a work surface like review: GOOGLEFINANCE formulas for the prices still wanted, rewritten by ingest
    Table("price_requests", ("symbol", "quote", "date", "result")),
    Table("loans", ("loan_id", "lender", "originated", "principal", "rate", "term_months", "escrow")),
    # what a loan's documents say beyond loans' columns (the closing disclosure's terms, who services it), one
    # fact per row; a later row for the same key (a servicer that changed) is in force from its as_of
    Table("loan_facts", ("loan_id", "key", "value", "as_of", "note")),
    # a loan's balance on a date, for loans with no amortization in loans (the legacy workbook's mortgages)
    Table("loan_balances", ("as_of", "loan_id", "balance", "doc_id")),
    # each home: when and for what it was bought (basis_costs: closing costs that add to basis), its loans
    # (loan_ids, separated by commas: loans rows or loan_balances ids) and its sale
    Table(
        "properties",
        (
            "property_id",
            "address",
            "parcel",
            "owner",
            "purchased",
            "purchase_price",
            "basis_costs",
            "loan_ids",
            "sold",
            "sale_price",
            "note",
        ),
    ),
    # what a home was estimated or assessed at: an online estimate, the county's assessment and its estimate of
    # fair market value, an appraisal, or the legacy workbook's tracked value
    Table(
        "property_values",
        ("as_of", "property_id", "source", "value", "note", "doc_id"),
        {
            "source": (
                "zillow",
                "redfin",
                "trulia",
                "county_assessed",
                "county_fair_market",
                "appraisal",
                "hand",
                "legacy",
            )
        },
    ),
    # a home's property tax bill for a tax year (the county's parcel page): its assessment, the mill rate, the
    # taxes, the credits, the specials (charges on the bill that are not tax) and what was due
    Table(
        "property_taxes",
        (
            "tax_year",
            "property_id",
            "assessed",
            "mill_rate",
            "total_taxes",
            "lottery_credit",
            "first_dollar_credit",
            "school_levy_credit",
            "specials",
            "net_taxes",
            "amount_due",
            "doc_id",
        ),
    ),
    # work done on a home: capital adds to basis, a repair doesn't, an energy_credit improvement adds its cost
    # less the credit claimed for it
    Table(
        "improvements",
        ("date", "property_id", "description", "kind", "cost", "credit", "receipt", "note", "doc_id"),
        {"kind": ("capital", "repair", "energy_credit")},
    ),
    # the Social Security statement (parsers/ssa_statement.py): whose it is and their birth date, the monthly
    # benefit it estimates at each claiming age (today's dollars), and the earnings record; each statement's
    # rows are kept under its date (as_of) and the latest is used
    Table("ss_statements", ("person", "birth_date", "as_of", "doc_id")),
    Table(
        "ss_estimates", ("person", "as_of", "kind", "age", "monthly", "doc_id"), {"kind": ("retirement", "disability")}
    ),
    Table("ss_earnings", ("person", "as_of", "year", "fica_earnings", "medicare_earnings", "doc_id")),
    Table(
        "loan_events",
        ("date", "loan_id", "kind", "amount", "doc_id"),
        {"kind": ("payment", "prepayment", "recast", "rate_change")},
    ),
    Table(
        "source_documents",
        (
            "doc_id",
            "kind",
            "sha256",
            "original_name",
            "drive_link",
            "account_id",
            "covers_from",
            "covers_to",
            "parser",
            "received_at",
            "precedence",
        ),
    ),
    Table(
        "proposal_decisions",
        ("proposal_key", "kind", "decided_at", "decision", "note"),
        {"decision": ("approve", "reject", "snooze")},
    ),
    # the review queue: a work surface, not a record. Each ingest run reads the answers typed here
    # into proposal_decisions and txn_corrections, then rewrites the tab with what is still open.
    Table(
        "review",
        (
            "key",
            "answer",
            "split",
            "guess",
            "lines",
            "total",
            "example",
            "first",
            "last",
            "accounts",
            "guessed_by",
            "confidence",
            "kind",
            "note",
        ),
        {"kind": ("payee", "line")},
    ),
)

REPORTS = (
    Table("run_log", ("run_id", "computed_at", "stage", "detail")),
    Table("run_notes", ("run_id", "stage", "at", "n", "message", "level")),
    # change sets the ledger holds, received in the last CHANGES_WRITTEN_DAYS: written by ingest, from
    # source_documents, so a later run never hides one (the views check discards against it)
    # the weekly email's to-do list, each with the dates it belongs in the email (nudges.py, nudge/Code.js)
    Table("nudges", ("group", "item", "detail", "show_from", "show_until", "link", "run_id")),
    # each plan year's FSA money: elected on the pay stubs, reimbursed by deposits, left to claim (nudges.py)
    Table("fsa_balances", ("year", "account", "elected", "reimbursed", "left", "claim_deadline", "run_id")),
    # the receipt exporter's Gmail searches, from the receipt_sender and receipt_subject settings (settings.py)
    Table("receipt_searches", ("store", "senders", "subjects", "search", "run_id")),
    Table("changes_written", ("file_id", "file", "written_at")),
    Table("monthly_actuals", ("month", "category", "spend", "txn_count", "run_id", "computed_at")),
    Table("legacy_check", ("month", "category", "legacy", "computed", "diff", "run_id")),
    Table("tax_check", ("year", "line", "filed", "computed", "diff", "match", "run_id")),
    Table(
        "withholding_backtest",
        (
            "pay_date",
            "person",
            "employer",
            "stub_id",
            "kind",
            "base",
            "withheld",
            "expected",
            "residual",
            "explanation",
            "run_id",
        ),
    ),
    Table(
        "wi_withholding_backtest",
        (
            "pay_date",
            "person",
            "employer",
            "stub_id",
            "kind",
            "base",
            "withheld",
            "expected",
            "residual",
            "explanation",
            "run_id",
        ),
    ),
    Table(
        "pa_withholding_backtest",
        (
            "pay_date",
            "person",
            "employer",
            "stub_id",
            "kind",
            "base",
            "withheld",
            "expected",
            "residual",
            "explanation",
            "run_id",
        ),
    ),
    Table(
        "projection",
        (
            "year",
            "scenario",
            "agi",
            "deduction",
            "taxable",
            "total_tax",
            "withheld",
            "owe_or_refund",
            "safe_harbor",
            "safe_harbor_met",
            "extra_to_break_even",
            "wi_taxable",
            "wi_tax",
            "wi_withheld",
            "wi_owe_or_refund",
            "run_id",
        ),
    ),
    Table("elections", ("year", "person", "employer", "plan", "ytd", "projected", "limit", "gap", "status", "run_id")),
    # both returns line by line per scenario, beside the filed value once the return is in (projection.py)
    Table("tax_lines", ("year", "scenario", "order", "form", "line", "key", "label", "value", "filed", "run_id")),
    # what each projected line is made of (projection.working): wages and withholding so far and to come per
    # person and employer, capital gains by term, and the estimates used
    Table("tax_inputs", ("year", "key", "person", "employer", "part", "amount", "detail", "run_id")),
    # each fund as the ledger has it (editor.securities), for the Investments page to change its asset class
    Table("editor_securities", ("symbol", "name", "asset_class", "expense_ratio", "quote", "wash_group", "run_id")),
    # what withholding lands the year near the target refund (projection.withholding_plan)
    Table(
        "withholding_plan",
        (
            "year",
            "return",
            "person",
            "employer",
            "paychecks_left",
            "vest_rate",
            "projected",
            "target",
            "current_extra",
            "suggested_extra",
            "lands_at",
            "penalty_free",
            "note",
            "run_id",
        ),
    ),
    Table("tax_sources", ("year", "item", "value", "document", "page", "quote", "record", "run_id")),
    Table(
        "transfers",
        (
            "txn_a",
            "date_a",
            "account_a",
            "payee_a",
            "txn_b",
            "date_b",
            "account_b",
            "payee_b",
            "amount",
            "kind",
            "run_id",
        ),
        {"kind": ("transfer", "card_payment", "reversal")},
    ),
    Table("transfers_unmatched", ("txn_id", "date", "account_id", "amount", "payee_raw", "run_id")),
    Table("coverage", ("source", "kind", "newest", "age_days", "due_after_days", "waiting_lines", "status", "run_id")),
    Table(
        "balance_check",
        (
            "account_id",
            "as_of",
            "balance",
            "ledger_through",
            "implied_opening",
            "first_transaction",
            "status",
            "run_id",
        ),
    ),
    Table(
        "paychecks", ("date", "person", "employer", "amount", "txn_id", "account_id", "payee_raw", "status", "run_id")
    ),
    Table(
        "recurring",
        (
            "key",
            "category",
            "cadence",
            "typical",
            "latest",
            "charges",
            "first",
            "last",
            "next_expected",
            "status",
            "run_id",
        ),
    ),
    Table("budget_targets", ("month", "line", "category", "account", "priority", "rule", "target", "basis", "run_id")),
    Table("budget_vs_actual", ("month", "category", "group", "priority", "target", "actual", "remaining", "run_id")),
    # spending per category per day, every era, for the budget's comparisons of part of a period
    Table("daily_actuals", ("date", "category", "spend", "run_id")),
    # each investment account's money in and out each year, by kind (investments.flows)
    Table("investment_flows", ("year", "account_id", "kind", "amount", "rows", "run_id")),
    Table("sinking_funds", ("month", "line", "category", "set_aside", "spent", "balance", "run_id")),
    # every investment account on each month-end and today: what it held and was worth (valuation.py)
    Table(
        "investment_values",
        (
            "date",
            "account_id",
            "owner",
            "wrapper",
            "bucket",
            "symbol",
            "source",
            "asset_class",
            "shares",
            "price",
            "value",
            "basis",
            "run_id",
        ),
    ),
    # the allocation tree against its targets, today and after the proposed rebalance (allocation.py)
    Table(
        "allocation",
        (
            "node",
            "parent",
            "depth",
            "order",
            "label",
            "leaf",
            "target_share",
            "target_value",
            "current_value",
            "current_share",
            "delta_value",
            "proposed_value",
            "proposed_share",
            "run_id",
        ),
    ),
    Table("rebalance", ("account_id", "bucket", "symbol", "asset_class", "current", "proposed", "trade", "run_id")),
    # each part of a lot sold, as Form 8949 reports it, wash sales worked out (lots.py)
    Table(
        "form_8949",
        (
            "year",
            "sale_date",
            "account_id",
            "symbol",
            "shares",
            "acquired",
            "holding_from",
            "term",
            "proceeds",
            "basis",
            "wash_carried",
            "code",
            "adjustment",
            "gain",
            "lot_id",
            "piece",
            "replaced_by",
            "reported_basis",
            "note",
            "run_id",
        ),
    ),
    # what each lot in a taxable account still holds today: its basis (wash sales added) and holding period
    Table(
        "open_lots",
        (
            "lot_id",
            "piece",
            "account_id",
            "symbol",
            "acquired",
            "holding_from",
            "shares",
            "cost_per_share",
            "basis",
            "wash_added",
            "kind",
            "grant_id",
            "price",
            "value",
            "gain",
            "term_if_sold",
            "run_id",
        ),
    ),
    # net worth on each valuation date, by part (a tax bucket, home equity, other debt) and owner (networth.py)
    Table("net_worth", ("date", "part", "owner", "value", "run_id")),
    # each home's numbers (house.py): basis, value, loans, equity and what a sale would net
    Table("property_summary", ("property_id", "order", "key", "label", "value", "as_of", "note", "run_id")),
    Table("home_values", ("property_id", "as_of", "source", "value", "note", "run_id")),
    # each home's tax by year: as billed, and estimated for a year assessed but not yet billed (house.py)
    Table(
        "home_taxes",
        (
            "property_id",
            "tax_year",
            "assessed",
            "mill_rate",
            "total_taxes",
            "credits",
            "specials",
            "amount_due",
            "basis",
            "run_id",
        ),
        {"basis": ("billed", "estimated")},
    ),
    Table(
        "home_improvements",
        ("property_id", "date", "description", "kind", "cost", "credit", "basis_added", "receipt", "note", "run_id"),
    ),
    # each loan's details for the Home page's Mortgage tab: its terms, its facts and its schedule's milestones
    Table("loan_details", ("loan_id", "order", "key", "label", "value", "kind", "as_of", "note", "run_id")),
    # the retirement plan (the retirement package): each retirement year under each return model; each strategy at the
    # plan's year; the plan year by year for each strategy; Social Security by person and the earnings it
    # counts; and every assumption with where it came from and the setting that changes it
    Table(
        "retirement_years",
        (
            "retire_year",
            "ages",
            "model",
            "scenario",
            "success",
            "median_end",
            "p10_end",
            "short_from",
            "at_retirement",
            "first_spending",
            "withdrawal_rate",
            "chosen",
            "run_id",
        ),
    ),
    Table(
        "retirement_strategies",
        (
            "strategy",
            "label",
            "order",
            "convert_to",
            "rule",
            "sepp",
            "claims",
            "model",
            "success",
            "median_after_tax",
            "p10_after_tax",
            "median_tax",
            "median_irmaa",
            "median_penalty",
            "median_converted",
            "median_spent",
            "lowest_spending",
            "chosen",
            "run_id",
        ),
    ),
    Table(
        "retirement_plan",
        (
            "strategy",
            "year",
            "ages",
            "phase",
            "contributions",
            "spending",
            "health",
            "social_security",
            "rmd",
            "from_taxable",
            "from_pre_tax",
            "from_roth",
            "from_hsa",
            "converted",
            "ordinary_income",
            "capital_gains",
            "federal_tax",
            "state_tax",
            "irmaa",
            "penalty",
            "short",
            "taxable",
            "pre_tax",
            "roth",
            "hsa",
            "total",
            "p10_total",
            "p50_total",
            "p90_total",
            "run_id",
        ),
    ),
    Table(
        "retirement_social_security",
        (
            "person",
            "claim_age",
            "claim_year",
            "statement_monthly",
            "computed_monthly",
            "plan_monthly",
            "plan_yearly",
            "aime",
            "pia",
            "note",
            "run_id",
        ),
    ),
    Table("retirement_earnings", ("person", "year", "fica_earnings", "indexed", "counted", "basis", "run_id")),
    Table("retirement_assumptions", ("section", "key", "label", "value", "source", "setting", "run_id")),
    # the spending the plan uses, line by line (retirement.budget_table): now, in the plan's first year and in
    # the retirement year, with the retirement_spending setting that changes each line
    Table(
        "retirement_budget",
        (
            "line",
            "group",
            "kind",
            "now",
            "first_year",
            "at_retirement",
            "retire_year",
            "setting",
            "is_set",
            "lumpy",
            "note",
            "run_id",
        ),
    ),
    Table(
        "mortgage",
        (
            "month",
            "loan_id",
            "payment",
            "interest",
            "principal",
            "escrow",
            "extra_principal",
            "balance",
            "paid_from_ledger",
            "run_id",
        ),
    ),
    # each month's gifts by recipient (giving.py): what was given, and whether the recipient is deductible
    Table("giving", ("month", "key", "name", "category", "given", "gifts", "deductible", "run_id")),
    # each gift line's recipient (giving.gift_lines), for the views to list a recipient's gifts
    Table("gift_lines", ("txn_id", "month", "key", "category", "from_key", "run_id")),
    Table("flows", ("period", "level", "source", "target", "amount", "run_id"), {"level": ("category", "group")}),
    Table(
        "categorized",
        (
            "txn_id",
            "date",
            "account_id",
            "amount",
            "payee_raw",
            "payee_text",
            "key",
            "category",
            "splits",
            "items",
            "decided_by",
            "confidence",
            "status",
            "note",
            "one_off",
            "run_id",
        ),
    ),
    # what the views' editors show and send back (editor.py): the settings in force and the review queue
    Table(
        "editor_budget_lines",
        (
            "line",
            "effective",
            "category",
            "account",
            "priority",
            "rule",
            "amount",
            "per",
            "note",
            "in_force",
            "in_words",
            "this_month",
            "last_12_months",
            "scheduled",
            "spends_from",
            "run_id",
        ),
    ),
    # each scheduled amount in force, one per line and date: a new amount on that date replaces it
    Table(
        "editor_scheduled",
        ("line", "effective", "category", "account", "priority", "rule", "amount", "per", "note", "run_id"),
    ),
    Table("editor_payee_rules", ("pattern", "category", "precedence", "effective", "ask", "status", "run_id")),
    # each category in force; loan names the loan whose payment splits into it (its loan_split setting)
    Table("editor_categories", ("category", "group", "priority", "loan", "run_id")),
    Table("editor_aliases", ("alias", "category", "note", "run_id")),
    Table("editor_parameters", ("key", "value", "source", "about", "run_id")),
    Table("editor_parameter_history", ("key", "n", "value", "source", "run_id")),
    Table(
        "editor_review",
        (
            "key",
            "guess",
            "lines",
            "total",
            "example",
            "first",
            "last",
            "accounts",
            "guessed_by",
            "confidence",
            "kind",
            "note",
            "run_id",
        ),
    ),
    # the pay history (comp_events) for the Jobs editor, each row with how the stubs since it compare
    Table(
        "editor_jobs", ("effective", "person", "employer", "salary", "band", "rating", "fte", "note", "stubs", "run_id")
    ),
    # every line ascribed to each charge in the Recurring editor, and whether it counts (recurring.charge_history)
    Table("recurring_charges", ("key", "txn_id", "date", "spend", "payee", "counts", "why", "run_id")),
    Table("budget_checks", ("check", "kind", "about", "category", "label", "detail", "open", "run_id")),
    # how each floating target was worked out (budget.target_inputs): the months it averages, for the views
    Table(
        "target_inputs",
        (
            "month",
            "line",
            "category",
            "rule",
            "input",
            "spend",
            "left_out",
            "mean",
            "spread",
            "sigma",
            "value",
            "run_id",
        ),
    ),
    # recurring charges you track (recurring.subscriptions), and the Recurring editor's rows
    Table(
        "subscriptions",
        (
            "key",
            "category",
            "cadence",
            "charges",
            "first",
            "last",
            "latest",
            "previous",
            "yearly",
            "next_expected",
            "status",
            "change",
            "example",
            "note",
            "includes",
            "merged",
            "name",
            "run_id",
        ),
    ),
    # each filed year's income split three ways, for the Taxes page's history (taxhistory.py)
    Table(
        "tax_history",
        ("year", "jurisdiction", "split", "part", "order", "value", "total", "basis", "run_id"),
        {"basis": ("filed", "projected")},
    ),
    # every filed year's return as filed (return_facts), line by line, for the Taxes page's years (taxhistory.py)
    Table("filed_lines", ("year", "form", "order", "line", "key", "label", "value", "run_id")),
    Table("subscription_months", ("month", "key", "category", "group", "expected", "actual", "name", "run_id")),
    Table(
        "editor_subscriptions",
        (
            "key",
            "marked",
            "status",
            "cadence",
            "charges",
            "last",
            "latest",
            "yearly",
            "category",
            "example",
            "note",
            "includes",
            "merged",
            "name",
            "line",
            "run_id",
        ),
    ),
    # paychecks beside comp_events (paycheck.py): runs that agree, disagree or have no row in force
    Table(
        "pay_check",
        (
            "person",
            "employer",
            "kind",
            "from",
            "to",
            "paychecks",
            "stubs_annual",
            "comp_effective",
            "comp_employer",
            "comp_salary",
            "comp_fte",
            "comp_annual",
            "off",
            "status",
            "finding",
            "run_id",
        ),
    ),
    Table(
        "possible_duplicates",
        (
            "account_id",
            "amount",
            "txn_a",
            "date_a",
            "payee_a",
            "doc_a",
            "txn_b",
            "date_b",
            "payee_b",
            "doc_b",
            "run_id",
        ),
    ),
)


def table(tables, name):
    for t in tables:
        if t.name == name:
            return t
    raise KeyError(name)
