// @ts-nocheck
// Loading each view, switching views, and starting the page.

function load() {
  document.getElementById("controls").hidden = view !== "flows";
  if (view === "status") {
    document.getElementById("status-title").textContent = "Loading…";
    call(
      "getStatus",
      [],
      function (status) {
        drawStatus(status);
        loadWaitingCard();
        loadAppliedAnywayCard();
      },
      "status-title",
    );
    return;
  }
  if (view === "edit") {
    loadEditor();
    return;
  }
  if (view === "taxes") {
    loadTaxes();
    return;
  }
  if (view === "investments") {
    loadInvestments();
    return;
  }
  if (view === "retirement") {
    loadRetirement();
    return;
  }
  if (view === "home") {
    loadHome();
    return;
  }
  if (view === "budget") {
    loadBudget();
    return;
  }
  if (view === "jobs") {
    loadJobs();
    return;
  }
  if (!periods.flows) {
    call(
      "getPeriods",
      [],
      function (list) {
        periods.flows = list;
        if (!list.length) {
          document.getElementById("chart").textContent = "No flows yet: run compute.";
          return;
        }
        load();
      },
      "chart",
    );
    return;
  }
  fillPeriods(periods.flows);
  document.getElementById("chart").textContent = "Loading…";
  call("getFlows", [chosen.flows, level], drawFlows, "chart");
}

function press(ids, on) {
  ids.forEach(function (id) {
    document.getElementById(id).setAttribute("aria-pressed", String(id === on));
  });
}

function setView(next) {
  if (next === "subscriptions" || next === "recurring") {
    next = "budget";
    budget.tab = "recurring";
  } // older links
  if (next !== "budget") closeDrawer(true);
  view = next;
  press(
    ["view-status", "view-flows", "view-budget", "view-investments", "view-retirement", "view-home", "view-taxes", "view-jobs"],
    "view-" + (view === "edit" ? EDIT_HOME[editTab] || "flows" : view),
  );
  if (view === "edit") drawEditTabs();
  document.getElementById("status").hidden = view !== "status";
  document.getElementById("flows").hidden = view !== "flows";
  document.getElementById("budget").hidden = view !== "budget";
  document.getElementById("edit").hidden = view !== "edit";
  document.getElementById("taxes").hidden = view !== "taxes";
  document.getElementById("investments").hidden = view !== "investments";
  document.getElementById("retirement").hidden = view !== "retirement";
  document.getElementById("home").hidden = view !== "home";
  document.getElementById("jobs").hidden = view !== "jobs";
  document.querySelector("main").classList.toggle("wide", view === "flows");
  load();
}

function setLevel(next) {
  level = next;
  press(["group", "category"], level);
  load();
}

google.charts.load("current", { packages: ["sankey"] });
google.charts.setOnLoadCallback(function () {
  document.getElementById("period").addEventListener("change", function (e) {
    chosen[view] = e.target.value;
    load();
  });
  document.getElementById("view-status").addEventListener("click", function () {
    setView("status");
  });
  document.getElementById("view-flows").addEventListener("click", function () {
    setView("flows");
  });
  document.getElementById("view-budget").addEventListener("click", function () {
    setView("budget");
  });
  document.getElementById("view-taxes").addEventListener("click", function () {
    setView("taxes");
  });
  document.getElementById("view-investments").addEventListener("click", function () {
    setView("investments");
  });
  document.getElementById("view-retirement").addEventListener("click", function () {
    setView("retirement");
  });
  document.getElementById("view-home").addEventListener("click", function () {
    setView("home");
  });
  document.getElementById("view-jobs").addEventListener("click", function () {
    setView("jobs");
  });
  document.querySelectorAll("#budget-tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      openPart("budget", b.dataset.tab);
    });
  });
  document.querySelectorAll("#status-tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      openPart("status", b.dataset.tab);
    });
  });
  document.querySelectorAll("#flows-tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      openPart("flows", b.dataset.tab);
    });
  });
  document.getElementById("pending-show").addEventListener("click", function () {
    var list = document.getElementById("pending-list");
    list.hidden = !list.hidden;
    this.textContent = list.hidden ? "Show" : "Hide";
  });
  document.getElementById("pending-save").addEventListener("click", saveStaged);
  document.getElementById("pending-dismiss").addEventListener("click", function () {
    document.getElementById("pending-msg").textContent = "";
    drawPending();
  });
  document.getElementById("pending-discard").addEventListener("click", discardStaged);
  if (window.matchMedia) {
    // the chart takes its colors when drawn: redraw it when light and dark swap
    var scheme = window.matchMedia("(prefers-color-scheme: dark)");
    var redraw = function () {
      if (view === "flows" || view === "investments" || view === "retirement" || view === "home") load();
    };
    var resized = null; // a wider or narrower window redraws the flow chart to fit, once it settles
    window.addEventListener("resize", function () {
      clearTimeout(resized);
      resized = setTimeout(function () {
        if (view === "flows") load();
      }, 250);
    });
    if (scheme.addEventListener) scheme.addEventListener("change", redraw);
    else if (scheme.addListener) scheme.addListener(redraw);
  }
  window.addEventListener("beforeunload", function (e) {
    // unsaved changes, and a save not yet answered, live only here
    if (stagedOrder.length || busy() || unconfirmed) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  document.getElementById("group").addEventListener("click", function () {
    setLevel("group");
  });
  document.getElementById("category").addEventListener("click", function () {
    setLevel("category");
  });
  // A link can open a page: ?view=taxes, or ?view=edit&tab=review (the weekly email's links do).
  if (google.script.url && google.script.url.getLocation) google.script.url.getLocation(openFrom);
  else load();
});

// A link can open a page: ?view=taxes, ?view=budget&tab=recurring, ?view=edit&tab=review (the weekly email's
// links do). Older links to tabs that became pages (Edit's budget lines, jobs, recurring) open the page.
function openFrom(location) {
  var p = (location && location.parameter) || {};
  if (p.view === "cashflow") p.view = "flows";
  var moved = { budget: "budget", jobs: "jobs", subscriptions: "recurring", recurring: "recurring" };
  if (p.view === "edit" && moved[p.tab]) {
    setView(moved[p.tab]);
    return;
  }
  if (p.view === "budget" && p.tab === "recurring") budget.tab = "recurring";
  if (!p.view && p.tab === "log") setStatusTab("log");
  // an editor part opens where it lives now: ?view=edit&tab=settings opens Status's Settings
  if (p.tab && EDIT_HOME[p.tab] && p.tab !== "transaction" && (!p.view || p.view === "edit" || p.view === EDIT_HOME[p.tab])) {
    editTab = p.tab;
    setView("edit");
    return;
  }
  if (p.view === "edit") {
    editTab = "review";
    setView("edit");
    return;
  }
  if (
    p.view === "taxes" &&
    p.tab &&
    PAGE_PARTS.taxes.some(function (t) {
      return t[0] === p.tab;
    })
  )
    taxView = p.tab;
  if (
    p.view === "retirement" &&
    p.tab &&
    RETIRE_PARTS.some(function (t) {
      return t[0] === p.tab;
    })
  )
    retireView = p.tab;
  if (
    p.view === "investments" &&
    p.tab &&
    INVEST_PARTS.some(function (t) {
      return t[0] === p.tab;
    })
  )
    investView = p.tab;
  if (
    ["flows", "budget", "investments", "retirement", "home", "taxes", "jobs", "edit", "subscriptions", "recurring"].indexOf(
      p.view,
    ) >= 0
  )
    setView(p.view);
  else load();
}
