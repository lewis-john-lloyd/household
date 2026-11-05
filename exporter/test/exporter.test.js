// The receipt exporter (exporter/Code.js) under Node, with Gmail, Drive and the script properties faked.
// Code.js is an Apps Script file: its functions are globals, so it runs in a fresh context per test.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CODE = fs.readFileSync(path.join(__dirname, "..", "Code.js"), "utf8");
const DAY = 24 * 60 * 60 * 1000;

function b64url(text) {
  return Buffer.from(text, "utf8").toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function message(id, isoDate, body, from = "Willow St <receipts@example.coop>") {
  return { id, internalDate: String(Date.parse(isoDate)),
           payload: { mimeType: "text/plain", headers: [{ name: "From", value: from }, { name: "Subject", value: "Receipt" }],
                      body: { data: b64url(body) } } };
}

function pad(n) { return String(n).padStart(2, "0"); }

/** A fresh exporter over a fake mailbox. mailbox: id -> message (or a function returning one, or throwing). */
function exporter(mailbox, props = {}) {
  const store = { SENDERS: JSON.stringify(["from:receipts@example.coop"]), FOLDER_ID: "folder1", ...props };
  const files = [];
  const logs = [];
  const triggers = [];
  const fetch = (id) => {
    const m = mailbox[id];
    if (m === undefined) throw new Error("API call to gmail.users.messages.get failed with error: Requested entity was not found.");
    return typeof m === "function" ? m() : m;
  };
  const context = {
    console: { log: (line) => logs.push(line) },
    JSON, Math, Date, Number, String, Object, Array, Error, RegExp,
    PropertiesService: { getScriptProperties: () => ({
      getProperty: (k) => (k in store ? store[k] : null), setProperty: (k, v) => { store[k] = String(v); } }) },
    Gmail: { Users: { Messages: {
      list: (_me, { q }) => {
        const after = /after:(\d{4})\/(\d{2})\/(\d{2})/.exec(q);
        const from = after ? Date.UTC(+after[1], +after[2] - 1, +after[3]) : 0;
        const ids = Object.keys(mailbox).filter((id) => {
          const m = mailbox[id];
          const when = typeof m === "function" ? m.when : Number(m.internalDate);
          return when >= from;
        });
        return { messages: ids.map((id) => ({ id })) };
      },
      get: (_me, id) => fetch(id),
    } } },
    Drive: { Files: {
      create: (meta, blob) => { files.push({ name: meta.name, text: blob ? blob.text : "" }); return { id: "file" + files.length }; },
      get: (id) => ({ id, trashed: false }),
    } },
    MimeType: { PLAIN_TEXT: "text/plain" },
    Utilities: {
      sleep: () => {},
      newBlob: (data, _mime, _name) => {
        const text = typeof data === "string" ? data : Buffer.from(data).toString("utf8");
        return { text, getDataAsString: () => text };
      },
      base64Decode: (text) => Array.from(Buffer.from(text, "base64")),
      formatDate: (d, _zone, format) => {
        const parts = { yyyy: d.getUTCFullYear(), MM: pad(d.getUTCMonth() + 1), dd: pad(d.getUTCDate()),
                        HH: pad(d.getUTCHours()), mm: pad(d.getUTCMinutes()), ss: pad(d.getUTCSeconds()) };
        return format.replace(/'T'/, "T").replace(/yyyy|MM|dd|HH|mm|ss/g, (k) => parts[k]);
      },
    },
    ScriptApp: {
      getProjectTriggers: () => triggers.slice(),
      deleteTrigger: (t) => triggers.splice(triggers.indexOf(t), 1),
      newTrigger: (handler) => ({ timeBased: () => ({ everyDays: () => ({ atHour: () => ({
        create: () => triggers.push({ getHandlerFunction: () => handler }) }) }) }) }),
    },
  };
  vm.createContext(context);
  vm.runInContext(CODE, context);
  return { run: () => context.exportReceipts(), installTrigger: () => context.installTrigger(), store, files, logs, triggers,
           decode: (d) => context.decode_(d), body: (p) => context.body_(p) };
}

const RECEIPT = "Rolled Oats 4.00 F\nTOTAL 4.00";

test("an unreadable receipt is not lost when later receipts move the search window past it", () => {
  // the reviewer's case: October 1 can't be read, October 10 can; the next search starts October 7
  let broken = true;
  const october1 = () => (broken ? { id: "m001", internalDate: String(Date.parse("2026-10-01T12:00:00Z")), payload: null }
                                 : message("m001", "2026-10-01T12:00:00Z", RECEIPT));
  october1.when = Date.parse("2026-10-01T12:00:00Z");
  const mailbox = { m001: october1, m002: message("m002", "2026-10-10T12:00:00Z", RECEIPT) };
  const first = exporter(mailbox);
  first.run();
  assert.equal(first.store.SINCE, "2026/10/07");
  assert.deepEqual(Object.keys(JSON.parse(first.store.FAILED)), ["m001"], first.logs.join("\n"));
  assert.match(first.files[0].text, /===== RECEIPT m002 =====/);
  assert.match(first.files[0].text, /===== RECEIPT m001 =====\nUnreadable: TypeError/);  // told to the engine
  assert.ok(first.logs.some((l) => /1 unreadable, tried again next run: m001/.test(l)));

  broken = false;  // a fixed exporter, or a message that reads now
  const second = exporter(mailbox, { ...first.store });
  second.run();
  assert.equal(second.files.length, 1);
  assert.match(second.files[0].text, /===== RECEIPT m001 =====\nFrom: Willow St/);
  assert.doesNotMatch(second.files[0].text, /m002/);  // already exported
  assert.deepEqual(JSON.parse(second.store.FAILED), {});
  assert.ok(second.logs.some((l) => /message m001 reads now, after 1 failed tries/.test(l)));
});

test("a message that still can't be read is tried every run and noted to the engine only once", () => {
  const bad = { id: "m003", internalDate: String(Date.parse("2026-10-01T12:00:00Z")), payload: null };
  const first = exporter({ m003: bad });
  first.run();
  assert.equal(first.files.length, 1);  // a file with only the note, so the status page shows it
  const second = exporter({ m003: bad }, { ...first.store });
  second.run();
  assert.equal(second.files.length, 0);
  assert.equal(JSON.parse(second.store.FAILED).m003.tries, 2);
});

test("a failed message deleted from Gmail since is dropped, not retried forever", () => {
  const failed = { m004: { when: Date.parse("2026-10-01T12:00:00Z"), tries: 3, error: "TypeError" } };
  const run = exporter({}, { FAILED: JSON.stringify(failed) });
  run.run();
  assert.deepEqual(JSON.parse(run.store.FAILED), {});
  assert.ok(run.logs.some((l) => /message m004 is gone from Gmail/.test(l)));
});

test("a receipt in the overlap between searches is exported once", () => {
  const mailbox = { m005: message("m005", "2026-10-10T12:00:00Z", RECEIPT) };
  const first = exporter(mailbox);
  first.run();
  const second = exporter(mailbox, { ...first.store });
  second.run();
  assert.equal(first.files.length, 1);
  assert.equal(second.files.length, 0);
});

test("a call Gmail refuses for quota is tried again", () => {
  let refusals = 2;
  const flaky = () => {
    if (refusals-- > 0) throw new Error("Quota exceeded for quota metric 'Queries'");
    return message("m006", "2026-10-10T12:00:00Z", RECEIPT);
  };
  flaky.when = Date.parse("2026-10-10T12:00:00Z");
  const run = exporter({ m006: flaky });
  run.run();
  assert.match(run.files[0].text, /RECEIPT m006/);
  assert.ok(run.logs.some((l) => /asked to slow down/.test(l)));
});

test("installTrigger replaces only its own trigger", () => {
  const run = exporter({});
  run.triggers.push({ getHandlerFunction: () => "somethingElse" }, { getHandlerFunction: () => "exportReceipts" });
  run.installTrigger();
  assert.deepEqual(run.triggers.map((t) => t.getHandlerFunction()), ["somethingElse", "exportReceipts"]);
  assert.ok(run.logs.some((l) => /replaced 1 older/.test(l)));
});

test("bodies decode from bytes or unpadded base64url, and HTML in the plain part reads as text", () => {
  const run = exporter({});
  assert.equal(run.decode(b64url("café ok")), "café ok");
  assert.equal(run.decode(Array.from(Buffer.from("bytes"))), "bytes");
  const html = "<table><tr><td>Oats</td><td>4.00</td></tr></table>";
  assert.equal(run.body({ mimeType: "text/plain", body: { data: b64url(html) } }).trim(), "Oats 4.00");
});
