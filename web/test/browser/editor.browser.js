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
  getWaitingEdits: [],
  getAppliedAnyway: [],
  discardWaiting: { remembered: true, warning: "" },
  findSave: null,
  getPeriods: [],
  getBudgetBook: { book: { targets: [], days: [], groups: {}, recurring: [] }, tracked: [], charges: [] },
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
before(async () => {
  browser = await chromium.launch();
});
after(async () => {
  await browser.close();
});

async function open(setup) {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route("https://www.gstatic.com/charts/loader.js", (r) =>
    r.fulfill({ contentType: "text/javascript", body: FAKE + (setup || "") }),
  );
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

test("a setting shows every value it has had, newest first, where it is changed", async () => {
  const page = await open(
    "window.__answers.getEditor.parameterHistory = [" +
      "  { key: 'transfer_account', n: '1', value: 'checking', source: 'seed' }," +
      "  { key: 'transfer_account', n: '2', value: 'savings', source: 'web' }," +
      "  { key: 'transfer_account', n: '3', value: 'brokerage', source: 'web 2026-11-13' }," +
      "  { key: 'target_refund', n: '1', value: '2000', source: 'web' }];" +
      "window.__answers.getEditor.parameters = [{ key: 'transfer_account', value: 'brokerage', source: 'web 2026-11-13' }," +
      "  { key: 'target_refund', value: '2000', source: 'web' }];",
  );
  await page.click("#status-tabs button[data-tab='edit:settings']");
  await page.click(".md-row:has-text('transfer_account')");
  await page.waitForSelector(".md-detail .setting-history");
  assert.deepEqual(await page.$$eval(".md-detail .setting-history li", (lis) => lis.map((li) => li.textContent)), [
    "brokerage from the views, Nov 13, 2026, in force",
    "savings from the views",
    "checking when the ledger was set up",
  ]);
  assert.match(await page.textContent(".md-detail"), /Sourcefrom the views, Nov 13, 2026/);
  // a setting with one value has no history to show
  await page.click(".md-row:has-text('target_refund')");
  await page.waitForSelector(".md-detail:has-text('target_refund')");
  assert.equal(await page.locator(".md-detail .setting-history").count(), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a change made while Save runs stays for the next save; only what was sent is cleared", async () => {
  const page = await open("window.__hold.saveChanges = true;");
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save"); // the save is now in flight, held
  await changeSetting(page, "target_refund", "1500"); // made while it runs
  await page.evaluate(() => window.__release("saveChanges"));
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(
    sent.map((e) => e.key),
    ["transfer_account"],
  );
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
  const page = await open(
    "window.__fail.getStatus = 'Could not read the run_log tab of Reports';" +
      "window.__fail.getWaiting = 'Could not open Reports';",
  );
  await page.waitForFunction(() => /Could not load/.test(document.getElementById("status-title").textContent));
  assert.match(await page.textContent("#status-title"), /Could not load: .*run_log/);
  await page.click("#status-tabs button[data-tab='edit:waiting']");
  await page.waitForFunction(() => /Could not load/.test(document.getElementById("edit-body").textContent));
  await page.close();
});

test("the status page's cards, the panels below them and the heading above keep apart", async () => {
  const page = await open(
    "window.__answers.getStatus.attention = [1, 2, 3, 4].map(function (n) { return { key: 'stubs_missing', " +
      "label: 'pay deposit has no stub', count: 1, note: 'line ' + n, items: [], detail: [], flags: [], action: 'review' }; });",
  );
  await page.waitForSelector("#status-attention .attention");
  const box = (s) => page.locator(s).first().boundingBox();
  const [head, cards, panels] = [await box(".status-head"), await box("#status-attention"), await box("#status-overview .two")];
  assert.ok(cards.y >= head.y + head.height + 16, "the cards sit below the heading");
  assert.ok(panels.y >= cards.y + cards.height + 16, "the panels sit below the cards");
  await page.close();
});

test("a save whose bookkeeping failed reads as saved, with the warning, and clears what it sent", async () => {
  const page = await open(
    "window.__saveWarning = 'Its list of edits for the Saved tab could not be kept (quota); the change set itself is saved.';",
  );
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
  await page.evaluate(() => {
    // Drive had it after all
    window.__answers.findSave = {
      id: "f1",
      name: "changes_2001-03-02T070000.000Z_a1.json",
      count: 1,
      madeFolder: false,
      warning: "",
      again: true,
    };
  });
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const calls = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges" || c.name === "findSave"));
  assert.deepEqual(
    calls.map((c) => c.name),
    ["saveChanges", "findSave"],
  );
  assert.equal(calls[1].args[0], calls[0].args[2]); // asked about the very request that failed
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
  await page.evaluate(() => {
    window.__fail.saveChanges = "";
  }); // findSave answers null: it did not land
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const calls = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges" || c.name === "findSave"));
  assert.deepEqual(
    calls.map((c) => c.name),
    ["saveChanges", "findSave", "saveChanges"],
  );
  assert.notEqual(calls[2].args[2], calls[0].args[2]);
  assert.deepEqual(
    calls[2].args[0].map((e) => e.key),
    ["transfer_account"],
  );
  assert.deepEqual(await staged(page), []);
  await page.close();
});

test("a past year opens from the history with its forms as they were, and a second click closes it", async () => {
  const line = (key, label, value, filed) => ({ form: "Form 1040", line: key, key, label, value, filed });
  const year = (y, filed) => ({
    year: y,
    filed: !!filed,
    forms: { "Form 1040": [line("24", "Total tax", 90, filed ? 90 : null)] },
  });
  const part = (y, projected) => ({
    year: y,
    jurisdiction: "federal",
    split: "went",
    part: "Federal income tax",
    order: 0,
    value: 90,
    total: 900,
    projected,
  });
  const page = await open(
    "window.__answers.getTaxes = " +
      JSON.stringify({
        years: [year("2002"), year("2001", true)],
        plan: [],
        accounts: [],
        notes: [],
        filed: {},
        history: [part("2001", false), part("2002", true)],
        totals: [
          { scenario: "as is", federal: 10, wisconsin: 5 },
          { scenario: "stock 20% lower", federal: 0, wisconsin: 0 },
        ],
      }) +
      ";",
  );
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
    window.__answers.findSave = {
      id: "f1",
      name: "changes_2001-03-02T070000.000Z_a1.json",
      count: 1,
      madeFolder: false,
      warning: "",
      again: true,
      discarded: true,
    };
  });
  await page.click("#pending-save");
  await page.waitForFunction(() => /discarded since/.test(document.getElementById("pending-msg").textContent));
  assert.deepEqual(await staged(page), ["setting transfer_account = savings"]);
  const names = await page.evaluate(() =>
    window.__calls.map((c) => c.name).filter((n) => n === "saveChanges" || n === "findSave"),
  );
  assert.deepEqual(names, ["saveChanges", "findSave"]);
  await page.evaluate(() => {
    window.__fail.saveChanges = "";
  });
  await page.click("#pending-save"); // the next Save sends them as a new request
  await page.waitForSelector("#pending-msg.saved");
  assert.deepEqual(await staged(page), []);
  await page.close();
});

