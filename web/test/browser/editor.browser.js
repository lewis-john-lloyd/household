// The views in a real browser (Playwright's Chromium), with google.script.run faked so each call's answer can
// be held back: Save's lifecycle, and loads that fail, as a person meets them. Run after `npm run build`:
//   node --test test/browser/editor.browser.js
// The data is made up.
const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const HTML = fs.readFileSync(path.join(__dirname, "..", "..", "dist", "Index.html"), "utf8");

// Every call is recorded in window.__calls and answered from window.__answers, at once unless window.__hold
// names it: then it waits in window.__held until the test calls window.__release(name).
const FAKE = `
window.__calls = []; window.__held = []; window.__hold = {}; window.__fail = {};
window.__answers = {
  getStatus: { stages: [{ stage: "compute", runId: "r1", at: "2001-03-02T07:00:00Z", summary: {}, lines: ["run r1"] }],
               attention: [], stale: false, projection: [], projectionYear: "", wiProjection: null, categories: null },
  getWaiting: [],
  getAppliedAnyway: [],
  discardWaiting: { remembered: true, warning: "" },
  findSave: null,
  getPeriods: [],
  getBudgetBook: { book: { targets: [], days: [], groups: {}, recurring: [] }, tracked: [] },
  getEditor: { baseRun: "r1", budgetLines: [], payeeRules: [], aliases: [], review: [], duplicates: [], withholding: [],
    categories: [{ category: "food", group: "living", priority: "high" }, { category: "kids", group: "family", priority: "" }],
    parameters: [{ key: "transfer_account", value: "checking", source: "seed" }, { key: "target_refund", value: "2000", source: "web" }] },
};
window.__release = function (name) {
  var keep = [];
  window.__held.forEach(function (h) { if (h.name === name) h.answer(); else keep.push(h); });
  window.__held = keep;
};
function runner(ok, fail) {
  return new Proxy({}, { get: function (_, name) {
    if (name === "withSuccessHandler") return function (f) { return runner(f, fail); };
    if (name === "withFailureHandler") return function (f) { return runner(ok, f); };
    return function () {
      var args = Array.prototype.slice.call(arguments);
      window.__calls.push({ name: name, args: JSON.parse(JSON.stringify(args)) });
      var answer = function () {
        if (window.__fail[name]) { fail && fail(new Error(window.__fail[name])); return; }
        var value = name === "saveChanges" ? { id: "f1", name: "changes_2001-03-02T070000.000Z_a1.json", count: args[0].length,
                                               madeFolder: false, warning: window.__saveWarning || "" }
                                           : window.__answers[name];
        ok && ok(value);
      };
      if (window.__hold[name]) window.__held.push({ name: name, answer: answer }); else setTimeout(answer, 0);
    };
  } });
}
window.google = { script: { run: runner(null, null),
                            url: { getLocation: function (f) { setTimeout(function () { f(window.__location || { parameter: {} }); }, 0); } } },
  charts: { load: function () {}, setOnLoadCallback: function (f) { setTimeout(f, 0); } } };
`;

let browser;
before(async () => { browser = await chromium.launch(); });
after(async () => { await browser.close(); });

async function open(setup) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route("https://www.gstatic.com/charts/loader.js", (r) => r.fulfill({ contentType: "text/javascript", body: FAKE + (setup || "") }));
  await page.route("https://fonts.googleapis.com/**", (r) => r.fulfill({ contentType: "text/css", body: "" }));
  await page.route("http://views.test/", (r) => r.fulfill({ contentType: "text/html", body: HTML }));
  await page.goto("http://views.test/");
  page.errors = errors;
  return page;
}

async function changeSetting(page, key, value) {
  // settings live on the Status page now, as one of its parts
  if (!(await page.isVisible("#edit-tabs button[data-tab='edit:settings']"))) {
    await page.click("#view-status");
    await page.click("#status-tabs button[data-tab='edit:settings']");
  }
  await page.click("#edit-tabs button[data-tab='edit:settings']");
  await page.click(`.md-row:has-text('${key}')`);
  await page.fill(".md-detail label:has-text('Value') input", value);
  await page.click(".md-detail button:has-text('Keep this change')");
}

// review, payee rules and duplicates live on Cashflow
async function openReview(page) {
  await page.click("#view-flows");
  await page.click("#flows-tabs button[data-tab='edit:review']");
}

const staged = (page) => page.$$eval("#pending-list li", (lis) => lis.map((li) => li.firstChild.textContent));

test("a change made while Save runs stays for the next save; only what was sent is cleared", async () => {
  const page = await open("window.__hold.saveChanges = true;");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");  // the save is now in flight, held
  await changeSetting(page, "target_refund", "1500");  // made while it runs
  await page.evaluate(() => window.__release("saveChanges"));
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent.map((e) => e.key), ["transfer_account"]);
  assert.deepEqual(await staged(page), ["setting target_refund = 1500"]);
  assert.match(await page.textContent("#pending-msg"), /1 change made since is still here/);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a setting changed again while its save runs keeps the newer value", async () => {
  const page = await open("window.__hold.saveChanges = true;");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await changeSetting(page, "transfer_account", "brokerage");
  await page.evaluate(() => window.__release("saveChanges"));
  await page.waitForSelector("#pending-msg.saved");
  assert.deepEqual(await staged(page), ["setting transfer_account = brokerage"]);
  await page.close();
});

