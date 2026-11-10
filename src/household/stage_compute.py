"""The compute stage: Reports rebuilt from the ledger, every table every run."""

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

from . import (
    allocation,
    budget,
    categorize,
    checks,
    compute,
    editor,
    flows,
    house,
    inbox,
    investments,
    legacy,
    logs,
    lots,
    mortgage,
    networth,
    nudges,
    paycheck,
    pennsylvania,
    projection,
    recurring,
    retirement,
    schema,
    settings,
    sheets,
    tax,
    taxhistory,
    taxsources,
    transfers,
    valuation,
    wisconsin,
    withholding,
)
from .books import categorize_books, read_books
from .kinds import Log, Session, Spreadsheet, StageResult
from .ledgerio import LedgerReading, LedgerView, bootstrap, new_run_id, read_ledger, versions
from .runlog import record_stage, recording, say


@dataclass
class Compute:
    """What every part of a compute run shares: where it reads and writes, which run and day it is, and its log."""

    session: Session
    ledger: LedgerView
    reports: Spreadsheet
    run_id: str
    computed_at: str
    log: Log

    @property
    def today(self) -> str:
        return self.computed_at[:10]


def _investments_and_homes(c: Compute, books, sched):
    """Investment values, the allocation and a rebalance, homes, and net worth (valuation.py, allocation.py,
    house.py, networth.py) into Reports. Returns figures for the run's result."""
    ledger, reports, run_id, today, log = c.ledger, c.reports, c.run_id, c.today, c.log
    drive = c.session.drive()
    accounts, params = books["accounts"], books["parameters"]
    holdings, activity = read_ledger(ledger, "holdings"), read_ledger(ledger, "investment_activity")
    securities = read_ledger(ledger, "securities")
    vals, notes = valuation.values(
        accounts,
        holdings,
        activity,
        read_ledger(ledger, "prices"),
        securities,
        today,
        partial=valuation.partial_docs(read_ledger(ledger, "source_documents")),
        proxies=valuation.proxies(params),
    )
    sheets.replace_table(reports, schema.table(schema.REPORTS, "investment_values"), vals.assign(run_id=run_id))
    now = vals[vals["date"] == today] if len(vals) else vals
    say(
        log,
        f"investments: {len(now)} positions in {now['account_id'].nunique() if len(now) else 0} accounts worth "
        f"{pd.to_numeric(now['value'], errors='coerce').sum():,.0f} today; {vals['date'].nunique() if len(vals) else 0}"
        " dates valued",
    )
    for line in notes:
        say(log, line)
    if len(now):
        by = now.assign(v=pd.to_numeric(now["value"], errors="coerce")).groupby("bucket")["v"].sum()
        say(log, "  by tax bucket: " + ", ".join(f"{valuation.BUCKET_NAMES.get(b, b)} {v:,.0f}" for b, v in by.items()))
        stale = now[now["basis"] == "carried"]
        if len(stale):
            say(
                log,
                f"  {len(stale)} positions carried at a reading's value (no price since): "
                + ", ".join(sorted(set(stale["account_id"] + ":" + stale["symbol"].replace("", "(total)")))),
            )
    nodes = allocation.tree(read_ledger(ledger, "allocation_targets"), today)
    scope = allocation.in_scope(vals)
    current = allocation.by_class(scope) if scope is not None and len(scope) else {}
    since = (pd.Timestamp(today) - pd.DateOffset(years=2)).date().isoformat()
    people = valuation.adults(params, read_ledger(ledger, "paystubs"))
    trades, proposed, more = (
        allocation.rebalance(
            scope,
            nodes,
            allocation.ever_held(holdings, activity, since),
            classes=valuation.classes(securities),
            people=people,
        )
        if current
        else (pd.DataFrame(columns=allocation.TRADE_COLUMNS), {}, [])
    )
    allocated = allocation.report(nodes, current, proposed)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "allocation"), allocated.assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "rebalance"), trades.assign(run_id=run_id))
    for line in more:
        say(log, line)
    tables = {
        n: read_ledger(ledger, n) for n in ("properties", "property_values", "improvements", "loans", "loan_balances")
    }
    props, pvals, improved, loans, balances = house.frames(tables)
    improved = house.improvements(improved)
    summary, more = house.summary(props, pvals, improved, loans, sched, balances, params, today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "property_summary"), summary.assign(run_id=run_id))
    sheets.replace_table(
        reports, schema.table(schema.REPORTS, "home_values"), house.values_report(pvals).assign(run_id=run_id)
    )
    sheets.replace_table(reports, schema.table(schema.REPORTS, "home_improvements"), improved.assign(run_id=run_id))
    taxed = house.taxes(props, pvals, read_ledger(ledger, "property_taxes"), today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "home_taxes"), taxed.assign(run_id=run_id))
    for r in taxed[taxed["basis"] == "estimated"].to_dict("records"):
        say(
            log,
            f"house: {r['property_id']} {r['tax_year']} property tax estimated at {r['amount_due']:,.0f} due "
            f"({r['assessed']:,.0f} assessed at the {r['mill_rate']:.6f} mill rate of the last bill)",
        )
    homes = house.parcels(props, pvals, today)
    if homes and drive is not None:
        try:
            if not inbox.write_parcels(drive, homes):
                say(log, "house: the county check has no parcels file yet; run its monthly function once, then compute")
        except Exception as e:  # the county check keeps the parcels it was last given
            say(log, f"house: could not write the county check's parcels file: {e}", "WARN")
    for line in more:
        say(log, line)
    for r in summary[summary["key"].isin(["value_used", "equity"])].to_dict("records"):
        say(
            log,
            f"house: {r['property_id']} {r['label'].lower()} {float(r['value']):,.0f}"
            + (f" ({r['note']})" if r["note"] else ""),
        )
    rule = settings.value(params, "home_value_basis", "purchase_price").strip() or "purchase_price"
    net = networth.build(vals, people, props, pvals, loans, sched, balances, rule)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "net_worth"), net.assign(run_id=run_id))
    totals = networth.totals(net)
    if totals:
        first, last = min(totals), max(totals)
        say(
            log,
            f"net worth: {totals[last]:,.0f} on {last} ({len(totals)} dates from {first}); owners "
            + ", ".join(people or ("none set",)),
        )
    books["values_today"], books["allocated"] = now, allocated  # for the retirement plan
    return {
        "investments_value": round(float(pd.to_numeric(now["value"], errors="coerce").sum()), 2) if len(now) else 0.0,
        "rebalance_trades": len(trades),
        "net_worth": totals.get(max(totals), 0.0) if totals else 0.0,
    }


