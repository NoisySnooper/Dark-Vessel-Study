# SCS Vessel Watch: stack decision

Research note for the product build. Written 2026-10-08/09 (UTC) by task R1-T4; revised 2026-10-09 07:00 to 08:00 UTC after review (Python pins, route order, bundle measurements on the real files, real chip sizes). Every version, date and licence below comes from the npm registry or PyPI, read in this session; every size and timing was measured in this session on the shared 4-core container (headless Chromium, software rendering), in a scratch build that is not part of the repo. Sources are listed at the end with what each one confirmed.

The product spec is `docs/product_design.md`; the data contract is `app/CONTRACT.md`.

## 1. Decision

| Layer | Choice | Why, in one line |
|---|---|---|
| UI toolkit | React 18.3.1 + Blueprint 6 (`@blueprintjs/core` 6.20.0, `icons` 6.14.1, `select` 6.3.6, `table` 6.2.6, `colors` 5.1.16) | Owner's toolkit; Blueprint is React-only; 6.20.0 is the newest core that passes the 14-day rule |
| Map | Leaflet 1.9.4, Canvas 2D, no tile layer, plus one custom typed-array canvas layer for bulk points | Only candidate with zero console errors in the single-file page opened from `file://`; no WebGL needed; +165 KB |
| Coordinates | `mgrs` 2.2.0 | MGRS in the Omnibar and the inspector (already used by the demo page) |
| Bundler | Vite 8.3.1 + `@vitejs/plugin-react` 6.1.1 + `vite-plugin-singlefile` 2.3.3 | One frontend, two outputs: `dist/` for the local app, one inlined HTML file for the shareable page |
| Types | TypeScript 6.0.3, `@types/react` 18.3.31, `@types/react-dom` 18.3.7 | Type checking only (`tsc --noEmit`); Vite strips types itself |
| Backend | FastAPI 0.141.1 on uvicorn 0.53.0, with starlette 1.7.0 and annotated-doc 0.0.5 pinned as well | Typed models that double as the contract, offline `TestClient`, OpenAPI JSON for the frontend types |
| Data access | geopandas 1.2.0, pyogrio 0.12.1, pyarrow 25.0.0, pandas 3.0.6, rasterio 1.4.4, pillow 12.3.0, pydantic 2.13.5 | Already in the `darkvessel` env; reads every source file in about 1.5 s |
| Browser tests | Playwright 1.56.1 and Chromium build 1194, both preinstalled (`/opt/node-tools`, `/opt/pw-browsers`) | Never run `playwright install`; the preinstalled pair is the only one with a browser |

Not adopted: MapLibre GL JS, deck.gl, React 19, TypeScript 7, `@blueprintjs/datetime`, any router, state or chart library (section 4).

## 2. Rules applied

