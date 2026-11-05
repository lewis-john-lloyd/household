/**
 * The views web app: serves Index.html and answers its calls. It runs as the
 * person viewing it, so it reads only what that person can open; the Reports
 * spreadsheet's id comes from the script property REPORTS_ID, never from code.
 * It reads Reports and writes only change sets, into a folder of its own (see
 * the editing section below and parsers/changes.py).
 */

function doGet(): GoogleAppsScript.HTML.HtmlOutput {
  return HtmlService.createHtmlOutputFromFile("Index").setTitle("Household views");
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
  try {
    return (Sheets.Spreadsheets.Values.get(reportsId_(), name, { valueRenderOption: "UNFORMATTED_VALUE" })
      .values as unknown[][] | undefined) || [];
  } catch (e) {
    throw new Error(`Could not read the ${name} tab of Reports (check REPORTS_ID and that you can open Reports): ` + e);
  }
}

let tabTitles: string[] | null = null;  // Reports' tab names, read once per call from the page

/** The names of Reports' tabs, so a tab not made yet can be told from a tab that could not be read. */
function tabTitles_(): string[] {
  if (tabTitles === null) {
    if (!Sheets || !Sheets.Spreadsheets) throw new Error("The Sheets advanced service is not enabled: push appsscript.json again.");
    let sheets: { properties?: { title?: string } }[] | undefined;
    try {
      sheets = Sheets.Spreadsheets.get(reportsId_(), { fields: "sheets.properties.title" }).sheets;
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
function getFlows(period: string, level: string): { rows: SankeyRow[]; dropped: SankeyRow[]; totals: NodeTotal[]; summary: FlowSummary } {
  const { rows, dropped } = toSankeyRows(readFlows_(), period, level);
  const totals = nodeTotals(rows);
  return { rows, dropped, totals, summary: flowSummary(totals) };
}

/** Months and years with budget rows, newest first. */
function getBudgetPeriods(): string[] {
  return budgetPeriods(parseBudget(readTab_("budget_vs_actual")));
}

/** Budget against actuals for one month or year ("2026-10", "2026") by category or group. */
function getBudget(period: string, level: string): BudgetLine[] {
  return budgetLines(parseBudget(readTab_("budget_vs_actual")), period, level);
}

/** The transactions behind one budget line: that period's, in those categories. */
function getTransactions(period: string, categories: string[]): Txn[] {
  return transactionsFor(parseTransactions(readTab_("categorized")), period, categories);
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
  return parseStatus(readTabIfAny_("run_log"), readTabIfAny_("run_notes"), readTabIfAny_("coverage"),
                     readTabIfAny_("editor_review"));
}

// --- editing ------------------------------------------------------------------------------------------
//
// The views write one kind of thing: a change set file, into a Drive folder of their own (drive.file: the
// app sees only files it made). The folder is made in My Drive on the first save; move it into the household
// inbox once, and ingest reads it there (parsers/changes.py). Its id, and what each saved file held, are kept
// in this person's user properties.

const EDITS_FOLDER_NAME = "web_edits";
const FOLDER_MIME_TYPE = "application/vnd.google-apps.folder";
const SAVED_KEEP = 40;  // saved change sets remembered for the waiting list: 40 records of 8 KB stay well inside 500 KB
const SAVED_PREFIX = "SAVED_";  // one user property per saved change set, its record (savedRecord, under 9 KB)

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
  const old = Object.values(saved_()).sort((a, b) => b.savedAt.localeCompare(a.savedAt)).slice(SAVED_KEEP);
  for (const s of old) props.deleteProperty(SAVED_PREFIX + s.id);
}

/** Everything the editors show: the settings in force, the review queue, duplicates and unexplained stubs. */
function getEditor(): EditorData {
  const names = ["run_log", "editor_budget_lines", "editor_payee_rules", "editor_categories", "editor_aliases",
                 "editor_parameters", "editor_review", "possible_duplicates", "withholding_backtest",
                 "wi_withholding_backtest"];
  const tabs: Record<string, unknown[][]> = {};
  for (const name of names) tabs[name] = readTabIfAny_(name);
  const data = editorData(tabs);
  console.log(`editor: run ${data.baseRun}, ${data.budgetLines.length} budget lines, ${data.review.length} review questions`);
  return data;
}

/**
 * Save: one change set file with these edits, written into the views' folder. described holds each edit
 * in words, kept for the waiting list. Returns the file and whether the folder was just made.
 */
/**
 * Save: one change set file with these edits, written into the views' folder. described holds each edit in
 * words, kept for the waiting list. Once the file is in Drive the save has happened: keeping its record is
 * best effort, and a failure there comes back as a warning, never as "not saved" (a retry would write the
 * change set twice).
 */
function saveChanges(edits: Edit[], described: string[]): { id: string; name: string; count: number; madeFolder: boolean; warning: string } {
  const lock = LockService.getUserLock();
  lock.waitLock(20000);
  try {
    const props = PropertiesService.getUserProperties();
    const base = latestComputeRun(readTabIfAny_("run_log"));
    const now = laterThan(new Date().toISOString(), props.getProperty("LAST_SAVED_AT") || "");  // strictly in order
    const file = changeSet(edits, now, base, Utilities.getUuid().slice(0, 8));
    const folder = editsFolder_(true) as { id: string; made: boolean };
    const made = drive_().Files.create({ name: file.name, mimeType: "application/json", parents: [folder.id] },
                                       Utilities.newBlob(file.text, "application/json", file.name));
    const id = String(made.id);
    console.log(`saved ${file.name} (${id}): ${edits.length} edits on run ${base || "(none)"}`);
    let warning = "";
    try {
      props.setProperty("LAST_SAVED_AT", now);
      keepSaved_(id, savedRecord(id, file.name, now, described || []));
    } catch (e) {
      warning = "Its list of edits for the Saved tab could not be kept (" + e + "); the change set itself is saved.";
      console.log(`saved ${file.name}, but not its record: ${e}`);
    }
    return { id, name: file.name, count: edits.length, madeFolder: folder.made, warning };
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
  do {  // every page: Drive hands back at most a page of files at a time
    const listed = drive_().Files.list({ q: `'${folder.id}' in parents and trashed = false`, pageSize: 200, pageToken,
                                         fields: "nextPageToken,files(id,name,createdTime)" });
    for (const f of (listed.files || []) as { id?: string; name?: string; createdTime?: string }[]) {
      files.push({ id: String(f.id), name: String(f.name), createdTime: String(f.createdTime || "") });
    }
    pageToken = listed.nextPageToken || undefined;
  } while (pageToken);
  const saved = saved_();
  const here = new Set(files.map((f) => f.id));
  try {  // a record for a file ingest has filed (or one discarded) is no longer needed
    const props = PropertiesService.getUserProperties();
    for (const id of Object.keys(saved)) if (!here.has(id)) props.deleteProperty(SAVED_PREFIX + id);
  } catch (e) {
    console.log("could not tidy saved records: " + e);
  }
  return waitingChangeSets(files, saved, readTabIfAny_("run_log"));
}

/** Move one of the views' own change sets to the trash (a refused one, or one saved by mistake). */
function discardWaiting(id: string): void {
  const folder = editsFolder_(false);
  if (!folder) throw new Error("No change sets saved yet.");
  const file = drive_().Files.get(id, { fields: "id,name,parents" });
  if ((file.parents || []).indexOf(folder.id) < 0) throw new Error("That file is not waiting in the views' folder.");
  drive_().Files.update({ trashed: true }, id);
  console.log(`discarded ${file.name}`);
}

/** One transaction, whole, for the transaction page (null when Reports has no such line). */
function getTransaction(id: string): Line | null {
  return findLine(readTab_("categorized"), String(id || "").trim());
}

/** Everything the tax page shows (taxes.ts). */
function getTaxes(): TaxPage {
  const tabs: Record<string, unknown[][]> = {};
  for (const name of ["tax_lines", "projection", "withholding_plan", "elections", "run_log", "run_notes"]) {
    tabs[name] = readTabIfAny_(name);
  }
  return taxPage(tabs);
}
