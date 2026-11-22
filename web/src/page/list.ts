// Transactions: the list beside the editor.

// --- the list and the editor ---------------------------------------------------------------------
// Every editor part is a list on the left, one row per item (its name, a line about it, and what is staged
// for it), and the chosen item's editor on the right. On a phone the editor replaces the list, with Back.
var selected: Record<string, string | null> = {}; // the item chosen in each tab, by key
var txnPage: { id: string; from: string; part?: string } | null = null; // the transaction the transaction page shows, and the view it was opened from

/** A fact to show: its name, and its value as text or an element (left out when blank). */
type Fact = [string, string | number | Node | null | undefined];

function facts(panel: HTMLElement, pairs: Fact[]) {
  var dl = el("dl", null, "facts");
  pairs.forEach(function (p) {
    if (p[1] === "" || p[1] === null || p[1] === undefined) return;
    dl.appendChild(el("dt", p[0]));
    var dd = el("dd");
    if (typeof p[1] === "object") dd.appendChild(p[1] as Node);
    else dd.textContent = String(p[1]);
    dl.appendChild(dd);
  });
  panel.appendChild(dl);
}

function section(panel: HTMLElement, title: string, help?: string) {
  var s = el("section", null, "editor-section");
  s.appendChild(el("h3", title));
  if (help) s.appendChild(el("p", help, "support"));
  panel.appendChild(s);
  return s;
}

/** An editor part as a list and an editor: its items, how each is named, and its editor. */
interface MasterDetail<T> {
  items: T[];
  key: (item: T) => string;
  title: (item: T) => string;
  sub?: (item: T) => string;
  staged?: (item: T) => string;
  section?: (item: T) => string;
  detail: (item: T, panel: HTMLElement, done: () => void) => void;
  add?: { label: string; form: (panel: HTMLElement, done: () => void) => void }[];
  noun: string;
  empty: string;
  none?: string;
  /** The chosen item's editor stays over the list on a phone as the part draws (an answer moved on to it). */
  open?: boolean;
  /** The fields a long list is found by, as Search's bar: typed, chosen as chips, one of them to group by. */
  fields?: ListField<T>[];
  /** An item in words, one and many ("question", "questions"), for the bar's counts. */
  unit?: [string, string];
}

/** A field of a list's items, for the query bar: as Search's fields, with an item's value. */
interface ListField<T> extends SearchBarField {
  of: (item: T) => string;
}

/** A filter in a list's bar: a pattern on a field ("" for any text), an amount's bounds, or a choice. */
interface ListFilter {
  field: string;
  kind: SearchOption["kind"];
  value: string;
  min?: number | null | undefined;
  max?: number | null | undefined;
  text: string; // the chip's words
  shown: string; // the chip's value
}

// how far each part's list is scrolled, kept across its redraws, so an answer leaves the list where it was
var listScrolls: Record<string, number> = {};

// what each part's bar holds, kept across its redraws (an answer redraws the part)
var listQueries: Record<string, { filters: ListFilter[]; groupBy: string; draft: string; typed: string }> = {};

/** Whether an item passes a list's filters. */
function listMatches<T>(item: T, filters: ListFilter[], fields: ListField<T>[]): boolean {
  var test = function (pattern: string, v: string) {
    try {
      return new RegExp(pattern, "i").test(v);
    } catch (e) {
      return v.toLowerCase().indexOf(pattern.toLowerCase()) >= 0; // half typed: as plain text
    }
  };
  return filters.every(function (f) {
    if (f.kind === "any")
      return fields.some(function (lf) {
        return lf.kind === "text" && test(f.value, lf.of(item));
      });
    var field = fields.filter(function (lf) {
      return lf.key === f.field;
    })[0];
    if (!field) return true;
    var v = field.of(item);
    if (f.kind === "amount") {
      var n = Math.abs(Number(v) || 0);
      return (
        (f.min === null || f.min === undefined || n >= f.min - 0.005) &&
        (f.max === null || f.max === undefined || n <= f.max + 0.005)
      );
    }
    if (f.kind === "choice") return v === f.value;
    return test(f.value, v);
  });
}

