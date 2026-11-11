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
  unplanned?: Record<string, boolean>; // the groups whose unplanned line items are shown
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
  var came = v.pay + v.otherIncome;
  if (came > 0)
    stat(
      money.format(came),
      "came in: take-home pay" + (v.otherIncome >= 0.5 ? " and " + money.format(v.otherIncome) + " other income" : ""),
      "lead",
    );
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
  if (came > 0)
    stat(
      v.actual > came + 0.5 ? money.format(v.actual - came) : money.format(came - v.actual),
      v.actual > came + 0.5 ? "drawn from cash balances: more went out than came in" : "not spent: what came in and stayed",
      v.actual > came + 0.5 ? "warn" : "",
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

// The kind of a budget line, by how its budget is set: what the page calls it.
var KIND_OF_RULE: Record<string, string> = {
  recurring: "Recurring",
  billed: "Recurring",
  sinking_fund: "Annual",
  lookback: "Floating",
  trailing: "Floating",
  seasonal: "Floating",
  fixed: "Fixed",
};

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

// An annual line, judged on its year: spent from January 1 to the period's last day against an even pace.
function annualStatus(v: BudgetView, category: string, lines: Row[]): { text: string; cls: string } | null {
  var year = lines
    .filter(function (x) {
      return x.rule === "sinking_fund";
    })
    .reduce(function (t, x) {
      return t + (Number(x.amount) || 0) * (PER_MONTH[x.per] || 1) * 12;
    }, 0);
  if (!year) return null;
  var through = v.through,
    from = through.slice(0, 4) + "-01-01";
  var spent = budget
    .data!.book.days.filter(function (d) {
      return d[1] === category && d[0] >= from && d[0] <= through;
    })
    .reduce(function (t, d) {
      return t + d[2];
    }, 0);
  var days = (Date.parse(through) - Date.parse(from)) / 864e5 + 1,
    inYear = (Date.parse(through.slice(0, 4) + "-12-31") - Date.parse(from)) / 864e5 + 1;
  var pace = (year * days) / inYear;
  var share = Math.round((100 * spent) / year) + "% of the year";
  if (spent > year + 0.5)
    return { text: (spent / year).toFixed(1) + " times the year's " + money.format(year), cls: "over-text" };
  if (spent > pace + 0.5) return { text: share + ", " + money.format(spent - pace) + " ahead of pace", cls: "warn-text" };
  return { text: share + ", on pace", cls: "ok-text" };
}

// What a line item's row says in its status column.
function lineStatus(v: BudgetView, l: ViewLine, lines: Row[]): { text: string; cls: string } | null {
  var annual = lines.some(function (x) {
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
    if (due) return { text: due + " charge" + (due > 1 ? "s" : "") + " not seen yet", cls: "" };
    if (paid) return { text: "Paid", cls: "ok-text" };
  }
  if (l.target > 0 && l.actual > l.target + 0.5)
    return { text: "Over by " + money.format(l.actual - l.target), cls: "over-text" };
  return null;
}

function budgetTable(v: BudgetView) {
  var wrap = el("div", null, "scroll");
  var table = el("table", null, "bv");
  var head = el("tr");
  var kindKey = function (x: ViewGroup | ViewLine) {
    return (x as ViewLine).category ? kindOf(linesFor((x as ViewLine).category)) : "";
  };
  // each column but "How the budget is set" and "Status" sorts the groups, and the line items within each
  var keys: (((x: ViewGroup | ViewLine) => string | number) | null)[] = [
    function (x) {
      return ((x as ViewGroup).name || (x as ViewLine).category || "").toLowerCase();
    },
    null,
    kindKey,
    function (x) {
      return x.target || 0;
    },
    function (x) {
      return x.actual || 0;
    },
    function (x) {
      return x.target > 0 ? x.actual / x.target : x.actual > 0 ? Infinity : 0;
    },
    null,
  ];
  var sort = budget.sort && budget.sort.col < keys.length ? budget.sort : { col: -1, dir: 1 };
  ["Line item", "How the budget is set", "Kind", "Budget", "Spent", "Used", "Status"].forEach(function (h, i) {
    var th = el("th", null, i === 1 || i === 2 || i === 5 || i === 6 ? "text" : "");
    if (!keys[i]) {
      th.textContent = h;
      head.appendChild(th);
      return;
    }
    var b = button(
      h + (sort.col === i ? (sort.dir > 0 ? " \u2191" : " \u2193") : ""),
      function () {
        budget.sort = sort.col === i ? { col: i, dir: -sort.dir } : { col: i, dir: i === 0 || i === 2 ? 1 : -1 };
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
  var inForce = function (category: string) {
    return linesFor(category).filter(function (x) {
      return x.in_force !== "ended";
    });
  };
  order(v.groups).forEach(function (g) {
    // A line item with no budget line in force is unplanned: a group's unplanned ones fold into one row at its
    // foot (they count in its spending), opened by a click. A group of one row is that row alone.
    var planned = g.lines.filter(function (l) {
      return inForce(l.category).length || l.target;
    });
    var unplanned = g.lines.filter(function (l) {
      return planned.indexOf(l) < 0;
    });
    var alone = planned.length + (unplanned.length ? 1 : 0) === 1 && !unplanned.length;
    if (!alone) {
      var gr = el("tr", null, "group");
      cell(gr, g.name);
      cell(gr, g.lines.length + " line item" + (g.lines.length > 1 ? "s" : ""), "text rule");
      cell(gr, "", "text");
      cell(gr, g.target ? money.format(g.target) : "");
      cell(gr, money.format(g.actual));
      var gUsed = cell(gr, "", "text");
      gUsed.appendChild(spentBar(g.actual, g.target));
      cell(gr, "", "text");
      body.appendChild(gr);
    }
    var lineRow = function (l: ViewLine, cls: string) {
      var lines = inForce(l.category);
      var row = el("tr", null, cls + (budget.open && budget.open.category === l.category && !budget.open.line ? " chosen" : ""));
      row.tabIndex = 0;
      var name = cell(row, l.category);
      if (alone && g.name && g.name !== l.category) name.appendChild(el("span", g.name, "tag"));
      var several = lines.length > 1; // each line its own row below, to open and change by itself
      if (
        lines.some(function (x) {
          return changedText("budget_line|" + x.line);
        })
      )
        name.appendChild(el("span", "changed", "tag warn"));
      cell(
        row,
        several ? lines.length + " budget lines" : lines.length ? lines[0].in_words : l.target ? "scheduled" : "no budget line",
        "text rule",
      );
      var kind = kindOf(lines);
      var kc = cell(row, "", "text");
      kc.appendChild(el("span", kind || "Unplanned", "kind kind-" + (kind || "Unplanned").toLowerCase()));
      cell(row, l.target ? money.format(l.target) : "");
      var over = l.target > 0 && l.actual > l.target + 0.5;
      cell(row, money.format(l.actual), over ? "over" : "");
      var used = cell(row, "", "text");
      used.appendChild(spentBar(l.actual, l.target));
      var st = lineStatus(v, l, lines);
      var sc = cell(row, "", "text status");
      if (st) sc.appendChild(el("span", st.text, st.cls));
      var open = function () {
        budget.open = { category: l.category };
        openLine(l.category, v);
      };
      row.onclick = open;
      row.onkeydown = onEnter(open);
      body.appendChild(row);
      if (several)
        lines.forEach(function (bl) {
          var part = (l.byLine || []).filter(function (x) {
            return x.line === bl.line;
          })[0];
          var sub = el(
            "tr",
            null,
            "rec subline" + (budget.open && budget.open.category === l.category && budget.open.line === bl.line ? " chosen" : ""),
          );
          sub.tabIndex = 0;
          var subName = cell(sub, bl.line);
          if (changedText("budget_line|" + bl.line)) subName.appendChild(el("span", "changed", "tag warn"));
          cell(sub, bl.in_words, "text rule");
          var skc = cell(sub, "", "text");
          var sk = KIND_OF_RULE[bl.rule] || "Fixed";
          skc.appendChild(el("span", sk, "kind kind-" + sk.toLowerCase()));
          cell(sub, part ? money.format(part.target) : "");
          cell(sub, "");
          cell(sub, "");
          cell(sub, "");
          var openOne = function (e?: Event) {
            if (e) e.stopPropagation();
            budget.open = { category: l.category, line: bl.line };
            openLine(l.category, v, bl.line);
          };
          sub.onclick = openOne;
          sub.onkeydown = onEnter(openOne);
          body.appendChild(sub);
        });
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
        cell(rec, "", "text");
        cell(rec, r.expected === null ? "" : money.format(r.expected));
        cell(rec, money.format(r.actual));
        cell(rec, "");
        cell(rec, r.actual > 0 ? "Paid" : r.expected ? "Not seen yet" : "", "text status");
        body.appendChild(rec);
      });
    };
    order(planned).forEach(function (l) {
      lineRow(l, "line");
    });
    if (unplanned.length) {
      var shown = !!(budget.unplanned && budget.unplanned[g.name]);
      var spent = unplanned.reduce(function (t, l) {
        return t + l.actual;
      }, 0);
      var u = el("tr", null, "line unplanned");
      u.tabIndex = 0;
      cell(u, "Unplanned");
      cell(
        u,
        unplanned
          .map(function (l) {
            return l.category;
          })
          .join(", "),
        "text rule",
      );
      var uk = cell(u, "", "text");
      uk.appendChild(el("span", "No budget", "kind kind-unplanned"));
      cell(u, "");
      cell(u, money.format(spent));
      cell(u, "");
      cell(u, shown ? "Hide" : "Show " + unplanned.length, "text status link");
      u.setAttribute("aria-expanded", shown ? "true" : "false");
      var toggle = function () {
        budget.unplanned = budget.unplanned || {};
        budget.unplanned[g.name] = !shown;
        drawBudgetPage();
      };
      u.onclick = toggle;
      u.onkeydown = onEnter(toggle);
      body.appendChild(u);
      if (shown)
        order(unplanned).forEach(function (l) {
          lineRow(l, "line unplanned-item");
        });
    }
  });
  table.appendChild(body);
  wrap.appendChild(table);
  return wrap;
}
