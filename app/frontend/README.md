# SCS Vessel Watch frontend

One React and Blueprint frontend that builds both product forms (docs/product_design.md section 13, app/CONTRACT.md section 7):

- `npm run build` writes `dist/` for the local app (relative base `./`, served by the backend from `app/frontend/dist/`).
- `npm run build:single` writes `dist-single/index.html` with every script, style and asset inlined and no data. The bundle builder (`app/build/`, round 3) injects one `<script type="application/json" id="scs-part-NAME">` element per part before `</body>` (contract 6.1).

The page picks its data adapter at start: embedded when `scs-part-meta` exists, else HTTP against `/api/v1`. Both adapters return the record shapes of contract section 3 (`src/adapters/types.ts`).

## Stack

Exactly the pins of `docs/research/stack_decision.md`: React 18.3.1, @blueprintjs/core 6.20.0, icons 6.14.1, select 6.3.6, table 6.2.6, colors 5.1.16, leaflet 1.9.4, mgrs 2.2.0, vite 8.3.1, @vitejs/plugin-react 6.1.1, vite-plugin-singlefile 2.3.3, typescript 6.0.3, @types/react 18.3.31, @types/react-dom 18.3.7. One dev-only addition for type checking: @types/leaflet 1.9.22 (MIT, published 2026-08-01). No router, state or chart library. `package-lock.json` was generated with `npm install --before=2026-09-24T23:59:59Z`; install with `npm ci`.

