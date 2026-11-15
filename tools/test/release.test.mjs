// tools/release.mjs against a made-up checkout: a git repo with an origin, the four Apps Script folders, and
// clasp and gh faked as programs on the PATH. The fake clasp keeps each script's code, versions and deployments
// in files and answers as clasp 3.4 does (its commands, its JSON, a manifest change skipped without --force
// when there is no terminal), so the tool's steps run for real: what it compares, pushes, deploys and refuses.
//
// Run: node --test tools/test/release.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const TOOL = join(dirname(fileURLToPath(import.meta.url)), "..", "release.mjs");

const FAKE_CLASP = `#!/usr/bin/env node
const fs = require("fs"), path = require("path");
const state = process.env.FAKE_STATE;
const argv = process.argv.slice(2);
fs.appendFileSync(path.join(state, "calls.log"), "clasp " + argv.join(" ") + " @" + path.basename(process.cwd()) + "\\n");
if (argv[0] === "--version") { console.log(process.env.FAKE_CLASP_VERSION || "3.4.1"); process.exit(0); }
if (argv[0] === "show-authorized-user") {
  const out = process.env.FAKE_CLASP_SIGNED_OUT ? { loggedIn: false } : { loggedIn: true, email: "alex@example.test" };
  console.log(JSON.stringify(out, null, 2));
  process.exit(0);
}
const cmd = argv[0];
const config = JSON.parse(fs.readFileSync(".clasp.json", "utf8"));
const root = path.resolve(config.rootDir || ".");
const script = path.join(state, "scripts", config.scriptId);
const read = (dir) => fs.existsSync(dir)
  ? Object.fromEntries(fs.readdirSync(dir).map((f) => [f, fs.readFileSync(path.join(dir, f), "utf8")])) : {};
const write = (dir, files) => {
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  for (const [f, text] of Object.entries(files)) fs.writeFileSync(path.join(dir, f), text);
};
// what clasp pushes: under the scripts' ignore file the script and its manifest, else every script, page and the manifest
const local = () => {
  const only = fs.existsSync(".claspignore") ? ["Code.js", "appsscript.json"] : null;
  const mine = fs.readdirSync(root).filter((f) => (only ? only.includes(f) : /\\.(js|html)$/.test(f) || f === "appsscript.json"));
  return Object.fromEntries(mine.map((f) => [f, fs.readFileSync(path.join(root, f), "utf8")]));
};
const deployments = () => JSON.parse(fs.readFileSync(path.join(script, "deployments.json"), "utf8"));
if (cmd === "show-file-status") {
  const files = Object.keys(local()).map((f) => path.relative(process.cwd(), path.join(root, f)));
  console.log(JSON.stringify({ filesToPush: files, untrackedFiles: [] }));
} else if (cmd === "pull") {
  const at = argv.indexOf("--versionNumber");
  const held = read(at < 0 ? path.join(script, "head") : path.join(script, "versions", argv[at + 1]));
  for (const [f, text] of Object.entries(held)) fs.writeFileSync(path.join(root, f), text);
  console.log("Pulled " + Object.keys(held).length + " files.");
} else if (cmd === "push") {
  if (fs.existsSync(path.join(script, "refuse-push"))) { console.error("Push failed: the API is not answering"); process.exit(1); }
  const head = read(path.join(script, "head")), mine = local();
  const changed = Object.keys(mine).filter((f) => head[f] !== mine[f]);
  if (!changed.length) console.log("Script is already up to date.");
  else if (changed.includes("appsscript.json") && !argv.includes("--force")) console.log("Skipping push."); // no terminal to ask in
  else { write(path.join(script, "head"), mine); console.log("Pushed " + Object.keys(mine).length + " files."); }
} else if (cmd === "list-deployments") {
  console.log(JSON.stringify(deployments(), null, 2));
} else if (cmd === "update-deployment") {
  const id = argv[1], description = argv[argv.indexOf("--description") + 1];
  const versions = path.join(script, "versions");
  const n = Math.max(0, ...(fs.existsSync(versions) ? fs.readdirSync(versions).map(Number) : [])) + 1;
  write(path.join(versions, String(n)), read(path.join(script, "head")));
  const all = deployments().map((d) => (d.deploymentId === id ? { deploymentId: id, versionNumber: n, description } : d));
  fs.writeFileSync(path.join(script, "deployments.json"), JSON.stringify(all));
  console.log(JSON.stringify({ deploymentId: id, versionNumber: n, description }, null, 2));
} else { console.error("fake clasp: no such command " + cmd); process.exit(2); }
`;

