// How a floating budget line's target was worked out, in its drawer: the months its average takes in (compute's
// target_inputs, the engine's own numbers), the arithmetic, and each month's lines with the fix on each. A line
// that isn't usual spending (a one-time purchase) is marked one-off here, and the working says what the target
// will be after the next run, so a number can be checked, fixed and seen to come right without asking anyone.

var MONTH_LONG = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
var RULE_MONTHS: Record<string, string> = {
  trailing: "the four months before it",
  lookback: "the twelve months before it",
  seasonal: "the same three months a year earlier",
};

var workingOpen: Record<string, boolean> = {}; // "line|month" whose lines are shown
var workingLines: Record<string, Txn[]> = {}; // "category|month": its lines, once loaded

function monthWords(m: string) {
  return MONTH_LONG[Number(m.slice(5, 7)) - 1] + " " + m.slice(0, 4);
}

function monthShort(m: string) {
  return MONTH_SHORT[Number(m.slice(5, 7)) - 1] + " " + m.slice(0, 4);
}

// The budget month a drawer explains: the period's last, or this month while the period runs on past it.
function workingMonth(v: BudgetView) {
  var last = periodDays(v.period)[1].slice(0, 7),
    now = v.through.slice(0, 7);
  return now && now < last ? now : last;
}

// Whether a line is one-off once the changes in hand are applied, and whether that differs from the last run.
function oneOffNow(t: Txn) {
  var e = edited("line|" + t.id + "|one_off");
  var now = e ? String((e.edit as Record<string, string>).value).toUpperCase() === "TRUE" : t.one_off;
  return { now: now, changed: now !== t.one_off };
}

function working(d: HTMLElement, bl: Row, v: BudgetView) {
  var inputs = budget.data!.inputs;
  if (!inputs) return; // before a run has written them
  var month = workingMonth(v);
  var rows = inputs.filter(function (x) {
    return x.line === bl.line && x.month === month;
  });
  var box = el("div", null, "working");
  box.dataset.line = bl.line;
  d.appendChild(box);
  drawWorking(box, bl, month, rows);
}

function drawWorking(box: HTMLElement, bl: Row, month: string, rows: TargetInput[]) {
  box.textContent = "";
  box.appendChild(el("h4", "How " + monthWords(month) + "'s budget was worked out"));
  if (!rows.length) {
    box.appendChild(
      el(
        "p",
        "None of " + (RULE_MONTHS[bl.rule] || "its months") + " has any spending yet, so it uses its own amount.",
        "support",
      ),
    );
    return;
  }
  var first = rows[0];
  box.appendChild(
    el(
      "p",
      "The average of " +
        RULE_MONTHS[first.rule] +
        " (a month with no spending counts as nothing), plus " +
        (first.sigma === 0.25 ? "a quarter" : first.sigma + " times") +
        " of how much the months differ. Lines marked one-off are left out.",
      "support",
    ),
  );
  var most = rows.reduce(function (a, b) {
    return b.spend > a.spend ? b : a;
  });
  var table = el("table", null, "txns working-months");
  var after: number[] = []; // each month's spending once the changes in hand are applied
  var pending = 0;
  rows.forEach(function (x) {
    var key = bl.line + "|" + x.input;
    var lines = workingLines[x.category + "|" + x.input];
    var spend = x.spend;
    (lines || []).forEach(function (t) {
      var before = !t.one_off,
        now = countsNow(t, x.category);
      if (before === now) return;
      pending++;
      spend += (now ? 1 : -1) * -t.amount; // a line's spending is its amount's negative
    });
    after.push(Math.max(spend, 0));
    var row = el("tr", null, x === most && rows.length > 1 && x.spend > 0 ? "most" : "");
    row.dataset.month = x.input;
    cell(row, monthShort(x.input), "text");
    cell(row, cents.format(x.spend), "spend");
    var about = cell(row, "", "text about");
    if (x === most && rows.length > 1 && x.spend > 0) about.appendChild(el("div", "The most of these months", "support"));
    if (x.left_out > 0.005) about.appendChild(el("div", cents.format(x.left_out) + " marked one-off, left out", "support"));
    var toggle = button(
      workingOpen[key] ? "Hide its lines" : "Its lines",
      function () {
        workingOpen[key] = !workingOpen[key];
        drawWorking(box, bl, month, rows);
      },
      "text",
    );
    toggle.setAttribute("aria-expanded", String(!!workingOpen[key]));
    cell(row, "", "text").appendChild(toggle);
    table.appendChild(row);
    if (workingOpen[key]) table.appendChild(monthLines(box, bl, month, rows, x));
  });
  box.appendChild(table);
  box.appendChild(
    el(
      "p",
      "Average " +
        cents.format(first.mean) +
        ", plus " +
        (first.sigma === 0.25 ? "a quarter of " : first.sigma + " × ") +
        cents.format(first.spread) +
        ": " +
        cents.format(first.value) +
        ".",
      "working-sum",
    ),
  );
  if (pending) {
    var next = floatingValue(after, first.sigma);
    box.appendChild(
      el(
        "p",
        "After the next run, about " +
          cents.format(next) +
          " (" +
          (next < first.value ? "down " : "up ") +
          cents.format(Math.abs(next - first.value)) +
          "), with " +
          countOf(pending, "change", "changes") +
          " to its lines in hand.",
        "working-after",
      ),
    );
  }
}