Runtime tree of the lockfile: 50 packages, licences MIT 42, Apache-2.0 5, BSD-2-Clause 1, BSD-3-Clause 1, 0BSD 1. `scripts/gen_licences.mjs` regenerates `src/views/licences.ts` (the About view's package table and the unmodified licence texts) from the lockfile and the installed LICENSE files; run it after a dependency change.

## Scripts

| Script | What |
|---|---|
| `npm run dev` | Vite dev server on 127.0.0.1:5173, proxies `/api` to the backend on 8750 |
| `npm run build` | `dist/` |
| `npm run build:single` | `dist-single/index.html`, nothing loaded from the network |
| `npm run typecheck` | `tsc --noEmit` |
| `node fixtures/inject.mjs [page] [bundle] out.html` | test page: fixture parts injected into the single file (scratch output, not committed) |
| `npm run check` | `checks/smoke.mjs` with the preinstalled Playwright (`NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`); never run `playwright install` |

The smoke check has two modes. Single-file (default): the fixture-injected page over `file://` at 1280x800 and 390x844 in dark and light, plus a 1024x800 tablet run (navbar on one line, rail icon strip clear of the queue header and the zoom control, inspector overlay on selection) and a blocked-storage run (the storage notice on the lead card, R records reviewing, the export button is highlighted and downloads). Local app: add `--http=http://127.0.0.1:8750` with the backend running; dark only, every non-GET API call is answered inside the browser so nothing is written, and any 4xx or 5xx response fails the run. Both modes check that the map fills the centre column at 1280 and 1440 and after the navbar Map button.

## Development fixture

`fixtures/make_fixture.py` (run with the `darkvessel` Python env) writes `fixtures/bundle_small.json` in the contract 6.2 encodings from real open-build files only (never `data/research/`): a few hundred live and September contacts with CNN scores, structures, VIIRS lights, aisstream vessels with simplified tracks, planned and processed passes, and the land, AOI, reporting-box and Marine Regions boundary-line geometry. Because `data/leads_open.gpkg` does not exist yet and no live contact is matched or unmatched so far, it adds SYNTHETIC leads and synthetic AIS statuses, every one flagged `synthetic = true` with a note; `meta.fixture.synthetic` is true and the page shows a FIXTURE tag in the navbar and on the Info tab. It is a test fixture, not a data product. `fixtures/test_encoders.py` round-trips the encoders offline; it sits outside the repo's pytest `testpaths`, so run it by path (`python -m pytest app/frontend/fixtures/test_encoders.py`). Lead explanations, change indicators and factor names are the lead builder's codes (`darkvessel.leads.rules`, `darkvessel.leads.priority`), as the API returns them; the frontend maps codes to sentences (`src/app/text.ts`) and shows an unknown code humanised.

```
/home/user/.mamba/envs/darkvessel/bin/python app/frontend/fixtures/make_fixture.py
npm run build:single
node fixtures/inject.mjs dist-single/index.html fixtures/bundle_small.json /path/to/scratch/index.fixture.html
NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers node checks/smoke.mjs /path/to/scratch/index.fixture.html /path/to/shots
```

## Readings of contract 6.2 taken by the fixture and the embedded adapter

Where section 6.2 leaves the part layout open, the fixture picks the simplest reading. These are listed for the contract bump that the round 3 bundle builder will need:

1. Each part is one JSON object `{"type", "n", "columns", "records", "prov", "note"}`. `prov` at part level is the default field-to-source map for every row; a record's `prov` overrides it.
2. `detid.w` is a list parallel to `pfx` (one width per prefix) so 5-digit regional and live ids and 4-digit Ca Mau ids share a column; a one-element list applies to every prefix.
3. `lightid` carries `q` (u32) and `w` (6); the id is rebuilt from the `satellite` and `time_utc` columns of the same part.
4. `dict8`/`dict16`, `bool8`, `time` and `ref16` columns carry an explicit `na`; integer columns carry `na` and `s` (omitted when 1); the frontend reads `t` and never assumes a type.
5. `vessels` holds its simplified tracks as `tracks`, a `geom` block of kind `line` with props `vessel_ref` (ref16), `start_utc`, `end_utc`, `n_positions`. Per-position times and gap detection stay in the local app.
6. `leads` columns: `lead_type`, `primary_type` (dict8 over `contacts`, `lights`, `cells`, `vessels`), `primary` (u32 row), `priority`, `state`, `reason`, `region_box`, `time_utc`, `next_look_utc`, `lon`, `lat`, one u8 column per factor; a part-level `factors` list names each factor column with `max_points` and `source`; `lawful_explanations` by type at part level; `records[lead_id]` holds `title`, `evidence`, `history`, `change_indicators`, `prov`.
7. `passes` is a GeoJSON FeatureCollection whose feature properties are the Pass record (contract 3.6) and whose geometry is the footprint.
8. `geo` is `{"layers": {name: geom}}` plus `eez_statement` and `eez_disclaimer`; polygon rings drop the closing vertex; a feature's rings are drawn with the even-odd rule, so holes and multipart outers need no flag.
9. `meta` adds `caveat_short`, `ais_recording` (period, gaps, counts, from `data/ais_live_summary.json`), `live_rules` (the live file's `about` rules shown on the Contact page), `data_credit`, `fixture`.
10. Contacts carry `src` as a dict8 column (`det_live`, `det_regional`, `det_camau`) and, in the fixture only, a `synthetic` bool8 column that the real builder does not write.
11. A `cells` part, when present, is columnar with a `cell_id` column (or `row` and `col`); `records[cell_id]` may add `nightly`, `eez` and `prov`. The fixture has no cells part, so the Cell page says the local app serves cells.
12. Lead `factor`, `lawful_explanations` and `change_indicators` hold codes, not sentences (the API does the same, although contract 3.5 types them as display strings).

## Local-app behaviour that depends on the API

- Radar chip: the Contact page requests `chip.webp` only when the record's `chip` field is set (contract 3.1). Otherwise it shows "No cached radar chip" with a Fetch chip button that calls `chip.webp?fetch=1` and shows the image or the backend's reason.
- Lead evidence: `GET /leads/{id}` carries previews; an evidence item without one is shown as not resolved and is never fetched one by one (that only produced 404s).
- Cell page: `GET /cells/{cell_id}` (static sea fields, nightly fields with each field's source, AIS reach, look probability, expected activity when present). The `eez` block is shown only while the EEZ layer is on, under "As published by Marine Regions".
- Decisions: R records `reviewing` at once (spec 4.1 and 9); E and X open the reason picker with no reason preselected and "Record decision" disabled until one is chosen; U needs a note.

## What is not in this round

Histogram tab, measure tool, rectangle search, Cell page in the single-file page (needs the `cells` part), Event objects (pending `data/events_open.gpkg`), chips (`chips` part from the bundle builder), rasters, Ca Mau radar view, label actions on the Contact page (keys 1 to 4), Shift+drag time filter and playback on the timeline, saved views. The simple timeline has pass stepping, "Last 12 days" and "All".