const FAKE_GH = `#!/usr/bin/env node
const fs = require("fs"), path = require("path");
const state = process.env.FAKE_STATE;
fs.appendFileSync(path.join(state, "calls.log"), "gh " + process.argv.slice(2).join(" ") + "\\n");
const ci = path.join(state, "ci.json");
if (!fs.existsSync(ci)) { console.error("gh: not signed in"); process.exit(1); }
console.log(fs.readFileSync(ci, "utf8"));
`;

const MANIFEST = (scopes) =>
  JSON.stringify({ timeZone: "America/Chicago", runtimeVersion: "V8", oauthScopes: scopes }, null, 2) + "\n";
const SCRIPTS = ["exporter", "nudge", "assessor"];

/** A made-up checkout at release 0.2.0, pushed to its origin, with each script as the release before left it:
 * the views' script and web app an older build, the three small scripts already holding this checkout. */
function sandbox() {
  const top = mkdtempSync(join(tmpdir(), "release-test-"));
  const [repo, bin, state] = ["repo", "bin", "state"].map((d) => join(top, d));
  for (const d of [repo, bin, state]) mkdirSync(d);
  const file = (path, text) => {
    mkdirSync(dirname(join(repo, path)), { recursive: true });
    writeFileSync(join(repo, path), text);
  };
  file("pyproject.toml", '[project]\nname = "household"\nversion = "0.2.0"\n');
  file("src/household/__init__.py", '__version__ = "0.2.0"\n');
  file(".gitignore", "web/dist/\nweb/node_modules/\n.clasp.json\n");
  cpSync(TOOL, join(repo, "tools", "release.mjs"), { recursive: true });
  // the views: a build that copies src to dist and stamps the release, and tests that pass
  file(
    "web/package.json",
    JSON.stringify({ scripts: { build: "node build.mjs", test: 'node -e ""', "test:server": 'node -e ""' } }),
  );
  file(
    "web/build.mjs",
    'import { cpSync, writeFileSync } from "node:fs";\n' +
      'cpSync("src", "dist", { recursive: true });\n' +
      'writeFileSync("dist/Version.js", \'var VIEWS_VERSION = "0.2.0";\\n\');\n',
  );
  file("web/src/Code.js", "function doGet() { return 2; }\n");
  file("web/src/Index.html", "<p>views</p>\n");
  file("web/src/appsscript.json", MANIFEST(["drive.file"]));
  mkdirSync(join(repo, "web", "node_modules"));
  writeFileSync(join(repo, "web", ".clasp.json"), JSON.stringify({ scriptId: "views", rootDir: "dist" }));
  for (const s of SCRIPTS) {
    file(`${s}/Code.js`, `function ${s}() {}\n`);
    file(`${s}/appsscript.json`, MANIFEST(["drive.file"]));
    file(`${s}/.claspignore`, "**/**\n!Code.js\n!appsscript.json\n");
    file(`${s}/test/${s}.test.js`, 'require("node:test").test("passes", () => {});\n');
    writeFileSync(join(repo, s, ".clasp.json"), JSON.stringify({ scriptId: s, rootDir: "." }));
  }
  writeFileSync(join(bin, "clasp"), FAKE_CLASP);
  writeFileSync(join(bin, "gh"), FAKE_GH);
  for (const f of ["clasp", "gh"]) chmodSync(join(bin, f), 0o755);

  const env = {
    ...process.env,
    PATH: bin + delimiter + process.env.PATH,
    FAKE_STATE: state,
    GIT_AUTHOR_NAME: "t",
    GIT_AUTHOR_EMAIL: "t@example.test",
    GIT_COMMITTER_NAME: "t",
    GIT_COMMITTER_EMAIL: "t@example.test",
    GIT_CONFIG_GLOBAL: join(top, "gitconfig"), // none of the machine's own git settings (hooks, signing)
    GIT_CONFIG_NOSYSTEM: "1",
  };
  const git = (...a) => {
    const r = spawnSync("git", a, { cwd: repo, env, encoding: "utf8" });
    assert.equal(r.status, 0, `git ${a.join(" ")}: ${r.stderr}`);
    return r.stdout.trim();
  };
  spawnSync("git", ["init", "-q", "--bare", join(top, "origin.git")], { env });
  git("init", "-q", "-b", "main");
  git("remote", "add", "origin", join(top, "origin.git"));
  git("add", "-A");
  git("commit", "-q", "-m", "Release 0.2.0");
  git("push", "-q", "origin", "main");

  // what each script holds
  const held = (id, where, files) => {
    const dir = join(state, "scripts", id, where);
    mkdirSync(dir, { recursive: true });
    for (const [f, text] of Object.entries(files)) writeFileSync(join(dir, f), text);
  };
  const before = {
    "Code.js": "function doGet() { return 1; }\n",
    "Index.html": "<p>views</p>\n",
    "appsscript.json": MANIFEST(["drive.file"]),
  };
  held("views", "head", before);
  held("views", join("versions", "1"), before);
  const deployments = (list) => writeFileSync(join(state, "scripts", "views", "deployments.json"), JSON.stringify(list));
  deployments([{ deploymentId: "head-deployment" }, { deploymentId: "web-app", versionNumber: 1, description: "v0.1.0" }]);
  for (const s of SCRIPTS) held(s, "head", { "Code.js": `function ${s}() {}\n`, "appsscript.json": MANIFEST(["drive.file"]) });
  writeFileSync(join(state, "ci.json"), JSON.stringify([{ name: "ci", status: "completed", conclusion: "success" }]));

  return {
    repo,
    state,
    git,
    file,
    deployments,
    head: () => git("rev-parse", "HEAD"),
    /** Run the tool. {status, out}. */
    release: (args = [], more = {}) => {
      const r = spawnSync(process.execPath, ["tools/release.mjs", ...args], {
        cwd: repo,
        env: { ...env, ...more },
        encoding: "utf8",
      });
      return { status: r.status, out: r.stdout + r.stderr };
    },
    /** What the tool asked clasp and gh to do, in order. */
    calls: (what) =>
      (existsSync(join(state, "calls.log")) ? readFileSync(join(state, "calls.log"), "utf8") : "")
        .split("\n")
        .filter((l) => l && (!what || l.includes(what))),
    /** What a script holds: its latest code, or one of its versions. */
    holds: (id, where = "head") => {
      const dir = join(state, "scripts", id, where);
      return Object.fromEntries(readdirSync(dir).map((f) => [f, readFileSync(join(dir, f), "utf8")]));
    },
    served: () => JSON.parse(readFileSync(join(state, "scripts", "views", "deployments.json"), "utf8")),
    done: () => rmSync(top, { recursive: true, force: true }),
  };
}