def _lots(c: Compute, books):
    """Tax lots and wash sales (lots.py) into Reports: form_8949 and open_lots. Returns form_8949 and figures for
    the run's result."""
    ledger, reports, run_id, today, log = c.ledger, c.reports, c.run_id, c.today, c.log
    accounts, securities = books["accounts"], read_ledger(ledger, "securities")
    holdings, activity, schedule = (
        read_ledger(ledger, "holdings"),
        read_ledger(ledger, "investment_activity"),
        read_ledger(ledger, "vest_schedule"),
    )
    group_of = lots.groups(securities)
    plan = set(lots.grant_symbols(schedule).values())
    held = lots.holders(holdings, accounts, plan)
    eq_acq, eq_sold, notes = lots.equity_lots(
        read_ledger(ledger, "equity_vests"), read_ledger(ledger, "equity_sales"), schedule, held, group_of
    )
    act_acq, act_sold = lots.activity_lots(activity, accounts, group_of, leave_out=plan)
    named = [x.strip() for x in str(settings.value(books["parameters"], "wash_sale_symbols")).split(",") if x.strip()]
    watched = named or sorted(plan)
    form, still, more = lots.build(
        pd.concat([eq_acq, act_acq], ignore_index=True),
        pd.concat([eq_sold, act_sold], ignore_index=True),
        read_ledger(ledger, "stock_splits"),
        wash_symbols=watched,
    )
    say(
        log,
        f"lots: wash sales checked for {', '.join(watched) or 'no symbol'}"
        + ("" if named else " (the stock plan's; the wash_sale_symbols setting names others)"),
    )
    still = lots.priced(still, valuation.Prices(read_ledger(ledger, "prices")), today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "form_8949"), form.assign(run_id=run_id))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "open_lots"), still.assign(run_id=run_id))
    books["open_lots"] = still
    facts = read_ledger(ledger, "return_facts")
    for line in notes + more + lots.year_lines(form, facts) + lots.holding_check(still, holdings, plan):
        say(log, line)
    off = lots.check_reported(form)
    if len(off):
        say(
            log,
            f"lots: {len(off)} sales whose basis before wash sales differs from the plan's: "
            + ", ".join(sorted(set(off["sale_date"] + " " + off["lot_id"])))[:400],
            "WARN",
        )
    say(
        log,
        f"lots: {len(form)} lot parts sold, {int((form['code'] == 'W').sum()) if len(form) else 0} of them wash "
        f"sales; {len(still)} lot parts open",
    )
    return form, {
        "lot_parts_sold": len(form),
        "wash_sales": int((form["code"] == "W").sum()) if len(form) else 0,
        "open_lot_parts": len(still),
    }


RETIREMENT_TABLES = (
    "retirement_years",
    "retirement_strategies",
    "retirement_plan",
    "retirement_social_security",
    "retirement_earnings",
    "retirement_assumptions",
    "retirement_budget",
)


def _retirement(c: Compute, books, actuals, sched):
    """The retirement plan (the retirement package) into Reports. A failure is logged and leaves the tables empty, so the
    rest of the run goes on. Returns figures for the run's result."""
    ledger, reports, run_id, today, log = c.ledger, c.reports, c.run_id, c.today, c.log
    started = time.perf_counter()
    empty = {
        name: pd.DataFrame(columns=[c for c in schema.table(schema.REPORTS, name).columns if c != "run_id"])
        for name in RETIREMENT_TABLES
    }
    out = dict(empty)
    found = {}
    try:
        params = books["parameters"]
        people = valuation.adults(params, read_ledger(ledger, "paystubs"))
        h, notes, assume = retirement.build(
            params,
            people,
            read_ledger(ledger, "ss_statements"),
            read_ledger(ledger, "ss_estimates"),
            read_ledger(ledger, "ss_earnings"),
            books["accounts"],
            books.get("values_today", pd.DataFrame()),
            read_ledger(ledger, "investment_activity"),
            books.get("open_lots"),
            actuals,
            books["categories"],
            sched,
            read_ledger(ledger, "equity_vests"),
            retirement.allocation_stock_share(books.get("allocated")),
            today,
        )
        out["retirement_assumptions"] = assume.reindex(columns=empty["retirement_assumptions"].columns)
        if h is None:
            for line in notes:
                say(log, line, "WARN")
        else:
            years, table, plan, ss, earned, more = retirement.run(h, params, today)
            out.update(
                retirement_years=years,
                retirement_strategies=table,
                retirement_plan=plan,
                retirement_social_security=ss,
                retirement_earnings=earned,
            )
            for line in notes:
                say(log, line, "WARN")
            for line in more:
                say(log, line)
            chosen = years[years["chosen"] == "yes"]
            found = {"retirement_year": int(chosen["retire_year"].iloc[0]) if len(chosen) else None}
            out["retirement_budget"] = retirement.budget_table(h, found["retirement_year"] or h.start)
    except Exception as e:  # the rest of the run doesn't wait on the retirement plan
        say(log, f"retirement: failed: {type(e).__name__}: {e}", "ERROR")
    for name in RETIREMENT_TABLES:
        sheets.replace_table(reports, schema.table(schema.REPORTS, name), out[name].assign(run_id=run_id))
    say(
        log,
        f"retirement: {len(out['retirement_strategies']) // 2} strategies and "
        f"{out['retirement_years']['retire_year'].nunique() if len(out['retirement_years']) else 0} retirement "
        f"years modeled in {time.perf_counter() - started:.0f}s",
    )
    return found


