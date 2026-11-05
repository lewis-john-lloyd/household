// The views' server code (dist/Code.js and the files it shares a scope with) under Node, with Apps Script's
// services faked: the user property store enforces Apps Script's limits (9 KB a value, 500 KB in all), so a
// save's bookkeeping meets the same walls it would in Apps Script. Run after `npm run build`.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const DIST = path.join(__dirname, "..", "..", "dist");
const CODE = ["shape.js", "edits.js", "taxes.js", "Code.js"].map((f) => fs.readFileSync(path.join(DIST, f), "utf8")).join("\n");
const VALUE_LIMIT = 9 * 1024, STORE_LIMIT = 500 * 1024;

function server({ refuseProperties = false, failCreate = false, now = () => new Date("2026-01-31T12:00:00.123Z"),
                  props: start = {},
                  runLog = [["run_id", "computed_at", "stage", "detail"], ["r1", "2026-01-31T00:00:00Z", "compute", "{}"]],
                  changesWritten = [["file", "written_at"]] } = {}) {
  const tabs = { run_log: runLog, changes_written: changesWritten };
  const props = { ...start };
  const files = [];
  let uuid = 0;
  const store = {
    getProperty: (k) => (k in props ? props[k] : null),
    getProperties: () => ({ ...props }),
    setProperty: (k, v) => {
      if (refuseProperties || props.__refuse) throw new Error("Service invoked too many times: properties");
      if (Buffer.byteLength(String(v), "utf8") > VALUE_LIMIT) throw new Error("Argument too large: value");
      const total = Object.entries({ ...props, [k]: String(v) }).reduce((a, [kk, vv]) => a + kk.length + vv.length, 0);
      if (total > STORE_LIMIT) throw new Error("You have exceeded the property storage quota");
      props[k] = String(v);
    },
    deleteProperty: (k) => { delete props[k]; },
  };
  const FakeDate = class extends Date {
    constructor(...a) { if (a.length) super(...a); else super(now().getTime()); }
    static now() { return now().getTime(); }
  };
  const context = {
    console: { log: () => {} }, JSON, Math, Object, Array, String, Number, Error, RegExp, Set, Map, Buffer, unescape, encodeURIComponent,
    Date: FakeDate,
    PropertiesService: { getUserProperties: () => store, getScriptProperties: () => ({ getProperty: () => "reports-id" }) },
    LockService: { getUserLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) },
    Utilities: { newBlob: (text) => ({ text }), getUuid: () => `u${String(++uuid).padStart(7, "0")}-rest` },
    Sheets: { Spreadsheets: {
      get: () => ({ sheets: Object.keys(tabs).map((title) => ({ properties: { title } })) }),
      Values: { get: (_, name) => ({ values: tabs[name] }) },
    } },
    Drive: { Files: {
      create: (meta, blob) => {
        if (failCreate) throw new Error("Drive is not answering");
        const id = meta.mimeType === "application/vnd.google-apps.folder" ? "folder" : `f${files.length + 1}`;
        if (id !== "folder") files.push({ id, name: meta.name, text: blob.text, parents: meta.parents, appProperties: meta.appProperties || {} });
        return { id };
      },
      get: (id) => {
        if (id === "folder") return { id, trashed: false };
        const f = files.find((x) => x.id === id);
        if (!f || f.deleted) throw new Error("File not found: " + id);
        return { id, trashed: !!f.trashed, parents: f.parents, name: f.name };
      },
      list: ({ q }) => {
        const asked = /appProperties has \{ key='(\w+)' and value='([^']*)' \}(?: and trashed = (true|false))?/.exec(q);
        if (asked) {  // every file the views made, wherever ingest has moved it (Drive's default includes the trash)
          return { files: files.filter((f) => !f.deleted && f.appProperties[asked[1]] === asked[2] &&
                                              (asked[3] === undefined || String(!!f.trashed) === asked[3]))
                             .map((f) => ({ id: f.id, name: f.name, appProperties: f.appProperties })) };
        }
        return { files: files.filter((f) => !f.filed).map((f) => ({ id: f.id, name: f.name, createdTime: "" })) };
      },
      update: (meta, id) => { const f = files.find((x) => x.id === id); if (f && meta.trashed) f.trashed = true; return {}; },
    } },
  };
  vm.createContext(context);
  vm.runInContext(CODE, context);
  return { context, props, files, runLog, changesWritten };
}

