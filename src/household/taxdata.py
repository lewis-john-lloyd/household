"""Public tax law: brackets, thresholds and limits by year, each with its source.

These are facts about the law, not about the household, so they live with the
code and change only in a reviewed release. Household facts (W-4 settings,
elections, return lines) live in the ledger. Every value cites the page of a
source document that says it (CITATIONS), and tools/verify_tax_law.py checks
each citation against the PDFs before a release.

Withholding: Publication 15-T's annual percentage method for a 2020-or-later
Form W-4 subtracts a fixed amount on line 1g (12,900 for married filing jointly
when the Step 2 box is not checked) and then applies a table that is the year's
rate schedule shifted up by `withholding_mfj_table_floor`. Equivalently, the
tentative annual withholding is tax(annual wages - 1g amount - table floor)
under that year's brackets. Payroll excludes imputed group-term life insurance
from income tax withholding (it is still taxable wages).
"""

RATES = (0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37)

# Married filing jointly: taxable income at which each rate in RATES begins.
MFJ_BRACKETS = {
    2024: (0, 23_200, 94_300, 201_050, 383_900, 487_450, 731_200),
    2025: (0, 23_850, 96_950, 206_700, 394_600, 501_050, 751_600),
    2026: (0, 24_800, 100_800, 211_400, 403_550, 512_450, 768_700),
}

PARAMS = {
    2024: {
        "standard_deduction_mfj": 29_200,
        "qdcg_zero_top_mfj": 94_050,
        "qdcg_fifteen_top_mfj": 583_750,
        "salt_cap": 10_000,
        "salt_phase_threshold": float("inf"),
        "salt_floor": 10_000,
        "salt_phase_rate": 0.0,
        "charity_floor_rate": 0.0,
        "itemized_cap_applies": False,
        "addl_medicare_threshold_mfj": 250_000,
        "addl_medicare_withholding_threshold": 200_000,
        "niit_threshold_mfj": 250_000,
        "ss_wage_base": 168_600,
        "ss_rate": 0.062,
        "medicare_rate": 0.0145,
        "addl_medicare_rate": 0.009,
        "niit_rate": 0.038,
        "elective_deferral_402g": 23_000,
        "annual_additions_415c": 69_000,
        "hsa_family": 8_300,
        "dependent_care_fsa_mfj": 5_000,
        "supplemental_flat_rate": 0.22,
        "supplemental_mandatory_rate": 0.37,
        "supplemental_mandatory_over": 1_000_000,
        "withholding_line_1g_mfj": 12_900,
        "withholding_mfj_table_floor": 16_300,
    },
    2025: {
        "standard_deduction_mfj": 31_500,
        "qdcg_zero_top_mfj": 96_700,
        "qdcg_fifteen_top_mfj": 600_050,
        "salt_cap": 40_000,
        "salt_phase_threshold": 500_000,
        "salt_floor": 10_000,
        "salt_phase_rate": 0.30,
        "charity_floor_rate": 0.0,
        "itemized_cap_applies": False,
        "addl_medicare_threshold_mfj": 250_000,
        "addl_medicare_withholding_threshold": 200_000,
        "niit_threshold_mfj": 250_000,
        "ss_wage_base": 176_100,
        "ss_rate": 0.062,
        "medicare_rate": 0.0145,
        "addl_medicare_rate": 0.009,
        "niit_rate": 0.038,
        "elective_deferral_402g": 23_500,
        "annual_additions_415c": 70_000,
        "hsa_family": 8_550,
        "dependent_care_fsa_mfj": 5_000,
        "supplemental_flat_rate": 0.22,
        "supplemental_mandatory_rate": 0.37,
        "supplemental_mandatory_over": 1_000_000,
        "withholding_line_1g_mfj": 12_900,
        "withholding_mfj_table_floor": 17_100,
    },
    2026: {
        "standard_deduction_mfj": 32_200,
        "qdcg_zero_top_mfj": 98_900,
        "qdcg_fifteen_top_mfj": 613_700,
        "salt_cap": 40_400,
        "salt_phase_threshold": 505_000,
        "salt_floor": 10_000,
        "salt_phase_rate": 0.30,
        "charity_floor_rate": 0.005,
        "itemized_cap_applies": True,
        "addl_medicare_threshold_mfj": 250_000,
        "addl_medicare_withholding_threshold": 200_000,
        "niit_threshold_mfj": 250_000,
        "ss_wage_base": 184_500,
        "ss_rate": 0.062,
        "medicare_rate": 0.0145,
        "addl_medicare_rate": 0.009,
        "niit_rate": 0.038,
        "elective_deferral_402g": 24_500,
        "annual_additions_415c": 72_000,
        "hsa_family": 8_750,
        "dependent_care_fsa_mfj": 7_500,
        "supplemental_flat_rate": 0.22,
        "supplemental_mandatory_rate": 0.37,
        "supplemental_mandatory_over": 1_000_000,
        "withholding_line_1g_mfj": 12_900,
        "withholding_mfj_table_floor": 19_300,
    },
}