def _transfers(c: Compute, books):
    """Transfer pairs between the household's accounts, and the lines with no partner, into Reports."""
    reports, run_id, log = c.reports, c.run_id, c.log
    effective, paired, lonely = books["effective"], books["paired"], books["lonely"]
    for name, df in (("transfers", paired), ("transfers_unmatched", lonely)):
        sheets.replace_table(reports, schema.table(schema.REPORTS, name), df.assign(run_id=run_id))
    assumed = int((paired["kind"] == transfers.ASSUMED).sum())
    answered = set(
        effective.loc[
            (effective["category"].astype(str).str.strip() != "") | (effective["split"].astype(str).str.strip() != ""),
            "txn_id",
        ]
    )
    waiting = lonely[~lonely["txn_id"].isin(answered)]  # a person's category or split answers one
    say(
        log,
        f"transfers: {len(paired) - assumed} pairs between your accounts, {len(lonely)} transfer lines "
        f"with no partner ({len(waiting)} not yet answered)"
        + (
            f", {assumed} more from before the other account's downloaded history, taken as transfers with it"
            if assumed
            else ""
        ),
    )
    return {"paired": paired, "lonely": lonely, "waiting": waiting}


def _categories(c: Compute, books):
    """Every line categorized, and the monthly and daily actuals, into Reports."""
    reports, run_id, computed_at, log = c.reports, c.run_id, c.computed_at, c.log
    categorized = categorize_books(books)
    recent = categorized[categorized["era"] != "legacy"]
    sheets.replace_table(reports, schema.table(schema.REPORTS, "categorized"), recent.assign(run_id=run_id))
    counts = categorize.summary(categorized)
    say(
        log,
        f"categories since {categorize.REVIEW_FROM}: decided {counts['decided']}; "
        f"{counts['open_lines']} lines open in review; waiting {counts['waiting']}",
    )
    actuals = compute.monthly_actuals(categorize.actual_rows(categorized))
    actuals["run_id"], actuals["computed_at"] = run_id, computed_at
    sheets.replace_table(reports, schema.table(schema.REPORTS, "monthly_actuals"), actuals)
    say(log, f"monthly_actuals: {len(actuals)} rows from {len(books['effective'])} transactions")
    daily = compute.daily_actuals(categorize.actual_rows(categorized), budget._not_spending(books["categories"]))
    sheets.replace_table(reports, schema.table(schema.REPORTS, "daily_actuals"), daily.assign(run_id=run_id))
    return {"categorized": categorized, "counts": counts, "actuals": actuals}


def _investment_flows(c: Compute):
    """Each investment account's money in and out by year and kind (investments.flows)."""
    ledger, reports, run_id, log = c.ledger, c.reports, c.run_id, c.log
    activity = read_ledger(ledger, "investment_activity")
    invested = investments.flows(activity)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "investment_flows"), invested.assign(run_id=run_id))
    if len(activity):
        say(log, f"investments: {len(activity)} activity rows in {activity['account_id'].nunique()} accounts")
        for r in (
            invested[invested["kind"].isin(["contribution", "conversion_in", "conversion_out", "withdrawal"])]
            .head(12)
            .to_dict("records")
        ):
            say(log, f"  {r['year']} {r['account_id']}: {r['kind']} {r['amount']:,.2f}")


def _say_links(log, rules, decisions):
    """A warning for each recurring charge linked to a budget line that is gone or doesn't take its target
    from its charges (a line's latest row decides)."""
    paid = recurring.links(decisions)
    if not paid:
        return
    latest = rules.assign(line=rules["line"].astype(str).str.strip()).drop_duplicates("line", keep="last")
    rule = dict(zip(latest["line"], latest["rule"].astype(str).str.strip()))
    for key, line in sorted(paid.items()):
        if line not in rule:
            say(log, logs.warn(f"recurring: {key} is linked to budget line {line}, which isn't in budget_rules"))
        elif rule[line] != "recurring":
            say(
                log,
                logs.warn(
                    f"recurring: {key} is linked to budget line {line}, whose rule is {rule[line]}: set it to "
                    f"recurring for its target to follow the charge"
                ),
            )
    say(log, f"recurring: {len(paid)} charges linked to budget lines")


