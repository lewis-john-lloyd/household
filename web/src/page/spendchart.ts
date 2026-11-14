// Where the money went, Over time: what was spent each month (daily_actuals, never what was budgeted), stacked by
// group or by a group's categories, in dollars or as each month's share, with one-offs left out on request. The
// book is the Budget page's (getBudgetBook, loaded once a page life and shared), shaped here by spendingOverTime
// in budgetview.ts. Up to eight groups or categories are drawn, each keeping its color while it stays chosen; the
// rest fold into Other, so a month's bar is always all of its spending.

var SPEND_MAX = 8; // --series-1 to --series-8
var SPEND_DEFAULT = 6;
var SPEND_RANGES: [string, string][] = [
  ["all", "All time"],
  ["5", "5 years"],
  ["2", "2 years"],
  ["1", "1 year"],
];
var spend = {
  percent: false,
  by: "group" as "group" | "category",
  group: "", // with categories: the group they are drawn from, or all of them
  range: "2",
  withoutOneOffs: false,
  picked: {} as Record<string, Record<string, number>>, // per level and group: key -> its color slot
};

function loadSpendOverTime() {
  var box = byId("flows-overtime");
  if (budget.data) {
    drawSpendOverTime();
    return;
  }
  box.textContent = "Loading…";
  call(
    "getBudgetBook",
    [],
    function (d) {
      budget.data = d;
      drawSpendOverTime();
    },
    "flows-overtime",
  );
}

// the first month the range shows: this month and the N years of months before it
function spendFrom(): string | undefined {
  if (spend.range === "all") return undefined;
  var now = today().slice(0, 7);
  return shiftPeriod(now, -12 * Number(spend.range) + 1);
}

function drawSpendOverTime() {
  if (view !== "flows" || flowsTab !== "overtime" || !budget.data) return;
  var box = byId("flows-overtime");
  box.textContent = "";
  var book = budget.data.book;
  var data = spendingOverTime(book, spend.by, {
    from: spendFrom(),
    to: today().slice(0, 7),
    withoutOneOffs: spend.withoutOneOffs,
    group: spend.by === "category" ? spend.group || undefined : undefined,
  });
  box.appendChild(spendToolbar(book));
  if (!data.months.length) {
    box.appendChild(el("p", "No spending in this range.", "support"));
    return;
  }
  // the chosen keys keep their color slot; a first look picks the most spent
  var context = spend.by + "|" + (spend.by === "category" ? spend.group : "");
  var picked = (spend.picked[context] = spend.picked[context] || {});
  Object.keys(picked).forEach(function (k) {
    if (data.keys.indexOf(k) < 0) delete picked[k];
  });
  if (!Object.keys(picked).length && !spend.picked[context + "|touched"])
    data.keys.slice(0, SPEND_DEFAULT).forEach(function (k, i) {
      picked[k] = i + 1;
    });
  var shown = data.keys.filter(function (k) {
    return picked[k] !== undefined;
  });
  var colorOf = function (k: string) {
    return k === "" ? "var(--outline)" : "var(--series-" + picked[k] + ")";
  };
  var nameOf = function (k: string) {
    return k === "" ? "Other" : pretty(k);
  };
  // each month's parts, the shown keys most spent first from the bottom, then Other on top
  var other = data.months.map(function (_, i) {
    var rest = data.totals[i];
    shown.forEach(function (k) {
      rest -= data.values[k][i];
    });
    return Math.max(Math.round(rest * 100) / 100, 0);
  });
  var stack = shown.concat(
    other.some(function (v) {
      return v > 0;
    })
      ? [""]
      : [],
  );
  var valueOf = function (k: string, i: number) {
    return k === "" ? other[i] : data.values[k][i];
  };

  var cols = el("div", null, "over-cols");
  box.appendChild(cols);
  var main = el("section", null, "panel spend-chart tax-history"); // the charts' shared legend and tooltip
  cols.appendChild(main);
  var level = spend.by === "group" ? "group" : spend.group ? pretty(spend.group) + "'s categories" : "category";
  main.appendChild(el("h3", "Spent each month, by " + level + (spend.percent ? ", as a share" : "")));
  var range =
    data.months[0] === data.months[data.months.length - 1]
      ? monthWords(data.months[0])
      : monthWords(data.months[0]) + " to " + monthWords(data.months[data.months.length - 1]);
  main.appendChild(
    el(
      "p",
      "What was spent, not what was budgeted, " +
        range +
        (spend.withoutOneOffs ? ", without lines marked one-off" : "") +
        ". " +
        (spend.by === "group" ? "Choose a group's bar to see its categories." : "Choose a bar to see its lines.") +
        (book.oneOffs === undefined ? " One-offs can be left out after the next run." : ""),
      "support",
    ),
  );
  var legend = el("div", null, "legend");
  stack.forEach(function (k) {
    var item = el("span", nameOf(k));
    item.style.setProperty("--swatch", colorOf(k));
    legend.appendChild(item);
  });
  main.appendChild(legend);
  main.appendChild(
    spendBars(data, stack, valueOf, colorOf, nameOf, function (k, month) {
      if (k === "") return;
      if (spend.by === "group") {
        spend.by = "category";
        spend.group = k;
        drawSpendOverTime();
        return;
      }
      openSpendLines(k, month, valueOf(k, data.months.indexOf(month)));
    }),
  );
  main.appendChild(spendTable(data, stack, valueOf, colorOf, nameOf));
  cols.appendChild(spendPicker(data, picked, context));
}

