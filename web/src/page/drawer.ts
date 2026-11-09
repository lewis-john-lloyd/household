// @ts-nocheck
// The drawer: one budget line's months, transactions and rule, or one recurring charge.

// --- the drawer: one line's months, transactions and budget line, or one recurring charge ---------------
function closeDrawer(quiet) {
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

function drawerKey(e) {
  if (e.key === "Escape") closeDrawer();
}

function drawer(title, sub) {
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

function openLine(category, v) {
  var l = null;
  v.groups.forEach(function (g) {
    g.lines.forEach(function (x) {
      if (x.category === category) l = x;
    });
  });
  l = l || {
    category: category,
    group: budget.data.book.groups[category] || "",
    target: 0,
    actual: 0,
    compared: 0,
    recurring: [],
  };
  var d = drawer(
    category,
    (l.group ? l.group + ". " : "") +
      periodName(v.period) +
      (v.sameWeeks ? " so far" : "") +
      ": " +
      money.format(l.actual) +
      (l.target ? " of " + money.format(l.target) : "") +
      "; " +
      compareName(v) +
      ": " +
      money.format(l.compared) +
      ".",
  );
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
  var lines = linesFor(category);
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
      lines.length > 1 ? "Budget line: " + bl.line : "Budget line",
      bl.in_force === "ended"
        ? "Ended " + bl.effective + "."
        : "Since " + bl.effective + ": " + bl.in_words + "." + (bl.scheduled ? " Scheduled: " + bl.scheduled + "." : ""),
    );
    var staged_ = stagedText("budget_line|" + bl.line);
    if (staged_) d.appendChild(el("p", "Waiting to be saved: " + staged_, "staged-note"));
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

function transactionTable(txns) {
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
    if (stagedText("line|" + t.id)) r.className = "staged";
    table.appendChild(r);
  });
  return table;
}

// A line's twelve months up to the period's last: what it spent each month (red past its target) and
// the target as a dashed step line. The period's own months are drawn full, the others lighter.
function lineHistory(d, category, v) {
  var NS = "http://www.w3.org/2000/svg";
  var book = budget.data.book;
  var lastMonth = periodDays(v.period)[1].slice(0, 7);
  var months = [];
  for (var i = 11; i >= 0; i--) months.push(shiftPeriod(lastMonth, -i));
  var spent = {},
    target = {};
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
  var y = function (n) {
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
  var add = function (name, attrs, text) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, attrs[k]);
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
  var inPeriod = function (m) {
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
  svg.setAttribute("height", H);
  d.appendChild(svg);
}
