# Status

Updated: 2026-10-10 19:30 (UTC); round 2 and round 3 results added (sections "Round 2" and "Round 3"), round 3 numbers refreshed after the R3-T7 and R3-T8 fixes. The owner reviews; the assistant does the heavy lifting.

> **"Dark" does not mean illegal.** It only means no AIS position was matched to a radar contact. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent. Where the live feed hears nothing, a contact is `no_coverage`, never dark.

## Look at this first

- **Demo page (private link, yours to share):** https://claude.ai/artifact/5VbpXaoghkKx8nwGoaEeUs (version 8). It holds:
  - a regional map of the South China Sea with all 78,615 vessel candidates of one 12-day cycle and the 90-day Sentinel-1 coverage layer;
  - a night-lights layer (VIIRS): every light of one dark night and the recurring-light sites, off by default;
  - wind and cloud-top temperature at each radar contact, and the optical and Satlas checks in the method section;
  - a radar view of one Ca Mau scene, with CNN scores;
  - a contact inspector (radar chip, DMS and MGRS position, date-time group);
  - in-browser labeling with CSV export.
- **ArcGIS Pro:** `data/detections_regional.gpkg` (vessel candidates), `data/structures_regional.gpkg` (fixed structures), `data/detections_baseline.gpkg` (Ca Mau), `data/aoi.gpkg`, rasters in `data/outputs/small/`. Every product has an EPSG:4326 layer or file plus a UTM one (49N regional, 48N Ca Mau).
- **Figures:** `docs/figures/coverage.png`, `docs/figures/look_probability.png`, `docs/figures/regional_detections.png`, `docs/figures/viirs_lights.png`, `docs/figures/optical_check.png`, `docs/figures/optical_examples.png`, `docs/figures/baseline_map.png`, `docs/figures/ml_1d_chips.png`, `docs/figures/nesz_by_satellite.png`.
- **SCS Vessel Watch, the product:** local app `make serve` (after `make app-build`); shareable single-file pages `make app-single` then `make app-check` (open and research builds, `app/build/out/`, published by the lead as private links). Round 3 pages: section "Round 3" below.
- **Run everything:** `make test`, `make regional`, `make context`, `make demo OUT=page.html`; live AIS and passes `make ais-watchdog`, `make live`; leads `make leads`; product `make serve`, `make app-single` (`Makefile`, header lists every target).

## Documents

| File | What it holds |
|---|---|
| `docs/OWNER_ACTIONS.md` | What only you can do, in order, with time estimates |
| `docs/scs_regional.md` | South China Sea: coverage, look probability, 12-day regional detection, weather, optical and Satlas checks |
| `docs/viirs_lights.md` | VIIRS night lights: method, results, limits |
| `docs/optical_check.md` | Sentinel-2 check of the radar classes |
| `docs/ml_verifier.md` | CNN verifier model card |
| `docs/gis_baseline.md` | Ca Mau scene detail and the CA-CFAR baseline |
| `docs/paper1_design.md`, `docs/paper1_manuscript.md` | Transfer letter: design, power analysis, manuscript skeleton |
| `docs/paper2_design.md` | Flagship paper: the miss budget |
| `docs/PROJECT_BOARD.md` | Project board: shared decisions D1 to D6, workstreams, round log, open risks |
| `docs/ais_live.md` | Live AIS from aisstream.io: recorder, watchdog, reach, measured coverage, recording gaps, Sentinel-1 pass windows |
| `docs/live_pass.md` | Live-pass pipeline: radar contacts of each new pass matched to live AIS, with identity and evidence; the Pearl River pass (round 3, in progress) |
| `docs/length_calibration.md` | Radar length calibration against AIS and registry hull lengths (round 3, in progress) |
| `docs/gfw_identity.md` | September identity from Global Fishing Watch, research build only (CC BY-NC 4.0) |
| `docs/ocean_context.md` | Ocean static and daily layers: depth, coast and port distance, shipping presence, EEZ as published, SST, fronts, chlorophyll, currents, waves |
| `docs/leads.md` | Dark-lead scoring and the leads queue: L1 and L7 rules, priority factors, open and research builds |
| `docs/product_design.md` | SCS Vessel Watch product specification (1.2 committed; 1.3 in progress this round) |
| `app/CONTRACT.md` | Data contract for the product (1.2.0 committed; 1.3.0 in progress this round): objects, fields, sources, API, bundle layout and budget, product caveat (1.1) |
| `app/frontend/README.md`, `app/build/README.md` | Frontend (stack, checks, bundle readings) and the single-file page builder (usage, parts, size budget, drops) |
| `docs/research/stack_decision.md` | Product stack: packages, pinned versions, measurements |
| `docs/research/interface_design_brief.md` | Research brief: government intelligence interface design and the Blueprint toolkit |
| `docs/research/mda_products_brief.md` | Research brief: maritime domain awareness products and event definitions |
| `docs/ocean_context_plan.md` | Approved plan: ocean layers (depth, SST, fronts, currents, chlorophyll, shipping lanes, EEZ) and the expected-activity model, built once access is open |
| `docs/bibliometrics.md`, `docs/journals.md` | Literature scan, gap analysis, venues |
| `docs/data_landscape.md`, `docs/data_additions.md` | Data sources with licences and access tests |

## Scope change (2026-10-02)

The AOI moved from Ca Mau to the whole South China Sea at the owner's request. AOI = Natural Earth marine areas "South China Sea", "Gulf of Tonkin" and "Gulf of Thailand", 3.58 million km2. Ca Mau stays as the scene-detail sub-area.

## Round 1, 2026-10-08 and 2026-10-09

