// One transaction's page: its facts, split and items.

// --- one transaction -------------------------------------------------------------------------------
function openTransaction(id: string) {
  txnPage = { id: id, from: view };
  openEdit("transaction");
}

function loadTransaction(id: string, panel: HTMLElement, done: () => void) {
  var where = el("p", "Loading…", "support");
  where.id = "txn-" + Math.random().toString(36).slice(2);
  panel.appendChild(where);
  call(
    "getTransaction",
    [id],
    function (t) {
      where.remove();
      if (!t) {
        panel.appendChild(el("p", "No transaction " + id + " in the last compute.", "error"));
        return;
      }
      transactionEditor(panel, t, done);
    },
    where.id,
  );
}

function amountText(cents: number) {
  return (cents / 100).toFixed(2);
}

// Category, split, note and flags for one line, each kept as its own change.
function transactionEditor(panel: HTMLElement, t: Line, done: () => void) {
  var said = t.date + " " + t.payee + " " + cents.format(t.amount);
  var id = el("code", t.id);
  id.title = "the line's txn_id";
  facts(panel, [
    ["Date", t.date],
    ["Account", t.account],
    ["Amount", cents.format(t.amount)],
    ["Category", t.parts.length ? "split" : t.category],
    ["Decided by", t.decidedBy],
    ["Bank's name", t.payeeRaw !== t.payee ? t.payeeRaw : ""],
    ["Note", t.note],
    ["Transaction", id],
  ]);
  // each item's category as it stands, a change not yet saved included; the split below follows them
  var itemCats = t.items.map(function (i) {
    var s = i.id && edited("item|" + i.id);
    return s ? (s.edit.category as string) : i.category;
  });
  var splitter: { fromItems(): void } | null = null;
  if (t.items.length) {
    var personSplit = /^you, split/.test(t.decidedBy);
    var bought = section(
      panel,
      "What it bought",
      "From the store's order history or receipt. Change an item's category " +
        "and the split below follows; the category is kept with the item, so this line splits by it once ingest and compute run." +
        (personSplit ? " This line has a split you set, which wins over its items: keep the split below to replace it." : ""),
    );
    var list = el("ul", null, "bought");
    t.items.forEach(function (i, n) {
      var li = el("li");
      li.appendChild(el("span", i.name));
      if (i.id) {
        var pick = categoryChoice(itemCats[n], "(category)");
        pick.setAttribute("aria-label", "Category for " + i.name);
        pick.onchange = function () {
          if (!pick.value) {
            pick.value = itemCats[n];
            return;
          }
          itemCats[n] = pick.value;
          if (pick.value === i.category) unstage("item|" + i.id);
          else stage({ kind: "item", item_id: i.id, category: pick.value }, i.name + ": " + pick.value);
          li.classList.toggle("changed", pick.value !== i.category);
          if (splitter) splitter.fromItems();
        };
        li.classList.toggle("changed", itemCats[n] !== i.category);
        li.appendChild(pick);
      } else {
        li.appendChild(el("span", i.category, "support"));
      }
      li.appendChild(el("span", cents.format(i.amount), "n"));
      list.appendChild(li);
    });
    bought.appendChild(list);
  }
  var pending = stagedText("line|" + t.id),
    kept = savedText("line|" + t.id);
  if (pending) panel.appendChild(el("p", "Not saved yet: " + pending, "staged-banner"));
  if (kept) panel.appendChild(el("p", "Saved, applies at the next run: " + kept, "staged-banner saved"));

  var one = section(panel, "One category");
  var oneForm = el("form", null, "edit");
  var category = field(oneForm, "Category", categoryChoice(t.parts.length ? "" : t.category));
  var merchant = document.createElement("input");
  merchant.type = "checkbox";
  if (t.key) field(oneForm, "For every line from " + t.key, merchant);
  var oneActions = el("div", null, "actions");
  var oneError = el("div", "", "error");
  oneActions.appendChild(
    button(
      "Keep category",
      function () {
        if (!category.value) {
          oneError.textContent = "Pick a category.";
          return;
        }
        if (merchant.checked && t.key)
          stage({ kind: "payee", key: t.key, category: category.value }, "every " + t.key + " line: " + category.value);
        else stage({ kind: "line", txn_id: t.id, field: "category", value: category.value }, said + ": " + category.value);
        done();
      },
      "filled",
    ),
  );
  oneForm.appendChild(oneActions);
  oneForm.appendChild(oneError);
  one.appendChild(oneForm);

  var split = section(
    panel,
    "Split",
    "Give each part a category and an amount. One part may leave its amount blank to take what's left.",
  );
  splitter = splitEditor(split, t, said, done, function () {
    return itemCats;
  });
  if (
    itemCats.some(function (c, n) {
      return c !== t.items[n].category;
    })
  )
    splitter!.fromItems();

  var more = section(panel, "Note and flags");
  var moreForm = el("form", null, "edit");
  var note = field(moreForm, "Note", textInput(t.note, "wide"));
  var oneOff = document.createElement("input");
  oneOff.type = "checkbox";
  oneOff.checked = t.oneOff;
  field(moreForm, "One-off (left out of averages)", oneOff);
  var moreActions = el("div", null, "actions");
  moreActions.appendChild(
    button("Keep note and flags", function () {
      if (note.value.trim() !== t.note) {
        stage(
          { kind: "line", txn_id: t.id, field: "note", value: note.value.trim() },
          said + ": note " + (note.value.trim() || "(cleared)"),
        );
      }
      if (oneOff.checked !== t.oneOff) {
        stage(
          { kind: "line", txn_id: t.id, field: "one_off", value: oneOff.checked ? "TRUE" : "FALSE" },
          said + ": " + (oneOff.checked ? "one-off" : "not a one-off"),
        );
      }
      done();
    }),
  );
  moreActions.appendChild(
    button(
      "Drop this line (a duplicate)",
      function () {
        stage({ kind: "line", txn_id: t.id, field: "void", value: "TRUE" }, said + ": dropped");
        done();
      },
      "text danger",
    ),
  );
  moreForm.appendChild(moreActions);
  more.appendChild(moreForm);
}

