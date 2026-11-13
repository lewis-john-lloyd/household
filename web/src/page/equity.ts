// The Equity page: the grants' shares held and still to vest, what they come to at the latest price and at
// prices a step or more either side of it (the equity_price_steps setting), each grant with its price at grant
// (set per grant, the grant_price:<grant> setting), and the value vesting each month over the grants' lives.
// The numbers are compute's (equity.py); the page moves the price, the same arithmetic as equity.py.

var equityData: EquityPage | null = null;

function loadEquity() {
  if (equityData) {
    drawEquity();
    return;
  }
  byId("equity-body").textContent = "Loading…";
  call(
    "getEquity",
    [],
    function (d) {
      equityData = d;
      drawEquity();
    },
    "equity-body",
  );
}

function drawEquity() {
  var body = byId("equity-body");
  body.textContent = "";
  var d = equityData!;
  if (!d.grants.length) {
    body.appendChild(
      el(
        "p",
        "No grants yet. Copy the award page (each grant and its vesting schedule) as text into the inbox, then run " + "compute.",
        "support",
      ),
    );
    return;
  }
  d.symbols.forEach(function (s) {
    var grants = d.grants.filter(function (g) {
      return g.symbol === s.symbol;
    });
    var box = el("section", null, "equity-symbol");
    box.appendChild(equityHead(s, grants));
    if (s.price === null)
      box.appendChild(
        el("p", "No price for " + s.symbol + " yet: compute values the shares once prices has a close.", "support"),
      );
    else box.appendChild(equitySteps_(s, grants, d));
    box.appendChild(grantsPanel(s, grants));
    box.appendChild(
      vestingChart(
        s,
        grants,
        d.months.filter(function (m) {
          return m.symbol === s.symbol;
        }),
      ),
    );
    body.appendChild(box);
  });
}

function shares(n: number) {
  return (Math.round(n * 1000) / 1000).toLocaleString("en-US") + (Math.abs(n - 1) < 1e-9 ? " share" : " shares");
}

// What the grants hold now, at the latest price: the A2 head, as the Budget page has it.
function equityHead(s: EquitySymbol, grants: EquityGrant[]) {
  var head = el("section", null, "a2-head");
  var titles = el("div", null, "a2-titles");
  titles.appendChild(
    el(
      "span",
      s.symbol + (s.price !== null ? " at " + cents.format(s.price) + (s.price_date ? ", " + longDay(s.price_date) : "") : ""),
      "a2-eyebrow",
    ),
  );
  titles.appendChild(el("h2", shares(s.held) + " held, " + shares(s.unvested) + " still to vest", "a2-headline"));
  head.appendChild(titles);
  var cards = el("div", null, "a2-cards");
  var card = function (label: string, big: string, small: string, cls?: string) {
    var c = el("div", null, "a2-card" + (cls ? " " + cls : ""));
    c.appendChild(el("span", label, "a2-label"));
    c.appendChild(el("span", big, "a2-big"));
    c.appendChild(el("span", small, "a2-small"));
    cards.appendChild(c);
  };
  card(
    "Held",
    s.value !== null ? money.format(s.value) : shares(s.held),
    shares(s.held) + ", a basis of " + money.format(s.basis),
  );
  card(
    "Gain if sold today",
    s.gain !== null ? signedMoney(s.gain) : "No price",
    s.price !== null ? "At " + cents.format(s.price) + " a share, less their basis" : "Needs a price",
    s.gain !== null && s.gain < -0.5 ? "warn" : "",
  );
  var left = grants.filter(function (g) {
    return g.unvested > 0;
  });
  card(
    "Still to vest",
    s.unvested_value !== null ? money.format(s.unvested_value) : shares(s.unvested),
    shares(s.unvested) + " across " + countOf(left.length, "grant", "grants"),
  );
  var next = left
    .map(function (g) {
      return g.next_vest;
    })
    .filter(Boolean)
    .sort()[0];
  var nextShares = (equityData!.months || [])
    .filter(function (m) {
      return m.symbol === s.symbol && next && m.month === next.slice(0, 7) && m.basis === "latest";
    })
    .reduce(function (t, m) {
      return t + m.shares;
    }, 0);
  card(
    "Next vest",
    next ? longDay(next) : "None",
    next ? "In " + monthWords(next.slice(0, 7)) + ": " + shares(nextShares) : "Every grant has vested",
  );
  head.appendChild(cards);
  return head;
}

