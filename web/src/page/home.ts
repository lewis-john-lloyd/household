// Home: each property, its taxes, mortgage and improvements.

function drawHome() {
  var d = home!, // drawn once loaded
    body = byId("home-body");
  body.innerHTML = "";
  if (!d.homes.length) {
    body.appendChild(
      el(
        "p",
        "No homes yet. Add one with a properties file in the inbox (its header is the properties table's columns).",
        "support",
      ),
    );
    return;
  }
  subtabs(
    body,
    "Home",
    [
      ["overview", "Overview"],
      ["taxes", "Property tax"],
      ["mortgage", "Mortgage"],
      ["improvements", "Improvements"],
    ],
    homeView,
    function (v) {
      homeView = v;
      drawHome();
    },
  );
  var isOwned = function (h: Home) {
    return h.lines.some(function (l) {
      return l.key === "status" && l.value === "owned";
    });
  };
  var owned = d.homes.filter(isOwned),
    gone = d.homes.filter(function (h) {
      return !isOwned(h);
    });
  owned.forEach(function (h) {
    var line: Record<string, HomeLine> = {};
    h.lines.forEach(function (l) {
      line[l.key] = l;
    });
    drawOneHome(
      body,
      d,
      h,
      line,
      h.lines.filter(function (l) {
        return l.key.indexOf("loan:") === 0;
      }),
    );
  });
  if (!owned.length) body.appendChild(el("p", "No home owned today.", "support"));
  if (gone.length && homeView === "overview")
    body.appendChild(
      el(
        "p",
        "Earlier homes (" +
          gone
            .map(function (h) {
              return h.id;
            })
            .join(", ") +
          ") count in net worth history while they were owned.",
        "support",
      ),
    );
}

function drawOneHome(body: HTMLElement, d: HomePage, h: Home, line: Record<string, HomeLine>, loans: HomeLine[]) {
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", h.id));
  if (line.purchase_price && line.purchase_price.asOf)
    head.appendChild(el("span", "bought " + longDate(line.purchase_price.asOf), "support"));
  body.appendChild(head);
  if (homeView === "taxes") {
    drawHomeTaxes(body, d, h);
    return;
  }
  if (homeView === "mortgage") {
    drawMortgage(body, d, loans);
    return;
  }
  if (homeView === "improvements") {
    drawImprovements(body, d, h);
    return;
  }
  var owed = loans.reduce(function (s, l) {
    return s + Number(l.value || 0);
  }, 0);
  var val = function (k: string) {
    return line[k] ? Number(line[k].value) : null;
  };
  figures(body, [
    val("equity") !== null ? [val("equity") as number, "equity at the value counted"] : null,
    [owed, "owed"],
    val("value_used") !== null ? [val("value_used") as number, "value counted (" + (line.value_used.note || "") + ")"] : null,
    val("estimate_median") !== null ? [val("estimate_median") as number, "median estimate"] : null,
    val("basis") !== null ? [val("basis") as number, "adjusted basis"] : null,
  ]);
  var grid = el("div", null, "w-grid");
  body.appendChild(grid);
  var vb = panel(grid, "Value and basis");
  var vt = wTable(vb, ["", "Amount"], [1]);
  h.lines.forEach(function (l) {
    if (typeof l.value !== "number") return;
    var label = el("div", l.label);
    var more = [l.note, l.key.indexOf("estimate:") === 0 && l.asOf ? longDate(l.asOf) : ""].filter(Boolean).join(", ");
    if (more) label.appendChild(el("div", more, "support"));
    vt.row([label, money.format(l.value).replace("-", "−")], ["basis", "equity", "sale_net"].indexOf(l.key) >= 0 ? "group" : "");
  });

  // the two settings these figures rest on, changed here (evidence.ts)
  var uses = el("div", null, "plan-settings");
  [
    [
      "home_value_basis",
      "What net worth counts the home at: purchase_price (the default), median, lowest, or one source, such as county_fair_market",
    ],
    ["home_sale_cost_rate", "What selling would cost, as a fraction of the price, for what a sale would net (0.06 unless set)"],
  ].forEach(function (s) {
    var row = el("div", null, "plan-setting");
    row.appendChild(el("span", s[1], "support"));
    row.appendChild(settingControl(s[0], null));
    uses.appendChild(row);
  });
  vb.appendChild(uses);

  var es = panel(
    grid,
    "Estimates",
    "The county's assessment and its estimate of fair market value, newest first. The county " +
      "check adds them each month; any other can be added with a property_values file in the inbox.",
  );
  var mine = d.estimates.filter(function (e) {
    return e.property === h.id && e.source !== "legacy";
  });
  if (!mine.length) es.appendChild(el("p", "None yet.", "support"));
  else {
    var et = wTable(es, ["Source", "Value", "As of"], [1]);
    mine.forEach(function (e) {
      et.row([e.source.replace(/_/g, " "), money.format(e.value), longDate(e.asOf)]);
    });
  }
}

