// The editor's forms: budget lines, payee rules, categories, jobs, settings.

// Each editor part lives on the page it is about: review, payee rules and duplicates (and a transaction's
// own page) on Cashflow, beside the flow of money they decide, categories on Budget, withholding on Taxes, settings and saved changes on
// Status. Each page's submenu lists its own parts and its editor parts together; an "edit:" part opens the
// editor under that page's name.
var EDIT_HOME: Record<string, string> = {
  review: "flows",
  rules: "flows",
  duplicates: "flows",
  transaction: "flows",
  categories: "budget",
  withholding: "taxes",
  settings: "status",
  waiting: "status",
};
var PAGE_PARTS: Record<string, string[][]> = {
  flows: [
    ["sankey", "Sankey"],
    ["table", "Table"],
    ["edit:review", "Review"],
    ["edit:rules", "Payee rules"],
    ["edit:duplicates", "Duplicates"],
  ],
  status: [
    ["overview", "Overview"],
    ["log", "Engine log"],
    ["edit:settings", "Settings"],
    ["edit:waiting", "Saved changes"],
  ],
  budget: [
    ["lines", "Lines"],
    ["recurring", "Recurring"],
    ["edit:categories", "Categories"],
  ],
  taxes: [
    ["year", "This year"],
    ["history", "History"],
    ["8949", "Form 8949"],
    ["edit:withholding", "Withholding"],
  ],
};

// Open an editor part where it belongs (an older name for one is still understood).
function openEdit(tab: string) {
  if (!EDIT_HOME[tab]) tab = "review";
  editTab = tab;
  if (view !== "edit") setView("edit");
  else {
    drawEditTabs();
    redrawEditor();
  }
}

// Go to a page's own part from its submenu.
function openPart(page: string, part: string) {
  if (part.indexOf("edit:") === 0) {
    openEdit(part.slice(5));
    return;
  }
  if (page === "status") {
    setView("status");
    setStatusTab(part);
    return;
  }
  if (page === "budget") {
    budget.tab = part;
    budget.open = null;
    setView("budget");
    return;
  }
  if (page === "taxes") {
    taxView = part;
    setView("taxes");
    return;
  }
  if (page === "flows") {
    flowsTab = part;
    setView("flows");
    setFlowsTab(part);
    return;
  }
  setView(page);
}

// The editor's submenu: the parts of the page the open editor part belongs to.
function drawEditTabs() {
  var home = EDIT_HOME[editTab] || "flows";
  var bar = byId("edit-tabs");
  bar.textContent = "";
  bar.className = "subtabs";
  bar.setAttribute(
    "aria-label",
    ({ flows: "Cashflow", status: "Status", budget: "Budget", taxes: "Taxes" } as Record<string, string>)[home],
  );
  PAGE_PARTS[home].forEach(function (p) {
    var b = button(p[1], function () {
      openPart(home, p[0]);
    });
    b.setAttribute("aria-pressed", String(p[0] === "edit:" + editTab));
    b.dataset.tab = p[0];
    bar.appendChild(b);
  });
}

function setEditTab(tab: string) {
  editTab = EDIT_HOME[tab] ? tab : "review";
  drawEditTabs();
  redrawEditor();
}

function loadEditor() {
  if (editor) {
    redrawEditor();
    return;
  }
  byId("edit-body").textContent = "Loading…";
  call(
    "getEditor",
    [],
    function (data) {
      editor = data;
      redrawEditor();
    },
    "edit-body",
  );
}

function redrawEditor() {
  if (!editor || view !== "edit") return;
  var body = byId("edit-body");
  body.textContent = "";
  if (editTab === "waiting") {
    drawWaiting(body);
    return;
  }
  if (editTab === "transaction") {
    drawTransactionPage(body);
    return;
  }
  if (!editor.baseRun) {
    body.appendChild(el("div", "No compute run with editor tables yet: run ingest and compute once with this release.", "note"));
    return;
  }
  (
    ({
      review: drawReview,
      rules: drawRules,
      categories: drawCategories,
      settings: drawSettings,
      withholding: drawWithholding,
      duplicates: drawDuplicates,
    }) as Record<string, (body: HTMLElement) => void>
  )[editTab](body);
}

