"""Public law the retirement model uses: Social Security's benefit formula, required minimum distributions,
the taxation of benefits, Medicare's income-related premiums, the federal deductions for those 65 and over,
Wisconsin's treatment of retirement income, and the rules on taking retirement money before 59 and a half.

Like taxdata.py these are facts about the law, not about the household; each block names its source. The
retirement model works in today's dollars, so an amount the law indexes to inflation (brackets, deductions,
IRMAA's tiers) is held as it is today, and one the law fixes in nominal dollars (the thresholds for taxing
Social Security, the net investment income tax's) shrinks each year by the assumed inflation (real()).
"""

# --- Social Security --------------------------------------------------------------------------------------

# National average wage index (SSA Office of the Chief Actuary, www.ssa.gov/OACT/COLA/awiseries.html)
AWI = {
    1951: 2799.16, 1952: 2973.32, 1953: 3139.44, 1954: 3155.64, 1955: 3301.44, 1956: 3532.36, 1957: 3641.72,
    1958: 3673.80, 1959: 3855.80, 1960: 4007.12, 1961: 4086.76, 1962: 4291.40, 1963: 4396.64, 1964: 4576.32,
    1965: 4658.72, 1966: 4938.36, 1967: 5213.44, 1968: 5571.76, 1969: 5893.76, 1970: 6186.24, 1971: 6497.08,
    1972: 7133.80, 1973: 7580.16, 1974: 8030.76, 1975: 8630.92, 1976: 9226.48, 1977: 9779.44, 1978: 10556.03,
    1979: 11479.46, 1980: 12513.46, 1981: 13773.10, 1982: 14531.34, 1983: 15239.24, 1984: 16135.07,
    1985: 16822.51, 1986: 17321.82, 1987: 18426.51, 1988: 19334.04, 1989: 20099.55, 1990: 21027.98,
    1991: 21811.60, 1992: 22935.42, 1993: 23132.67, 1994: 23753.53, 1995: 24705.66, 1996: 25913.90,
    1997: 27426.00, 1998: 28861.44, 1999: 30469.84, 2000: 32154.82, 2001: 32921.92, 2002: 33252.09,
    2003: 34064.95, 2004: 35648.55, 2005: 36952.94, 2006: 38651.41, 2007: 40405.48, 2008: 41334.97,
    2009: 40711.61, 2010: 41673.83, 2011: 42979.61, 2012: 44321.67, 2013: 44888.16, 2014: 46481.52,
    2015: 48098.63, 2016: 48642.15, 2017: 50321.89, 2018: 52145.80, 2019: 54099.99, 2020: 55628.60,
    2021: 60575.07, 2022: 63795.13, 2023: 66621.80, 2024: 69846.57, 2025: 72800.33,
}
# The PIA formula's bend points for the year a person turns 62 are 180 and 1,085 scaled by AWI two years
# earlier over AWI 1977, rounded to the dollar; the factors are 90%, 32% and 15% (SSA, "Benefit formula
# bend points", www.ssa.gov/OACT/COLA/bendpoints.html; Social Security Act 215(a)). The PIA is cut to the
# dime. Average indexed monthly earnings (AIME) are the 35 highest years of earnings, each indexed to the
# AWI of the year before eligibility (earnings from then on count as they are), over 420 months, cut to
# the dollar.
BEND_BASE = (180, 1085)
BEND_AWI_YEAR = 1977
PIA_FACTORS = (0.90, 0.32, 0.15)
COMPUTATION_YEARS = 35
# Full retirement age is 67 for those born in 1960 or later (Social Security Act 216(l)); earlier birth
# years step down two months a year to 66 for 1943 to 1954.
FRA_MONTHS = {1955: 66 * 12 + 2, 1956: 66 * 12 + 4, 1957: 66 * 12 + 6, 1958: 66 * 12 + 8, 1959: 66 * 12 + 10}
# Claiming before full retirement age cuts the benefit 5/9 of 1% a month for the first 36 months and 5/12
# of 1% for each month beyond; a spouse's benefit 25/36 of 1% and then 5/12 of 1%. Each month claimed after
# full retirement age, to 70, adds 2/3 of 1% (born 1943 or later). (www.ssa.gov/benefits/retirement/
# planner/agereduction.html, delayret.html)
EARLY_FIRST, EARLY_LATER, SPOUSE_FIRST = 5 / 900, 5 / 1200, 25 / 3600
DELAY_PER_MONTH = 2 / 300
SPOUSE_SHARE = 0.5
# The part of benefits that is taxable (IRC 86): for a joint return, half of the benefits over a provisional
# income of 32,000, up to 85% above 44,000. These are not indexed.
SS_TAX_BASE_MFJ = (32_000, 44_000)


