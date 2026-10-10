// Smoke check for the SCS Vessel Watch frontend (docs/product_design.md section 16, subset for rounds 2 and 3).
// Single-file mode (default): opens the fixture-injected single file over file:// at 1280x800 and 390x844 in dark and
// light, plus a 1024x800 tablet run and a blocked-storage run; visits the queue, a lead, a contact, a vessel, the map,
// a pass, a cell and the Omnibar. Round 3 adds: the newest live pass with its contacts per AIS status (identity of the
// matched ones with the aisstream label), one Contact page per AIS status present (Identification block, provenance
// chips, ocean Context section or its "no context yet" line), a Light page with its Context section, a Cell page with
// expected activity and both caveats, the Context layers group (every overlay off at load; switching two on draws them
// with a legend and the ocean caveat; shipping as presence), and the queue's newest-live-pass quick filter.
// Stale leads (review of round 3): no L1 lead in the queue may cite a primary contact that is now ambiguous, matched or
// without AIS coverage (a leads file older than the live file's rematch). File mode decodes the bundle and checks the
// queue count, then runs once more on a variant page in which one lead's contact is made ambiguous: the queue drops the
// lead, its page says it is stale and offers no decision, and the contact page has no Lead section. Local-app mode
// reads every L1 lead's primary contact (up to 500 by priority) from the API.
// Local-app mode (--http=URL): the same routes against the running backend at 1280 and 390 px in dark and light, with
// every non-GET API call intercepted so nothing is written. Fails on any console error or page error, a missing
// caveat banner, an EEZ layer on at load, a request off the page's origin, a failed request, a horizontal scroll at
// 390 px, a map that does not fill the centre column, an overlapping tablet layout, or an icon that is not bundled.
// Run: NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node checks/smoke.mjs [page.html] [shot_dir]
//      ... node checks/smoke.mjs --http=http://127.0.0.1:8750 [shot_dir]
// Never run playwright install: the preinstalled browser is used.
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = dirname(fileURLToPath(import.meta.url));
const flags = process.argv.slice(2).filter((a) => a.startsWith("--"));
const positional = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const HTTP = (flags.find((f) => f.startsWith("--http=")) || "").slice("--http=".length).replace(/\/$/, "") || null;
const page_path = positional[0] || resolve(here, "..", "dist-single", "index.fixture.html");
// In --http mode the only positional argument is the screenshot directory.
const shotDir = (HTTP ? positional[0] : positional[1]) || resolve(process.env.SCS_SHOT_DIR || "/tmp/claude-0/-home-user-Dark-Vessel-Study/cec849af-66e9-5eb5-ae39-8ecc39ea8096/scratchpad/frontend");
mkdirSync(shotDir, { recursive: true });
if (!HTTP && !existsSync(page_path)) {
  console.error("page not found: " + page_path + " (build with npm run build:single, then node fixtures/inject.mjs <out>)");
  process.exit(2);
}
const url = HTTP ? HTTP + "/" : "file://" + page_path;
const origin = HTTP ? new URL(HTTP).origin : null;
const mode = HTTP ? "http" : "file";
const CAVEAT_SHORT = "Dark = no AIS match. Not evidence of illegal activity.";
const DASHES = /[\u2013\u2014]/;
const AISSTREAM_LABEL = "live AIS relayed by aisstream.io; terms UNVERIFIED";
const OCEAN_NOTE = "Ocean and weather layers describe the sea, not what any vessel does.";
const RUNS = HTTP
  ? [
    { name: "desktop", width: 1280, height: 800, theme: "dark" }, { name: "desktop", width: 1280, height: 800, theme: "light" },
    { name: "phone", width: 390, height: 844, theme: "dark" }, { name: "phone", width: 390, height: 844, theme: "light" },
  ]
  : [
    { name: "desktop", width: 1440, height: 900, theme: "dark" }, { name: "desktop", width: 1440, height: 900, theme: "light" },
    { name: "desktop", width: 1280, height: 800, theme: "dark" }, { name: "desktop", width: 1280, height: 800, theme: "light" },
    { name: "phone", width: 390, height: 844, theme: "dark" }, { name: "phone", width: 390, height: 844, theme: "light" },
    { name: "tablet", width: 1024, height: 800, theme: "dark" }, { name: "tablet", width: 1024, height: 800, theme: "light" },
    { name: "desktop", width: 1280, height: 800, theme: "dark", blockedStorage: true },
  ];
// Only the run selected with --only=<substring of the run label> (for iterating on one width); all runs by default.
const ONLY = (flags.find((f) => f.startsWith("--only=")) || "").slice("--only=".length) || null;
const PEARL_RIVER = "live_S1D_20261010T1032";
// Documented 4xx in the local app: GET /api/v1/vessels/mmsi:<mmsi> answers 404 for an MMSI that the backend's vessel
// table (data/ais_live.gpkg vessels_latest_4326) does not hold; the page then says "Vessel not in this build". Each
// such URL is listed at the end of the run. Any other 4xx or 5xx fails the run.
const DOCUMENTED_404 = /\/api\/v1\/vessels\/mmsi%3A\d{9}$|\/api\/v1\/vessels\/mmsi:\d{9}$/;
const documented404 = new Set();
const LOW_QUALITY_LABEL = "low-quality pairing, identity not confirmed";
const failures = [];
const contextSeen = {};
const shots = [];
const fail = (msg) => { failures.push(msg); console.error("FAIL " + msg); };
const ok = (msg) => console.log("ok   " + msg);
// Every assertion this run made, by name, with the number of times it was evaluated (printed at the end).
const asserted = new Map();
const A = (name) => asserted.set(name, (asserted.get(name) || 0) + 1);
const allowed = (u) => /^(data|blob):/.test(u) || (HTTP ? u.startsWith(origin + "/") : /^file:/.test(u));
const overlap = (a, b) => !!a && !!b && a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
const rect = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; }, sel);


// Single-file mode: ids picked from the embedded parts (one contact per AIS status, a light, cells with expected rows).
function bundleIds(file) {
  const html = readFileSync(file, "utf8");
  const part = (name) => {
    const m = new RegExp(`<script type="application/json" id="scs-part-${name}">([\\s\\S]*?)</script>`).exec(html);
    return m ? JSON.parse(m[1]) : null;
  };
  const out = { contact: {}, light: null, cells: [] };
  const arr = (col, Ctor) => { const b = Buffer.from(col.b, "base64"); return new Ctor(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); };
  const c = part("contacts");
  if (c) {
    const d = c.columns.det_id;
    const p = (() => { const b = Buffer.from(d.p, "base64"); return new Uint16Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); })();
    const q = (() => { const b = Buffer.from(d.q, "base64"); return new Uint32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); })();
    const st = c.columns.ais_status;
    const conf = c.columns.confidence;
    const sIdx = st.t === "const" ? null : arr(st, st.t === "dict16" ? Uint16Array : Uint8Array);
    const cIdx = conf && conf.t !== "const" ? arr(conf, conf.t === "dict16" ? Uint16Array : Uint8Array) : null;
    for (let i = 0; i < c.n; i++) {
      const status = st.t === "const" ? st.v : st.dict[sIdx[i]];
      if (!status || out.contact[status]) continue;
      if (cIdx && conf.dict[cIdx[i]] === "fixed") continue;
      const w = Array.isArray(d.w) ? (d.w.length === 1 ? d.w[0] : d.w[p[i]]) : d.w;
      out.contact[status] = d.pfx[p[i]] + "_" + String(q[i]).padStart(w, "0");
    }
  }
  const passes = part("passes");
  out.livePasses = ((passes && passes.features) || []).map((f) => f.properties || {}).filter((p) => String(p.pass_id).startsWith("live_") && p.processed)
    .sort((a, b) => Date.parse(b.start_utc) - Date.parse(a.start_utc)).map((p) => p.pass_id);
  const cells = part("cells");
  if (cells) out.cells = Object.keys(cells.records || {}).filter((k) => cells.records[k].expected_activity);
  // first light: <SPP|N20|N21>_<time_utc yyyymmddThhmmss>_<pad(q, w)> (contract 6.2 lightid)
  const l = part("lights");
  if (l && l.n && l.columns.light_id && l.columns.time_utc && l.columns.time_utc.t === "time") {
    const sat = l.columns.satellite;
    const satName = sat.t === "const" ? sat.v : sat.dict[arr(sat, sat.t === "dict16" ? Uint16Array : Uint8Array)[0]];
    const t = arr(l.columns.time_utc, Uint32Array)[0];
    const iso = new Date(Date.parse(l.columns.time_utc.e) + t * 1000).toISOString();
    const q = (() => { const b = Buffer.from(l.columns.light_id.q, "base64"); return new Uint32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); })()[0];
    const code = { "S-NPP": "SPP", "NOAA-20": "N20", "NOAA-21": "N21" }[satName] || satName;
    out.light = `${code}_${iso.replace(/[-:]/g, "").slice(0, 15)}_${String(q).padStart(l.columns.light_id.w || 6, "0")}`;
  }
  return out;
}
const fileIds = HTTP ? null : bundleIds(page_path);

