// The drawer: one budget line's months, transactions and rule, or one recurring charge.

// --- the drawer: one line's months, transactions and budget line, or one recurring charge ---------------
function closeDrawer(quiet?: boolean) {
  document.querySelectorAll(".drawer, .scrim").forEach(function (n) {
    n.remove();
  });
  document.removeEventListener("keydown", drawerKey);
  if (!quiet) {
    budget.open = null;
    document.querySelectorAll("table.bv tr.chosen").forEach(function (r) {
      r.classList.remove("chosen");
    });
  }
}

function drawerKey(e: KeyboardEvent) {
  if (e.key === "Escape") closeDrawer();
}

function drawer(title: string, sub?: string) {
  closeDrawer(true);
  var scrim = el("div", null, "scrim");
  scrim.onclick = function () {
    closeDrawer();
  };
  var d = el("aside", null, "drawer");
  d.setAttribute("role", "dialog");
  d.setAttribute("aria-label", title);
  var head = el("div", null, "drawer-head");
  var titles = el("div");
  titles.appendChild(el("h2", title));
  if (sub) titles.appendChild(el("div", sub, "support"));
  head.appendChild(titles);
  var x = button(
    "Close",
    function () {
      closeDrawer();
    },
    "text",
  );
  head.appendChild(x);
  d.appendChild(head);
  document.body.appendChild(scrim);
  document.body.appendChild(d);
  document.addEventListener("keydown", drawerKey);
  x.focus();
  return d;
}

// A category's drawer: its spending and history, and its budget lines to change. With only, one budget line
// of a category that has several: its own section, under its name (spending is the category's, shared).
function openLine(category: string, v: BudgetView, only?: string) {
  var l: ViewLine | null = null;
  v.groups.forEach(function (g) {
    g.lines.forEach(function (x) {
      if (x.category === category) l = x;
    });
  });
  var line: ViewLine = l || {
    // a category with no line in the view
    category: category,
    group: budget.data!.book.groups[category] || "",
    priority: "",
    target: 0,
    actual: 0,
    compared: 0,
    recurring: [],
    byLine: [],
    recipients: [],
    balance: null,
  };
  var part = only
    ? line.byLine.filter(function (x) {
        return x.line === only;
      })[0]
    : null;
  var d = drawer(
    pretty(only ? only : category),
    (only ? pretty(category) + (part ? ", its target " + money.format(part.target) + " of the category's" : "") + ". " : "") +
      (line.group ? pretty(line.group) + ". " : "") +
      periodName(v.period) +
      (v.sameWeeks ? " so far" : "") +
      ": " +
      money.format(line.actual) +
      (line.target ? " of " + money.format(line.target) : "") +
      "; " +
      compareName(v) +
      ": " +
      money.format(line.compared) +
      ".",
  );
  lineChecks(d, line, v);
  lineHistory(d, category, v);
  var txns = el("div", null, "drawer-section");
  var show = button(
    "Show its transactions",
    function () {
      show.remove();
      txns.appendChild(el("div", "Loading…", "support"));
      txns.id = "drawer-txns";
      call(
        "getTransactions",
        [v.period, [category], v.sameWeeks ? v.through : ""],
        function (list) {
          txns.textContent = "";
          txns.appendChild(transactionTable(list));
        },
        "drawer-txns",
      );
    },
    "small",
  );
  txns.appendChild(show);
  d.appendChild(txns);
  var lines = linesFor(category).filter(function (bl) {
    return !only || bl.line === only;
  });
  var redraw = function () {
    drawBudgetPage();
  };
  if (!lines.length) {
    section(d, "Budget line", "No budget line pays for " + category + " yet. Add one: it counts from the month it starts.");
    d.appendChild(budgetForm(null, redraw, category));
    return;
  }
  lines.forEach(function (bl) {
    section(
      d,
      lines.length > 1 || only ? "Budget line: " + bl.line : "Budget line",
      bl.in_force === "ended"
        ? "Ended " + bl.effective + "."
        : "Since " + longDay(bl.effective) + ": " + bl.in_words + "." + (bl.scheduled ? " Scheduled: " + bl.scheduled + "." : ""),
    );
    var note = changeNote("budget_line|" + bl.line);
    if (note) d.appendChild(el("p", note, "staged-note"));
    if (bl.rule === "loan") {
      // its target is the schedule's: the Home page shows it, month by month
      var to = el("p", null, "support");
      to.appendChild(
        button(
          "Open the amortization schedule",
          function () {
            closeDrawer(true);
            setView("home");
          },
          "text",
        ),
      );
      d.appendChild(to);
    }
    d.appendChild(budgetForm(bl, redraw, category));
  });
}

