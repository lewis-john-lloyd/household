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
    Table("accounts", ("account_id", "institution", "owner", "type", "tax_treatment",
                       "opened", "closed", "last4", "expected_docs", "max_gap_days"),
          {"type": ("checking", "savings", "credit_card", "brokerage", "retirement",
                    "hsa", "education", "loan", "other"),
           "tax_treatment": ("taxable", "pre_tax", "roth", "hsa", "education", "none")}),
    Table("transactions", ("txn_id", "date", "account_id", "amount", "payee_raw", "category",
                           "one_off", "note", "source", "era", "doc_id", "recorded_at"),
          {"source": ("legacy", "csv", "ofx", "pdf", "feed", "hand"),
           "one_off": ("TRUE", "FALSE")}),
    Table("txn_corrections", ("txn_id", "field", "value", "recorded_at", "reason", "source"),
          {"field": ("category", "one_off", "note", "split", "void")}),
    Table("balances", ("as_of", "account_id", "balance", "doc_id")),
    # who a payment service says a payment went to or came from, for naming the bank or card
    # line it funded ("PAYPAL *XYZ" on a card is a payment to a named merchant for an item)
    Table("payment_details", ("detail_id", "date", "amount", "service", "kind", "counterparty", "memo",
                              "doc_id")),
    # what an Amazon order history export says was bought (or refunded), trimmed to what matching needs
    Table("order_items", ("item_id", "service", "order_id", "kind", "order_date", "date", "amount", "card",
                          "department", "item", "website", "doc_id"),
          {"kind": ("item", "refund")}),
    # a person's category for one item an order history or receipt listed (the latest per item wins), so a
    # line those items explain splits by it
    Table("item_categories", ("item_id", "category", "recorded_at", "source")),
    Table("categories", ("category", "group", "priority"),
          {"priority": ("high", "medium", "low")}),
    # old category names (the 2017 to 2024 books) and the category each now counts as
    Table("category_aliases", ("alias", "category", "note")),
    # a pattern on the payee and the category it means from `effective` on; ask TRUE only pre-fills the
    # guess and still asks about each line (paper checks)
    Table("payee_rules", ("pattern", "category", "precedence", "effective", "ask"),
          {"ask": ("TRUE", "FALSE")}),
    # one row per budget line per change (see budget.py); scheduled rows are one-off amounts in their month
    Table("budget_rules", ("line", "effective", "category", "account", "priority", "rule", "amount", "per",
                           "note"),
          {"rule": ("fixed", "seasonal", "trailing", "lookback", "sinking_fund", "scheduled", "end"),
           "priority": ("high", "medium", "low"),
           "per": ("monthly", "biweekly", "weekly", "semi_monthly", "quarterly", "semi_annually", "annually")}),
    Table("allocation_targets", ("effective", "node", "parent", "weight")),
    Table("parameters", ("year", "jurisdiction", "key", "value", "source")),
    Table("return_facts", ("year", "kind", "key", "person", "value", "source_doc", "note", "doc_id"),
          {"kind": ("input", "filed", "estimate")}),
    # the household's history of pay changes: each raise, promotion, new job or schedule change
    Table("comp_events", ("effective", "person", "employer", "salary", "band", "rating", "fte", "note")),
    Table("paystubs", ("pay_date", "period_start", "period_end", "person", "employer",
                       "stub_id", "line_item", "kind", "amount", "ytd_amount", "doc_id"),
          {"kind": ("income", "pre_tax", "tax", "post_tax", "employer", "memo")}),
    Table("equity_vests", ("vest_date", "grant_id", "award_date", "net_shares", "fmv",
                           "deposit_date", "doc_id")),
    Table("equity_sales", ("sale_date", "grant_id", "vest_date", "shares", "sale_price",
                           "proceeds", "basis", "gain", "term", "doc_id"),
          {"term": ("short_term", "long_term")}),
    Table("holdings", ("as_of", "account_id", "symbol", "shares", "value", "doc_id")),
    Table("securities", ("symbol", "asset_class", "expense_ratio", "wash_group")),
    Table("loans", ("loan_id", "lender", "originated", "principal", "rate", "term_months", "escrow")),
    Table("loan_events", ("date", "loan_id", "kind", "amount", "doc_id"),
          {"kind": ("payment", "prepayment", "recast", "rate_change")}),
    Table("source_documents", ("doc_id", "kind", "sha256", "original_name", "drive_link",
                               "account_id", "covers_from", "covers_to", "parser",
                               "received_at", "precedence")),
    Table("proposal_decisions", ("proposal_key", "kind", "decided_at", "decision", "note"),
          {"decision": ("approve", "reject", "snooze")}),
    # the review queue: a work surface, not a record. Each ingest run reads the answers typed here
    # into proposal_decisions and txn_corrections, then rewrites the tab with what is still open.
    Table("review", ("key", "answer", "split", "guess", "lines", "total", "example", "first", "last", "accounts",
                     "guessed_by", "confidence", "kind", "note"),
          {"kind": ("payee", "line")}),
)