Run by the project board (`docs/PROJECT_BOARD.md`, round log). Every number below is taken from the named file. Container outages stopped every process: 2026-10-08 15:19 to 22:38, 2026-10-09 02:19 to 04:59, four short restarts between 07:42 and 08:17, 08:21 to 13:33, and 16:17 on 2026-10-09 to 00:19 on 2026-10-10 (`docs/ais_live.md` section 4.5). Each time the session hook (`scripts/session_start.sh`) of the next session restarted the watchdog, recorder and live watcher; since round 2 it also resumes the regional CNN run.

### Live AIS recorder and watchdog (`docs/ais_live.md`; recorder and watchdog in commit 807c619)
- `scripts/26_ais_record.py` records aisstream.io positions and static data over the AOI into `data/cache/ais/aisstream/` (git-ignored). `scripts/29_ais_watchdog.py` restarts it when it dies or goes silent (restart 37 s after a stop in the live test). Supervision of the live-pass watcher and the regional CNN run was added in round 2 (task R2-T4, not yet committed). `scripts/session_start.sh` starts the watchdog at every session start; nothing inside the container survives a container restart.
- Coverage, from `data/ais_live_summary.json` (2026-10-08 14:31 to 23:37 UTC, 4 recorded hours): 15,780 positions from 1,277 MMSI; static data for 817 MMSI. The feed heard 42 of the 4,778 AOI cells (0.25 degree), **0.9 %**, and **0 of the 74 cells of the Ca Mau detail area**. Nothing was heard in the Natural Earth Gulf of Thailand and Gulf of Tonkin parts of the AOI. Dense: Pearl River mouth and Hong Kong, the Singapore Strait (just outside the AOI) and the Bangka Strait. The free feed is terrestrial; off Vietnam a radar contact can only be `no_coverage` in the open build.
- Recording gaps over 60 s, 2026-10-08 14:31:47 to 2026-10-10 00:30 UTC (`docs/ais_live.md` section 4.5, as of 2026-10-10 00:49 UTC): eight, together 23 h 30 min of 33 h 58 min, so the feed was recorded for about 31 % of that time. The longest: 8 h 01 min (2026-10-09 16:17 to 2026-10-10 00:19), 7 h 19 min (2026-10-08 15:19 to 22:38, before the watchdog existed) and 5 h 12 min (2026-10-09 08:21 to 13:33). Three of the four Sentinel-1 passes over the AOI in that time fell in a gap and have no AIS: S1D 2026-10-09 11:22, S1D 22:04 and S1C 22:50. Only the S1D pass of 2026-10-08 22:58 has AIS. Only a host outside the container can close such gaps.
- Sentinel-1 pass plan from ESA's acquisition plan: `data/s1_next_passes.json`; reach rasters in `data/outputs/small/`.

### Live-pass pipeline (`docs/live_pass.md`, commit de5cb53)
- `scripts/30_live_pass.py` watches the AWS mirror for new Sentinel-1C/1D scenes, detects and CNN-verifies contacts, matches them to the recorded AIS and attaches identity. Outputs: `data/live/live_S1D_20261008T2258.gpkg`, `data/live/live_contacts.gpkg`, `data/live/live_summary.json`.
- First pass, S1D 2026-10-08 (scenes 23:00:43 to 23:02:48 UTC, planned 22:58), east side of the Gulf of Thailand: **5 scenes, 143,755 km2 tested, 3,081 contacts** (983 high, 1,507 medium, 591 fixed; CNN accepts 1,063). **All 3,081 are `no_coverage`**; 0 matched, 0 unmatched, 0 dark leads. The feed was up (995 to 1,012 MMSI per scene window somewhere in the AOI) but heard nothing inside the five footprints or within 0.3 degree of them. The nearest placed AIS vessel was **93 km** from a contact (93 to 412 km, median 226 km).
- The best open-build chance of a real identification is S1D 2026-10-10 10:32 UTC over the Pearl River mouth (808 MMSI heard in its box so far).

