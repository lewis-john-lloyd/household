// Where the money went, Search: every categorized line, found by any of its fields and sorted by any column.
// One search bar: the filters in force are chips in it, and typing offers fields (to filter or to group by),
// values the lines hold and any text (budgetview.ts searchSuggest); a pattern is a regular expression found
// anywhere in its field, ignoring case, ^ and $ anchoring it (compileSearch). The dates stand apart: a length and
// the day it ends (searchRange), 90 days ending today to start. A card beside the lines holds each filter, to
// read and change in place, and the grouping.
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
  range: { unit: "days", days: 90, end: "today" } as SearchRange, // the dates searched: 90 days ending today
  draft: "", // a field chosen in the search bar, waiting for its value
  typed: "", // what is typed in the search bar
  cardOpen: null as boolean | null, // the Filters card open (shut on a phone until asked for)
};

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
// A drawer's lines found again in Search, where they can be grouped, totaled and changed together: the category
// or group they share, over the drawer's dates (a whole month, quarter or year named as one).
function searchLink(field: "category" | "group", value: string, from: string, to: string) {
  return button(
    "Find these lines in Search",
    function () {
      closeDrawer();
      var now = today();
      var end = to >= now ? "today" : to;
      var range: SearchRange = {
        unit: "days",
        days: Math.round((Date.parse(minDay(to, now)) - Date.parse(from)) / DAY_MS) + 1,
        end: end,
      };
      (["month", "quarter", "year"] as const).forEach(function (u) {
        if (range.unit === "days" && from === periodStart(from, u) && to === periodEnd(from, u))
          range = { unit: u, days: 90, end: end };
      });
      searchState.query = { text: {}, direction: "any", recurring: "any", oneOff: "any", split: "any", status: "any" };
      searchState.query.text[field] = "^" + searchEscape(value) + "$";
      searchState.range = range;
      searchState.groupBy = "";
      searchState.draft = "";
      searchState.typed = "";
      searchState.shown = SEARCH_PAGE;
      openPart("flows", "search");
    },
    "text",
  );
}

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

// The words a filter is shown in, on its chip and in the card: [what, value].
function searchFilters(q: SearchQuery): { field: string; label: string; value: string }[] {
  var out: { field: string; label: string; value: string }[] = [];
  if (q.any) out.push({ field: "any", label: "Any text", value: q.any });
  SEARCH_BAR_FIELDS.forEach(function (f) {
    if (f.kind === "text") {
      var v = q.text[f.key as SearchTextField];
      if (v) out.push({ field: f.key, label: f.label, value: v });
    } else if (f.kind === "amount") {
      var lo = q.min,
        hi = q.max;
      var has = function (n: number | null | undefined) {
        return n !== null && n !== undefined && !isNaN(n);
      };
      if (has(lo) || has(hi))
        out.push({
          field: "amount",
          label: "Amount",
          value: has(lo) && has(hi) ? (lo === hi ? String(lo) : lo + " to " + hi) : has(lo) ? "at least " + lo : "at most " + hi,
        });
    } else if (f.kind === "choice") {
      var c = searchChoice(q, f.key);
      if (c && c !== "any")
        out.push({
          field: f.key,
          label: f.label,
          value:
            (f.choices || []).filter(function (x) {
              return x[0] === c;
            })[0]?.[1] || c,
        });
    }
  });
  return out;
}

// the query's key for a yes-or-no or choice field of the search bar
var SEARCH_CHOICE_KEY: Record<string, "direction" | "recurring" | "oneOff" | "split" | "status"> = {
  direction: "direction",
  inRecurring: "recurring",
  oneOff: "oneOff",
  split: "split",
  status: "status",
};

function searchChoice(q: SearchQuery, field: string): string {
  var k = SEARCH_CHOICE_KEY[field];
  return k ? String(q[k] || "any") : "";
}

/** Take a filter off the query. */
function searchDrop(q: SearchQuery, field: string) {
  if (field === "any") delete q.any;
  else if (field === "amount") q.min = q.max = null;
  else if (SEARCH_CHOICE_KEY[field]) (q as unknown as Record<string, string>)[SEARCH_CHOICE_KEY[field]] = "any";
  else delete q.text[field as SearchTextField];
}

