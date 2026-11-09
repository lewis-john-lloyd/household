// Transactions' parts: review, payee rules, categories, settings, withholding, duplicates.

// --- the tabs ---------------------------------------------------------------------------------------
// A merchant question split into its lines leaves the list once each of those lines has a category
// waiting to be saved; undoing one brings the question back. splits: question key -> its open line ids.
var splits: Record<string, string[]> = {};
function splitAnswered(r: Row) {
  var ids = splits[r.key];
  return !!(
    ids &&
    ids.length &&
    ids.every(function (id) {
      return staged["line|" + id + "|category"];
    })
  );
}

function drawReview(body: HTMLElement) {
  masterDetail(body, {
    items: editor!.review.filter(function (r) {
      return r.kind === "line" || !splitAnswered(r);
    }),
    noun: "questions",
    key: function (r) {
      return r.key;
    },
    title: function (r) {
      return r.kind === "line" ? r.example : r.key;
    },
    sub: function (r) {
      return (
        (r.kind === "line" ? "one line, " + r.first : r.lines + " lines") +
        ", " +
        cents.format(Number(r.total) || 0) +
        (r.guess ? ", guess " + r.guess : "")
      );
    },
    staged: function (r) {
      return stagedText(r.kind === "line" ? "line|" + r.key : "payee|" + r.key.toUpperCase());
    },
    empty:
      "Choose a question on the left to answer it. A merchant is answered once for all its lines; a single line can also be split.",
    none: "No questions: every line has a category.",
    detail: function (r, panel, done) {
      if (r.kind === "line") {
        loadTransaction(r.key, panel, done);
        return;
      }
      facts(panel, [
        ["Lines", r.lines],
        ["Total", cents.format(Number(r.total) || 0)],
        ["Dates", r.first === r.last ? r.first : r.first + " to " + r.last],
        ["Accounts", r.accounts],
        ["Spelled", r.example],
        ["Guess", r.guess ? r.guess + " (" + r.guessed_by + ")" : ""],
      ]);
      if (r.note) panel.appendChild(el("p", r.note, "error"));
      var target = "payee|" + r.key.toUpperCase();
      var now = staged[target] ? staged[target].edit : null;
      var form = el("form", null, "edit");
      var key = field(
        form,
        "Merchant (shorten it to cover more spellings)",
        textInput(now ? (now.key as string) : r.key, "wide"),
      );
      var pick = field(form, "Category for all its lines", categoryChoice(now ? (now.category as string) : r.guess));
      var error = el("div", "", "error");
      var actions = el("div", null, "actions");
      actions.appendChild(
        button(
          "Keep answer",
          function () {
            if (!pick.value || !key.value.trim()) {
              error.textContent = "Give the merchant and a category.";
              return;
            }
            unstage(target);
            stage(
              { kind: "payee", key: key.value.trim(), category: pick.value },
              "every " + key.value.trim() + " line: " + pick.value,
            );
            done();
          },
          "filled",
        ),
      );
      if (now)
        actions.appendChild(
          button(
            "Undo",
            function () {
              unstage(target);
              done();
            },
            "text",
          ),
        );
      actions.appendChild(
        button(
          "Split into its lines",
          function () {
            form.remove();
            splitIntoLines(r, panel, done);
          },
          "text",
        ),
      );
      form.appendChild(actions);
      form.appendChild(error);
      panel.appendChild(form);
    },
  });
}