// Rows of category and amount, adding up live against the line.
function splitEditor(box: HTMLElement, t: Line, said: string, done: () => void, itemCategories?: () => string[]) {
  var total = Math.round(Math.abs(t.amount) * 100);
  var current = edited("line|" + t.id + "|category");
  var start =
    current && current.edit.field === "split"
      ? (current.edit.value as string).split("; ").map(function (p) {
          var m = /^(\S+)(?: (\S+))?$/.exec(p) || [];
          return [m[1] || "", m[2] || ""];
        })
      : t.parts.length
        ? t.parts.map(function (p) {
            return [p[0], amountText(Math.round(Math.abs(p[1]) * 100))];
          })
        : [
            [t.category, ""],
            ["", ""],
          ];
  var table = el("div", null, "split-rows");
  var status = el("p", "", "split-status");
  var rows: { pick: HTMLSelectElement; much: HTMLInputElement }[] = [];
  function addRow(category: string, amount: string) {
    var r = el("div", null, "split-row");
    var pick = categoryChoice(category, "(category)");
    pick.setAttribute("aria-label", "Category");
    var much = textInput(amount, "amount", "the rest");
    much.setAttribute("aria-label", "Amount");
    var remove = button(
      "Remove",
      function () {
        rows.splice(rows.indexOf(entry), 1);
        r.remove();
        check();
      },
      "text",
    );
    var entry = { pick: pick, much: much };
    pick.onchange = check;
    much.oninput = check;
    r.appendChild(pick);
    r.appendChild(much);
    r.appendChild(remove);
    table.appendChild(r);
    rows.push(entry);
  }
  function parts() {
    return rows.map(function (r) {
      var text = r.much.value.trim().replace(/,/g, "").replace(/^-/, "");
      return { category: r.pick.value, cents: text === "" ? null : isAmount(text) ? Math.round(Number(text) * 100) : NaN };
    });
  }
  function check() {
    var p = parts(),
      wrong = "";
    var given = p.reduce(function (a, x) {
      return a + (x.cents || 0);
    }, 0);
    var blanks = p.filter(function (x) {
      return x.cents === null;
    }).length;
    var names = p.map(function (x) {
      return x.category;
    });
    if (p.length < 2) wrong = "A split needs two parts or more.";
    else if (names.indexOf("") >= 0) wrong = "Give every part a category.";
    else if (
      names.some(function (n) {
        return !known(n);
      })
    )
      wrong =
        names.filter(function (n) {
          return !known(n);
        })[0] + " is not a category.";
    else if (
      names.some(function (n, i) {
        return names.indexOf(n) !== i;
      })
    )
      wrong = "Each category can be one part only.";
    else if (
      p.some(function (x) {
        return isNaN(x.cents as number);
      })
    )
      wrong = "An amount is not a number.";
    else if (
      p.some(function (x) {
        return x.cents === 0;
      })
    )
      wrong = "A part can't be zero.";
    else if (blanks > 1) wrong = "Only one part can leave its amount blank.";
    else if (blanks === 1 && given >= total)
      wrong = "The parts come to " + money2(given) + ", leaving nothing for the blank one.";
    else if (blanks === 0 && given !== total)
      wrong =
        "The parts come to " +
        money2(given) +
        " of " +
        money2(total) +
        (given < total
          ? "; " + money2(total - given) + " is left to place."
          : "; that's " + money2(given - total) + " too much.");
    status.className = "split-status" + (wrong ? " error" : " ok");
    status.textContent =
      wrong ||
      "The parts come to the line's " + money2(total) + (blanks ? ", with " + money2(total - given) + " in the blank one." : ".");
    keep.disabled = !!wrong;
    return wrong
      ? null
      : p
          .map(function (x) {
            return x.category + (x.cents === null ? "" : " " + amountText(x.cents));
          })
          .join("; ");
  }
  function money2(c: number) {
    return cents.format(c / 100);
  }
  function known(n: string) {
    return (editor ? editor.categories : []).some(function (c) {
      return c.category === n;
    });
  }
  start.forEach(function (s) {
    addRow(s[0], s[1]);
  });
  box.appendChild(table);
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(
      "Add a part",
      function () {
        addRow("", "");
        check();
      },
      "text",
    ),
  );
  // the parts the items' categories make, biggest first; the last takes what's left (tax, shipping, rounding)
  function fromItems() {
    var cats = itemCategories
      ? itemCategories()
      : t.items.map(function (i) {
          return i.category;
        });
    var byCategory: Record<string, number> = {};
    t.items.forEach(function (i, n) {
      byCategory[cats[n]] = (byCategory[cats[n]] || 0) + Math.round(Math.abs(i.amount) * 100);
    });
    var names = Object.keys(byCategory)
      .filter(Boolean)
      .sort(function (a, b) {
        return byCategory[b] - byCategory[a];
      });
    table.textContent = "";
    rows.length = 0;
    names.forEach(function (c, n) {
      addRow(c, n === names.length - 1 && names.length > 1 ? "" : amountText(byCategory[c]));
    });
    check();
  }
  if (t.items.length) actions.appendChild(button("Split by what it bought", fromItems, "text"));
  var keep = button(
    "Keep split",
    function () {
      var value = check();
      if (!value) return;
      stage({ kind: "line", txn_id: t.id, field: "split", value: value }, said + ": split " + value);
      done();
    },
    "filled",
  );
  actions.appendChild(keep);
  box.appendChild(status);
  box.appendChild(actions);
  check();
  return { fromItems: fromItems };
}
