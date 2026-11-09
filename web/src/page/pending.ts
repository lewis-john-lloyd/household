// @ts-nocheck
// Editing: the pending changes and saving them as a change set.

// --- editing ---------------------------------------------------------------------------------------
// Edits are staged here, one per setting (a newer edit of the same thing replaces the older), and
// shown in the pending bar. Save sends them to saveChanges, which writes one change set for ingest.
var editor = null,
  editTab = "review";
var staged = {},
  stagedOrder = [];
var RULES = ["fixed", "billed", "recurring", "trailing", "lookback", "seasonal", "sinking_fund"];
var PERS = ["monthly", "biweekly", "weekly", "semi_monthly", "quarterly", "semi_annually", "annually"];
var PRIORITIES = ["", "high", "medium", "low"];

// The setting an edit changes; the same rule as editTarget in edits.ts.
function targetOf(e) {
  switch (e.kind) {
    case "budget_line":
      return e.rule === "scheduled" ? "budget_line|" + e.line + "|scheduled|" + e.effective : "budget_line|" + e.line;
    case "line":
      return "line|" + e.txn_id + "|" + (e.field === "split" ? "category" : e.field);
    case "payee":
      return "payee|" + e.key.toUpperCase();
    case "payee_rule":
      return "payee_rule|" + e.pattern;
    case "category":
      return "category|" + e.category;
    case "alias":
      return "alias|" + e.alias;
    case "parameter":
      return "parameter|" + e.key;
    case "explanation":
      return "explanation|" + e.jurisdiction + "|" + e.stub_id;
    case "not_duplicate":
      return "not_duplicate|" + [e.txn_a, e.txn_b].sort().join("|");
    case "item":
      return "item|" + e.item_id;
    case "job":
      return "job|" + e.person + "|" + e.effective + "|" + e.employer;
    case "subscription":
      return "subscription|" + e.key.toUpperCase();
    case "recurring_merge":
      return "recurring_merge|" + e.key.toUpperCase();
    case "recurring_name":
      return "recurring_name|" + e.key.toUpperCase();
  }
  return e.kind + "|" + JSON.stringify(e);
}

function stage(edit, text) {
  var t = targetOf(edit);
  if (!staged[t]) stagedOrder.push(t);
  staged[t] = { edit: edit, text: text };
  drawPending();
  return t;
}

function unstage(t) {
  if (!staged[t]) return;
  delete staged[t];
  stagedOrder.splice(stagedOrder.indexOf(t), 1);
  drawPending();
}

function drawPending() {
  var bar = document.getElementById("pending");
  var n = stagedOrder.length;
  bar.hidden = !n && !unconfirmed && !document.getElementById("pending-msg").textContent;
  document.body.classList.toggle("editing", !bar.hidden);
  document.getElementById("pending-count").textContent = n ? n + (n === 1 ? " change" : " changes") + " not saved" : "";
  // a save not yet answered keeps Save and Discard all, so it can still be settled with nothing staged
  ["pending-save", "pending-discard"].forEach(function (id) {
    document.getElementById(id).hidden = !n && !unconfirmed;
  });
  document.getElementById("pending-show").hidden = !n;
  document.getElementById("pending-save").disabled = busy();
  document.getElementById("pending-discard").disabled = busy();
  document.getElementById("pending-dismiss").hidden = !!n || !!unconfirmed;
  var list = document.getElementById("pending-list");
  list.textContent = "";
  stagedOrder.forEach(function (t) {
    var li = document.createElement("li");
    li.textContent = staged[t].text;
    var undo = document.createElement("button");
    undo.className = "small";
    undo.textContent = "undo";
    if (locked(t)) {
      // it went to a save not yet answered: undoing it here would not take it out of Drive
      undo.disabled = true;
      undo.title = "Sent in a save that hasn't confirmed yet";
    }
    undo.onclick = function () {
      unstage(t);
      redrawEditor();
    };
    li.appendChild(undo);
    list.appendChild(li);
  });
  if (!n) list.hidden = true;
}

// Save sends what is staged now, with an id for this request. Editing stays open while it runs, so on
// success only the entries sent, and still the same entries, are cleared: one staged or changed meanwhile
// stays, to go in the next save. When Save fails the change set may still have been written (its answer
// can be lost after Drive has it), so the request is kept: the next Save first asks whether it landed,
// and writes nothing twice.
// Discard and undo must not quietly drop what a save may already have written: while a save or a check
// runs, Save and Discard all wait, and an entry sent in an unanswered save can't be undone until it is.
var unconfirmed = null; // { request, sent } of a Save that failed
var inFlight = null; // what the running Save sent, or true while a check runs

function busy() {
  return !!inFlight;
}

function sentIn(sent, t) {
  return sent.some(function (x) {
    return x.target === t && staged[t] === x.entry;
  });
}

function locked(t) {
  return !!(inFlight && inFlight !== true && sentIn(inFlight, t)) || !!(unconfirmed && sentIn(unconfirmed.sent, t));
}

// Asks whether the unconfirmed save landed; then(r) with its file, or null when it did not.
function checkUnconfirmed(then, onError) {
  var u = unconfirmed;
  inFlight = true;
  drawPending();
  google.script.run
    .withSuccessHandler(function (r) {
      inFlight = null;
      unconfirmed = null;
      then(r, u);
    })
    .withFailureHandler(function (error) {
      inFlight = null;
      drawPending();
      onError(error);
    })
    .findSave(u.request);
}

