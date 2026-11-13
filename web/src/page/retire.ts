// Retirement: the plan, year by year, strategies, Social Security, budget, assumptions.

// --- Retirement -------------------------------------------------------------------------------------------------
var retire: RetirementPage | null = null,
  retireView = "start",
  retireStrategy = "";
var RETIRE_PARTS: string[][] = [
  ["start", "Start here"],
  ["plan", "Plan"],
  ["budget", "Budget"],
  ["years", "Year by year"],
  ["strategies", "Taking money out"],
  ["ss", "Social Security"],
  ["assumptions", "Assumptions"],
];
var RETIRE_SECTIONS: Record<string, string> = {
  people: "People",
  today: "Balances today",
  saving: "While working, a year",
  spending: "Spending now, a year (the last 12 full months)",
  changes: "Spending changes in retirement",
  health: "Health cover",
  returns: "Returns",
  plan: "The plan",
  law: "Law",
};
var MONEY_SECTIONS = ["today", "saving", "spending", "health"];
var ORDER_SHORT: Record<string, string> = {
  conventional: "Taxable, pre-tax, Roth",
  proportional: "In proportion",
  bracket: "Pre-tax to 12%, then taxable",
};
var RULE_SHORT: Record<string, string> = {
  constant: "Constant real spending",
  guardrails: "Guardrails (Guyton-Klinger)",
  vpw: "Variable percentage withdrawal",
};
var whole = new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 0 });

function loadRetirement() {
  if (retire) {
    drawRetirement();
    return;
  }
  byId("retirement-body").textContent = "Loading…";
  call(
    "getRetirement",
    [],
    function (d) {
      retire = d;
      retireStrategy = d.chosenStrategy;
      drawRetirement();
    },
    "retirement-body",
  );
}

function shareText(v: number | null | undefined) {
  return v === null || v === undefined ? "–" : whole.format(v);
}

function agesIn(ages: string, from: number, year: number) {
  // "59/58" in from, as of year
  return ages
    .split("/")
    .map(function (a) {
      return Number(a) + year - from;
    })
    .join("/");
}

function agesWords(ages: string) {
  var a = ages.split("/");
  return a.length === 2 ? a[0] + " and " + a[1] : a.join(", ");
}

// The plan's figures as every page's head draws them: what each is above, the number below.
function retireFigures(parent: HTMLElement, items: string[][]) {
  var box = el("div", null, "w-figures");
  items.forEach(function (f) {
    var c = el("div", null, "w-figure a2-card");
    c.appendChild(el("span", f[1].charAt(0).toUpperCase() + f[1].slice(1), "what a2-label"));
    c.appendChild(el("span", f[0], "num a2-big"));
    box.appendChild(c);
  });
  parent.appendChild(box);
}

function drawRetirement() {
  var d = retire!, // drawn once loaded
    body = byId("retirement-body");
  body.innerHTML = "";
  subtabs(body, "Retirement", RETIRE_PARTS, retireView, function (v) {
    retireView = v;
    drawRetirement();
  });
  if (!d.years.length && retireView !== "assumptions" && retireView !== "start") {
    body.appendChild(
      el(
        "p",
        d.notes.length
          ? d.notes.join(" ")
          : "No retirement plan yet: run compute. It needs each adult's birth " +
              "date, from their Social Security statement in the inbox or the birth_date setting.",
        "support",
      ),
    );
    return;
  }
  (
    ({
      start: drawRetireStart,
      plan: drawRetirePlan,
      budget: drawRetireBudget,
      years: drawRetireYears,
      strategies: drawRetireStrategies,
      ss: drawRetireSocialSecurity,
      assumptions: drawRetireAssumptions,
    }) as Record<string, (body: HTMLElement, d: RetirementPage) => void>
  )[retireView](body, d);
}

function chosenYear(d: RetirementPage) {
  return (
    d.years.filter(function (r) {
      return r.chosen;
    })[0] || d.years[d.years.length - 1]
  );
}

function strategyOf(d: RetirementPage, key: string) {
  return d.strategies.filter(function (s) {
    return s.key === key;
  })[0];
}

