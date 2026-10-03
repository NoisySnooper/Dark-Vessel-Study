# Status

Updated: 2026-10-03 (UTC). The owner reviews; the assistant does the heavy lifting.

> **"Dark" does not mean illegal.** It only means no AIS position was matched to a radar detection. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite AIS misses messages in busy coastal waters. No AIS is connected yet, so nothing in this repo is labeled dark.

## Look at this first

- **Demo page (private link, yours to share):** https://claude.ai/artifact/5VbpXaoghkKx8nwGoaEeUs (version 6). It holds:
  - a regional map of the South China Sea with all 78,615 vessel candidates of one 12-day cycle and the 90-day Sentinel-1 coverage layer;
  - a radar view of one Ca Mau scene, with CNN scores;
  - a contact inspector (radar chip, DMS and MGRS position, date-time group);
  - in-browser labeling with CSV export.
- **ArcGIS Pro:** `data/detections_regional.gpkg` (vessel candidates), `data/structures_regional.gpkg` (fixed structures), `data/detections_baseline.gpkg` (Ca Mau), `data/aoi.gpkg`, rasters in `data/outputs/small/`. Every product has an EPSG:4326 layer or file plus a UTM one (49N regional, 48N Ca Mau).
- **Figures:** `docs/figures/coverage.png`, `docs/figures/look_probability.png`, `docs/figures/regional_detections.png`, `docs/figures/viirs_lights.png`, `docs/figures/optical_check.png`, `docs/figures/optical_examples.png`, `docs/figures/baseline_map.png`, `docs/figures/ml_1d_chips.png`, `docs/figures/nesz_by_satellite.png`.
- **Run everything:** `make test`, `make regional`, `make context`, `make demo OUT=page.html` (`Makefile`).

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
| `docs/bibliometrics.md`, `docs/journals.md` | Literature scan, gap analysis, venues |
| `docs/data_landscape.md`, `docs/data_additions.md` | Data sources with licences and access tests |

## Scope change (2026-10-02)

The AOI moved from Ca Mau to the whole South China Sea at the owner's request. AOI = Natural Earth marine areas "South China Sea", "Gulf of Tonkin" and "Gulf of Thailand", 3.58 million km2. Ca Mau stays as the scene-detail sub-area.

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
- **VIIRS night lights** over the whole AOI, every night (`scripts/15_viirs_lights.py`, `docs/viirs_lights.md`): 27-night run in progress; results follow in this file.
- **Run harness:** `Makefile` with the pipeline in dependency order.
- **Paper 2 design** (`docs/paper2_design.md`): the miss budget (coverage, detection by length, fleet composition, lit activity), what is measured and what each term still needs.

### Paper 1 groundwork (`docs/paper1_design.md`)
- Noise floor from the products' own annotation: Sentinel-1C and 1D are 1.4 dB (VV) and 1.8 dB (VH) below Sentinel-1A (2022) at every incidence angle, and 1C and 1D match each other (`docs/figures/nesz_by_satellite.png`). This explains about half of the darker 1D chip backgrounds.
- Labeling design: a fixed random sample per class, plus every CNN-accepted Ca Mau contact (149, enough to detect a 10-point precision drop). The demo page queues them, and `scripts/12_score_labels.py` turns your CSV into per-class shares and CNN precision and recall with intervals.

## Blocked

| Blocker | Effect | Fix (owner action) |
|---|---|---|
| Network policy denies most hosts (api.openalex.org, planetarycomputer.microsoft.com, Copernicus Data Space, scimagojr.com, elsevier.com, retractionwatch.com, doi.org, globalfishingwatch.org, esa.int, huggingface.co, zenodo.org) | No live OpenAlex API, no STAC search, no SJR file, no Scopus discontinued list, no hijacked-journal check, no DOI resolution. Many facts stay UNVERIFIED. | Cloud environment settings > Network access: Full, or allow the listed hosts |
| No keys | OpenAlex API (key-only since 2026), Copernicus S3 and GFW API unavailable | Add `OPENALEX_API_KEY`, `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY` and `GFW_API_TOKEN` as environment secrets. Never in chat or git. |
| No AIS source | Nothing can be labeled dark; no AIS-based recall by length on 1C/1D scenes | GFW token (noncommercial) for the papers; a commercial feed for anything Viettel-facing |
| No 1C/1D ground truth | Transfer to 1C/1D cannot be scored | Hand labels: next task 2 |
| Compute | Regional run covers one 12-day cycle of the 90 days, on a shared 4-core machine | `scripts/09_run_regional.py --days 90` on a bigger machine (checkpointed per scene) |

Worked around: OpenAlex via its public S3 snapshot; Sentinel-1 via the AWS Open Data mirror; land mask via ESA WorldCover on S3; AOI and coastline via Natural Earth on GitHub; labels via the public Skylight repo.

## Defaults applied (say the word to change any)

- Map naming: "South China Sea" as written in your request. For a Vietnamese audience you may prefer "East Sea (Bien Dong)"; it is a one-line change in `src/darkvessel/config.py` and the figure titles.
- No maritime boundaries or claim lines are drawn anywhere.
- Display: files in the repo, short chat summaries, and one private demo page.
- Commits: one per workstream plus labeled work-in-progress commits, no Co-Authored-By trailer. Commit author is the container default; give a name and email to switch.
- Training data: AI2 Skylight labels (Apache-2.0) only; xView3-SAR is reported noncommercial (UNVERIFIED), so it is not used.

## Next 3 tasks (smallest first, each fits a 5 h week)

The full list of owner actions, with steps, is in `docs/OWNER_ACTIONS.md`.

1. **Unblock the environment (about 1 h).** In the cloud environment settings, set network access to Full or allow the hosts in the Blocked table. Add the four keys as environment secrets. Then tell me. I will close the main UNVERIFIED items: SJR and quartiles, the Scopus and hijacked-journal screens, DOI resolution, a CDSE STAC cross-check of the scene list, and a GFW AIS pull for the regional window.
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
