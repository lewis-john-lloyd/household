// @ts-nocheck
// Cashflow: the Sankey and the money by what it is.

// Cashflow: the Sankey, and beside it the same money by what it is to the household.
function drawFlowSummary(s) {
  var box = document.getElementById("flow-summary");
  box.textContent = "";
  var sum = function (items) {
    return items.reduce(function (a, i) {
      return a + i.amount;
    }, 0);
  };
  [
    ["Income", s.income, "came"],
    ["Other money in", s.other, "came"],
    ["Taxes and deductions", s.taxes, "taxes"],
    ["Went to", s.went, "went"],
  ].forEach(function (c) {
    if (!c[1].length) return;
    var panel = el("section", null, "panel flow-column " + c[2]);
    var head = el("div", null, "flow-head");
    head.appendChild(el("h2", c[0]));
    var total = sum(c[1]);
    head.appendChild(el("span", money.format(total), "flow-total"));
    panel.appendChild(head);
    var list = el("ul", null, "flow-items");
    c[1].forEach(function (i) {
      var li = el("li");
      var label = el("div", null, "flow-label");
      label.appendChild(el("span", i.name));
      label.appendChild(el("span", money.format(i.amount), "n"));
      li.appendChild(label);
      var bar = el("div", null, "flow-bar");
      var fill = el("span");
      var widest = Math.max.apply(
        null,
        c[1].map(function (x) {
          return Math.abs(x.amount);
        }),
      );
      fill.style.width = (100 * Math.max(i.amount, 0)) / (widest || 1) + "%"; // a loss has no bar
      bar.appendChild(fill);
      li.appendChild(bar);
      li.title =
        i.name +
        ": " +
        money.format(i.amount) +
        (i.amount > 0 && total > 0 ? ", " + Math.round((100 * i.amount) / total) + "% of " + c[0].toLowerCase() : "");
      list.appendChild(li);
    });
    panel.appendChild(list);
    box.appendChild(panel);
  });
  if (s.through.length) {
    box.appendChild(
      el(
        "p",
        "On the way: " +
          s.through
            .map(function (t) {
              return t.name + " " + money.format(t.amount);
            })
            .join(", ") +
          ".",
        "support flow-through",
      ),
    );
  }
}

var flowsTab = "sankey",
  lastFlows = null;

// Sankey or Table: the chart is drawn only while it shows, since Google sizes it to its box
function setFlowsTab(tab) {
  flowsTab = tab;
  document.querySelectorAll("#flows-tabs button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.tab === tab));
  });
  document.getElementById("flows-sankey").hidden = tab !== "sankey";
  document.getElementById("flow-summary").hidden = tab !== "table";
  if (tab === "sankey" && lastFlows) drawFlows(lastFlows);
}

function drawFlows(result) {
  var chart = document.getElementById("chart");
  var note = document.getElementById("flows-note");
  lastFlows = result;
  drawFlowSummary(result.summary);
  if (flowsTab !== "sankey") return;
  if (!result.rows.length) {
    chart.textContent = "No flows for this period.";
    note.textContent = "";
    return;
  }
  var label = {}; // each node shows its total after its name
  result.totals.forEach(function (t) {
    label[t.name] = t.name + "  " + money.format(t.total);
  });
  var data = new google.visualization.DataTable();
  data.addColumn("string", "From");
  data.addColumn("string", "To");
  data.addColumn("number", "Amount");
  data.addColumn({ type: "string", role: "tooltip" });
  data.addRows(
    result.rows.map(function (r) {
      return [label[r[0]], label[r[1]], r[2], r[0] + " → " + r[1] + ": " + money.format(r[2])];
    }),
  );
  var height = Math.max(480, 28 * result.rows.length);
  chart.style.height = height + "px";
  chart.textContent = "";
  // Google draws the chart in its own colors: hand it the page's, so labels read in dark mode too
  var css = getComputedStyle(document.documentElement);
  var token = function (name) {
    return css.getPropertyValue(name).trim();
  };
  new google.visualization.Sankey(chart).draw(data, {
    height: height,
    backgroundColor: "transparent",
    sankey: {
      node: {
        nodePadding: 18,
        width: 8,
        label: { color: token("--on-surface"), fontName: "Roboto Flex", fontSize: 13 },
        colors: [token("--seg-1"), token("--seg-2"), token("--seg-3"), token("--seg-4"), token("--primary")],
      },
      link: { color: { fill: token("--outline"), fillOpacity: 0.45 } },
    },
    tooltip: { textStyle: { color: "#1f1f1f" } },
  });
  note.textContent = result.dropped.length
    ? "Left out to keep the chart drawable (they would loop back): " +
      result.dropped
        .map(function (r) {
          return r[0] + " → " + r[1];
        })
        .join(", ")
    : "";
}

// What an order history says a charge bought: the first few items, the rest behind "+N more".
function itemList(items) {
  var list = document.createElement("ul");
  list.className = "items";
  function add(i) {
    var li = document.createElement("li");
    li.textContent = i.name + " · " + cents.format(i.amount);
    li.title = i.name;
    list.appendChild(li);
  }
  items.slice(0, ITEMS_SHOWN).forEach(add);
  if (items.length > ITEMS_SHOWN) {
    var more = document.createElement("li");
    more.className = "more";
    more.textContent = "+" + (items.length - ITEMS_SHOWN) + " more";
    more.title = "show them all";
    more.onclick = function (e) {
      e.stopPropagation();
      more.remove();
      items.slice(ITEMS_SHOWN).forEach(add);
    };
    list.appendChild(more);
  }
  return list;
}