function drawRetirePlan(body: HTMLElement, d: RetirementPage) {
  var y = chosenYear(d),
    s = strategyOf(d, d.chosenStrategy);
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", "Retire in " + y.year + ", at " + agesWords(y.ages)));
  head.appendChild(el("span", "everything in today's dollars", "support"));
  body.appendChild(head);
  retireFigures(body, [
    [shareText(y.monteCarlo), "of Monte Carlo runs never run short"],
    [shareText(y.historical), "of the historical sequences since 1928 never run short"],
    [compactMoney(y.atRetirement), "invested when you retire (the median run)"],
    [money.format(y.firstSpending), "the first year's spending and health cover"],
    [pct.format(y.withdrawalRate), "the first year's withdrawals, of what is invested"],
  ]);
  if (s)
    body.appendChild(
      el(
        "p",
        "Strategy: " + s.label + ". Taking money out compares the others; Assumptions says " + "where every number comes from.",
        "support",
      ),
    );
  if (d.notes.length > 1) {
    var ul = el("ul", null, "w-notes");
    d.notes.slice(1).forEach(function (n) {
      ul.appendChild(el("li", n));
    });
    body.appendChild(ul);
  }
  var plan = d.plans[d.chosenStrategy] || [];
  var byYear: Record<number, PlanYear> = {};
  plan.forEach(function (p) {
    byYear[p.year] = p;
  });
  var mid = y.fixed.length ? y.fixed[Math.floor(y.fixed.length / 2)].scenario : "";
  var chart = el("section", null, "panel w-wide");
  chart.appendChild(el("h3", "What is invested, year by year"));
  chart.appendChild(
    el(
      "p",
      "The Monte Carlo runs' median and their 10th percentile (one run in ten does worse), and the plan at a " +
        "steady " +
        mid +
        " a year before inflation. The Year by year tab has the 90th percentile too.",
      "support",
    ),
  );
  body.appendChild(chart);
  var at = function (f: (p: PlanYear) => number) {
    return plan.map(function (p) {
      return [p.year + "-07-01", f(p)] as [string, number];
    });
  };
  lineChart(
    chart,
    [
      {
        name: "Median",
        color: "var(--series-1)",
        points: at(function (p) {
          return p.p50;
        }),
      },
      {
        name: "10th percentile",
        color: "var(--series-6)",
        points: at(function (p) {
          return p.p10;
        }),
      },
      {
        name: "At " + mid,
        color: "var(--series-4)",
        points: at(function (p) {
          return p.total;
        }),
      },
    ],
    {
      label: "What is invested each year: the Monte Carlo spread and the plan at a steady return",
      dateName: function (dt) {
        var p = byYear[Number(dt.slice(0, 4))];
        return dt.slice(0, 4) + (p ? " (ages " + p.ages + ")" : "");
      },
      now: y.year + "-01-01",
      nowLabel: "retire",
    },
  );

  var when = panel(
    body,
    "When you could retire",
    "For each year, the share of runs that never run short of money, by Monte Carlo and by history, and at each steady " +
      "return (before inflation) whether the money lasts (or the ages it runs out). The plan's year is the first whose Monte Carlo share " +
      "reaches the retirement_success_target setting.",
  );
  when.classList.add("w-wide");
  var names = y.fixed.map(function (f) {
    return f.scenario;
  });
  var heads = ["Retire in", "Ages", "Monte Carlo", "History"]
    .concat(
      names.map(function (n) {
        return "At " + n;
      }),
    )
    .concat(["Invested then", "First year", "Withdrawals"]);
  var numeric = heads
    .map(function (_, i) {
      return i;
    })
    .filter(function (i) {
      return i !== 1 && (i < 4 || i >= 4 + names.length);
    });
  var t = wTable(when, heads, numeric);
  d.years.forEach(function (r) {
    var fixed = names.map(function (n) {
      var f = r.fixed.filter(function (x) {
        return x.scenario === n;
      })[0];
      if (!f) return "–";
      return f.success ? "lasts" : "short at " + agesIn(r.ages, r.year, f.shortFrom as number);
    });
    var row = t.row(
      [String(r.year), r.ages, shareText(r.monteCarlo), shareText(r.historical)]
        .concat(fixed)
        .concat([compactMoney(r.atRetirement), money.format(r.firstSpending), pct.format(r.withdrawalRate)]),
      r.chosen ? "picked" : "",
    );
    if (r.chosen) row.setAttribute("aria-current", "true");
  });
}

