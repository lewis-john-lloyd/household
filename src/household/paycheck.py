"""Pay on the stubs against the pay history in comp_events.

Each regular paycheck's base pay (salary, regular hours and paid time off: BASE) is put on a yearly
footing by its pay period (a two-week period is 26 a year) and set beside the comp_events row in
force on the period's first day: its salary times its FTE (1 when blank). A job's first and last
paychecks are left out, since they cover part of a period.

Two kinds of run are judged:
  salary   two or more paychecks in a row with the same base pay: off by more than SALARY_TOLERANCE
           (a raise of a percent or two shows) disagrees
  hourly   the rest under one comp_events row, three or more of them: hours vary, so their median is
           judged, off by more than HOURLY_TOLERANCE

Only paychecks since January 1 of the year before the latest stub's are judged. A run that disagrees
says what a comp_events row would need, so the Jobs editor can offer it.
"""

import re

import pandas as pd

BASE = re.compile(
    r"^(salary|regular|pto|holiday|bereave|.*time off|telework|fmla|sick|vacation(?!.*p[a-z]*out)|"
    r"leave w/?o pay|jury)",
    re.I,
)
SALARY_TOLERANCE = 0.005
HOURLY_TOLERANCE = 0.05
MIN_HOURLY = 3
NO_ROW = -1  # a paycheck with no comp_events row in force
COLUMNS = [
    "person",
    "employer",
    "kind",
    "from",
    "to",
    "paychecks",
    "stubs_annual",
    "comp_effective",
    "comp_employer",
    "comp_salary",
    "comp_fte",
    "comp_annual",
    "off",
    "status",
    "finding",
]


def periods_per_year(start, end):
    """Pay periods a year for a period from start to end (dates as text): weekly, every two weeks,
    twice a month or monthly."""
    try:
        days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    except (ValueError, TypeError):
        return 26
    if days <= 8:
        return 52
    if days <= 14:
        return 26
    if days <= 17:
        return 24
    return 12


def rows_in_force(comp_events):
    """comp_events with a date, one row per (effective, person, employer): a later row for the same
    change corrects it. Salary and FTE as numbers (FTE 1 when blank)."""
    if comp_events is None or comp_events.empty:
        return pd.DataFrame(columns=["effective", "person", "employer", "salary", "fte"])
    df = comp_events.copy()
    df["effective"] = df["effective"].astype(str).str[:10]
    df = df[df["effective"].str.match(r"^\d{4}-\d{2}-\d{2}$")]
    df = df.drop_duplicates(["effective", "person", "employer"], keep="last")
    df["salary"] = pd.to_numeric(df["salary"].astype(str).str.replace(",", ""), errors="coerce")
    fte = pd.to_numeric(df.get("fte", pd.Series(dtype=str)).astype(str), errors="coerce")
    df["fte"] = fte.where(fte > 0, 1.0).fillna(1.0)
    return df.sort_values(["person", "effective"], kind="stable").reset_index(drop=True)


def paychecks(paystubs):
    """One row per regular paycheck with base pay: person, employer, pay_date, period, base, annual."""
    if paystubs is None or paystubs.empty:
        return pd.DataFrame(columns=["person", "employer", "pay_date", "period_start", "period_end", "base", "annual"])
    lines = paystubs[(paystubs["kind"] == "income") & paystubs["line_item"].astype(str).str.match(BASE)].copy()
    lines["amount"] = pd.to_numeric(lines["amount"], errors="coerce").fillna(0.0)
    keys = ["person", "employer", "pay_date", "stub_id", "period_start", "period_end"]
    out = lines.groupby(keys, as_index=False)["amount"].sum().rename(columns={"amount": "base"})
    out = out[out["base"] > 0]
    out["base"] = out["base"].round(2)
    out["annual"] = [
        round(b * periods_per_year(s, e), 2) for b, s, e in zip(out["base"], out["period_start"], out["period_end"])
    ]
    return out.sort_values(["person", "employer", "pay_date"], kind="stable").reset_index(drop=True)


def _in_force(rows, person, day):
    mine = rows[(rows["person"] == person) & (rows["effective"] <= day)]
    return mine.iloc[-1] if len(mine) else None


