// The Budget page: lenses, comparisons, the lines and their gauges.

// --- the Budget page -----------------------------------------------------------------------------
// Lines: one period (a month, a quarter or a calendar year) beside any other of the same kind, shaped here
// by budgetview.js from the book getBudgetBook sends once, so a new lens or comparison asks the server
// nothing. A line opens a drawer with its months and its budget line. Recurring: the charges you track,
// by the line they belong to, with what changed first.
var budget: {
  tab: string;
  lens: string;
  period: string;
  compare: string;
  sameWeeks: boolean;
  data: Answer<"getBudgetBook"> | null;
  open: { category?: string; line?: string; charge?: string } | null; // the drawer open: a line's category, a new line, or a charge
  sort?: { col: number; dir: number }; // the lines' sort: a column, and 1 or -1
  unplanned?: Record<string, boolean>; // the groups whose unplanned line items are shown
  shut?: Record<string, boolean>; // the groups shut ("group:<name>"); every group is open at first
  layout?: string; // the Lines tab's view: "group" (A, the default), "kind" (B) or "cards" (C)
  compareOpen?: boolean; // the comparison menu is open (kept across a redraw)
  formOpen?: Record<string, boolean>; // the budget lines whose form is open in the drawer
  opened?: Record<string, boolean>; // the breakdowns open ("line:<category>", "loan:<loan id>"); shut at first
} = { tab: "lines", lens: "month", period: "", compare: "", sameWeeks: true, data: null, open: null };

var LENS_NAMES: Record<string, string> = { month: "Month", quarter: "Quarter", year: "Year" };

var RULE_NAMES: Record<string, string> = {
  fixed: "A fixed amount",
  recurring: "Its recurring charges, plus an allowance",
  trailing: "What the last 4 months spent",
  lookback: "What the last 12 months spent",
  seasonal: "What the same months last year spent",
  sinking_fund: "Set aside, spent from a balance",
  billed: "A bill in the months it comes (quarterly, say)",
  loan: "Its part of the loan payment (the amortization schedule)",
};

var PER_MONTH: Record<string, number> = {
  monthly: 1,
  biweekly: 26 / 12,
  weekly: 52 / 12,
  semi_monthly: 2,
  quarterly: 1 / 3,
  semi_annually: 1 / 6,
  annually: 1 / 12,
  "": 1,
};

var MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function loadBudget() {
  if (budget.data && editor) {
    drawBudgetPage();
    return;
  }
  byId("budget-body").textContent = "Loading…";
  var waiting = 0;
  var ready = function () {
    waiting -= 1;
    if (!waiting) drawBudgetPage();
  };
  if (!budget.data) {
    waiting += 1;
    call(
      "getBudgetBook",
      [],
      function (d) {
        budget.data = d;
        ready();
      },
      "budget-body",
    );
  }
  if (!editor) {
    waiting += 1;
    call(
      "getEditor",
      [],
      function (d) {
        editor = d;
        ready();
      },
      "budget-body",
    );
  }
}

function drawBudgetPage() {
  if (view !== "budget" || !budget.data || !editor) return;
  budgetDrawGen++;
  document.querySelectorAll<HTMLElement>("#budget-tabs button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.tab === budget.tab));
  });
  // redrawn where it is: emptying the page would otherwise drop the window back to the top
  var x = window.scrollX,
    y = window.scrollY;
  var wide = document.querySelector<HTMLElement>("#budget-body .scroll");
  var left = wide ? wide.scrollLeft : 0;
  var body = byId("budget-body");
  body.style.minHeight = body.offsetHeight + "px";
  body.textContent = "";
  closeDrawer(true);
  if (budget.tab === "recurring") drawRecurring(body);
  else drawLinesTab(body);
  if (budget.open && budget.open.charge) openCharge(budget.open.charge); // on either tab: a check opens one too
  body.style.minHeight = "";
  var again = document.querySelector<HTMLElement>("#budget-body .scroll");
  if (again) again.scrollLeft = left;
  window.scrollTo(x, y);
}

function shortDay(d: string) {
  return MONTH_SHORT[Number(d.slice(5, 7)) - 1] + " " + Number(d.slice(8, 10));
}

// A day in a sentence: "Nov 1, 2026" (an ISO date belongs in a table, not in prose).
function longDay(d: string) {
  return /^\d{4}-\d{2}-\d{2}/.test(d || "") ? shortDay(d) + ", " + d.slice(0, 4) : d;
}

function signedMoney(n: number) {
  return (n > 0.5 ? "+" : n < -0.5 ? "-" : "") + money.format(Math.abs(n));
}

function defaultCompare(p: string) {
  var q = quickPicks(p);
  return q[q.length - 1];
}

function setLens(lens: string) {
  var day = today();
  if (budget.period && budget.period !== periodOf(today(), lensOf(budget.period))) day = periodDays(budget.period)[0];
  budget.lens = lens;
  budget.period = periodOf(day, lens);
  budget.compare = defaultCompare(budget.period);
}

// The budget lines in force (or ended) that pay for a category, from the editor's budget lines.
// A key handler that runs act on Enter or Space, as a click would.
function onEnter(act: () => void) {
  return function (e: KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      act();
    }
  };
}

function linesFor(category: string) {
  return editor!.budgetLines.filter(function (l) {
    return (l.category || l.spends_from) === category;
  });
}