REPORTS = (
    Table("run_log", ("run_id", "computed_at", "stage", "detail")),
    Table("run_notes", ("run_id", "stage", "at", "n", "message")),
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
    Table("withholding_backtest", ("pay_date", "person", "employer", "stub_id", "kind", "base",
                                   "withheld", "expected", "residual", "explanation", "run_id")),
    Table("wi_withholding_backtest", ("pay_date", "person", "employer", "stub_id", "kind", "base",
                                      "withheld", "expected", "residual", "explanation", "run_id")),
    Table("pa_withholding_backtest", ("pay_date", "person", "employer", "stub_id", "kind", "base",
                                      "withheld", "expected", "residual", "explanation", "run_id")),
    Table("projection", ("year", "scenario", "agi", "deduction", "taxable", "total_tax", "withheld",
                         "owe_or_refund", "safe_harbor", "safe_harbor_met", "extra_to_break_even",
                         "wi_taxable", "wi_tax",
                         "wi_withheld", "wi_owe_or_refund", "run_id")),
    Table("elections", ("year", "person", "employer", "plan", "ytd", "projected", "limit", "gap", "status",
                        "run_id")),
    # both returns line by line per scenario, beside the filed value once the return is in (projection.py)
    Table("tax_lines", ("year", "scenario", "order", "form", "line", "key", "label", "value", "filed", "run_id")),
    # what withholding lands the year near the target refund (projection.withholding_plan)
    Table("withholding_plan", ("year", "return", "person", "employer", "paychecks_left", "vest_rate", "projected",
                               "target", "current_extra", "suggested_extra", "lands_at", "penalty_free", "note",
                               "run_id")),
    Table("tax_sources", ("year", "item", "value", "document", "page", "quote", "record", "run_id")),
    Table("transfers", ("txn_a", "date_a", "account_a", "payee_a", "txn_b", "date_b", "account_b",
                        "payee_b", "amount", "kind", "run_id"),
          {"kind": ("transfer", "card_payment", "reversal")}),
    Table("transfers_unmatched", ("txn_id", "date", "account_id", "amount", "payee_raw", "run_id")),
    Table("coverage", ("source", "kind", "newest", "age_days", "due_after_days", "waiting_lines", "status",
                       "run_id")),
    Table("balance_check", ("account_id", "as_of", "balance", "ledger_through", "implied_opening",
                            "first_transaction", "status", "run_id")),
    Table("paychecks", ("date", "person", "employer", "amount", "txn_id", "account_id", "payee_raw", "status",
                        "run_id")),
    Table("recurring", ("key", "category", "cadence", "typical", "latest", "charges", "first", "last",
                        "next_expected", "status", "run_id")),
    Table("budget_targets", ("month", "line", "category", "account", "priority", "rule", "target", "basis",
                             "run_id")),
    Table("budget_vs_actual", ("month", "category", "group", "priority", "target", "actual", "remaining",
                               "run_id")),
    Table("sinking_funds", ("month", "line", "category", "set_aside", "spent", "balance", "run_id")),
    Table("mortgage", ("month", "loan_id", "payment", "interest", "principal", "escrow", "extra_principal",
                       "balance", "paid_from_ledger", "run_id")),
    Table("flows", ("period", "level", "source", "target", "amount", "run_id"),
          {"level": ("category", "group")}),
    Table("categorized", ("txn_id", "date", "account_id", "amount", "payee_raw", "payee_text", "key",
                          "category", "splits", "items", "decided_by", "confidence", "status", "note", "one_off",
                          "run_id")),
    # what the views' editors show and send back (editor.py): the settings in force and the review queue
    Table("editor_budget_lines", ("line", "effective", "category", "account", "priority", "rule", "amount", "per",
                                  "note", "in_force", "in_words", "this_month", "last_12_months", "scheduled",
                                  "spends_from", "run_id")),
    # each scheduled amount in force, one per line and date: a new amount on that date replaces it
    Table("editor_scheduled", ("line", "effective", "category", "account", "priority", "rule", "amount", "per", "note",
                               "run_id")),
    Table("editor_payee_rules", ("pattern", "category", "precedence", "effective", "ask", "status", "run_id")),
    Table("editor_categories", ("category", "group", "priority", "run_id")),
    Table("editor_aliases", ("alias", "category", "note", "run_id")),
    Table("editor_parameters", ("key", "value", "source", "about", "run_id")),
    Table("editor_review", ("key", "guess", "lines", "total", "example", "first", "last", "accounts", "guessed_by",
                            "confidence", "kind", "note", "run_id")),
    Table("possible_duplicates", ("account_id", "amount", "txn_a", "date_a", "payee_a", "doc_a",
                                  "txn_b", "date_b", "payee_b", "doc_b", "run_id")),
)


def table(tables, name):
    for t in tables:
        if t.name == name:
            return t
    raise KeyError(name)
