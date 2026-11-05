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
  findSave: null,
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
window.google = { script: { run: runner(null, null) },
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
  await page.click("#edit-tabs button[data-tab=settings]");
  await page.click(`.md-row:has-text('${key}')`);
  await page.fill(".md-detail label:has-text('Value') input", value);
  await page.click(".md-detail button:has-text('Keep this change')");
}

const staged = (page) => page.$$eval("#pending-list li", (lis) => lis.map((li) => li.firstChild.textContent));

test("a change made while Save runs stays for the next save; only what was sent is cleared", async () => {
  const page = await open("window.__hold.saveChanges = true;");
  await page.click("#view-edit");
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
  await page.click("#view-edit");
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
  await page.click("#view-edit");
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
  await page.click("#view-edit");
  await page.click("#edit-tabs button[data-tab=waiting]");
  await page.waitForFunction(() => /Could not load/.test(document.getElementById("edit-body").textContent));
  await page.close();
});

test("a save whose bookkeeping failed reads as saved, with the warning, and clears what it sent", async () => {
  const page = await open("window.__saveWarning = 'Its list of edits for the Saved tab could not be kept (quota); the change set itself is saved.';");
  await page.click("#view-edit");
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
  await page.click("#view-edit");
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
  await page.click("#view-edit");
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

test("a past year shows its forms as they were, with no scenario to choose", async () => {
  const line = (key, label, value, filed) => ({ form: "Form 1040", line: key, key, label, value, filed });
  const year = (y, filed) => ({ year: y, filed: !!filed, forms: { "Form 1040": [line("24", "Total tax", 90, filed ? 90 : null)] } });
  const page = await open("window.__answers.getTaxes = " + JSON.stringify({
    years: [year("2002"), year("2001", true)], plan: [], accounts: [], notes: [],
    totals: [{ scenario: "as is", federal: 10, wisconsin: 5 }, { scenario: "stock 20% lower", federal: 0, wisconsin: 0 }] }) + ";");
  await page.click("#view-taxes");
  await page.waitForSelector("#taxes-body .panel");
  await page.click("#taxes-body [aria-label=Year] button:has-text('2001')");
  assert.equal(await page.$("#taxes-body [aria-label=Scenario]"), null);
  assert.doesNotMatch(await page.textContent("#taxes-body"), /Scenarios for this year|stock 20%/i);
  assert.match(await page.textContent("#taxes-body"), /Total tax/);
  assert.deepEqual(page.errors, []);
  await page.close();
});