// A merchant question's lines, each with its own category: the answers are line edits, so the merchant
// stays unanswered and its later lines are asked again. With every line answered the question leaves
// the list (splits); with some left on (choose) it stays, for the rest.
function splitIntoLines(r: Row, panel: HTMLElement, done: () => void) {
  var box = el("div", "Loading its lines…", "support");
  box.id = "split-lines";
  panel.appendChild(box);
  var alone = editor!.review
    .filter(function (q) {
      return q.kind === "line";
    })
    .map(function (q) {
      return q.key;
    });
  call(
    "getReviewLines",
    [r.key],
    function (lines) {
      box.textContent = "";
      box.className = "";
      lines = lines.filter(function (l) {
        return alone.indexOf(l.id) < 0;
      });
      if (!lines.length) {
        box.appendChild(el("p", "No open lines for " + r.key + " in the latest compute.", "support"));
        return;
      }
      section(
        box,
        lines.length === 1 ? "1 line" : lines.length + " lines",
        "Give each line its own category; a line left on (choose) stays a question.",
      );
      var form = el("form", null, "edit");
      var table = el("table", null, "split-lines");
      var picks = lines.map(function (l) {
        var row = el("tr");
        cell(row, l.date);
        cell(row, l.payee);
        cell(row, l.account);
        cell(row, cents.format(l.amount), "num");
        var now = staged["line|" + l.id + "|category"];
        var pick = categoryChoice(now ? (now.edit.value as string) : r.guess || "");
        pick.setAttribute("aria-label", "Category for " + l.payee + " on " + l.date);
        row.appendChild(el("td")).appendChild(pick);
        table.appendChild(row);
        return [l, pick] as [ReviewLine, HTMLSelectElement];
      });
      form.appendChild(table);
      var error = el("div", "", "error");
      var actions = el("div", null, "actions");
      actions.appendChild(
        button(
          "Keep answers",
          function () {
            var chosen = picks.filter(function (p) {
              return p[1].value;
            });
            if (!chosen.length) {
              error.textContent = "Choose a category for at least one line.";
              return;
            }
            chosen.forEach(function (p) {
              var l = p[0];
              stage(
                { kind: "line", txn_id: l.id, field: "category", value: p[1].value },
                l.payee + " " + l.date + ": " + p[1].value,
              );
            });
            splits[r.key] = lines.map(function (l) {
              return l.id;
            });
            if (splitAnswered(r)) selected[editTab] = null; // its question is gone, so nothing stays open
            done();
          },
          "filled",
        ),
      );
      form.appendChild(actions);
      form.appendChild(error);
      box.appendChild(form);
    },
    "split-lines",
  );
}

function drawRules(body: HTMLElement) {
  masterDetail(body, {
    items: editor!.payeeRules,
    noun: "payee rules",
    key: function (r) {
      return r.pattern;
    },
    title: function (r) {
      return r.pattern;
    },
    sub: function (r) {
      return (r.category || "no guess") + (r.ask === "TRUE" ? ", asks each line" : "") + ", since " + r.effective;
    },
    section: function (r) {
      return r.status === "stopped" ? "Stopped" : "In force";
    },
    staged: function (r) {
      return stagedText("payee_rule|" + r.pattern);
    },
    empty: "Choose a payee rule to change or stop it.",
    add: [
      {
        label: "New payee rule",
        form: function (panel, done) {
          panel.appendChild(ruleForm(null, done));
        },
      },
    ],
    detail: function (r, panel, done) {
      facts(panel, [
        ["Category", r.category || "(none)"],
        ["Precedence", r.precedence],
        ["Since", r.effective],
        ["Asks each line", r.ask === "TRUE" ? "yes" : "no"],
        ["Status", r.status],
      ]);
      panel.appendChild(ruleForm(r, done));
    },
  });
}

function drawCategories(body: HTMLElement) {
  var items = editor!.categories
    .map(function (c) {
      return { kind: "category", row: c };
    })
    .concat(
      editor!.aliases.map(function (a) {
        return { kind: "alias", row: a };
      }),
    );
  masterDetail(body, {
    items: items,
    noun: "categories and old names",
    key: function (i) {
      return i.kind + "|" + (i.row.category && i.kind === "category" ? i.row.category : i.row.alias);
    },
    title: function (i) {
      return i.kind === "category" ? i.row.category : i.row.alias;
    },
    sub: function (i) {
      return i.kind === "category" ? [i.row.group, i.row.priority].filter(Boolean).join(", ") : "counts as " + i.row.category;
    },
    section: function (i) {
      return i.kind === "category" ? "Categories" : "Old names";
    },
    staged: function (i) {
      return stagedText(i.kind === "category" ? "category|" + i.row.category : "alias|" + i.row.alias);
    },
    empty: "Choose a category to regroup it, or an old name to point it elsewhere.",
    add: [
      {
        label: "New category",
        form: function (panel, done) {
          panel.appendChild(categoryForm(null, done));
        },
      },
      {
        label: "New old name",
        form: function (panel, done) {
          panel.appendChild(aliasForm(null, done));
        },
      },
    ],
    detail: function (i, panel, done) {
      if (i.kind === "category") {
        facts(panel, [
          ["Group", i.row.group],
          ["Priority", i.row.priority],
        ]);
        panel.appendChild(categoryForm(i.row, done));
      } else {
        facts(panel, [
          ["Counts as", i.row.category],
          ["Note", i.row.note],
        ]);
        panel.appendChild(aliasForm(i.row, done));
      }
    },
  });
}

