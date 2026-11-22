// Recurring: the charges tracked, by line, and their price changes.

// --- Recurring: the charges you track, by line ------------------------------------------------------------
// A table like Lines: the charges grouped by the budget line each pays (its own link, else the one line in force
// for its category, else its category), with what needs an answer first (charges not tracked yet) and what is
// done with folded at the foot (stopped, not recurring). A price change is a status mark, as on Lines; a row
// opens the charge's drawer, where its price change, its lines, its budget line and its merges are.
type RecGroup = { id: string; name: string; how: string; rows: Row[]; byDefault: boolean; line: string; foot: boolean };

var CADENCE_RANK: Record<string, number> = { weekly: 1, biweekly: 2, monthly: 3, quarterly: 4, yearly: 5, irregular: 6 };

function drawRecurring(body: HTMLElement) {
  var tracked = budget.data!.tracked,
    rows = editor!.subscriptions;
  var active = tracked.filter(function (t) {
    return t.status !== "stopped" && t.status !== "no charges";
  });
  var yearly = active.reduce(function (s, t) {
    return s + (Number(t.yearly) || 0);
  }, 0);
  var month = budgetView(budget.data!.book, periodOf(today(), "month"), periodOf(today(), "month"), false, today());
  var box = el("div", null, "bv-summary");
  [
    [money.format(yearly / 12), "a month recurring, on average"],
    [money.format(yearly), "a year"],
    month.target ? [Math.round((100 * yearly) / 12 / month.target) + "%", "of this month's budget"] : null,
  ].forEach(function (s) {
    if (!s) return;
    var d = el("div");
    d.appendChild(el("b", s[0]));
    d.appendChild(el("span", s[1]));
    box.appendChild(d);
  });
  body.appendChild(box);
  body.appendChild(
    el(
      "p",
      "Tracked charges are the fixed part of the budget: a line can take its target from its charges, " +
        "plus an allowance for the rest of its spending.",
      "support",
    ),
  );
  var groups = recurringGroups(tracked, rows);
  if (!groups.length) {
    empty(body, "Nothing tracked yet. Charges that come back regularly are listed here to track once a run finds them.");
    return;
  }
  body.appendChild(recurringTable(groups));
}

function recurringGroups(tracked: Row[], rows: Row[]): RecGroup[] {
  var unanswered = rows.filter(function (r) {
    return r.marked === "" && r.status !== "stopped";
  });
  var lines: Record<string, RecGroup> = {};
  tracked
    .filter(function (t) {
      return t.status !== "stopped";
    })
    .forEach(function (t) {
      var pays = chargeLine(t.key);
      var inForce = linesFor(t.category).filter(function (l) {
        return l.in_force === "yes";
      });
      var line = pays || (inForce.length === 1 ? inForce[0].line : "");
      var id = line ? "line:" + line : "category:" + (t.category || "");
      if (!lines[id]) {
        var bl = line
          ? editor!.budgetLines.filter(function (l) {
              return l.line === line;
            })[0]
          : null;
        lines[id] = {
          id: id,
          name: line || t.category || "(no charges yet)",
          how: bl ? shortHow(bl) : inForce.length > 1 ? countOf(inForce.length, "budget line", "budget lines") : "No budget line",
          rows: [],
          byDefault: true,
          line: line,
          foot: false,
        };
      }
      lines[id].rows.push(t);
    });
  var out: RecGroup[] = [];
  if (unanswered.length)
    out.push({
      id: "untracked",
      name: "Not tracked yet",
      how: "Come back regularly: track them, or say they aren't recurring",
      rows: unanswered,
      byDefault: true,
      line: "",
      foot: false,
    });
  Object.keys(lines).forEach(function (id) {
    out.push(lines[id]);
  });
  var stopped = tracked
    .filter(function (t) {
      return t.status === "stopped";
    })
    .concat(
      rows.filter(function (r) {
        return r.marked === "" && r.status === "stopped";
      }),
    );
  if (stopped.length)
    out.push({
      id: "stopped",
      name: "Seem to have stopped",
      how: "No charge for over one and a half cadences: merge one into the charge that took its place to keep one history",
      rows: stopped,
      byDefault: false,
      line: "",
      foot: true,
    });
  var no = rows.filter(function (r) {
    return r.marked === "no";
  });
  if (no.length)
    out.push({ id: "no", name: "Not recurring", how: "Turned down", rows: no, byDefault: false, line: "", foot: true });
  return out;
}