// The name a recurring charge shows by: one saved or waiting to be saved, else the one you gave it, else its key.
function chargeName(r: Row): string {
  var s = edited("recurring_name|" + String(r.key).toUpperCase());
  if (s) return (s.edit.name as string) || r.key;
  return r.name || r.key;
}

// A recurring charge's name, with a pencil that turns it into a box to rename it in place (Enter or
// Save stages the rename, Escape or Cancel puts the name back). The key stays below a name you gave.
function chargeTitle(r: Row, redraw: () => void, tag?: keyof HTMLElementTagNameMap) {
  var box = el("span", null, "charge-name");
  var shown = chargeName(r);
  box.appendChild(el(tag || "strong", shown));
  var pencil = button("", null, "icon-button");
  pencil.appendChild(icon("edit"));
  pencil.setAttribute("aria-label", "Rename " + shown);
  pencil.title = "Rename";
  box.appendChild(pencil);
  if (shown !== r.key) box.appendChild(el("span", r.key + " on the statement", "support key"));
  pencil.onclick = function (ev) {
    ev.stopPropagation();
    box.textContent = "";
    var input = textInput(shown === r.key ? "" : shown, "rename", r.key);
    input.maxLength = 60;
    input.setAttribute("aria-label", "Name for " + r.key);
    var save = function () {
      var name = input.value.trim().replace(/\s+/g, " ");
      var t = "recurring_name|" + String(r.key).toUpperCase();
      unstage(t);
      if (name === r.key) name = "";
      if (name !== (r.name || "")) {
        stage({ kind: "recurring_name", key: r.key, name: name }, r.key + (name ? ": shown as " + name : ": shown by its key"));
      }
      redraw();
    };
    input.onkeydown = function (e) {
      e.stopPropagation();
      if (e.key === "Enter") {
        e.preventDefault();
        save();
      }
      if (e.key === "Escape") {
        e.preventDefault();
        redraw();
      }
    };
    input.onclick = function (e) {
      e.stopPropagation();
    };
    box.appendChild(input);
    var ok = button(
      "Save",
      function (e) {
        e.stopPropagation();
        save();
      },
      "text",
    );
    var no = button(
      "Cancel",
      function (e) {
        e.stopPropagation();
        redraw();
      },
      "text",
    );
    box.appendChild(ok);
    box.appendChild(no);
    input.focus();
  };
  return box;
}

// What is staged for a setting (and, with a prefix ending in "|", for everything under it), in words.
function stagedText(t: string) {
  return stagedOrder
    .filter(function (k) {
      return k === t || k.indexOf(t + "|") === 0;
    })
    .map(function (k) {
      return staged[k].text;
    })
    .join("; ");
}

// What is saved for a setting (and everything under it) that no run has applied yet, in words; a setting
// with a newer edit waiting to be saved is left to stagedText.
function savedText(t: string) {
  return Object.keys(savedEdits)
    .filter(function (k) {
      return (k === t || k.indexOf(t + "|") === 0) && !staged[k];
    })
    .map(function (k) {
      return savedEdits[k].text;
    })
    .join("; ");
}

// Whether a setting has a change in hand, saved or not, in words: what marks it changed on the page.
function changedText(t: string) {
  return [stagedText(t), savedText(t)].filter(Boolean).join("; ");
}

// A setting's change in hand as a note: what waits to be saved, and what is saved and waits for a run.
function changeNote(t: string) {
  var waiting = stagedText(t),
    saved = savedText(t);
  return [waiting && "Waiting to be saved: " + waiting, saved && "Saved, applies at the next run: " + saved]
    .filter(Boolean)
    .join(" ");
}

// Every field an edit can change, as the page showed it (WAS_FIELDS in edits.ts and changes.py).
function budgetWas(l: Row) {
  return l.in_force === "scheduled only" ? null : rowWas(l);
}

function rowWas(l: Row): Row {
  return {
    effective: l.effective,
    category: l.category,
    account: l.account,
    priority: l.priority,
    rule: l.rule,
    amount: l.amount,
    per: l.per,
    note: l.note,
  };
}