function openNewLine() {
  var d = drawer("New budget line", "A new line counts from the month it starts.");
  d.appendChild(
    budgetForm(
      null,
      function () {
        budget.open = null;
        drawBudgetPage();
      },
      "",
    ),
  );
}

function transactionTable(txns: Txn[]) {
  if (!txns.length) return el("p", "No transactions.", "support");
  var table = el("table", null, "txns");
  txns.forEach(function (t) {
    var r = el("tr");
    cell(r, t.date);
    var payee = cell(r, t.payee, "text");
    if (t.split) {
      var badge = el("span", "split", "badge");
      badge.title = t.split.split("; ").join("\n");
      payee.appendChild(document.createTextNode(" "));
      payee.appendChild(badge);
    }
    if (t.items && t.items.length) payee.appendChild(itemList(t.items));
    cell(r, cents.format(t.amount));
    var open = button(
      t.id,
      function (e) {
        e.stopPropagation();
        closeDrawer();
        openTransaction(t.id);
      },
      "link txn-link",
    );
    open.title = "Edit this transaction: its category, split, note and flags";
    cell(r, "", "text").appendChild(open);
    if (changedText("line|" + t.id)) r.className = "staged";
    table.appendChild(r);
  });
  return table;
}

// A line's twelve months up to the period's last: what it spent each month (red past its target) and
// the target as a dashed step line. The period's own months are drawn full, the others lighter.
function lineHistory(d: HTMLElement, category: string, v: BudgetView) {
  var NS = "http://www.w3.org/2000/svg";
  var book = budget.data!.book;
  var lastMonth = periodDays(v.period)[1].slice(0, 7);
  var months: string[] = [];
  for (var i = 11; i >= 0; i--) months.push(shiftPeriod(lastMonth, -i));
  var spent: Record<string, number> = {},
    target: Record<string, number> = {};
  book.days.forEach(function (x) {
    if (x[1] === category) spent[x[0].slice(0, 7)] = (spent[x[0].slice(0, 7)] || 0) + x[2];
  });
  book.targets.forEach(function (t) {
    if (t.category === category) target[t.month] = (target[t.month] || 0) + t.target;
  });
  var hi = 0;
  months.forEach(function (m) {
    hi = Math.max(hi, spent[m] || 0, target[m] || 0);
  });
  if (!hi) {
    d.appendChild(el("p", "Nothing spent or budgeted in the twelve months to " + periodName(lastMonth) + ".", "support"));
    return;
  }
  var step = Math.pow(10, Math.floor(Math.log10(hi)));
  var nice =
    [1, 2, 2.5, 5, 10]
      .map(function (k) {
        return k * step;
      })
      .filter(function (s) {
        return hi / s <= 4;
      })[0] || step * 10;
  var top = Math.ceil(hi / nice) * nice;
  var W = 440,
    H = 190,
    L = 48,
    B = 160,
    T = 16;
  var y = function (n: number) {
    return T + ((top - n) / top) * (B - T);
  };
  var svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("class", "history");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    category + ": spending each month against its target, " + periodName(months[0]) + " to " + periodName(lastMonth),
  );
  var add = function (name: string, attrs: Record<string, string | number>, text?: string) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, String(attrs[k]));
    });
    if (text !== undefined) e.textContent = text;
    svg.appendChild(e);
    return e;
  };
  for (var t = 0; t <= top + 0.001; t += nice) {
    add("line", { x1: L, x2: W, y1: y(t), y2: y(t), class: "grid" });
    add("text", { x: L - 6, y: y(t) + 4, "text-anchor": "end", class: "axis" }, money.format(t));
  }
  var bw = (W - L) / 12;
  var inPeriod = function (m: string) {
    return m + "-01" >= v.from && m + "-01" <= v.through;
  };
  months.forEach(function (m, n) {
    var s = spent[m] || 0,
      x = L + n * bw;
    var over = target[m] > 0 && s > target[m] + 0.5;
    var bar = add("rect", {
      x: x + 4,
      width: bw - 8,
      y: y(Math.max(s, 0)),
      height: Math.max(y(0) - y(Math.max(s, 0)), s > 0 ? 1 : 0),
      rx: 3,
      class: "bar-mark" + (over ? " over" : "") + (inPeriod(m) ? "" : " faint"),
    });
    var tip = document.createElementNS(NS, "title");
    tip.textContent =
      periodName(m) +
      ": spent " +
      money.format(s) +
      (target[m] ? " of " + money.format(target[m]) + (over ? ", over by " + money.format(s - target[m]) : "") : ", no target");
    bar.appendChild(tip);
    add(
      "text",
      { x: x + bw / 2, y: B + 16, "text-anchor": "middle", class: "axis" },
      MONTH_SHORT[Number(m.slice(5)) - 1].charAt(0),
    );
    if (m.slice(5) === "01" || n === 0)
      add("text", { x: x + bw / 2, y: B + 29, "text-anchor": "middle", class: "axis" }, m.slice(0, 4));
  });
  var path = "";
  months.forEach(function (m, n) {
    if (!target[m]) return;
    var x = L + n * bw;
    path += (path && target[months[n - 1]] ? " L" : " M") + x + "," + y(target[m]) + " L" + (x + bw) + "," + y(target[m]);
  });
  if (path) add("path", { d: path, class: "target-line" });
  var lastTarget = target[lastMonth];
  if (lastTarget)
    add("text", { x: W, y: y(lastTarget) - 6, "text-anchor": "end", class: "axis strong" }, "target " + money.format(lastTarget));
  svg.setAttribute("height", String(H));
  d.appendChild(svg);
}