def fra_months(birth_year):
    if birth_year >= 1960:
        return 67 * 12
    if birth_year <= 1954:
        return 66 * 12 if birth_year >= 1943 else 65 * 12
    return FRA_MONTHS[birth_year]


def bend_points(eligible_year):
    """The PIA bend points for someone eligible (turning 62) in that year, from the AWI two years before
    (the latest published AWI when that year's is not out yet)."""
    awi = AWI[min(eligible_year - 2, max(AWI))]
    return tuple(round(b * awi / AWI[BEND_AWI_YEAR]) for b in BEND_BASE)


def aime(earnings, index_year):
    """Average indexed monthly earnings: earnings {year: dollars} indexed to index_year's AWI (later years as
    they are), the highest 35 years, over 420 months, cut to the dollar."""
    base = AWI[min(index_year, max(AWI))]
    indexed = [e * base / AWI[y] if y <= index_year and y in AWI else e for y, e in earnings.items()]
    return int(sum(sorted(indexed, reverse=True)[:COMPUTATION_YEARS]) / (COMPUTATION_YEARS * 12))


def pia(monthly_earnings, bends):
    first, second = bends
    a, b, c = PIA_FACTORS
    amount = (a * min(monthly_earnings, first) + b * max(min(monthly_earnings, second) - first, 0)
              + c * max(monthly_earnings - second, 0))
    return int(amount * 10 + 1e-9) / 10


def claim_factor(birth_year, claim_months):
    """The share of the PIA paid when claiming at claim_months of age (62*12 to 70*12)."""
    early = fra_months(birth_year) - claim_months
    if early > 0:
        return 1 - (min(early, 36) * EARLY_FIRST + max(early - 36, 0) * EARLY_LATER)
    late = min(-early, 70 * 12 - fra_months(birth_year))
    return 1 + late * DELAY_PER_MONTH


def spouse_factor(birth_year, claim_months):
    """The share of the spouse's benefit paid when claiming it at claim_months (no credit for waiting)."""
    early = max(fra_months(birth_year) - claim_months, 0)
    return 1 - (min(early, 36) * SPOUSE_FIRST + max(early - 36, 0) * EARLY_LATER)


# --- Required minimum distributions -----------------------------------------------------------------------

# The age RMDs begin (IRC 401(a)(9)(C)(v) as SECURE 2.0 section 107 amended it): 73 for those born 1951 to
# 1959, 75 for those born 1960 or later.
def rmd_age(birth_year):
    if birth_year >= 1960:
        return 75
    if birth_year >= 1951:
        return 73
    return 72


# Uniform Lifetime Table (26 CFR 1.401(a)(9)-9(c)), ages 72 to 120 and over: an owner's RMD is the prior
# year-end balance over the distribution period for their age that year.
UNIFORM_LIFETIME = {
    72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4,
    82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2, 91: 11.5,
    92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4, 101: 6.0, 102: 5.6,
    103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1, 108: 3.9, 109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3,
    113: 3.1, 114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5, 119: 2.3, 120: 2.0,
}


def distribution_period(age):
    return UNIFORM_LIFETIME[min(max(age, 72), 120)]


# Single Life Table (26 CFR 1.401(a)(9)-9(b)), ages 40 to 75: the life expectancy a 72(t) payment plan's
# amortization runs over.
SINGLE_LIFE = {
    40: 45.7, 41: 44.8, 42: 43.8, 43: 42.9, 44: 41.9, 45: 41.0, 46: 40.0, 47: 39.0, 48: 38.1, 49: 37.1,
    50: 36.2, 51: 35.3, 52: 34.3, 53: 33.4, 54: 32.5, 55: 31.6, 56: 30.6, 57: 29.8, 58: 28.9, 59: 28.0,
    60: 27.1, 61: 26.2, 62: 25.4, 63: 24.5, 64: 23.7, 65: 22.9, 66: 22.0, 67: 21.2, 68: 20.4, 69: 19.6,
    70: 18.8, 71: 18.0, 72: 17.2, 73: 16.4, 74: 15.6, 75: 14.8,
}

