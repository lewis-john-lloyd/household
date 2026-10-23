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
          {"source": ("legacy", "csv", "pdf", "feed", "hand"),
           "one_off": ("TRUE", "FALSE")}),
    Table("txn_corrections", ("txn_id", "field", "value", "recorded_at", "reason", "source"),
          {"field": ("category", "one_off", "note", "split", "void")}),
    Table("balances", ("as_of", "account_id", "balance", "doc_id")),
    Table("categories", ("category", "group", "priority"),
          {"priority": ("high", "medium", "low")}),
    Table("payee_rules", ("pattern", "category", "precedence", "effective")),
    Table("budget_rules", ("effective", "category", "rule", "params", "note"),
          {"rule": ("fixed", "trailing", "seasonal", "sinking_fund", "scheduled", "end")}),
    Table("allocation_targets", ("effective", "node", "parent", "weight")),
    Table("parameters", ("year", "jurisdiction", "key", "value", "source")),
    Table("tax_brackets", ("year", "jurisdiction", "filing_status", "floor", "rate")),
    Table("return_facts", ("year", "kind", "key", "person", "value", "source_doc", "note", "doc_id"),
          {"kind": ("input", "filed", "estimate")}),
    Table("comp_events", ("effective", "person", "employer", "salary", "band", "fte")),
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
)

REPORTS = (
    Table("run_log", ("run_id", "computed_at", "stage", "detail")),
    Table("monthly_actuals", ("month", "category", "spend", "txn_count", "run_id", "computed_at")),
    Table("legacy_check", ("month", "category", "legacy", "computed", "diff", "run_id")),
    Table("tax_check", ("year", "line", "filed", "computed", "diff", "match", "run_id")),
    Table("withholding_backtest", ("pay_date", "person", "employer", "stub_id", "kind", "base",
                                   "withheld", "expected", "residual", "explanation", "run_id")),
    Table("projection", ("year", "scenario", "agi", "deduction", "taxable", "total_tax", "withheld",
                         "owe_or_refund", "safe_harbor", "safe_harbor_met", "run_id")),
    Table("elections", ("year", "person", "employer", "plan", "projected", "limit", "gap", "status",
                        "run_id")),
    Table("possible_duplicates", ("account_id", "amount", "txn_a", "date_a", "payee_a", "doc_a",
                                  "txn_b", "date_b", "payee_b", "doc_b", "run_id")),
)


def table(tables, name):
    for t in tables:
        if t.name == name:
            return t
    raise KeyError(name)
