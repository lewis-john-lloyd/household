"""Paychecks beside the pay history in comp_events, on made-up stubs."""

from datetime import date, timedelta

import pandas as pd

from household import paycheck


def stub(n, pay_date, start, lines, person="alex", employer="Acme Widgets"):
    end = (date.fromisoformat(start) + timedelta(days=13)).isoformat()
    return [
        {
            "pay_date": pay_date,
            "period_start": start,
            "period_end": end,
            "person": person,
            "employer": employer,
            "stub_id": f"{employer}{n}",
            "line_item": name,
            "kind": "income",
            "amount": amount,
            "ytd_amount": 0,
            "doc_id": "d",
        }
        for name, amount in lines
    ]


def run(first, count, lines_for, **kw):
    out, start = [], date.fromisoformat(first)
    for n in range(count):
        pay = start + timedelta(days=19)
        out += stub(n, pay.isoformat(), start.isoformat(), lines_for(n), **kw)
        start += timedelta(days=14)
    return out


def jobs(*rows):
    return pd.DataFrame(
        [
            {"effective": e, "person": p, "employer": m, "salary": s, "band": "", "rating": "", "fte": f, "note": ""}
            for e, p, m, s, f in rows
        ]
    )


# salary of 2,600 a period (67,600 a year), a raise to 2,700 from the ninth period, then hourly at 60%
def lines(n):
    if n < 8:
        return [("Salary", 2600), ("Group Term Life", 5)]
    if n < 14:
        return [("Salary", 2700), ("Bonus", 50 if n == 10 else 0)]
    hours = [48, 50, 46, 48, 52, 47, 48][n - 14]
    return [("Regular", round(hours * 2700 / 80, 2))] if n != 16 else [("Regular", 1215), ("PTO", 270)]


STUBS = pd.DataFrame(run("2026-01-04", 21, lines))


def test_a_raise_and_a_cut_in_hours_with_no_row_disagree_and_say_what_the_stubs_show():
    found = paycheck.check(STUBS, jobs(("2025-06-01", "alex", "Acme", "67600", "")))
    assert found[["kind", "from", "paychecks", "stubs_annual", "status"]].values.tolist() == [
        ["salary", "2026-01-18", 7, 67600.0, "agrees"],  # the first paycheck is left out
        ["salary", "2026-04-26", 6, 70200.0, "disagrees"],
        ["hourly", "2026-07-19", 6, 42120.0, "disagrees"],
    ]  # the last is left out too
    assert found["finding"].iloc[1] == (
        "paid at 70,200 a year from 2026-04-26 to 2026-07-18 (6 paychecks); "
        "comp_events says 67,600 from 2025-06-01 (+3.8%)"
    )


def test_rows_for_the_raise_and_the_hours_make_every_run_agree():
    found = paycheck.check(
        STUBS,
        jobs(
            ("2025-06-01", "alex", "Acme", "67600", ""),
            ("2026-04-26", "alex", "Acme", "70,200", ""),
            ("2026-07-19", "alex", "Acme", "70200", "0.6"),
        ),
    )
    assert set(found["status"]) == {"agrees"}
    assert found["comp_annual"].tolist() == [67600.0, 70200.0, 42120.0]
    shown = paycheck.jobs(jobs(("2026-07-19", "alex", "Acme", "70200", "0.6")), found)
    assert shown[["effective", "stubs"]].values.tolist() == [["2026-07-19", "agrees"]]


def test_paychecks_before_any_row_say_so_and_old_years_are_not_judged():
    assert set(paycheck.check(STUBS, jobs())["status"]) == {"no row"}
    later = paycheck.check(STUBS, jobs(("2025-06-01", "alex", "Acme", "67600", "")), since="2026-09-01")
    assert later["from"].tolist() == ["2026-08-16"]  # only the hourly paychecks paid since
    assert paycheck.periods_per_year("2026-01-01", "2026-01-15") == 24