// The scheduled amount in force on this line and date (editor_scheduled), which a new one replaces, or null.
function scheduledOn(line: string, date: string) {
  return (
    (editor!.scheduled || []).filter(function (x) {
      return x.line === line && x.effective.slice(0, 10) === date;
    })[0] || null
  );
}

// The budget line a recurring charge pays: one saved or waiting to be saved, else the one it is linked to ("" for none).
function chargeLine(key: string): string {
  var s = edited("recurring_line|" + key.toUpperCase());
  if (s) return (s.edit.line as string) || "";
  var r = (editor!.subscriptions || []).filter(function (x) {
    return x.key === key;
  })[0];
  return (r && r.line) || "";
}

// Links a recurring charge to the budget line it pays ("" unlinks it); a charge that pays a line is tracked.
function linkCharge(r: Row, line: string) {
  stage({ kind: "recurring_line", key: r.key, line: line }, r.key + (line ? ": pays budget line " + line : ": pays no line"));
  if (line && r.marked !== "yes" && !edited("subscription|" + r.key.toUpperCase()))
    stage({ kind: "subscription", key: r.key, subscription: "yes" }, r.key + ": track as recurring");
}

// A budget line's change from a date, its end, or a scheduled amount. A "recurring" line's target is the
// charges picked as the ones it pays (else its category's tracked charges) plus the amount, an allowance for
// the rest, shown added up as it is typed.
function budgetForm(given: Row | null, done: () => void, preset?: string) {
  var form = el("form", null, "edit");
  var isNew = !given;
  var l: Row = given || {
    line: preset || "",
    effective: "",
    category: preset || "",
    account: "",
    priority: "",
    rule: "fixed",
    amount: "",
    per: "monthly",
    note: "",
    in_force: "new",
  };
  var name = isNew ? field(form, "Line", textInput(preset || "", "", "groceries")) : null;
  var effective = field(form, "From", dateInput(firstOfMonth()));
  var rule = field(form, "Target from", choice(RULES, l.rule === "end" ? "fixed" : l.rule, RULE_NAMES));
  var amount = field(form, "Amount", textInput(l.amount, "amount"));
  var per = field(form, "Per", choice(PERS, l.per || "monthly"));
  var category = field(form, "Category", categoryChoice(l.category || l.spends_from));
  var charges = el("div", null, "recurring-sum");
  form.appendChild(charges);
  // the charges this line pays: picked here, or linked already (a recurring line counts these alone)
  var ongoing = (editor!.subscriptions || []).filter(function (r) {
    return r.status !== "stopped" && r.marked !== "no";
  });
  var paid: Record<string, boolean> = {};
  ongoing.forEach(function (r) {
    paid[r.key] = !isNew && chargeLine(r.key) === l.line;
  });
  var picked: Record<string, boolean> = Object.assign({}, paid);
  var showCharges = function () {
    var recurring = rule.value === "recurring";
    amount.parentNode!.firstChild!.textContent = recurring ? "Allowance for the rest (blank for none)" : "Amount";
    charges.textContent = "";
    charges.hidden = !recurring;
    if (!recurring) return;
    var anyPicked = ongoing.some(function (r) {
      return picked[r.key];
    });
    var mine = anyPicked
      ? ongoing.filter(function (r) {
          return picked[r.key];
        })
      : ongoing.filter(function (r) {
          return r.marked === "yes" && r.category === category.value && !chargeLine(r.key);
        });
    var total = 0;
    mine.forEach(function (r) {
      total += (Number(r.yearly) || 0) / 12;
    });
    var pick = el("fieldset", null, "charge-pick");
    pick.appendChild(el("legend", "Recurring charges this line pays"));
    ongoing
      .slice()
      .sort(function (a, b) {
        var rank = function (r: Row) {
          return picked[r.key] ? 0 : r.category === category.value ? 1 : 2;
        };
        return rank(a) - rank(b) || chargeName(a).localeCompare(chargeName(b));
      })
      .forEach(function (r) {
        var other = chargeLine(r.key);
        var box = el("input");
        box.type = "checkbox";
        box.checked = !!picked[r.key];
        box.addEventListener("change", function () {
          picked[r.key] = box.checked;
          showCharges();
        });
        var label = el("label", null, "sum-row");
        var name = el("span");
        name.appendChild(box);
        name.appendChild(
          document.createTextNode(" " + chargeName(r) + (other && other !== lineName() ? " (pays " + other + ")" : "")),
        );
        label.appendChild(name);
        label.appendChild(el("span", cents.format((Number(r.yearly) || 0) / 12) + " a month"));
        pick.appendChild(label);
      });
    if (!ongoing.length) pick.appendChild(el("div", "No recurring charges found yet.", "support"));
    charges.appendChild(pick);
    if (!anyPicked)
      charges.appendChild(
        el(
          "div",
          mine.length
            ? "None picked: the line counts the tracked charges in " + category.value + " that pay no other line."
            : "None picked, and no tracked charges in " + (category.value || "this category") + ": pick the ones it pays.",
          "support",
        ),
      );
    var allowance = isAmount(amount.value.trim()) ? Number(amount.value.replace(/,/g, "")) * (PER_MONTH[per.value] || 1) : 0;
    var a = el("div", null, "sum-row");
    a.appendChild(el("span", "Allowance for the rest"));
    a.appendChild(el("span", cents.format(allowance) + " a month"));
    charges.appendChild(a);
    var t = el("div", null, "sum-row total");
    t.appendChild(el("strong", "Monthly target"));
    t.appendChild(el("strong", cents.format(total + allowance)));
    charges.appendChild(t);
    charges.appendChild(el("div", "A charge's new price moves the target with it.", "support"));
  };
  [rule, per, category].forEach(function (c) {
    c.addEventListener("change", showCharges);
  });
  amount.addEventListener("input", showCharges);
  showCharges();
  var priority = field(form, "Priority", choice(PRIORITIES, l.priority));
  var account = field(form, "Paid from", textInput(l.account));
  var note = field(form, "Note", textInput(l.note, "wide"));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  function lineName() {
    return isNew ? name!.value.trim() : l.line;
  }
  actions.appendChild(
    button(isNew ? "Add line" : l.in_force === "ended" ? "Start again" : "Keep this change", function () {
      var e = {
        kind: "budget_line",
        line: lineName(),
        effective: effective.value,
        rule: rule.value,
        category: category.value,
        account: account.value.trim(),
        priority: priority.value,
        amount: amount.value.trim().replace(/,/g, ""),
        per: per.value,
        note: note.value.trim(),
        was: isNew ? null : budgetWas(l),
      };
      var wrong = !e.line
        ? "Name the line."
        : !isDate(e.effective)
          ? "Pick the date it starts."
          : !e.category
            ? "Pick a category."
            : e.amount && !isAmount(e.amount)
              ? "The amount is not a number."
              : !e.amount && (e.rule === "fixed" || e.rule === "billed" || e.rule === "sinking_fund")
                ? "A " + e.rule + " line needs an amount."
                : "";
      if (wrong) {
        error.textContent = wrong;
        return;
      }
      stage(
        e,
        "budget line " +
          e.line +
          ": " +
          e.rule +
          (e.amount ? " " + e.amount + " " + e.per : "") +
          " for " +
          e.category +
          " from " +
          e.effective,
      );
      if (e.rule === "recurring")
        ongoing.forEach(function (r) {
          if (picked[r.key] !== paid[r.key]) linkCharge(r, picked[r.key] ? e.line : "");
        });
      done();
    }),
  );
  if (!isNew && l.in_force === "yes") {
    actions.appendChild(
      button("End line", function () {
        if (!isDate(effective.value)) {
          error.textContent = "Pick the date it ends.";
          return;
        }
        stage(
          { kind: "budget_line", line: l.line, effective: effective.value, rule: "end", was: budgetWas(l) },
          "budget line " + l.line + ": ends " + effective.value,
        );
        done();
      }),
    );
  }
  form.appendChild(actions);
  if (!isNew) {
    var on = field(form, "Scheduled on", dateInput(""));
    var much = field(form, "Scheduled amount (0 cancels)", textInput("", "amount"));
    var add = el("div", null, "actions");
    add.appendChild(
      button("Add scheduled amount", function () {
        if (!isDate(on.value) || !isAmount(much.value.trim())) {
          error.textContent = "A scheduled amount needs a date and an amount.";
          return;
        }
        var before = scheduledOn(l.line, on.value); // a new amount on a date replaces the one there
        stage(
          {
            kind: "budget_line",
            line: l.line,
            effective: on.value,
            rule: "scheduled",
            category: l.category || l.spends_from,
            amount: much.value.trim().replace(/,/g, ""),
            was: before ? rowWas(before) : null,
          },
          "budget line " +
            l.line +
            ": " +
            much.value.trim() +
            " scheduled " +
            on.value +
            (before ? ", replacing " + before.amount : ""),
        );
        done();
      }),
    );
    form.appendChild(add);
  }
  form.appendChild(error);
  return form;
}

