/**
 * The county check: once a month, reads each home's parcel page on Dane County's AccessDane and drops what
 * is new into the household inbox, for ingest: each year's assessment and the county's estimate of fair
 * market value (a property_values file), and each tax year's bill with its mill rate, credits and specials
 * (a property_taxes file). Ingest is still the only writer of the ledger; this script only adds files.
 *
 * Which parcels: compute writes them into the description of this script's parcels file (from the ledger's
 * properties: each home owned today with a parcel number, and the year it was bought, so older years are
 * left out). Nothing about the household is kept in this script.
 *
 * Drive access is drive.file: the script can see and change only what it created. So it makes its own folder
 * ("county_assessments", in My Drive) on the first run; move that folder into the household inbox folder once,
 * and the engine reads it there. Its network access is limited to AccessDane by the manifest's
 * urlFetchWhitelist: the parcel numbers it sends there are public records. It reads no spreadsheet and no
 * other file, and sends no mail.
 *
 * It keeps its own state in the script properties: FOLDER_ID (its folder), PARCELS_ID (its parcels file) and
 * SENT (the keys of rows already written, so a month with nothing new writes nothing).
 *
 * Run monthly once by hand (it asks for permission and makes its folder and parcels file), move the folder
 * into the inbox, run compute (it fills the parcels file), run monthly again, then installTrigger once.
 */

const SITE = "https://accessdane.danecounty.gov/";
const FOLDER_NAME = "county_assessments";
const FOLDER_MIME = "application/vnd.google-apps.folder";
const PARCELS_NAME = "parcels (written by compute).json";
const PARCELS_PROPERTY = "household";  // its value marks the file, so ingest skips it and compute finds it
const VALUE_HEADER = ["as_of", "property_id", "source", "value", "note", "doc_id"];
const TAX_HEADER = ["tax_year", "property_id", "assessed", "mill_rate", "total_taxes", "lottery_credit",
                    "first_dollar_credit", "school_levy_credit", "specials", "net_taxes", "amount_due", "doc_id"];
// the assessment table's rows (a column per year) and the tax bill's lines (one table per tax year)
const ASSESSED = { "Land Value": "land", "Improved Value": "improved", "Total Value": "total",
                   "Estimated Fair Market Value": "fmv" };
const BILL = { "Net Assessed Value Rate (mill rate)": "mill_rate", "School Levy Tax Credit": "school_levy_credit",
               "Total Taxes": "total_taxes", "LOTTERY CREDIT": "lottery_credit",
               "FIRST DOLLAR CREDIT": "first_dollar_credit", "Specials": "specials",
               "Total Taxes Less Credits": "net_taxes", "Total Amount Due": "amount_due" };

/** The page's text as a list of its pieces in reading order: each cell, heading and label apart. */
function pieces(html) {
  return String(html)
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]*>/g, "\n")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&#39;|&#x27;/g, "'").replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .split("\n").map((t) => t.replace(/\s+/g, " ").trim()).filter((t) => t);
}

