// Investments: overview, over time, allocation, accounts and lots.

// --- Investments and Home -----------------------------------------------------------------------------------
var investments: WealthPage | null = null,
  home: HomePage | null = null,
  investView = "overview",
  homeView = "overview";
var INVEST_PARTS: string[][] = [
  ["history", "Over time"],
  ["overview", "Overview"],
  ["allocation", "Allocation"],
  ["accounts", "Accounts"],
  ["lots", "Lots"],
];
var pct = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });
var BUCKET_SERIES: Record<string, number> = {
  "401k_pretax": 1,
  "401k_roth": 2,
  ira_pretax: 3,
  ira_roth: 4,
  hsa: 5,
  taxable: 6,
  ibonds: 7,
  "529": 8,
};
var PART_NAMES: Record<string, string> = {
  "401k_pretax": "401(k) pre-tax",
  "401k_roth": "401(k) Roth",
  ira_pretax: "IRA pre-tax",
  ira_roth: "IRA Roth",
  hsa: "HSA",
  taxable: "Taxable",
  ibonds: "I-Bonds",
  "529": "529",
  other: "Other",
  home_equity: "Home equity",
  other_debt: "Other debt",
};
var KIND_NAMES: Record<string, string> = { capital: "Capital", repair: "Repair", energy_credit: "Energy credit" };

function loadInvestments() {
  if (investments) {
    drawInvestments();
    return;
  }
  byId("investments-body").textContent = "Loading…";
  call(
    "getInvestments",
    [],
    function (d) {
      investments = d;
      drawInvestments();
    },
    "investments-body",
  );
}

function loadHome() {
  if (home) {
    drawHome();
    return;
  }
  byId("home-body").textContent = "Loading…";
  call(
    "getHome",
    [],
    function (d) {
      home = d;
      drawHome();
    },
    "home-body",
  );
}

function change(n: number) {
  return (n > 0.5 ? "+" : n < -0.5 ? "−" : "") + money.format(Math.abs(n));
}
function longDate(d: string) {
  return MONTH_SHORT[Number(d.slice(5, 7)) - 1] + " " + Number(d.slice(8, 10)) + ", " + d.slice(0, 4);
}
function ownerName(o: string) {
  return o === "kids" ? "Kids" : o.charAt(0).toUpperCase() + o.slice(1);
}

// A page's submenu: one button per part, the part shown pressed; choosing one redraws the page on that part.
function subtabs(parent: HTMLElement, label: string, parts: string[][], current: string, pick: (part: string) => void) {
  var box = el("div", null, "subtabs");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", label);
  parts.forEach(function (t) {
    var b = button(t[1], function () {
      pick(t[0]);
    });
    b.setAttribute("aria-pressed", String(current === t[0]));
    box.appendChild(b);
  });
  parent.appendChild(box);
  return box;
}

function panel(parent: HTMLElement, title: string, lead?: string | null) {
  var p = el("section", null, "panel");
  p.appendChild(el("h3", title));
  if (lead) p.appendChild(el("p", lead, "support"));
  parent.appendChild(p);
  return p;
}

// A page's figures at a glance, as the Budget and Equity heads draw them: what each is above, the number below.
function figures(parent: HTMLElement, items: ([number, string] | null)[]) {
  var box = el("div", null, "w-figures");
  items.forEach(function (f) {
    if (!f) return;
    var c = el("div", null, "w-figure a2-card");
    c.appendChild(el("span", f[1].charAt(0).toUpperCase() + f[1].slice(1), "what a2-label"));
    c.appendChild(el("span", money.format(f[0]).replace("-", "−"), "num a2-big" + (f[0] < -0.5 ? " neg" : "")));
    box.appendChild(c);
  });
  parent.appendChild(box);
}

// A table for the Investments and Home pages. Every titled column sorts: choosing a heading sorts by it
// (numbers largest first, text A to Z), choosing it again reverses. Rows sort within their group (the rows
// between two group rows), and a total row stays last in its group. A row can give its sort keys; else
// each cell's text is read (money, a percent, a date or words).
/** What a table row sorts by in each column: a number, a date's or words' text, or null (sorts last). */
type SortKey = string | number | null;
/** A table row, with the sort keys it gives (else its cells' text is read). */
type KeyedRow = HTMLTableRowElement & { _keys?: SortKey[] };