function ruleWas(r: Row) {
  return { category: r.category, precedence: r.precedence, effective: r.effective, ask: r.ask };
}

function ruleForm(given: Row | null, done: () => void) {
  var form = el("form", null, "edit");
  var isNew = !given;
  var r: Row = given || { pattern: "", category: "", precedence: "", effective: "", ask: "FALSE" };
  var pattern = isNew ? field(form, "Pattern (matches the payee)", textInput("", "wide", "ACME GROCER")) : null;
  var category = field(form, "Category", categoryChoice(r.category, isNew ? "(choose)" : "(none: only asks)"));
  var precedence = field(form, "Precedence (lower wins)", textInput(r.precedence, "amount"));
  var ask = document.createElement("input");
  ask.type = "checkbox";
  ask.checked = r.ask === "TRUE";
  field(form, "Ask about each line", ask);
  var effective = field(form, "From", dateInput(today()));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(isNew ? "Add rule" : "Keep this change", function () {
      var text = isNew ? pattern!.value.trim() : r.pattern;
      try {
        new RegExp(text);
      } catch (err) {
        error.textContent = "The pattern does not read: " + (err as Error).message;
        return;
      }
      var e = {
        kind: "payee_rule",
        pattern: text,
        category: category.value,
        effective: effective.value,
        precedence: precedence.value.trim(),
        ask: ask.checked ? "TRUE" : "FALSE",
        was: isNew ? null : ruleWas(r),
      };
      var wrong = !text
        ? "Write a pattern."
        : text.length > 200
          ? "Keep the pattern under 200 characters."
          : !isDate(e.effective)
            ? "Pick the date it starts."
            : e.precedence && !isAmount(e.precedence)
              ? "Precedence is a number."
              : !e.category && !ask.checked
                ? "Pick a category, or tick ask."
                : "";
      if (wrong) {
        error.textContent = wrong;
        return;
      }
      stage(
        e,
        "payee rule " +
          text +
          ": " +
          (e.category || "(no guess)") +
          (ask.checked ? ", asks each line" : "") +
          " from " +
          e.effective,
      );
      done();
    }),
  );
  if (!isNew && r.status !== "stopped") {
    actions.appendChild(
      button("Stop rule", function () {
        if (!isDate(effective.value)) {
          error.textContent = "Pick the date it stops.";
          return;
        }
        stage(
          {
            kind: "payee_rule",
            pattern: r.pattern,
            category: "",
            effective: effective.value,
            precedence: r.precedence,
            ask: "FALSE",
            was: ruleWas(r),
          },
          "payee rule " + r.pattern + ": stopped from " + effective.value,
        );
        done();
      }),
    );
  }
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

