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
  // each shown by its name in words; its value stays the key the ledger keeps
  var labels: Record<string, string> = { "": blank || "(choose)" };
  names.forEach(function (n) {
    labels[n] = pretty(n);
  });
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

// A page's parts as a row of buttons, the open one pressed: fills a bar the page has, or adds one.
function tabBar(box: HTMLElement, label: string, parts: string[][], current: string, pick: (part: string) => void) {
  box.textContent = "";
  box.className = "subtabs";
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", label);
  parts.forEach(function (t) {
    var b = button(t[1], function () {
      pick(t[0]);
    });
    b.dataset.tab = t[0];
    b.setAttribute("aria-pressed", String(current === t[0]));
    box.appendChild(b);
  });
  return box;
}

function subtabs(parent: HTMLElement, label: string, parts: string[][], current: string, pick: (part: string) => void) {
  var box = tabBar(el("div"), label, parts, current, pick);
  parent.appendChild(box);
  return box;
}

/** The lengths a page's date chip offers: days (and any number of them), the calendar month, quarter and year,
 * whole years, all time, and whether the menu also picks the day the dates end (else they end today, and the
 * arrows step back from there). */
interface RangeChoices {
  days?: number[];
  ownDays?: boolean;
  units?: boolean;
  years?: number[];
  allTime?: boolean;
  ends?: boolean;
}

// A date chip: what the dates are and the days they cover, arrows either side (back, on; null when there is
// nowhere to go), and a menu under it that fill draws into, given a way to close it. Every page's dates use it.
function dateChip(
  parent: HTMLElement,
  said: { label: string; span: string },
  back: (() => void) | null,
  on: (() => void) | null,
  fill: (menu: HTMLElement, close: () => void) => void,
) {
  var dates = el("div", null, "search-dates");
  var step = function (go: (() => void) | null, name: string, words: string) {
    var b = button(
      "",
      function () {
        go!();
      },
      "step",
    );
    b.appendChild(icon(name));
    b.setAttribute("aria-label", words);
    b.disabled = !go;
    return b;
  };
  var chip = button(
    "",
    function () {
      show(!dates.querySelector(".search-dmenu"));
    },
    "search-datechip",
  );
  chip.setAttribute("aria-haspopup", "dialog");
  chip.setAttribute("aria-expanded", "false");
  chip.appendChild(icon("calendar"));
  var words = el("span", null, "dl");
  words.appendChild(el("span", said.label));
  words.appendChild(el("span", said.span, "sub"));
  chip.appendChild(words);
  chip.appendChild(icon("expand"));
  dates.appendChild(step(back, "chevron_left", "Back by the same length"));
  dates.appendChild(chip);
  dates.appendChild(step(on, "chevron", "On by the same length"));
  var show = function (open: boolean) {
    var was = dates.querySelector(".search-dmenu");
    if (was) was.remove();
    chip.setAttribute("aria-expanded", String(open));
    if (!open) return;
    var menu = el("div", null, "search-dmenu");
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Dates");
    menu.onkeydown = function (ev) {
      if (ev.key === "Escape") {
        show(false);
        chip.focus();
      }
    };
    fill(menu, function () {
      show(false);
    });
    dates.appendChild(menu);
    (menu.querySelector("button.row.on") as HTMLElement | null)?.focus();
  };
  parent.appendChild(dates);
  return dates;
}

