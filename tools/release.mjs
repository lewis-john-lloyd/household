#!/usr/bin/env node
// Deploys a release from this checkout: checks that the checkout is the release, runs the tests it can, pushes
// each Apps Script project whose script holds something else, points the views' web app at the new code, and
// prints the tag command. It never tags: the owner does, and the tag is what upgrades the engine notebook.
//
// Needs Node 22, git and clasp 3 (npm i -g @google/clasp), signed in as the scripts' owner (clasp login), and
// each folder's .clasp.json (node tools/clasp_projects.mjs --apply writes them). If gh is installed it is asked
// for CI's verdict on the commit.
//
// A dry run builds web/dist and reads each script through clasp into a temporary folder. Nothing else is written.
// --help says how to run it.

import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";

// each Apps Script folder, in the order they are pushed: the views first, since their deployment follows
const PROJECTS = [
  { folder: "web", name: "the views", webApp: true },
  { folder: "exporter", name: "the receipt exporter" },
  { folder: "nudge", name: "the weekly email" },
  { folder: "assessor", name: "the county check" },
];
const MANIFEST = "appsscript.json";
const USAGE = `Usage, from the repo's top folder:
  node tools/release.mjs               dry run: checks and compares everything; pushes, deploys and tags nothing
  node tools/release.mjs --apply       pushes what differs and updates the web app's deployment
Options:
  --permissions       with --apply, also push a project whose manifest changed (its permissions and services);
                      without it such a project is shown and left alone
  --deployment <id>   the web app's deployment, when the views' script has more than one
  --skip-tests        skip the local tests (the views are still built, to compare them)`;

const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const valueOf = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : undefined);
const known = ["--apply", "--permissions", "--skip-tests", "--deployment", "--help", "-h"];
const unknown = args.filter((a, i) => !known.includes(a) && args[i - 1] !== "--deployment");
if (flag("--help") || flag("-h") || unknown.length) {
  if (unknown.length) console.error(`Not an option: ${unknown.join(" ")}\n`);
  console.log(USAGE);
  process.exit(unknown.length ? 2 : 0);
}
const apply = flag("--apply");
const permissions = flag("--permissions");
const skipTests = flag("--skip-tests");
const deploymentAsked = valueOf("--deployment");

let problems = 0;
const say = (text = "") => console.log(text);
const step = (n, title) => say(`\n== Step ${n}: ${title}`);
const problem = (text) => {
  problems++;
  say(`  PROBLEM: ${text}`);
};

const MINUTES = 60 * 1000;

/** Run a command, echoing it first. {ok, out, err, missing}: missing when the program isn't installed. A command
 * that hasn't answered in ten minutes is stopped and counts as failed. */
function run(cmd, cmdArgs, { cwd = ".", quiet = false } = {}) {
  if (!quiet) say(`  run: ${cmd} ${cmdArgs.join(" ")}${cwd === "." ? "" : `   (in ${cwd})`}`);
  const r = spawnSync(cmd, cmdArgs, {
    cwd,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    timeout: 10 * MINUTES,
    shell: process.platform === "win32" && cmd !== "git", // npm and clasp are .cmd files there; git's ^{} must stay as typed
  });
  return {
    ok: !r.error && r.status === 0,
    out: r.stdout || "",
    err: (r.stderr || "") + (r.error ? String(r.error.message) : ""),
    missing: !!r.error && r.error.code === "ENOENT",
  };
}

/** The last lines of what a failed command said, indented. */
function tail(r, lines = 12) {
  const text = (r.out + "\n" + r.err).trim().split("\n").slice(-lines);
  return text.map((l) => "    " + l).join("\n");
}

/** The JSON a command printed, whatever it printed around it; null when there is none. */
function jsonOf(text) {
  const starts = [text.indexOf("{"), text.indexOf("[")].filter((i) => i >= 0);
  if (!starts.length) return null;
  const from = Math.min(...starts);
  const to = Math.max(text.lastIndexOf("}"), text.lastIndexOf("]"));
  try {
    return JSON.parse(text.slice(from, to + 1));
  } catch {
    return null;
  }
}

