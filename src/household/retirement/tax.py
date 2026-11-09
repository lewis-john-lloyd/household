"""Federal and Wisconsin tax in retirement, vectorized over the paths."""

from collections.abc import Sequence

import numpy as np
from numpy import ndarray

from .. import retirelaw as law
from .. import taxdata
from .model import LAW_YEAR

# --- tax, vectorized over paths ---------------------------------------------------------------------------


def _schedule(x: ndarray, steps: Sequence[tuple[float, float]]) -> ndarray:
    tax = np.zeros_like(x)
    for i, (floor, rate) in enumerate(steps):
        top = steps[i + 1][0] if i + 1 < len(steps) else np.inf
        tax += rate * np.clip(x - floor, 0, top - floor)
    return tax


FED = taxdata.brackets(LAW_YEAR)
WI = taxdata.wi_brackets(LAW_YEAR)
P = taxdata.params(LAW_YEAR)


def bracket_top(rate: float) -> int:
    """Taxable income at the top of the federal bracket with this rate."""
    floors = [f for f, r in FED]
    return floors[[r for _, r in FED].index(rate) + 1]


def taxable_social_security(ss: ndarray, other: ndarray, deflator: float | ndarray) -> ndarray:
    """IRC 86 on a joint return: other is income before Social Security (AGI without it)."""
    b1, b2 = (b / deflator for b in law.SS_TAX_BASE_MFJ)
    prov = other + 0.5 * ss
    mid = np.minimum(0.5 * np.maximum(prov - b1, 0), 0.5 * ss)
    high = np.minimum(0.85 * ss, 0.85 * np.maximum(prov - b2, 0) + np.minimum(0.5 * (b2 - b1), 0.5 * ss))
    return np.where(prov <= b1, 0.0, np.where(prov <= b2, mid, high))


def deduction(n65: int, agi: ndarray, year: int) -> int | ndarray:
    d = P["standard_deduction_mfj"] + law.AGED_DEDUCTION_MFJ * n65
    if law.SENIOR_YEARS[0] <= year <= law.SENIOR_YEARS[1]:
        d = d + n65 * np.maximum(
            law.SENIOR_DEDUCTION - law.SENIOR_PHASE_RATE * np.maximum(agi - law.SENIOR_PHASE_START_MFJ, 0), 0
        )
    return d


def taxes(
    ordinary: ndarray,
    gains: ndarray,
    dividends: ndarray,
    ss: ndarray,
    penalized: ndarray,
    excluded: ndarray,
    n65: int,
    n_people: int,
    deflator: float | ndarray,
    year: int,
) -> tuple[ndarray, ndarray, ndarray, ndarray]:
    """(federal, wisconsin, agi, taxable Social Security) for a year: ordinary income other than Social
    Security, long-term gains, qualified dividends, benefits, the part that pays the 10% early, and the
    retirement income Wisconsin excludes."""
    preferred_income = gains + dividends
    ss_taxed = taxable_social_security(ss, ordinary + preferred_income, deflator)
    agi = ordinary + preferred_income + ss_taxed
    ti = np.maximum(agi - deduction(n65, agi, year), 0)
    pref = np.minimum(preferred_income, ti)
    ordinary_ti = ti - pref
    zero, fifteen = P["qdcg_zero_top_mfj"], P["qdcg_fifteen_top_mfj"]
    at0 = np.maximum(np.minimum(ti, zero) - ordinary_ti, 0)
    at15 = np.minimum(pref - at0, np.maximum(np.minimum(ti, fifteen) - np.maximum(ordinary_ti, zero), 0))
    at20 = pref - at0 - at15
    federal = _schedule(ordinary_ti, FED) + 0.15 * at15 + 0.20 * at20
    federal += law.NIIT_RATE * np.minimum(preferred_income, np.maximum(agi - law.NIIT_THRESHOLD_MFJ / deflator, 0))
    penalty = law.EARLY_PENALTY * penalized
    wi_income = np.maximum(agi - ss_taxed - law.WI_LTCG_EXCLUSION * gains - excluded, 0)
    full, start, rate = (
        P["wi_standard_deduction_mfj"],
        P["wi_standard_deduction_phase_start_mfj"],
        P["wi_standard_deduction_phase_rate_mfj"],
    )
    std = np.maximum(full - rate * np.maximum(wi_income - start, 0), 0)
    exemptions = (P["wi_exemption"] * n_people + law.WI_AGED_EXEMPTION * n65) / deflator
    wisconsin = _schedule(np.maximum(wi_income - std - exemptions, 0), WI) + law.WI_EARLY_SHARE * penalty
    return federal + penalty, wisconsin, agi, ss_taxed


def irmaa(magi: ndarray, enrolled: int) -> tuple[ndarray, ndarray]:
    """Yearly Part B and the Part D surcharge for the people enrolled, by the MAGI two years before."""
    floors = np.array([t[0] for t in law.IRMAA_MFJ])
    tier = np.searchsorted(floors, magi, side="right") - 1
    b = np.array([t[1] for t in law.IRMAA_MFJ])[tier]
    d = np.array([t[2] for t in law.IRMAA_MFJ])[tier]
    return (b + d) * 12 * enrolled, (b - law.IRMAA_MFJ[0][1] + d) * 12 * enrolled
