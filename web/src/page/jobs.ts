// The Jobs page: each person's pay over time.

// --- the Jobs page -------------------------------------------------------------------------------------
// A pay chart per person from comp_events. Choosing a stretch of a line shows its details, what the
// paychecks say about it, and a correction; + adds a change. Runs of paychecks that disagree with the
// jobs come first, each with the change it suggests.
function loadJobs() {
  if (editor) {
    drawJobsPage();
    return;
  }
  byId("jobs-body").textContent = "Loading…";
  call(
    "getEditor",
    [],
    function (d) {
      editor = d;
      drawJobsPage();
    },
    "jobs-body",
  );
}

// After an edit staged from a page's dialog or drawer, draw that page again.
function redrawPage() {
  if (view === "jobs") drawJobsPage();
  else if (view === "budget") drawBudgetPage();
  else redrawEditor();
}

function drawJobsPage() {
  if (view !== "jobs" || !editor) return;
  var body = byId("jobs-body");
  body.textContent = "";
  if (!editor.baseRun) {
    body.appendChild(el("p", "No compute run with editor tables yet: run ingest and compute once with this release.", "support"));
    return;
  }
  editor.payCheck
    .filter(function (r) {
      return r.status !== "agrees";
    })
    .forEach(function (r) {
      body.appendChild(payAlert(r));
    });
  if (!editor.jobs.length) {
    body.appendChild(el("p", "No jobs yet. Add each job and pay change, from the date it took effect.", "support"));
    var add = el("div", null, "actions");
    add.appendChild(
      button(
        "Add a job",
        function () {
          jobDialog("", {}, "Add a job");
        },
        "filled",
      ),
    );
    body.appendChild(add);
    return;
  }
  payCharts(body, editor.jobs);
  body.appendChild(
    el(
      "div",
      "Choose a stretch of a line for its details and what the paychecks say about it, or + to add a change. " +
        "The tax projection and the pay check read these.",
      "note",
    ),
  );
}

function personName(p: string) {
  return p ? p.charAt(0).toUpperCase() + p.slice(1) : "";
}

// A run of paychecks that disagrees with the jobs: what it says, and the change it suggests. Pay well under
// the job's salary reads as fewer hours: the salary is kept and the share of full time suggested.
function payAlert(r: Row) {
  var box = el("div", null, "alert changed");
  var text = el("span");
  text.appendChild(el("strong", personName(r.person) + "'s paychecks, " + r.from + " to " + r.to + ": "));
  text.appendChild(document.createTextNode(r.finding + "."));
  box.appendChild(text);
  var actions = el("span", null, "actions");
  var full = Number(r.comp_salary),
    paid = Number(r.stubs_annual);
  var fewer = full > 0 && paid < 0.9 * full;
  actions.appendChild(
    button(
      "Record the change",
      function () {
        jobDialog(
          personName(r.person),
          {
            effective: r.from,
            person: r.person,
            employer: r.comp_employer || r.employer,
            salary: String(Math.round(fewer ? full : paid)),
            fte: fewer ? String(Math.round((paid / full) * 100) / 100) : "",
          },
          "Record the change the paychecks show",
        );
      },
      "filled",
    ),
  );
  box.appendChild(actions);
  return box;
}

// One stretch of a pay line: the job row it comes from, what the paychecks say, and a correction.
function showJob(box: HTMLElement, name: string, r: PayStretch, next: PayStretch | null, close: () => void) {
  box.textContent = "";
  box.hidden = false;
  var head = el("div", null, "pay-head");
  head.appendChild(el("h3", r.employer + ", " + r.at + " to " + (next ? next.at : "now")));
  head.appendChild(button("Close", close, "text"));
  box.appendChild(head);
  var row = editor!.jobs.filter(function (j) {
    return j.person === r.person && j.effective === r.at && j.employer === r.employer;
  })[0];
  var stubs = row && row.stubs;
  facts(box, [
    [
      "Pay a year",
      money.format(r.salary) +
        (r.fte !== 1 ? " full time; " + Math.round(r.fte * 100) + "% time, " + money.format(r.pay) + " paid" : ""),
    ],
    ["Band", r.band],
    ["Rating", r.rating],
    ["Note", r.note],
    [
      "The paychecks",
      stubs === "agrees" ? "agree with it" : stubs || "none checked against it (the check reads last year's and this year's)",
    ],
  ]);
  var t = "job|" + r.person + "|" + r.at + "|" + r.employer;
  if (changeNote(t)) box.appendChild(el("p", changeNote(t), "staged-note"));
  var actions = el("div", null, "actions");
  actions.appendChild(
    button("Correct this", function () {
      jobDialog(
        name,
        {
          effective: r.at,
          person: r.person,
          employer: r.employer,
          salary: String(r.salary),
          fte: r.fte !== 1 ? String(r.fte) : "",
          band: r.band || "",
          rating: r.rating || "",
          note: r.note || "",
        },
        "Correct " + r.employer + " from " + r.at,
        "Keep this correction",
      );
    }),
  );
  actions.appendChild(el("span", "A row with the same date, person and employer replaces this one.", "support"));
  box.appendChild(actions);
}
