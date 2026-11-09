// @ts-nocheck
// Recurring: the charges tracked, by line, and their price changes.

// --- Recurring: the charges you track, by line ------------------------------------------------------------
function drawRecurring(body) {
  var tracked = budget.data.tracked,
    rows = editor.subscriptions;
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
  var month = budgetView(budget.data.book, periodOf(today(), "month"), periodOf(today(), "month"), false, today());
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
  var byCategory = {};
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
      var h = el("h3", (budget.data.book.groups[category] ? budget.data.book.groups[category] + ": " : "") + category);
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
              .join("; "),
            "tag" +
              (lines.some(function (l) {
                return l.rule === "recurring";
              })
                ? ""
                : " plain"),
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
function editorCharge(key) {
  return (
    editor.subscriptions.filter(function (r) {
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

function chargeRow(t) {
  var row = el("div", null, "rec-row");
  row.tabIndex = 0;
  var left = el("div");
  left.appendChild(chargeTitle(t, drawBudgetPage, "span"));
  if (t.status === "price changed") left.appendChild(el("span", "price changed", "tag warn"));
  var staged_ =
    stagedText("subscription|" + t.key.toUpperCase()) ||
    stagedText("recurring_merge|" + t.key.toUpperCase()) ||
    stagedText("recurring_name|" + t.key.toUpperCase());
  if (staged_) left.appendChild(el("span", staged_, "tag warn"));
  var when = t.status === "stopped" ? "last charged " + t.last : t.next_expected ? "next " + t.next_expected : "";
  left.appendChild(
    el(
      "div",
      [t.cadence, t.status === "price changed" ? t.change : "", when, t.includes ? "includes " + t.includes : ""]
        .filter(Boolean)
        .join(", "),
      "support",
    ),
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

function openCharge(key) {
  var r = editorCharge(key);
  var t = trackByKey(key);
  var d = drawer(chargeName(t || r), r.marked === "yes" ? "Tracked" : r.marked === "no" ? "Not recurring" : "Not tracked yet");
  facts(d, [
    ["Every", (t && t.cadence) || r.cadence],
    ["Latest", t && t.latest ? cents.format(Number(t.latest)) : r.latest ? cents.format(Number(r.latest)) : ""],
    ["Last charged", (t && t.last) || r.last],
    ["A year", (t && t.yearly) || r.yearly ? cents.format(Number((t && t.yearly) || r.yearly)) : ""],
    ["Category", (t && t.category) || r.category],
    ["Spelled", r.example],
    ["Includes", (t && t.includes) || r.includes],
    ["Note", r.note],
  ]);
  var done = function () {
    closeDrawer();
    drawBudgetPage();
  };
  d.appendChild(subscriptionForm(r, done));
  d.appendChild(mergeForm(r, editor.subscriptions, done));
}

function trackByKey(key) {
  return trackedByKey()[key] || null;
}

// A tracked charge whose price changed: what it means for its line, and the change that follows it.
function priceAlert(t) {
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
    t.last +
    ". ";
  var actions = el("span", null, "actions");
  var perYear = { weekly: 52, biweekly: 26, monthly: 12, quarterly: 4, yearly: 1 }[t.cadence] || 0;
  var delta = ((Number(t.latest) - Number(t.previous)) * perYear) / 12;
  var line = lines[0];
  if (!line) words += "No budget line pays for " + t.category + ".";
  else if (line.rule === "recurring") words += line.line + " takes its target from its charges, so the target moves with it.";
  else if (line.rule === "fixed" && perYear) {
    var per = line.per || "monthly";
    var now = Number(String(line.amount).replace(/,/g, "")) || 0;
    var next = Math.round((now + delta / PER_MONTH[per]) * 100) / 100;
    words +=
      line.line + "'s target is " + cents.format(now) + " " + per + "; at the new price it would be " + cents.format(next) + ".";
    var t_ = "budget_line|" + line.line;
    if (stagedText(t_)) actions.appendChild(el("span", "Waiting to be saved: " + stagedText(t_), "support"));
    else
      actions.appendChild(
        button(
          "Update " + line.line + " to " + cents.format(next),
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
  } else words += line.line + "'s target follows its spending on its own (" + line.in_words + ").";
  text.appendChild(document.createTextNode(words));
  box.appendChild(text);
  box.appendChild(actions);
  return box;
}

// A charge that comes back but isn't tracked: track it, merge it into one that is, or turn it down.
function trackAlert(r) {
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
        (r.category ? " Its lines are in " + r.category + "." : ""),
    ),
  );
  box.appendChild(text);
  var actions = el("span", null, "actions");
  var t = "subscription|" + r.key.toUpperCase();
  if (stagedText(t)) {
    actions.appendChild(el("span", "Waiting to be saved: " + stagedText(t), "support"));
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