// The gain on the shares held and the value still to vest at the latest price and a step or more either side,
// with the arithmetic in words and the steps a setting changed in place.
function equitySteps_(s: EquitySymbol, grants: EquityGrant[], d: EquityPage) {
  var panel = el("section", null, "panel equity-steps");
  panel.appendChild(el("h2", "At other prices"));
  var price = s.price as number;
  var moves = d.steps
    .slice()
    .reverse()
    .map(function (x) {
      return -x;
    })
    .concat([0], d.steps);
  var table = el("table", null, "txns equity-table");
  var head = el("tr");
  ["Share price", "Change", "Gain on shares held", "Still to vest, worth"].forEach(function (h, i) {
    head.appendChild(el("th", h, i ? "num" : "text"));
  });
  table.appendChild(head);
  moves.forEach(function (move) {
    var p = Math.max(price + move, 0);
    var gain = s.held * p - s.basis;
    var unvested = grants.reduce(function (t, g) {
      return t + g.unvested * sharePrice(g.award_type, p, g.grant_price);
    }, 0);
    var row = el("tr", null, move === 0 ? "now" : "");
    row.dataset.move = String(move);
    cell(row, cents.format(p) + (move === 0 ? ", now" : ""), "text");
    cell(row, move === 0 ? "" : signedMoney(move) + " a share", "num");
    cell(row, signedMoney(gain), "num" + (gain < -0.5 ? " over" : ""));
    cell(row, money.format(unvested), "num");
    table.appendChild(row);
  });
  panel.appendChild(table);
  panel.appendChild(
    el(
      "p",
      "Gain: the " +
        shares(s.held) +
        " held at the price, less their basis of " +
        money.format(s.basis) +
        ". Still to vest: each grant's unvested shares at the price; an option's at what the price is over its grant " +
        "price.",
      "support",
    ),
  );
  var steps = el("p", null, "support");
  steps.appendChild(document.createTextNode("Steps, in dollars a share: "));
  steps.appendChild(settingControl("equity_price_steps", d.stepsSet !== null ? d.stepsSet : d.steps.join(", ")));
  panel.appendChild(steps);
  return panel;
}

// Each grant: what it granted, vested and has still to vest, its next vest, its grant price (set per grant in
// place), and what its price has done since.
function grantsPanel(s: EquitySymbol, grants: EquityGrant[]) {
  var panel = el("section", null, "panel equity-grants");
  panel.appendChild(el("h2", "Grants"));
  var table = el("table", null, "txns equity-table");
  var head = el("tr");
  ["Grant", "Granted", "Vested", "To vest", "Next vest", "Grant price", "Since grant"].forEach(function (h, i) {
    head.appendChild(el("th", h, i === 0 || i === 4 || i === 5 ? "text" : "num"));
  });
  table.appendChild(head);
  grants.forEach(function (g) {
    var row = el("tr");
    row.dataset.grant = g.grant_id;
    var who = cell(row, "", "text");
    who.appendChild(el("div", g.grant_id));
    who.appendChild(el("div", g.award_type + ", " + longDay(g.award_date), "support"));
    cell(row, (Math.round(g.granted * 1000) / 1000).toLocaleString("en-US"), "num");
    cell(row, (Math.round(g.vested * 1000) / 1000).toLocaleString("en-US"), "num");
    var left = cell(row, "", "num");
    left.appendChild(el("div", (Math.round(g.unvested * 1000) / 1000).toLocaleString("en-US")));
    if (s.price !== null && g.unvested > 0)
      left.appendChild(el("div", money.format(g.unvested * sharePrice(g.award_type, s.price, g.grant_price)), "support"));
    cell(row, g.next_vest ? longDay(g.next_vest) : "Vested", "text");
    var priced = cell(row, "", "text");
    priced.appendChild(
      settingControl("grant_price:" + grantKey(g.grant_id), g.grant_price !== null ? String(g.grant_price) : null),
    );
    if (g.price_from === "award page") priced.appendChild(el("div", "from the award page", "support"));
    var since = cell(row, "", "num");
    if (s.price === null || g.grant_price === null) since.appendChild(el("span", "Needs a grant price", "support"));
    else if (OPTION_TYPES.indexOf(g.award_type) >= 0)
      since.appendChild(el("span", cents.format(Math.max(s.price - g.grant_price, 0)) + " a share over it", ""));
    else {
      var move = s.price - g.grant_price;
      since.appendChild(el("div", signedCents(move) + " a share"));
      since.appendChild(el("div", signedMoney(move * g.granted) + " on the grant", "support"));
    }
    table.appendChild(row);
  });
  panel.appendChild(table);
  panel.appendChild(
    el(
      "p",
      "A grant price is the one on the award page until you set one for the grant. Since grant: the price now less " +
        "the grant price, a share and on the grant's shares.",
      "support",
    ),
  );
  return panel;
}