function drawSettings(body: HTMLElement) {
  masterDetail(body, {
    items: editor!.parameters,
    noun: "settings",
    key: function (p) {
      return p.key;
    },
    title: function (p) {
      return p.key;
    },
    sub: function (p) {
      return p.source === "not set" ? "not set: " + p.about : p.value;
    },
    staged: function (p) {
      return stagedText("parameter|" + p.key);
    },
    empty:
      "Household settings, such as the account transfers come from, the links in the weekly email and " +
      "the FSA claim deadlines. Choose one to change it.",
    add: [
      {
        label: "New setting",
        form: function (panel, done) {
          panel.appendChild(parameterForm(null, done));
        },
      },
    ],
    detail: function (p, panel, done) {
      facts(
        panel,
        (
          [
            ["What it's for", p.about || ""],
            ["Value", p.source === "not set" ? "not set" : p.value],
            ["Source", p.source === "not set" ? "" : p.source],
          ] as Fact[]
        ).filter(function (f) {
          return f[1];
        }),
      );
      panel.appendChild(parameterForm(p, done));
    },
  });
}

function drawWithholding(body: HTMLElement) {
  masterDetail(body, {
    items: editor!.withholding,
    noun: "stubs unexplained",
    key: function (w) {
      return w.jurisdiction + "|" + w.stub_id;
    },
    title: function (w) {
      return w.pay_date + ", " + w.person;
    },
    sub: function (w) {
      return (
        (w.jurisdiction === "wi" ? "Wisconsin" : "Federal") +
        ", withheld " +
        cents.format(Number(w.withheld) || 0) +
        " of " +
        cents.format(Number(w.expected) || 0) +
        " expected"
      );
    },
    staged: function (w) {
      return stagedText("explanation|" + w.jurisdiction + "|" + w.stub_id);
    },
    empty: "Choose a stub to say why its withholding differs from the tables (an extra amount asked for, a bonus).",
    none: "Every stub's withholding is explained.",
    detail: function (w, panel, done) {
      facts(panel, [
        ["Employer", w.employer],
        ["Tax", w.jurisdiction === "wi" ? "Wisconsin" : "Federal"],
        ["Withheld", cents.format(Number(w.withheld) || 0)],
        ["Expected", cents.format(Number(w.expected) || 0)],
        ["Off by", cents.format(Number(w.residual) || 0)],
        ["The check says", w.explanation],
      ]);
      var target = "explanation|" + w.jurisdiction + "|" + w.stub_id;
      var form = el("form", null, "edit");
      var why = field(
        form,
        "Why",
        textInput(staged[target] ? (staged[target].edit.note as string) : "", "wide", "extra withholding asked for"),
      );
      var error = el("div", "", "error");
      var actions = el("div", null, "actions");
      actions.appendChild(
        button(
          "Keep explanation",
          function () {
            if (!why.value.trim()) {
              error.textContent = "Say why.";
              return;
            }
            stage(
              { kind: "explanation", jurisdiction: w.jurisdiction, stub_id: w.stub_id, note: why.value.trim() },
              w.jurisdiction + " withholding " + w.pay_date + " " + w.person + ": " + why.value.trim(),
            );
            done();
          },
          "filled",
        ),
      );
      form.appendChild(actions);
      form.appendChild(error);
      panel.appendChild(form);
    },
  });
}

// Jobs: the pay history in comp_events, with the runs of paychecks that disagree with it on top. A run that
// disagrees offers the row the paychecks suggest: the date its first period began, its pay on a yearly
// footing, the employer of the row in force.
// Merge one charge into another, so their charges are one history; or separate a key merged into this one.
function mergeForm(r: Row, items: Row[], done: () => void) {
  var box = el("div");
  section(
    box,
    "Same charge as another entry?",
    "Merge it into the entry it is part of: their charges become one history, under that entry's name.",
  );
  var form = el("form", null, "edit");
  box.appendChild(form);
  var others = items
    .map(function (i) {
      return i.key;
    })
    .filter(function (k) {
      return k !== r.key;
    });
  var into = field(form, "Merge into", choice([""].concat(others), "", { "": "(choose an entry)" }));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button("Merge", function () {
      if (!into.value) {
        error.textContent = "Choose the entry this one is part of.";
        return;
      }
      unstage("recurring_merge|" + r.key.toUpperCase());
      stage({ kind: "recurring_merge", key: r.key, same_as: into.value }, r.key + ": part of " + into.value);
      done();
    }),
  );
  (r.merged ? String(r.merged).split(", ") : []).forEach(function (k) {
    actions.appendChild(
      button(
        "Separate " + k,
        function () {
          unstage("recurring_merge|" + k.toUpperCase());
          stage({ kind: "recurring_merge", key: k, same_as: "" }, k + ": on its own again");
          done();
        },
        "text",
      ),
    );
  });
  form.appendChild(actions);
  form.appendChild(error);
  return box;
}

