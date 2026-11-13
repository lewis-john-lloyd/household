"""The figures the views offer to add an estimate for are the ones the projection reads from estimates."""

import inspect
import re
from pathlib import Path

import pandas as pd

from household import projection, tax
from test_projection import stubs_through

TAXES_TS = Path(__file__).resolve().parents[1] / "web" / "src" / "page" / "taxes.ts"


def test_the_views_offer_exactly_the_figures_the_projection_takes_from_estimates():
    text = TAXES_TS.read_text(encoding="utf-8")
    block = text[text.index("var TAX_ESTIMATES") :]
    block = block[: block.index("];")]
    offered = re.findall(r'\["([a-z0-9_]+)", "', block)
    assert offered == list(projection.ESTIMABLE)


def test_each_estimable_figure_is_read_by_the_return_and_not_set_by_the_projection_itself():
    reads = set(re.findall(r'g\("([a-z0-9_]+)"\)', inspect.getsource(tax.federal_return)))
    for key in projection.ESTIMABLE:
        assert key in reads, key
    # an estimate for a figure the projection sets from stubs or sales would be thrown away
    facts = pd.DataFrame(
        [
            {"year": "2026", "kind": "estimate", "key": k, "person": "", "value": "7", "source_doc": "", "note": ""}
            for k in projection.ESTIMABLE
        ]
    )
    i, _ = projection.inputs_for(2026, stubs_through(24), pd.DataFrame(), facts)
    for key in projection.ESTIMABLE:
        assert i.get(key) == 7.0, key