def _budget(c: Compute, books, categorized, actuals):
    """Budget targets, budget against actual and the sinking funds (budget.py). Returns the rules and the targets."""
    ledger, reports, run_id, today, log = c.ledger, c.reports, c.run_id, c.today, c.log
    rules = read_ledger(ledger, "budget_rules")
    planned = pd.DataFrame(columns=budget.TARGET_COLUMNS)
    if not rules.empty:
        expected = recurring.monthly_expected(categorized, books["decisions"], today[:7], today)
        by_line = recurring.line_expected(categorized, books["decisions"], today[:7])
        planned = budget.targets(rules, actuals, today[:7], expected, by_line)
        _say_links(log, rules, books["decisions"])
        sheets.replace_table(reports, schema.table(schema.REPORTS, "budget_targets"), planned.assign(run_id=run_id))
        compared = budget.versus(planned, actuals, books["categories"])
        sheets.replace_table(reports, schema.table(schema.REPORTS, "budget_vs_actual"), compared.assign(run_id=run_id))
        funds = budget.sinking_funds(planned, actuals)
        sheets.replace_table(reports, schema.table(schema.REPORTS, "sinking_funds"), funds.assign(run_id=run_id))
        now = compared[compared["month"] == today[:7]]
        over = now[now["remaining"] < 0]
        say(
            log,
            f"budget: {rules['line'].nunique()} lines; this month {now['target'].sum():,.0f} budgeted, "
            f"{now['actual'].sum():,.0f} spent, {len(over)} categories over",
        )
        warnings = planned[planned["basis"].str.contains("no usable|unknown|no fallback", regex=True)]
        for line in sorted(set(warnings["line"])):
            say(log, f"  budget line {line}: {warnings.loc[warnings['line'] == line, 'basis'].iloc[-1]}")
    else:
        say(log, "budget: no budget_rules yet")
    return {"rules": rules, "planned": planned}


def _editor(c: Compute, books, rules, planned, actuals):
    """What the views' editors show: each setting in force, budget lines in words, the review queue."""
    ledger, reports, run_id, today, log = c.ledger, c.reports, c.run_id, c.today, c.log
    published = editor.publish(books, read_ledger(ledger, "review"), rules, planned, actuals, today[:7])
    for name, df in published.items():
        sheets.replace_table(reports, schema.table(schema.REPORTS, name), df.assign(run_id=run_id))
    say(
        log,
        "editor: "
        + ", ".join(f"{len(df)} {name[len('editor_') :].replace('_', ' ')}" for name, df in published.items())
        + " for the views",
    )
    return published


def _checks(c: Compute, books, effective, categorized):
    """The monthly checklist: downloads due, balances that disagree, paychecks with no deposit or no stub."""
    ledger, reports, run_id, today, log = c.ledger, c.reports, c.run_id, c.today, c.log
    due = checks.coverage(
        books["accounts"],
        effective,
        read_ledger(ledger, "balances"),
        read_ledger(ledger, "source_documents"),
        categorized,
        books["coverage"],
        today,
    )
    sheets.replace_table(reports, schema.table(schema.REPORTS, "coverage"), due.assign(run_id=run_id))
    to_get = due[due["status"].isin(["due", "never downloaded"])]
    say(log, f"coverage: {len(to_get)} downloads due" + (": " + ", ".join(to_get["source"]) if len(to_get) else ""))
    balanced = checks.balance_check(read_ledger(ledger, "balances"), effective)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "balance_check"), balanced.assign(run_id=run_id))
    off = balanced[balanced["status"].str.startswith("off")]
    say(
        log,
        f"balance_check: {len(balanced)} balances, {len(off)} that disagree with the ledger",
        "WARN" if len(off) else "INFO",
    )
    for row in off.to_dict("records"):
        say(log, f"  {row['account_id']} {row['as_of']}: {row['status']}")
    unpaired = checks.paychecks(read_ledger(ledger, "paystubs"), categorized)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "paychecks"), unpaired.assign(run_id=run_id))
    forgot = unpaired[unpaired["status"].str.startswith("missing stub")]
    lost = unpaired[unpaired["status"] == "no deposit"]
    say(
        log,
        f"paychecks: {logs.counted(len(forgot), 'pay deposit')} with no stub, "
        f"{logs.counted(len(lost), 'stub')} with no deposit",
    )
    for who, g in forgot.groupby("person"):
        say(log, f"  did you forget {who}'s stubs? {len(g)} missing, the latest paid {g['date'].max()}", "WARN")
    for row in lost.to_dict("records"):
        say(
            log,
            f"  {row['date']} {row['person']} ({row['employer']}): net pay {row['amount']:,.2f} "
            "never arrived in an account",
        )
    return {"due": due, "to_get": to_get, "off": off, "forgot": forgot, "lost": lost}


def _mortgage(c: Compute, categorized):
    """Each loan's schedule to the end of its term, and its details, for the Home page (mortgage.py)."""
    ledger, reports, run_id, today, log = c.ledger, c.reports, c.run_id, c.today, c.log
    loans = read_ledger(ledger, "loans")
    sched = pd.DataFrame(columns=mortgage.COLUMNS)
    if not loans.empty:
        # to the end of each loan's term: the months ahead are the schedule as it stands, for the Home page
        sched, notes = mortgage.schedule(loans, read_ledger(ledger, "loan_events"), categorized, "9999-12")
        sched.loc[sched["month"] > today[:7], "paid_from_ledger"] = ""
        sheets.replace_table(reports, schema.table(schema.REPORTS, "mortgage"), sched.assign(run_id=run_id))
        for line in notes + mortgage.summary(sched[sched["month"] <= today[:7]]):
            say(log, line)
        detail = mortgage.details(loans, read_ledger(ledger, "loan_facts"), sched, today[:7])
        sheets.replace_table(reports, schema.table(schema.REPORTS, "loan_details"), detail.assign(run_id=run_id))
        ahead = sched[sched["month"] > today[:7]]
        for loan_id, g in ahead.groupby("loan_id"):
            say(
                log,
                f"mortgage: {loan_id} paid off {g['month'].iloc[-1]} as scheduled, "
                f"{g['interest'].sum():,.0f} interest still to pay",
            )
    return sched