function drawRetireYears(body: HTMLElement, d: RetirementPage) {
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", "Year by year"));
  head.appendChild(el("span", "at a steady return, in today's dollars", "support"));
  body.appendChild(head);
  var pick = el("select");
  pick.setAttribute("aria-label", "Strategy");
  d.strategies
    .slice()
    .sort(function (a, b) {
      return (b.monteCarlo || 0) - (a.monteCarlo || 0);
    })
    .forEach(function (s) {
      var o = el("option", (s.chosen ? "The plan: " : "") + s.label + " (" + shareText(s.monteCarlo) + ")");
      o.value = s.key;
      o.selected = s.key === retireStrategy;
      pick.appendChild(o);
    });
  pick.onchange = function () {
    retireStrategy = pick.value;
    drawRetirement();
  };
  var bar = el("div", null, "r-toolbar");
  bar.appendChild(pick);
  body.appendChild(bar);
  var plan = d.plans[retireStrategy] || [];
  var box = panel(
    body,
    "Money in and out each year",
    "While working, what goes into the accounts. Once retired: spending and health cover are paid from Social Security, " +
      "required distributions (RMD) and withdrawals; taxes are federal and Wisconsin together; the range is the Monte " +
      "Carlo runs' middle 80% of what is invested at the year's end.",
  );
  box.classList.add("w-wide");
  var heads = [
    "Year",
    "Ages",
    "Saved",
    "Spending",
    "Health",
    "Social Security",
    "RMD",
    "Taxable",
    "Pre-tax",
    "Roth",
    "HSA",
    "Converted",
    "Taxes",
    "IRMAA",
    "Penalty",
    "Invested",
    "Range",
  ];
  var t = wTable(box, heads, [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
  var blank = function (v: number) {
    return v ? money.format(v) : "–";
  };
  plan.forEach(function (p) {
    var working = p.phase === "working";
    var row = t.row(
      [
        String(p.year),
        p.ages,
        working ? money.format(p.contributions) : "–",
        working ? "–" : money.format(p.spending),
        working ? "–" : money.format(p.health),
        blank(p.socialSecurity),
        blank(p.rmd),
        blank(p.fromTaxable),
        blank(p.fromPreTax),
        blank(p.fromRoth),
        blank(p.fromHsa),
        blank(p.converted),
        blank(p.federalTax + p.stateTax),
        blank(p.irmaa),
        blank(p.penalty),
        compactMoney(p.total),
        compactMoney(p.p10) + "–" + compactMoney(p.p90),
      ],
      (working ? "sub" : "") + (p.short > 1 ? " short" : ""),
    );
    if (p.short > 1) row.title = money.format(p.short) + " short this year";
  });
}

// ---- what the methods mean, with where each comes from ----

var RETIRE_SOURCES: Record<string, [string, string]> = {
  bengen: [
    "Bengen, Determining Withdrawal Rates Using Historical Data (1994)",
    "https://www.financialplanningassociation.org/sites/default/files/2021-04/MAR04%20Determining%20Withdrawal%20Rates%20Using%20Historical%20Data.pdf",
  ],
  guyton: [
    "Guyton and Klinger, Decision Rules and Maximum Initial Withdrawal Rates (2006)",
    "https://www.financialplanningassociation.org/sites/default/files/2021-11/2006%20-%20Guyton%20and%20Klinger%20-%20Decision%20Rules%20and%20SWR%20(1).PDF",
  ],
  guardrails: [
    "Kitces, on Guyton-Klinger guardrails",
    "https://www.kitces.com/blog/guyton-klinger-guardrails-retirement-income-rules-risk-based/",
  ],
  vpw: ["Variable percentage withdrawal (finiki)", "https://www.finiki.org/wiki/Variable_percentage_withdrawal"],
  order: [
    "Kitces, tax-efficient retirement draw-down",
    "https://www.financial-planning.com/news/kitces-tax-efficient-retirement-draw-down-strategies",
  ],
  proportional: [
    "Cook, Meyer and Reichenstein, Tax-Efficient Withdrawal Strategies (Financial Analysts Journal, 2015)",
    "https://ideas.repec.org/a/taf/ufajxx/v71y2015i2p16-29.html",
  ],
  vanguard: [
    "Vanguard, tax-efficient retirement withdrawals",
    "https://investor.vanguard.com/advice/tax-efficient-retirement-strategy",
  ],
  roth: [
    "Kitces, partial Roth conversions to fill the lower brackets",
    "https://www.kitces.com/blog/using-systematic-partial-roth-ira-conversions-and-recharacterizations-to-fill-the-lower-tax-bracket-buckets/",
  ],
  sepp: [
    "IRS, substantially equal periodic payments",
    "https://www.irs.gov/retirement-plans/substantially-equal-periodic-payments",
  ],
  irmaa: [
    "Social Security, Medicare premiums and the income-related amount (IRMAA)",
    "https://www.ssa.gov/benefits/medicare/medicare-premiums.html",
  ],
  montecarlo: [
    "Kitces, reading a Monte Carlo probability of success",
    "https://www.kitces.com/blog/monte-carlo-retirement-projection-probability-success-adjustment-minimum-odds/",
  ],
  returns: [
    "Damodaran, historical returns on stocks, bonds and bills since 1928",
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histretSP.html",
  ],
  early: [
    "Social Security, starting before full retirement age",
    "https://www.ssa.gov/benefits/retirement/planner/agereduction.html",
  ],
  delayed: ["Social Security, delayed retirement credits", "https://www.ssa.gov/benefits/retirement/planner/delayret.html"],
};

// The terms the page uses, each in plain words with its sources: [term, what it means, source keys].
var RETIRE_TERMS: [string, string, string[]][] = [
  [
    "Lasts (Monte Carlo)",
    "Out of 1,000 made-up futures, each year's stock, bond and inflation return drawn at random from the years since " +
      "1928, the share in which the money never runs short. The plan picks the first year this reaches 90%. It is a " +
      "range of outcomes, not a forecast: below 100% means some bad futures need spending cut, not that you go broke.",
    ["montecarlo", "returns"],
  ],
  ["Lasts (History)", "The same, over the real sequences of years that started in each year since 1928.", ["bengen", "returns"]],
  [
    "After tax at the end",
    "What the median future leaves at the end of the plan, counting pre-tax money after a 24% tax and taxable gains " +
      "after 15%. More is better, once the money lasts.",
    [],
  ],
  [
    "Taxable, pre-tax, Roth",
    "Spend the brokerage account first, then 401(k) and IRA money, and Roth last, so tax-free money grows longest.",
    ["order", "vanguard"],
  ],
  [
    "In proportion",
    "Take from every account in proportion to its size each year, which spreads taxable income evenly over the years.",
    ["proportional"],
  ],
  [
    "Pre-tax to 12%, then taxable",
    "Each year take 401(k) and IRA money up to the top of the 12% bracket while it is taxed lightly, then the " +
      "brokerage account, and Roth last.",
    ["order", "vanguard"],
  ],
  [
    "Convert to the top of a bracket",
    "In the years before Social Security and required withdrawals, move pre-tax money into a Roth, paying the tax " +
      "now up to the top of that bracket, so later withdrawals and required minimums are smaller.",
    ["roth"],
  ],
  [
    "72(t)",
    "Fixed yearly payments from an IRA before 59 1/2 without the 10% early-withdrawal penalty. Once started they must " +
      "go on for five years or until 59 1/2, whichever is later.",
    ["sepp"],
  ],
  [
    "Constant real spending",
    "The same spending every year, raised with inflation: the rule behind the \u201c4% rule\u201d research.",
    ["bengen"],
  ],
  [
    "Guardrails (Guyton-Klinger)",
    "Start like constant spending, but cut spending 10% when withdrawals run 20% above where they started and raise " +
      "it 10% when they run 20% below. Spending moves a little so the money lasts longer.",
    ["guyton", "guardrails"],
  ],
  [
    "Variable percentage withdrawal",
    "Each year spend a share of what is invested, set so the money lasts to the plan's end. It never runs out, but " +
      "spending rises and falls with markets.",
    ["vpw"],
  ],
  [
    "IRMAA",
    "Higher Medicare premiums when income two years earlier is above a threshold; large withdrawals or conversions " +
      "after 63 can set it off.",
    ["irmaa"],
  ],
  [
    "Claiming at 62, 67 or 70",
    "Social Security is cut for each month claimed before full retirement age and raised for each month waited after " +
      "it, to 70, for life (and the spouse's half).",
    ["early", "delayed"],
  ],
];

function sourceLinks(keys: string[]) {
  var span = el("span", null, "sources");
  keys.forEach(function (k, i) {
    var s = RETIRE_SOURCES[k];
    if (!s) return;
    if (i) span.appendChild(document.createTextNode("; "));
    var a = el("a", s[0]) as HTMLAnchorElement;
    a.href = s[1];
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    span.appendChild(a);
  });
  return span;
}

// The terms, each a line that opens to what it means and its sources.
function retireTerms(parent: HTMLElement, which?: string[]) {
  var list = el("div", null, "retire-terms");
  RETIRE_TERMS.forEach(function (t) {
    if (which && which.indexOf(t[0]) < 0) return;
    var d = el("details", null, "retire-term");
    d.appendChild(el("summary", t[0]));
    d.appendChild(el("p", t[1]));
    if (t[2].length) {
      var src = el("p", "Source: ", "support");
      src.appendChild(sourceLinks(t[2]));
      d.appendChild(src);
    }
    list.appendChild(d);
  });
  parent.appendChild(list);
  return list;
}

// Start here: the plan in a sentence, how to read each part, and the three numbers the page leans on.
function drawRetireStart(body: HTMLElement, d: RetirementPage) {
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", "How to read this page"));
  head.appendChild(el("span", "everything is in today's dollars", "support"));
  body.appendChild(head);
  if (d.years.length) {
    var y = chosenYear(d),
      s = strategyOf(d, d.chosenStrategy);
    body.appendChild(
      el(
        "p",
        "The plan: retire in " +
          y.year +
          " (at " +
          agesWords(y.ages) +
          "), spending " +
          money.format(y.firstSpending) +
          " the first year. The money lasts in " +
          shareText(y.monteCarlo) +
          " of simulated futures" +
          (s ? ", taking money out by " + strategyWords(s) : "") +
          ".",
        "retire-answer",
      ),
    );
  } else body.appendChild(el("p", "No plan yet: run compute once each adult's birth date is known.", "support"));
  var parts = el("section", null, "panel w-wide");
  parts.appendChild(el("h3", "Where to look"));
  var ul = el("ul", null, "retire-parts");
  [
    ["plan", "Plan", "the answer: when, how much is invested then, and the first year's spending."],
    ["budget", "Budget", "what you spend now and what the plan spends in retirement, line by line; change any line."],
    ["years", "Year by year", "each year's income, spending, taxes and balances, if you want the detail."],
    ["strategies", "Taking money out", "the order to draw accounts down and how much to spend, compared side by side."],
    ["ss", "Social Security", "each person's benefit at each claiming age, from their earnings record."],
    ["assumptions", "Assumptions", "every number the plan starts from, where it came from, and the setting that changes it."],
  ].forEach(function (p) {
    var li = el("li");
    var go = button(
      p[1],
      function () {
        retireView = p[0];
        drawRetirement();
      },
      "text",
    );
    li.appendChild(go);
    li.appendChild(document.createTextNode(" " + p[2]));
    ul.appendChild(li);
  });
  parts.appendChild(ul);
  body.appendChild(parts);
  var terms = el("section", null, "panel w-wide");
  terms.appendChild(el("h3", "Three numbers to know"));
  retireTerms(terms, ["Lasts (Monte Carlo)", "Lasts (History)", "After tax at the end"]);
  body.appendChild(terms);
}

// The picked strategy in words: its order, its conversions, its spending rule.
function strategyWords(s: RetireStrategy) {
  var order: Record<string, string> = {
    conventional: "spending the brokerage account first, then 401(k) and IRA money, then Roth",
    proportional: "taking from every account in proportion",
    bracket: "taking 401(k) and IRA money to the top of the 12% bracket first",
  };
  var rule: Record<string, string> = {
    constant: "the same spending each year",
    guardrails: "spending trimmed or raised 10% at the guardrails",
    vpw: "spending a share of what is invested each year",
  };
  return (
    (order[s.order] || s.order) +
    (s.convertTo ? ", converting to Roth up to the top of the " + whole.format(s.convertTo) + " bracket" : "") +
    (s.sepp ? ", with a 72(t) plan before 59 1/2" : "") +
    ", with " +
    (rule[s.rule] || s.rule)
  );
}

var retireAllMeasures = false; // every column of the comparisons, or the few that decide

// Make a strategy the plan: the retirement_strategy setting, staged like any change and run at the next compute.
function pickStrategy(s: RetireStrategy, done: () => void) {
  withEditor(function () {
    var known = (editor!.parameters || []).filter(function (p) {
      return p.key === "retirement_strategy" && p.source !== "not set";
    })[0];
    stage(
      { kind: "parameter", key: "retirement_strategy", value: s.key, was: known ? { value: known.value } : null },
      "retirement plan: " + s.label,
    );
    done();
  }, "retirement-body");
}

function strategyPick(s: RetireStrategy, redraw: () => void): HTMLElement {
  var t = editTarget({ kind: "parameter", key: "retirement_strategy" });
  var e = edited(t);
  var said = e ? String((e.edit as Record<string, string>).value) : "";
  if (said === s.key) {
    var box = el("span");
    box.appendChild(el("span", stagedText(t) ? "the plan once saved" : "the plan after the next run", "tag warn"));
    if (staged[t])
      box.appendChild(
        button(
          "Undo",
          function () {
            unstage(t);
            redraw();
          },
          "text",
        ),
      );
    return box;
  }
  if (s.chosen && !said) return el("span", "The plan", "tag ok");
  var b = button(
    "Make it the plan",
    function () {
      pickStrategy(s, redraw);
    },
    "text",
  );
  b.setAttribute("aria-label", "Make it the plan: " + s.label);
  return b;
}

function drawRetireStrategies(body: HTMLElement, d: RetirementPage) {
  var redraw = function () {
    drawRetirement();
  };
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", "Taking money out"));
  head.appendChild(el("span", "retiring in " + (d.chosenYear || "\u2013") + ", everything in today's dollars", "support"));
  body.appendChild(head);
  var picked = strategyOf(d, d.chosenStrategy);
  if (picked)
    body.appendChild(
      el(
        "p",
        "The plan takes money out by " +
          strategyWords(picked) +
          ". It lasts in " +
          shareText(picked.monteCarlo) +
          " of simulated futures and leaves " +
          compactMoney(picked.afterTax) +
          " after tax at the end. Below, every other way, best first: the first column that matters is how often " +
          "the money lasts, then what is left.",
        "retire-answer",
      ),
    );
  var toggle = button(
    retireAllMeasures ? "Show fewer measures" : "Show every measure",
    function () {
      retireAllMeasures = !retireAllMeasures;
      redraw();
    },
    "text",
  );
  toggle.setAttribute("aria-pressed", String(retireAllMeasures));
  body.appendChild(toggle);
  var sorted = function (list: RetireStrategy[]) {
    return list.slice().sort(function (a, b) {
      return (b.monteCarlo || 0) - (a.monteCarlo || 0) || b.afterTax - a.afterTax;
    });
  };
  var mark = function (s: RetireStrategy) {
    return s.chosen ? "picked" : "";
  };
  var all = retireAllMeasures;
  var base = d.strategies.filter(function (s) {
    return s.rule === "constant" && !s.claims;
  });
  var order = panel(
    body,
    "Which account first, and Roth conversions",
    "Each spends the same every year; they differ in which accounts pay for it and in the taxes that costs.",
  );
  order.classList.add("w-wide");
  var cols = ["Withdrawals", "Convert to Roth", "Lasts (Monte Carlo)", "After tax at the end", "Lifetime taxes"].concat(
    all ? ["72(t)", "Lasts (History)", "IRMAA", "Penalties", "Converted"] : [],
  );
  var t = wTable(order, cols.concat([""]), all ? [2, 3, 4, 6, 7, 8, 9] : [2, 3, 4]);
  sorted(base).forEach(function (s) {
    var cells: (string | Node)[] = [
      ORDER_SHORT[s.order] || s.order,
      s.convertTo ? "to the top of " + whole.format(s.convertTo) : "none",
      shareText(s.monteCarlo),
      compactMoney(s.afterTax),
      compactMoney(s.tax),
    ];
    if (all)
      cells = cells.concat([
        s.sepp ? "yes" : "",
        shareText(s.historical),
        money.format(s.irmaa),
        money.format(s.penalty),
        compactMoney(s.converted),
      ]);
    cells.push(strategyPick(s, redraw));
    t.row(cells, mark(s)).title = s.label;
  });
  var rules = d.strategies.filter(function (s) {
    return !s.claims && (s.rule !== "constant" || s.chosen);
  });
  var rp = panel(
    body,
    "How much to spend each year",
    "Constant spending is steady; the other two let spending move with markets so the money lasts longer. " +
      "\u201cLowest year\u201d is how lean a bad stretch gets: the 10th percentile of each future's leanest year, " +
      "against the plan's spending.",
  );
  rp.classList.add("w-wide");
  var rcols = ["Spending rule", "Lasts (Monte Carlo)", "Lowest year", "After tax at the end"].concat(
    all ? ["Lasts (History)", "Spent in retirement"] : [],
  );
  var rt = wTable(rp, rcols.concat([""]), all ? [1, 2, 3, 4, 5] : [1, 2, 3]);
  sorted(rules).forEach(function (s) {
    var cells: (string | Node)[] = [
      RULE_SHORT[s.rule] || s.rule,
      shareText(s.monteCarlo),
      whole.format(s.lowestSpending),
      compactMoney(s.afterTax),
    ];
    if (all) cells = cells.concat([shareText(s.historical), compactMoney(s.spent)]);
    cells.push(strategyPick(s, redraw));
    rt.row(cells, mark(s)).title = s.label;
  });
  var claims = d.strategies.filter(function (s) {
    return s.claims || s.chosen;
  });
  if (claims.length > 1) {
    var cp = panel(
      body,
      "When to claim Social Security",
      "The plan's way of taking money out, with each adult claiming at 62, 67 or 70. Waiting raises the benefit for " +
        "life; claiming early leaves more invested longer.",
    );
    cp.classList.add("w-wide");
    var ct = wTable(cp, ["Claiming at", "Lasts (Monte Carlo)", "After tax at the end", "Lifetime taxes", ""], [1, 2, 3]);
    sorted(claims).forEach(function (s) {
      ct.row(
        [
          s.claims || "the settings' ages",
          shareText(s.monteCarlo),
          compactMoney(s.afterTax),
          compactMoney(s.tax),
          strategyPick(s, redraw),
        ],
        mark(s),
      );
    });
  }
  var terms = el("section", null, "panel w-wide");
  terms.appendChild(el("h3", "What the methods mean"));
  retireTerms(terms);
  body.appendChild(terms);
}

function drawRetireSocialSecurity(body: HTMLElement, d: RetirementPage) {
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", "Social Security"));
  head.appendChild(el("span", "monthly, today's dollars", "support"));
  body.appendChild(head);
  var grid = el("div", null, "w-grid");
  body.appendChild(grid);
  d.benefits.forEach(function (b) {
    var p = panel(grid, ownerName(b.person) + ", claiming at " + b.claimAge + " (" + b.claimYear + ")", b.note || null);
    var facts = el("dl", null, "facts");
    var add = function (k: string, v: string) {
      facts.appendChild(el("dt", k));
      facts.appendChild(el("dd", v, "amount"));
    };
    add("The statement's estimate", b.statement === null ? "–" : money.format(b.statement));
    add("Figured here, working to " + b.claimAge, money.format(b.computed));
    add("Retiring in " + (d.chosenYear || "–"), money.format(b.plan));
    add("A year once claimed, with any spouse's half", money.format(b.planYearly));
    add("AIME and PIA, retiring then", money.format(b.aime) + " and " + money.format(b.pia));
    p.appendChild(facts);
    var mine = d.earnings.filter(function (e) {
      return e.person === b.person;
    });
    if (mine.length) {
      var more = el("details");
      more.appendChild(
        el(
          "summary",
          "Earnings record: " +
            mine.filter(function (e) {
              return !e.projected;
            }).length +
            " years posted, " +
            mine.filter(function (e) {
              return e.projected;
            }).length +
            " to come",
        ),
      );
      var t = wTable(more, ["Year", "Earnings", "Indexed", "Counted", ""], [1, 2]);
      mine.forEach(function (e) {
        t.row(
          [
            String(e.year),
            money.format(e.earnings),
            money.format(e.indexed),
            e.counted ? "one of the 35" : "",
            e.projected ? "assumed" : "",
          ],
          e.projected ? "sub" : "",
        );
      });
      p.appendChild(more);
    }
  });
  body.appendChild(
    el(
      "p",
      "The statement assumes work until claiming at the last posted year's earnings; retiring sooner leaves " +
        "zeros among the 35 years counted, so the plan's benefit is lower. A spouse gets the larger of their own " +
        "benefit and half the other's at full retirement age, once both have claimed.",
      "support",
    ),
  );
}

// The spending the plan uses, line by line. Each line starts at the last 12 months; a change here is a
// retirement_spending:<line> setting, staged like any other edit, and the next compute reruns the plan.
var budgetOpen = ""; // the line whose editor is open

function budgetSteps(text: string): string[][] {
  // "18000; 9000 from 2040" -> [[null, "18000"], ["2040", "9000"]]
  return String(text || "")
    .split(";")
    .map(function (p) {
      var m = p.trim().match(/^(-?[\d,]+(?:\.\d+)?)(?:\s+from\s+(\d{4}))?$/);
      return m ? [m[2] || "", m[1].replace(/,/g, "")] : null;
    })
    .filter(Boolean) as string[][];
}

function budgetValue(firstFrom: string, first: string, thenFrom: string, then: string) {
  var parts: string[] = [];
  if (first !== "") parts.push(first + (firstFrom ? " from " + firstFrom : ""));
  if (then !== "") parts.push(then + " from " + thenFrom);
  return parts.join("; ");
}

function budgetEditor(line: string, b: BudgetLine | null, start: number, done: () => void) {
  var form = el("form", null, "edit budget-edit");
  var steps = b ? budgetSteps(b.setting) : [];
  var one = steps[0] || ["", b ? String(Math.round(b.now)) : ""],
    two = steps[1] || ["", ""];
  var name = b ? null : field(form, "Line", textInput("", "", "travel"));
  var first = field(form, "A year", textInput(one[1], "", "0"));
  var firstFrom = field(form, "From (blank: from " + start + ")", textInput(one[0], "", String(start)));
  var then = field(form, "Then a year (optional)", textInput(two[1], "", ""));
  var thenFrom = field(form, "From", textInput(two[0], "", ""));
  [first, then].forEach(function (i) {
    i.inputMode = "decimal";
  });
  [firstFrom, thenFrom].forEach(function (i) {
    i.inputMode = "numeric";
  });
  var error = el("div", "", "error");
  function put(value: string, words: string) {
    var key = b
      ? b.line
      : name!.value
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "_")
          .replace(/^_|_$/g, "");
    if (!/^[a-z][a-z0-9_]*$/.test(key)) {
      error.textContent = "Name the line with letters, digits and _.";
      return;
    }
    var e = {
      kind: "parameter",
      key: "retirement_spending:" + key,
      value: value,
      was: b && b.isSet ? { value: b.setting } : null,
    };
    stage(e, "retirement budget: " + key + " " + words);
    done();
  }
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(
      "Keep this change",
      function () {
        var a = first.value.trim().replace(/,/g, ""),
          af = firstFrom.value.trim(),
          t = then.value.trim().replace(/,/g, ""),
          tf = thenFrom.value.trim();
        var num = /^-?\d+(\.\d+)?$/,
          yr = /^\d{4}$/;
        if (!num.test(a)) {
          error.textContent = "A year is an amount in today's dollars, such as 12000.";
          return;
        }
        if (af && !yr.test(af)) {
          error.textContent = "From is a year, such as " + start + ".";
          return;
        }
        if (t && (!num.test(t) || !yr.test(tf))) {
          error.textContent = "Then needs an amount and the year it starts.";
          return;
        }
        if (t && af && Number(tf) <= Number(af)) {
          error.textContent = "Then starts after the first change.";
          return;
        }
        var v = budgetValue(af, a, tf, t);
        put(v, "= " + v);
      },
      "filled",
    ),
  );
  if (b)
    actions.appendChild(
      button("Leave out", function () {
        put("0", "left out (0)");
      }),
    );
  if (b && b.isSet && b.setting)
    actions.appendChild(
      button("Back to actual", function () {
        put("", "back to the last 12 months");
      }),
    );
  actions.appendChild(
    button("Cancel", function () {
      done();
    }),
  );
  form.appendChild(actions);
  form.appendChild(error);
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
  });
  return form;
}

