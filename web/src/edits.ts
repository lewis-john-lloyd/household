/**
 * Editing in the views: what the editors show, and the change set Save writes. Pure functions, no Apps
 * Script calls, so the tests run them under Node (see shape.ts).
 *
 * The views never write the ledger. Edits are held in the browser; Save sends them here, and saveChanges
 * (Code.ts) writes them as one change set file into the views' own folder inside the inbox, where ingest
 * reads it (parsers/changes.py). These checks mirror the engine's shape checks so a mistake is caught
 * at Save; the engine checks everything again, against the ledger, and refuses the whole file if
 * anything does not fit.
 */

type Row = Record<string, string>;

/** One edit, as parsers/changes.py reads it: a kind and its fields, `was` for edits that replace a setting. */
interface Edit {
  kind: string;
  was?: Row | null;
  [field: string]: string | Row | null | undefined;
}

interface EditorData {
  baseRun: string;
  budgetLines: Row[];
  scheduled: Row[]; // each scheduled amount in force, one per line and date
  payeeRules: Row[];
  categories: Row[];
  aliases: Row[];
  parameters: Row[];
  parameterHistory: Row[]; // every value each household setting has had, in the ledger's order (n from 1)
  review: Row[];
  duplicates: Row[];
  withholding: Row[]; // stubs whose withholding is unexplained, federal and wi
  jobs: Row[]; // comp_events, newest first per person, each with what the paychecks under it say
  payCheck: Row[]; // runs of paychecks beside comp_events that disagree or have no row in force
  subscriptions: Row[]; // yours, then recurring charges not marked, then the ones you said are not
}

const CHANGE_FORMAT = "household_changes";
const CHANGE_VERSION = 1;
const MAX_CHANGE_EDITS = 1000;
const MAX_CHANGE_TEXT = 500;
const MAX_CHANGE_BYTES = 1024 * 1024;

// kind -> [required fields, optional fields], as in parsers/changes.py
const EDIT_KINDS: Record<string, [string[], string[]]> = {
  payee: [["key", "category"], []],
  line: [["txn_id", "field", "value"], []],
  budget_line: [
    ["line", "effective", "rule"],
    ["category", "account", "priority", "amount", "per", "note", "was"],
  ],
  payee_rule: [
    ["pattern", "category", "effective"],
    ["precedence", "ask", "was"],
  ],
  category: [["category"], ["group", "priority", "was"]],
  alias: [
    ["alias", "category"],
    ["note", "was"],
  ],
  parameter: [["key", "value"], ["was"]],
  explanation: [["jurisdiction", "stub_id", "note"], []],
  not_duplicate: [["txn_a", "txn_b"], []],
  item: [["item_id", "category"], []],
  job: [
    ["effective", "person", "employer"],
    ["salary", "band", "rating", "fte", "note"],
  ],
  subscription: [["key", "subscription"], ["note"]],
  recurring_merge: [["key", "same_as"], []],
  recurring_name: [["key", "name"], []],
  recurring_line: [["key", "line"], []],
  recipient: [["key", "deductible"], []],
  looks_right: [["check", "looks_right"], []],
  security: [["symbol", "asset_class"], ["was"]],
  estimate: [
    ["year", "key", "value"],
    ["person", "note", "was"],
  ],
};

// every field an edit that replaces a setting can change: its `was` gives them all (WAS_FIELDS in changes.py)
const WAS_FIELDS: Record<string, string[]> = {
  budget_line: ["effective", "category", "account", "priority", "rule", "amount", "per", "note"],
  payee_rule: ["category", "precedence", "effective", "ask"],
  category: ["group", "priority"],
  alias: ["category", "note"],
  parameter: ["value"],
  security: ["name", "asset_class", "expense_ratio", "quote", "wash_group"],
  estimate: ["value"],
};

/** A tab's values (header first) as rows of text by column name; blank rows are skipped. */
function records(values: unknown[][]): Row[] {
  if (!values.length) return [];
  const header = values[0].map((h) => String(h).trim());
  const out: Row[] = [];
  for (const v of values.slice(1)) {
    const row: Row = {};
    header.forEach((h, i) => {
      if (h) row[h] = String(v[i] ?? "").trim();
    });
    if (Object.values(row).some((x) => x !== "")) out.push(row);
  }
  return out;
}