function wTable(parent: HTMLElement, headers: string[], numeric: number[]) {
  var wrap = el("div", null, "w-scroll");
  var t = el("table", null, "w-table");
  var tr = document.createElement("tr");
  var state = { col: -1, dir: "" };
  var heads: HTMLElement[] = [];
  headers.forEach(function (h, i) {
    var th = el("th", null, numeric.indexOf(i) >= 0 ? "num" : "");
    if (h) {
      var b = button(
        h,
        function () {
          sortBy(i);
        },
        "sort",
      );
      b.setAttribute("aria-label", "Sort by " + h);
      th.appendChild(b);
      th.setAttribute("aria-sort", "none");
    }
    heads.push(th);
    tr.appendChild(th);
  });
  t.appendChild(tr);
  wrap.appendChild(t);
  parent.appendChild(wrap);
  function keyOf(r: KeyedRow, i: number): SortKey {
    if (r._keys && r._keys[i] !== undefined) return r._keys[i];
    var c = r.children[i];
    return sortKey(
      c
        ? c.firstChild && c.firstChild.nodeType === 1 && c.firstChild.firstChild
          ? c.firstChild.firstChild.textContent
          : c.textContent
        : "",
    );
  }
  function sortBy(i: number) {
    var dir = state.col === i ? (state.dir === "asc" ? "desc" : "asc") : numeric.indexOf(i) >= 0 ? "desc" : "asc";
    state = { col: i, dir: dir };
    heads.forEach(function (th, k) {
      var b = th.querySelector("button.sort");
      if (b) b.setAttribute("data-dir", k === i ? dir : "");
      if (th.hasAttribute("aria-sort"))
        th.setAttribute("aria-sort", k === i ? (dir === "asc" ? "ascending" : "descending") : "none");
    });
    var rows: KeyedRow[] = Array.prototype.slice.call(t.querySelectorAll("tr")).slice(1);
    var segments: KeyedRow[][] = [[]];
    rows.forEach(function (r) {
      if (r.classList.contains("group")) segments.push([r], []);
      else segments[segments.length - 1].push(r);
    });
    var sign = dir === "asc" ? 1 : -1;
    segments.forEach(function (seg) {
      if (seg.length && seg[0].classList.contains("group")) return;
      var totals = seg.filter(function (r) {
        return r.classList.contains("total");
      });
      var body = seg.filter(function (r) {
        return !r.classList.contains("total");
      });
      body.sort(function (a, b) {
        var x = keyOf(a, i),
          y = keyOf(b, i);
        if (x === y) return 0;
        if (x === null) return 1;
        if (y === null) return -1;
        return (x < y ? -1 : 1) * sign;
      });
      seg.length = 0;
      Array.prototype.push.apply(seg, body.concat(totals));
    });
    segments.forEach(function (seg) {
      seg.forEach(function (r) {
        t.appendChild(r);
      });
    });
  }
  return {
    row: function (cells: (string | Node)[], cls?: string, keys?: SortKey[]) {
      var r: KeyedRow = document.createElement("tr");
      if (cls) r.className = cls;
      if (keys) r._keys = keys;
      cells.forEach(function (c, i) {
        var td = cell(r, c instanceof Node ? "" : c, numeric.indexOf(i) >= 0 ? "num" : "");
        if (c instanceof Node) td.appendChild(c);
      });
      t.appendChild(r);
      return r;
    },
  };
}

// What a table cell sorts by: a number for money (a sign and commas allowed), a percent or a count, a
// date's ISO text for "Nov 9, 2026", else its words in lower case; a blank or a dash sorts last.
function sortKey(text: string | null): SortKey {
  var x = String(text).trim();
  if (!x || x === "–" || x === "-") return null;
  var n = x
    .replace(/[$,%\s]/g, "")
    .replace(/^\+/, "")
    .replace(/^[\u2212-]/, "-");
  if (/^-?\d+(\.\d+)?$/.test(n)) return Number(n);
  var m = /^([A-Z][a-z]{2}) (\d{1,2}), (\d{4})$/.exec(x);
  if (m && MONTH_SHORT.indexOf(m[1]) >= 0)
    return m[3] + "-" + String(MONTH_SHORT.indexOf(m[1]) + 1).padStart(2, "0") + "-" + m[2].padStart(2, "0");
  return x.toLowerCase();
}