test("a save that fails keeps every change and says so", async () => {
  const page = await open("window.__fail.saveChanges = 'Drive said no';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  assert.match(await page.textContent("#pending-msg"), /Save did not confirm \(your changes are still here\): .*Drive said no/);
  assert.deepEqual(await staged(page), ["setting transfer_account = savings"]);
  await page.close();
});

test("a load that fails says so instead of showing nothing", async () => {
  const page = await open("window.__fail.getStatus = 'Could not read the run_log tab of Reports';" +
                          "window.__fail.getWaiting = 'Could not open Reports';");
  await page.waitForFunction(() => /Could not load/.test(document.getElementById("status-title").textContent));
  assert.match(await page.textContent("#status-title"), /Could not load: .*run_log/);
  await page.click("#status-tabs button[data-tab='edit:waiting']");
  await page.waitForFunction(() => /Could not load/.test(document.getElementById("edit-body").textContent));
  await page.close();
});

test("a save whose bookkeeping failed reads as saved, with the warning, and clears what it sent", async () => {
  const page = await open("window.__saveWarning = 'Its list of edits for the Saved tab could not be kept (quota); the change set itself is saved.';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const text = await page.textContent("#pending-msg");
  assert.match(text, /^Saved 1 changes/);
  assert.match(text, /the change set itself is saved/);
  assert.doesNotMatch(text, /Not saved/);
  assert.deepEqual(await staged(page), []);
  await page.close();
});

test("after a save whose answer was lost, Save finds the file it wrote and writes nothing twice", async () => {
  const page = await open("window.__fail.saveChanges = 'The connection was lost';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  await page.evaluate(() => {  // Drive had it after all
    window.__answers.findSave = { id: "f1", name: "changes_2001-03-02T070000.000Z_a1.json", count: 1, madeFolder: false,
                                  warning: "", again: true };
  });
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const calls = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges" || c.name === "findSave"));
  assert.deepEqual(calls.map((c) => c.name), ["saveChanges", "findSave"]);
  assert.equal(calls[1].args[0], calls[0].args[2]);  // asked about the very request that failed
  assert.match(await page.textContent("#pending-msg"), /^The last save did go through\. Saved 1 changes/);
  assert.deepEqual(await staged(page), []);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("after a save that did not land, Save saves what is staged as a new request", async () => {
  const page = await open("window.__fail.saveChanges = 'Drive said no';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  await page.evaluate(() => { window.__fail.saveChanges = ""; });  // findSave answers null: it did not land
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const calls = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges" || c.name === "findSave"));
  assert.deepEqual(calls.map((c) => c.name), ["saveChanges", "findSave", "saveChanges"]);
  assert.notEqual(calls[2].args[2], calls[0].args[2]);
  assert.deepEqual(calls[2].args[0].map((e) => e.key), ["transfer_account"]);
  assert.deepEqual(await staged(page), []);
  await page.close();
});

test("a past year opens from the history with its forms as they were, and a second click closes it", async () => {
  const line = (key, label, value, filed) => ({ form: "Form 1040", line: key, key, label, value, filed });
  const year = (y, filed) => ({ year: y, filed: !!filed, forms: { "Form 1040": [line("24", "Total tax", 90, filed ? 90 : null)] } });
  const part = (y, projected) => ({ year: y, jurisdiction: "federal", split: "went", part: "Federal income tax", order: 0,
                                    value: 90, total: 900, projected });
  const page = await open("window.__answers.getTaxes = " + JSON.stringify({
    years: [year("2002"), year("2001", true)], plan: [], accounts: [], notes: [], filed: {},
    history: [part("2001", false), part("2002", true)],
    totals: [{ scenario: "as is", federal: 10, wisconsin: 5 }, { scenario: "stock 20% lower", federal: 0, wisconsin: 0 }] }) + ";");
  await page.click("#view-taxes");
  await page.waitForSelector("#taxes-body .panel");
  assert.match(await page.textContent("#taxes-body"), /Taxes for 2002/);
  await page.click("#taxes-body .subtabs button:has-text('History')");
  assert.match(await page.textContent("#taxes-body"), /2002 \(projected\)/);
  await page.click("#taxes-body .history-column:not(.projected) rect[fill=transparent]");
  await page.waitForSelector("#tax-year-detail");
  assert.doesNotMatch(await page.textContent("#taxes-body"), /Scenarios for this year|stock 20%/i);
  assert.match(await page.textContent("#taxes-body"), /2001 return.*Total tax/s);
  await page.click("#taxes-body .history-column:not(.projected) rect[fill=transparent]");
  assert.equal(await page.$("#tax-year-detail"), null);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("after a save whose file was discarded since, Save says so and keeps every change", async () => {
  const page = await open("window.__fail.saveChanges = 'The connection was lost';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  await page.evaluate(() => {
    window.__answers.findSave = { id: "f1", name: "changes_2001-03-02T070000.000Z_a1.json", count: 1, madeFolder: false,
                                  warning: "", again: true, discarded: true };
  });
  await page.click("#pending-save");
  await page.waitForFunction(() => /discarded since/.test(document.getElementById("pending-msg").textContent));
  assert.deepEqual(await staged(page), ["setting transfer_account = savings"]);
  const names = await page.evaluate(() => window.__calls.map((c) => c.name).filter((n) => n === "saveChanges" || n === "findSave"));
  assert.deepEqual(names, ["saveChanges", "findSave"]);
  await page.evaluate(() => { window.__fail.saveChanges = ""; });
  await page.click("#pending-save");  // the next Save sends them as a new request
  await page.waitForSelector("#pending-msg.saved");
  assert.deepEqual(await staged(page), []);
  await page.close();
});

test("a scheduled amount on a date that has one says what it replaces, so ingest can tell if it moved", async () => {
  const line = { line: "camp", effective: "2001-06-01", category: "kids", account: "", priority: "", rule: "scheduled",
                 amount: "90", per: "", note: "", in_force: "scheduled only", in_words: "scheduled amounts only",
                 this_month: "0", last_12_months: "0", scheduled: "2001-06-01 90.00", spends_from: "kids" };
  const page = await open("window.__answers.getEditor.budgetLines = [" + JSON.stringify(line) + "];" +
    "window.__answers.getEditor.scheduled = [" + JSON.stringify({ line: "camp", effective: "2001-06-01", category: "kids",
      account: "", priority: "", rule: "scheduled", amount: "90", per: "", note: "" }) + "];");
  await page.evaluate(() => { window.__answers.getBudgetBook.book.days = [[(function (d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); })(new Date()), "kids", 30]]; });
  await page.click("#view-budget");
  await page.click("table.bv tr.line:has-text('kids')");
  for (const [date, amount] of [["2001-06-01", "100"], ["2001-07-01", "40"]]) {  // the drawer stays open between them
    await page.fill(".drawer label:has-text('Scheduled on') input", date);
    await page.fill(".drawer label:has-text('Scheduled amount') input", amount);
    await page.click(".drawer button:has-text('Add scheduled amount')");
  }
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent.map((e) => [e.effective, e.amount, e.was && e.was.amount]), [["2001-06-01", "100", "90"], ["2001-07-01", "40", null]]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("change sets the last ingest never saw say the views' folder isn't in the inbox, not to run ingest", async () => {
  const page = await open("window.__answers.getWaiting = [{ id: 'f2', name: 'changes_2001-03-02T060000.000Z_b2.json', " +
    "savedAt: '2001-03-02T06:00:00.000Z', edits: ['payee ACME GROCER: food'], refused: '', unseen: true }];");
  await page.waitForSelector("#status-attention .attention");
  const text = await page.textContent("#status-attention");
  assert.match(text, /saved change set ingest hasn't seen/);
  assert.match(text, /Move web_edits into the inbox once/);
  assert.doesNotMatch(text, /Run ingest, then compute, to apply it/);
  await page.close();
});

const leaving = (page) => page.evaluate(() => {
  const e = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(e);
  return e.defaultPrevented;
});

test("while a save runs, Discard all and undo of what it sent wait for its answer", async () => {
  const page = await open("window.__hold.saveChanges = true;");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForFunction(() => window.__held.length === 1);
  assert.equal(await page.isDisabled("#pending-discard"), true);
  assert.equal(await page.isDisabled("#pending-save"), true);
  assert.equal(await page.isDisabled("#pending-list li button"), true);
  assert.equal(await leaving(page), true);
  await page.click("#pending-discard", { force: true });  // a click that lands anyway does nothing
  assert.deepEqual(await staged(page), ["setting transfer_account = savings"]);
  await page.evaluate(() => window.__release("saveChanges"));
  await page.waitForSelector("#pending-msg.saved");
  assert.deepEqual(await staged(page), []);
  assert.equal(await leaving(page), false);
  await page.close();
});

test("Discard all after a save that didn't confirm finds it saved, and offers to discard its file too", async () => {
  const page = await open("window.__fail.saveChanges = 'The connection was lost';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  assert.equal(await page.isDisabled("#pending-list li button"), true);  // undo waits until the save is settled
  await page.evaluate(() => {
    window.__answers.findSave = { id: "f1", name: "changes_2001-03-02T070000.000Z_a1.json", count: 1, madeFolder: false,
                                  warning: "", again: true, discarded: false };
  });
  await page.click("#pending-discard");
  await page.waitForFunction(() => /did go through/.test(document.getElementById("pending-msg").textContent));
  assert.match(await page.textContent("#pending-msg"), /its 1 changes are saved and ingest will apply them/);
  assert.deepEqual(await staged(page), []);
  await page.click("#pending-msg button:has-text('Discard that change set too')");
  await page.waitForFunction(() => /no ingest from now on will read it/.test(document.getElementById("pending-msg").textContent));
  assert.match(await page.textContent("#pending-msg"), /An ingest already running may have read it first/);
  const calls = await page.evaluate(() => window.__calls.filter((c) => c.name === "discardWaiting"));
  assert.deepEqual(calls.map((c) => c.args), [["f1"]]);
  assert.equal(await leaving(page), false);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("Discard all after a save that didn't land discards here and touches nothing in Drive", async () => {
  const page = await open("window.__fail.saveChanges = 'Drive said no';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  await page.click("#pending-discard");  // findSave answers null
  await page.waitForFunction(() => /hadn't gone through/.test(document.getElementById("pending-msg").textContent));
  assert.deepEqual(await staged(page), []);
  const names = await page.evaluate(() => window.__calls.map((c) => c.name));
  assert.equal(names.indexOf("discardWaiting"), -1);
  assert.equal(names.filter((n) => n === "findSave").length, 1);
  await page.close();
});

test("a save that didn't confirm keeps Save and the leave warning even with nothing staged", async () => {
  const page = await open("window.__fail.saveChanges = 'The connection was lost';");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  await changeSetting(page, "transfer_account", "brokerage");  // a newer edit replaces the one sent; it can be undone
  await page.click("#pending-show");
  await page.click("#pending-list li button");
  assert.deepEqual(await staged(page), []);
  assert.equal(await page.isVisible("#pending-save"), true);
  assert.equal(await leaving(page), true);
  await page.click("#pending-save");  // findSave answers null: nothing to save
  await page.waitForFunction(() => /nothing to save/.test(document.getElementById("pending-msg").textContent));
  assert.equal(await leaving(page), false);
  await page.close();
});

test("a discarded change set an ingest applied anyway is told on the status page, until Got it", async () => {
  const page = await open("window.__answers.getAppliedAnyway = [{ id: 'e5', name: 'changes_2001-03-02T060000.000Z_e5.json', " +
    "savedAt: '', discardedAt: '2001-03-02T06:00:30Z', edits: ['payee ACME GROCER: food'], more: 0, writtenAt: '2001-03-02T06:01:00Z' }];");
  await page.waitForSelector("#status-attention .attention");
  const text = await page.textContent("#status-attention");
  assert.match(text, /discarded change set was applied anyway/);
  assert.match(text, /written .*: payee ACME GROCER: food/);
  await page.click("#status-attention button:has-text('Got it')");
  const calls = await page.evaluate(() => window.__calls.filter((c) => c.name === "forgetDiscarded").map((c) => c.args));
  assert.deepEqual(calls, [["e5"]]);
  assert.doesNotMatch(await page.textContent("#status-attention"), /applied anyway/);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a discard the views couldn't remember says the status page can't tell, instead of promising it will", async () => {
  const page = await open("window.__fail.saveChanges = 'The connection was lost';" +
    "window.__answers.discardWaiting = { remembered: false, warning: 'The views could not remember this discard (quota), so check the next ingest.' };");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  await page.evaluate(() => {
    window.__answers.findSave = { id: "f1", name: "changes_2001-03-02T070000.000Z_a1.json", count: 1, madeFolder: false,
                                  warning: "", again: true, discarded: false };
  });
  await page.click("#pending-discard");
  await page.click("#pending-msg button:has-text('Discard that change set too')");
  await page.waitForFunction(() => /could not remember this discard/.test(document.getElementById("pending-msg").textContent));
  assert.doesNotMatch(await page.textContent("#pending-msg"), /the status page will say so/);
  await page.close();
});

test("a link opens the page it names, and a known setting not set yet is added as a new one", async () => {
  const page = await open("window.__location = { parameter: { view: 'edit', tab: 'settings' } };" +
    "window.__answers.getEditor.parameters.push({ key: 'payroll_url', value: '', source: 'not set', " +
    "about: 'Where W-4, WT-4 and supplemental withholding are changed' });");
  await page.waitForSelector("#edit:not([hidden]) .md-list");
  assert.equal(await page.getAttribute("#edit-tabs button[data-tab='edit:settings']", "aria-pressed"), "true");
  assert.equal(await page.getAttribute("#view-status", "aria-pressed"), "true");  // settings live on Status
  assert.match(await page.textContent(".md-row:has-text('payroll_url')"), /not set: Where W-4/);
  await changeSetting(page, "payroll_url", "https://payroll.example");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [{ kind: "parameter", key: "payroll_url", value: "https://payroll.example", was: null }]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a merchant split into its lines leaves review once every line has a category, and comes back on undo", async () => {
  const page = await open("window.__location = { parameter: { view: 'edit', tab: 'review' } };" +
    "window.__answers.getEditor.review = [{ key: 'ACME GROCER', kind: 'payee', lines: '2', total: '-60', first: '2001-02-01', " +
    "last: '2001-02-15', accounts: 'checking', example: 'ACME GROCER 12', guess: '', guessed_by: '', note: '' }];" +
    "window.__answers.getReviewLines = [{ id: 't1', date: '2001-02-01', payee: 'ACME GROCER 12', account: 'checking', amount: -20 }, " +
    "{ id: 't2', date: '2001-02-15', payee: 'ACME GROCER 12', account: 'checking', amount: -40 }];");
  await page.waitForSelector("#edit:not([hidden]) .md-list");
  const split = async (choices) => {
    await page.click(".md-row:has-text('ACME GROCER')");
    await page.click(".md-detail button:has-text('Split into its lines')");
    await page.waitForSelector("table.split-lines");
    const picks = await page.$$("table.split-lines select");
    for (let i = 0; i < choices.length; i++) if (choices[i]) await picks[i].selectOption(choices[i]);
    await page.click(".md-detail button:has-text('Keep answers')");
  };
  await split(["food", ""]);  // one line left on (choose): the question stays for it
  assert.equal(await page.$$eval(".md-row:has-text('ACME GROCER')", (r) => r.length), 1);
  await split(["food", "kids"]);
  assert.equal(await page.$$eval(".md-row:has-text('ACME GROCER')", (r) => r.length), 0);
  assert.deepEqual(await staged(page), ["ACME GROCER 12 2001-02-01: food", "ACME GROCER 12 2001-02-15: kids"]);
  await page.evaluate(() => Array.from(document.querySelectorAll("#pending-list li"))  // undo one: the question is back
    .filter((li) => li.textContent.includes("2001-02-15"))[0].querySelector("button").click());
  assert.equal(await page.$$eval(".md-row:has-text('ACME GROCER')", (r) => r.length), 1);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("recurring: a charge that comes back is offered to track, and one that stopped merges into the one that replaced it", async () => {
  const row = (key, status, last) => ({ key, marked: "", status, cadence: "monthly", charges: "4", last, latest: "30",
                                        yearly: "360", category: "petrol", example: key, note: "", includes: "", merged: "" });
  const page = await open("window.__location = { parameter: { view: 'budget', tab: 'recurring' } };" +
    "window.__answers.getEditor.subscriptions = " + JSON.stringify([
      row("BLUE PUMP FUEL", "stopped", "2001-01-03"), row("WILLOW ST", "active", "2001-06-03")]) + ";");
  await page.waitForSelector("#budget:not([hidden]) .alert");
  assert.match(await page.textContent(".alert"), /WILLOW ST comes back monthly for about \$30.00 and isn't tracked/);  // leak-guard: allow (made up)
  assert.match(await page.textContent(".rec-card.wide"), /Seem to have stopped.*BLUE PUMP FUEL.*last charged 2001-01-03/s);
  await page.click(".alert button:has-text('Track it')");
  await page.click(".rec-row:has-text('BLUE PUMP FUEL')");
  await page.selectOption(".drawer label:has-text('Merge into') select", "WILLOW ST");
  await page.click(".drawer button:has-text('Merge')");
  assert.deepEqual(await staged(page), ["WILLOW ST: track as recurring", "BLUE PUMP FUEL: part of WILLOW ST"]);
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [{ kind: "subscription", key: "WILLOW ST", subscription: "yes" },
                          { kind: "recurring_merge", key: "BLUE PUMP FUEL", same_as: "WILLOW ST" }]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("the budget compares any period with another, and a line's target can come from its recurring charges", async () => {
  // made-up spending: today, and the same day a year earlier; food budgeted at 60 this month
  const page = await open("window.__location = { parameter: { view: 'budget' } };" +
    "(function () { var t = (function (d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); })(new Date()), y = (Number(t.slice(0, 4)) - 1) + t.slice(4);" +
    "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { food: 'living', media: 'fun' }, recurring: []," +
    "    targets: [{ month: t.slice(0, 7), category: 'food', priority: 'high', target: 60 }]," +
    "    days: [[t, 'food', 12], [y, 'food', 8], [t, 'media', 5]] } };" +
    "  window.__answers.getEditor.budgetLines = [{ line: 'food', effective: '2001-01-01', category: 'food', account: '', priority: 'high'," +
    "    rule: 'fixed', amount: '60', per: 'monthly', note: '', in_force: 'yes', in_words: '60.00 a month', this_month: '12'," +
    "    last_12_months: '0', scheduled: '', spends_from: 'food' }];" +
    "  window.__answers.getEditor.subscriptions = [{ key: 'PAGE TURNER BOOKS', marked: 'yes', status: 'active', cadence: 'monthly'," +
    "    charges: '9', last: t, latest: '10', yearly: '120', category: 'food', example: '', note: '', includes: '', merged: '' }];" +
    "})();");
  await page.waitForSelector("table.bv");
  const food = await page.$$eval("table.bv tr.line:has-text('food') td", (tds) => tds.map((td) => td.textContent));
  assert.deepEqual([food[0], food[1], food[3], food[5], food[6]], ["food", "60.00 a month", "$12", "$8", "+$4"]);  // leak-guard: allow (made up)
  assert.match(await page.textContent("table.bv thead"), /same weeks/);
  assert.match(await page.textContent(".bv-summary"), /\$60budgeted for .*\$17spent, 28% of the budget/);  // leak-guard: allow (made up)
  await page.click("table.bv th button.sort:has-text('Spent')");  // sorts by spent, largest first; again reverses
  assert.equal(await page.getAttribute("table.bv th:nth-child(4)", "aria-sort"), "descending");
  await page.click("table.bv th button.sort:has-text('Spent')");
  assert.equal(await page.getAttribute("table.bv th:nth-child(4)", "aria-sort"), "ascending");
  assert.equal(await page.locator(".bv-gauge").count() >= 2, true);  // the whole budget and each group
  assert.equal(await page.$$eval("table.bv tr.group", (rows) => rows.length), 2);  // living, then fun
  await page.click(".bv-controls button:has-text('Year')");
  const year = String(new Date().getFullYear());
  assert.equal(await page.inputValue(".bv-picker select"), year);
  assert.equal(await page.inputValue(".bv-compare select"), String(Number(year) - 1));
  await page.click(".bv-controls button:has-text('Quarter')");
  assert.match(await page.inputValue(".bv-picker select"), /^\d{4}-Q[1-4]$/);
  await page.click("table.bv tr.line:has-text('food')");
  await page.waitForSelector(".drawer svg.history");
  await page.selectOption(".drawer label:has-text('Target from') select", "recurring");
  await page.fill(".drawer label:has-text('Allowance') input", "20");
  assert.match(await page.textContent(".drawer .recurring-sum"), /PAGE TURNER BOOKS \(monthly\)\$10\.00 a month.*Monthly target\$30\.00/s);  // leak-guard: allow (made up)
  await page.click(".drawer button:has-text('Keep this change')");
  assert.match((await staged(page))[0], /^budget line food: recurring 20 monthly for food from /);
  await page.keyboard.press("Escape");
  assert.equal(await page.$$eval(".drawer", (d) => d.length), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("jobs: choosing a stretch of a pay line shows it, and a correction is staged like any change", async () => {
  const j = (effective, employer, salary, fte) => ({ effective, person: "alex", employer, salary: String(salary), band: "", rating: "",
                                                      fte: fte ? String(fte) : "", note: "", stubs: effective === "2020-01-15" ? "agrees" : "" });
  const page = await open("window.__location = { parameter: { view: 'jobs' } };" +
    "window.__answers.getEditor.jobs = " + JSON.stringify([j("2016-04-01", "Acme Widgets", 80000), j("2020-01-15", "Quill & Ink", 90000),
                                                           j("2022-05-01", "Quill & Ink", 90000, 0.6)]) + "; window.__answers.getEditor.payCheck = [];");
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.waitForSelector(".pay-chart svg");
  assert.equal(await page.isVisible("#edit-tabs button[data-tab=jobs]"), false);
  await page.focus(".seg-hit[aria-label^='Quill & Ink from 2020-01-15']");
  await page.keyboard.press("Enter");
  assert.match(await page.textContent(".job-detail"), /Quill & Ink, 2020-01-15 to 2022-05-01.*\$90,000.*agree with it/s);  // leak-guard: allow (made up)
  await page.click(".job-detail button:has-text('Correct this')");
  await page.fill("dialog.job-dialog label:has-text('Band') input", "L4");
  await page.click("dialog.job-dialog button:has-text('Keep this correction')");
  assert.deepEqual(await staged(page), ["job: alex at Quill & Ink at 90000 from 2020-01-15"]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("each editor part lives on the page it is about, in that page's submenu", async () => {
  const page = await open("window.__location = { parameter: { view: 'edit', tab: 'withholding' } };");
  await page.waitForSelector("#edit:not([hidden])");
  assert.equal(await page.getAttribute("#view-taxes", "aria-pressed"), "true");
  assert.deepEqual(await page.$$eval("#edit-tabs button", (b) => b.map((x) => x.textContent)), ["This year", "History", "Withholding"]);
  await page.click("#view-budget");
  await page.click("#budget-tabs button[data-tab='edit:categories']");
  await page.waitForSelector("#edit:not([hidden])");
  assert.equal(await page.getAttribute("#view-budget", "aria-pressed"), "true");
  assert.equal(await page.getAttribute("#edit-tabs button[data-tab='edit:categories']", "aria-pressed"), "true");
  await page.click("#edit-tabs button[data-tab=lines]");  // back to the budget's own part
  await page.waitForSelector("#budget:not([hidden])");
  assert.deepEqual(await page.$$eval("nav.tabs button", (b) => b.map((x) => x.textContent)),
                   ["Status", "Jobs", "Budget", "Cashflow", "Taxes", "Home", "Investments", "Retirement"]);
  await openReview(page);
  assert.equal(await page.getAttribute("#view-flows", "aria-pressed"), "true");
  assert.deepEqual(await page.$$eval("#edit-tabs button", (b) => b.map((x) => x.textContent)),
                   ["Sankey", "Table", "Review", "Payee rules", "Duplicates"]);
  assert.equal(await page.getAttribute("#edit-tabs button[data-tab='edit:review']", "aria-pressed"), "true");
  await page.click("#edit-tabs button[data-tab=table]");  // back to the flow of money
  await page.waitForSelector("#flows:not([hidden])");
  assert.equal(await page.getAttribute("#flows-tabs button[data-tab=table]", "aria-pressed"), "true");
  assert.deepEqual(page.errors, []);
  await page.close();
});

const RETIREMENT = {
  chosenYear: 2031, chosenStrategy: "bracket/to22/constant", notes: ["2031 (51/49) with the plan", "no Social Security statement for sam"],
  years: [2030, 2031, 2032].map((y, i) => ({ year: y, ages: (50 + i) + "/" + (48 + i), chosen: y === 2031, monteCarlo: 0.85 + i * 0.04,
    historical: 0.8 + i * 0.05, fixed: [{ scenario: "5%", success: false, shortFrom: 2060, end: 0 }, { scenario: "9%", success: true, shortFrom: null, end: 90 }],
    atRetirement: 40 + i * 5, firstSpending: 4, withdrawalRate: 0.05, medianEnd: 60, p10End: 1 })),
  strategies: [
    { key: "bracket/to22/constant", label: "Pre-tax to the 12% bracket, then taxable; convert to the top of 22%; constant real spending", order: "bracket",
      convertTo: 0.22, rule: "constant", sepp: false, claims: "", chosen: true, historical: 0.9, monteCarlo: 0.89, afterTax: 70, afterTaxP10: 4,
      tax: 8, irmaa: 1, penalty: 0, converted: 30, spent: 90, lowestSpending: 1 },
    { key: "bracket/to22/vpw", label: "Variable percentage withdrawal", order: "bracket", convertTo: 0.22, rule: "vpw", sepp: false, claims: "",
      chosen: false, historical: 1, monteCarlo: 1, afterTax: 20, afterTaxP10: 0, tax: 5, irmaa: 0, penalty: 0, converted: 10, spent: 95, lowestSpending: 0.4 },
    { key: "bracket/to22/constant/alex70/sam67", label: "Claiming later", order: "bracket", convertTo: 0.22, rule: "constant", sepp: false,
      claims: "alex 70, sam 67", chosen: false, historical: 0.92, monteCarlo: 0.9, afterTax: 72, afterTaxP10: 4, tax: 8, irmaa: 1, penalty: 0,
      converted: 30, spent: 90, lowestSpending: 1 }],
  plans: { "bracket/to22/constant": [2030, 2031, 2032].map((y, i) => ({ year: y, ages: (50 + i) + "/" + (48 + i), phase: y < 2031 ? "working" : "retired",
    contributions: 7, spending: 4, health: 1, socialSecurity: 0, rmd: 0, fromTaxable: 2, fromPreTax: 3, fromRoth: 0, fromHsa: 0, converted: 5,
    ordinaryIncome: 8, capitalGains: 1, federalTax: 1, stateTax: 0.5, irmaa: 0, penalty: 0, short: y === 2032 ? 3 : 0, taxable: 10, preTax: 20,
    roth: 5, hsa: 1, total: 36 - i, p10: 20, p50: 36, p90: 60 })) },
  benefits: [{ person: "alex", claimAge: 67, claimYear: 2047, statement: 27, computed: 27, plan: 24, planYearly: 288, aime: 50, pia: 24, note: "" },
             { person: "sam", claimAge: 67, claimYear: 2049, statement: null, computed: 0, plan: 0, planYearly: 144, aime: 0, pia: 0,
               note: "no statement: set social_security_monthly:sam" }],
  earnings: [{ person: "alex", year: 2001, earnings: 10, indexed: 20, counted: true, projected: false },
             { person: "alex", year: 2027, earnings: 10, indexed: 10, counted: true, projected: true }],
  assumptions: [{ section: "today", key: "taxable", label: "Taxable accounts", value: "90", source: "investment values today", setting: "" },
                { section: "changes", key: "kids", label: "kids (a group)", value: "0 from 2032", source: "now 20 a year", setting: "retirement_spending:kids" }],
  budget: [
    { line: "home", group: "home", kind: "group", now: 30, firstYear: 30, atRetirement: 30, retireYear: 2031, setting: "", isSet: false, lumpy: null, note: "" },
    { line: "household", group: "home", kind: "category", now: 10, firstYear: 10, atRetirement: 10, retireYear: 2031, setting: "", isSet: false, lumpy: null, note: "" },
    { line: "hobby", group: "home", kind: "category", now: 20, firstYear: 20, atRetirement: 20, retireYear: 2031, setting: "", isSet: false, lumpy: 0.9, note: "" },
    { line: "kids", group: "kids", kind: "group", now: 20, firstYear: 20, atRetirement: 0, retireYear: 2031, setting: "0 from 2031", isSet: true, lumpy: null, note: "" },
    { line: "gifts", group: "", kind: "added", now: 0, firstYear: 0, atRetirement: 5, retireYear: 2031, setting: "5 from 2031", isSet: true, lumpy: null, note: "" }],
};

test("retirement: the plan, each year, the strategies, Social Security and the assumptions, each in its own part", async () => {
  const page = await open(`window.__answers.getRetirement = ${JSON.stringify(RETIREMENT)};
                           window.__location = { parameter: { view: 'retirement' } };`);
  await page.waitForSelector("#retirement-body h2");
  assert.equal(await page.getAttribute("#view-retirement", "aria-pressed"), "true");
  assert.equal(await page.textContent("#retirement-body h2"), "Retire in 2031, at 51 and 49");
  assert.deepEqual(await page.$$eval("#retirement-body .subtabs button", (b) => b.map((x) => x.textContent)),
                   ["Plan", "Budget", "Year by year", "Strategies", "Social Security", "Assumptions"]);
  assert.equal(await page.textContent("#retirement-body tr.picked td"), "2031");
  assert.match(await page.textContent("#retirement-body table"), /short at 80\/78/);  // at 5% the money runs out in 2060
  await page.click("#retirement-body .subtabs button:has-text('Year by year')");
  assert.equal(await page.$$eval("#retirement-body tr.short", (r) => r.length), 1);
  await page.click("#retirement-body .subtabs button:has-text('Strategies')");
  assert.match(await page.textContent("#retirement-body"), /alex 70, sam 67/);
  await page.click("#retirement-body .subtabs button:has-text('Social Security')");
  assert.match(await page.textContent("#retirement-body"), /set social_security_monthly:sam/);
  await page.click("#retirement-body .subtabs button:has-text('Assumptions')");
  assert.match(await page.textContent("#retirement-body"), /retirement_spending:kids/);
  await page.click("#retirement-body button:has-text('Open Settings')");
  await page.waitForSelector("#edit:not([hidden])");
  assert.equal(await page.getAttribute("#view-status", "aria-pressed"), "true");
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("retirement budget: each line from the last 12 months, changed, left out or added as a staged setting", async () => {
  const page = await open(`window.__answers.getRetirement = ${JSON.stringify(RETIREMENT)};
                           window.__location = { parameter: { view: 'retirement', tab: 'budget' } };`);
  await page.waitForSelector("#retirement-body h2");
  assert.equal(await page.textContent("#retirement-body h2"), "Spending in retirement");
  const figures = await page.$$eval("#retirement-body .w-figure .num", (n) => n.map((x) => x.textContent));
  assert.deepEqual(figures.map((f) => f.replace("\u0024", "")), ["50", "50", "35"]);  // groups now; groups and added lines in 2030 and 2031
  assert.match(await page.textContent("#retirement-body tr:has-text('hobby')"), /one-off\?/);
  await page.click("#retirement-body tr:has-text('hobby') button:has-text('Change')");
  await page.click("#retirement-body .budget-edit button:has-text('Leave out')");
  await page.click("#retirement-body tr:has-text('kids') button:has-text('Change')");
  assert.equal(await page.inputValue("#retirement-body .budget-edit input >> nth=0"), "0");
  assert.equal(await page.inputValue("#retirement-body .budget-edit input >> nth=1"), "2031");
  await page.fill("#retirement-body .budget-edit input >> nth=2", "4");
  await page.fill("#retirement-body .budget-edit input >> nth=3", "2029");
  await page.click("#retirement-body .budget-edit button:has-text('Keep this change')");
  assert.match(await page.textContent("#retirement-body .budget-edit .error"), /starts after/);
  await page.fill("#retirement-body .budget-edit input >> nth=3", "2040");
  await page.click("#retirement-body .budget-edit button:has-text('Keep this change')");
  await page.click("#retirement-body button:has-text('Add a line')");
  await page.fill("#retirement-body .budget-edit input >> nth=0", "Travel Fund");
  await page.fill("#retirement-body .budget-edit input >> nth=1", "6");
  await page.click("#retirement-body .budget-edit button:has-text('Keep this change')");
  assert.deepEqual(await staged(page), ["retirement budget: hobby left out (0)", "retirement budget: kids = 0 from 2031; 4 from 2040",
                                        "retirement budget: travel_fund = 6"]);
  assert.match(await page.textContent("#retirement-body tr:has-text('hobby')"), /edited/);
  await page.click("#pending-save");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent.map((e) => [e.key, e.value, e.was]), [["retirement_spending:hobby", "0", null],
    ["retirement_spending:kids", "0 from 2031; 4 from 2040", { value: "0 from 2031" }], ["retirement_spending:travel_fund", "6", null]]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

const LINE = (key, group, label, owner, values) => ({ key, group, label, owner,
  points: values.map((v, i) => [`20${String(10 + i).padStart(2, "0")}-12-31`, v]) });
const INVESTMENTS = {
  asOf: "2025-12-31", total: 90, buckets: [{ key: "taxable", label: "Taxable", value: 90 }], funds: [], trades: [], allocation: [],
  accounts: [{ account: "a_brokerage", owner: "alex", wrapper: "brokerage", bucket: "taxable", value: 90, carried: false }],
  owners: [{ owner: "alex", parts: { taxable: 90 }, total: 90 }], history: [], notes: [], openLots: [], lotSales: [], lotYears: [],
  lotNotes: [],
  lines: [LINE("net_worth", "total", "Net worth", "", [10, 20, 30]), LINE("invested", "total", "Investments", "", [10, 20, 30]),
          LINE("owner:alex", "owner", "alex", "alex", [10, 20, 30]), LINE("bucket:taxable", "bucket", "Taxable", "", [10, 20, 30])]
    .concat(Array.from({ length: 9 }, (_, n) => LINE(`account:acct_${n}`, "account", `acct_${n}`, "alex", [n, n + 1, n + 2]))),
};

test("investments: over time draws any lines chosen, each keeping its color, up to eight", async () => {
  const page = await open(`window.__answers.getInvestments = ${JSON.stringify(INVESTMENTS)};
                           window.__location = { parameter: { view: 'investments', tab: 'history' } };`);
  await page.waitForSelector("#investments-body .line-picks");
  assert.equal(await page.textContent("#investments-body h2"), "Over time");
  assert.deepEqual(await page.$$eval("#investments-body .subtabs button", (b) => b.map((x) => x.textContent)),
                   ["Over time", "Overview", "Allocation", "Accounts", "Lots"]);
  const pressed = () => page.$$eval("#investments-body .line-pick[aria-pressed='true']", (b) => b.map((x) => x.textContent));
  assert.deepEqual(await pressed(), ["Net worth", "Investments"]);  // no home equity line to start with
  const color = (name) => page.$eval(`#investments-body .line-pick:has-text('${name}') .swatch`, (s) => s.style.background);
  await page.setViewportSize({ width: 1280, height: 400 });
  await page.evaluate(() => window.scrollTo(0, 300));
  await page.click("#investments-body .line-pick:has-text('acct_3')");
  assert.equal(await page.evaluate(() => window.scrollY), 300);  // choosing a line keeps the page where it was
  assert.equal(await color("acct_3"), "var(--series-3)");
  await page.click("#investments-body .line-pick:has-text('Net worth')");
  assert.equal(await color("acct_3"), "var(--series-3)");  // taking one off doesn't repaint the rest
  assert.equal(await page.$$eval("#investments-body table.w-table tr:has(td)", (r) => r.length), 2);
  for (const n of [0, 1, 2, 4, 5, 6]) await page.click(`#investments-body .line-pick:has-text('acct_${n} ')`);
  assert.equal((await pressed()).length, 8);
  assert.equal(await page.$eval("#investments-body .line-pick:has-text('acct_7')", (b) => b.disabled), true);
  await page.click("#investments-body .segmented button:has-text('1 year')");
  assert.equal(await page.$eval("#investments-body .segmented button[aria-pressed='true']", (b) => b.textContent), "1 year");
  await page.click("#investments-body .subtabs button:has-text('Overview')");
  assert.equal(await page.$$eval("#investments-body svg.w-chart, #investments-body .line-picks", (x) => x.length), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});
