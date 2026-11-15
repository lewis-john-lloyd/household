// Where the money went, Search: every categorized line, found by any of its fields and sorted by any column.
// Each text box is a regular expression found anywhere in its field, ignoring case; ^ and $ anchor it
// (budgetview.ts compileSearch).
// What came from the bank (date, account, amount, its text, the line's id, the file) is shown as it is and never
// changes; a line opens on its own page to correct its category, split, note, one-off flag or recurring charge,
// and the lines found can be moved to a category or marked one-off together. Every change goes to the pending
// bar like any other, so it can be undone before Save. The lines load once a page life (getSearch).

var SEARCH_PAGE = 200; // rows drawn at a time
var searchData: SearchPage | null = null;
var searchState = {
  query: { text: {}, direction: "any", recurring: "any", oneOff: "any", split: "any", status: "any" } as SearchQuery,
  sort: "date",
  descending: true,
  shown: SEARCH_PAGE,
  groupBy: "", // a field the lines found are grouped by, "" for none
  opened: {} as Record<string, boolean>, // the groups opened to their lines, by grouping and key
  bulk: false, // the changes to all the lines found shown (Bulk edit)
};

var SEARCH_FIELDS: [SearchTextField, string, string][] = [
  ["payee", "Payee", "what the line is called"],
  ["raw", "Bank's text", "as downloaded"],
  ["category", "Category", "a split's parts count"],
  ["group", "Group", ""],
  ["account", "Account", ""],
  ["recurring", "Recurring charge", "its name or key"],
  ["key", "Merchant key", "what review asks by"],
  ["note", "Note", ""],
  ["items", "Items bought", "from an order history"],
  ["source", "Read from", "csv, ofx, pdf, hand"],
  ["decidedBy", "Decided by", "rule, you, a service"],
  ["id", "Line id", "txn_id"],
];
// every field the lines found can be sorted by
var SEARCH_SORTS: [string, string][] = [
  ["date", "Date"],
  ["amount", "Amount"],
  ["payee", "Payee"],
  ["raw", "Bank's text"],
  ["category", "Category"],
  ["group", "Group"],
  ["account", "Account"],
  ["recurring", "Recurring charge"],
  ["key", "Merchant key"],
  ["note", "Note"],
  ["oneOff", "One-off"],
  ["status", "Status"],
  ["decidedBy", "Decided by"],
  ["source", "Read from"],
  ["doc", "File"],
  ["id", "Line id"],
];
var SEARCH_MAIN = 6; // the first fields stand out; the rest wait under More fields
// what the lines found can be grouped by
var SEARCH_GROUPINGS: [string, string][] = [
  ["", "No groups"],
  ["category", "Category"],
  ["group", "Group"],
  ["payee", "Payee"],
  ["key", "Merchant key"],
  ["account", "Account"],
  ["recurring", "Recurring charge"],
  ["month", "Month"],
  ["year", "Year"],
  ["decidedBy", "Decided by"],
];

function loadSearch() {
  var box = byId("flows-search");
  if (searchData) {
    drawSearch();
    return;
  }
  box.textContent = "Loading…";
  call(
    "getSearch",
    [],
    function (d) {
      searchData = d;
      drawSearch();
    },
    "flows-search",
  );
}

// the category and one-off a line will have once its changes are saved
function searchCategory(l: SearchLine): { category: string; changed: boolean } {
  var e = edited(editTarget({ kind: "line", txn_id: l.id, field: "category" }));
  var v = e ? String((e.edit as Record<string, string>).value || "") : "";
  if (e && (e.edit as Record<string, string>).field === "split") return { category: "split", changed: true };
  return v ? { category: v, changed: v !== l.category } : { category: l.category, changed: false };
}

function searchOneOff(l: SearchLine): { oneOff: boolean; changed: boolean } {
  var e = edited(editTarget({ kind: "line", txn_id: l.id, field: "one_off" }));
  if (!e) return { oneOff: l.oneOff, changed: false };
  var yes = String((e.edit as Record<string, string>).value).toUpperCase() === "TRUE";
  return { oneOff: yes, changed: yes !== l.oneOff };
}