function drawRetireBudget(body: HTMLElement, d: RetirementPage) {
  var lines = d.budget || [];
  var y = lines.length ? lines[0].retireYear : d.chosenYear || 0;
  var plan = d.plans[d.chosenStrategy] || [];
  var start = plan.length ? plan[0].year : y || new Date().getFullYear() + 1;
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", "Spending in retirement"));
  head.appendChild(el("span", "a year, in today's dollars", "support"));
  body.appendChild(head);
  if (!lines.length) {
    body.appendChild(el("p", "None yet: run compute.", "support"));
    return;
  }
  var counted = function (k: "firstYear" | "atRetirement") {
    return lines.reduce(function (s, b) {
      return s + (b.kind !== "category" && b[k] !== null ? (b[k] as number) : 0);
    }, 0);
  };
  var now = lines.reduce(function (s, b) {
    return s + (b.kind === "group" ? b.now : 0);
  }, 0);
  retireFigures(body, [
    [money.format(now), "spent in the last 12 months"],
    [money.format(counted("firstYear")), "the plan's spending in " + start],
    [money.format(counted("atRetirement")), "the plan's spending in " + y + ", when you retire"],
  ]);
  body.appendChild(
    el(
      "p",
      "Each line starts at what the last 12 months spent. Change a line that won't carry on (a renovation, " +
        "the kids' costs, a car), leave it out, or add one you expect (travel). Changes wait in the bar " +
        "below until Save; the next compute reruns the plan with them. Health cover and taxes are worked " +
        "out separately and aren't here.",
      "support",
    ),
  );
  var p = el("section", null, "panel w-wide");
  body.appendChild(p);
  var actions = el("div", null, "actions");
  actions.appendChild(
    button("Add a line", function () {
      budgetOpen = "+";
      drawRetirement();
    }),
  );
  p.appendChild(actions);
  if (budgetOpen === "+")
    p.appendChild(
      budgetEditor("+", null, start, function () {
        budgetOpen = "";
        drawRetirement();
      }),
    );
  var t = wTable(p, ["Line", "Last 12 months", "In " + start, "In " + y, "Setting", ""], [1, 2, 3]);
  var cols = 6;
  lines.forEach(function (b) {
    var pending = edited(editTarget({ kind: "parameter", key: "retirement_spending:" + b.line }));
    var name = el("span", b.kind === "category" ? b.line : b.line);
    var cellName = el("span");
    cellName.appendChild(name);
    if (b.kind === "added") cellName.appendChild(el("span", "added", "tag plain"));
    if (b.kind === "loan") cellName.appendChild(el("span", "loan ends", "tag plain"));
    if (b.lumpy !== null) {
      var lump = el("span", "one-off?", "tag warn");
      lump.title = whole.format(b.lumpy) + " of it came in two months, so it may not recur";
      cellName.appendChild(lump);
    }
    if (pending) cellName.appendChild(el("span", "edited", "tag"));
    var change =
      b.kind === "loan"
        ? ""
        : button(
            budgetOpen === b.line ? "Close" : "Change",
            function () {
              budgetOpen = budgetOpen === b.line ? "" : b.line;
              drawRetirement();
            },
            "small",
          );
    var amount = function (v: number | null) {
      return v === null ? "in group" : money.format(v);
    };
    var row = t.row(
      [
        cellName,
        b.kind === "loan" || b.kind === "added" ? "" : money.format(b.now),
        amount(b.firstYear),
        amount(b.atRetirement),
        pending ? (pending.edit.value as string) || "(actual)" : b.setting || b.note,
        change,
      ],
      b.kind === "group" ? "budget-group" : b.kind === "category" ? "budget-member" : "",
    );
    if (budgetOpen === b.line) {
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = cols;
      td.appendChild(
        budgetEditor(b.line, b, start, function () {
          budgetOpen = "";
          drawRetirement();
        }),
      );
      tr.appendChild(td);
      row.parentNode!.insertBefore(tr, row.nextSibling);
    }
  });
}