function categoryForm(given: Row | null, done: () => void) {
  var form = el("form", null, "edit");
  var isNew = !given;
  var c: Row = given || { category: "", group: "", priority: "" };
  var name = isNew ? field(form, "Category", textInput("", "", "lower_case")) : null;
  var group = field(form, "Group", textInput(c.group));
  var priority = field(form, "Priority", choice(PRIORITIES, c.priority));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(isNew ? "Add category" : "Keep this change", function () {
      var e = {
        kind: "category",
        category: isNew ? name!.value.trim() : c.category,
        group: group.value.trim(),
        priority: priority.value,
        was: isNew ? null : { group: c.group, priority: c.priority },
      };
      if (!/^[a-z][a-z0-9_]*$/.test(e.category)) {
        error.textContent = "Use lower case letters, digits and _.";
        return;
      }
      if (
        isNew &&
        editor!.categories.some(function (x) {
          return x.category === e.category;
        })
      ) {
        error.textContent = "That category exists.";
        return;
      }
      stage(e, "category " + e.category + ": group " + (e.group || "(none)") + ", priority " + (e.priority || "(none)"));
      done();
    }),
  );
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

function aliasForm(given: Row | null, done: () => void) {
  var form = el("form", null, "edit");
  var isNew = !given;
  var a: Row = given || { alias: "", category: "", note: "" };
  var name = isNew ? field(form, "Old name", textInput("")) : null;
  var category = field(form, "Counts as", categoryChoice(a.category));
  var note = field(form, "Note", textInput(a.note, "wide"));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(isNew ? "Add alias" : "Keep this change", function () {
      var e = {
        kind: "alias",
        alias: isNew ? name!.value.trim() : a.alias,
        category: category.value,
        note: note.value.trim(),
        was: isNew ? null : { category: a.category, note: a.note },
      };
      if (!e.alias || !e.category) {
        error.textContent = "An alias needs a name and a category.";
        return;
      }
      stage(e, "alias " + e.alias + " counts as " + e.category);
      done();
    }),
  );
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

