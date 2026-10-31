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

function readFlows_(): FlowRow[] {
  const sheet = SpreadsheetApp.openById(reportsId_()).getSheetByName("flows");
  if (!sheet) throw new Error("The Reports spreadsheet has no flows tab yet: run compute first.");
  return parseRows(sheet.getDataRange().getValues());
}

/** Periods with flows, newest first. */
function getPeriods(): string[] {
  return periods(readFlows_());
}

/** Sankey rows for one period ("2026" or "2026-10") at one level ("category" or "group"). */
function getFlows(period: string, level: string): { rows: SankeyRow[]; dropped: SankeyRow[] } {
  return toSankeyRows(readFlows_(), period, level);
}
