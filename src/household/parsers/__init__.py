"""Document parsers. Each module has sniff(text) and parse(text) -> Result.

Order matters only when two formats could both match; the most specific goes first.
A change set from the views goes first of all (it is JSON in a declared format, and its notes may quote
anything); then tax law sources, which quote the phrases other recognizers look for.
"""

from . import (
    adp_statement,
    amazon,
    boa_csv,
    changes,
    edvest,
    email_receipts,
    fidelity,
    fidelity_positions,
    google_takeout,
    documents,
    healthequity,
    healthequity_cash,
    earnings_statement,
    ofx,
    ofx_invest,
    pay_statement,
    paypal_qif,
    return_facts,
    netbenefits_statement,
    schwab_awards,
    schwab_equity,
    seed,
    ssa_statement,
    treasury_direct,
    stock_options,
    target,
    tax_law,
    usaa_csv,
    vanguard_plan,
)
from .common import ParseError, Result

PARSERS = (
    changes,
    tax_law,
    seed,
    return_facts,
    ssa_statement,
    treasury_direct,
    netbenefits_statement,
    schwab_awards,
    stock_options,
    schwab_equity,
    vanguard_plan,
    ofx_invest,
    ofx,
    fidelity_positions,
    fidelity,
    healthequity,
    healthequity_cash,
    edvest,
    usaa_csv,
    boa_csv,
    paypal_qif,
    amazon,
    google_takeout,
    target,
    email_receipts,
    pay_statement,
    earnings_statement,
    adp_statement,
    documents,
)


def identify(text):
    """The parser module that recognizes this text, or None."""
    return next((p for p in PARSERS if p.sniff(text)), None)