const BUILD = {
  "Code.js": "function doGet() { return 2; }\n",
  "Index.html": "<p>views</p>\n",
  "Version.js": 'var VIEWS_VERSION = "0.2.0";\n',
  "appsscript.json": MANIFEST(["drive.file"]),
};

test("a dry run says what a release would do and does none of it", () => {
  const s = sandbox();
  const before = s.holds("views");
  const r = s.release();
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /^== Dry run: nothing is pushed, deployed or tagged/);
  assert.match(r.out, /pyproject\.toml says 0\.2\.0, src\/household\/__init__\.py says 0\.2\.0/);
  assert.match(r.out, /v0\.2\.0 is not on origin yet: this is the commit to tag/);
  assert.match(r.out, /CI's ci run: success/);
  assert.match(r.out, /clasp is signed in as alex@example\.test/);
  // the views differ from their script, file by file; the three small scripts don't
  assert.match(
    r.out,
    /-- web: the views\n(.*\n)*?  differs from the script \(2 files\):\n    changed: Code\.js\n    new: Version\.js\n  would push it/,
  );
  for (const name of SCRIPTS)
    assert.match(r.out, new RegExp(`-- ${name}: .*\\n(.*\\n){2}  up to date: the script holds this checkout`));
  assert.match(r.out, /deployment web-app serves version 1 \(v0\.1\.0\)/);
  assert.match(r.out, /would push the views, then point it at a new version of the script, described v0\.2\.0/);
  assert.ok(r.out.includes(`    git tag v0.2.0 ${s.head()}\n    git push origin v0.2.0`));
  assert.match(r.out, /== Dry run done: no problems\. Run again with --apply to release\.\n$/);
  // every command is echoed before it runs
  assert.match(r.out, /  run: npm run build {3}\(in web\)/);
  assert.match(r.out, /  run: clasp show-file-status --json {3}\(in exporter\)/);
  // and nothing was pushed, deployed or tagged
  assert.deepEqual(s.calls("push"), []);
  assert.deepEqual(s.calls("update-deployment"), []);
  assert.deepEqual(s.holds("views"), before);
  assert.equal(s.git("tag", "-l"), "");
  assert.equal(s.git("status", "--porcelain"), ""); // the build and the reads left the checkout as it was
  s.done();
});

