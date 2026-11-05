/**
 * Receipt exporter: copies new store receipts from Gmail into the household inbox, once a day.
 *
 * It runs only the Gmail searches listed in the script property SENDERS, reads each new message, and
 * writes all of a run's messages as one text file (receipts_<date>.txt) in a folder of its own, where
 * the engine's receipt reader parses them. It never changes Gmail.
 *
 * Drive access is drive.file: the exporter can see and change only what it created. So it makes its
 * own folder ("email_receipts", in My Drive) on the first run; move that folder into the
 * household inbox folder once, and the engine reads it there. It keeps writing into the folder
 * wherever it is moved, and nothing else in Drive is open to it.
 *
 * Script properties, set once by hand in Project Settings: SENDERS (a JSON list of searches, such as
 * ["from:receipts@example.coop"]; kept out of this file so no store is named in the repo). It keeps
 * its own state there too: FOLDER_ID (its folder), SINCE (the date the next search starts from),
 * DONE (ids already exported, kept for a few weeks so the overlap between searches is not exported
 * twice) and FAILED (messages it could not read, retried by id every run).
 *
 * A message it can't read is never lost to the moving search window: it goes in FAILED and is fetched
 * again by its id at the start of every run until it reads (after a fix to this script, say), however
 * far SINCE has moved on; only a message deleted from Gmail, or past FAILED_KEEP of them the oldest, is
 * dropped, with a log line. The first time a message fails, the run's file says so (an "Unreadable"
 * entry with the Gmail id and the error), so the engine's ingest notes, and the status page, show it.
 *
 * Its Gmail permission, gmail.readonly, lets it read the whole mailbox: Gmail has no narrower scope that
 * still shows a message's body. SENDERS limits what it reads, not what it could read; the boundary is
 * this file, which is short so it can be read in full before it is pushed.
 *
 * Run exportReceipts once by hand (it asks for permission, then exports everything so far), then
 * installTrigger once to run it every morning.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const OVERLAP_DAYS = 3;   // each search reaches back this far before SINCE, in case a receipt arrives late
const KEEP_DAYS = 30;     // DONE remembers ids this long
const SEPARATOR = "===== RECEIPT ";
const FOLDER_NAME = "email_receipts";
const FOLDER_MIME = "application/vnd.google-apps.folder";

const PROGRESS_EVERY = 25;  // log a line every this many messages read
const PAUSE_MS = 150;       // between message reads, to stay under Gmail's per-minute quota
const RETRIES = 6;          // a call refused for quota is tried again, waiting 2, 4, 8 ... seconds
const FAILED_KEEP = 200;    // unreadable messages remembered for retry; past this, the oldest are dropped (logged)
const TRIGGER_HANDLER = "exportReceipts";
const started_ = Date.now();

/** A log line with the seconds since the run started, so a long run shows it is moving. */
function log_(text) {
  console.log("[" + Math.round((Date.now() - started_) / 1000) + "s] " + text);
}

/** call(), tried again with growing waits while Gmail refuses it for quota or rate. */
function gmail_(what, call) {
  for (let attempt = 0; ; attempt++) {
    try {
      return call();
    } catch (e) {
      const limited = /quota|rate limit|too many|429|backend error|500|503/i.test(String(e));
      if (!limited || attempt >= RETRIES) throw e;
      const wait = Math.pow(2, attempt + 1) * 1000;
      log_(what + ": Gmail asked to slow down, waiting " + wait / 1000 + "s (" + String(e).slice(0, 120) + ")");
      Utilities.sleep(wait);
    }
  }
}