/** A money or rate text as a number, null for "N/A", undefined for anything else. */
function amount(text) {
  const t = String(text).trim();
  if (/^n\/a$/i.test(t)) return null;
  const m = /^\(?-?\$?\s*([0-9][0-9,]*(\.[0-9]+)?|\.[0-9]+)\)?$/.exec(t);
  if (!m) return undefined;
  const n = Number(m[1].replace(/,/g, ""));
  return /^\(|^-/.test(t) ? -n : n;
}

/**
 * {assessments: {year: {land, improved, total, fmv}}, bills: {year: {mill_rate, ...}}} from a parcel page.
 * An assessment table starts at "Assessment Year" and its years; each row it names then gives one value per
 * year. A tax bill starts at "Tax Year <year>"; each line it names takes the first number after it.
 */
function parsePage(html) {
  const t = pieces(html);
  const assessments = {}, bills = {};
  let years = [], bill = null;
  for (let i = 0; i < t.length; i++) {
    const label = t[i].replace(/:$/, "").trim();
    if (label === "Assessment Year") {
      years = [];
      for (let j = i + 1; j < t.length && /^\d{4}$/.test(t[j]); j++) years.push(t[j]);
      continue;
    }
    const year = /^Tax Year (\d{4})$/.exec(label);
    if (year) { bill = bills[year[1]] = bills[year[1]] || {}; continue; }
    if (ASSESSED[label] && years.length) {
      const got = t.slice(i + 1, i + 1 + years.length).map(amount);
      if (got.every((v) => v !== undefined)) {
        years.forEach((y, n) => { (assessments[y] = assessments[y] || {})[ASSESSED[label]] = got[n]; });
        i += years.length;
      }
      continue;
    }
    if (BILL[label] && bill && bill[BILL[label]] === undefined) {
      for (let j = i + 1; j < Math.min(t.length, i + 4); j++) {
        const v = amount(t[j]);
        if (v !== undefined && v !== null) { bill[BILL[label]] = v; break; }
      }
    }
  }
  return { assessments, bills };
}

/** The page's address on AccessDane: the parcel number's digits after its municipality code. */
function pageUrl(parcel) {
  const digits = String(parcel).split("/").pop().replace(/\D/g, "");
  if (digits.length !== 12) throw new Error("not a Dane County parcel number: " + parcel);
  return SITE + digits;
}

/** The rows a page gives for a home from a year on: {values, taxes}, each a list of rows by header. */
function rowsFor(home, page) {
  const values = [], taxes = [];
  const since = String(home.since || "0000");
  const source = "AccessDane " + home.parcel;
  Object.keys(page.assessments).sort().forEach((y) => {
    if (y < since) return;
    const a = page.assessments[y];
    if (typeof a.total === "number") {
      values.push({ as_of: y + "-01-01", property_id: home.property_id, source: "county_assessed", value: a.total,
                    note: source + ": land " + (a.land ?? "") + " and improvements " + (a.improved ?? ""), doc_id: "" });
    }
    if (typeof a.fmv === "number") {
      values.push({ as_of: y + "-01-01", property_id: home.property_id, source: "county_fair_market", value: a.fmv,
                    note: source + ": the assessment at the year's assessment ratio", doc_id: "" });
    }
  });
  Object.keys(page.bills).sort().forEach((y) => {
    const b = page.bills[y];
    if (y < since || typeof b.total_taxes !== "number") return;
    const total = (page.assessments[y] || {}).total;
    taxes.push(Object.assign({ tax_year: y, property_id: home.property_id, assessed: typeof total === "number" ? total : "",
                               doc_id: "" }, b));
  });
  return { values, taxes };
}

/** The key a row is kept under in SENT: what ingest keys it on. */
function keyOf(row) {
  return row.tax_year ? ["tax", row.tax_year, row.property_id].join("|") : ["value", row.as_of, row.property_id, row.source].join("|");
}

function csv(header, rows) {
  const cell = (v) => {
    const s = v === undefined || v === null ? "" : String(v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  return [header.join(",")].concat(rows.map((r) => header.map((h) => cell(r[h])).join(","))).join("\n") + "\n";
}

/** The parcels compute wrote: [{property_id, parcel, since}]. */
function readParcels(description) {
  let got;
  try {
    got = JSON.parse(description || "{}");
  } catch (e) {
    throw new Error("The parcels file's description isn't JSON: run compute to write it again. " + e);
  }
  return (got && got.version === 1 && Array.isArray(got.parcels)) ? got.parcels : [];
}

// --- Apps Script services -----------------------------------------------------------------------------

function log_(text) { console.log(text); }

/** This script's own folder: made on the first run, then the same one wherever it is moved. */
function folder_(props) {
  const id = props.getProperty("FOLDER_ID");
  if (id) {
    const found = Drive.Files.get(id, { fields: "id,trashed" });
    if (found.trashed) throw new Error("The county_assessments folder is in the trash. Restore it, or delete FOLDER_ID.");
    return id;
  }
  const made = Drive.Files.create({ name: FOLDER_NAME, mimeType: FOLDER_MIME });
  props.setProperty("FOLDER_ID", made.id);
  log_("made the folder '" + FOLDER_NAME + "' in My Drive: move it into the household inbox folder");
  return made.id;
}

/** The parcels file: made on the first run in the folder, then read from its description. */
function parcels_(props, folderId) {
  const id = props.getProperty("PARCELS_ID");
  if (id) return readParcels(Drive.Files.get(id, { fields: "description" }).description);
  const made = Drive.Files.create({ name: PARCELS_NAME, mimeType: "application/json", parents: [folderId],
                                    properties: { [PARCELS_PROPERTY]: "parcels" } },
                                  Utilities.newBlob("{}", "application/json"));
  props.setProperty("PARCELS_ID", made.id);
  log_("made the parcels file: run compute, which fills it, then run monthly again");
  return [];
}

/** The trigger's function: write what is new on each home's parcel page into the folder. */
function monthly() {
  const props = PropertiesService.getScriptProperties();
  const folderId = folder_(props);
  const homes = parcels_(props, folderId);
  if (!homes.length) { log_("no parcels yet: run compute"); return; }
  const sent = JSON.parse(props.getProperty("SENT") || "{}");
  const values = [], taxes = [];
  for (const home of homes) {
    const response = UrlFetchApp.fetch(pageUrl(home.parcel), { muteHttpExceptions: true, followRedirects: true });
    if (response.getResponseCode() !== 200) {
      throw new Error("AccessDane answered " + response.getResponseCode() + " for " + home.parcel);
    }
    const page = parsePage(response.getContentText());
    if (!Object.keys(page.assessments).length) throw new Error("No assessment table found for " + home.parcel + ": has the page changed?");
    const got = rowsFor(home, page);
    got.values.filter((r) => !sent[keyOf(r)]).forEach((r) => values.push(r));
    got.taxes.filter((r) => !sent[keyOf(r)]).forEach((r) => taxes.push(r));
  }
  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd");
  const write = (name, header, rows) => {
    if (!rows.length) return;
    Drive.Files.create({ name: name, mimeType: "text/csv", parents: [folderId] }, Utilities.newBlob(csv(header, rows), "text/csv"));
    rows.forEach((r) => { sent[keyOf(r)] = today; });
    log_("wrote " + name + ": " + rows.length + " rows");
  };
  write("property_values_county_" + today + ".csv", VALUE_HEADER, values);
  write("property_taxes_county_" + today + ".csv", TAX_HEADER, taxes);
  props.setProperty("SENT", JSON.stringify(sent));
  if (!values.length && !taxes.length) log_("nothing new on " + homes.length + " parcel page(s)");
}

/** Run monthly on the 3rd of each month at 6 in the morning (the project's time zone); run once. */
function installTrigger() {
  for (const t of ScriptApp.getProjectTriggers()) {
    if (t.getHandlerFunction() === "monthly") ScriptApp.deleteTrigger(t);
  }
  ScriptApp.newTrigger("monthly").timeBased().onMonthDay(3).atHour(6).create();
  log_("monthly runs on the 3rd of each month at 6");
}