/** The latest compute's run id, from run_log: a change set names the run its edits were made on. */
function latestComputeRun(runLog: unknown[][]): string {
  const runs = records(runLog)
    .filter((r) => r.stage === "compute")
    .sort((a, b) => a.computed_at.localeCompare(b.computed_at));
  return runs.length ? runs[runs.length - 1].run_id : "";
}

/** Stubs a backtest left unexplained, with the jurisdiction an explanation is filed under. */
function unexplained(rows: Row[], jurisdiction: string): Row[] {
  return rows.filter((r) => (r.explanation || "").indexOf("unexplained") === 0).map((r) => ({ ...r, jurisdiction }));
}

/** What the editors show, from the Reports tabs compute writes for them. */
function editorData(tabs: Record<string, unknown[][]>): EditorData {
  const tab = (name: string): Row[] => records(tabs[name] || []);
  const drop = (rows: Row[]): Row[] =>
    rows.map((r) => {
      const { run_id, ...rest } = r;
      return rest;
    });
  return {
    baseRun: latestComputeRun(tabs.run_log || []),
    budgetLines: drop(tab("editor_budget_lines")),
    scheduled: drop(tab("editor_scheduled")),
    payeeRules: drop(tab("editor_payee_rules")),
    categories: drop(tab("editor_categories")).sort((a, b) => a.category.localeCompare(b.category)),
    aliases: drop(tab("editor_aliases")),
    parameters: drop(tab("editor_parameters")),
    parameterHistory: drop(tab("editor_parameter_history")),
    review: drop(tab("editor_review")),
    duplicates: drop(tab("possible_duplicates")),
    withholding: drop(
      unexplained(tab("withholding_backtest"), "federal").concat(unexplained(tab("wi_withholding_backtest"), "wi")),
    ),
    jobs: drop(tab("editor_jobs")),
    payCheck: drop(tab("pay_check")).filter((r) => r.status !== "agrees"),
    subscriptions: drop(tab("editor_subscriptions")),
  };
}

/** What is wrong with one edit's shape, judged alone ([] when nothing). */
function editProblems(edit: Edit): string[] {
  if (!edit || typeof edit !== "object") return ["not an edit"];
  const spec = EDIT_KINDS[edit.kind];
  if (!spec) return [`unknown kind ${JSON.stringify(edit.kind)}`];
  const [required, optional] = spec;
  const fields = Object.keys(edit).filter((f) => f !== "kind");
  const out = required.filter((f) => fields.indexOf(f) < 0).map((f) => `missing ${f}`);
  for (const f of fields) {
    const value = edit[f];
    if (required.indexOf(f) < 0 && optional.indexOf(f) < 0) out.push(`unknown field ${f}`);
    else if (f === "was") {
      if (value !== null && (typeof value !== "object" || Object.values(value as Row).some((v) => typeof v !== "string"))) {
        out.push("was must be null or an object of strings");
      } else if (value !== null && WAS_FIELDS[edit.kind]) {
        const missing = WAS_FIELDS[edit.kind].filter((w) => !(w in (value as Row)));
        if (missing.length) out.push(`was must give every field the edit replaces; missing ${missing.join(", ")}`);
      }
    } else if (typeof value !== "string") out.push(`${f} must be text`);
    else if (value.length > MAX_CHANGE_TEXT) out.push(`${f} is longer than ${MAX_CHANGE_TEXT} characters`);
    else if (value !== value.trim()) out.push(`${f} has spaces at its ends`);
  }
  // the value checks ingest makes that an editor could get wrong: caught here, they don't refuse a whole save
  if (MERCHANT_KINDS.indexOf(edit.kind) >= 0 && edit.key === "") out.push("key is empty");
  if (edit.kind === "looks_right" && edit.check === "") out.push("check is empty");
  const yesNo = YES_NO_FIELDS[edit.kind];
  if (yesNo && edit[yesNo] !== undefined && edit[yesNo] !== "yes" && edit[yesNo] !== "no")
    out.push(`${yesNo} ${JSON.stringify(edit[yesNo])} is not yes or no`);
  return out;
}

// kinds about a merchant key, and the fields that are yes or no, as parsers/changes.py checks them
const MERCHANT_KINDS = ["payee", "subscription", "recurring_merge", "recurring_name", "recurring_line", "recipient"];
const YES_NO_FIELDS: Record<string, string> = {
  subscription: "subscription",
  recipient: "deductible",
  looks_right: "looks_right",
};

