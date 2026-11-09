// The page's shared state and its calls to the server.

/** The pages: status, cashflow (flows) and the rest, each a view of its own. */
var view = "status",
  level = "group";
var periodsLoaded: { flows: string[] | null; budget: string[] | null } = { flows: null, budget: null }; // loaded once per view
var chosen: Record<string, string> = { flows: "", budget: "" };
var money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
var cents = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
var ITEMS_SHOWN = 5; // items listed under a charge before the rest fold into one line
var PRIORITY: Record<string, number> = { high: 0, medium: 1, low: 2 };

/** What a server function returns. */
type Answer<K extends keyof ServerCalls> = ReturnType<ServerCalls[K]>;

/** Calls the server's function name with args; done(its answer), or failed(the error). */
function ask<K extends keyof ServerCalls>(
  name: K,
  args: Parameters<ServerCalls[K]>,
  done: (answer: Answer<K>) => void,
  failed: (error: Error) => void,
): void {
  var runner = google.script.run.withSuccessHandler(done).withFailureHandler(failed);
  (runner[name] as (...a: Parameters<ServerCalls[K]>) => void).apply(runner, args);
}

/** Calls the server's function name with args, then done(its answer); a failure is said in the element where. */
function call<K extends keyof ServerCalls>(
  name: K,
  args: Parameters<ServerCalls[K]>,
  done: (answer: Answer<K>) => void,
  where: string,
) {
  ask(name, args, done, function (error) {
    byId(where).textContent = "Could not load: " + ((error && error.message) || error);
  });
}

/** The page's element with this id (the page has every one the code names). */
function byId(id: string): HTMLElement {
  var e = document.getElementById(id);
  if (!e) throw new Error("the page has no #" + id);
  return e;
}

function cell(row: HTMLElement, text: string | number, cls?: string) {
  var td = document.createElement("td");
  td.textContent = String(text);
  if (cls) td.className = cls;
  row.appendChild(td);
  return td;
}
