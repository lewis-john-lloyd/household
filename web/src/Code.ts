/**
 * The views web app: serves Index.html and answers its calls. It runs as the
 * person viewing it, so it reads only what that person can open; the Reports
 * spreadsheet's id comes from the script property REPORTS_ID, never from code.
 * It reads Reports and writes only change sets, into a folder of its own (see
 * the editing section below and parsers/changes.py).
 */

// The browser tab's icon. Apps Script takes it only as an https address of an image, so it is served from the
// repo (web/assets/icon.png, drawn from icon.svg beside it).
const ICON = "https://github.com/lewis-john-lloyd/household/raw/main/web/assets/icon.png";

function doGet(): GoogleAppsScript.HTML.HtmlOutput {
  return HtmlService.createHtmlOutputFromFile("Index").setTitle("The Steward").setFaviconUrl(ICON);
}

function reportsId_(): string {
  const id = PropertiesService.getScriptProperties().getProperty("REPORTS_ID");
  if (!id) throw new Error("Set the script property REPORTS_ID to the Reports spreadsheet's id.");
  return id;
}

/**
 * A Reports tab's values, read through the Sheets API (the advanced service in appsscript.json): it works under
 * the read-only spreadsheets scope, where SpreadsheetApp.openById demands full read and write access. Any
 * failure (permissions, the API, a wrong REPORTS_ID) is thrown, never taken for an empty tab.
 */
function readValues_(name: string): unknown[][] {
  if (!Sheets || !Sheets.Spreadsheets || !Sheets.Spreadsheets.Values) {
    throw new Error("The Sheets advanced service is not enabled: push appsscript.json again.");
  }
  const api = Sheets.Spreadsheets.Values;
  try {
    return (
      (patiently_(() => api.get(reportsId_(), name, { valueRenderOption: "UNFORMATTED_VALUE" })).values as
        unknown[][] | undefined) || []
    );
  } catch (e) {
    throw new Error(`Could not read the ${name} tab of Reports (check REPORTS_ID and that you can open Reports): ` + e);
  }
}

// Sheets allows each person 60 read requests a minute. A page reads all its tabs in one request (readTabsIfAny_),
// and a request refused for quota waits and tries again, up to about half a minute, before the page says so.
const QUOTA_WAITS_MS = [4000, 10000, 16000];

function patiently_<T>(call: () => T): T {
  for (let i = 0; ; i++) {
    try {
      return call();
    } catch (e) {
      if (i >= QUOTA_WAITS_MS.length || !/quota|rate limit|429/i.test(String(e))) throw e;
      console.log(`Sheets refused for quota; trying again in ${QUOTA_WAITS_MS[i] / 1000}s`);
      Utilities.sleep(QUOTA_WAITS_MS[i]);
    }
  }
}

/**
 * Several Reports tabs in one request: each that exists, read; each that doesn't yet, []. Any failure is thrown,
 * as readTabIfAny_ does.
 */
function readTabsIfAny_(names: string[]): Record<string, unknown[][]> {
  const tabs: Record<string, unknown[][]> = {};
  const present = names.filter((n) => tabTitles_().indexOf(n) >= 0);
  names.forEach((n) => {
    tabs[n] = [];
  });
  if (!present.length) return tabs;
  if (!Sheets || !Sheets.Spreadsheets || !Sheets.Spreadsheets.Values) {
    throw new Error("The Sheets advanced service is not enabled: push appsscript.json again.");
  }
  const api = Sheets.Spreadsheets.Values;
  let ranges: { values?: unknown[][] | undefined }[] | undefined;
  try {
    ranges = patiently_(() =>
      api.batchGet(reportsId_(), {
        ranges: present.map((n) => `'${n.replace(/'/g, "''")}'`),
        valueRenderOption: "UNFORMATTED_VALUE",
      }),
    ).valueRanges;
  } catch (e) {
    throw new Error(
      `Could not read the ${present.join(", ")} tabs of Reports (check REPORTS_ID and that you can open Reports): ` + e,
    );
  }
  present.forEach((n, i) => {
    tabs[n] = ((ranges && ranges[i] && ranges[i].values) as unknown[][] | undefined) || [];
  });
  return tabs;
}

