// Copies what tsc does not compile into dist/, the folder clasp pushes, and inlines the compiled
// budgetview.js into the page where Index.html marks it, so the page and the tests run the same code.
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

copyFileSync("appsscript.json", "dist/appsscript.json");
const MARK = "/* budgetview.js */";
const page = readFileSync("src/Index.html", "utf8");
if (!page.includes(MARK)) throw new Error(`src/Index.html has no ${MARK} for the budget view`);
writeFileSync("dist/Index.html", page.replace(MARK, () => readFileSync("dist/budgetview.js", "utf8")));
