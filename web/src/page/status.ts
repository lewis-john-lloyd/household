// @ts-nocheck
// Status: what needs you, the engine log, the projection.

var UNSEEN_NOTE =
  "Ingest ran after it was saved and didn't find it, so the views' folder, web_edits, isn't in the " +
  "household inbox: it starts in My Drive. Move web_edits into the inbox once, then run ingest.";

// Change sets saved and not yet read by ingest, and any it refused.
function drawWaiting(body) {
  body.textContent = "Loading…";
  call(
    "getWaiting",
    [],
    function (list) {
      body.textContent = "";
      if (!list.length) {
        body.appendChild(el("div", "Nothing waiting: every saved change has been read by ingest.", "note"));
        return;
      }
      list.forEach(function (w) {
        var card = el("div", null, "card" + (w.refused ? "" : " clear"));
        card.appendChild(el("strong", w.name));
        card.appendChild(
          el(
            "div",
            (w.savedAt ? "saved " + when(w.savedAt) + ", " : "") +
              (w.refused ? "refused by the last ingest" : w.unseen ? "not seen by the last ingest" : "waiting for ingest"),
            "detail",
          ),
        );
        if (w.refused) card.appendChild(el("div", w.refused, "detail refused"));
        else if (w.unseen) card.appendChild(el("div", UNSEEN_NOTE, "detail refused"));
        w.edits.forEach(function (text) {
          card.appendChild(el("div", text, "detail"));
        });
        card.appendChild(
          button(
            "Discard",
            function () {
              call(
                "discardWaiting",
                [w.id],
                function (d) {
                  drawWaiting(body);
                  if (d && !d.remembered) {
                    // discarded, but the status page won't be able to tell if it was written anyway
                    var msg = document.getElementById("pending-msg");
                    msg.className = "error";
                    msg.textContent = d.warning;
                    drawPending();
                  }
                },
                "edit-body",
              );
            },
            "small",
          ),
        );
        body.appendChild(card);
      });
    },
    "edit-body",
  );
}

// Discarded change sets an ingest applied anyway: it was already reading them when they were discarded.
function loadAppliedAnywayCard() {
  google.script.run
    .withSuccessHandler(function (list) {
      if (!list.length) return;
      var cards = document.getElementById("status-attention");
      var clear = cards.querySelector(".card.clear");
      if (clear) clear.remove();
      var card = attentionCard(
        {
          key: "applied_anyway",
          count: list.length,
          action: "",
          flags: [],
          detail: [],
          label: list.length === 1 ? "discarded change set was applied anyway" : "discarded change sets were applied anyway",
          note:
            "An ingest was already reading " +
            (list.length === 1 ? "it" : "them") +
            " when " +
            (list.length === 1 ? "it was" : "they were") +
            " discarded, and wrote the changes. Edit those settings back if you didn't want them.",
          items: list.map(function (d) {
            return {
              title: d.name,
              detail:
                "written " +
                when(d.writtenAt) +
                (d.edits.length ? ": " + d.edits.join("; ") : "") +
                (d.more ? " and " + d.more + " more" : ""),
            };
          }),
        },
        false,
      );
      var actions = el("div", null, "actions");
      actions.appendChild(
        button(
          "Got it",
          function () {
            list.forEach(function (d) {
              google.script.run.forgetDiscarded(d.id);
            });
            card.remove();
          },
          "filled",
        ),
      );
      card.lastChild.appendChild(actions);
      cards.insertBefore(card, cards.firstChild);
    })
    .withFailureHandler(function (error) {
      console.log("could not check discarded change sets: " + ((error && error.message) || error));
    })
    .getAppliedAnyway();
}