function drawHomeTaxes(body: HTMLElement, d: HomePage, h: Home) {
  var taxes = d.taxes.filter(function (t) {
    return t.property === h.id;
  });
  if (!taxes.length) body.appendChild(el("p", "No tax bills yet: the county check adds each one from AccessDane.", "support"));
  if (taxes.length) {
    var tx = el("section", null, "panel w-wide");
    tx.appendChild(el("h3", "Property tax"));
    var next = taxes.filter(function (t) {
      return t.estimated;
    })[0];
    tx.appendChild(
      el(
        "p",
        (next
          ? "The " +
            next.year +
            " bill, due by the end of January, is estimated at " +
            money.format(next.due) +
            ": the " +
            next.year +
            " assessment at the last bill's mill rate, credits and specials. "
          : "") + "The county sets the mill rate when it bills in December; the county check reads each bill.",
        "support",
      ),
    );
    var tt = wTable(tx, ["Tax year", "Assessed", "Mill rate", "Taxes", "Credits", "Specials", "Due"], [1, 2, 3, 4, 5, 6]);
    taxes.forEach(function (t) {
      var year = el("span", t.year);
      if (t.estimated) year.appendChild(el("span", "estimate", "tag"));
      tt.row(
        [
          year,
          money.format(t.assessed),
          (t.millRate * 1000).toFixed(3),
          money.format(t.totalTaxes),
          "\u2212" + money.format(t.credits),
          money.format(t.specials),
          money.format(t.due),
        ],
        t.estimated ? "projected" : "",
      );
    });
    body.appendChild(tx);
  }
}

// A loan's value as its kind says: money, a rate in percent, a date, a month, a term in months, or text.
function loanValue(x: LoanDetail) {
  var n = Number(String(x.value).replace(/,/g, ""));
  if (x.kind === "money" && x.value !== "" && !isNaN(n)) return cents.format(n);
  if (x.kind === "percent" && x.value !== "" && !isNaN(n)) return n + "%";
  if (x.kind === "date" && /^\d{4}-\d{2}-\d{2}$/.test(x.value)) return longDate(x.value);
  if (x.kind === "month" && /^\d{4}-\d{2}$/.test(x.value))
    return MONTH_SHORT[Number(x.value.slice(5)) - 1] + " " + x.value.slice(0, 4);
  if (x.kind === "months" && !isNaN(n) && n) return n + " months" + (n % 12 === 0 ? " (" + n / 12 + " years)" : "");
  return x.value;
}