function drawSearch() {
  if (view !== "flows" || flowsTab !== "search" || !searchData) return;
  var box = byId("flows-search");
  var d = searchData;
  box.textContent = "";
  var q = searchState.query;
  var timer: number | undefined;
  var later = function () {
    if (timer) window.clearTimeout(timer);
    timer = window.setTimeout(function () {
      searchState.shown = SEARCH_PAGE;
      drawResults();
    }, 150);
  };
  var now = today();
  var applyRange = function () {
    var r = searchRange(searchState.range, now);
    q.from = r.from;
    q.to = r.to;
  };
  applyRange();

  // the dates, apart from the filters: a length and the day it ends, stepped back and on by the length
  var top = el("div", null, "search-top");
  box.appendChild(top);
  rangeChip(top, searchState.range, now, { days: [30, 90, 365], ownDays: true, units: true, ends: true }, function (r) {
    searchState.range = r;
    applyRange();
    searchState.shown = SEARCH_PAGE;
    drawResults();
  });

  // the query bar: the filters in force as chips, and a box that offers fields, values and grouping as you type
  var label = function (field: string) {
    return field === "any" ? "Any text" : (searchBarField(field) || { label: field }).label;
  };
  var GROUPABLE = SEARCH_BAR_FIELDS.filter(function (f) {
    return f.group;
  });
  var queries = queryBar(top, {
    menuId: "search-menu",
    ariaLabel: "Search lines",
    empty: "Search: a word, or a field such as category",
    fields: SEARCH_BAR_FIELDS,
    named: ["category", "group"],
    state: searchState,
    suggest: function (text, draft) {
      var inRange = d.lines.filter(function (l) {
        return l.date >= (q.from || "") && l.date <= (q.to || "9999");
      });
      return searchSuggest(text, draft, inRange, d.groups);
    },
    chips: function () {
      var shown: BarChip[] = searchFilters(q).map(function (f) {
        return {
          text: f.label,
          value: f.value,
          cls: "",
          remove: function () {
            searchDrop(q, f.field);
            refilter();
          },
          focus: function () {
            var at = card.querySelector<HTMLElement>(
              "[data-filter='" + f.field + "'] input, [data-filter='" + f.field + "'] select",
            );
            if (at) {
              cardBox.open = true;
              at.focus();
              at.scrollIntoView({ block: "nearest" });
            }
          },
        };
      });
      if (searchState.groupBy)
        shown.push({
          text: "Grouped by " + label(searchState.groupBy).toLowerCase(),
          value: "",
          cls: "group",
          remove: function () {
            searchState.groupBy = "";
            refilter();
          },
          focus: function () {
            cardBox.open = true;
            (card.querySelector("select[aria-label='Group by']") as HTMLElement).focus();
          },
        });
      return shown;
    },
    choose: function (o, group) {
      if (o.kind === "group" || (o.kind === "field" && group)) searchState.groupBy = o.field;
      else if (o.kind === "any") q.any = o.value;
      else if (o.kind === "pattern" || o.kind === "value") q.text[o.field as SearchTextField] = o.value;
      else if (o.kind === "amount") {
        q.min = o.min === undefined ? null : o.min;
        q.max = o.max === undefined ? null : o.max;
      } else if (o.kind === "choice") (q as unknown as Record<string, string>)[SEARCH_CHOICE_KEY[o.field]] = o.value;
      refilter();
    },
    dropLast: function () {
      var all = searchFilters(q);
      if (searchState.groupBy) searchState.groupBy = "";
      else if (all.length) searchDrop(q, all[all.length - 1].field);
      else return false;
      refilter();
      return true;
    },
  });
  var input = queries.input,
    drawChips = queries.drawChips,
    drawMenu = queries.drawMenu;

  // the filters in force, each to read and change in place, and the grouping
  var layout = el("div", null, "search-layout");
  box.appendChild(layout);
  var results = el("section", null, "panel search-results");
  layout.appendChild(results);
  var cardBox = el("details", null, "panel search-card");
  if (searchState.cardOpen === null) searchState.cardOpen = window.innerWidth > 800;
  cardBox.open = searchState.cardOpen;
  cardBox.ontoggle = function () {
    searchState.cardOpen = cardBox.open;
  };
  layout.appendChild(cardBox);
  var cardHead = el("summary");
  cardBox.appendChild(cardHead);
  var card = el("div", null, "search-card-body");
  cardBox.appendChild(card);
  var errors: Record<string, HTMLElement> = {};
  var drawCard = function () {
    var n = searchFilters(q).length + (searchState.groupBy ? 1 : 0);
    cardHead.textContent = n ? "Filters and grouping (" + n + ")" : "Filters and grouping";
    card.textContent = "";
    errors = {};
    var filters = searchFilters(q);
    if (!filters.length) empty(card, "No filters: every line in these dates is shown. Type in the search box to add one.");
    var row = function (f: { field: string; label: string }, control: HTMLElement, sub?: string) {
      var r = el("div", null, "frow");
      r.setAttribute("data-filter", f.field);
      var head = el("div", null, "head");
      head.appendChild(el("span", f.label, "flabel"));
      var x = button(
        "",
        function () {
          searchDrop(q, f.field);
          refilter();
        },
        "x",
      );
      x.appendChild(icon("close"));
      x.setAttribute("aria-label", "Remove the " + f.label + " filter");
      head.appendChild(x);
      r.appendChild(head);
      r.appendChild(control);
      if (sub) r.appendChild(el("span", sub, "support small"));
      card.appendChild(r);
      return r;
    };
    filters.forEach(function (f) {
      var bf = searchBarField(f.field);
      if (f.field === "any" || (bf && bf.kind === "text")) {
        var pat = textInput(f.field === "any" ? q.any : q.text[f.field as SearchTextField], "mono");
        pat.spellcheck = false;
        pat.autocomplete = "off";
        pat.setAttribute("data-field", f.field);
        pat.setAttribute("aria-label", f.label + " pattern");
        pat.oninput = function () {
          if (f.field === "any") q.any = pat.value;
          else q.text[f.field as SearchTextField] = pat.value;
          drawChips();
          later();
        };
        var r = row(f, pat, f.field === "any" ? "Found in the payee, bank's text, note, items bought or category" : bf!.hint);
        var why = el("span", "", "search-error");
        errors[f.field] = why;
        r.appendChild(why);
      } else if (bf && bf.kind === "amount") {
        var pair = el("div", null, "pair");
        var num = function (v: number | null | undefined, name: string, set: (n: number | null) => void) {
          var i = textInput(v === null || v === undefined ? "" : String(v), "mono", name === "Smallest" ? "0" : "any");
          i.inputMode = "decimal";
          i.setAttribute("aria-label", name + " amount, dollars");
          i.oninput = function () {
            var n = parseFloat(i.value.replace(/[$,]/g, ""));
            set(isNaN(n) ? null : n);
            drawChips();
            later();
          };
          return i;
        };
        pair.appendChild(
          num(q.min, "Smallest", function (n) {
            q.min = n;
          }),
        );
        pair.appendChild(el("span", "to", "support"));
        pair.appendChild(
          num(q.max, "Largest", function (n) {
            q.max = n;
          }),
        );
        row(f, pair, "Dollars, in or out");
      } else if (bf && bf.kind === "choice") {
        var names: Record<string, string> = {};
        (bf.choices || []).forEach(function (c) {
          names[c[0]] = c[1];
        });
        var sel = choice(Object.keys(names), searchChoice(q, bf.key), names);
        sel.setAttribute("aria-label", f.label);
        sel.onchange = function () {
          (q as unknown as Record<string, string>)[SEARCH_CHOICE_KEY[bf!.key]] = sel.value;
          drawChips();
          searchState.shown = SEARCH_PAGE;
          drawResults();
        };
        row(f, sel);
      }
    });
    var groupNames: Record<string, string> = { "": "No groups" };
    GROUPABLE.forEach(function (g) {
      groupNames[g.key] = g.label;
    });
    var grouper = choice(Object.keys(groupNames), searchState.groupBy, groupNames);
    grouper.setAttribute("aria-label", "Group by");
    grouper.onchange = function () {
      searchState.groupBy = grouper.value;
      drawChips();
      searchState.shown = SEARCH_PAGE;
      drawResults();
    };
    var g = el("label", null, "frow");
    g.appendChild(el("span", "Group by", "flabel"));
    g.appendChild(grouper);
    card.appendChild(g);
    var add = el("div", null, "search-add");
    add.appendChild(el("span", "Add", "flabel"));
    ["amount", "account", "category"].forEach(function (k) {
      if (
        searchFilters(q).some(function (f) {
          return f.field === k;
        })
      )
        return;
      add.appendChild(
        button(
          label(k),
          function () {
            searchState.draft = k;
            drawChips();
            input.focus();
          },
          "text",
        ),
      );
    });
    add.appendChild(
      button(
        "More fields",
        function () {
          searchState.draft = "";
          input.value = "";
          drawChips();
          input.focus();
        },
        "text",
      ),
    );
    card.appendChild(add);
    if (filters.length || searchState.groupBy)
      card.appendChild(
        button(
          "Clear all",
          function () {
            searchState.query = { text: {}, direction: "any", recurring: "any", oneOff: "any", split: "any", status: "any" };
            searchState.groupBy = "";
            searchState.draft = "";
            searchState.shown = SEARCH_PAGE;
            drawSearch();
          },
          "text",
        ),
      );
    var help = el("p", null, "support small");
    help.textContent = "A pattern is found anywhere in its field, any case: ^ starts it, $ ends it, a|b is either.";
    card.appendChild(help);
  };

  // a filter added, changed or taken off: the chips, the card and the lines found
  var refilter = function () {
    drawChips();
    drawCard();
    searchState.shown = SEARCH_PAGE;
    drawResults();
    drawMenu();
  };

  var drawResults = function () {
    results.textContent = "";
    var compiled = compileSearch(q, d.groups);
    Object.keys(errors).forEach(function (f) {
      var why = (compiled.errors as Record<string, string>)[f] || "";
      errors[f].textContent = why ? "Not a pattern: " + why : "";
      var box = card.querySelector<HTMLInputElement>("input[data-field='" + f + "']");
      if (box) {
        box.classList.toggle("bad", !!why);
        box.setAttribute("aria-invalid", String(!!why));
      }
    });
    var dated = d.lines.filter(function (l) {
      return l.date >= (q.from || "") && l.date <= (q.to || "9999");
    }).length;
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
          dated.toLocaleString("en-US") +
          " lines in these dates: " +
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
      empty(results, "No line matches. Loosen a pattern, or Clear.");
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
      sortHead(th, c[1], on ? (searchState.descending ? "desc" : "asc") : "", function () {
        if (searchState.sort === c[0]) searchState.descending = !searchState.descending;
        else {
          searchState.sort = c[0];
          searchState.descending = c[0] === "date";
        }
        drawResults();
      });
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
  drawChips();
  drawCard();
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
            l.date + " " + l.payee + " " + cents.format(l.amount) + ": " + pretty(to.value),
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