function loadWaitingCard() {
  google.script.run
    .withSuccessHandler(function (list) {
      if (!list.length) return;
      var refused = list.filter(function (w) {
        return w.refused;
      });
      var unseen = list.filter(function (w) {
        return w.unseen && !w.refused;
      });
      var cards = document.getElementById("status-attention");
      var clear = cards.querySelector(".card.clear");
      if (clear && (refused.length || unseen.length)) clear.remove();
      var card = attentionCard(
        refused.length
          ? {
              key: "waiting_changes",
              count: refused.length,
              action: "waiting",
              flags: [],
              detail: [],
              label: refused.length === 1 ? "saved change set was refused" : "saved change sets were refused",
              note: "Ingest wrote nothing from it. Fix the reason, or discard it and edit again.",
              items: refused.map(function (w) {
                return { title: w.name, detail: w.refused };
              }),
            }
          : unseen.length
            ? {
                key: "waiting_changes",
                count: unseen.length,
                action: "waiting",
                flags: [],
                detail: [],
                label: unseen.length === 1 ? "saved change set ingest hasn't seen" : "saved change sets ingest hasn't seen",
                note: UNSEEN_NOTE,
                items: [],
              }
            : {
                key: "waiting_changes",
                count: list.length,
                action: "waiting",
                flags: [],
                detail: [],
                label: list.length === 1 ? "saved change set waits for ingest" : "saved change sets wait for ingest",
                note: "Run ingest, then compute, to apply it.",
                items: [],
              },
        !refused.length && !unseen.length,
      );
      cards.insertBefore(card, cards.firstChild);
    })
    .withFailureHandler(function (error) {
      // no folder yet is not an error (getWaiting gives []): this is one
      var cards = document.getElementById("status-attention");
      cards.insertBefore(
        attentionCard({
          key: "waiting_changes",
          count: 0,
          action: "",
          flags: [],
          detail: [],
          items: [],
          label: "saved changes could not be checked",
          note: String((error && error.message) || error),
        }),
        cards.firstChild,
      );
    })
    .getWaiting();
}

// The view, its period list and the level.
function fillPeriods(list) {
  var select = document.getElementById("period");
  select.textContent = "";
  list.forEach(function (p) {
    var option = document.createElement("option");
    option.value = p;
    option.textContent = p;
    select.appendChild(option);
  });
  if (chosen[view] && list.indexOf(chosen[view]) >= 0) select.value = chosen[view];
  chosen[view] = select.value;
}

function when(at) {
  var d = new Date(at);
  return isNaN(d) ? at : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

// Material icons (Apache 2.0), drawn inline so they need no font.
var ICONS = {
  add: "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
  edit: "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
  download: "M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z",
  inbox:
    "M19 3H4.99C3.89 3 3 3.9 3 5l.01 14c0 1.1.89 2 1.99 2H19c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 12h-4c0 1.66-1.35 3-3 3s-3-1.34-3-3H4.99V5H19v10z",
  copy: "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z",
  warning: "M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z",
  check: "M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
  sync: "M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z",
};
var CARD_ICON = {
  review_open_lines: "edit",
  downloads_due: "download",
  inbox_unread: "inbox",
  possible_duplicates: "copy",
  transfers_unmatched: "edit",
  waiting_changes: "sync",
};
var ACTION_LABEL = {
  review: "Answer in Review",
  duplicates: "Settle duplicates",
  withholding: "Explain stubs",
  waiting: "See saved changes",
  "view:jobs": "Open Jobs",
  "view:recurring": "Open Recurring",
};
var CARD_NOTE = { downloads_due: "Download each one into the inbox, then run ingest." };

function icon(name) {
  var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || ICONS.warning);
  svg.appendChild(path);
  return svg;
}

// One thing that needs you: the count and what it counts, a sentence more, the things themselves, and
// the button that answers it.
function attentionCard(a, waiting) {
  var card = el("article", null, "card attention" + (waiting ? " waiting" : ""));
  var badge = el("div", null, "badge-icon");
  badge.appendChild(icon(CARD_ICON[a.key] || "warning"));
  card.appendChild(badge);
  var body = el("div");
  var headline = el("div", null, "headline");
  headline.appendChild(el("span", a.count.toLocaleString(), "num"));
  headline.appendChild(document.createTextNode(a.label));
  body.appendChild(headline);
  var note = [a.note, CARD_NOTE[a.key] || ""].filter(Boolean).join(" ");
  if (note) body.appendChild(el("p", note, "support"));
  if (a.items.length) {
    var list = el("ul", null, "things");
    a.items.forEach(function (i) {
      var li = el("li");
      li.appendChild(el("strong", i.title));
      li.appendChild(el("span", i.detail));
      list.appendChild(li);
    });
    body.appendChild(list);
  }
  a.detail.forEach(function (d) {
    body.appendChild(el("p", d.replace(/^[a-z_ ]+:\s*/, ""), "support"));
  });
  if (a.action) {
    var actions = el("div", null, "actions");
    actions.appendChild(
      button(
        ACTION_LABEL[a.action] || "Open",
        function () {
          if (a.action.indexOf("view:") === 0) {
            setView(a.action.slice(5));
            return;
          }
          openEdit(a.action);
        },
        "filled",
      ),
    );
    body.appendChild(actions);
  }
  card.appendChild(body);
  return card;
}