function drawSearch() {
  if (view !== "flows" || flowsTab !== "search" || !searchData) return;
  var box = byId("flows-search");
  var d = searchData;
  box.textContent = "";
  var filters = el("section", null, "panel search-filters");
  box.appendChild(filters);
  var results = el("section", null, "panel search-results");
  box.appendChild(results);
  var q = searchState.query;
  var timer: number | undefined;
  var later = function () {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(function () {
      searchState.shown = SEARCH_PAGE;
      drawResults();
    }, 150);
  };
  var errors: Record<string, HTMLElement> = {};

  filters.appendChild(el("h3", "Find lines"));
  var help = el("p", null, "support");
  [
    ["Each box is a pattern found anywhere in the field, ignoring case: ", ""],
    ["fuel", "code"],
    [" holds fuel, ", ""],
    ["^ACME", "code"],
    [" starts with ACME, ", ""],
    ["^food$", "code"],
    [" is exactly food, ", ""],
    ["food|petrol", "code"],
    [" is either. Dates take a year, a month or a day; amounts are dollars, in or out.", ""],
  ].forEach(function (p) {
    help.appendChild(p[1] ? el("code", p[0]) : document.createTextNode(p[0]));
  });
  filters.appendChild(help);
  var textBox = function (f: [SearchTextField, string, string], into: HTMLElement) {
    var label = el("label", null, "search-field");
    label.appendChild(el("span", f[1]));
    var input = textInput(q.text[f[0]] || "", "", f[2]);
    input.spellcheck = false;
    input.autocomplete = "off";
    input.setAttribute("data-field", f[0]);
    input.oninput = function () {
      q.text[f[0]] = input.value;
      later();
    };
    label.appendChild(input);
    var why = el("span", "", "search-error");
    errors[f[0]] = why;
    label.appendChild(why);
    into.appendChild(label);
  };
  var grid = el("div", null, "search-grid");
  filters.appendChild(grid);
  SEARCH_FIELDS.slice(0, SEARCH_MAIN).forEach(function (f) {
    textBox(f, grid);
  });
  var pair = function (label: string, a: HTMLInputElement, b: HTMLInputElement) {
    var l = el("div", null, "search-field search-pair");
    l.appendChild(el("span", label));
    var both = el("div", null, "search-both");
    both.appendChild(a);
    both.appendChild(el("span", "to", "support"));
    both.appendChild(b);
    l.appendChild(both);
    grid.appendChild(l);
  };
  var from = textInput(q.from || "", "", "YYYY-MM-DD"),
    to = textInput(q.to || "", "", "YYYY-MM-DD");
  from.setAttribute("aria-label", "From date");
  to.setAttribute("aria-label", "To date");
  from.oninput = function () {
    q.from = from.value.trim();
    later();
  };
  to.oninput = function () {
    q.to = to.value.trim();
    later();
  };
  pair("Date", from, to);
  var number = function (v: string) {
    var n = parseFloat(v.replace(/[$,]/g, ""));
    return isNaN(n) ? null : n;
  };
  var lo = textInput(q.min === null || q.min === undefined ? "" : String(q.min), "", "0"),
    hi = textInput(q.max === null || q.max === undefined ? "" : String(q.max), "", "any");
  lo.inputMode = hi.inputMode = "decimal";
  lo.setAttribute("aria-label", "Smallest amount, dollars");
  hi.setAttribute("aria-label", "Largest amount, dollars");
  lo.oninput = function () {
    q.min = number(lo.value);
    later();
  };
  hi.oninput = function () {
    q.max = number(hi.value);
    later();
  };
  pair("Amount, $", lo, hi);
  var pick = function (
    label: string,
    key: "direction" | "recurring" | "oneOff" | "split" | "status",
    names: Record<string, string>,
    into: HTMLElement,
  ) {
    var l = el("label", null, "search-field");
    l.appendChild(el("span", label));
    var s = choice(Object.keys(names), (q[key] as string) || "any", names);
    s.onchange = function () {
      (q as unknown as Record<string, unknown>)[key] = s.value;
      searchState.shown = SEARCH_PAGE;
      drawResults();
    };
    l.appendChild(s);
    into.appendChild(l);
  };
  pick("Money", "direction", { any: "In or out", out: "Out", in: "In" }, grid);
  pick("In a recurring charge", "recurring", { any: "Either", yes: "Yes", no: "No" }, grid);
  var more = el("details", null, "search-more");
  var opened =
    SEARCH_FIELDS.slice(SEARCH_MAIN).some(function (f) {
      return !!q.text[f[0]];
    }) ||
    [q.oneOff, q.split, q.status].some(function (v) {
      return v && v !== "any";
    });
  more.open = opened;
  more.appendChild(el("summary", "More fields"));
  var moreGrid = el("div", null, "search-grid");
  more.appendChild(moreGrid);
  SEARCH_FIELDS.slice(SEARCH_MAIN).forEach(function (f) {
    textBox(f, moreGrid);
  });
  pick("One-off", "oneOff", { any: "Either", yes: "Yes", no: "No" }, moreGrid);
  pick("Split", "split", { any: "Either", yes: "Yes", no: "No" }, moreGrid);
  pick("Status", "status", { any: "Any", decided: "Decided", open: "Open in review", waiting: "Waiting" }, moreGrid);
  filters.appendChild(more);
  filters.appendChild(
    button(
      "Clear",
      function () {
        searchState.query = { text: {}, direction: "any", recurring: "any", oneOff: "any", split: "any", status: "any" };
        searchState.shown = SEARCH_PAGE;
        drawSearch();
      },
      "text",
    ),
  );

  var drawResults = function () {
    results.textContent = "";
    var compiled = compileSearch(q, d.groups);
    Object.keys(errors).forEach(function (f) {
      var why = (compiled.errors as Record<string, string>)[f] || "";
      errors[f].textContent = why ? "Not a pattern: " + why : "";
      var input = errors[f].previousSibling as HTMLInputElement;
      input.classList.toggle("bad", !!why);
      input.setAttribute("aria-invalid", String(!!why));
    });
    var found = sortSearch(d.lines.filter(compiled.test), searchState.sort, searchState.descending, d.groups);
    var out = 0,
      inn = 0;
    found.forEach(function (l) {
      if (l.amount < 0) out -= l.amount;
      else inn += l.amount;
    });
    var head = el("div", null, "search-head");
    var total = el("p", null, "search-total");
    total.appendChild(el("span", "Total ", "label"));
    total.appendChild(el("strong", cents.format(inn - out), "num"));
    head.appendChild(total);
    head.appendChild(
      el(
        "p",
        found.length.toLocaleString("en-US") +
          " of " +
          d.lines.length.toLocaleString("en-US") +
          " lines: " +
          cents.format(out) +
          " out, " +
          cents.format(inn) +
          " in.",
        "search-count",
      ),
    );
    results.appendChild(head);
    if (!d.ascribed)
      results.appendChild(el("p", "Recurring charges and where each line was read from show after the next run.", "support"));
    if (found.length) {
      var tools = el("div", null, "search-tools");
      // any field sorts, the table's columns by their headings and the rest from here
      var sortNames: Record<string, string> = {};
      SEARCH_SORTS.forEach(function (f) {
        sortNames[f[0]] = "Sort by " + f[1].toLowerCase();
      });
      var sorter = choice(
        SEARCH_SORTS.map(function (f) {
          return f[0];
        }),
        searchState.sort,
        sortNames,
      );
      sorter.setAttribute("aria-label", "Sort by");
      sorter.onchange = function () {
        searchState.sort = sorter.value;
        searchState.descending = sorter.value === "date";
        drawResults();
      };
      tools.appendChild(sorter);
      var groupNames: Record<string, string> = {};
      SEARCH_GROUPINGS.forEach(function (g) {
        groupNames[g[0]] = g[0] ? "Group by " + g[1].toLowerCase() : g[1];
      });
      var grouper = choice(
        SEARCH_GROUPINGS.map(function (g) {
          return g[0];
        }),
        searchState.groupBy,
        groupNames,
      );
      grouper.setAttribute("aria-label", "Group by");
      grouper.onchange = function () {
        searchState.groupBy = grouper.value;
        drawResults();
      };
      tools.appendChild(grouper);
      // changing every line found at once is kept out of the way until asked for
      var bulkToggle = button(
        searchState.bulk ? "Close bulk edit" : "Bulk edit",
        function () {
          searchState.bulk = !searchState.bulk;
          drawResults();
        },
        "text",
      );
      bulkToggle.setAttribute("aria-expanded", String(searchState.bulk));
      tools.appendChild(bulkToggle);
      results.appendChild(tools);
      if (searchState.bulk) results.appendChild(searchBulk(found, d));
    }
    if (!found.length) {
      results.appendChild(el("p", "No line matches. Loosen a pattern, or Clear.", "support"));
      return;
    }
    var wrap = el("div", null, "scroll");
    var table = el("table", null, "w-table search-table");
    var cols: [string, string, string][] = [
      ["date", "Date", ""],
      ["account", "Account", ""],
      ["payee", "Payee", ""],
      ["amount", "Amount", "num"],
      ["category", "Category", ""],
      ["recurring", "Recurring", ""],
      ["note", "Note", ""],
    ];
    var tr = el("tr");
    cols.forEach(function (c) {
      var th = el("th", null, c[2]);
      var on = searchState.sort === c[0];
      th.setAttribute("aria-sort", on ? (searchState.descending ? "descending" : "ascending") : "none");
      var b = button(
        c[1] + (on ? (searchState.descending ? " ↓" : " ↑") : ""),
        function () {
          if (searchState.sort === c[0]) searchState.descending = !searchState.descending;
          else {
            searchState.sort = c[0];
            searchState.descending = c[0] === "date" || c[0] === "amount" ? c[0] === "date" : false;
          }
          drawResults();
        },
        "text sort",
      );
      th.appendChild(b);
      tr.appendChild(th);
    });
    table.appendChild(el("thead")).appendChild(tr);
    var body = el("tbody");
    var drawn = 0; // line rows drawn, against searchState.shown
    var lineRow = function (l: SearchLine) {
      drawn++;
      var row = el("tr", null, "search-row");
      var cat = searchCategory(l),
        once = searchOneOff(l);
      if (cat.changed || once.changed) row.classList.add("changed");
      row.tabIndex = 0;
      row.setAttribute("role", "link");
      row.setAttribute("aria-label", l.date + " " + l.payee + " " + cents.format(l.amount) + ": open the line");
      var open = function () {
        openTransaction(l.id);
      };
      row.onclick = open;
      row.onkeydown = function (ev) {
        if ((ev as KeyboardEvent).key === "Enter") open();
      };
      row.appendChild(el("td", l.date, "nowrap"));
      row.appendChild(el("td", l.account));
      var who = el("td");
      who.appendChild(el("div", l.payee));
      if (l.raw && l.raw !== l.payee) who.appendChild(el("div", l.raw, "support small"));
      row.appendChild(who);
      row.appendChild(el("td", cents.format(l.amount), "num" + (l.amount > 0 ? " in" : "")));
      var c = el("td");
      c.appendChild(el("div", cat.category === "split" || (!cat.changed && l.split) ? "split" : pretty(cat.category)));
      var tags: string[] = [];
      if (d.groups[cat.category]) tags.push(pretty(d.groups[cat.category]));
      if (once.oneOff) tags.push("one-off");
      if (l.decidedBy && !cat.changed) tags.push("by " + l.decidedBy); // why it has this category
      if (l.status === "open") tags.push("open in review");
      if (l.status === "waiting") tags.push("waiting");
      if (cat.changed || once.changed) tags.push("changed, not saved");
      if (tags.length) c.appendChild(el("div", tags.join(", "), "support small"));
      row.appendChild(c);
      var rec = el("td");
      if (l.recurring) {
        rec.appendChild(el("div", l.recurringName));
        if (!l.recurringCounts || once.oneOff) rec.appendChild(el("div", "listed, not counted", "support small"));
      }
      row.appendChild(rec);
      row.appendChild(el("td", l.note, "search-note"));
      body.appendChild(row);
    };
    // a row of totals: a group's head, or the whole search's foot; the amount sits under Amount
    var sumRow = function (label: string, lines: number, net: number, cls: string) {
      var row = el("tr", null, cls);
      var name = el("td");
      name.colSpan = 3;
      name.appendChild(el("span", label));
      name.appendChild(el("span", " " + lines.toLocaleString("en-US") + (lines === 1 ? " line" : " lines"), "support small"));
      row.appendChild(name);
      row.appendChild(el("td", cents.format(net), "num" + (net > 0 ? " in" : "")));
      row.appendChild(el("td")).colSpan = 3;
      return { row: row, name: name };
    };
    var left = 0; // lines not drawn, for Show more
    if (searchState.groupBy) {
      var by = searchState.groupBy;
      var grouped = groupSearch(found, by, d.groups, by === searchState.sort && !searchState.descending);
      grouped.forEach(function (g) {
        var id = by + "|" + g.key;
        var opened = !!searchState.opened[id];
        var label = !g.key ? "(none)" : by === "category" || by === "group" ? pretty(g.key) : g.key;
        var head = sumRow(label, g.lines.length, g.net, "search-group");
        var toggle = function () {
          searchState.opened[id] = !opened;
          drawResults();
          results.querySelectorAll<HTMLElement>("tr.search-group").forEach(function (n) {
            if (n.dataset.fold === id) n.focus({ preventScroll: true }); // back on the row used
          });
        };
        head.row.dataset.fold = id;
        head.row.tabIndex = 0;
        head.row.setAttribute("role", "button");
        head.row.setAttribute("aria-expanded", String(opened));
        head.row.setAttribute(
          "aria-label",
          label + ", " + g.lines.length + " lines, " + cents.format(g.net) + ": " + (opened ? "close" : "open"),
        );
        var chev = el("span", null, "chev" + (opened ? " open" : ""));
        chev.appendChild(icon("chevron"));
        head.name.insertBefore(chev, head.name.firstChild);
        head.row.onclick = toggle;
        head.row.onkeydown = function (ev) {
          var k = (ev as KeyboardEvent).key;
          if (k === "Enter" || k === " ") {
            ev.preventDefault();
            toggle();
          }
        };
        body.appendChild(head.row);
        if (!opened) return;
        g.lines.forEach(function (l) {
          if (drawn < searchState.shown) lineRow(l);
          else left++;
        });
      });
    } else
      found.forEach(function (l) {
        if (drawn < searchState.shown) lineRow(l);
        else left++;
      });
    table.appendChild(body);
    if (found.length > 1 || searchState.groupBy)
      table.appendChild(el("tfoot")).appendChild(sumRow("Total", found.length, inn - out, "search-sum").row);
    wrap.appendChild(table);
    results.appendChild(wrap);
    if (left)
      results.appendChild(
        button(
          "Show " + Math.min(SEARCH_PAGE, left) + " more",
          function () {
            searchState.shown += SEARCH_PAGE;
            drawResults();
          },
          "tonal",
        ),
      );
  };
  drawResults();
}