test("a release pushes only the project that differs, moves the web app to it, and a second run finds nothing to do", () => {
  const s = sandbox();
  const r = s.release(["--apply"]);
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /^== Releasing: pushes what differs and updates the web app's deployment\. It does not tag\./);
  assert.match(r.out, /  pushed: the script now holds this checkout/);
  assert.match(r.out, /  updated: the web app serves version 2 \(v0\.2\.0\), at the same address/);
  assert.match(r.out, /== Done\. Tag the release \(step 5\), then Run all in the engine notebook\.\n$/);
  assert.deepEqual(s.calls("clasp push"), ["clasp push @web"]); // the three scripts already held the checkout
  assert.deepEqual(s.holds("views"), BUILD);
  assert.deepEqual(s.holds("views", join("versions", "2")), BUILD);
  assert.deepEqual(s.served(), [
    { deploymentId: "head-deployment" },
    { deploymentId: "web-app", versionNumber: 2, description: "v0.2.0" },
  ]);
  assert.equal(s.git("tag", "-l"), ""); // the tag is the owner's
  // again: all four hold the checkout and the web app serves it
  const again = s.release(["--apply", "--skip-tests"]);
  assert.equal(again.status, 0, again.out);
  assert.equal(again.out.match(/up to date: the script holds this checkout/g).length, 4);
  assert.match(again.out, /up to date: the web app serves this checkout's build/);
  assert.match(again.out, /local tests skipped \(--skip-tests\)/);
  assert.equal(s.calls("clasp push").length, 1);
  assert.equal(s.calls("update-deployment").length, 1);
  s.done();
});

test("a changed manifest is shown, and pushed only when --permissions says so", () => {
  const s = sandbox();
  s.file("exporter/appsscript.json", MANIFEST(["drive.file", "gmail.readonly"]));
  s.git("commit", "-q", "-am", "The exporter reads mail");
  s.git("push", "-q", "origin", "main");
  const dry = s.release(["--skip-tests"]);
  assert.equal(dry.status, 0, dry.out);
  assert.match(
    dry.out,
    /differs from the script \(1 file\):\n    changed: appsscript\.json\n    its manifest changed, so the script may ask for its permissions again:\n {6}\+ "gmail\.readonly"\n  would push it only with --permissions/,
  );
  const held = s.release(["--apply", "--skip-tests"]);
  assert.equal(held.status, 1);
  assert.match(held.out, /PROBLEM: its manifest changed: read the change above, then run again with --apply --permissions/);
  assert.deepEqual(s.holds("exporter")["appsscript.json"], MANIFEST(["drive.file"])); // left alone
  assert.deepEqual(s.holds("views"), BUILD); // the others went ahead
  const pushed = s.release(["--apply", "--permissions", "--skip-tests"]);
  assert.equal(pushed.status, 0, pushed.out);
  assert.deepEqual(s.calls("clasp push").slice(-1), ["clasp push --force @exporter"]);
  assert.deepEqual(s.holds("exporter")["appsscript.json"], MANIFEST(["drive.file", "gmail.readonly"]));
  s.done();
});

test("a checkout that isn't the release stops a release before anything is pushed", () => {
  const stopped = (s, r, why) => {
    assert.equal(r.status, 1, r.out);
    assert.match(r.out, why);
    assert.match(r.out, /== Stopped: \d+ problem\(s\) above\. Nothing was pushed or deployed\.\n$/);
    assert.deepEqual(s.calls("clasp push"), []);
    assert.deepEqual(s.calls("update-deployment"), []);
    s.done();
  };
  let s = sandbox();
  s.file("exporter/Code.js", "function exporter() { return 1; }\n");
  stopped(s, s.release(["--apply"]), /PROBLEM: the checkout has changes that aren't committed.*\n {4} M exporter\/Code\.js/);

  s = sandbox();
  s.file("src/household/__init__.py", '__version__ = "0.1.9"\n');
  s.git("commit", "-q", "-am", "One version only");
  s.git("push", "-q", "origin", "main");
  stopped(
    s,
    s.release(["--apply"]),
    /says 0\.2\.0, src\/household\/__init__\.py says 0\.1\.9\n  PROBLEM: the two versions differ/,
  );

  s = sandbox();
  s.file("exporter/Code.js", "function exporter() { return 1; }\n");
  s.git("commit", "-q", "-am", "Not pushed");
  stopped(s, s.release(["--apply"]), /PROBLEM: this commit is not what origin's main has: pull or push first/);

  s = sandbox();
  writeFileSync(join(s.state, "ci.json"), JSON.stringify([{ name: "ci", status: "completed", conclusion: "failure" }]));
  stopped(s, s.release(["--apply"]), /CI's ci run: failure\n  PROBLEM: CI's ci did not pass/);

  s = sandbox();
  writeFileSync(join(s.state, "ci.json"), JSON.stringify([{ name: "ci", status: "in_progress", conclusion: "" }]));
  stopped(s, s.release(["--apply"]), /PROBLEM: CI's ci has not finished: wait for it/);

  s = sandbox();
  s.file(
    "web/package.json",
    JSON.stringify({ scripts: { build: "node build.mjs", test: 'node -e "process.exit(1)"', "test:server": 'node -e ""' } }),
  );
  s.git("commit", "-q", "-am", "A failing test");
  s.git("push", "-q", "origin", "main");
  stopped(s, s.release(["--apply"]), /PROBLEM: a test failed:/);

  s = sandbox();
  stopped(
    s,
    s.release(["--apply"], { FAKE_CLASP_VERSION: "2.4.2" }),
    /PROBLEM: this needs clasp 3 or later; this one is 2\.4\.2/,
  );

  s = sandbox();
  stopped(
    s,
    s.release(["--apply"], { FAKE_CLASP_SIGNED_OUT: "1" }),
    /PROBLEM: clasp is not signed in: clasp login, as the scripts' owner/,
  );
});

test("a dry run goes on past a problem, to show everything a release would meet", () => {
  const s = sandbox();
  s.file("exporter/Code.js", "function exporter() { return 1; }\n");
  const r = s.release(["--skip-tests"]);
  assert.equal(r.status, 1);
  assert.match(r.out, /PROBLEM: the checkout has changes that aren't committed/);
  assert.match(r.out, /-- exporter: .*\n(.*\n){2}  differs from the script \(1 file\):\n    changed: Code\.js\n  would push it/);
  assert.match(r.out, /== Step 5: the tag \(yours to make\)\n  not yet: the tag goes on the release commit/);
  assert.doesNotMatch(r.out, /git tag v0\.2\.0/);
  assert.match(r.out, /== 1 problem\(s\) above: a run with --apply would stop at them\.\n$/);
  s.done();
});

test("CI that can't be asked is said, and doesn't stop a release", () => {
  const s = sandbox();
  rmSync(join(s.state, "ci.json"));
  const r = s.release(["--apply", "--skip-tests"]);
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /gh could not ask CI \(not signed in, or offline\): check the commit's checks on GitHub/);
  s.done();
});

test("the tag is checked against origin: there already, on another commit, or checked out", () => {
  // already tagged at this commit: nothing to tag
  let s = sandbox();
  s.git("tag", "-a", "v0.2.0", "-m", "v0.2.0");
  s.git("push", "-q", "origin", "v0.2.0");
  let r = s.release(["--skip-tests"]);
  assert.equal(r.status, 0, r.out);
  assert.match(r.out, /v0\.2\.0 is on origin, at this commit/);
  assert.match(r.out, /v0\.2\.0 is already on origin at this commit: nothing to do/);
  assert.doesNotMatch(r.out, /git tag v0\.2\.0/);
  // main has moved on since the tag, with no new release commit: not a release
  const released = s.head();
  s.file("exporter/Code.js", "function exporter() { return 1; }\n");
  s.git("commit", "-q", "-am", "After the release");
  s.git("push", "-q", "origin", "main");
  r = s.release(["--apply", "--skip-tests"]);
  assert.equal(r.status, 1);
  assert.ok(r.out.includes(`PROBLEM: v0.2.0 is already on origin at ${released.slice(0, 7)}, another commit.`), r.out);
  assert.match(r.out, /git checkout v0\.2\.0/);
  assert.deepEqual(s.calls("clasp push"), []);
  // the tag checked out: that release can be deployed again, whatever main has since
  s.git("checkout", "-q", "v0.2.0");
  r = s.release(["--apply", "--skip-tests"]);
  assert.equal(r.status, 0, r.out);
  assert.deepEqual(s.calls("clasp push"), ["clasp push @web"]);
  s.done();
});

test("the web app's deployment is the one versioned deployment, or the one named", () => {
  let s = sandbox();
  s.deployments([
    { deploymentId: "head-deployment" },
    { deploymentId: "web-app", versionNumber: 1, description: "v0.1.0" },
    { deploymentId: "an-old-one", versionNumber: 1 },
  ]);
  let r = s.release(["--apply", "--skip-tests"]);
  assert.equal(r.status, 1);
  assert.match(r.out, /deployment web-app serves version 1 \(v0\.1\.0\)\n  deployment an-old-one serves version 1\n/);
  assert.match(r.out, /PROBLEM: the views' script has several deployments: name the web app's with --deployment <id>/);
  assert.deepEqual(s.calls("update-deployment"), []);
  r = s.release(["--apply", "--skip-tests", "--deployment", "nope"]);
  assert.match(r.out, /PROBLEM: the views' script has no deployment nope/);
  r = s.release(["--apply", "--skip-tests", "--deployment", "web-app"]);
  assert.equal(r.status, 0, r.out);
  assert.deepEqual(s.calls("update-deployment"), ["clasp update-deployment web-app --description v0.2.0 --json @web"]);
  assert.deepEqual(s.served()[2], { deploymentId: "an-old-one", versionNumber: 1 });
  s.done();

  s = sandbox();
  s.deployments([{ deploymentId: "head-deployment" }]);
  r = s.release(["--apply", "--skip-tests"]);
  assert.equal(r.status, 1);
  assert.match(r.out, /PROBLEM: the views have no web app deployment yet: make one in the script editor/);
  s.done();
});

test("a folder with no .clasp.json, and a push that fails, are problems of their own and the rest goes on", () => {
  let s = sandbox();
  rmSync(join(s.repo, "nudge", ".clasp.json"));
  let r = s.release(["--apply", "--skip-tests"]);
  assert.equal(r.status, 1);
  assert.match(r.out, /PROBLEM: nudge\/\.clasp\.json is missing.*node tools\/clasp_projects\.mjs --apply writes it/);
  assert.deepEqual(s.holds("views"), BUILD); // the views were still released
  assert.equal(s.served()[1].versionNumber, 2);
  s.done();

  s = sandbox();
  writeFileSync(join(s.state, "scripts", "views", "refuse-push"), "");
  r = s.release(["--apply", "--skip-tests"]);
  assert.equal(r.status, 1);
  assert.match(r.out, /PROBLEM: the push failed:\n {4}Push failed: the API is not answering/);
  assert.match(r.out, /PROBLEM: the web app was left as it is: the views' script doesn't hold this checkout \(step 3\)/);
  assert.equal(s.served()[1].versionNumber, 1);
  s.done();
});

test("a push clasp skips is caught: the script is read back after every push", () => {
  // a manifest clasp would not overwrite without a terminal to ask in: the tool pushes it with --force only under
  // --permissions, so here the fake is made to skip as clasp does, by taking the flag away from what it sees
  const s = sandbox();
  const clasp = join(s.state, "..", "bin", "clasp");
  writeFileSync(clasp, readFileSync(clasp, "utf8").replace('!argv.includes("--force")', "true"));
  s.file("exporter/appsscript.json", MANIFEST(["drive.file", "gmail.readonly"]));
  s.git("commit", "-q", "-am", "The exporter reads mail");
  s.git("push", "-q", "origin", "main");
  const r = s.release(["--apply", "--permissions", "--skip-tests"]);
  assert.equal(r.status, 1);
  assert.match(
    r.out,
    /PROBLEM: after the push the script still differs from the checkout:\n {4}changed: appsscript\.json\n {4}Skipping push\./,
  );
  s.done();
});
