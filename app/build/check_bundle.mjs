// Check the single-file pages built by app/build/build_single.py (docs/product_design.md section 16, PW-01 and PW-02,
// plus the identity spot check of the single-file bundle).
// Static checks on the page file: the open page holds no GFW name or field (the shell scanned as text, the data parts
// as JSON keys and string values, base64 payloads skipped); no 12-character prefix of any value in the repo's .env
// (read in memory only; a hit prints the variable name, never a value); no em or en dash in the shell.
// Browser checks: opens each page over file:// in the preinstalled Chromium at 1280 x 800 and 390 x 844, dark and
// light, and visits #/leads, #/map, one Contact of each AIS status present (the matched one from the newest live pass
// when there is one), one Vessel, one Light, one Lead, the newest live Pass and #/about. Fails on any console error,
// page error, failed request, request whose URL is not file:, data: or blob:, a missing caveat banner (top and bottom,
// meta.caveat_short) on any view, an EEZ layer on at load on the map, a theme that does not apply, or a horizontal
// scroll. Then decodes 50 contacts per page with the frontend's own embedded adapter (bundled in memory from
// app/frontend/src with rolldown and added to the page as an inline script) and compares their D1 fields, and for live
// contacts their identification fields, with the backend API records that the builder saved in
// out/spotcheck_<build>.json (numbers within the column's stated scale, everything else equal). The object context
// (README reading 13 block) of the sampled contacts and of 20 lights is compared with the API the same way (the fields
// the page carries; time, region, cell, unit, valid time, source; caveat starting with the API's), and the Contact and
// Light pages visited must show the Context table where the API has context and the "no context" line only where not.
// Run: NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
//        node app/build/check_bundle.mjs [out_dir] [--build=open|research] [--shots=DIR]
// Never run playwright install: the preinstalled browser is used.
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const here = dirname(fileURLToPath(import.meta.url));
const flags = process.argv.slice(2).filter((a) => a.startsWith("--"));
const positional = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const outDir = resolve(positional[0] || resolve(here, "out"));
const only = (flags.find((f) => f.startsWith("--build=")) || "").slice(8) || null;
const shotDir = (flags.find((f) => f.startsWith("--shots=")) || "").slice(8) || resolve(outDir, "shots");
mkdirSync(shotDir, { recursive: true });
const builds = only ? [only] : ["open", "research"];
const RUNS = [
  { name: "desktop", width: 1280, height: 800, theme: "dark" },
  { name: "desktop", width: 1280, height: 800, theme: "light" },
  { name: "phone", width: 390, height: 844, theme: "dark" },
  { name: "phone", width: 390, height: 844, theme: "light" },
];
// Identification fields of a live contact that live in its record (README reading 16), compared when the API has them;
// identity_label as the page shows it (the frontend's identityLabel rule).
const LIVE_ID_FIELDS = ["review_note", "identity_label", "match_ambiguous", "ambiguous_mmsi", "az_shift_m", "match_alt_dist_m"];
// Keys of base64 payloads in the bundle encodings (contract 6.2): skipped by the open-page name scan.
const PAYLOAD_KEYS = new Set(["b", "p", "q", "xy", "ring", "feat"]);
const failures = [];
const fail = (m) => { failures.push(m); console.error("FAIL " + m); };
const ok = (m) => console.log("ok   " + m);
const allowed = (u) => /^(file|data|blob):/.test(u);

