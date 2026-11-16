// Status, Alerts: every alert the weekly emails can send, by section, each with who gets it, and the people
// they go to. Who gets what is two kinds of household setting, staged like any other: alert_recipient:<name>
// (an address) and alert_to:<alert> (names separated by commas, or none). nudges.py lists the alerts.

var ALERT_SECTIONS = ["Taxes and benefits", "Books upkeep", "Spending", "System"];
var ALERT_LIVE = ["inbox", "compute", "email"]; // found by the weekly script when it runs, not by compute

// A recipient's name from its setting's last part: "sam_lee" is Sam Lee.
function recipientName(id: string) {
  return id
    .split("_")
    .filter(Boolean)
    .map(function (w) {
      return w.charAt(0).toUpperCase() + w.slice(1);
    })
    .join(" ");
}

// A typed name as a setting's last part: lower case letters, digits and _.
function personId(name: string) {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

// A setting's row in the ledger, or null when it isn't set there.
function storedRow(key: string): Row | null {
  return (
    (editor!.parameters || []).filter(function (p) {
      return p.key === key && p.source !== "not set";
    })[0] || null
  );
}

// A setting as it will be: staged or saved for the next run, else as the ledger has it, else "".
function settingNow(key: string): string {
  var e = edited(editTarget({ kind: "parameter", key: key }));
  if (e) return String((e.edit as Record<string, string>).value);
  var row = storedRow(key);
  return row ? row.value : "";
}

// Stage a setting's new value, or take back the staged one when the value is what it already is.
function stageSetting(key: string, value: string, text: string) {
  var t = editTarget({ kind: "parameter", key: key });
  var row = storedRow(key);
  var saved = savedEdits[t] ? String((savedEdits[t].edit as Record<string, string>).value) : null;
  if (value === (saved !== null ? saved : row ? row.value : "")) {
    unstage(t);
    return;
  }
  stage({ kind: "parameter", key: key, value: value, was: row ? { value: row.value } : null }, text);
}

// Everyone alerts can go to, as it will be: {name: address}, the stored, saved and staged settings together.
function alertPeople(): Record<string, string> {
  var ids: Record<string, boolean> = {};
  (editor!.parameters || []).forEach(function (p) {
    if (p.key.indexOf("alert_recipient:") === 0) ids[p.key.slice(16)] = true;
  });
  Object.keys(staged)
    .concat(Object.keys(savedEdits))
    .forEach(function (t) {
      var m = /^parameter\|alert_recipient:(.+)$/.exec(t);
      if (m) ids[m[1]] = true;
    });
  var out: Record<string, string> = {};
  Object.keys(ids)
    .sort()
    .forEach(function (id) {
      var v = settingNow("alert_recipient:" + id);
      if (v) out[id] = v;
    });
  return out;
}

// Who an alert goes to, as it will be: "none", or the names it lists.
function alertRoute(key: string): { off: boolean; names: string[] } {
  var v = settingNow("alert_to:" + key).trim();
  if (v.toLowerCase() === "none") return { off: true, names: [] };
  return {
    off: false,
    names: v
      .split(",")
      .map(function (n) {
        return n.trim().toLowerCase();
      })
      .filter(Boolean),
  };
}

function setRoute(key: string, name: string, off: boolean, names: string[]) {
  var value = off ? "none" : names.join(", ");
  stageSetting(
    "alert_to:" + key,
    value,
    name +
      ": " +
      (off
        ? "don't email"
        : names.length
          ? "email " +
            names
              .map(function (n) {
                return recipientName(n);
              })
              .join(" and ")
          : "no one chosen"),
  );
}

// What an alert is doing now, in words: in this week's emails, from a later day, or nothing to do.
function alertState(rows: Row[], now: string): { words: string; due: boolean } {
  var due = rows.filter(function (r) {
    return r.show_from && r.show_from <= now && (!r.show_until || r.show_until >= now);
  });
  if (due.length)
    return {
      due: true,
      words:
        "Due now: " +
        due
          .map(function (r) {
            return (r.item !== r.name ? r.item + ", " : "") + r.detail;
          })
          .join("; "),
    };
  var later = rows
    .filter(function (r) {
      return r.show_from && r.show_from > now;
    })
    .sort(function (a, b) {
      return a.show_from.localeCompare(b.show_from);
    })[0];
  if (later) return { due: false, words: "Emailed from " + dayWords(later.show_from) + " if nothing changes: " + later.detail };
  var first = rows[0];
  if (ALERT_LIVE.indexOf(first.key) >= 0)
    return { due: false, words: "Checked when the weekly email runs: " + first.detail.replace(/^checked live: /, "") };
  return { due: false, words: "Nothing to do: " + first.detail };
}

function drawAlerts(body: HTMLElement) {
  var rows = editor!.alerts || [];
  var page = el("div", null, "alerts");
  body.appendChild(page);
  page.appendChild(
    el(
      "p",
      "Each Saturday, everyone below is emailed the alerts set for them that need doing, in one email, or nothing " +
        "when none do. An alert no one is chosen for goes to the account the weekly email runs as, marked, until " +
        "someone is chosen or it is set to Don't email.",
      "support",
    ),
  );
  var people = alertPeople();
  var names = Object.keys(people);
  drawAlertPeople(page, people);
  if (!rows.length) {
    empty(page, "No alerts yet.", "The next compute lists every alert the weekly emails can send.");
    return;
  }
  // each line item once, its rows together, in its section
  var keys: string[] = [];
  var byKey: Record<string, Row[]> = {};
  rows.forEach(function (r) {
    if (!byKey[r.key]) {
      keys.push(r.key);
      byKey[r.key] = [];
    }
    byKey[r.key].push(r);
  });
  var now = today();
  var unchosen = keys.filter(function (k) {
    var route = alertRoute(k);
    return (
      !route.off &&
      !route.names.some(function (n) {
        return !!people[n];
      })
    );
  });
  if (unchosen.length)
    page.appendChild(
      el(
        "p",
        unchosen.length +
          (unchosen.length === 1 ? " alert has" : " alerts have") +
          " no one chosen. Choose who gets each, or check Don't email.",
        "note warn alerts-unchosen",
      ),
    );
  ALERT_SECTIONS.concat(
    keys
      .map(function (k) {
        return byKey[k][0].section;
      })
      .filter(function (s, i, all) {
        return ALERT_SECTIONS.indexOf(s) < 0 && all.indexOf(s) === i;
      }),
  ).forEach(function (section) {
    var mine = keys.filter(function (k) {
      return byKey[k][0].section === section;
    });
    if (!mine.length) return;
    var box = el("section", null, "card alert-section");
    box.appendChild(el("h3", section || "Other"));
    var list = el("ul", null, "alert-list");
    mine.forEach(function (key) {
      var first = byKey[key][0];
      var route = alertRoute(key);
      var state = alertState(byKey[key], now);
      var li = el("li", null, "alert-row" + (state.due ? " due" : ""));
      li.dataset.alert = key;
      var what = el("div", null, "alert-what");
      what.appendChild(el("strong", first.name));
      what.appendChild(el("span", state.words, "support"));
      var note = changeNote("parameter|alert_to:" + key);
      if (note) what.appendChild(el("span", note, "md-staged"));
      li.appendChild(what);
      var who = el("div", null, "alert-who");
      who.setAttribute("role", "group");
      who.setAttribute("aria-label", "Who is emailed " + first.name);
      names.forEach(function (n) {
        var on = route.names.indexOf(n) >= 0;
        var b = button(
          "",
          function () {
            var next = on
              ? route.names.filter(function (x) {
                  return x !== n;
                })
              : route.names.concat([n]);
            setRoute(key, first.name, false, next);
            redrawEditor();
          },
          "chip person",
        );
        if (on) b.appendChild(icon("check"));
        b.appendChild(document.createTextNode(recipientName(n)));
        b.setAttribute("aria-pressed", String(on));
        b.disabled = route.off;
        who.appendChild(b);
      });
      if (
        !route.off &&
        !route.names.some(function (n) {
          return !!people[n];
        })
      )
        who.appendChild(el("span", "No one chosen", "tag warn"));
      li.appendChild(who);
      var off = el("label", null, "alert-off");
      var check = document.createElement("input");
      check.type = "checkbox";
      check.checked = route.off;
      check.onchange = function () {
        setRoute(key, first.name, check.checked, check.checked ? [] : route.names);
        redrawEditor();
      };
      off.appendChild(check);
      off.appendChild(document.createTextNode("Don't email"));
      li.appendChild(off);
      list.appendChild(li);
    });
    box.appendChild(list);
    page.appendChild(box);
  });
}

// The people alerts go to: each with its address to change or remove, and a form to add one.
function drawAlertPeople(page: HTMLElement, people: Record<string, string>) {
  var box = el("section", null, "card alert-people");
  box.appendChild(el("h3", "People"));
  var ids = Object.keys(people);
  if (!ids.length) empty(box, "No one to email yet.", "Add a person, then choose the alerts they get.");
  var list = el("ul", null, "alert-list");
  ids.forEach(function (id) {
    var li = el("li", null, "alert-row person");
    li.dataset.person = id;
    var shown = function () {
      li.textContent = "";
      var what = el("div", null, "alert-what");
      what.appendChild(el("strong", recipientName(id)));
      what.appendChild(el("span", people[id], "support"));
      var note = changeNote("parameter|alert_recipient:" + id);
      if (note) what.appendChild(el("span", note, "md-staged"));
      li.appendChild(what);
      var acts = el("div", null, "actions");
      acts.appendChild(
        button(
          "Change",
          function () {
            li.textContent = "";
            li.appendChild(personForm(id, people[id], shown));
          },
          "text",
        ),
      );
      acts.appendChild(
        button(
          "Remove",
          function () {
            removePerson(id);
            redrawEditor();
          },
          "text",
        ),
      );
      li.appendChild(acts);
    };
    shown();
    list.appendChild(li);
  });
  box.appendChild(list);
  var add = el("div", null, "actions");
  add.appendChild(
    button(
      "Add a person",
      function () {
        add.textContent = "";
        add.appendChild(
          personForm("", "", function () {
            redrawEditor();
          }),
        );
      },
      "tonal",
    ),
  );
  box.appendChild(add);
  page.appendChild(box);
}

// A person's name and address, new or changed: a new name moves their alerts to it.
function personForm(id: string, address: string, cancel: () => void) {
  var form = el("form", null, "edit person-form");
  var name = field(form, "Name", textInput(id ? recipientName(id) : "", "", "Sam"));
  var mail = field(form, "Email", textInput(address, "wide", "sam@example.com"));
  mail.type = "email";
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(id ? "Keep" : "Add", function () {
      var next = personId(name.value);
      var to = mail.value.trim();
      var others = alertPeople();
      if (!next) error.textContent = "A name is letters and digits.";
      else if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/.test(to))
        error.textContent = "That is not one email address.";
      else if (next !== id && others[next]) error.textContent = recipientName(next) + " is already here.";
      else {
        stageSetting("alert_recipient:" + next, to, "email alerts to " + recipientName(next) + " at " + to);
        if (id && next !== id) {
          moveAlerts(id, next);
          stageSetting("alert_recipient:" + id, "", "stop emailing alerts to " + recipientName(id));
        }
        redrawEditor();
        return;
      }
    }),
  );
  actions.appendChild(button("Cancel", cancel, "text"));
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

// Every alert that names one person names another instead (none: names no one).
function moveAlerts(from: string, to: string | null) {
  (editor!.alerts || [])
    .map(function (r) {
      return [r.key, r.name];
    })
    .filter(function (k, i, all) {
      return (
        all.findIndex(function (x) {
          return x[0] === k[0];
        }) === i
      );
    })
    .forEach(function (k) {
      var route = alertRoute(k[0]);
      if (route.off || route.names.indexOf(from) < 0) return;
      var names = route.names
        .map(function (n) {
          return n === from ? to : n;
        })
        .filter(function (n, i, all): n is string {
          return !!n && all.indexOf(n) === i;
        });
      setRoute(k[0], k[1], false, names);
    });
}

function removePerson(id: string) {
  moveAlerts(id, null);
  stageSetting("alert_recipient:" + id, "", "stop emailing alerts to " + recipientName(id));
}
