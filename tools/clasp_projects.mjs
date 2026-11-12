#!/usr/bin/env node
// Writes the .clasp.json each Apps Script folder needs, on a fresh checkout. The files name each script by its
// id, which stays out of this public repo (.gitignore), so this looks the ids up instead: it asks clasp for the
// scripts your Google account owns and finds each one by its exact title, as the docs created them.
//
// Needs clasp (npm i -g @google/clasp) signed in as the scripts' owner (clasp login).
//
// Usage, from the repo's top folder:
//   node tools/clasp_projects.mjs            dry run: looks everything up, shows each file it would write
//   node tools/clasp_projects.mjs --apply    writes them; a .clasp.json already there is left as it is
//   node tools/clasp_projects.mjs --apply --replace    writes them, replacing any already there

import { execFileSync } from "node:child_process";
import { existsSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// each folder, the title its script was created with (docs/views.md, docs/apps-scripts.md), and where its files are
const PROJECTS = [
  { folder: "web", title: "Household views", rootDir: "dist" },
  { folder: "exporter", title: "Household receipt exporter", rootDir: "." },
  { folder: "nudge", title: "Household weekly email", rootDir: "." },
  { folder: "assessor", title: "Household county check", rootDir: "." },
];

const apply = process.argv.includes("--apply");
const replace = process.argv.includes("--replace");

console.log(apply ? "== Writing the .clasp.json files." : "== Dry run: nothing is written. Run again with --apply to write.");

console.log("\n== Step 1: ask clasp for your scripts");
console.log("  run: clasp list-scripts --json --noShorten");
let scripts;
try {
  scripts = JSON.parse(execFileSync("clasp", ["list-scripts", "--json", "--noShorten"], { encoding: "utf8" }));
} catch (e) {
  console.error("  clasp failed. Is it installed (npm i -g @google/clasp) and signed in (clasp login)?");
  console.error("  " + String(e.message || e).split("\n")[0]);
  process.exit(1);
}
console.log(`  found ${scripts.length} scripts`);

console.log("\n== Step 2: each folder's script, by its exact title");
let problems = 0;
for (const p of PROJECTS) {
  const file = join(p.folder, ".clasp.json");
  console.log(`\n-- ${p.folder}: "${p.title}", files from ${p.rootDir === "." ? "the folder itself" : p.folder + "/" + p.rootDir}`);
  const matches = scripts.filter((s) => s.name === p.title);
  if (matches.length !== 1) {
    console.log(
      matches.length
        ? `  problem: ${matches.length} scripts have this title; keep one, or write ${file} by hand`
        : "  problem: no script has this title; create it as the docs say, or write the file by hand",
    );
    problems++;
    continue;
  }
  const settings = { scriptId: matches[0].id, rootDir: p.rootDir };
  const text = JSON.stringify(settings, null, 2) + "\n";
  if (existsSync(file) && !replace) {
    console.log(`  ${file} is already there: left as it is (--replace writes over it)`);
    continue;
  }
  if (!apply) {
    console.log(`  would write ${file}:`);
    console.log(text.replace(/^/gm, "    ").trimEnd());
    continue;
  }
  writeFileSync(file, text);
  console.log(`  wrote ${file}`);
}

console.log(
  problems
    ? `\n== ${problems} folder(s) need a look (above).`
    : apply
      ? "\n== Done. `npm run push` in web, and `clasp push` in the others, now know their scripts."
      : "\n== Dry run done. If each folder found its script, run again with --apply.",
);
process.exit(problems ? 1 : 0);
