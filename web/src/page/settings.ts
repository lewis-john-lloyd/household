// @ts-nocheck
// A household setting's form.

function parameterForm(p, done) {
  var form = el("form", null, "edit");
  var isNew = !p;
  p = p || { key: "", value: "" };
  var key = isNew ? field(form, "Setting", textInput("", "", "order_payee:books")) : null;
  var value = field(form, "Value", textInput(p.value, "wide"));
  var error = el("div", "", "error");
  var actions = el("div", null, "actions");
  actions.appendChild(
    button(isNew ? "Add setting" : "Keep this change", function () {
      var unset = !isNew && p.source === "not set"; // a known setting not in the ledger yet: a new one
      var e = {
        kind: "parameter",
        key: isNew ? key.value.trim() : p.key,
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
