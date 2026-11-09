// Editing: the pending changes and saving them as a change set.

// --- editing ---------------------------------------------------------------------------------------
// Edits are staged here, one per setting (a newer edit of the same thing replaces the older), and
// shown in the pending bar. Save sends them to saveChanges, which writes one change set for ingest.
var editor: EditorData | null = null,
  editTab = "review";
/** A staged edit and how it reads in the pending bar. */
interface StagedEdit {
  edit: Edit;
  text: string;
}
/** An entry a Save sent: the setting it changes and the staged edit. */
interface SentEdit {
  target: string;
  entry: StagedEdit;
}
var staged: Record<string, StagedEdit> = {},
  stagedOrder: string[] = [];
var RULES = ["fixed", "billed", "recurring", "trailing", "lookback", "seasonal", "sinking_fund"];
var PERS = ["monthly", "biweekly", "weekly", "semi_monthly", "quarterly", "semi_annually", "annually"];
var PRIORITIES = ["", "high", "medium", "low"];

function stage(edit: Edit, text: string) {
  var t = editTarget(edit); // the setting it changes (edits.ts)
  if (!staged[t]) stagedOrder.push(t);
  staged[t] = { edit: edit, text: text };
  drawPending();
  return t;
}

function unstage(t: string) {
  if (!staged[t]) return;
  delete staged[t];
  stagedOrder.splice(stagedOrder.indexOf(t), 1);
  drawPending();
}

function drawPending() {
  var bar = byId("pending");
  var n = stagedOrder.length;
  bar.hidden = !n && !unconfirmed && !byId("pending-msg").textContent;
  document.body.classList.toggle("editing", !bar.hidden);
  byId("pending-count").textContent = n ? n + (n === 1 ? " change" : " changes") + " not saved" : "";
  // a save not yet answered keeps Save and Discard all, so it can still be settled with nothing staged
  ["pending-save", "pending-discard"].forEach(function (id) {
    byId(id).hidden = !n && !unconfirmed;
  });
  byId("pending-show").hidden = !n;
  (byId("pending-save") as HTMLButtonElement).disabled = busy();
  (byId("pending-discard") as HTMLButtonElement).disabled = busy();
  byId("pending-dismiss").hidden = !!n || !!unconfirmed;
  var list = byId("pending-list");
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
var unconfirmed: { request: string; sent: SentEdit[] } | null = null; // a Save that failed
var inFlight: SentEdit[] | true | null = null; // what the running Save sent, or true while a check runs

function busy() {
  return !!inFlight;
}

function sentIn(sent: SentEdit[], t: string) {
  return sent.some(function (x) {
    return x.target === t && staged[t] === x.entry;
  });
}

function locked(t: string) {
  return !!(inFlight && inFlight !== true && sentIn(inFlight, t)) || !!(unconfirmed && sentIn(unconfirmed.sent, t));
}

// Asks whether the unconfirmed save landed; then(r) with its file, or null when it did not.
function checkUnconfirmed(
  then: (r: SaveResult | null, u: { request: string; sent: SentEdit[] }) => void,
  onError: (error: Error) => void,
) {
  var u = unconfirmed!; // called only while a save is unconfirmed
  inFlight = true;
  drawPending();
  ask(
    "findSave",
    [u.request],
    function (r) {
      inFlight = null;
      unconfirmed = null;
      then(r, u);
    },
    function (error) {
      inFlight = null;
      drawPending();
      onError(error);
    },
  );
}

function newRequest() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "r" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

function saveStaged() {
  if (busy()) return;
  var msg = byId("pending-msg");
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
  var msg = byId("pending-msg");
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
  ask(
    "saveChanges",
    [edits, texts, request],
    function (r) {
      inFlight = null;
      saved(sent, r, "");
    },
    function (error) {
      inFlight = null;
      unconfirmed = { request: request, sent: sent };
      msg.className = "error";
      msg.textContent =
        "Save did not confirm (your changes are still here): " +
        ((error && error.message) || error) +
        ". Saving again first checks whether it went through, so nothing is saved twice.";
      drawPending();
    },
  );
}

// Discard all drops what is staged here. After a save that didn't confirm it first learns whether that
// save wrote a change set: if it did, those changes are saved, not discarded, and the page offers to
// discard that file too, since only that takes them out of ingest's way.
function discardStaged() {
  if (busy()) return;
  var msg = byId("pending-msg");
  function dropAll(lead: string) {
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
            ask(
              "discardWaiting",
              [r.id],
              function (d) {
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
              },
              function (error) {
                msg.className = "error";
                msg.textContent =
                  "Could not discard " +
                  r.name +
                  ": " +
                  ((error && error.message) || error) +
                  ". If ingest has read it already, edit the settings back instead.";
                drawPending();
              },
            );
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

function saved(sent: SentEdit[], r: SaveResult, lead: string) {
  var msg = byId("pending-msg");
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