function subscriptionForm(r: Row | null, done: () => void) {
  var form = el("form", null, "edit");
  var key = r ? null : field(form, "Merchant, as review names it", textInput("", "wide", "APPLE ONE"));
  var note = field(form, "Note", textInput(r ? r.note : "", "wide"));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  var mark = function (yes: boolean) {
    var name = r ? r.key : key!.value.trim().toUpperCase();
    if (!name) {
      error.textContent = "Name the merchant as review does.";
      return;
    }
    var e: Edit = { kind: "subscription", key: name, subscription: yes ? "yes" : "no" };
    if (note.value.trim()) e.note = note.value.trim();
    unstage("subscription|" + name);
    stage(e, name + (yes ? ": track as recurring" : ": don't track"));
    done();
  };
  if (!r || r.marked !== "yes")
    actions.appendChild(
      button(
        "Track it",
        function () {
          mark(true);
        },
        "filled",
      ),
    );
  if (!r || r.marked !== "no")
    actions.appendChild(
      button(
        "Don't track",
        function () {
          mark(false);
        },
        r ? "" : "text",
      ),
    );
  if (r && r.marked === "yes")
    actions.appendChild(
      button(
        "Keep note",
        function () {
          mark(true);
        },
        "text",
      ),
    );
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

function drawDuplicates(body: HTMLElement) {
  masterDetail(body, {
    items: editor!.duplicates,
    noun: "possible duplicates",
    key: function (d) {
      return d.txn_a + "|" + d.txn_b;
    },
    title: function (d) {
      return d.payee_a;
    },
    sub: function (d) {
      return cents.format(Number(d.amount) || 0) + ", " + d.date_a + " and " + d.date_b + ", " + d.account_id;
    },
    staged: function (d) {
      return [
        stagedText("not_duplicate|" + [d.txn_a, d.txn_b].sort().join("|")),
        stagedText("line|" + d.txn_a + "|void"),
        stagedText("line|" + d.txn_b + "|void"),
      ]
        .filter(Boolean)
        .join("; ");
    },
    empty: "Choose a pair that looks like one charge recorded twice, then keep both or drop one.",
    none: "No possible duplicates.",
    detail: function (d, panel, done) {
      [
        ["First", d.txn_a, d.date_a, d.payee_a],
        ["Second", d.txn_b, d.date_b, d.payee_b],
      ].forEach(function (x) {
        var link = button(
          x[2] + " " + x[3],
          function () {
            openTransaction(x[1]);
          },
          "link",
        );
        facts(panel, [[x[0], link]]);
      });
      facts(panel, [
        ["Account", d.account_id],
        ["Amount", cents.format(Number(d.amount) || 0)],
      ]);
      var targets = [
        "not_duplicate|" + [d.txn_a, d.txn_b].sort().join("|"),
        "line|" + d.txn_a + "|void",
        "line|" + d.txn_b + "|void",
      ];
      function only(edit: Edit, text: string) {
        targets.forEach(unstage);
        stage(edit, text);
        done();
      }
      var actions = el("div", null, "actions");
      actions.appendChild(
        button(
          "Both are real",
          function () {
            only(
              { kind: "not_duplicate", txn_a: d.txn_a, txn_b: d.txn_b },
              d.payee_a + " " + d.date_a + " and " + d.date_b + ": both real",
            );
          },
          "filled",
        ),
      );
      actions.appendChild(
        button("Drop the first", function () {
          only(
            { kind: "line", txn_id: d.txn_a, field: "void", value: "TRUE" },
            d.payee_a + " " + d.date_a + ": dropped as a duplicate",
          );
        }),
      );
      actions.appendChild(
        button("Drop the second", function () {
          only(
            { kind: "line", txn_id: d.txn_b, field: "void", value: "TRUE" },
            d.payee_b + " " + d.date_b + ": dropped as a duplicate",
          );
        }),
      );
      panel.appendChild(actions);
    },
  });
}

function drawTransactionPage(body: HTMLElement) {
  var page = el("section", null, "panel txn-page");
  if (txnPage && txnPage.from && txnPage.from !== "edit") {
    var from = txnPage.from;
    page.appendChild(
      button(
        "Back to " + { budget: "Budget against actual", flows: "Cashflow", status: "Status" }[from],
        function () {
          setView(from);
        },
        "text",
      ),
    );
  }
  if (!txnPage) {
    page.appendChild(el("p", "Open a transaction from Budget against actual.", "support"));
    body.appendChild(page);
    return;
  }
  page.appendChild(el("h2", "Transaction", "md-heading"));
  body.appendChild(page);
  loadTransaction(txnPage.id, page, function () {
    redrawEditor();
  });
}
