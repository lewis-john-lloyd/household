"""Mortgage amortization from the loan and its events.

A loan row (loans) gives the original principal, the annual rate, the term in
months, the origination date and, when the payment includes escrow, the
monthly escrow amount (mortgage insurance paid with the payment counts as escrow
here: it comes off before principal and interest). Interest is paid in
arrears and the closing prepays the rest of the closing month, so the first
payment is due the second month after origination (closing in May, first
payment in July). Each month:

  1. a rate_change dated that month sets the new annual rate (its amount) and
     re-amortizes the balance over the months left;
  2. interest is a month's worth of the annual rate on the balance;
  3. the payment is the sum of that month's payment events, or the scheduled
     payment when the month has none (the last one clears what rounding left). Escrow comes off first; of the rest,
     up to the scheduled payment pays interest then principal, and anything
     above it is extra principal;
  4. a prepayment dated that month is extra principal;
  5. a recast dated that month applies its amount (a lump sum, if any) as
     extra principal, then re-amortizes the balance over the months left at
     the same rate, which lowers the payment and keeps the term.

The schedule stops at the balance reaching zero, the end of the term or
through_month, whichever comes first. paid_from_ledger is what the household's
own transactions categorized as mortgage add up to that month, for comparing
with the schedule; it is filled for a month only when one loan is running.

Missing loan fields produce a note and no rows, never an error.
"""

from datetime import date

import pandas as pd

COLUMNS = ["month", "loan_id", "payment", "interest", "principal", "escrow", "extra_principal", "balance",
           "paid_from_ledger"]


def _rate(value):
    """An annual rate as a fraction: 0.065, or 6.5 meaning percent."""
    r = float(value)
    return r / 100 if r > 1 else r


def _money(value):
    text = str(value).replace(",", "").replace("$", "").strip()
    return float(text) if text else 0.0


def payment(balance, annual_rate, months):
    """The level monthly payment that pays off balance over months."""
    if months <= 0:
        return round(balance, 2)
    r = annual_rate / 12
    if r == 0:
        return round(balance / months, 2)
    return round(balance * r / (1 - (1 + r) ** -months), 2)


def _month(value):
    return str(value).strip()[:7]


def _next_month(month):
    return (pd.Period(month, freq="M") + 1).strftime("%Y-%m")


def amortize(loan, events=None, through_month=None):
    """Monthly schedule rows for one loan (a dict-like loans row). Returns (DataFrame, notes)."""
    loan_id = str(loan.get("loan_id", "")).strip()
    try:
        principal = _money(loan["principal"])
        rate = _rate(loan["rate"])
        term = int(float(loan["term_months"]))
        start = _next_month(_next_month(_month(loan["originated"])))
    except (KeyError, TypeError, ValueError):
        return pd.DataFrame(columns=COLUMNS), [f"{loan_id or 'a loan'}: needs principal, rate, term_months and "
                                               "originated to be amortized; skipped"]
    if principal <= 0 or term <= 0:
        return pd.DataFrame(columns=COLUMNS), [f"{loan_id}: principal and term_months must be positive; skipped"]
    escrow = _money(loan.get("escrow", ""))
    through = _month(through_month or date.today().isoformat())

    ev = events if events is not None else pd.DataFrame(columns=["date", "loan_id", "kind", "amount"])
    ev = ev[ev["loan_id"].astype(str).str.strip() == loan_id].copy()
    ev["month"] = ev["date"].map(_month)
    ev["amount"] = ev["amount"].map(_money)

    def on(month, kind):
        return ev[(ev["month"] == month) & (ev["kind"] == kind)]["amount"]

    balance, scheduled = round(principal, 2), payment(principal, rate, term)
    rows, month, n = [], start, 0
    while n < term and month <= through and balance > 0.005:
        left = term - n
        changes = on(month, "rate_change")
        if len(changes):
            rate = _rate(changes.iloc[-1])
            scheduled = payment(balance, rate, left)
        interest = round(balance * rate / 12, 2)
        # the last payment, or one larger than what is owed, clears the balance (the cents rounding left)
        due = round(balance + interest, 2) if left == 1 else min(scheduled, round(balance + interest, 2))
        paid = on(month, "payment")
        total = round(float(paid.sum()), 2) if len(paid) else round(due + escrow, 2)
        to_escrow = round(min(escrow, total), 2)
        p_and_i = round(total - to_escrow, 2)
        regular = min(p_and_i, due)
        extra = round(p_and_i - regular + float(on(month, "prepayment").sum()), 2)
        principal_part = round(regular - interest, 2)
        recast = on(month, "recast")
        extra = round(extra + float(recast.sum()), 2)
        extra = min(extra, round(balance - principal_part, 2))
        balance = round(balance - principal_part - extra, 2)
        n += 1
        if len(recast):
            scheduled = payment(balance, rate, term - n)
        rows.append({"month": month, "loan_id": loan_id, "payment": total, "interest": interest,
                     "principal": principal_part, "escrow": to_escrow, "extra_principal": extra,
                     "balance": max(balance, 0.0), "paid_from_ledger": ""})
        month = _next_month(month)
    return pd.DataFrame(rows, columns=COLUMNS), []


def ledger_payments(categorized):
    """{month: amount} the household paid toward the mortgage, from its own categorized transactions."""
    if categorized is None or categorized.empty:
        return {}
    df = categorized[(categorized["category"] == "mortgage") & (categorized["era"] != "legacy")]
    amounts = -pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    return amounts.groupby(df["date"].astype(str).str[:7]).sum().round(2).to_dict()


