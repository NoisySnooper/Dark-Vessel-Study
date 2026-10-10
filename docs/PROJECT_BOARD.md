# Project board: SCS Vessel Watch

Owner of this file: the project manager (PM). Updated: 2026-10-09 14:25 UTC, round 2.

> "Dark" does not mean illegal. It means only that no AIS position was matched to a radar contact. Many vessels need not carry AIS, AIS can be off for lawful reasons, and satellite and terrestrial AIS have blind spots. An AIS gap is not proof of intent.

## Goal and priorities

A dark-vessel GEOINT pipeline and maritime OSINT product over the South China Sea AOI (Natural Earth South China Sea, Gulf of Tonkin, Gulf of Thailand), Ca Mau as the detail area. Deliverable early January 2027 (Viettel application); two papers (`docs/paper1_design.md`, `docs/paper2_design.md`).

Priorities, in the owner's order:

- **P0. Dark vessel detection and ID, first and foremost.** Detection: Sentinel-1C/1D radar contacts, verified (CNN verifier, clutter rules, persistence, optical checks). ID: for each contact, who it is. Either an AIS match to an MMSI with identity (name, call sign, type, length, flag), or, when unmatched, a dark lead with all its evidence (length estimate, class, nearest AIS vessels and gaps, VIIRS light, location context). "Dark" means only "no AIS match".
- **P1. The product, "SCS Vessel Watch".** Government intelligence platform style, built on public design patterns and the open-source Blueprint toolkit (Apache 2.0). Never a vendor name, logo or product name. Core: leads triage queue, object pages (Contact, Vessel, Light, Event, Lead), map, timeline, Omnibar search, provenance on every field. Two forms: a local web app (Python backend plus frontend, `make serve`) and a single-file shareable page from the same frontend. Two builds: open (commercial-clean, no GFW) and research (adds GFW, CC BY-NC 4.0, labelled noncommercial).
- **P2. "Then add in the other layers."** Ocean context (depth, SST, fronts, chlorophyll, currents, waves, shipping presence, EEZ off by default), expected-activity model and anomalies (`docs/ocean_context_plan.md`), as context in the product and for paper 2.

## Definition of done (product complete when all hold)

| # | Criterion | State at round 2 start |
|---|---|---|
| a1 | Every radar contact of the September regional run carries an AIS status (matched with identity, unmatched dark lead, or no AIS coverage), research build via GFW | **done** (R1-T3, commit 4e1a4d3): 78,615 contacts, 9,954 matched, 68,470 unmatched, 191 no_coverage; 278 gear-buoy identities flagged low |
| a2 | At least one live Sentinel-1C/1D pass matched against recorded aisstream AIS (open build), or documented as impossible with evidence | **in progress**: pipeline runs detached (R1-T2, de5cb53). First pass (S1D 2026-10-08 22:58, 5 scenes, 3,081 contacts) is all `no_coverage`: 0 AIS positions inside the footprints. Best chance: S1D 2026-10-10 10:32 UTC, Pearl River mouth (about 15 % of the footprint heard) |
| a3 | Ranked leads queue with evidence and caveats, open and research builds | **next** (R2-T1) |
| b1 | `docs/product_design.md` spec and `app/CONTRACT.md` data contract | **done** (R1-T4, 95dfe02): spec 1.2, contract 1.2.0, stack decision |
| b2 | Local app (`make serve`) and single-file open build exist, follow the spec | **next**: backend R2-T3, frontend R2-T2; single-file bundle builder round 3 |
| b3 | Playwright checks pass with no console errors at desktop and phone widths; caveat on every view; EEZ off by default | not started (smoke checks in R2-T2; full checks round 3) |
| c | Ocean layers integrated as context in the product and documented in `docs/ocean_context.md` | static and daily layers **done** (R1-T5, e94ef6a); object context and expected-activity model **next** (R2-T6); product integration round 3 |
| d | `docs/STATUS.md`, `Makefile`, `environment.yml` updated; full test suite passes; everything committed and pushed | suite 378 pass, 0 fail (14:15 UTC); STATUS, Makefile, environment stale (R2-T5); 6 files uncommitted (R2-T6 and this board) |

## Shared decisions (PM, binding on all tasks)

### D1. Contact identity schema (round 1)

Every contact table that carries AIS identity (live pass, September regional run) uses these columns. Extra columns are allowed; these names and values are fixed so the product contract can rely on them.