def _recurring(c: Compute, books, categorized):
    """Recurring charges found and tracked, their months and their price changes (recurring.py)."""
    reports, run_id, today, log = c.reports, c.run_id, c.today, c.log
    subscriptions = recurring.discover(categorized, today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "recurring"), subscriptions.assign(run_id=run_id))
    say(
        log,
        f"recurring: {len(subscriptions)} recurring charges, "
        f"{int((subscriptions['status'] == 'price changed').sum())} changed price, "
        f"{int((subscriptions['status'] == 'stopped').sum())} stopped",
    )
    subs = recurring.subscriptions(categorized, books["decisions"], today)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "subscriptions"), subs.assign(run_id=run_id))
    sheets.replace_table(
        reports,
        schema.table(schema.REPORTS, "editor_subscriptions"),
        recurring.editor_rows(subs, subscriptions, categorized, books["decisions"], today).assign(run_id=run_id),
    )
    sheets.replace_table(
        reports,
        schema.table(schema.REPORTS, "subscription_months"),
        recurring.subscription_months(categorized, books["decisions"], books["categories"], today).assign(
            run_id=run_id
        ),
    )
    changed = recurring.fresh_changes(subs, today)
    yearly = pd.to_numeric(subs["yearly"], errors="coerce")[subs["status"] != "stopped"].sum()
    say(log, f"tracked recurring charges: {len(subs)}, {yearly:,.0f} a year, {len(changed)} changed price lately")
    for r in changed.to_dict("records"):
        say(log, f"recurring charge: {r['key']} now {r['change']}")
    return changed


def _flows(c: Compute, books, categorized):
    """The Sankey's flows by month and year (flows.py)."""
    ledger, reports, run_id, log = c.ledger, c.reports, c.run_id, c.log
    money = flows.build(
        read_ledger(ledger, "paystubs"), categorized, books["categories"], read_ledger(ledger, "equity_sales")
    )
    sheets.replace_table(reports, schema.table(schema.REPORTS, "flows"), money.assign(run_id=run_id))
    say(log, f"flows: {money['period'].nunique()} months and years for the Sankey view")


def _duplicates(c: Compute, books, effective):
    """Possible duplicate lines, less the pairs a person said are not."""
    reports, run_id, log = c.reports, c.run_id, c.log
    decisions = books["decisions"]
    dismissed = decisions.loc[(decisions["kind"] == "duplicate") & (decisions["decision"] == "reject"), "proposal_key"]
    dupes = compute.possible_duplicates(effective, dismissed=set(dismissed))
    dupes["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "possible_duplicates"), dupes)
    say(log, f"possible_duplicates: {len(dupes)}")
    return dupes


def _filed_taxes(c: Compute, result):
    """Each filed return checked line by line, its history and its lines as filed. Returns return_facts."""
    ledger, reports, run_id, log = c.ledger, c.reports, c.run_id, c.log
    facts = sheets.read_table(ledger, schema.table(schema.LEDGER, "return_facts"))
    if not facts.empty:
        check = tax.check_against_return(facts)
        check["run_id"] = run_id
        sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_check"), check)
        result["tax_check_mismatches"] = int((~check["match"]).sum())
        say(log, f"tax_check: {len(check)} filed lines, {result['tax_check_mismatches']} off by more than 1 dollar")
        unchecked = sorted(set(facts.loc[facts["kind"] == "filed", "year"].astype(int)) - set(tax.taxdata.PARAMS))
        if unchecked:
            say(
                log,
                f"tax_check: {', '.join(map(str, unchecked))} shown as filed; their tax law is not on file, "
                "so their lines are not recomputed",
            )
        past = taxhistory.history(facts).assign(basis="filed")
        sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_history"), past.assign(run_id=run_id))
        say(log, f"tax_history: {past['year'].nunique()} filed years")
        sheets.replace_table(
            reports, schema.table(schema.REPORTS, "filed_lines"), taxhistory.filed_lines(facts).assign(run_id=run_id)
        )
    return facts


def _tax_sources(c: Compute, result):
    """Every cited tax law value and whether its source PDF is in the record store."""
    ledger, reports, run_id, log = c.ledger, c.reports, c.run_id, c.log
    documents = read_ledger(ledger, "source_documents")
    sources = taxsources.report(documents)
    sources["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_sources"), sources)
    unfiled = int((sources["record"] == "not in record store").sum())
    result["tax_sources_unfiled"] = unfiled
    say(log, f"tax_sources: {len(sources)} cited values, {unfiled} whose source PDF is not in the record store")


def _pay(c: Compute, result):
    """Paychecks beside the history of pay changes, and the Jobs editor's rows. Returns the stubs and jobs."""
    ledger, reports, run_id, log = c.ledger, c.reports, c.run_id, c.log
    stubs = sheets.read_table(ledger, schema.table(schema.LEDGER, "paystubs"))
    jobs = sheets.read_table(ledger, schema.table(schema.LEDGER, "comp_events"))
    pay = paycheck.check(stubs, jobs)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "pay_check"), pay.assign(run_id=run_id))
    sheets.replace_table(
        reports, schema.table(schema.REPORTS, "editor_jobs"), paycheck.jobs(jobs, pay).assign(run_id=run_id)
    )
    wrong_pay = pay[pay["status"] != "agrees"]
    result["pay_mismatches"] = len(wrong_pay)
    say(
        log,
        f"pay_check: {len(pay)} runs of paychecks beside comp_events, {len(wrong_pay)} disagree",
        "WARN" if len(wrong_pay) else "INFO",
    )
    for row in wrong_pay.to_dict("records"):
        say(log, f"  {row['person']} at {row['employer']}: {row['finding']}")
    return stubs, jobs


