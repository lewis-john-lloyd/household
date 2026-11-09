"""Retirement: when the household can stop working, and how to draw its savings down, with the taxes and the
age rules that decide what a dollar in each kind of account is worth.

Everything is in today's dollars. The model runs a year at a time from next year to the year the younger adult
reaches the plan's last age (retirement_plan_to_age, default 95), with both alive throughout.

While working, each account grows at the year's return and takes the contributions the last 12 months show
(investment_activity: workplace plans, IRAs and HSAs, each row's money source deciding pre-tax or Roth), and
the taxable accounts take the household's taxable savings (setting retirement_taxable_savings, else the last
12 months: what the savings categories took from the bank, less what went into IRAs and 529s, plus the value
of shares that vested). Social Security credits each year worked at the last posted year's earnings.

From the retirement year on, each year:
  1. Spending is the last 12 months' actual spending (the categories that are spending: not income, savings,
     transfers or retirement_leave_out), each category or group replaced from a year on by the
     retirement_spending:<name> settings, less each loan's payments after its payoff, plus health cover:
     retirement_health_pre65 a year for the household (half for each adult under 65), and from 65 Medicare
     Part B, the income-related surcharges (IRMAA, from the return two years before) and
     retirement_health_supplement a person.
  2. Money comes in from Social Security (each adult's own benefit at their claiming age, or half the
     other's at full retirement age when that is more, once both have claimed), required minimum
     distributions from pre-tax accounts at the RMD age, a 72(t) plan's payments if the strategy has one,
     the taxable accounts' dividends (retirement_taxable_yield), and the HSA for the health costs it may pay.
  3. The rest, and the tax on everything, comes out of the accounts in the strategy's order; a Roth
     conversion strategy then fills the year's ordinary income to the top of a bracket. Taxes are worked out
     the way the forms do: federal brackets with the 65-and-over deductions, capital gains at 0/15/20%, the
     taxable part of Social Security, the net investment income tax, the 10% on money taken early, and
     Wisconsin's (no tax on Social Security, 30% of long-term gains and 24,000 a person of retirement income
     excluded from 67). Withdrawals and taxes are solved together, since each depends on the other.
  4. What is left grows at the year's return.

Before 59 1/2 money comes first from the taxable accounts, Roth contributions and conversions five years old,
the HSA's health costs, the plan of an employer left in or after the year turning 55, and a 72(t) plan; only
then from accounts that pay the 10%.

Returns come three ways, side by side: fixed yearly returns (retirement_cagrs, nominal, less
retirement_inflation) over the whole plan; each historical sequence since 1928 starting in the retirement year
(markets.py), with the years before at history's average; and Monte Carlo (each year drawn at random from
history, the stocks', bonds' and inflation's returns together) over the whole plan. The portfolio is
retirement_stock_share stocks (default: the allocation targets' share) rebalanced yearly.

A strategy is an order of withdrawal (conventional: taxable, then pre-tax, then Roth; proportional: from
each in proportion to its balance; bracket: pre-tax up to the top of the 12% bracket, then taxable, then
Roth), a Roth conversion target (none, or fill to the top of the 12%, 22% or 24% bracket), a spending rule
(constant real spending; Guyton-Klinger guardrails, cutting 10% when the withdrawal rate is 20% over its start
and raising 10% when 20% under; or variable percentage withdrawal, the portfolio's annuity payment over the
years left), and whether a 72(t) plan bridges the years before 59 1/2.

The retirement year is retirement_year if set, else the earliest year whose Monte Carlo success (no year
short of money) reaches retirement_success_target (default 0.9). The plan's strategy is retirement_strategy
if set, else the one that ends with the most after-tax wealth among those whose success is within a point of
the best.
"""

from .model import (  # noqa: F401
    BRACKET_ORDER_RATE,
    CONVERSIONS,
    DEFAULTS,
    GAIN_RATE_AT_END,
    GUARD_BAND,
    GUARD_LAST_YEARS,
    GUARD_STEP,
    HEIR_RATE,
    Household,
    ITERATIONS,
    LAW_YEAR,
    ORDERS,
    ORDER_NAMES,
    Person,
    RULES,
    RULE_NAMES,
    SEED,
    Strategy,
    TRUSTEES_CUT_YEAR,
    VPW_RETURNS,
    parse_strategy,
)
from .ssa import (  # noqa: F401
    benefits,
    pia,
)
from .tax import (  # noqa: F401
    FED,
    P,
    WI,
    bracket_top,
    deduction,
    irmaa,
    taxable_social_security,
    taxes,
)
from .simulation import (  # noqa: F401
    central,
    fixed_paths,
    historical_paths,
    monte_carlo_paths,
    run_all,
    simulate,
    success,
)
from .reports import (  # noqa: F401
    EARNINGS_COLUMNS,
    PLAN_COLUMNS,
    SEARCH_AGAIN,
    SS_COLUMNS,
    STRATEGY_COLUMNS,
    YEAR_COLUMNS,
    best,
    first_spending,
    plan_rows,
    run,
    search,
    social_security,
    strategies,
    success_at,
    variants,
)
from .ledger import (  # noqa: F401
    BUCKET_MAP,
    BUDGET_COLUMNS,
    LUMPY_MIN,
    LUMPY_SHARE,
    STOCK_CLASSES,
    allocation_stock_share,
    baseline,
    budget_table,
    build,
    spending_path,
    spending_steps,
)