### GFW September identity, research build only (`docs/gfw_identity.md`, commits 8971723, 4e1a4d3)
- Global Fishing Watch data, CC BY-NC 4.0, noncommercial; everything under `data/research/`. Scripts `scripts/27_gfw_pull.py` and `scripts/31_gfw_identity.py`. From `data/research/regional_identity_summary.json` (built 2026-10-09 13:51 UTC):
- **78,615 contacts**: **matched 9,954** (12.7 %), **unmatched 68,470**, **no_coverage 191**.
  - GFW SAR cell-hour match, 5,478: high 1,220, medium 1,677, low 2,581.
  - GFW presence cell-hour match, 4,476: medium 59, low 4,417.
  - Identity of the matched: 9,572 vessel, **278 GEAR buoy identities** (always quality low; not the vessel's own identity), 104 unknown type.
- Matched share by length: 5.2 % under 25 m, 32.1 % at 100 m and longer. Radar against AIS length on 161 registry lengths: median ratio 1.59, Spearman 0.376.
- `data/research/regional_identity.parquet` is committed; `data/research/regional_identity.gpkg` (112 MB) is git-ignored and rebuilt by `scripts/31_gfw_identity.py --steps outputs --offline`. Model comparison: `data/research/radar_vs_gfw.*`.

### CNN verification of the regional run (`docs/ml_verifier.md`, commit a7f0d73)
- `scripts/32_cnn_regional.py`: the CNN verifier on every September regional object of the high, medium and fixed classes. From `data/ml/regional_cnn.json`: **103,839 objects scored** (68,213 new, 35,626 reused from the shared-cell run). **Contact (high plus medium) acceptance 0.307** [0.304, 0.310]; high 0.650, medium 0.104, fixed 0.242; contacts under 25 m 0.009. Never filter small boats on `cnn_vessel`. Outputs: `data/ml/regional_cnn.parquet`, `data/detections_regional_verified.gpkg`, `docs/figures/regional_cnn.png`.
- Low class (823,285 objects, optional): in progress, not in the outputs. As of 2026-10-10 01:56 UTC `data/cache/regional_cnn/low/` holds 92 of 119 scene checkpoints and the run is active (`data/cache/regional_cnn/run.log`). `data/ml/regional_cnn.json` (written 2026-10-10 00:44:59 UTC) records 58 of 119 scenes; it is rewritten only when a run builds. The run was killed by the outages of 07:42 and 16:18 on 2026-10-09, resumed by hand at 15:10, and resumed by the watchdog at 00:20 on 2026-10-10 (`docs/ais_live.md` section 4.5). Rebuild with `make cnn-regional-build` when it completes.

### Ocean static and daily layers (`docs/ocean_context.md`, commit e94ef6a)
- Static (`scripts/22_static_layers.py`): GEBCO_2026 depth and contours, distance to coast and to a major port, ports, World Bank/IMF shipping density, Marine Regions EEZ in its own file (`data/eez_marineregions.gpkg`, off by default, labelled as published, no position taken). Cell table `data/ocean_static_cells.parquet`, `data/ocean_static_summary.json`.
- **Shipping density is presence only** (board D4.3): many published values cannot be counts, so magnitudes are not used for ranking, lanes or model features. Share of AOI sea with any value above 0: all 66.9 %, fishing 0.75 %.
- Daily (`scripts/23_daily_ocean.py`, 2026-09-05 to 2026-10-01): SST and fronts, chlorophyll, currents, sea level and mixed layer, waves and wind. `data/ocean_daily_cells.parquet`, `data/ocean_radar_pass_cells.parquet`, `data/ocean_daily_summary.json`, `data/ocean_fronts.gpkg`, COGs in `data/outputs/small/`, figures `docs/figures/ocean_static.png` and `docs/figures/ocean_showcase_fronts.png`.

### Product: SCS Vessel Watch (commits 29851ff, 95dfe02)
- Product spec version 1.2 (`docs/product_design.md`), data contract 1.2.0 (`app/CONTRACT.md`, one product caveat in section 1.1, now `darkvessel.config.PRODUCT_CAVEAT`) and stack decision (`docs/research/stack_decision.md`: FastAPI backend, React with Blueprint frontend, pinned versions).
- Round 2 (started 2026-10-09 14:25 UTC) builds the backend (`app/backend/`), frontend (`app/frontend/`), leads queue (`scripts/33_leads.py`, `docs/leads.md`) and expected-activity model (`scripts/34_expected_activity.py`); see the board. Entry points: `make serve`, `make app-build`, `make leads`.

## Round 2, 2026-10-09 14:25 to 2026-10-10 02:36 UTC

Run by the project board (`docs/PROJECT_BOARD.md`, round log, tasks R2-T1 to R2-T6). Every number is taken from the named file. A container outage from 2026-10-09 16:17 to 2026-10-10 00:19 UTC stopped every process; both Sentinel-1 passes that landed in it (S1D 2026-10-09 11:22 and 22:05) have no AIS.

### Leads queue (`docs/leads.md`, `scripts/33_leads.py`; commits e8a7610, 555c207)
- Two lead types. L1: an `unmatched` radar contact where AIS was heard nearby (high or medium class, CNN score at least 0.5, both channels, not in a clutter zone or near a structure, weather gated per part). L7: lit activity (VIIRS) where Sentinel-1 does not look. Priority 0 to 100 from five factors (evidence quality 30, corroboration 25, AIS reach 20, persistence 15, area weight 10), model `lead_priority_v1_20261010`, **uncalibrated** until the owner labels contacts. A lead is a prompt for review, never a finding.
- Research build (`data/research/leads_research_summary.json`, 2026-10-10 14:49 UTC): **14,261 leads**, 12,124 L1 from the September GFW identity and 2,137 L7; bands 7 high, 8,830 medium, 5,424 low. `data/research/leads_research.parquet` is committed; the 85 MB GeoPackage is rebuilt locally and git-ignored.
- Open build at the end of round 2: 2,137 L7 and 0 L1 (built 13:28 UTC on 2026-10-10, before any live contact was `unmatched`). The round 3 rebuild is below.

### Product backend and frontend (commits f760cb1, d0f8363, 0bbd05c)
- Backend (`app/backend/`, FastAPI, contract 1.2.0): file catalog with the open-build guard (the open build never opens `data/research/`), loaders for contacts, vessels, lights, events, leads, passes, cells, geo and rasters, the product caveat on every record, append-only lead decisions, exports, offline contract tests. Research queue answered in 0.69 s (median) after the snapshot-store fix.
- Frontend (`app/frontend/`, React, Blueprint 6.20.0, Leaflet with no tiles): leads queue with decisions, map (EEZ off by default), Contact, Vessel and Lead pages with provenance, Omnibar, dark and light themes, the local build and a single-file shell of 1,976,005 bytes with 0 console errors in the smoke check.

### Object context and expected activity (`docs/ocean_context.md`; commit 4ba8e5f)
- Ocean context at every object (`data/ocean_context_objects.parquet`, `data/ocean_context_objects.json`): **360,013 rows** (191,622 VIIRS lights, 162,386 regional radar objects, 6,005 Ca Mau objects): depth, distance to coast and port, shipping presence (presence only, board D4.3), SST and gradient, distance to a front, chlorophyll, currents, mixed layer, waves.
- Expected-activity model (`data/expected_activity.json`, model `expected_activity_v1_5bae587d`): count models of lit vessel candidates per cell-night and radar candidates per cell-scene, cross-validated by leaving out one week at a time. The full model beats the per-cell climatology out of sample (pooled deviance skill D2 0.217 for VIIRS, 0.381 for radar), so the anomaly gate passed. The counts are overdispersed (Pearson dispersion 3.82 and 19.24), so the Poisson false-discovery control does not hold; the set to review is the cells also significant under a negative binomial tail: **35 robust anomaly cells** (22 VIIRS, 13 radar, all above expectation). An anomaly is a lead for review, not evidence.

### CNN verification, low class complete (`data/ml/regional_cnn.json`, outputs in commit f3acd89)
- All 119 scenes of the low class are scored: **823,285 low objects**, 5,178 accepted (0.006). With round 1, every regional object of every class has a CNN score (103,839 high, medium and fixed; 823,285 low). The transfer caveat stands: the model was trained on Sentinel-1A/1B labels and is not yet scored on 1C/1D truth.

### Operations (commits 105ccd2, f3acd89) and shared files (commit 0eb5ef0)
- The watchdog also supervises the live-pass watcher and resumes the CNN run; the session hook restarts them after a container restart. `PRODUCT_CAVEAT` is in `darkvessel.config`; Makefile targets for every round 2 step.

## Round 3, 2026-10-10

Run by the project board (tasks R3-T7 to R3-T12, second dispatch after the first was cut off). Work of R3-T7 to R3-T11 is **in progress this round**: its numbers below are the files as they stand at 19:30 UTC (live outputs of 17:51, leads of 17:33), not final results, until the board accepts each task.

### Live identification on a real pass: Pearl River mouth (`data/live/live_summary.json`, generated 17:51 UTC; R3-T7, in progress)
- Sentinel-1D, 2026-10-10 10:32:47 to 10:33:41 UTC, 2 scenes, 33,788 km2 tested, while the aisstream recorder was running (426 MMSI heard inside the footprint, the most per scene).
- **4,712 contacts** (1,335 high, 2,048 medium, 1,329 fixed; the CNN accepts 1,658). AIS status: **33 matched**, **1,947 unmatched**, **2,732 no_coverage**. Of the 33 matches, 32 carry a vessel name, 24 a call sign, 31 a ship type, 31 an AIS length and 16 an IMO number (`data/live/live_S1D_20261010T1032.gpkg`); every one has the flag of its MMSI's Maritime Identification Digits. Match quality 18 high, 8 medium, 7 low; median match distance 74.7 m. 62 unmatched contacts are ambiguous (too close to two AIS vessels to call) and never form a lead; 1,364 unmatched high or medium contacts are dark leads. 278 AIS vessels have no matched contact (49 on tested sea).
- Hand check (analyst judgment, not truth): of the 33 matches 18 confirmed, 11 plausible, 4 doubtful; 34 unmatched (28 confirmed, 4 plausible, 2 doubtful) and 10 no_coverage contacts were also checked. Board D6.2: only a high or medium match that is not doubtful counts as an identification, which is **25 of the 33** today (high: 13 confirmed, 5 plausible; medium: 5 confirmed, 2 plausible); a low match keeps its row with "low-quality pairing, identity not confirmed", and a doubtful match (for example YUN DA YOU 11, `S1D_20261010T103247_08819`, probably the tanker's wake) is not an identification.
- Radar length disagrees with AIS length for several large ships (radar 1.5 to 4.5 times the AIS length); the length calibration is in progress (R3-T9, `docs/length_calibration.md`). `length_est_m` stays the raw radar estimate.
- All three live passes (`totals`): 9 scenes, 8,573 contacts, 33 matched, 1,947 unmatched, 6,593 no_coverage, AIS recorded for 32 hours. The other two passes heard nothing in their footprints: S1D 2026-10-08 23:00 (Gulf of Thailand, 3,081 contacts, all no_coverage) and S1C 2026-10-10 11:19 (780 contacts, all no_coverage; 876 MMSI heard elsewhere in the AOI during the pass).

### Open build coverage limit
- The open build's AIS is the free aisstream.io feed of shore receivers. In its first summarised recording (`data/ais_live_summary.json`, 2026-10-08 14:31 to 23:37 UTC) it heard 42 of the 4,778 AOI cells (0.9 %), **0 of the 74 Ca Mau cells**, and nothing in the Gulf of Thailand and Gulf of Tonkin parts of the AOI; the Pearl River mouth and Hong Kong box was heard in 8 of 17 cells. Only a pass over such a box can be identified in the open build; off Vietnam every open-build contact is `no_coverage`, which never means dark. The summary has not been rebuilt from the 32 recorded hours since (its stale vessel snapshot is contract open item 8.17).

### Leads rebuild with the first open L1 leads (R3-T8, in progress)
- `data/leads_open_summary.json` (17:33 UTC): **2,531 open leads**, 394 L1 (all from the Pearl River pass, North shelf box; 271 medium, 123 low) and 2,137 L7. It was built from `data/live/live_contacts.gpkg` of 15:23 UTC, before the R3-T7 rerun of 17:51 (the AIS status counts are the same; hand-check grades changed). Research leads (`data/research/leads_research_summary.json`, 17:33 UTC): 14,261 (12,124 L1, 2,137 L7). The open leads file keeps `leads_4326` in `data/leads_open.gpkg` and moves `lead_evidence` and `leads_utm49n` to `data/leads_open_detail.gpkg` (board D6.3), both under 20 MB.

### Backend 1.3.0 and frontend round 3 (R3-T10, R3-T11, in progress)
- Contract 1.3.0 (`app/CONTRACT.md`): object context of contacts and lights, expected activity of cells, live identification evidence (hand-check note, azimuth shift, ambiguity, aisstream label) with field-level provenance, AIS-only vessels per pass, the bundle layout made normative (section 6.4).
- Frontend: Pass page (identification first), identification for every AIS status with the D6.2 label, ocean Context sections, context overlays off by default; the single-file shell is 1,437,277 bytes in the frozen copy (from 1,976,005). The R3-T11 fix (ship type code 0, the D6.2 note, the stale-lead guard) is in progress.

### Single-file pages (R3-T12)
- The builder (`app/build/`, `tests/app_build/`) was reviewed and is committable: `.gitignore` now ignores only the root `/build/`, and `app/build/out/` (the pages), `data/research/leads_research.gpkg` and the frontend check screenshots by name. 48 offline tests in `tests/app_build/`. Fixes: drop rule 7 never drops a live pass with a matched contact; every matched, hand-checked and ambiguous live contact carries its full identification record; records keep only the provenance that differs; the passes part fits its budget and holds the AIS-only vessels that fit; the record cache depends on every input file; the spot check takes the matched live contacts first; the bundle check also tests the caveat banners, the EEZ default, GFW names and credential prefixes in the whole page. Fix round: the ocean context of every contact and light with a context row is in the pages (README reading 13), with the fields that fit under the cap; every field left out of a page is listed in `meta.dropped`. Details: `app/build/README.md`.
- Pages built 2026-10-10 19:28 and 19:31 UTC (builder 1.2.0) from a frozen copy of the frontend shell (16:16 UTC, 1,437,277 bytes, board D6.6), contract 6.3 budgets plus the object context in the room left under the cap: **open 14,825,654 bytes**, **research 14,899,904 bytes** (cap 15,000,000). Outputs in `app/build/out/`, git-ignored; the lead publishes them as private links. The pages built at 17:06 and 17:09 UTC are replaced and must not be published.
- Open page: the Pearl River pass (4,712 contacts: 33 matched, 1,947 unmatched, 2,732 no_coverage) and the S1C 2026-10-10 11:19 pass (780, no_coverage); the S1D 2026-10-08 pass (3,081 contacts, all no_coverage) left the page by drop rule 7 to meet the 4.4 MB contacts budget (listed in `meta.dropped`, in the local app). 2,531 leads (394 L1, 2,137 L7), 1,416 vessels, 673 radar chips (every L1 lead primary, every matched live contact, 246 CNN-accepted unmatched live contacts), 28 of the pass's 278 AIS-only vessels (tested sea first). Ocean context for 65,571 contacts and 48,692 lights: 7 of 16 fields (depth, distance to the coast and to a port, shipping presence all and fishing, SST, waves) and the cell.
- Research page: all three live passes, 9,987 matched contacts (9,954 September GFW, 33 live), 9,728 of 14,261 leads (the lowest-priority 4,533 left out), 12,611 vessels, 353 chips. Ocean context for 62,256 contacts and 48,692 lights: 5 of 16 fields (depth, distance to the coast, shipping presence all and fishing, waves).
- Every one of the 33 matched live contacts is in both pages with its full identification record; MMSI, name and hand-check grade equal the live file for 33 of 33 (18 confirmed, 11 plausible, 4 doubtful); 32 show a vessel name (one matched vessel sent no static message).
- Checks: `app/build/check_bundle.mjs` passed on both pages: 48 route visits per page at 1280 and 390 px in dark and light with 0 console errors, 0 failed requests and no request outside the page; the caveat banners on every view; EEZ off at load; no GFW name or field anywhere in the open page; no credential prefix in either page; 50 contacts per page decoded by the frontend's own adapter equal to the API records (open 1,550 of 1,550 values, research 1,600 of 1,600, plus the live identification fields, with 11 and 10 matched Pearl River contacts); the object context of 50 contacts and 20 lights per page equal to the API (open 385 field values, research 280, 0 mismatches), and the Context table shown where the API has context. The frontend smoke check of the R3-T11 fix (18:20 UTC) fails 41 checks per page, all on shell changes of that fix that the frozen shell lacks; scratch pages from the same data and the R3-T11 shell of 18:39 UTC pass it on the open page and fail 1 check on the research page (a stale-lead variant, R3-T11's).
- Known gaps in the pages (in `app/build/README.md`, for the PM): the open contacts budget leaves out every detection extension column, and the frozen shell shows those fields as "not computed" or "unknown" on September contacts (open item 8.19 and a frontend change); the leads predate the 17:51 live rerun; publish after the leads rebuild and, if the lead wants the R3-T11 fix in the pages, after that task is accepted.
- Shared files: `make leads` builds both lead files; targets `app-single`, `app-check`, `live-review`, `live-figures`, `length-cal`, `leads-open`, `leads-research`; pytest also collects `app/frontend/fixtures` (9 tests); `environment.yml` names `pillow` (WebP) and `httpx` (test client, conda-forge), both already in the environment. Full suite on the working tree (other tasks' uncommitted changes included) at 19:20 UTC: 630 passed, 1 skipped in 145 s.

### Caveat
"Dark" means only that no AIS position was matched to a radar contact. It does not mean illegal: many vessels need not carry AIS, AIS can be off for lawful reasons, and satellite and shore AIS both have blind spots. An AIS gap is not proof of intent. Where the live feed heard nothing, a contact is `no_coverage`, never dark. Every number above that counts unmatched contacts or leads counts prompts for review, not wrongdoing.

## Done

### Workstream 1: bibliometrics (`docs/bibliometrics.md`, `data/biblio/`)
- 4,328-work corpus, 2015 to 2026, built from the OpenAlex Parquet snapshot of 2026-09-23 on S3 (the API is blocked). Output grew 3.8 times from 2015 to 2025. Dark-vessel work: 143 papers. SAR and AIS fusion: 213. Small vessels: 813.
- Southeast Asia: 116 on-topic works. Vietnam: 15. No on-topic work detects vessels at sea in SAR and names Vietnam. No work names the Gulf of Tonkin or the Paracels.
- Anchor papers summarised: Paolo 2024, Park 2020, xView3-SAR, Elvidge 2015.
- Gap analysis per paper. Paper 1: no peer-reviewed vessel detection on Sentinel-1C/1D, and the within-mission 1A to 1C/1D pair is unmeasured. Paper 2: no Sentinel-1 recall-by-length curve for Southeast Asia, nothing measured below 15 to 20 m, and no work adds coverage, detection by length and fleet share into one miss budget. Closest prior work and framing are in the section.

### Workstream 2: journals (`docs/journals.md`, `data/journals.csv`)
- 28 venues screened. Letter: IEEE GRSL, then IGARSS 2027, then Remote Sensing Letters. Flagship: Remote Sensing of Environment, then Fish and Fisheries, then ICES Journal of Marine Science.
- Verified: OpenAlex fields (publisher, ISSN, OA flags, APC) and Retraction Watch counts.
- Search snippets only: SJR, quartiles, review times and page limits.
- Scopus discontinued list and hijacked-journal check: NOT CHECKED (hosts blocked).

### Data landscape (`docs/data_landscape.md`, `data/data_sources.csv`)
- Usable now and commercially clean: Sentinel-1 on the AWS mirror (median 3.5 h from sensing to upload), CDSE as fallback, ESA WorldCover, Skylight labels (Apache-2.0).
- Noncommercial, so kept out of any Viettel-facing path: GFW and SARDet-100K (licences read), xView3-SAR (reported, UNVERIFIED).
- No free AIS with verified coverage of Vietnamese waters. Sentinel-1C/1D onboard AIS is restricted (UNVERIFIED).
- In Vietnam the compliance stream for fishing vessels of 15 m and longer is VMS, which is not open.

### Workstream 3: GIS (`docs/scs_regional.md`, `docs/gis_baseline.md`)
- Repo scaffold, `environment.yml` verified by a real install, secret-safe `.gitignore`, `.env.example`, walkthrough notebook, offline `pytest` suite (185 tests pass in the conda environment).
- Coverage, 90 days: 1,042 Sentinel-1C/1D IW products in 280 passes. 55 % of the AOI imaged at least once. **1.61 million km2 (45 %), the whole central sea including the Spratly area, never imaged.** On an average day Sentinel-1 images 6.3 % of the AOI.
- Ca Mau detail scene (Sentinel-1D, 2026-09-29): 720 vessel candidates (285 in both channels), 349 fixed structures, 4,936 low-confidence objects over about 17,700 km2 of open sea.
- Regional run over one 12-day repeat cycle, 20 September to 1 October 2026: 119 Sentinel-1C/1D scenes, 2.38 million km2 of sea tested. **78,615 vessel candidates** (29,228 in both channels) and 25,224 fixed structures. Density map and rasters are done (`docs/figures/regional_detections.png`).
- Quicklooks of the densest cells showed four false sources:
  - rain cells (off Brunei, Gulf of Thailand);
  - aquaculture rafts (Zhanjiang Bay);
  - an offshore wind farm (Shanwei).

  Two rules now move candidates to the low class: 5 or more weak returns within 1 km (52,820), and within 250 m of a fixed structure (5,727). The first costs about 2 % of labelled vessels on 1A/1B scenes; the cost of the second is unmeasured. Both keep their rows in the full file.
- A sea-mask bug that dropped nearshore sea in 9 coastal scenes was found and fixed. 22,400 km2 was recovered, including the Gulf of Thailand off Kien Giang and the northern Gulf of Tonkin.
- First look for paper 1: on 373 cells both satellites imaged, Sentinel-1C and 1D agree. Density is 38.8 against 41.1 per 1,000 km2 per look. The CNN accepts 60 % against 63 % of both-channel candidates.
- AIS matching module (interface, synthetic generator, tests). No real AIS used.

### ML stage (`docs/ml_verifier.md`, `data/detections_ml.gpkg`)
- CNN verifier: 294k parameters, VV and VH chips of 640 m. Trained from scratch on AI2 Skylight expert labels (Apache-2.0): 454 Sentinel-1A/1B scenes (334 in Southeast Asia) and 69,274 CFAR candidates.
- Held-out test (91 scenes, 1,422 labels, 50 m rule):

  | | Precision | Recall |
  |---|---|---|
  | CFAR alone | 0.09 | 0.80 |
  | CFAR + CNN | **0.77** [0.75, 0.79] | 0.75 [0.73, 0.77] |
  | CFAR + CNN, loose rule (150 m or 0.75 x length) | 0.82 | 0.92 |

- Recall by AIS length: the test set has no label under 15 m and 2 at 15 to 25 m. AI2 labels cannot measure small-boat recall; that needs other truth.
- Ca Mau Sentinel-1D scene:
  - The CNN keeps 149 of 720 baseline candidates (27 % of both-channel ones) and nothing under 25 m.
  - Many rejects are lines of point targets with cross-shaped sidelobes (stake nets or other fixed gear; `docs/figures/ml_1d_chips.png`).
  - Chip backgrounds are 3.6 dB darker than the training clutter. That is either a sensor or sea-state shift, or a model that knows only large ships.
  - There is no 1D ground truth yet; next task 2 supplies it.
- Applied regionally only on the shared 1C/1D cells (above), as a consistency check; the transfer has to be scored first.

### Extra data (`docs/data_additions.md`, `data/data_additions.csv`)
- 27 further sources researched, with access tested from this environment. Night lights, weather and sea state, optical, other SAR and Vietnamese sources each come with licence, latency and the owner action needed.
- Added now, with no owner action:
  - VIIRS Day/Night Band lights at sea, every night, over the whole AOI including the central sea that Sentinel-1 never imaged (`scripts/15_viirs_lights.py`, run in progress);
  - GFS 10 m wind and Himawari-9 cloud tops at each radar object (`scripts/16_weather_context.py`). 39 % of the objects the clutter rule removes sit under deep convection, against 23 % of the both-channel candidates it keeps.

### Added 2026-10-03: more sensors, same pipeline
- **When does Sentinel-1 look?** Chance of a look within 1, 7 and 30 days for every 0.05 degree cell (`scripts/17_look_probability.py`): AOI mean 6.3 %, 34 % and 50 %; 42 % of the AOI is looked at within every 30-day window and 45 % never (`docs/scs_regional.md`, `docs/figures/look_probability.png`).
- **Sentinel-2 optical check** (`scripts/19_optical_check.py`, `docs/optical_check.md`): on a random sample with a clear Sentinel-2 view, 37 % of fixed structures show a bright object at the spot (54 % near Satlas platforms), against 2.2 % of both-channel candidates, 1.5 % of one-channel candidates and 1 % of open sea. The persistence test finds things that stay put; the vessel classes are not structures in disguise.
- **Satlas check** (`scripts/20_satlas_check.py`): the fixed class has a structure within 250 m of 72 % of the Satlas platform and turbine points in the tested sea (88 % of turbines, 62 % of platforms).
- **VIIRS night lights** over the whole AOI, every night (`scripts/15_viirs_lights.py`, `docs/viirs_lights.md`): 27 nights, 191,622 lights at sea (164,333 lit vessel candidates; 2,129 recurring-light sites). 34 % of clear-sky lit candidates lie where Sentinel-1 never looked in 90 days. The Gulf of Tonkin lit fleet tracks the wind (Spearman -0.76) and all but vanished in a mid-September wind event. On two same-night pairs, 91 to 96 % of the cells with a light also hold a radar candidate.
- **Run harness:** `Makefile` with the pipeline in dependency order.
- **Paper 2 design** (`docs/paper2_design.md`): the miss budget (coverage, detection by length, fleet composition, lit activity), what is measured and what each term still needs.
- **Ocean context plan, approved** (`docs/ocean_context_plan.md`). Access is now open and the static and daily layers are built (round 1 above, `docs/ocean_context.md`). Your decisions of 2026-10-03:
  - build depth, SST and fronts, currents, waves, chlorophyll, shipping lanes and the expected-activity model in one go once access is open;
  - EEZ as an optional layer, off by default;
  - Global Fishing Watch for the papers only;
  - a few key layers on the demo page.

  Reachability as first tested (before 2026-10-08): depth (ETOPO1), 0.25 degree SST, currents and sea level were open; chlorophyll, 1 km SST, shipping density and EEZ lines were blocked. Superseded: all are reachable now.

### Paper 1 groundwork (`docs/paper1_design.md`)
- Noise floor from the products' own annotation: Sentinel-1C and 1D are 1.4 dB (VV) and 1.8 dB (VH) below Sentinel-1A (2022) at every incidence angle, and 1C and 1D match each other (`docs/figures/nesz_by_satellite.png`). This explains about half of the darker 1D chip backgrounds.
- Labeling design: a fixed random sample per class, plus every CNN-accepted Ca Mau contact (149, enough to detect a 10-point precision drop). The demo page queues them, and `scripts/12_score_labels.py` turns your CSV into per-class shares and CNN precision and recall with intervals.

## Blocked

| Blocker | Effect | Fix (owner action) |
|---|---|---|
| Network (rechecked 2026-10-10 01:58 UTC with curl through the proxy) | Open: doi.org, globalfishingwatch.org, esa.int, huggingface.co, zenodo.org, planetarycomputer.microsoft.com, Copernicus Data Space STAC, elsevier.com, retractionwatch.com, marineregions.org (HTTP 200). api.openalex.org answers 429 (reachable, rate-limited without a key). scimagojr.com answers 403, so SJR and quartiles stay UNVERIFIED. Facts marked UNVERIFIED in earlier docs were not all rechecked since the network opened. | Recheck the UNVERIFIED items host by host; SJR needs another route |
| Keys | Present in the git-ignored `.env`: `GFW_API_TOKEN` (GFW used 2026-10-08 to 2026-10-09, `data/research/regional_identity_summary.json`) and `AISSTREAM_API_KEY`. Absent: OpenAlex API (key-only since 2026) and Copernicus S3 | Add `OPENALEX_API_KEY`, `CDSE_S3_ACCESS_KEY` and `CDSE_S3_SECRET_KEY` to `.env` or as environment secrets if needed. Never in chat or git. |
| AIS coverage off Vietnam (updated 2026-10-09) | The open build's live AIS (aisstream.io, terrestrial) hears 0 of 74 Ca Mau cells, so contacts there are `no_coverage`; GFW (connected 2026-10-08) identifies September contacts for the research build only. aisstream terms are UNVERIFIED | A commercial satellite AIS feed for anything Viettel-facing; written confirmation of aisstream terms |
| No 1C/1D ground truth | Transfer to 1C/1D cannot be scored | Hand labels: next task 2 |
| Compute | Regional run covers one 12-day cycle of the 90 days, on a shared 4-core machine | `scripts/09_run_regional.py --days 90` on a bigger machine (checkpointed per scene) |

Worked around: OpenAlex via its public S3 snapshot; Sentinel-1 via the AWS Open Data mirror; land mask via ESA WorldCover on S3; AOI and coastline via Natural Earth on GitHub; labels via the public Skylight repo.

## Defaults applied (say the word to change any)

- Map naming: "South China Sea" as written in your request. For a Vietnamese audience you may prefer "East Sea (Bien Dong)"; it is a one-line change in `src/darkvessel/config.py` and the figure titles.
- No maritime boundaries or claim lines are drawn now. Approved on 2026-10-03: EEZ lines from Marine Regions as an optional layer, off by default, labelled as the source publishes them. Done: `data/eez_marineregions.gpkg` (commit e94ef6a, `docs/ocean_context.md`).
- Display: files in the repo, short chat summaries, and one private demo page.
- Commits: one per workstream plus labeled work-in-progress commits, no Co-Authored-By trailer. Commit author is the container default; give a name and email to switch.
- Training data: AI2 Skylight labels (Apache-2.0) only; xView3-SAR is reported noncommercial (UNVERIFIED), so it is not used.

## Next 3 tasks (smallest first, each fits a 5 h week)

The full list of owner actions, with steps, is in `docs/OWNER_ACTIONS.md`.

1. **Superseded (2026-10-10).** The environment is unblocked (network open except scimagojr.com; GFW and aisstream keys present), and current work is planned in the round log of `docs/PROJECT_BOARD.md`. Still open from the old item: SJR and quartiles, the Scopus and hijacked-journal screens, DOI resolution of older citations, and a CDSE STAC cross-check of the scene list.
2. **Label the Ca Mau queue on the demo page (about 3 h).** Open the page, switch to "Scene detail: Ca Mau" and press N: it steps through 384 contacts, each with a radar chip, including all 149 the CNN accepts. Press 1 to 4 (vessel, structure, clutter, unsure); about 30 s each. The regional queue (about 100 more) can wait for a later week. Press "Copy labels as CSV" and paste the result into `data/labels/owner_2026-10.csv`, or send it to me. These are the first Sentinel-1C/1D labels. They score the transfer for paper 1 (`docs/paper1_design.md`, sections 3 and 4) and train the next verifier.
3. **Make three decisions (about 4 h with reading).** Read this file, `docs/scs_regional.md`, the gap analysis at the end of `docs/bibliometrics.md` and the short answer in `docs/journals.md`. Then decide:
   - (a) AIS source: GFW (noncommercial) for the papers, plus a commercial quote for Viettel.
   - (b) Letter venue: IGARSS 2027 or GRSL. The IGARSS 2027 deadline is not published yet. The 2026 deadline was 10 January 2026 (UNVERIFIED), so plan for early January 2027.
   - (c) Naming: South China Sea or East Sea.

## Could not verify

Each item points to the file that holds its sources. "Snippet" means seen only in web-search result text, because the page host is blocked.

Missions and data
- Sentinel-1A end of operations on 30 June 2026: the bucket shows no 1A IW product after 2026-06-29 (verified). The official date is a snippet (`docs/data_landscape.md`).
- Sentinel-1C/1D onboard AIS: restricted access, recording masked to European waters; 1C AIS matched 40 % of class A ships against 85 % for Spire (snippets; `docs/data_landscape.md`, gap analysis).
- New Sentinel-1C radiometric calibration deployed 3 February 2026 (snippet; gap analysis).
- Whether the central-sea coverage gap follows the Sentinel-1 observation scenario, and whether wave mode is the open-ocean default (ESA pages blocked; `docs/scs_regional.md`, gap analysis).
- Scene counts from the AWS mirror were not cross-checked against CDSE (`docs/scs_regional.md`).
- LOTUSat-1 launch timing, NISAR ocean products and RCM foreign-water products (snippets; `docs/data_landscape.md`).

Vietnam, AIS and VMS
- AIS carriage rules (SOLAS V/19) exempting most small fishing boats (snippet).
- Vietnamese VMS: about 99 % compliance for vessels of 15 m and longer, 79,360 registered vessels, a 2-hour position interval, Viettel named among VMS providers (snippets; `docs/data_landscape.md`, gap analysis).
- FAO SOFIA 2024: 89 % of vessels with known length under 12 m (snippet; gap analysis).
- GFW API terms, latency (72 hours to about 5 days), AIS providers and the Vietnam partnership since 2019 (snippets; `docs/data_landscape.md`).

Literature and venues
- SJR, quartiles, review times, APCs, page limits and AI policies for every venue (snippets; `data/journals.csv` holds the URLs). Scopus discontinued and hijacked-journal screens: NOT CHECKED.
- IGARSS 2027 dates and rules (snippets; `docs/journals.md`).
- ESA LPS25 presentation on Sentinel-1C ship detection, the only 1C detection result found, and not peer reviewed (snippet; gap analysis).
- Paolo 2024 detection calibration (about 60 % at 15 to 20 m) and the Liu 2025 cross-sensor AP drop of 23.75 % (snippets; gap analysis).
- 2025 and 2026 publication counts will rise as OpenAlex indexing catches up; the lag was not measured (`docs/bibliometrics.md`).

Project hypotheses
- What the fixed structures off Ca Mau are, and why low-confidence objects cluster on the shallow shelf (`docs/gis_baseline.md`). Regionally, Sentinel-2 confirms a bright object at 37 % of a fixed-structure sample, including platforms, small islets and anchorages (`docs/optical_check.md`); the Ca Mau stake-net reading is still by eye.
- The rule AI2 used to attach AIS attributes to its labels is undocumented (`docs/ml_verifier.md`).
- The false hot spots were identified as rain cells and aquaculture rafts by eye from the radar image. Weather is now checked (39 % of clutter-flagged objects under deep convection against 23 % of kept candidates, Himawari-9); aquaculture is not checked against any data (`docs/scs_regional.md`).
- The clutter-zone rule may also remove very dense fleets of small boats; its cost was measured only on AI2 labels, which hold few such fleets (`docs/scs_regional.md`).
- The lines of point targets the CNN rejects off Ca Mau look like stake nets or other fixed gear; not verified (`docs/ml_verifier.md`).
