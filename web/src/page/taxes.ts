// Taxes: this year, history, the forms, the plan and scenarios.

var taxes: TaxPage | null = null,
  taxYear = "";
var PLAN_NAME: Record<string, string> = { federal: "Federal", wisconsin: "Wisconsin" };

function loadTaxes() {
  if (taxes) {
    drawTaxes();
    return;
  }
  byId("taxes-body").textContent = "Loading…";
  call(
    "getTaxes",
    [],
    function (data) {
      taxes = data;
      drawTaxes();
    },
    "taxes-body",
  );
}

// Taxes over the years: one column per filed year, its income split where it came from, how it was taxed,
// or where it went, in dollars or as shares of the whole; hover a column for its parts and their change on the
// year before, click it to open that year below when the page has it.
var historyView: Record<string, string> = { split: "went", measure: "Dollars", jurisdiction: "federal" };
var HISTORY_SPLITS: Record<string, string> = { went: "Where it went", came: "Where it came from", taxed: "How it was taxed" };
var HISTORY_RETURNS: Record<string, string> = { federal: "Federal", state: "State" };
var STATE_CODES: Record<string, string> = { pa: "PA", wi: "WI" };
var STATE_NAMES_BY_CODE: Record<string, string> = { pa: "Pennsylvania", wi: "Wisconsin" };
var PART_COLOR: Record<string, number> = {
  "Kept after income tax": 1,
  "Federal income tax": 7,
  "Other federal taxes": 5,
  "State income tax": 2,
  "Pennsylvania income tax": 2,
  "Wisconsin income tax": 2,
  Compensation: 1,
  "Wisconsin income": 1,
  "Interest & dividends": 4,
  "Capital gains": 3,
  "Other income": 6,
  "Taxable income": 1,
  Deducted: 6,
  Adjustments: 4,
  Wages: 1,
};
var CREDIT_PART = "Credits (part of what was kept)";

