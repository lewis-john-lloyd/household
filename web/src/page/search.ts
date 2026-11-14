// Where the money went, Search: every categorized line, found by any of its fields and sorted by any column.
// Each text box is a regular expression the whole field must match, ignoring case (budgetview.ts compileSearch).
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
var SEARCH_MAIN = 6; // the first fields stand out; the rest wait under More fields

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
    ["Each box is a pattern the whole field must match, ignoring case: ", ""],
    ["ACME.*", "code"],
    [" starts with ACME, ", ""],
    [".*fuel.*", "code"],
    [" holds it anywhere, ", ""],
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
    if (found.length) results.appendChild(searchBulk(found, d));
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
    found.slice(0, searchState.shown).forEach(function (l) {
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
    });
    table.appendChild(body);
    wrap.appendChild(table);
    results.appendChild(wrap);
    if (found.length > searchState.shown)
      results.appendChild(
        button(
          "Show " + Math.min(SEARCH_PAGE, found.length - searchState.shown) + " more",
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
        whole.forEach(function (l) {
          if (l.category === to.value && !edited(editTarget({ kind: "line", txn_id: l.id, field: "category" }))) return;
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
        found.forEach(function (l) {
          var now = searchOneOff(l).oneOff;
          if (now === yes) return;
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

// a short word on what just happened, under the results' count
function flash(words: string) {
  var head = document.querySelector("#flows-search .search-head");
  if (!head) return;
  var p = el("p", words, "search-flash");
  p.setAttribute("role", "status");
  head.appendChild(p);
}
