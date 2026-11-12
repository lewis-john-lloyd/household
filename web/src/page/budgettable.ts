// The Lines tab's table by group (the A2 design): groups that open and shut, line items with their budget lines,
// recurring charges and giving recipients as breakdowns, and a loan's parts rolled into one row. A redraw keeps
// the page where it is and the focus on the row used.

// A row that opens and shuts what is under it: its name with a chevron, and a click or Enter to toggle.
function foldName(td: HTMLElement, name: string, open: boolean) {
  var chev = el("span", null, "chev" + (open ? " open" : ""));
  chev.appendChild(icon("chevron"));
  td.appendChild(chev);
  td.appendChild(document.createTextNode(name));
}

function foldRow(row: HTMLElement, key: string, open: boolean, flip: () => void) {
  row.tabIndex = 0;
  row.dataset.fold = key;
  row.setAttribute("aria-expanded", open ? "true" : "false");
  var go = function () {
    flip();
    redrawKeeping(key);
  };
  row.onclick = go;
  row.onkeydown = onEnter(go);
}

// Redraw the page (where it is) with the focus back on the row or button that was used.
function redrawKeeping(key: string) {
  drawBudgetPage();
  document.querySelectorAll<HTMLElement>("#budget-body [data-fold]").forEach(function (n) {
    if (n.dataset.fold === key) n.focus({ preventScroll: true });
  });
}

// A recipient of gifts under its giving line item: what it was given in the year so far, and whether gifts
// to it count toward the deduction, marked here (a recipient edit, saved like any other).
function recipientRow(r: Recipient, year: string) {
  var t = "recipient|" + r.key.toUpperCase();
  var e = edited(t);
  var said = e ? String((e.edit as Record<string, string>).deductible || "") : r.deductible;
  var row = el("tr", null, "rec recipient");
  var name = cell(row, r.name);
  if (changedText(t)) name.appendChild(el("span", "changed", "tag warn"));
  cell(row, countOf(r.gifts, "gift", "gifts") + " in " + year, "text");
  cell(row, "", "text");
  cell(row, "");
  cell(row, money.format(r.given));
  cell(row, "");
  var sc = cell(row, "", "text status");
  if (!r.key) {
    // a gift whose line names no payee (a check, say): there is no one to mark
    sc.appendChild(el("span", "No payee to mark", "muted"));
    return row;
  }
  var pick = el("select", null, "deductible-pick") as HTMLSelectElement;
  pick.setAttribute("aria-label", "Whether gifts to " + r.name + " are deductible");
  pick.dataset.fold = t;
  [
    ["", "Deductible?"],
    ["yes", "Deductible"],
    ["no", "Not deductible"],
  ].forEach(function (o) {
    if (o[0] === "" && said) return; // once said, it is yes or no
    var opt = el("option", o[1]) as HTMLOptionElement;
    opt.value = o[0];
    opt.selected = o[0] === said;
    pick.appendChild(opt);
  });
  pick.onclick = function (ev) {
    ev.stopPropagation();
  };
  pick.onchange = function () {
    if (pick.value === r.deductible && staged[t]) unstage(t);
    else if (pick.value)
      stage(
        { kind: "recipient", key: r.key, deductible: pick.value },
        r.name + ": " + (pick.value === "yes" ? "deductible" : "not deductible"),
      );
    redrawKeeping(t);
  };
  sc.appendChild(pick);
  if (said === "") row.classList.add("unsaid");
  return row;
}

