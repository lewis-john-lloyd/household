// Loading each view, switching views, and starting the page.

function load() {
  byId("controls").hidden = view !== "flows" || flowsTab === "overtime";
  if (view === "status") {
    byId("status-title").textContent = "Loading…";
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
  if (view === "equity") {
    loadEquity();
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
  if (flowsTab === "overtime") {
    loadSpendOverTime();
    return;
  }
  if (!periodsLoaded.flows) {
    call(
      "getPeriods",
      [],
      function (list) {
        periodsLoaded.flows = list;
        if (!list.length) {
          byId("chart").textContent = "No flows yet: run compute.";
          return;
        }
        load();
      },
      "chart",
    );
    return;
  }
  if (!periodsLoaded.flows.length) {
    // an empty list is still an answer: coming back to the page says so again rather than asking for no period
    byId("chart").textContent = "No flows yet: run compute.";
    return;
  }
  fillPeriods(periodsLoaded.flows);
  byId("chart").textContent = "Loading…";
  call("getFlows", [chosen.flows, level], drawFlows, "chart");
}

function press(ids: string[], on: string) {
  ids.forEach(function (id) {
    byId(id).setAttribute("aria-pressed", String(id === on));
  });
}

function setView(next: string) {
  if (next === "subscriptions" || next === "recurring") {
    next = "budget";
    budget.tab = "recurring";
  } // older links
  if (next !== "budget") closeDrawer(true);
  view = next;
  press(
    [
      "view-status",
      "view-flows",
      "view-budget",
      "view-investments",
      "view-equity",
      "view-retirement",
      "view-home",
      "view-taxes",
      "view-jobs",
    ],
    "view-" + (view === "edit" ? EDIT_HOME[editTab] || "flows" : view),
  );
  if (view === "edit") drawEditTabs();
  byId("status").hidden = view !== "status";
  byId("flows").hidden = view !== "flows";
  byId("budget").hidden = view !== "budget";
  byId("edit").hidden = view !== "edit";
  byId("taxes").hidden = view !== "taxes";
  byId("investments").hidden = view !== "investments";
  byId("retirement").hidden = view !== "retirement";
  byId("home").hidden = view !== "home";
  byId("equity").hidden = view !== "equity";
  byId("jobs").hidden = view !== "jobs";
  document.querySelector("main")!.classList.toggle("wide", view === "flows");
  load();
}

function setLevel(next: string) {
  level = next;
  press(["group", "category"], level);
  load();
}

google.charts.load("current", { packages: ["sankey"] });
google.charts.setOnLoadCallback(function () {
  byId("period").addEventListener("change", function (e) {
    chosen[view] = (e.target as HTMLSelectElement).value;
    load();
  });
  byId("view-status").addEventListener("click", function () {
    setView("status");
  });
  byId("view-flows").addEventListener("click", function () {
    setView("flows");
  });
  byId("view-budget").addEventListener("click", function () {
    setView("budget");
  });
  byId("view-taxes").addEventListener("click", function () {
    setView("taxes");
  });
  byId("view-investments").addEventListener("click", function () {
    setView("investments");
  });
  byId("view-retirement").addEventListener("click", function () {
    setView("retirement");
  });
  byId("view-home").addEventListener("click", function () {
    setView("home");
  });
  byId("view-equity").addEventListener("click", function () {
    setView("equity");
  });
  byId("view-jobs").addEventListener("click", function () {
    setView("jobs");
  });
  document.querySelectorAll<HTMLElement>("#budget-tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      openPart("budget", b.dataset.tab as string);
    });
  });
  document.querySelectorAll<HTMLElement>("#status-tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      openPart("status", b.dataset.tab as string);
    });
  });
  document.querySelectorAll<HTMLElement>("#flows-tabs button").forEach(function (b) {
    b.addEventListener("click", function () {
      openPart("flows", b.dataset.tab as string);
    });
  });
  byId("pending-show").addEventListener("click", function () {
    var list = byId("pending-list");
    list.hidden = !list.hidden;
    this.textContent = list.hidden ? "Show" : "Hide";
  });
  byId("pending-save").addEventListener("click", saveStaged);
  byId("pending-dismiss").addEventListener("click", function () {
    byId("pending-msg").textContent = "";
    drawPending();
  });
  byId("pending-discard").addEventListener("click", discardStaged);
  if (window.matchMedia) {
    // the chart takes its colors when drawn: redraw it when light and dark swap
    var scheme = window.matchMedia("(prefers-color-scheme: dark)");
    var redraw = function () {
      if (view === "flows" || view === "investments" || view === "retirement" || view === "home") load();
    };
    var resized: number | null = null; // a wider or narrower window redraws the flow chart to fit, once it settles
    window.addEventListener("resize", function () {
      clearTimeout(resized!);
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
  byId("group").addEventListener("click", function () {
    setLevel("group");
  });
  byId("category").addEventListener("click", function () {
    setLevel("category");
  });
  loadSavedEdits(); // what was saved and no run has applied yet shows as saved, not as unchanged
  // A link can open a page: ?view=taxes, or ?view=edit&tab=review (the weekly email's links do).
  if (google.script.url && google.script.url.getLocation) google.script.url.getLocation(openFrom);
  else load();
});

// A link can open a page: ?view=taxes, ?view=budget&tab=recurring, ?view=edit&tab=review (the weekly email's
// links do). Older links to tabs that became pages (Edit's budget lines, jobs, recurring) open the page.
function openFrom(location: PageLocation | null) {
  var p = (location && location.parameter) || {};
  if (p.view === "cashflow") p.view = "flows";
  var moved: Record<string, string> = { budget: "budget", jobs: "jobs", subscriptions: "recurring", recurring: "recurring" };
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
    [
      "flows",
      "budget",
      "investments",
      "equity",
      "retirement",
      "home",
      "taxes",
      "jobs",
      "edit",
      "subscriptions",
      "recurring",
    ].indexOf(p.view) >= 0
  )
    setView(p.view);
  else load();
}
