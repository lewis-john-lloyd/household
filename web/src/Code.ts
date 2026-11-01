/**
 * The views web app: serves Index.html and answers its calls. It runs as the
 * person viewing it, so it reads only what that person can open; the Reports
 * spreadsheet's id comes from the script property REPORTS_ID, never from code.
 */

function doGet(): GoogleAppsScript.HTML.HtmlOutput {
  return HtmlService.createHtmlOutputFromFile("Index").setTitle("Household flows");
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
function readFlows_(): FlowRow[] {
  if (!Sheets || !Sheets.Spreadsheets || !Sheets.Spreadsheets.Values) {
    throw new Error("The Sheets advanced service is not enabled: push appsscript.json again.");
  }
  let values: unknown[][] | undefined;
  try {
    values = Sheets.Spreadsheets.Values.get(reportsId_(), "flows", { valueRenderOption: "UNFORMATTED_VALUE" })
      .values as unknown[][] | undefined;
  } catch (e) {
    throw new Error("Could not read the flows tab of Reports (run compute first, and check REPORTS_ID): " + e);
  }
  if (!values || !values.length) throw new Error("The Reports flows tab is empty: run compute first.");
  return parseRows(values);
}

/** Periods with flows, newest first. */
function getPeriods(): string[] {
  return periods(readFlows_());
}

/** Sankey rows for one period ("2026" or "2026-10") at one level ("category" or "group"). */
function getFlows(period: string, level: string): { rows: SankeyRow[]; dropped: SankeyRow[] } {
  return toSankeyRows(readFlows_(), period, level);
}