function drawMortgage(body: HTMLElement, d: HomePage, loans: HomeLine[]) {
  var ids = loans.map(function (l) {
    return l.key.slice(5);
  });
  ids.forEach(function (id) {
    var facts = (d.loans || []).filter(function (x) {
      return x.loan === id;
    });
    if (!facts.length) return;
    var box = el("section", null, "panel w-wide");
    box.appendChild(el("h3", "The loan" + (ids.length > 1 ? ": " + id : "")));
    box.appendChild(
      el(
        "p",
        "Its terms from the ledger's loans row and its facts (the closing disclosure's, a servicer's " +
          "letters: add more with a loan_facts file in the inbox), and where the schedule stands.",
        "support",
      ),
    );
    var grid = el("dl", null, "loan-facts");
    facts.forEach(function (x) {
      var pair = el("div");
      pair.appendChild(el("dt", x.label));
      var dd = el("dd", loanValue(x));
      var more = [
        x.note,
        x.asOf && x.kind !== "date" && x.key !== "balance" ? "since " + (x.asOf.length === 7 ? x.asOf : longDate(x.asOf)) : "",
      ]
        .filter(Boolean)
        .join(", ");
      if (x.key === "balance" && x.asOf)
        more = "after the " + MONTH_SHORT[Number(x.asOf.slice(5)) - 1] + " " + x.asOf.slice(0, 4) + " payment";
      if (more) dd.appendChild(el("span", more, "support"));
      pair.appendChild(dd);
      grid.appendChild(pair);
    });
    box.appendChild(grid);
    body.appendChild(box);
  });
  var sched = d.schedule.filter(function (p) {
    return ids.indexOf(p.loan) >= 0;
  });
  if (sched.length) {
    var am = el("section", null, "panel w-wide");
    am.appendChild(el("h3", "Amortization"));
    var ahead = sched.filter(function (p) {
      return p.month > d.month;
    });
    var paid = sched.filter(function (p) {
      return p.month <= d.month;
    });
    var thisYear = paid.filter(function (p) {
      return p.month.slice(0, 4) === d.month.slice(0, 4);
    });
    var sum = function (rows: Payment[], k: "interest") {
      return rows.reduce(function (s, p) {
        return s + p[k];
      }, 0);
    };
    am.appendChild(
      el(
        "p",
        (ahead.length
          ? "Paid off " +
            MONTH_SHORT[Number(ahead[ahead.length - 1].month.slice(5)) - 1] +
            " " +
            ahead[ahead.length - 1].month.slice(0, 4) +
            " on the current schedule, with " +
            money.format(sum(ahead, "interest")) +
            " of interest still to pay. "
          : "") +
          "Interest paid so far: " +
          money.format(sum(paid, "interest")) +
          ", " +
          money.format(sum(thisYear, "interest")) +
          " of it this year. Extra principal and recasts change " +
          "the schedule once they are in loan_events.",
        "support",
      ),
    );
    lineChart(
      am,
      ids.map(function (id, i) {
        return {
          name: "Balance" + (ids.length > 1 ? " " + id : ""),
          color: "var(--series-" + (i + 1) + ")",
          points: sched
            .filter(function (p) {
              return p.loan === id;
            })
            .map(function (p) {
              return [p.month, p.balance] as [string, number];
            }),
        };
      }),
      {
        label: "Loan balance each month to payoff",
        now: d.month,
        dateName: function (m) {
          return MONTH_SHORT[Number(m.slice(5)) - 1] + " " + m.slice(0, 4);
        },
      },
    );
    var years = el("details");
    years.appendChild(el("summary", "By year"));
    var yt = wTable(
      years,
      ["Year", "Payments", "Interest", "Principal", "Extra", "Escrow", "Balance at year end"],
      [1, 2, 3, 4, 5, 6],
    );
    d.years
      .filter(function (y) {
        return ids.indexOf(y.loan) >= 0;
      })
      .forEach(function (y) {
        yt.row(
          [
            y.year + (y.projected ? " (ahead)" : ""),
            String(y.payments),
            money.format(y.interest),
            money.format(y.principal),
            y.extra ? money.format(y.extra) : "–",
            money.format(y.escrow),
            money.format(y.endBalance),
          ],
          y.projected ? "projected" : "",
        );
      });
    am.appendChild(years);
    body.appendChild(am);
  }
  if (!sched.length) body.appendChild(el("p", "No loan on this home.", "support"));
}

// Work on a home, added, corrected or removed here (an `improvement` edit; the latest row for a home, date and
// description stands, house.improvements). A transaction page's "Add to a home's work" leaves its line here as
// a draft (improvementDraft), so a payment becomes its work with the line as its receipt.
var improvementDraft: { date: string; description: string; cost: string; receipt: string } | null = null;
var IMPROVEMENT_WORK = ["capital", "repair", "energy_credit"];

function improvementTarget(property: string, date: string, description: string) {
  return editTarget({ kind: "improvement", property_id: property, date: date, description: description });
}