function budgetTable(v: BudgetView) {
  var wrap = el("div", null, "scroll");
  var table = el("table", null, "bv");
  var head = el("tr");
  var kindKey = function (x: ViewGroup | ViewLine) {
    return (x as ViewLine).category ? kindOf(linesFor((x as ViewLine).category)) : "";
  };
  // each column but "How the budget is set" and "Status" sorts the groups, and the line items within each
  var keys: (((x: ViewGroup | ViewLine) => string | number) | null)[] = [
    function (x) {
      return ((x as ViewGroup).name || (x as ViewLine).category || "").toLowerCase();
    },
    null,
    kindKey,
    function (x) {
      return x.target || 0;
    },
    function (x) {
      return x.actual || 0;
    },
    function (x) {
      return x.target > 0 ? x.actual / x.target : x.actual > 0 ? Infinity : 0;
    },
    null,
  ];
  var sort = budget.sort && budget.sort.col < keys.length ? budget.sort : { col: -1, dir: 1 };
  ["Line item", "How the budget is set", "Kind", "Budget", "Spent", "Used", "Status"].forEach(function (h, i) {
    var th = el("th", null, i === 1 || i === 2 || i === 5 || i === 6 ? "text" : "");
    if (!keys[i]) {
      th.textContent = h;
      head.appendChild(th);
      return;
    }
    var b = button(
      h + (sort.col === i ? (sort.dir > 0 ? " \u2191" : " \u2193") : ""),
      function () {
        budget.sort = sort.col === i ? { col: i, dir: -sort.dir } : { col: i, dir: i === 0 || i === 2 ? 1 : -1 };
        drawBudgetPage();
      },
      "sort",
    );
    th.setAttribute("aria-sort", sort.col === i ? (sort.dir > 0 ? "ascending" : "descending") : "none");
    th.appendChild(b);
    head.appendChild(th);
  });
  table.appendChild(el("thead")).appendChild(head);
  var body = el("tbody");
  var tracked = trackedByKey();
  var order = function <T extends ViewGroup | ViewLine>(list: T[]): T[] {
    if (sort.col < 0) return list;
    var k = keys[sort.col]!; // a column that sorts
    return list.slice().sort(function (a, b) {
      var x = k(a),
        y = k(b);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  };
  var inForce = function (category: string) {
    return linesFor(category).filter(function (x) {
      return x.in_force !== "ended";
    });
  };
  var loanOf = budget.data!.book.loans || {}; // category -> loan id, from compute
  var deductibleShown = false;
  order(v.groups).forEach(function (g) {
    // A line item with no budget line in force is unplanned: a group's unplanned ones fold into one row at its
    // foot (they count in its spending), opened by a click. A group of one row is that row alone. A group's
    // header opens and shuts it (open at first); a line item's breakdown, and a loan's parts, open on a click.
    var planned = g.lines.filter(function (l) {
      return inForce(l.category).length || l.target;
    });
    var unplanned = g.lines.filter(function (l) {
      return planned.indexOf(l) < 0;
    });
    var alone = planned.length + (unplanned.length ? 1 : 0) === 1 && !unplanned.length;
    var gKey = "group:" + g.name;
    var gOpen = alone || !(budget.shut && budget.shut[gKey]);
    if (!alone) {
      var gr = el("tr", null, "group fold");
      foldName(cell(gr, ""), pretty(g.name), gOpen);
      cell(gr, g.lines.length + " line item" + (g.lines.length > 1 ? "s" : ""), "text rule");
      cell(gr, "", "text");
      cell(gr, g.target ? money.format(g.target) : "");
      cell(gr, money.format(g.actual));
      var gUsed = cell(gr, "", "text");
      gUsed.appendChild(spentBar(g.actual, g.target));
      cell(gr, "", "text");
      foldRow(gr, gKey, gOpen, function () {
        budget.shut = budget.shut || {};
        budget.shut[gKey] = gOpen;
      });
      body.appendChild(gr);
    }
    if (!gOpen) return;
    var lineRow = function (l: ViewLine, cls: string) {
      var lines = inForce(l.category);
      var several = lines.length > 1; // each line its own row below, to open and change by itself
      var lKey = "line:" + l.category;
      var nest = alone ? "" : " nested"; // a row inside a group: drawn on the group's guide line
      var broken = several || l.recurring.length > 0 || l.recipients.length > 0;
      var lOpen = broken && !!(budget.opened && budget.opened[lKey]);
      var row = el(
        "tr",
        null,
        cls +
          (alone ? "" : " nested") +
          (budget.open && budget.open.category === l.category && !budget.open.line ? " chosen" : ""),
      );
      row.tabIndex = 0;
      var name = cell(row, "");
      if (broken) {
        var chev = button(
          "",
          function (e) {
            e.stopPropagation();
            budget.opened = budget.opened || {};
            budget.opened[lKey] = !lOpen;
            redrawKeeping(lKey);
          },
          "chev",
        );
        chev.appendChild(icon("chevron"));
        chev.setAttribute("aria-expanded", lOpen ? "true" : "false");
        chev.setAttribute("aria-label", (lOpen ? "Hide " : "Show ") + l.category + "'s breakdown");
        chev.dataset.fold = lKey;
        if (lOpen) chev.classList.add("open");
        name.appendChild(chev);
      } else name.appendChild(el("span", null, "chev-space"));
      var shown = lines.length === 1 ? pretty(lines[0].line) : pretty(l.category);
      name.appendChild(document.createTextNode(shown));
      if (shown.toLowerCase() !== pretty(l.category).toLowerCase()) name.title = l.category;
      if (alone && g.name && g.name !== l.category) name.appendChild(el("span", pretty(g.name), "tag"));
      if (
        lines.some(function (x) {
          return changedText("budget_line|" + x.line);
        })
      )
        name.appendChild(el("span", "changed", "tag warn"));
      var parts: string[] = [];
      if (several) parts.push(lines.length + " budget lines");
      else if (lines.length === 1 && lines[0].rule === "recurring" && l.recurring.length)
        parts.push(countOf(l.recurring.length, "recurring charge", "recurring charges")); // the rule is its charges
      else parts.push(lines.length ? shortHow(lines[0]) : l.target ? "Scheduled" : "No budget line");
      if (l.recurring.length && !(lines.length === 1 && lines[0].rule === "recurring"))
        parts.push(countOf(l.recurring.length, "recurring charge", "recurring charges"));
      if (l.recipients.length) parts.push(countOf(l.recipients.length, "recipient", "recipients"));
      var how = cell(row, parts.join("; "), "text rule");
      if (lines.length === 1) how.title = String(lines[0].in_words || "");
      var kind = kindOf(lines);
      var kc = cell(row, "", "text");
      kc.appendChild(el("span", kind || "Unplanned", "kind kind-" + (kind || "Unplanned").toLowerCase()));
      cell(row, l.target ? money.format(l.target) : "");
      var over = l.target > 0 && l.actual > l.target + 0.5;
      cell(row, money.format(l.actual), over ? "over" : "");
      var used = cell(row, "", "text");
      used.appendChild(spentBar(l.actual, l.target));
      var st = lineStatus(v, l, lines);
      if (!st && l.recipients.length && v.deductible)
        st = { text: money.format(v.deductible.given) + " deductible in " + v.deductible.year, cls: "" };
      var sc = cell(row, "", "text status");
      if (st) sc.appendChild(el("span", st.text, st.cls));
      if (l.balance !== null) {
        var fund = fundText(l.balance);
        sc.appendChild(el("span", fund.text, "fund-balance " + fund.cls));
      }
      var open = function () {
        budget.open = { category: l.category };
        openLine(l.category, v);
      };
      row.onclick = open;
      row.onkeydown = onEnter(open);
      body.appendChild(row);
      if (!lOpen) return;
      if (several)
        lines.forEach(function (bl) {
          var part = (l.byLine || []).filter(function (x) {
            return x.line === bl.line;
          })[0];
          var sub = el(
            "tr",
            null,
            "rec subline" +
              nest +
              (budget.open && budget.open.category === l.category && budget.open.line === bl.line ? " chosen" : ""),
          );
          sub.tabIndex = 0;
          var subName = cell(sub, pretty(bl.line));
          subName.title = bl.line;
          if (changedText("budget_line|" + bl.line)) subName.appendChild(el("span", "changed", "tag warn"));
          cell(sub, shortHow(bl), "text rule").title = String(bl.in_words || "");
          var skc = cell(sub, "", "text");
          var sk = KIND_OF_RULE[bl.rule] || "Fixed";
          skc.appendChild(el("span", sk, "kind kind-" + sk.toLowerCase()));
          cell(sub, part ? money.format(part.target) : "");
          cell(sub, "");
          cell(sub, "");
          cell(sub, "");
          var openOne = function (e?: Event) {
            if (e) e.stopPropagation();
            budget.open = { category: l.category, line: bl.line };
            openLine(l.category, v, bl.line);
          };
          sub.onclick = openOne;
          sub.onkeydown = onEnter(openOne);
          body.appendChild(sub);
        });
      l.recurring.forEach(function (r) {
        var t = tracked[r.key] || {};
        var rec = el("tr", null, "rec" + nest);
        cell(rec, chargeName({ key: r.key, name: t.name }));
        var about = [
          t.cadence,
          t.status === "price changed" && t.previous
            ? "now " + cents.format(Number(t.latest)) + " (was " + cents.format(Number(t.previous)) + ")"
            : "",
        ];
        cell(rec, about.filter(Boolean).join(", "), "text");
        cell(rec, "", "text");
        cell(rec, r.expected === null ? "" : money.format(r.expected));
        cell(rec, money.format(r.actual));
        cell(rec, "");
        cell(rec, r.actual > 0 ? "Paid" : r.expected ? "Not seen yet" : "", "text status");
        body.appendChild(rec);
      });
      if (l.recipients.length && v.deductible && !deductibleShown) {
        // the head of the giving list: the year's gifts that count toward the deduction, then the recipients that
        // make it up, each marked here
        deductibleShown = true;
        var dr = el("tr", null, "rec deductible" + nest);
        cell(dr, "Deductible in " + v.deductible.year);
        cell(
          dr,
          "Gifts in " +
            v.deductible.year +
            (v.deductible.year === v.through.slice(0, 4) && v.partial ? " so far" : "") +
            " to recipients marked deductible" +
            (v.deductible.unsaid ? "; " + countOf(v.deductible.unsaid, "recipient", "recipients") + " not marked yet" : ""),
          "text rule",
        );
        cell(dr, "", "text");
        cell(dr, "");
        cell(dr, money.format(v.deductible.given));
        cell(dr, "");
        cell(dr, "", "text status");
        body.appendChild(dr);
      }
      l.recipients.forEach(function (r) {
        var rr = recipientRow(r, v.through.slice(0, 4));
        if (nest) rr.classList.add("nested");
        body.appendChild(rr);
      });
    };
    // a loan's parts (its loan_split setting's categories) roll up into one row, opened to show each part
    var rolled: Record<string, ViewLine[]> = {};
    planned.forEach(function (l) {
      var id = loanOf[l.category];
      if (id) (rolled[id] = rolled[id] || []).push(l);
    });
    var drawn: Record<string, boolean> = {};
    order(planned).forEach(function (l) {
      var id = loanOf[l.category];
      if (!id || rolled[id].length < 2) {
        lineRow(l, "line");
        return;
      }
      if (drawn[id]) return;
      drawn[id] = true;
      var parts = order(rolled[id]);
      var key = "loan:" + id;
      var lOpen = !!(budget.opened && budget.opened[key]);
      var sum = function (f: (x: ViewLine) => number) {
        return parts.reduce(function (t, x) {
          return t + f(x);
        }, 0);
      };
      var target = sum(function (x) {
          return x.target;
        }),
        actual = sum(function (x) {
          return x.actual;
        });
      var lr = el("tr", null, "line fold loan nested");
      foldName(cell(lr, ""), pretty(id), lOpen);
      cell(lr, parts.length + " parts: principal and interest from the amortization schedule, and the escrow", "text rule");
      var lk = cell(lr, "", "text");
      lk.appendChild(el("span", "Recurring", "kind kind-recurring"));
      cell(lr, target ? money.format(target) : "");
      cell(lr, money.format(actual), target > 0 && actual > target + 0.5 ? "over" : "");
      var lu = cell(lr, "", "text");
      lu.appendChild(spentBar(actual, target));
      cell(lr, "", "text status");
      foldRow(lr, key, lOpen, function () {
        budget.opened = budget.opened || {};
        budget.opened[key] = !lOpen;
      });
      body.appendChild(lr);
      if (lOpen)
        parts.forEach(function (x) {
          lineRow(x, "line part");
        });
    });
    if (unplanned.length) {
      var shown = !!(budget.unplanned && budget.unplanned[g.name]);
      var spent = unplanned.reduce(function (t, l) {
        return t + l.actual;
      }, 0);
      var uKey = "unplanned:" + g.name;
      var u = el("tr", null, "line unplanned fold nested");
      foldName(cell(u, ""), "Unplanned", shown);
      cell(
        u,
        unplanned
          .map(function (l) {
            return l.category;
          })
          .join(", "),
        "text rule",
      );
      var uk = cell(u, "", "text");
      uk.appendChild(el("span", "No budget", "kind kind-unplanned"));
      cell(u, "");
      cell(u, money.format(spent));
      cell(u, "");
      cell(u, shown ? "Hide" : "Show " + unplanned.length, "text status link");
      foldRow(u, uKey, shown, function () {
        budget.unplanned = budget.unplanned || {};
        budget.unplanned[g.name] = !shown;
      });
      body.appendChild(u);
      if (shown)
        order(unplanned).forEach(function (l) {
          lineRow(l, "line unplanned-item");
        });
    }
  });
  table.appendChild(body);
  wrap.appendChild(table);
  return wrap;
}
