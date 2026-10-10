// Build-time checks, run before `npm run build` and `npm run build:single` (package.json pre-scripts):
// 1. Codes (board D5.1): every lead code the lead builder can emit has a display string in src/app/text.ts:
//    factor names (`"factor": "..."` in src/darkvessel/leads/priority.py), explanation codes (LAWFUL_EXPLANATIONS) and
//    indicator codes (CHANGE_INDICATORS) in src/darkvessel/leads/rules.py. The repo's pytest twin is
//    app/frontend/fixtures/test_text_codes.py, which imports the Python modules instead of parsing them.
// 2. Icons: every icon name the source passes as a string is in the bundled set of src/theme/icons.ts.
// 3. Text: no em or en dash in src/app/text.ts (owner's style rule).
// Exits 1 with a list of what is missing. Outside the repo (no rules.py) check 1 is skipped with a note.
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { resolve, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const front = resolve(here, "..");
const repo = resolve(front, "..", "..");
const problems = [];

// ---------------------------------------------------------------------------------------------- 1. codes
const textTs = readFileSync(join(front, "src/app/text.ts"), "utf8");
function tsMapKeys(name) {
  const m = new RegExp(`export const ${name}: Record<string, string> = \\{([\\s\\S]*?)\\n\\};`).exec(textTs);
  if (!m) {
    problems.push(`text.ts: map ${name} not found`);
    return new Set();
  }
  return new Set([...m[1].matchAll(/^\s+(?:"([^"]+)"|([a-z0-9_]+)):/gm)].map((x) => x[1] || x[2]));
}
function pyDictBlock(src, name) {
  // The top-level dict literal `NAME = {` ... `\n}` and the string keys at its second nesting level.
  const start = src.indexOf(`\n${name} = {`);
  if (start < 0) return null;
  const end = src.indexOf("\n}\n", start);
  const body = src.slice(start, end);
  return new Set([...body.matchAll(/^ {8}"([a-z0-9_]+)":/gm)].map((x) => x[1]));
}
const rulesPy = join(repo, "src/darkvessel/leads/rules.py");
const priorityPy = join(repo, "src/darkvessel/leads/priority.py");
let nCodes = 0;
if (existsSync(rulesPy) && existsSync(priorityPy)) {
  const rules = readFileSync(rulesPy, "utf8");
  const prio = readFileSync(priorityPy, "utf8");
  const lawful = pyDictBlock(rules, "LAWFUL_EXPLANATIONS");
  const change = pyDictBlock(rules, "CHANGE_INDICATORS");
  const factors = new Set([...prio.matchAll(/"factor": "([^"]+)"/g)].map((x) => x[1]));
  const want = [["LAWFUL_TEXT", lawful], ["CHANGE_TEXT", change], ["FACTOR_LABEL", factors]];
  for (const [tsName, codes] of want) {
    if (!codes || codes.size === 0) {
      problems.push(`codes: no ${tsName} codes parsed from the lead builder (layout changed?)`);
      continue;
    }
    const have = tsMapKeys(tsName);
    for (const c of codes) {
      nCodes++;
      if (!have.has(c)) problems.push(`codes: "${c}" has no display string in ${tsName} (src/app/text.ts)`);
    }
  }
} else {
  console.log("check_build: src/darkvessel/leads not found next to the frontend; code check skipped");
}

// ---------------------------------------------------------------------------------------------- 2. icons
const iconsTs = readFileSync(join(front, "src/theme/icons.ts"), "utf8");
const globs = [...iconsTs.matchAll(/generated\/(16|20)px\/paths\/\{([a-z0-9,-]+)\}\.js/g)];
const sets = globs.map((g) => new Set(g[2].split(",")));
if (sets.length !== 2 || [...sets[0]].some((n) => !sets[1].has(n)) || sets[0].size !== sets[1].size) problems.push("icons: the 16 px and 20 px globs in src/theme/icons.ts differ");
const bundled = sets[0] || new Set();
for (const n of bundled) {
  for (const s of ["16", "20"]) {
    if (!existsSync(join(front, `node_modules/@blueprintjs/icons/lib/esm/generated/${s}px/paths/${n}.js`))) problems.push(`icons: no ${s} px paths for "${n}"`);
  }
}
function walk(dir, out = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx?|mjs)$/.test(f)) out.push(p);
  }
  return out;
}
const used = new Map();
for (const f of walk(join(front, "src"))) {
  if (f.endsWith("icons.ts")) continue;
  const src = readFileSync(f, "utf8");
  const add = (n) => used.set(n, (used.get(n) || []).concat(f.slice(front.length + 1)));
  for (const m of src.matchAll(/\b(?:icon|rightIcon|leftIcon)=\{?"([a-z0-9-]+)"/g)) add(m[1]);
  for (const m of src.matchAll(/\b(?:icon|rightIcon|leftIcon)=\{[^}]*?\?\s*"([a-z0-9-]+)"\s*:\s*"([a-z0-9-]+)"/g)) { add(m[1]); add(m[2]); }
  for (const m of src.matchAll(/\bicon:\s*"([a-z0-9-]+)"/g)) add(m[1]);
}
for (const [n, files] of used) if (!bundled.has(n)) problems.push(`icons: "${n}" is used in ${[...new Set(files)].join(", ")} but not bundled in src/theme/icons.ts`);

// ---------------------------------------------------------------------------------------------- 3. dashes
if (/[\u2013\u2014]/.test(textTs)) problems.push("text.ts: em or en dash in UI text");

if (problems.length) {
  console.error("check_build failed:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log(`check_build: ${nCodes} lead codes have display strings; ${used.size} icon names used, ${bundled.size} bundled`);