function chip(iconName, text) {
  var c = el("span", null, "chip");
  c.appendChild(icon(iconName));
  c.appendChild(document.createTextNode(text));
  return c;
}

var STAGE_NAME = { run: "Run", ingest: "Ingest", compute: "Compute" };

function drawStatus(status) {
  var title = document.getElementById("status-title");
  var runs = document.getElementById("status-runs");
  var stale = document.getElementById("status-stale");
  var cards = document.getElementById("status-attention");
  var logs = document.getElementById("status-logs");
  [runs, cards, logs].forEach(function (e) {
    e.textContent = "";
  });
  if (!status.stages.length) {
    title.textContent = "No runs yet";
    cards.appendChild(el("p", "Run ingest and compute once with this release, and this page fills in.", "support"));
    return;
  }
  var n = status.attention.length;
  title.textContent = n ? (n === 1 ? "One thing needs you" : n + " things need you") : "Nothing needs you";
  status.stages.forEach(function (s) {
    runs.appendChild(chip("sync", STAGE_NAME[s.stage] + " " + when(s.at)));
  });
  stale.hidden = !status.stale;
  stale.textContent = "Compute hasn't run since the last ingest, so these numbers are from before it. Run compute.";
  var flagged = {};
  status.attention.forEach(function (a) {
    a.flags.forEach(function (l) {
      flagged[l] = true;
    });
    cards.appendChild(attentionCard(a));
  });
  if (!n) {
    var clear = el("article", null, "card attention clear");
    var badge = el("div", null, "badge-icon");
    badge.appendChild(icon("check"));
    clear.appendChild(badge);
    clear.appendChild(el("div", "Every line has a category, every download is current, and every check agrees.", "headline"));
    cards.appendChild(clear);
  }
  drawBreakdown(status.categories);
  drawProjection(status.projection, status.projectionYear, status.wiProjection);
  status.stages.forEach(function (s) {
    var panel = el("details", null, "panel");
    var head = el("summary");
    head.appendChild(el("h2", STAGE_NAME[s.stage]));
    var levelOf = function (i) {
      return (s.levels && s.levels[i]) || "INFO";
    };
    var flags = s.lines.filter(function (l) {
      return flagged[l];
    }).length;
    var errors = s.lines.filter(function (l, i) {
      return levelOf(i) === "ERROR";
    }).length;
    var warns = s.lines.filter(function (l, i) {
      return levelOf(i) === "WARN";
    }).length;
    head.appendChild(el("span", when(s.at) + ", run " + s.runId + ", " + s.lines.length + " lines", "support"));
    if (errors) head.appendChild(el("span", errors + (errors === 1 ? " error" : " errors"), "tag error"));
    if (warns) head.appendChild(el("span", warns + (warns === 1 ? " warning" : " warnings"), "tag warn"));
    if (flags) head.appendChild(el("span", flags === 1 ? "1 line needs you" : flags + " lines need you", "tag warn"));
    panel.appendChild(head);
    logTopics(s.lines).forEach(function (t) {
      var topic = el("details", null, "topic");
      var th = el("summary", t.name);
      th.appendChild(el("span", " " + t.lines.length, "count"));
      var worst = t.at.map(levelOf).reduce(function (w, l) {
        return LEVEL_RANK[l] > LEVEL_RANK[w] ? l : w;
      }, "INFO");
      if (worst !== "INFO") th.appendChild(el("span", worst, "tag " + (worst === "ERROR" ? "error" : "warn")));
      topic.appendChild(th);
      var list = el("ol");
      t.lines.forEach(function (l, k) {
        var lv = levelOf(t.at[k]);
        var li = el("li", null, flagged[l] || lv === "ERROR" ? "flag" : lv === "WARN" ? "warn" : /^\s/.test(l) ? "indent" : "");
        li.appendChild(el("span", lv, "lvl " + lv.toLowerCase()));
        li.appendChild(document.createTextNode(l));
        list.appendChild(li);
      });
      topic.appendChild(list);
      topic.open =
        worst !== "INFO" ||
        t.lines.some(function (l) {
          return flagged[l];
        });
      panel.appendChild(topic);
    });
    logs.appendChild(panel);
  });
}