function niceTicks(lo: number, hi: number, count: number) {
  var span = hi - lo || Math.abs(hi) || 1;
  var step = Math.pow(10, Math.floor(Math.log10(span / count)));
  step =
    [1, 2, 2.5, 5, 10]
      .map(function (k) {
        return k * step;
      })
      .filter(function (s) {
        return span / s <= count;
      })[0] || step * 10;
  var ticks: number[] = [];
  for (var t = Math.floor(lo / step) * step; t <= hi + step * 0.001; t += step) ticks.push(Math.round(t * 100) / 100);
  if (ticks[ticks.length - 1] < hi) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function compactMoney(n: number) {
  var a = Math.abs(n),
    s = n < 0 ? "−" : "";
  if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return s + "$" + Math.round(a / 1e3) + "k";
  return s + "$" + Math.round(a);
}

// A line chart over dates: series [{name, color, points: [[date, value]]}], one y axis, a crosshair and a
// tooltip with every series at the date nearest the pointer, each line labeled at its end.
/** A line on a chart: its name and color, and its [date, value] points in date order. */
interface ChartSeries {
  name: string;
  color: string;
  points: [string, number][];
}

function lineChart(
  parent: HTMLElement,
  series: ChartSeries[],
  opts: { label: string; now?: string; nowLabel?: string; dateName?: (d: string) => string },
) {
  var NS = "http://www.w3.org/2000/svg";
  var dates: string[] = [];
  series.forEach(function (s) {
    s.points.forEach(function (p) {
      if (dates.indexOf(p[0]) < 0) dates.push(p[0]);
    });
  });
  dates.sort();
  if (dates.length < 2) {
    parent.appendChild(el("p", "Not enough dates to draw yet.", "support"));
    return;
  }
  var t = function (d: string) {
    return Date.parse(d.length === 7 ? d + "-15" : d);
  };
  var lo = 0,
    hi = 0;
  series.forEach(function (s) {
    s.points.forEach(function (p) {
      lo = Math.min(lo, p[1]);
      hi = Math.max(hi, p[1]);
    });
  });
  var ticks = niceTicks(lo, hi, 5);
  lo = ticks[0];
  hi = ticks[ticks.length - 1];
  var W = Math.max(520, Math.round(parent.clientWidth || 760)),
    H = 280,
    L = 56,
    R = 110,
    T = 12,
    B = 250;
  var t0 = t(dates[0]),
    t1 = t(dates[dates.length - 1]);
  var x = function (d: string) {
    return L + ((t(d) - t0) / (t1 - t0)) * (W - L - R);
  };
  var y = function (v: number) {
    return T + ((hi - v) / (hi - lo)) * (B - T);
  };
  var box = el("div", null, "w-chart-box");
  var svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("class", "w-chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", opts.label);
  var add = function (name: string, attrs: Record<string, string | number>, text?: string, into?: Element) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, String(attrs[k]));
    });
    if (text !== undefined) e.textContent = text;
    (into || svg).appendChild(e);
    return e;
  };
  ticks.forEach(function (v) {
    add("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: v === 0 ? "zero" : "grid" });
    add("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", class: "axis" }, compactMoney(v));
  });
  var y0 = Number(dates[0].slice(0, 4)),
    y1 = Number(dates[dates.length - 1].slice(0, 4));
  var every = Math.max(1, Math.ceil((y1 - y0 + 1) / 10));
  for (var yr = y0 + 1; yr <= y1; yr++) {
    if ((yr - y0) % every) continue;
    var xx = x(yr + "-01-01");
    add("text", { x: xx, y: B + 18, "text-anchor": "middle", class: "axis" }, String(yr));
  }
  if (opts.now && opts.now > dates[0] && opts.now < dates[dates.length - 1]) {
    add("line", { x1: x(opts.now), x2: x(opts.now), y1: T, y2: B, class: "now" });
    add("text", { x: x(opts.now) + 4, y: B - 6, class: "axis" }, opts.nowLabel || "today");
  }
  var ends: { y: number; x: number; text: string; color: string }[] = [];
  series.forEach(function (s) {
    var d = s.points
      .map(function (p, i) {
        return (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1);
      })
      .join(" ");
    add("path", { d: d, class: "line", stroke: s.color });
    var last = s.points[s.points.length - 1];
    if (last) ends.push({ y: y(last[1]), x: x(last[0]), text: s.name, color: s.color });
  });
  ends.sort(function (a, b) {
    return a.y - b.y;
  });
  for (var i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
  ends.forEach(function (e) {
    add("text", { x: e.x + 6, y: e.y + 4, class: "lbl" }, e.text);
  });
  var cross = add("line", { x1: 0, x2: 0, y1: T, y2: B, class: "cross", visibility: "hidden" });
  var dots = series.map(function (s) {
    return add("circle", { r: 4, fill: s.color, stroke: "var(--surface-low)", "stroke-width": 2, visibility: "hidden" });
  });
  var hit = add("rect", { x: L, y: T, width: W - L - R, height: B - T, fill: "transparent" });
  var tip = el("div", null, "w-tip");
  tip.hidden = true;
  var byDate = series.map(function (s) {
    var m: Record<string, number> = {};
    s.points.forEach(function (p) {
      m[p[0]] = p[1];
    });
    return m;
  });
  var show = function (ev: MouseEvent) {
    var r = svg.getBoundingClientRect();
    var px = ((ev.clientX - r.left) / r.width) * W;
    var best = dates[0];
    dates.forEach(function (d) {
      if (Math.abs(x(d) - px) < Math.abs(x(best) - px)) best = d;
    });
    cross.setAttribute("x1", String(x(best)));
    cross.setAttribute("x2", String(x(best)));
    cross.setAttribute("visibility", "visible");
    tip.innerHTML = "";
    tip.appendChild(el("div", opts.dateName ? opts.dateName(best) : longDate(best), "strong"));
    series.forEach(function (s, n) {
      var v = byDate[n][best];
      if (v === undefined) {
        dots[n].setAttribute("visibility", "hidden");
        return;
      }
      dots[n].setAttribute("cx", String(x(best)));
      dots[n].setAttribute("cy", String(y(v)));
      dots[n].setAttribute("visibility", "visible");
      var row = el("div", null, "row");
      var name = el("span");
      var key = el("i", null, "key");
      key.style.background = s.color;
      name.appendChild(key);
      name.appendChild(document.createTextNode(s.name));
      row.appendChild(name);
      row.appendChild(el("span", money.format(v).replace("-", "−")));
      tip.appendChild(row);
    });
    tip.hidden = false;
    var left = (x(best) / W) * r.width;
    tip.style.left = Math.min(Math.max(left + 12, 0), r.width - tip.offsetWidth) + "px";
    tip.style.top = "8px";
  };
  hit.addEventListener("mousemove", show as EventListener);
  hit.addEventListener("mouseleave", function () {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
    dots.forEach(function (d) {
      d.setAttribute("visibility", "hidden");
    });
  });
  box.appendChild(svg);
  box.appendChild(tip);
  parent.appendChild(box);
  if (series.length > 1) {
    var legend = el("div", null, "w-legend");
    series.forEach(function (s) {
      var item = el("span");
      var key = el("i", null, "key");
      key.style.background = s.color;
      item.appendChild(key);
      item.appendChild(document.createTextNode(s.name));
      legend.appendChild(item);
    });
    parent.appendChild(legend);
  }
}

