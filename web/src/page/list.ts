// @ts-nocheck
// Transactions: the list beside the editor.

// --- the list and the editor ---------------------------------------------------------------------
// Every editor part is a list on the left, one row per item (its name, a line about it, and what is staged
// for it), and the chosen item's editor on the right. On a phone the editor replaces the list, with Back.
var selected = {}; // the item chosen in each tab, by key
var txnPage = null; // the transaction the transaction page shows, and the view it was opened from

function facts(panel, pairs) {
  var dl = el("dl", null, "facts");
  pairs.forEach(function (p) {
    if (p[1] === "" || p[1] === null || p[1] === undefined) return;
    dl.appendChild(el("dt", p[0]));
    var dd = el("dd");
    if (typeof p[1] === "object") dd.appendChild(p[1]);
    else dd.textContent = String(p[1]);
    dl.appendChild(dd);
  });
  panel.appendChild(dl);
}

function section(panel, title, help) {
  var s = el("section", null, "editor-section");
  s.appendChild(el("h3", title));
  if (help) s.appendChild(el("p", help, "support"));
  panel.appendChild(s);
  return s;
}

function masterDetail(body, opts) {
  var wrap = el("div", null, "md");
  var side = el("section", null, "md-list");
  var panel = el("section", null, "md-detail");
  var head = el("div", null, "md-head");
  var count = el("div", null, "md-count");
  head.appendChild(count);
  var filter = textInput("", "md-filter", "Filter");
  filter.setAttribute("aria-label", "Filter the list");
  if (opts.items.length > 8) head.appendChild(filter);
  side.appendChild(head);
  if (opts.add) {
    var adds = el("div", null, "actions md-adds");
    opts.add.forEach(function (a) {
      adds.appendChild(
        button(
          a.label,
          function () {
            selected[editTab] = null;
            mark();
            open(a.label, function () {
              a.form(panel, done);
            });
          },
          "small",
        ),
      );
    });
    side.appendChild(adds);
  }
  var list = el("ul", null, "md-items");
  var rows = [];
  var lastSection = null;
  opts.items.forEach(function (item) {
    var name = opts.section ? opts.section(item) : null;
    if (name && name !== lastSection) {
      list.appendChild(el("li", name, "md-section"));
      lastSection = name;
    }
    var li = el("li");
    var row = button(
      "",
      function () {
        selected[editTab] = opts.key(item);
        mark();
        show(item);
      },
      "md-row",
    );
    row.appendChild(el("span", opts.title(item), "md-title"));
    var sub = opts.sub ? opts.sub(item) : "";
    if (sub) row.appendChild(el("span", sub, "md-sub"));
    var said = opts.staged ? opts.staged(item) : "";
    if (said) row.appendChild(el("span", said, "md-staged"));
    li.appendChild(row);
    list.appendChild(li);
    rows.push({ item: item, li: li, row: row, text: (opts.title(item) + " " + sub).toLowerCase() });
  });
  side.appendChild(list);
  function counted() {
    var shown = rows.filter(function (r) {
      return !r.li.hidden;
    }).length;
    count.textContent =
      (shown === rows.length ? "" : shown.toLocaleString() + " of ") + rows.length.toLocaleString() + " " + opts.noun;
  }
  filter.oninput = function () {
    var q = filter.value.trim().toLowerCase();
    rows.forEach(function (r) {
      r.li.hidden = !!q && r.text.indexOf(q) < 0;
    });
    list.querySelectorAll(".md-section").forEach(function (s) {
      var next = s.nextElementSibling,
        any = false;
      while (next && !next.classList.contains("md-section")) {
        if (!next.hidden) any = true;
        next = next.nextElementSibling;
      }
      s.hidden = !any;
    });
    counted();
  };
  function mark() {
    rows.forEach(function (r) {
      r.row.setAttribute("aria-current", String(opts.key(r.item) === selected[editTab]));
    });
  }
  function open(title, fill) {
    panel.textContent = "";
    var back = button(
      "Back to the list",
      function () {
        wrap.classList.remove("showing");
      },
      "text md-back",
    );
    panel.appendChild(back);
    panel.appendChild(el("h2", title, "md-heading"));
    fill();
    wrap.classList.add("showing");
  }
  function show(item) {
    open(opts.title(item), function () {
      opts.detail(item, panel, done);
    });
  }
  function done() {
    redrawEditor();
  }
  counted();
  wrap.appendChild(side);
  wrap.appendChild(panel);
  body.appendChild(wrap);
  var chosen = rows.filter(function (r) {
    return opts.key(r.item) === selected[editTab];
  })[0];
  if (chosen) {
    mark();
    show(chosen.item);
    wrap.classList.remove("showing");
  } else if (!rows.length) panel.appendChild(el("p", opts.none || "Nothing here.", "support"));
  else panel.appendChild(el("p", opts.empty, "support md-empty"));
}
