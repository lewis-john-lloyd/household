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
 * The flows tab, read through the Sheets API (the advanced service in appsscript.json): it works under
 * the read-only spreadsheets scope, where SpreadsheetApp.openById demands full read and write access.
 */
function readTab_(name: string): unknown[][] {
  if (!Sheets || !Sheets.Spreadsheets || !Sheets.Spreadsheets.Values) {
    throw new Error("The Sheets advanced service is not enabled: push appsscript.json again.");
  }
  let values: unknown[][] | undefined;
  try {
    values = Sheets.Spreadsheets.Values.get(reportsId_(), name, { valueRenderOption: "UNFORMATTED_VALUE" })
      .values as unknown[][] | undefined;
  } catch (e) {
    throw new Error(`Could not read the ${name} tab of Reports (run compute first, and check REPORTS_ID): ` + e);
  }
  if (!values || !values.length) throw new Error(`The Reports ${name} tab is empty: run compute first.`);
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
function getFlows(period: string, level: string): { rows: SankeyRow[]; dropped: SankeyRow[]; totals: NodeTotal[] } {
  const { rows, dropped } = toSankeyRows(readFlows_(), period, level);
  return { rows, dropped, totals: nodeTotals(rows) };
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

/** A Reports tab's values, or [] when it is missing or empty (the status tabs start with the first run that writes them). */
function readTabIfAny_(name: string): unknown[][] {
  try {
    return readTab_(name);
  } catch (e) {
    return [];
  }
}

/** The latest ingest and compute: their summaries, every line they printed, and what needs the owner. */
function getStatus(): Status {
  return parseStatus(readTabIfAny_("run_log"), readTabIfAny_("run_notes"));
}

// --- editing ------------------------------------------------------------------------------------------
//
// The views write one kind of thing: a change set file, into a Drive folder of their own (drive.file: the
// app sees only files it made). The folder is made in My Drive on the first save; move it into the household
// inbox once, and ingest reads it there (parsers/changes.py). Its id, and what each saved file held, are kept
// in this person's user properties.

const EDITS_FOLDER_NAME = "web_edits";
const FOLDER_MIME_TYPE = "application/vnd.google-apps.folder";
const SAVED_KEEP = 50;  // saved change sets remembered for the waiting list

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

function saved_(): Record<string, Saved> {
  try {
    return JSON.parse(PropertiesService.getUserProperties().getProperty("SAVED_CHANGES") || "{}") as Record<string, Saved>;
  } catch (e) {
    return {};
  }
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
function saveChanges(edits: Edit[], described: string[]): { id: string; name: string; count: number; madeFolder: boolean } {
  const lock = LockService.getUserLock();
  lock.waitLock(20000);
  try {
    const base = latestComputeRun(readTabIfAny_("run_log"));
    const now = new Date().toISOString();
    const file = changeSet(edits, now, base);
    const folder = editsFolder_(true) as { id: string; made: boolean };
    const made = drive_().Files.create({ name: file.name, mimeType: "application/json", parents: [folder.id] },
                                       Utilities.newBlob(file.text, "application/json", file.name));
    const saved = saved_();
    saved[String(made.id)] = { id: String(made.id), name: file.name, savedAt: now, edits: (described || []).slice(0, MAX_CHANGE_EDITS) };
    const keep = Object.values(saved).sort((a, b) => b.savedAt.localeCompare(a.savedAt)).slice(0, SAVED_KEEP);
    PropertiesService.getUserProperties().setProperty("SAVED_CHANGES", JSON.stringify(
      Object.fromEntries(keep.map((s) => [s.id, s]))));
    console.log(`saved ${file.name}: ${edits.length} edits on run ${base || "(none)"}`);
    return { id: String(made.id), name: file.name, count: edits.length, madeFolder: folder.made };
  } finally {
    lock.releaseLock();
  }
}

/** Change sets saved but not yet read by ingest, and those ingest refused, with the reason. */
function getWaiting(): Waiting[] {
  const folder = editsFolder_(false);
  if (!folder) return [];
  const listed = drive_().Files.list({ q: `'${folder.id}' in parents and trashed = false`,
                                       fields: "files(id,name,createdTime)", pageSize: 200 });
  const files = (listed.files || []).map((f: { id?: string; name?: string; createdTime?: string }) => ({ id: String(f.id), name: String(f.name), createdTime: String(f.createdTime || "") }));
  return waitingChangeSets(files, saved_(), readTabIfAny_("run_log"));
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