def _withholding_backtests(c: Compute, result, stubs, decisions):
    """Every stub's federal, Wisconsin and Pennsylvania withholding recomputed and explained."""
    reports, run_id, log = c.reports, c.run_id, c.log
    backtest = withholding.backtest(stubs, withholding.explained_by_people(decisions, "federal"))
    backtest["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "withholding_backtest"), backtest)
    unexplained = int(backtest["explanation"].str.startswith("unexplained").sum())
    by_you = int(backtest["explanation"].str.startswith("explained by you").sum())
    result["withholding_unexplained"] = unexplained
    say(log, f"withholding_backtest: {len(backtest)} stubs, {unexplained} unexplained, {by_you} explained by you")

    wi = wisconsin.backtest(stubs, withholding.explained_by_people(decisions, "wi"))
    wi["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "wi_withholding_backtest"), wi)
    wi_unexplained = wi[wi["explanation"].str.startswith("unexplained")]
    result["wi_withholding_unexplained"] = len(wi_unexplained)
    wi_by_you = int(wi["explanation"].str.startswith("explained by you").sum())
    say(
        log,
        f"wi_withholding_backtest: {len(wi)} stubs, {len(wi_unexplained)} unexplained, {wi_by_you} explained by you",
    )
    for row in wi_unexplained.to_dict("records"):
        say(log, f"  {row['pay_date']} {row['person']}: {row['explanation']}")

    pa = pennsylvania.backtest(stubs)
    pa["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "pa_withholding_backtest"), pa)
    pa_unexplained = int(pa["explanation"].str.startswith("unexplained").sum())
    result["pa_withholding_unexplained"] = pa_unexplained
    say(log, f"pa_withholding_backtest: {len(pa)} stubs, {pa_unexplained} unexplained")


def _projection(c: Compute, result, books, stubs, jobs, facts, form_8949):
    """This year's return projected from the stubs, vests and sales (with last year beside it), the withholding
    plan and the elections. Returns the year's plan and elections for the weekly email (None for another year)."""
    ledger, log = c.ledger, c.log
    year = int(stubs["pay_date"].astype(str).max()[:4])
    sales = sheets.read_table(ledger, schema.table(schema.LEDGER, "equity_sales"))
    if len(form_8949):  # the gains as reported, wash sales worked out
        sales = lots.adjusted_sales(form_8949)
    if year not in tax.taxdata.PARAMS:
        return None, None
    vests = projection.scheduled_vests(
        *(
            sheets.read_table(ledger, schema.table(schema.LEDGER, t))
            for t in ("vest_schedule", "equity_vests", "prices")
        )
    )
    if vests:
        say(
            log,
            f"projection: vests from the vesting schedule at {vests['price']:,.2f} a share, the "
            f"{vests['months']}-month average (latest {vests['latest']:,.2f} on {vests['price_date']}; "
            "a month-old date means prices need fetching)",
        )
    lines, notes = _tax_lines(c, year, stubs, sales, facts, jobs, vests)
    tax_plan = _withholding_plan(c, year, books, stubs, sales, facts, jobs, vests)
    _say_projection(c, result, year, lines, notes)
    tax_elections = _elections(c, result, year, books, stubs, jobs)
    result["projection"] = dict(zip(lines["scenario"], lines["owe_or_refund"]))
    result["projection_year"] = year
    return tax_plan, tax_elections


def _tax_lines(c: Compute, year, stubs, sales, facts, jobs, vests):
    """The year projected, joining the tax history until its return is in, and last year's lines: as filed when
    its return is in return_facts, else from its stubs. Returns the projection's scenarios and notes."""
    reports, run_id, log = c.reports, c.run_id, c.log
    lines, notes, detail = projection.project(year, stubs, sales, facts, jobs, vests)
    lines["year"], lines["run_id"] = year, run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "projection"), lines)
    # last year too: as filed once its return is in return_facts, until then the year its stubs show
    details = [detail.assign(year=year)]
    filed_years = set(facts.loc[facts["kind"] == "filed", "year"].astype(str)) if not facts.empty else set()
    if str(year) not in filed_years:  # this year joins the history as projected, until its return is in
        now = taxhistory.projected(year, detail[detail["scenario"] == "as is"]).assign(basis="projected")
        before = taxhistory.history(facts).assign(basis="filed") if not facts.empty else now.iloc[0:0]
        sheets.replace_table(
            reports,
            schema.table(schema.REPORTS, "tax_history"),
            pd.concat([before, now], ignore_index=True).assign(run_id=run_id),
        )
    last = year - 1
    if last in tax.taxdata.PARAMS and projection.has_return(facts, last):
        details.append(projection.as_filed(last, facts).assign(year=last))
        filed_lines = details[-1][details[-1]["filed"].notna()]
        off = filed_lines[(filed_lines["value"] - filed_lines["filed"]).abs() > 1]
        say(
            log,
            f"tax_lines: {last} as filed, {len(filed_lines)} lines beside the filed return, "
            f"{len(off)} off by more than 1",
        )
    elif last in tax.taxdata.PARAMS and (stubs["pay_date"].astype(str).str[:4] == str(last)).any():
        details.append(projection.project(last, stubs, sales, facts, jobs, vests)[2].assign(year=last))
        say(log, f"tax_lines: {last} from its pay stubs until its return's figures are in return_facts")
    every = pd.concat(details, ignore_index=True)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "tax_lines"), every.assign(run_id=run_id))
    return lines, notes


