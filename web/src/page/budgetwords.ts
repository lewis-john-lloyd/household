// The Budget page's words for a line item: its name, how its budget is set, its kind and its status, and the
// small bar of how much of a budget is spent. Shared by the three views (budgettable.ts, budgetviews.ts).

// A name to show for a ledger id: "food_and_dining" as "Food and dining", "subscription: netflix" as "Netflix".
function pretty(id: string) {
  var s = String(id || "");
  if (s.indexOf(": ") >= 0) s = s.slice(s.lastIndexOf(": ") + 2);
  s = s.replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

var PER_WORDS: Record<string, string> = {
  monthly: "a month",
  weekly: "a week",
  biweekly: "every two weeks",
  semi_monthly: "twice a month",
  quarterly: "a quarter",
  semi_annually: "every six months",
  annually: "a year",
  "": "a month",
};

function plainAmount(n: number) {
  return n.toLocaleString("en-US", { minimumFractionDigits: n % 1 ? 2 : 0, maximumFractionDigits: 2 });
}

// How a budget line is set, in a few words (the A2 design's column); the full rule is its tooltip.
function shortHow(bl: Row) {
  var n = Number(String(bl.amount || "").replace(/[^\d.-]/g, "")) || 0;
  var per = PER_WORDS[String(bl.per || "")] || String(bl.per || "");
  switch (bl.rule) {
    case "fixed":
      return n ? plainAmount(n) + " " + per : "A fixed amount";
    case "sinking_fund":
      return n ? plainAmount(Math.round(n * 12 * (PER_MONTH[String(bl.per || "")] || 1))) + " a year, set aside" : "Set aside";
    case "billed":
      return n ? plainAmount(n) + " " + per + ", when the bill comes" : "When the bill comes";
    case "recurring":
      return "Its recurring charges" + (n ? ", plus " + plainAmount(n) + " " + per : "");
    case "trailing":
      return "From the last 4 months";
    case "lookback":
      return "From the last 12 months";
    case "seasonal":
      return "From the same months last year";
    case "loan":
      return "From the amortization schedule";
    case "scheduled":
      return "Scheduled amounts";
  }
  return String(bl.in_words || "");
}

// A bar for how much of a budget is spent: full and red past it, with a mark where the budget ends. `judged`
// says whether it counts as over when the caller judges by more than this sum (a total holding a line set per
// year, which a month's payment takes past its twelfth without being over).
function spentBar(actual: number, target: number, judged?: boolean) {
  var m = el("span", null, "spent-bar");
  var fill = el("i");
  var over = judged === undefined ? target > 0 && actual > target + 0.5 : judged;
  fill.style.width = (target > 0 ? Math.max(0, Math.min(100, (100 * actual) / target)) : actual > 0 ? 100 : 0) + "%";
  if (over) fill.className = "over";
  if (!target) fill.className = "unbudgeted";
  m.appendChild(fill);
  if (over) {
    var mark = el("b");
    mark.style.left = (100 * target) / actual + "%";
    m.appendChild(mark);
  }
  return m;
}

function trackedByKey(): Record<string, Row> {
  var by: Record<string, Row> = {};
  budget.data!.tracked.forEach(function (t) {
    by[t.key] = t;
  });
  return by;
}

// The kind of a budget line, by how its budget is set: what the page calls it.
var KIND_OF_RULE: Record<string, string> = {
  recurring: "Recurring",
  billed: "Recurring",
  loan: "Recurring",
  sinking_fund: "Annual",
  lookback: "Floating",
  trailing: "Floating",
  seasonal: "Floating",
  fixed: "Fixed",
};

// A kind as a chip: its mark (a shape, the same in every view) and its name. Kinds are told apart by shape,
// so color stays free to mean a status.
function kindChip(kind: string) {
  var k = kind || "Unplanned";
  var chip = el("span", null, "kind kind-" + k.toLowerCase());
  chip.appendChild(kindMark(k));
  chip.appendChild(document.createTextNode(k));
  return chip;
}

function kindMark(kind: string) {
  var mark = el("span", null, "kind-mark mark-" + kind.toLowerCase());
  mark.setAttribute("aria-hidden", "true");
  return mark;
}

// A line item's kind: its budget lines' (or "Mixed"), or "" when it has none in force.
function kindOf(lines: Row[]) {
  var kinds = lines
    .map(function (x) {
      return KIND_OF_RULE[x.rule] || "Fixed";
    })
    .filter(function (k, i, all) {
      return all.indexOf(k) === i;
    });
  return kinds.length > 1 ? "Mixed" : kinds[0] || "";
}

// A line item's status, in one grammar for every view: a tone, shown as an icon and words (never color alone).
//   ok     as planned: paid, on pace
//   watch  worth knowing: ahead of pace, a charge not seen yet
//   check  a number that may rest on a wrong input: look at what it came from (`open`, budget_checks.py)
//   over   past its budget
//   ""     a plain fact
interface LineStatus {
  text: string;
  tone: string;
  open?: string; // for a check, what to look at: "charge:<key>", "txn:<id>" or "line:<category>"
}

var TONE_ICON: Record<string, string> = { ok: "check", watch: "trending", check: "warning", over: "error" };
var TONE_NAME: Record<string, string> = { ok: "On track", watch: "Watch", check: "Check", over: "Over" };

// A status as a tag: its icon and words; with `act`, a button that opens what it is about.
function statusTag(st: LineStatus, act?: () => void): HTMLElement {
  var tag = act ? button("", act, "st st-" + (st.tone || "fact")) : el("span", null, "st st-" + (st.tone || "fact"));
  if (TONE_ICON[st.tone]) {
    var i = icon(TONE_ICON[st.tone]);
    tag.appendChild(i);
    tag.setAttribute("aria-label", TONE_NAME[st.tone] + ": " + st.text);
  }
  tag.appendChild(el("span", st.text));
  if (act) {
    tag.addEventListener("click", function (e) {
      e.stopPropagation(); // the row's own click opens the line's drawer
    });
    tag.addEventListener("keydown", function (e) {
      e.stopPropagation();
    });
  }
  return tag;
}

// The mark at a row's end that says a click opens its drawer (shown on hover and focus).
function openHint() {
  var hint = el("span", null, "open-hint");
  hint.appendChild(icon("open"));
  return hint;
}

// The tracked charges of a line item whose latest charge changed price: worth a look before trusting the line,
// since a one-time purchase from the same merchant reads as a new price. A charge belongs to the budget line it
// pays, else to its line item's recurring charges or its category.
function priceChecks(l: ViewLine, lines: Row[]): Row[] {
  var names = lines.map(function (x) {
    return x.line;
  });
  var keys = l.recurring.map(function (r) {
    return r.key;
  });
  return (budget.data!.tracked || []).filter(function (t) {
    if (t.status !== "price changed") return false;
    var pays = chargeLine(t.key);
    return pays ? names.indexOf(pays) >= 0 : keys.indexOf(t.key) >= 0 || t.category === l.category;
  });
}

// A line item's checks (budget_checks.py, published by compute), each with whether someone said it looks
// right here and the run has not caught up yet. Before a run has written the table, the tracked charges that
// changed price stand in, worked out here.
function checksFor(l: ViewLine, lines: Row[]): { c: BudgetCheck; settled: boolean }[] {
  var all: BudgetCheck[] =
    budget.data!.checks === null || budget.data!.checks === undefined
      ? priceChecks(l, lines).map(function (t) {
          return {
            check: "price_changed|" + t.key + "|" + t.last + "|" + Number(t.latest).toFixed(2),
            kind: "price_changed",
            about: t.key,
            category: l.category,
            label: chargeName(t) + " " + priceWords(t),
            detail:
              chargeName(t) +
              " went from " +
              priceWords(t) +
              " at its latest charge (" +
              longDay(t.last) +
              "). A one-time purchase from the same merchant reads as a new price: if that charge isn't part of " +
              "it, mark it so in its charges.",
            open: "charge:" + t.key,
          };
        })
      : budget.data!.checks.filter(function (c) {
          return c.category === l.category;
        });
  return all.map(function (c) {
    var e = edited("looks_right|" + c.check);
    return { c: c, settled: !!e && String((e.edit as Record<string, string>).looks_right) === "yes" };
  });
}

// What a check opens: what it rests on (a charge's lines, a transaction's page), or the line item's drawer.
function openCheck(open: string, category: string, v: BudgetView) {
  var at = open.indexOf(":");
  var what = open.slice(0, at),
    id = open.slice(at + 1);
  if (what === "charge") openCharge(id);
  else if (what === "txn") openTransaction(id);
  else {
    budget.open = { category: category };
    openLine(category, v);
  }
}

function checkAction(st: LineStatus, category: string, v: BudgetView) {
  return function () {
    openCheck(st.open || "line:" + category, category, v);
  };
}

// The words on a check's button: what it opens.
var CHECK_OPENS: Record<string, string> = { charge: "See its charges", txn: "Open that line", line: "Open the line item" };

function priceWords(t: Row) {
  return cents.format(Number(t.previous)) + " \u2192 " + cents.format(Number(t.latest));
}

// A line paid less often than monthly (a fixed amount set per quarter, half year or year: tuition, a yearly
// gift) has a budget of a twelfth of its year each month, so a month says little: most look under and the one
// the payment lands in looks far over. Such a line item, and an annual (sinking fund) one, is judged on its year.
var YEARLY_PER: Record<string, boolean> = { quarterly: true, semi_annually: true, annually: true };

function yearlyLine(x: Row) {
  return x.in_force !== "ended" && (x.rule === "sinking_fund" || (x.rule === "fixed" && !!YEARLY_PER[x.per]));
}

// Whether a line item is judged on its year rather than its period: every budget line in force is yearly.
function yearPaced(lines: Row[]) {
  var now = lines.filter(function (x) {
    return x.in_force !== "ended";
  });
  return now.length > 0 && now.every(yearlyLine);
}

// Each category's spending from a year's start to a day, in one pass over the book's days, kept while the book
// and the days asked are the same: every line item judged on its year reads it, so the book is read once a draw.
var ytdCache: { days: unknown; from: string; through: string; sums: Record<string, number> } | null = null;

function yearToDate(from: string, through: string): Record<string, number> {
  var days = budget.data!.book.days;
  if (ytdCache && ytdCache.days === days && ytdCache.from === from && ytdCache.through === through) return ytdCache.sums;
  var sums: Record<string, number> = {};
  for (var i = 0; i < days.length; i++) {
    var d = days[i];
    if (d[0] >= from && d[0] <= through) sums[d[1]] = (sums[d[1]] || 0) + d[2];
  }
  ytdCache = { days: days, from: from, through: through, sums: sums };
  return sums;
}

interface YearPace {
  year: number; // the year's budget: its yearly lines' amounts, a year's worth
  spent: number; // spent from January 1 to the period's last day
  pace: number; // an even pace's share of the year by then
  through: string;
  setAside: boolean; // a sinking fund (set aside evenly), not a payment that lands when it lands
}

function yearPace(v: BudgetView, category: string, lines: Row[]): YearPace | null {
  var yearly = lines.filter(yearlyLine);
  var year = yearly.reduce(function (t, x) {
    return t + (Number(x.amount) || 0) * (PER_MONTH[x.per] || 1) * 12;
  }, 0);
  if (!year) return null;
  var through = v.through,
    from = through.slice(0, 4) + "-01-01";
  var spent = yearToDate(from, through)[category] || 0;
  var days = (Date.parse(through) - Date.parse(from)) / 864e5 + 1,
    inYear = (Date.parse(through.slice(0, 4) + "-12-31") - Date.parse(from)) / 864e5 + 1;
  return {
    year: year,
    spent: spent,
    pace: (year * days) / inYear,
    through: through,
    setAside: yearly.some(function (x) {
      return x.rule === "sinking_fund";
    }),
  };
}

// A line item judged on its year: past the year's budget is over. An annual fund is set aside evenly, so
// spending ahead of an even pace is worth knowing; a payment set per year lands when it lands, so it isn't.
function annualStatus(v: BudgetView, category: string, lines: Row[]): LineStatus | null {
  var p = yearPace(v, category, lines);
  if (!p) return null;
  var share = Math.round((100 * p.spent) / p.year) + "% of the year";
  if (p.spent > p.year + 0.5)
    return { text: (p.spent / p.year).toFixed(1) + " times the year's " + money.format(p.year), tone: "over" };
  if (!p.setAside) return { text: share + "'s " + money.format(p.year), tone: "ok" };
  if (p.spent > p.pace + 0.5) return { text: share + ", " + money.format(p.spent - p.pace) + " ahead of pace", tone: "watch" };
  return { text: share + ", on pace", tone: "ok" };
}

// A line item, or a total of them, as the over judgment needs it: its period's numbers, and whether it is
// judged on its year (and then whether it is over its year).
interface Judged {
  actual: number;
  target: number;
  yearly: boolean;
  over: boolean;
}

// One line item, judged by its own measure: its year for a line set per year, else its period.
function judgeLine(v: BudgetView, l: { category: string; target: number; actual: number }, lines: Row[]): Judged {
  var pace = yearPaced(lines) ? yearPace(v, l.category, lines) : null;
  return {
    actual: l.actual,
    target: l.target,
    yearly: !!pace,
    over: pace ? pace.spent > pace.year + 0.5 : l.target > 0 && l.actual > l.target + 0.5,
  };
}

// Whether a total (a group, a kind) is over: its other line items' spending past their budgets, or one judged
// on its year past its year. A yearly payment landing this month doesn't turn its group red, and every view
// says the same of the same numbers.
function totalOver(parts: Judged[]) {
  var actual = 0,
    target = 0,
    yearlyOver = false;
  parts.forEach(function (p) {
    if (p.yearly) yearlyOver = yearlyOver || p.over;
    else {
      actual += p.actual;
      target += p.target;
    }
  });
  return yearlyOver || (target > 0 && actual > target + 0.5);
}

// The bar for a line item judged on its year: spent this year against the year's budget, with an annual
// fund's even pace marked.
function yearBar(p: YearPace) {
  var bar = spentBar(p.spent, p.year);
  bar.classList.add("of-year");
  if (p.setAside && p.spent <= p.year + 0.5) {
    var mark = el("b", null, "pace");
    mark.style.left = Math.min(100, (100 * p.pace) / p.year) + "%";
    bar.appendChild(mark);
  }
  bar.title =
    "This year: " +
    money.format(p.spent) +
    " of " +
    money.format(p.year) +
    (p.setAside ? "; an even pace is " + money.format(p.pace) + " by " + longDay(p.through) : "");
  return bar;
}

// What a line item's row says in its status column.
function lineStatus(v: BudgetView, l: ViewLine, lines: Row[]): LineStatus | null {
  var checks = checksFor(l, lines).filter(function (x) {
    return !x.settled;
  });
  if (checks.length === 1) return { text: checks[0].c.label, tone: "check", open: checks[0].c.open };
  if (checks.length) return { text: checks.length + " things to check", tone: "check", open: "line:" + l.category };
  var annual =
    yearPaced(lines) ||
    lines.some(function (x) {
      return x.rule === "sinking_fund";
    })
      ? annualStatus(v, l.category, lines)
      : null;
  if (annual) return annual;
  if (l.recurring.length) {
    var paid = l.recurring.filter(function (r) {
      return r.actual > 0;
    }).length;
    var due = l.recurring.filter(function (r) {
      return r.expected && r.actual <= 0;
    }).length;
    if (due) return { text: due + " charge" + (due > 1 ? "s" : "") + " not seen yet", tone: "watch" };
    if (paid) return { text: "Paid", tone: "ok" };
  }
  if (l.target > 0 && l.actual > l.target + 0.5) return { text: "Over by " + money.format(l.actual - l.target), tone: "over" };
  return null;
}

// An annual line's fund in words: what is set aside and not yet spent, or how far it is overdrawn.
function fundText(balance: number): LineStatus {
  return balance < -0.5
    ? { text: "Overdrawn " + money.format(-balance), tone: "over" }
    : { text: "Balance " + money.format(balance), tone: "" };
}

function countOf(n: number, one: string, many: string) {
  return n + " " + (n === 1 ? one : many);
}