// Which dates a view covers, as one chip: the length and the days it covers, arrows either side stepping back
// and on by the length, and a menu of the lengths the page offers. Drawn into parent; each change is given to
// onChange and the chip redrawn in place. now is the day the view counts back from.
function rangeChip(
  parent: HTMLElement,
  start: SearchRange,
  now: string,
  choices: RangeChoices,
  onChange: (r: SearchRange) => void,
) {
  var range = start;
  var holder = el("div", null, "range-chip");
  parent.appendChild(holder);
  var changed = function (r: SearchRange) {
    range = r;
    draw();
    onChange(r);
  };
  var draw = function () {
    holder.textContent = "";
    var earlier = stepRange(range, -1, now),
      onward = stepRange(range, 1, now);
    dateChip(
      holder,
      rangeLabel(range, now),
      earlier
        ? function () {
            changed(earlier!);
          }
        : null,
      onward
        ? function () {
            changed(onward!);
          }
        : null,
      fill,
    );
  };
  var fill = function (box: HTMLElement, close: () => void) {
    var pick: SearchRange = { unit: range.unit, days: range.days, end: choices.ends ? range.end : "today" };
    if (range.years !== undefined) pick.years = range.years;
    if (!choices.ends) box.classList.add("one");
    var cols = el("div", null, "cols");
    var long = el("div", null, "col"),
      ends = el("div", null, "col");
    long.appendChild(el("span", "How long", "colh"));
    cols.appendChild(long);
    if (choices.ends) {
      ends.appendChild(el("span", "Ending", "colh"));
      cols.appendChild(ends);
    }
    box.appendChild(cols);
    var preview = el("span");
    var marks: (() => void)[] = [];
    var row = function (into: HTMLElement, label: string, sub: string, isOn: () => boolean, set: () => void) {
      var b = button(
        "",
        function () {
          set();
          redraw();
        },
        "row",
      );
      b.appendChild(el("span", label));
      if (sub) b.appendChild(el("span", sub, "sub"));
      into.appendChild(b);
      marks.push(function () {
        b.classList.toggle("on", isOn());
        b.setAttribute("aria-pressed", String(isOn()));
      });
    };
    var dayList = choices.days || [];
    dayList.forEach(function (n) {
      row(
        long,
        n + " days",
        "",
        function () {
          return pick.unit === "days" && pick.days === n;
        },
        function () {
          pick.unit = "days";
          pick.days = n;
        },
      );
    });
    var nDays = textInput("", "mono", "N");
    var own = el("label", null, "row custom");
    if (choices.ownDays) {
      nDays.inputMode = "numeric";
      nDays.setAttribute("aria-label", "Days");
      own.appendChild(nDays);
      own.appendChild(el("span", "days"));
      long.appendChild(own);
      nDays.oninput = function () {
        var n = Math.round(Number(nDays.value));
        if (n >= 1 && n <= 36600) {
          pick.unit = "days";
          pick.days = n;
          redraw(true);
        }
      };
    }
    if (choices.units) {
      if (dayList.length || choices.ownDays) long.appendChild(el("div", null, "sep"));
      (
        [
          ["month", "A month", "the calendar month the end falls in"],
          ["quarter", "A quarter", ""],
          ["year", "A year", ""],
        ] as ["month" | "quarter" | "year", string, string][]
      ).forEach(function (u) {
        row(
          long,
          u[1],
          u[2],
          function () {
            return pick.unit === u[0];
          },
          function () {
            pick.unit = u[0];
          },
        );
      });
    }
    (choices.years || []).forEach(function (n) {
      row(
        long,
        n === 1 ? "1 year" : n + " years",
        "",
        function () {
          return pick.unit === "years" && pick.years === n;
        },
        function () {
          pick.unit = "years";
          pick.years = n;
        },
      );
    });
    if (choices.allTime)
      row(
        long,
        "All time",
        "",
        function () {
          return pick.unit === "all";
        },
        function () {
          pick.unit = "all";
        },
      );
    var onDate = textInput(/^\d{4}-\d\d-\d\d$/.test(pick.end) ? pick.end : "", "mono", "YYYY-MM-DD");
    var dated = el("label", null, "row custom");
    if (choices.ends) {
      var endWords = function (end: string) {
        return dayWords(rangeEnd({ unit: "days", days: 1, end: end }, now));
      };
      (
        [
          ["today", "Today"],
          ["month", "End of last month"],
          ["quarter", "End of last quarter"],
          ["year", "End of last year"],
        ] as [string, string][]
      ).forEach(function (e) {
        row(
          ends,
          e[1],
          endWords(e[0]),
          function () {
            return pick.end === e[0];
          },
          function () {
            pick.end = e[0];
          },
        );
      });
      onDate.setAttribute("aria-label", "End date");
      dated.appendChild(onDate);
      ends.appendChild(dated);
      onDate.oninput = function () {
        var v = onDate.value.trim();
        if (/^\d{4}-\d\d-\d\d$/.test(v) && !isNaN(Date.parse(v))) {
          pick.end = v > now ? "today" : v;
          redraw(true);
        }
      };
    }
    var foot = el("div", null, "dfoot");
    foot.appendChild(preview);
    foot.appendChild(
      button(
        "Apply",
        function () {
          close();
          changed(pick);
          (holder.querySelector(".search-datechip") as HTMLElement).focus();
        },
        "filled",
      ),
    );
    box.appendChild(foot);
    var redraw = function (typing?: boolean) {
      marks.forEach(function (m) {
        m();
      });
      var ownOn = pick.unit === "days" && dayList.indexOf(pick.days) < 0;
      own.classList.toggle("on", ownOn);
      dated.classList.toggle("on", /^\d{4}/.test(pick.end));
      if (!typing) {
        nDays.value = ownOn ? String(pick.days) : "";
        if (!/^\d{4}/.test(pick.end)) onDate.value = "";
      }
      var r = rangeLabel(pick, now);
      preview.textContent = r.label + ": " + r.span;
    };
    redraw();
  };
  draw();
  return holder;
}

