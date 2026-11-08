"""Document parsers. Each module has sniff(text) and parse(text) -> Result.

Order matters only when two formats could both match; the most specific goes first.
A change set from the views goes first of all (it is JSON in a declared format, and its notes may quote
anything); then tax law sources, which quote the phrases other recognizers look for.
"""

from . import (adp_statement, amazon, boa_csv, changes, email_receipts, fidelity, google_takeout, documents, earnings_statement, ofx, pay_statement, paypal_qif, return_facts,
               schwab_equity, seed, target, tax_law, usaa_csv)
from .common import ParseError, Result

PARSERS = (changes, tax_law, seed, return_facts, schwab_equity, ofx, fidelity, usaa_csv, boa_csv, paypal_qif, amazon, google_takeout, target,
           email_receipts,
           pay_statement, earnings_statement, adp_statement, documents)


def identify(text):
    """The parser module that recognizes this text, or None."""
    return next((p for p in PARSERS if p.sniff(text)), None)