// a receipt as something to open: a transaction's page, or the link it was kept as
function receiptLink(receipt: string): Node | string {
  if (!receipt) return "–";
  if (/^https:\/\//.test(receipt)) {
    var a = el("a", "Receipt") as HTMLAnchorElement;
    a.href = receipt;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }
  return button(
    "Payment",
    function () {
      openTransaction(receipt);
    },
    "text",
  );
}

// start: the form's values; editing: the work it corrects, when it corrects one (a draft from a line is new work)
function improvementForm(h: Home, start: Record<string, string>, editing: boolean, done: () => void) {
  var form = el("div", null, "improvement-form");
  var field = function (label: string, input: HTMLElement) {
    var l = el("label", null, "improvement-field");
    l.appendChild(el("span", label, "support"));
    l.appendChild(input);
    form.appendChild(l);
    return input;
  };
  var date = field("Date", textInput(start.date || today(), "", "YYYY-MM-DD")) as HTMLInputElement;
  var what = field("Work", textInput(start.description || "", "", "what was done")) as HTMLInputElement;
  var work = field("Kind", choice(IMPROVEMENT_WORK, start.kind || "capital", KIND_NAMES)) as HTMLSelectElement;
  var cost = field("Cost", textInput(start.cost || "", "", "0.00")) as HTMLInputElement;
  var credit = field("Credit", textInput(start.credit || "", "", "0.00")) as HTMLInputElement;
  var receipt = field(
    "Receipt",
    textInput(start.receipt || "", "", "a link, or the paying line's transaction id"),
  ) as HTMLInputElement;
  var said = el("p", null, "support");
  var creditShown = function () {
    (credit.parentNode as HTMLElement).hidden = work.value !== "energy_credit";
  };
  work.onchange = creditShown;
  creditShown();
  form.appendChild(said);
  var act = el("div", null, "form-actions");
  act.appendChild(
    button(
      editing ? "Keep changes" : "Add work",
      function () {
        var wrong = [] as string[];
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date.value.trim())) wrong.push("a date as YYYY-MM-DD");
        if (!what.value.trim()) wrong.push("what was done");
        if (isNaN(parseFloat(cost.value.replace(/[$,]/g, "")))) wrong.push("its cost");
        var r = receipt.value.trim();
        if (r && /^http/.test(r) && !/^https:\/\//.test(r)) wrong.push("an https link");
        if (wrong.length) {
          said.textContent = "Needs " + wrong.join(", ") + ".";
          return;
        }
        if (editing && (start.date !== date.value.trim() || start.description.toLowerCase() !== what.value.trim().toLowerCase()))
          // a new date or name is another piece of work: the old one goes
          stage(
            { kind: "improvement", property_id: h.id, date: start.date, description: start.description, work: "removed" },
            "home " + h.id + ": " + start.description + " replaced",
          );
        var edit: Edit = {
          kind: "improvement",
          property_id: h.id,
          date: date.value.trim(),
          description: what.value.trim(),
          work: work.value,
          cost: cost.value.replace(/[$,]/g, "").trim(),
        };
        if (work.value === "energy_credit" && credit.value.trim()) edit.credit = credit.value.replace(/[$,]/g, "").trim();
        if (r) edit.receipt = r;
        stage(
          edit,
          "home " +
            h.id +
            ": " +
            edit.description +
            ", " +
            (KIND_NAMES[work.value] || work.value).toLowerCase() +
            " " +
            edit.cost,
        );
        done();
      },
      "tonal",
    ),
  );
  act.appendChild(button("Cancel", done, "text"));
  form.appendChild(act);
  return form;
}