def check(paystubs, comp_events, since=None):
    """Runs of paychecks beside comp_events (COLUMNS); status agrees, disagrees or no row."""
    pay = paychecks(paystubs)
    if pay.empty:
        return pd.DataFrame(columns=COLUMNS)
    rows = rows_in_force(comp_events)
    latest = str(pay["pay_date"].max())
    since = since or f"{int(latest[:4]) - 1}-01-01"
    out = []
    for (person, employer), g in pay.groupby(["person", "employer"], sort=False):
        g = g.iloc[1:-1] if len(g) > 2 else g.iloc[0:0]  # a job's first and last paychecks are partial
        g = g[g["pay_date"].astype(str) >= since].reset_index(drop=True)
        if g.empty:
            continue
        forced = [_in_force(rows, person, str(s)[:10] or str(d)[:10]) for s, d in zip(g["period_start"], g["pay_date"])]
        g["row"] = [NO_ROW if r is None else int(r.name) for r in forced]
        # salary runs: two or more in a row with the same base pay under the same row
        run_id = ((g["base"] != g["base"].shift()) | (g["row"] != g["row"].shift())).cumsum()
        g["run"] = run_id
        sizes = g.groupby("run")["base"].transform("size")
        g["kind"] = ["salary" if n >= 2 else "hourly" for n in sizes]
        groups = [("salary", r) for _, r in g[g["kind"] == "salary"].groupby("run", sort=False)]
        hourly = g[g["kind"] == "hourly"]
        groups += [("hourly", r) for _, r in hourly.groupby("row", sort=False) if len(r) >= MIN_HOURLY]
        for kind, r in groups:
            out.append(_judge(person, employer, kind, r, rows))
    df = pd.DataFrame(out, columns=COLUMNS)
    return df.sort_values(["person", "from"], kind="stable").reset_index(drop=True)


def _judge(person, employer, kind, r, rows):
    annual = float(r["annual"].iloc[0]) if kind == "salary" else float(r["annual"].median())
    first, last = str(r["period_start"].iloc[0])[:10], str(r["period_end"].iloc[-1])[:10]
    n = len(r)
    what = f"paid at {annual:,.0f} a year" if kind == "salary" else f"paid a median of {annual:,.0f} a year"
    span = f"from {first} to {last} ({n} paychecks)"
    base = {
        "person": person,
        "employer": employer,
        "kind": kind,
        "from": first,
        "to": last,
        "paychecks": n,
        "stubs_annual": round(annual, 2),
    }
    key = int(r["row"].iloc[0])
    if key == NO_ROW:
        return {
            **base,
            "comp_effective": "",
            "comp_employer": "",
            "comp_salary": None,
            "comp_fte": None,
            "comp_annual": None,
            "off": None,
            "status": "no row",
            "finding": f"{what} {span}; comp_events has no row for {person} by then",
        }
    row = rows.loc[key]
    expected = None if pd.isna(row["salary"]) else float(row["salary"]) * float(row["fte"])
    off = None if not expected else annual / expected - 1
    tolerance = SALARY_TOLERANCE if kind == "salary" else HOURLY_TOLERANCE
    agrees = off is not None and abs(off) <= tolerance
    fte = "" if float(row["fte"]) == 1 else f" at {float(row['fte']):g} FTE"
    said = "no salary" if expected is None else f"{float(row['salary']):,.0f}{fte}"
    return {
        **base,
        "comp_effective": row["effective"],
        "comp_employer": row["employer"],
        "comp_salary": None if pd.isna(row["salary"]) else float(row["salary"]),
        "comp_fte": float(row["fte"]),
        "comp_annual": None if expected is None else round(expected, 2),
        "off": None if off is None else round(off, 4),
        "status": "agrees" if agrees else "disagrees",
        "finding": f"{what} {span}; comp_events says {said} from {row['effective']}"
        + ("" if agrees or off is None else f" ({off:+.1%})"),
    }


def jobs(comp_events, checked):
    """The Jobs editor's rows: every comp_events row in force, newest first per person, each with what the
    paychecks judged under it say: "agrees", the findings that disagree, or "" when none were judged."""
    columns = ["effective", "person", "employer", "salary", "band", "rating", "fte", "note", "stubs"]
    if comp_events is None or comp_events.empty:
        return pd.DataFrame(columns=columns)
    df = comp_events.copy()
    df["effective"] = df["effective"].astype(str).str[:10]
    df = df.drop_duplicates(["effective", "person", "employer"], keep="last").reindex(columns=columns, fill_value="")
    runs = {}
    for r in (checked if checked is not None else pd.DataFrame(columns=COLUMNS)).to_dict("records"):
        if r["comp_effective"]:
            runs.setdefault((r["person"], r["comp_effective"], r["comp_employer"]), []).append(r)

    def said(key):
        mine = runs.get(key, [])
        off = [r["finding"] for r in mine if r["status"] != "agrees"]
        return "; ".join(off) if off else ("agrees" if mine else "")

    df["stubs"] = [said(k) for k in zip(df["person"], df["effective"], df["employer"])]
    return df.sort_values(["person", "effective"], ascending=[True, False], kind="stable").reset_index(drop=True)
