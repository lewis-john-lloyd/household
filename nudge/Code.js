/**
 * The weekly email: every Saturday morning, one email listing what needs doing in the household books, or
 * no email when nothing does.
 *
 * Compute works out the list and publishes it to Reports as the nudges table (src/household/nudges.py):
 * each thing to do, under the action it calls for, with the dates between which it belongs in the email.
 * This script reads that table, keeps the rows whose dates take in today, and adds what only it can see
 * live: files that have sat in the inbox a month, files the last ingest refused, and an ingest a month old
 * that compute never followed. A thing goes in the email only when there is something to do about it.
 *
 * It reads; it never writes. Its permissions: Reports read-only (spreadsheets.readonly), the names and dates
 * of Drive files (drive.metadata.readonly, to see what waits in the inbox, never its contents), sending mail
 * as you (script.send_mail, only to your own address), and its own trigger (script.scriptapp).
 *
 * Script property, set once by hand in Project Settings: REPORTS_ID, the Reports spreadsheet's id (the
 * views use the same one). Everything else comes from Reports: the inbox's folder from the last ingest, and
 * the links from the household settings (views_url, payroll_url, benefits_url, fsa_claims_url).
 *
 * Run preview once by hand (it asks for permission, then logs the email it would send), then installTrigger
 * once to send it every Saturday at 7 in the morning, in the project's time zone.
 */

const STALE_DAYS = 30;  // as nudges.py: a thing is worth an email once it has been stale this long
const DAY_MS = 24 * 60 * 60 * 1000;
const FOLDER_MIME = "application/vnd.google-apps.folder";
const GROUP_ORDER = [  // the email's sections in order; nudges.py names the ones that come from Reports
  "Fix a setting",
  "Claim FSA reimbursements",
  "Change before the year's last paychecks",
  "Fix or remove in the inbox",
  "Run ingest, then compute",
  "Run compute",
  "Download into the inbox",
  "Answer in Review",
  "Check the weekly email",
];

/** A tab's values (header first) as rows of text by column name; blank rows skipped. */
function records(values) {
  if (!values || !values.length) return [];
  const header = values[0].map((h) => String(h).trim());
  const out = [];
  for (const v of values.slice(1)) {
    const row = {};
    header.forEach((h, i) => { if (h) row[h] = String(v[i] === undefined || v[i] === null ? "" : v[i]).trim(); });
    if (Object.values(row).some((x) => x !== "")) out.push(row);
  }
  return out;
}

/** "2026-11-07" from a date or an ISO time. */
function day(text) {
  return String(text || "").slice(0, 10);
}

/** The day STALE_DAYS before today. */
function staleBefore(today) {
  return new Date(Date.parse(today + "T00:00:00Z") - STALE_DAYS * DAY_MS).toISOString().slice(0, 10);
}

function longDate(text) {
  const d = new Date(day(text) + "T00:00:00Z");
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                  "November", "December"];
  return months[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + d.getUTCFullYear();
}

/** The nudges compute published whose dates take in today. */
function reportItems(nudges, today) {
  return nudges.filter((n) => n.show_from && n.show_from <= today && (!n.show_until || n.show_until >= today))
    .map((n) => ({ group: n.group, item: n.item, detail: n.detail, link: n.link || "" }));
}

/** The latest run_log row for a stage, or null. */
function lastRun(runLog, stage) {
  const runs = runLog.filter((r) => r.stage === stage).sort((a, b) => a.computed_at.localeCompare(b.computed_at));
  return runs.length ? runs[runs.length - 1] : null;
}

/** A run_log row's detail, or {} when it isn't JSON. */
function detailOf(run) {
  try {
    return run ? JSON.parse(run.detail) : {};
  } catch (e) {
    return {};
  }
}

/** Ingest a month old that compute never followed: Reports are behind the ledger. */
function behindItems(runLog, today) {
  const ingest = lastRun(runLog, "ingest");
  const compute = lastRun(runLog, "compute");
  if (!ingest || (compute && compute.computed_at >= ingest.computed_at) || day(ingest.computed_at) > staleBefore(today)) {
    return [];
  }
  return [{ group: "Run compute", item: "Reports are behind the ledger",
            detail: "the last ingest, on " + longDate(ingest.computed_at) + ", was never followed by compute", link: "" }];
}

/**
 * What has waited in the inbox a month: files the last ingest refused, each with its reason, and the rest
 * as one line asking for ingest. files: [{name, createdTime}] from the inbox and the folders in it.
 */
function inboxItems(files, runLog, today) {
  const refused = {};
  for (const f of detailOf(lastRun(runLog, "ingest")).files || []) {
    if (f.file && f.status && ["ingested", "already_present"].indexOf(f.status) < 0) refused[f.file] = f.reason || f.status;
  }
  const old = files.filter((f) => day(f.createdTime) && day(f.createdTime) <= staleBefore(today))
    .sort((a, b) => a.createdTime.localeCompare(b.createdTime));
  const out = old.filter((f) => refused[f.name]).map((f) => ({
    group: "Fix or remove in the inbox", item: f.name, detail: "ingest can't read it: " + refused[f.name], link: "" }));
  const waiting = old.filter((f) => !refused[f.name]);
  if (waiting.length) {
    out.push({ group: "Run ingest, then compute", item: waiting.length + (waiting.length === 1 ? " file" : " files") +
               " in the inbox", detail: "the oldest, " + waiting[0].name + ", since " + longDate(waiting[0].createdTime),
               link: "" });
  }
  return out;
}

