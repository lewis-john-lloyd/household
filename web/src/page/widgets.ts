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

// A button that says what it holds and opens a small panel over the page: the one menu every page uses (the
// Budget page's comparison, the header's waiting changes). It closes on Escape or a click elsewhere, like any
// menu; `toggled` hears each open and close, so a page can keep it open when it draws again.
function popMenu(cls: string, summary: HTMLElement, panel: HTMLElement, open: boolean, toggled?: (open: boolean) => void) {
  var menu = el("details", null, "pop-menu " + cls) as HTMLDetailsElement;
  menu.open = open;
  var head = el("summary");
  head.appendChild(summary);
  head.appendChild(icon("chevron"));
  menu.appendChild(head);
  panel.classList.add("menu-panel");
  menu.appendChild(panel);
  var away = function (e: Event) {
    if (!document.body.contains(menu)) return document.removeEventListener("pointerdown", away);
    if (!menu.contains(e.target as Node)) menu.open = false;
  };
  menu.addEventListener("toggle", function () {
    if (toggled) toggled(menu.open);
    if (menu.open) document.addEventListener("pointerdown", away);
    else document.removeEventListener("pointerdown", away);
  });
  menu.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && menu.open) {
      e.stopPropagation();
      menu.open = false;
      head.focus();
    }
  });
  return menu;
}
