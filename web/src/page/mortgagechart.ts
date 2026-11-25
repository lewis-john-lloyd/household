// Home, Mortgage: each month's payment in its parts from the first payment to payoff (mortgage_parts), stacked:
// the loan's principal and interest below, the escrow's parts (mortgage insurance, property tax, insurance, as
// the loan's loan_split setting names them) above. Each part, or a whole group, is shown or hidden from the
// picks above the chart, kept for the page's life; the pointer reads any month.

var mortgageHidden: Record<string, boolean> = {}; // parts hidden ("<loan>|<part>")

function mortgageChart(box: HTMLElement, d: HomePage, loan: string, redraw: () => void) {
  var mine = (d.parts || []).filter(function (p) {
    return p.loan === loan;
  });
  if (!mine.length) {
    box.appendChild(
      el("p", "The payment's parts show after the next run of the engine (it publishes them as mortgage_parts).", "support"),
    );
    return;
  }
  // the parts in stacking order, loan first: principal, interest, then the escrow's in the setting's order
  var order: { part: string; kind: string; label: string }[] = [];
  mine.forEach(function (p) {
    if (
      !order.some(function (o) {
        return o.part === p.part;
      })
    )
      order.push({ part: p.part, kind: p.kind, label: p.label });
  });
  order.sort(function (a, b) {
    return (
      (a.kind === "loan" ? 0 : 1) - (b.kind === "loan" ? 0 : 1) || (a.part === "principal" ? -1 : b.part === "principal" ? 1 : 0)
    );
  });
  var colorOf: Record<string, string> = {};
  order.forEach(function (o, i) {
    colorOf[o.part] = "var(--series-" + (i + 1) + ")";
  });
  var key = function (part: string) {
    return loan + "|" + part;
  };
  var shown = order.filter(function (o) {
    return !mortgageHidden[key(o.part)];
  });

  // the picks: a group's name shows or hides all of it, a part's itself
  var picks = el("div", null, "mortgage-picks");
  [
    ["loan", "Loan"],
    ["escrow", "Escrow"],
  ].forEach(function (g) {
    var parts = order.filter(function (o) {
      return o.kind === g[0];
    });
    if (!parts.length) return;
    var row = el("div", null, "line-picks");
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", g[1]);
    var allOn = parts.every(function (o) {
      return !mortgageHidden[key(o.part)];
    });
    var head = el("button", g[1], "line-pick group-pick");
    head.type = "button";
    head.setAttribute("aria-pressed", String(allOn));
    head.title = (allOn ? "Hide" : "Show") + " every part of the " + g[1].toLowerCase();
    head.addEventListener("click", function () {
      parts.forEach(function (o) {
        mortgageHidden[key(o.part)] = allOn;
      });
      redraw();
    });
    row.appendChild(head);
    parts.forEach(function (o) {
      var on = !mortgageHidden[key(o.part)];
      var b = el("button", null, "line-pick");
      b.type = "button";
      b.setAttribute("aria-pressed", String(on));
      var sw = el("i", null, "swatch");
      if (on) sw.style.background = colorOf[o.part];
      b.appendChild(sw);
      b.appendChild(document.createTextNode(o.label));
      b.addEventListener("click", function () {
        mortgageHidden[key(o.part)] = on;
        redraw();
      });
      row.appendChild(b);
    });
    picks.appendChild(row);
  });
  box.appendChild(picks);

  // each month's amount per part
  var months: string[] = [];
  var at: Record<string, Record<string, number>> = {};
  mine.forEach(function (p) {
    if (!at[p.month]) {
      at[p.month] = {};
      months.push(p.month);
    }
    at[p.month][p.part] = p.amount;
  });
  months.sort();
  var balance: Record<string, number> = {};
  d.schedule.forEach(function (p) {
    if (p.loan === loan) balance[p.month] = p.balance;
  });
  var totalOf = function (m: string) {
    return shown.reduce(function (s, o) {
      return s + (at[m][o.part] || 0);
    }, 0);
  };
  var hi = Math.max.apply(null, months.map(totalOf).concat([1]));
  var step = Math.pow(10, Math.floor(Math.log10(hi / 4)));
  [1, 2, 2.5, 5, 10].some(function (k) {
    if (hi / (step * k) <= 5) {
      step *= k;
      return true;
    }
    return false;
  });
  var top = Math.ceil((hi * 1.05) / step) * step;

  var wrap = el("div", null, "spend-plot mortgage-plot tax-history");
  var NS = "http://www.w3.org/2000/svg";
  var svgEl = function (name: string, attrs: Record<string, string | number>, parent?: Element) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, String(attrs[k]));
    });
    if (parent) parent.appendChild(e);
    return e;
  };
  var width = Math.max(Math.min(box.clientWidth || 900, 1100), 320),
    height = 300;
  var mg = { top: 12, right: 12, bottom: 28, left: 56 };
  var n = months.length;
  var x = function (i: number) {
    return mg.left + (n > 1 ? (i / (n - 1)) * (width - mg.left - mg.right) : 0);
  };
  var y = function (v: number) {
    return mg.top + ((top - v) / top) * (height - mg.top - mg.bottom);
  };
  var svg = svgEl("svg", {
    viewBox: "0 0 " + width + " " + height,
    class: "history-chart mortgage-areas",
    role: "img",
    "aria-label":
      "Each month's payment, stacked by its parts, from the first payment to payoff; the years are in the table below",
  });
  wrap.appendChild(svg);
  var label = function (v: number) {
    return v >= 1000 ? "$" + Math.round(v / 10) / 100 + "k" : "$" + Math.round(v); // two places, so a quarter of a thousand isn't rounded away
  };
  for (var t = 0; t <= top + 0.001; t += step) {
    svgEl("line", { x1: mg.left, x2: width - mg.right, y1: y(t), y2: y(t), class: t === 0 ? "zero" : "grid" }, svg);
    svgEl("text", { x: mg.left - 8, y: y(t) + 4, "text-anchor": "end", class: "axis" }, svg).textContent = label(t);
  }
  // a band per part, each on the ones below it
  var below = months.map(function () {
    return 0;
  });
  shown.forEach(function (o) {
    var upper = months.map(function (m, i) {
      return below[i] + (at[m][o.part] || 0);
    });
    var path = "";
    upper.forEach(function (v, i) {
      path += (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1);
    });
    for (var i = n - 1; i >= 0; i--) path += "L" + x(i).toFixed(1) + "," + y(below[i]).toFixed(1);
    svgEl("path", { d: path + "Z", fill: colorOf[o.part], class: "band", "data-part": o.part }, svg);
    // a thin surface line on each band's top, so neighbors read apart
    var edge = "";
    upper.forEach(function (v, i) {
      edge += (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1);
    });
    svgEl("path", { d: edge, class: "band-edge" }, svg);
    below = upper;
  });
  // the years along the bottom, every fifth past a decade's worth
  var every = n > 180 ? 5 : n > 60 ? 2 : 1;
  months.forEach(function (m, i) {
    if (m.slice(5) !== "01" || Number(m.slice(0, 4)) % every) return;
    svgEl("text", { x: x(i), y: height - 8, "text-anchor": "middle", class: "axis" }, svg).textContent = m.slice(0, 4);
  });
  var nowAt = months.indexOf(d.month);
  if (nowAt >= 0) {
    svgEl("line", { x1: x(nowAt), x2: x(nowAt), y1: mg.top, y2: height - mg.bottom, class: "now-line" }, svg);
    svgEl("text", { x: x(nowAt) + 4, y: mg.top + 10, class: "axis" }, svg).textContent = "now";
  }
  // the pointer: a crosshair on the nearest month, its parts in a tip
  var cross = svgEl("line", { x1: 0, x2: 0, y1: mg.top, y2: height - mg.bottom, class: "cross" }, svg);
  cross.setAttribute("visibility", "hidden");
  var tip = el("div", null, "tip");
  tip.hidden = true;
  wrap.appendChild(tip);
  var hit = svgEl(
    "rect",
    { x: mg.left, y: mg.top, width: width - mg.left - mg.right, height: height - mg.top - mg.bottom, fill: "transparent" },
    svg,
  );
  var read = function (clientX: number) {
    var r = svg.getBoundingClientRect();
    var px = ((clientX - r.left) / r.width) * width;
    var i = Math.max(0, Math.min(n - 1, Math.round(((px - mg.left) / (width - mg.left - mg.right)) * (n - 1))));
    var m = months[i];
    cross.setAttribute("x1", String(x(i)));
    cross.setAttribute("x2", String(x(i)));
    cross.setAttribute("visibility", "visible");
    tip.textContent = "";
    tip.appendChild(el("div", monthWords(m) + (m > d.month ? " (ahead)" : "") + ": " + cents.format(totalOf(m)), "strong"));
    if (balance[m] !== undefined) tip.appendChild(el("div", "Owed after it: " + money.format(balance[m]), "support"));
    shown
      .slice()
      .reverse()
      .forEach(function (o) {
        var row = el("div", null, "row");
        var name = el("span");
        var k = el("i", null, "key");
        k.style.background = colorOf[o.part];
        name.appendChild(k);
        name.appendChild(document.createTextNode(o.label));
        row.appendChild(name);
        row.appendChild(el("span", cents.format(at[m][o.part] || 0)));
        tip.appendChild(row);
      });
    tip.hidden = false;
    var w = wrap.getBoundingClientRect();
    var left = (x(i) / width) * r.width;
    tip.style.left = Math.max(Math.min(left + 12, w.width - tip.offsetWidth - 4), 0) + "px";
    tip.style.top = "8px";
  };
  hit.addEventListener("mousemove", function (e) {
    read((e as MouseEvent).clientX);
  });
  hit.addEventListener("mouseleave", function () {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
  });
  if (!shown.length) box.appendChild(el("p", "Every part is hidden: choose one above to draw it.", "support"));
  box.appendChild(wrap);
  // where the split turns: the first month more of the payment pays principal than interest
  var turn = months.filter(function (m) {
    return (at[m].principal || 0) > (at[m].interest || 0);
  })[0];
  if (turn && turn !== months[0])
    box.appendChild(
      el(
        "p",
        "From " + monthWords(turn) + (turn > d.month ? " (ahead)" : "") + ", more of each payment pays principal than interest.",
        "support",
      ),
    );
}