function drawTaxHistory(body: HTMLElement) {
  var all = taxes!.history;
  var state = function (r: HistoryPart) {
    return r.jurisdiction !== "federal";
  };
  var returns = ["federal", "state"].filter(function (j) {
    return all.some(function (r) {
      return j === "state" ? state(r) : r.jurisdiction === "federal";
    });
  });
  if (returns.indexOf(historyView.jurisdiction) < 0) historyView.jurisdiction = returns[0];
  var mine = all.filter(function (r) {
    return historyView.jurisdiction === "state" ? state(r) : r.jurisdiction === "federal";
  });
  var splits = Object.keys(HISTORY_SPLITS).filter(function (k) {
    return mine.some(function (r) {
      return r.split === k;
    });
  });
  if (splits.indexOf(historyView.split) < 0) historyView.split = splits[0];
  var rows = mine.filter(function (r) {
    return r.split === historyView.split;
  });
  var people = Array.from(
    new Set(
      all
        .filter(function (r) {
          return /^Wages, /.test(r.part);
        })
        .map(function (r) {
          return r.part;
        }),
    ),
  );
  var colorOf = function (part: string) {
    var n = PART_COLOR[part] || (people.indexOf(part) >= 0 ? people.indexOf(part) + 1 : 8);
    return "var(--series-" + n + ")";
  };
  var section = el("section", null, "panel tax-history");
  var head = el("div", null, "tax-head");
  head.appendChild(el("h2", "Taxes over the years"));
  section.appendChild(head);
  var controls = el("div", null, "history-controls");
  var redraw = function () {
    drawTaxes();
  };
  controls.appendChild(
    pill(
      splits.map(function (k) {
        return HISTORY_SPLITS[k];
      }),
      HISTORY_SPLITS[historyView.split],
      function (label: string) {
        historyView.split = Object.keys(HISTORY_SPLITS).filter(function (k) {
          return HISTORY_SPLITS[k] === label;
        })[0];
        redraw();
      },
      "Split by",
    ),
  );
  controls.appendChild(
    pill(
      ["Dollars", "Share of income"],
      historyView.measure,
      function (m: string) {
        historyView.measure = m;
        redraw();
      },
      "Measure",
    ),
  );
  if (returns.length > 1)
    controls.appendChild(
      pill(
        returns.map(function (j) {
          return HISTORY_RETURNS[j];
        }),
        HISTORY_RETURNS[historyView.jurisdiction],
        function (label: string) {
          historyView.jurisdiction = returns.filter(function (j) {
            return HISTORY_RETURNS[j] === label;
          })[0];
          redraw();
        },
        "Return",
      ),
    );
  section.appendChild(controls);
  body.appendChild(section);
  if (!rows.length) {
    section.appendChild(el("p", "Nothing to show for this return yet.", "support"));
    return;
  }

  // a column per return a year has: one federal, or each state's side by side within its year
  var key = function (r: HistoryPart) {
    return r.year + "|" + r.jurisdiction;
  };
  var columns: { key: string; year: string; jurisdiction: string }[] = [];
  rows.forEach(function (r) {
    if (
      !columns.some(function (c) {
        return c.key === key(r);
      })
    )
      columns.push({ key: key(r), year: r.year, jurisdiction: r.jurisdiction });
  });
  columns.sort(function (a, b) {
    return a.year.localeCompare(b.year) || a.jurisdiction.localeCompare(b.jurisdiction);
  });
  var years = Array.from(
    new Set(
      columns.map(function (c) {
        return c.year;
      }),
    ),
  );
  var parts: string[] = []; // in the split's order, bottom of the column first
  rows
    .slice()
    .sort(function (a, b) {
      return a.order - b.order;
    })
    .forEach(function (r) {
      if (parts.indexOf(r.part) < 0) parts.push(r.part);
    });
  var legend = el("div", null, "legend");
  parts
    .slice()
    .reverse()
    .forEach(function (part) {
      var item = el("span", part);
      item.style.setProperty(
        "--swatch",
        part === CREDIT_PART ? "repeating-linear-gradient(135deg, var(--series-8) 0 2px, transparent 2px 5px)" : colorOf(part),
      );
      legend.appendChild(item);
    });
  section.appendChild(legend);
  var share = historyView.measure !== "Dollars";
  var at: Record<string, Record<string, HistoryPart>> = {};
  rows.forEach(function (r) {
    (at[key(r)] || (at[key(r)] = {}))[r.part] = r;
  });
  var totalOf = function (k: string) {
    var any = rows.filter(function (r) {
      return key(r) === k;
    })[0];
    return any ? any.total : 0;
  };
  var scaled = function (k: string, v: number) {
    return share ? (totalOf(k) ? (100 * v) / totalOf(k) : 0) : v;
  };
  var hi = 0,
    lo = 0;
  columns.forEach(function (c) {
    var up = 0,
      down = 0;
    parts.forEach(function (p) {
      var r = at[c.key][p];
      if (!r) return;
      var v = scaled(c.key, r.value);
      if (v > 0) up += v;
      else down += v;
    });
    hi = Math.max(hi, up);
    lo = Math.min(lo, down);
  });
  var step = share ? 20 : Math.pow(10, Math.floor(Math.log10(Math.max(hi - lo, 1) / 4)));
  if (!share)
    [1, 2, 2.5, 5, 10].some(function (k) {
      if ((hi - lo) / (step * k) <= 5) {
        step *= k;
        return true;
      }
      return false;
    });
  var top = share ? Math.max(100, Math.ceil(hi / step) * step) : Math.ceil((hi * 1.08) / step) * step;
  var bottom = lo < 0 ? lo * 1.2 : 0; // room for a loss below the line, without a whole empty step
  var NS = "http://www.w3.org/2000/svg";
  var svgEl = function (name: string, attrs: Record<string, string | number>, parent?: Element) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, String(attrs[k]));
    });
    if (parent) parent.appendChild(e);
    return e;
  };
  var several = historyView.jurisdiction === "state";
  var width = Math.max(section.clientWidth - 40, 360),
    height = 360 + (several ? 16 : 0);
  var m = { top: 22, right: 12, bottom: 26 + (several ? 16 : 0), left: 60 };
  var y = function (v: number) {
    return m.top + ((top - v) / Math.max(top - bottom, 1)) * (height - m.top - m.bottom);
  };
  var svg = svgEl("svg", {
    viewBox: "0 0 " + width + " " + height,
    class: "history-chart",
    role: "img",
    "aria-label": HISTORY_SPLITS[historyView.split] + " by year; the return for each year is below",
  });
  var defs = svgEl("defs", {}, svg);
  var pat = svgEl(
    "pattern",
    { id: "credit-hatch", width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" },
    defs,
  );
  svgEl("rect", { width: 5, height: 5, fill: "var(--surface-low)" }, pat);
  svgEl("line", { x1: 0, y1: 0, x2: 0, y2: 5, stroke: "var(--series-8)", "stroke-width": 2 }, pat);
  section.appendChild(svg);
  var short = function (v: number) {
    var a = Math.abs(v),
      sign = v < 0 ? "-" : "";
    return (
      sign + (a >= 1e6 ? "$" + (a / 1e6).toFixed(1) + "M" : a >= 1000 ? "$" + Math.round(a / 1000) + "k" : "$" + Math.round(a))
    );
  };
  var ticks: number[] = [];
  for (var v = 0; v <= top + 0.001; v += step) ticks.push(v);
  for (v = -step; v >= bottom - 0.001; v -= step) ticks.push(v);
  ticks.forEach(function (v) {
    svgEl("line", { x1: m.left, x2: width - m.right, y1: y(v), y2: y(v), class: v === 0 ? "zero" : "grid" }, svg);
    var t = svgEl("text", { x: m.left - 8, y: y(v) + 4, "text-anchor": "end", class: "axis" }, svg);
    t.textContent = share ? Math.round(v) + "%" : short(v);
  });
  var gap = (width - m.left - m.right) / years.length;
  var most = Math.max.apply(
    null,
    years.map(function (yr) {
      return columns.filter(function (c) {
        return c.year === yr;
      }).length;
    }),
  );
  var bw = Math.min(72, (gap * 0.6) / most - (most > 1 ? 4 : 0));
  var tip = el("div", null, "tip");
  tip.hidden = true;
  section.appendChild(tip);
  var opens = function (yr: string) {
    return !!yearReturns(yr);
  };
  var isProjected = function (yr: string) {
    return rows.some(function (r) {
      return r.year === yr && r.projected;
    });
  };
  years.forEach(function (yr, i) {
    var mineCols = columns.filter(function (c) {
      return c.year === yr;
    });
    var span = mineCols.length * bw + (mineCols.length - 1) * 8;
    var mid = m.left + gap * (i + 0.5);
    var yl = svgEl(
      "text",
      { x: mid, y: height - 6, "text-anchor": "middle", class: "axis" + (yr === historyYear ? " strong" : "") },
      svg,
    );
    yl.textContent = yr + (isProjected(yr) ? " (projected)" : "");
    mineCols.forEach(function (c, n) {
      var x = mid - span / 2 + n * (bw + 8),
        cx = x + bw / 2,
        up = 0,
        down = 0;
      var col = svgEl(
        "g",
        { class: "history-column" + (isProjected(yr) ? " projected" : "") + (yr === historyYear ? " chosen" : "") },
        svg,
      );
      parts.forEach(function (p) {
        var r = at[c.key][p];
        if (!r) return;
        var v = scaled(c.key, r.value);
        var y0 = v >= 0 ? y(up + v) : y(down),
          h = Math.abs(y(v >= 0 ? up : down) - y(v >= 0 ? up + v : down + v));
        svgEl(
          "rect",
          {
            x: x,
            width: bw,
            y: y0 + (v >= 0 ? 0 : 1),
            height: Math.max(h - 2, 1),
            fill: p === CREDIT_PART ? "url(#credit-hatch)" : colorOf(p),
            rx: 2,
          },
          col,
        );
        if (h > 18 && p !== CREDIT_PART && bw >= 34) {
          var label = svgEl("text", { x: cx, y: y0 + h / 2 + 4, "text-anchor": "middle", class: "inside" }, col);
          label.textContent = share ? Math.round(v) + "%" : short(r.value);
        }
        if (v >= 0) up += v;
        else down += v;
      });
      var t = svgEl("text", { x: cx, y: y(up) - 6, "text-anchor": "middle", class: "lbl" }, col);
      if (share && historyView.split === "went") {
        var taxed = parts
          .filter(function (p) {
            return p !== "Kept after income tax" && p !== CREDIT_PART;
          })
          .reduce(function (sum, p) {
            return sum + (at[c.key][p] ? at[c.key][p].value : 0);
          }, 0);
        t.textContent = Math.round((100 * taxed) / (totalOf(c.key) || 1)) + "% tax";
      } else if (!share) t.textContent = short(totalOf(c.key));
      if (several) {
        var st = svgEl("text", { x: cx, y: height - 24, "text-anchor": "middle", class: "axis state" }, col);
        st.textContent = STATE_CODES[c.jurisdiction] || c.jurisdiction.toUpperCase();
      }
      var hit = svgEl("rect", { x: x - 4, width: bw + 8, y: m.top, height: height - m.top - m.bottom, fill: "transparent" }, col);
      hit.addEventListener("mouseenter", function () {
        tip.textContent = "";
        var name = several ? STATE_NAMES_BY_CODE[c.jurisdiction] || c.jurisdiction : "Federal";
        tip.appendChild(el("div", yr + ", " + name + ": " + money.format(totalOf(c.key)), "strong"));
        var before = years[years.indexOf(yr) - 1],
          prevKey = before + "|" + c.jurisdiction;
        parts
          .slice()
          .reverse()
          .forEach(function (p) {
            var r = at[c.key][p];
            if (!r) return;
            var prev = at[prevKey] && at[prevKey][p] ? at[prevKey][p].value : null;
            var change =
              prev === null
                ? ""
                : " (" + (r.value - prev >= 0 ? "+" : "-") + money.format(Math.abs(r.value - prev)) + " on " + before + ")";
            tip.appendChild(
              el(
                "div",
                p + ": " + money.format(r.value) + (share ? ", " + Math.round(scaled(c.key, r.value)) + "%" : "") + change,
              ),
            );
          });
        if (isProjected(yr)) tip.appendChild(el("div", "Projected: the year so far and the rest of it as expected", "support"));
        if (opens(yr)) tip.appendChild(el("div", "Click for its return below", "support"));
        tip.hidden = false;
        var scale = svg.getBoundingClientRect().width / width;
        var left = (x + bw + 12) * scale;
        tip.style.left = (left + 300 > section.clientWidth ? Math.max(0, (x - 12) * scale - 290) : left) + "px";
        tip.style.top = svg.getBoundingClientRect().top - section.getBoundingClientRect().top + 20 + "px";
      });
      hit.addEventListener("mouseleave", function () {
        tip.hidden = true;
      });
      if (opens(yr)) {
        (hit as SVGElement).style.cursor = "pointer";
        hit.addEventListener("click", function () {
          historyYear = historyYear === yr ? "" : yr; // a second click on the open year closes it
          drawTaxes();
          var detail = document.getElementById("tax-year-detail");
          if (detail) detail.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
    });
  });
  section.appendChild(
    el(
      "p",
      several
        ? "Each year's state returns side by side, each its own income on that return. A year you lived in two states has a part-year return for each."
        : historyView.split === "went"
          ? "Each year's total income, split by where it went: federal and state income taxes after credits, and what was kept. The hatched part of what was kept is the tax that credits cancelled."
          : historyView.split === "came"
            ? "Each year's income on the return, by where it came from; a capital loss shows below the line, at the most the return could deduct."
            : "Each year's income, split into what was taxed and what was deducted before it.",
      "support",
    ),
  );
}

function signed(n: number) {
  return (n > 0 ? "Owe " : n < 0 ? "Refund " : "") + money.format(Math.abs(n));
}

function pill(options: string[], value: string, onpick: (option: string) => void, label: string) {
  var group = el("span", null, "segmented");
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", label);
  options.forEach(function (o) {
    var b = button(o.charAt(0).toUpperCase() + o.slice(1), function () {
      onpick(o);
    });
    b.setAttribute("aria-pressed", String(o === value));
    group.appendChild(b);
  });
  return group;
}

// Taxes in two views: this year's projected returns beside the withholding and what the projection assumes,
// or the history (filed years and this year's projection) with any year's return below when it is chosen.
var taxView = "year",
  historyYear = "";

function drawTaxes() {
  var body = byId("taxes-body");
  body.textContent = "";
  subtabs(body, "Taxes", PAGE_PARTS.taxes, taxView, function (v) {
    if (v.indexOf("edit:") === 0) openEdit(v.slice(5));
    else {
      taxView = v;
      drawTaxes();
    }
  });
  if (taxView === "history") {
    drawTaxHistoryView(body);
    return;
  }
  if (taxView === "8949") {
    drawForm8949(body);
    return;
  }
  if (!taxes!.years.length) {
    body.appendChild(el("p", "No projection yet: the next compute with this year's pay stubs fills this page in.", "support"));
    return;
  }
  var year = taxes!.years[0];
  var head = el("div", null, "tax-head");
  head.appendChild(el("h2", "Taxes for " + year.year));
  body.appendChild(head);
  body.appendChild(
    el(
      "p",
      year.filed
        ? "As filed: each line computed from the W-2s and other figures on the return, beside the line you filed."
        : "Projected from this year's pay stubs, vests and sales, and your estimates.",
      "support",
    ),
  );
  body.appendChild(bottomLines(year.forms));
  var side = el("div", null, "tax-side");
  var returns = el("div", null, "stacked");
  Object.keys(year.forms).forEach(function (form) {
    var panel = el("section", null, "panel");
    panel.appendChild(el("h2", form === "Form 1040" ? "Federal, Form 1040" : form));
    panel.appendChild(formTable(year.forms[form], year.filed, year.filed ? "Computed" : "From stubs", year.year));
    returns.appendChild(panel);
  });
  side.appendChild(returns);
  var aside = el("div", null, "stacked");
  if (taxes!.plan.length) aside.appendChild(planCard(taxes!.plan));
  if (taxes!.notes.length) {
    var notes = el("section", null, "panel");
    notes.appendChild(el("h2", "What the projection assumes"));
    var list = el("ul", null, "notes");
    taxes!.notes.forEach(function (n) {
      list.appendChild(el("li", n));
    });
    notes.appendChild(list);
    aside.appendChild(notes);
  }
  if (taxes!.totals.length > 1) aside.appendChild(scenariosPanel(taxes!.totals));
  if (taxes!.accounts.length) aside.appendChild(accountsPanel(taxes!.accounts));
  if (taxes!.law && taxes!.law.length) aside.appendChild(lawPanel(taxes!.law));
  side.appendChild(aside);
  body.appendChild(side);
}

// the cards with each return's bottom line: owed or refunded
function bottomLines(forms: Record<string, TaxLine[]>) {
  var summary = el("div", null, "cards tax-summary");
  Object.keys(forms).forEach(function (form) {
    var lines = forms[form] || [];
    var last = lines[lines.length - 1];
    if (!last || !/^(Refund|Amount owed)$/.test(last.label)) return;
    var v = last.label === "Refund" ? -last.value : last.value;
    var card = el("article", null, "card total " + (v > 0 ? "owe" : "refund"));
    card.appendChild(el("div", STATE_NAMES[form] || form, "support"));
    card.appendChild(el("div", signed(v), "big"));
    summary.appendChild(card);
  });
  return summary;
}

// the years a breakdown can open for: computed from their inputs (taxes.years) or as filed (taxes.filed)
function yearReturns(yr: string) {
  var computed = taxes!.years.filter(function (y) {
    return y.year === yr;
  })[0];
  if (computed) return { forms: computed.forms, filed: computed.filed, computed: true };
  var filed = taxes!.filed && taxes!.filed[yr];
  return filed ? { forms: filed, filed: false, computed: false } : null;
}

// Form 8949: each year's sales as the form lays them out, Part I short-term and Part II long-term, with
// the totals that carry to Schedule D beside the return as filed.
var f8949: F8949Page | null = null,
  f8949Year = "";
var FORM_PARTS: Record<string, string[]> = {
  short_term: ["Part I, short-term", "Schedule D line 7"],
  long_term: ["Part II, long-term", "Schedule D line 15"],
};

function centsText(n: number) {
  return cents.format(n).replace("-", "−");
}

function drawForm8949(body: HTMLElement) {
  if (!f8949) {
    var wait = el("p", "Loading…", "support");
    wait.id = "f8949-loading";
    body.appendChild(wait);
    call(
      "getForm8949",
      [],
      function (d) {
        f8949 = d;
        if (taxView === "8949") drawTaxes();
      },
      "f8949-loading",
    );
    return;
  }
  if (!f8949.years.length) {
    body.appendChild(
      el("p", "No sales yet: compute works out Form 8949 from the plan's sales and each account's activity.", "support"),
    );
    return;
  }
  if (
    !f8949.years.some(function (y) {
      return y.year === f8949Year;
    })
  )
    f8949Year = f8949.years[0].year;
  var y = f8949.years.filter(function (x) {
    return x.year === f8949Year;
  })[0];
  var pick = el("span", null, "segmented");
  pick.setAttribute("role", "group");
  pick.setAttribute("aria-label", "Year");
  f8949.years.forEach(function (x) {
    var b = button(x.year, function () {
      f8949Year = x.year;
      drawTaxes();
    });
    b.setAttribute("aria-pressed", String(x.year === f8949Year));
    pick.appendChild(b);
  });
  var head = el("div", null, "tax-head");
  head.appendChild(el("h2", "Form 8949 for " + y.year));
  head.appendChild(pick);
  body.appendChild(head);
  body.appendChild(
    el(
      "p",
      "Each sale as it goes on the form, wash sales worked out across every account. The box for each part " +
        "(A to F) comes from the 1099-B: A or D when the broker reported the basis to the IRS, B or E when it " +
        "didn't. A wash sale (code W) adds the disallowed loss back in column (g).",
      "support",
    ),
  );
  var gains: Record<string, number> = {};
  y.parts.forEach(function (p) {
    gains[p.term] = p.gain;
  });
  figures(body, [
    gains.short_term !== undefined ? [gains.short_term, "short-term gain (Part I)"] : null,
    gains.long_term !== undefined ? [gains.long_term, "long-term gain (Part II)"] : null,
    y.disallowed ? [y.disallowed, "wash-sale losses disallowed"] : null,
  ]);
  y.parts.forEach(function (p) {
    var names = FORM_PARTS[p.term];
    var panel = el("section", null, "panel w-wide f8949-part");
    panel.dataset.term = p.term;
    panel.appendChild(el("h3", names[0]));
    body.appendChild(panel);
    var t = wTable(
      panel,
      [
        "(a) Description",
        "(b) Acquired",
        "(c) Sold",
        "(d) Proceeds",
        "(e) Basis",
        "(f) Code",
        "(g) Adjustment",
        "(h) Gain or loss",
      ],
      [3, 4, 6, 7],
    );
    p.rows.forEach(function (r) {
      var what = el("span", +r.shares.toFixed(3) + " sh " + r.symbol);
      what.appendChild(el("span", r.account, "tag plain"));
      var acquired = el("span", r.acquired);
      if (r.holdingFrom && r.holdingFrom !== r.acquired) acquired.appendChild(el("span", "held from " + r.holdingFrom, "tag"));
      var basis = el("span", centsText(r.basis));
      if (r.reportedBasis !== null && Math.abs(r.reportedBasis - (r.basis - r.washCarried)) > 0.02) {
        var tag = el("span", "1099-B: " + centsText(r.reportedBasis), "tag warn");
        tag.title =
          "The plan reported a different basis for these shares: check the 1099-B, and use code B if the basis it shows is wrong";
        basis.appendChild(tag);
      }
      t.row(
        [
          what,
          acquired,
          r.saleDate,
          centsText(r.proceeds),
          basis,
          r.code,
          r.adjustment ? centsText(r.adjustment) : "",
          centsText(r.gain),
        ],
        "",
        [null, r.acquired, r.saleDate, r.proceeds, r.basis, r.code, r.adjustment, r.gain],
      );
    });
    t.row(
      [
        "Totals",
        "",
        "",
        centsText(p.proceeds),
        centsText(p.basis),
        "",
        p.adjustment ? centsText(p.adjustment) : "",
        centsText(p.gain),
      ],
      "total",
    );
    var line =
      names[1] +
      (p.filed === null
        ? y.filedOnFile
          ? ": nothing filed"
          : ": not filed yet"
        : " as filed: " +
          centsText(p.filed) +
          (Math.abs(p.filed - p.gain) > 0.5
            ? ", " +
              centsText(Math.abs(p.filed - p.gain)) +
              (p.filed > p.gain ? " more" : " less") +
              " than these sales " +
              "(the line also takes capital gain distributions, carryovers and any sale not in the ledger)"
            : ", the same as these sales"));
    panel.appendChild(el("p", line, "support f8949-filed"));
  });
}

function drawTaxHistoryView(body: HTMLElement) {
  if (taxes!.history && taxes!.history.length) drawTaxHistory(body);
  else body.appendChild(el("p", "No filed returns yet: return_facts fills this in.", "support"));
  if (!historyYear || !yearReturns(historyYear)) return;
  var r = yearReturns(historyYear)!;
  var projected = taxes!.history.some(function (h) {
    return h.year === historyYear && h.projected;
  });
  var head = el("div", null, "tax-head");
  head.id = "tax-year-detail";
  head.appendChild(el("h2", historyYear + (projected ? ", projected" : " return")));
  head.appendChild(
    button(
      "Close",
      function () {
        historyYear = "";
        drawTaxes();
      },
      "text",
    ),
  );
  body.appendChild(head);
  body.appendChild(
    el(
      "p",
      projected
        ? "Projected from this year's pay stubs, vests and sales, and your estimates."
        : r.computed && r.filed
          ? "Each line computed from the W-2s and other figures on the return, beside the line you filed."
          : r.computed
            ? "The whole year from its pay stubs."
            : "The lines as filed.",
      "support",
    ),
  );
  body.appendChild(bottomLines(r.forms));
  var two = el("div", null, "two");
  var states = el("div", null, "stacked");
  Object.keys(r.forms).forEach(function (form) {
    var panel = el("section", null, "panel");
    panel.appendChild(el("h2", form === "Form 1040" ? "Federal, Form 1040" : form));
    panel.appendChild(
      formTable(r.forms[form], r.computed && r.filed, r.computed && r.filed ? "Computed" : "Amount", historyYear),
    );
    (form === "Form 1040" ? two : states).appendChild(panel);
  });
  if (states.childNodes.length) two.appendChild(states);
  body.appendChild(two);
}

// For each return, the extra withholding per paycheck that lands the year on a refund, on no balance, and
// on owing, from the year so far plus the projection.
function planCard(plan: Row[]) {
  var card = el("article", null, "card plan");
  card.appendChild(el("h2", "Withholding for the rest of the year"));
  var byReturn: Record<string, Row[]> = {};
  plan.forEach(function (p) {
    (byReturn[p["return"]] = byReturn[p["return"]] || []).push(p);
  });
  var vest = "",
    rate = "",
    target = "";
  Object.keys(byReturn).forEach(function (name) {
    var rows = byReturn[name],
      first = rows[0];
    var section = el("div", null, "plan-row");
    section.appendChild(el("div", PLAN_NAME[name] || name, "plan-title"));
    if (first.suggested_extra === "") {
      section.appendChild(el("p", first.note, "support"));
      card.appendChild(section);
      return;
    }
    if (first.vest_rate) {
      vest = Math.round(Number(first.vest_rate) * 100) + "%";
      rate = first.vest_rate;
    }
    if (first.target !== "" && first.target !== undefined) target = first.target;
    var now = Number(first.projected);
    section.appendChild(
      el(
        "p",
        "As things stand: " +
          (now >= 0 ? "refund " : "owe ") +
          money.format(Math.abs(now)) +
          ". " +
          (name === "wisconsin" ? "WT-4 additional withholding" : "W-4 line 4(c)") +
          " for " +
          first.person +
          " (" +
          first.employer +
          ") is " +
          cents.format(Number(first.current_extra)) +
          " a paycheck, with " +
          first.paychecks_left +
          " paychecks left.",
        "support",
      ),
    );
    var table = el("table", null, "plan-table");
    var head = el("tr");
    ["To land on", "Extra a paycheck", ""].forEach(function (h) {
      head.appendChild(el("th", h));
    });
    table.appendChild(el("thead")).appendChild(head);
    var body = el("tbody");
    rows.forEach(function (p) {
      var goal = Number(p.target),
        lands = Number(p.lands_at);
      var row = el("tr", null, Number(p.suggested_extra) === Number(p.current_extra) ? "current" : "");
      cell(
        row,
        goal > 0 ? "a refund of " + money.format(goal) : goal < 0 ? "owing " + money.format(-goal) : "no balance",
        "text",
      );
      cell(row, cents.format(Number(p.suggested_extra)));
      var notes: string[] = [];
      if (Math.abs(lands - goal) > 1)
        notes.push("gets to " + (lands >= 0 ? "a refund of " : "owing ") + money.format(Math.abs(lands)) + " at most");
      if (p.penalty_free === "FALSE" || p.penalty_free === "false") notes.push("an underpayment penalty is possible");
      else if (lands < -1 && name === "federal") notes.push("no underpayment penalty");
      cell(row, notes.join("; "), "text plan-note" + (/penalty is possible/.test(notes.join()) ? " off" : ""));
      body.appendChild(row);
    });
    table.appendChild(body);
    section.appendChild(table);
    if (/no Wisconsin settings/.test(first.note)) section.appendChild(el("p", first.note + ".", "support"));
    card.appendChild(section);
  });
  card.appendChild(
    el(
      "p",
      "From the year so far plus the projection" + (vest ? ", with future vests withheld at " + vest : "") + ".",
      "support small-print",
    ),
  );
  // the two settings this plan rests on, changed here (evidence.ts); the next run plans with them
  var settings = el("div", null, "plan-settings");
  var refund = el("div", null, "plan-setting");
  refund.appendChild(el("span", "The refund it aims for", "support"));
  refund.appendChild(settingControl("target_refund", target));
  settings.appendChild(refund);
  var withheld = el("div", null, "plan-setting");
  withheld.appendChild(el("span", "What future vests are withheld at", "support"));
  withheld.appendChild(settingControl("planned_supplemental_rate", rate));
  settings.appendChild(withheld);
  card.appendChild(settings);
  return card;
}

// the summary card's name for each form's bottom line
var STATE_NAMES: Record<string, string> = {
  "Form 1040": "Federal",
  "Wisconsin Form 1": "Wisconsin",
  "Wisconsin Form 1NPR": "Wisconsin",
  "Pennsylvania PA-40": "Pennsylvania",
};
var RATIO_KEYS = ["wi1npr_32"]; // a share, not dollars

var taxLineOpen: Record<string, boolean> = {}; // "year|key" whose parts are shown

function formTable(lines: TaxLine[], withFiled: boolean, computedHead: string, year?: string) {
  var wrap = el("div", null, "scroll");
  var table = el("table", null, "form-lines");
  var head = el("tr");
  ["Line", "", withFiled ? computedHead : "Amount"].concat(withFiled ? ["Filed", "Off by"] : []).forEach(function (h) {
    head.appendChild(el("th", h));
  });
  table.appendChild(el("thead")).appendChild(head);
  var tbody = el("tbody");
  lines.forEach(function (l) {
    var row = el(
      "tr",
      null,
      /^of which|^state and|^mortgage|^gifts/.test(l.label)
        ? "sub"
        : /^(Total tax|Adjusted gross income|Taxable income|Refund|Amount owed|Net tax|Total payments)$/.test(l.label)
          ? "key-line"
          : "",
    );
    cell(row, l.line, "line-no");
    var parts = year && taxes && taxes.parts ? taxes.parts[year + "|" + l.key] : undefined;
    var labelCell = cell(row, parts && parts.length ? "" : l.label, "text");
    if (parts && parts.length) {
      // a projected line opens to what it is made of, each part with where it comes from
      var open = year + "|" + l.key;
      var toggle = button(
        l.label,
        function () {
          taxLineOpen[open] = !taxLineOpen[open];
          toggle.setAttribute("aria-expanded", String(!!taxLineOpen[open]));
          var next = row.nextElementSibling;
          if (next && next.classList.contains("line-parts")) next.remove();
          if (taxLineOpen[open]) row.parentNode!.insertBefore(partsRow(l, parts!, withFiled ? 5 : 3, year!), row.nextSibling);
        },
        "line-why",
      );
      toggle.setAttribute("aria-expanded", String(!!taxLineOpen[open]));
      labelCell.appendChild(toggle);
    }
    var ratio = RATIO_KEYS.indexOf(l.key) >= 0;
    var show = function (v: number) {
      return ratio ? (v * 100).toFixed(2) + "%" : cents.format(v);
    };
    cell(row, show(l.value));
    if (withFiled) {
      // a line with nothing filed comes back without a filed value (null or missing)
      var has = typeof l.filed === "number" && isFinite(l.filed);
      var isOff = has && Math.abs(l.value - (l.filed as number)) > (ratio ? 0.00005 : 1);
      cell(row, has ? show(l.filed as number) : "");
      cell(row, isOff ? show(l.value - (l.filed as number)) : "", isOff ? "off" : "");
    }
    tbody.appendChild(row);
    if (parts && parts.length && taxLineOpen[year + "|" + l.key]) tbody.appendChild(partsRow(l, parts, withFiled ? 5 : 3, year!));
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

// 401(k), the mega backdoor, HSA and the rest: so far, by December 31, and against the limit.
function accountsPanel(accounts: Row[]) {
  var panel = el("section", null, "panel");
  panel.appendChild(el("h2", "Retirement and health accounts"));
  panel.appendChild(el("p", "So far this year from the pay stubs, and by December 31 at the same contributions.", "support"));
  var people: Record<string, Row[]> = {};
  accounts.forEach(function (a) {
    (people[a.person] = people[a.person] || []).push(a);
  });
  Object.keys(people)
    .sort(function (a, b) {
      return Number(a === "household") - Number(b === "household") || a.localeCompare(b);
    })
    .forEach(function (person) {
      panel.appendChild(el("h3", person === "household" ? "Household" : person, "account-person"));
      people[person].forEach(function (a) {
        var row = el("div", null, "account");
        var top = el("div", null, "account-top");
        top.appendChild(el("span", a.plan + (a.employer ? ", " + a.employer : ""), "account-name"));
        top.appendChild(
          el(
            "span",
            a.status,
            "account-status " + (a.status.indexOf("over limit") === 0 ? "off" : a.status.indexOf("at limit") === 0 ? "done" : ""),
          ),
        );
        row.appendChild(top);
        if (a.limit !== "") {
          var limit = Number(a.limit) || 1,
            ytd = Number(a.ytd) || 0,
            projected = Number(a.projected) || 0;
          var meter = el("div", null, "meter");
          var by = el("span", null, "by-year-end");
          by.style.width = Math.min(100, (100 * projected) / limit) + "%";
          var so = el("span", null, "so-far");
          so.style.width = Math.min(100, (100 * ytd) / limit) + "%";
          meter.appendChild(by);
          meter.appendChild(so);
          meter.setAttribute("role", "img");
          meter.setAttribute(
            "aria-label",
            money.format(ytd) + " so far, " + money.format(projected) + " by year end, of " + money.format(limit),
          );
          row.appendChild(meter);
          row.appendChild(
            el(
              "div",
              money.format(ytd) +
                " so far, " +
                money.format(projected) +
                " by December 31, of " +
                money.format(limit) +
                (Number(a.gap) > 0 ? "; " + money.format(Number(a.gap)) + " of room left" : ""),
              "support",
            ),
          );
        }
        panel.appendChild(row);
      });
    });
  return panel;
}

function scenariosPanel(totals: ScenarioTotals[]) {
  var panel = el("section", null, "panel");
  panel.appendChild(el("h2", "Scenarios for this year"));
  panel.appendChild(el("p", "Federal as a bar from zero (refund left, owed right), Wisconsin beside it.", "support"));
  var box = el("div");
  box.id = "tax-scenarios-chart";
  panel.appendChild(box);
  drawScenarioBars(box, totals);
  return panel;
}

function drawScenarioBars(box: HTMLElement, totals: ScenarioTotals[]) {
  var biggest =
    Math.max.apply(
      null,
      totals.map(function (r) {
        return Math.abs(r.federal);
      }),
    ) || 1;
  var list = el("div", null, "scenarios");
  list.style.marginTop = "12px";
  totals.forEach(function (r) {
    var owe = r.federal > 0;
    var row = el("div", null, "scenario");
    row.appendChild(el("span", r.scenario.charAt(0).toUpperCase() + r.scenario.slice(1)));
    var axis = el("div", null, "axis");
    var bar = el("span", null, owe ? "owe" : "refund");
    bar.style.width = (50 * Math.abs(r.federal)) / biggest + "%";
    axis.appendChild(bar);
    row.appendChild(axis);
    row.appendChild(el("span", signed(r.federal), "amount " + (owe ? "owe" : "refund")));
    row.appendChild(el("span", "WI " + signed(r.wisconsin), "amount wi " + (r.wisconsin > 0 ? "owe" : "refund")));
    list.appendChild(row);
  });
  box.appendChild(list);
}

// The tax law these numbers use (tax_sources): each limit, rate and threshold with the document, page and words it
// comes from, a link to the PDF on file. Closed until opened, and drawn only then: the page has no need of it first.
function lawPanel(law: Row[]) {
  var box = el("details", null, "card law");
  var summary = el("summary");
  summary.appendChild(el("h2", "The law these numbers use"));
  summary.appendChild(
    el("span", law[0].year + ": " + countOf(law.length, "value", "values") + ", each with its source", "support"),
  );
  box.appendChild(summary);
  var drawn = false;
  box.addEventListener("toggle", function () {
    if (drawn || !(box as HTMLDetailsElement).open) return;
    drawn = true;
    var table = el("table", null, "law-lines");
    law
      .slice()
      .sort(function (a, b) {
        return a.item.localeCompare(b.item);
      })
      .forEach(function (r) {
        var row = el("tr");
        row.dataset.item = r.item;
        cell(row, r.item.replace(/_/g, " ").replace(/:/g, ": "), "text");
        cell(row, lawValue(r.item, r.value), "num");
        var from = cell(row, "", "text from");
        var doc = r.record ? el("a", r.document + ", page " + r.page) : el("span", r.document + ", page " + r.page);
        if (r.record) {
          (doc as HTMLAnchorElement).href = r.record;
          (doc as HTMLAnchorElement).target = "_blank";
          (doc as HTMLAnchorElement).rel = "noopener";
        }
        from.appendChild(doc);
        if (r.quote) from.appendChild(el("div", "\u201c" + r.quote + "\u201d", "support quote"));
        table.appendChild(row);
      });
    box.appendChild(table);
  });
  return box;
}

// A law value as it reads: a rate as a percent, a dollar amount as dollars, anything else as written.
function lawValue(item: string, value: string) {
  var n = Number(value);
  if (value === "" || isNaN(n)) return value;
  if (/rate/.test(item) && n > 0 && n < 1) return (n * 100).toFixed(n * 100 < 1 ? 2 : 1).replace(/\.0$/, "") + "%";
  if (n >= 100) return money.format(n);
  return value;
}

var TAX_PART_NAMES: Record<string, string> = {
  "so far": "So far",
  paychecks: "Paychecks to come",
  vests: "Vests to come",
  "short-term": "Short-term",
  "long-term": "Long-term",
  estimate: "Your estimate",
};

// What a projected line is made of: each part with whose it is and where it comes from, what they add up to, and
// the way to the input behind a part: a job's pay changes and end on Jobs, the sales on Form 8949.
function partsRow(l: TaxLine, parts: TaxPart[], span: number, year: string) {
  var estimated: Record<string, boolean> = {}; // the people whose estimate already has its Change
  var tr = el("tr", null, "line-parts");
  var td = el("td");
  td.colSpan = span;
  tr.appendChild(td);
  var table = el("table", null, "parts");
  var total = 0;
  parts.forEach(function (p) {
    total += p.amount;
    var r = el("tr");
    var who = cell(r, "", "text who");
    who.appendChild(el("div", TAX_PART_NAMES[p.part] || p.part));
    if (p.person || p.employer) who.appendChild(el("div", [p.person, p.employer].filter(Boolean).join(", "), "support"));
    cell(r, cents.format(p.amount), "num");
    var from = cell(r, "", "text");
    from.appendChild(el("div", p.detail, "support"));
    if (p.part === "estimate" && !estimated[p.person]) {
      // the estimate is changed where it shows, as the whole figure for its person (its parts added up)
      estimated[p.person] = true;
      var mine = parts.filter(function (x) {
        return x.part === "estimate" && x.person === p.person;
      });
      from.appendChild(
        estimateControl(
          year,
          l.key,
          l.label,
          p.person,
          mine.reduce(function (t, x) {
            return t + x.amount;
          }, 0),
          mine.length,
        ),
      );
    }
    if (p.part === "paychecks" || p.part === "vests")
      from.appendChild(
        button(
          "Pay changes and job end, on Jobs",
          function () {
            setView("jobs");
          },
          "text",
        ),
      );
    if (p.part === "short-term" || p.part === "long-term")
      from.appendChild(
        button(
          "The sales, on Form 8949",
          function () {
            taxView = "8949";
            drawTaxes();
          },
          "text",
        ),
      );
    table.appendChild(r);
  });
  td.appendChild(table);
  var rounded = Math.abs(total - l.value) >= 0.005;
  td.appendChild(
    el(
      "p",
      "Adds up to " + cents.format(total) + (rounded ? "; the return rounds it to " + cents.format(l.value) + "." : "."),
      "parts-sum",
    ),
  );
  return tr;
}

// A tax estimate changed where the line it feeds shows it: the whole figure for its year, key and person (an
// `estimate` edit; ingest keeps it as the figure in place of the parts before it). Staged like any change, and
// said on the line until the run applies it.
function estimateControl(year: string, key: string, label: string, person: string, total: number, parts: number) {
  var box = el("span", null, "setting-control estimate-control");
  box.dataset.estimate = key + (person ? "|" + person : "");
  var target = editTarget({ kind: "estimate", year: year, key: key, person: person });
  var draw = function () {
    box.textContent = "";
    var e = edited(target);
    if (e) {
      var to = String((e.edit as Record<string, string>).value);
      box.appendChild(el("strong", money.format(Number(to)), "setting-value"));
      box.appendChild(el("span", stagedText(target) ? "waiting to be saved" : "applies at the next run", "tag warn"));
    }
    var change = button(
      "Change",
      function () {
        box.textContent = "";
        var value = textInput(e ? String((e.edit as Record<string, string>).value) : String(Math.round(total * 100) / 100), "");
        value.setAttribute("aria-label", "Your estimate for " + label + (person ? ", " + person : ""));
        value.inputMode = "decimal";
        box.appendChild(value);
        box.appendChild(
          button(
            "Keep",
            function () {
              var v = value.value.trim().replace(/[$,]/g, "");
              if (!/^-?[0-9]+(\.[0-9]+)?$/.test(v)) {
                value.setCustomValidity("A number, like 1200");
                value.reportValidity();
                return;
              }
              var edit: Edit = { kind: "estimate", year: year, key: key, value: v, was: { value: String(total) } };
              if (person) edit.person = person;
              stage(edit, "your estimate for " + label + (person ? " (" + person + ")" : "") + ", " + year + ": " + v);
              draw();
            },
            "text",
          ),
        );
        if (staged[target])
          box.appendChild(
            button(
              "Undo",
              function () {
                unstage(target);
                draw();
              },
              "text",
            ),
          );
        box.appendChild(button("Cancel", draw, "text"));
        if (parts > 1) box.appendChild(el("div", "The figure you keep stands in for its " + parts + " parts.", "support"));
        value.focus();
      },
      "text",
    );
    change.setAttribute("aria-label", "Change your estimate for " + label);
    box.appendChild(change);
  };
  draw();
  return box;
}