function perMonthOf(t: Row) {
  return (Number(t.yearly) || 0) / 12;
}

function recurringTable(groups: RecGroup[]) {
  var wrap = el("div", null, "scroll");
  var table = el("table", null, "bv rec-table");
  var head = el("tr");
  var keys: (((t: Row) => string | number) | null)[] = [
    function (t) {
      return chargeName(t).toLowerCase();
    },
    function (t) {
      return CADENCE_RANK[t.cadence] || 9;
    },
    function (t) {
      return t.last || "";
    },
    function (t) {
      return t.next_expected || "";
    },
    function (t) {
      return Number(t.latest) || 0;
    },
    perMonthOf,
    null,
  ];
  var sort = budget.recSort && budget.recSort.col < keys.length ? budget.recSort : { col: -1, dir: 1 };
  ["Charge", "Every", "Last charged", "Next", "Latest", "A month", "Status"].forEach(function (h, i) {
    var th = el("th", null, i < 4 || i === 6 ? "text" : "");
    if (!keys[i]) {
      th.textContent = h;
      head.appendChild(th);
      return;
    }
    sortHead(th, h, sort.col === i ? (sort.dir > 0 ? "asc" : "desc") : "", function () {
      budget.recSort = sort.col === i ? { col: i, dir: -sort.dir } : { col: i, dir: i < 4 ? 1 : -1 };
      drawBudgetPage();
    });
    head.appendChild(th);
  });
  table.appendChild(el("thead")).appendChild(head);
  var body = el("tbody");
  var order = function (list: Row[]) {
    if (sort.col < 0)
      return list.slice().sort(function (a, b) {
        return perMonthOf(b) - perMonthOf(a); // the dearest first
      });
    var k = keys[sort.col]!;
    return list.slice().sort(function (a, b) {
      var x = k(a),
        y = k(b);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  };
  var total = function (g: RecGroup) {
    return g.rows.reduce(function (s, t) {
      return s + perMonthOf(t);
    }, 0);
  };
  // what needs an answer leads, then the lines by name (or by their total, sorted by it), then the folded foot
  var lead = groups.filter(function (g) {
    return g.id === "untracked";
  });
  var middle = groups
    .filter(function (g) {
      return g.id !== "untracked" && !g.foot;
    })
    .sort(function (a, b) {
      if (sort.col === 5) return (total(a) - total(b)) * sort.dir;
      var dir = sort.col === 0 ? sort.dir : 1;
      return (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1) * dir;
    });
  var foot = groups.filter(function (g) {
    return g.foot;
  });
  lead.concat(middle, foot).forEach(function (g) {
    var key = "rec:" + g.id;
    var open = g.byDefault ? !(budget.shut && budget.shut[key]) : !!(budget.opened && budget.opened[key]);
    var gr = el("tr", null, "group fold" + (g.foot ? " foot" : ""));
    var name = cell(gr, "");
    foldName(name, g.id.indexOf("line:") === 0 || g.id.indexOf("category:") === 0 ? pretty(g.name) : g.name, open);
    if (g.line && changedText("budget_line|" + g.line)) name.appendChild(el("span", "changed", "tag warn"));
    var how = cell(gr, countOf(g.rows.length, "charge", "charges") + ". " + g.how, "text rule");
    how.colSpan = 3; // Every, Last charged and Next
    cell(gr, "");
    cell(gr, g.foot ? "" : cents.format(total(g)));
    cell(gr, "", "text status");
    foldRow(gr, key, open, function () {
      if (g.byDefault) {
        budget.shut = budget.shut || {};
        budget.shut[key] = open;
      } else {
        budget.opened = budget.opened || {};
        budget.opened[key] = !open;
      }
    });
    body.appendChild(gr);
    if (!open) return;
    order(g.rows).forEach(function (t) {
      body.appendChild(chargeRow(t, g.id === "untracked"));
    });
  });
  table.appendChild(body);
  wrap.appendChild(table);
  return wrap;
}

// The editor's row for a merchant key (what is tracked, found or turned down), for its forms.
function editorCharge(key: string): Row {
  return (
    editor!.subscriptions.filter(function (r) {
      return r.key === key;
    })[0] || {
      key: key,
      marked: "",
      status: "",
      cadence: "",
      charges: "",
      last: "",
      latest: "",
      yearly: "",
      category: "",
      example: "",
      note: "",
      includes: "",
      merged: "",
    }
  );
}

// A charge's row: its name, cadence, when it came and comes next, its price, and its status (a price change, a
// change in hand, or for one not tracked yet the two answers). The row opens its drawer.
function chargeRow(t: Row, asks?: boolean) {
  var row = el("tr", null, "line nested charge" + (budget.open && budget.open.charge === t.key ? " chosen" : ""));
  row.tabIndex = 0;
  row.dataset.key = t.key;
  var name = cell(row, chargeName(t));
  if (chargeName(t) !== t.key) name.title = t.key + " on the statement";
  if (t.includes) name.appendChild(el("div", "includes " + t.includes, "support"));
  cell(row, t.cadence, "text");
  cell(
    row,
    t.last ? shortDay(t.last) + (t.last.slice(0, 4) !== today().slice(0, 4) ? ", " + t.last.slice(0, 4) : "") : "",
    "text",
  );
  cell(row, t.status === "stopped" ? "" : t.next_expected ? shortDay(t.next_expected) : "", "text");
  cell(row, t.latest ? cents.format(Number(t.latest)) : "");
  cell(row, t.yearly ? cents.format(perMonthOf(t)) : "");
  var sc = cell(row, "", "text status");
  var open = function () {
    openCharge(t.key);
  };
  var keys = ["subscription|", "recurring_merge|", "recurring_name|", "recurring_line|"].map(function (k) {
    return k + t.key.toUpperCase();
  });
  var note = keys
    .map(function (k) {
      return changeNote(k);
    })
    .filter(Boolean)
    .join(" ");
  var pays = chargeLine(t.key);
  if (asks && pays) sc.appendChild(el("span", "pays " + pays, "tag"));
  if (note) {
    sc.appendChild(el("span", note, "support"));
    var mine = keys.filter(function (k) {
      return staged[k];
    });
    if (asks && mine.length)
      sc.appendChild(
        button(
          "Undo",
          function (e) {
            e.stopPropagation();
            mine.forEach(unstage);
            drawBudgetPage();
          },
          "text",
        ),
      );
  } else if (asks) {
    var answers = el("span", null, "actions inline");
    answers.appendChild(
      button(
        "Track it",
        function (e) {
          e.stopPropagation();
          stage({ kind: "subscription", key: t.key, subscription: "yes" }, t.key + ": track as recurring");
          drawBudgetPage();
        },
        "small",
      ),
    );
    answers.appendChild(
      button(
        "Not recurring",
        function (e) {
          e.stopPropagation();
          stage({ kind: "subscription", key: t.key, subscription: "no" }, t.key + ": don't track");
          drawBudgetPage();
        },
        "text",
      ),
    );
    sc.appendChild(answers);
  } else if (t.status === "price changed")
    sc.appendChild(
      statusTag({ text: priceWords(t), tone: "check" }, function (e?: Event) {
        if (e) e.stopPropagation();
        open();
      }),
    );
  else if (t.status === "stopped") sc.appendChild(el("span", "Stopped", "muted"));
  row.setAttribute("aria-haspopup", "dialog");
  sc.appendChild(openHint());
  row.onclick = open;
  row.onkeydown = onEnter(open);
  return row;
}

function openCharge(key: string) {
  var r = editorCharge(key);
  var t = trackByKey(key);
  var d = drawer(chargeName(t || r), r.marked === "yes" ? "Tracked" : r.marked === "no" ? "Not recurring" : "Not tracked yet");
  budget.open = { charge: key }; // kept open when the page draws again (Status opens a charge this way too)
  if (t && t.status === "price changed") d.appendChild(priceAlert(t, true));
  var named = el("div", null, "charge-rename");
  named.appendChild(
    chargeTitle(t || r, function () {
      openCharge(key);
    }),
  );
  d.appendChild(named);
  facts(d, [
    ["Every", (t && t.cadence) || r.cadence],
    ["Latest", t && t.latest ? cents.format(Number(t.latest)) : r.latest ? cents.format(Number(r.latest)) : ""],
    ["Last charged", longDay((t && t.last) || r.last)],
    ["A year", (t && t.yearly) || r.yearly ? cents.format(Number((t && t.yearly) || r.yearly)) : ""],
    ["Category", pretty((t && t.category) || r.category)],
    ["Spelled", r.example],
    ["Includes", (t && t.includes) || r.includes],
    ["Pays budget line", chargeLine(key)],
    ["Note", r.note],
  ]);
  var done = function () {
    closeDrawer();
    drawBudgetPage();
  };
  chargeHistory(d, key, chargeName(t || r), t);
  d.appendChild(paysForm(r, done));
  d.appendChild(subscriptionForm(r, done));
  d.appendChild(mergeForm(r, editor!.subscriptions, done));
}

var CHARGE_LINES_SHOWN = 12; // the newest, before "Show all"
var chargeLinesAll: Record<string, boolean> = {}; // charges whose every line is shown

// The lines matched with a charge, newest first, each saying whether it counts toward the price and cadence.
// One that is not part of it (a one-time purchase from the same merchant, say) is marked one-off here, a line
// edit like the transaction drawer's; the next run works the charge out again without it.
function chargeHistory(d: HTMLElement, key: string, name: string, tracked: Row | null) {
  var all = ((budget.data && budget.data.charges) || []).filter(function (c) {
    return c.key === key;
  });
  var s = section(
    d,
    "Its charges",
    all.length
      ? "Every line matched with this charge, newest first. Mark one that isn't part of it, and the next run " +
          "works out the price and how often without it."
      : "No lines yet: the next run lists them here.",
  );
  if (!all.length) return;
  var shown = chargeLinesAll[key] ? all : all.slice(0, CHARGE_LINES_SHOWN);
  var table = el("table", null, "txns charge-lines");
  var reopen = function () {
    var at = (document.querySelector(".drawer") as HTMLElement | null)?.scrollTop || 0;
    openCharge(key);
    var now = document.querySelector(".drawer") as HTMLElement | null;
    if (now) now.scrollTop = at;
  };
  // the payee as most lines spell it goes unsaid; a line spelled otherwise says so
  var spelled: Record<string, number> = {};
  all.forEach(function (c) {
    spelled[c.payee] = (spelled[c.payee] || 0) + 1;
  });
  var usual = Object.keys(spelled).sort(function (x, y) {
    return spelled[y] - spelled[x];
  })[0];
  shown.forEach(function (c) {
    var t = "line|" + c.txn_id + "|one_off";
    var e = edited(t);
    var marked = c.why === "marked one-off"; // as of the last run
    var oneOff = e ? String((e.edit as Record<string, string>).value).toUpperCase() === "TRUE" : marked;
    var row = el("tr", null, oneOff || (!c.counts && !marked) ? "left-out" : ""); // left out once this is run
    row.dataset.txn = c.txn_id;
    cell(row, c.date, "text");
    cell(row, cents.format(c.spend), "spend");
    var said =
      oneOff !== marked
        ? oneOff
          ? "Left out after the next run"
          : "Counts again after the next run"
        : c.why
          ? c.why.charAt(0).toUpperCase() + c.why.slice(1)
          : "";
    var about = cell(row, "", "text about");
    if (c.payee !== usual) about.appendChild(el("div", c.payee));
    if (said) about.appendChild(el("div", said, "support"));
    if (changedText(t)) about.appendChild(el("span", "changed", "tag warn"));
    var act = cell(row, "", "text");
    var text = name + ": the " + cents.format(c.spend) + " line of " + c.date;
    if (oneOff)
      act.appendChild(
        button(
          "Count it",
          function () {
            if (staged[t] && !marked)
              unstage(t); // undo a change not saved yet
            else stage({ kind: "line", txn_id: c.txn_id, field: "one_off", value: "FALSE" }, text + " counts again");
            reopen();
          },
          "text",
        ),
      );
    else if (c.counts || marked)
      act.appendChild(
        button(
          "Not part of it",
          function () {
            if (staged[t] && marked) unstage(t);
            else stage({ kind: "line", txn_id: c.txn_id, field: "one_off", value: "TRUE" }, text + " is not part of it");
            reopen();
          },
          "text",
        ),
      );
    table.appendChild(row);
  });
  s.appendChild(table);
  var after = chargeAfter(all, name, tracked);
  if (after) s.appendChild(after);
  if (shown.length < all.length)
    s.appendChild(
      button(
        "Show all " + all.length,
        function () {
          chargeLinesAll[key] = true;
          reopen();
        },
        "text",
      ),
    );
}

var CHARGES_A_YEAR: Record<string, number> = { weekly: 52, biweekly: 26, monthly: 12, quarterly: 4, yearly: 1 }; // recurring.PER_YEAR
var EVERY_WORDS: Record<string, string> = {
  weekly: "every week",
  biweekly: "every two weeks",
  monthly: "every month",
  quarterly: "every quarter",
  yearly: "every year",
  irregular: "with no steady cadence",
};

// What a charge's price and cadence will be after the next run, with the changes to its lines in hand: its latest
// line that still counts, against the one before (priceChanged), and the cadence its counted lines fall in
// (chargeCadence), each the engine's rule. Null with no change in hand.
function chargeAfter(all: ChargeLine[], name: string, tracked: Row | null): HTMLElement | null {
  var pending = 0;
  var counted = all.filter(function (c) {
    var marked = c.why === "marked one-off"; // as of the last run
    var e = edited("line|" + c.txn_id + "|one_off");
    var oneOff = e ? String((e.edit as Record<string, string>).value).toUpperCase() === "TRUE" : marked;
    if (oneOff !== marked) pending++;
    return !oneOff && (c.counts || marked); // a line an order history shows as goods stays out
  }); // newest first, as the lines are
  if (!pending) return null;
  var words: string;
  if (!counted.length) words = "After the next run no line counts, so " + name + " has no price.";
  else {
    var latest = counted[0].spend,
      previous = counted.length > 1 ? counted[1].spend : null;
    var changed = priceChanged(latest, previous);
    // its cadence by the engine's rule (chargeCadence), from the lines that will count
    var every = chargeCadence(
      counted.map(function (c) {
        return c.date;
      }),
    );
    var was = tracked ? tracked.cadence : "";
    var yearAgo = new Date(Date.now() - 365 * 864e5).toISOString().slice(0, 10);
    var perYear = every
      ? latest * CHARGES_A_YEAR[every]
      : counted
          .filter(function (c) {
            return c.date.slice(0, 10) > yearAgo;
          })
          .reduce(function (t, c) {
            return t + c.spend;
          }, 0); // irregular: the last twelve months' charges, as the engine counts it
    var cadenceWords =
      tracked && (every || "irregular") !== was
        ? "It comes " + EVERY_WORDS[every || "irregular"] + " (it said " + (EVERY_WORDS[was] || was) + "). "
        : "";
    words =
      "After the next run: " +
      cents.format(latest) +
      " a charge, from the line of " +
      longDay(counted[0].date) +
      (perYear ? ", about " + money.format(perYear) + " a year" : "") +
      ". " +
      cadenceWords +
      (changed
        ? "It still differs from the one before (" + cents.format(previous as number) + "), so it still says price changed."
        : tracked && tracked.status === "price changed"
          ? "The one before is the same, so the price change clears."
          : "");
  }
  return el(
    "p",
    (words + " " + countOf(pending, "change", "changes") + " to its lines in hand.").replace("  ", " "),
    "working-after charge-after",
  );
}

// The budget line a charge pays: picking one makes that line's target follow the charge (once its rule is
// Recurring, which the form offers to set).
function paysForm(r: Row, done: () => void) {
  var form = el("form", null, "edit");
  var lines = editor!.budgetLines
    .filter(function (l) {
      return l.in_force === "yes";
    })
    .map(function (l) {
      return l.line;
    })
    .sort();
  var now = chargeLine(r.key);
  var labels: Record<string, string> = { "": "(no line)" };
  var pick = field(form, "Pays budget line", choice([""].concat(lines), now, labels));
  var say = el("div", "", "support");
  var actions = el("div", null, "actions");
  var line = function (name: string) {
    return editor!.budgetLines.filter(function (l) {
      return l.line === name;
    })[0];
  };
  var explain = function () {
    var l = pick.value ? line(pick.value) : null;
    say.textContent = !l
      ? ""
      : l.rule === "recurring"
        ? "Its target follows this charge's price, plus its allowance."
        : "Its target comes from its " + l.rule + " rule now; keeping this also makes it Recurring, so it follows the charge.";
  };
  pick.addEventListener("change", explain);
  explain();
  actions.appendChild(
    button(
      "Keep",
      function () {
        if (pick.value === now) {
          done();
          return;
        }
        linkCharge(r, pick.value);
        var l = pick.value ? line(pick.value) : null;
        if (l && l.rule !== "recurring")
          stage(
            {
              kind: "budget_line",
              line: l.line,
              effective: firstOfMonth(),
              rule: "recurring",
              category: l.category,
              account: l.account,
              priority: l.priority,
              amount: "",
              per: "monthly",
              note: l.note,
              was: budgetWas(l),
            },
            "budget line " + l.line + ": recurring for " + l.category + " from " + firstOfMonth(),
          );
        done();
      },
      "filled",
    ),
  );
  form.appendChild(say);
  form.appendChild(actions);
  return form;
}

function trackByKey(key: string) {
  return trackedByKey()[key] || null;
}

// A tracked charge whose price changed: what it means for its line, and the change that follows it.
function priceAlert(t: Row, inDrawer?: boolean) {
  var box = el("div", null, "alert changed");
  var text = el("span");
  text.appendChild(el("strong", chargeName(t)));
  var lines = linesFor(t.category).filter(function (l) {
    return l.in_force === "yes";
  });
  var words =
    " went from " +
    cents.format(Number(t.previous)) +
    " to " +
    cents.format(Number(t.latest)) +
    " " +
    t.cadence +
    " on " +
    longDay(t.last) +
    ". ";
  var actions = el("span", null, "actions");
  var perYear = ({ weekly: 52, biweekly: 26, monthly: 12, quarterly: 4, yearly: 1 } as Record<string, number>)[t.cadence] || 0;
  var delta = ((Number(t.latest) - Number(t.previous)) * perYear) / 12;
  var line = lines[0];
  if (!line) words += "No budget line pays for " + pretty(t.category) + ".";
  else if (line.rule === "recurring")
    words += pretty(line.line) + " takes its target from its charges, so the target moves with it.";
  else if (line.rule === "fixed" && perYear) {
    var per = line.per || "monthly";
    var now = Number(String(line.amount).replace(/,/g, "")) || 0;
    var next = Math.round((now + delta / PER_MONTH[per]) * 100) / 100;
    words +=
      pretty(line.line) +
      "'s target is " +
      cents.format(now) +
      " " +
      (PER_WORDS[per] || per) +
      "; at the new price it would be " +
      cents.format(next) +
      ".";
    var t_ = "budget_line|" + line.line;
    if (changeNote(t_)) actions.appendChild(el("span", changeNote(t_), "support"));
    else
      actions.appendChild(
        button(
          "Update " + pretty(line.line) + " to " + cents.format(next),
          function () {
            stage(
              {
                kind: "budget_line",
                line: line.line,
                effective: today(),
                rule: "fixed",
                category: line.category,
                account: line.account,
                priority: line.priority,
                amount: next.toFixed(2),
                per: per,
                note: line.note,
                was: budgetWas(line),
              },
              "budget line " +
                line.line +
                ": fixed " +
                next.toFixed(2) +
                " " +
                per +
                " from " +
                today() +
                " (" +
                t.key +
                "'s new price)",
            );
            drawBudgetPage();
          },
          "filled",
        ),
      );
  } else words += pretty(line.line) + "'s target follows its spending on its own (" + line.in_words + ").";
  // the price comes from its lines: one that isn't part of the charge can be left out there
  if (!inDrawer)
    actions.appendChild(
      button(
        "See its charges",
        function () {
          openCharge(t.key);
        },
        "text",
      ),
    );
  text.appendChild(document.createTextNode(words));
  box.appendChild(text);
  box.appendChild(actions);
  return box;
}
