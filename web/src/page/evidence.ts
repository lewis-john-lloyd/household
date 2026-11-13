// Evidence and its fixes, the same on every page: the lines behind a number, each with the fixes a line can take
// (move it to another category, mark it one-off, open its page), and a setting edited where a number uses it.
// Reads are cached for the page's life (Reports change only when compute runs) and asked for in one call per
// thing a person does, since each call to the server costs a round trip.

var readCache: Record<string, unknown> = {}; // "name|args": the server's answer
var readWaiting: Record<string, ((answer: unknown) => void)[]> = {}; // the same read asked again while it runs

// A read the page may ask for again (a drawer opened twice, a month opened again): asked once, then answered from
// the page. Two asks while the first runs share it.
function cachedCall<K extends keyof ServerCalls>(
  name: K,
  args: Parameters<ServerCalls[K]>,
  done: (answer: Answer<K>) => void,
  where: string,
) {
  var key = name + "|" + JSON.stringify(args);
  if (key in readCache) {
    done(readCache[key] as Answer<K>);
    return;
  }
  if (readWaiting[key]) {
    readWaiting[key].push(done as (answer: unknown) => void);
    return;
  }
  readWaiting[key] = [done as (answer: unknown) => void];
  ask(
    name,
    args,
    function (answer) {
      readCache[key] = answer;
      var waiting = readWaiting[key];
      delete readWaiting[key];
      waiting.forEach(function (f) {
        f(answer);
      });
    },
    function (error) {
      delete readWaiting[key];
      var at = document.getElementById(where);
      if (at) at.textContent = "Could not load: " + ((error && error.message) || error);
    },
  );
}

// The editor's data (settings, categories) for a page that needs it only once someone edits: loaded on the
// first need, then kept.
function withEditor(then: () => void, where: string) {
  if (editor) {
    then();
    return;
  }
  cachedCall(
    "getEditor",
    [],
    function (data) {
      editor = editor || data;
      then();
    },
    where,
  );
}

// ---- lines and their fixes ----

var lineListSeq = 0;

// The lines behind a number, each with its fixes: Move (to another category; not for a split line, whose parts
// are changed on its page), One-off or Count it, and Open. A change is staged like any other, shown on the
// line until the run applies it, and undone from the same button; `redraw` draws the list's owner again.
function lineList(lines: Txn[], redraw: () => void): HTMLElement {
  if (!lines.length) return el("p", "No lines.", "support");
  var table = el("table", null, "txns line-list");
  lines.forEach(function (t) {
    var o = oneOffNow(t);
    var moveTo = "line|" + t.id + "|category",
      flag = "line|" + t.id + "|one_off";
    var moving = edited(moveTo);
    var to = moving ? String((moving.edit as Record<string, string>).value) : "";
    var row = el("tr", null, o.now ? "left-out" : "");
    row.dataset.txn = t.id;
    cell(
      row,
      shortDay(t.date) + (t.date.slice(0, 4) !== String(new Date().getFullYear()) ? ", " + t.date.slice(0, 4) : ""),
      "text day",
    );
    cell(row, cents.format(-t.amount), "spend");
    var about = cell(row, "", "text about");
    about.appendChild(el("div", t.payee));
    if (t.split) about.appendChild(el("div", "Split: " + t.split, "support"));
    if (to) about.appendChild(el("div", "Moves to " + pretty(to) + " after the next run", "support"));
    if (o.changed)
      about.appendChild(el("div", o.now ? "Left out after the next run" : "Counts again after the next run", "support"));
    else if (o.now) about.appendChild(el("div", "Marked one-off", "support"));
    if (t.items && t.items.length) about.appendChild(itemList(t.items));
    var act = cell(row, "", "text acts");
    var said = pretty(t.category) + ": the " + cents.format(-t.amount) + " line of " + t.date;
    if (!t.split)
      act.appendChild(
        button(
          to ? "Undo move" : "Move",
          function () {
            if (staged[moveTo]) {
              unstage(moveTo);
              redraw();
              return;
            }
            // the picker gets a row of its own under the line, so it never crowds the line's buttons
            var next = row.nextElementSibling;
            if (next && next.classList.contains("move-row")) {
              next.remove();
              return;
            }
            var id = "line-move-" + ++lineListSeq;
            var pickRow = el("tr", null, "move-row");
            var td = el("td");
            td.colSpan = 4;
            td.id = id;
            td.appendChild(el("span", "Loading\u2026", "support"));
            pickRow.appendChild(td);
            row.parentNode!.insertBefore(pickRow, row.nextSibling);
            withEditor(function () {
              td.textContent = "";
              var label = el("label", null, "move-to");
              label.appendChild(el("span", "Move to"));
              var pick = categoryChoice(t.category);
              label.appendChild(pick);
              td.appendChild(label);
              td.appendChild(
                button(
                  "Move it",
                  function () {
                    if (pick.value && pick.value !== t.category)
                      stage(
                        { kind: "line", txn_id: t.id, field: "category", value: pick.value },
                        said + " moves to " + pick.value,
                      );
                    redraw();
                  },
                  "text",
                ),
              );
              td.appendChild(
                button(
                  "Cancel",
                  function () {
                    pickRow.remove();
                  },
                  "text",
                ),
              );
              pick.focus();
            }, id);
          },
          "text",
        ),
      );
    act.appendChild(
      button(
        o.now ? "Count it" : "One-off",
        function () {
          if (staged[flag])
            unstage(flag); // undo a change not saved yet
          else
            stage(
              { kind: "line", txn_id: t.id, field: "one_off", value: o.now ? "FALSE" : "TRUE" },
              said + (o.now ? " counts again" : " is a one-off"),
            );
          redraw();
        },
        "text",
      ),
    );
    var open = button(
      "Open",
      function () {
        closeDrawer();
        openTransaction(t.id);
      },
      "text",
    );
    open.title = "Its page: category, split, note and flags";
    act.appendChild(open);
    table.appendChild(row);
  });
  return table;
}

