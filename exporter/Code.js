/**
 * Receipt exporter: copies new store receipts from Gmail into the household inbox, once a day.
 *
 * It runs only the Gmail searches listed in the script property SENDERS, reads each new message, and
 * writes all of a run's messages as one text file (receipts_<date>.txt) in a folder of its own, where
 * the engine's receipt reader parses them. It never changes Gmail.
 *
 * Drive access is drive.file: the exporter can see and change only what it created. So it makes its
 * own folder ("receipts from email", in My Drive) on the first run; move that folder into the
 * household inbox folder once, and the engine reads it there. It keeps writing into the folder
 * wherever it is moved, and nothing else in Drive is open to it.
 *
 * Script properties, set once by hand in Project Settings: SENDERS (a JSON list of searches, such as
 * ["from:receipts@example.coop"]; kept out of this file so no store is named in the repo). It keeps
 * its own state there too: FOLDER_ID (its folder), SINCE (the date the next search starts from) and
 * DONE (ids already exported, kept for a few weeks so the overlap between searches is not exported
 * twice).
 *
 * Run exportReceipts once by hand (it asks for permission, then exports everything so far), then
 * installTrigger once to run it every morning.
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const OVERLAP_DAYS = 3;   // each search reaches back this far before SINCE, in case a receipt arrives late
const KEEP_DAYS = 30;     // DONE remembers ids this long
const SEPARATOR = "===== RECEIPT ";
const FOLDER_NAME = "receipts from email";
const FOLDER_MIME = "application/vnd.google-apps.folder";

function exportReceipts() {
  const props = PropertiesService.getScriptProperties();
  const folderId = folder_(props);
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
    const name = "receipts_" + stamp + ".txt";
    Drive.Files.create({ name: name, mimeType: MimeType.PLAIN_TEXT, parents: [folderId] },
                       Utilities.newBlob(parts.join("\n"), MimeType.PLAIN_TEXT, name));
  }
  const start = new Date((newest || Date.now()) - OVERLAP_DAYS * DAY_MS);
  props.setProperty("SINCE", Utilities.formatDate(start, "UTC", "yyyy/MM/dd"));
  const cutoff = Date.now() - KEEP_DAYS * DAY_MS;
  for (const id of Object.keys(done)) if (done[id] < cutoff) delete done[id];
  props.setProperty("DONE", JSON.stringify(done));
  console.log("exported " + parts.length + " receipts");
}

/** The exporter's own folder: made on the first run, then the same one wherever it is moved. */
function folder_(props) {
  const id = props.getProperty("FOLDER_ID");
  if (!id) {
    const made = Drive.Files.create({ name: FOLDER_NAME, mimeType: FOLDER_MIME });
    props.setProperty("FOLDER_ID", made.id);
    console.log("made the folder '" + FOLDER_NAME + "' in My Drive: move it into the household inbox folder");
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
  return id;
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