function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** The email for these items, grouped by what to do: {subject, text, html}, or null when there are none. */
function compose(items, today) {
  if (!items.length) return null;
  const groups = [];
  for (const item of items) {
    let g = groups.find((x) => x.name === item.group);
    if (!g) groups.push(g = { name: item.group, items: [] });
    g.items.push(item);
  }
  const rank = (name) => (GROUP_ORDER.indexOf(name) < 0 ? GROUP_ORDER.length : GROUP_ORDER.indexOf(name));
  groups.sort((a, b) => rank(a.name) - rank(b.name));
  const n = items.length;
  const subject = "Household: " + n + (n === 1 ? " thing" : " things") + " to do this weekend";
  const text = [];
  const html = ['<div style="font-family:Roboto,Arial,sans-serif;font-size:14px;line-height:1.5;color:#1d1b20">'];
  for (const g of groups) {
    text.push(g.name);
    html.push('<h3 style="font-size:16px;margin:20px 0 6px">' + escapeHtml(g.name) + "</h3><ul style=\"margin:0;padding-left:20px\">");
    for (const i of g.items) {
      text.push("- " + i.item + ": " + i.detail + (i.link ? " (" + i.link + ")" : ""));
      html.push("<li><b>" + escapeHtml(i.item) + "</b>: " + escapeHtml(i.detail) +
                (i.link ? ' <a href="' + escapeHtml(i.link) + '">Open</a>' : "") + "</li>");
    }
    text.push("");
    html.push("</ul>");
  }
  const footer = "As of " + longDate(today) + ". Sent on Saturdays only when something needs doing.";
  text.push(footer);
  html.push('<p style="color:#49454f;font-size:12px;margin-top:24px">' + escapeHtml(footer) + "</p></div>");
  return { subject, text: text.join("\n"), html: html.join("") };
}

// --- Apps Script services -----------------------------------------------------------------------------

function reportsId_() {
  const id = PropertiesService.getScriptProperties().getProperty("REPORTS_ID");
  if (!id) throw new Error("Set the script property REPORTS_ID to the Reports spreadsheet's id.");
  return id;
}

/** Reports tabs by name, through the Sheets API (read-only scope). */
function readTabs_(names) {
  const got = Sheets.Spreadsheets.Values.batchGet(reportsId_(), { ranges: names });
  const out = {};
  names.forEach((name, i) => { out[name] = records((got.valueRanges[i] || {}).values || []); });
  return out;
}

/** Every file in a folder and in the folders directly in it: name and when it arrived. */
function listInbox_(folderId) {
  const list = (parent) => {
    const files = [];
    let token = null;
    do {
      const page = Drive.Files.list({ q: "'" + parent + "' in parents and trashed = false", pageSize: 200,
                                      fields: "nextPageToken, files(id, name, mimeType, createdTime)", pageToken: token });
      for (const f of page.files || []) files.push(f);
      token = page.nextPageToken;
    } while (token);
    return files;
  };
  const out = [];
  for (const f of list(folderId)) {
    if (f.mimeType === FOLDER_MIME) list(f.id).filter((g) => g.mimeType !== FOLDER_MIME).forEach((g) => out.push(g));
    else out.push(f);
  }
  return out;
}

/** This week's email: {subject, text, html}, or null when nothing needs doing. */
function weeklyEmail_(today) {
  const items = [];
  let tabs;
  try {
    tabs = readTabs_(["nudges", "run_log"]);
  } catch (e) {
    return compose([{ group: "Check the weekly email", item: "Reports couldn't be read",
                      detail: String(e) + ". Check REPORTS_ID in the script's settings.", link: "" }], today);
  }
  reportItems(tabs.nudges, today).forEach((i) => items.push(i));
  behindItems(tabs.run_log, today).forEach((i) => items.push(i));
  const inbox = detailOf(lastRun(tabs.run_log, "ingest")).inbox;
  if (inbox) {
    try {
      inboxItems(listInbox_(inbox), tabs.run_log, today).forEach((i) => items.push(i));
    } catch (e) {
      items.push({ group: "Check the weekly email", item: "The inbox couldn't be listed", detail: String(e), link: "" });
    }
  }
  return compose(items, today);
}

function today_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd");
}

/** The trigger's function: send this week's email to you, if anything needs doing. */
function weekly() {
  const email = weeklyEmail_(today_());
  if (!email) {
    console.log("nothing needs doing: no email this week");
    return;
  }
  MailApp.sendEmail({ to: Session.getEffectiveUser().getEmail(), subject: email.subject, body: email.text,
                      htmlBody: email.html });
  console.log("sent: " + email.subject);
}

/** Log the email weekly would send today, without sending it. */
function preview() {
  const email = weeklyEmail_(today_());
  console.log(email ? email.subject + "\n\n" + email.text : "nothing needs doing: no email this week");
}

/** Send weekly every Saturday at 7 in the morning (the project's time zone); run once. */
function installTrigger() {
  for (const t of ScriptApp.getProjectTriggers()) {
    if (t.getHandlerFunction() === "weekly") ScriptApp.deleteTrigger(t);
  }
  ScriptApp.newTrigger("weekly").timeBased().onWeekDay(ScriptApp.WeekDay.SATURDAY).atHour(7).create();
  console.log("weekly runs every Saturday at 7");
}