/** Every file under a folder, as paths from it with forward slashes. */
function filesUnder(folder, at = "") {
  return readdirSync(join(folder, at), { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? filesUnder(folder, join(at, e.name)) : [join(at, e.name).split("\\").join("/")],
  );
}

/** What a script holds, {path: text}, read through clasp into a temporary folder: its latest code, or one of its
 * versions. null when clasp couldn't read it (said in place). */
function scriptHolds(scriptId, version) {
  // its real path: clasp refuses a folder reached through a link, and a Mac's temporary folder is one
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "household-release-")));
  try {
    writeFileSync(join(dir, ".clasp.json"), JSON.stringify({ scriptId, rootDir: "." }));
    say(`  run: clasp pull${version ? ` --versionNumber ${version}` : ""}   (into a temporary folder)`);
    const r = run("clasp", version ? ["pull", "--versionNumber", String(version)] : ["pull"], { cwd: dir, quiet: true });
    if (!r.ok) {
      problem("clasp could not read the script:");
      say(tail(r));
      return null;
    }
    const held = {};
    for (const f of filesUnder(dir)) if (f !== ".clasp.json") held[f] = readFileSync(join(dir, f), "utf8");
    return held;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** What clasp would push from a folder, {path from its root folder: text}; null when clasp couldn't say. */
function checkoutHolds(project) {
  const r = run("clasp", ["show-file-status", "--json"], { cwd: project.folder });
  const status = r.ok ? jsonOf(r.out) : null;
  if (!status || !Array.isArray(status.filesToPush)) {
    problem("clasp could not list the files it would push:");
    say(tail(r));
    return null;
  }
  const root = join(project.folder, project.rootDir);
  const held = {};
  for (const f of status.filesToPush) {
    const path = join(project.folder, f);
    held[relative(root, path).split("\\").join("/")] = readFileSync(path, "utf8");
  }
  // Apps Script names a file without its extension and keeps "Code" and "code" apart, so a stale file whose name
  // differs only in capitals (or extension) is pushed beside the new one and declares everything twice
  const named = {};
  for (const f of Object.keys(held)) {
    const name = f.replace(/\.[^./]+$/, "").toLowerCase();
    (named[name] = named[name] || []).push(f);
  }
  const twice = Object.values(named).filter((fs) => fs.length > 1);
  if (twice.length) {
    for (const fs of twice)
      problem(`${fs.sort().join(" and ")} would be pushed as one script's two files: remove the stale one` +
        (project.rootDir !== "." ? ` (or the whole ${join(project.folder, project.rootDir)} folder, then build again)` : ""));
    return null;
  }
  return held;
}

/** How the checkout differs from what a script holds: the files a push would write, and the ones it would remove. */
function difference(local, remote) {
  return {
    changed: Object.keys(local)
      .filter((f) => f in remote && remote[f] !== local[f])
      .sort(),
    added: Object.keys(local)
      .filter((f) => !(f in remote))
      .sort(),
    removed: Object.keys(remote)
      .filter((f) => !(f in local))
      .sort(),
  };
}

const differs = (d) => d.changed.length + d.added.length + d.removed.length > 0;

function sayDifference(d) {
  for (const [what, files] of [
    ["changed", d.changed],
    ["new", d.added],
    ["only in the script (a push removes it)", d.removed],
  ])
    if (files.length) say(`    ${what}: ${files.join(", ")}`);
}

/** The lines of a manifest that one side has and the other doesn't. */
function sayManifest(local, remote) {
  const lines = (text) =>
    (text || "")
      .split("\n")
      .map((l) => l.trim().replace(/,$/, ""))
      .filter(Boolean);
  const [mine, theirs] = [lines(local), lines(remote)];
  for (const l of theirs.filter((x) => !mine.includes(x))) say(`      - ${l}`);
  for (const l of mine.filter((x) => !theirs.includes(x))) say(`      + ${l}`);
}

// ---------------------------------------------------------------------------------------------------------

say(
  apply
    ? "== Releasing: pushes what differs and updates the web app's deployment. It does not tag."
    : "== Dry run: nothing is pushed, deployed or tagged. Run again with --apply to release.",
);

step(1, "the release this checkout is");
const version = /^version\s*=\s*"([^"]+)"/m.exec(readFileSync("pyproject.toml", "utf8"))?.[1];
const packaged = /^__version__\s*=\s*"([^"]+)"/m.exec(readFileSync("src/household/__init__.py", "utf8"))?.[1];
say(`  pyproject.toml says ${version}, src/household/__init__.py says ${packaged}`);
if (!version || version !== packaged) problem("the two versions differ: a release commit sets both");
const tag = `v${version}`;
const head = run("git", ["rev-parse", "HEAD"]).out.trim();
const dirty = run("git", ["status", "--porcelain"]).out.trimEnd(); // each line keeps its two status letters
if (dirty) {
  problem("the checkout has changes that aren't committed; a release is deployed from a commit:");
  say(
    dirty
      .split("\n")
      .slice(0, 10)
      .map((l) => "    " + l)
      .join("\n"),
  );
}
const remote = run("git", ["ls-remote", "origin", "refs/heads/main", `refs/tags/${tag}`, `refs/tags/${tag}^{}`]);
let tagged = "";
if (!remote.ok) {
  problem("git could not ask origin what it has:");
  say(tail(remote, 4));
} else {
  const refs = Object.fromEntries(
    remote.out
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((l) => l.split(/\s+/).reverse()),
  );
  const main = refs["refs/heads/main"] || "";
  tagged = refs[`refs/tags/${tag}^{}`] || refs[`refs/tags/${tag}`] || ""; // an annotated tag's commit, else the tag's
  say(`  this commit is ${head.slice(0, 7)}; origin's main is ${main.slice(0, 7) || "not there"}`);
  if (tagged === head) say(`  ${tag} is on origin, at this commit`);
  else if (tagged)
    problem(
      `${tag} is already on origin at ${tagged.slice(0, 7)}, another commit. To deploy that release again, check it ` +
        `out (git checkout ${tag}); what came after it waits for a release commit with a new version.`,
    );
  else if (main !== head)
    problem("this commit is not what origin's main has: pull or push first, so the tag and the deploy are one commit");
  else say(`  ${tag} is not on origin yet: this is the commit to tag (step 5)`);
}

