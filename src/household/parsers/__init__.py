"""Document parsers. Each module has sniff(text) and parse(text) -> Result.

Order matters only when two formats could both match; the most specific goes first.
"""

from . import accounts_seed, documents, earnings_statement, pay_statement, schwab_equity
from .common import ParseError, Result

PARSERS = (accounts_seed, schwab_equity, pay_statement, earnings_statement, documents)


def identify(text):
    """The parser module that recognizes this text, or None."""
    return next((p for p in PARSERS if p.sniff(text)), None)
