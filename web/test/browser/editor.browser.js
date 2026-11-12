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
  await page.evaluate(() => {
    forgetSaved("f9");
    redrawPage();
  });
  await page.waitForSelector(".alert button:has-text('Track it')");
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
  assert.equal(await page.locator(".drawer form.edit").count(), 1); // only this line's form
  assert.match(await page.textContent(".drawer"), /Budget line: camp sam/);
  assert.doesNotMatch(await page.textContent(".drawer"), /Budget line: camp alex/);
  await page.fill(".drawer label:has-text('Amount') input", "300");
  await page.click(".drawer button:has-text('Keep this change')");
  assert.match((await staged(page))[0], /^budget line camp sam: fixed 300 annually for kids from /);
  await page.waitForSelector("table.bv tr.subline:has-text('camp sam') .tag.warn");
  assert.equal(await page.locator("table.bv tr.subline:has-text('camp alex') .tag.warn").count(), 0);
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
  await page.click(".drawer tr[data-txn='t3'] button"); // changed my mind: the staged change goes
  assert.deepEqual(await staged(page), []);
  await page.click(".drawer tr[data-txn='t1'] button"); // and a line marked before counts again
  assert.deepEqual(await staged(page), ["Books: the $9.00 line of 2001-04-03 counts again"]); // leak-guard: allow (made up)
  assert.deepEqual((await rows())[3], ["t1", "", "Counts again after the next runchanged", "Not part of it"]); // and can go back
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
