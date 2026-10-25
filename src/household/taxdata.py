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

# Wisconsin, married filing jointly: taxable income at which each rate in WI_RATES begins.
# The 2025 instructions print no rate schedule; these reproduce every row of the tax table
# and the subtraction amounts of the tax computation worksheet (taxsources checks both).
WI_RATES = (0.035, 0.044, 0.053, 0.0765)
WI_BRACKETS_MFJ = {
    2025: (0, 19_580, 67_300, 431_060),
    2026: (0, 19_580, 67_300, 431_060),  # 2025 amounts until 2026's are published
}

# Wisconsin withholding: annual net wage at which each rate begins (Pub W-166), and the same
# bands of annual gross salary choose the flat rate on supplemental pay.
WI_WITHHOLDING_RATES = (0.0354, 0.0465, 0.053, 0.0765)
WI_WITHHOLDING_BRACKETS = (0, 12_760, 25_520, 280_950)

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
        # Pennsylvania: one flat rate on taxable income and on withholding
        "pa_rate": 0.0307,
        # Wisconsin withholding (Pub W-166 alternate method, unchanged since 2022)
        "wi_wh_exemption": 400,
        "wi_wh_married_deduction": 9_461,
        "wi_wh_married_phase_start": 25_727,
        "wi_wh_married_phase_rate": 0.20,
        "wi_wh_single_deduction": 6_702,
        "wi_wh_single_phase_start": 17_780,
        "wi_wh_single_phase_rate": 0.12,
        # Wisconsin income tax, married filing jointly
        "wi_exemption": None,
        "wi_standard_deduction_mfj": None,
        "wi_standard_deduction_phase_start_mfj": None,
        "wi_standard_deduction_phase_rate_mfj": None,
        "wi_itemized_credit_rate": None,
        "wi_married_couple_rate": None,
        "wi_married_couple_max_earned": None,
        "wi_school_property_credit_rate": None,
        "wi_school_property_credit_max": None,
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
        # Pennsylvania: one flat rate on taxable income and on withholding
        "pa_rate": 0.0307,
        # Wisconsin withholding (Pub W-166 alternate method, unchanged since 2022)
        "wi_wh_exemption": 400,
        "wi_wh_married_deduction": 9_461,
        "wi_wh_married_phase_start": 25_727,
        "wi_wh_married_phase_rate": 0.20,
        "wi_wh_single_deduction": 6_702,
        "wi_wh_single_phase_start": 17_780,
        "wi_wh_single_phase_rate": 0.12,
        # Wisconsin income tax, married filing jointly
        "wi_exemption": 700,
        "wi_standard_deduction_mfj": 25_110,
        "wi_standard_deduction_phase_start_mfj": 28_210,
        "wi_standard_deduction_phase_rate_mfj": 0.19778,
        "wi_itemized_credit_rate": 0.05,
        "wi_married_couple_rate": 0.03,
        "wi_married_couple_max_earned": 16_000,
        "wi_school_property_credit_rate": 0.12,
        "wi_school_property_credit_max": 300,
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
        # Pennsylvania: one flat rate on taxable income and on withholding
        "pa_rate": 0.0307,
        # Wisconsin withholding (Pub W-166 alternate method, unchanged since 2022)
        "wi_wh_exemption": 400,
        "wi_wh_married_deduction": 9_461,
        "wi_wh_married_phase_start": 25_727,
        "wi_wh_married_phase_rate": 0.20,
        "wi_wh_single_deduction": 6_702,
        "wi_wh_single_phase_start": 17_780,
        "wi_wh_single_phase_rate": 0.12,
        # Wisconsin income tax: 2025 amounts until the 2026 indexed amounts are published
        "wi_exemption": 700,
        "wi_standard_deduction_mfj": 25_110,
        "wi_standard_deduction_phase_start_mfj": 28_210,
        "wi_standard_deduction_phase_rate_mfj": 0.19778,
        "wi_itemized_credit_rate": 0.05,
        "wi_married_couple_rate": 0.03,
        "wi_married_couple_max_earned": 16_000,
        "wi_school_property_credit_rate": 0.12,
        "wi_school_property_credit_max": 300,
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
    "wi-form-1-instructions-2025": ("Wisconsin Form 1 Instructions (2025)", "dd7e88901ac4ec56"),
    "wi-form-1npr-instructions-2025": ("Wisconsin Form 1NPR Instructions (2025)",
                                       "ec79685479ed59e7"),  # leak-guard: allow (hash of a public PDF)
    "wi-publication-w-166-2026-01": ("Wisconsin Publication W-166 (1/26)", "c400ce62d7a945f1"),
    "wi-form-1-2025": ("Wisconsin Form 1 (2025, blank)", "428a8c3bab9cdfc8"),
    "pa-40-instructions-2025": ("Pennsylvania PA-40 Instructions (2025)", "e8996504a4302a65"),
    "pa-40-2025": ("Pennsylvania PA-40 (2025, blank)", "fcd7648601f3ce5a"),
    "pa-schedule-g-l-2024-03": ("Pennsylvania Schedule G-L (03-24)", "b30072eb0bacf5c2"),
    "pa-rev-415-2023-01": ("Pennsylvania REV-415 (01-23)", "e64161ea109692f7"),
}

