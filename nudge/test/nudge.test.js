// The weekly email (nudge/Code.js) under Node, with Sheets, Drive, mail and the script properties faked.
// Code.js is an Apps Script file: its functions are globals, so it runs in a fresh context per test.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CODE = fs.readFileSync(path.join(__dirname, "..", "Code.js"), "utf8");
const TODAY = "2026-11-07";

const NUDGES = [
  ["group", "item", "detail", "show_from", "show_until", "link", "run_id"],
  ["Download into the inbox", "BLUE PUMP FUEL card", "has everything through September 30, 2026", "2026-10-30", "",
   "https://views.example/exec?view=status", "r2"],
  ["Claim FSA reimbursements", "Dependent care FSA 2025", "50 of 5,000 not reimbursed yet; claims due April 15",
   "2026-01-01", "2026-04-15", "https://claims.example", "r2"],  // not yet: it starts in January
  ["Change before the year's last paychecks", "Federal (W-4 Step 4(c)) withholding", "projected owing 90",
   "2026-11-25", "2026-12-25", "https://payroll.example", "r2"],  // not yet: 30 days before the last paycheck
  ["Answer in Review", "2 questions", "covering 5 lines, the oldest from September 1, 2026", "2026-10-01", "",
   "https://views.example/exec?view=edit&tab=review", "r2"],
];

function runLog(ingestAt, computeAt, files = []) {
  const rows = [["run_id", "computed_at", "stage", "detail"]];
  if (ingestAt) rows.push(["r1", ingestAt, "ingest", JSON.stringify({ inbox: "inbox1", files })]);
  if (computeAt) rows.push(["r2", computeAt, "compute", "{}"]);
  return rows;
}