// What may be wrong with a line item's numbers, first in its drawer: each check in words, a button to what it
// rests on, and "Looks right" for one that was looked at and is right (a looks_right edit: it settles this
// evidence only, so the check comes back if the evidence changes). One settled here says so until a run.
function lineChecks(d: HTMLElement, l: ViewLine, v: BudgetView) {
  checksFor(l, linesFor(l.category)).forEach(function (x) {
    var c = x.c,
      t = "looks_right|" + c.check;
    var box = el("div", null, "alert check" + (x.settled ? " settled" : ""));
    box.dataset.check = c.check;
    var text = el("span");
    text.appendChild(icon(x.settled ? "check" : "warning"));
    text.appendChild(document.createTextNode(c.detail));
    // whether "Looks right" waits to be saved, or for a run: the tag the page uses for any change in hand
    if (stagedText(t)) text.appendChild(el("span", "waiting to be saved", "tag warn"));
    else if (savedText(t)) text.appendChild(el("span", "saved, applies at the next run", "tag warn"));
    box.appendChild(text);
    var actions = el("span", null, "actions");
    var what = c.open.slice(0, c.open.indexOf(":"));
    if (!x.settled && what !== "line")
      actions.appendChild(
        button(
          CHECK_OPENS[what] || "Open",
          function () {
            openCheck(c.open, l.category, v);
          },
          "text",
        ),
      );
    actions.appendChild(
      button(
        x.settled ? "Check it again" : "Looks right",
        function () {
          if (staged[t])
            unstage(t); // undo a change not saved yet
          else
            stage(
              { kind: "looks_right", check: c.check, looks_right: x.settled ? "no" : "yes" },
              pretty(l.category) + ": " + c.label + (x.settled ? ", check again" : ", looks right"),
            );
          budget.open = { category: l.category };
          drawBudgetPage(); // the row's status follows, and the drawer opens again where it was
        },
        "text",
      ),
    );
    box.appendChild(actions);
    d.appendChild(box);
  });
}