const isRelease = problems === 0; // whether this commit is one to tag

step(2, "the tests");
const ci = run("gh", ["run", "list", "--commit", head, "--json", "name,status,conclusion", "--limit", "30"]);
if (ci.missing) say("  gh is not installed, so CI was not asked: check the commit's checks on GitHub");
else if (!ci.ok || !Array.isArray(jsonOf(ci.out))) {
  say("  gh could not ask CI (not signed in, or offline): check the commit's checks on GitHub");
} else {
  const runs = jsonOf(ci.out);
  if (!runs.length) say("  CI has no run for this commit: check that it was pushed to a branch");
  for (const r of runs) {
    const verdict = r.status === "completed" ? r.conclusion : r.status;
    say(`  CI's ${r.name} run: ${verdict}`);
    if (r.status !== "completed") problem(`CI's ${r.name} has not finished: wait for it`);
    else if (!["success", "skipped", "neutral"].includes(r.conclusion)) problem(`CI's ${r.name} did not pass`);
  }
}
if (!existsSync("web/node_modules")) {
  const installed = run("npm", ["ci"], { cwd: "web" });
  if (!installed.ok) {
    problem("the views' tools could not be installed:");
    say(tail(installed));
  }
}
const built = run("npm", ["run", "build"], { cwd: "web" });
if (!built.ok) {
  problem("the views did not build:");
  say(tail(built));
}
if (skipTests) say("  local tests skipped (--skip-tests)");
else {
  const suites = [
    ["npm", ["test"], "web"],
    ["npm", ["run", "test:server"], "web"],
    ...PROJECTS.filter((p) => !p.webApp).map((p) => ["node", ["--test", `${p.folder}/test/${p.folder}.test.js`], "."]),
  ];
  for (const [cmd, cmdArgs, cwd] of suites) {
    const r = run(cmd, cmdArgs, { cwd });
    if (r.ok) say("    passed");
    else {
      problem("a test failed:");
      say(tail(r, 25));
    }
  }
  say("  the Python and browser suites are CI's (above)");
}

const clasp = run("clasp", ["--version"]);
if (clasp.missing) problem("clasp is not installed: npm i -g @google/clasp, then clasp login");
else if (!/^[3-9]\./.test(clasp.out.trim())) problem(`this needs clasp 3 or later; this one is ${clasp.out.trim() || "unknown"}`);
else {
  const who = jsonOf(run("clasp", ["show-authorized-user", "--json"]).out);
  if (who && who.loggedIn === false) problem("clasp is not signed in: clasp login, as the scripts' owner");
  else if (who && who.email) say(`  clasp is signed in as ${who.email}`);
}

if (apply && problems) {
  say(`\n== Stopped: ${problems} problem(s) above. Nothing was pushed or deployed.`);
  process.exit(1);
}