function spendToolbar(book: BudgetBook) {
  var bar = el("div", null, "r-toolbar over-bar spend-bar");
  var seg = function (label: string, choices: [string, string][], on: string, pick: (v: string) => void) {
    var s = el("div", null, "segmented");
    s.setAttribute("role", "group");
    s.setAttribute("aria-label", label);
    choices.forEach(function (c) {
      var b = button(c[1], function () {
        pick(c[0]);
        drawSpendOverTime();
      });
      b.setAttribute("aria-pressed", String(on === c[0]));
      s.appendChild(b);
    });
    bar.appendChild(s);
  };
  seg(
    "Show",
    [
      ["dollars", "Dollars"],
      ["percent", "Percent"],
    ],
    spend.percent ? "percent" : "dollars",
    function (v) {
      spend.percent = v === "percent";
    },
  );
  seg(
    "Stack by",
    [
      ["group", "Groups"],
      ["category", "Categories"],
    ],
    spend.by,
    function (v) {
      spend.by = v as "group" | "category";
    },
  );
  if (spend.by === "category") {
    var groups = Object.keys(book.groups)
      .map(function (c) {
        return book.groups[c];
      })
      .filter(function (g, i, all) {
        return all.indexOf(g) === i;
      })
      .sort();
    var names: Record<string, string> = { "": "All groups" };
    groups.forEach(function (g) {
      names[g] = pretty(g);
    });
    var which = choice([""].concat(groups), spend.group, names);
    which.setAttribute("aria-label", "Which group's categories");
    which.onchange = function () {
      spend.group = which.value;
      drawSpendOverTime();
    };
    bar.appendChild(which);
  }
  seg("How far back", SPEND_RANGES, spend.range, function (v) {
    spend.range = v;
  });
  var once = el("label", null, "spend-oneoff");
  var check = el("input") as HTMLInputElement;
  check.type = "checkbox";
  check.checked = spend.withoutOneOffs;
  check.disabled = book.oneOffs === undefined;
  check.onchange = function () {
    spend.withoutOneOffs = check.checked;
    drawSpendOverTime();
  };
  once.appendChild(check);
  once.appendChild(document.createTextNode(" Leave out one-offs"));
  if (check.disabled) once.title = "The next run marks which spending was one-off";
  bar.appendChild(once);
  return bar;
}