const EDIT = { kind: "parameter", key: "transfer_account", value: "savings", was: { value: "checking" } };

test("a save with a thousand edits keeps a record under the 9 KB limit and says so in the Saved tab", () => {
  const s = server();
  const described = Array.from({ length: 1000 }, (_, n) => `budget line line_${n}: fixed 100 monthly for food from 2026-11-01`);
  const r = s.context.saveChanges([EDIT], described);
  assert.equal(r.warning, "");
  assert.equal(s.files.length, 1);
  const record = Object.entries(s.props).filter(([k]) => k.startsWith("SAVED_"));
  assert.equal(record.length, 1);
  assert.ok(Buffer.byteLength(record[0][1], "utf8") <= 8000);
  const waiting = s.context.getWaiting();
  assert.equal(waiting[0].edits[waiting[0].edits.length - 1].startsWith("and "), true);
});

test("when the record can't be kept the save still succeeds, once, with a warning", () => {
  // the folder is made already (a first save that can't record its folder fails before any file is written,
  // so "not saved" is true there)
  const s = server({ refuseProperties: true, props: { EDITS_FOLDER_ID: "folder" } });
  const r = s.context.saveChanges([EDIT], ["setting transfer_account = savings"]);
  assert.equal(s.files.length, 1);  // the change set is in Drive: a retry would write it twice
  assert.match(r.warning, /could not be kept .*the change set itself is saved/);
  assert.equal(r.name, s.files[0].name);
});

test("many saves stay inside the store: old records are dropped, and filed ones tidied", () => {
  let t = Date.parse("2026-01-31T12:00:00.000Z");
  const s = server({ now: () => new Date(t += 1000) });
  const described = Array.from({ length: 400 }, (_, n) => `a long description of edit ${n} `.repeat(3));
  for (let n = 0; n < 60; n++) s.context.saveChanges([EDIT], described);
  const kept = Object.keys(s.props).filter((k) => k.startsWith("SAVED_"));
  assert.equal(kept.length, 40);
  s.files.slice(0, 55).forEach((f) => { f.filed = true; });  // ingest moved them out
  s.context.getWaiting();
  assert.equal(Object.keys(s.props).filter((k) => k.startsWith("SAVED_")).length, 5);
});

test("two saves in the same millisecond get distinct names in the order they were made", () => {
  const s = server();  // the clock never moves
  const a = s.context.saveChanges([EDIT], []);
  const b = s.context.saveChanges([EDIT], []);
  assert.notEqual(a.name, b.name);
  assert.ok(a.name < b.name, `${a.name} before ${b.name}`);
  const at = s.files.map((f) => JSON.parse(f.text).created_at);
  assert.deepEqual(at, ["2026-01-31T12:00:00.123Z", "2026-01-31T12:00:00.124Z"]);
});

const REQUEST = "0b9d6e4a-1c2f-4b7e-9a10-3d5e7f9a1b2c";

test("the same request saved twice writes one file and answers with it both times", () => {
  const s = server();
  const a = s.context.saveChanges([EDIT], ["setting transfer_account = savings"], REQUEST);
  const b = s.context.saveChanges([EDIT], ["setting transfer_account = savings"], REQUEST);
  assert.equal(s.files.length, 1);
  assert.equal(b.id, a.id);
  assert.equal(b.name, a.name);
  assert.equal(b.again, true);
  assert.equal(a.again, false);
  assert.deepEqual(s.context.findSave(REQUEST), b);
});

test("a request is found on its file when its record was never kept or was tidied after ingest", () => {
  const s = server({ refuseProperties: true, props: { EDITS_FOLDER_ID: "folder" } });
  const a = s.context.saveChanges([EDIT, { ...EDIT, key: "target_refund", value: "90", was: { value: "50" } }], [], REQUEST);
  assert.match(a.warning, /could not be kept/);
  s.files[0].filed = true;  // ingest moved it out of the views' folder
  const found = s.context.findSave(REQUEST);
  assert.equal(found.id, a.id);
  assert.equal(found.count, 2);
  assert.equal(s.context.saveChanges([EDIT], [], REQUEST).again, true);
  assert.equal(s.files.length, 1);
});