// ---- a setting where a number uses it ----

// A household setting shown where it is used (an assumption, a target), with Change to edit it in place: the
// same `parameter` edit as Status, Settings. `shown` is the value the page shows when nothing is staged; null
// when the page doesn't have it, so the stored value shows once the editor's data is loaded.
// after: called once the setting is staged or put back, for a page that previews with it (the Taxes plan)
function settingControl(key: string, shown: string | null, after?: () => void): HTMLElement {
  var box = el("span", null, "setting-control");
  box.dataset.setting = key;
  var draw = function () {
    box.textContent = "";
    var t = editTarget({ kind: "parameter", key: key });
    var e = edited(t);
    var stored = editor
      ? (editor.parameters || []).filter(function (p) {
          return p.key === key && p.source !== "not set";
        })[0]
      : undefined;
    var now = e ? String((e.edit as Record<string, string>).value) : shown !== null ? shown : stored ? stored.value : null;
    if (now !== null) box.appendChild(el("strong", now === "" ? "not set" : now, "setting-value"));
    box.appendChild(el("code", key, "setting-key"));
    if (e)
      box.appendChild(
        el(
          "span",
          stagedText(t) ? "set to " + now + ", waiting to be saved" : "set to " + now + ", applies at the next run",
          "tag warn",
        ),
      );
    var change = button(
      "Change",
      function () {
        var id = "setting-" + key.replace(/[^a-z0-9]+/gi, "-");
        box.id = id;
        withEditor(function () {
          box.textContent = "";
          var known = (editor!.parameters || []).filter(function (p) {
            return p.key === key;
          })[0];
          // the setting as stored when it is (an assumption may show it in words); else what the page shows
          var value = textInput(e || !known || known.source === "not set" ? now || "" : known.value, "");
          value.setAttribute("aria-label", key);
          box.appendChild(value);
          box.appendChild(
            button(
              "Keep",
              function () {
                var v = value.value.trim();
                if (v === (now === null && known ? known.value : now)) {
                  draw();
                  return;
                }
                stage(
                  {
                    kind: "parameter",
                    key: key,
                    value: v,
                    was: known && known.source !== "not set" ? { value: known.value } : null,
                  },
                  "setting " + key + " = " + (v || "(blank)"),
                );
                if (after) after();
                else draw();
              },
              "text",
            ),
          );
          if (staged[t])
            box.appendChild(
              button(
                "Undo",
                function () {
                  unstage(t);
                  if (after) after();
                  else draw();
                },
                "text",
              ),
            );
          box.appendChild(button("Cancel", draw, "text"));
          var history = settingHistory(key); // what it was before, so a change can be checked or put back
          if (history) box.appendChild(history);
          value.focus();
        }, id);
      },
      "text",
    );
    change.setAttribute("aria-label", "Change " + key);
    box.appendChild(change);
  };
  draw();
  return box;
}