function spendBars(
  data: SpendOverTime,
  stack: string[],
  valueOf: (k: string, i: number) => number,
  colorOf: (k: string) => string,
  nameOf: (k: string) => string,
  choose: (k: string, month: string) => void,
) {
  var wrap = el("div", null, "spend-plot");
  var NS = "http://www.w3.org/2000/svg";
  var svgEl = function (name: string, attrs: Record<string, string | number>, parent?: Element) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, String(attrs[k]));
    });
    if (parent) parent.appendChild(e);
    return e;
  };
  var share = function (v: number, i: number) {
    return data.totals[i] > 0 ? (v / data.totals[i]) * 100 : 0;
  };
  var shown = function (k: string, i: number) {
    return spend.percent ? share(valueOf(k, i), i) : valueOf(k, i);
  };
  var hi = spend.percent ? 100 : Math.max.apply(null, data.totals.concat([1]));
  var step = spend.percent ? 25 : Math.pow(10, Math.floor(Math.log10(hi / 4)));
  if (!spend.percent)
    [1, 2, 2.5, 5, 10].some(function (k) {
      if (hi / (step * k) <= 5) {
        step *= k;
        return true;
      }
      return false;
    });
  var top = spend.percent ? 100 : Math.ceil((hi * 1.05) / step) * step;
  var width = Math.max(Math.min(byId("flows-overtime").clientWidth - (window.innerWidth > 900 ? 360 : 48), 1100), 320),
    height = 320;
  var mg = { top: 12, right: 8, bottom: 28, left: 52 };
  var y = function (v: number) {
    return mg.top + ((top - v) / top) * (height - mg.top - mg.bottom);
  };
  var svg = svgEl("svg", {
    viewBox: "0 0 " + width + " " + height,
    class: "history-chart spend-bars",
    role: "img",
    "aria-label":
      "Spending each month, stacked by " + (spend.by === "group" ? "group" : "category") + "; the months are in the table below",
  });
  wrap.appendChild(svg);
  var label = function (v: number) {
    if (spend.percent) return Math.round(v) + "%";
    return v >= 1e6 ? "$" + (v / 1e6).toFixed(1) + "M" : v >= 1000 ? "$" + Math.round(v / 100) / 10 + "k" : "$" + Math.round(v);
  };
  for (var t = 0; t <= top + 0.001; t += step) {
    svgEl("line", { x1: mg.left, x2: width - mg.right, y1: y(t), y2: y(t), class: t === 0 ? "zero" : "grid" }, svg);
    svgEl("text", { x: mg.left - 8, y: y(t) + 4, "text-anchor": "end", class: "axis" }, svg).textContent = label(t);
  }
  var n = data.months.length;
  var gap = (width - mg.left - mg.right) / n;
  var bw = Math.max(Math.min(gap - 2, 32), 2);
  var now = today().slice(0, 7);
  var every = n > 36 ? 12 : n > 18 ? 3 : 1; // labels: years over long ranges, quarters, else every month
  var tip = el("div", null, "tip");
  tip.hidden = true;
  wrap.appendChild(tip);
  data.months.forEach(function (month, i) {
    var x = mg.left + gap * i + (gap - bw) / 2;
    var col = svgEl("g", { class: "spend-month" + (month === now ? " now" : "") }, svg);
    var up = 0;
    var parts = stack.filter(function (k) {
      return valueOf(k, i) > 0;
    });
    parts.forEach(function (k, p) {
      var v = shown(k, i);
      var h = y(up) - y(up + v);
      var seg = svgEl(
        "rect",
        {
          x: x,
          width: bw,
          y: y(up + v),
          height: Math.max(h - (h > 6 ? 2 : h > 3 ? 1 : 0), 0.5), // a surface gap between the parts
          fill: colorOf(k),
          rx: p === parts.length - 1 ? Math.min(2, bw / 2) : 0,
          class: k === "" ? "other" : "part",
        },
        col,
      );
      seg.addEventListener("click", function () {
        choose(k, month);
      });
      up += v;
    });
    var crowded = i > 0 && i < (every === 1 ? 1 : every === 3 ? 2 : 4); // too near the first month's label
    if (!crowded && (i === 0 || (every === 12 ? month.slice(5) === "01" : every === 3 ? /-(01|04|07|10)$/.test(month) : true))) {
      var text =
        every === 12 || (month.slice(5) === "01" && every === 3)
          ? month.slice(0, 4)
          : MONTH_SHORT[Number(month.slice(5)) - 1] + (i === 0 || month.slice(5) === "01" ? " " + month.slice(2, 4) : "");
      if (every === 12 && i === 0 && month.slice(5) !== "01")
        text = MONTH_SHORT[Number(month.slice(5)) - 1] + " " + month.slice(0, 4);
      svgEl("text", { x: x + bw / 2, y: height - 8, "text-anchor": "middle", class: "axis" }, svg).textContent = text;
    }
    // the whole column answers the pointer, wider than its bar
    var hit = svgEl(
      "rect",
      { x: mg.left + gap * i, width: gap, y: mg.top, height: height - mg.top - mg.bottom, fill: "transparent", class: "hit" },
      col,
    );
    hit.addEventListener("click", function (ev) {
      // a click on the column, not a part: the part under the pointer's height
      var r = svg.getBoundingClientRect();
      var py = (((ev as MouseEvent).clientY - r.top) / r.height) * height;
      var at = 0;
      for (var p = 0; p < parts.length; p++) {
        at += shown(parts[p], i);
        if (py >= y(at)) {
          choose(parts[p], month);
          return;
        }
      }
    });
    hit.addEventListener("mouseenter", function () {
      tip.textContent = "";
      tip.appendChild(
        el("div", monthWords(month) + (month === now ? ", so far" : "") + ": " + money.format(data.totals[i]), "strong"),
      );
      parts
        .slice()
        .reverse()
        .forEach(function (k) {
          var row = el("div", null, "row");
          var name = el("span");
          var key = el("i", null, "key");
          key.style.background = colorOf(k);
          name.appendChild(key);
          name.appendChild(document.createTextNode(nameOf(k)));
          row.appendChild(name);
          var figures = el("span", null, "figures");
          figures.appendChild(el("span", money.format(valueOf(k, i))));
          figures.appendChild(el("span", Math.round(share(valueOf(k, i), i)) + "%", "pct"));
          row.appendChild(figures);
          tip.appendChild(row);
        });
      Object.keys(data.below).forEach(function (k) {
        if (data.below[k][i])
          tip.appendChild(el("div", nameOf(k) + ": refunds " + money.format(data.below[k][i]) + " more than spent", "support"));
      });
      if (!parts.length) tip.appendChild(el("div", "Nothing spent"));
      tip.hidden = false;
      var r = svg.getBoundingClientRect(),
        w = wrap.getBoundingClientRect();
      var left = ((mg.left + gap * (i + 1)) / width) * r.width;
      tip.style.left = Math.max(Math.min(left + 8, w.width - tip.offsetWidth - 4), 0) + "px";
      tip.style.top = "8px";
    });
    hit.addEventListener("mouseleave", function () {
      tip.hidden = true;
    });
  });
  return wrap;
}