function drawInvestments() {
  var d = investments!, // drawn once loaded
    body = byId("investments-body");
  body.innerHTML = "";
  if (!d.asOf) {
    body.appendChild(el("p", "No investment values yet: run compute after the holdings are in.", "support"));
    return;
  }
  subtabs(body, "Investments", INVEST_PARTS, investView, function (v) {
    investView = v;
    drawInvestments();
  });
  var head = el("div", null, "w-head");
  head.appendChild(
    el(
      "h2",
      (
        {
          overview: "Investments",
          history: "Over time",
          allocation: "Allocation",
          accounts: "Accounts",
          lots: "Lots and sales",
        } as Record<string, string>
      )[investView],
    ),
  );
  head.appendChild(el("span", "as of " + longDate(d.asOf), "support"));
  body.appendChild(head);
  if (investView === "history") {
    drawOverTime(body, d);
    return;
  }
  if (investView === "allocation") {
    drawAllocation(body, d);
    return;
  }
  if (investView === "accounts") {
    drawAccounts(body, d);
    return;
  }
  if (investView === "lots") {
    drawLots(body, d);
    return;
  }
  var parts: Record<string, number> = {};
  d.owners.forEach(function (o) {
    Object.keys(o.parts).forEach(function (k) {
      parts[k] = (parts[k] || 0) + o.parts[k];
    });
  });
  var worth = d.owners.reduce(function (s, o) {
    return s + o.total;
  }, 0);
  figures(body, [
    [worth, "net worth"],
    [d.total, "in investment accounts"],
    parts.home_equity ? [parts.home_equity, "home equity"] : null,
    parts.other_debt ? [parts.other_debt, "other debt"] : null,
  ]);

  var grid = el("div", null, "w-grid");
  body.appendChild(grid);
  var byTax = panel(grid, "By tax treatment", "Every investment account today, 529s included.");
  var bars = el("div", null, "w-bars");
  var widest = Math.max.apply(
    null,
    d.buckets
      .map(function (b) {
        return b.value;
      })
      .concat([1]),
  );
  d.buckets.forEach(function (b) {
    bars.appendChild(el("span", b.label, "name"));
    var track = el("span", null, "track");
    var fill = el("i");
    fill.style.width = Math.max(0, (b.value / widest) * 100) + "%";
    fill.style.background = BUCKET_SERIES[b.key] ? "var(--series-" + BUCKET_SERIES[b.key] + ")" : "var(--outline)";
    track.appendChild(fill);
    track.title = b.label + ": " + money.format(b.value) + ", " + pct.format(b.value / d.total);
    bars.appendChild(track);
    bars.appendChild(el("span", money.format(b.value), "num"));
    bars.appendChild(el("span", pct.format(b.value / d.total), "share"));
  });
  byTax.appendChild(bars);

  var adults = d.owners
    .filter(function (o) {
      return o.owner !== "kids";
    })
    .map(function (o) {
      return ownerName(o.owner);
    });
  var byOwner = panel(
    grid,
    "By owner",
    "Joint accounts and the home split evenly between " +
      (adults.join(" and ") || "the adults") +
      "; the kids' column is their own accounts.",
  );
  var cols: [string, (o: OwnerRow) => number][] = [
    [
      "Investments",
      function (o) {
        return Object.keys(o.parts)
          .filter(function (k) {
            return k !== "home_equity" && k !== "other_debt";
          })
          .reduce(function (s, k) {
            return s + o.parts[k];
          }, 0);
      },
    ],
    [
      "Home equity",
      function (o) {
        return o.parts.home_equity || 0;
      },
    ],
    [
      "Debt",
      function (o) {
        return o.parts.other_debt || 0;
      },
    ],
    [
      "Net worth",
      function (o) {
        return o.total;
      },
    ],
  ];
  if (!parts.other_debt) cols.splice(2, 1);
  var ot = wTable(
    byOwner,
    ["Owner"].concat(
      cols.map(function (c) {
        return c[0];
      }),
    ),
    cols.map(function (_, i) {
      return i + 1;
    }),
  );
  d.owners.forEach(function (o) {
    ot.row(
      [ownerName(o.owner)].concat(
        cols.map(function (c) {
          return money.format(c[1](o)).replace("-", "−");
        }),
      ),
    );
  });
  var buckets = panel(byOwner, "By tax treatment and owner");
  var keys = d.buckets.map(function (b) {
    return b.key;
  });
  var bt = wTable(
    buckets,
    ["Account type"].concat(
      d.owners.map(function (o) {
        return ownerName(o.owner);
      }),
    ),
    d.owners.map(function (_, i) {
      return i + 1;
    }),
  );
  keys.forEach(function (k) {
    bt.row(
      [PART_NAMES[k] || k].concat(
        d.owners.map(function (o) {
          return o.parts[k] ? money.format(o.parts[k]) : "–";
        }),
      ),
    );
  });
  buckets.style.border = "0";
  buckets.style.padding = "12px 0 0";
}

