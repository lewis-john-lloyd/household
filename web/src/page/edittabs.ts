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
      return edited("line|" + id + "|category");
    })
  );
}

// --- Review -----------------------------------------------------------------------------------------
// Answering is meant to go quickly: the model's guess is already chosen, Enter keeps the answer and opens the
// next question that has none, and a single line is answered from what the question itself says, with no
// wait for the server. The last answer kept can be taken back from the question that follows it.
var reviewFocus = false; // the next draw puts the cursor on the answer: set when an answer moved on to a question
var reviewKept: { targets: string[]; words: string; key: string } | null = null; // the last answer, for its undo

function reviewTarget(r: Row) {
  return r.kind === "line" ? "line|" + r.key + "|category" : "payee|" + r.key.toUpperCase();
}

function reviewAnswered(r: Row) {
  return !!edited(reviewTarget(r)) || (r.kind !== "line" && splitAnswered(r));
}

function reviewOpen() {
  return editor!.review.filter(function (r) {
    return r.kind === "line" || !splitAnswered(r);
  });
}

/** The question to answer after this one: the next in the list with no answer, from the top again if need be. */
function reviewNext(key: string): string | null {
  var open = reviewOpen();
  var at = open.findIndex(function (r) {
    return r.key === key;
  });
  for (var i = 1; i <= open.length; i++) {
    var r = open[(at + i) % open.length];
    if (r.key !== key && !reviewAnswered(r)) return r.key;
  }
  return null;
}

function reviewMoveOn(key: string, done: () => void) {
  selected[editTab] = reviewNext(key);
  reviewFocus = true;
  done();
}

/** A single line's text without what a store's order history added after it ("| maybe: ..."). */
function reviewText(r: Row) {
  return String(r.example || "")
    .split(" | ")[0]
    .trim();
}