def _withholding_plan(c: Compute, year, books, stubs, sales, facts, jobs, vests):
    """For each return, the extra withholding a paycheck that lands the year on the target refund. Returns the
    plan when the year is this one, for the weekly email."""
    reports, run_id, today, log = c.reports, c.run_id, c.today, c.log
    params = books["parameters"]
    setting = lambda key, default: float(categorize.household_parameter(params, key, default) or default)  # noqa: E731
    target, rate = setting("target_refund", 2000), setting("planned_supplemental_rate", 0.35)
    plan = projection.withholding_plan(year, stubs, sales, facts, target, rate, jobs, vests)
    tax_plan = plan if year == int(today[:4]) else None
    sheets.replace_table(
        reports, schema.table(schema.REPORTS, "withholding_plan"), plan.assign(year=year, run_id=run_id)
    )
    for row in plan.to_dict("records"):
        if pd.isna(row["suggested_extra"]):
            say(log, f"withholding plan {year}, {row['return']}: {row['note']}")
            continue
        goal = (
            ("a refund of " if row["target"] > 0 else "owing ") + format(abs(row["target"]), ",.0f")
            if row["target"]
            else "no balance"
        )
        say(
            log,
            f"withholding plan {year}, {row['return']}: with vests at {rate:.0%}, "
            f"{'refund' if row['projected'] >= 0 else 'owe'} {abs(row['projected']):,.0f}; for {goal}, "
            f"{row['person']} ({row['employer']}) withholds {row['suggested_extra']:,.2f} extra a "
            f"paycheck (now {row['current_extra']:,.2f}) for {row['paychecks_left']} paychecks"
            + ("" if abs(row["lands_at"] - row["target"]) <= 1 else f", which can only get to {row['lands_at']:,.0f}")
            + (" (an underpayment penalty is possible)" if row["penalty_free"] is False else ""),
        )
    return tax_plan


def _say_projection(c: Compute, result, year, lines, notes):
    """The projection's notes and each scenario's refund or balance, federal and Wisconsin, into the run log."""
    log = c.log
    for note in notes:
        say(log, f"projection {year}: {note}")
    for row in lines.to_dict("records"):
        verdict = "refund" if row["owe_or_refund"] < 0 else "owe"
        even = row["extra_to_break_even"]
        say(
            log,
            f"projection {year}, {row['scenario']}: {verdict} "
            f"{abs(row['owe_or_refund']):,.0f} (tax {row['total_tax']:,.0f}, "
            f"paid {row['withheld']:,.0f})"
            + ("" if even is None or pd.isna(even) else f"; extra withholding to break even {even:,.0f} a paycheck"),
        )
    wi_row = lines.iloc[0]
    wi_verdict = "refund" if wi_row["wi_owe_or_refund"] < 0 else "owe"
    say(
        log,
        f"projection {year}, Wisconsin: {wi_verdict} "
        f"{abs(wi_row['wi_owe_or_refund']):,.0f} (tax {wi_row['wi_tax']:,.0f}, "
        f"paid {wi_row['wi_withheld']:,.0f})",
    )
    result["wi_projection"] = int(wi_row["wi_owe_or_refund"])


def _elections(c: Compute, result, year, books, stubs, jobs):
    """Each person's plan contributions against the year's limits. Returns them when the year is this one."""
    reports, run_id, today, log = c.reports, c.run_id, c.today, c.log
    converts = categorize.household_parameter(books["parameters"], "after_tax_401k_converts_to_roth").upper() == "TRUE"
    plans = projection.elections(year, stubs, after_tax_converts=converts, comp_events=jobs)
    plans["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "elections"), plans)
    tax_elections = plans if year == int(today[:4]) else None
    for row in plans[plans["status"] != "at limit"].to_dict("records"):
        say(log, f"elections {year}: {row['person']} {row['plan']}: {row['status']} ({row['gap']:+,.2f})")
    return tax_elections


def _settings_and_receipts(c: Compute, params):
    """Settings that can't be used, and the receipt exporter's searches (written to its file in Drive)."""
    reports, run_id, log, session = c.reports, c.run_id, c.log, c.session
    for key, v, problem in settings.check(params):
        say(log, f"settings: {key} {v!r} {problem}")
    searches = settings.receipt_searches(params)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "receipt_searches"), searches.assign(run_id=run_id))
    say(
        log,
        f"receipt_searches: {len(searches)} for the receipt exporter"
        + (f" ({', '.join(searches['store'])})" if len(searches) else ""),
    )
    try:
        written = inbox.write_receipt_searches(session.drive(), searches)
        if not written:
            say(log, "receipt_searches: the exporter has no searches file yet; run exportReceipts once, then compute")
    except Exception as e:  # the rest of compute goes on: the exporter keeps the searches it was last given
        say(log, f"receipt_searches: could not write the exporter's searches file: {e}", "WARN")


def _nudges(c: Compute, params, stubs, categorized, due, editor_review, tax_plan, tax_elections, changed):
    """FSA balances and the weekly email's to-dos."""
    reports, run_id, today, log = c.reports, c.run_id, c.today, c.log
    day = date.fromisoformat(today)
    fsa = nudges.fsa_balances(stubs, categorized, params, day, tax_elections)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "fsa_balances"), fsa.assign(run_id=run_id))
    for r in fsa.to_dict("records"):
        say(
            log,
            f"fsa_balances {r['year']} {r['account']}: {r['reimbursed']:,.2f} of {r['elected']:,.2f} "
            f"reimbursed, {r['left']:,.2f} left"
            + (f", claims due {r['claim_deadline']}" if r["claim_deadline"] else ""),
        )
    todo = nudges.build(day, params, due, editor_review, tax_plan, tax_elections, stubs, fsa, changed)
    sheets.replace_table(reports, schema.table(schema.REPORTS, "nudges"), todo.assign(run_id=run_id))
    showing = todo[(todo["show_from"] <= today) & ((todo["show_until"] == "") | (todo["show_until"] >= today))]
    say(log, f"nudges: {len(todo)} for the weekly email, {len(showing)} of them due in it as of today")