| Column | Type | Meaning |
|---|---|---|
| `det_id` | str | unique contact id, stable across reruns |
| `run_id` | str | `live_<mission>_<yyyymmddThhmm>` for a live pass, `regional_2026-09` for the September run |
| `mission`, `acq_utc`, `lon`, `lat` | | as in `data/detections_regional.gpkg` |
| `length_est_m`, `confidence` | | radar length estimate and detector class (high, medium, fixed, low) |
| `cnn_score`, `cnn_vessel` | float, bool | CNN verifier (`data/models/verifier_v0.pt`); null when not scored |
| `ais_status` | str | exactly one of `matched`, `unmatched`, `no_coverage` (product extension `not_checked`, see D4) |
| `ais_source` | str | `aisstream` (open build) or `gfw` (research build) |
| `match_method` | str | e.g. `track_interp_hungarian`, `gfw_sar_cell_hour`, `gfw_presence_cell_hour` |
| `match_dist_m`, `match_dt_s` | float | distance and time offset of the match; null when unmatched |
| `match_quality` | str | `high`, `medium`, `low` with the rule in the file's `about` layer |
| `mmsi`, `imo`, `vessel_name`, `call_sign`, `flag`, `ship_type`, `length_ais_m` | | identity of the matched vessel; null when unmatched |
| `identity_source` | str | where the identity fields came from (aisstream static message, GFW vessels API) |
| `gfw_vessel_id` | str | research build only |
| `nearest_ais_mmsi`, `nearest_ais_dist_m`, `nearest_ais_dt_s`, `n_ais_10km` | | evidence for unmatched contacts |
| `ais_reach` | float | share of hours with any AIS heard in the contact's 0.25 degree cell during the recording (open build) or the GFW AIS presence equivalent (research build) |
| `research_only` | bool | true for any row that uses GFW data |
| `caveat` | str | the dark caveat (`darkvessel.config.DARK_CAVEAT`) on every row |

`no_coverage` is used only with a stated rule (for example no AIS heard in the cell during the recording window). It never means "dark".

### D2. Output locations

- Live passes (open build): `data/live/` (GeoPackage, layers `contacts_4326` and `contacts_utm49n`, plus `about`).
- September identity (research build, GFW): `data/research/` only, CC BY-NC 4.0 stamped.
- Leads: `data/leads_open.gpkg` (open), `data/research/leads_research.gpkg` (research), schema `app/CONTRACT.md` section 3.5.
- Product code: `app/` (backend `app/backend/`, frontend `app/frontend/`, single-file builder `app/build/`, checks `app/checks/`). The open build must never read `data/research/`.

### D3. Ownership and commits

- No two tasks in a round own the same file. Read-only use of any other file is fine; a task that needs a change in a file it does not own reports it instead of editing.
- Shared files are assigned to one task per round (round 2: R2-T5 owns `Makefile`, `environment.yml`, `src/darkvessel/config.py`, `.gitignore`, `README.md`, `docs/STATUS.md`).
- Tasks do not commit or push. The commit agent commits each task with the task's commit message. Never a Co-Authored-By or other trailer.

### D4. Round 2 decisions

1. `not_checked` is ratified as a product extension of the D1 `ais_status` values, for files with no AIS source (the September run in the open build). It never appears in a D1 producer file.
2. `PRODUCT_CAVEAT` (exact text in `app/CONTRACT.md` 1.1) goes into `darkvessel.config` this round (R2-T5). Producers and the backend import it with a fallback to their own identical copy until the constant exists; a test asserts equality.
3. Shipping density rasters (World Bank/IMF) are presence only. Magnitudes are not counts and are not used for ranking, lanes or model features (R1-T5 finding, `docs/ocean_context.md` 3.4).
4. Entry points fixed now so parallel tasks agree: `make serve` runs `PYTHONPATH=app/backend $(PY) -m scs_api.serve --build $(BUILD) --port $(PORT)` (BUILD open by default, PORT 8750); lead scoring is `scripts/33_leads.py --build open|research`; the expected-activity model is `scripts/34_expected_activity.py`; the frontend builds with `npm run build` (to `app/frontend/dist/`) and `npm run build:single` (to `app/frontend/dist-single/index.html`, no data).
5. Lead rule L1, weather gate: the gate (wind below 12 m/s, no deep convection) applies where weather is known. Where it is unknown (live passes have no weather join yet), the lead is kept and its factor list says "weather unknown" with 0 weather points.
6. `data/eez_marineregions.gpkg` stays committed (already in e94ef6a, 15.0 MB). The product shows it off by default, labelled as published, and never offers it as a download.
7. The open build may show aisstream-derived matches only with the label "live AIS relayed by aisstream.io; terms UNVERIFIED". Before any commercial or Viettel-facing use the owner asks the operator in writing (owner action).
8. Blueprint stays at core 6.20.0 (stack decision). A bump to 6.21.0 is reviewed after 2026-10-14, not before.

