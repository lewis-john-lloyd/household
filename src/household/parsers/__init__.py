"""Document parsers. Each module has sniff(text) and parse(text) -> Result.

Order matters only when two formats could both match; the most specific goes first.
Tax law sources go first of all: they quote the phrases other recognizers look for.
"""

from . import (boa_csv, documents, earnings_statement, ofx, pay_statement, paypal_qif, return_facts,
               schwab_equity, seed, tax_law, usaa_csv)
from .common import ParseError, Result

PARSERS = (tax_law, seed, return_facts, schwab_equity, ofx, usaa_csv, boa_csv, paypal_qif, pay_statement,
           earnings_statement, documents)


def identify(text):
    """The parser module that recognizes this text, or None."""
    return next((p for p in PARSERS if p.sniff(text)), None)