let tabTitles: string[] | null = null; // Reports' tab names, read once per call from the page

/** The names of Reports' tabs, so a tab not made yet can be told from a tab that could not be read. */
function tabTitles_(): string[] {
  if (tabTitles === null) {
    if (!Sheets || !Sheets.Spreadsheets)
      throw new Error("The Sheets advanced service is not enabled: push appsscript.json again.");
    const api = Sheets.Spreadsheets;
    let sheets: { properties?: { title?: string | undefined } | undefined }[] | undefined;
    try {
      sheets = patiently_(() => api.get(reportsId_(), { fields: "sheets.properties.title" })).sheets;
    } catch (e) {
      throw new Error("Could not open Reports (check REPORTS_ID and that you can open it): " + e);
    }
    tabTitles = (sheets || []).map((s) => String((s.properties && s.properties.title) || ""));
  }
  return tabTitles;
}

/** A tab the page can't do without: missing or empty is an error that says to run compute. */
function readTab_(name: string): unknown[][] {
  const values = readValues_(name);
  if (!values.length) throw new Error(`The Reports ${name} tab is empty: run compute first.`);
  return values;
}

function readFlows_(): FlowRow[] {
  return parseRows(readTab_("flows"));
}

/** Periods with flows, newest first. */
function getPeriods(): string[] {
  return periods(readFlows_());
}

/** Sankey rows for one period ("2026" or "2026-10") at one level ("category" or "group"), with each node's total. */
function getFlows(
  period: string,
  level: string,
): { rows: SankeyRow[]; dropped: SankeyRow[]; totals: NodeTotal[]; summary: FlowSummary } {
  const { rows, dropped } = toSankeyRows(readFlows_(), period, level);
  const totals = nodeTotals(rows);
  return { rows, dropped, totals, summary: flowSummary(totals) };
}

/** The transactions behind one budget line: that period's (through a day, when given), in those categories. */
function getTransactions(period: string, categories: string[], through?: string): Txn[] {
  return transactionsFor(parseTransactions(readTab_("categorized")), period, categories, through || "");
}

/**
 * The Budget page's data (shape.ts budgetView shapes it in the page): each month's targets, every day's
 * spending, the recurring charges' months, and the recurring charges you track.
 */
function getBudgetBook(): { book: BudgetBook; tracked: Record<string, string>[] } {
  const tabs = readTabsIfAny_([
    "budget_vs_actual",
    "daily_actuals",
    "subscription_months",
    "editor_categories",
    "subscriptions",
    "budget_targets",
    "flows",
    "giving",
    "sinking_funds",
  ]);
  if (!tabs.budget_vs_actual.length) throw new Error("The Reports budget_vs_actual tab is empty: run compute first.");
  const book = budgetBook(
    tabs.budget_vs_actual,
    tabs.daily_actuals,
    tabs.subscription_months,
    tabs.editor_categories,
    tabs.budget_targets,
    tabs.flows,
    tabs.giving,
    tabs.sinking_funds,
  );
  console.log(`budget book: ${book.targets.length} targets, ${book.days.length} days, ${book.recurring.length} recurring months`);
  return { book, tracked: subscriptionsPage(tabs.subscriptions).subscriptions };
}

/**
 * A tab that may not exist yet (each starts with the first run of the release that writes it): [] when Reports
 * has no such tab or it is empty. Any other failure is thrown, so the page says it could not load rather than
 * showing nothing as if there were nothing.
 */
function readTabIfAny_(name: string): unknown[][] {
  if (tabTitles_().indexOf(name) < 0) return [];
  return readValues_(name);
}

/** The latest ingest and compute: their summaries, every line they printed, and what needs the owner. */
function getStatus(): Status {
  const tabs = readTabsIfAny_(["run_log", "run_notes", "coverage", "editor_review"]);
  return parseStatus(tabs.run_log, tabs.run_notes, tabs.coverage, tabs.editor_review);
}