def schedule(loans, events=None, categorized=None, through_month=None):
    """Every loan's schedule, with the household's recorded payments beside it. Returns (DataFrame, notes)."""
    if loans is None or loans.empty:
        return pd.DataFrame(columns=COLUMNS), ["mortgage: no loans in the ledger; add one to see its schedule"]
    frames, notes = [], []
    for loan in loans.to_dict("records"):
        rows, more = amortize(loan, events, through_month)
        frames.append(rows)
        notes += more
    out = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in frames) else pd.DataFrame(columns=COLUMNS)
    paid = ledger_payments(categorized)
    if not out.empty and paid:
        running = out.groupby("month")["loan_id"].transform("nunique")
        out["paid_from_ledger"] = [paid.get(m, 0.0) if k == 1 else ""
                                   for m, k in zip(out["month"], running)]
    return out.reset_index(drop=True), notes


def summary(sched):
    """One line per loan: its latest balance and how many payments the schedule holds."""
    lines = []
    for loan_id, g in sched.groupby("loan_id"):
        last = g.iloc[-1]
        lines.append(f"mortgage: {loan_id} balance {float(last['balance']):,.2f} after {len(g)} payments "
                     f"(through {last['month']})")
    return lines


# a loan's details, in the order the Home page lists them: (key, label, kind). kind says how to show a value:
# money, percent (an annual rate in percent), date, month, months, or text. Keys not listed here show after
# these, under their own name, as text.
DETAILS = [
    ("lender", "Lender", "text"), ("servicer", "Servicer", "text"), ("loan_type", "Loan type", "text"),
    ("purpose", "Purpose", "text"), ("product", "Product", "text"), ("originated", "Closed", "date"),
    ("first_payment", "First payment", "month"), ("maturity", "Last payment as scheduled", "month"),
    ("term_months", "Term", "months"), ("principal", "Loan amount", "money"), ("rate", "Interest rate", "percent"),
    ("apr", "APR", "percent"), ("monthly_pi", "Principal and interest a month", "money"),
    ("mortgage_insurance", "Mortgage insurance a month", "money"),
    ("mortgage_insurance_ends", "Mortgage insurance ends", "text"), ("escrow", "Escrow a month", "money"),
    ("points", "Points paid", "money"), ("closing_costs", "Closing costs", "money"),
    ("cash_to_close", "Cash to close", "money"), ("prepayment_penalty", "Prepayment penalty", "text"),
    ("balloon", "Balloon payment", "text"), ("assumable", "Assumable", "text"), ("late_fee", "Late fee", "text"),
    ("tip", "Total interest percentage", "percent"), ("lender_nmls", "Lender NMLS ID", "text"),
    ("balance", "Balance now", "money"), ("paid_principal", "Principal paid so far", "money"),
    ("paid_interest", "Interest paid so far", "money"), ("interest_left", "Interest still to pay", "money"),
]
DETAIL_COLUMNS = ["loan_id", "order", "key", "label", "value", "kind", "as_of", "note"]


def details(loans, facts, sched, month):
    """Each loan's details (DETAIL_COLUMNS): its loans row, its facts (the latest row per key on or before
    month wins), and from its schedule the first and last payments, the balance now and what has been and is
    still to be paid."""
    known = {k: (i, label, kind) for i, (k, label, kind) in enumerate(DETAILS)}
    rows = []
    for loan in (loans.to_dict("records") if loans is not None and len(loans) else []):
        lid = str(loan["loan_id"]).strip()
        got = {}
        for k in ("lender", "originated", "principal", "rate", "term_months", "escrow"):
            if str(loan.get(k, "")).strip():
                got[k] = (str(loan[k]).strip(), "", "")
        mine = facts[facts["loan_id"].astype(str).str.strip() == lid] if facts is not None and len(facts) else None
        if mine is not None and len(mine):
            mine = mine.assign(_at=mine["as_of"].astype(str).str.strip()).sort_values("_at", kind="stable")
            for r in mine.to_dict("records"):
                if r["_at"] and r["_at"][:7] > month:
                    continue
                got[str(r["key"]).strip()] = (str(r["value"]).strip(), r["_at"], str(r.get("note", "") or "").strip())
        g = sched[sched["loan_id"] == lid] if sched is not None and len(sched) else None
        if g is not None and len(g):
            paid, ahead = g[g["month"] <= month], g[g["month"] > month]
            got.setdefault("first_payment", (g["month"].iloc[0], "", "from the schedule"))
            got["maturity"] = (g["month"].iloc[-1], "", "the schedule as it stands")
            if len(paid):
                got["balance"] = (round(float(paid["balance"].iloc[-1]), 2), paid["month"].iloc[-1], "")
                got["paid_principal"] = (round(float(pd.to_numeric(paid["principal"]).sum()
                                                   + pd.to_numeric(paid["extra_principal"]).sum()), 2), "", "")
                got["paid_interest"] = (round(float(pd.to_numeric(paid["interest"]).sum()), 2), "", "")
            got["interest_left"] = (round(float(pd.to_numeric(ahead["interest"]).sum()), 2), "", "")
        for k, (value, at, note) in got.items():
            order, label, kind = known.get(k, (len(DETAILS), k.replace("_", " ").capitalize(), "text"))
            rows.append({"loan_id": lid, "order": order, "key": k, "label": label, "value": value, "kind": kind,
                         "as_of": at, "note": note})
    out = pd.DataFrame(rows, columns=DETAIL_COLUMNS)
    return out.sort_values(["loan_id", "order", "key"], kind="stable").reset_index(drop=True)
