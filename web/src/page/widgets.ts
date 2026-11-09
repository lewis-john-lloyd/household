// Small builders for elements and form fields.

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string | number | null,
  cls?: string,
): HTMLElementTagNameMap[K] {
  var e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = String(text);
  if (cls) e.className = cls;
  return e;
}

function field<C extends HTMLElement>(form: HTMLElement, label: string, control: C): C {
  var l = el("label", label);
  l.appendChild(control);
  form.appendChild(l);
  return control;
}

function textInput(value?: string | null, cls?: string, placeholder?: string) {
  var i = document.createElement("input");
  i.type = "text";
  i.value = value || "";
  if (cls) i.className = cls;
  if (placeholder) i.placeholder = placeholder;
  return i;
}

function dateInput(value?: string | null) {
  var i = document.createElement("input");
  i.type = "date";
  i.value = value || "";
  return i;
}

function choice(options: string[], value: string | null | undefined, labels?: Record<string, string>) {
  var s = document.createElement("select");
  options.forEach(function (o) {
    var opt = el("option", labels && labels[o] !== undefined ? labels[o] : o || "(none)");
    opt.value = o;
    s.appendChild(opt);
  });
  s.value = value != null && options.indexOf(value) >= 0 ? value : options[0];
  return s;
}

function categoryChoice(value?: string | null, blank?: string) {
  var names = (editor ? editor.categories : []).map(function (c) {
    return c.category;
  });
  var labels: Record<string, string> = { "": blank || "(choose)" };
  if (value && names.indexOf(value) < 0) {
    // shown as it is, so a name that isn't a category is seen, not dropped
    names.push(value);
    labels[value] = value + " (not a category)";
  }
  return choice([""].concat(names), value, labels);
}

function button(text: string, onclick: ((this: HTMLButtonElement, e: MouseEvent) => void) | null, cls?: string) {
  var b = el("button", text, cls);
  b.type = "button";
  b.onclick = onclick as (this: GlobalEventHandlers, e: MouseEvent) => void; // called on the button
  return b;
}

function today() {
  var d = new Date();
  return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
}

function firstOfMonth() {
  return today().slice(0, 8) + "01";
}

function isDate(text: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(text);
}

function isAmount(text: string) {
  return /^-?\d+(\.\d{1,2})?$/.test(text.replace(/,/g, ""));
}
