// Cashflow: the Sankey, and beside it the same money by what it is to the household.
function drawFlowSummary(s: FlowSummary) {
  var box = byId("flow-summary");
  box.textContent = "";
  var sum = function (items: FlowItem[]) {
    return items.reduce(function (a, i) {
      return a + i.amount;
    }, 0);
  };
  (
    [
      ["Income", s.income, "came"],
      ["Other money in", s.other, "came"],
      ["Taxes and deductions", s.taxes, "taxes"],
      ["Went to", s.went, "went"],
    ] as [string, FlowItem[], string][]
  ).forEach(function (c) {
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
      if (c[2] === "went") {
        // where it went opens its lines, each with its fixes (evidence.ts)
        li.classList.add("opens");
        li.tabIndex = 0;
        li.setAttribute("role", "button");
        li.setAttribute("aria-haspopup", "dialog");
        var go = function () {
          openFlowLines(i.name, i.amount);
        };
        li.onclick = go;
        li.onkeydown = onEnter(go);
      }
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
  lastFlows: Answer<"getFlows"> | null = null;

// Sankey, Table or Over time: a chart is drawn only while it shows, since it is sized to its box
function setFlowsTab(tab: string) {
  flowsTab = tab;
  document.querySelectorAll<HTMLElement>("#flows-tabs button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.tab === tab));
  });
  byId("flows-sankey").hidden = tab !== "sankey";
  byId("flow-summary").hidden = tab !== "table";
  byId("flows-overtime").hidden = tab !== "overtime";
  byId("controls").hidden = tab === "overtime"; // one period's controls; Over time sets its own range
  if (tab === "overtime") loadSpendOverTime();
  else if (tab === "sankey" && lastFlows) drawFlows(lastFlows);
  else if (!lastFlows && view === "flows") load();
}

function drawFlows(result: Answer<"getFlows">) {
  var chart = byId("chart");
  var note = byId("flows-note");
  lastFlows = result;
  drawFlowSummary(result.summary);
  if (flowsTab !== "sankey") return;
  if (!result.rows.length) {
    chart.textContent = "No flows for this period.";
    note.textContent = "";
    return;
  }
  var label: Record<string, string> = {}; // each node shows its total after its name
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
  var named: Record<string, string> = {}; // a node's label back to its name, for a click on it
  Object.keys(label).forEach(function (n) {
    named[label[n]] = n;
  });
  var went: Record<string, number> = {};
  result.summary.went.forEach(function (i) {
    went[i.name] = i.amount;
  });
  var height = Math.max(480, 28 * result.rows.length);
  chart.style.height = height + "px";
  chart.textContent = "";
  // Google draws the chart in its own colors: hand it the page's, so labels read in dark mode too
  var css = getComputedStyle(document.documentElement);
  var token = function (name: string) {
    return css.getPropertyValue(name).trim();
  };
  var sankey = new google.visualization.Sankey(chart);
  // a click on where money went opens its lines, as the table's rows do
  google.visualization.events.addListener(sankey, "select", function () {
    var picked = sankey.getSelection()[0] as { name?: string } | undefined;
    var name = picked && picked.name ? named[picked.name] : "";
    if (name && name in went) openFlowLines(name, went[name]);
  });
  sankey.draw(data, {
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
function itemList(items: Item[]) {
  var list = document.createElement("ul");
  list.className = "items";
  function add(i: Item) {
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

// The lines behind where money went in the chosen period: a category's (or, by group, its categories'), the most
// spent first, each with its fixes. A year's monthly average is a twelfth of the year's lines, so it lists those.
function openFlowLines(name: string, amount: number) {
  var period = chosen.flows || "";
  var average = period.indexOf(" ") > 0;
  var whole = average ? period.slice(0, 4) : period;
  var days = periodDays(whole);
  var byGroup = level === "group";
  var d = drawer(
    pretty(name),
    (average ? "Each month of " + whole + " on average: " : periodName(whole) + ": ") +
      money.format(amount) +
      (byGroup ? ", every category in the group." : ".") +
      (average ? " These are the year's lines; the average is a twelfth of them." : ""),
  );
  var box = el("div", "Loading\u2026", "drawer-section support");
  box.id = "flow-lines";
  d.appendChild(box);
  if (!byGroup) {
    // a category's budget is one click on: its drawer on the Budget page
    var link = button(
      "Open its budget",
      function () {
        closeDrawer();
        budget.tab = "lines";
        budget.open = { category: name };
        setView("budget");
      },
      "text",
    );
    d.insertBefore(link, box);
  }
  cachedCall(
    "getLines",
    [days[0], days[1], byGroup ? [] : [name], byGroup ? [name] : []],
    function (list) {
      var draw = function () {
        box.textContent = "";
        box.className = "drawer-section";
        box.appendChild(lineList(list, draw));
      };
      draw();
    },
    "flow-lines",
  );
}