// --- editing ------------------------------------------------------------------------------------------
//
// The views write one kind of thing: a change set file, into a Drive folder of their own (drive.file: the
// app sees only files it made). The folder is made in My Drive on the first save; move it into the household
// inbox once, and ingest reads it there (parsers/changes.py). Its id, and what each saved file held, are kept
// in this person's user properties.

const EDITS_FOLDER_NAME = "web_edits";
const FOLDER_MIME_TYPE = "application/vnd.google-apps.folder";
const SAVED_KEEP = 40; // saved change sets remembered for the waiting list: 40 records of 8 KB stay well inside 500 KB
const SAVED_PREFIX = "SAVED_"; // one user property per saved change set, its record (savedRecord, under 9 KB)

function drive_(): GoogleAppsScript.Drive {
  if (!Drive || !Drive.Files) throw new Error("The Drive advanced service is not enabled: push appsscript.json again.");
  return Drive;
}

/** The views' own folder: made on the first save, then the same one wherever it is moved. */
function editsFolder_(make: boolean): { id: string; made: boolean } | null {
  const props = PropertiesService.getUserProperties();
  const id = props.getProperty("EDITS_FOLDER_ID");
  if (id) {
    let found: { trashed?: boolean };
    try {
      found = drive_().Files.get(id, { fields: "id,trashed" });
    } catch (e) {
      throw new Error(`The views' folder (${EDITS_FOLDER_NAME}) can't be reached; restore it, or reset the views' folder: ${e}`);
    }
    if (found.trashed) throw new Error(`The views' folder (${EDITS_FOLDER_NAME}) is in the trash: restore it.`);
    return { id, made: false };
  }
  if (!make) return null;
  const made = drive_().Files.create({ name: EDITS_FOLDER_NAME, mimeType: FOLDER_MIME_TYPE });
  props.setProperty("EDITS_FOLDER_ID", String(made.id));
  console.log(`made the folder ${EDITS_FOLDER_NAME} in My Drive (${made.id}): move it into the household inbox`);
  return { id: String(made.id), made: true };
}

/** The saved change sets' records, by file id; an unreadable one is left out. */
function saved_(): Record<string, Saved> {
  const all = PropertiesService.getUserProperties().getProperties();
  const out: Record<string, Saved> = {};
  for (const key of Object.keys(all)) {
    if (key.indexOf(SAVED_PREFIX) !== 0) continue;
    try {
      const record = JSON.parse(all[key]) as Saved;
      out[record.id] = record;
    } catch (e) {
      console.log(`a saved record (${key}) could not be read; it is skipped`);
    }
  }
  return out;
}

/** Keep this change set's record, and drop the oldest past SAVED_KEEP (and the one-property record of 0.9.x). */
function keepSaved_(id: string, record: string): void {
  const props = PropertiesService.getUserProperties();
  props.setProperty(SAVED_PREFIX + id, record);
  props.deleteProperty("SAVED_CHANGES");
  const old = Object.values(saved_())
    .sort((a, b) => b.savedAt.localeCompare(a.savedAt))
    .slice(SAVED_KEEP);
  for (const s of old) props.deleteProperty(SAVED_PREFIX + s.id);
}

/** Everything the editors show: the settings in force, the review queue, duplicates and unexplained stubs. */
function getEditor(): EditorData {
  const names = [
    "run_log",
    "editor_budget_lines",
    "editor_scheduled",
    "editor_payee_rules",
    "editor_categories",
    "editor_aliases",
    "editor_parameters",
    "editor_review",
    "possible_duplicates",
    "withholding_backtest",
    "wi_withholding_backtest",
    "editor_jobs",
    "pay_check",
    "editor_subscriptions",
  ];
  const data = editorData(readTabsIfAny_(names));
  console.log(`editor: run ${data.baseRun}, ${data.budgetLines.length} budget lines, ${data.review.length} review questions`);
  return data;
}

interface SaveResult {
  id: string;
  name: string;
  count: number;
  madeFolder: boolean;
  warning: string;
  again: boolean; // this request had already saved: this is that file, and nothing new was written
  discarded: boolean; // that file is in the trash since: ingest will never read it
}

const REQUEST_PROPERTY = "household_request"; // on each change set file: the Save that wrote it

