"""The weekly email's to-do list, and the FSA balances it nags about.

Compute publishes nudges to Reports: each a thing to do, under the action it calls for, with the window of
dates in which it is worth an email (show_from to show_until). The weekly script (nudge/Code.js) only
compares those dates with the day it runs, so a Reports table a few weeks old still gives the right email.
The script adds what only it can see live: files waiting in the inbox, and Reports behind the ledger.

The rules, as the household set them: an email is for things that need doing. A thing that has gone stale
is worth one once it has been stale STALE_DAYS; a thing with a deadline, once the deadline is LEAD_DAYS away.
FSA money left from last year is worth one every week from January 1 until it is claimed or its deadline
passes.

FSA balances: what was elected (the plan year's deductions on the pay stubs) less what came back
(reimbursement deposits from the administrator, fsa_payee_pattern, in the categories each account pays,
lpfsa_categories and dcfsa_categories). A deposit between January 1 and last year's claim deadline pays
last year's money first, since last year's is the money with a deadline; a note "fsa 2026" on the
transaction puts it in that plan year instead.
"""

import re
from datetime import date, timedelta

import pandas as pd

from . import projection, settings

STALE_DAYS = 30
LEAD_DAYS = 30
NOTE_YEAR = re.compile(r"\bfsa\s*(20\d\d)\b", re.IGNORECASE)
COLUMNS = ["group", "item", "detail", "show_from", "show_until", "link"]
FSA_COLUMNS = ["year", "account", "elected", "reimbursed", "left", "claim_deadline"]
ACCOUNTS = {  # account: (its name, the pay stub line matcher, its categories setting, its deadline setting, its
    # plan in the elections report)
    "lpfsa": ("Limited-purpose FSA", projection.HEALTH_FSA, "lpfsa_categories", "lpfsa_claim_deadline",
              projection.HEALTH_FSA_PLAN),
    "dcfsa": ("Dependent care FSA", projection.DEPENDENT_CARE, "dcfsa_categories", "dcfsa_claim_deadline",
              "dependent-care FSA"),
}
GROUPS = {  # the email's sections, in order
    "settings": "Fix a setting",
    "fsa": "Claim FSA reimbursements",
    "tax": "Change before the year's last paychecks",
    "download": "Download into the inbox",
    "review": "Answer in Review",
}
MIN_401K_GAP = 100  # dollars a 401(k) projection may miss its limit by before it is worth an email


def _iso(d):
    return d.isoformat() if d else ""


def _day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        return None


def _lines(n):
    return f"{n} line" if n == 1 else f"{n} lines"


def _money(x):
    return f"{abs(float(x)):,.0f}"


def _view(params, view, tab=""):
    base = settings.link(params, "views_url")
    if not base:
        return ""
    return f"{base}{'&' if '?' in base else '?'}view={view}" + (f"&tab={tab}" if tab else "")


def _deadline(params, key, plan_year):
    """The claim deadline, and whether it was set (an unset one is taken as March 31 of the next year)."""
    set_ = settings.claim_deadline(params, key, plan_year)
    return (set_, True) if set_ else (date(plan_year + 1, 3, 31), False)


def elected(stubs, matcher, year, today):
    """A plan year's FSA deductions: the year-to-date on each person's last stub that year, and for the year in
    progress the latest deduction on each paycheck still to come (fsa_balances prefers the elections report's
    projection for this year, which knows the paychecks that carry no deductions)."""
    if stubs is None or stubs.empty:
        return 0.0
    rows = stubs[(stubs["kind"] == "pre_tax") & stubs["line_item"].astype(str).map(matcher)
                 & (stubs["pay_date"].astype(str).str[:4] == str(year))].copy()
    if rows.empty:
        return 0.0
    rows["ytd"] = pd.to_numeric(rows["ytd_amount"], errors="coerce").fillna(0.0)
    rows["now"] = pd.to_numeric(rows["amount"], errors="coerce").fillna(0.0)
    total = 0.0
    for _, one in rows.sort_values("pay_date", kind="stable").groupby(["person", "employer"]):
        last = one.iloc[-1]
        total += float(last["ytd"])
        if year == today.year:
            total += float(last["now"]) * len(projection.remaining_pay_dates(str(last["pay_date"])[:10], year))
    return round(total, 2)


