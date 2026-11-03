/**
 * Receipt exporter: copies new store receipts from Gmail into the household inbox folder, once a day.
 *
 * It runs only the Gmail searches listed in the script property SENDERS, reads each new message, and
 * writes all of a run's messages as one text file (receipts_<date>.txt) in the inbox, where the
 * engine's receipt reader parses them. It never changes Gmail. Script properties, set once by hand
 * in Project Settings: INBOX_FOLDER_ID (the household inbox folder) and SENDERS (a JSON list of
 * searches, such as ["from:receipts@example.coop"]; kept out of this file so no store is named in
 * the repo). It keeps its own state there too: SINCE (the date the next search starts from) and DONE
 * (ids already exported, kept for a few weeks so the overlap between searches is not exported twice).
 *
 * Run exportReceipts once by hand (it asks for permission, then exports everything so far), then
 * installTrigger once to run it every morning.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const OVERLAP_DAYS = 3;   // each search reaches back this far before SINCE, in case a receipt arrives late
const KEEP_DAYS = 30;     // DONE remembers ids this long
const SEPARATOR = "===== RECEIPT ";

function exportReceipts() {
  const props = PropertiesService.getScriptProperties();
  const folderId = props.getProperty("INBOX_FOLDER_ID");
  if (!folderId) throw new Error("Set the script property INBOX_FOLDER_ID to the household inbox folder's id.");
  const folder = DriveApp.getFolderById(folderId);
  const senders = JSON.parse(props.getProperty("SENDERS") || "[]");
  if (!senders.length) throw new Error("Set the script property SENDERS to a JSON list of Gmail searches.");
  const since = props.getProperty("SINCE");   // "yyyy/mm/dd", or none for everything so far
  const done = JSON.parse(props.getProperty("DONE") || "{}");   // id -> epoch ms of the message
  const parts = [];
  let newest = 0;
  for (const sender of senders) {
    const query = sender + (since ? " after:" + since : "");
    let pageToken;
    do {
      const page = Gmail.Users.Messages.list("me", { q: query, maxResults: 100, pageToken: pageToken });
      for (const ref of page.messages || []) {
        if (done[ref.id]) continue;
        const message = Gmail.Users.Messages.get("me", ref.id, { format: "full" });
        const when = Number(message.internalDate);
        parts.push(render_(message));
        done[ref.id] = when;
        newest = Math.max(newest, when);
      }
      pageToken = page.nextPageToken;
    } while (pageToken);
  }
  if (parts.length) {
    const stamp = Utilities.formatDate(new Date(), "UTC", "yyyy-MM-dd'T'HHmmss");
    folder.createFile("receipts_" + stamp + ".txt", parts.join("\n"), MimeType.PLAIN_TEXT);
  }
  const start = new Date((newest || Date.now()) - OVERLAP_DAYS * DAY_MS);
  props.setProperty("SINCE", Utilities.formatDate(start, "UTC", "yyyy/MM/dd"));
  const cutoff = Date.now() - KEEP_DAYS * DAY_MS;
  for (const id of Object.keys(done)) if (done[id] < cutoff) delete done[id];
  props.setProperty("DONE", JSON.stringify(done));
  console.log("exported " + parts.length + " receipts");
}

function installTrigger() {
  for (const t of ScriptApp.getProjectTriggers()) ScriptApp.deleteTrigger(t);
  ScriptApp.newTrigger("exportReceipts").timeBased().everyDays(1).atHour(5).create();
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

/** The message's plain text, or its HTML reduced to text when the plain part says too little. */
function body_(payload) {
  const found = { plain: "", html: "" };
  (function walk(part) {
    if (part.body && part.body.data) {
      const text = Utilities.newBlob(Utilities.base64DecodeWebSafe(part.body.data)).getDataAsString();
      if (part.mimeType === "text/plain" && !found.plain) found.plain = text;
      if (part.mimeType === "text/html" && !found.html) found.html = text;
    }
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