/** The setting an edit changes: one change set holds one edit per setting, so a newer edit replaces an older one. */
function editTarget(edit: Edit): string {
  const f = (name: string): string => String(edit[name] ?? "");
  switch (edit.kind) {
    case "budget_line":
      return f("rule") === "scheduled" ? `budget_line|${f("line")}|scheduled|${f("effective")}` : `budget_line|${f("line")}`;
    case "line":
      return `line|${f("txn_id")}|${f("field") === "split" ? "category" : f("field")}`;
    case "payee":
      return `payee|${f("key").toUpperCase()}`;
    case "payee_rule":
      return `payee_rule|${f("pattern")}`;
    case "category":
      return `category|${f("category")}`;
    case "alias":
      return `alias|${f("alias")}`;
    case "parameter":
      return `parameter|${f("key")}`;
    case "explanation":
      return `explanation|${f("jurisdiction")}|${f("stub_id")}`;
    case "not_duplicate":
      return `not_duplicate|${[f("txn_a"), f("txn_b")].sort().join("|")}`;
    case "item":
      return `item|${f("item_id")}`;
    case "job":
      return `job|${f("person")}|${f("effective")}|${f("employer")}`;
    case "subscription":
      return `subscription|${f("key").toUpperCase()}`;
    case "recurring_merge":
      return `recurring_merge|${f("key").toUpperCase()}`;
    case "recurring_name":
      return `recurring_name|${f("key").toUpperCase()}`;
    case "recurring_line":
      return `recurring_line|${f("key").toUpperCase()}`;
    case "recipient":
      return `recipient|${f("key").toUpperCase()}`;
    case "looks_right":
      return `looks_right|${f("check")}`;
    case "security":
      return `security|${f("symbol")}`;
    case "estimate":
      return `estimate|${f("year")}|${f("key")}|${f("person")}`;
    default:
      return `${edit.kind}|${JSON.stringify(edit)}`;
  }
}

/** "changes_2026-01-31T120000Z.json": named by when it was saved, so ingest reads change sets in order. */
function changeSetName(createdAt: string, suffix = ""): string {
  return "changes_" + createdAt.slice(0, 23).replace(/:/g, "") + "Z" + (suffix ? "_" + suffix : "") + ".json";
}

/** createdAt made later than the last save's, so one person's change sets are strictly in order. */
function laterThan(createdAt: string, last: string): string {
  return last && createdAt <= last ? new Date(Date.parse(last) + 1).toISOString() : createdAt;
}

/**
 * The change set file for these edits, or an error listing every problem. createdAt is an ISO time in
 * UTC ("2026-01-31T12:00:00.000Z").
 */
function changeSet(edits: Edit[], createdAt: string, baseRun: string, suffix = ""): { name: string; text: string } {
  const problems: string[] = [];
  if (!Array.isArray(edits) || !edits.length) problems.push("nothing to save");
  else if (edits.length > MAX_CHANGE_EDITS) problems.push(`${edits.length} edits, more than ${MAX_CHANGE_EDITS}`);
  const seen: Record<string, number> = {};
  (Array.isArray(edits) ? edits : []).forEach((e, n) => {
    editProblems(e).forEach((p) => problems.push(`edit ${n + 1} (${e && e.kind}): ${p}`));
    const target = editTarget(e);
    if (seen[target]) problems.push(`edit ${n + 1} (${e.kind}): edits the same thing as edit ${seen[target]}`);
    seen[target] = n + 1;
  });
  const at = createdAt.slice(0, 23) + "Z"; // milliseconds: two saves in one second stay in order
  const text = JSON.stringify(
    { format: CHANGE_FORMAT, version: CHANGE_VERSION, created_at: at, base_run: baseRun, edits },
    null,
    1,
  );
  if (text.length > MAX_CHANGE_BYTES) problems.push(`the change set is over ${MAX_CHANGE_BYTES} bytes`);
  if (problems.length) throw new Error("Not saved: " + problems.join("; "));
  return { name: changeSetName(createdAt, suffix), text };
}

interface Saved {
  id: string;
  name: string;
  savedAt: string;
  request?: string; // the browser's id for the Save that wrote it (see saveChanges)
  edits: string[]; // each edit in words, as the browser described it at Save, as many as fit
  more?: number; // edits left out of edits to keep the record small
}

const SAVED_RECORD_BYTES = 8000; // under Apps Script's 9 KB a property
const SAVED_TEXT_CHARS = 200; // one edit's words, at most