/**
 * The change set an earlier call with this request wrote, or null. Its saved record answers first, once
 * Drive confirms the file is there; a file whose record was never kept, or was tidied after ingest filed it,
 * is found by the request on the file. A file in the trash comes back as discarded, never as saved.
 */
function savedFor_(request: string): SaveResult | null {
  const result = (id: string, name: string, count: number, discarded: boolean): SaveResult => ({
    id,
    name,
    count,
    madeFolder: false,
    warning: "",
    again: true,
    discarded,
  });
  const known = Object.values(saved_()).filter((r) => r.request === request)[0];
  if (known) {
    let trashed: boolean | null = null;
    try {
      trashed = !!drive_().Files.get(known.id, { fields: "id,trashed" }).trashed;
    } catch (e) {
      console.log(`the record for ${known.name} names a file Drive no longer has: ${e}`); // deleted: search below
    }
    if (trashed !== null) return result(known.id, known.name, known.edits.length + (known.more || 0), trashed);
  }
  const search = (trashed: boolean) => {
    const listed = drive_().Files.list({
      q: `appProperties has { key='${REQUEST_PROPERTY}' and value='${request}' } and trashed = ${trashed}`,
      pageSize: 1,
      fields: "files(id,name,appProperties)",
    });
    return ((listed.files || []) as { id?: string; name?: string; appProperties?: Record<string, string> }[])[0];
  };
  const live = search(false);
  const file = live || search(true);
  if (!file) return null;
  return result(String(file.id), String(file.name), Number((file.appProperties || {}).household_edits) || 0, !live);
}

/**
 * Save: one change set file with these edits, written into the views' folder. described holds each edit in
 * words, kept for the waiting list. request is the browser's id for this Save: a call with a request that
 * already saved returns that file and writes nothing, so a Save whose answer was lost is never written twice.
 * Once the file is in Drive the save has happened: keeping its record is best effort, and a failure there
 * comes back as a warning, never as "not saved".
 */
function saveChanges(edits: Edit[], described: string[], request?: string): SaveResult {
  const lock = LockService.getUserLock();
  lock.waitLock(20000);
  try {
    const req = requestId(request);
    const before = req ? savedFor_(req) : null;
    if (before && !before.discarded) {
      console.log(`request ${req} saved already, as ${before.name}: nothing new written`);
      return before;
    }
    const props = PropertiesService.getUserProperties();
    const base = latestComputeRun(readTabIfAny_("run_log"));
    const now = laterThan(new Date().toISOString(), props.getProperty("LAST_SAVED_AT") || ""); // strictly in order
    const file = changeSet(edits, now, base, Utilities.getUuid().slice(0, 8));
    const folder = editsFolder_(true) as { id: string; made: boolean };
    const meta: Record<string, unknown> = { name: file.name, mimeType: "application/json", parents: [folder.id] };
    if (req) meta.appProperties = { [REQUEST_PROPERTY]: req, household_edits: String(edits.length) };
    const made = drive_().Files.create(meta, Utilities.newBlob(file.text, "application/json", file.name));
    const id = String(made.id);
    console.log(`saved ${file.name} (${id}): ${edits.length} edits on run ${base || "(none)"}, request ${req || "(none)"}`);
    let warning = "";
    try {
      props.setProperty("LAST_SAVED_AT", now);
      keepSaved_(id, savedRecord(id, file.name, now, described || [], req));
    } catch (e) {
      warning = "Its list of edits for the Saved tab could not be kept (" + e + "); the change set itself is saved.";
      console.log(`saved ${file.name}, but not its record: ${e}`);
    }
    return { id, name: file.name, count: edits.length, madeFolder: folder.made, warning, again: false, discarded: false };
  } finally {
    lock.releaseLock();
  }
}

/**
 * Whether the Save with this request wrote its change set: the file (marked discarded if it is in the trash
 * since), or null when it did not. Asked after a Save whose answer never came. It waits for any Save still running, so a slow one is not missed.
 */