def fsa_balances(stubs, categorized, params, today, elections=None):
    """Each plan year's FSA money per account: elected, reimbursed, left, and the claim deadline. This year's
    election is the elections report's projection when it is given."""
    years = sorted({int(y) for y in stubs["pay_date"].astype(str).str[:4]}) if stubs is not None and len(stubs) else []
    pattern = settings.value(params, "fsa_payee_pattern")
    out = []
    for account, (_, matcher, categories_key, deadline_key, plan) in ACCOUNTS.items():
        chosen = {y: elected(stubs, matcher, y, today) for y in years}
        if elections is not None and len(elections) and today.year in chosen:
            mine = elections[(elections["plan"] == plan) & (elections["year"].astype(str) == str(today.year))]
            if len(mine):
                chosen[today.year] = round(float(pd.to_numeric(mine["projected"], errors="coerce").sum()), 2)
        chosen = {y: v for y, v in chosen.items() if v}
        if not chosen:
            continue
        paid = {y: 0.0 for y in chosen}
        categories = settings.names(params, categories_key)
        if pattern and categories and categorized is not None and len(categorized):
            c = categorized
            deposits = c[c["payee_raw"].astype(str).str.contains(pattern, case=False, regex=True)
                         & (pd.to_numeric(c["amount"], errors="coerce") > 0)
                         & c["category"].astype(str).str.lower().isin(categories)].sort_values("date", kind="stable")
            for d in deposits.to_dict("records"):
                amount, day = float(d["amount"]), _day(d["date"])
                said = NOTE_YEAR.search(str(d.get("note", "")))
                if said and int(said.group(1)) in paid:
                    paid[int(said.group(1))] += amount
                    continue
                prior = day.year - 1 if day else None
                if prior in paid and day <= _deadline(params, deadline_key, prior)[0]:
                    room = max(chosen[prior] - paid[prior], 0.0)
                    paid[prior] += min(amount, room)
                    amount -= min(amount, room)
                if amount > 0 and day and day.year in paid:
                    paid[day.year] += amount
                elif amount > 0 and prior in paid:  # past last year's deadline, with no plan this year
                    paid[prior] += amount
        for y in chosen:
            deadline, _ = _deadline(params, deadline_key, y)
            out.append({"year": y, "account": account, "elected": chosen[y], "reimbursed": round(paid[y], 2),
                        "left": round(chosen[y] - paid[y], 2),
                        "claim_deadline": _iso(settings.claim_deadline(params, deadline_key, y))})
    return pd.DataFrame(out, columns=FSA_COLUMNS)


def _last_paycheck(stubs, person, employer, year):
    mine = stubs[(stubs["person"].astype(str) == str(person)) & (stubs["employer"].astype(str) == str(employer))]
    if mine.empty:
        return None
    dates = projection.remaining_pay_dates(str(mine["pay_date"].astype(str).max())[:10], year)
    return dates[-1] if dates else None