function drawImprovements(body: HTMLElement, d: HomePage, h: Home) {
  var im = el("section", null, "panel w-wide");
  im.id = "improvements-" + h.id;
  im.appendChild(el("h3", "Improvements"));
  im.appendChild(
    el(
      "p",
      "Capital improvements add their cost to basis; energy-credit work adds its cost less the credit; " +
        "repairs add nothing. Add or correct work here, with its receipt: a link, or the line that paid it.",
      "support",
    ),
  );
  var redraw = function () {
    drawHome();
    var at = document.getElementById("improvements-" + h.id);
    if (at) at.scrollIntoView({ block: "nearest" });
  };
  var formAt = el("div");
  im.appendChild(formAt);
  var openForm = function (start: Record<string, string>, editing: boolean) {
    formAt.textContent = "";
    formAt.appendChild(improvementForm(h, start, editing, redraw));
    adding.hidden = true; // one form at a time; done redraws the page
  };
  var adding = button(
    "Add work",
    function () {
      openForm({}, false);
    },
    "tonal",
  );
  im.appendChild(adding);
  if (improvementDraft) {
    var draft = improvementDraft;
    improvementDraft = null;
    openForm(draft, false);
  }
  var work = d.improvements.filter(function (w) {
    return w.property === h.id;
  });
  // work staged here and not yet in the books shows too, marked
  var known: Record<string, boolean> = {};
  work.forEach(function (w) {
    known[improvementTarget(h.id, w.date, w.description)] = true;
  });
  var fresh = Object.keys(staged)
    .map(function (t) {
      return staged[t].edit as Record<string, string>;
    })
    .filter(function (e) {
      return e.kind === "improvement" && e.property_id === h.id && e.work !== "removed" && !known[editTarget(e as Edit)];
    });
  if (!work.length && !fresh.length) {
    im.appendChild(el("p", "None recorded yet.", "support"));
    body.appendChild(im);
    return;
  }
  var it = wTable(im, ["Date", "Work", "Kind", "Cost", "Credit", "Adds to basis", "Receipt", ""], [3, 4, 5]);
  var total = { cost: 0, added: 0 };
  work.forEach(function (w) {
    var t = improvementTarget(h.id, w.date, w.description);
    var change = staged[t] ? (staged[t].edit as Record<string, string>) : null;
    var acts = el("span", null, "row-acts");
    if (change) {
      acts.appendChild(el("span", change.work === "removed" ? "removed, not saved" : "changed, not saved", "support"));
      acts.appendChild(
        button(
          "Undo",
          function () {
            unstage(t);
            redraw();
          },
          "text",
        ),
      );
    } else {
      acts.appendChild(
        button(
          "Change",
          function () {
            openForm(
              {
                date: w.date,
                description: w.description,
                kind: w.kind,
                cost: String(w.cost),
                credit: w.credit ? String(w.credit) : "",
                receipt: w.receipt || "",
              },
              true,
            );
          },
          "text",
        ),
      );
      acts.appendChild(
        button(
          "Remove",
          function () {
            stage(
              { kind: "improvement", property_id: h.id, date: w.date, description: w.description, work: "removed" },
              "home " + h.id + ": " + w.description + " removed",
            );
            redraw();
          },
          "text danger",
        ),
      );
    }
    var row = it.row(
      [
        longDate(w.date),
        w.description,
        KIND_NAMES[w.kind] || w.kind,
        money.format(w.cost),
        w.credit ? money.format(w.credit) : "–",
        money.format(w.basisAdded),
        receiptLink(w.receipt),
        acts,
      ],
      change ? "changed" : "",
    );
    row.classList.toggle("struck", !!change && change.work === "removed");
    total.cost += w.cost;
    total.added += w.basisAdded;
  });
  fresh.forEach(function (e) {
    var t = editTarget(e as Edit);
    var cost = Number(e.cost) || 0,
      credit = Number(e.credit) || 0;
    var added = e.work === "capital" ? cost : e.work === "energy_credit" ? cost - credit : 0;
    var acts = el("span", null, "row-acts");
    acts.appendChild(el("span", "new, not saved", "support"));
    acts.appendChild(
      button(
        "Undo",
        function () {
          unstage(t);
          redraw();
        },
        "text",
      ),
    );
    it.row(
      [
        longDate(e.date),
        e.description,
        KIND_NAMES[e.work] || e.work,
        money.format(cost),
        credit ? money.format(credit) : "–",
        money.format(added),
        receiptLink(e.receipt || ""),
        acts,
      ],
      "changed",
    );
  });
  it.row(["Total, as recorded", "", "", money.format(total.cost), "", money.format(total.added), "", ""], "total");
  body.appendChild(im);
}
