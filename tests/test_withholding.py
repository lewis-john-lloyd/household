"""Withholding recomputation and backtest on synthetic stubs."""

import pandas as pd
import pytest

from household import withholding


def test_matches_the_published_2026_biweekly_mfj_table():
    # Pub 15-T 2026 biweekly MFJ standard: 446.16 plus 22% over 5,115 (thresholds rounded to dollars)
    assert withholding.regular_withholding(7_000, 26, 2026) == pytest.approx(446.16 + 0.22 * 1_885, abs=0.15)
    assert withholding.regular_withholding(1_200, 26, 2026) == 0


def test_w4_entries_shift_withholding():
    base = withholding.regular_withholding(6_000, 26, 2025)
    assert withholding.regular_withholding(6_000, 26, 2025, step3=4_000, step4c=525) == pytest.approx(
        base - 4_000 / 26 + 525, abs=0.01)


def test_decode_prefers_known_step3():
    residual = round(525 - 4_000 / 26, 2)
    assert withholding.decode_w4(residual, 26, prefer=[4_000]) == (525, 4_000)
    assert withholding.decode_w4(1_295, 26) == (1_295, 0)
    assert withholding.decode_w4(0.37, 26) is None


def stub(pay_date, stub_id, base, fit, gtl=0.0, start="2025-01-01", end="2025-01-14"):
    common = {"pay_date": pay_date, "period_start": start, "period_end": end, "person": "alex",
              "employer": "Acme", "stub_id": stub_id, "doc_id": "d"}
    rows = [("Regular Pay", "income", base + gtl), ("FIT Taxable Wages", "memo", base + gtl),
            ("Federal Income Tax", "tax", fit)]
    if gtl:
        rows.append(("Group Term Life", "income", gtl))
        rows[0] = ("Regular Pay", "income", base)
    return [{**common, "line_item": a, "kind": k, "amount": v, "ytd_amount": 0} for a, k, v in rows]


def test_backtest_explains_settings_flat_rates_and_flags_one_offs():
    expected = withholding.regular_withholding(5_000, 26, 2025)
    rows = (stub("2025-01-17", "1", 5_000, expected, gtl=20)
            + stub("2025-01-31", "2", 5_000, round(expected + 100, 2))
            + stub("2025-02-14", "3", 10_000, 2_200.00)
            + stub("2025-02-28", "4", 5_000, round(expected + 0.37, 2)))
    bt = withholding.backtest(pd.DataFrame(rows)).set_index("stub_id")
    assert bt.loc["1", "explanation"] == "matches the table"  # group-term life excluded
    assert bt.loc["2", "explanation"] == "W-4: Step 4(c) 100"
    assert (bt.loc["3", "kind"], bt.loc["3", "explanation"]) == ("supplemental", "flat 22%")
    assert bt.loc["4", "explanation"].startswith("unexplained")


def test_years_without_tables_are_marked_unchecked():
    bt = withholding.backtest(pd.DataFrame(stub("1999-01-15", "1", 5_000, 700, start="1999-01-01",
                                                end="1999-01-14")))
    assert bt.loc[0, "kind"] == "unchecked"