step(3, "each Apps Script project against what its script holds");
let viewsCurrent = false; // whether the views' script now holds this checkout's build
let views = null; // the views' project and build, for the deployment
for (const project of PROJECTS) {
  say(`\n-- ${project.folder}: ${project.name}`);
  const settings = join(project.folder, ".clasp.json");
  if (!existsSync(settings)) {
    problem(`${settings} is missing, so this folder's script isn't known: node tools/clasp_projects.mjs --apply writes it`);
    continue;
  }
  const { scriptId, rootDir = "." } = JSON.parse(readFileSync(settings, "utf8"));
  Object.assign(project, { scriptId, rootDir });
  const local = clasp.missing ? null : checkoutHolds(project);
  const held = local && scriptHolds(scriptId);
  if (!local || !held) continue;
  if (project.webApp) views = { project, local };
  const d = difference(local, held);
  if (!differs(d)) {
    say("  up to date: the script holds this checkout");
    if (project.webApp) viewsCurrent = true;
    continue;
  }
  const manifest = d.changed.includes(MANIFEST) || d.added.includes(MANIFEST);
  const count = d.changed.length + d.added.length + d.removed.length;
  say(`  differs from the script (${count} ${count === 1 ? "file" : "files"}):`);
  sayDifference(d);
  if (manifest) {
    say("    its manifest changed, so the script may ask for its permissions again:");
    sayManifest(local[MANIFEST], held[MANIFEST]);
  }
  if (!apply) {
    say(manifest && !permissions ? "  would push it only with --permissions" : "  would push it");
    continue;
  }
  if (manifest && !permissions) {
    problem("its manifest changed: read the change above, then run again with --apply --permissions to push it");
    continue;
  }
  const pushed = run("clasp", manifest ? ["push", "--force"] : ["push"], { cwd: project.folder });
  if (!pushed.ok) {
    problem("the push failed:");
    say(tail(pushed));
    continue;
  }
  const now = scriptHolds(scriptId);
  if (!now) continue;
  if (differs(difference(local, now))) {
    problem("after the push the script still differs from the checkout:");
    sayDifference(difference(local, now));
    say(tail(pushed));
    continue;
  }
  say("  pushed: the script now holds this checkout");
  if (project.webApp) viewsCurrent = true;
}

step(4, "the views' web app");
if (!views) say("  skipped: the views' script could not be compared (above)");
else {
  const listed = run("clasp", ["list-deployments", "--json"], { cwd: "web" });
  const all = listed.ok ? jsonOf(listed.out) : null;
  if (!Array.isArray(all)) {
    problem("clasp could not list the views' deployments:");
    say(tail(listed));
  } else {
    // the web app is a deployment of a version; the one clasp shows at @HEAD is the editor's test deployment
    const versioned = all.filter((d) => d.versionNumber);
    for (const d of versioned)
      say(`  deployment ${d.deploymentId} serves version ${d.versionNumber}${d.description ? ` (${d.description})` : ""}`);
    const chosen = deploymentAsked
      ? versioned.find((d) => d.deploymentId === deploymentAsked)
      : versioned.length === 1
        ? versioned[0]
        : null;
    if (deploymentAsked && !chosen) problem(`the views' script has no deployment ${deploymentAsked}`);
    else if (!versioned.length)
      problem(
        "the views have no web app deployment yet: make one in the script editor (Deploy, New deployment, Web app; docs/views.md)",
      );
    else if (!chosen) problem("the views' script has several deployments: name the web app's with --deployment <id>");
    else {
      const serving = scriptHolds(views.project.scriptId, chosen.versionNumber);
      if (serving && !differs(difference(views.local, serving))) say("  up to date: the web app serves this checkout's build");
      else if (serving && !apply)
        say(`  would ${viewsCurrent ? "" : "push the views, then "}point it at a new version of the script, described ${tag}`);
      else if (serving && !viewsCurrent)
        problem("the web app was left as it is: the views' script doesn't hold this checkout (step 3)");
      else if (serving) {
        const updated = run("clasp", ["update-deployment", chosen.deploymentId, "--description", tag, "--json"], { cwd: "web" });
        const result = updated.ok ? jsonOf(updated.out) : null;
        const after = result && result.versionNumber ? scriptHolds(views.project.scriptId, result.versionNumber) : null;
        if (!result || !result.versionNumber) {
          problem("the deployment was not updated:");
          say(tail(updated));
        } else if (!after || differs(difference(views.local, after))) {
          problem(`the web app now serves version ${result.versionNumber}, which is not this checkout's build`);
        } else say(`  updated: the web app serves version ${result.versionNumber} (${tag}), at the same address`);
      }
    }
  }
}

step(5, "the tag (yours to make)");
if (tagged === head) say(`  ${tag} is already on origin at this commit: nothing to do`);
else if (!isRelease) say("  not yet: the tag goes on the release commit, once step 1 has no problem");
else {
  say("  The engine notebook installs the newest tag at its next Run all. To tag this commit:");
  say(`    git tag ${tag} ${head}`);
  say(`    git push origin ${tag}`);
}

say(
  problems
    ? `\n== ${problems} problem(s) above${apply ? "" : ": a run with --apply would stop at them"}.`
    : apply
      ? "\n== Done. Tag the release (step 5), then Run all in the engine notebook."
      : "\n== Dry run done: no problems. Run again with --apply to release.",
);
process.exit(problems ? 1 : 0);