// Over time: any line the model offers (totals, an owner, a tax bucket, an account), up to eight at once. Each
// chosen line keeps its color while it stays chosen, so taking one off doesn't repaint the rest.
var overTime: Record<string, number> | null = null, // each chosen line's series number, by key
  overRange = "all";
var OVER_GROUPS: string[][] = [
  ["total", "Totals"],
  ["owner", "By owner"],
  ["bucket", "By tax treatment"],
  ["account", "Accounts"],
];
var OVER_RANGES: string[][] = [
  ["all", "All"],
  ["10", "10 years"],
  ["5", "5 years"],
  ["1", "1 year"],
];
var OVER_MAX = 8;

function overLabel(l: ValueLine) {
  if (l.group === "owner") return ownerName(l.owner);
  if (l.group === "account") return l.label + (l.owner ? " (" + ownerName(l.owner) + ")" : "");
  return l.label;
}

function drawOverTime(body: HTMLElement, d: WealthPage) {
  var box = el("div", null, "over-time");
  body.appendChild(box);
  fillOverTime(box, d);
}

function refreshOverTime() {
  // redraw the part in place: the page doesn't jump
  var box = document.querySelector<HTMLElement>("#investments-body .over-time");
  if (!box) {
    drawInvestments();
    return;
  }
  var y = window.scrollY;
  box.innerHTML = "";
  fillOverTime(box, investments!);
  window.scrollTo(0, y);
}

function fillOverTime(body: HTMLElement, d: WealthPage) {
  var byKey: Record<string, ValueLine> = {};
  d.lines.forEach(function (l) {
    byKey[l.key] = l;
  });
  if (!overTime) {
    overTime = {};
    ["net_worth", "invested", "home_equity"]
      .filter(function (k) {
        return byKey[k];
      })
      .forEach(function (k, i) {
        overTime![k] = i + 1;
      });
  }
  Object.keys(overTime!).forEach(function (k) {
    if (!byKey[k]) delete overTime![k];
  });
  var chosen = Object.keys(overTime!).sort(function (a, b) {
    return overTime![a] - overTime![b];
  });
  var from = "";
  if (overRange !== "all") {
    var cut = new Date(d.asOf + "T00:00:00");
    cut.setFullYear(cut.getFullYear() - Number(overRange));
    from = cut.toISOString().slice(0, 10);
  }
  var inRange = function (l: ValueLine) {
    return l.points.filter(function (p) {
      return p[0] >= from;
    });
  };

  var bar = el("div", null, "r-toolbar over-bar");
  var seg = el("div", null, "segmented");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "How far back");
  OVER_RANGES.forEach(function (r) {
    var b = button(r[1], function () {
      overRange = r[0];
      refreshOverTime();
    });
    b.setAttribute("aria-pressed", String(overRange === r[0]));
    seg.appendChild(b);
  });
  bar.appendChild(seg);
  if (chosen.length)
    bar.appendChild(
      button("Clear", function () {
        overTime = {};
        refreshOverTime();
      }),
    );
  body.appendChild(bar);
  var cols = el("div", null, "over-cols");
  body.appendChild(cols);
  var main = el("div", null, "over-main");
  cols.appendChild(main);

  var chart = el("section", null, "panel");
  chart.appendChild(el("h3", chosen.length ? "Value over time" : "Choose lines below"));
  chart.appendChild(
    el(
      "p",
      "Month-ends from the first reading, and today. Home equity counts each home at its purchase price " +
        "(the home_value_basis setting) less what is owed on it; an account's line is all its parts together.",
      "support",
    ),
  );
  main.appendChild(chart);
  var series = chosen
    .map(function (k) {
      return { name: overLabel(byKey[k]), color: "var(--series-" + overTime![k] + ")", points: inRange(byKey[k]) };
    })
    .filter(function (s) {
      return s.points.length;
    });
  if (series.length) {
    lineChart(chart, series, {
      label:
        "Value over time: " +
        series
          .map(function (s) {
            return s.name;
          })
          .join(", "),
    });
    var t = wTable(chart, ["Line", "From", "Then", "Now", "Change"], [2, 3, 4]);
    series.forEach(function (s) {
      var first = s.points[0],
        last = s.points[s.points.length - 1];
      var name = el("span", null, "over-name");
      var sw = el("i", null, "swatch");
      sw.style.background = s.color;
      name.appendChild(sw);
      name.appendChild(document.createTextNode(s.name));
      t.row(
        [
          name,
          longDate(first[0]),
          money.format(first[1]),
          money.format(last[1]),
          (last[1] - first[1] >= 0 ? "+" : "−") + money.format(Math.abs(last[1] - first[1])),
        ],
        "",
        [s.name.toLowerCase(), first[0], first[1], last[1], last[1] - first[1]],
      );
    });
  } else if (chosen.length) {
    chart.appendChild(el("p", "Nothing in this range.", "support"));
  }

  var pick = panel(cols, "Lines", "Up to " + OVER_MAX + " at once.");
  pick.classList.add("over-picks");
  OVER_GROUPS.forEach(function (g) {
    var mine = d.lines.filter(function (l) {
      return l.group === g[0];
    });
    if (!mine.length) return;
    pick.appendChild(el("h4", g[1], "over-group"));
    var row = el("div", null, "line-picks");
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", g[1]);
    mine.forEach(function (l) {
      var on = overTime![l.key] !== undefined;
      var b = el("button", null, "line-pick");
      b.type = "button";
      b.setAttribute("aria-pressed", String(on));
      var sw = el("i", null, "swatch");
      if (on) sw.style.background = "var(--series-" + overTime![l.key] + ")";
      b.appendChild(sw);
      b.appendChild(document.createTextNode(overLabel(l)));
      b.disabled = !on && chosen.length >= OVER_MAX;
      if (b.disabled) b.title = OVER_MAX + " lines at most: take one off first";
      b.addEventListener("click", function () {
        if (on) {
          delete overTime![l.key];
        } else {
          var used = Object.keys(overTime!).map(function (k) {
            return overTime![k];
          });
          for (var slot = 1; used.indexOf(slot) >= 0; slot++) {
            /* the lowest free color */
          }
          overTime![l.key] = slot;
        }
        refreshOverTime();
      });
      row.appendChild(b);
    });
    pick.appendChild(row);
  });
}