// what the lines found can have done to them together: a category, or one-off either way, each line its own
// change in the pending bar (a split line keeps its split: open it to change that)
function searchBulk(found: SearchLine[], d: SearchPage) {
  var bar = el("div", null, "search-bulk");
  var whole = found.filter(function (l) {
    return !l.split;
  });
  var many = found.length > MAX_CHANGE_EDITS;
  var names: Record<string, string> = { "": "Move these lines to…" };
  d.categories.forEach(function (c) {
    names[c] = pretty(c);
  });
  var to = choice([""].concat(d.categories), "", names);
  to.setAttribute("aria-label", "Category for the lines found");
  bar.appendChild(to);
  var said = el("span", "", "support");
  var moveButton = bar.appendChild(
    button(
      "Move " + whole.length.toLocaleString("en-US") + (whole.length === 1 ? " line" : " lines"),
      function () {
        if (!to.value) {
          said.textContent = "Pick a category first.";
          return;
        }
        if (many) {
          said.textContent = "At most " + MAX_CHANGE_EDITS + " at once: narrow the search.";
          return;
        }
        var moved = 0;
        searchUndo = [];
        whole.forEach(function (l) {
          if (l.category === to.value && !edited(editTarget({ kind: "line", txn_id: l.id, field: "category" }))) return;
          rememberStaged(editTarget({ kind: "line", txn_id: l.id, field: "category" }));
          stage(
            { kind: "line", txn_id: l.id, field: "category", value: to.value },
            l.date + " " + l.payee + " " + cents.format(l.amount) + ": " + to.value,
          );
          moved++;
        });
        drawSearch();
        flash(
          moved +
            " moved to " +
            pretty(to.value) +
            (found.length > whole.length ? "; split lines left as they are" : "") +
            ". Not saved yet.",
        );
      },
      "tonal",
    ),
  );
  if (many) {
    (moveButton as HTMLButtonElement).disabled = true;
    said.textContent =
      "Narrow the search to " + MAX_CHANGE_EDITS.toLocaleString("en-US") + " lines or fewer to change them together.";
  }
  var mark = function (yes: boolean) {
    return button(
      yes ? "Mark one-off" : "Not one-off",
      function () {
        if (many) {
          said.textContent = "At most " + MAX_CHANGE_EDITS + " at once: narrow the search.";
          return;
        }
        var n = 0;
        searchUndo = [];
        found.forEach(function (l) {
          var now = searchOneOff(l).oneOff;
          if (now === yes) return;
          rememberStaged(editTarget({ kind: "line", txn_id: l.id, field: "one_off" }));
          stage(
            { kind: "line", txn_id: l.id, field: "one_off", value: yes ? "TRUE" : "FALSE" },
            l.date + " " + l.payee + ": " + (yes ? "one-off" : "not one-off"),
          );
          n++;
        });
        drawSearch();
        flash(n + (yes ? " marked one-off" : " no longer one-off") + ". Not saved yet.");
      },
      "text",
    );
  };
  var marks = [mark(true), mark(false)];
  marks.forEach(function (b) {
    (b as HTMLButtonElement).disabled = many;
    bar.appendChild(b);
  });
  bar.appendChild(said);
  return bar;
}

// what the last change to the lines found staged, and what each of those lines had staged before it, so the
// whole change is undone in one step (each is also its own entry in the pending bar)
var searchUndo: { target: string; before: StagedEdit | null }[] = [];

function rememberStaged(target: string) {
  searchUndo.push({ target: target, before: staged[target] || null });
}

// a short word on what just happened, under the results' count, with its undo
function flash(words: string) {
  var head = document.querySelector("#flows-search .search-head");
  if (!head) return;
  var p = el("p", words + " ", "search-flash");
  p.setAttribute("role", "status");
  if (searchUndo.length)
    p.appendChild(
      button(
        "Undo",
        function () {
          searchUndo.forEach(function (u) {
            unstage(u.target);
            if (u.before) stage(u.before.edit, u.before.text);
          });
          searchUndo = [];
          drawSearch();
        },
        "text",
      ),
    );
  head.appendChild(p);
}