test("a scheduled amount on a date that has one says what it replaces, so ingest can tell if it moved", async () => {
  const line = {
    line: "camp",
    effective: "2001-06-01",
    category: "kids",
    account: "",
    priority: "",
    rule: "scheduled",
    amount: "90",
    per: "",
    note: "",
    in_force: "scheduled only",
    in_words: "scheduled amounts only",
    this_month: "0",
    last_12_months: "0",
    scheduled: "2001-06-01 90.00",
    spends_from: "kids",
  };
  const page = await open(
    "window.__answers.getEditor.budgetLines = [" +
      JSON.stringify(line) +
      "];" +
      "window.__answers.getEditor.scheduled = [" +
      JSON.stringify({
        line: "camp",
        effective: "2001-06-01",
        category: "kids",
        account: "",
        priority: "",
        rule: "scheduled",
        amount: "90",
        per: "",
        note: "",
      }) +
      "];",
  );
  await page.evaluate(() => {
    window.__answers.getBudgetBook.book.days = [
      [
        (function (d) {
          return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
        })(new Date()),
        "kids",
        30,
      ],
    ];
  });
  await page.click("#view-budget");
  await page.click("table.bv tr.line:has-text('camp')"); // kids' one budget line, shown by its name
  await page.click(".drawer .line-change > button:has-text('Change')"); // the form waits behind Change
  for (const [date, amount] of [
    ["2001-06-01", "100"],
    ["2001-07-01", "40"],
  ]) {
    // the drawer stays open between them
    await page.fill(".drawer label:has-text('Scheduled on') input", date);
    await page.fill(".drawer label:has-text('Amount that month') input", amount);
    await page.click(".drawer button:has-text('Schedule it')");
  }
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(
    sent.map((e) => [e.effective, e.amount, e.was && e.was.amount]),
    [
      ["2001-06-01", "100", "90"],
      ["2001-07-01", "40", null],
    ],
  );
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("change sets the last ingest never saw say the views' folder isn't in the inbox, not to run ingest", async () => {
  const page = await open(
    "window.__answers.getWaiting = [{ id: 'f2', name: 'changes_2001-03-02T060000.000Z_b2.json', " +
      "savedAt: '2001-03-02T06:00:00.000Z', edits: ['payee ACME GROCER: food'], refused: '', unseen: true }];",
  );
  await page.waitForSelector("#status-attention .attention");
  const text = await page.textContent("#status-attention");
  assert.match(text, /saved change set ingest hasn't seen/);
  assert.match(text, /Move web_edits into the inbox once/);
  assert.doesNotMatch(text, /Run ingest, then compute, to apply it/);
  await page.close();
});

const leaving = (page) =>
  page.evaluate(() => {
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
  await page.click("#pending-discard", { force: true }); // a click that lands anyway does nothing
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
  assert.equal(await page.isDisabled("#pending-list li button"), true); // undo waits until the save is settled
  await page.evaluate(() => {
    window.__answers.findSave = {
      id: "f1",
      name: "changes_2001-03-02T070000.000Z_a1.json",
      count: 1,
      madeFolder: false,
      warning: "",
      again: true,
      discarded: false,
    };
  });
  await page.click("#pending-discard");
  await page.waitForFunction(() => /did go through/.test(document.getElementById("pending-msg").textContent));
  assert.match(await page.textContent("#pending-msg"), /its 1 changes are saved and ingest will apply them/);
  assert.deepEqual(await staged(page), []);
  await page.click("#pending-msg button:has-text('Discard that change set too')");
  await page.waitForFunction(() => /no ingest from now on will read it/.test(document.getElementById("pending-msg").textContent));
  assert.match(await page.textContent("#pending-msg"), /An ingest already running may have read it first/);
  const calls = await page.evaluate(() => window.__calls.filter((c) => c.name === "discardWaiting"));
  assert.deepEqual(
    calls.map((c) => c.args),
    [["f1"]],
  );
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
  await page.click("#pending-discard"); // findSave answers null
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
  await changeSetting(page, "transfer_account", "brokerage"); // a newer edit replaces the one sent; it can be undone
  await page.click("#pending-show");
  await page.click("#pending-list li button");
  assert.deepEqual(await staged(page), []);
  assert.equal(await page.isVisible("#pending-save"), true);
  assert.equal(await leaving(page), true);
  await page.click("#pending-save"); // findSave answers null: nothing to save
  await page.waitForFunction(() => /nothing to save/.test(document.getElementById("pending-msg").textContent));
  assert.equal(await leaving(page), false);
  await page.close();
});

test("a discarded change set an ingest applied anyway is told on the status page, until Got it", async () => {
  const page = await open(
    "window.__answers.getAppliedAnyway = [{ id: 'e5', name: 'changes_2001-03-02T060000.000Z_e5.json', " +
      "savedAt: '', discardedAt: '2001-03-02T06:00:30Z', edits: ['payee ACME GROCER: food'], more: 0, writtenAt: '2001-03-02T06:01:00Z' }];",
  );
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
  const page = await open(
    "window.__fail.saveChanges = 'The connection was lost';" +
      "window.__answers.discardWaiting = { remembered: false, warning: 'The views could not remember this discard (quota), so check the next ingest.' };",
  );
  await openReview(page);
  await page.waitForSelector(".md-list");
  await changeSetting(page, "transfer_account", "savings");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.error");
  await page.evaluate(() => {
    window.__answers.findSave = {
      id: "f1",
      name: "changes_2001-03-02T070000.000Z_a1.json",
      count: 1,
      madeFolder: false,
      warning: "",
      again: true,
      discarded: false,
    };
  });
  await page.click("#pending-discard");
  await page.click("#pending-msg button:has-text('Discard that change set too')");
  await page.waitForFunction(() => /could not remember this discard/.test(document.getElementById("pending-msg").textContent));
  assert.doesNotMatch(await page.textContent("#pending-msg"), /the status page will say so/);
  await page.close();
});

test("a link opens the page it names, and a known setting not set yet is added as a new one", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'edit', tab: 'settings' } };" +
      "window.__answers.getEditor.parameters.push({ key: 'payroll_url', value: '', source: 'not set', " +
      "about: 'Where W-4, WT-4 and supplemental withholding are changed' });",
  );
  await page.waitForSelector("#edit:not([hidden]) .md-list");
  assert.equal(await page.getAttribute("#edit-tabs button[data-tab='edit:settings']", "aria-pressed"), "true");
  assert.equal(await page.getAttribute("#view-status", "aria-pressed"), "true"); // settings live on Status
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
  const page = await open(
    "window.__location = { parameter: { view: 'edit', tab: 'review' } };" +
      "window.__answers.getEditor.review = [{ key: 'ACME GROCER', kind: 'payee', lines: '2', total: '-60', first: '2001-02-01', " +
      "last: '2001-02-15', accounts: 'checking', example: 'ACME GROCER 12', guess: '', guessed_by: '', note: '' }];" +
      "window.__answers.getReviewLines = [{ id: 't1', date: '2001-02-01', payee: 'ACME GROCER 12', account: 'checking', amount: -20 }, " +
      "{ id: 't2', date: '2001-02-15', payee: 'ACME GROCER 12', account: 'checking', amount: -40 }];",
  );
  await page.waitForSelector("#edit:not([hidden]) .md-list");
  const split = async (choices) => {
    await page.click(".md-row:has-text('ACME GROCER')");
    await page.click(".md-detail button:has-text('Split into its lines')");
    await page.waitForSelector("table.split-lines");
    const picks = await page.$$("table.split-lines select");
    for (let i = 0; i < choices.length; i++) if (choices[i]) await picks[i].selectOption(choices[i]);
    await page.click(".md-detail button:has-text('Keep answers')");
  };
  await split(["food", ""]); // one line left on (choose): the question stays for it
  assert.equal(await page.$$eval(".md-row:has-text('ACME GROCER')", (r) => r.length), 1);
  await split(["food", "kids"]);
  assert.equal(await page.$$eval(".md-row:has-text('ACME GROCER')", (r) => r.length), 0);
  assert.deepEqual(await staged(page), ["ACME GROCER 12 2001-02-01: food", "ACME GROCER 12 2001-02-15: kids"]);
  await page.evaluate(() =>
    Array.from(document.querySelectorAll("#pending-list li")) // undo one: the question is back
      .filter((li) => li.textContent.includes("2001-02-15"))[0]
      .querySelector("button")
      .click(),
  );
  assert.equal(await page.$$eval(".md-row:has-text('ACME GROCER')", (r) => r.length), 1);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("recurring: a charge that comes back is offered to track, and one that stopped merges into the one that replaced it", async () => {
  const row = (key, status, last) => ({
    key,
    marked: "",
    status,
    cadence: "monthly",
    charges: "4",
    last,
    latest: "30",
    yearly: "360",
    category: "petrol",
    example: key,
    note: "",
    includes: "",
    merged: "",
  });
  const page = await open(
    "window.__location = { parameter: { view: 'budget', tab: 'recurring' } };" +
      "window.__answers.getEditor.subscriptions = " +
      JSON.stringify([row("BLUE PUMP FUEL", "stopped", "2001-01-03"), row("WILLOW ST", "active", "2001-06-03")]) +
      ";",
  );
  await page.waitForSelector("#budget:not([hidden]) .alert");
  assert.match(await page.textContent(".alert"), /WILLOW ST comes back monthly for about \$30.00 and isn't tracked/); // leak-guard: allow (made up)
  assert.match(await page.textContent(".rec-card.wide"), /Seem to have stopped.*BLUE PUMP FUEL.*last charged Jan 3, 2001/s);
  await page.click(".alert button:has-text('Track it')");
  await page.click(".rec-row:has-text('BLUE PUMP FUEL')");
  await page.selectOption(".drawer label:has-text('Merge into') select", "WILLOW ST");
  await page.click(".drawer button:has-text('Merge')");
  assert.deepEqual(await staged(page), ["WILLOW ST: track as recurring", "BLUE PUMP FUEL: part of WILLOW ST"]);
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [
    { kind: "subscription", key: "WILLOW ST", subscription: "yes" },
    { kind: "recurring_merge", key: "BLUE PUMP FUEL", same_as: "WILLOW ST" },
  ]);
  // saved, the page says so until a run applies it, instead of going back to how it was
  await page.waitForSelector(".alert:has-text('Saved, applies at the next run')");
  assert.match(await page.textContent(".alert"), /Saved, applies at the next run: WILLOW ST: track as recurring/);
  assert.equal(await page.locator(".alert button:has-text('Undo')").count(), 0); // saved is not undone here
  assert.equal(await page.locator(".alert button:has-text('Track it')").count(), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a change saved earlier and not yet applied reads as saved when the page opens, until a discard", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget', tab: 'recurring' } };" +
      "window.__answers.getEditor.subscriptions = [{ key: 'WILLOW ST', marked: '', status: 'active', cadence: 'monthly', " +
      "charges: '4', last: '2001-06-03', latest: '30', yearly: '360', category: 'petrol', example: 'WILLOW ST', note: '', " +
      "includes: '', merged: '' }];" +
      "window.__answers.getWaitingEdits = [{ id: 'f9', savedAt: '2001-06-04T00:00:00Z', " +
      "edits: [{ kind: 'subscription', key: 'WILLOW ST', subscription: 'yes' }], texts: ['WILLOW ST: track as recurring'] }];",
  );
  await page.waitForSelector(".alert:has-text('Saved, applies at the next run: WILLOW ST: track as recurring')");
  assert.deepEqual(await staged(page), []); // nothing waits to be saved: it is saved
  // the header counts it on every page, and its menu says what it will do and leads to the full list
  assert.equal(await page.textContent("#waiting-count summary"), "1 change waits for a run");
  await page.click("#waiting-count summary");
  assert.deepEqual(await page.$$eval("#waiting-count li", (lis) => lis.map((li) => li.textContent)), [
    "WILLOW ST: track as recurring",
  ]);
  await page.click("#waiting-count button:has-text('See saved changes')");
  assert.equal(await page.getAttribute("#view-status", "aria-pressed"), "true");
  assert.equal(await page.isVisible("#waiting-count .menu-panel"), false);
  await page.evaluate(() => {
    forgetSaved("f9");
    setView("budget");
  });
  await page.waitForSelector(".alert button:has-text('Track it')");
  assert.equal(await page.locator("#waiting-count summary").count(), 0); // discarded: no count
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("the budget compares any period with another, and a line's target can come from its recurring charges", async () => {
  // made-up spending: today, and the same day a year earlier; food budgeted at 60 this month
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var t = (function (d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); })(new Date()), y = (Number(t.slice(0, 4)) - 1) + t.slice(4);" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { food: 'living', media: 'fun' }, recurring: []," +
      "    targets: [{ month: t.slice(0, 7), category: 'food', priority: 'high', target: 60 }]," +
      "    days: [[t, 'food', 12], [y, 'food', 8], [t, 'media', 5]] } };" +
      "  window.__answers.getEditor.budgetLines = [{ line: 'food', effective: '2001-01-01', category: 'food', account: '', priority: 'high'," +
      "    rule: 'fixed', amount: '60', per: 'monthly', note: '', in_force: 'yes', in_words: '60.00 a month', this_month: '12'," +
      "    last_12_months: '0', scheduled: '', spends_from: 'food' }];" +
      "  window.__answers.getEditor.subscriptions = [{ key: 'PAGE TURNER BOOKS', marked: 'yes', status: 'active', cadence: 'monthly'," +
      "    charges: '9', last: t, latest: '10', yearly: '120', category: 'food', example: '', note: '', includes: '', merged: '' }];" +
      "})();",
  );
  await page.waitForSelector("table.bv");
  const food = await page.$$eval("table.bv tr.line:has-text('food') td", (tds) => tds.map((td) => td.textContent));
  // food is living's one category: its row alone, the group's name a tag on it
  assert.deepEqual([food[0], food[1], food[2], food[3], food[4]], ["FoodLiving", "60 a month", "Fixed", "$60", "$12"]); // leak-guard: allow (made up)
  // the period's head, as the A2 design has it: what went out, and four cards
  assert.match(await page.textContent(".a2-headline"), /^\$17 went out$/); // leak-guard: allow (made up)
  assert.match(await page.textContent(".a2-head"), /against the same weeks of /); // the other period
  assert.match(await page.textContent(".a2-card:has-text('Planned')"), /\$60/); // leak-guard: allow (made up)
  await page.click("table.bv th button.sort:has-text('Spent')"); // sorts by spent, largest first; again reverses
  assert.equal(await page.getAttribute("table.bv th:nth-child(5)", "aria-sort"), "descending");
  await page.click("table.bv th button.sort:has-text('Spent')");
  assert.equal(await page.getAttribute("table.bv th:nth-child(5)", "aria-sort"), "ascending");
  assert.equal(await page.locator(".a2-card").count(), 4);
  // living's one line item stands alone; fun's only one, media, has no budget line, so it folds under fun as unplanned
  assert.equal(await page.$$eval("table.bv tr.group", (rows) => rows.length), 1);
  assert.match(await page.textContent("table.bv tr.unplanned"), /Unplanned\s*media.*\$5.*Show 1/); // leak-guard: allow (made up)
  await page.click("table.bv tr.unplanned");
  await page.waitForSelector("table.bv tr.unplanned-item:has-text('media')");
  await page.click(".bv-controls button:has-text('Year')");
  const year = String(new Date().getFullYear());
  assert.equal(await page.inputValue(".bv-picker select"), year);
  assert.equal(await page.inputValue(".bv-compare select"), String(Number(year) - 1));
  await page.click(".bv-controls button:has-text('Quarter')");
  assert.match(await page.inputValue(".bv-picker select"), /^\d{4}-Q[1-4]$/);
  await page.click("table.bv tr.line:has-text('food')");
  await page.waitForSelector(".drawer svg.history");
  await page.click(".drawer .line-change > button:has-text('Change')"); // the form waits behind Change
  await page.selectOption(".drawer label:has-text('Target from') select", "recurring");
  await page.fill(".drawer label:has-text('Allowance') input", "20");
  // none picked: the tracked charges in its category count
  assert.match(await page.textContent(".drawer .recurring-sum"), /None picked: the line counts the tracked charges in food/);
  assert.match(await page.textContent(".drawer .recurring-sum"), /Monthly target\$30\.00/); // leak-guard: allow (made up)
  await page.check(".drawer .charge-pick label:has-text('PAGE TURNER BOOKS') input");
  assert.doesNotMatch(await page.textContent(".drawer .recurring-sum"), /None picked/);
  assert.match(await page.textContent(".drawer .recurring-sum"), /Monthly target\$30\.00/); // leak-guard: allow (made up)
  await page.click(".drawer button:has-text('Keep this change')");
  const kept = await staged(page);
  assert.match(kept[0], /^budget line food: recurring 20 monthly for food from /);
  assert.equal(kept[1], "PAGE TURNER BOOKS: pays budget line food"); // already tracked: no track edit
  await page.keyboard.press("Escape");
  assert.equal(await page.$$eval(".drawer", (d) => d.length), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a category with two budget lines shows each as its own row, to open and change by itself", async () => {
  const bl = (line, amount) => ({
    line,
    effective: "2001-01-01",
    category: "kids",
    account: "",
    priority: "high",
    rule: "fixed",
    amount: String(amount),
    per: "annually",
    note: "",
    in_force: "yes",
    in_words: amount + ".00 a year",
    this_month: "",
    last_12_months: "0",
    scheduled: "",
    spends_from: "kids",
  });
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7);" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { kids: 'family', gifts: 'family' }, recurring: [], days: []," +
      "    targets: [{ month: m, category: 'kids', priority: 'high', target: 50 }, { month: m, category: 'gifts', priority: 'low', target: 10 }]," +
      "    lineTargets: [{ month: m, line: 'camp alex', category: 'kids', target: 30 }, { month: m, line: 'camp sam', category: 'kids', target: 20 }] } };" +
      "  window.__answers.getEditor.budgetLines = " +
      JSON.stringify([bl("camp alex", 360), bl("camp sam", 240)]) +
      ";" +
      "})();",
  );
  await page.waitForSelector("table.bv tr.line:has-text('kids')");
  assert.equal(await page.locator("table.bv tr.subline").count(), 0); // a line item's breakdown is shut at first
  await page.click("table.bv tr.line:has-text('kids') button.chev");
  await page.waitForSelector("table.bv tr.subline");
  assert.equal(await page.locator(".drawer").count(), 0); // opening it does not open the drawer
  const row = await page.$$eval("table.bv tr.line:has-text('kids') td", (tds) => tds.map((td) => td.textContent));
  assert.deepEqual([row[0], row[1], row[2], row[3]], ["Kids", "2 budget lines", "Fixed", "$50"]); // leak-guard: allow (made up)
  const group = await page.$$eval("table.bv tr.group td", (tds) => tds.map((td) => td.textContent));
  assert.deepEqual([group[0], group[1], group[3]], ["Family", "2 line items", "$60"]); // leak-guard: allow (made up)
  const subs = await page.$$eval("table.bv tr.subline", (rows) =>
    rows.map((r) =>
      Array.from(r.querySelectorAll("td"))
        .map((td) => td.textContent)
        .slice(0, 4),
    ),
  );
  assert.deepEqual(subs, [
    ["Camp alex", "360 a year", "Fixed", "$30"], // leak-guard: allow (made up)
    ["Camp sam", "240 a year", "Fixed", "$20"], // leak-guard: allow (made up)
  ]);
  await page.click("table.bv tr.subline:has-text('camp sam')");
  await page.waitForSelector(".drawer h2:has-text('camp sam')");
  assert.equal(await page.locator(".drawer .line-change").count(), 1); // only this line's
  assert.equal(await page.locator(".drawer form.edit").count(), 0); // its form behind Change
  await page.click(".drawer .line-change > button:has-text('Change')"); // the form waits behind Change
  assert.match(await page.textContent(".drawer"), /Budget line: camp sam/);
  assert.doesNotMatch(await page.textContent(".drawer"), /Budget line: camp alex/);
  await page.fill(".drawer label:has-text('Amount') input", "300");
  await page.click(".drawer button:has-text('Keep this change')");
  assert.match((await staged(page))[0], /^budget line camp sam: fixed 300 annually for kids from /);
  await page.waitForSelector("table.bv tr.subline:has-text('camp sam') .tag.warn");
  assert.equal(await page.locator("table.bv tr.subline:has-text('camp alex') .tag.warn").count(), 0);
  assert.equal(await page.locator(".drawer form.edit").count(), 1); // kept: still open, for another change
  await page.click(".drawer .line-change button:has-text('Cancel')");
  assert.equal(await page.locator(".drawer form.edit").count(), 0);
  await page.click(".drawer .line-change > button:has-text('Change')");
  await page.keyboard.press("Escape"); // a drawer opened again leads with the line, not its form
  await page.click("table.bv tr.subline:has-text('camp sam')");
  await page.waitForSelector(".drawer .line-change > button:has-text('Change')");
  assert.equal(await page.locator(".drawer form.edit").count(), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("groups open and shut, a loan's parts roll up into one row, and neither click moves the page", async () => {
  const cats = ["interest", "principal", "pmi", "household"];
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01', groups = { food: 'living' }, targets = [];" +
      JSON.stringify(cats) +
      ".forEach(function (c) { groups[c] = 'home'; targets.push({ month: m, category: c, priority: 'high', target: 20 }); });" +
      "  for (var i = 0; i < 40; i++) { groups['c' + i] = 'g' + (i >> 1); targets.push({ month: m, category: 'c' + i, priority: 'low', target: 10 }); }" +
      "  var loans = { interest: 'acme_loan', principal: 'acme_loan', pmi: 'acme_loan' };" + // compute says which are a loan's
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: groups, loans: loans, recurring: [], days: [[d, 'interest', 15]], targets: targets } };" +
      "})();",
  );
  await page.waitForSelector("table.bv tr.loan");
  const loan = await page.$$eval("table.bv tr.loan td", (tds) => tds.map((td) => td.textContent));
  assert.deepEqual([loan[0], loan[3], loan[4]], ["Acme loan", "$60", "$15"]); // leak-guard: allow (made up)
  assert.equal(await page.locator("table.bv tr.part").count(), 0);
  assert.equal(await page.locator("table.bv tr.line:has-text('household')").count(), 1);
  await page.click("table.bv tr.loan");
  assert.deepEqual(await page.$$eval("table.bv tr.part td:first-child", (tds) => tds.map((td) => td.textContent)), [
    "Interest",
    "Pmi",
    "Principal",
  ]);
  assert.equal(await page.locator(".drawer").count(), 0);
  await page.click("table.bv tr.group:has-text('home')");
  assert.equal(await page.locator("table.bv tr.loan").count(), 0); // the group shut: none of its rows
  assert.equal(await page.$eval("table.bv tr.group:has-text('home')", (r) => r.getAttribute("aria-expanded")), "false");
  await page.click("table.bv tr.group:has-text('home')");
  await page.waitForSelector("table.bv tr.part"); // open again, as it was
  const last = "table.bv tr.group:has-text('g19')";
  await page.$eval(last, (r) => r.scrollIntoView({ block: "center" })); // clear of the sticky bar, so the click needs no scroll
  const before = await page.evaluate(() => window.scrollY);
  assert.ok(before > 0);
  await page.click(last);
  assert.equal(await page.evaluate(() => window.scrollY), before);
  assert.equal(
    await page.evaluate(() => document.activeElement && document.activeElement.textContent),
    await page.textContent(last),
  );
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("the Lines tab's three views: by group, by kind and a card per group, each opening the same drawer", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { food: 'living', hobby: 'living', media: 'fun' }, recurring: []," +
      "    days: [[d, 'food', 40], [d, 'hobby', 5], [d, 'media', 9]], funds: [{ month: m, line: 'hobby', category: 'hobby', balance: -15 }]," +
      "    targets: [{ month: m, category: 'food', priority: 'high', target: 60 }, { month: m, category: 'hobby', priority: 'low', target: 20 }] } };" +
      "  var bl = function (line, category, rule, amount) { return { line: line, effective: '2001-01-01', category: category, account: '', priority: 'high'," +
      "    rule: rule, amount: amount, per: 'monthly', note: '', in_force: 'yes', in_words: 'x', this_month: '', last_12_months: '0', scheduled: '', spends_from: category }; };" +
      "  window.__answers.getEditor.budgetLines = [bl('groceries', 'food', 'trailing', '60'), bl('hobby', 'hobby', 'sinking_fund', '20')];" +
      "})();",
  );
  await page.waitForSelector("table.bv");
  assert.equal(await page.getAttribute(".layout-switch button:has-text('By group')", "aria-pressed"), "true");
  await page.click(".layout-switch button:has-text('By kind')");
  await page.waitForSelector(".kind-view");
  const panels = await page.$$eval(".kind-view .item-panel h3", (hs) => hs.map((h) => h.textContent));
  assert.deepEqual(panels, ["Floating", "Annual", "Unplanned"]);
  assert.match(await page.textContent(".kind-panel-annual"), /Overdrawn \$15/); // leak-guard: allow (made up)
  assert.match(await page.textContent(".kind-panel-floating"), /Groceries\s*Living, from the last 4 months/);
  assert.match(await page.textContent(".a2-cards"), /Floating\$40 of \$60/); // leak-guard: allow (made up)
  await page.click(".layout-switch button:has-text('Cards')");
  await page.waitForSelector(".group-cards");
  const cards = await page.$$eval(".group-card h3", (hs) => hs.map((h) => h.textContent));
  assert.deepEqual(cards, ["Living", "Fun"]);
  assert.equal(await page.getAttribute(".group-card .item-row:has-text('Hobby') .kind-mark", "title"), "Annual");
  assert.match(await page.textContent(".group-card .item-row:has-text('Hobby')"), /Overdrawn \$15/); // leak-guard: allow (made up)
  await page.click(".group-card .item-row:has-text('Groceries')");
  await page.waitForSelector(".drawer h2:has-text('food')");
  await page.keyboard.press("Escape");
  await page.click(".layout-switch button:has-text('By group')");
  assert.match(await page.textContent("table.bv tr.line:has-text('Hobby') td.status"), /Overdrawn \$15/); // leak-guard: allow (made up)
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("the comparison is one button saying what it is, opening its choices, and closing like a menu", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { food: 'living' }, recurring: []," +
      "    days: [[d, 'food', 40]], targets: [{ month: m, category: 'food', priority: 'high', target: 60 }] } };" +
      "})();",
  );
  await page.waitForSelector(".bv-compare-menu summary");
  const said = () => page.textContent(".bv-compare-menu summary");
  const shown = () => page.isVisible(".bv-compare-menu .menu-panel");
  // this month runs on, so it is set beside the same weeks a year earlier, and says so
  assert.match(await said(), /^Compared with \w+ \d{4}, same weeks$/);
  assert.equal(await shown(), false); // out of the way until wanted
  await page.click(".bv-compare-menu summary");
  assert.equal(await shown(), true);
  await page.uncheck(".bv-compare-menu .check input");
  assert.match(await said(), /^Compared with \w+ \d{4}$/); // the page draws again, the menu stays open beside the choice
  assert.equal(await shown(), true);
  const before = await page.inputValue(".bv-compare select");
  const other = await page.$$eval(".bv-compare select option", (os) => os.map((o) => o.value));
  const pick = other.find((p) => p !== before);
  await page.selectOption(".bv-compare select", pick);
  assert.equal(await shown(), false); // chosen: it closes
  assert.equal(await page.inputValue(".bv-compare select"), pick);
  await page.click(".bv-compare-menu summary");
  await page.keyboard.press("Escape");
  assert.equal(await shown(), false);
  await page.click(".bv-compare-menu summary");
  await page.click(".a2-headline"); // anywhere else
  assert.equal(await shown(), false);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("on a phone the page leads with the answer: cards, the period's judgment under the headline", async () => {
  const page = await browser.newPage({ viewport: { width: 412, height: 860 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route("https://www.gstatic.com/charts/loader.js", (r) =>
    r.fulfill({
      contentType: "text/javascript",
      body:
        FAKE +
        "window.__location = { parameter: { view: 'budget' } };" +
        "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
        "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { kids: 'family', family: 'family' }, recurring: []," +
        "    days: [[d, 'kids', 1200], [d, 'family', 5]], targets: [{ month: m, category: 'kids', priority: 'high', target: 100 }," +
        "      { month: m, category: 'family', priority: 'high', target: 10 }] } };" +
        "  var bl = function (line, category, rule, amount, per) { return { line: line, effective: '2001-01-01', category: category," +
        "    account: '', priority: 'high', rule: rule, amount: amount, per: per, note: '', in_force: 'yes', in_words: 'x', this_month: ''," +
        "    last_12_months: '0', scheduled: '', spends_from: category }; };" +
        "  window.__answers.getEditor.budgetLines = [bl('kids', 'kids', 'fixed', '1200', 'annually'), bl('family', 'family', 'fixed', '10', 'monthly')];" +
        "})();",
    }),
  );
  await page.route("https://fonts.googleapis.com/**", (r) => r.fulfill({ contentType: "text/css", body: "" }));
  await page.route("http://views.test/", (r) => r.fulfill({ contentType: "text/html", body: HTML }));
  await page.goto("http://views.test/");
  await page.waitForSelector(".group-cards");
  assert.equal(await page.getAttribute(".layout-switch button:has-text('Cards')", "aria-pressed"), "true");
  assert.equal(await page.isVisible(".a2-sub"), true);
  assert.equal(await page.isVisible(".a2-card .a2-small"), false);
  // the year's payment landed this month: neither the line item nor its group reads as over
  assert.equal(await page.locator(".group-card .spent-bar i.over").count(), 0);
  assert.equal(await page.locator(".group-card .item-num.over").count(), 0);
  // a choice is kept: the table, once picked, stays
  await page.click(".layout-switch button:has-text('By group')");
  await page.waitForSelector("table.bv");
  assert.deepEqual(errors, []);
  await page.close();
});

test("an annual fund's balance shows its working: carried, set aside, spent, and a line moved out", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-02', y = m.slice(0, 4), k = Number(m.slice(5, 7));" +
      "  var funds = [{ month: (Number(y) - 1) + '-12', line: 'media', category: 'media', set_aside: 10, spent: 0, balance: 30 }];" +
      "  for (var i = 1; i <= k; i++) { var mm = y + '-' + String(i).padStart(2, '0');" +
      "    funds.push({ month: mm, line: 'media', category: 'media', set_aside: 10, spent: i === k ? 9 : 0, balance: 30 + 10 * i - (i === k ? 9 : 0) }); }" +
      "  window.__k = k;" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { media: 'fun' }, recurring: [], funds: funds," +
      "    days: [[d, 'media', 9]], targets: [{ month: m, category: 'media', priority: 'low', target: 10 }] } };" +
      "  window.__answers.getEditor.budgetLines = [{ line: 'media', effective: '2001-01-01', category: 'media', account: ''," +
      "    priority: 'low', rule: 'sinking_fund', amount: '10', per: 'monthly', note: '', in_force: 'yes', in_words: '120 a year, set aside'," +
      "    this_month: '', last_12_months: '0', scheduled: '', spends_from: 'media' }];" +
      "  window.__answers.getLines = [{ id: 't1', date: d, account: 'a1', payee: 'PAGE TURNER BOOKS', category: 'media', amount: -9," +
      "    split: '', items: [], key: 'PAGE TURNER BOOKS', note: '', one_off: false }];" +
      "})();",
  );
  await page.waitForSelector("table.bv tr.line:has-text('Media')");
  await page.click("table.bv tr.line:has-text('Media') td:nth-child(2)");
  await page.waitForSelector(".drawer .fund-working");
  const k = await page.evaluate(() => window.__k);
  const year = new Date().getFullYear();
  assert.match(await page.textContent(".drawer .fund-working p.support"), new RegExp(`^It carried \\$30\\.00 into ${year}\\.`)); // leak-guard: allow (made up)
  assert.equal(await page.locator(".drawer .fund-months tr[data-month]").count(), k); // this year's months so far
  const end = 30 + 10 * k - 9;
  assert.match(
    await page.textContent(".drawer .fund-working .working-sum"),
    new RegExp(`^\\$30\\.00 carried, plus \\$${10 * k}\\.00 set aside, less \\$9\\.00 spent: \\$${end}\\.00 at the end of `), // leak-guard: allow (made up)
  );
  // the month's line belongs elsewhere: moved out, the balance after the next run is said
  await page.click(".drawer .fund-months tr[data-month]:last-of-type button:has-text('Its lines')");
  await page.waitForSelector(".drawer .fund-months tr[data-txn='t1']");
  await page.click(".drawer .fund-months tr[data-txn='t1'] button:has-text('Move')");
  await page.waitForSelector(".drawer .fund-months tr.move-row select option[value='food']", { state: "attached" });
  await page.selectOption(".drawer .fund-months tr.move-row select", "food");
  await page.click(".drawer .fund-months tr.move-row button:has-text('Move it')");
  await page.waitForSelector(".drawer .fund-working .working-after");
  assert.equal(
    await page.textContent(".drawer .fund-working .working-after"),
    `After the next run, about $${end + 9}.00 (up $9.00), with 1 line moved out of it.`, // leak-guard: allow (made up)
  );
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a line set per year is judged on its year: a month a payment lands in isn't over", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { kids: 'family', family: 'family', hobby: 'fun', media: 'fun' }," +
      "    recurring: [], days: [[d, 'kids', 1200], [d, 'family', 150], [d, 'hobby', 50], [d, 'media', 9]]," +
      "    targets: [{ month: m, category: 'kids', priority: 'high', target: 100 }, { month: m, category: 'family', priority: 'high', target: 10 }," +
      "      { month: m, category: 'hobby', priority: 'low', target: 20 }, { month: m, category: 'media', priority: 'low', target: 10 }] } };" +
      "  var bl = function (line, category, rule, amount, per) { return { line: line, effective: '2001-01-01', category: category, account: ''," +
      "    priority: 'high', rule: rule, amount: amount, per: per, note: '', in_force: 'yes', in_words: 'x', this_month: ''," +
      "    last_12_months: '0', scheduled: '', spends_from: category }; };" +
      "  window.__answers.getEditor.budgetLines = [bl('kids', 'kids', 'fixed', '1200', 'annually'), bl('family', 'family', 'fixed', '120', 'annually')," +
      "    bl('hobby', 'hobby', 'fixed', '20', 'monthly'), bl('media', 'media', 'sinking_fund', '10', 'monthly')];" +
      "})();",
  );
  await page.waitForSelector("table.bv tr.line:has-text('Kids')");
  const row = (name) => `table.bv tr.line:has-text('${name}')`;
  // the year's payment, landed this month: the year's budget, spent, not 12 times the month's
  assert.equal(await page.textContent(`${row("Kids")} td.status .st`), "100% of the year's $1,200"); // leak-guard: allow (made up)
  assert.equal(await page.getAttribute(`${row("Kids")} td.status .st`, "class"), "st st-ok");
  assert.equal(await page.locator(`${row("Kids")} td.over`).count(), 0);
  assert.equal(await page.getAttribute(`${row("Kids")} .spent-bar`, "title"), "This year: $1,200 of $1,200"); // leak-guard: allow (made up)
  // past the year's budget is still over
  assert.equal(await page.textContent(`${row("Family")} td.status .st`), "1.3 times the year's $120"); // leak-guard: allow (made up)
  // the period's plan judges each line by its own measure: the yearly ones apart, one past its year by name
  assert.match(
    await page.textContent(".a2-card:has-text('Went out') .a2-small"),
    /^\$30 more than planned, besides \$1,359 on lines judged on their year \(Family is past its year\); /, // leak-guard: allow (made up)
  );
  assert.equal(await page.locator(`${row("Family")} td.over`).count(), 1);
  // a monthly line is judged on its month as before, and an annual fund's bar marks its even pace
  assert.equal(await page.textContent(`${row("Hobby")} td.status .st`), "Over by $30"); // leak-guard: allow (made up)
  assert.equal(await page.locator(`${row("Media")} .spent-bar.of-year b.pace`).count(), 1);
  assert.equal(await page.locator(`${row("Hobby")} .spent-bar.of-year`).count(), 0);
  // its drawer says why, and its months aren't marked over
  await page.click(`${row("Kids")} td:nth-child(2)`);
  await page.waitForSelector(".drawer p.year-pace");
  assert.match(
    await page.textContent(".drawer p.year-pace"),
    /^\$1,200 of the year's \$1,200 spent from January 1 to .+\. It is paid when it comes, so it is judged on its year/, // leak-guard: allow (made up)
  );
  assert.equal(await page.locator(".drawer svg.history rect.over").count(), 0);
  // the other views say the same
  await page.keyboard.press("Escape");
  await page.click(".layout-switch button:has-text('By kind')");
  await page.waitForSelector(".kind-view");
  assert.match(await page.textContent(".kind-panel-fixed"), /100% of the year's \$1,200/); // leak-guard: allow (made up)
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("giving: each recipient under its line item, marked deductible here, and the year's deductible gifts", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { gifts: 'giving', food: 'living' }, recurring: []," +
      "    days: [[d, 'gifts', 70], [d, 'food', 20]]," +
      "    targets: [{ month: m, category: 'gifts', priority: 'low', target: 100 }, { month: m, category: 'food', priority: 'high', target: 60 }]," +
      "    giving: [{ month: m, key: 'QUILL & INK', name: 'Quill & Ink', category: 'gifts', given: 40, gifts: 2, deductible: 'yes' }," +
      "      { month: m, key: 'WILLOW ST', name: 'WILLOW ST', category: 'gifts', given: 30, gifts: 1, deductible: '' }] } };" +
      "})();",
  );
  await page.waitForSelector("table.bv tr.line:has-text('gifts')");
  assert.match(await page.textContent("table.bv tr.line:has-text('gifts') td.status"), /^\$40 deductible in \d{4}$/); // leak-guard: allow (made up)
  await page.click("table.bv tr.line:has-text('gifts') button.chev");
  await page.waitForSelector("table.bv tr.recipient");
  const rows = await page.$$eval("table.bv tr.recipient", (rs) =>
    rs.map((r) => {
      const td = Array.from(r.querySelectorAll("td")).map((c) => c.textContent);
      return [td[0], td[1], td[4], r.querySelector("select").value];
    }),
  );
  assert.deepEqual(rows, [
    ["Quill & Ink", "2 gifts in " + new Date().getFullYear(), "$40", "yes"], // leak-guard: allow (made up)
    ["WILLOW ST", "1 gift in " + new Date().getFullYear(), "$30", ""], // leak-guard: allow (made up)
  ]);
  const total = await page.$$eval("table.bv tr.deductible td", (tds) => tds.map((td) => td.textContent));
  assert.match(total[0], /^Deductible in \d{4}$/);
  assert.match(total[1], /1 recipient not marked yet/);
  // the total heads the list: it comes before the recipients
  assert.deepEqual(
    await page.$$eval("table.bv tr.deductible, table.bv tr.recipient", (rs) => rs.map((r) => r.className.split(" ")[1])),
    ["deductible", "recipient", "recipient"],
  );
  assert.equal(total[4], "$40"); // leak-guard: allow (made up)
  await page.selectOption("table.bv tr.recipient:has-text('WILLOW ST') select", "no");
  assert.deepEqual(await staged(page), ["WILLOW ST: not deductible"]);
  await page.waitForSelector("table.bv tr.recipient:has-text('WILLOW ST') .tag.warn");
  assert.equal(await page.$eval("table.bv tr.recipient:has-text('WILLOW ST') select", (s) => s.value), "no");
  assert.equal(await page.locator(".drawer").count(), 0); // choosing does not open the line's drawer
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("giving: a recipient opens to its gifts, each with the fixes a line takes", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  window.__day = d;" +
      "  window.__answers.getBudgetBook = { tracked: [], book: { groups: { gifts: 'giving' }, recurring: []," +
      "    days: [[d, 'gifts', 70]], targets: [{ month: m, category: 'gifts', priority: 'low', target: 100 }]," +
      "    giving: [{ month: m, key: 'QUILL & INK', name: 'Quill & Ink', category: 'gifts', given: 40, gifts: 2, deductible: 'yes' }," +
      "      { month: m, key: 'WILLOW ST', name: 'WILLOW ST', category: 'gifts', given: 30, gifts: 1, deductible: '' }] }," +
      "    gifts: [{ txn_id: 'g1', month: m, key: 'QUILL & INK', category: 'gifts', from_key: '' }," +
      "      { txn_id: 'g2', month: m, key: 'QUILL & INK', category: 'gifts', from_key: 'ACME GROCER' }," +
      "      { txn_id: 'g3', month: m, key: 'WILLOW ST', category: 'gifts' }] };" +
      "  var t = function (id, payee, amount) { return { id: id, date: d, account: 'a1', payee: payee, category: 'gifts', amount: -amount," +
      "    split: '', items: [], key: payee, note: '', one_off: false }; };" +
      "  window.__answers.getLines = [t('g1', 'QUILL & INK', 25), t('g2', 'QUILL & INK', 15), t('g3', 'WILLOW ST', 30)," +
      "    t('x1', 'ACME GROCER', 5)];" +
      "})();",
  );
  await page.waitForSelector("table.bv tr.line:has-text('gifts')");
  await page.click("table.bv tr.line:has-text('gifts') button.chev");
  const quill = "table.bv tr.recipient:has-text('Quill & Ink') button.fold-name";
  await page.waitForSelector(quill);
  assert.equal(await page.getAttribute(quill, "aria-expanded"), "false");
  await page.click(quill);
  await page.waitForSelector("table.bv tr.gift-lines tr[data-txn]");
  assert.equal(await page.getAttribute(quill, "aria-expanded"), "true");
  // only this recipient's gifts: not another recipient's, and not a line in the category that isn't a gift
  const ids = () => page.$$eval("table.bv tr.gift-lines tr[data-txn]", (rs) => rs.map((r) => r.dataset.txn));
  assert.deepEqual(await ids(), ["g1", "g2"]);
  assert.equal((await page.locator("table.bv tr.line").count()) > 0, true); // opening a recipient opens no drawer
  assert.equal(await page.locator(".drawer").count(), 0);
  // the other recipient opens from the same read
  await page.click("table.bv tr.recipient:has-text('WILLOW ST') button.fold-name");
  await page.waitForFunction(() => document.querySelectorAll("table.bv tr.gift-lines").length === 2);
  assert.deepEqual(await ids(), ["g1", "g2", "g3"]);
  assert.equal(await page.evaluate(() => window.__calls.filter((c) => c.name === "getLines").length), 1);
  // a gift listed under the wrong line item moves, staged like any change
  await page.click("table.bv tr.gift-lines tr[data-txn='g1'] button:has-text('Move')");
  await page.waitForSelector("table.bv tr.move-row select option[value='food']", { state: "attached" });
  await page.selectOption("table.bv tr.move-row select", "food");
  await page.click("table.bv tr.move-row button:has-text('Move it')");
  const day = await page.evaluate(() => window.__day);
  assert.deepEqual(await staged(page), [`Gifts: the $25.00 line of ${day} moves to food`]); // leak-guard: allow (made up)
  await page.waitForSelector("table.bv tr.gift-lines tr[data-txn='g1']:has-text('Moves to Food after the next run')");
  await page.click("table.bv tr.gift-lines tr[data-txn='g1'] button:has-text('Undo move')");
  assert.deepEqual(await staged(page), []);
  // a recipient that is another under a second key: said once, counted as one after the next run
  const quillPanel = "table.bv tr.gift-lines >> nth=0";
  await page.selectOption(`${quillPanel} >> .same-recipient select`, "WILLOW ST");
  await page.click(`${quillPanel} >> .same-recipient button:has-text('Count as one')`);
  assert.deepEqual(await staged(page), ["Quill & Ink is the same recipient as WILLOW ST"]);
  await page.waitForSelector("table.bv tr.gift-lines .same-recipient .tag:has-text('Counts as WILLOW ST after the next run')");
  await page.click(`${quillPanel} >> .same-recipient button:has-text('Undo')`);
  assert.deepEqual(await staged(page), []);
  // a key already counted with it is named, with the way back out
  await page.click(`${quillPanel} >> .joined-key button:has-text('Separate')`);
  assert.deepEqual(await staged(page), ["ACME GROCER is a recipient of its own"]);
  await page.click(`${quillPanel} >> .joined-key button:has-text('Undo')`);
  assert.deepEqual(await staged(page), []);
  // and closing it puts the gifts away
  await page.click(quill);
  await page.waitForFunction(() => document.querySelectorAll("table.bv tr.gift-lines").length === 1);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("jobs: choosing a stretch of a pay line shows it, and a correction is staged like any change", async () => {
  const j = (effective, employer, salary, fte) => ({
    effective,
    person: "alex",
    employer,
    salary: String(salary),
    band: "",
    rating: "",
    fte: fte ? String(fte) : "",
    note: "",
    stubs: effective === "2020-01-15" ? "agrees" : "",
  });
  const page = await open(
    "window.__location = { parameter: { view: 'jobs' } };" +
      "window.__answers.getEditor.jobs = " +
      JSON.stringify([
        j("2016-04-01", "Acme Widgets", 80000),
        j("2020-01-15", "Quill & Ink", 90000),
        j("2022-05-01", "Quill & Ink", 90000, 0.6),
      ]) +
      "; window.__answers.getEditor.payCheck = [];",
  );
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.waitForSelector(".pay-chart svg");
  assert.equal(await page.isVisible("#edit-tabs button[data-tab=jobs]"), false);
  await page.focus(".seg-hit[aria-label^='Quill & Ink from 2020-01-15']");
  await page.keyboard.press("Enter");
  assert.match(await page.textContent(".job-detail"), /Quill & Ink, 2020-01-15 to 2022-05-01.*\$90,000.*agree with it/s); // leak-guard: allow (made up)
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
  assert.deepEqual(await page.$$eval("#edit-tabs button", (b) => b.map((x) => x.textContent)), [
    "This year",
    "History",
    "Form 8949",
    "Withholding",
  ]);
  await page.click("#view-budget");
  await page.click("#budget-tabs button[data-tab='edit:categories']");
  await page.waitForSelector("#edit:not([hidden])");
  assert.equal(await page.getAttribute("#view-budget", "aria-pressed"), "true");
  assert.equal(await page.getAttribute("#edit-tabs button[data-tab='edit:categories']", "aria-pressed"), "true");
  await page.click("#edit-tabs button[data-tab=lines]"); // back to the budget's own part
  await page.waitForSelector("#budget:not([hidden])");
  assert.deepEqual(await page.$$eval("nav.tabs button", (b) => b.map((x) => x.textContent)), [
    "Status",
    "Jobs",
    "Budget",
    "Cashflow",
    "Taxes",
    "Home",
    "Investments",
    "Retirement",
  ]);
  await openReview(page);
  assert.equal(await page.getAttribute("#view-flows", "aria-pressed"), "true");
  assert.deepEqual(await page.$$eval("#edit-tabs button", (b) => b.map((x) => x.textContent)), [
    "Sankey",
    "Table",
    "Review",
    "Payee rules",
    "Duplicates",
  ]);
  assert.equal(await page.getAttribute("#edit-tabs button[data-tab='edit:review']", "aria-pressed"), "true");
  await page.click("#edit-tabs button[data-tab=table]"); // back to the flow of money
  await page.waitForSelector("#flows:not([hidden])");
  assert.equal(await page.getAttribute("#flows-tabs button[data-tab=table]", "aria-pressed"), "true");
  assert.deepEqual(page.errors, []);
  await page.close();
});

const RETIREMENT = {
  chosenYear: 2031,
  chosenStrategy: "bracket/to22/constant",
  notes: ["2031 (51/49) with the plan", "no Social Security statement for sam"],
  years: [2030, 2031, 2032].map((y, i) => ({
    year: y,
    ages: 50 + i + "/" + (48 + i),
    chosen: y === 2031,
    monteCarlo: 0.85 + i * 0.04,
    historical: 0.8 + i * 0.05,
    fixed: [
      { scenario: "5%", success: false, shortFrom: 2060, end: 0 },
      { scenario: "9%", success: true, shortFrom: null, end: 90 },
    ],
    atRetirement: 40 + i * 5,
    firstSpending: 4,
    withdrawalRate: 0.05,
    medianEnd: 60,
    p10End: 1,
  })),
  strategies: [
    {
      key: "bracket/to22/constant",
      label: "Pre-tax to the 12% bracket, then taxable; convert to the top of 22%; constant real spending",
      order: "bracket",
      convertTo: 0.22,
      rule: "constant",
      sepp: false,
      claims: "",
      chosen: true,
      historical: 0.9,
      monteCarlo: 0.89,
      afterTax: 70,
      afterTaxP10: 4,
      tax: 8,
      irmaa: 1,
      penalty: 0,
      converted: 30,
      spent: 90,
      lowestSpending: 1,
    },
    {
      key: "bracket/to22/vpw",
      label: "Variable percentage withdrawal",
      order: "bracket",
      convertTo: 0.22,
      rule: "vpw",
      sepp: false,
      claims: "",
      chosen: false,
      historical: 1,
      monteCarlo: 1,
      afterTax: 20,
      afterTaxP10: 0,
      tax: 5,
      irmaa: 0,
      penalty: 0,
      converted: 10,
      spent: 95,
      lowestSpending: 0.4,
    },
    {
      key: "bracket/to22/constant/alex70/sam67",
      label: "Claiming later",
      order: "bracket",
      convertTo: 0.22,
      rule: "constant",
      sepp: false,
      claims: "alex 70, sam 67",
      chosen: false,
      historical: 0.92,
      monteCarlo: 0.9,
      afterTax: 72,
      afterTaxP10: 4,
      tax: 8,
      irmaa: 1,
      penalty: 0,
      converted: 30,
      spent: 90,
      lowestSpending: 1,
    },
  ],
  plans: {
    "bracket/to22/constant": [2030, 2031, 2032].map((y, i) => ({
      year: y,
      ages: 50 + i + "/" + (48 + i),
      phase: y < 2031 ? "working" : "retired",
      contributions: 7,
      spending: 4,
      health: 1,
      socialSecurity: 0,
      rmd: 0,
      fromTaxable: 2,
      fromPreTax: 3,
      fromRoth: 0,
      fromHsa: 0,
      converted: 5,
      ordinaryIncome: 8,
      capitalGains: 1,
      federalTax: 1,
      stateTax: 0.5,
      irmaa: 0,
      penalty: 0,
      short: y === 2032 ? 3 : 0,
      taxable: 10,
      preTax: 20,
      roth: 5,
      hsa: 1,
      total: 36 - i,
      p10: 20,
      p50: 36,
      p90: 60,
    })),
  },
  benefits: [
    {
      person: "alex",
      claimAge: 67,
      claimYear: 2047,
      statement: 27,
      computed: 27,
      plan: 24,
      planYearly: 288,
      aime: 50,
      pia: 24,
      note: "",
    },
    {
      person: "sam",
      claimAge: 67,
      claimYear: 2049,
      statement: null,
      computed: 0,
      plan: 0,
      planYearly: 144,
      aime: 0,
      pia: 0,
      note: "no statement: set social_security_monthly:sam",
    },
  ],
  earnings: [
    { person: "alex", year: 2001, earnings: 10, indexed: 20, counted: true, projected: false },
    { person: "alex", year: 2027, earnings: 10, indexed: 10, counted: true, projected: true },
  ],
  assumptions: [
    { section: "today", key: "taxable", label: "Taxable accounts", value: "90", source: "investment values today", setting: "" },
    {
      section: "changes",
      key: "kids",
      label: "kids (a group)",
      value: "0 from 2032",
      source: "now 20 a year",
      setting: "retirement_spending:kids",
    },
  ],
  budget: [
    {
      line: "home",
      group: "home",
      kind: "group",
      now: 30,
      firstYear: 30,
      atRetirement: 30,
      retireYear: 2031,
      setting: "",
      isSet: false,
      lumpy: null,
      note: "",
    },
    {
      line: "household",
      group: "home",
      kind: "category",
      now: 10,
      firstYear: 10,
      atRetirement: 10,
      retireYear: 2031,
      setting: "",
      isSet: false,
      lumpy: null,
      note: "",
    },
    {
      line: "hobby",
      group: "home",
      kind: "category",
      now: 20,
      firstYear: 20,
      atRetirement: 20,
      retireYear: 2031,
      setting: "",
      isSet: false,
      lumpy: 0.9,
      note: "",
    },
    {
      line: "kids",
      group: "kids",
      kind: "group",
      now: 20,
      firstYear: 20,
      atRetirement: 0,
      retireYear: 2031,
      setting: "0 from 2031",
      isSet: true,
      lumpy: null,
      note: "",
    },
    {
      line: "gifts",
      group: "",
      kind: "added",
      now: 0,
      firstYear: 0,
      atRetirement: 5,
      retireYear: 2031,
      setting: "5 from 2031",
      isSet: true,
      lumpy: null,
      note: "",
    },
  ],
};

test("retirement: the plan, each year, the strategies, Social Security and the assumptions, each in its own part", async () => {
  const page = await open(`window.__answers.getRetirement = ${JSON.stringify(RETIREMENT)};
                           window.__location = { parameter: { view: 'retirement' } };`);
  await page.waitForSelector("#retirement-body h2");
  assert.equal(await page.getAttribute("#view-retirement", "aria-pressed"), "true");
  assert.equal(await page.textContent("#retirement-body h2"), "Retire in 2031, at 51 and 49");
  assert.deepEqual(await page.$$eval("#retirement-body .subtabs button", (b) => b.map((x) => x.textContent)), [
    "Plan",
    "Budget",
    "Year by year",
    "Strategies",
    "Social Security",
    "Assumptions",
  ]);
  assert.equal(await page.textContent("#retirement-body tr.picked td"), "2031");
  assert.match(await page.textContent("#retirement-body table"), /short at 80\/78/); // at 5% the money runs out in 2060
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
  assert.deepEqual(
    figures.map((f) => f.replace("\u0024", "")),
    ["50", "50", "35"],
  ); // groups now; groups and added lines in 2030 and 2031
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
  assert.deepEqual(await staged(page), [
    "retirement budget: hobby left out (0)",
    "retirement budget: kids = 0 from 2031; 4 from 2040",
    "retirement budget: travel_fund = 6",
  ]);
  assert.match(await page.textContent("#retirement-body tr:has-text('hobby')"), /edited/);
  await page.click("#pending-save");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(
    sent.map((e) => [e.key, e.value, e.was]),
    [
      ["retirement_spending:hobby", "0", null],
      ["retirement_spending:kids", "0 from 2031; 4 from 2040", { value: "0 from 2031" }],
      ["retirement_spending:travel_fund", "6", null],
    ],
  );
  assert.deepEqual(page.errors, []);
  await page.close();
});

const FORM_8949 = {
  years: [
    {
      year: "2002",
      filedOnFile: false,
      disallowed: 0,
      parts: [
        {
          term: "long_term",
          proceeds: 50,
          basis: 60,
          adjustment: 0,
          gain: -10,
          filed: null,
          rows: [
            {
              saleDate: "2002-05-01",
              acquired: "2001-07-01",
              holdingFrom: "2001-05-01",
              account: "alex_brokerage",
              symbol: "ACMEX",
              shares: 1,
              proceeds: 50,
              basis: 60,
              code: "",
              adjustment: 0,
              gain: -10,
              washCarried: 10,
              reportedBasis: null,
              note: "",
            },
          ],
        },
      ],
    },
    {
      year: "2001",
      filedOnFile: true,
      disallowed: 10,
      parts: [
        {
          term: "short_term",
          proceeds: 40,
          basis: 50,
          adjustment: 10,
          gain: 0,
          filed: 5,
          rows: [
            {
              saleDate: "2001-06-01",
              acquired: "2001-02-01",
              holdingFrom: "2001-02-01",
              account: "alex_brokerage",
              symbol: "ACMEX",
              shares: 2,
              proceeds: 40,
              basis: 50,
              code: "W",
              adjustment: 10,
              gain: 0,
              washCarried: 0,
              reportedBasis: 45,
              note: "",
            },
          ],
        },
      ],
    },
  ],
};

test("taxes, Form 8949: each year's sales by part, a wash sale's code and adjustment, and the filed line beside the totals", async () => {
  const page = await open(`window.__answers.getForm8949 = ${JSON.stringify(FORM_8949)};
                           window.__answers.getTaxes = { history: [], filed: {}, years: [], totals: [], plan: [], accounts: [], notes: [] };
                           window.__location = { parameter: { view: 'taxes', tab: '8949' } };`);
  await page.waitForSelector("#taxes-body h2");
  assert.equal(await page.textContent("#taxes-body h2"), "Form 8949 for 2002");
  assert.match(await page.textContent(".f8949-part[data-term=long_term]"), /held from 2001-05-01/);
  assert.match(await page.textContent(".f8949-filed"), /not filed yet/);
  await page.click("#taxes-body .segmented button:has-text('2001')");
  const part = ".f8949-part[data-term=short_term]";
  assert.equal(await page.textContent(`${part} h3`), "Part I, short-term");
  const row = await page.$$eval(`${part} tr`, (rows) => rows[1].textContent);
  assert.match(row, /W/);
  assert.match(row, /1099-B/); // the plan reported another basis for these shares
  assert.match(await page.textContent(`${part} .f8949-filed`), /line 7 as filed.*more than these sales/);
  assert.deepEqual(await page.$$eval("#taxes-body .subtabs button", (b) => b.map((x) => x.textContent)), [
    "This year",
    "History",
    "Form 8949",
    "Withholding",
  ]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

const LINE = (key, group, label, owner, values) => ({
  key,
  group,
  label,
  owner,
  points: values.map((v, i) => [`20${String(10 + i).padStart(2, "0")}-12-31`, v]),
});
const INVESTMENTS = {
  asOf: "2025-12-31",
  total: 90,
  buckets: [{ key: "taxable", label: "Taxable", value: 90 }],
  funds: [],
  trades: [],
  allocation: [],
  accounts: [{ account: "a_brokerage", owner: "alex", wrapper: "brokerage", bucket: "taxable", value: 90, carried: false }],
  owners: [{ owner: "alex", parts: { taxable: 90 }, total: 90 }],
  history: [],
  notes: [],
  openLots: [],
  lotSales: [],
  lotYears: [],
  lotNotes: [],
  lines: [
    LINE("net_worth", "total", "Net worth", "", [10, 20, 30]),
    LINE("invested", "total", "Investments", "", [10, 20, 30]),
    LINE("owner:alex", "owner", "alex", "alex", [10, 20, 30]),
    LINE("bucket:taxable", "bucket", "Taxable", "", [10, 20, 30]),
  ].concat(Array.from({ length: 9 }, (_, n) => LINE(`account:acct_${n}`, "account", `acct_${n}`, "alex", [n, n + 1, n + 2]))),
};

test("investments: over time draws any lines chosen, each keeping its color, up to eight", async () => {
  const page = await open(`window.__answers.getInvestments = ${JSON.stringify(INVESTMENTS)};
                           window.__location = { parameter: { view: 'investments', tab: 'history' } };`);
  await page.waitForSelector("#investments-body .line-picks");
  assert.equal(await page.textContent("#investments-body h2"), "Over time");
  assert.deepEqual(await page.$$eval("#investments-body .subtabs button", (b) => b.map((x) => x.textContent)), [
    "Over time",
    "Overview",
    "Allocation",
    "Accounts",
    "Lots",
  ]);
  const pressed = () => page.$$eval("#investments-body .line-pick[aria-pressed='true']", (b) => b.map((x) => x.textContent));
  assert.deepEqual(await pressed(), ["Net worth", "Investments"]); // no home equity line to start with
  const color = (name) => page.$eval(`#investments-body .line-pick:has-text('${name}') .swatch`, (s) => s.style.background);
  await page.setViewportSize({ width: 1280, height: 400 });
  await page.evaluate(() => window.scrollTo(0, 300));
  await page.click("#investments-body .line-pick:has-text('acct_3')");
  assert.equal(await page.evaluate(() => window.scrollY), 300); // choosing a line keeps the page where it was
  assert.equal(await color("acct_3"), "var(--series-3)");
  await page.click("#investments-body .line-pick:has-text('Net worth')");
  assert.equal(await color("acct_3"), "var(--series-3)"); // taking one off doesn't repaint the rest
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

test("investments: accounts name each account's kind in words", async () => {
  const page = await open(`window.__answers.getInvestments = ${JSON.stringify({
    ...INVESTMENTS,
    accounts: [{ account: "a_plan", owner: "alex", wrapper: "workplace_plan", bucket: "401k_pretax", value: 90, carried: false }],
  })};
                           window.__location = { parameter: { view: 'investments', tab: 'accounts' } };`);
  await page.waitForSelector("#investments-body table.w-table");
  const kinds = await page.$$eval("#investments-body table.w-table tr:has(td) td:nth-child(3)", (c) =>
    c.map((x) => x.textContent),
  );
  assert.equal(kinds[0], "401(k) pre-tax"); // the Form 8949 parts once took this table's names
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("recurring: a charge says the budget line it pays, and paying one makes that line follow the charge", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget', tab: 'recurring' } };" +
      "window.__answers.getEditor.subscriptions = [{ key: 'WILLOW ST', marked: '', status: 'active', cadence: 'monthly'," +
      "  charges: '6', last: '2001-06-03', latest: '30', yearly: '360', category: 'family', example: 'WILLOW ST', note: ''," +
      "  includes: '', merged: '', name: '', line: '' }];" +
      "window.__answers.getEditor.budgetLines = [{ line: 'lessons', effective: '2001-01-01', category: 'family', account: ''," +
      "  priority: 'high', rule: 'trailing', amount: '25', per: 'monthly', note: '', in_force: 'yes', in_words: '', this_month: '25'," +
      "  last_12_months: '0', scheduled: '', spends_from: 'family' }];",
  );
  await page.waitForSelector("#budget:not([hidden]) .alert");
  await page.click(".alert button:has-text('Pays a budget line')"); // not tracked yet: offered from its alert
  await page.selectOption(".drawer label:has-text('Pays budget line') select", "lessons");
  assert.match(await page.textContent(".drawer"), /comes from its trailing rule now; keeping this also makes it Recurring/);
  await page.click(".drawer button:has-text('Keep')");
  const kept = await staged(page);
  assert.equal(kept[0], "WILLOW ST: pays budget line lessons");
  assert.equal(kept[1], "WILLOW ST: track as recurring"); // paying a line tracks the charge
  assert.match(kept[2], /^budget line lessons: recurring for family from /);
  assert.match(await page.textContent(".alert:has-text('WILLOW ST')"), /pays lessons.*Waiting to be saved/s);
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent.slice(0, 2), [
    { kind: "recurring_line", key: "WILLOW ST", line: "lessons" },
    { kind: "subscription", key: "WILLOW ST", subscription: "yes" },
  ]);
  assert.equal(sent[2].rule, "recurring");
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("recurring: a charge lists its lines, and one that isn't part of it is left out from its drawer", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget', tab: 'recurring' } };" +
      "var sub = { key: 'PAGE TURNER BOOKS', marked: 'yes', status: 'price changed', cadence: 'monthly', charges: '3'," +
      "  last: '2001-06-03', latest: '15', previous: '4', yearly: '180', category: 'media', example: 'PAGE TURNER BOOKS'," +
      "  note: '', includes: '', merged: '', name: 'Books', line: '' };" +
      "window.__answers.getEditor.subscriptions = [sub];" +
      "window.__answers.getBudgetBook.tracked = [sub];" +
      "var c = function (id, date, spend, counts, why) {" +
      "  return { key: 'PAGE TURNER BOOKS', txn_id: id, date: date, spend: spend, payee: 'PAGE TURNER BOOKS', counts: counts, why: why }; };" +
      "window.__answers.getBudgetBook.charges = [c('t4', '2001-06-03', 15, true, 'sets the latest price')," +
      "  c('t3', '2001-05-20', 4, true, ''), c('t2', '2001-05-03', 15, true, ''), c('t1', '2001-04-03', 9, false, 'marked one-off')," +
      "  { key: 'WILLOW ST', txn_id: 't0', date: '2001-04-01', spend: 5, payee: 'WILLOW ST', counts: true, why: '' }];",
  );
  await page.waitForSelector("#budget:not([hidden]) .alert.changed");
  await page.click(".alert.changed button:has-text('See its charges')"); // the price alert leads to the lines behind it
  await page.waitForSelector(".drawer table.charge-lines");
  const rows = () =>
    page.$$eval(".drawer table.charge-lines tr", (rs) =>
      rs.map((r) => [
        r.dataset.txn,
        r.className,
        r.querySelector("td.about").textContent,
        r.querySelector("button")?.textContent || "",
      ]),
    );
  assert.deepEqual(await rows(), [
    ["t4", "", "Sets the latest price", "Not part of it"],
    ["t3", "", "", "Not part of it"],
    ["t2", "", "", "Not part of it"],
    ["t1", "left-out", "Marked one-off", "Count it"],
  ]); // only this charge's lines, newest first
  await page.click(".drawer tr[data-txn='t3'] button");
  assert.deepEqual(await staged(page), ["Books: the $4.00 line of 2001-05-20 is not part of it"]); // leak-guard: allow (made up)
  assert.deepEqual((await rows())[1], ["t3", "left-out", "Left out after the next runchanged", "Count it"]);
  // the price the next run will find, by the engine's rule (priceChanged): the one before t4 is now t2, the same
  const after = () => page.$$eval(".drawer .charge-after", (ps) => ps.map((p) => p.textContent));
  assert.deepEqual(await after(), [
    "After the next run: $15.00 a charge, from the line of Jun 3, 2001, about $180 a year. " + // leak-guard: allow (made up)
      "The one before is the same, so the price change clears. 1 change to its lines in hand.",
  ]);
  await page.click(".drawer tr[data-txn='t3'] button"); // changed my mind: the staged change goes
  assert.deepEqual(await staged(page), []);
  assert.deepEqual(await after(), []); // nothing in hand, nothing to preview
  await page.click(".drawer tr[data-txn='t1'] button"); // and a line marked before counts again
  assert.deepEqual(await staged(page), ["Books: the $9.00 line of 2001-04-03 counts again"]); // leak-guard: allow (made up)
  assert.deepEqual((await rows())[3], ["t1", "", "Counts again after the next runchanged", "Not part of it"]); // and can go back
  assert.deepEqual(await after(), [
    "After the next run: $15.00 a charge, from the line of Jun 3, 2001. " + // leak-guard: allow (made up)
      // the line counted again breaks its month-apart rhythm: by the engine's rule it no longer has a cadence
      "It comes with no steady cadence (it said every month). " +
      "It still differs from the one before ($4.00), so it still says price changed. 1 change to its lines in hand.", // leak-guard: allow (made up)
  ]);
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [{ kind: "line", txn_id: "t1", field: "one_off", value: "FALSE" }]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a line whose charge changed price says Check, and the check opens the charge's lines", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  var sub = { key: 'PAGE TURNER BOOKS', marked: 'yes', status: 'price changed', cadence: 'monthly', charges: '3'," +
      "    last: d, latest: '24', previous: '4', yearly: '288', category: 'media', example: 'PAGE TURNER BOOKS'," +
      "    note: '', includes: '', merged: '', name: 'Books', line: '' };" +
      "  window.__answers.getEditor.subscriptions = [sub];" +
      "  window.__answers.getBudgetBook = { tracked: [sub], charges: [{ key: 'PAGE TURNER BOOKS', txn_id: 't1', date: d, spend: 24," +
      "    payee: 'PAGE TURNER BOOKS', counts: true, why: 'sets the latest price' }]," +
      "    book: { groups: { media: 'fun', food: 'living' }, recurring: [], days: [[d, 'media', 24], [d, 'food', 20]]," +
      "      targets: [{ month: m, category: 'media', priority: 'low', target: 30 }, { month: m, category: 'food', priority: 'high', target: 60 }] } };" +
      "})();",
  );
  await page.waitForSelector("table.bv tr.line:has-text('Media')");
  const check = "table.bv tr.line:has-text('Media') td.status button.st-check";
  assert.equal(await page.textContent(check), "Books $4.00 → $24.00"); // leak-guard: allow (made up)
  assert.equal(await page.getAttribute(check, "aria-label"), "Check: Books $4.00 → $24.00"); // leak-guard: allow (made up)
  assert.equal(await page.locator("table.bv tr.line:has-text('Food') td.status .st-check").count(), 0);
  await page.click(check);
  await page.waitForSelector(".drawer table.charge-lines");
  assert.match(await page.textContent(".drawer h2"), /Books/); // the charge's drawer, not the line's
  await page.keyboard.press("Escape");
  // the line's own drawer says the same first, and leads to the same lines
  await page.click("table.bv tr.line:has-text('Media') td:nth-child(2)");
  await page.waitForSelector(".drawer .alert.check");
  assert.match(await page.textContent(".drawer .alert.check"), /Books went from \$4\.00 → \$24\.00 at its latest charge/); // leak-guard: allow (made up)
  await page.click(".drawer .alert.check button:has-text('See its charges')");
  await page.waitForSelector(".drawer table.charge-lines");
  // and the other views show it too
  await page.keyboard.press("Escape");
  await page.click(".layout-switch button:has-text('Cards')");
  await page.waitForSelector(".group-card button.st-check");
  await page.click(".layout-switch button:has-text('By kind')");
  await page.waitForSelector(".kind-view button.st-check");
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("ending a budget line asks once in place, and a line naming no category says so", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  window.__answers.getBudgetBook = { tracked: [], charges: [], book: { groups: { hobby: 'fun' }, recurring: []," +
      "    days: [[d, 'hobby', 20]], targets: [{ month: m, category: 'hobby', priority: 'low', target: 40 }] } };" +
      "  window.__answers.getEditor.categories = [{ category: 'food', group: 'living', priority: 'high' }];" +
      "  window.__answers.getEditor.budgetLines = [{ line: 'hobby', effective: '2001-01-01', category: 'hobby', account: ''," +
      "    priority: 'low', rule: 'fixed', amount: '40', per: 'monthly', note: '', in_force: 'yes', in_words: '40.00 a month'," +
      "    this_month: '40', last_12_months: '0', scheduled: '', spends_from: 'hobby' }];" +
      "})();",
  );
  await page.click("table.bv tr.line:has-text('Hobby') td:nth-child(2)");
  await page.waitForSelector(".drawer form");
  assert.match(await page.textContent(".drawer .field-note"), /hobby isn't a category: pick one, or add it under Categories/);
  await page.fill(".drawer label:has-text('From') input", "2001-06-01");
  await page.click(".drawer button:has-text('End line')");
  assert.deepEqual(await staged(page), []); // the first click only asks
  assert.equal(await page.textContent(".drawer button.danger.asking"), "End it on Jun 1, 2001?");
  await page.click(".drawer button.danger.asking");
  assert.deepEqual(await staged(page), ["budget line hobby: ends 2001-06-01"]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("with no flows yet, coming back to Cashflow says so again and asks for no period", async () => {
  const page = await open("");
  await page.click("#view-flows");
  await page.waitForSelector("#chart:has-text('No flows yet')");
  await page.click("#view-budget");
  await page.click("#view-flows");
  await page.waitForSelector("#chart:has-text('No flows yet')");
  assert.equal(await page.evaluate(() => window.__calls.filter((c) => c.name === "getFlows").length), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a check compute published: its words on the row, its evidence a click away, and Looks right settles it here", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  window.__answers.getBudgetBook = { tracked: [], charges: [], checks: [{ check: 'target_high|hobby|t9', kind: 'target_high'," +
      "    about: 'hobby', category: 'hobby', label: 'Budget $55.00, usually $20.00'," + // leak-guard: allow (made up)
      "    detail: 'Its budget this month is well above a usual month. If that was a one-off, mark it so.', open: 'txn:t9' }]," +
      "    book: { groups: { hobby: 'fun', food: 'living' }, recurring: [], days: [[d, 'hobby', 20], [d, 'food', 20]]," +
      "      targets: [{ month: m, category: 'hobby', priority: 'low', target: 55 }, { month: m, category: 'food', priority: 'high', target: 60 }] } };" +
      "  window.__answers.getTransaction = null;" +
      "})();",
  );
  const check = "table.bv tr.line:has-text('Hobby') td.status button.st-check";
  await page.waitForSelector(check);
  assert.equal(await page.textContent(check), "Budget $55.00, usually $20.00"); // leak-guard: allow (made up)
  await page.click(check); // opens the line it rests on
  await page.waitForFunction(() => window.__calls.some((c) => c.name === "getTransaction" && c.args[0] === "t9"));
  await page.click("#view-budget");
  await page.click("table.bv tr.line:has-text('Hobby') td:nth-child(2)");
  await page.waitForSelector(".drawer .alert.check");
  assert.match(await page.textContent(".drawer .alert.check"), /well above a usual month/);
  await page.click(".drawer .alert.check button:has-text('Looks right')");
  assert.deepEqual(await staged(page), ["Hobby: Budget $55.00, usually $20.00, looks right"]); // leak-guard: allow (made up)
  await page.waitForSelector(".drawer .alert.check.settled");
  assert.equal(await page.locator(check).count(), 0); // the row no longer asks
  await page.click(".drawer .alert.check button:has-text('Check it again')"); // not saved yet: undone
  assert.deepEqual(await staged(page), []);
  await page.waitForSelector(check);
  await page.click(".drawer .alert.check button:has-text('Looks right')");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [{ kind: "looks_right", check: "target_high|hobby|t9", looks_right: "yes" }]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a check on the Status page opens its line item's drawer on the Budget page", async () => {
  const page = await open(
    "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  var c = { check: 'target_high|hobby|t9', kind: 'target_high', about: 'hobby', category: 'hobby'," +
      "    label: 'Budget $55.00, usually $20.00', detail: 'Its budget this month is well above a usual month.', open: 'txn:t9' };" + // leak-guard: allow (made up)
      "  window.__answers.getStatus.attention = [{ key: 'budget_checks', label: 'thing to check on the Budget page', count: 1," +
      "    note: '', detail: [], flags: [], action: 'view:budget', items: [{ title: c.label, detail: c.detail, link: 'budget:hobby' }] }];" +
      "  window.__answers.getBudgetBook = { tracked: [], charges: [], checks: [c], book: { groups: { hobby: 'fun' }, recurring: []," +
      "    days: [[d, 'hobby', 20]], targets: [{ month: m, category: 'hobby', priority: 'low', target: 55 }] } };" +
      "})();",
  );
  await page.waitForSelector(".attention button.thing-link");
  await page.click(".attention button.thing-link");
  await page.waitForSelector(".drawer .alert.check");
  assert.match(await page.textContent(".drawer h2"), /Hobby/);
  assert.equal(await page.getAttribute("#view-budget", "aria-pressed"), "true");
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a charge that changed price, on the Status page, opens that charge's drawer and stays open on a redraw", async () => {
  const page = await open(
    "var sub = { key: 'PAGE TURNER BOOKS', marked: 'yes', status: 'price changed', cadence: 'monthly', charges: '3'," +
      "  last: '2001-06-03', latest: '15', previous: '4', yearly: '180', category: 'media', example: 'PAGE TURNER BOOKS'," +
      "  note: '', includes: '', merged: '', name: 'Books', line: '' };" +
      "window.__answers.getEditor.subscriptions = [sub];" +
      "window.__answers.getBudgetBook.tracked = [sub];" +
      "window.__answers.getBudgetBook.charges = [{ key: 'PAGE TURNER BOOKS', txn_id: 't4', date: '2001-06-03', spend: 15," +
      "  payee: 'PAGE TURNER BOOKS', counts: true, why: 'sets the latest price' }, { key: 'PAGE TURNER BOOKS', txn_id: 't3'," +
      "  date: '2001-05-03', spend: 4, payee: 'PAGE TURNER BOOKS', counts: true, why: '' }];" +
      "window.__answers.getStatus.attention = [{ key: 'subscription_price_changes', label: 'recurring charge changed price', count: 1," +
      "  note: '', detail: [], flags: [], action: 'view:recurring', items: [{ title: 'PAGE TURNER BOOKS'," +
      "  detail: 'Now 15.00, was 4.00 (+275%) from 2001-06-03', link: 'charge:PAGE TURNER BOOKS' }] }];", // leak-guard: allow (made up)
  );
  await page.waitForSelector(".attention button.thing-link");
  await page.click(".attention button.thing-link");
  await page.waitForSelector(".drawer table.charge-lines");
  assert.equal(await page.textContent(".drawer h2"), "Books");
  assert.equal(await page.getAttribute("#budget-tabs button[data-tab='recurring']", "aria-pressed"), "true");
  // marking the line that set the price redraws the page under it; the charge stays open
  await page.click(".drawer tr[data-txn='t4'] button");
  await page.click(".drawer tr[data-txn='t4'] button"); // and back
  await page.evaluate(() => drawBudgetPage());
  await page.waitForSelector(".drawer table.charge-lines");
  await page.keyboard.press("Escape"); // closed, it stays closed
  await page.evaluate(() => drawBudgetPage());
  assert.equal(await page.locator(".drawer").count(), 0);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a floating line shows its working, a month's lines, and what marking one off will do", async () => {
  const page = await open(
    "window.__location = { parameter: { view: 'budget' } };" +
      "(function () { var m = new Date().toISOString().slice(0, 7), d = m + '-01';" +
      "  var back = function (n) { var t = new Date(m + '-15'); t.setMonth(t.getMonth() - n); return t.toISOString().slice(0, 7); };" +
      "  var spends = [40, 90, 50, 0], inputs = [4, 3, 2, 1].map(function (n, i) {" +
      "    return { month: m, line: 'petrol', category: 'petrol', rule: 'trailing', input: back(n), spend: spends[i]," +
      "      left_out: i === 0 ? 5 : 0, mean: 45, spread: 37.0, sigma: 0.25, value: 54.25 }; });" +
      "  window.__july = back(3);" +
      "  window.__answers.getBudgetBook = { tracked: [], charges: [], checks: [], inputs: inputs," +
      "    book: { groups: { petrol: 'car' }, recurring: [], days: [[d, 'petrol', 20]]," +
      "      targets: [{ month: m, category: 'petrol', priority: 'high', target: 54.25 }] } };" +
      "  window.__answers.getEditor.budgetLines = [{ line: 'petrol', effective: '2001-01-01', category: 'petrol', account: ''," +
      "    priority: 'high', rule: 'trailing', amount: '', per: 'monthly', note: '', in_force: 'yes', in_words: 'from the last 4 months'," +
      "    this_month: '54.25', last_12_months: '0', scheduled: '', spends_from: 'petrol' }];" +
      "  window.__answers.getLines = [" +
      "    { id: 't7', date: back(3) + '-20', account: 'a1', payee: 'BLUE PUMP FUEL', category: 'petrol', amount: -60, split: ''," +
      "      items: [], key: 'BLUE PUMP FUEL', note: '', one_off: false }," +
      "    { id: 't6', date: back(3) + '-05', account: 'a1', payee: 'BLUE PUMP FUEL', category: 'petrol', amount: -30, split: ''," +
      "      items: [], key: 'BLUE PUMP FUEL', note: '', one_off: false }];" +
      "})();",
  );
  await page.click("table.bv tr.line:has-text('Petrol') td:nth-child(2)");
  await page.waitForSelector(".drawer .working");
  assert.match(await page.textContent(".drawer .working h4"), /^How \w+ \d{4}'s budget was worked out$/);
  const months = () =>
    page.$$eval(".drawer .working-months > tr[data-month]", (rs) =>
      rs.map((r) => [r.querySelector("td.spend").textContent, r.querySelector("td.about").textContent, r.className]),
    );
  assert.deepEqual(await months(), [
    ["$40.00", "$5.00 marked one-off, left out", ""], // leak-guard: allow (made up)
    ["$90.00", "The most of these months", "most"], // leak-guard: allow (made up)
    ["$50.00", "", ""], // leak-guard: allow (made up)
    ["$0.00", "", ""], // leak-guard: allow (made up)
  ]);
  assert.equal(await page.textContent(".drawer .working-sum"), "Average $45.00, plus a quarter of $37.00: $54.25."); // leak-guard: allow (made up)
  // the big month's lines, the most spent first, each with the fix
  const july = await page.evaluate(() => window.__july);
  await page.click(`.drawer .working-months tr[data-month='${july}'] button:has-text('Its lines')`);
  await page.waitForSelector(".drawer .working tr.month-lines tr[data-txn='t7']");
  assert.deepEqual(await page.$$eval(".drawer .working tr.month-lines tr[data-txn]", (rs) => rs.map((r) => r.dataset.txn)), [
    "t7",
    "t6",
  ]);
  assert.equal(await page.locator(".drawer .working-after").count(), 0); // nothing in hand, nothing to preview
  await page.click(".drawer .working tr[data-txn='t7'] button:has-text('One-off')");
  assert.deepEqual(await staged(page), [`Petrol: the $60.00 line of ${july}-20 is a one-off`]); // leak-guard: allow (made up)
  // July drops to 30: the average of 40, 30, 50 and 0 is 30, plus a quarter of their spread
  const v = [40, 30, 50, 0],
    mean = 30,
    sd = Math.sqrt(v.reduce((t, x) => t + (x - mean) ** 2, 0) / 3);
  const about = "$" + (mean + 0.25 * sd).toFixed(2);
  assert.equal(
    await page.textContent(".drawer .working-after"),
    `After the next run, about ${about} (down $${(54.25 - mean - 0.25 * sd).toFixed(2)}), with 1 change to its lines in hand.`,
  );
  assert.equal(await page.getAttribute(".drawer .working tr[data-txn='t7']", "class"), "left-out");
  await page.click(".drawer .working tr[data-txn='t7'] button:has-text('Count it')"); // changed my mind
  assert.deepEqual(await staged(page), []);
  assert.equal(await page.locator(".drawer .working-after").count(), 0);
  await page.click(".drawer .working tr[data-txn='t7'] button:has-text('One-off')");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [{ kind: "line", txn_id: "t7", field: "one_off", value: "TRUE" }]);
  // saved but not run: still counted as in hand, so the preview stays
  await page.waitForSelector(".drawer .working-after");
  // the whole window came in one read: opening another month asks for nothing more
  await page.click(".drawer .working-months tr[data-month]:first-child button:has-text('Its lines')");
  await page.waitForSelector(".drawer .working tr.month-lines .line-list, .drawer .working tr.month-lines .support");
  assert.equal(await page.evaluate(() => window.__calls.filter((c) => c.name === "getLines").length), 1);
  assert.deepEqual(page.errors, []);
  await page.close();
});

const SANKEY = // a Sankey that keeps its select handler and selection, so a test can click a node
  "window.google.visualization = { DataTable: function () { this.addColumn = function () {}; this.addRows = function () {}; }," +
  "  Sankey: function () { var me = this; window.__sankey = me; me.draw = function () {}; me.picked = [];" +
  "    me.getSelection = function () { return me.picked; }; }," +
  "  events: { addListener: function (chart, ev, f) { chart.onselect = f; } } };";

const FLOWS =
  "window.__answers.getPeriods = ['2001-10', '2001', '2001 monthly average'];" +
  "window.__answers.getFlows = { rows: [['accounts', 'food', 80], ['accounts', 'hobby', 20]], dropped: []," +
  "  totals: [{ name: 'accounts', into: 0, out: 100, total: 100 }, { name: 'food', into: 80, out: 0, total: 80 }," +
  "    { name: 'hobby', into: 20, out: 0, total: 20 }]," +
  "  summary: { income: [], other: [], taxes: [], went: [{ name: 'food', amount: 80 }, { name: 'hobby', amount: 20 }], through: [] } };" +
  "window.__answers.getLines = [{ id: 't1', date: '2001-10-04', account: 'a1', payee: 'ACME GROCER', category: 'food', amount: -50," +
  "    split: '', items: [], key: 'ACME GROCER', note: '', one_off: false }," +
  "  { id: 't2', date: '2001-10-09', account: 'a1', payee: 'WILLOW ST', category: 'food', amount: -30, split: '', items: []," +
  "    key: 'WILLOW ST', note: '', one_off: false }];" +
  "window.__answers.getEditor.categories = [{ category: 'food', group: 'living', priority: 'high' }," +
  "  { category: 'household', group: 'home', priority: 'medium' }];";

test("where money went opens its lines, from the table or the chart, each with its fixes", async () => {
  const page = await open(SANKEY + FLOWS);
  await page.click("#view-flows");
  await page.click("#flows-tabs button[data-tab='table']");
  await page.waitForSelector(".flow-column.went li.opens");
  await page.click(".flow-column.went li.opens:has-text('food')");
  await page.waitForSelector(".drawer .line-list tr[data-txn='t1']");
  assert.match(await page.textContent(".drawer h2"), /Food/);
  const asked = await page.evaluate(() => window.__calls.filter((c) => c.name === "getLines").map((c) => c.args));
  assert.deepEqual(asked, [["2001-10-01", "2001-10-31", [], ["food"]]]); // the page opens by group
  // move a line to another category: the categories load only now, and the move is undone in place
  assert.equal(await page.evaluate(() => window.__calls.filter((c) => c.name === "getEditor").length), 0);
  await page.click(".drawer tr[data-txn='t2'] button:has-text('Move')");
  await page.waitForSelector(".drawer tr.move-row select"); // the picker's own row, under the line
  await page.selectOption(".drawer tr.move-row select", "household");
  await page.click(".drawer tr.move-row button:has-text('Move it')");
  assert.deepEqual(await staged(page), ["Food: the $30.00 line of 2001-10-09 moves to household"]); // leak-guard: allow (made up)
  assert.match(await page.textContent(".drawer tr[data-txn='t2']"), /Moves to Household after the next run/);
  await page.click(".drawer tr[data-txn='t2'] button:has-text('Undo move')");
  assert.deepEqual(await staged(page), []);
  await page.keyboard.press("Escape");
  // the chart: a click on where money went opens the same lines, from the page's copy
  await page.click("#flows-tabs button[data-tab='sankey']");
  await page.evaluate(() => {
    window.__sankey.picked = [{ name: "food  $80" }]; // leak-guard: allow (made up)
    window.__sankey.onselect();
  });
  await page.waitForSelector(".drawer .line-list tr[data-txn='t1']");
  assert.equal(await page.evaluate(() => window.__calls.filter((c) => c.name === "getLines").length), 1); // cached
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a year's monthly average opens the year's lines and says so", async () => {
  const page = await open(SANKEY + FLOWS);
  await page.click("#view-flows");
  await page.waitForSelector("#period option", { state: "attached" });
  await page.selectOption("#period", "2001 monthly average");
  await page.click("#flows-tabs button[data-tab='table']");
  await page.click(".flow-column.went li.opens:has-text('food')");
  await page.waitForSelector(".drawer .line-list");
  assert.match(await page.textContent(".drawer"), /These are the year's lines; the average is a twelfth of them/);
  const asked = await page.evaluate(() => window.__calls.filter((c) => c.name === "getLines").map((c) => c.args));
  assert.deepEqual(asked, [["2001-01-01", "2001-12-31", [], ["food"]]]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a setting a number uses is changed beside it: the tax plan's target, a retirement assumption", async () => {
  const line = (key, label, value) => ({ form: "Form 1040", line: key, key, label, value, filed: null });
  const plan = {
    year: "2001",
    return: "federal",
    person: "alex",
    employer: "Acme Widgets",
    paychecks_left: "4",
    vest_rate: "0.35",
    projected: "-10",
    target: "50",
    current_extra: "0",
    suggested_extra: "5",
    lands_at: "50",
    penalty_free: "TRUE",
    note: "",
  };
  const law = [
    {
      year: "2001",
      item: "hsa_family",
      value: "8000",
      document: "Rev. Proc. 2000-1",
      page: "2",
      quote: "family coverage is 8,000",
      record: "https://example.test/doc",
    }, // leak-guard: allow (made up)
    {
      year: "2001",
      item: "addl_medicare_rate",
      value: "0.009",
      document: "Instructions for Form 8959",
      page: "1",
      quote: "",
      record: "",
    },
  ];
  const page = await open(
    "window.__answers.getTaxes = " +
      JSON.stringify({
        years: [{ year: "2001", filed: false, forms: { "Form 1040": [line("24", "Total tax", 90)] } }],
        plan: [plan],
        accounts: [],
        notes: [],
        filed: {},
        history: [],
        totals: [],
        law,
      }) +
      ";" +
      "window.__answers.getEditor.parameters = [{ key: 'target_refund', value: '50', source: 'ledger' }];",
  );
  await page.click("#view-taxes");
  await page.waitForSelector("#taxes-body .plan-settings");
  const refund = "#taxes-body .setting-control[data-setting='target_refund']";
  assert.match(await page.textContent(refund), /target_refund/);
  assert.equal(await page.evaluate(() => window.__calls.filter((c) => c.name === "getEditor").length), 0); // not until needed
  await page.click(refund + " button:has-text('Change')");
  await page.waitForSelector(refund + " input");
  assert.equal(await page.inputValue(refund + " input"), "50");
  await page.fill(refund + " input", "40");
  await page.click(refund + " button:has-text('Keep')");
  assert.deepEqual(await staged(page), ["setting target_refund = 40"]);
  assert.match(await page.textContent(refund), /set to 40, waiting to be saved/);
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [{ kind: "parameter", key: "target_refund", value: "40", was: { value: "50" } }]);
  // a setting not in the ledger yet is a new one: no was
  const rate = "#taxes-body .setting-control[data-setting='planned_supplemental_rate']";
  await page.click(rate + " button:has-text('Change')");
  await page.fill(rate + " input", "0.3");
  await page.click(rate + " button:has-text('Keep')");
  assert.deepEqual(await staged(page), ["setting planned_supplemental_rate = 0.3"]);
  // the law behind the numbers: closed, drawn only when opened
  assert.equal(await page.locator("#taxes-body details.law table").count(), 0);
  await page.click("#taxes-body details.law summary");
  await page.waitForSelector("#taxes-body details.law table");
  assert.deepEqual(
    await page.$$eval("#taxes-body details.law tr", (rs) => rs.map((r) => [...r.children].slice(0, 2).map((c) => c.textContent))),
    [
      ["addl medicare rate", "0.90%"],
      ["hsa family", "$8,000"], // leak-guard: allow (made up)
    ],
  );
  assert.equal(
    await page.getAttribute("#taxes-body details.law tr[data-item='hsa_family'] a", "href"),
    "https://example.test/doc",
  );
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a retirement assumption's setting is changed beside it", async () => {
  const page = await open(`window.__answers.getRetirement = ${JSON.stringify(RETIREMENT)};
                           window.__answers.getEditor.parameterHistory = [
                             { key: 'retirement_spending:kids', n: '1', value: '0 from 2030', source: 'seed' },
                             { key: 'retirement_spending:kids', n: '2', value: '0 from 2032', source: 'web 2026-10-01' }];
                           window.__location = { parameter: { view: 'retirement', tab: 'assumptions' } };`);
  const kids = "#retirement-body .setting-control[data-setting='retirement_spending:kids']";
  await page.waitForSelector(kids);
  await page.click(kids + " button:has-text('Change')");
  await page.waitForSelector(kids + " input");
  assert.equal(await page.inputValue(kids + " input"), "0 from 2032");
  // what it was before, beside the change: an old value can be checked or put back
  assert.deepEqual(await page.$$eval(kids + " .setting-history li", (lis) => lis.map((li) => li.textContent)), [
    "0 from 2032 from the views, Oct 1, 2026, in force",
    "0 from 2030 when the ledger was set up",
  ]);
  await page.fill(kids + " input", "0 from 2033");
  await page.click(kids + " button:has-text('Keep')");
  assert.deepEqual(await staged(page), ["setting retirement_spending:kids = 0 from 2033"]);
  await page.click(kids + " button:has-text('Change')");
  await page.click(kids + " button:has-text('Undo')");
  assert.deepEqual(await staged(page), []);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("performance: ten years of a household's spending draws and redraws quickly", async () => {
  // about four times the household's real data (daily_actuals, budget targets, two years of floating workings),
  // so a change that makes the page's work grow badly with the data fails here before anyone feels it. Generous
  // limits: a slow CI machine passes, a quadratic loop does not.
  const page = await open(`(function () {
    var cats = [], groups = {}, days = [], targets = [], inputs = [];
    for (var i = 0; i < 60; i++) { cats.push("cat" + i); groups["cat" + i] = "group" + (i % 8); }
    var end = new Date(), d = new Date(Date.UTC(end.getUTCFullYear() - 10, end.getUTCMonth(), 1));
    for (; d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
      var day = d.toISOString().slice(0, 10);
      for (var k = 0; k < 15; k++) days.push([day, cats[(d.getUTCDate() * 7 + k * 13) % 60], 5 + ((k * 37) % 90)]);
      if (day.slice(8) === "01") cats.forEach(function (c) { targets.push({ month: day.slice(0, 7), category: c, priority: "medium", target: 300 }); });
    }
    window.__answers.getBudgetBook = { tracked: [], charges: [], checks: [], inputs: inputs, book: { groups: groups, recurring: [], days: days, targets: targets } };
    window.__location = { parameter: { view: "budget" } };
  })();`);
  await page.waitForSelector("table.bv tr.line");
  const timing = await page.evaluate(() => {
    const t0 = performance.now();
    for (let i = 0; i < 10; i++) drawBudgetPage();
    const draw = (performance.now() - t0) / 10;
    const t1 = performance.now();
    for (let i = 0; i < 10; i++)
      budgetView(budget.data.book, budget.period, budget.compare, budget.sameWeeks, budget.through || "");
    return { draw, view: (performance.now() - t1) / 10, rows: document.querySelectorAll("table.bv tr").length };
  });
  assert.ok(timing.rows > 60, "the table drew its lines");
  assert.ok(timing.draw < 250, `a full redraw took ${Math.round(timing.draw)} ms`);
  assert.ok(timing.view < 120, `shaping the period took ${Math.round(timing.view)} ms`);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a projected tax line opens to what it is made of, adding up to it, with the way to each input", async () => {
  const line = (key, label, value) => ({ form: "Form 1040", line: key, key, label, value, filed: null });
  const part = (key, person, employer, kind, amount, detail) => ({ person, employer, part: kind, amount, detail });
  const page = await open(
    "window.__answers.getTaxes = " +
      JSON.stringify({
        years: [
          {
            year: "2001",
            filed: false,
            forms: {
              "Form 1040": [line("1z", "Wages (W-2 box 1)", 90), line("25a", "Withheld, W-2s", 12), line("24", "Total tax", 9)],
            },
          },
        ],
        plan: [],
        accounts: [],
        notes: [],
        filed: {},
        history: [],
        totals: [],
        law: [],
        parts: {
          "2001|1z": [
            part("1z", "alex", "Acme Widgets", "so far", 60, "20 pay stubs through 2001-09-25"),
            part("1z", "alex", "Acme Widgets", "paychecks", 30, "7 more paychecks like the latest"),
          ],
          "2001|25a": [
            part("25a", "alex", "Acme Widgets", "so far", 11.6, "20 pay stubs"),
            part("25a", "alex", "Acme Widgets", "vests", 0.7, "2 vests"),
          ],
        },
      }) +
      ";",
  );
  await page.click("#view-taxes");
  await page.waitForSelector("#taxes-body table.form-lines");
  // a line with parts reads as one that opens; one without stays plain
  assert.equal(await page.locator("#taxes-body button.line-why").count(), 2);
  await page.click("#taxes-body button.line-why:has-text('Wages')");
  await page.waitForSelector("#taxes-body tr.line-parts");
  assert.equal(await page.getAttribute("#taxes-body button.line-why:has-text('Wages')", "aria-expanded"), "true");
  assert.deepEqual(
    await page.$$eval("#taxes-body tr.line-parts table.parts tr", (rs) =>
      rs.map((r) => [r.querySelector(".who div").textContent, r.querySelector(".num").textContent]),
    ),
    [
      ["So far", "$60.00"], // leak-guard: allow (made up)
      ["Paychecks to come", "$30.00"], // leak-guard: allow (made up)
    ],
  );
  assert.equal(await page.textContent("#taxes-body tr.line-parts .parts-sum"), "Adds up to $90.00."); // leak-guard: allow (made up)
  await page.click("#taxes-body button.line-why:has-text('Wages')"); // and closes
  assert.equal(await page.locator("#taxes-body tr.line-parts").count(), 0);
  // a line the return rounds says so
  await page.click("#taxes-body button.line-why:has-text('Withheld')");
  assert.equal(
    await page.textContent("#taxes-body tr.line-parts .parts-sum"),
    "Adds up to $12.30; the return rounds it to $12.00.", // leak-guard: allow (made up)
  );
  // a vest leads to the job behind it
  await page.click("#taxes-body tr.line-parts button:has-text('on Jobs')");
  await page.waitForFunction(() => document.querySelector("#view-jobs").getAttribute("aria-pressed") === "true");
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("a tax line's estimate is changed where it shows, as the whole figure in place of its parts", async () => {
  const line = (key, label, value) => ({ form: "Form 1040", line: key, key, label, value, filed: null });
  const part = (person, kind, amount, detail) => ({ person, employer: "", part: kind, amount, detail });
  const page = await open(
    "window.__answers.getTaxes = " +
      JSON.stringify({
        years: [{ year: "2001", filed: false, forms: { "Form 1040": [line("charity", "Gifts to charity", 50)] } }],
        plan: [],
        accounts: [],
        notes: [],
        filed: {},
        history: [],
        totals: [],
        law: [],
        parts: {
          "2001|charity": [
            part("", "estimate", 30, "your estimate in return_facts: a"),
            part("", "estimate", 20, "your estimate in return_facts: b"),
          ],
        },
      }) +
      ";",
  );
  await page.click("#view-taxes");
  await page.click("#taxes-body button.line-why:has-text('Gifts to charity')");
  await page.waitForSelector("#taxes-body tr.line-parts .estimate-control");
  assert.equal(await page.locator("#taxes-body .estimate-control").count(), 1); // one figure, one Change
  await page.click("#taxes-body .estimate-control button:has-text('Change')");
  assert.equal(await page.inputValue("#taxes-body .estimate-control input"), "50"); // its parts added up
  assert.match(await page.textContent("#taxes-body .estimate-control"), /stands in for its 2 parts/);
  await page.fill("#taxes-body .estimate-control input", "words");
  await page.click("#taxes-body .estimate-control button:has-text('Keep')");
  assert.deepEqual(await staged(page), []); // not a number: nothing staged
  await page.fill("#taxes-body .estimate-control input", "$60"); // leak-guard: allow (made up)
  await page.click("#taxes-body .estimate-control button:has-text('Keep')");
  assert.deepEqual(await staged(page), ["your estimate for Gifts to charity, 2001: 60"]);
  const edit = await page.evaluate(() => Object.values(staged)[0].edit);
  assert.deepEqual(edit, { kind: "estimate", year: "2001", key: "charity", value: "60", was: { value: "50" } });
  assert.match(await page.textContent("#taxes-body .estimate-control"), /\$60waiting to be saved/); // leak-guard: allow (made up)
  await page.click("#taxes-body .estimate-control button:has-text('Change')");
  await page.click("#taxes-body .estimate-control button:has-text('Undo')");
  assert.deepEqual(await staged(page), []);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("investments: a fund with no asset class is flagged, and its class is set where it shows", async () => {
  const leaf = (node, label) => ({
    node,
    parent: "",
    depth: 0,
    label,
    leaf: true,
    targetShare: 0.5,
    currentShare: 0.5,
    targetValue: 45,
    currentValue: 45,
    deltaValue: 0,
    proposedValue: null,
    proposedShare: null,
  });
  const page = await open(`window.__answers.getInvestments = ${JSON.stringify({
    ...INVESTMENTS,
    allocation: [leaf("SDT", "US stocks"), leaf("BT", "US bonds")],
    funds: [
      {
        symbol: "ACMEX",
        assetClass: "SDT",
        value: 60,
        accounts: ["a_brokerage"],
        security: {
          symbol: "ACMEX",
          name: "Acme Index",
          asset_class: "SDT",
          expense_ratio: "0.04",
          quote: "ACMEX",
          wash_group: "total",
        },
      },
      {
        symbol: "WILLX",
        assetClass: "",
        value: 30,
        accounts: ["a_brokerage"],
        security: { symbol: "WILLX", name: "Willow Bond", asset_class: "", expense_ratio: "", quote: "WILLX", wash_group: "" },
      },
      { symbol: "(a_plan total)", assetClass: "", value: 5, accounts: ["a_plan"], security: null },
    ],
  })};
                           window.__location = { parameter: { view: 'investments', tab: 'accounts' } };`);
  await page.waitForSelector("#investments-body .st-check");
  assert.match(await page.textContent("#investments-body .st-check"), /1 fund has no asset class.*: WILLX/);
  assert.equal(await page.evaluate(() => document.querySelector("#investments-body details").open), true);
  const willx = "#investments-body tr:has-text('WILLX') .class-control";
  await page.click(willx + " button:has-text('Set')");
  await page.fill(willx + " input", "SDT 60, XX 40");
  await page.click(willx + " button:has-text('Keep')");
  assert.match(await page.textContent(willx + " .error"), /Not a class in the allocation targets: XX/);
  await page.fill(willx + " input", "BT");
  await page.click(willx + " button:has-text('Keep')");
  assert.deepEqual(await staged(page), ["security WILLX: asset class BT"]);
  assert.match(await page.textContent(willx), /BT.*waiting to be saved/);
  assert.equal(await page.locator("#investments-body tr:has-text('(a_plan total)') .class-control").count(), 0); // not a fund
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [
    {
      kind: "security",
      symbol: "WILLX",
      asset_class: "BT",
      was: { name: "Willow Bond", asset_class: "", expense_ratio: "", quote: "WILLX", wash_group: "" },
    },
  ]);
  assert.deepEqual(page.errors, []);
  await page.close();
});

test("home: the settings its figures rest on are changed beside them, the stored value shown once loaded", async () => {
  const line = (key, label, value, note = "") => ({ key, label, value, asOf: "2001-01-01", note });
  const home = {
    loans: [],
    estimates: [],
    improvements: [],
    schedule: [],
    years: [],
    taxes: [],
    month: "2001-06",
    notes: [],
    homes: [
      {
        id: "willow",
        lines: [line("status", "Status", "owned"), line("value_used", "Value counted in net worth", 90, "purchase price")],
      },
    ],
  };
  const page = await open(
    `window.__answers.getHome = ${JSON.stringify(home)};` +
      "window.__answers.getEditor.parameters = [{ key: 'home_sale_cost_rate', value: '0.05', source: 'ledger' }," +
      "  { key: 'home_value_basis', value: '', source: 'not set' }];",
  );
  await page.click("#view-home");
  const rate = "#home-body .setting-control[data-setting='home_sale_cost_rate']";
  await page.waitForSelector(rate);
  assert.equal(await page.locator(rate + " .setting-value").count(), 0); // the page doesn't have it yet
  await page.click(rate + " button:has-text('Change')");
  await page.waitForSelector(rate + " input");
  assert.equal(await page.inputValue(rate + " input"), "0.05"); // the stored value, once loaded
  await page.fill(rate + " input", "0.04");
  await page.click(rate + " button:has-text('Keep')");
  assert.deepEqual(await staged(page), ["setting home_sale_cost_rate = 0.04"]);
  // a setting not set yet starts blank, and is a new one
  const basis = "#home-body .setting-control[data-setting='home_value_basis']";
  assert.equal(await page.locator(basis + " .setting-value").count(), 0); // nothing stored to show
  await page.click(basis + " button:has-text('Change')");
  assert.equal(await page.inputValue(basis + " input"), "");
  await page.fill(basis + " input", "median");
  await page.click(basis + " button:has-text('Keep')");
  await page.click("#pending-save");
  await page.waitForSelector("#pending-msg.saved");
  const sent = await page.evaluate(() => window.__calls.filter((c) => c.name === "saveChanges")[0].args[0]);
  assert.deepEqual(sent, [
    { kind: "parameter", key: "home_sale_cost_rate", value: "0.04", was: { value: "0.05" } },
    { kind: "parameter", key: "home_value_basis", value: "median", was: null },
  ]);
  assert.deepEqual(page.errors, []);
  await page.close();
});