// the same months as a table, newest first, for reading the numbers (and for a screen reader)
function spendTable(
  data: SpendOverTime,
  stack: string[],
  valueOf: (k: string, i: number) => number,
  colorOf: (k: string) => string,
  nameOf: (k: string) => string,
) {
  var more = el("details", null, "spend-table");
  more.appendChild(el("summary", "The months as a table"));
  var scroll = el("div", null, "scroll");
  var table = el("table", null, "w-table");
  var head = el("tr");
  head.appendChild(el("th", "Month"));
  stack.forEach(function (k) {
    var th = el("th", null, "num");
    var sw = el("i", null, "swatch");
    sw.style.background = colorOf(k);
    th.appendChild(sw);
    th.appendChild(document.createTextNode(" " + nameOf(k)));
    head.appendChild(th);
  });
  head.appendChild(el("th", "Total", "num"));
  table.appendChild(el("thead")).appendChild(head);
  var body = el("tbody");
  for (var i = data.months.length - 1; i >= 0; i--) {
    var tr = el("tr");
    tr.appendChild(el("td", monthWords(data.months[i])));
    stack.forEach(function (k) {
      var v = valueOf(k, i);
      tr.appendChild(
        el("td", spend.percent ? (data.totals[i] ? Math.round((v / data.totals[i]) * 100) + "%" : "–") : money.format(v), "num"),
      );
    });
    tr.appendChild(el("td", money.format(data.totals[i]), "num"));
    body.appendChild(tr);
  }
  table.appendChild(body);
  scroll.appendChild(table);
  more.appendChild(scroll);
  return more;
}