// A pay change for comp_events: a raise, a new job or a change of hours. j holds what to start from (a row
// to correct, or what the paychecks suggest); a row for the same date, person and employer corrects it.
function jobForm(given: Row | null, done: () => void, label?: string) {
  var form = el("form", null, "edit");
  var j: Row = given || {};
  var people = Array.from(
    new Set(
      editor!.jobs.map(function (r) {
        return r.person;
      }),
    ),
  ).sort();
  var effective = field(form, "From", dateInput(j.effective || today()));
  var person = field(form, "Person", people.length ? choice(people, j.person || people[0]) : textInput(j.person || ""));
  var employer = field(form, "Employer", textInput(j.employer || "", "", "the name comp_events uses"));
  var salary = field(form, "Salary, full time, a year", textInput(j.salary || "", "", "70000"));
  var fte = field(form, "Share of full time", textInput(j.fte || "", "", "blank for full time, 0.6 for 60%"));
  var band = field(form, "Band", textInput(j.band || ""));
  var rating = field(form, "Rating", textInput(j.rating || ""));
  var note = field(form, "Note", textInput(j.note || "", "wide"));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(
      label || "Keep this change",
      function () {
        var e: { kind: string; [field: string]: string } = {
          kind: "job",
          effective: effective.value,
          person: person.value.trim(),
          employer: employer.value.trim(),
          salary: salary.value.replace(/,/g, "").trim(),
          fte: fte.value.trim(),
          band: band.value.trim(),
          rating: rating.value.trim(),
          note: note.value.trim(),
        };
        ["salary", "fte", "band", "rating", "note"].forEach(function (f) {
          if (!e[f]) delete e[f];
        });
        if (!isDate(e.effective)) {
          error.textContent = "Give the date the change took effect.";
          return;
        }
        if (!/^[a-z][a-z0-9_]*$/.test(e.person)) {
          error.textContent = "Use the lower case name the pay stubs use.";
          return;
        }
        if (!e.employer) {
          error.textContent = "Name the employer.";
          return;
        }
        if (e.salary && !isAmount(e.salary)) {
          error.textContent = "The salary is a number a year.";
          return;
        }
        if (e.fte && !(Number(e.fte) > 0 && Number(e.fte) <= 1.5)) {
          error.textContent = "Share of full time is like 0.6 for 60%.";
          return;
        }
        stage(
          e,
          "job: " +
            e.person +
            " at " +
            e.employer +
            (e.salary ? " at " + e.salary : "") +
            (e.fte ? ", " + e.fte + " FTE" : "") +
            " from " +
            e.effective,
        );
        done();
      },
      "filled",
    ),
  );
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

// Pay over time, one chart per person, from comp_events: pay a year (salary times share of full time) as a
// step line colored by employer (the same employer the same color in every chart), a marker at each change
// with a callout (the new employer, a change of hours, or the raise in percent), and the detail on hover.