function drawRetireAssumptions(body: HTMLElement, d: RetirementPage) {
  var head = el("div", null, "w-head");
  head.appendChild(el("h2", "Assumptions"));
  head.appendChild(
    button("Open Settings", function () {
      openPart("status", "edit:settings");
    }),
  );
  body.appendChild(head);
  body.appendChild(
    el(
      "p",
      "Every number the plan starts from and where it comes from. The setting named beside one changes it: " +
        "retirement_spending:<category or group> takes an amount a year, or amounts from years " +
        "(0 from 2032; 9000 from 2040), for spending that stops or changes once you retire.",
      "support",
    ),
  );
  if (!d.assumptions.length) {
    body.appendChild(el("p", "None yet: run compute.", "support"));
    return;
  }
  Object.keys(RETIRE_SECTIONS).forEach(function (sec) {
    var rows = d.assumptions.filter(function (a) {
      return a.section === sec;
    });
    if (!rows.length) return;
    var p = panel(body, RETIRE_SECTIONS[sec]);
    p.classList.add("w-wide");
    var t = wTable(p, ["What", "Value", "From", "Setting"], [1]);
    rows.forEach(function (a) {
      var v =
        MONEY_SECTIONS.indexOf(sec) >= 0 && a.value !== "" && !isNaN(Number(a.value)) ? money.format(Number(a.value)) : a.value;
      // the setting behind an assumption is changed here, beside it (evidence.ts)
      t.row([a.label, v, a.source, a.setting ? settingControl(a.setting, a.value) : ""]);
    });
  });
}