function drawAllocation(body: HTMLElement, d: WealthPage) {
  var grid2 = el("div");
  body.appendChild(grid2);
  var al = panel(grid2, "Allocation", "Against the targets in allocation_targets. 529s are left out; I-Bonds count as US bonds.");
  var after = d.allocation.some(function (r) {
    return r.proposedShare !== null;
  });
  var at = wTable(al, ["Asset class", "Target", "Now", "", after ? "After" : "", "Off by"], [1, 2, 4, 5]);
  d.allocation.forEach(function (r) {
    var name = el("span", r.label);
    name.style.paddingLeft = r.depth * 16 + "px";
    var meter = el("span", null, "w-meter");
    var fill = el("i");
    var scale = Math.max(r.targetShare, r.currentShare, 0.0001) * 1.25;
    fill.style.width = Math.min(100, (r.currentShare / scale) * 100) + "%";
    var mark = el("b");
    mark.style.left = Math.min(100, (r.targetShare / scale) * 100) + "%";
    meter.appendChild(fill);
    meter.appendChild(mark);
    meter.title = r.label + ": now " + pct.format(r.currentShare) + ", target " + pct.format(r.targetShare);
    at.row(
      [
        name,
        pct.format(r.targetShare),
        pct.format(r.currentShare),
        meter,
        after && r.proposedShare !== null ? pct.format(r.proposedShare) : "",
        change(r.deltaValue),
      ],
      r.leaf ? (r.depth ? "sub" : "") : "group",
    );
  });

  al.classList.add("w-wide");
  var rb = panel(
    grid2,
    "Rebalance proposal",
    "Trades inside tax-advantaged accounts only, among funds each account holds " +
      "or has held in the last two years; taxable accounts and I-Bonds stay as they are. " +
      "Where it is worth the trades, bonds go to pre-tax money and the stocks expected to " +
      "grow most to Roth and HSA money. It also keeps each adult's own money (and half of what is joint) near " +
      "the target mix, where their accounts allow, so neither carries a class alone.",
  );
  rb.classList.add("w-wide");
  if (!d.trades.length) rb.appendChild(el("p", "Nothing to trade.", "support"));
  else {
    var tt = wTable(rb, ["Fund", "Class", "Now", "Proposed", "Trade"], [2, 3, 4]);
    var last = "";
    d.trades.forEach(function (t) {
      var group = t.account + "\u0000" + t.bucket;
      if (group !== last) {
        var kinds = t.bucket
          .split("+")
          .map(function (b) {
            return PART_NAMES[b] || b;
          })
          .join(" and ");
        tt.row([t.account + "  (" + kinds + ")", "", "", "", ""], "group");
        last = group;
      }
      var r = tt.row([t.symbol, t.assetClass, money.format(t.current), money.format(t.proposed), change(t.trade)]);
      (r.lastChild as HTMLElement).className = "num " + (t.trade > 0 ? "buy" : "sell");
    });
  }
  if (d.notes.length) {
    var ul = el("ul", null, "w-notes");
    d.notes.forEach(function (n) {
      ul.appendChild(el("li", n.replace(/^(rebalance|valuation): /, "")));
    });
    rb.appendChild(ul);
  }
}