function drawLinesTab(body: HTMLElement) {
  if (!budget.period) setLens(budget.lens);
  var book = budget.data!.book;
  var v = budgetView(book, budget.period, budget.compare, budget.sameWeeks, today());
  body.appendChild(budgetControls(v));
  if (!book.targets.length && !book.days.length) {
    body.appendChild(el("p", "No budget yet: add a budget line, then run ingest and compute.", "support"));
  } else {
    body.appendChild(layoutSwitch());
    body.appendChild(budgetSummary(v));
    var layout = layoutNow();
    body.appendChild(layout === "kind" ? kindView(v) : layout === "cards" ? cardsView(v) : budgetTable(v));
  }
  var add = el("div", null, "actions");
  add.appendChild(
    button(
      "New budget line",
      function () {
        budget.open = { line: "" };
        openNewLine();
      },
      "small",
    ),
  );
  body.appendChild(add);
  body.appendChild(
    el("div", "Choose a line for its months, its transactions and its budget line. Transfers and income are left out.", "note"),
  );
  if (budget.open && budget.open.category) openLine(budget.open.category, v, budget.open.line);
  else if (budget.open && budget.open.line === "") openNewLine();
}

function budgetControls(v: BudgetView) {
  var c = el("div", null, "bv-controls");
  var seg = el("span", null, "segmented");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "Length of the period");
  ["month", "quarter", "year"].forEach(function (l) {
    var b = button(LENS_NAMES[l], function () {
      setLens(l);
      drawBudgetPage();
    });
    b.setAttribute("aria-pressed", String(budget.lens === l));
    seg.appendChild(b);
  });
  c.appendChild(seg);
  var list = lensPeriods(budget.data!.book, budget.lens, today());
  var at = list.indexOf(budget.period);
  var move = function (n: number) {
    budget.period = list[at + n];
    budget.compare = defaultCompare(budget.period);
    drawBudgetPage();
  };
  var picker = el("span", null, "bv-picker");
  var earlier = button(
    "\u2039",
    function () {
      move(1);
    },
    "icon-button",
  );
  earlier.setAttribute("aria-label", "The " + budget.lens + " before");
  earlier.disabled = at < 0 || at >= list.length - 1;
  var labels: Record<string, string> = {};
  var current = periodOf(today(), budget.lens);
  // on a phone the lens and the period share one row: months as "Nov 2026", and "so far" left to the headline
  var narrow = !!(window.matchMedia && window.matchMedia("(max-width: 600px)").matches);
  list.forEach(function (p) {
    var name = periodName(p);
    if (narrow && budget.lens === "month") name = name.slice(0, 3) + name.slice(name.indexOf(" "));
    labels[p] = name + (p === current && !narrow ? ", so far" : "");
  });
  var pick = choice(list, budget.period, labels);
  pick.setAttribute("aria-label", "Period");
  pick.onchange = function () {
    budget.period = pick.value;
    budget.compare = defaultCompare(pick.value);
    drawBudgetPage();
  };
  var later = button(
    "\u203a",
    function () {
      move(-1);
    },
    "icon-button",
  );
  later.setAttribute("aria-label", "The " + budget.lens + " after");
  later.disabled = at <= 0;
  picker.appendChild(earlier);
  picker.appendChild(pick);
  picker.appendChild(later);
  c.appendChild(picker);
  var words = { month: "month", quarter: "quarter", year: "year" }[budget.lens] || budget.lens;
  c.appendChild(compareMenu(v, list, words));
  return c;
}

// What the period is set beside, as one button that says it ("Compared with November 2025, same weeks") and opens
// the choices: another period, and whether to count only the same weeks. Kept out of the way, since the default
// (a year earlier) is usually the one wanted.
function compareMenu(v: BudgetView, list: string[], words: string) {
  var panel = el("div");
  var label = el("label", null, "bv-compare");
  label.appendChild(el("span", "Compare to", "support"));
  var cmp = document.createElement("select");
  var quick = document.createElement("optgroup");
  quick.label = "Quick picks";
  quickPicks(budget.period).forEach(function (p, i) {
    var o = el("option", (i === 0 ? "Previous " + words : "A year earlier") + " (" + periodName(p) + ")");
    o.value = p;
    quick.appendChild(o);
  });
  cmp.appendChild(quick);
  var any = document.createElement("optgroup");
  any.label = "Any " + words;
  list
    .filter(function (p) {
      return p !== budget.period;
    })
    .forEach(function (p) {
      var o = el("option", periodName(p));
      o.value = p;
      any.appendChild(o);
    });
  cmp.appendChild(any);
  cmp.value = budget.compare;
  cmp.onchange = function () {
    budget.compare = cmp.value;
    budget.compareOpen = false; // chosen: the menu has done its job
    drawBudgetPage();
  };
  label.appendChild(cmp);
  panel.appendChild(label);
  if (v.partial) {
    var same = el("label", null, "check");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = budget.sameWeeks;
    box.onchange = function () {
      budget.sameWeeks = box.checked;
      drawBudgetPage(); // the menu stays open, so the effect shows beside the choice
    };
    same.appendChild(box);
    same.appendChild(document.createTextNode(" Same weeks only (" + shortDay(v.from) + " to " + shortDay(today()) + ")"));
    panel.appendChild(same);
  }
  return popMenu(
    "bv-compare-menu",
    el(
      "span",
      (window.matchMedia && window.matchMedia("(max-width: 600px)").matches ? "Against " : "Compared with ") +
        periodName(v.compare) +
        (v.partial && v.sameWeeks ? ", same weeks" : ""),
    ),
    panel,
    !!budget.compareOpen,
    function (open) {
      budget.compareOpen = open;
    },
  );
}

function compareName(v: BudgetView) {
  return (v.sameWeeks ? "the same weeks of " : "") + periodName(v.compare);
}