// Lines asked about one by one that read the same but for their numbers (a payment app's lines differ only
// in a reference), from the same account: offered beside the line being answered, to be given its answer too.
function reviewShape(r: Row) {
  return (
    reviewText(r)
      .replace(/[\d*#]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .toUpperCase() +
    "|" +
    r.accounts
  );
}

function reviewAlike(r: Row): Row[] {
  var shape = reviewShape(r);
  return editor!.review.filter(function (q) {
    return q.kind === "line" && q.key !== r.key && !reviewAnswered(q) && reviewShape(q) === shape;
  });
}

// The answer's control: the usual list of categories, the guess chosen. Typing a category's first letters
// picks it, Enter keeps the answer and Shift+Enter passes to the next question.
function reviewPick(form: HTMLFormElement, label: string, value: string, skip: () => void) {
  var pick = field(form, label, categoryChoice(value));
  pick.onkeydown = function (e) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (e.shiftKey) skip();
    else form.requestSubmit();
  };
  if (reviewFocus) {
    reviewFocus = false;
    setTimeout(function () {
      pick.focus();
    }, 0);
  }
  return pick;
}

function reviewActions(form: HTMLFormElement, r: Row, done: () => void, more: HTMLElement[]) {
  var target = reviewTarget(r);
  var actions = el("div", null, "actions");
  var keep = el("button", "Keep", "filled");
  keep.type = "submit";
  actions.appendChild(keep);
  if (reviewNext(r.key))
    actions.appendChild(
      button(
        "Answer later",
        function () {
          reviewMoveOn(r.key, done); // nothing kept: the question stays in the list
        },
        "text",
      ),
    );
  if (edited(target))
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
  more.forEach(function (b) {
    actions.appendChild(b);
  });
  form.appendChild(actions);
  form.appendChild(
    el(
      "p",
      "Enter keeps the answer and opens the next question; Shift+Enter leaves this one in the list for later.",
      "support keys",
    ),
  );
  return keep;
}

// The answer just kept, with its undo, in the list's head beside the count, so it reads as the queue's last
// answer and not as part of the question now open. On a phone the list is hidden under the open question, so a
// copy says the same at the question's top, named as the previous one (CSS shows one or the other).
function reviewLast(panel: HTMLElement, done: () => void, where: "list" | "phone" = "list") {
  var kept = reviewKept;
  if (
    !kept ||
    !kept.targets.every(function (t) {
      return !!staged[t];
    })
  )
    return;
  var p = el("p", (where === "phone" ? "Previous question kept: " : "Kept: ") + kept.words + ". ", "review-kept " + where);
  p.setAttribute("role", "status");
  p.appendChild(
    button(
      "Undo",
      function () {
        kept!.targets.forEach(unstage);
        selected[editTab] = kept!.key;
        reviewKept = null;
        reviewFocus = true;
        done();
      },
      "text",
    ),
  );
  if (where === "list") panel.insertBefore(p, panel.querySelector(".md-filter"));
  else panel.appendChild(p);
}

function reviewKeep(r: Row, edits: { edit: Edit; text: string }[], words: string, done: () => void) {
  var targets = edits.map(function (e) {
    return stage(e.edit, e.text);
  });
  reviewKept = { targets: targets, words: words, key: r.key };
  reviewMoveOn(r.key, done);
}

function drawReview(body: HTMLElement) {
  var open = reviewOpen();
  var answered = open.filter(reviewAnswered).length;
  var advanced = reviewFocus; // an answer or a skip brought this draw: on a phone the next question stays open
  masterDetail(body, {
    items: open,
    noun: "questions" + (answered ? ", " + answered.toLocaleString() + " answered" : ""),
    open: advanced,
    key: function (r) {
      return r.key;
    },
    title: function (r) {
      return r.kind === "line" ? r.example : r.key;
    },
    sub: function (r) {
      return (
        (r.kind === "line" ? "one line, " + r.first : r.lines === "1" ? "1 line" : r.lines + " lines") +
        ", " +
        cents.format(Number(r.total) || 0) +
        (r.guess ? ", guess " + r.guess : "")
      );
    },
    section: function (r) {
      return r.kind === "line" ? "Single lines" : "Merchants";
    },
    staged: function (r) {
      return changeNote(reviewTarget(r));
    },
    empty:
      "Choose a question on the left to answer it. A merchant is answered once for all its lines; a single line can also be split. " +
      "Each answer opens the next question, so Enter after Enter goes through the list.",
    none: "No questions: every line has a category.",
    detail: function (r, panel, done) {
      reviewLast(panel, done, "phone");
      if (r.kind === "line") reviewLine(r, panel, done);
      else reviewMerchant(r, panel, done);
    },
  });
  var head = body.querySelector<HTMLElement>(".md-head");
  if (head) reviewLast(head, redrawEditor);
  if (advanced && !selected[editTab] && open.length) {
    // the last question was answered: say so where the next one would have been
    var panel = body.querySelector<HTMLElement>(".md-detail");
    var wrap = body.querySelector<HTMLElement>(".md");
    if (panel && wrap) {
      panel.textContent = "";
      panel.appendChild(
        button(
          "Back to the list",
          function () {
            wrap!.classList.remove("showing");
          },
          "text md-back",
        ),
      );
      panel.appendChild(el("h2", "Every question has an answer", "md-heading"));
      reviewLast(panel, redrawEditor, "phone");
      panel.appendChild(
        el("p", "Save the changes below; the next run of the engine applies them and asks about whatever is new.", "support"),
      );
      wrap.classList.add("showing"); // on a phone, said where the next question would have been
    }
    reviewFocus = false;
  }
}

function reviewMerchant(r: Row, panel: HTMLElement, done: () => void) {
  facts(panel, [
    ["Lines", r.lines],
    ["Total", cents.format(Number(r.total) || 0)],
    ["Dates", r.first === r.last ? r.first : r.first + " to " + r.last],
    ["Accounts", r.accounts],
    ["Spelled", r.example],
    ["Guess", r.guess ? r.guess + " (" + r.guessed_by + ")" : ""],
  ]);
  if (r.note) panel.appendChild(el("p", r.note, "error"));
  var target = reviewTarget(r);
  var now = edited(target) ? edited(target)!.edit : null;
  var form = el("form", null, "edit");
  var key = field(form, "Merchant (shorten it to cover more spellings)", textInput(now ? (now.key as string) : r.key, "wide"));
  var pick = reviewPick(form, "Category", now ? (now.category as string) : r.guess, function () {
    reviewMoveOn(r.key, done);
  });
  // whom the answer is for: the merchant, now and later, or only the lines asked about now
  var scope = el("fieldset", null, "review-scope");
  scope.appendChild(el("legend", "This answer is for"));
  var n = Number(r.lines) || 0;
  var scopes: [string, string][] = [
    ["merchant", "Every " + r.key + " line, now and later"],
    ["lines", (n === 1 ? "Only this line" : "Only these " + n + " lines") + "; the next one from it is asked about again"],
    ["ask", (n === 1 ? "This line" : "These " + n + " lines") + ", and ask about each of its lines from now on"],
  ];
  var radios = scopes.map(function (s, i) {
    var label = el("label");
    var radio = el("input") as HTMLInputElement;
    radio.type = "radio";
    radio.name = "review-scope";
    radio.value = s[0];
    radio.checked = i === 0;
    radio.onchange = function () {
      key.disabled = s[0] !== "merchant"; // a merchant's spelling matters only to an answer for the merchant
    };
    label.appendChild(radio);
    label.appendChild(document.createTextNode(" " + s[1]));
    scope.appendChild(label);
    return radio;
  });
  form.appendChild(scope);
  var error = el("div", "", "error");
  form.onsubmit = function (e) {
    e.preventDefault();
    if (!radios[0].checked) {
      if (!pick.value) {
        error.textContent = "Choose a category.";
        return;
      }
      reviewOnlyLines(r, pick.value, error, done, radios[2].checked);
      return;
    }
    if (!pick.value || !key.value.trim()) {
      error.textContent = "Give the merchant and a category.";
      return;
    }
    unstage(target);
    var words = "every " + key.value.trim() + " line: " + pretty(pick.value);
    reviewKeep(r, [{ edit: { kind: "payee", key: key.value.trim(), category: pick.value }, text: words }], words, done);
  };
  reviewActions(form, r, done, [
    button(
      "Split into its lines",
      function () {
        form.remove();
        splitIntoLines(r, panel, done);
      },
      "text",
    ),
  ]);
  form.appendChild(error);
  panel.appendChild(form);
}

// One line, answered from what its question says. Lines that read the same can be ticked to take the same
// answer; the whole line (its split, note and flags) opens below when asked for.
function reviewLine(r: Row, panel: HTMLElement, done: () => void) {
  facts(panel, [
    ["Date", r.first],
    ["Amount", cents.format(Number(r.total) || 0)],
    ["Account", r.accounts],
    ["Bank's text", r.example],
    ["Guess", r.guess ? r.guess + (r.confidence ? " (" + Math.round(Number(r.confidence) * 100) + "% sure)" : "") : ""],
    ["Asked by itself", /^model$|^$/.test(r.guessed_by) ? "" : r.guessed_by],
  ]);
  if (r.note) panel.appendChild(el("p", r.note, "error"));
  var target = reviewTarget(r);
  var now = edited(target) ? edited(target)!.edit : null;
  var form = el("form", null, "edit");
  var pick = reviewPick(form, "Category", now ? (now.value as string) : r.guess, function () {
    reviewMoveOn(r.key, done);
  });
  var alike = reviewAlike(r);
  var ticks: [Row, HTMLInputElement][] = [];
  var error = el("div", "", "error");
  var keep: HTMLButtonElement;
  function count() {
    var n = ticks.filter(function (t) {
      return t[1].checked;
    }).length;
    keep.textContent = n ? "Keep for " + (n + 1) + " lines" : "Keep";
  }
  if (alike.length) {
    var same = el("fieldset", null, "review-alike");
    same.appendChild(
      el("legend", alike.length === 1 ? "1 more line reads the same" : alike.length + " more lines read the same"),
    );
    same.appendChild(
      el("p", "From the same account, differing only in their numbers. Tick the ones this answer is also for.", "support"),
    );
    var quick = el("div", null, "actions");
    var tick = function (which: (q: Row) => boolean) {
      return function () {
        ticks.forEach(function (t) {
          t[1].checked = which(t[0]);
        });
        count();
      };
    };
    var guessed = alike.filter(function (q) {
      return q.guess === r.guess;
    });
    quick.appendChild(
      button(
        "All " + alike.length,
        tick(function () {
          return true;
        }),
        "small",
      ),
    );
    if (r.guess && guessed.length && guessed.length < alike.length)
      quick.appendChild(
        button(
          "The " + guessed.length + " guessed " + r.guess,
          tick(function (q) {
            return q.guess === r.guess;
          }),
          "small",
        ),
      );
    quick.appendChild(
      button(
        "None",
        tick(function () {
          return false;
        }),
        "small",
      ),
    );
    same.appendChild(quick);
    var list = el("ul", null, "review-alike-list");
    alike.forEach(function (q) {
      var box = document.createElement("input");
      box.type = "checkbox";
      box.onchange = count;
      var l = el("label");
      l.appendChild(box);
      l.appendChild(
        document.createTextNode(
          " " + q.first + ", " + cents.format(Number(q.total) || 0) + (q.guess ? ", guess " + q.guess : ""),
        ),
      );
      list.appendChild(el("li")).appendChild(l);
      ticks.push([q, box]);
    });
    same.appendChild(list);
    form.appendChild(same);
  }
  form.onsubmit = function (e) {
    e.preventDefault();
    if (!pick.value) {
      error.textContent = "Choose a category.";
      return;
    }
    var rows = [r].concat(
      ticks
        .filter(function (t) {
          return t[1].checked;
        })
        .map(function (t) {
          return t[0];
        }),
    );
    var edits = rows.map(function (q) {
      return {
        edit: { kind: "line", txn_id: q.key, field: "category", value: pick.value } as Edit,
        text: reviewText(q) + " " + q.first + ": " + pretty(pick.value),
      };
    });
    var words =
      rows.length > 1
        ? rows.length + " lines reading " + reviewText(r) + ": " + pretty(pick.value)
        : reviewText(r) + " " + r.first + ": " + pretty(pick.value);
    reviewKeep(r, edits, words, done);
  };
  keep = reviewActions(form, r, done, [
    button(
      "Open the whole line",
      function () {
        form.remove();
        loadTransaction(r.key, panel, done);
      },
      "text",
    ),
  ]);
  form.appendChild(error);
  panel.appendChild(form);
}

// A merchant question's lines, each with its own category: the answers are line edits, so the merchant
// stays unanswered and its later lines are asked again. With every line answered the question leaves
// the list (splits); with some left on (choose) it stays, for the rest.
// A merchant's open lines, less those asked about one by one (they are questions of their own).
function reviewLinesOf(r: Row, then: (lines: ReviewLine[]) => void, failed: (why: string) => void) {
  var alone = editor!.review
    .filter(function (q) {
      return q.kind === "line";
    })
    .map(function (q) {
      return q.key;
    });
  ask(
    "getReviewLines",
    [r.key],
    function (lines) {
      then(
        lines.filter(function (l) {
          return alone.indexOf(l.id) < 0;
        }),
      );
    },
    function (e) {
      failed(String((e && e.message) || e));
    },
  );
}

// A payee rule's pattern for a merchant's key: its words in order, whatever stands between them in the bank's
// text ("AV KL RF" finds "PAYPAL *AV-KL-RF-GL"), as whole words.
function reviewKeyPattern(key: string) {
  return (
    "\\b" +
    key
      .trim()
      .split(/\s+/)
      .map(function (w) {
        return w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      })
      .join("\\W*") +
    "\\b"
  );
}

// A merchant's answer for its lines asked about now, and not for the merchant: each line gets the category as
// a line's own correction, so the merchant's next line is asked about again. With ask, a payee rule that asks
// about each of the merchant's lines from today, the category its guess, so it is never answered for the
// merchant as a whole.
function reviewOnlyLines(r: Row, category: string, error: HTMLElement, done: () => void, ask?: boolean) {
  error.textContent = "Getting its lines…";
  reviewLinesOf(
    r,
    function (lines) {
      if (!lines.length) {
        error.textContent = "No open lines for " + r.key + " in the latest compute.";
        return;
      }
      error.textContent = "";
      var next = reviewNext(r.key); // before its lines are answered and the question leaves the list
      unstage(reviewTarget(r));
      var targets = lines.map(function (l) {
        return stage(
          { kind: "line", txn_id: l.id, field: "category", value: category },
          l.payee + " " + l.date + ": " + pretty(category),
        );
      });
      splits[r.key] = lines.map(function (l) {
        return l.id;
      });
      var which = lines.length === 1 ? "the " + r.key + " line" : "the " + lines.length + " " + r.key + " lines";
      var words = which + " only: " + pretty(category);
      if (ask) {
        var pattern = reviewKeyPattern(r.key);
        var held = editor!.payeeRules.filter(function (p) {
          return p.pattern === pattern;
        })[0];
        targets.push(
          stage(
            {
              kind: "payee_rule",
              pattern: pattern,
              category: category,
              effective: today(),
              precedence: held ? held.precedence : "",
              ask: "TRUE",
              was: held ? ruleWas(held) : null,
            },
            "payee rule " + pattern + ": asks about each line, guessing " + pretty(category),
          ),
        );
        words = which + ": " + pretty(category) + ", and each of its lines asked about from now on";
      }
      reviewKept = { targets: targets, words: words, key: r.key };
      selected[editTab] = next;
      reviewFocus = true;
      done();
    },
    function (why) {
      error.textContent = "Could not get its lines: " + why;
    },
  );
}

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
        var now = edited("line|" + l.id + "|category");
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
          "Keep",
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
                l.payee + " " + l.date + ": " + pretty(p[1].value),
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
      return changeNote("payee_rule|" + r.pattern);
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
      return changeNote(i.kind === "category" ? "category|" + i.row.category : "alias|" + i.row.alias);
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
      return changeNote("parameter|" + p.key);
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
            ["Source", p.source === "not set" ? "" : sourceWords(p.source)],
          ] as Fact[]
        ).filter(function (f) {
          return f[1];
        }),
      );
      panel.appendChild(parameterForm(p, done));
      var history = settingHistory(p.key);
      if (history) panel.appendChild(history);
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
      return changeNote("explanation|" + w.jurisdiction + "|" + w.stub_id);
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
        textInput(edited(target) ? (edited(target)!.edit.note as string) : "", "wide", "extra withholding asked for"),
      );
      var error = el("div", "", "error");
      var actions = el("div", null, "actions");
      actions.appendChild(
        button(
          "Keep",
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
        changeNote("not_duplicate|" + [d.txn_a, d.txn_b].sort().join("|")),
        changeNote("line|" + d.txn_a + "|void"),
        changeNote("line|" + d.txn_b + "|void"),
      ]
        .filter(Boolean)
        .join(" ");
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
        "Back to " +
          ((txnPage.part === "search" ? "Search" : txnPage.part === "overtime" ? "Over time" : "") ||
            (
              { budget: "Budget", flows: "Cashflow", status: "Status", home: "Home", taxes: "Taxes", equity: "Equity" } as Record<
                string,
                string
              >
            )[from] ||
            "where you were"),
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