// Whether a line counts toward its category's averages once the changes in hand apply: not one-off, and not
// moved to another category.
function countsNow(t: Txn, category: string) {
  var moving = edited("line|" + t.id + "|category");
  var to = moving ? String((moving.edit as Record<string, string>).value) : t.category;
  return !oneOffNow(t).now && to === category;
}

// One month's lines in the category, with their fixes (evidence.ts). The first month opened loads the whole
// window in one read, so the others open at once and the preview counts every month's changes.
function monthLines(box: HTMLElement, bl: Row, month: string, rows: TargetInput[], x: TargetInput) {
  var tr = el("tr", null, "month-lines");
  var td = el("td");
  td.colSpan = 4;
  tr.appendChild(td);
  var lines = workingLines[x.category + "|" + x.input];
  if (!lines) {
    var id = "working-" + bl.line.replace(/[^a-z0-9]+/gi, "-") + "-" + x.input;
    td.id = id;
    td.appendChild(el("span", "Loading\u2026", "support"));
    var months = rows.map(function (r) {
      return r.input;
    });
    var first = months.reduce(function (a, b) {
        return b < a ? b : a;
      }),
      last = months.reduce(function (a, b) {
        return b > a ? b : a;
      });
    cachedCall(
      "getLines",
      [first + "-01", periodDays(last)[1], [x.category], []],
      function (list) {
        months.forEach(function (m) {
          workingLines[x.category + "|" + m] = [];
        });
        list.forEach(function (t) {
          var at = workingLines[x.category + "|" + t.date.slice(0, 7)];
          if (at && t.category === x.category) at.push(t);
        });
        drawWorking(box, bl, month, rows);
      },
      id,
    );
    return tr;
  }
  td.appendChild(
    lineList(lines, function () {
      drawWorking(box, bl, month, rows);
    }),
  );
  return tr;
}

// ---- an annual fund's balance ----

var fundOpen: Record<string, boolean> = {}; // "line|month" whose lines are shown
var fundLines: Record<string, Txn[]> = {}; // "category|month": its lines, once loaded

// How an annual (sinking fund) line's balance was worked out, in its drawer: what it carried into the year, then
// each month this year to the period's last: set aside, spent from it (every line in its category, one-offs
// included), and the balance after. Each month opens its lines, where one that belongs elsewhere is moved out,
// and the balance after the next run is said once a change is in hand. The amount set aside is the line's own,
// changed with its Change.
function fundWorking(d: HTMLElement, bl: Row, v: BudgetView) {
  var all = (budget.data!.book.funds || []).filter(function (f) {
    return f.line === bl.line;
  });
  if (!all.length || all[0].spent === undefined) return; // before a run has written them
  var box = el("div", null, "working fund-working");
  box.dataset.line = bl.line;
  d.appendChild(box);
  drawFund(box, bl, workingMonth(v), all);
}

