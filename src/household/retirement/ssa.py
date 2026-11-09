"""Social Security: each person's benefit at a claiming age, from their earnings record."""

from .. import retirelaw as law

# --- Social Security --------------------------------------------------------------------------------------


def _claim_year(p, age):
    return p.born.year + age


def _first_year_share(p):
    """The share of the claiming year a benefit is paid for: from the birthday month on."""
    return (13 - p.born.month) / 12


def pia(p, retire_year, today_year):
    """(AIME, PIA) for a person who works until retire_year at the last posted year's earnings, figured as the
    statement figures it: earnings indexed to the AWI of two years ago, this year's bend points."""
    if not p.earnings:
        return 0, p.fra_monthly
    last = max(p.earnings)
    earnings = dict(p.earnings)
    for y in range(last + 1, retire_year):
        earnings[y] = p.earnings[last]
    index_year = min(p.born.year + 60, today_year - 2)
    a = law.aime(earnings, index_year)
    return a, law.pia(a, law.bend_points(min(p.born.year + 62, today_year)))


def benefits(h, retire_year, claims, today_year):
    """{person: {year: yearly benefit}}, each adult's own benefit or the spouse's half when more, from the
    year they claim (in part for that year), less the cut a setting may assume."""
    pias = {p.name: pia(p, retire_year, today_year)[1] for p in h.people}
    out = {}
    for p in h.people:
        age = claims.get(p.name, p.claim_age)
        own = pias[p.name] * law.claim_factor(p.born.year, age * 12)
        others = [q for q in h.people if q.name != p.name]
        by_year = {}
        for y in h.years:
            if y < _claim_year(p, age):
                continue
            monthly = own
            for q in others:
                if y >= _claim_year(q, claims.get(q.name, q.claim_age)):
                    extra = law.SPOUSE_SHARE * pias[q.name] - pias[p.name]
                    if extra > 0:
                        monthly = own + extra * law.spouse_factor(p.born.year, age * 12)
            share = _first_year_share(p) if y == _claim_year(p, age) else 1.0
            cut = 1 - h.ss_cut if y >= h.ss_cut_from else 1.0
            by_year[y] = monthly * 12 * share * cut
        out[p.name] = by_year
    return out
