// Recurring: the charges tracked, by line, and their price changes.

// --- Recurring: the charges you track, by line ------------------------------------------------------------
function drawRecurring(body: HTMLElement) {
  var tracked = budget.data!.tracked,
    rows = editor!.subscriptions;
  var lead = el(
    "p",
    "Tracked charges are the fixed part of the budget: a line can take its target from its charges, " +
      "plus an allowance for the rest of its spending.",
    "support",
  );
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
  body.appendChild(lead);
  tracked
    .filter(function (t) {
      return t.status === "price changed";
    })
    .forEach(function (t) {
      body.appendChild(priceAlert(t));
    });
  var unanswered = rows.filter(function (r) {
    return r.marked === "" && r.status !== "stopped";
  });
  unanswered.forEach(function (r) {
    body.appendChild(trackAlert(r));
  });
  var byCategory: Record<string, Row[]> = {};
  tracked
    .filter(function (t) {
      return t.status !== "stopped";
    })
    .forEach(function (t) {
      (byCategory[t.category || "(no charges yet)"] = byCategory[t.category || "(no charges yet)"] || []).push(t);
    });
  var cards = el("div", null, "rec-cards");
  Object.keys(byCategory)
    .sort()
    .forEach(function (category) {
      var card = el("section", null, "panel rec-card");
      var h = el("h3", pretty(category));
      if (budget.data!.book.groups[category])
        h.appendChild(el("span", pretty(budget.data!.book.groups[category]), "tag group-tag"));
      var lines = linesFor(category).filter(function (l) {
        return l.in_force !== "ended";
      });
      if (lines.length)
        h.appendChild(
          el(
            "span",
            lines
              .map(function (l) {
                return l.in_words;
              })
              .join("; ")
              .replace(/^./, function (c) {
                return c.toUpperCase();
              }),
            "tag plain",
          ),
        );
      else h.appendChild(el("span", "no budget line", "tag plain"));
      card.appendChild(h);
      byCategory[category].forEach(function (t) {
        card.appendChild(chargeRow(t));
      });
      cards.appendChild(card);
    });
  if (!tracked.length)
    body.appendChild(el("p", "Nothing tracked yet. Charges that come back regularly are listed above to track.", "support"));
  body.appendChild(cards);
  var stopped = tracked
    .filter(function (t) {
      return t.status === "stopped";
    })
    .concat(
      rows.filter(function (r) {
        return r.marked === "" && r.status === "stopped";
      }),
    );
  if (stopped.length) {
    var card = el("section", null, "panel rec-card wide");
    card.appendChild(el("h3", "Seem to have stopped"));
    card.appendChild(
      el(
        "p",
        "No charge for over one and a half cadences. Merge one into the charge that took its place to keep one history.",
        "support",
      ),
    );
    stopped.forEach(function (t) {
      card.appendChild(chargeRow(t));
    });
    body.appendChild(card);
  }
  var no = rows.filter(function (r) {
    return r.marked === "no";
  });
  if (no.length) {
    var more = el("details");
    more.appendChild(el("summary", "Not recurring (" + no.length + ")"));
    no.forEach(function (r) {
      more.appendChild(chargeRow(r));
    });
    body.appendChild(more);
  }
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

function chargeRow(t: Row) {
  var row = el("div", null, "rec-row");
  row.tabIndex = 0;
  var left = el("div");
  left.appendChild(chargeTitle(t, drawBudgetPage, "span"));
  if (t.status === "price changed") left.appendChild(statusTag({ text: priceWords(t), tone: "check" }));
  var staged_ =
    changedText("subscription|" + t.key.toUpperCase()) ||
    changedText("recurring_merge|" + t.key.toUpperCase()) ||
    changedText("recurring_name|" + t.key.toUpperCase()) ||
    changedText("recurring_line|" + t.key.toUpperCase());
  if (staged_) left.appendChild(el("span", staged_, "tag warn"));
  var pays = chargeLine(t.key);
  if (pays) left.appendChild(el("span", "Pays " + pretty(pays), "tag plain"));
  var when =
    t.status === "stopped" ? "last charged " + longDay(t.last) : t.next_expected ? "next " + longDay(t.next_expected) : "";
  left.appendChild(
    el("div", [t.cadence, when, t.includes ? "includes " + t.includes : ""].filter(Boolean).join(", "), "support"),
  );
  row.appendChild(left);
  var perMonth = Number(t.yearly) / 12;
  row.appendChild(el("div", t.yearly ? cents.format(perMonth) + " a month" : "", "num"));
  var open = function () {
    openCharge(t.key);
  };
  row.onclick = open;
  row.onkeydown = function (e) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };
  return row;
}

function openCharge(key: string) {
  var r = editorCharge(key);
  var t = trackByKey(key);
  var d = drawer(chargeName(t || r), r.marked === "yes" ? "Tracked" : r.marked === "no" ? "Not recurring" : "Not tracked yet");
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

// What a charge's price will be after the next run, with the changes to its lines in hand: its latest line that
// still counts, against the one before (priceChanged, the engine's rule). Null with no change in hand.
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
    var perYear = tracked && CHARGES_A_YEAR[tracked.cadence];
    words =
      "After the next run: " +
      cents.format(latest) +
      " a charge, from the line of " +
      longDay(counted[0].date) +
      (perYear ? ", about " + money.format(latest * perYear) + " a year" : "") +
      ". " +
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
function priceAlert(t: Row) {
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

// A charge that comes back but isn't tracked: track it, merge it into one that is, or turn it down.
function trackAlert(r: Row) {
  var box = el("div", null, "alert");
  var text = el("span");
  text.appendChild(chargeTitle(r, drawBudgetPage));
  text.appendChild(
    document.createTextNode(
      " comes back " +
        r.cadence +
        " for about " +
        cents.format(Number(r.latest)) +
        " and isn't tracked." +
        (r.category ? " Its lines are in " + pretty(r.category) + "." : ""),
    ),
  );
  if (chargeLine(r.key)) text.appendChild(el("span", "pays " + chargeLine(r.key), "tag"));
  box.appendChild(text);
  var actions = el("span", null, "actions");
  var t = "subscription|" + r.key.toUpperCase();
  if (changeNote(t)) {
    actions.appendChild(el("span", changeNote(t), "support"));
    if (staged[t])
      actions.appendChild(
        button(
          "Undo",
          function () {
            unstage(t);
            drawBudgetPage();
          },
          "text",
        ),
      );
  } else {
    actions.appendChild(
      button("Track it", function () {
        stage({ kind: "subscription", key: r.key, subscription: "yes" }, r.key + ": track as recurring");
        drawBudgetPage();
      }),
    );
    actions.appendChild(
      button(
        "Pays a budget line\u2026",
        function () {
          openCharge(r.key);
        },
        "text",
      ),
    );
    actions.appendChild(
      button(
        "Merge into\u2026",
        function () {
          openCharge(r.key);
        },
        "text",
      ),
    );
    actions.appendChild(
      button(
        "Not recurring",
        function () {
          stage({ kind: "subscription", key: r.key, subscription: "no" }, r.key + ": don't track");
          drawBudgetPage();
        },
        "text",
      ),
    );
  }
  box.appendChild(actions);
  return box;
}
