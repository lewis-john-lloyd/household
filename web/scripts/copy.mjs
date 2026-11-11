// Copies what tsc does not compile into dist/, the folder clasp pushes, and inlines the browser's code into the
// page where Index.html marks it: the compiled budgetview.js and edits.js, and the page's own modules (src/page, compiled
// to build/page by tsconfig.page.json) in the order below, so the page and the tests run the same code.
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

// The page's modules share one script, so their top-level code runs in this order (startup last).
export const PAGE = [
  "shell",
  "cashflow",
  "budget",
  "budgetviews",
  "drawer",
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
page = inline(page, "/* page.js */", PAGE.map((m) => readFileSync(`build/page/page/${m}.js`, "utf8")).join("\n"));
writeFileSync("dist/Index.html", page);