// A list of periods (newest first: months, quarters, years, a year's monthly average) as a date chip: the arrows
// step to the period of the same kind before or after, and the menu lists each kind in its own column. name says a
// period; pick is given the one chosen.
function periodChip(
  parent: HTMLElement,
  list: string[],
  current: string,
  name: (p: string) => string,
  pick: (p: string) => void,
) {
  var kindOf = function (p: string) {
    return p.indexOf(" ") > 0 ? "average" : lensOf(p);
  };
  var kind = kindOf(current);
  var same = list.filter(function (p) {
    return kindOf(p) === kind;
  });
  var at = same.indexOf(current);
  var whole = current.slice(0, current.indexOf(" ") > 0 ? 4 : current.length);
  var days = /^\d{4}(-\d\d|-Q[1-4])?$/.test(whole) ? periodDays(whole) : null;
  var go = function (p: string | undefined) {
    return p === undefined
      ? null
      : function () {
          pick(p);
        };
  };
  return dateChip(
    parent,
    {
      label: name(current),
      // a period not over yet ends today
      span: days ? (kind === "average" ? "Each month of " : "") + rangeSpan(days[0], minDay(days[1], today())) : "",
    },
    go(same[at + 1]),
    at > 0 ? go(same[at - 1]) : null,
    function (box, close) {
      box.classList.add("periods");
      var cols = el("div", null, "cols");
      box.appendChild(cols);
      (
        [
          ["month", "Months"],
          ["quarter", "Quarters"],
          ["year", "Years"],
          ["average", "A month on average"],
        ] as [string, string][]
      ).forEach(function (k) {
        var mine = list.filter(function (p) {
          return kindOf(p) === k[0];
        });
        if (!mine.length) return;
        var col = el("div", null, "col");
        col.appendChild(el("span", k[1], "colh"));
        var rows = el("div", null, "rows");
        col.appendChild(rows);
        mine.forEach(function (p) {
          var b = button(
            name(p),
            function () {
              close();
              pick(p);
            },
            "row" + (p === current ? " on" : ""),
          );
          b.dataset.period = p;
          b.setAttribute("aria-pressed", String(p === current));
          rows.appendChild(b);
        });
        cols.appendChild(col);
      });
    },
  );
}

/** A chip in a query bar: what it filters or groups by, the value, a class ("" or "group"), and what removing it
 * and choosing it do. */
interface BarChip {
  text: string;
  value: string;
  cls: string;
  remove: () => void;
  focus?: () => void;
}

/** A query bar's list: its fields, how it suggests for what is typed, the chips in force, and what choosing an
 * option or dropping the last chip does (each redraws what it filters, then the bar's chips). */
interface QueryBarOptions {
  menuId: string;
  ariaLabel: string;
  empty: string; // the box's words with nothing in force
  fields: SearchBarField[];
  named: string[]; // fields whose values are categories or groups, shown as the pages name them
  state: { draft: string; typed: string }; // the field waiting for its value, and what is typed
  suggest: (text: string, draft: string) => SearchOption[];
  chips: () => BarChip[];
  choose: (o: SearchOption, group: boolean) => void;
  dropLast: () => boolean; // false when there was nothing to drop
}

