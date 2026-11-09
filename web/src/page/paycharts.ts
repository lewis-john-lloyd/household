// Pay over time: the Jobs page's charts.

/** A stretch of a person's pay: a comp_events row in force from at until the next. */
interface PayStretch {
  at: string;
  t: number; // at, as a time
  person: string;
  employer: string;
  salary: number;
  fte: number;
  pay: number; // salary times fte
  band: string;
  rating: string;
  note: string;
  clipped?: boolean; // starts before the chart does
}

var SERIES = 8;
var YEARS_SHOWN = 12; // a pay chart opens at the level in force this many years back
function payCharts(body: HTMLElement, jobs: Row[]) {
  var rows = jobs
    .filter(function (j) {
      return /^\d{4}-\d{2}-\d{2}$/.test(j.effective) && Number(j.salary) > 0;
    })
    .map(function (j): PayStretch {
      var fte = Number(j.fte) > 0 ? Number(j.fte) : 1;
      return {
        at: j.effective,
        t: Date.parse(j.effective + "T00:00:00Z"),
        person: j.person,
        employer: j.employer,
        salary: Number(j.salary),
        fte: fte,
        pay: Number(j.salary) * fte,
        band: j.band,
        rating: j.rating,
        note: j.note,
      };
    })
    .sort(function (a, b) {
      return a.t - b.t;
    });
  if (!rows.length) return;
  var slot: Record<string, number> = {},
    next = 0; // color follows the employer, by first appearance, across every chart
  rows.forEach(function (r) {
    var k = r.employer.toLowerCase();
    if (!(k in slot)) {
      slot[k] = next < SERIES ? next + 1 : 0;
      next += 1;
    }
  });
  var colorOf = function (employer: string) {
    var n = slot[employer.toLowerCase()];
    return n ? "var(--series-" + n + ")" : "var(--outline)";
  };
  var wrap = el("div", null, "pay-charts");
  body.appendChild(wrap);
  var people = Array.from(
    new Set(
      rows.map(function (r) {
        return r.person;
      }),
    ),
  ).sort();
  people.forEach(function (person) {
    var panel = el("section", null, "panel pay-chart");
    wrap.appendChild(panel);
    var name = person.charAt(0).toUpperCase() + person.slice(1);
    var head = el("div", null, "pay-head");
    head.appendChild(el("h3", name + ": pay a year"));
    var mine = rows.filter(function (r) {
      return r.person === person;
    });
    var latest = mine[mine.length - 1];
    var add = button(
      "",
      function () {
        // a new change starts from the job in force: same employer, salary and hours, dated today
        jobDialog(name, {
          person: person,
          employer: latest.employer,
          salary: String(latest.salary),
          fte: latest.fte !== 1 ? String(latest.fte) : "",
          band: latest.band || "",
        });
      },
      "icon-button",
    );
    add.appendChild(icon("add"));
    add.setAttribute("aria-label", "Add a pay change for " + name);
    add.title = "Add a pay change for " + name;
    head.appendChild(add);
    panel.appendChild(head);
    var detail = el("div", null, "job-detail");
    detail.hidden = true;
    var pick: (i: number | null) => void = payChart(
      panel,
      mine,
      colorOf,
      function (r, next) {
        showJob(detail, name, r, next, function () {
          detail.hidden = true;
          pick(null);
        });
      },
      function () {
        detail.hidden = true;
      },
    );
    panel.appendChild(detail);
  });
}