# (year, item) -> (document, page, quote). Pages are PDF pages (1 = first), as a
# viewer numbers them. The quote appears on that page once whitespace is
# collapsed and dollar signs dropped, and for numbers it contains the value as
# printed. "brackets" checks every threshold; "withholding_mfj_table_floor" checks
# every row of Pub 15-T's annual MFJ table against the brackets shifted by it.
_LAW = "public-law-119-21"
_W166 = "wi-publication-w-166-2026-01"
_WI1 = "wi-form-1-instructions-2025"
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
    # Wisconsin withholding: W-166 (1/26) says its method has applied since January 1, 2022.
    "wi_withholding_brackets": (_W166, 26, "14,582.83 + 7.65% of excess over 280,950"),
    "wi_wh_exemption": (_W166, 26, "exemptions claimed by 400"),
    "wi_wh_married_deduction": (_W166, 26, "less than 25,727, deduction amount = 9,461"),
    "wi_wh_married_phase_start": (_W166, 26, "9,461 \u2013 0.20 (Annual gross earnings \u2013 25,727)"),
    "wi_wh_married_phase_rate": (_W166, 26, "subtracting from 9,461, 20% of annual gross earnings"),
    "wi_wh_single_deduction": (_W166, 26, "less than 17,780, deduction amount = 6,702"),
    "wi_wh_single_phase_start": (_W166, 26, "6,702 \u2013 0.12 (Annual gross earnings \u2013 17,780)"),
    "wi_wh_single_phase_rate": (_W166, 26, "subtracting from 6,702, 12% of annual gross earnings"),
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
    (2025, "wi_brackets"): (_WI1, 44, "less than 431,060"),
    (2025, "wi_exemption"): (_WI1, 15, "(700 or 250)"),
    (2025, "wi_standard_deduction_mfj"): (_WI1, 35, "0 13,390 13,560 25,110"),
    (2025, "wi_standard_deduction_phase_start_mfj"): (_WI1, 35, "2025 Standard Deduction Table"),
    (2025, "wi_standard_deduction_phase_rate_mfj"): (_WI1, 35, "2025 Standard Deduction Table"),
    (2025, "wi_school_property_credit_rate"): (_WI1, 20, "Homeowner\u2019s School Property Tax Credit Table"),
    (2025, "wi_school_property_credit_max"): (_WI1, 20, "can\u2019t be more than 300"),
    (2025, "pa_rate"): ("pa-40-instructions-2025", 1,
                        "The state income tax rate for 2025 is 3.07 percent (0.0307)"),
    (2026, "pa_rate"): ("pa-rev-415-2023-01", 13, "by the current per - centage rate, 3.07%"),
    (2025, "wi_itemized_credit_rate"): ("wi-form-1-2025", 4, "Rate of credit is .05 (5%)"),
    (2025, "wi_married_couple_rate"): ("wi-form-1-2025", 4, "Rate of credit is .03 (3%)"),
    (2025, "wi_married_couple_max_earned"): ("wi-form-1-2025", 4, "If more than 16,000, fill in 16,000"),
}

# Items whose value is not printed as such but is proved by reproducing a printed table.
TABLE_CHECKED = {
    "brackets", "withholding_mfj_table_floor", "wi_brackets", "wi_withholding_brackets",
    "wi_standard_deduction_phase_start_mfj", "wi_standard_deduction_phase_rate_mfj",
    "wi_school_property_credit_rate",
}

# Rules the code implements (not single numbers), with where each is written down.
METHODS = {
    "withholding: annual percentage method (Worksheet 1A)": ("publication-15-t-2026", 10, "Worksheet 1A"),
    "withholding: no income tax withheld on group-term life": (
        "publication-15-b-2026", 15, "withhold federal income tax or pay FUTA tax on any group-term"),
    "tax: qualified dividends and capital gain tax worksheet": (
        "instructions-for-form-1040-2025", 37, "Qualified Dividends and Capital Gain Tax Worksheet"),
    "safe harbor: 110% of prior-year tax above 150,000 AGI": ("publication-505-2026", 21, "110%"),
    "wi withholding: flat percentages on supplemental pay": (_W166, 25, "Approved Flat Percentages"),
    "pa: tax is the flat rate on adjusted taxable income (PA-40 line 12)": (
        "pa-40-2025", 2, "PA Tax Liability. Multiply Line 11 by 3.07 percent (0.0307)"),
    "pa: 401(k) deferrals are compensation": (
        "pa-rev-415-2023-01", 8, "Payments by employers into pension plans, 401(k) plans"),
    "pa: health benefit plans and group-term life are not compensation": (
        "pa-rev-415-2023-01", 7, "most group-term life insurance"),
    "pa: Schedule G-L resident credit, ratio to six decimal places": (
        "pa-schedule-g-l-2024-03", 1, "Enter the result here (calculate to six decimal places)"),
}

# Values with no source document on file, and why they are trusted anyway.
UNCITED = {
    2024: "Rev. Proc. 2023-34, Notice 2023-75, Rev. Proc. 2023-23 and Pub 15-T (2024) are not on file; "
          "every 2024 pay stub reproduces under these values in the withholding backtest",
    (2025, "ss_wage_base"): "SSA's 2025 announcement (not on file); the filed 2025 return's excess Social "
                            "Security credit reproduces only with this wage base",
    **{(2026, k): "Wisconsin publishes 2026 indexed amounts with the 2026 Form 1 instructions (about "
                  "December 2026); the 2025 amount is used until then"
       for k in ("wi_brackets", "wi_exemption", "wi_standard_deduction_mfj",
                 "wi_standard_deduction_phase_start_mfj", "wi_standard_deduction_phase_rate_mfj",
                 "wi_itemized_credit_rate", "wi_married_couple_rate", "wi_married_couple_max_earned",
                 "wi_school_property_credit_rate", "wi_school_property_credit_max")},
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


def wi_brackets(year):
    if year not in WI_BRACKETS_MFJ:
        raise KeyError(f"no Wisconsin brackets for {year}")
    return tuple(zip(WI_BRACKETS_MFJ[year], WI_RATES))


def wi_withholding_brackets():
    return tuple(zip(WI_WITHHOLDING_BRACKETS, WI_WITHHOLDING_RATES))
