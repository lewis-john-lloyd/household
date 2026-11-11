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
  open: { category?: string; line?: string } | null; // the drawer open: a line's category, or a new line
  sort?: { col: number; dir: number }; // the lines' sort: a column, and 1 or -1
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

function setBudgetTab(tab: string) {
  budget.tab = tab;
  budget.open = null;
  drawBudgetPage();
}

function drawBudgetPage() {
  if (view !== "budget" || !budget.data || !editor) return;
  document.querySelectorAll<HTMLElement>("#budget-tabs button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.tab === budget.tab));
  });
  var body = byId("budget-body");
  body.textContent = "";
  closeDrawer(true);
  if (budget.tab === "recurring") drawRecurring(body);
  else drawLinesTab(body);
}

function shortDay(d: string) {
  return MONTH_SHORT[Number(d.slice(5, 7)) - 1] + " " + Number(d.slice(8, 10));
}
function shortPeriod(p: string) {
  return lensOf(p) === "month" ? MONTH_SHORT[Number(p.slice(5, 7)) - 1] + " " + p.slice(0, 4) : periodName(p);
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
    body.appendChild(budgetSummary(v));
    body.appendChild(budgetTable(v));
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
  if (budget.open && budget.open.category) openLine(budget.open.category, v);
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
  list.forEach(function (p) {
    labels[p] = periodName(p) + (p === current ? ", so far" : "");
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
  var label = el("label", null, "bv-compare");
  label.appendChild(el("span", "Compare to", "support"));
  var cmp = document.createElement("select");
  var quick = document.createElement("optgroup");
  quick.label = "Quick picks";
  var words = { month: "month", quarter: "quarter", year: "year" }[budget.lens];
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
    drawBudgetPage();
  };
  label.appendChild(cmp);
  c.appendChild(label);
  if (v.partial) {
    var same = el("label", null, "check");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = budget.sameWeeks;
    box.onchange = function () {
      budget.sameWeeks = box.checked;
      drawBudgetPage();
    };
    same.appendChild(box);
    same.appendChild(document.createTextNode(" Same weeks only (" + shortDay(v.from) + " to " + shortDay(today()) + ")"));
    c.appendChild(same);
  }
  return c;
}

function compareName(v: BudgetView) {
  return (v.sameWeeks ? "the same weeks of " : "") + periodName(v.compare);
}

// The period at a glance: what is budgeted, what is spent, and a gauge of how full the budget is against
// how far through the period it is, then the same for each group.
function budgetSummary(v: BudgetView) {
  var box = el("div", null, "bv-summary bv-period");
  var figures = el("div", null, "bv-figures");
  var stat = function (big: string, small: string, cls?: string) {
    var s = el("div", null, cls);
    s.appendChild(el("b", big));
    s.appendChild(el("span", small));
    figures.appendChild(s);
  };
  stat(money.format(v.target), "budgeted for " + periodName(v.period), "lead");
  stat(
    money.format(v.actual),
    "spent" + (v.target ? ", " + Math.round((100 * v.actual) / v.target) + "% of the budget" : ""),
    "lead",
  );
  stat(
    money.format(Math.max(v.target - v.actual, 0)),
    v.actual > v.target + 0.5 ? "left: over by " + money.format(v.actual - v.target) : "left",
  );
  var d = v.actual - v.compared;
  stat(
    Math.abs(d) < 0.5 ? money.format(0) : signedMoney(d),
    (Math.abs(d) < 0.5 ? "the same as " : d > 0 ? "more than " : "less than ") +
      compareName(v) +
      " (" +
      money.format(v.compared) +
      ")",
  );
  if (v.recurring && v.actual > 0)
    stat(money.format(v.recurring), "of it recurring (" + Math.round((100 * v.recurring) / v.actual) + "%)");
  box.appendChild(figures);
  var days = periodDays(v.period),
    now = today();
  var span = (Date.parse(days[1]) - Date.parse(days[0])) / (24 * 3600 * 1000) + 1;
  var gone = v.partial ? Math.min(span, Math.max(0, (Date.parse(now) - Date.parse(days[0])) / (24 * 3600 * 1000) + 1)) / span : 1;
  box.appendChild(fullness("All lines", v.actual, v.target, gone, true));
  var groups = el("div", null, "bv-groups");
  v.groups
    .filter(function (g) {
      return g.target > 0 || g.actual > 0;
    })
    .forEach(function (g) {
      groups.appendChild(fullness(g.name, g.actual, g.target, gone, false));
    });
  box.appendChild(groups);
  if (v.partial)
    box.appendChild(
      el(
        "p",
        "The mark is how far through " +
          periodName(v.period) +
          " today is (" +
          Math.round(gone * 100) +
          "%): spending to its left is on pace.",
        "support small-print",
      ),
    );
  return box;
}

// One gauge: spent against budgeted, with a mark at the share of the period gone.
function fullness(name: string, actual: number, target: number, gone: number, big: boolean) {
  var row = el("div", null, "bv-gauge" + (big ? " big" : ""));
  var label = el("div", null, "gauge-label");
  label.appendChild(el("span", name, "gauge-name"));
  label.appendChild(
    el("span", money.format(actual) + (target ? " of " + money.format(target) : " spent, nothing budgeted"), "gauge-num"),
  );
  row.appendChild(label);
  var track = el("div", null, "gauge-track");
  var share = target > 0 ? actual / target : actual > 0 ? 1 : 0;
  var fill = el("i", null, share > 1.005 ? "over" : share > gone + 0.005 ? "ahead" : "");
  fill.style.width = Math.min(100, share * 100) + "%";
  track.appendChild(fill);
  if (gone < 1) {
    var mark = el("b");
    mark.style.left = gone * 100 + "%";
    track.appendChild(mark);
  }
  track.setAttribute("role", "img");
  track.setAttribute(
    "aria-label",
    name + ": " + Math.round(share * 100) + "% of the budget spent, " + Math.round(gone * 100) + "% of the period gone",
  );
  track.title = Math.round(share * 100) + "% spent, " + Math.round(gone * 100) + "% of the period gone";
  row.appendChild(track);
  return row;
}

// A bar for how much of a budget is spent: full and red past it, with a mark where the budget ends.
function spentBar(actual: number, target: number) {
  var m = el("span", null, "spent-bar");
  var fill = el("i");
  var over = target > 0 && actual > target + 0.5;
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

function budgetTable(v: BudgetView) {
  var wrap = el("div", null, "scroll");
  var table = el("table", null, "bv");
  var head = el("tr");
  // each column but "Target from" sorts the groups, and the lines within each, by it; again reverses
  var keys: (((x: ViewGroup | ViewLine) => string | number) | null)[] = [
    function (x) {
      return ((x as ViewGroup).name || (x as ViewLine).category || "").toLowerCase();
    },
    null,
    function (x) {
      return x.target || 0;
    },
    function (x) {
      return x.actual || 0;
    },
    function (x) {
      return x.target > 0 ? x.actual / x.target : x.actual > 0 ? Infinity : 0;
    },
    function (x) {
      return x.compared || 0;
    },
    function (x) {
      return (x.actual || 0) - (x.compared || 0);
    },
  ];
  var sort = budget.sort || { col: -1, dir: 1 };
  [
    "Line",
    "Target from",
    "Budget",
    "Spent",
    "Used",
    shortPeriod(v.compare) + (v.sameWeeks ? ", same weeks" : ""),
    "Change",
  ].forEach(function (h, i) {
    var th = el("th", null, i === 1 ? "text" : "");
    if (!keys[i]) {
      th.textContent = h;
      head.appendChild(th);
      return;
    }
    var b = button(
      h + (sort.col === i ? (sort.dir > 0 ? " \u2191" : " \u2193") : ""),
      function () {
        budget.sort = sort.col === i ? { col: i, dir: -sort.dir } : { col: i, dir: i === 0 ? 1 : -1 };
        drawBudgetPage();
      },
      "sort",
    );
    th.setAttribute("aria-sort", sort.col === i ? (sort.dir > 0 ? "ascending" : "descending") : "none");
    th.appendChild(b);
    head.appendChild(th);
  });
  table.appendChild(el("thead")).appendChild(head);
  var body = el("tbody");
  var tracked = trackedByKey();
  var order = function <T extends ViewGroup | ViewLine>(list: T[]): T[] {
    if (sort.col < 0) return list;
    var k = keys[sort.col]!; // a column that sorts
    return list.slice().sort(function (a, b) {
      var x = k(a),
        y = k(b);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  };
  order(v.groups).forEach(function (g) {
    var gr = el("tr", null, "group");
    cell(gr, g.name);
    cell(gr, "", "text");
    cell(gr, g.target ? money.format(g.target) : "");
    cell(gr, money.format(g.actual));
    cell(gr, "");
    cell(gr, money.format(g.compared));
    cell(gr, signedMoney(g.actual - g.compared));
    body.appendChild(gr);
    order(g.lines).forEach(function (l) {
      var lines = linesFor(l.category).filter(function (x) {
        return x.in_force !== "ended";
      });
      var row = el("tr", null, "line" + (budget.open && budget.open.category === l.category ? " chosen" : ""));
      row.tabIndex = 0;
      var name = cell(row, l.category);
      if (
        lines.some(function (x) {
          return x.rule === "recurring";
        })
      )
        name.appendChild(el("span", "recurring", "tag"));
      if (changedText("budget_line|" + (lines[0] ? lines[0].line : "\u0000")))
        name.appendChild(el("span", "changed", "tag warn"));
      cell(
        row,
        lines.length
          ? lines
              .map(function (x) {
                return x.in_words;
              })
              .join("; ")
          : l.target
            ? "scheduled"
            : "no budget line",
        "text rule",
      );
      cell(row, l.target ? money.format(l.target) : "");
      var over = l.target > 0 && l.actual > l.target + 0.5;
      cell(row, money.format(l.actual), over ? "over" : "");
      var used = cell(row, "", "text");
      used.appendChild(spentBar(l.actual, l.target));
      if (over) used.appendChild(el("span", "over " + money.format(l.actual - l.target), "over-text"));
      cell(row, money.format(l.compared));
      cell(row, signedMoney(l.actual - l.compared));
      var open = function () {
        budget.open = { category: l.category };
        openLine(l.category, v);
      };
      row.onclick = open;
      row.onkeydown = function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      };
      body.appendChild(row);
      l.recurring.forEach(function (r) {
        var t = tracked[r.key] || {};
        var rec = el("tr", null, "rec");
        cell(rec, chargeName({ key: r.key, name: t.name }));
        var about = [
          t.cadence,
          t.status === "price changed" && t.previous
            ? "now " + cents.format(Number(t.latest)) + " (was " + cents.format(Number(t.previous)) + ")"
            : "",
        ];
        cell(rec, about.filter(Boolean).join(", "), "text");
        cell(rec, r.expected === null ? "" : money.format(r.expected));
        cell(rec, money.format(r.actual));
        cell(rec, "");
        cell(rec, money.format(r.compared));
        cell(rec, "");
        body.appendChild(rec);
      });
    });
  });
  table.appendChild(body);
  wrap.appendChild(table);
  return wrap;
}