# Source documents, by the label the tax_law parser gives them, with the first 16
# hex digits of the SHA-256 of the exact PDF every citation below was checked
# against (tools/verify_tax_law.py). The PDFs live in the record store.
DOCUMENTS = {
    "public-law-119-21": ("Public Law 119-21 (July 4, 2025)", "42f86c3d408bccb1"),
    "rev-proc-2024-40": ("Rev. Proc. 2024-40", "4de9db6b6662b5c3"),
    "rev-proc-2025-32": ("Rev. Proc. 2025-32", "e9ada115fb43a4af"),
    "rev-proc-2024-25": ("Rev. Proc. 2024-25", "6093d2e3923f1f85"),
    "rev-proc-2025-19": ("Rev. Proc. 2025-19", "ff2ba4bac62ef694"),
    "notice-2024-80": ("Notice 2024-80", "cbf7843c9c253174"),
    "notice-2025-67": ("Notice 2025-67", "1eea8f141b0cddd1"),
    "publication-15-t-2025": ("Publication 15-T (2025)", "dbf925980a6a79a2"),
    "publication-15-t-2026": ("Publication 15-T (2026)", "31b3e2428628e8d2"),
    "publication-15-2026": ("Publication 15 (2026)", "b46c3622439d8521"),
    "publication-15-b-2026": ("Publication 15-B (2026)", "a0af8513d6f45e65"),
    "publication-505-2026": ("Publication 505 (2026)", "c69e87caa93cdefa"),
    "instructions-for-form-1040-2025": ("Instructions for Form 1040 (2025)", "482e9c487c608f1b"),
    "instructions-for-schedule-a-2025": ("Instructions for Schedule A (2025)", "b0999b12d9dc4b13"),
    "instructions-for-form-8959-2025": ("Instructions for Form 8959 (2025)", "1d223be73b4bac95"),
    "instructions-for-form-8960-2025": ("Instructions for Form 8960 (2025)", "b10fbb23ff4a053d"),
}