// A run's lines by topic, in the order each topic first appears: a line's topic is the words before its first
// colon (a year or a run id left off), and an indented line belongs to the line above it.
var LEVEL_RANK = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };

function logTopics(lines) {
  var topics = [],
    by = {},
    last = "run";
  lines.forEach(function (l, i) {
    var name = last;
    if (!/^\s/.test(l)) {
      var m = /^([A-Za-z][A-Za-z _'-]{0,40}?)(?: \d{4}\b[^:]*| [0-9A-Z]{8,}[^:]*)?:/.exec(l);
      name = m ? m[1].trim() : "run";
    }
    if (!by[name]) {
      by[name] = { name: name, lines: [], at: [] };
      topics.push(by[name]);
    }
    by[name].lines.push(l);
    by[name].at.push(i);
    last = name;
  });
  return topics;
}

var statusTab = "overview";

function setStatusTab(tab) {
  statusTab = tab;
  document.querySelectorAll("#status-tabs button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.tab === tab));
  });
  document.getElementById("status-overview").hidden = tab !== "overview";
  document.getElementById("status-log").hidden = tab !== "log";
}

var SEGMENT_COLOR = { waiting: "var(--waiting)", open: "var(--error)" };

function drawBreakdown(c) {
  var box = document.getElementById("status-categories");
  var since = document.getElementById("status-since");
  box.textContent = "";
  if (!c || !c.total) {
    since.textContent = "";
    box.appendChild(el("p", "The next compute fills this in.", "support"));
    return;
  }
  var d = new Date(c.since + "T00:00:00");
  since.textContent =
    c.total.toLocaleString() + " lines since " + (isNaN(d) ? c.since : d.toLocaleDateString([], { dateStyle: "long" }));
  var stack = el("div", null, "stack");
  stack.setAttribute("role", "img");
  stack.setAttribute(
    "aria-label",
    c.segments
      .map(function (s) {
        return s.label + " " + s.count;
      })
      .join(", "),
  );
  var legend = el("ul", null, "legend");
  var shade = 0;
  c.segments.forEach(function (s) {
    var color = SEGMENT_COLOR[s.kind] || "var(--seg-" + Math.min(5, ++shade) + ")";
    var part = el("span");
    part.style.flex = String(s.count);
    part.style.background = color;
    part.title = s.label + ": " + s.count.toLocaleString();
    stack.appendChild(part);
    var li = el("li");
    var swatch = el("span", null, "swatch");
    swatch.style.background = color;
    li.appendChild(swatch);
    li.appendChild(el("span", s.label));
    li.appendChild(el("span", s.count.toLocaleString(), "n"));
    li.appendChild(el("span", Math.round((100 * s.count) / c.total) + "%", "pct"));
    legend.appendChild(li);
  });
  box.appendChild(stack);
  box.appendChild(legend);
}

// The tax projection in a line, with the way to the tax page.
function drawProjection(rows, year, wi) {
  var box = document.getElementById("status-projection");
  document.getElementById("status-projection-title").textContent = "Taxes" + (year ? " for " + year : "");
  box.textContent = "";
  var asIs = rows.filter(function (r) {
    return r[0] === "as is";
  })[0];
  if (!asIs) {
    box.appendChild(el("p", "No projection in the last compute.", "support"));
    return;
  }
  var facts = el("dl", null, "facts tax-glance");
  facts.appendChild(el("dt", "Federal"));
  facts.appendChild(el("dd", signed(asIs[1]), "amount " + (asIs[1] > 0 ? "owe" : "refund")));
  if (wi !== null && wi !== undefined) {
    facts.appendChild(el("dt", "Wisconsin"));
    facts.appendChild(el("dd", signed(wi), "amount " + (wi > 0 ? "owe" : "refund")));
  }
  box.appendChild(facts);
  box.appendChild(el("p", "As things stand, projected from this year's pay stubs.", "support"));
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(
      "Open Taxes",
      function () {
        setView("taxes");
      },
      "filled",
    ),
  );
  box.appendChild(actions);
}