// The query bar Search and the lists share: the filters in force as chips, and a box that offers fields, values
// and grouping as you type. Choosing a field waits for its value (a draft chip); anything else is the owner's.
function queryBar(parent: HTMLElement, o: QueryBarOptions) {
  var bar = el("div", null, "search-bar");
  parent.appendChild(bar);
  bar.appendChild(icon("search"));
  var chips = el("div", null, "search-chips");
  bar.appendChild(chips);
  var draftChip = el("span", null, "fchip draft");
  var input = textInput(o.state.typed, "search-input", o.empty);
  input.type = "search";
  input.spellcheck = false;
  input.autocomplete = "off";
  input.setAttribute("aria-label", o.ariaLabel);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", o.menuId);
  input.setAttribute("aria-expanded", "false");
  bar.appendChild(draftChip);
  bar.appendChild(input);
  var menu = el("div", null, "search-menu");
  menu.id = o.menuId;
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  bar.appendChild(menu);
  var options: SearchOption[] = [];
  var hi = 0;
  // the menu shows while something is typed or a field waits for its value, or when asked for (a click, Down)
  var wanted = false;
  var fieldOf = function (key: string) {
    return (
      o.fields.filter(function (f) {
        return f.key === key;
      })[0] || null
    );
  };
  var label = function (field: string) {
    return field === "any" ? "Any text" : (fieldOf(field) || { label: field }).label;
  };
  var groups = o.fields.some(function (f) {
    return f.group;
  });

  var drawChips = function () {
    chips.textContent = "";
    var shown = o.chips();
    shown.forEach(function (c) {
      var chip = el("span", null, "fchip " + c.cls);
      var body = button(
        "",
        c.focus ||
          function () {
            input.focus();
          },
        "fchip-body",
      );
      body.appendChild(el("span", c.text, "k"));
      if (c.value) body.appendChild(el("span", c.value, "v"));
      chip.appendChild(body);
      var x = button(
        "",
        function () {
          c.remove();
          input.focus();
        },
        "x",
      );
      x.appendChild(icon("close"));
      x.setAttribute("aria-label", "Remove " + c.text + (c.value ? " " + c.value : ""));
      chip.appendChild(x);
      chips.appendChild(chip);
    });
    draftChip.hidden = !o.state.draft;
    draftChip.textContent = o.state.draft ? label(o.state.draft) : "";
    input.placeholder = o.state.draft
      ? (fieldOf(o.state.draft) || { hint: "" }).hint || "a pattern"
      : shown.length
        ? "Add a filter" + (groups ? " or a grouping" : "")
        : o.empty;
  };

  var drawMenu = function () {
    options = o.suggest(input.value, o.state.draft);
    menu.textContent = "";
    var show = document.activeElement === input && options.length > 0 && (wanted || !!input.value || !!o.state.draft);
    menu.hidden = !show;
    input.setAttribute("aria-expanded", String(show));
    if (!show) return;
    if (hi >= options.length) hi = 0;
    var section = "";
    options.forEach(function (opt, i) {
      var sec = opt.kind === "field" ? "Fields" : opt.kind === "value" && !o.state.draft ? "Values" : "";
      if (sec && sec !== section) menu.appendChild(el("div", sec, "sec"));
      section = sec || section;
      var row = el("div", null, "opt" + (i === hi ? " hi" : ""));
      row.id = o.menuId + "-opt-" + i;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(i === hi));
      var what = el("div", null, "what");
      // a category or group offered by itself shows as the pages name it
      var named = opt.kind === "value" && o.named.indexOf(opt.field) >= 0 && opt.label.indexOf(" is ") < 0;
      what.appendChild(el("span", named ? pretty(opt.label) : opt.label));
      if (opt.detail) what.appendChild(el("span", opt.detail));
      row.appendChild(what);
      row.onmousedown = function (ev) {
        ev.preventDefault(); // keep the box focused
      };
      row.onclick = function () {
        choose(opt, false);
      };
      var f = opt.kind === "field" ? fieldOf(opt.field) : null;
      if (f && i === hi) {
        if (f.kind !== "group") {
          var filt = button(
            "Filter",
            function (ev) {
              ev.stopPropagation();
              choose(opt, false);
            },
            "act pri",
          );
          filt.onmousedown = row.onmousedown;
          row.appendChild(filt);
        }
        if (f.group) {
          var grp = button(
            "Group by",
            function (ev) {
              ev.stopPropagation();
              choose(opt, true);
            },
            "act",
          );
          grp.onmousedown = row.onmousedown;
          row.appendChild(grp);
        }
      }
      menu.appendChild(row);
    });
    input.setAttribute("aria-activedescendant", o.menuId + "-opt-" + hi);
    var keys = el("div", null, "keys");
    keys.textContent = o.state.draft
      ? "Enter keeps the highlighted one; what is typed is a pattern found anywhere, ^ and $ anchor it; Backspace in an empty box drops " +
        label(o.state.draft)
      : "Up and down to move, Enter to choose, " +
        (groups ? "Shift+Enter groups by the field, " : "") +
        "Backspace in an empty box removes the last chip, Esc closes";
    menu.appendChild(keys);
  };

  // a field waits for its value, unless it is grouped by; everything else is the owner's to apply
  var choose = function (opt: SearchOption, group: boolean) {
    var f = fieldOf(opt.field);
    input.value = "";
    o.state.typed = "";
    hi = 0;
    if (opt.kind === "field" && !group && !(f && f.kind === "group")) {
      o.state.draft = opt.field;
      wanted = true;
      drawChips();
      drawMenu();
    } else {
      o.state.draft = "";
      wanted = false;
      o.choose(opt, group || opt.kind === "field");
    }
    input.focus();
  };

  input.oninput = function () {
    o.state.typed = input.value;
    hi = 0;
    drawMenu();
  };
  input.onclick = function () {
    wanted = true;
    drawMenu();
  };
  input.onblur = function () {
    window.setTimeout(function () {
      if (document.activeElement !== input) {
        wanted = false;
        menu.hidden = true;
        input.setAttribute("aria-expanded", "false");
      }
    }, 0);
  };
  input.onkeydown = function (ev) {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (menu.hidden) {
        wanted = true; // Down opens the menu first
        drawMenu();
        return;
      }
      if (!options.length) return;
      hi = (hi + (ev.key === "ArrowDown" ? 1 : options.length - 1)) % options.length;
      drawMenu();
      var at = document.getElementById(o.menuId + "-opt-" + hi);
      if (at) at.scrollIntoView({ block: "nearest" });
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      if (options[hi]) choose(options[hi], ev.shiftKey);
    } else if (ev.key === "Escape") {
      if (!menu.hidden) {
        wanted = false;
        menu.hidden = true;
        input.setAttribute("aria-expanded", "false");
      } else if (o.state.draft) {
        o.state.draft = "";
        drawChips();
      }
    } else if (ev.key === "Backspace" && !input.value) {
      if (o.state.draft) {
        o.state.draft = "";
        drawChips();
        drawMenu();
      } else o.dropLast();
    }
  };
  return { input: input, drawChips: drawChips, drawMenu: drawMenu };
}

// A heading that sorts its column. dir is how it sorts now ("asc", "desc", or "" for not by this one),
// shown by the one arrow every table uses; a click calls pick, which sorts and redraws.
function sortHead(th: HTMLElement, label: string, dir: string, pick: () => void) {
  var b = button(label, pick, "sort");
  b.setAttribute("aria-label", "Sort by " + label);
  th.appendChild(b);
  showSort(th, dir);
  return b;
}

function showSort(th: HTMLElement, dir: string) {
  var b = th.querySelector("button.sort");
  if (b) b.setAttribute("data-dir", dir);
  th.setAttribute("aria-sort", dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none");
}

// Which part is open, without drawing the bar again.
function pressTab(box: HTMLElement, current: string) {
  box.querySelectorAll<HTMLElement>(":scope > button").forEach(function (b) {
    b.setAttribute("aria-pressed", String(b.dataset.tab === current));
  });
}

function minDay(a: string, b: string) {
  return a < b ? a : b;
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

/** An empty state: what is empty, and, when something would fill it, what does. */
function empty(parent: HTMLElement | null, what: string, next?: string) {
  var p = el("p", what + (next ? " " + next : ""), "support empty");
  if (parent) parent.appendChild(p);
  return p;
}