function drawAccounts(body: HTMLElement, d: WealthPage) {
  var acc = el("section", null, "panel w-wide"); // the part's own heading names it, so the panel needs none
  acc.appendChild(
    el(
      "p",
      "“Carried” means part of the account had no price since its last reading, so it shows the value read then.",
      "support",
    ),
  );
  body.appendChild(acc);
  var ac = wTable(acc, ["Account", "Owner", "Type", "Value"], [3]);
  d.accounts.forEach(function (a) {
    var name = el("span", a.account);
    if (a.carried) name.appendChild(el("span", "carried", "tag"));
    if (a.tracked) name.appendChild(el("span", "estimated from prices", "tag"));
    ac.row([name, ownerName(a.owner || "joint"), PART_NAMES[a.bucket] || a.bucket, money.format(a.value)], "", [
      a.account.toLowerCase(),
      ownerName(a.owner || "joint").toLowerCase(),
      (PART_NAMES[a.bucket] || a.bucket).toLowerCase(),
      a.value,
    ]);
  });
  ac.row(["Total", "", "", money.format(d.total)], "total");
  var funds = el("details");
  funds.appendChild(el("summary", d.funds.length + " funds and positions"));
  // a fund with no asset class counts nowhere in the allocation: say so, and open the list to fix it here
  var blank = d.funds.filter(function (f) {
    return !f.assetClass && isFund(f) && !edited("security|" + f.symbol);
  });
  if (blank.length) {
    (funds as HTMLDetailsElement).open = true;
    acc.appendChild(
      statusTag({
        text:
          countOf(blank.length, "fund has", "funds have") +
          " no asset class, so the allocation and the rebalance leave " +
          (blank.length === 1 ? "it" : "them") +
          " out: " +
          blank
            .map(function (f) {
              return f.symbol;
            })
            .join(", "),
        tone: "check",
      }),
    );
  }
  var ft = wTable(funds, ["Fund", "Class", "Accounts", "Value"], [3]);
  d.funds.forEach(function (f) {
    var name = f.security && f.security.name ? f.symbol + ", " + f.security.name : f.symbol;
    ft.row([name, isFund(f) ? classControl(f, d) : f.assetClass || "", f.accounts.join(", "), money.format(f.value)], "", [
      name.toLowerCase(),
      (f.assetClass || "").toLowerCase(),
      f.accounts.join(", ").toLowerCase(),
      f.value,
    ]);
  });
  acc.appendChild(funds);
}

var TERM_NAMES: Record<string, string> = { short_term: "Short term", long_term: "Long term" };

// Tax lots: what each lot in a taxable account holds, and every lot part sold, as Form 8949 has it
function drawLots(body: HTMLElement, d: WealthPage) {
  if (!d.openLots.length && !d.lotSales.length) {
    body.appendChild(
      el("p", "No lots yet: compute works them out from vests, the plan's sales and each account's activity.", "support"),
    );
    return;
  }
  var sum = function (rows: OpenLot[], k: "value" | "gain" | "washAdded") {
    return rows.reduce(function (s, r) {
      return s + (r[k] || 0);
    }, 0);
  };
  var priced = d.openLots.filter(function (l) {
    return l.value !== null;
  });
  var carried = sum(d.openLots, "washAdded");
  figures(body, [
    [sum(priced, "value"), "in open lots"],
    [sum(priced, "gain"), "gain if sold today"],
    carried ? [carried, "wash-sale losses carried in their basis"] : null,
  ]);

  var op = el("section", null, "panel w-wide");
  op.appendChild(el("h3", "Open lots"));
  op.appendChild(
    el(
      "p",
      "Each lot still held in a taxable account, oldest first. Its basis includes any wash-sale loss it " +
        "carries, and its holding period then starts earlier than it was acquired. Selling at a loss " +
        "within 30 days of another purchase or vest of the same stock is a wash.",
      "support",
    ),
  );
  body.appendChild(op);
  if (!d.openLots.length) op.appendChild(el("p", "None.", "support"));
  else {
    var ot = wTable(op, ["Acquired", "Held from", "Shares", "Basis", "Value", "Gain", "If sold today"], [2, 3, 4, 5]);
    var last = "";
    d.openLots.forEach(function (l) {
      var group = l.account + " " + l.symbol;
      if (group !== last) {
        ot.row([group, "", "", "", "", "", ""], "group");
        last = group;
      }
      var when = el("span", longDate(l.acquired));
      if (l.washAdded) when.appendChild(el("span", "wash", "tag"));
      ot.row([
        when,
        l.holdingFrom !== l.acquired ? longDate(l.holdingFrom) : "",
        l.shares.toFixed(3),
        money.format(l.basis),
        l.value === null ? "–" : money.format(l.value),
        l.gain === null ? "–" : change(l.gain),
        TERM_NAMES[l.term] || l.term,
      ]);
    });
  }

  var sold = el("section", null, "panel w-wide");
  sold.appendChild(el("h3", "Sales by year"));
  sold.appendChild(
    el(
      "p",
      "Gains as Form 8949 reports them, wash sales worked out across every account. A wash (code W) " +
        "disallows the loss for the shares replaced and moves it into their basis.",
      "support",
    ),
  );
  body.appendChild(sold);
  if (!d.lotYears.length) sold.appendChild(el("p", "No sales yet.", "support"));
  else {
    var yt = wTable(sold, ["Year", "Lot parts sold", "Short-term gain", "Long-term gain", "Wash sales disallowed"], [1, 2, 3, 4]);
    d.lotYears.forEach(function (y) {
      yt.row([y.year, String(y.sales), change(y.shortTerm), change(y.longTerm), y.disallowed ? money.format(y.disallowed) : "–"]);
    });
    d.lotYears.forEach(function (y, i) {
      var box = el("details");
      box.open = i === 0;
      box.appendChild(el("summary", y.year + ": each lot part sold"));
      var st = wTable(
        box,
        ["Sold", "Acquired", "Held from", "Shares", "Proceeds", "Basis", "Code", "Adjustment", "Gain", "Term"],
        [3, 4, 5, 7, 8],
      );
      d.lotSales
        .filter(function (x) {
          return x.year === y.year;
        })
        .forEach(function (x) {
          st.row([
            longDate(x.saleDate),
            longDate(x.acquired),
            x.holdingFrom !== x.acquired ? longDate(x.holdingFrom) : "",
            x.shares.toFixed(3),
            money.format(x.proceeds),
            money.format(x.basis),
            x.code,
            x.adjustment ? money.format(x.adjustment) : "",
            change(x.gain),
            TERM_NAMES[x.term] || x.term,
          ]);
        });
      sold.appendChild(box);
    });
  }
  if (d.lotNotes.length) {
    var ul = el("ul", null, "w-notes");
    d.lotNotes.forEach(function (n) {
      ul.appendChild(el("li", n.replace(/^lots:? /, "")));
    });
    sold.appendChild(ul);
  }
}