test("a request that never reached Drive is not found, and saving it then writes it", () => {
  const s = server({ failCreate: true, props: { EDITS_FOLDER_ID: "folder" } });
  assert.throws(() => s.context.saveChanges([EDIT], [], REQUEST), /not answering/);
  assert.equal(s.context.findSave(REQUEST), null);
  assert.throws(() => s.context.findSave("x' or trashed = true or name contains '"), /Not a request id/);
});

test("a request whose file was discarded since reads as discarded, by its record and by Drive's search", () => {
  const s = server();
  const a = s.context.saveChanges([EDIT], ["setting transfer_account = savings"], REQUEST);
  s.context.discardWaiting(a.id);
  const byRecord = s.context.findSave(REQUEST);
  assert.equal(byRecord.discarded, true);
  assert.equal(byRecord.id, a.id);
  for (const k of Object.keys(s.props)) if (k.startsWith("SAVED_")) delete s.props[k];  // the record is gone
  assert.equal(s.context.findSave(REQUEST).discarded, true);
  const again = s.context.saveChanges([EDIT], [], REQUEST);  // saving it again writes it anew
  assert.equal(again.again, false);
  assert.equal(s.files.length, 2);
  assert.equal(s.context.findSave(REQUEST).discarded, false);
  assert.equal(s.context.findSave(REQUEST).id, again.id);
});

test("a record whose file Drive no longer has falls back to Drive's search", () => {
  const s = server();
  const a = s.context.saveChanges([EDIT], [], REQUEST);
  s.files[0].deleted = true;
  assert.equal(s.context.findSave(REQUEST), null);
  assert.equal(s.context.saveChanges([EDIT], [], REQUEST).again, false);
  assert.notEqual(s.files[1].id, a.id);
});

test("a discarded change set is remembered, and told as applied anyway once the ledger holds it", () => {
  const s = server();
  const a = s.context.saveChanges([EDIT], ["setting transfer_account = savings"], REQUEST);
  assert.equal(s.context.discardWaiting(a.id).remembered, true);
  assert.equal(s.files[0].trashed, true);
  assert.deepEqual(JSON.parse(JSON.stringify(s.context.getAppliedAnyway())), []);  // the ledger doesn't hold it
  // an ingest that had downloaded it before the discard wrote it all the same, whatever its run_log row says
  s.changesWritten.push([a.name, "2026-01-31T12:00:05+00:00"]);
  const told = s.context.getAppliedAnyway();
  assert.deepEqual(told.map((d) => [d.id, d.writtenAt, d.edits]), [[a.id, "2026-01-31T12:00:05+00:00", ["setting transfer_account = savings"]]]);
  s.context.forgetDiscarded(a.id);
  assert.equal(s.context.getAppliedAnyway().length, 0);
});

test("a discard that can't be remembered says so, and the file is discarded all the same", () => {
  const s = server();
  const a = s.context.saveChanges([EDIT], [], REQUEST);
  s.props.__refuse = "1";
  const out = s.context.discardWaiting(a.id);
  assert.equal(s.files[0].trashed, true);
  assert.equal(out.remembered, false);
  assert.match(out.warning, /could not remember this discard/);
});

test("only the newest discards are remembered, and none past thirty days", () => {
  const s = server();
  for (let n = 0; n < 25; n++) {
    const a = s.context.saveChanges([EDIT], ["x"], `req-${String(n).padStart(6, "0")}`);
    s.context.discardWaiting(a.id);
  }
  assert.equal(Object.keys(s.props).filter((k) => k.startsWith("DISCARDED_")).length, 20);
  s.props.DISCARDED_old = JSON.stringify({ id: "old", name: "n", savedAt: "", edits: [], discardedAt: "2025-01-01T00:00:00Z" });
  s.context.getAppliedAnyway();
  assert.equal("DISCARDED_old" in s.props, false);
});