# --- Taking money out before 59 and a half ----------------------------------------------------------------

# A distribution before 59 1/2 adds 10% federal tax (IRC 72(t)(1)) and Wisconsin adds 33% of that federal tax
# (Wis. Stat. 71.83(1)(e)), unless an exception applies:
#   - separation from the employer in or after the year the employee turns 55: that employer's plan only, not
#     an IRA (IRC 72(t)(2)(A)(v));
#   - substantially equal periodic payments from an IRA (IRC 72(t)(2)(A)(iv)), here by the amortization method
#     at an interest rate of up to the greater of 5% or 120% of the federal mid-term rate (Notice 2022-6) over
#     the Single Life Table; the payments must run five years or to 59 1/2, whichever is later;
#   - a Roth IRA gives back contributions first and then conversions, oldest first; a conversion taken within
#     five years of the year it was made pays the 10% (IRC 408A(d)(3)(F), (d)(4)).
EARLY_AGE = 59.5
EARLY_PENALTY = 0.10
WI_EARLY_SHARE = 0.33
RULE_OF_55 = 55
SEPP_RATE = 0.05
SEPP_MIN_YEARS = 5
CONVERSION_WAIT = 5


def sepp_payment(balance, age):
    """The yearly 72(t) payment by amortization at SEPP_RATE over the Single Life Table."""
    n = SINGLE_LIFE[min(max(int(age), min(SINGLE_LIFE)), max(SINGLE_LIFE))]
    return balance * SEPP_RATE / (1 - (1 + SEPP_RATE) ** -n)


# --- Federal tax for those 65 and over --------------------------------------------------------------------

# 2026 (Rev. Proc. 2025-32): an added standard deduction of 1,650 for each spouse 65 or over; for 2025 to 2028
# a further deduction of 6,000 for each spouse 65 or over, less 6% of modified AGI over 150,000 on a joint
# return (IRC 151(d)(5)(C) as Public Law 119-21 section 70103 added it).
AGED_DEDUCTION_MFJ = 1_650
SENIOR_DEDUCTION = 6_000
SENIOR_PHASE_START_MFJ = 150_000
SENIOR_PHASE_RATE = 0.06
SENIOR_YEARS = (2025, 2028)
NIIT_THRESHOLD_MFJ = 250_000  # IRC 1411, not indexed
NIIT_RATE = 0.038

# --- Medicare ---------------------------------------------------------------------------------------------

MEDICARE_AGE = 65
# 2026 Part B premium and IRMAA (CMS fact sheet, "2026 Medicare Parts A & B Premiums and Deductibles"):
# per person a month, by the joint return's modified AGI two years earlier (the lower edge of each tier), with
# the Part D surcharge on top of the plan's own premium. The tiers are indexed to inflation.
IRMAA_LOOKBACK = 2
IRMAA_MFJ = ((0, 202.90, 0.0), (218_000, 284.10, 14.50), (274_000, 405.80, 37.50), (342_000, 527.50, 60.40),
             (410_000, 649.20, 83.30), (750_000, 689.90, 91.00))


# --- Wisconsin --------------------------------------------------------------------------------------------

# Social Security benefits are not taxed (Wis. Stat. 71.05(6)(b)6.); 30% of net long-term capital gain is
# excluded (71.05(6)(b)9.); from 2025 each spouse who is 67 or over at the end of the year may subtract up to
# 24,000 of payments from qualified retirement plans and IRAs (2025 Wisconsin Act 15); each person 65 or
# over adds 250 to the 700 personal exemption (71.07(8)(b)).
WI_LTCG_EXCLUSION = 0.30
WI_RETIREMENT_EXCLUSION = 24_000
WI_RETIREMENT_AGE = 67
WI_AGED_EXEMPTION = 250


def real(amount, years, inflation):
    """A nominal amount fixed in law, in today's dollars that many years on."""
    return amount / (1 + inflation) ** max(years, 0)
