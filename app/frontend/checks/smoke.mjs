// Smoke check for the SCS Vessel Watch frontend (docs/product_design.md section 16, subset for round 2).
// Single-file mode (default): opens the fixture-injected single file over file:// at 1280x800 and 390x844 in dark and
// light, plus a 1024x800 tablet run and a blocked-storage run; visits the queue, a lead, a contact, a vessel, the map,
// a pass, a cell and the Omnibar. Local-app mode (--http=URL): the same routes against the running backend, dark only,
// with every non-GET API call intercepted so nothing is written. Fails on any console error or page error, a missing
// caveat banner, an EEZ layer on at load, a request off the page's origin, a horizontal scroll at 390 px, a map that
// does not fill the centre column, or an overlapping tablet layout.
// Run: NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node checks/smoke.mjs [page.html] [shot_dir] [--http=http://127.0.0.1:8750]
// Never run playwright install: the preinstalled browser is used.
import { createRequire } from "node:module";
import { existsSync, mkdirSync, statSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const here = dirname(fileURLToPath(import.meta.url));
const flags = process.argv.slice(2).filter((a) => a.startsWith("--"));
const positional = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const HTTP = (flags.find((f) => f.startsWith("--http=")) || "").slice("--http=".length).replace(/\/$/, "") || null;
const page_path = positional[0] || resolve(here, "..", "dist-single", "index.fixture.html");
const shotDir = positional[1] || resolve(process.env.SCS_SHOT_DIR || "/tmp/claude-0/-home-user-Dark-Vessel-Study/cec849af-66e9-5eb5-ae39-8ecc39ea8096/scratchpad/frontend");
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
const RUNS = HTTP
  ? [{ name: "desktop", width: 1280, height: 800, theme: "dark" }, { name: "phone", width: 390, height: 844, theme: "dark" }]
  : [
    { name: "desktop", width: 1280, height: 800, theme: "dark" }, { name: "desktop", width: 1280, height: 800, theme: "light" },
    { name: "phone", width: 390, height: 844, theme: "dark" }, { name: "phone", width: 390, height: 844, theme: "light" },
    { name: "tablet", width: 1024, height: 800, theme: "dark" },
    { name: "desktop", width: 1280, height: 800, theme: "dark", blockedStorage: true },
  ];
const failures = [];
const shots = [];
const fail = (msg) => { failures.push(msg); console.error("FAIL " + msg); };
const ok = (msg) => console.log("ok   " + msg);
const allowed = (u) => /^(data|blob):/.test(u) || (HTTP ? u.startsWith(origin + "/") : /^file:/.test(u));
const overlap = (a, b) => !!a && !!b && a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
const rect = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; }, sel);

