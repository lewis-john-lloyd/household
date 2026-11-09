// @ts-nocheck
// Small builders for elements and form fields.

function el(tag, text, cls) {
  var e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function field(form, label, control) {
  var l = el("label", label);
  l.appendChild(control);
  form.appendChild(l);
  return control;
}

function textInput(value, cls, placeholder) {
  var i = document.createElement("input");
  i.type = "text";
  i.value = value || "";
  if (cls) i.className = cls;
  if (placeholder) i.placeholder = placeholder;
  return i;
}

function dateInput(value) {
  var i = document.createElement("input");
  i.type = "date";
  i.value = value || "";
  return i;
}

function choice(options, value, labels) {
  var s = document.createElement("select");
  options.forEach(function (o) {
    var opt = el("option", labels && labels[o] !== undefined ? labels[o] : o || "(none)");
    opt.value = o;
    s.appendChild(opt);
  });
  s.value = options.indexOf(value) >= 0 ? value : options[0];
  return s;
}

function categoryChoice(value, blank) {
  var names = (editor ? editor.categories : []).map(function (c) {
    return c.category;
  });
  var labels = { "": blank || "(choose)" };
  if (value && names.indexOf(value) < 0) {
    // shown as it is, so a name that isn't a category is seen, not dropped
    names.push(value);
    labels[value] = value + " (not a category)";
  }
  return choice([""].concat(names), value, labels);
}

function button(text, onclick, cls) {
  var b = el("button", text, cls);
  b.type = "button";
  b.onclick = onclick;
  return b;
}

function today() {
  var d = new Date();
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

function firstOfMonth() {
  return today().slice(0, 8) + "01";
}

function isDate(text) {
  return /^\d{4}-\d{2}-\d{2}$/.test(text);
}

function isAmount(text) {
  return /^-?\d+(\.\d{1,2})?$/.test(text.replace(/,/g, ""));
}

// Each editor part lives on the page it is about: review, payee rules and duplicates (and a transaction's
// own page) on Cashflow, beside the flow of money they decide, categories on Budget, withholding on Taxes, settings and saved changes on
// Status. Each page's submenu lists its own parts and its editor parts together; an "edit:" part opens the
// editor under that page's name.