// A pay change in a dialog over the charts: the job form, staged like any edit when kept.
function jobDialog(name: string, start: Row, title?: string, label?: string) {
  var dialog = document.createElement("dialog");
  dialog.className = "job-dialog";
  dialog.appendChild(el("h2", title || "Add a pay change for " + name));
  dialog.appendChild(
    el(
      "p",
      "A raise, a new job, or a change of hours, from the date it took effect. " +
        "For fewer hours keep the full-time salary and give the share of full time.",
      "support",
    ),
  );
  var close = function () {
    dialog.close();
    dialog.remove();
  };
  var form = jobForm(
    start,
    function () {
      close();
      redrawPage();
    },
    label || "Add pay change",
  );
  var cancel = button("Cancel", close, "text");
  (form.querySelector(".actions") as HTMLElement).appendChild(cancel);
  dialog.appendChild(form);
  dialog.addEventListener("cancel", function () {
    dialog.remove();
  });
  document.body.appendChild(dialog);
  dialog.showModal();
  var first = dialog.querySelector("input");
  if (first) first.focus();
}

// onSelect(row, next) is called when a stretch of the line (or its marker) is chosen; payChart returns a
// function that marks the chosen stretch (null for none).
function payChart(
  panel: HTMLElement,
  rows: PayStretch[],
  colorOf: (employer: string) => string,
  onSelect: (r: PayStretch, next: PayStretch | null) => void,
  onClear: () => void,
) {
  var NS = "http://www.w3.org/2000/svg";
  var svgEl = function (name: string, attrs: Record<string, string | number>, parent?: Element) {
    var e = document.createElementNS(NS, name);
    Object.keys(attrs).forEach(function (k) {
      e.setAttribute(k, String(attrs[k]));
    });
    if (parent) parent.appendChild(e);
    return e;
  };
  var legend = el("div", null, "legend");
  Array.from(
    new Set(
      rows.map(function (r) {
        return r.employer;
      }),
    ),
  ).forEach(function (e) {
    var item = el("span", e);
    item.style.setProperty("--swatch", colorOf(e));
    legend.appendChild(item);
  });
  panel.appendChild(legend);
  var width = Math.max(panel.clientWidth - 40, 320),
    height = 240;
  var m = { top: 34, right: 16, bottom: 26, left: 56 };
  var now = Date.now();
  var t1 = Math.max(now, rows[rows.length - 1].t);
  var t0 = Math.max(rows[0].t, Date.UTC(new Date(t1).getUTCFullYear() - YEARS_SHOWN, 0, 1));
  rows = rows
    .filter(function (r, i) {
      return r.t >= t0 || !rows[i + 1] || rows[i + 1].t > t0;
    })
    .map(function (r) {
      return r.t < t0 ? Object.assign({}, r, { t: t0, clipped: true }) : r;
    });
  var pays = rows
    .map(function (r) {
      return r.pay;
    })
    .concat(
      rows.map(function (r) {
        return r.salary;
      }),
    );
  var lo = Math.min.apply(null, pays),
    hi = Math.max.apply(null, pays);
  var step = Math.pow(10, Math.floor(Math.log10(Math.max(hi - lo, hi * 0.2) / 3)));
  [1, 2, 2.5, 5, 10].some(function (k) {
    if ((hi - lo) / (step * k) <= 4) {
      step *= k;
      return true;
    }
    return false;
  });
  var y0 = Math.max(0, Math.floor((lo * 0.9) / step) * step),
    y1 = Math.ceil((hi * 1.04) / step) * step;
  var x = function (t: number) {
    return m.left + ((t - t0) / Math.max(t1 - t0, 1)) * (width - m.left - m.right);
  };
  var y = function (v: number) {
    return m.top + ((y1 - v) / Math.max(y1 - y0, 1)) * (height - m.top - m.bottom);
  };
  var svg = svgEl("svg", {
    viewBox: "0 0 " + width + " " + height,
    role: "img",
    "aria-label": "Pay a year over time, by employer; the list below has every change",
  });
  panel.appendChild(svg);
  for (var v = y0; v <= y1 + 0.5; v += step) {
    // recessive grid and axis
    svgEl("line", { x1: m.left, x2: width - m.right, y1: y(v), y2: y(v), class: "grid" }, svg);
    var label = svgEl("text", { x: m.left - 8, y: y(v) + 4, "text-anchor": "end", class: "axis" }, svg);
    label.textContent = "$" + Math.round(v / 1000) + "k";
  }
  var firstYear = new Date(t0).getUTCFullYear() + 1,
    lastYear = new Date(t1).getUTCFullYear();
  var every = Math.max(1, Math.ceil((lastYear - firstYear + 1) / Math.max(1, Math.floor((width - m.left) / 70))));
  for (var yr = firstYear; yr <= lastYear; yr += every) {
    var tx = svgEl("text", { x: x(Date.UTC(yr, 0, 1)), y: height - 6, "text-anchor": "middle", class: "axis" }, svg);
    tx.textContent = String(yr);
  }
  // the step line: a level per row until the next, risers in neutral ink so color stays the employer's
  var levels: Element[] = [],
    current = -1;
  var choose = function (i: number) {
    current = i;
    levels.forEach(function (l, n) {
      l.setAttribute("stroke-width", String(n === i ? 5 : 2));
    });
  };
  // choosing the stretch already open closes its details again
  var select = function (i: number) {
    if (i === current) {
      choose(-1);
      if (onClear) onClear();
      return;
    }
    choose(i);
    if (onSelect) onSelect(rows[i], rows[i + 1] || null);
  };
  rows.forEach(function (r, i) {
    var end = i + 1 < rows.length ? rows[i + 1].t : t1;
    levels.push(
      svgEl(
        "line",
        {
          x1: x(r.t),
          x2: x(end),
          y1: y(r.pay),
          y2: y(r.pay),
          stroke: colorOf(r.employer),
          "stroke-width": 2,
          "stroke-linecap": "round",
        },
        svg,
      ),
    );
    if (i + 1 < rows.length) svgEl("line", { x1: x(end), x2: x(end), y1: y(r.pay), y2: y(rows[i + 1].pay), class: "riser" }, svg);
    // part time: a ghost of the full-time salary above what is paid, so the job's level stays in view
    if (r.fte !== 1) {
      svgEl(
        "line",
        {
          x1: x(r.t),
          x2: x(end),
          y1: y(r.salary),
          y2: y(r.salary),
          stroke: colorOf(r.employer),
          "stroke-width": 2,
          "stroke-dasharray": "4 4",
          opacity: 0.45,
        },
        svg,
      );
      // named once, at the start of a part-time stretch, above its dashed line and only where there is room
      // for it, so it never runs into the callout of the change that ends the stretch
      var startsGhost = i === 0 || rows[i - 1].fte === 1 || rows[i - 1].employer.toLowerCase() !== r.employer.toLowerCase();
      if (startsGhost && x(end) - x(r.t) > 70) {
        var g = svgEl("text", { x: x(r.t) + 8, y: y(r.salary) - 6, class: "axis" }, svg);
        g.textContent = "full time";
      }
    }
  });
  // each stretch of the line can be chosen, by pointer or keyboard, for its details below the chart
  rows.forEach(function (r, i) {
    var end = i + 1 < rows.length ? rows[i + 1].t : t1;
    var seg = svgEl(
      "line",
      {
        x1: x(r.t),
        x2: x(end),
        y1: y(r.pay),
        y2: y(r.pay),
        class: "seg-hit",
        tabindex: 0,
        role: "button",
        "aria-label": r.employer + " from " + r.at + ": " + money.format(r.pay) + " a year",
      },
      svg,
    );
    seg.addEventListener("click", function () {
      select(i);
    });
    seg.addEventListener("keydown", function (event) {
      var e = event as KeyboardEvent;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        select(i);
      }
    });
  });
  // callouts: the new employer, a change of hours, or the raise; raises closer together than a label are
  // told once, at the last, as the change across them. Raises and moves sit above their marker, a drop below;
  // a second lane takes a neighbour that would collide. Every change keeps its marker and its hover detail.
  var tip = el("div", null, "tip");
  tip.hidden = true;
  panel.appendChild(tip);
  var lanes = { up: [-Infinity, -Infinity], down: [-Infinity, -Infinity] };
  var runFrom: number | null = null; // the pay before a run of raises told as one
  rows.forEach(function (r, i) {
    var prev = rows[i - 1],
      after = rows[i + 1];
    var moved = !prev || prev.employer.toLowerCase() !== r.employer.toLowerCase();
    var hours = !moved && r.fte !== prev.fte;
    var raise = !moved && !hours && r.pay !== prev.pay;
    var text = "";
    if (moved) text = r.employer;
    else if (hours) text = r.fte === 1 ? "full time" : Math.round(r.fte * 100) + "% time";
    else if (raise) {
      if (runFrom === null) runFrom = prev.pay;
      var nextRaise =
        after &&
        after.employer.toLowerCase() === r.employer.toLowerCase() &&
        after.fte === r.fte &&
        after.pay !== r.pay &&
        x(after.t) - x(r.t) < 24;
      if (!nextRaise) {
        text = (r.pay > runFrom ? "+" : "") + (100 * (r.pay / runFrom - 1)).toFixed(1) + "%";
        runFrom = null;
      }
    }
    var cx = x(r.t),
      cy = y(r.pay);
    // a change just before a move to another employer is told by the move's label, which it would run into
    if (!moved && after && after.employer.toLowerCase() !== r.employer.toLowerCase() && x(after.t) - cx < 60) text = "";
    if (r.clipped) return; // older than the chart: it only sets the level the chart opens at
    svgEl("circle", { cx: cx, cy: cy, r: 4, fill: colorOf(r.employer), stroke: "var(--surface-low)", "stroke-width": 2 }, svg);
    if (text) {
      var w = text.length * 6.6 + 6;
      var down = prev && r.pay < prev.pay; // a cut or a move to less pay: below, clear of the level before
      var ends = lanes[down ? "down" : "up"];
      var lane = cx - w / 2 > ends[0] ? 0 : cx - w / 2 > ends[1] ? 1 : -1;
      if (lane >= 0) {
        ends[lane] = cx + w / 2;
        var ty = down ? cy + 18 + lane * 14 : cy - 10 - lane * 14;
        var c = svgEl(
          "text",
          {
            x: Math.min(Math.max(cx, m.left + w / 2), width - w / 2),
            y: Math.max(ty, 12),
            "text-anchor": "middle",
            class: "callout" + (moved ? " move" : ""),
          },
          svg,
        );
        c.textContent = text;
      }
    }
    var change = moved
      ? ""
      : hours
        ? text
        : r.pay !== prev.pay
          ? (r.pay > prev.pay ? "+" : "") + (100 * (r.pay / prev.pay - 1)).toFixed(1) + "%"
          : "";
    var detail = [
      r.at + ", " + r.employer,
      money.format(r.salary) +
        " a year" +
        (r.fte !== 1 ? " at " + Math.round(r.fte * 100) + "% time, " + money.format(r.pay) + " paid" : ""),
      [change, r.band, r.rating ? "rating " + r.rating : ""].filter(Boolean).join(", "),
      r.note,
    ].filter(Boolean);
    var hit = svgEl("circle", { cx: cx, cy: cy, r: 12, fill: "transparent", class: "mark-hit" }, svg);
    hit.addEventListener("click", function () {
      select(i);
    });
    hit.addEventListener("mouseenter", function () {
      tip.textContent = "";
      detail.forEach(function (line, n) {
        tip.appendChild(el("div", line, n ? "" : "strong"));
      });
      tip.hidden = false;
      var scale = svg.getBoundingClientRect().width / width;
      tip.style.left = Math.max(0, Math.min(cx * scale + 12, panel.clientWidth - 280)) + "px";
      tip.style.top = svg.getBoundingClientRect().top - panel.getBoundingClientRect().top + cy * scale + 14 + "px";
    });
    hit.addEventListener("mouseleave", function () {
      tip.hidden = true;
    });
  });
  return function (i: number | null) {
    choose(i === null ? -1 : i);
  };
}