function findSave(request: string): SaveResult | null {
  const req = requestId(request);
  if (!req) throw new Error("Not a request id: " + JSON.stringify(request));
  const lock = LockService.getUserLock();
  lock.waitLock(20000);
  try {
    const found = savedFor_(req);
    console.log(
      `request ${req}: ${found ? (found.discarded ? "saved as " + found.name + ", since discarded" : "saved as " + found.name) : "not saved"}`,
    );
    return found;
  } finally {
    lock.releaseLock();
  }
}

/** Change sets saved but not yet read by ingest, and those ingest refused, with the reason. */
function getWaiting(): Waiting[] {
  const folder = editsFolder_(false);
  if (!folder) return [];
  const files: { id: string; name: string; createdTime: string }[] = [];
  let pageToken: string | undefined;
  do {
    // every page: Drive hands back at most a page of files at a time
    const listed = drive_().Files.list({
      q: `'${folder.id}' in parents and trashed = false`,
      pageSize: 200,
      pageToken,
      fields: "nextPageToken,files(id,name,createdTime)",
    });
    for (const f of (listed.files || []) as { id?: string; name?: string; createdTime?: string }[]) {
      files.push({ id: String(f.id), name: String(f.name), createdTime: String(f.createdTime || "") });
    }
    pageToken = listed.nextPageToken || undefined;
  } while (pageToken);
  const saved = saved_();
  const here = new Set(files.map((f) => f.id));
  try {
    // a record for a file ingest has filed (or one discarded) is no longer needed
    const props = PropertiesService.getUserProperties();
    for (const id of Object.keys(saved)) if (!here.has(id)) props.deleteProperty(SAVED_PREFIX + id);
  } catch (e) {
    console.log("could not tidy saved records: " + e);
  }
  return waitingChangeSets(files, saved, readTabIfAny_("run_log"));
}

/**
 * The edits of every change set still waiting (saved, not yet taken in by ingest), oldest first, so the page can
 * show what was saved until a run applies it. A change set ingest refused is left out: it will not apply as it is.
 */
function getWaitingEdits(): WaitingEdits[] {
  const out: WaitingEdits[] = [];
  const waiting = getWaiting()
    .filter((w) => !w.refused)
    .sort((a, b) => a.savedAt.localeCompare(b.savedAt));
  for (const w of waiting) {
    try {
      const set = JSON.parse(fileText_(w.id)) as { edits?: Edit[] };
      const edits = Array.isArray(set.edits) ? set.edits : [];
      out.push({ id: w.id, savedAt: w.savedAt, edits, texts: edits.map((_, n) => w.edits[n] || "") });
    } catch (e) {
      console.log(`waiting change set ${w.name} could not be read: ${e}`);
    }
  }
  return out;
}

/** A file's text, through the Drive advanced service (it hands back a string, or the bytes). */
function fileText_(id: string): string {
  const got = drive_().Files.get(id, { alt: "media" }) as unknown;
  if (typeof got === "string") return got;
  if (Array.isArray(got)) return Utilities.newBlob(got as number[]).getDataAsString();
  throw new Error("Drive gave no text for " + id);
}

const DISCARDED_PREFIX = "DISCARDED_"; // one user property per discarded change set, its edits in words
const DISCARDED_KEEP = 20;
const DISCARDED_DAYS = 30; // long enough for the next ingest to say whether it had read the file already

/**
 * Move one of the views' own change sets to the trash (a refused one, or one saved by mistake). No ingest
 * from then on reads it, but one already running may have read it first and will still apply it; the
 * discard is remembered, so the status page can say so (getAppliedAnyway) once the ledger holds it; when it
 * can't be remembered, the answer says so.
 */