// --------------------------------------------------------------- bundle decoding for the stale-lead checks (file mode)
function partOf(html, name) {
  const m = new RegExp(`<script type="application/json" id="scs-part-${name}">([\\s\\S]*?)</script>`).exec(html);
  return m ? JSON.parse(m[1]) : null;
}
function typedOf(col) {
  const b = Buffer.from(col.b || col.p || "", "base64");
  const buf = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
  return { u32: Uint32Array, i32: Int32Array, u16: Uint16Array, ref16: Uint16Array, dict16: Uint16Array, i16: Int16Array, f32: Float32Array }[col.t] ? new ({ u32: Uint32Array, i32: Int32Array, u16: Uint16Array, ref16: Uint16Array, dict16: Uint16Array, i16: Int16Array, f32: Float32Array }[col.t])(buf) : new Uint8Array(buf);
}
/** Values of a simple column (const, dict8, dict16, bool8, u8 to u32, ref16), null for the column's null code. */
function columnValues(col, n) {
  if (!col) return new Array(n).fill(null);
  if (col.t === "const") return new Array(n).fill(col.v);
  if (col.t === "str") return col.v;
  const a = typedOf(col);
  const na = col.na ?? (col.t === "dict8" || col.t === "bool8" || col.t === "u8" ? 255 : col.t === "u32" ? 4294967295 : 65535);
  return Array.from(a, (x) => (x === na ? null : col.t === "dict8" || col.t === "dict16" ? col.dict[x] : col.t === "bool8" ? x === 1 : x));
}
function detIds(col, n) {
  const pb = Buffer.from(col.p, "base64");
  const qb = Buffer.from(col.q, "base64");
  const p = new Uint16Array(pb.buffer.slice(pb.byteOffset, pb.byteOffset + pb.byteLength));
  const q = new Uint32Array(qb.buffer.slice(qb.byteOffset, qb.byteOffset + qb.byteLength));
  return Array.from({ length: n }, (_, i) => col.pfx[p[i]] + "_" + String(q[i]).padStart(Array.isArray(col.w) ? (col.w.length === 1 ? col.w[0] : col.w[p[i]]) : col.w, "0"));
}
/** Every lead of the bundle with its primary contact's status: {lead_id, lead_type, det_id, ais_status, ambiguous}. */
function bundleLeads(html) {
  const L = partOf(html, "leads");
  const C = partOf(html, "contacts");
  if (!L || !C) return null;
  const ids = detIds(C.columns.det_id, C.n);
  const status = columnValues(C.columns.ais_status, C.n);
  const ambCol = columnValues(C.columns.match_ambiguous, C.n);
  const type = columnValues(L.columns.lead_type, L.n);
  const ptype = columnValues(L.columns.primary_type, L.n);
  const prim = columnValues(L.columns.primary, L.n);
  return Array.from({ length: L.n }, (_, i) => {
    if (ptype[i] !== "contacts" || prim[i] === null) return { lead_id: `${type[i]}-?`, lead_type: type[i], det_id: null };
    const det = ids[prim[i]];
    const rec = (C.records || {})[det] || {};
    const ambiguous = rec.match_ambiguous === true || ambCol[prim[i]] === true || (typeof rec.ambiguous_mmsi === "string" && /\d{9}/.test(rec.ambiguous_mmsi));
    return { lead_id: `${type[i]}-${det}`, lead_type: type[i], det_id: det, ais_status: rec.ais_status || status[prim[i]], ambiguous, row: prim[i] };
  });
}
// The rule of src/app/identity.ts staleLeadReason: an L1 lead stands only on an unmatched, unambiguous contact.
const isStale = (l) => l.lead_type === "L1" && !!l.det_id && (l.ais_status === "matched" || l.ais_status === "no_coverage" || (l.ais_status === "unmatched" && l.ambiguous));
const fileLeads = HTTP ? null : bundleLeads(readFileSync(page_path, "utf8"));
// A hand-checked no_coverage contact (its note may say "not dark"): from the bundle records in file mode.
function noCovNoted(html) {
  const C = partOf(html, "contacts");
  if (!C) return null;
  const ids = detIds(C.columns.det_id, C.n);
  const status = columnValues(C.columns.ais_status, C.n);
  const i = ids.findIndex((d, k) => status[k] === "no_coverage" && typeof (C.records || {})[d]?.review_note === "string");
  return i < 0 ? null : ids[i];
}
const fileNoCovNoted = HTTP ? null : noCovNoted(readFileSync(page_path, "utf8"));
/** File mode: a copy of the page in which the first L1 lead's primary contact is made ambiguous (the stale case). */
function staleVariant(file) {
  const html = readFileSync(file, "utf8");
  const target = (bundleLeads(html) || []).find((l) => l.lead_type === "L1" && l.det_id && !isStale(l));
  if (!target) return null;
  const C = partOf(html, "contacts");
  C.records = C.records || {};
  C.records[target.det_id] = { ...(C.records[target.det_id] || {}), match_ambiguous: true, ambiguous_mmsi: "412000001;412000002" };
  const json = JSON.stringify(C).replace(/<\//g, "<\\/");
  const out = resolve(shotDir, "stale_variant.html");
  writeFileSync(out, html.replace(/<script type="application\/json" id="scs-part-contacts">[\s\S]*?<\/script>/, () => `<script type="application/json" id="scs-part-contacts">${json}</script>`));
  return { file: out, lead_id: target.lead_id, det_id: target.det_id, n: (bundleLeads(html) || []).length };
}
// Rendered text that calls something dark, after the negated uses ("not dark", "never means dark", "no ... dark").
const affirmsDark = (t) => /\bdark\b/i.test(t.replace(/\b(not|never|no)\b[^.;:]{0,30}?\bdark\b/gi, ""));

const browser = await chromium.launch({ args: ["--disable-gpu"] });
// Local-app mode, once per run: every L1 lead's primary contact (up to 500 by priority) and a hand-checked no_coverage
// contact of the Pearl River pass (its note may say "not dark"), read from the API.
let httpOnce = null;
async function httpDiscovery(page) {
  if (httpOnce) return httpOnce;
  httpOnce = await page.evaluate(async (PR) => {
    const get = async (u) => { const r = await fetch(u); return r.ok ? r.json() : null; };
    const one = async (u) => (await get(u))?.item || null;  // a single record comes in the envelope's `item`
    const states = "new,reviewing,closed_explained,closed_unexplained,closed_false_alarm";
    const j = await get(`/api/v1/leads?lead_type=L1&state=${states}&sort=-priority&limit=500`);
    const leads = ((j && j.items) || []).filter((l) => l.primary_type === "contact");
    const stale = [];
    let checked = 0;
    for (let i = 0; i < leads.length; i += 25) {
      const batch = leads.slice(i, i + 25);
      const recs = await Promise.all(batch.map((l) => one(`/api/v1/contacts/${encodeURIComponent(l.primary_id)}`)));
      recs.forEach((c, k) => {
        if (!c) return;
        checked++;
        const amb = c.match_ambiguous === true || /\d{9}/.test(String(c.ambiguous_mmsi || ""));
        if (c.ais_status === "matched" || c.ais_status === "no_coverage" || (c.ais_status === "unmatched" && amb)) stale.push(`${batch[k].lead_id} (${c.ais_status}${amb ? ", ambiguous" : ""})`);
      });
    }
    const nc = await get(`/api/v1/contacts?pass_id=${PR}&ais_status=no_coverage&sort=-cnn_score&limit=300`);
    let noted = null;
    const items = (nc && nc.items) || [];
    for (let i = 0; i < items.length && !noted; i += 30) {
      const recs = await Promise.all(items.slice(i, i + 30).map((c) => one(`/api/v1/contacts/${encodeURIComponent(c.det_id)}`)));
      noted = recs.find((c) => c && typeof c.review_note === "string" && c.review_note)?.det_id || null;
    }
    return { n: leads.length, total: (j && j.total) || 0, checked, stale, noted };
  }, PEARL_RIVER);
  return httpOnce;
}
try {
  for (const vp of RUNS) {
    const theme = vp.theme;
    if (ONLY && !`${mode} ${vp.name} ${vp.width} ${theme}${vp.blockedStorage ? " blocked-storage" : ""}`.includes(ONLY)) continue;
    const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 1, colorScheme: "dark" });
    if (vp.blockedStorage) {
      await context.addInitScript(() => { Object.defineProperty(window, "localStorage", { configurable: true, get() { throw new DOMException("storage blocked by the check", "SecurityError"); } }); });
    } else {
      await context.addInitScript((t) => { try { window.localStorage.setItem("scs.theme", t); } catch (e) { /* storage blocked */ } }, theme);
    }
    const page = await context.newPage();
    if (HTTP) {
      // Nothing may be written by the check: answer every non-GET API call here, never at the backend.
      await page.route("**/api/v1/**", (route) => (route.request().method() === "GET" ? route.continue() : route.fulfill({ status: 418, contentType: "application/json", body: JSON.stringify({ error: { code: "smoke", message: "write blocked by the smoke check" } }) })));
    }
    const label = `${mode} ${vp.name} ${vp.width} ${theme}${vp.blockedStorage ? " blocked-storage" : ""}`;
    const tag = `${mode === "http" ? "http_" : ""}${vp.name}_${vp.width}_${theme}${vp.blockedStorage ? "_blocked" : ""}`;
    const errors = [];
    page.on("console", (m) => {
      // A documented 404 (below) is also logged by the browser as a failed resource load: counted there, not here.
      if (m.type() === "error" && /Failed to load resource: the server responded with a status of 404/.test(m.text()) && DOCUMENTED_404.test(m.location()?.url || "")) return;
      if (m.type() === "error") errors.push(`console.error: ${m.text()}`);
      if (m.type() === "warning" && /icon not bundled/.test(m.text())) errors.push(`console.warn: ${m.text()}`);
    });
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    page.on("request", (r) => { const u = r.url(); if (!allowed(u)) fail(`${label}: request off the page's origin ${u}`); });
    page.on("response", (r) => {
      if (r.status() === 404 && HTTP && DOCUMENTED_404.test(r.url())) { documented404.add(r.url().replace(/^.*\/api\/v1/, "")); return; }
      if (r.status() >= 400 && !(HTTP && r.request().method() !== "GET")) errors.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()}`);
    });
    page.on("requestfailed", (r) => { const u = r.url(); if (!/^(data|blob):/.test(u)) fail(`${label}: failed request ${u} (${r.failure()?.errorText})`); });
    const expectedTheme = vp.blockedStorage ? "dark" : theme;

    const check = async (route, name, opts = {}) => {
      await page.goto(url + route, { waitUntil: "load" });
      await page.waitForSelector(".scs-app", { timeout: 20000 });
      await page.waitForTimeout(HTTP ? 1500 : 700);
      const banners = await page.$$eval("[data-banner]", (els) => els.map((e) => ({ pos: e.getAttribute("data-banner"), text: e.textContent || "", visible: !!(e.offsetWidth && e.offsetHeight) })));
      const top = banners.find((b) => b.pos === "top");
      const bottom = banners.find((b) => b.pos === "bottom");
      A("caveat banner top and bottom on every view"); A("theme state and opaque body background"); A("no horizontal scroll"); A("no status word outside the caveat"); A("no em or en dash in rendered text");
      if (!top || !top.visible || !top.text.includes(CAVEAT_SHORT)) fail(`${label} ${name}: top caveat banner missing`);
      if (!bottom || !bottom.visible || !bottom.text.includes(CAVEAT_SHORT)) fail(`${label} ${name}: bottom caveat banner missing`);
      const htmlTheme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
      const bpDark = await page.evaluate(() => document.body.classList.contains("bp6-dark"));
      const bodyBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      if (htmlTheme !== expectedTheme || bpDark !== (expectedTheme === "dark")) fail(`${label} ${name}: theme state data-theme=${htmlTheme} bp6-dark=${bpDark}`);
      if (!bodyBg || bodyBg === "rgba(0, 0, 0, 0)" || bodyBg === "transparent") fail(`${label} ${name}: body background transparent`);
      const sw = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth, document.body.scrollWidth]);
      if (sw[0] > sw[1] || sw[2] > sw[1]) fail(`${label} ${name}: horizontal scroll (scrollWidth ${sw[0]}/${sw[2]} > innerWidth ${sw[1]})`);
      if (vp.name === "phone") {
        A("phone side gutter at least 16 px");
        const gutter = await page.evaluate(() => { const el = document.querySelector(".scs-page, .scs-rail"); if (!el) return null; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return { left: r.left, pl: parseFloat(cs.paddingLeft) }; });
        if (gutter && Math.round(gutter.left + gutter.pl) < 16) fail(`${label} ${name}: side gutter ${gutter.left + gutter.pl} px under 16`);
      }
      if (opts.objectPage) {
        A("short caveat under the title of every object page");
        const under = await page.evaluate((c) => (document.querySelector("[data-page] .scs-caveat-line")?.textContent || "").includes(c), CAVEAT_SHORT);
        if (!under) fail(`${label} ${name}: short caveat missing under the title`);
      }
      const bad = await page.evaluate(() => { const t = document.body.innerText || ""; const hits = []; for (const w of ["illegal", "suspicious", "violation", "threat"]) { const re = new RegExp(w, "ig"); let m; while ((m = re.exec(t))) { const ctx = t.slice(Math.max(0, m.index - 60), m.index + 60); if (!/Not evidence of illegal activity|does not mean illegal|Dark does not mean illegal/i.test(ctx)) hits.push(w + ": " + ctx.replace(/\s+/g, " ")); } } return hits; });
      for (const h of bad) fail(`${label} ${name}: status word outside the caveat: ${h}`);
      const dashes = await page.evaluate((src) => new RegExp(src).test(document.body.innerText || ""), DASHES.source);
      if (dashes) fail(`${label} ${name}: em or en dash in rendered text`);
      A("AIS ship type 0 shown as not reported, never as a raw code");
      if (await page.evaluate(() => /\bcode 0\b/.test(document.body.innerText || ""))) fail(`${label} ${name}: raw AIS ship type "code 0" in rendered text`);
      const shot = resolve(shotDir, `smoke_${tag}_${name}.png`);
      await page.screenshot({ path: shot, fullPage: vp.name === "phone" });
      shots.push(shot);
      ok(`${label} ${name}`);
    };

    const mapFill = async (what) => {
      const r = await page.evaluate(() => {
        const lc = document.querySelector(".scs-centre .leaflet-container");
        const c = document.querySelector(".scs-centre");
        if (!lc || !c) return null;
        const a = lc.getBoundingClientRect();
        const b = c.getBoundingClientRect();
        return { map: [Math.round(a.width), Math.round(a.height)], centre: [Math.round(b.width), Math.round(b.height)] };
      });
      A("map fills the centre column");
      if (!r) fail(`${label} ${what}: no map in the centre column`);
      else if (r.map[1] < 0.9 * r.centre[1] || r.map[0] < 0.9 * r.centre[0]) fail(`${label} ${what}: map ${r.map.join("x")} does not fill the centre ${r.centre.join("x")}`);
      else ok(`${label} ${what}: map ${r.map.join("x")} fills the centre ${r.centre.join("x")}`);
    };

    // ---------------------------------------------------------------- tablet: layout only
    if (vp.name === "tablet") {
      for (const [route, name] of [["#/leads", "queue"], ["#/map", "map"]]) {
        await check(route, name);
        const nav = await rect(page, ".scs-navbar");
        const groups = await page.$$eval(".scs-navbar .bp6-navbar-group", (els) => els.map((e) => e.getBoundingClientRect().bottom));
        if (nav && groups.some((b) => b > nav.bottom + 1)) fail(`${label} ${name}: navbar group wraps below the navbar (${groups.join(", ")} > ${nav.bottom})`);
        const strip = await rect(page, ".scs-railstrip");
        if (!strip) fail(`${label} ${name}: no rail icon strip`);
        if (overlap(strip, await rect(page, ".scs-queue-head"))) fail(`${label} ${name}: rail strip covers the queue header`);
        if (overlap(strip, await rect(page, ".scs-centre .leaflet-control-zoom"))) fail(`${label} ${name}: rail strip covers the zoom control`);
        A("tablet layout: navbar on one line, rail strip clear of the queue header and zoom");
        if (name === "map") await mapFill("map at 1024");
      }
      await page.goto(url + "#/leads", { waitUntil: "load" });
      await page.waitForTimeout(700);
      await page.keyboard.press("j");
      await page.waitForTimeout(400);
      const insp = await page.evaluate(() => { const e = document.querySelector("[data-inspector]"); return e ? e.getBoundingClientRect().width : 0; });
      if (insp < 200) fail(`${label}: J did not open the inspector overlay (width ${insp})`);
      else ok(`${label}: inspector overlay opens on selection`);
      A("0 console errors, 0 page errors, no 4xx or 5xx, no request off the page's origin");
      for (const e of errors) fail(`${label}: ${e}`);
      await context.close();
      continue;
    }

    // ---------------------------------------------------------------- blocked storage: notice and highlighted export
    if (vp.blockedStorage) {
      await check("#/leads", "queue");
      await page.keyboard.press("j");
      await page.waitForTimeout(400);
      const notice = await page.$("[data-inspector] [data-storage-warning]");
      if (!notice) fail(`${label}: storage notice missing on the console lead card`);
      const st0 = await page.evaluate(() => document.querySelector("[data-inspector] .scs-leadcard")?.getAttribute("data-state"));
      if (st0 === "new") {
        await page.keyboard.press("r");
        await page.waitForTimeout(500);
        const st1 = await page.evaluate(() => document.querySelector("[data-inspector] .scs-leadcard")?.getAttribute("data-state"));
        if (st1 !== "reviewing") fail(`${label}: R left the state at ${st1}`);
        const hi = await page.$("[data-inspector] [data-export-highlight='1']");
        if (!hi) fail(`${label}: export button not highlighted after a decision`);
        else ok(`${label}: decision recorded, notice shown, export highlighted`);
      }
      const dl = page.waitForEvent("download", { timeout: 3000 }).catch(() => null);
      await page.click("[data-inspector] [data-export-highlight]");
      const d = await dl;
      if (!d) fail(`${label}: the JSONL export did not download`);
      A("0 console errors, 0 page errors, no 4xx or 5xx, no request off the page's origin");
      for (const e of errors) fail(`${label}: ${e}`);
      await context.close();
      continue;
    }

    // ---------------------------------------------------------------- full run
    await check("#/leads", "queue");
    // the first queue request after a backend start builds its caches (research: about 14,000 leads); wait for it
    await page.waitForSelector("[data-queue], [data-queue-empty]", { timeout: 30000 }).catch(() => null);
    const queueKind = await page.evaluate(() => document.querySelector("[data-queue]")?.getAttribute("data-queue") || (document.querySelector("[data-queue-empty]") ? "empty" : "none"));
    if (queueKind === "none") fail(`${label}: no queue rendered`);
    A("queue: no lead whose primary contact is now ambiguous, matched or without coverage");
    if (HTTP) {
      const d = await httpDiscovery(page);
      if (d.n && d.checked < d.n) fail(`${label}: only ${d.checked} of ${d.n} L1 lead primary contacts could be read`);
      if (d.stale.length) fail(`${label}: ${d.stale.length} of ${d.checked} L1 leads cite a primary contact that no longer forms a lead (leads file older than the live file?): ${d.stale.slice(0, 5).join(", ")}`);
      else ok(`${label}: ${d.checked} L1 leads checked against their primary contacts (of ${d.total}); none stale`);
    } else if (fileLeads) {
      const want = fileLeads.filter((l) => !isStale(l)).length;
      const shown = await page.evaluate(() => (document.querySelector("[data-queue-count]")?.textContent || "").match(/of\s+([\d,]+)/)?.[1]?.replace(/,/g, "") || null);
      if (fileLeads.some(isStale)) ok(`${label}: the bundle holds ${fileLeads.filter(isStale).length} stale leads; the queue must leave them out`);
      if (shown === null) fail(`${label}: no queue count`);
      else if (Number(shown) !== want) fail(`${label}: the queue lists ${shown} leads, the bundle has ${want} that stand`);
    }
    if (vp.name === "phone") {
      const firstTab = await page.evaluate(() => { const b = document.querySelector(".scs-tabbar button"); return b ? [b.getAttribute("data-tab"), b.getAttribute("aria-selected")] : null; });
      if (!firstTab || firstTab[0] !== "queue" || firstTab[1] !== "true") fail(`${label}: phone first tab is not Queue selected (${JSON.stringify(firstTab)})`);
      if (queueKind !== "cards" && queueKind !== "empty") fail(`${label}: phone queue should be a card list, got ${queueKind}`);
    } else {
      if (queueKind !== "table" && queueKind !== "empty") fail(`${label}: desktop queue should be a table, got ${queueKind}`);
      const tl = await page.$(".scs-timeline");
      if (!tl) fail(`${label}: no timeline on the console at #/leads`);
    }
    // Quick filter: newest live pass (present whenever a processed live pass exists)
    const qf = await page.$("[data-quick-filter='newest-live-pass']");
    if (qf) {
      await qf.click();
      await page.waitForTimeout(HTTP ? 1200 : 300);
      const pill = await page.evaluate(() => Array.from(document.querySelectorAll("[data-filterbar] .bp6-tag")).some((t) => /^pass: live_/.test(t.textContent || "")));
      if (!pill) fail(`${label}: the newest-live-pass quick filter set no pass filter`);
      await page.click("[data-quick-filter='newest-live-pass']");
      await page.waitForTimeout(300);
      ok(`${label}: newest-live-pass quick filter toggles`);
    } else if (!HTTP) fail(`${label}: no newest-live-pass quick filter on the queue`);
    // Keyboard: J selects the first row (the local app loads the lead record first)
    await page.waitForSelector("[data-queue]", { timeout: 15000 }).catch(() => null);
    await page.keyboard.press("j");
    await page.waitForSelector("[data-lead-id]", { timeout: HTTP ? 8000 : 2000 }).catch(() => null);
    const lead = await page.evaluate(() => document.querySelector("[data-lead-id]")?.getAttribute("data-lead-id") || null);
    if (!lead) fail(`${label}: J did not select a lead (no lead card)`);
    let primary = null;
    if (lead) {
      await check(`#/lead/${encodeURIComponent(lead)}`, "lead", { objectPage: true });
      if (!(await page.$("[data-factors]"))) fail(`${label} lead: factor list missing`);
      await page.waitForTimeout(HTTP ? 800 : 200);
      if (await page.$("[data-stale-lead]")) fail(`${label} lead: the queue's first lead ${lead} is stale (its primary contact no longer forms a lead)`);
      if (!(await page.$$(".scs-decisions button")).length) fail(`${label} lead: decision buttons missing`);
      const raw = await page.evaluate(() => { const t = document.querySelector(".scs-leadcard")?.innerText || ""; return (t.match(/\b[a-z]+(_[a-z0-9]+)+\b/g) || []).filter((w) => !/^(ais|det|s1|cnn|lead|mmsi)_/.test(w) && !/_v\d/.test(w)); });
      if (raw.length) fail(`${label} lead: raw codes in the lead card: ${[...new Set(raw)].join(", ")}`);
      primary = await page.evaluate(() => { const a = document.querySelector("[data-field='primary_id'] a"); return a ? a.getAttribute("href") : null; });
      if (primary && primary.startsWith("#/contact/")) {
        A("lead card names who the primary contact is (identity, nearest AIS vessel or candidates)");
        await page.waitForSelector(".scs-leadcard [data-lead-identity]", { timeout: HTTP ? 8000 : 2000 }).catch(() => null);
        const li = await page.evaluate(() => { const e = document.querySelector(".scs-leadcard [data-lead-identity]"); return e ? [e.getAttribute("data-lead-identity"), e.closest(".scs-field")?.textContent || ""] : null; });
        if (!li) fail(`${label} lead: the lead card does not say who the contact is`);
        else if (li[0] === "matched" && /low/.test(li[1]) && !li[1].includes(LOW_QUALITY_LABEL)) fail(`${label} lead: low-quality match on the lead card without the label`);
        else if (li[1].includes("aisstream") && !li[1].includes(AISSTREAM_LABEL)) fail(`${label} lead: aisstream identity on the lead card without the label`);
      }
      const state0 = await page.evaluate(() => document.querySelector(".scs-leadcard[data-lead-id]")?.getAttribute("data-state"));
      if (!HTTP && state0 === "new") {
        // R records reviewing directly (spec 4.1 and 9: no input)
        await page.keyboard.press("r");
        await page.waitForTimeout(400);
        if (await page.$(".bp6-dialog")) fail(`${label} lead: R opened a dialog; reviewing needs no input`);
        const state = await page.evaluate(() => document.querySelector(".scs-leadcard[data-lead-id]")?.getAttribute("data-state"));
        if (state !== "reviewing") fail(`${label} lead: state after R is ${state}`);
        else ok(`${label} lead: R recorded reviewing`);
      }
      // X opens the reason picker with no preselected reason; Record stays disabled until a reason is chosen
      const closed = await page.evaluate(() => (document.querySelector(".scs-leadcard[data-lead-id]")?.getAttribute("data-state") || "").startsWith("closed"));
      if (!closed) {
        await page.keyboard.press("x");
        await page.waitForTimeout(300);
        const dlg = await page.$(".bp6-dialog");
        if (!dlg) fail(`${label} lead: X did not open the reason picker`);
        else {
          const dis0 = await page.$eval(".bp6-dialog .bp6-button.bp6-intent-primary", (b) => b.disabled || b.classList.contains("bp6-disabled"));
          if (!dis0) fail(`${label} lead: Record decision enabled before a reason was chosen`);
          await page.selectOption(".bp6-dialog select", { index: 1 });
          const dis1 = await page.$eval(".bp6-dialog .bp6-button.bp6-intent-primary", (b) => b.disabled || b.classList.contains("bp6-disabled"));
          if (dis1) fail(`${label} lead: Record decision still disabled after a reason was chosen`);
          else ok(`${label} lead: reason picker starts empty`);
          await page.keyboard.press("Escape");
          await page.waitForTimeout(300);
        }
      }
    }
    // Contact: in the fixture the primary of the lead; in the local app a live contact without a cached chip
    let contactId = null;
    if (HTTP) {
      contactId = await page.evaluate(async () => {
        const r = await fetch("/api/v1/contacts?limit=1&sort=-acq_utc");
        const j = await r.json();
        return j.items?.[0]?.det_id || null;
      });
    } else {
      contactId = await page.evaluate(() => { const a = Array.from(document.querySelectorAll("a[href^='#/contact/']"))[0]; return a ? decodeURIComponent(a.getAttribute("href").slice("#/contact/".length)) : null; });
      // a queue of coverage leads (L7, cell primaries) links no contact: take one from the embedded contacts part
      if (!contactId && fileIds) contactId = Object.values(fileIds.contact).find(Boolean) || null;
    }
    let vesselKey = null;
    if (contactId) {
      // twice in a row on phone in the local app: the earlier intermittent canvas error came between two contact pages
      for (let k = 0; k < (HTTP && vp.name === "phone" ? 3 : 1); k++) await check(`#/contact/${encodeURIComponent(contactId)}`, "contact", { objectPage: true });
      if (!(await page.$("[data-identification]"))) fail(`${label} contact: identification block missing`);
      const chips = await page.$$(".scs-prov-chip");
      if (chips.length < 10) fail(`${label} contact: only ${chips.length} provenance chips`);
      await chips[0].focus();
      await chips[0].click();
      await page.waitForTimeout(300);
      const pop = await page.evaluate(() => document.querySelector(".scs-prov-pop")?.textContent || "");
      if (!/licence/i.test(pop) || !/access date/i.test(pop)) fail(`${label} contact: provenance popover lacks licence or access date`);
      await page.keyboard.press("Escape");
      const chipState = await page.evaluate(() => document.querySelector("[data-chip]")?.getAttribute("data-chip") || null);
      if (!chipState) fail(`${label} contact: radar chip section missing`);
      if (HTTP && chipState === "none" && !(await page.$("[data-fetch-chip]"))) fail(`${label} contact: no Fetch chip button in the local app`);
      vesselKey = await page.evaluate(() => { const a = Array.from(document.querySelectorAll("a[href^='#/vessel/']"))[0]; return a ? decodeURIComponent(a.getAttribute("href").slice("#/vessel/".length)) : null; });
    } else fail(`${label}: no contact found`);
    // the local app: a vessel the backend holds (a link to an MMSI outside its vessel table is the documented 404)
    if (HTTP && vesselKey && !(await page.evaluate(async (k) => (await fetch(`/api/v1/vessels/${encodeURIComponent(k)}`)).ok, vesselKey))) vesselKey = null;
    if (!vesselKey && HTTP) vesselKey = await page.evaluate(async () => { const j = await (await fetch("/api/v1/vessels?limit=1")).json(); return j.items?.[0]?.vessel_key || null; });
    if (vesselKey) {
      await check(`#/vessel/${encodeURIComponent(vesselKey)}`, "vessel", { objectPage: true });
      if (!(await page.evaluate(() => (document.body.innerText || "").includes("self-reported")))) fail(`${label} vessel: identity note missing`);
    } else fail(`${label}: no vessel link found`);
    // Passes: the Pearl River pass (the first live pass with AIS in its footprint: identities) and the newest processed
    // live pass. Each leads with its identification or coverage result, shows the AIS window and lists its contacts per
    // AIS status (identity for matched, nearest AIS vessel, lead or ambiguity for unmatched, the rule for no_coverage).
    const livePasses = HTTP
      ? await page.evaluate(async () => {
        const j = await (await fetch("/api/v1/passes")).json();
        return (j.items || []).filter((p) => String(p.pass_id).startsWith("live_") && p.processed).sort((a, b) => Date.parse(b.start_utc) - Date.parse(a.start_utc)).map((p) => p.pass_id);
      })
      : fileIds.livePasses;
    const passIds = [...new Set([livePasses.includes(PEARL_RIVER) ? PEARL_RIVER : null, livePasses[0] || null].filter(Boolean))];
    if (!livePasses.includes(PEARL_RIVER)) fail(`${label}: the Pearl River pass ${PEARL_RIVER} is not in this build`);
    const roles = {};
    let roleHasLead = false;
    for (const passId of passIds) {
      const pr = passId === PEARL_RIVER;
      await check(`#/pass/${encodeURIComponent(passId)}`, pr ? "pass_pearl_river" : "pass_newest", { objectPage: true });
      A("pass page: result first, AIS window, status tabs, AIS counts not credited to the plan");
      const wrong = await page.evaluate(() => Array.from(document.querySelectorAll(".scs-provtable tr")).filter((tr) => /^ais_/.test(tr.cells[0]?.textContent || "") && /acquisition plan/i.test(tr.textContent || "")).length);
      if (wrong) fail(`${label} pass ${passId}: ${wrong} AIS count rows credited to the acquisition plan`);
      if (!(await page.$("[data-id-result]"))) fail(`${label} pass ${passId}: the identification or coverage result does not lead the live pass page`);
      if (!(await page.$("[data-ais-window]"))) fail(`${label} pass ${passId}: AIS recording window missing`);
      if (pr && (await page.$("[data-coverage-result]"))) fail(`${label} pass ${passId}: the Pearl River pass leads with a coverage result, not its identification result`);
      if (pr) {
        A("D6.2 on the Pass result line: identifications and low-quality pairings counted apart");
        await page.waitForSelector("[data-matched-split='1']", { timeout: HTTP ? 20000 : 3000 }).catch(() => null);
        const split = await page.evaluate(() => document.querySelector("[data-matched-split='1']")?.textContent || "");
        if (!/\d+ identified/.test(split) || !/low-quality pairing/.test(split)) fail(`${label} pass ${passId}: the result line does not split identifications from low-quality pairings: ${split}`);
        else ok(`${label} pass ${passId}: ${split.replace(/\s+/g, " ").slice(0, 90)}`);
      }
      const tabs = await page.$$eval("[data-pass-tabs] [data-tab-status]", (els) => els.map((e) => e.getAttribute("data-tab-status")));
      if (!tabs.length) fail(`${label} pass ${passId}: no AIS status tabs on the contacts table`);
      for (const t of tabs) {
        await page.click(`[data-pass-tabs] [data-tab-status='${t}']`);
        await page.waitForTimeout(HTTP ? 2000 : 300);
        const rows = [];
        const maxPages = pr && t === "unmatched" ? (HTTP ? 10 : 4) : pr && t === "matched" ? 4 : 1;
        for (let k = 0; k < maxPages; k++) {
          await page.waitForSelector("[data-pass-contacts]", { timeout: HTTP ? 20000 : 3000 }).catch(() => null);
          rows.push(...await page.$$eval("[data-pass-contacts] [data-det-id]", (els) => els.map((e) => ({
            id: e.getAttribute("data-det-id"), text: e.textContent || "",
            quality: e.querySelector("[data-quality]")?.getAttribute("data-quality") || null,
            low: !!e.querySelector("[data-low-quality]"),
            amb: !!e.querySelector("[data-ambiguous]"), cand: Array.from(e.querySelectorAll("[data-ambiguous] a")).map((a) => a.textContent || ""),
            dark: !!e.querySelector("[data-dark-lead]"), lead: e.querySelectorAll("[data-dark-lead] a[href^='#/lead/']").length,
            mmsi: Array.from(e.querySelectorAll("a[href^='#/vessel/']")).map((a) => a.textContent || ""),
            nearestName: e.querySelector("[data-nearest] a[href^='#/vessel/']")?.textContent || null,
          }))));
          if (t === "unmatched" && rows.some((r) => r.amb) && (rows.some((r) => r.lead) || (k >= 2 && rows.some((r) => r.dark)))) break;
          const next = await page.$("[aria-label='Next contacts']");
          if (!next || !(await next.isEnabled())) break;
          await next.click();
          await page.waitForTimeout(HTTP ? 2500 : 300);
        }
        const none = await page.$("[data-pass-contacts='none']");
        if (!rows.length && !none) fail(`${label} pass ${passId}: tab ${t} shows neither contacts nor a reason`);
        if (t === "matched" && rows.length) {
          A("Pearl River pass: matched rows with name, MMSI, quality and the aisstream label");
          const lab = await page.evaluate(() => document.querySelector("[data-identity-label]")?.textContent || "");
          if (!lab.includes(AISSTREAM_LABEL)) fail(`${label} pass ${passId}: matched identities lack the aisstream label`);
          for (const r of rows) {
            if (!r.mmsi.some((m) => /^\d{9}$/.test(m))) fail(`${label} pass ${passId}: matched row ${r.id} without an MMSI`);
            if (!["high", "medium", "low"].includes(r.quality || "")) fail(`${label} pass ${passId}: matched row ${r.id} without a quality`);
            if (r.quality === "low" && !r.low) fail(`${label} pass ${passId}: low-quality row ${r.id} without "${LOW_QUALITY_LABEL}"`);
          }
          A("D6.2: every low-quality match carries the low-quality label");
          if (pr && !rows.some((r) => r.quality === "low")) fail(`${label} pass ${passId}: no low-quality match listed (the pass has 7)`);
          if (pr && !rows.some((r) => r.low && /low-quality pairing, identity not confirmed/.test(r.text))) fail(`${label} pass ${passId}: the low-quality label text is not shown`);
          roles.matched_high = roles.matched_high || rows.find((r) => r.quality === "high" && !r.low)?.id;
          roles.matched_low = roles.matched_low || rows.find((r) => r.quality === "low")?.id;
          if (pr) ok(`${label} pass ${passId}: ${rows.length} matched rows (${rows.filter((r) => r.quality === "low").length} low, each labelled)`);
        }
        if (t === "unmatched" && rows.length) {
          A("ambiguous contacts list candidates and are not leads; dark leads link their lead");
          for (const r of rows.filter((x) => x.amb)) {
            if (!r.cand.some((m) => /^\d{9}$/.test(m))) fail(`${label} pass ${passId}: ambiguous row ${r.id} lists no candidate MMSI`);
            if (r.lead || !/not a lead/.test(r.text)) fail(`${label} pass ${passId}: ambiguous row ${r.id} shown as a lead`);
          }
          if (pr && !rows.some((r) => r.amb)) fail(`${label} pass ${passId}: no ambiguous contact found in ${rows.length} unmatched rows`);
          if (pr) {
            A("unmatched rows name the nearest AIS vessel where the vessel table has a name");
            const named = rows.filter((r) => r.nearestName && !/^\d{9}$/.test(r.nearestName)).length;
            const withNearest = rows.filter((r) => r.nearestName).length;
            if (withNearest && !named) fail(`${label} pass ${passId}: no unmatched row names its nearest AIS vessel (${withNearest} rows by bare MMSI)`);
            else ok(`${label} pass ${passId}: nearest AIS vessel named on ${named} of ${withNearest} unmatched rows`);
          }
          roles.ambiguous = roles.ambiguous || rows.find((r) => r.amb)?.id;
          // a dark lead with its lead; a build whose leads do not cover this pass (the research build's leads are the
          // September run's) has dark lead candidates without a lead
          if (!roles.unmatched_dark) {
            const withLead = rows.find((r) => r.lead);
            roles.unmatched_dark = withLead?.id || rows.find((r) => r.dark)?.id;
            roleHasLead = !!withLead;
            if (roles.unmatched_dark && !withLead) ok(`${label} pass ${passId}: no lead covers this pass in this build; dark lead candidates are listed without one`);
          }
        }
        if (t === "no_coverage") {
          A("no_coverage tab states the rule and never calls a contact dark");
          const ruleText = await page.evaluate(() => document.querySelector("[data-nocov-rule]")?.textContent || null);
          if (ruleText === null) fail(`${label} pass ${passId}: no_coverage rule missing`);
          else if (/\.\./.test(ruleText) || affirmsDark(ruleText)) fail(`${label} pass ${passId}: no_coverage rule text: ${ruleText}`);
          for (const r of rows) if (affirmsDark(r.text)) fail(`${label} pass ${passId}: no_coverage row ${r.id} says dark`);
          roles.no_coverage = roles.no_coverage || rows[0]?.id;
        }
      }
      if (pr) {
        A("Pearl River pass: AIS vessels with no matched contact listed with the aisstream label");
        const ao = await page.evaluate(() => { const rows = document.querySelectorAll("[data-ais-only-rows] [data-mmsi]"); const sec = document.querySelector("[data-ais-only-rows]")?.closest(".bp6-section"); return { n: rows.length, text: sec?.textContent || "" }; });
        if (!ao.n) fail(`${label} pass ${passId}: no AIS-only vessel rows`);
        else if (!ao.text.includes(AISSTREAM_LABEL)) fail(`${label} pass ${passId}: AIS-only vessels without the aisstream label`);
      }
      ok(`${label} pass ${passId}: tabs ${tabs.join(", ")}`);
    }
    // Contact pages: one per role found on the pass pages, plus a not_checked contact (September run, open build)
    const statusContacts = { ...roles };
    if (HTTP && !statusContacts.not_checked) statusContacts.not_checked = await page.evaluate(async () => { const j = await (await fetch(`/api/v1/contacts?ais_status=not_checked&limit=1`)).json(); return j.items?.[0]?.det_id || null; });
    if (!HTTP && fileIds.contact.not_checked) statusContacts.not_checked = fileIds.contact.not_checked;
    statusContacts.no_coverage_noted = HTTP ? (await httpDiscovery(page)).noted : fileNoCovNoted;
    if (!statusContacts.no_coverage_noted) fail(`${label}: no hand-checked no_coverage contact found`);
    for (const want of ["matched_high", "matched_low", "unmatched_dark", "ambiguous", "no_coverage"]) if (!statusContacts[want]) fail(`${label}: no ${want} contact found on the Pearl River pass page`);
    const STATUS_OF = { matched_high: "matched", matched_low: "matched", unmatched_dark: "unmatched", ambiguous: "unmatched", no_coverage: "no_coverage", no_coverage_noted: "no_coverage", not_checked: "not_checked" };
    for (const [role, det] of Object.entries(statusContacts)) {
      if (!det) continue;
      const st = STATUS_OF[role];
      await check(`#/contact/${encodeURIComponent(det)}`, `contact_${role}`, { objectPage: true });
      A("contact page: Identification block for its AIS status with provenance chips");
      const block = await page.$(`[data-identification='${st}']`);
      if (!block) fail(`${label} contact ${role}: Identification block for ${st} missing`);
      const sec = await page.evaluate(() => { const s = document.querySelector("[data-identification]")?.closest(".bp6-section"); return { text: s?.innerText || "", chips: s?.querySelectorAll(".scs-prov-chip").length || 0 }; });
      if (sec.chips < 2) fail(`${label} contact ${role}: only ${sec.chips} provenance chips in Identification`);
      if (role === "matched_high" || role === "matched_low") {
        A("matched contact: identity (name, MMSI, quality) with provenance and the aisstream label");
        const head = await page.evaluate(() => document.querySelector("[data-identity-headline]")?.textContent || "");
        if (!/\d{9}/.test(head) || !/quality/.test(head)) fail(`${label} contact ${role}: identity headline lacks MMSI or quality: ${head}`);
        if (!sec.text.includes(AISSTREAM_LABEL)) fail(`${label} contact ${role}: aisstream identity without the label`);
        for (const f of ["mmsi", "vessel_name", "call_sign", "flag", "ship_type", "match_quality"]) if (!(await page.$(`[data-identification='matched'] [data-field='${f}'] .scs-prov-chip`))) fail(`${label} contact ${role}: field ${f} without its chip`);
        const lowShown = await page.$("[data-identification='matched'][data-low-quality='1']");
        if (role === "matched_low") {
          A("D6.2: a low-quality match says 'low-quality pairing, identity not confirmed' on the Contact page");
          if (!lowShown || !sec.text.includes(LOW_QUALITY_LABEL)) fail(`${label} contact ${role}: low-quality match without "${LOW_QUALITY_LABEL}"`);
          if (!(await page.$("[data-review-note]"))) fail(`${label} contact ${role}: hand-check note not shown`);
          const note = await page.evaluate(() => document.querySelector("[data-low-quality-note]")?.textContent || "");
          if (!/not a confirmed identity/.test(note)) fail(`${label} contact ${role}: the D6.2 note does not say the shown vessel is not a confirmed identity: ${note}`);
        }
        if (role === "matched_high" && lowShown) fail(`${label} contact ${role}: a high-quality match is labelled low quality`);
      }
      if (role === "unmatched_dark") {
        A("unmatched dark lead: its evidence (nearest AIS vessel, distance, vessels within 10 km, reach, length and class, lead)");
        if (!(await page.$("[data-identification='unmatched'][data-dark-lead='1']"))) fail(`${label} contact ${role}: not shown as a dark lead`);
        for (const f of ["nearest_ais_mmsi", "nearest_ais_dist_m", "n_ais_10km", "ais_reach", "length_est_m", "dark_lead", "lead_ids"]) if (!(await page.$(`[data-identification='unmatched'] [data-field='${f}']`))) fail(`${label} contact ${role}: evidence field ${f} missing`);
        if (roleHasLead && !(await page.$("[data-identification='unmatched'] a[href^='#/lead/']"))) fail(`${label} contact ${role}: no link to its lead`);
      }
      if (role === "ambiguous") {
        A("ambiguous contact: candidate MMSIs, never a lead");
        const cand = await page.$$eval("[data-candidates] a", (els) => els.map((e) => e.textContent || ""));
        if (!cand.some((m) => /^\d{9}$/.test(m))) fail(`${label} contact ${role}: no candidate MMSI`);
        if (!/never forms a lead/.test(sec.text) || (await page.$("[data-identification='unmatched'] a[href^='#/lead/']"))) fail(`${label} contact ${role}: ambiguous contact shown as a lead`);
        A("ambiguous contact page has no Lead section");
        if (await page.$("[data-lead-section]")) fail(`${label} contact ${role}: an ambiguous contact's page has a Lead section`);
      }
      if (role === "no_coverage" || role === "no_coverage_noted") {
        A("no_coverage contact says no AIS coverage and never calls it dark (negated uses such as 'not dark' allowed)");
        if (!sec.text.includes("No AIS coverage here")) fail(`${label} contact ${role}: does not say "No AIS coverage here"`);
        if (affirmsDark(sec.text)) fail(`${label} contact ${role}: the Identification block calls a no_coverage contact dark`);
        if (/\.\./.test(sec.text)) fail(`${label} contact ${role}: double full stop in the Identification block`);
        if (role === "no_coverage_noted") {
          A("hand-checked no_coverage contact shows its note");
          if (!(await page.$("[data-review-note]"))) fail(`${label} contact ${role}: hand-check note not shown`);
        }
        if (await page.$("[data-lead-section]")) fail(`${label} contact ${role}: a no_coverage contact shows a Lead section`);
      }
      A("Context section shows values, or 'No ocean context for this object yet' when object_context is null");
      const ctx = await page.evaluate(() => document.querySelector("[data-context]")?.getAttribute("data-context") || null);
      if (!ctx) fail(`${label} contact ${role}: Context section missing`);
      if (ctx === "none" && !(await page.evaluate(() => (document.body.innerText || "").includes("No ocean context for this object yet")))) fail(`${label} contact ${role}: null context without its line`);
      if (ctx === "1") {
        const shipping = await page.$$eval("[data-context-field^='ship_presence'] td:nth-child(2)", (els) => els.map((e) => e.textContent || ""));
        if (shipping.some((x) => !/present/.test(x) && !/no value/.test(x))) fail(`${label} contact ${role}: shipping shown as something other than presence: ${shipping.join(" | ")}`);
        const units = await page.$$eval("[data-context-table] tbody tr", (els) => els.length);
        if (units < 5) fail(`${label} contact ${role}: context table has ${units} rows`);
      }
      contextSeen[ctx || "missing"] = (contextSeen[ctx || "missing"] || 0) + 1;
      if (!(await page.evaluate((n) => (document.body.innerText || "").includes(n), OCEAN_NOTE))) fail(`${label} contact ${role}: ocean caveat missing`);
    }
    // Light page with its Context section
    let lightId = HTTP ? await page.evaluate(async () => { const j = await (await fetch("/api/v1/lights?limit=1")).json(); return j.items?.[0]?.light_id || null; }) : fileIds?.light || null;
    if (!lightId) {
      await page.goto(url + "#/leads", { waitUntil: "load" });
      await page.waitForTimeout(700);
      lightId = await page.evaluate(() => { const a = document.querySelector("a[href^='#/light/']"); return a ? decodeURIComponent(a.getAttribute("href").slice("#/light/".length)) : null; });
    }
    if (lightId) {
      await check(`#/light/${encodeURIComponent(lightId)}`, "light", { objectPage: true });
      if (!(await page.$("[data-context]"))) fail(`${label} light: Context section missing`);
    } else ok(`${label}: no light link found (light page skipped)`);
    // Cell: expected activity with the ocean and anomaly caveats (a cell with rows in the fixture; the lead's cell otherwise)
    const cellRoute = fileIds && fileIds.cells.length ? `#/cell/${fileIds.cells[0]}` : primary && primary.startsWith("#/cell/") ? primary : "#/cell/r13c64";
    await check(cellRoute, "cell", { objectPage: true });
    if (HTTP && !(await page.$("[data-cell-id]"))) fail(`${label} cell: the local app's cell record did not render`);
    if (await page.$("[data-cell-id]")) {
      if (!(await page.$("[data-expected]"))) fail(`${label} cell: expected activity section missing`);
      if (!(await page.$("[data-anomaly-caveat]"))) fail(`${label} cell: anomaly caveat missing`);
      if (!(await page.$("[data-ocean-caveat]"))) fail(`${label} cell: ocean caveat missing`);
      if (fileIds && fileIds.cells.length && !(await page.$("[data-sparkline]"))) fail(`${label} cell: no observed against expected sparkline`);
    }
    // Map: EEZ off at load, no tiles, fills the centre
    await check("#/map", "map");
    if (vp.name === "phone") {
      const tabSel = await page.evaluate(() => document.querySelector(".scs-tabbar button[aria-selected='true']")?.getAttribute("data-tab"));
      if (tabSel !== "map") fail(`${label} map: phone map tab not selected`);
    } else {
      await mapFill(`map at ${vp.width}`);
      if (vp.width !== 1440) {
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.waitForTimeout(500);
        await mapFill("map at 1440");
      }
      await page.setViewportSize({ width: vp.width, height: vp.height });
      // Leads to Map through the navbar button
      await page.goto(url + "#/leads", { waitUntil: "load" });
      await page.waitForTimeout(700);
      await page.click(".scs-navbtn:has-text('Map')");
      await page.waitForTimeout(700);
      await mapFill("map from the navbar");
    }
    A("EEZ layer off at load, no notice, no tiles, a point canvas");
    const eezOn = await page.evaluate(() => document.querySelector(".scs-map")?.getAttribute("data-eez-on"));
    if (eezOn !== "0") fail(`${label} map: EEZ layer state at load is ${eezOn}`);
    if (await page.$("[data-eez-notice]")) fail(`${label} map: EEZ notice visible at load`);
    const tiles = await page.evaluate(() => document.querySelectorAll(".leaflet-tile-pane img, .leaflet-tile").length);
    if (tiles > 0) fail(`${label} map: ${tiles} tile elements`);
    if ((await page.evaluate(() => document.querySelectorAll(".scs-point-layer").length)) < 1) fail(`${label} map: no point canvas layer`);
    if (vp.name === "desktop") {
      await page.keyboard.press("l");
      await page.waitForTimeout(300);
      const sw = await page.$("[data-layer='eez_boundaries'] input[type=checkbox]");
      if (!sw) fail(`${label} map: EEZ layer toggle missing`);
      else {
        if (await sw.isChecked()) fail(`${label} map: EEZ toggle checked at load`);
        await sw.click({ force: true });
        await page.waitForTimeout(400);
        if (!(await page.evaluate(() => (document.body.innerText || "").includes("no position on any boundary")))) fail(`${label} map: Marine Regions statement missing after enabling the EEZ layer`);
        await sw.click({ force: true });
      }
      // Context layers: listed, all off at load; two switched on draw with a legend and the ocean caveat
      await page.waitForSelector("[data-context-layers] [data-overlay-switch], [data-no-rasters]", { timeout: 8000 }).catch(() => null);
      A("context layers all off at load; two drawn with legend and ocean caveat; shipping as presence");
      const ov = await page.$$eval("[data-overlay-switch]", (els) => els.map((e) => [e.getAttribute("data-overlay-switch"), e.getAttribute("data-on")]));
      if (!ov.length) fail(`${label} map: no context layers listed`);
      if (ov.some((o) => o[1] !== "0")) fail(`${label} map: a context layer is on at load`);
      if (await page.$("[data-overlay-legend]")) fail(`${label} map: overlay legend visible at load`);
      const pick = [ov.find((o) => !/^ship_density/.test(o[0])), ov.find((o) => /^ship_density/.test(o[0]))].filter(Boolean).map((o) => o[0]);
      for (const name of pick) await page.click(`[data-overlay-switch='${name}'] label`, { force: true });
      if (pick.length) {
        await page.waitForSelector(".scs-overlay", { timeout: 10000 }).catch(() => null);
        await page.waitForTimeout(HTTP ? 1500 : 500);
        const drawn = await page.evaluate(() => document.querySelectorAll(".scs-centre .scs-overlay").length);
        if (drawn < pick.length) fail(`${label} map: ${drawn} of ${pick.length} overlays drawn`);
        const cav = await page.evaluate((n) => { const e = document.querySelector("[data-overlay-legend] [data-ocean-caveat]"); return !!e && !!(e.offsetWidth && e.offsetHeight) && (e.textContent || "").includes(n); }, OCEAN_NOTE);
        if (!cav) fail(`${label} map: ocean caveat not shown while an overlay is on`);
        const rows = await page.$$eval("[data-overlay-legend] [data-overlay]", (els) => els.map((e) => e.textContent || ""));
        if (rows.length !== pick.length || rows.some((r) => !/valid:/.test(r))) fail(`${label} map: overlay legend rows ${JSON.stringify(rows)}`);
        const ship = rows.find((r) => /presence/i.test(r));
        if (pick.some((n) => n.startsWith("ship_density")) && (!ship || !/not a count/.test(ship))) fail(`${label} map: shipping legend is not presence only`);
        await page.screenshot({ path: resolve(shotDir, `smoke_${tag}_overlays.png`) });
        shots.push(resolve(shotDir, `smoke_${tag}_overlays.png`));
        for (const name of pick) await page.click(`[data-overlay-switch='${name}'] label`, { force: true });
        await page.waitForTimeout(300);
        if (await page.$("[data-overlay-legend]")) fail(`${label} map: overlay legend stays after switching the overlays off`);
        else ok(`${label} map: overlays ${pick.join(", ")} drawn with legend and ocean caveat`);
      }
    }
    // Omnibar
    await page.keyboard.press(process.platform === "darwin" ? "Meta+k" : "Control+k");
    await page.waitForTimeout(300);
    let omni = await page.$(".bp6-omnibar input");
    if (!omni) {
      await page.click("[data-omnibar-trigger]");
      await page.waitForTimeout(300);
      omni = await page.$(".bp6-omnibar input");
    }
    if (!omni) fail(`${label}: Omnibar did not open`);
    else {
      const tryQuery = async (q, expectType, what) => {
        await omni.fill(q);
        await page.waitForTimeout(150);
        await page.waitForSelector(".bp6-omnibar .bp6-menu-header", { timeout: 5000 }).catch(() => null);
        const first = await page.evaluate(() => { const li = document.querySelector(".bp6-omnibar .bp6-menu-item"); const hdr = document.querySelector(".bp6-omnibar .bp6-menu-header"); return { item: li ? li.textContent : null, header: hdr ? hdr.textContent : null }; });
        if (!first.item || !(first.header || "").toLowerCase().includes(expectType)) fail(`${label} omnibar ${what} "${q}": got ${JSON.stringify(first)}`);
        else ok(`${label} omnibar ${what}: ${first.header}`);
      };
      if (contactId) await tryQuery(contactId, "contact", "det_id");
      if (vesselKey && vesselKey.startsWith("mmsi:")) await tryQuery(vesselKey.slice(5), "vessel", "MMSI");
      await tryQuery("10.25, 107.5", "go to point", "DD");
      await tryQuery("10 15 00 N 107 30 00 E", "go to point", "DMS");
      await tryQuery("48PVQ8899650631", "go to point", "MGRS");
      const shot = resolve(shotDir, `smoke_${tag}_omnibar.png`);
      await page.screenshot({ path: shot });
      shots.push(shot);
      await page.keyboard.press("Escape");
      const closedOmni = await page.waitForSelector(".bp6-omnibar input", { state: "detached", timeout: 2000 }).then(() => true, () => false);
      if (!closedOmni) fail(`${label}: Esc did not close the Omnibar`);
    }
    await check("#/about", "about");
    A("0 console errors, 0 page errors, no 4xx or 5xx, no request off the page's origin");
    for (const e of errors) fail(`${label}: ${e}`);
    await context.close();
  }
  // ---------------------------------------------------------------- stale lead variant (file mode, one desktop run)
  // One L1 lead's primary contact is made ambiguous in a copy of the page: the queue must drop the lead, its own page
  // must say it is stale and offer no decision, and the contact page must show no Lead section.
  const sv = !HTTP && (!ONLY || "file stale-variant".includes(ONLY) || ONLY.includes("stale")) ? staleVariant(page_path) : null;
  if (sv) {
    const label = "file stale-variant 1280 dark";
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, colorScheme: "dark" });
    await context.addInitScript(() => { try { window.localStorage.setItem("scs.theme", "dark"); } catch (e) { /* storage blocked */ } });
    const page = await context.newPage();
    const errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(`console.error: ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    const surl = "file://" + sv.file;
    A("stale lead: left out of the queue, its page says stale with no decision, its contact page has no Lead section");
    await page.goto(surl + "#/leads", { waitUntil: "load" });
    await page.waitForSelector("[data-queue-count]", { timeout: 20000 }).catch(() => null);
    const shown = await page.evaluate(() => (document.querySelector("[data-queue-count]")?.textContent || "").match(/of\s+([\d,]+)/)?.[1]?.replace(/,/g, "") || null);
    const want = (fileLeads || []).filter((l) => !isStale(l)).length - 1;
    if (shown === null || Number(shown) !== want) fail(`${label}: the queue lists ${shown} leads; with ${sv.lead_id} stale it should list ${want}`);
    await page.goto(surl + `#/lead/${encodeURIComponent(sv.lead_id)}`, { waitUntil: "load" });
    await page.waitForSelector("[data-stale-lead]", { timeout: 5000 }).catch(() => null);
    const staleCard = await page.evaluate(() => ({ callout: document.querySelector("[data-stale-lead]")?.textContent || null, tag: !!document.querySelector("[data-stale-tag]"), buttons: document.querySelectorAll(".scs-decisions button").length, none: !!document.querySelector("[data-stale-decisions]") }));
    if (!staleCard.callout || !/ambiguous/.test(staleCard.callout) || !staleCard.tag) fail(`${label}: lead page of ${sv.lead_id} does not say it is stale: ${JSON.stringify(staleCard)}`);
    if (staleCard.buttons || !staleCard.none) fail(`${label}: lead page of ${sv.lead_id} still offers ${staleCard.buttons} decision buttons`);
    await page.keyboard.press("x");
    await page.waitForTimeout(300);
    if (await page.$(".bp6-dialog")) fail(`${label}: X opened a decision dialog on a stale lead`);
    await page.screenshot({ path: resolve(shotDir, "smoke_stale_variant_lead.png") });
    await page.goto(surl + `#/contact/${encodeURIComponent(sv.det_id)}`, { waitUntil: "load" });
    await page.waitForSelector("[data-stale-leads]", { timeout: 5000 }).catch(() => null);
    const cp = await page.evaluate(() => ({ section: !!document.querySelector("[data-lead-section]"), staleLinks: document.querySelector("[data-stale-leads]")?.textContent || null, amb: !!document.querySelector("[data-identification='unmatched'][data-ambiguous='1']"), darkTag: !!document.querySelector("[data-header-dark-lead]") }));
    if (cp.section) fail(`${label}: the ambiguous contact ${sv.det_id} still shows a Lead section`);
    if (!cp.staleLinks || !cp.staleLinks.includes(sv.lead_id)) fail(`${label}: the contact page does not mark ${sv.lead_id} as stale`);
    if (!cp.amb || cp.darkTag) fail(`${label}: the contact page does not show ${sv.det_id} as ambiguous: ${JSON.stringify(cp)}`);
    await page.screenshot({ path: resolve(shotDir, "smoke_stale_variant_contact.png"), fullPage: true });
    A("0 console errors, 0 page errors, no 4xx or 5xx, no request off the page's origin");
    for (const e of errors) fail(`${label}: ${e}`);
    if (!failures.some((f) => f.startsWith(label))) ok(`${label}: ${sv.lead_id} left out of the queue (${shown} listed), marked stale on its page with no decision, no Lead section on ${sv.det_id}`);
    await context.close();
  }
} finally {
  await browser.close();
}
if (!HTTP) console.log(`page ${page_path}: ${(statSync(page_path).size / 1e6).toFixed(2)} MB`);
console.log("screenshots:\n  " + shots.join("\n  "));
if (documented404.size) console.log(`documented 404s (vessels not in the backend's vessel table): ${documented404.size}\n  ` + [...documented404].slice(0, 20).join("\n  "));
console.log("Context sections seen on contact pages: " + JSON.stringify(contextSeen) + " (1 = values, none = 'No ocean context for this object yet')");
console.log("assertions run (times evaluated):\n  " + [...asserted.entries()].map(([k, n]) => `${k} (${n})`).join("\n  "));
if (failures.length) {
  console.error(`\n${failures.length} failure(s)`);
  process.exit(1);
}
console.log(`\nsmoke check passed (${mode} mode)`);
