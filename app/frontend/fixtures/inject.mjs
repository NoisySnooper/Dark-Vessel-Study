// Inject the fixture bundle into the single-file page as a test page (contract 6.1 layout).
// Usage: node fixtures/inject.mjs [dist-single/index.html] [fixtures/bundle_small.json] <out.html>
// Writes one <script type="application/json" id="scs-part-NAME"> per part before </body>. Scratch output, not committed.
import { readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const out = args.pop();
if (!out) {
  console.error("usage: node fixtures/inject.mjs [page.html] [bundle.json] out.html");
  process.exit(2);
}
const page = args[0] || resolve(here, "..", "dist-single", "index.html");
const bundle = args[1] || resolve(here, "bundle_small.json");

const html = readFileSync(page, "utf8");
const data = JSON.parse(readFileSync(bundle, "utf8"));
const parts = data.parts || data;
let scripts = "";
for (const [name, part] of Object.entries(parts)) {
  // Escape the one sequence that could end the element early.
  const json = JSON.stringify(part).replace(/<\//g, "<\\/");
  scripts += `<script type="application/json" id="scs-part-${name}">${json}</script>\n`;
}
const idx = html.lastIndexOf("</body>");
if (idx < 0) throw new Error("no </body> in " + page);
const result = html.slice(0, idx) + scripts + html.slice(idx);
writeFileSync(out, result);
console.log(`wrote ${out} (${(result.length / 1e6).toFixed(2)} MB; page ${(html.length / 1e6).toFixed(2)} MB; parts ${Object.keys(parts).join(", ")})`);
