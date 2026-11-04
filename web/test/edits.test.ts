import { test } from "node:test";
import * as assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// edits.ts is an Apps Script file like shape.ts: evaluate the compiled file and pick its functions out.
const compiled = readFileSync(join(__dirname, "..", "src", "edits.js"), "utf8");
const edits = new Function(`${compiled}\nreturn { records, latestComputeRun, editorData, editProblems, editTarget, changeSet, waitingChangeSets };`)() as {
  records: (values: unknown[][]) => Row[];
  latestComputeRun: (runLog: unknown[][]) => string;
  editorData: (tabs: Record<string, unknown[][]>) => EditorData;
  editProblems: (edit: Edit) => string[];
  editTarget: (edit: Edit) => string;
  changeSet: (list: Edit[], createdAt: string, baseRun: string) => { name: string; text: string };
  waitingChangeSets: (files: { id: string; name: string; createdTime?: string }[], saved: Record<string, Saved>,
                      runLog: unknown[][]) => Waiting[];
};

const RUN_LOG: unknown[][] = [
  ["run_id", "computed_at", "stage", "detail"],
  ["r2", "2026-01-31T10:00:00+00:00", "ingest", JSON.stringify({ files: [
    { file: "changes_2026-01-30T090000Z.json", status: "failed", reason: "changes: refused, nothing written: edit 1 (line t9): no such transaction" },
    { file: "receipts.txt", status: "ingested" }] })],
  ["r1", "2026-01-30T10:00:00+00:00", "compute", "{}"],
  ["r3", "2026-01-31T11:00:00+00:00", "compute", "{}"],
];

test("records reads a tab by its header, as text, skipping blank rows", () => {
  assert.deepEqual(edits.records([["line", "amount"], ["groceries", 600], ["", ""], ["camp"]]),
                   [{ line: "groceries", amount: "600" }, { line: "camp", amount: "" }]);
  assert.deepEqual(edits.records([]), []);
});

test("a change set names the latest compute run its edits were made on", () => {
  assert.equal(edits.latestComputeRun(RUN_LOG), "r3");
  assert.equal(edits.latestComputeRun([]), "");
});

test("editorData gathers the editor tabs and only the stubs still unexplained", () => {
  const data = edits.editorData({
    run_log: RUN_LOG,
    editor_categories: [["category", "group", "priority", "run_id"], ["kids", "family", "high", "r3"], ["food", "living", "high", "r3"]],
    withholding_backtest: [["stub_id", "explanation"], ["s1", "unexplained: 40.00 more than expected"], ["s2", "matches"]],
    wi_withholding_backtest: [["stub_id", "explanation"], ["s1", "unexplained: 5.00 less"]],
  });
  assert.equal(data.baseRun, "r3");
  assert.deepEqual(data.categories.map((c) => c.category), ["food", "kids"]);
  assert.equal("run_id" in data.categories[0], false);
  assert.deepEqual(data.withholding.map((w) => [w.stub_id, w.jurisdiction]), [["s1", "federal"], ["s1", "wi"]]);
  assert.deepEqual(data.budgetLines, []);
});

test("an edit's shape is checked as the engine checks it", () => {
  assert.deepEqual(edits.editProblems({ kind: "payee", key: "ACME GROCER", category: "food" }), []);
  assert.deepEqual(edits.editProblems({ kind: "budget_line", line: "camp", effective: "2026-06-01", rule: "end", was: { amount: "90" } }), []);
  assert.deepEqual(edits.editProblems({ kind: "payee", key: "ACME", extra: "x" }),
                   ["missing category", "unknown field extra"]);
  assert.deepEqual(edits.editProblems({ kind: "line", txn_id: "t1", field: "note", value: " padded" }), ["value has spaces at its ends"]);
  assert.deepEqual(edits.editProblems({ kind: "explode" }), ['unknown kind "explode"']);
  assert.deepEqual(edits.editProblems({ kind: "category", category: "gifts", was: { group: 3 } as unknown as Row }),
                   ["was must be null or an object of strings"]);
});

test("an edit's target says which setting it changes", () => {
  assert.equal(edits.editTarget({ kind: "line", txn_id: "t1", field: "split", value: "" }),
               edits.editTarget({ kind: "line", txn_id: "t1", field: "category", value: "food" }));
  assert.notEqual(edits.editTarget({ kind: "budget_line", line: "camp", rule: "scheduled", effective: "2026-06-01" }),
                  edits.editTarget({ kind: "budget_line", line: "camp", rule: "scheduled", effective: "2026-07-01" }));
  assert.equal(edits.editTarget({ kind: "not_duplicate", txn_a: "t2", txn_b: "t1" }),
               edits.editTarget({ kind: "not_duplicate", txn_a: "t1", txn_b: "t2" }));
});

test("changeSet writes the engine's format, named by when it was saved", () => {
  const out = edits.changeSet([{ kind: "payee", key: "ACME GROCER", category: "food" }], "2026-01-31T12:00:00.123Z", "r3");
  assert.equal(out.name, "changes_2026-01-31T120000Z.json");
  assert.deepEqual(JSON.parse(out.text), { format: "household_changes", version: 1, created_at: "2026-01-31T12:00:00Z",
                                           base_run: "r3", edits: [{ kind: "payee", key: "ACME GROCER", category: "food" }] });
});

test("changeSet refuses nothing to save, a bad edit, or one setting edited twice", () => {
  assert.throws(() => edits.changeSet([], "2026-01-31T12:00:00Z", "r3"), /nothing to save/);
  assert.throws(() => edits.changeSet([{ kind: "payee", key: "ACME" }], "2026-01-31T12:00:00Z", "r3"),
                /edit 1 \(payee\): missing category/);
  assert.throws(() => edits.changeSet([{ kind: "category", category: "gifts", group: "family", was: null },
                                       { kind: "category", category: "gifts", group: "fun", was: null }], "2026-01-31T12:00:00Z", "r3"),
                /edit 2 \(category\): edits the same thing as edit 1/);
});

test("waiting change sets carry what they held and why the last ingest refused one", () => {
  const files = [{ id: "f2", name: "changes_2026-01-31T120000Z.json", createdTime: "2026-01-31T12:00:01Z" },
                 { id: "f1", name: "changes_2026-01-30T090000Z.json" }];
  const saved = { f2: { id: "f2", name: files[0].name, savedAt: "2026-01-31T12:00:00Z", edits: ["payee ACME GROCER: food"] } };
  const out = edits.waitingChangeSets(files, saved, RUN_LOG);
  assert.deepEqual(out.map((w) => w.id), ["f1", "f2"]);
  assert.match(out[0].refused, /no such transaction/);
  assert.deepEqual(out[1].edits, ["payee ACME GROCER: food"]);
  assert.equal(out[1].refused, "");
});
