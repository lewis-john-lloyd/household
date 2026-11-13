// A household setting's form.

function parameterForm(given: Row | null, done: () => void) {
  var form = el("form", null, "edit");
  var isNew = !given;
  var p: Row = given || { key: "", value: "" };
  var key = isNew ? field(form, "Setting", textInput("", "", "order_payee:books")) : null;
  var value = field(form, "Value", textInput(p.value, "wide"));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(isNew ? "Add setting" : "Keep this change", function () {
      var unset = !isNew && p.source === "not set"; // a known setting not in the ledger yet: a new one
      var e = {
        kind: "parameter",
        key: key ? key.value.trim() : p.key,
        value: value.value.trim(),
        was: isNew || unset ? null : { value: p.value },
      };
      if (!/^[a-z][a-z0-9_]*(:[a-z0-9_]+)?$/.test(e.key)) {
        error.textContent = "A setting is lower case letters, digits, _ and one ':'.";
        return;
      }
      stage(e, "setting " + e.key + " = " + (e.value || "(blank)"));
      done();
    }),
  );
  form.appendChild(actions);
  form.appendChild(error);
  return form;
}

// Where a setting's value came from, in words: "web 2026-11-13" (saved from the views that day), "web" (saved
// from the views before rows kept their day), "seed" (set when the ledger was set up), or the source as written.
function sourceWords(source: string) {
  var m = /^web(?: (\d{4}-\d{2}-\d{2}))?$/.exec(source || "");
  if (m) return "from the views" + (m[1] ? ", " + longDay(m[1]) : "");
  if (source === "seed") return "when the ledger was set up";
  return source || "";
}

// A setting's history where it is changed: every value it has had, newest first, each with where it came from.
// The ledger keeps every row, so an old value can be seen and put back. Null when it has had only one.
function settingHistory(key: string): HTMLElement | null {
  var rows = ((editor && editor.parameterHistory) || [])
    .filter(function (r) {
      return r.key === key;
    })
    .sort(function (a, b) {
      return Number(b.n) - Number(a.n);
    });
  if (rows.length < 2) return null;
  var box = el("div", null, "setting-history");
  box.appendChild(el("h4", "Its history"));
  var list = el("ol");
  rows.forEach(function (r, i) {
    var li = el("li");
    li.appendChild(el("strong", r.value === "" ? "(blank)" : r.value));
    var from = sourceWords(r.source);
    li.appendChild(el("span", (from ? " " + from : "") + (i === 0 ? ", in force" : ""), "support"));
    list.appendChild(li);
  });
  box.appendChild(list);
  return box;
}