function exportReceipts() {
  const props = PropertiesService.getScriptProperties();
  const folderId = folder_(props);
  const senders = JSON.parse(props.getProperty("SENDERS") || "[]");
  if (!senders.length) throw new Error("Set the script property SENDERS to a JSON list of Gmail searches.");
  const since = props.getProperty("SINCE");   // "yyyy/mm/dd", or none for everything so far
  const done = JSON.parse(props.getProperty("DONE") || "{}");   // id -> epoch ms of the message
  const failed = JSON.parse(props.getProperty("FAILED") || "{}");   // id -> {when, tries, error}
  log_("starting: " + senders.length + " searches, " + (since ? "receipts after " + since : "every receipt so far")
       + ", " + Object.keys(done).length + " recent ids already exported, "
       + Object.keys(failed).length + " unreadable messages to try again");
  const parts = [];
  let newest = 0;
  let read = 0;
  const fresh_failures = [];

  // One message: rendered into the file, or kept in FAILED for the next run. Returns whether it read.
  function take(id, message, when) {
    try {
      parts.push(render_(message));
    } catch (e) {  // one unreadable message is logged and kept for retry, not the end of the run
      const error = String(e).replace(/\s+/g, " ").slice(0, 300);
      const before = failed[id];
      failed[id] = { when: when || (before && before.when) || 0, tries: (before ? before.tries : 0) + 1, error: error };
      log_("could not read message " + id + " (try " + failed[id].tries + "): " + error);
      if (!before) fresh_failures.push(id);
      return false;
    }
    if (failed[id]) log_("message " + id + " reads now, after " + failed[id].tries + " failed tries");
    delete failed[id];
    done[id] = when;
    newest = Math.max(newest, when);
    return true;
  }

  for (const id of Object.keys(failed)) {  // first the ones that failed before, by id: SINCE may have passed them
    let message;
    try {
      message = gmail_("message " + id, () => Gmail.Users.Messages.get("me", id, { format: "full" }));
    } catch (e) {
      if (/not found|404/i.test(String(e))) {  // deleted from Gmail since: nothing left to export
        log_("message " + id + " is gone from Gmail: no longer tried");
        delete failed[id];
        continue;
      }
      failed[id].tries++;
      failed[id].error = "could not fetch: " + String(e).replace(/\s+/g, " ").slice(0, 200);
      log_("could not fetch message " + id + " again (try " + failed[id].tries + "): " + failed[id].error);
      continue;
    }
    Utilities.sleep(PAUSE_MS);
    take(id, message, Number(message.internalDate));
  }
  for (const sender of senders) {
    const query = sender + (since ? " after:" + since : "");
    log_("searching: " + query);
    let pageToken, found = 0, fresh = 0;
    do {
      const page = gmail_("search", () => Gmail.Users.Messages.list("me", { q: query, maxResults: 100, pageToken: pageToken }));
      found += (page.messages || []).length;
      for (const ref of page.messages || []) {
        if (done[ref.id] || failed[ref.id]) continue;  // exported already, or tried above
        const message = gmail_("message " + ref.id, () => Gmail.Users.Messages.get("me", ref.id, { format: "full" }));
        Utilities.sleep(PAUSE_MS);
        read++;
        if (read % PROGRESS_EVERY === 0) log_("read " + read + " messages so far");
        if (take(ref.id, message, Number(message.internalDate))) fresh++;
      }
      pageToken = page.nextPageToken;
    } while (pageToken);
    log_("  " + found + " matched, " + fresh + " new");
  }
  const receipts = parts.length;
  for (const id of fresh_failures) parts.push(unreadable_(id, failed[id]));  // told to the engine once
  if (parts.length) {
    const stamp = Utilities.formatDate(new Date(), "UTC", "yyyy-MM-dd'T'HHmmss");
    const name = "receipts_" + stamp + ".txt";
    const text = parts.join("\n");
    Drive.Files.create({ name: name, mimeType: MimeType.PLAIN_TEXT, parents: [folderId] },
                       Utilities.newBlob(text, MimeType.PLAIN_TEXT, name));
    log_("wrote " + name + " (" + receipts + " receipts" + (fresh_failures.length ? ", " + fresh_failures.length
         + " newly unreadable noted" : "") + ", " + Math.round(text.length / 1024) + " KB)");
  } else {
    log_("nothing new: no file written");
  }
  const start = new Date((newest || Date.now()) - OVERLAP_DAYS * DAY_MS);
  props.setProperty("SINCE", Utilities.formatDate(start, "UTC", "yyyy/MM/dd"));
  const cutoff = Date.now() - KEEP_DAYS * DAY_MS;
  for (const id of Object.keys(done)) if (done[id] < cutoff) delete done[id];
  props.setProperty("DONE", JSON.stringify(done));
  const waiting = Object.keys(failed).sort((a, b) => failed[a].when - failed[b].when);
  for (const id of waiting.slice(0, Math.max(0, waiting.length - FAILED_KEEP))) {
    log_("giving up on message " + id + " after " + failed[id].tries + " tries: more than " + FAILED_KEEP + " unreadable");
    delete failed[id];
  }
  props.setProperty("FAILED", JSON.stringify(failed));
  const left = Object.keys(failed);
  log_("done: exported " + receipts + " receipts"
       + (left.length ? "; " + left.length + " unreadable, tried again next run: " + left.join(", ") : "")
       + "; next run searches after " + props.getProperty("SINCE"));
}

