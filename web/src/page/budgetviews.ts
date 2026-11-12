// The Lines tab's three views of one period, as the mockups drew them: A (the table by group, budget.ts's
// budgetTable), B (the line items by kind: what is spoken for, then what floats) and C (a card per group).
// B and C are drawn here from the same line items as A and open the same drawer.

interface BudgetItem {
  name: string;
  group: string; // the group's name, shown
  groupId: string; // its name in the ledger
  kind: string; // Recurring, Floating, Annual, Fixed, Mixed or Unplanned
  how: string; // how its budget is set, in a few words
  status: LineStatus | null;
  target: number;
  actual: number;
  category: string; // the category whose drawer it opens, or "" for a loan's parts together
  loan: string; // the loan whose parts it rolls up, or ""
  balance: number | null; // an annual line item's fund at the period's last month so far
}

interface BudgetItemGroup {
  name: string;
  target: number;
  actual: number;
  items: BudgetItem[];
}

var LAYOUTS: [string, string][] = [
  ["group", "By group"],
  ["kind", "By kind"],
  ["cards", "Cards"],
];

// Every line item in the period, by group, as the three views show them. A loan's parts (its loan_split
// setting's categories) are one item when two or more are in the period.
function budgetItems(v: BudgetView): BudgetItemGroup[] {
  var loanOf = budget.data!.book.loans || {}; // category -> loan id, from compute
  return v.groups.map(function (g) {
    var items: BudgetItem[] = [];
    var parts: Record<string, BudgetItem[]> = {};
    g.lines.forEach(function (l) {
      var lines = linesFor(l.category).filter(function (x) {
        return x.in_force !== "ended";
      });
      var recurringRule = lines.length === 1 && lines[0].rule === "recurring";
      var how =
        lines.length > 1
          ? lines.length + " budget lines"
          : recurringRule && l.recurring.length
            ? countOf(l.recurring.length, "recurring charge", "recurring charges")
            : lines.length
              ? shortHow(lines[0])
              : l.target
                ? "Scheduled"
                : "Not budgeted";
      var st = lineStatus(v, l, lines);
      if (!st && l.recipients.length && v.deductible)
        st = { text: money.format(v.deductible.given) + " deductible in " + v.deductible.year, tone: "" };
      var item: BudgetItem = {
        name: lines.length === 1 ? pretty(lines[0].line) : pretty(l.category),
        group: pretty(g.name),
        groupId: g.name,
        kind: kindOf(lines) || (l.target ? "Fixed" : "Unplanned"),
        how: how,
        status: st,
        target: l.target,
        actual: l.actual,
        category: l.category,
        loan: "",
        balance: l.balance,
      };
      var id = loanOf[l.category];
      if (id) (parts[id] = parts[id] || []).push(item);
      else items.push(item);
    });
    Object.keys(parts).forEach(function (id) {
      var p = parts[id];
      if (p.length < 2) {
        items.push(p[0]);
        return;
      }
      items.push({
        name: pretty(id),
        group: pretty(g.name),
        groupId: g.name,
        kind: "Recurring",
        how: p.length + " parts, from the amortization schedule and the escrow",
        status: null,
        target: toCents(
          p.reduce(function (t, x) {
            return t + x.target;
          }, 0),
        ),
        actual: toCents(
          p.reduce(function (t, x) {
            return t + x.actual;
          }, 0),
        ),
        category: "",
        loan: id,
        balance: null,
      });
    });
    items.sort(function (a, b) {
      return b.target - a.target || b.actual - a.actual || a.name.localeCompare(b.name);
    });
    return { name: pretty(g.name), target: g.target, actual: g.actual, items: items };
  });
}

// Open an item: a line item's drawer, or for a loan the table by group with its parts open.
function openItem(item: BudgetItem, v: BudgetView) {
  if (item.loan) {
    budget.layout = "group";
    budget.opened = budget.opened || {};
    budget.opened["loan:" + item.loan] = true;
    budget.shut = budget.shut || {};
    budget.shut["group:" + item.groupId] = false;
    redrawKeeping("loan:" + item.loan);
    return;
  }
  budget.open = { category: item.category };
  openLine(item.category, v);
}

function itemRow(item: BudgetItem, v: BudgetView, middle: HTMLElement | null, sub: string) {
  var row = el("div", null, "item-row");
  row.tabIndex = 0;
  row.setAttribute("role", "button");
  var names = el("div", null, "item-names");
  names.appendChild(el("span", item.name, "item-name"));
  if (sub) names.appendChild(el("span", sub, "item-sub"));
  row.appendChild(names);
  var mid = el("div", null, "item-mid");
  if (middle) mid.appendChild(middle);
  row.appendChild(mid);
  row.appendChild(el("span", item.target ? money.format(item.target) : "", "item-num muted"));
  row.appendChild(
    el("span", money.format(item.actual), "item-num" + (item.target > 0 && item.actual > item.target + 0.5 ? " over" : "")),
  );
  var go = function () {
    openItem(item, v);
  };
  row.onclick = go;
  row.onkeydown = onEnter(go);
  return row;
}

