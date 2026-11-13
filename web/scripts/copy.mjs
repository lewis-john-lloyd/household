// Copies what tsc does not compile into dist/, the folder clasp pushes, and inlines the browser's code into the
// page where Index.html marks it: the compiled budgetview.js and edits.js, and the page's own modules (src/page, compiled
// to build/page by tsconfig.page.json) in the order below, so the page and the tests run the same code.
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

// The page's modules share one script, so their top-level code runs in this order (startup last).
export const PAGE = [
  "shell",
  "cashflow",
  "budget",
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
  if (!page.includes(mark)) throw new Error(`src/Index.html has no ${mark}`);
  return page.replace(mark, () => code);
}

copyFileSync("appsscript.json", "dist/appsscript.json");
let page = readFileSync("src/Index.html", "utf8");
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
writeFileSync("dist/Index.html", page);
