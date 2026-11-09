// @ts-nocheck
// The page's shared state and its calls to the server.

var view = "status",
  level = "group";
var periods = { flows: null, budget: null }; // loaded once per view
var chosen = { flows: "", budget: "" };
var money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
var cents = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
var ITEMS_SHOWN = 5; // items listed under a charge before the rest fold into one line
var PRIORITY = { high: 0, medium: 1, low: 2 };

function call(name, args, done, where) {
  var runner = google.script.run.withSuccessHandler(done).withFailureHandler(function (error) {
    document.getElementById(where).textContent = "Could not load: " + ((error && error.message) || error);
  });
  runner[name].apply(runner, args);
}

function cell(row, text, cls) {
  var td = document.createElement("td");
  td.textContent = text;
  if (cls) td.className = cls;
  row.appendChild(td);
  return td;
}
