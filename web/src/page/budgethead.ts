// The Lines tab's head, as the A2 design has it: what went out and what came in, the kinds, and four cards
// (budgetviews.ts swaps in a card per kind for the view by kind).

// The period at a glance: what is budgeted, what is spent, and a gauge of how full the budget is against
// how far through the period it is, then the same for each group.
// The period at the top, as the A2 design has it: what went out and what came in, the kinds a line item can
// be, and four cards: take-home pay, the plan, what went out, and what the period drew from (or left in) cash.
function budgetSummary(v: BudgetView) {
  var box = el("section", null, "a2-head");
  var top = el("div", null, "a2-top");
  var titles = el("div", null, "a2-titles");
  titles.appendChild(el("span", "After the paycheck, " + periodName(v.period) + (v.sameWeeks ? " so far" : ""), "a2-eyebrow"));
  var came = v.pay + v.otherIncome;
  titles.appendChild(
    el("h2", money.format(v.actual) + " went out" + (came > 0 ? ", " + money.format(came) + " came in" : ""), "a2-headline"),
  );
  top.appendChild(titles);
  // a key to the kinds' marks, not controls: plain words beside each mark
  var legend = el("div", null, "a2-legend");
  legend.setAttribute("aria-hidden", "true");
  ["Recurring", "Floating", "Annual", "Fixed"].forEach(function (k) {
    var item = el("span", null, "key-item");
    item.appendChild(kindMark(k));
    item.appendChild(document.createTextNode(k));
    legend.appendChild(item);
  });
  top.appendChild(legend);
  box.appendChild(top);
  var cards = el("div", null, "a2-cards");
  var card = function (label: string, big: string, small: string, cls?: string) {
    var c = el("div", null, "a2-card" + (cls ? " " + cls : ""));
    c.appendChild(el("span", label, "a2-label"));
    c.appendChild(el("span", big, "a2-big"));
    c.appendChild(el("span", small, "a2-small"));
    cards.appendChild(c);
  };
  card(
    "Take-home pay",
    money.format(v.pay),
    came > 0
      ? "Both paychecks, after tax and payroll deductions; " + money.format(v.otherIncome) + " other income"
      : "No pay in this period's flows yet",
  );
  card("Planned", money.format(v.target), "Every line item's budget, savings included");
  var d = v.actual - v.compared;
  var against = planWords(v) + "; " + (Math.abs(d) < 0.5 ? "the same as " : signedMoney(d) + " against ") + compareName(v);
  card("Went out", money.format(v.actual), against);
  // on a phone the cards keep only their numbers, so the one sentence that judges the period sits under the headline
  titles.appendChild(el("p", against, "a2-sub"));
  if (came > 0)
    card(
      v.actual > came + 0.5 ? "From cash balances" : "Kept",
      money.format(Math.abs(v.actual - came)),
      v.actual > came + 0.5 ? "What the period drew down beyond its income" : "What came in and stayed",
      v.actual > came + 0.5 ? "warn" : "ok",
    );
  else card("Recurring", money.format(v.recurring), "What the recurring charges took");
  box.appendChild(layoutNow() === "kind" ? kindCards(budgetItems(v)) : cards);
  return box;
}

// How the period stands against its plan, each line item by its own measure: the lines judged on the period
// against their budgets, and apart from them what lines judged on their year spent (a yearly payment landing
// this month is no overspend; one past its year is said by name).
function planWords(v: BudgetView) {
  var actual = 0,
    target = 0,
    yearly = 0;
  var past: string[] = [];
  budgetItems(v).forEach(function (g) {
    g.items.forEach(function (x) {
      if (!x.judged.yearly) {
        actual += x.actual;
        target += x.target;
        return;
      }
      yearly += x.actual;
      if (x.judged.over) past.push(x.name);
    });
  });
  var over = actual - target;
  var words =
    Math.abs(over) < 0.5 ? "As planned" : money.format(Math.abs(over)) + (over > 0 ? " more than planned" : " under plan");
  if (yearly > 0.5) words += ", besides " + money.format(yearly) + " on lines judged on their year";
  if (past.length) words += " (" + past.join(" and ") + (past.length > 1 ? " are past their year)" : " is past its year)");
  return words;
}
