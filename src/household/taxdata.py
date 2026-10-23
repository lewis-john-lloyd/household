"""Public tax law: brackets, thresholds and limits by year, each with its source.

These are facts about the law, not about the household, so they live with the
code and change only in a reviewed release. Household facts (W-4 settings,
elections, return lines) live in the ledger.

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

SOURCES = {
    2024: (
        "Rev. Proc. 2023-34 (brackets, standard deduction, capital gains thresholds, 402(g)/415(c) "
        "via Notice 2023-75, HSA via Rev. Proc. 2023-23)",
        "Publication 15-T (2024): line 1g 12,900 MFJ; MFJ standard table floor 16,300",
        "SSA 2024 wage base; SALT cap 10,000 before Public Law 119-21",
    ),
    2025: (
        "Rev. Proc. 2024-40 (brackets, capital gains thresholds, HSA via Rev. Proc. 2024-25)",
        "Public Law 119-21 (2025 reconciliation act): standard deduction, SALT cap and phase-down",
        "Publication 15-T (2025): annual percentage method, line 1g, MFJ standard table",
        "Notice 2024-80 (402(g), 415(c)); SSA 2025 wage base",
        "Checked against the household's filed 2025 return (QD/CG worksheet thresholds)",
    ),
    2026: (
        "Rev. Proc. 2025-32 (brackets, standard deduction, capital gains thresholds)",
        "Public Law 119-21: SALT cap indexing, 0.5% charitable floor, 2/37 itemized limitation, "
        "dependent care FSA limit",
        "Publication 15-T (2026): line 1g 12,900 MFJ; MFJ standard biweekly table "
        "(0% below 1,238 = (19,300 + 12,900) / 26)",
        "SSA 2026 wage base; 402(g) and 415(c) as shown on 2026 pay stubs; HSA Rev. Proc. 2025-19",
    ),
}


def params(year):
    if year not in PARAMS:
        raise KeyError(f"no tax parameters for {year}")
    return PARAMS[year]


def brackets(year):
    if year not in MFJ_BRACKETS:
        raise KeyError(f"no brackets for {year}")
    return tuple(zip(MFJ_BRACKETS[year], RATES))