function spendPicker(data: SpendOverTime, picked: Record<string, number>, context: string) {
  var box = el("section", null, "panel over-picks");
  box.appendChild(el("h3", spend.by === "group" ? "Groups" : "Categories"));
  box.appendChild(el("p", "Up to " + SPEND_MAX + " drawn; the rest are Other. Most spent first, over the range.", "support"));
  var inRange: Record<string, number> = {};
  data.keys.forEach(function (k) {
    inRange[k] = data.values[k].reduce(function (a, b) {
      return a + b;
    }, 0);
  });
  var acts = el("div", null, "spend-pick-acts");
  acts.appendChild(
    button(
      "Most spent",
      function () {
        Object.keys(picked).forEach(function (k) {
          delete picked[k];
        });
        data.keys.slice(0, SPEND_DEFAULT).forEach(function (k, i) {
          picked[k] = i + 1;
        });
        drawSpendOverTime();
      },
      "text",
    ),
  );
  acts.appendChild(
    button(
      "None",
      function () {
        Object.keys(picked).forEach(function (k) {
          delete picked[k];
        });
        spend.picked[context + "|touched"] = {};
        drawSpendOverTime();
      },
      "text",
    ),
  );
  if (spend.by === "category" && spend.group)
    acts.appendChild(
      button(
        "Back to groups",
        function () {
          spend.by = "group";
          drawSpendOverTime();
        },
        "text",
      ),
    );
  box.appendChild(acts);
  var list = el("div", null, "line-picks");
  list.setAttribute("role", "group");
  list.setAttribute("aria-label", spend.by === "group" ? "Groups drawn" : "Categories drawn");
  var count = Object.keys(picked).length;
  data.keys
    .slice()
    .sort(function (a, b) {
      return inRange[b] - inRange[a] || data.keys.indexOf(a) - data.keys.indexOf(b);
    })
    .forEach(function (k) {
      var on = picked[k] !== undefined;
      var b = el("button", null, "line-pick") as HTMLButtonElement;
      b.type = "button";
      b.setAttribute("aria-pressed", String(on));
      var sw = el("i", null, "swatch");
      if (on) sw.style.background = "var(--series-" + picked[k] + ")";
      b.appendChild(sw);
      b.appendChild(el("span", pretty(k), "pick-name"));
      b.appendChild(el("span", money.format(inRange[k]), "pick-total"));
      b.disabled = !on && count >= SPEND_MAX;
      if (b.disabled) b.title = SPEND_MAX + " at most: take one off first";
      b.addEventListener("click", function () {
        spend.picked[context + "|touched"] = {};
        if (on) delete picked[k];
        else {
          var used = Object.keys(picked).map(function (x) {
            return picked[x];
          });
          for (var slot = 1; used.indexOf(slot) >= 0; slot++) {
            /* the lowest free color */
          }
          picked[k] = slot;
        }
        drawSpendOverTime();
      });
      list.appendChild(b);
    });
  box.appendChild(list);
  return box;
}

// a category's lines in one month, as the Sankey's drawer shows them, with its budget a click on
function openSpendLines(category: string, month: string, amount: number) {
  var days = periodDays(month);
  var d = drawer(
    pretty(category),
    monthWords(month) + ": " + money.format(amount) + " spent" + (spend.withoutOneOffs ? ", one-offs left out." : "."),
  );
  d.appendChild(
    button(
      "Open its budget",
      function () {
        closeDrawer();
        budget.tab = "lines";
        budget.open = { category: category };
        setView("budget");
      },
      "text",
    ),
  );
  var box = el("div", "Loading…", "drawer-section support");
  box.id = "spend-lines";
  d.appendChild(box);
  cachedCall(
    "getLines",
    [days[0], days[1], [category], []],
    function (list) {
      var draw = function () {
        box.textContent = "";
        box.className = "drawer-section";
        box.appendChild(lineList(list, draw));
      };
      draw();
    },
    "spend-lines",
  );
}