// 12-character prefixes of the .env values (never printed); the same rule as scs_bundle/checks.py env_prefixes.
function envPrefixes() {
  const p = resolve(here, "..", "..", ".env");
  const out = {};
  if (!existsSync(p)) return out;
  for (let line of readFileSync(p, "utf8").split(/\r?\n/)) {
    line = line.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const i = line.indexOf("=");
    const k = line.slice(0, i).replace(/^export\s+/, "").trim();
    const v = line.slice(i + 1).trim().replace(/^["']|["']$/g, "");
    if (v.length >= 8) out[k] = v.slice(0, 12);
  }
  return out;
}

// The page split into the shell (everything outside the data parts) and the parsed parts.
function splitPage(html) {
  const parts = {};
  const re = /<script type="application\/json" id="scs-part-([a-z]+)">([\s\S]*?)<\/script>/g;
  let shell = "";
  let last = 0;
  let m;
  while ((m = re.exec(html))) {
    shell += html.slice(last, m.index);
    last = re.lastIndex;
    parts[m[1]] = JSON.parse(m[2]);
  }
  shell += html.slice(last);
  return { shell, parts };
}

// JSON keys and string values that name GFW, base64 payloads and data URIs skipped.
function gfwHits(obj, path = "", out = []) {
  if (Array.isArray(obj)) obj.forEach((v, i) => gfwHits(v, `${path}[${i}]`, out));
  else if (obj && typeof obj === "object") {
    for (const [k, v] of Object.entries(obj)) {
      if (/gfw/i.test(k)) out.push(`key ${path}.${k}`);
      if (PAYLOAD_KEYS.has(k) && typeof v === "string") continue;
      gfwHits(v, `${path}.${k}`, out);
    }
  } else if (typeof obj === "string" && !obj.startsWith("data:") && (/gfw/i.test(obj) || /global fishing watch/i.test(obj))) {
    out.push(`value ${path}: ${obj.slice(0, 60)}`);
  }
  return out;
}

// The frontend's embedded adapter as an IIFE (rolldown from app/frontend/node_modules; nothing installed, nothing written).
async function adapterScript() {
  const { rolldown } = await import(pathToFileURL(resolve(here, "..", "frontend", "node_modules", "rolldown", "dist", "index.mjs")).href);
  const bundle = await rolldown({ input: resolve(here, "adapter_entry.ts"), platform: "browser", logLevel: "silent" });
  const { output } = await bundle.generate({ format: "iife" });
  await bundle.close();
  return output[0].code;
}

function close(a, b, tol) {
  if (a === null || a === undefined || b === null || b === undefined) return (a ?? null) === (b ?? null);
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= (tol || 0) + 1e-9 * Math.max(1, Math.abs(a));
  return a === b;
}

const prefixes = envPrefixes();
const script = await adapterScript();
const browser = await chromium.launch({ args: ["--disable-gpu"] });
const summary = {};
try {
  for (const build of builds) {
    const page_path = resolve(outDir, `scs_vessel_watch_${build}.html`);
    const spotPath = resolve(outDir, `spotcheck_${build}.json`);
    if (!existsSync(page_path)) { fail(`${build}: page not found ${page_path}`); continue; }
    const spot = existsSync(spotPath) ? JSON.parse(readFileSync(spotPath, "utf8")) : null;
    if (!spot) fail(`${build}: ${spotPath} missing (run build_single.py)`);
    const url = pathToFileURL(page_path).href;
    const res = { bytes: 0, routes: 0, requests_checked: 0, spot: null, static: {} };
    // ------------------------------------------------------------ static checks on the file
    const html = readFileSync(page_path, "utf8");
    res.bytes = Buffer.byteLength(html, "utf8");
    if (res.bytes > 15000000) fail(`${build}: page ${res.bytes} bytes over the 15,000,000 byte cap`);
    const { shell, parts } = splitPage(html);
    const meta = parts.meta || {};
    const caveatShort = meta.caveat_short || "Dark = no AIS match. Not evidence of illegal activity.";
    const credHits = Object.entries(prefixes).filter(([, p]) => p && html.includes(p)).map(([k]) => k);
    res.static.credential_prefixes_checked = Object.keys(prefixes).length;
    res.static.credential_hits = credHits;
    if (credHits.length) fail(`${build}: credential prefix of ${credHits.join(", ")} found in the page`);
    else ok(`${build}: no prefix of the ${Object.keys(prefixes).length} .env values in the page`);
    res.static.shell_dashes = (shell.match(/[\u2013\u2014]/g) || []).length;
    if (build === "open") {
      const shellHits = (shell.match(/gfw|global fishing watch/gi) || []).length;
      const dataHits = gfwHits(parts);
      res.static.gfw_in_shell = shellHits;
      res.static.gfw_in_data = dataHits.length;
      if (shellHits) fail(`open: the shell names GFW ${shellHits} time(s)`);
      for (const h of dataHits.slice(0, 10)) fail(`open: GFW in a data part: ${h}`);
      if (!shellHits && !dataHits.length) ok("open: no GFW name or field in the page (shell text, data keys and values)");
      const ro = JSON.stringify(parts).match(/"research_only":true/g) || [];
      if (ro.length) fail(`open: ${ro.length} research_only true values`);
    }
    const layers = (parts.geo && parts.geo.layers) || {};
    if (layers.eez || Object.entries(layers).some(([k, v]) => k.startsWith("eez") && v.kind === "polygon")) fail(`${build}: EEZ polygons in the geo part`);
    // ------------------------------------------------------------ ids to visit, read through the adapter
    let ids = null;
    {
      const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await context.newPage();
      await page.goto(url + "#/leads", { waitUntil: "load" });
      await page.waitForSelector(".scs-app", { timeout: 60000 });
      await page.addScriptTag({ content: script });
      const statusIds = {};
      if (spot) {
        for (const s of spot.status_present) {
          const pool = s === "matched" && (spot.live_matched || []).length ? spot.live_matched : spot.det_ids;
          statusIds[s] = pool.find((d) => spot.records[d].ais_status === s) || null;
        }
      }
      ids = await page.evaluate(async ([statusIds, spot]) => {
        const A = new window.__scsCheck.EmbeddedAdapter();
        const out = { contacts: statusIds };
        const lead = (await A.leads({ state: "all", limit: 1 })).items[0];
        out.lead = lead ? lead.lead_id : null;
        // a light of the spot sample with object context in the API (README reading 13), else the first light
        const spotLights = Object.entries(((spot || {}).object_context || {}).lights || {}).filter(([, c]) => c).map(([k]) => k);
        const light = spotLights.length ? { light_id: spotLights[0] } : (await A.lights({ limit: 1 })).items[0];
        out.light = light ? light.light_id : null;
        const passes = (await A.passes()).items;
        const live = passes.filter((p) => p.pass_id.startsWith("live_"));
        // the live pass with the most matched contacts (the identification demo), and the newest live pass
        const nm = (p) => ((p.n_contacts || {}).matched || 0);
        const best = [...live].sort((a, b) => nm(b) - nm(a))[0];
        out.pass = best && nm(best) > 0 ? best.pass_id : live.length ? live[live.length - 1].pass_id : (passes[0] || {}).pass_id || null;
        out.pass_newest = live.length && live[live.length - 1].pass_id !== out.pass ? live[live.length - 1].pass_id : null;
        // the vessel the matched contact references (identity by reference), else the first vessel row
        const m = statusIds.matched ? await A.contact(statusIds.matched) : null;
        const vp = JSON.parse(document.getElementById("scs-part-vessels").textContent);
        const v = window.__scsCheck.decodePart(vp);
        out.vessel = (m && m.vessel_key && (await A.vessel(m.vessel_key)) ? m.vessel_key : null) || (v.n ? v.get.vessel_key(0) : null);
        return out;
      }, [statusIds, spot ? { object_context: spot.object_context } : null]);
      // -------------------------------------------------------- spot check: adapter record versus API record
      if (spot) {
        const got = await page.evaluate(async (dets) => {
          const A = new window.__scsCheck.EmbeddedAdapter();
          const out = {};
          for (const d of dets) {
            const c = await A.contact(d);
            // the label the page shows (identity.ts identityLabel: the record's own, else the aisstream label by source)
            if (c) c.identity_label = window.__scsCheck.identityLabel(c);
            out[d] = c;
          }
          return out;
        }, spot.det_ids);
        let fields = 0, equal = 0, idFields = 0;
        const mismatches = [];
        for (const d of spot.det_ids) {
          const api = spot.records[d];
          const rec = got[d];
          if (!rec) { mismatches.push({ det_id: d, field: "*", api: "record", page: "missing" }); continue; }
          for (const f of spot.d1_fields) {
            fields++;
            if (close(api[f], rec[f], (spot.tolerance || {})[f])) equal++;
            else mismatches.push({ det_id: d, field: f, api: api[f], page: rec[f] });
          }
          const extra = (spot.live_fields || {})[d] || {};
          for (const f of LIVE_ID_FIELDS) {
            if (!(f in extra)) continue;
            idFields++;
            const want = extra[f];
            const have = rec[f] === undefined ? null : rec[f];
            const tol = f.endsWith("_m") ? 0.5 : 0;
            if (!close(want, have, tol)) mismatches.push({ det_id: d, field: f, api: want, page: have });
          }
        }
        const byStatus = Object.fromEntries(spot.status_present.map((s) => [s, spot.det_ids.filter((d) => spot.records[d].ais_status === s).length]));
        res.spot = { contacts: spot.det_ids.length, fields, equal, live_id_fields: idFields, mismatches: mismatches.length, by_status: byStatus,
                     live_matched: (spot.live_matched || []).length };
        if (mismatches.length) {
          for (const m of mismatches.slice(0, 20)) fail(`${build} spot: ${m.det_id} ${m.field}: API ${JSON.stringify(m.api)} page ${JSON.stringify(m.page)}`);
        } else ok(`${build} spot: ${spot.det_ids.length} contacts, ${fields} D1 values and ${idFields} live identification values equal to the API records`);
        // identity: every matched contact in the sample shows its vessel name and MMSI
        const matched = spot.det_ids.filter((d) => spot.records[d].ais_status === "matched");
        const named = matched.filter((d) => got[d] && got[d].vessel_name === spot.records[d].vessel_name && got[d].mmsi === spot.records[d].mmsi);
        res.spot.matched = matched.length;
        res.spot.matched_identity_equal = named.length;
        if (named.length !== matched.length) fail(`${build} spot: ${matched.length - named.length} matched contacts lost their identity`);
        if ((spot.live_matched || []).length) ok(`${build} spot: ${spot.live_matched.length} matched live contacts (newest pass first) with name and MMSI equal to the API`);
        // object context (README reading 13): the page's block against the API record, contacts and lights
        const oc = spot.object_context || { contacts: {}, lights: {} };
        const gotCtx = await page.evaluate(async (oc) => {
          const A = new window.__scsCheck.EmbeddedAdapter();
          const out = { contacts: {}, lights: {} };
          for (const d of Object.keys(oc.contacts || {})) { const c = await A.contact(d); out.contacts[d] = c ? c.object_context ?? null : "missing"; }
          for (const l of Object.keys(oc.lights || {})) { const c = await A.light(l); out.lights[l] = c ? c.object_context ?? null : "missing"; }
          return out;
        }, oc);
        let ctxObjects = 0, ctxWith = 0, ctxValues = 0;
        const ctxBad = [];
        for (const kind of ["contacts", "lights"]) {
          const want = (spot.context_fields || {})[kind] || [];
          for (const [id, api] of Object.entries(oc[kind] || {})) {
            ctxObjects++;
            const pg = gotCtx[kind][id];
            if (pg === "missing") { ctxBad.push(`${kind} ${id}: object not in the page`); continue; }
            if (!api) { if (pg) ctxBad.push(`${kind} ${id}: page has context, API null`); continue; }
            ctxWith++;
            if (!pg) { ctxBad.push(`${kind} ${id}: API has context, page shows none`); continue; }
            for (const k of ["time_utc", "region"]) if ((api[k] ?? null) !== (pg[k] ?? null)) ctxBad.push(`${kind} ${id} ${k}: API ${api[k]} page ${pg[k]}`);
            const cellWant = (spot.context_cell_id || {})[kind] ? api.cell_id ?? null : null;
            if ((pg.cell_id ?? null) !== cellWant) ctxBad.push(`${kind} ${id} cell_id: API ${api.cell_id} page ${pg.cell_id}`);
            if (!String(pg.caveat || "").startsWith(api.caveat || "")) ctxBad.push(`${kind} ${id}: caveat differs from the API's`);
            for (const f of Object.keys(pg.fields || {})) if (!want.includes(f)) ctxBad.push(`${kind} ${id}: field ${f} not in the block's list`);
            for (const f of want) {
              const a = (api.fields || {})[f], p = (pg.fields || {})[f];
              ctxValues++;
              if (!p) { ctxBad.push(`${kind} ${id} ${f}: missing in the page`); continue; }
              if (!close(a.value, p.value, (spot.context_tolerance || {})[f])) ctxBad.push(`${kind} ${id} ${f}: API ${a.value} page ${p.value}`);
              for (const k of ["unit", "time", "src"]) if ((a[k] ?? null) !== (p[k] ?? null)) ctxBad.push(`${kind} ${id} ${f}.${k}: API ${a[k]} page ${p[k]}`);
            }
          }
        }
        res.spot.object_context = { objects: ctxObjects, with_context: ctxWith, field_values: ctxValues, mismatches: ctxBad.length,
                                    fields: spot.context_fields || {} };
        if (ctxBad.length) for (const m of ctxBad.slice(0, 20)) fail(`${build} context: ${m}`);
        else ok(`${build} context: ${ctxObjects} objects (${ctxWith} with context), ${ctxValues} field values, unit, valid time and source equal to the API`);
      }
      await context.close();
    }
    // ------------------------------------------------------------ routes at two sizes and two themes
    const routes = [["#/leads", "leads"], ["#/map", "map"]];
    for (const [st, d] of Object.entries(ids.contacts || {})) if (d) routes.push([`#/contact/${encodeURIComponent(d)}`, `contact_${st}`]);
    if (ids.vessel) routes.push([`#/vessel/${encodeURIComponent(ids.vessel)}`, "vessel"]);
    if (ids.light) routes.push([`#/light/${encodeURIComponent(ids.light)}`, "light"]);
    if (ids.lead) routes.push([`#/lead/${encodeURIComponent(ids.lead)}`, "lead"]);
    if (ids.pass) routes.push([`#/pass/${encodeURIComponent(ids.pass)}`, "pass"]);
    if (ids.pass_newest) routes.push([`#/pass/${encodeURIComponent(ids.pass_newest)}`, "pass_newest"]);
    routes.push(["#/about", "about"]);
    for (const what of ["vessel", "light", "lead", "pass"]) if (!ids[what]) fail(`${build}: no ${what} to visit`);
    res.ids = ids;
    for (const vp of RUNS) {
      const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 1, colorScheme: vp.theme });
      await context.addInitScript((t) => { try { window.localStorage.setItem("scs.theme", t); } catch (e) { /* storage blocked */ } }, vp.theme);
      const page = await context.newPage();
      const label = `${build} ${vp.name} ${vp.width} ${vp.theme}`;
      const errors = [];
      page.on("console", (m) => { if (m.type() === "error") errors.push(`console.error: ${m.text()}`); });
      page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
      page.on("request", (r) => { res.requests_checked++; if (!allowed(r.url())) errors.push(`request outside file:, data:, blob: ${r.url().slice(0, 120)}`); });
      page.on("requestfailed", (r) => errors.push(`failed request ${r.url().slice(0, 120)} (${r.failure()?.errorText})`));
      for (const [route, name] of routes) {
        const n0 = errors.length;
        await page.goto(url + route, { waitUntil: "load" });
        await page.waitForSelector(".scs-app", { timeout: 60000 });
        await page.waitForTimeout(name === "map" ? 1500 : 700);
        const theme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
        if (theme !== vp.theme) errors.push(`${name}: theme ${theme}, expected ${vp.theme}`);
        const sw = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
        if (sw[0] > sw[1]) errors.push(`${name}: horizontal scroll ${sw[0]} > ${sw[1]}`);
        const banners = await page.$$eval("[data-banner]", (els) => els.map((e) => ({ pos: e.getAttribute("data-banner"), text: e.textContent || "", visible: !!(e.offsetWidth && e.offsetHeight) })));
        for (const pos of ["top", "bottom"]) {
          const b = banners.find((x) => x.pos === pos);
          if (!b || !b.visible || !b.text.includes(caveatShort)) errors.push(`${name}: ${pos} caveat banner missing`);
        }
        if (name === "map") {
          const eez = await page.evaluate(() => document.querySelector(".scs-map")?.getAttribute("data-eez-on"));
          if (eez !== "0") errors.push(`map: EEZ layer state at load is ${eez}`);
          const toggle = await page.$("[data-layer='eez_boundaries'] input[type=checkbox]");
          if (toggle && (await toggle.isChecked())) errors.push("map: EEZ toggle checked at load");
        }
        if (spot && (name.startsWith("contact_") || name === "light")) {
          const oc = spot.object_context || {};
          const id = name === "light" ? ids.light : ids.contacts[name.slice(8)];
          const api = name === "light" ? (oc.lights || {})[id] : (oc.contacts || {})[id];
          if (api !== undefined) {
            const st = await page.evaluate(() => [document.querySelector("[data-context='1']") !== null, document.querySelector("[data-context='none']") !== null]);
            if (api && (!st[0] || st[1])) errors.push(`${name}: the API has object context but the page shows none`);
            if (!api && st[0]) errors.push(`${name}: the page shows object context the API does not have`);
          }
        }
        if (name.startsWith("contact_matched") && spot) {
          const d = ids.contacts.matched;
          const nm = spot.records[d] && spot.records[d].vessel_name;
          if (nm && !(await page.evaluate((s) => (document.body.innerText || "").includes(s), nm))) errors.push(`${name}: vessel name ${nm} not shown`);
        }
        if (vp.theme === "dark") await page.screenshot({ path: resolve(shotDir, `${build}_${vp.name}_${name}.png`), fullPage: false });
        res.routes++;
        if (errors.length === n0) ok(`${label} ${name}`);
      }
      for (const e of errors) fail(`${label}: ${e}`);
      await context.close();
    }
    summary[build] = res;
  }
} finally {
  await browser.close();
}
writeFileSync(resolve(outDir, "check_bundle.json"), JSON.stringify({ summary, failures }, null, 1));
console.log(JSON.stringify(summary, null, 1));
if (failures.length) {
  console.error(`\n${failures.length} failure(s)`);
  process.exit(1);
}
console.log("\ncheck_bundle passed");
