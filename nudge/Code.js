/**
 * The weekly emails: every Saturday morning, each person set to get alerts is emailed the ones of theirs that
 * need doing in the household books, or nothing when none do.
 *
 * Compute works out the list and publishes it to Reports as the nudges table (src/household/nudges.py):
 * each thing to do, under the action it calls for, with the dates between which it belongs in the email.
 * This script reads that table, keeps the rows whose dates take in today, and adds what only it can see
 * live: files that have sat in the inbox a month, files the last ingest refused, and an ingest a month old
 * that compute never followed. A thing goes in the email only when there is something to do about it.
 *
 * Who gets what: each alert's line item (its key) names its people in the household settings, which compute
 * resolves into each row's addresses (`to`) and `routing`: sent, off (no one is emailed), or unassigned. An
 * unassigned alert, and the script's own trouble, go to you (the account the script runs as), the unassigned
 * ones marked so that Status, Alerts in the views can say who should get them.
 *
 * It reads; it never writes. Its permissions: Reports read-only (spreadsheets.readonly), the names and dates
 * of Drive files (drive.metadata.readonly, to see what waits in the inbox, never its contents), sending mail
 * as you (script.send_mail, to the addresses the household settings name and to you), and its own trigger
 * (script.scriptapp).
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
const SECTION_ORDER = ["Taxes and benefits", "Books upkeep", "Spending", "System"];  // as nudges.py
const GROUP_ORDER = [  // the actions in a section in order; nudges.py names the ones that come from Reports
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

/** The nudges compute published whose dates take in today, each with who gets it. */
function reportItems(nudges, today) {
  return nudges.filter((n) => n.show_from && n.show_from <= today && (!n.show_until || n.show_until >= today))
    .map((n) => ({ key: n.key || "", section: n.section || "", group: n.group, item: n.item, detail: n.detail,
                   link: n.link || "", to: n.to || "", routing: n.routing || "unassigned" }));
}

/** Who gets an alert the script finds live (inbox, compute): its line item's row in the nudges. */
function routed(item, nudges) {
  const row = nudges.find((n) => n.key === item.key);
  return Object.assign(item, { to: row ? row.to || "" : "", routing: row ? row.routing || "unassigned" : "unassigned" });
}

/**
 * Each person's email: [{to, subject, text, html}], one per address with something to do. An alert set to no
 * one is left out; an unassigned one goes to the owner, marked; the script's own trouble goes to the owner.
 */
function deliveries(items, owner, today) {
  const by = {};
  const give = (address, item) => (by[address] = by[address] || []).push(item);
  for (const item of items) {
    if (item.routing === "off") continue;
    const to = item.routing === "sent" ? item.to.split(",").map((a) => a.trim()).filter(Boolean) : [];
    if (to.length) to.forEach((a) => give(a, item));
    else give(owner, Object.assign({}, item, { unassigned: item.routing !== "owner" }));
  }
  return Object.keys(by).sort().map((address) => Object.assign({ to: address }, compose(by[address], today)));
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
  return [{ key: "compute", section: "Books upkeep", group: "Run compute", item: "Reports are behind the ledger",
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
    key: "inbox", section: "Books upkeep", group: "Fix or remove in the inbox", item: f.name, detail: "ingest can't read it: " + refused[f.name], link: "" }));
  const waiting = old.filter((f) => !refused[f.name]);
  if (waiting.length) {
    out.push({ key: "inbox", section: "Books upkeep", group: "Run ingest, then compute", item: waiting.length + (waiting.length === 1 ? " file" : " files") +
               " in the inbox", detail: "the oldest, " + waiting[0].name + ", since " + longDate(waiting[0].createdTime),
               link: "" });
  }
  return out;
}

function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/**
 * The email for these items, by section and then by what to do: {subject, text, html}, or null when there
 * are none. An item marked unassigned says no one is set to get it.
 */