/** A message that could not be read, as an entry the engine's reader notes (and reads no rows from). */
function unreadable_(id, failure) {
  return [
    SEPARATOR + id + " =====",
    "Unreadable: " + failure.error,
    "Date: " + (failure.when ? new Date(failure.when).toISOString() : ""),
    "",
    "",
  ].join("\n");
}

/** The exporter's own folder: made on the first run, then the same one wherever it is moved. */
function folder_(props) {
  const id = props.getProperty("FOLDER_ID");
  if (!id) {
    const made = Drive.Files.create({ name: FOLDER_NAME, mimeType: FOLDER_MIME });
    props.setProperty("FOLDER_ID", made.id);
    log_("made the folder '" + FOLDER_NAME + "' in My Drive: move it into the household inbox folder");
    return made.id;
  }
  let found;
  try {
    found = Drive.Files.get(id, { fields: "id,trashed" });
  } catch (e) {
    throw new Error("The exporter's folder (FOLDER_ID " + id + ") can't be reached. Restore it, or delete "
                    + "FOLDER_ID in Project Settings to make a new one: " + e);
  }
  if (found.trashed) throw new Error("The exporter's folder is in the trash. Restore it, or delete FOLDER_ID to make a new one.");
  log_("writing into the folder " + id);
  return id;
}

/** The daily trigger: replaces this exporter's own trigger and leaves any other trigger alone. */
function installTrigger() {
  let removed = 0;
  for (const t of ScriptApp.getProjectTriggers()) {
    if (t.getHandlerFunction() === TRIGGER_HANDLER) {
      ScriptApp.deleteTrigger(t);
      removed++;
    }
  }
  ScriptApp.newTrigger(TRIGGER_HANDLER).timeBased().everyDays(1).atHour(5).create();
  log_("installed: " + TRIGGER_HANDLER + " runs every day around 5am" + (removed ? " (replaced " + removed + " older)" : ""));
}

/** One message as text: a separator line with its id, the headers the reader needs, then the body. */
function render_(message) {
  const headers = message.payload.headers || [];
  const header = (name) => (headers.find((h) => h.name.toLowerCase() === name) || {}).value || "";
  return [
    SEPARATOR + message.id + " =====",
    "From: " + header("from"),
    "Subject: " + header("subject"),
    "Date: " + new Date(Number(message.internalDate)).toISOString(),
    "",
    body_(message.payload),
    "",
  ].join("\n");
}

/** A part's body as text. The Gmail service hands it over as bytes, or as base64url that may lack its
 * padding, depending on the message; both are read. */
function decode_(data) {
  if (typeof data !== "string") return Utilities.newBlob(data).getDataAsString("UTF-8");
  let text = data.replace(/-/g, "+").replace(/_/g, "/").replace(/\s/g, "");
  while (text.length % 4) text += "=";
  return Utilities.newBlob(Utilities.base64Decode(text)).getDataAsString("UTF-8");
}

/** The message's plain text, or its HTML reduced to text when the plain part says too little. */
function body_(payload) {
  const found = { plain: "", html: "" };
  (function walk(part) {
    const kind = part.mimeType === "text/plain" ? "plain" : part.mimeType === "text/html" ? "html" : "";
    if (kind && !found[kind] && part.body && part.body.data) found[kind] = decode_(part.body.data);
    (part.parts || []).forEach(walk);
  })(payload);
  // Some stores put HTML in the plain-text part, so that counts as HTML too.
  const plainIsHtml = /<(table|td|tr|div|br|pre)\b/i.test(found.plain);
  if (found.plain.length > 400 && !plainIsHtml) return found.plain;
  const html = found.html || (plainIsHtml ? found.plain : "");
  if (!html) return found.plain;
  return html
    .replace(/<(style|script)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<br\s*\/?>|<\/(p|div|tr|li|h\d|table)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .replace(/&zwnj;|&shy;|&copy;/g, "")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&#39;|&rsquo;/g, "'").replace(/&quot;/g, "\"")
    .replace(/\r/g, "")
    .replace(/[ \t ]+/g, " ")
    .replace(/\n\s*\n+/g, "\n");
}