function nextMonth(m: string) {
  var y = Number(m.slice(0, 4)),
    mo = Number(m.slice(5, 7)) + 1;
  if (mo > 12) {
    y++;
    mo = 1;
  }
  return y + "-" + (mo < 10 ? "0" : "") + mo;
}

function signedCents(n: number) {
  return (n > 0.005 ? "+" : n < -0.005 ? "−" : "") + cents.format(Math.abs(n));
}

var GRANT_COLORS = 8; // --series-1 to --series-8; more grants share one gray

// The value vesting each month over the grants' lives, a bar a month stacked by grant: months vested at their
// vests' value, months to come at the latest price (lighter), today marked between them.
function vestingChart(s: EquitySymbol, grants: EquityGrant[], months: EquityMonth[]) {
  var panel = el("section", null, "panel equity-chart tax-history");
  panel.appendChild(el("h2", "Vesting, month by month"));
  if (!months.length) {
    panel.appendChild(el("p", "No vests to show yet.", "support"));
    return panel;
  }
  var ids = grants.map(function (g) {
    return g.grant_id;
  });
  var colorOf = function (id: string) {
    var i = ids.indexOf(id);
    return i >= 0 && i < GRANT_COLORS ? "var(--series-" + (i + 1) + ")" : "var(--outline)";
  };
  var legend = el("div", null, "legend");
  ids.forEach(function (id, i) {
    if (i > GRANT_COLORS) return;
    var item = el("span", i === GRANT_COLORS ? "Other grants" : id);
    item.style.setProperty("--swatch", i === GRANT_COLORS ? "var(--outline)" : colorOf(id));
    legend.appendChild(item);
  });
  panel.appendChild(legend);
  // every month from the first vest to the last, so a gap reads as a month with nothing vesting
  var first = months[0].month,
    last = months[months.length - 1].month;
  var all: string[] = [];
  for (var m = first; m <= last; m = nextMonth(m)) all.push(m);
  var byMonth: Record<string, EquityMonth[]> = {};
  months.forEach(function (x) {
    (byMonth[x.month] = byMonth[x.month] || []).push(x);
  });
  var total = function (mm: string) {
    return (byMonth[mm] || []).reduce(function (t, x) {
      return t + x.value;
    }, 0);
  };
  var hi = Math.max.apply(null, all.map(total).concat([1]));
  var step = Math.pow(10, Math.floor(Math.log10(hi / 4)));
  [1, 2, 2.5, 5, 10].some(function (k) {
    if (hi / (step * k) <= 5) {
      step *= k;
      return true;
    }
    return false;
  });
  var top = Math.ceil((hi * 1.08) / step) * step;
  var NS = "http://www.w3.org/2000/svg";
  var svgEl = function (name: string, attrs: Record<string, string | number>, parent?: Element) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, String(attrs[k]));
    });
    if (parent) parent.appendChild(e);
    return e;
  };
  var width = Math.max(Math.min(byId("equity-body").clientWidth - 48, 1100), 340),
    height = 300;
  var mg = { top: 16, right: 8, bottom: 28, left: 56 };
  var y = function (v: number) {
    return mg.top + ((top - v) / top) * (height - mg.top - mg.bottom);
  };
  var svg = svgEl("svg", {
    viewBox: "0 0 " + width + " " + height,
    class: "history-chart vesting-chart",
    role: "img",
    "aria-label": "The value vesting each month, by grant; the months are in the table below",
  });
  panel.appendChild(svg);
  var short = function (v: number) {
    return v >= 1e6 ? "$" + (v / 1e6).toFixed(1) + "M" : v >= 1000 ? "$" + Math.round(v / 1000) + "k" : "$" + Math.round(v);
  };
  for (var t = 0; t <= top + 0.001; t += step) {
    svgEl("line", { x1: mg.left, x2: width - mg.right, y1: y(t), y2: y(t), class: t === 0 ? "zero" : "grid" }, svg);
    var tl = svgEl("text", { x: mg.left - 8, y: y(t) + 4, "text-anchor": "end", class: "axis" }, svg);
    tl.textContent = short(t);
  }
  var gap = (width - mg.left - mg.right) / all.length;
  var bw = Math.max(Math.min(gap - 2, 28), 2);
  var now = today().slice(0, 7);
  var tip = el("div", null, "tip");
  tip.hidden = true;
  panel.appendChild(tip);
  all.forEach(function (mm, i) {
    var x = mg.left + gap * i + (gap - bw) / 2;
    var col = svgEl("g", { class: "vest-month" + (mm < now ? " past" : mm === now ? " now" : "") }, svg);
    var up = 0;
    (byMonth[mm] || []).forEach(function (part) {
      var h = y(up) - y(up + part.value);
      svgEl(
        "rect",
        {
          x: x,
          width: bw,
          y: y(up + part.value),
          height: Math.max(h - (h > 3 ? 1 : 0), 0.5),
          fill: colorOf(part.grant_id),
          rx: 1.5,
        },
        col,
      );
      up += part.value;
    });
    // a year's label under its January (and under the first month), so the axis stays readable over many years
    if (mm.slice(5) === "01" || i === 0) {
      var yl = svgEl("text", { x: x + bw / 2, y: height - 8, "text-anchor": "middle", class: "axis" }, svg);
      yl.textContent = mm.slice(5) === "01" ? mm.slice(0, 4) : MONTH_SHORT[Number(mm.slice(5)) - 1] + " " + mm.slice(0, 4);
    }
    var hit = svgEl(
      "rect",
      { x: mg.left + gap * i, width: gap, y: mg.top, height: height - mg.top - mg.bottom, fill: "transparent" },
      col,
    );
    hit.addEventListener("mouseenter", function () {
      var parts = byMonth[mm] || [];
      tip.textContent = "";
      tip.appendChild(el("div", monthWords(mm) + ": " + money.format(total(mm)), "strong"));
      if (!parts.length) tip.appendChild(el("div", "Nothing vests"));
      parts.forEach(function (p) {
        tip.appendChild(
          el(
            "div",
            p.grant_id + ": " + shares(p.shares) + " at " + cents.format(p.price) + (p.basis === "vested" ? ", as vested" : ""),
          ),
        );
      });
      tip.hidden = false;
      var box = (svg as unknown as SVGGraphicsElement).getBoundingClientRect(),
        pbox = panel.getBoundingClientRect();
      var left = box.left - pbox.left + ((mg.left + gap * (i + 0.5)) / width) * box.width;
      tip.style.left = Math.min(Math.max(left - 110, 0), pbox.width - 230) + "px";
      tip.style.top = box.top - pbox.top + 8 + "px";
    });
    hit.addEventListener("mouseleave", function () {
      tip.hidden = true;
    });
  });
  // today, between the months vested and the months to come
  var at = all.indexOf(now);
  if (at >= 0) {
    var tx = mg.left + gap * at;
    svgEl("line", { x1: tx, x2: tx, y1: mg.top, y2: height - mg.bottom, class: "today" }, svg);
    var tt = svgEl("text", { x: tx + 4, y: mg.top + 10, class: "axis" }, svg);
    tt.textContent = "Today";
  }
  panel.appendChild(
    el(
      "p",
      "Months vested at each vest's value when the ledger has it; months to come at " +
        (s.price !== null ? cents.format(s.price) + " a share, the latest price" : "the latest price") +
        ", an option's at what that is over its grant price.",
      "support",
    ),
  );
  // the same numbers as a table, for reading or copying
  var details = el("details", null, "equity-months");
  details.appendChild(el("summary", "The months as a table"));
  var table = el("table", null, "txns equity-table");
  var head = el("tr");
  ["Month", "Shares", "Value", ""].forEach(function (h, i) {
    head.appendChild(el("th", h, i === 0 || i === 3 ? "text" : "num"));
  });
  table.appendChild(head);
  all.forEach(function (mm) {
    var parts = byMonth[mm] || [];
    if (!parts.length) return;
    var row = el("tr", null, mm < now ? "past" : "");
    cell(row, monthShort(mm), "text");
    cell(
      row,
      (
        Math.round(
          parts.reduce(function (t, p) {
            return t + p.shares;
          }, 0) * 1000,
        ) / 1000
      ).toLocaleString("en-US"),
      "num",
    );
    cell(row, money.format(total(mm)), "num");
    cell(row, mm < now ? "vested" : "to come", "text support");
    table.appendChild(row);
  });
  details.appendChild(table);
  panel.appendChild(details);
  return panel;
}