function discardWaiting(id: string): { remembered: boolean; warning: string } {
  const folder = editsFolder_(false);
  if (!folder) throw new Error("No change sets saved yet.");
  const file = drive_().Files.get(id, { fields: "id,name,parents" });
  if ((file.parents || []).indexOf(folder.id) < 0) throw new Error("That file is not waiting in the views' folder.");
  const known = saved_()[id];
  drive_().Files.update({ trashed: true }, id);
  console.log(`discarded ${file.name}`);
  try {
    const props = PropertiesService.getUserProperties();
    const record = JSON.parse(
      savedRecord(id, String(file.name), known ? known.savedAt : "", known ? known.edits : []),
    ) as Discarded;
    if (known && known.more) record.more = (record.more || 0) + known.more;
    record.discardedAt = new Date().toISOString();
    props.setProperty(DISCARDED_PREFIX + id, JSON.stringify(record));
    const older = Object.values(discarded_())
      .sort((a, b) => b.discardedAt.localeCompare(a.discardedAt))
      .slice(DISCARDED_KEEP);
    for (const d of older) props.deleteProperty(DISCARDED_PREFIX + d.id); // the newest DISCARDED_KEEP stay
  } catch (e) {
    console.log(`discarded ${file.name}, but could not remember it: ${e}`);
    return {
      remembered: false,
      warning:
        `The views could not remember this discard (${e}), so the status page can't ` +
        `tell you if an ingest already running wrote it: after the next ingest, look for ${file.name} in its file list.`,
    };
  }
  return { remembered: true, warning: "" };
}

/** Discarded change sets still remembered, by file id; older than DISCARDED_DAYS, or unreadable, are dropped. */
function discarded_(): Record<string, Discarded> {
  const props = PropertiesService.getUserProperties();
  const all = props.getProperties();
  const out: Record<string, Discarded> = {};
  const oldest = Date.now() - DISCARDED_DAYS * 24 * 60 * 60 * 1000;
  for (const key of Object.keys(all)) {
    if (key.indexOf(DISCARDED_PREFIX) !== 0) continue;
    try {
      const record = JSON.parse(all[key]) as Discarded;
      if (Date.parse(record.discardedAt) >= oldest) {
        out[record.id] = record;
        continue;
      }
    } catch (e) {
      // unreadable: dropped below
    }
    try {
      props.deleteProperty(key);
    } catch (e) {
      /* tidied next time */
    }
  }
  return out;
}

/** Discarded change sets an ingest applied anyway (appliedAnyway in edits.ts). */
function getAppliedAnyway(): AppliedAnyway[] {
  return appliedAnyway(discarded_(), readTabIfAny_("changes_written"));
}

/** Stop telling about a discarded change set an ingest applied anyway, once its settings are seen to. */
function forgetDiscarded(id: string): void {
  PropertiesService.getUserProperties().deleteProperty(DISCARDED_PREFIX + String(id));
}

/** One transaction, whole, for the transaction page (null when Reports has no such line). */
function getTransaction(id: string): Line | null {
  return findLine(readTab_("categorized"), String(id || "").trim());
}

/** The open lines a review question covers, to answer them one by one (shape.ts reviewLines). */
function getReviewLines(key: string): ReviewLine[] {
  return reviewLines(readTab_("categorized"), String(key || "").trim());
}

/** Everything the tax page shows (taxes.ts). */
function getTaxes(): TaxPage {
  const tabs = readTabsIfAny_([
    "tax_lines",
    "projection",
    "withholding_plan",
    "elections",
    "run_log",
    "run_notes",
    "tax_history",
    "filed_lines",
  ]);
  return taxPage(tabs);
}

/** The Taxes page's Form 8949 part (form8949.ts). */
function getForm8949(): F8949Page {
  return form8949Page(readTabsIfAny_(["form_8949", "filed_lines"]));
}

function getInvestments(): WealthPage {
  const tabs = readTabsIfAny_([
    "investment_values",
    "net_worth",
    "allocation",
    "rebalance",
    "open_lots",
    "form_8949",
    "run_log",
    "run_notes",
  ]);
  return wealthPage(tabs);
}

function getHome(): HomePage {
  const tabs = readTabsIfAny_([
    "property_summary",
    "home_values",
    "home_improvements",
    "home_taxes",
    "mortgage",
    "loan_details",
    "run_log",
    "run_notes",
  ]);
  return homePage(tabs, Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM"));
}

/** Everything the Retirement page shows (retirement.ts). */
function getRetirement(): RetirementPage {
  const tabs = readTabsIfAny_([
    "retirement_years",
    "retirement_strategies",
    "retirement_plan",
    "retirement_social_security",
    "retirement_earnings",
    "retirement_assumptions",
    "retirement_budget",
    "run_log",
    "run_notes",
  ]);
  return retirementPage(tabs);
}