function drawFund(box: HTMLElement, bl: Row, month: string, all: NonNullable<BudgetBook["funds"]>) {
  box.textContent = "";
  var year = month.slice(0, 4);
  var rows = all
    .filter(function (f) {
      return f.month.slice(0, 4) === year && f.month <= month;
    })
    .sort(function (a, b) {
      return a.month < b.month ? -1 : 1;
    });
  var before = all
    .filter(function (f) {
      return f.month < year + "-01";
    })
    .sort(function (a, b) {
      return a.month < b.month ? -1 : 1;
    });
  box.appendChild(el("h4", "How its balance was worked out"));
  if (!rows.length) {
    box.appendChild(el("p", "It has no months in " + year + " yet.", "support"));
    return;
  }
  var carried = before.length ? before[before.length - 1].balance : 0;
  box.appendChild(
    el(
      "p",
      (before.length
        ? "It carried " + cents.format(carried) + " into " + year + ". "
        : "It began in " + monthWords(rows[0].month) + ". ") +
        "Each month it sets aside its amount and pays what its category spent, every line counted, one-offs too.",
      "support",
    ),
  );
  var table = el("table", null, "txns working-months fund-months");
  var head = el("tr");
  ["Month", "Set aside", "Spent", "Balance", ""].forEach(function (h, i) {
    var th = el("th", h, i === 0 || i === 4 ? "text" : "");
    head.appendChild(th);
  });
  table.appendChild(head);
  var pending = 0,
    shift = 0; // what the changes in hand move out of (or into) the fund's spending
  rows.forEach(function (f) {
    var key = bl.line + "|" + f.month;
    (fundLines[bl.category + "|" + f.month] || []).forEach(function (t) {
      var was = t.category === bl.category,
        now = movedTo(t) === bl.category;
      if (was === now) return;
      pending++;
      shift += (now ? 1 : -1) * -t.amount;
    });
    var row = el("tr");
    row.dataset.month = f.month;
    cell(row, monthShort(f.month), "text");
    cell(row, cents.format(f.set_aside || 0), "");
    cell(row, cents.format(f.spent || 0), "spend");
    cell(row, cents.format(f.balance), f.balance < -0.005 ? "over" : "");
    var toggle = button(
      fundOpen[key] ? "Hide its lines" : "Its lines",
      function () {
        fundOpen[key] = !fundOpen[key];
        drawFund(box, bl, month, all);
      },
      "text",
    );
    toggle.setAttribute("aria-expanded", String(!!fundOpen[key]));
    cell(row, "", "text").appendChild(toggle);
    table.appendChild(row);
    if (fundOpen[key]) table.appendChild(fundMonthLines(box, bl, month, all, rows, f.month));
  });
  box.appendChild(table);
  var last = rows[rows.length - 1];
  box.appendChild(
    el(
      "p",
      cents.format(carried) +
        " carried, plus " +
        cents.format(
          rows.reduce(function (t, f) {
            return t + (f.set_aside || 0);
          }, 0),
        ) +
        " set aside, less " +
        cents.format(
          rows.reduce(function (t, f) {
            return t + (f.spent || 0);
          }, 0),
        ) +
        " spent: " +
        cents.format(last.balance) +
        " at the end of " +
        monthShort(last.month) +
        ".",
      "working-sum",
    ),
  );
  if (pending) {
    var next = last.balance - shift;
    box.appendChild(
      el(
        "p",
        "After the next run, about " +
          cents.format(next) +
          " (" +
          (next < last.balance ? "down " : "up ") +
          cents.format(Math.abs(next - last.balance)) +
          "), with " +
          countOf(pending, "line", "lines") +
          " moved " +
          (shift < 0 ? "out of it" : "into it") +
          ".",
        "working-after",
      ),
    );
  }
}

// The category a line is in once the changes in hand apply.
function movedTo(t: Txn) {
  var moving = edited("line|" + t.id + "|category");
  return moving ? String((moving.edit as Record<string, string>).value) : t.category;
}

// One month's lines in the fund's category, with their fixes. The first month opened loads the year so far in
// one read, so the others open at once and the preview counts every month's moves.
function fundMonthLines(
  box: HTMLElement,
  bl: Row,
  month: string,
  all: NonNullable<BudgetBook["funds"]>,
  rows: NonNullable<BudgetBook["funds"]>,
  m: string,
) {
  var tr = el("tr", null, "month-lines");
  var td = el("td");
  td.colSpan = 5;
  tr.appendChild(td);
  var lines = fundLines[bl.category + "|" + m];
  if (!lines) {
    var id = "fund-" + bl.line.replace(/[^a-z0-9]+/gi, "-") + "-" + m;
    td.id = id;
    td.appendChild(el("span", "Loading\u2026", "support"));
    cachedCall(
      "getLines",
      [rows[0].month + "-01", periodDays(rows[rows.length - 1].month)[1], [bl.category], []],
      function (list) {
        rows.forEach(function (r) {
          fundLines[bl.category + "|" + r.month] = [];
        });
        list.forEach(function (t) {
          var at = fundLines[bl.category + "|" + t.date.slice(0, 7)];
          if (at && t.category === bl.category) at.push(t);
        });
        drawFund(box, bl, month, all);
      },
      id,
    );
    return tr;
  }
  td.appendChild(
    lineList(lines, function () {
      drawFund(box, bl, month, all);
    }),
  );
  return tr;
}
