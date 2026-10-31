// Copies what tsc does not compile into dist/, the folder clasp pushes.
import { copyFileSync } from "node:fs";

for (const [from, to] of [["appsscript.json", "dist/appsscript.json"], ["src/Index.html", "dist/Index.html"]]) {
  copyFileSync(from, to);
}