function compose(items, today) {
  if (!items.length) return null;
  const rank = (list, name) => (list.indexOf(name) < 0 ? list.length : list.indexOf(name));
  const sorted = items.slice().sort((a, b) => rank(SECTION_ORDER, a.section) - rank(SECTION_ORDER, b.section) ||
                                              rank(GROUP_ORDER, a.group) - rank(GROUP_ORDER, b.group));
  const n = items.length;
  const subject = "Household: " + n + (n === 1 ? " thing" : " things") + " to do this weekend";
  const text = [];
  const html = ['<div style="font-family:Roboto,Arial,sans-serif;font-size:14px;line-height:1.5;color:#1d1b20">'];
  let section = null;
  let group = null;
  for (const i of sorted) {
    if (i.section !== section) {
      section = i.section;
      group = null;
      if (section) {
        text.push(section.toUpperCase(), "");
        html.push('<h2 style="font-size:18px;margin:24px 0 4px">' + escapeHtml(section) + "</h2>");
      }
    }
    if (i.group !== group) {
      if (group !== null) {
        text.push("");
        html.push("</ul>");
      }
      group = i.group;
      text.push(group);
      html.push('<h3 style="font-size:16px;margin:12px 0 6px">' + escapeHtml(group) +
                "</h3><ul style=\"margin:0;padding-left:20px\">");
    }
    const mark = i.unassigned ? " (no one is set to get this)" : "";
    text.push("- " + i.item + ": " + i.detail + mark + (i.link ? " (" + i.link + ")" : ""));
    html.push("<li><b>" + escapeHtml(i.item) + "</b>: " + escapeHtml(i.detail) + escapeHtml(mark) +
              (i.link ? ' <a href="' + escapeHtml(i.link) + '">Open</a>' : "") + "</li>");
  }
  text.push("");
  html.push("</ul>");
  const unassigned = items.filter((i) => i.unassigned).length;
  const footer = (unassigned ? "No one is set to get " + (unassigned === 1 ? "one of these" : unassigned + " of these") +
                  ", so they come to you: choose who gets each on Status, Alerts in the views. " : "") +
                 "As of " + longDate(today) + ". Sent on Saturdays only when something of yours needs doing.";
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

/** What to check, given why a call failed: a service missing from the manifest, or else the settings. */
function hint_(e, otherwise) {
  const missing = /ReferenceError: (Sheets|Drive) is not defined/.exec(String(e));
  return missing ? "The " + missing[1] + " service isn't turned on: push the repo's appsscript.json (clasp push)."
                 : otherwise;
}

/** The script's own trouble: always to the owner. */
function trouble(item, detail) {
  return { key: "email", section: "System", group: "Check the weekly email", item, detail, link: "", to: "",
           routing: "owner" };
}

/** This week's emails, one per person with something to do: [{to, subject, text, html}]. */
function weeklyEmails_(today, owner) {
  const items = [];
  let tabs;
  try {
    tabs = readTabs_(["nudges", "run_log"]);
  } catch (e) {
    return deliveries([trouble("Reports couldn't be read",
                               String(e) + ". " + hint_(e, "Check REPORTS_ID in the script's settings."))], owner, today);
  }
  reportItems(tabs.nudges, today).forEach((i) => items.push(i));
  behindItems(tabs.run_log, today).forEach((i) => items.push(routed(i, tabs.nudges)));
  const inbox = detailOf(lastRun(tabs.run_log, "ingest")).inbox;
  if (inbox) {
    try {
      inboxItems(listInbox_(inbox), tabs.run_log, today).forEach((i) => items.push(routed(i, tabs.nudges)));
    } catch (e) {
      items.push(trouble("The inbox couldn't be listed", String(e) + hint_(e, "").replace(/^(?=.)/, ". ")));
    }
  }
  return deliveries(items, owner, today);
}

function today_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd");
}

/** The trigger's function: email each person their alerts that need doing, if any do. */
function weekly() {
  const emails = weeklyEmails_(today_(), Session.getEffectiveUser().getEmail());
  if (!emails.length) {
    console.log("nothing needs doing: no email this week");
    return;
  }
  for (const email of emails) {
    MailApp.sendEmail({ to: email.to, subject: email.subject, body: email.text, htmlBody: email.html });
  }
  console.log("sent " + emails.length + (emails.length === 1 ? " email: " : " emails: ") +
              emails.map((e) => e.subject).join("; "));
}

/** Log the emails weekly would send today, each with whom it goes to, without sending them. */
function preview() {
  const emails = weeklyEmails_(today_(), Session.getEffectiveUser().getEmail());
  if (!emails.length) console.log("nothing needs doing: no email this week");
  for (const email of emails) console.log("To: " + email.to + "\n" + email.subject + "\n\n" + email.text);
}

/** Send weekly every Saturday at 7 in the morning (the project's time zone); run once. */
function installTrigger() {
  for (const t of ScriptApp.getProjectTriggers()) {
    if (t.getHandlerFunction() === "weekly") ScriptApp.deleteTrigger(t);
  }
  ScriptApp.newTrigger("weekly").timeBased().onWeekDay(ScriptApp.WeekDay.SATURDAY).atHour(7).create();
  console.log("weekly runs every Saturday at 7");
}