def build(today, params, coverage=None, review=None, plan=None, elections=None, stubs=None, fsa=None):
    """The nudges: one row per thing to do, with the dates between which it belongs in the weekly email."""
    rows = []

    def add(group, item, detail, show_from, show_until=None, link=""):
        rows.append({"group": GROUPS[group], "item": item, "detail": detail, "show_from": _iso(show_from),
                     "show_until": _iso(show_until), "link": link})

    for key, v, problem in settings.check(params):
        add("settings", key, f"{v!r} {problem}; the weekly email leaves it out until it is fixed", today,
            link=_view(params, "edit", "settings"))

    if fsa is not None and len(fsa):
        for r in fsa[(fsa["year"] < today.year) & (fsa["left"] > 0.005)].to_dict("records"):
            name, _, _, deadline_key, _ = ACCOUNTS[r["account"]]
            deadline, known = _deadline(params, deadline_key, int(r["year"]))
            if deadline < today:
                continue
            due = (f"claims due {deadline:%B} {deadline.day}" if known else
                   f"set {deadline_key} for the real deadline; until then March 31 is assumed")
            add("fsa", f"{name} {r['year']}", f"{_money(r['left'])} of {_money(r['elected'])} not reimbursed yet; {due}",
                date(int(r["year"]) + 1, 1, 1), deadline, settings.link(params, "fsa_claims_url"))
        unset = [k for k in ("fsa_payee_pattern", "lpfsa_categories", "dcfsa_categories") if not settings.value(params, k)]
        if unset and (fsa["year"] < today.year).any():
            add("settings", ", ".join(unset), "not set, so no FSA reimbursement is counted and every balance looks "
                "unclaimed", date(today.year, 1, 1), link=_view(params, "edit", "settings"))

    tolerance = settings.number(params, "withholding_tolerance", 500)
    target = settings.number(params, "target_refund", 2000)
    if plan is not None and len(plan) and stubs is not None:
        aimed = plan[(pd.to_numeric(plan["target"], errors="coerce") - target).abs() < 0.5]
        for r in aimed.to_dict("records"):
            if pd.isna(r.get("suggested_extra")) or abs(float(r["projected"]) - target) <= tolerance:
                continue
            last = _last_paycheck(stubs, r["person"], r["employer"], today.year)
            if not last:
                continue
            federal = r["return"] == "federal"
            now, extra, lands = float(r["current_extra"]), float(r["suggested_extra"]), float(r["lands_at"])
            vest = float(r["vest_rate"]) if pd.notna(r.get("vest_rate")) else None
            # the supplemental rate is the other lever, and only for federal withholding on vests and bonuses
            supplemental = federal and vest is not None and abs(lands - target) > tolerance
            if abs(extra - now) < 0.01 and not supplemental:
                continue  # nothing the household can change gets closer
            verdict = (f"a refund of {_money(r['projected'])}" if float(r["projected"]) >= 0
                       else f"owing {_money(r['projected'])}")
            steps = []
            if abs(extra - now) >= 0.01:
                steps.append(f"set the extra withholding to {extra:,.2f} a paycheck (now {now:,.2f}) for the last "
                             f"{int(r['paychecks_left'])} paychecks"
                             + ("" if abs(lands - target) <= 1 else
                                f", which gets to {'a refund of ' if lands >= 0 else 'owing '}{_money(lands)}"))
            if supplemental:
                steps.append(f"{'then ' if steps else ''}{'lower' if lands > target else 'raise'} the supplemental "
                             f"rate for vests and bonuses (now {vest:.0%}) to close the rest")
            which = "Federal (W-4 Step 4(c))" if federal else "Wisconsin (WT-4 additional amount)"
            add("tax", f"{which} withholding", f"projected {verdict} against a target refund of {_money(target)}: "
                + "; ".join(steps), last - timedelta(days=LEAD_DAYS), last, settings.link(params, "payroll_url"))
    if elections is not None and len(elections) and stubs is not None:
        mine = elections[elections["plan"].isin(["401(k) elective deferral", projection.MEGA_BACKDOOR])]
        for r in mine.to_dict("records"):
            if r["status"] == "at limit" or pd.isna(r.get("gap")) or abs(float(r["gap"])) < MIN_401K_GAP:
                continue
            last = _last_paycheck(stubs, r["person"], r["employer"], today.year)
            if not last:
                continue
            off = (f"{_money(r['gap'])} short of" if float(r["gap"]) > 0 else f"{_money(r['gap'])} over")
            add("tax", f"{r['person']} {r['plan']}", f"on pace for {_money(r['projected'])}, {off} the "
                f"{_money(r['limit'])} limit; change the contribution before the last paycheck on {last:%B} {last.day}",
                last - timedelta(days=LEAD_DAYS), last,
                settings.link(params, "benefits_url") or settings.link(params, "payroll_url"))

    if coverage is not None and len(coverage):
        for r in coverage[coverage["status"].isin(["due", "never downloaded"])].to_dict("records"):
            newest = _day(r.get("newest", ""))
            waiting = int(float(r.get("waiting_lines") or 0))
            if newest:
                add("download", r["source"], f"has everything through {newest:%B} {newest.day}, {newest.year}"
                    + (f"; {_lines(waiting)} {'waits' if waiting == 1 else 'wait'} for it" if waiting else ""),
                    newest + timedelta(days=STALE_DAYS), link=_view(params, "status"))
            elif waiting:
                add("download", r["source"], f"never downloaded; {_lines(waiting)} {'waits' if waiting == 1 else 'wait'} for it", today,
                    link=_view(params, "status"))

    if review is not None and len(review):
        firsts = [d for d in (_day(x) for x in review["first"]) if d]
        if firsts:
            oldest = min(firsts)
            lines = int(pd.to_numeric(review["lines"], errors="coerce").fillna(0).sum())
            add("review", f"{len(review)} question{'' if len(review) == 1 else 's'}",
                f"covering {_lines(lines)}, the oldest from {oldest:%B} {oldest.day}, "
                f"{oldest.year}", oldest + timedelta(days=STALE_DAYS), link=_view(params, "edit", "review"))
    order = list(GROUPS.values())
    return pd.DataFrame(rows, columns=COLUMNS).sort_values(
        "group", key=lambda g: g.map(order.index), kind="stable").reset_index(drop=True)