/** A fresh script over fake Reports tabs and a fake inbox: folder id -> [files]. */
function script({ tabs = { nudges: NUDGES, run_log: runLog("2026-11-01T10:00:00+00:00", "2026-11-01T10:05:00+00:00") },
                  folders = { inbox1: [] }, props = { REPORTS_ID: "reports1" }, failSheets = "", failDrive = "" } = {}) {
  const sent = [];
  const logs = [];
  const triggers = [];
  const context = {
    console: { log: (line) => logs.push(line) },
    JSON, Math, Date, Number, String, Object, Array, Error, RegExp,
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in props ? props[k] : null) }) },
    Sheets: { Spreadsheets: { Values: { batchGet: (id, { ranges }) => {
      if (failSheets) throw new Error(failSheets);
      assert.equal(id, props.REPORTS_ID);
      return { valueRanges: ranges.map((r) => ({ values: tabs[r] || [] })) };
    } } } },
    Drive: { Files: { list: ({ q, pageToken }) => {
      if (failDrive) throw new Error(failDrive);
      const parent = /'([^']+)' in parents/.exec(q)[1];
      const all = folders[parent] || [];
      const start = pageToken ? Number(pageToken) : 0;  // two files a page, so paging is exercised
      return { files: all.slice(start, start + 2), nextPageToken: start + 2 < all.length ? String(start + 2) : undefined };
    } } },
    MailApp: { sendEmail: (m) => sent.push(m) },
    Session: { getEffectiveUser: () => ({ getEmail: () => "alex@example.com" }), getScriptTimeZone: () => "UTC" },
    Utilities: { formatDate: () => TODAY },
    ScriptApp: {
      getProjectTriggers: () => triggers.slice(),
      deleteTrigger: (t) => triggers.splice(triggers.indexOf(t), 1),
      WeekDay: { SATURDAY: "SATURDAY" },
      newTrigger: (fn) => {
        const t = { fn, getHandlerFunction: () => fn };
        const chain = { timeBased: () => chain, onWeekDay: (d) => { t.day = d; return chain; },
                        atHour: (h) => { t.hour = h; return chain; }, create: () => { triggers.push(t); return t; } };
        return chain;
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(CODE, context);
  return { context, sent, logs, triggers };
}

const file = (name, createdTime, mimeType = "text/csv") => ({ id: name, name, mimeType, createdTime });

test("the email holds only the things whose dates take in today, grouped by what to do", () => {
  const s = script();
  s.context.weekly();
  assert.equal(s.sent.length, 1);
  const m = s.sent[0];
  assert.equal(m.to, "alex@example.com");
  assert.equal(m.subject, "Household: 2 things to do this weekend");
  assert.equal(m.body.split("\n\n")[0], "Download into the inbox\n" +
    "- BLUE PUMP FUEL card: has everything through September 30, 2026 (https://views.example/exec?view=status)");
  assert.match(m.body, /Answer in Review\n- 2 questions: covering 5 lines/);
  assert.doesNotMatch(m.body, /FSA|W-4/);
  assert.match(m.htmlBody, /<a href="https:\/\/views.example\/exec\?view=edit&amp;tab=review">Open<\/a>/);
});

test("nothing to do sends nothing", () => {
  const s = script({ tabs: { nudges: [NUDGES[0]], run_log: runLog("2026-11-01T10:00:00Z", "2026-11-01T10:05:00Z") } });
  s.context.weekly();
  assert.equal(s.sent.length, 0);
  assert.deepEqual(s.logs, ["nothing needs doing: no email this week"]);
});

test("files a month in the inbox ask for ingest, and ones ingest refused say why", () => {
  const folders = {
    inbox1: [file("statement_new.csv", "2026-11-02T08:00:00Z"), file("old_export.csv", "2026-09-15T08:00:00Z"),
             file("web_edits", "2026-01-01T00:00:00Z", "application/vnd.google-apps.folder"),
             file("mystery.bin", "2026-08-01T00:00:00Z")],
    web_edits: [file("changes_2026-09-20T120000.000Z_a1.json", "2026-09-20T12:00:00Z")],
  };
  const tabs = { nudges: [NUDGES[0]], run_log: runLog("2026-11-01T10:00:00Z", "2026-11-01T10:05:00Z",
    [{ file: "mystery.bin", status: "unrecognized", reason: "no reader knows this file" }]) };
  const s = script({ tabs, folders });
  s.context.weekly();
  const body = s.sent[0].body;
  assert.match(body, /^Fix or remove in the inbox\n- mystery.bin: ingest can't read it: no reader knows this file/);
  assert.match(body, /Run ingest, then compute\n- 2 files in the inbox: the oldest, old_export.csv, since September 15, 2026/);
  assert.doesNotMatch(body, /statement_new/);  // a week old is not stale
});

test("an ingest a month old that compute never followed asks for compute", () => {
  const tabs = { nudges: [NUDGES[0]], run_log: runLog("2026-10-01T10:00:00Z", "2026-09-01T10:00:00Z") };
  const s = script({ tabs });
  s.context.weekly();
  assert.match(s.sent[0].body, /Run compute\n- Reports are behind the ledger: the last ingest, on October 1, 2026, was never/);
  const recent = script({ tabs: { nudges: [NUDGES[0]], run_log: runLog("2026-10-20T10:00:00Z", "2026-09-01T10:00:00Z") } });
  recent.context.weekly();
  assert.equal(recent.sent.length, 0);  // three weeks behind is not yet a month
});

test("Reports it can't read, or an inbox it can't list, is in the email, never a silent week", () => {
  const s = script({ failSheets: "The caller does not have permission" });
  s.context.weekly();
  assert.match(s.sent[0].body, /Check the weekly email\n- Reports couldn't be read: Error: The caller does not have permission/);
  const d = script({ failDrive: "File not found: inbox1" });
  d.context.weekly();
  assert.match(d.sent[0].body, /The inbox couldn't be listed: Error: File not found/);
  assert.match(d.sent[0].body, /BLUE PUMP FUEL card/);  // the rest of the email still goes
});

test("a service the manifest doesn't turn on is named, not blamed on REPORTS_ID", () => {
  const s = script();
  delete s.context.Sheets;  // the default manifest clasp create-script writes enables no advanced services
  s.context.weekly();
  assert.match(s.sent[0].body, /Reports couldn't be read: ReferenceError: Sheets is not defined\. The Sheets service isn't turned on: push the repo's appsscript\.json/);
  assert.doesNotMatch(s.sent[0].body, /REPORTS_ID/);
});

test("preview logs the email without sending it, and installTrigger keeps one Saturday trigger", () => {
  const s = script();
  s.context.preview();
  assert.equal(s.sent.length, 0);
  assert.match(s.logs[0], /^Household: 2 things to do this weekend\n\nDownload into the inbox/);
  s.context.installTrigger();
  s.context.installTrigger();
  assert.deepEqual(s.triggers.map((t) => [t.fn, t.day, t.hour]), [["weekly", "SATURDAY", 7]]);
});

test("text in the email is escaped in its HTML", () => {
  const tabs = { nudges: [NUDGES[0], ["Fix a setting", "payroll_url", "'<b>x</b>' is not an https link", "2026-11-01",
                                      "", "", "r2"]], run_log: runLog("", "") };
  const s = script({ tabs });
  s.context.weekly();
  assert.match(s.sent[0].htmlBody, /&lt;b&gt;x&lt;\/b&gt;/);
  assert.doesNotMatch(s.sent[0].htmlBody, /<b>x<\/b>/);
});