def _legacy_check(c: Compute, result, legacy_id, legacy_year, legacy_actuals, legacy_blank):
    """One legacy year's monthly actuals against the old rollup, and its lines with no category."""
    reports, run_id, log, session = c.reports, c.run_id, c.log, c.session
    rows = (
        session.sheets.open_by_key(legacy_id)
        .worksheet(str(legacy_year))
        .get_values(value_render_option=sheets.UNFORMATTED)
    )
    year_actuals = legacy_actuals[legacy_actuals["month"].str.startswith(str(legacy_year))]
    check = compute.compare_to_legacy(year_actuals, legacy.parse_rollup_rows(rows))
    check["run_id"] = run_id
    sheets.replace_table(reports, schema.table(schema.REPORTS, "legacy_check"), check)
    result.update({"checked": len(check), "mismatches": int((~check["match"]).sum())})
    say(log, f"legacy check {legacy_year}: {result['mismatches']} mismatches of {len(check)}")
    blank = legacy_blank[legacy_blank["date"].astype(str).str.startswith(str(legacy_year))]
    result["legacy_uncategorized"] = len(blank)
    for row in blank.to_dict("records"):
        say(log, f"  not in the old rollup (no category): {row['date']} {row['payee_raw']} {-float(row['amount']):.2f}")


def compute_all(
    session: Session,
    folder_id: str,
    legacy_id: str | None = None,
    legacy_year: int | None = None,
    run_id: str | None = None,
    log: Log = print,
) -> StageResult:
    """Stage 2: read the ledger, rebuild Reports, and optionally check one year against the legacy rollup.

    Each part below writes its own Reports tables and says what it did; this function only orders them and
    hands each the results it needs from the parts before."""
    run_id = run_id or new_run_id()
    log, said = recording(log)
    say(log, f"run {run_id}: reading the ledger")
    say(log, f"versions: {versions()}")
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    raw, reports, _ = bootstrap(session, folder_id)
    reading, problems = LedgerReading.of(raw)
    for line in problems:
        say(log, line, "WARN")
    for line in reading.notes():
        say(log, line)
    ledger = LedgerView(raw, reading)  # every read below sees accounts renamed, readings moved, activity corrected
    c = Compute(session, ledger, reports, run_id, computed_at, log)

    books = read_books(ledger)
    effective, decisions, params = books["effective"], books["decisions"], books["parameters"]
    moved = _transfers(c, books)
    legacy_checked, legacy_blank = compute.legacy_rows(books["txns"])
    legacy_actuals = compute.monthly_actuals(legacy_checked)  # as ingested, for the legacy check
    cat = _categories(c, books)
    categorized, counts, actuals = cat["categorized"], cat["counts"], cat["actuals"]
    _investment_flows(c)
    plan = _budget(c, books, categorized, actuals)
    published = _editor(c, books, plan["rules"], plan["planned"], actuals)
    checked = _checks(c, books, effective, categorized)
    sched = _mortgage(c, categorized)
    worth = _investments_and_homes(c, books, sched)
    form_8949, lot_result = _lots(c, books)
    worth.update(lot_result)
    worth.update(_retirement(c, books, actuals, sched))
    changed = _recurring(c, books, categorized)
    _flows(c, books, categorized)
    dupes = _duplicates(c, books, effective)

    paired, lonely, waiting = moved["paired"], moved["lonely"], moved["waiting"]
    result = {
        "run_id": run_id,
        "months": actuals["month"].nunique(),
        "rows": len(actuals),
        "subscription_price_changes": len(changed),
        "review_open_lines": counts["open_lines"],
        "downloads_due": len(checked["to_get"]),
        "balances_off": len(checked["off"]),
        "stubs_missing": len(checked["forgot"]),
        "pay_not_deposited": len(checked["lost"]),
        "possible_duplicates": len(dupes),
        "transfer_pairs": len(paired),
        "transfers_unmatched": len(waiting),
        "transfers_unmatched_answered": len(lonely) - len(waiting),
        # how lines since REVIEW_FROM were categorized, for the status page's breakdown
        "categories": {
            "since": categorize.REVIEW_FROM,
            "decided": counts["decided"],
            "waiting": counts["waiting"],
            "open_lines": counts["open_lines"],
        },
        **worth,
    }

    facts = _filed_taxes(c, result)
    _tax_sources(c, result)
    stubs, jobs = _pay(c, result)
    tax_plan = tax_elections = None  # this year's, for the weekly email
    if not stubs.empty:
        _withholding_backtests(c, result, stubs, decisions)
        tax_plan, tax_elections = _projection(c, result, books, stubs, jobs, facts, form_8949)
    _settings_and_receipts(c, params)
    _nudges(c, params, stubs, categorized, checked["due"], published["editor_review"], tax_plan, tax_elections, changed)
    if legacy_id and legacy_year:
        _legacy_check(c, result, legacy_id, legacy_year, legacy_actuals, legacy_blank)

    record_stage(reports, "compute", run_id, computed_at, result | {"versions": versions()}, said)
    return result