function masterDetail<T>(body: HTMLElement, opts: MasterDetail<T>) {
  var wrap = el("div", null, "md");
  var side = el("section", null, "md-list");
  var panel = el("section", null, "md-detail");
  var head = el("div", null, "md-head");
  var count = el("div", null, "md-count");
  head.appendChild(count);
  var unit = opts.unit || ["item", "items"];
  var fields = opts.fields || null;
  var query = fields ? listQueries[editTab] || (listQueries[editTab] = { filters: [], groupBy: "", draft: "", typed: "" }) : null;
  var filter = textInput("", "md-filter", "Filter");
  filter.setAttribute("aria-label", "Filter the list");
  if (opts.items.length > 8 && !fields) head.appendChild(filter);
  side.appendChild(head);
  if (opts.add) {
    var adds = el("div", null, "actions md-adds");
    opts.add.forEach(function (a) {
      adds.appendChild(
        button(
          a.label,
          function () {
            selected[editTab] = null;
            mark();
            open(a.label, function () {
              a.form(panel, done);
            });
          },
          "small",
        ),
      );
    });
    side.appendChild(adds);
  }
  var list = el("ul", null, "md-items");
  var tab = editTab;
  list.addEventListener("scroll", function () {
    listScrolls[tab] = list.scrollTop;
  });
  var rows: { item: T; li: HTMLElement; row: HTMLButtonElement; text: string; section: string }[] = [];
  opts.items.forEach(function (item) {
    var li = el("li");
    var row = button(
      "",
      function () {
        selected[editTab] = opts.key(item);
        mark();
        show(item);
      },
      "md-row",
    );
    row.appendChild(el("span", opts.title(item), "md-title"));
    var sub = opts.sub ? opts.sub(item) : "";
    if (sub) row.appendChild(el("span", sub, "md-sub"));
    var said = opts.staged ? opts.staged(item) : "";
    if (said) row.appendChild(el("span", said, "md-staged"));
    li.appendChild(row);
    rows.push({
      item: item,
      li: li,
      row: row,
      text: (opts.title(item) + " " + sub).toLowerCase(),
      section: opts.section ? opts.section(item) : "",
    });
  });
  side.appendChild(list);
  var amountField = fields
    ? fields.filter(function (f) {
        return f.kind === "amount";
      })[0]
    : undefined;
  var counting = function (n: number) {
    return n.toLocaleString() + " " + (n === 1 ? unit[0] : unit[1]);
  };
  // the rows in their sections (or, grouped, a section per value with its count and sum), those filtered out hidden
  function layout() {
    list.textContent = "";
    var grouped = query && query.groupBy ? fieldOfList(query.groupBy) : null;
    var order = rows;
    var names: Record<string, string> = {};
    if (grouped) {
      var tally: Record<string, [number, number]> = {};
      rows.forEach(function (r) {
        var k = grouped!.of(r.item);
        var t = tally[k] || (tally[k] = [0, 0]);
        if (r.li.hidden) return;
        t[0]++;
        t[1] += amountField ? Number(amountField.of(r.item)) || 0 : 0;
      });
      Object.keys(tally).forEach(function (k) {
        var choice = (grouped!.choices || []).filter(function (c) {
          return c[0] === k;
        })[0];
        var said = k ? (choice ? choice[1] : isNamed(grouped!) ? pretty(k) : k) : "No " + grouped!.label.toLowerCase();
        names[k] = said + ": " + counting(tally[k][0]) + (amountField ? ", " + cents.format(tally[k][1]) : "");
      });
      order = rows.slice().sort(function (a, b) {
        var x = grouped!.of(a.item),
          y = grouped!.of(b.item);
        return tally[y][0] - tally[x][0] || (x < y ? -1 : x > y ? 1 : 0);
      });
    }
    var last: string | null = null;
    var sec: HTMLElement | null = null;
    order.forEach(function (r) {
      var name = grouped ? names[grouped.of(r.item)] : r.section;
      if (name && name !== last) {
        sec = el("li", name, "md-section");
        sec.hidden = true;
        list.appendChild(sec);
        last = name;
      }
      if (sec && !r.li.hidden) sec.hidden = false;
      list.appendChild(r.li);
    });
  }
  function fieldOfList(key: string) {
    return (
      fields!.filter(function (f) {
        return f.key === key;
      })[0] || null
    );
  }
  function isNamed(f: ListField<T>) {
    return f.key === "guess" || f.key === "category" || f.key === "group";
  }
  function counted() {
    var shown = rows.filter(function (r) {
      return !r.li.hidden;
    }).length;
    count.textContent =
      (shown === rows.length ? "" : shown.toLocaleString() + " of ") + rows.length.toLocaleString() + " " + opts.noun;
  }
  function refilter() {
    rows.forEach(function (r) {
      r.li.hidden = query ? !listMatches(r.item, query.filters, fields!) : false;
    });
    layout();
    counted();
  }
  filter.oninput = function () {
    var q = filter.value.trim().toLowerCase();
    rows.forEach(function (r) {
      r.li.hidden = !!q && r.text.indexOf(q) < 0;
    });
    layout();
    counted();
  };
  if (fields && query && (opts.items.length > 8 || query.filters.length || query.groupBy)) {
    var q = query;
    var bar = queryBar(head, {
      menuId: "list-menu",
      ariaLabel: "Find in the list",
      empty: "Find: a word, or a field such as " + fields[0].label.toLowerCase(),
      fields: fields,
      named: fields.filter(isNamed).map(function (f) {
        return f.key;
      }),
      state: q,
      suggest: function (text, draft) {
        return suggest(text, draft, {
          fields: fields!,
          values: function (field, typed, most) {
            var f = fieldOfList(field);
            if (!f) return [];
            var n: Record<string, number> = {};
            var t = typed.trim().toLowerCase();
            rows.forEach(function (r) {
              var v = f!.of(r.item);
              if (v && (!t || v.toLowerCase().indexOf(t) >= 0)) n[v] = (n[v] || 0) + 1;
            });
            return Object.keys(n)
              .sort(function (a, b) {
                return n[b] - n[a] || (a < b ? -1 : 1);
              })
              .slice(0, most)
              .map(function (v) {
                return [v, n[v], counting(n[v])] as [string, number, string];
              });
          },
          anyIn: fields!
            .filter(function (f) {
              return f.kind === "text";
            })
            .map(function (f) {
              return f.label;
            })
            .join(", "),
          valueFields: fields!
            .filter(function (f) {
              return f.kind === "text";
            })
            .map(function (f) {
              return f.key;
            }),
        });
      },
      chips: function () {
        var shown: BarChip[] = q.filters.map(function (f, i) {
          return {
            text: f.text,
            value: f.shown,
            cls: "",
            remove: function () {
              q.filters.splice(i, 1);
              changed();
            },
          };
        });
        if (q.groupBy)
          shown.push({
            text: "Grouped by " + (fieldOfList(q.groupBy) || { label: q.groupBy }).label.toLowerCase(),
            value: "",
            cls: "group",
            remove: function () {
              q.groupBy = "";
              changed();
            },
          });
        return shown;
      },
      choose: function (o, group) {
        if (o.kind === "group" || (o.kind === "field" && group)) q.groupBy = o.field;
        else {
          var f = fieldOfList(o.field);
          var name = o.kind === "any" ? "Any text" : f ? f.label : o.field;
          var shown =
            o.kind === "amount"
              ? o.label.slice(name.length + 1)
              : o.kind === "choice"
                ? o.label.slice(name.length + 2)
                : o.kind === "value"
                  ? f && isNamed(f)
                    ? pretty(o.label.replace(/^.* is /, ""))
                    : o.label.replace(/^.* is /, "")
                  : o.value;
          q.filters = q.filters
            .filter(function (x) {
              return x.field !== o.field || o.kind === "any";
            })
            .concat([{ field: o.field, kind: o.kind, value: o.value, min: o.min, max: o.max, text: name, shown: shown }]);
        }
        changed();
      },
      dropLast: function () {
        if (q.groupBy) q.groupBy = "";
        else if (q.filters.length) q.filters.pop();
        else return false;
        changed();
        return true;
      },
    });
    var changed = function () {
      refilter();
      bar.drawChips();
      bar.drawMenu();
    };
    bar.drawChips();
  }
  refilter();
  function mark() {
    rows.forEach(function (r) {
      r.row.setAttribute("aria-current", String(opts.key(r.item) === selected[editTab]));
    });
  }
  function open(title: string, fill: () => void) {
    panel.textContent = "";
    var back = button(
      "Back to the list",
      function () {
        wrap.classList.remove("showing");
      },
      "text md-back",
    );
    panel.appendChild(back);
    panel.appendChild(el("h2", title, "md-heading"));
    fill();
    wrap.classList.add("showing");
  }
  function show(item: T) {
    open(opts.title(item), function () {
      opts.detail(item, panel, done);
    });
  }
  function done() {
    redrawEditor();
  }
  counted();
  wrap.appendChild(side);
  wrap.appendChild(panel);
  body.appendChild(wrap);
  var chosen = rows.filter(function (r) {
    return opts.key(r.item) === selected[editTab];
  })[0];
  list.scrollTop = listScrolls[tab] || 0; // where it was before the redraw
  if (chosen) {
    mark();
    show(chosen.item);
    if (!opts.open) wrap.classList.remove("showing");
    // the chosen row (the next question, after an answer) brought into view within the list alone, the page left
    // where it is
    var top = chosen.li.offsetTop - list.offsetTop,
      bottom = top + chosen.li.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
    listScrolls[tab] = list.scrollTop;
  } else if (!rows.length) panel.appendChild(el("p", opts.none || "Nothing here.", "support"));
  else panel.appendChild(el("p", opts.empty, "support md-empty"));
}