const browser = await chromium.launch({ args: ["--disable-gpu"] });
try {
  for (const vp of RUNS) {
    const theme = vp.theme;
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
    const label = `${mode} ${vp.name} ${theme}${vp.blockedStorage ? " blocked-storage" : ""}`;
    const tag = `${mode === "http" ? "http_" : ""}${vp.name}_${theme}${vp.blockedStorage ? "_blocked" : ""}`;
    const errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(`console.error: ${m.text()}`); });
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    page.on("request", (r) => { const u = r.url(); if (!allowed(u)) fail(`${label}: request off the page's origin ${u}`); });
    page.on("response", (r) => { if (r.status() >= 400 && !(HTTP && r.request().method() !== "GET")) errors.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()}`); });
    page.on("requestfailed", (r) => { const u = r.url(); if (!/^(data|blob):/.test(u)) fail(`${label}: failed request ${u} (${r.failure()?.errorText})`); });
    const expectedTheme = vp.blockedStorage ? "dark" : theme;

    const check = async (route, name, opts = {}) => {
      await page.goto(url + route, { waitUntil: "load" });
      await page.waitForSelector(".scs-app", { timeout: 20000 });
      await page.waitForTimeout(HTTP ? 1500 : 700);
      const banners = await page.$$eval("[data-banner]", (els) => els.map((e) => ({ pos: e.getAttribute("data-banner"), text: e.textContent || "", visible: !!(e.offsetWidth && e.offsetHeight) })));
      const top = banners.find((b) => b.pos === "top");
      const bottom = banners.find((b) => b.pos === "bottom");
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
        const gutter = await page.evaluate(() => { const el = document.querySelector(".scs-page, .scs-rail"); if (!el) return null; const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return { left: r.left, pl: parseFloat(cs.paddingLeft) }; });
        if (gutter && Math.round(gutter.left + gutter.pl) < 16) fail(`${label} ${name}: side gutter ${gutter.left + gutter.pl} px under 16`);
      }
      if (opts.objectPage) {
        const under = await page.evaluate((c) => (document.querySelector("[data-page] .scs-caveat-line")?.textContent || "").includes(c), CAVEAT_SHORT);
        if (!under) fail(`${label} ${name}: short caveat missing under the title`);
      }
      const bad = await page.evaluate(() => { const t = document.body.innerText || ""; const hits = []; for (const w of ["illegal", "suspicious", "violation", "threat"]) { const re = new RegExp(w, "ig"); let m; while ((m = re.exec(t))) { const ctx = t.slice(Math.max(0, m.index - 60), m.index + 60); if (!/Not evidence of illegal activity|does not mean illegal|Dark does not mean illegal/i.test(ctx)) hits.push(w + ": " + ctx.replace(/\s+/g, " ")); } } return hits; });
      for (const h of bad) fail(`${label} ${name}: status word outside the caveat: ${h}`);
      const dashes = await page.evaluate((src) => new RegExp(src).test(document.body.innerText || ""), DASHES.source);
      if (dashes) fail(`${label} ${name}: em or en dash in rendered text`);
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
        if (name === "map") await mapFill("map at 1024");
      }
      await page.goto(url + "#/leads", { waitUntil: "load" });
      await page.waitForTimeout(700);
      await page.keyboard.press("j");
      await page.waitForTimeout(400);
      const insp = await page.evaluate(() => { const e = document.querySelector("[data-inspector]"); return e ? e.getBoundingClientRect().width : 0; });
      if (insp < 200) fail(`${label}: J did not open the inspector overlay (width ${insp})`);
      else ok(`${label}: inspector overlay opens on selection`);
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
      for (const e of errors) fail(`${label}: ${e}`);
      await context.close();
      continue;
    }

    // ---------------------------------------------------------------- full run
    await check("#/leads", "queue");
    const queueKind = await page.evaluate(() => document.querySelector("[data-queue]")?.getAttribute("data-queue") || (document.querySelector("[data-queue-empty]") ? "empty" : "none"));
    if (queueKind === "none") fail(`${label}: no queue rendered`);
    if (vp.name === "phone") {
      const firstTab = await page.evaluate(() => { const b = document.querySelector(".scs-tabbar button"); return b ? [b.getAttribute("data-tab"), b.getAttribute("aria-selected")] : null; });
      if (!firstTab || firstTab[0] !== "queue" || firstTab[1] !== "true") fail(`${label}: phone first tab is not Queue selected (${JSON.stringify(firstTab)})`);
      if (queueKind !== "cards" && queueKind !== "empty") fail(`${label}: phone queue should be a card list, got ${queueKind}`);
    } else {
      if (queueKind !== "table" && queueKind !== "empty") fail(`${label}: desktop queue should be a table, got ${queueKind}`);
      const tl = await page.$(".scs-timeline");
      if (!tl) fail(`${label}: no timeline on the console at #/leads`);
    }
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
      if (!(await page.$$(".scs-decisions button")).length) fail(`${label} lead: decision buttons missing`);
      const raw = await page.evaluate(() => { const t = document.querySelector(".scs-leadcard")?.innerText || ""; return (t.match(/\b[a-z]+(_[a-z0-9]+)+\b/g) || []).filter((w) => !/^(ais|det|s1|cnn|lead|mmsi)_/.test(w) && !/_v\d/.test(w)); });
      if (raw.length) fail(`${label} lead: raw codes in the lead card: ${[...new Set(raw)].join(", ")}`);
      primary = await page.evaluate(() => { const a = document.querySelector("[data-field='primary_id'] a"); return a ? a.getAttribute("href") : null; });
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
    if (!vesselKey && HTTP) vesselKey = await page.evaluate(async () => { const j = await (await fetch("/api/v1/vessels?limit=1")).json(); return j.items?.[0]?.vessel_key || null; });
    if (vesselKey) {
      await check(`#/vessel/${encodeURIComponent(vesselKey)}`, "vessel", { objectPage: true });
      if (!(await page.evaluate(() => (document.body.innerText || "").includes("self-reported")))) fail(`${label} vessel: identity note missing`);
    } else fail(`${label}: no vessel link found`);
    // Pass: the live pass leads with the coverage result; its empty fields carry no source
    const passId = "live_S1D_20261008T2258"; // the first live pass, in the fixture and the local app
    if (passId) {
      await check(`#/pass/${encodeURIComponent(passId)}`, "pass", { objectPage: true });
      const wrong = await page.evaluate(() => Array.from(document.querySelectorAll(".scs-provtable tr")).filter((tr) => /^ais_/.test(tr.cells[0]?.textContent || "") && /acquisition plan/i.test(tr.textContent || "")).length);
      if (wrong) fail(`${label} pass: ${wrong} AIS count rows credited to the acquisition plan`);
      if (!(await page.$("[data-coverage-result]"))) fail(`${label} pass: the coverage result does not lead the live pass page`);
    }
    // Cell: the lead's primary when it is a cell, else a fixed id
    const cellRoute = primary && primary.startsWith("#/cell/") ? primary : "#/cell/r13c64";
    await check(cellRoute, "cell", { objectPage: true });
    if (HTTP && !(await page.$("[data-cell-id]"))) fail(`${label} cell: the local app's cell record did not render`);
    // Map: EEZ off at load, no tiles, fills the centre
    await check("#/map", "map");
    if (vp.name === "phone") {
      const tabSel = await page.evaluate(() => document.querySelector(".scs-tabbar button[aria-selected='true']")?.getAttribute("data-tab"));
      if (tabSel !== "map") fail(`${label} map: phone map tab not selected`);
    } else {
      await mapFill("map at 1280");
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.waitForTimeout(500);
      await mapFill("map at 1440");
      await page.setViewportSize({ width: vp.width, height: vp.height });
      // Leads to Map through the navbar button
      await page.goto(url + "#/leads", { waitUntil: "load" });
      await page.waitForTimeout(700);
      await page.click(".scs-navbtn:has-text('Map')");
      await page.waitForTimeout(700);
      await mapFill("map from the navbar");
    }
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
    for (const e of errors) fail(`${label}: ${e}`);
    await context.close();
  }
} finally {
  await browser.close();
}
if (!HTTP) console.log(`page ${page_path}: ${(statSync(page_path).size / 1e6).toFixed(2)} MB`);
console.log("screenshots:\n  " + shots.join("\n  "));
if (failures.length) {
  console.error(`\n${failures.length} failure(s)`);
  process.exit(1);
}
console.log(`\nsmoke check passed (${mode} mode)`);
