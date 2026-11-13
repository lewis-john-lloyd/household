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
