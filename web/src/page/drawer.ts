// The drawer: one budget line's months, transactions and rule, or one recurring charge.

// --- the drawer: one line's months, transactions and budget line, or one recurring charge ---------------
function closeDrawer(quiet?: boolean) {
  document.querySelectorAll(".drawer, .scrim").forEach(function (n) {
    n.remove();
  });
  document.removeEventListener("keydown", drawerKey);
  if (!quiet) {
    budget.open = null;
    budget.formOpen = {}; // a drawer opened again leads with the line, not its form
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
  var inForce = linesFor(category);
  var pace = yearPaced(inForce) ? yearPace(v, category, inForce) : null;
  if (pace) d.appendChild(yearWords(pace));
  lineHistory(d, category, v, !!pace);
  var txns = el("div", null, "drawer-section");
  var show = button(
    "Show its transactions",
    function () {
      show.remove();
      txns.appendChild(el("div", "Loading…", "support"));
      txns.id = "drawer-txns";
      cachedCall(
        "getTransactions",
        [v.period, [category], v.sameWeeks ? v.through : ""],
        function (list) {
          var draw = function () {
            txns.textContent = "";
            // a month's lines biggest first (as the server sends them); a quarter's or a year's by date, as its
            // chart reads
            var shown =
              lensOf(v.period) === "month"
                ? list
                : list.slice().sort(function (x, y) {
                    return x.date < y.date ? -1 : x.date > y.date ? 1 : 0;
                  });
            txns.appendChild(lineList(shown, draw)); // each line with its fixes (evidence.ts)
            var days = periodDays(v.period);
            txns.appendChild(searchLink("category", category, days[0], v.sameWeeks && v.through ? v.through : days[1]));
          };
          draw();
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
    if (RULE_MONTHS[bl.rule]) working(d, bl, v); // a floating target shows its working (working.ts)
    if (bl.rule === "sinking_fund") fundWorking(d, bl, v); // an annual fund's balance shows its working (working.ts)
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
    d.appendChild(lineChange(bl, redraw, category));
  });
}

// A budget line's form behind one Change, so the drawer leads with the line in words and its evidence: what
// most visits are for. Opened, it stays open while the drawer is (a second amount can be scheduled at once);
// Cancel or closing the drawer puts it away. A line naming no category opens with it, its note in view.
function lineChange(bl: Row, redraw: () => void, category: string) {
  var box = el("div", null, "line-change");
  var named = bl.category || bl.spends_from;
  var broken =
    !!named &&
    !(editor!.categories || []).some(function (c) {
      return c.category === named;
    });
  var chosen = budget.formOpen && budget.formOpen[bl.line];
  var open = chosen === undefined ? broken : chosen;
  var set = function (now: boolean) {
    budget.formOpen = budget.formOpen || {};
    budget.formOpen[bl.line] = now;
  };
  if (!open) {
    var change = button(
      "Change",
      function () {
        set(true);
        box.replaceWith(lineChange(bl, redraw, category));
      },
      "small",
    );
    change.setAttribute("aria-label", "Change budget line " + bl.line);
    var acts = el("div", null, "actions");
    acts.appendChild(change);
    if (bl.in_force !== "scheduled only") {
      acts.appendChild(
        button(
          "Rename",
          function () {
            box.replaceWith(renameForm(bl, redraw, category));
          },
          "text",
        ),
      );
      if (mergeTargets(bl).length)
        acts.appendChild(
          button(
            "Merge into…",
            function () {
              box.replaceWith(lineMergeForm(bl, redraw, category));
            },
            "text",
          ),
        );
    }
    box.appendChild(acts);
    var renamed = changeNote("budget_line_name|" + bl.line);
    if (renamed) box.appendChild(el("p", renamed, "staged-note"));
    return box;
  }
  box.appendChild(budgetForm(bl, redraw, category));
  box.appendChild(
    button(
      "Cancel",
      function () {
        set(false);
        box.replaceWith(lineChange(bl, redraw, category));
      },
      "text",
    ),
  );
  return box;
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

// How a line item judged on its year stands, in its drawer: what it spent since January 1 against the year's
// budget, and why a month alone says little.
function yearWords(p: YearPace) {
  var box = el("p", null, "year-pace");
  var st = el("strong", money.format(p.spent) + " of the year's " + money.format(p.year));
  box.appendChild(st);
  box.appendChild(
    document.createTextNode(
      " spent from January 1 to " +
        longDay(p.through) +
        (p.setAside
          ? "; an even pace is " + money.format(p.pace) + ". It is set aside each month and spent from its balance, "
          : ". It is paid when it comes, ") +
        "so it is judged on its year rather than the month: each month's budget is only a twelfth of the year.",
    ),
  );
  return box;
}

// A line's twelve months up to the period's last: what it spent each month (red past its target, unless it is
// judged on its year) and the target as a dashed step line. The period's own months are drawn full, the others
// lighter.
function lineHistory(d: HTMLElement, category: string, v: BudgetView, yearly?: boolean) {
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
    empty(d, "Nothing spent or budgeted in the twelve months to " + periodName(lastMonth) + ".");
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
    var over = !yearly && target[m] > 0 && s > target[m] + 0.5;
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
    if (!x.settled && c.kind === "target_high" && budget.data!.inputs)
      // its evidence is the line's working, further down this drawer: every month it averages, and their lines
      actions.appendChild(
        button(
          "See how it was worked out",
          function () {
            var w = d.querySelector(".working");
            var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            if (w) w.scrollIntoView({ block: "start", behavior: still ? "auto" : "smooth" });
          },
          "text",
        ),
      );
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

// The names budget lines have or are about to have (a rename waiting for a run counts), so a new name is new.
function lineNamesTaken(): string[] {
  var names = (editor!.budgetLines || []).map(function (l) {
    return l.line;
  });
  Object.keys(staged)
    .concat(Object.keys(savedEdits))
    .forEach(function (t) {
      var s = staged[t] || savedEdits[t];
      if (s && s.edit.kind === "budget_line_name") names.push(String(s.edit.name));
    });
  return names;
}

// A line's new name: its history, scheduled amounts and the charges it pays go with it (decisions.line_names).
function renameForm(bl: Row, redraw: () => void, category: string) {
  var form = el("form", null, "edit line-change");
  form.appendChild(el("p", "Its history, scheduled amounts and the charges it pays keep the new name.", "support"));
  var name = field(form, "New name", textInput(bl.line, "wide"));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button("Keep", function () {
      var next = name.value.trim();
      var why = !next
        ? "Name the line."
        : next.length > 60
          ? "A name is at most 60 characters."
          : next === bl.line
            ? "That is its name now."
            : lineNamesTaken().indexOf(next) >= 0
              ? "A line is already called " + next + ": merge the two instead."
              : "";
      if (why) {
        error.textContent = why;
        return;
      }
      stage({ kind: "budget_line_name", line: bl.line, name: next }, "budget line " + bl.line + ": renamed " + next);
      redraw();
    }),
  );
  actions.appendChild(
    button(
      "Cancel",
      function () {
        form.replaceWith(lineChange(bl, redraw, category));
      },
      "text",
    ),
  );
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

// The lines one could be merged into: the others in force, its own category's first.
function mergeTargets(bl: Row): Row[] {
  var own = bl.category || bl.spends_from;
  return (editor!.budgetLines || [])
    .filter(function (l) {
      return l.line !== bl.line && l.in_force === "yes";
    })
    .sort(function (a, b) {
      var x = (a.category || a.spends_from) === own ? 0 : 1,
        y = (b.category || b.spends_from) === own ? 0 : 1;
      return x - y || a.line.localeCompare(b.line);
    });
}

// Two lines become one: this one ends on a date and the line it joins carries the amount both should have from
// then (the two added up when they budget the same way, else the other's own), and the charges this one pays
// pay the other. Before the date each keeps its own; a date at the lines' start makes them one all along.
function lineMergeForm(bl: Row, redraw: () => void, category: string) {
  var form = el("form", null, "edit line-change");
  var targets = mergeTargets(bl);
  var labels: Record<string, string> = {};
  targets.forEach(function (t) {
    labels[t.line] = t.line + " (" + t.in_words + ")";
  });
  var into = field(
    form,
    "Merge into",
    choice(
      targets.map(function (t) {
        return t.line;
      }),
      targets[0].line,
      labels,
    ),
  );
  var from = field(form, "From", dateInput(firstOfMonth()));
  var amount = field(form, "Amount", textInput("", "amount"));
  var said = el("p", "", "support");
  form.appendChild(said);
  var target = function (): Row {
    return targets.filter(function (t) {
      return t.line === into.value;
    })[0];
  };
  var fill = function () {
    var t = target();
    var same = t.rule === bl.rule && t.per === bl.per && isAmount(t.amount) && isAmount(bl.amount);
    amount.value = same ? String(Math.round((Number(t.amount) + Number(bl.amount)) * 100) / 100) : t.amount;
    said.textContent =
      bl.line +
      " ends on the date; " +
      t.line +
      " budgets this amount " +
      (t.per || "") +
      " from then" +
      (same ? ", the two added up" : ", its own way (" + t.in_words + ")") +
      ", and the charges " +
      bl.line +
      " pays pay it. If the two were always one, pick the day they started and the amount of one.";
  };
  into.onchange = fill;
  fill();
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button("Merge", function () {
      var t = target();
      var much = amount.value.trim().replace(/,/g, "");
      if (!isDate(from.value)) error.textContent = "Pick the date they become one.";
      else if (much && !isAmount(much)) error.textContent = "The amount is not a number.";
      else if (!much && (t.rule === "fixed" || t.rule === "billed" || t.rule === "sinking_fund"))
        error.textContent = "A " + t.rule + " line needs an amount.";
      else {
        stage(
          {
            kind: "budget_line",
            line: t.line,
            effective: from.value,
            rule: t.rule,
            category: t.category,
            account: t.account,
            priority: t.priority,
            amount: much,
            per: t.per,
            note: t.note,
            was: budgetWas(t),
          },
          "budget line " +
            t.line +
            ": " +
            bl.line +
            " merged in, " +
            (much ? much + " " + t.per + " " : "") +
            "from " +
            from.value,
        );
        stage(
          { kind: "budget_line", line: bl.line, effective: from.value, rule: "end", was: budgetWas(bl) },
          "budget line " + bl.line + ": merged into " + t.line + " from " + from.value,
        );
        (editor!.subscriptions || []).forEach(function (r) {
          if (chargeLine(r.key) === bl.line) linkCharge(r, t.line);
        });
        redraw();
        return;
      }
    }),
  );
  actions.appendChild(
    button(
      "Cancel",
      function () {
        form.replaceWith(lineChange(bl, redraw, category));
      },
      "text",
    ),
  );
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}