/**
 * The saved change set's record for its own user property: its edits in words, each cut to
 * SAVED_TEXT_CHARS and as many as fit in maxBytes, with how many more there were.
 */
function savedRecord(
  id: string,
  name: string,
  savedAt: string,
  described: string[],
  request = "",
  maxBytes = SAVED_RECORD_BYTES,
): string {
  const texts = (described || []).map((d) => String(d).slice(0, SAVED_TEXT_CHARS));
  const record: Saved = { id, name, savedAt, request, edits: [], more: 0 };
  const bytes = (s: string): number => unescape(encodeURIComponent(s)).length;
  for (const text of texts) {
    record.edits.push(text);
    record.more = texts.length - record.edits.length;
    if (bytes(JSON.stringify(record)) > maxBytes) {
      record.edits.pop();
      break;
    }
  }
  record.more = texts.length - record.edits.length;
  return JSON.stringify(record);
}

/**
 * A Save's request id, as the browser made it: letters, digits and dashes, 8 to 64 of them, so it is safe
 * inside a Drive query. "" when it is not one.
 */
function requestId(request: unknown): string {
  return typeof request === "string" && /^[A-Za-z0-9-]{8,64}$/.test(request) ? request : "";
}

interface Waiting extends Saved {
  refused: string; // why the last ingest refused it, or ""
  unseen: boolean; // ingest ran after it was saved and never listed it: ingest can't see the views' folder
}

/**
 * Change sets still in the views' folder (ingest moves each one out once it is written), with what the
 * last ingest said about each: a refused one stays, with its reason, until it is discarded.
 */
function waitingChangeSets(
  files: { id: string; name: string; createdTime?: string }[],
  saved: Record<string, Saved>,
  runLog: unknown[][],
): Waiting[] {
  const ingests = records(runLog)
    .filter((r) => r.stage === "ingest")
    .sort((a, b) => a.computed_at.localeCompare(b.computed_at));
  const reasons: Record<string, string> = {};
  const listed = new Set<string>();
  let lastIngest = 0; // when the last ingest ran, if its row lists the files it read
  if (ingests.length) {
    try {
      const last = ingests[ingests.length - 1];
      const detail = JSON.parse(last.detail) as { files?: { file?: string; status?: string; reason?: string }[] };
      for (const f of detail.files || []) {
        if (f.file) listed.add(f.file);
        if (f.file && f.status && f.status !== "ingested" && f.status !== "already_present")
          reasons[f.file] = f.reason || f.status;
      }
      if (Array.isArray(detail.files)) lastIngest = Date.parse(last.computed_at) || 0;
    } catch (e) {
      // an older run_log row: no reasons to show
    }
  }
  return files
    .map((f) => {
      const known = saved[f.id];
      const edits = known ? known.edits.concat(known.more ? [`and ${known.more} more`] : []) : [];
      const savedAt = known ? known.savedAt : f.createdTime || "";
      const unseen = !!lastIngest && !!savedAt && Date.parse(savedAt) < lastIngest && !listed.has(f.name);
      return { id: f.id, name: f.name, savedAt, edits, refused: reasons[f.name] || "", unseen };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** A waiting change set's edits as saved, each with its words, for the page to show as saved until applied. */
interface WaitingEdits {
  id: string;
  savedAt: string;
  edits: Edit[];
  texts: string[]; // each edit in words, as it was described at Save ("" past the ones the record kept)
}

interface Discarded extends Saved {
  discardedAt: string;
}

interface AppliedAnyway extends Discarded {
  writtenAt: string; // when the ingest that wrote it received it
}

/**
 * Discarded change sets the ledger holds anyway. Trashing a file stops every ingest from then on, but one
 * already running may have read it first. changes_written (compute's Reports, written by each ingest from
 * the ledger's source_documents) lists every change set written lately, so a later run never hides one, and
 * one whose filing in Drive failed after its rows were written is there too.
 */
function appliedAnyway(discarded: Record<string, Discarded>, changesWritten: unknown[][]): AppliedAnyway[] {
  const written: Record<string, string> = {}; // by Drive file id: names need not be unique, ids are
  for (const r of records(changesWritten)) if (r.file_id) written[r.file_id] = r.written_at;
  return Object.values(discarded)
    .filter((d) => d.id in written)
    .map((d) => ({ ...d, writtenAt: written[d.id] }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