1. Exact versions only, each published at least 14 days before 2026-10-08, that is on or before 2026-09-24 (owner's artifact rule).
2. The rule covers the whole dependency tree, not only the top-level packages: the lockfile is generated with `npm install --before=2026-09-24T23:59:59Z`, which "will rebuild the npm tree such that only versions that were available on or before the --before time get installed" (npm config docs). Then `npm ci` from the committed `app/frontend/package-lock.json`.
3. Licences must be permissive for the open build. The trial install resolved 106 runtime packages, all published on or before 2026-09-24 (newest: `maplibre-gl` 6.11.2, 2026-09-24T12:44Z, a trial-only package), licences MIT 80, ISC 9, Apache-2.0 7, BSD-3-Clause 5, BSD-2-Clause 3, 0BSD 1, MIT OR Apache-2.0 1. Build-only tools add MPL-2.0 (`lightningcss`, not shipped in the page).
4. The single-file page loads nothing from the network (no CDN, no tiles, no fonts). Everything is inlined by the build.

## 3. Pinned packages

### 3.1 npm (frontend)

Publish times from `npm view <pkg> time --json`, licences from the registry document of that exact version.

| Package | Version | Published (UTC) | Licence | Latest on 2026-10-08 and why not |
|---|---|---|---|---|
| react | 18.3.1 | 2024-04-26 16:42 | MIT | 19.3.0 (2026-09-09) passes the date rule; 18 is the instructed major and the one every Blueprint 6 peer range (`18 \|\| 19`) and the demo history were proven on |
| react-dom | 18.3.1 | 2024-04-26 16:42 | MIT | as react |
| @blueprintjs/core | 6.20.0 | 2026-09-17 16:45 | Apache-2.0 | 6.21.0 (2026-09-30) is 8 days old; it passes the rule on 2026-10-14 |
| @blueprintjs/icons | 6.14.1 | 2026-09-17 16:49 | Apache-2.0 | is latest |
| @blueprintjs/select | 6.3.6 | 2026-09-17 16:39 | Apache-2.0 | 6.4.0 (2026-09-30) too new |
| @blueprintjs/table | 6.2.6 | 2026-09-17 16:38 | Apache-2.0 | 6.3.0 (2026-09-30) too new |
| @blueprintjs/colors | 5.1.16 | 2026-03-25 01:00 | Apache-2.0 | is latest |
| leaflet | 1.9.4 | 2023-05-18 11:04 | BSD-2-Clause | is latest stable (2.0.0-alpha.1 is a pre-release) |
| mgrs | 2.2.0 | 2026-07-11 14:03 | MIT | is latest |
| vite | 8.3.1 | 2026-09-24 12:26 | MIT | 8.3.4 (2026-10-08) too new |
| @vitejs/plugin-react | 6.1.1 | 2026-08-28 03:30 | MIT | 6.1.2 (2026-10-05) too new; 6.1.1 peers `vite ^8.0.0` |
| vite-plugin-singlefile | 2.3.3 | 2026-04-17 22:28 | MIT | is latest; peers `vite ^5.4.21 \|\| ^6 \|\| ^7 \|\| ^8` |
| typescript | 6.0.3 | 2026-04-16 23:38 | Apache-2.0 | 7.0.2 (2026-07-08) passes the date rule but ships per-platform native binaries as 20 optional dependencies; 6.0.3 is plain JavaScript and runs anywhere |
| @types/react | 18.3.31 | 2026-06-05 20:10 | MIT | newest 18.x types |
| @types/react-dom | 18.3.7 | 2025-04-30 10:37 | MIT | newest 18.x types |

Blueprint's own dependencies resolved under the cutoff include `@floating-ui/react` 0.27.20 (2026-07-11, MIT), `react-popper` 2.3.0 (MIT), `react-transition-group` 4.4.5 (BSD-3-Clause), `normalize.css` 8.0.1 (MIT), `classnames` 2.5.1 (MIT), `tslib` 2.6.3 (0BSD).

Playwright is not a dependency of the app. The checks run with the preinstalled `playwright` 1.56.1 (published 2025-10-17 00:51, Apache-2.0) via `NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`. A pinned `@playwright/test` would need its own browser build, and the environment rule forbids `playwright install`.

### 3.2 Python (backend)

PyPI JSON API, read 2026-10-08. Installed versions from `pip show` in `/home/user/.mamba/envs/darkvessel`.

| Package | Version | Released (UTC) | Licence | In the env? |
|---|---|---|---|---|
| fastapi | 0.141.1 | 2026-07-29 17:18 | MIT | no: install (0.142.0 to 0.143.0 were released 2026-09-29 to 2026-10-08, too new) |
| uvicorn | 0.53.0 | 2026-09-14 07:44 | BSD-3-Clause | no: install (0.54.0 is 2026-09-25) |
| starlette | 1.7.0 | 2026-09-23 07:30 | BSD-3-Clause | no: pulled in by fastapi; pin it (below) |
| annotated-doc | 0.0.5 | 2026-07-28 13:50 | MIT | no: pulled in by fastapi; pin it (below) |
| pydantic | 2.13.5 | not read | not read | yes |
| anyio 4.15.1, click 8.5.0, h11 0.16.0, typing-inspection 0.4.4, typing_extensions 4.16.0 | | | | yes |
| geopandas 1.2.0, pyogrio 0.12.1 (MIT), pyarrow 25.0.0, pandas 3.0.6 (BSD-3-Clause), shapely 2.1.2 (BSD-3-Clause), rasterio 1.4.4 (BSD), numpy 2.4.6, pillow 12.3.0, httpx 0.28.1 (BSD-3-Clause), pytest 9.1.1 | | | | yes |

`pip install --dry-run fastapi==0.141.1 uvicorn==0.53.0` in the env reports: "Would install annotated-doc-0.0.5 fastapi-0.141.1 starlette-1.7.0 uvicorn-0.53.0". Nothing else changes. Nothing was installed by this task.

Pin all four, not only the two top-level packages. FastAPI 0.141.1 declares `starlette>=0.46.0` and `annotated-doc>=0.0.2` with no upper bound (PyPI `requires_dist`, read 2026-10-09). Starlette 1.7.0 (2026-09-23) is its newest release at the check, so today the resolver picks it; any later Starlette release would be pulled in by an unpinned install the day it appears, before it is 14 days old. The round 2 backend task installs, and the PM adds to `environment.yml` (a shared file):

```
fastapi==0.141.1
uvicorn==0.53.0
starlette==1.7.0
annotated-doc==0.0.5
```

## 4. Options compared

### 4.1 Map library

Three variants of the same Blueprint shell (navbar, tabs, tree, card, table, omnibar, drawer, callout, hotkeys) were built with Vite 8.3.1 and `vite-plugin-singlefile` into one HTML file each, then opened from `file://` in headless Chromium at 1440 x 900 and 390 x 844 with 2,000 points and one land polygon.

| | Leaflet 1.9.4 | MapLibre GL JS 6.11.2 | deck.gl 9.4.0 (core + layers, no basemap) |
|---|---|---|---|
| Licence | BSD-2-Clause | BSD-3-Clause | MIT |
| Single-file page, raw | 1,774 KB | 2,714 KB | 2,533 KB |
| Same, gzip | 458 KB | 691 KB | 661 KB |
| Added to the 1,610 KB shell | +165 KB | +1,105 KB | +923 KB |
| Console errors from `file://` | 0 | 3 errors and 1 page error: `SecurityError: Failed to construct 'Worker'` (the ESM build starts a module worker from a script URL the inlined page does not have) | 0 errors; 2 warnings ("Automatic fallback to software WebGL has been deprecated") |
| Needs WebGL | no (Canvas 2D) | yes | yes |
| Text labels without a server | yes (DOM) | needs a glyph source (`glyphs` URL); inlining PBF glyphs means a custom protocol | yes (TextLayer builds its own atlas) |
| Time to map ready (desktop / phone) | 426 / 406 ms | 966 / 521 ms | 2,319 / 1,077 ms |
| Raster overlays | `L.imageOverlay` | image source | BitmapLayer |
| Proven in this repo | yes: demo page v8 (`src/darkvessel/viz/demo_template.html`) | no | no |

MapLibre's worker can be pointed at an inlined blob with `setWorkerUrl()` (listed in the MapLibre API docs), but that is extra build plumbing and the WebGL requirement remains: Chromium now warns that software WebGL fallback is deprecated, which puts the no-console-error acceptance check at the mercy of the test machine's GPU. Leaflet needs neither.

Bulk points, measured with Leaflet 1.9.4, 80,000 random points over the AOI at zoom 5, 1440 x 900:

| Method | First draw | Pan, per 200 px step | JS heap |
|---|---|---|---|
| 80,000 `L.circleMarker` on one `L.canvas` renderer | 633 ms | 225 ms | 72 MB |
| One custom `L.Layer` drawing typed arrays on one canvas, viewport culling | 105 ms | 63 ms | 10 MB |

Decision: Leaflet, with the custom typed-array layer for the regional contacts (78,615), fixed structures (25,224) and lights (48,692); `L.circleMarker` only for small sets (selection, one scene, one pass, leads). Hit testing for the custom layer uses a uniform grid index over the visible points. The map runs in Web Mercator (Leaflet's default CRS) with no tile layer: land from Natural Earth 10 m, rasters as image overlays. The Ca Mau radar view keeps the demo's approach (`L.CRS.Simple` over the radar image grid).

### 4.2 UI toolkit details

- Blueprint is the owner's choice and is React-only, so React is fixed. Both 18 and 19 satisfy Blueprint 6's peer range.
- Do not import `@blueprintjs/icons/lib/css/blueprint-icons.css`. It is the icon font; it references `.ttf`, `.eot`, `.woff2`, `.woff` and `.svg` files, and the SVG font (517 KB each for 16 px and 20 px) is not inlined ("Inlining of SVG isn't supported directly by Vite", plugin README). The React `Icon` component draws inline SVG paths and needs no font.
- With the plugin, Blueprint's lazy icon loader is inlined whole. The 1,610 KB shell includes every icon path; a custom loader that registers only the icons in use is a later optimisation, not needed for the budget.
- `@blueprintjs/datetime` 6.2.6 depends on `date-fns` ^2.28.0, `date-fns-tz` and `react-day-picker` ^8.10.0. The timeline uses core `RangeSlider` plus typed ISO 8601 inputs instead. Add datetime later only if an analyst asks for a calendar picker.
- No router library: object pages use a 50-line hash router (`#/contact/<det_id>`), which also works in the single-file page where there is no server. No state library: React context plus reducers. No chart library: the histogram and timeline bars are hand-drawn SVG.

### 4.3 Bundler

Vite 8.3.1 built each trial in 1.1 to 3.4 s. `vite-plugin-singlefile` 2.3.3 inlined all JavaScript and CSS into one HTML file (plugin README: "allows you to inline all JavaScript and CSS resources directly into the final dist/index.html file"). The local app uses the normal multi-file `dist/`. Data is never baked into the JavaScript bundle: the single-file build injects one JSON script element per object type after the Vite build (contract section 6), so the same compiled frontend serves both forms.

### 4.4 Backend

| | FastAPI 0.141.1 + uvicorn 0.53.0 | Flask 3.1.3 (2026-02-19, BSD-3-Clause) | Standard library `http.server` |
|---|---|---|---|
| New packages in the env | 4 (fastapi, uvicorn, starlette, annotated-doc) | 4 or more (flask, werkzeug, itsdangerous, blinker; jinja2 if absent) plus a production server | 0 |
| Query and body validation | pydantic models (pydantic 2.13.5 already installed) | by hand or an extension | by hand |
| Machine-readable contract | `/openapi.json` generated from the models; frontend types can be generated from it | no | no |
| Offline tests | `fastapi.testclient.TestClient` on httpx 0.28.1 (installed) | `app.test_client()` | by hand |
| Fit | read-mostly JSON API over files loaded at start; one append-only write (lead decisions) | same | same, more code |

Decision: FastAPI. The response models are the contract in code, and the OpenAPI document lets the frontend task check its types against the backend without sharing files. Disable `docs_url` and `redoc_url` (their pages load scripts from a CDN); keep `/openapi.json`. Bind to 127.0.0.1 only. Serve the built frontend from the same process.

Route order: FastAPI evaluates path operations in declaration order, so "the path for /users/me is declared before the one for /users/{user_id}" (FastAPI path parameters tutorial, "Order matters"). In this API, `GET /api/v1/cells/at` must be declared before `GET /api/v1/cells/{cell_id}`, or `at` is read as a cell id; contract test (h) checks it. Mount the static frontend last, after every `/api/v1` route.

Load time, measured with pyogrio (Arrow) in the env: contacts `detections_regional_4326` (78,615 rows) 0.12 s, `viirs_lights_4326` (48,692) 0.66 s, `structures_regional_4326` (25,224) 0.59 s, `detections_verified_4326` (6,005) 0.03 s, `vessels_latest_4326` (1,277) 0.01 s, `tracks_4326` (1,094) 0.04 s, EEZ boundaries 0.03 s, `weather_context.parquet` (162,386) 0.08 s; about 1.5 s for all GeoPackage layers. Everything fits in memory; no database.

## 5. Size budget inputs (single-file page, limit 16 MB)

Two kinds of numbers. The trial measurements (frontend shell, map libraries) come from the scratch builds of section 4. The data numbers were re-measured at 07:00 UTC on 2026-10-09, after review, by encoding the real files exactly as `app/CONTRACT.md` section 6.2 specifies (typed little-endian columns in base64, `dict8`/`dict16` for categories, `detid` and `lightid` ids, `ref16` vessel references, `const` for the caveat) and taking the length of the serialised JSON. The measuring script is a scratch file, not part of the repo; round 2's `build_single.py` reproduces the numbers with the real encoder and prints them per part.

| Item | Measured | Note |
|---|---|---|
| Frontend shell (React, Blueprint core, select, table, all icon paths, CSS) | 1,610 KB raw, 409 KB gzip; CSS 492 KB, JS 1,116 KB | `none` variant |
| Shell plus Leaflet | 1,774 KB | chosen stack, before app code |
| Demo page v8 regional columns | 46.7 bytes per contact in base64 typed columns (16 columns, 103,839 rows, 4.85 MB) | earlier demo encoding, for comparison |
| Open contacts: September run, 78,615 rows, `not_checked`, CNN scores from `data/ml/regional_cnn.parquet` | 2.73 MB | D1 numeric fields that are not null in the open build |
| Open contacts: live pass `live_S1D_20261008T2258` (813), Ca Mau (6,005), structures (25,224) | 0.03, 0.21, 0.30 MB | |
| Research contacts: September, 78,615 rows with every D1 numeric field plus `vessel_ref` and `nearest_ref` | 4.72 MB | identity by reference (contract section 6.2) |
| Same, with the matched contacts' identity strings as JSON records instead (9,954 rows, 10 fields) | 3.82 MB more | why identity is stored by reference |
| Research vessels: 8,924 `gfw_vessels.parquet` rows plus 2,407 nearest-AIS stubs, identity strings once per vessel | 1.27 MB | |
| Open vessels: 1,277 aisstream vessels; 1,094 tracks simplified at 0.002 degree (3,122 vertices) | 0.11 MB; 0.06 MB | |
| Lights: 48,692 rows (position, time, radiance, quality, satellite, nights seen) plus the `lightid` index | 1.10 MB plus 0.26 MB | the id is rebuilt from satellite, time and a 6-digit index; this holds for every row |
| Cells: 5,116 static rows; their `marineregions_*` block; one night of daily fields | 0.35, 0.08, 0.10 MB | |
| Same tables as plain JSON (`orient=split`), trial | contacts 10.3 MB, lights 8.0 MB | why the bundle uses typed columns |
| Radar chips, 130 x 64 px WebP q70, real 64 px GRD windows (400 CNN training chips in `data/chips/*.npz`, Sentinel-1A and 1B, 10 m pixels, from 51 scenes) | 4,006 bytes mean per chip entry (key, data-URI prefix and base64), median 4,390, 90th percentile 4,594; JPEG q80 5,052 | the first estimate, 2,804 bytes, came from upscaled 98 x 48 px demo JPEG chips and was too low; chips are therefore capped by bytes |
| Ocean raster overlays at 0.05 degree (462 x 540 px), WebP q80, trial | 1 KB (AIS reach) to 47 KB (front frequency); SST 12, depth 19, shipping density 33, chlorophyll 14, distance to coast 15 | colour-mapped, transparent outside data |
| Ca Mau radar backdrop from `data/outputs/small/sigma0_vv_db_utm48n_40m_u8.tif`, WebP | 0.49 MB at 2,562 x 2,600 px q75; 0.36 MB at q60; 0.11 MB at half size q75 | the demo page carried it as a 1.56 MB base64 JPEG |

The per-part budgets that follow from these numbers are fixed in `app/CONTRACT.md` section 6.3: planned 13.05 MB (open) and 14.45 MB (research), hard cap 15.0 MB, limit 16 MB, with a drop rule per part.

## 6. Risks and how the spec handles them

1. **Blueprint 6.21.0 is too new today.** Pin 6.20.0 family; review a bump after 2026-10-14.
2. **Blueprint design tokens use CSS relative colour syntax** (the interface brief notes Chrome 122+ and Safari 18+). The product's own colour tokens are plain hex values (spec section 10), so older browsers still get correct colours.
3. **Software WebGL deprecation in Chromium.** Avoided by choosing Canvas 2D.
4. **aisstream.io terms.** The operator publishes no terms of use: `/terms`, `/tos` and `/terms-of-service` return 404, only `/privacypolicy` exists (checked 2026-10-08). The open build therefore cannot yet be called fully commercial-clean while it shows aisstream identity. Owner question in the spec (section 18).
5. **aisstream forbids direct browser connections** ("Direct browser connections are not permitted", aisstream documentation). The frontend never opens the AIS stream; only the recorder does, server side, with the key from `.env`.
6. **Bundle growth.** Each part has a byte budget and its own drop rule (contract section 6.3); the build script prints the per-part sizes and fails when a part is still over its budget after its drops or the page exceeds 15.0 MB. A live pass of about 800 contacts adds about 0.03 MB; the open contacts budget (3.8 MB against 3.27 MB measured plus about 0.23 MB of chip-contact records) leaves room for about 10 more such passes before drop rule 4 starts, and drop rule 4 frees 1.5 MB more.
7. **Unpinned transitive Python packages.** FastAPI's open-ended `starlette>=0.46.0` would pull a Starlette release younger than 14 days; pinned in section 3.2.

## 7. Sources (resolved in this session)

npm registry (registry.npmjs.org, via `npm view ... --json` and `fetch`): react, react-dom, @blueprintjs/core, icons, select, table, datetime, colors, maplibre-gl, leaflet, deck.gl, @deck.gl/core, @deck.gl/layers, vite, vite-plugin-singlefile, @vitejs/plugin-react, typescript, mgrs, @playwright/test, playwright, date-fns, flatbush, pmtiles, @types/react, @types/react-dom; plus the 106 runtime packages of the trial lockfile.

PyPI JSON API: https://pypi.org/pypi/fastapi/json , https://pypi.org/pypi/fastapi/0.141.1/json , https://pypi.org/pypi/uvicorn/json , https://pypi.org/pypi/uvicorn/0.53.0/json , https://pypi.org/pypi/starlette/json , https://pypi.org/pypi/flask/json , https://pypi.org/pypi/orjson/json , https://pypi.org/pypi/annotated-doc/0.0.5/json (all HTTP 200).

Documentation and licences:
- https://docs.npmjs.com/cli/v10/using-npm/config : the `--before` text quoted in section 2.
- https://unpkg.com/vite-plugin-singlefile@2.3.3/README.md : inlining statement and the SVG caveat.
- https://leafletjs.com/reference.html : `preferCanvas` option; `CRS.EPSG3857` is the default `crs`.
- https://raw.githubusercontent.com/Leaflet/Leaflet/v1.9.4/LICENSE : BSD 2-clause text.
- https://maplibre.org/maplibre-gl-js/docs/ : `setWorkerUrl()` and the CSP directives page exist; "uses WebGL to render interactive maps".
- https://unpkg.com/mgrs@2.2.0/README.md : `forward` takes `[lon,lat]` and returns an MGRS string; `inverse` and `toPoint` exist.
- https://raw.githubusercontent.com/palantir/blueprint/develop/LICENSE : Apache License 2.0. https://raw.githubusercontent.com/palantir/blueprint/develop/README.md : package list.
- Blueprint 6.20.0 package files from the registry tarball (`lib/css/blueprint.css`, `lib/esm/components/drawer/drawer.js`, `@blueprintjs/colors` 5.1.16 `lib/esm/colors.js`): navbar 50 px high with 0 16px padding, dark navbar #252a31, `.bp6-dark` text #f6f7f9, body font stack and 14 px, `--bp-surface-spacing` 4px, Drawer SMALL 360px, STANDARD 50%, LARGE 90%, and the palette hex values used in the spec.
- https://fastapi.tiangolo.com/tutorial/testing/ : `TestClient`.
- https://fastapi.tiangolo.com/tutorial/path-params/ : "Order matters" (route order note in section 4.4).
- https://pypi.org/pypi/fastapi/0.141.1/json : `requires_dist` includes `starlette>=0.46.0` and `annotated-doc>=0.0.2`. https://pypi.org/pypi/starlette/1.7.0/json : 1.7.0, BSD-3-Clause, uploaded 2026-09-23T07:30Z, the only Starlette release since 2026-09-01. https://pypi.org/pypi/annotated-doc/0.0.5/json : MIT, 2026-07-28T13:50Z (all read 2026-10-09).
- https://aisstream.io/documentation : "Connections per account 3 subscribed connections", "Direct browser connections are not permitted". https://aisstream.io/privacypolicy (200); https://aisstream.io/terms , /tos , /terms-of-service (404).
- https://caniuse.com/webp : WebP fully supported in Chrome from 32, desktop Safari from 16.0 (partial 14 to 15.6) and Safari on iOS from 14.