function newRequest() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "r" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

function saveStaged() {
  if (busy()) return;
  var msg = document.getElementById("pending-msg");
  msg.className = "";
  if (unconfirmed) {
    msg.textContent = "Checking whether the last save went through…";
    checkUnconfirmed(
      function (r, u) {
        if (!r && !stagedOrder.length) {
          msg.textContent = "The last save hadn't gone through, and nothing is staged now: nothing to save.";
          drawPending();
          return;
        }
        if (!r) {
          saveNow();
          return;
        } // it did not: save what is staged now
        if (r.discarded) {
          // it did, but that file is in the trash: nothing of it will be applied
          msg.className = "error";
          msg.textContent =
            "The last save went through as " +
            r.name +
            ", but that change set has been discarded since, " +
            "so ingest won't read it. Your changes are still here: Save again to send them.";
          drawPending();
          return;
        }
        saved(u.sent, r, "The last save did go through. ");
      },
      function (error) {
        msg.className = "error";
        msg.textContent =
          "Could not check the last save (your changes are still here): " +
          ((error && error.message) || error) +
          ". Save again to check once more; nothing is saved twice.";
      },
    );
    return;
  }
  saveNow();
}

function saveNow() {
  var msg = document.getElementById("pending-msg");
  var sent = stagedOrder.map(function (t) {
    return { target: t, entry: staged[t] };
  });
  var request = newRequest();
  var edits = sent.map(function (s) {
    return s.entry.edit;
  });
  var texts = sent.map(function (s) {
    return s.entry.text;
  });
  msg.className = "";
  msg.textContent = "Saving " + edits.length + " changes…";
  inFlight = sent;
  drawPending();
  google.script.run
    .withSuccessHandler(function (r) {
      inFlight = null;
      saved(sent, r, "");
    })
    .withFailureHandler(function (error) {
      inFlight = null;
      unconfirmed = { request: request, sent: sent };
      msg.className = "error";
      msg.textContent =
        "Save did not confirm (your changes are still here): " +
        ((error && error.message) || error) +
        ". Saving again first checks whether it went through, so nothing is saved twice.";
      drawPending();
    })
    .saveChanges(edits, texts, request);
}

// Discard all drops what is staged here. After a save that didn't confirm it first learns whether that
// save wrote a change set: if it did, those changes are saved, not discarded, and the page offers to
// discard that file too, since only that takes them out of ingest's way.
function discardStaged() {
  if (busy()) return;
  var msg = document.getElementById("pending-msg");
  function dropAll(lead) {
    stagedOrder.slice().forEach(unstage);
    msg.className = "";
    msg.textContent = lead + "Discarded every change on this page.";
    drawPending();
    redrawEditor();
  }
  if (!unconfirmed) {
    dropAll("");
    return;
  }
  msg.className = "";
  msg.textContent = "Checking whether the last save went through before discarding…";
  checkUnconfirmed(
    function (r, u) {
      if (!r || r.discarded) {
        dropAll(r ? "The last save's change set was discarded already. " : "The last save hadn't gone through. ");
        return;
      }
      u.sent.forEach(function (x) {
        if (staged[x.target] === x.entry) unstage(x.target);
      });
      stagedOrder.slice().forEach(unstage);
      msg.className = "error";
      msg.textContent =
        "The last save did go through, as " +
        r.name +
        ", so its " +
        r.count +
        " changes are saved and ingest will apply them. Everything else on this page is discarded. ";
      msg.appendChild(
        button(
          "Discard that change set too",
          function () {
            this.disabled = true;
            google.script.run
              .withSuccessHandler(function (d) {
                msg.className = d && d.remembered ? "" : "error";
                msg.textContent =
                  "Discarded " +
                  r.name +
                  " too: no ingest from now on will read it. An ingest already " +
                  "running may have read it first; " +
                  (d && d.remembered
                    ? "if one did, the status page will say so, and you can edit those settings back."
                    : "and " +
                      ((d && d.warning) || "the views could not remember this discard, so check the next ingest's file list."));
                drawPending();
              })
              .withFailureHandler(function (error) {
                msg.className = "error";
                msg.textContent =
                  "Could not discard " +
                  r.name +
                  ": " +
                  ((error && error.message) || error) +
                  ". If ingest has read it already, edit the settings back instead.";
                drawPending();
              })
              .discardWaiting(r.id);
          },
          "small",
        ),
      );
      drawPending();
      redrawEditor();
    },
    function (error) {
      msg.className = "error";
      msg.textContent =
        "Could not check the last save, so nothing is discarded yet: " +
        ((error && error.message) || error) +
        ". Try Discard all again.";
    },
  );
}

function saved(sent, r, lead) {
  var msg = document.getElementById("pending-msg");
  sent.forEach(function (s) {
    if (staged[s.target] === s.entry) unstage(s.target);
  });
  var left = stagedOrder.length;
  msg.className = "saved";
  msg.textContent =
    lead +
    "Saved " +
    r.count +
    " changes as " +
    r.name +
    ". Run ingest, then compute, to apply them." +
    (left
      ? " " +
        left +
        (left === 1 ? " change" : " changes") +
        " made since " +
        (left === 1 ? "is" : "are") +
        " still here, for the next save."
      : "") +
    (r.madeFolder ? " This first save made the folder web_edits in My Drive: move it into the household inbox once." : "") +
    (r.warning ? " " + r.warning : "");
  drawPending();
  redrawEditor();
}

// Small builders for the editor's forms.