// Whether a row is a fund the ledger's securities can describe: not an account's total, nor cash.
function isFund(f: FundValue) {
  return !/^\(.* total\)$/.test(f.symbol) && f.symbol.toLowerCase() !== "cash";
}

// The asset class codes the allocation targets use: each leaf of the tree, with its label.
function classCodes(d: WealthPage): [string, string][] {
  return d.allocation
    .filter(function (a) {
      return a.leaf;
    })
    .map(function (a) {
      return [a.node, a.label] as [string, string];
    });
}

// A fund's asset class, changed where it shows: one code or a blend ("SDT 60, BT 40"), each code a class of the
// allocation targets, staged as a security edit that carries the fund's row as the ledger has it.
function classControl(f: FundValue, d: WealthPage): HTMLElement {
  var box = el("span", null, "class-control");
  var t = "security|" + f.symbol;
  var codes = classCodes(d);
  var label: Record<string, string> = {};
  codes.forEach(function (c) {
    label[c[0]] = c[1];
  });
  var draw = function () {
    box.textContent = "";
    var e = edited(t);
    var now = e ? String((e.edit as Record<string, string>).asset_class) : f.assetClass;
    if (now) {
      var shown = el("span", now, "class-now");
      shown.title = now
        .split(",")
        .map(function (part) {
          var code = part.trim().split(/[\s:]+/)[0];
          return label[code] ? part.trim() + ": " + label[code] : part.trim();
        })
        .join("\n");
      box.appendChild(shown);
    } else box.appendChild(el("span", "unclassified", "class-none"));
    if (e) box.appendChild(el("span", stagedText(t) ? "waiting to be saved" : "applies at the next run", "tag warn"));
    var change = button(
      now ? "Change" : "Set",
      function () {
        box.textContent = "";
        var list = el("datalist");
        list.id = "classes-" + f.symbol.replace(/[^a-z0-9]+/gi, "-");
        codes.forEach(function (c) {
          var o = document.createElement("option");
          o.value = c[0];
          o.label = c[1];
          list.appendChild(o);
        });
        var input = textInput(now, "");
        input.setAttribute("list", list.id);
        input.setAttribute("aria-label", "Asset class of " + f.symbol);
        input.placeholder = "SDT, or SDT 60, BT 40";
        var error = el("div", "", "error");
        box.appendChild(input);
        box.appendChild(list);
        box.appendChild(
          button(
            "Keep",
            function () {
              var v = input.value.trim();
              var parts = v.split(",").map(function (p) {
                return p.trim().split(/[\s:]+/)[0];
              });
              var unknown = parts.filter(function (p) {
                return p && !(p in label);
              });
              if (!v || unknown.length) {
                error.textContent = v
                  ? "Not a class in the allocation targets: " + unknown.join(", ")
                  : "Give a class, or a blend like SDT 60, BT 40.";
                return;
              }
              if (v === f.assetClass) {
                if (staged[t]) unstage(t);
                draw();
                return;
              }
              var s = f.security;
              stage(
                {
                  kind: "security",
                  symbol: f.symbol,
                  asset_class: v,
                  was: s
                    ? {
                        name: s.name,
                        asset_class: s.asset_class,
                        expense_ratio: s.expense_ratio,
                        quote: s.quote,
                        wash_group: s.wash_group,
                      }
                    : null,
                },
                "security " + f.symbol + ": asset class " + v,
              );
              draw();
            },
            "text",
          ),
        );
        if (staged[t])
          box.appendChild(
            button(
              "Undo",
              function () {
                unstage(t);
                draw();
              },
              "text",
            ),
          );
        box.appendChild(button("Cancel", draw, "text"));
        box.appendChild(error);
        input.focus();
      },
      "text",
    );
    change.setAttribute("aria-label", (now ? "Change" : "Set") + " the asset class of " + f.symbol);
    box.appendChild(change);
  };
  draw();
  return box;
}