# (year, item) -> (document, page, quote). Pages are PDF pages (1 = first), as a
# viewer numbers them. The quote appears on that page once whitespace is
# collapsed and dollar signs dropped, and for numbers it contains the value as
# printed. "brackets" checks every threshold; "withholding_mfj_table_floor" checks
# every row of Pub 15-T's annual MFJ table against the brackets shifted by it.
_LAW = "public-law-119-21"
_BOTH = {  # cited the same way for 2025 and 2026
    "salt_floor": (_LAW, 99, "less than 10,000"),
    "salt_phase_rate": (_LAW, 99, "reduced by 30 percent of the excess"),
    "addl_medicare_threshold_mfj": ("instructions-for-form-8959-2025", 1, "Married filing jointly 250,000"),
    "addl_medicare_rate": ("instructions-for-form-8959-2025", 1, "A 0.9% Additional Medicare"),
    "addl_medicare_withholding_threshold": ("publication-15-2026", 30, "in excess of 200,000"),
    "niit_threshold_mfj": ("instructions-for-form-8960-2025", 2, "Qualifying surviving spouse is 250,000"),
    "niit_rate": ("instructions-for-form-8960-2025", 2, "must pay 3.8%"),
    "ss_rate": ("publication-15-2026", 1, "6.2% each"),
    "medicare_rate": ("publication-15-2026", 2,
                      "1.45% each for the employee and employer, unchanged from 2025"),
    "supplemental_flat_rate": ("publication-15-2026", 2, "remains 22%"),
    "supplemental_mandatory_rate": ("publication-15-2026", 2, "(37% if"),
    "supplemental_mandatory_over": ("publication-15-2026", 2, "exceed 1 million"),
}
CITATIONS = {
    **{(2025, k): v for k, v in _BOTH.items()},
    **{(2026, k): v for k, v in _BOTH.items()},
    (2025, "brackets"): ("rev-proc-2024-40", 5, "751,600"),
    (2025, "standard_deduction_mfj"): ("rev-proc-2025-32", 9, "31,500"),
    (2025, "qdcg_zero_top_mfj"): ("rev-proc-2024-40", 7, "Surviving Spouse 96,700"),
    (2025, "qdcg_fifteen_top_mfj"): ("rev-proc-2024-40", 7, "96,700 600,050"),
    (2025, "salt_cap"): (_LAW, 99, "calendar year 2025, 40,000"),
    (2025, "salt_phase_threshold"): (_LAW, 99, "calendar year 2025, 500,000"),
    # Not yet in effect in 2025: cited to each provision's effective date.
    (2025, "charity_floor_rate"): (_LAW, 167, "beginning after December 31, 2025. SEC. 70426"),
    (2025, "itemized_cap_applies"): (_LAW, 95, "beginning after December 31, 2025. SEC. 70112"),
    (2025, "elective_deferral_402g"): ("notice-2024-80", 1, "from 23,000 to 23,500"),
    (2025, "annual_additions_415c"): ("notice-2024-80", 1, "from 69,000 to 70,000"),
    (2025, "hsa_family"): ("rev-proc-2024-25", 2,
                           "family coverage under a high deductible health plan is 8,550"),
    (2025, "dependent_care_fsa_mfj"): (_LAW, 144, "striking \u2018\u20185,000"),
    (2025, "withholding_line_1g_mfj"): ("publication-15-t-2025", 5, "12,900 if the employee"),
    (2025, "withholding_mfj_table_floor"): ("publication-15-t-2025", 11, "0 17,100 0.00 0%"),
    (2026, "brackets"): ("rev-proc-2025-32", 10, "768,700"),
    (2026, "standard_deduction_mfj"): ("rev-proc-2025-32", 18, "32,200"),
    (2026, "qdcg_zero_top_mfj"): ("rev-proc-2025-32", 13, "Surviving Spouse 98,900"),
    (2026, "qdcg_fifteen_top_mfj"): ("rev-proc-2025-32", 13, "98,900 613,700"),
    (2026, "salt_cap"): (_LAW, 99, "calendar year 2026, 40,400"),
    (2026, "salt_phase_threshold"): (_LAW, 99, "calendar year 2026, 505,000"),
    (2026, "charity_floor_rate"): (_LAW, 165, "exceeds 0.5 percent of the taxpayer"),
    (2026, "itemized_cap_applies"): (_LAW, 94, "reduced by 2\u204437 of the lesser of"),
    (2026, "ss_wage_base"): ("publication-15-2026", 2, "wage base limit is 184,500"),
    (2026, "elective_deferral_402g"): ("notice-2025-67", 1, "from 23,500 to 24,500"),
    (2026, "annual_additions_415c"): ("notice-2025-67", 1, "from 70,000 to 72,000"),
    (2026, "hsa_family"): ("rev-proc-2025-19", 2,
                           "family coverage under a high deductible health plan is 8,750"),
    (2026, "dependent_care_fsa_mfj"): (_LAW, 144, "inserting \u2018\u20187,500"),
    (2026, "withholding_line_1g_mfj"): ("publication-15-t-2026", 6, "12,900 if the employee"),
    (2026, "withholding_mfj_table_floor"): ("publication-15-t-2026", 12, "0 19,300 0.00 0%"),
}

# Rules the code implements (not single numbers), with where each is written down.
METHODS = {
    "withholding: annual percentage method (Worksheet 1A)": ("publication-15-t-2026", 10, "Worksheet 1A"),
    "withholding: no income tax withheld on group-term life": (
        "publication-15-b-2026", 15, "withhold federal income tax or pay FUTA tax on any group-term"),
    "tax: qualified dividends and capital gain tax worksheet": (
        "instructions-for-form-1040-2025", 37, "Qualified Dividends and Capital Gain Tax Worksheet"),
    "safe harbor: 110% of prior-year tax above 150,000 AGI": ("publication-505-2026", 21, "110%"),
}

# Values with no source document on file, and why they are trusted anyway.
UNCITED = {
    2024: "Rev. Proc. 2023-34, Notice 2023-75, Rev. Proc. 2023-23 and Pub 15-T (2024) are not on file; "
          "every 2024 pay stub reproduces under these values in the withholding backtest",
    (2025, "ss_wage_base"): "SSA's 2025 announcement (not on file); the filed 2025 return's excess Social "
                            "Security credit reproduces only with this wage base",
}


def citation(year, item):
    """(document, page, quote) for one value, or None."""
    return CITATIONS.get((year, item))


def params(year):
    if year not in PARAMS:
        raise KeyError(f"no tax parameters for {year}")
    return PARAMS[year]


def brackets(year):
    if year not in MFJ_BRACKETS:
        raise KeyError(f"no brackets for {year}")
    return tuple(zip(MFJ_BRACKETS[year], RATES))