## Workstreams

| Workstream | Priority | Status | Owner (round) | Notes |
|---|---|---|---|---|
| Radar detection, regional (September 12-day run) | P0 | done | earlier | 78,615 contacts, `data/detections_regional.gpkg` |
| CNN verification of regional contacts (high, medium, fixed) | P0 | done | R1-T6 (a7f0d73) | 103,839 objects; contact acceptance 0.307; under 25 m acceptance 0.9 %: never filter small boats on `cnn_vessel` |
| CNN low class (optional, 823,285 objects) | P0 | stalled | R2-T4 | run died in the 08:21 container outage at 27 scene checkpoints; session hook does not resume it |
| Live AIS recorder and watchdog | P0 | done, running | R1-T1 (807c619) | recorder pid 165, watchdog pid 163, restarted by the session hook at 13:33 UTC; 802 MMSI since 13:33 |
| Session-start hook | P0 | done | c64d0b4 | restarts watchdog and live watcher; does not resume the CNN low run or supervise the watcher between restarts (R2-T4) |
| Live-pass pipeline | P0 | done, running; identification pending | R1-T2 (de5cb53) | watcher pid 1852, polls every 10 min. 2026-10-09 11:22 S1D pass has no AIS (container down 08:21 to 13:33) |
| Live identification on a real pass | P0 | waiting on data | R3 | S1D 2026-10-10 10:32 UTC Pearl River; scenes on the mirror about 16:30 to 17:00 UTC 10-10 |
| GFW pull and September identity (research) | P0 | done | R1-T3 (4e1a4d3) | `data/research/regional_identity.parquet`, `docs/gfw_identity.md` |
| Dark-lead scoring and leads queue | P0 | next | R2-T1 | L1 (open live, research September), L7 (VIIRS where radar does not look); L2 and L4 research from GFW events if time |
| Radar length calibration | P0 | open | R3 | radar/AIS median length ratio 1.59 on 161 registry lengths, Spearman 0.376 |
| Product spec, contract, stack | P1 | done | R1-T4 (95dfe02) | spec 1.2, contract 1.2.0 |
| Backend (FastAPI) | P1 | next | R2-T3 | `app/backend/`, `tests/app/` |
| Frontend (React, Blueprint, Leaflet) | P1 | next | R2-T2 | `app/frontend/`, develops on its own fixture bundle this round |
| Single-file bundle builder and full Playwright checks | P1 | blocked on R2-T2, R2-T3 | R3 | `app/build/`, `app/checks/` |
| Ocean static and daily layers | P2 | done | R1-T5 (e94ef6a) | shipping density presence only (D4.3) |
| Object context and expected-activity model | P2 | next | R2-T6 | `context.py`, `model.py`, scripts 25 and 34; uncommitted skeletons since round 0 |
| Docs, Makefile, environment, STATUS | all | next | R2-T5 | follow-ups from all of round 1 |

## Round log

### Round 1 (2026-10-08 22:45 UTC to 2026-10-09 14:10 UTC)

| Task | Title | Result | Commit |
|---|---|---|---|
| R1-T1 | AIS live: failing test, recorder watchdog, pass plan | accepted. Watchdog restarts the recorder (37 s measured). pandas 3 NaN strings fixed. Finding: 0 of 74 Ca Mau cells heard, 0.9 % of AOI cells heard | 807c619 |
| R1-T2 | Live-pass pipeline | accepted after one fix round. 5 scenes of S1D 10-08 22:58 processed, 3,081 contacts, all `no_coverage` (nearest placed AIS 93 km). Retries, cycle lock, no-op reruns | de5cb53 |
| R1-T3 | GFW pull and September identity | accepted after one fix round (cell-centre alignment, licence stamps, gear buoys, presence rule documented, 112 MB gpkg ignored) | 8971723, 4e1a4d3 |
| R1-T4 | Stack, spec, data contract | accepted after one fix round (research bundle by vessel reference, real column names, file status) | 29851ff, 95dfe02 |
| R1-T5 | Ocean static and daily layers | accepted after one fix round (shipping density is presence only) | e94ef6a |
| R1-T6 | CNN verification of regional contacts | accepted | a7f0d73 |