// B: what is spoken for (recurring bills, annual funds, fixed amounts), then what floats, then what had no
// budget, each kind a panel of its line items with the group each belongs to.
var KIND_PANELS: [string, string, string][] = [
  ["Recurring", "Recurring bills", "Budget is what the charges and their schedules expect"],
  ["Floating", "Floating", "Budget from what these usually cost"],
  ["Annual", "Annual", "Set aside monthly, judged on the year"],
  ["Fixed", "Fixed", "A set amount each period"],
  ["Mixed", "Mixed", "Budget lines of more than one kind"],
  ["Unplanned", "Unplanned", "Spending with no budget line"],
];

function kindCards(groups: BudgetItemGroup[]) {
  var cards = el("div", null, "a2-cards");
  KIND_PANELS.slice(0, 4).forEach(function (k) {
    var items = allItems(groups).filter(function (x) {
      return x.kind === k[0];
    });
    var target = items.reduce(function (t, x) {
        return t + x.target;
      }, 0),
      actual = items.reduce(function (t, x) {
        return t + x.actual;
      }, 0);
    var c = el("div", null, "a2-card");
    c.appendChild(el("span", k[1], "a2-label"));
    var big = el("span", money.format(actual), "a2-big");
    if (target) big.appendChild(el("small", " of " + money.format(target)));
    c.appendChild(big);
    c.appendChild(spentBar(actual, target));
    c.appendChild(el("span", countOf(items.length, "line item", "line items"), "a2-small"));
    cards.appendChild(c);
  });
  return cards;
}

function allItems(groups: BudgetItemGroup[]) {
  var out: BudgetItem[] = [];
  groups.forEach(function (g) {
    out = out.concat(g.items);
  });
  return out;
}

function kindView(v: BudgetView) {
  var groups = budgetItems(v);
  var wrap = el("div", null, "kind-view");
  KIND_PANELS.forEach(function (k) {
    var items = allItems(groups).filter(function (x) {
      return x.kind === k[0];
    });
    if (!items.length) return;
    var panel = el("section", null, "item-panel kind-panel-" + k[0].toLowerCase());
    var head = el("div", null, "item-panel-head");
    head.appendChild(el("h3", k[1]));
    head.appendChild(el("span", k[2], "item-sub"));
    panel.appendChild(head);
    items.forEach(function (x) {
      var middle: HTMLElement | null = null;
      if (x.status && x.status.tone === "check") middle = statusTag(x.status, checkAction(x.status, x.category, v));
      else if (x.balance !== null) {
        middle = statusTag(fundText(x.balance));
        if (x.status) middle.title = x.status.text;
      } else if (x.status) middle = statusTag(x.status);
      else if (x.kind !== "Recurring" && x.kind !== "Unplanned") middle = spentBar(x.actual, x.target);
      panel.appendChild(itemRow(x, v, middle, x.group + ", " + x.how.charAt(0).toLowerCase() + x.how.slice(1)));
    });
    wrap.appendChild(panel);
  });
  return wrap;
}

// C: a card per group, each line item marked by its kind.
function cardsView(v: BudgetView) {
  var wrap = el("div", null, "group-cards");
  budgetItems(v).forEach(function (g) {
    var card = el("section", null, "item-panel group-card");
    var head = el("div", null, "item-panel-head");
    head.appendChild(el("h3", g.name));
    var sum = el("span", money.format(g.actual), "group-sum");
    if (g.target) sum.appendChild(el("small", " of " + money.format(g.target)));
    head.appendChild(sum);
    card.appendChild(head);
    card.appendChild(spentBar(g.actual, g.target));
    g.items.forEach(function (x) {
      var row = itemRow(
        x,
        v,
        null,
        x.status && x.status.tone === "check"
          ? ""
          : x.balance !== null
            ? fundText(x.balance).text
            : x.status
              ? x.status.text
              : x.how,
      );
      if (x.status && x.status.tone === "check")
        row.querySelector(".item-names")!.appendChild(statusTag(x.status, checkAction(x.status, x.category, v)));
      var mark = el("span", null, "kind-mark mark-" + x.kind.toLowerCase());
      mark.title = x.kind;
      mark.setAttribute("aria-label", x.kind);
      row.insertBefore(mark, row.firstChild);
      card.appendChild(row);
    });
    wrap.appendChild(card);
  });
  return wrap;
}

// The views' switch, above the period's head.
function layoutSwitch() {
  var seg = el("span", null, "segmented layout-switch");
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", "View");
  LAYOUTS.forEach(function (l) {
    var b = button(l[1], function () {
      budget.layout = l[0];
      redrawKeeping("layout:" + l[0]);
    });
    b.dataset.fold = "layout:" + l[0];
    b.setAttribute("aria-pressed", String((budget.layout || "group") === l[0]));
    seg.appendChild(b);
  });
  return seg;
}
