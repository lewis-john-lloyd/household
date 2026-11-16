// Copies what tsc does not compile into dist/, the folder clasp pushes, and inlines the browser's code into the
// page where index.html marks it: the compiled budgetview.js and edits.js, and the page's own modules (src/page, compiled
// to build/page by tsconfig.page.json) in the order below, so the page and the tests run the same code.
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

// The page's modules share one script, so their top-level code runs in this order (startup last).
export const PAGE = [
  "shell",
  "cashflow",
  "budget",
  "spendchart",
  "search",
  "budgetwords",
  "budgethead",
  "budgettable",
  "budgetviews",
  "drawer",
  "evidence",
  "working",
  "recurring",
  "jobs",
  "pending",
  "widgets",
  "forms",
  "paycharts",
  "settings",
  "alerts",
  "list",
  "transaction",
  "edittabs",
  "investments",
  "home",
  "equity",
  "taxes",
  "status",
  "retire",
  "main",
];

function inline(page, mark, code) {
  if (!page.includes(mark)) throw new Error(`src/index.html has no ${mark}`);
  return page.replace(mark, () => code);
}

copyFileSync("appsscript.json", "dist/appsscript.json");
// The release these views are built from, for the Status page to set beside the engine's: the version in
// pyproject.toml, which a release commit sets, so the two can't drift.
const release = /^version\s*=\s*"([^"]+)"/m.exec(readFileSync("../pyproject.toml", "utf8"));
if (!release) throw new Error("../pyproject.toml has no version");
writeFileSync(
  "dist/version.js",
  `// Written by scripts/copy.mjs from pyproject.toml.\nvar VIEWS_VERSION = ${JSON.stringify(release[1])};\n`,
);
let page = readFileSync("src/index.html", "utf8");
page = inline(
  page,
  "/* budgetview.js, edits.js */",
  ["budgetview.js", "edits.js"].map((f) => readFileSync(`dist/${f}`, "utf8")).join("\n"),
);
// The page's modules share one script, so a name two of them declare at the top level is one name, and the later
// silently replaces the earlier (TypeScript allows a var declared twice). Refuse to build that.
const declared = new Map();
for (const m of PAGE) {
  const code = readFileSync(`build/page/page/${m}.js`, "utf8");
  for (const [, name] of code.matchAll(/^(?:var|let|const|function|class)\s+([A-Za-z_$][\w$]*)/gm)) {
    if (declared.has(name) && declared.get(name) !== m)
      throw new Error(`src/page/${m}.ts and src/page/${declared.get(name)}.ts both declare ${name} at the top level`);
    declared.set(name, m);
  }
}
page = inline(page, "/* page.js */", PAGE.map((m) => readFileSync(`build/page/page/${m}.js`, "utf8")).join("\n"));
writeFileSync("dist/index.html", page);