Interruptions: container outages at about 02:20 UTC and 08:21 to 13:33 UTC on 2026-10-09. The session hook restarted the watchdog, recorder and watcher at 13:33; the CNN low run stayed dead.

### Round 2 (2026-10-09 14:25 UTC)

State found: HEAD e94ef6a, pushed, in sync with origin. Untracked: this board, `scripts/25_object_context.py`, `src/darkvessel/ocean/context.py`, `src/darkvessel/ocean/model.py`, `tests/test_ocean_context.py`, `tests/test_ocean_model.py`. Full suite 378 pass in 23 s. Recorder connected (last message 14:11 UTC, 0 errors). Watcher polling, 0 new scenes since the 10-08 pass. CNN low run not running (27 low scene checkpoints, 269,366 objects). Disk 11 GB free; GFW cache 3.6 GB.

| Task | Title | Priority | Difficulty |
|---|---|---|---|
| R2-T1 | Dark-lead scoring: L1 and L7 leads with evidence, open and research builds | P0 | hard |
| R2-T2 | Frontend: SCS Vessel Watch shell, leads queue, object pages, map, Omnibar, two outputs | P1 | hard |
| R2-T3 | Backend: FastAPI app on the data contract, offline contract tests | P1 | medium |
| R2-T4 | Operations: supervise the live watcher and the CNN low run, keep live outputs committed | P0 | medium |
| R2-T5 | Shared files: config caveat, Makefile targets, environment pins, STATUS, README | P1 | easy |
| R2-T6 | Object context and expected-activity model: fix, run, evaluate, document | P2 | medium |

## Open risks

1. **Container outages** kill every process (twice on 2026-10-09). The session hook restores the watchdog, recorder and watcher only when a session starts. AIS recorded during an outage is lost; a pass inside an outage cannot be matched.
2. **The open build may never identify a contact off Vietnam.** aisstream is terrestrial; 0 of 74 Ca Mau cells heard. Open-build identification depends on passes near Hong Kong, Bangka and Manila. If the 10-10 10:32 pass also gives no match, criterion a2 falls back to "documented with evidence", and the next candidates are S1D 10-11 21:44 (Manila Bay) and S1D 10-12 22:30 (Bangka, predicted from the repeat only).
3. **aisstream terms UNVERIFIED** (no terms page). Commercial-clean status of the open build's AIS layer depends on the owner's written confirmation.
4. **Shipping density encoding UNVERIFIED** (World Bank). Presence only until answered.
5. **CPU**: 4 cores shared by the live watcher, the CNN low run, npm builds and model fitting. Heavy jobs run with `nice`; the live watcher has priority around pass arrival times (10-09 about 17:30 and 04:00 to 06:00 UTC on 10-10, then 10-10 about 16:30 UTC).
6. **Bundle budget**: research single-file page has about 0.17 MB headroom before drop rule 5 (contract 6.3).
7. **Lead weights are uncalibrated** until the owner labels contacts (`data/labels/owner_2026-10.csv`, owner action).
8. **Usage limits cut agents off.** Tasks checkpoint; long jobs run detached with pid files.

## Decisions log

- 2026-10-08 PM: contact identity schema D1 and output locations D2 fixed for round 1.
- 2026-10-08 PM: the CNN verifier is applied to every regional candidate so that every contact in the product carries a verifier score.
- 2026-10-09 PM: D4 (round 2 decisions above): `not_checked` ratified; `PRODUCT_CAVEAT` into config; shipping density presence only; fixed entry points; L1 weather gate rule; EEZ file stays committed and off by default; aisstream label; Blueprint pin held.
- 2026-10-09 PM: the single-file bundle builder waits for round 3 because it reuses the backend's loaders and serialisers. The frontend develops on its own small fixture in the contract 6.2 format.
- 2026-10-09 PM: the earlier R1-T3 follow-up that `radar_vs_gfw.parquet` had disappeared is withdrawn; the file exists (83,472 rows).
