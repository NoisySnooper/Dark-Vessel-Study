# SCS Vessel Watch: data contract

Contract version **1.3.0**, 2026-10-10 (UTC). Task R3-T10 (round 3, second dispatch). 1.2.0 (R1-T4, 2026-10-09) is the base; 1.3.0 records what the backend in `app/backend/scs_api` does after round 3 and the board decisions D5 (round 3) and D6.2 (`docs/PROJECT_BOARD.md`). What changed: the object context of contacts and lights (D5.3) and the expected activity of cells (D5.4) are served; live contacts carry their identification evidence (azimuth shift, ambiguity, hand check, live weather) with field-level provenance (`field_prov`) and the aisstream label (D4.7); passes carry their AIS-only vessels and the azimuth check; every light an L7 lead cites resolves to a Light record; every vessel key a live record links to resolves (aisstream stubs); lead codes pass through unchanged (D5.1); `research_only` is per row (D5.5); the leads detail file of D6.3 is read when needed; the part layout of the single-file bundle is normative (D5.2, section 6.4); the 15 changes R2-T3 proposed for 1.2.1 are decided (section 8); every file is re-checked (16:24 UTC, and again at 18:03 UTC after the R3-T7 fix rerun and the leads rebuild). Binding on the round 4 backend, frontend and single-file build tasks. The product spec is `docs/product_design.md` (1.3); package choices are in `docs/research/stack_decision.md`.

> 'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent.

This is the product caveat (section 1.1). Every record this contract defines carries it in a `caveat` field.

## 1. Conventions

- **Types.** `str`, `int`, `float`, `bool`, `datetime` (ISO 8601 in UTC with a `Z` suffix in the API, for example `2026-09-20T10:48:16Z`; source files may write `+00:00`, the backend normalises), `enum` (one of the listed strings), `list[...]`, `geom` (GeoJSON geometry, EPSG:4326 lon/lat). Null is JSON `null`; a column listed as not nullable never holds null.
- **Units** are in the field name where the repo already does so (`_m`, `_km`, `_s`, `_db`, `_deg`, `_kn`, `_ms`, `_c`, `_nw`, `_pct`) and in the Unit column below.
- **Positions.** `lon`, `lat` in decimal degrees, WGS 84 (EPSG:4326). Exports add UTM 49N (EPSG:32649) for regional objects and UTM 48N (EPSG:32648) for Ca Mau objects.
- **Builds.** `open` or `research`. The Build column says which build may read a field or file: `both`, `open` (open build only), `research` (research build only). The open build never opens a path under `data/research/` (D2); the backend catalog refuses such paths when `build=open`, and a test asserts it.
- **File status** (re-checked 2026-10-10 16:24 UTC, and again at 18:03 UTC for the fix round, with the backend catalog, `scs_api.catalog.Catalog.entries()`: `pyogrio.list_layers`, `pyogrio.read_info` and `pyarrow.parquet.read_metadata`, both builds; the time in brackets is the file's modification time, UTC, 2026-10-10 unless stated; at 18:03 the live files, review tables, leads files, chips and AIS hour files had changed, and their rows carry the new counts and times): **existing** (layer present, row count shown) or **missing** (the producing task or round is named; the consumer must handle its absence: the view shows a `NonIdealState` "not built yet", never an error). Row counts of growing files (live passes, AIS recording) are the counts at the check. `GET /api/v1/meta` `files` returns the same status, rows and modification time at any moment.
- **Ids.** Object ids are strings and stable across reruns: `det_id`, `vessel_key`, `light_id`, `site_id`, `event_id`, `lead_id`, `pass_id`, `cell_id`.
- **Codes, not sentences (board D5.1).** Lead `factors[].factor`, `lawful_explanations` and `change_indicators` hold codes (for example `no_carriage_requirement`, `late_ais_match`, `evidence_quality`) in every producer file, API response and bundle, exactly as the lead builder (`darkvessel.leads.rules`, `darkvessel.leads.priority`) wrote them; the backend passes them through unchanged. The frontend maps each code to its display text (`app/frontend/src/app/text.ts`); `app/frontend/fixtures/test_text_codes.py` asserts every code the lead builder can emit has a display string. Factor `value` strings are the producer's own and are shown as data, not mapped.
- **`research_only` is per row (board D5.5).** It is true on a row that uses GFW data and false otherwise, in both builds. Every record of the research build also carries the research line in its `caveat` (section 1.1) and every research response the envelope's `research_label` and `attribution` (section 5), so a research page is labelled even where a row is not GFW data (live contacts, lights, passes, cells).
- **Field-level provenance (spec 4.8).** `src` and `prov` (section 2) name a field's source; `field_prov` (contract 1.3.0) adds, for a field whose source states it, the acquisition or valid time and the source's own words: `{"<field>": {"src", "time", "text"}}`. It holds an entry only for a field that has a value. `time` is ISO 8601 UTC with `Z`, a `YYYY-MM-DD` date when the source gives only a date, or null; a source time the backend cannot read as one of these is null, never passed through raw (`scs_api.records.prov_time`; a test and the real-data crawl check every `field_prov` and `object_context` time). The ocean fields of `object_context` carry their own `src` and `time` (section 3.1, Object context).

### 1.1 The product caveat

One string, used unchanged in the API `caveat` field of every record and envelope, in every export, on every lead card and lead page, and behind the short banner text (the banner links to it):

`PRODUCT_CAVEAT` = "'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent."

- It extends `darkvessel.config.DARK_CAVEAT`, which names only the satellite blind spot, although the open build's AIS is terrestrial (aisstream relays shore receivers). `darkvessel.config.PRODUCT_CAVEAT` holds this exact text since round 2 (board D4.2, commit 0eb5ef0); the backend imports it (`app/backend/scs_api/config.py` keeps the same text as a fallback), and a contract test asserts that the two and the line above are equal.
- The short banner text stays `darkvessel.config.DARK_CAVEAT_SHORT`: "Dark = no AIS match. Not evidence of illegal activity."
- Research build records append: "Research build, noncommercial, CC BY-NC 4.0. Contains Global Fishing Watch data. Powered by Global Fishing Watch."
- The `caveat` column of a source row (today `DARK_CAVEAT` in the live and research files) is not shown in its place. When it differs from `PRODUCT_CAVEAT`, the backend passes it through as `extra.source_caveat`, so nothing the producer wrote is lost.
- Sources of the claims in it: AIS carriage applies to "all ships of 300 gross tonnage and upwards engaged on international voyages, cargo ships of 500 gross tonnage and upwards not engaged on international voyages and all passenger ships" (IMO AIS page), so the IMO requirement does not cover smaller vessels such as most fishing boats on domestic voyages (national rules can add duties; not checked for this contract); lawful switch-off is in IMO Resolution A.1106(29) paragraph 22 ("If the master believes that the continual operation of AIS might compromise the safety or security of his/her ship or where security incidents are imminent, the AIS may be switched off."); terrestrial AIS covers "only specific coastal areas where a land-based AIS receiver is installed" (MarineTraffic support article); the satellite clause is the existing config text. URLs in section 8.

## 2. Sources and provenance

Every record carries `src` (the default source key of the record) and `prov` (a map from field name to source key, only for fields whose source differs from `src`). Contact, Pass and Cell records also carry `field_prov` (section 1, Field-level provenance; contract 1.3.0): per field with a value, `{src, time, text}`, where `time` is the field's acquisition or valid time as its source states it (ISO 8601 UTC with `Z`, or a date when the source gives only a date) and `text` the source's own words (for example the GFS cycle and file, the Himawari file, the hand-check table). `GET /api/v1/meta` and the bundle's `meta` part hold the source registry. The UI builds the per-field provenance chip from these (spec section 4.8).

Source registry entry: `{key, name, method, script, licence, licence_url, url, access_date, credit, research_only}` plus `git_hash` of the repo at build time (one value for the build).

**Open build:** `GET /api/v1/meta` and the bundle's `meta.sources` drop every registry entry with `research_only` true (the three `gfw_*` keys below), so no `gfw_` key or GFW name reaches an open response or page (check PW-06).

| Key | Name | Method and script | Licence as published | Build |
|---|---|---|---|---|
| `s1_grd` | Copernicus Sentinel-1C/1D IW GRD, AWS Open Data mirror `sentinel-s1-l1c` | input to every radar product | Copernicus Sentinel Data Legal Notice: free, full and open; credit "Contains modified Copernicus Sentinel data 2026" | both |
| `det_regional` | September regional run, 119 scenes, 2026-09-20 to 2026-10-01 | CA-CFAR, PFA 1e-6, VV/VH fusion, clutter-zone and near-fixed rules, persistence; `scripts/09_run_regional.py` | derived from `s1_grd` | both |
| `det_live` | Live passes | same detector settings; `scripts/30_live_pass.py` (`darkvessel.live`) | derived from `s1_grd` | both |
| `det_camau` | Ca Mau detail scene, Sentinel-1D, 2026-09-29 | CA-CFAR baseline; `scripts/03_run_baseline.py` | derived from `s1_grd` | both |
| `cnn_v0` | CNN verifier `verifier_v0` (`data/models/verifier_v0.pt`, model id `verifier_v0_356af0ca`, threshold 0.631783) | `scripts/06_apply_verifier.py` (Ca Mau), `scripts/14_cnn_shared_cells.py`, `scripts/32_cnn_regional.py` (every September contact and structure), live pipeline | trained on Allen Institute for AI Sentinel-1A/1B vessel point labels, Apache-2.0 | both |
| `aisstream` | aisstream.io websocket relay of shore receivers | `scripts/26_ais_record.py` recorder; matching `darkvessel.live` | UNVERIFIED: the operator publishes no terms of use (only a privacy policy) | open (also readable by research) |
| `mid_itu` | ITU Table of Maritime Identification Digits | `darkvessel.live.mid` (292 codes parsed 2026-10-08) | ITU public table | both |
| `gfw_4wings` | Global Fishing Watch 4Wings AIS presence and SAR detections | `scripts/27_gfw_pull.py`, `scripts/31_gfw_identity.py` | CC BY-NC 4.0, noncommercial; "Powered by Global Fishing Watch." | research (`research_only` true) |
| `gfw_vessels` | GFW vessels API identity and registry fields | `scripts/31_gfw_identity.py` | CC BY-NC 4.0 | research (`research_only` true) |
| `gfw_events` | GFW gaps, encounters, loitering and port-visit events | `scripts/27_gfw_pull.py` | CC BY-NC 4.0 | research (`research_only` true) |
| `viirs_dnb` | VIIRS Day/Night Band SDR and GEO, JRR cloud mask (S-NPP, NOAA-20, NOAA-21), NOAA JPSS on AWS | `scripts/15_viirs_lights.py` | NOAA open data: "open to the public and can be used as desired" | both |
| `gfs_wind` | NOAA GFS 0.25 degree 10 m wind | `scripts/16_weather_context.py` (`darkvessel.weather`) | NOAA open data | both |
| `himawari_ctt` | Himawari-9 AHI L2 cloud-top temperature (`noaa-himawari9`) | `scripts/16_weather_context.py` | "Himawari data is produced and managed by JMA. NOAA has rights to distribute this data freely and openly to the public." | both |
| `s2_optical` | Sentinel-2 L2A COGs (`sentinel-cogs`) | `scripts/19_optical_check.py` | Copernicus; "contains modified Copernicus Sentinel data 2026" | both |
| `satlas` | AI2 Satlas marine infrastructure points | `scripts/20_satlas_check.py` | ODC-BY | both |
| `worldcover` | ESA WorldCover 2021 v200 (sea mask) | detector land mask | CC BY 4.0 | both |
| `natural_earth` | Natural Earth 10 m land, marine areas, ports | AOI, coast, land | public domain | both |
| `gebco_2026` | GEBCO_2026 Grid | `scripts/22_static_layers.py` | public domain, acknowledge the source, not for navigation | both |
| `wpi` | NGA World Port Index (Pub 150) | `scripts/22_static_layers.py` | NGA claims no copyright in posted products; no endorsement | both |
| `marineregions_v12` | Marine Regions World EEZ v12 (Flanders Marine Institute, VLIZ), doi:10.14284/632 | `scripts/22_static_layers.py` | CC BY 4.0 | both |
| `worldbank_density` | World Bank and IMF Global Shipping Traffic Density | `scripts/22_static_layers.py` | CC BY 4.0 | both |
| `mur_sst` | MUR v4.1 SST (JPL PO.DAAC) via NOAA CoastWatch ERDDAP | `scripts/23_daily_ocean.py` | free of charge under the PO.DAAC data policy | both |
| `chl_dineof` | NOAA CoastWatch chlorophyll-a, DINEOF gap-filled | `scripts/23_daily_ocean.py` | per dataset in `data/ocean_daily_summary.json` (CC0-1.0 or NOAA no-copyright text) | both |
| `rtofs` | NOAA RTOFS global nowcast 2-D diagnostics | `scripts/23_daily_ocean.py` | NOAA open data | both |
| `gfs_wave` | NOAA GFS-Wave 0.25 degree significant wave height | `scripts/23_daily_ocean.py` | NOAA open data | both |
| `ocean_context` | Ocean context at radar objects and VIIRS lights (`data/ocean_context_objects.parquet`) | static and daily sea fields sampled at each object's position and time; every field names its own source and valid time; `scripts/25_object_context.py` | derived; each field carries the licence of its own source | both |
| `expected_activity` | Expected-activity model (`data/expected_activity.parquet`, `data/expected_activity.json`) | expected counts of lit and radar vessel candidates per cell and night or pass from sea and weather fields; observed against expected, z, Benjamini-Hochberg q and flags are model output; `scripts/34_expected_activity.py` | derived | both |
| `esa_acq_plan` | ESA Sentinel-1 acquisition plan KML files | `darkvessel.ais.s1_passes` | ESA public plan; repeat predictions are not ESA's plan | both |
| `analyst` | Owner labels and lead decisions | the app (local: `data/labels/`; single-file: browser) | the owner's | both |
| `app` | values computed by the product (review priority, evidence counts) | `app/backend` (`priority_model_id`) | derived | both |

The licences above are copied from the `about` layers and summaries the producing scripts wrote, and were re-read at their URLs in this session where listed in the Sources section (section 9). The backend's registry (`app/backend/scs_api/sources.py`) holds the same 30 keys (27 in the open build, which drops the three `gfw_*` entries); `ocean_context` and `expected_activity` were added in 1.3.0.

## 3. Object types

### 3.1 Contact

One radar contact. **The fields are the D1 columns of `docs/PROJECT_BOARD.md`, with the same names, in the same order**, followed by extension fields. Live and research files carry D1 natively (re-checked 2026-10-10 16:24 UTC: the first 31 columns of `data/live/live_contacts.gpkg` `contacts_4326` equal `darkvessel.live.schema.D1_COLUMNS`, the first 32 of `data/research/regional_identity.parquet` equal `darkvessel.ais.gfw_identity.D1_COLUMNS`); the two older files without identity are mapped onto D1 by the backend as shown in the last two columns.

**D1 fields**

| # | Field | Type | Unit | Null | Meaning | from `detections_regional_4326` (no identity) | from `detections_verified_4326` (Ca Mau) |
|---|---|---|---|---|---|---|---|
| 1 | `det_id` | str | | no | unique contact id, stable across reruns (`<mission>_<scene start yyyymmddThhmmss>_<5-digit index>`; Ca Mau `<mission>_<yyyymmddThhmm>_<4-digit>`) | `det_id` | `det_id` |
| 2 | `run_id` | str | | no | `live_<mission>_<yyyymmddThhmm>` or `regional_2026-09` | constant `regional_2026-09` | constant `camau_2026-09-29` (product extension, not a D1 value) |
| 3 | `mission` | enum `S1C`, `S1D` | | no | satellite | `mission` | first 3 characters of `det_id` |
| 4 | `acq_utc` | datetime | | no | scene start time | `acq_utc` | `acq_utc` |
| 5 | `lon` | float | deg | no | longitude | `lon` | `lon` |
| 6 | `lat` | float | deg | no | latitude | `lat` | `lat` |
| 7 | `length_est_m` | float | m | yes | radar length estimate (pixel extent, crude, biased upward) | `length_est_m` | `length_est_m` |
| 8 | `confidence` | enum `high`, `medium`, `fixed`, `low` | | no | detector class | `confidence` | `confidence` |
| 9 | `cnn_score` | float | 0 to 1 | yes | CNN verifier score; null when not scored | join on `det_id` to `data/ml/regional_cnn.parquet` (every row scored) | `cnn_score` |
| 10 | `cnn_vessel` | bool | | yes | score at or above the model threshold | as `cnn_score` | `cnn_vessel` |
| 11 | `ais_status` | enum `matched`, `unmatched`, `no_coverage`, `not_checked` | | no | AIS status; `not_checked` is a product extension for files outside D1 (spec section 18, question 2) | `not_checked` (file value `not_checked`) | `not_checked` (file value `not_checked: no AIS source connected`) |
| 12 | `ais_source` | enum `aisstream`, `gfw` | | yes | AIS source of the status | null | null |
| 13 | `match_method` | str | | yes | `track_interp_hungarian` (live), `gfw_sar_cell_hour`, `gfw_presence_cell_hour` (research) | null | null |
| 14 | `match_dist_m` | float | m | yes | contact to matched AIS position; null unless matched | null | null |
| 15 | `match_dt_s` | float | s | yes | live: time from the scene to the matched vessel's nearest AIS report; research: hour-bucket or SAR time offset (the file's `about` layer states it) | null | null |
| 16 | `match_quality` | enum `high`, `medium`, `low` | | yes | rule in the file's `about` layer | null | null |
| 17 | `mmsi` | str (digits) | | yes | matched vessel MMSI (files hold int64 in live, string in research; the API always returns a string) | null | null |
| 18 | `imo` | str (digits) | | yes | IMO number as reported | null | null |
| 19 | `vessel_name` | str | | yes | name as reported | null | null |
| 20 | `call_sign` | str | | yes | call sign as reported | null | null |
| 21 | `flag` | str | | yes | live: ITU wording of the MMSI's MID country (the flag the transponder claims); research: GFW's flag field as published | null | null |
| 22 | `ship_type` | str | | yes | live: ITU-R M.1371 ship type label; research: GFW vessel type | null | null |
| 23 | `length_ais_m` | float | m | yes | live: hull dimensions A + B from static messages; research: registry length where GFW publishes one | null | null |
| 24 | `identity_source` | str | | yes | where the identity fields came from (text) | null | null |
| 25 | `gfw_vessel_id` | str | | yes | research build only; absent from open files and from every open response (the open Contact model has 31 fields) | absent | absent |
| 26 | `nearest_ais_mmsi` | str (digits) | | yes | nearest AIS vessel placed at the scene time, any distance | null | null |
| 27 | `nearest_ais_dist_m` | float | m | yes | distance to it | null | null |
| 28 | `nearest_ais_dt_s` | float | s | yes | time from the scene to its nearest report | null | null |
| 29 | `n_ais_10km` | int | count | yes | distinct MMSI with a report within 10 km during the window | null | null |
| 30 | `ais_reach` | float | share 0 to 1 | yes | open: share of recorded hours with any AIS in the contact's 0.25 degree cell; research: GFW AIS presence equivalent | null | null |
| 31 | `research_only` | bool | | no | true for any row that uses GFW data | false | false |
| 32 | `caveat` | str | | no | `PRODUCT_CAVEAT` (section 1.1), plus the research line in the research build; the source row's own text goes to `extra.source_caveat` when it differs | constant | constant |

`no_coverage` is used only with the stated rule of the producing file (open live rule: nothing heard during the window in the contact's 0.25 degree cell or within 20 km). It never means dark. All 3,081 contacts of the first live pass (`live_S1D_20261008T2258`, five scenes, east side of the Gulf of Thailand, rerun at 07:37 UTC) are `no_coverage`, and `dark_lead` is false for every one. The feed was up: each scene window recorded 8,812 to 9,074 positions from 995 to 1,012 MMSI somewhere in the AOI, but none inside any of the five footprints and none within 0.3 degree of them (`scenes_4326` columns `ais_aoi_*`, `ais_footprint_*`, `ais_near_footprint_mmsi`; `data/live/live_summary.json`; `docs/live_pass.md`). The free terrestrial feed does not reach that water. The product must show such a pass as a coverage result, not as 3,081 dark leads. The second live pass (`live_S1C_20261010T1119`, 780 contacts) is also all `no_coverage`. The third, the Pearl River pass (`live_S1D_20261010T1032`, Sentinel-1D, 2026-10-10 10:32 UTC, 2 scenes), is the first with AIS in its footprint: 4,712 contacts, 33 `matched`, 1,947 `unmatched` (62 of them held back by the ambiguity rule, never dark leads), 2,732 `no_coverage` (re-checked 16:24 UTC; R3-T7 owns the pass and its hand check, `docs/live_pass.md`).

**CNN score join.** For every September row, in both builds, `cnn_score` and `cnn_vessel` come from `data/ml/regional_cnn.parquet` (model `verifier_v0_356af0ca`, threshold 0.631783; 68,213 rows scored by `scripts/32_cnn_regional.py`, 35,626 reused unchanged from `shared_cells_cnn.parquet`). Since its rebuild at 13:51 UTC the research file takes its scores from the same file (`regional_identity_summary.json` `cnn_source` = `data/ml/regional_cnn.parquet`): all 78,615 rows are scored and equal to `regional_cnn.parquet` (checked 13:55 UTC, maximum difference 0.0). The 07:00 UTC version held scores only for the 26,799 shared-cell rows; the join stays in the backend so an older file still works. The low-object run finished in round 2 (R2-T4): `regional_cnn.parquet` now holds 927,124 rows, the 103,839 contacts and structures plus the 823,285 `low` objects; low objects stay out of the regional product (section 4), and the backend filters the table to the product's det_ids in Arrow before converting it. `regional_identity.gpkg` stores `cnn_vessel` as Int16 (1, 0, null) and the parquet as bool; the API always returns bool or null.

**Extension fields** (allowed by D1; null when the source lacks them)

| Field | Type | Unit | Source (open live / research / regional / Ca Mau) |
|---|---|---|---|
| `view` | enum `regional`, `live`, `camau` | | derived from `run_id` |
| `dark_lead` | bool | | live: the producer's flag, true for an unmatched `high` or `medium` contact (`about` layer `dark_lead_rule`); research and regional: null. It marks an L1 candidate only: the L1 rule adds the CNN, weather and clutter conditions (spec section 4.1), and the Lead object is what the queue shows |
| `vessel_key` | str | | link to the Vessel object: `mmsi:<mmsi>` (live, matched), `gfw:<gfw_vessel_id>` (research, matched); null when not matched |
| `nearest_vessel_key` | str | | `mmsi:<nearest_ais_mmsi>` (live), `gfw:<nearest_ais_vessel_id>` (research) |
| `scene_id` | str | | live `scene_id`; regional via `scene_idx` to `scenes_processed_4326.product_id`; Ca Mau `scene_id` |
| `pass_id` | str | | live: `run_id`; research: `pass_id` evidence column; regional: pass group of the scene (backend groups scenes by mission and start within 10 min) |
| `pass_dir`, `orbit_rel` | str, int | | live columns; regional via `scenes_processed_4326` |
| `inc_angle_deg` | float | deg | all four sources |
| `pol_class` | str | | live, Ca Mau |
| `n_pixels` | int | px | live, Ca Mau |
| `scr_vv_db`, `scr_vh_db` | float | dB | all four sources |
| `low_reason` | str | | live, Ca Mau |
| `persist_dates`, `persist_dates_checked` | int | count | all four sources |
| `n_low_1km`, `near_fixed_m` | int, float | count, m | live |
| `match_gate_m`, `ais_sog_kn`, `length_ratio`, `ais_class`, `mmsi_mid`, `ais_footprint_positions` | float, float, float, str, int, int | m, kn, ratio, count | live (`ais_footprint_positions`: AIS positions heard inside the scene footprint during the window; renamed from `ais_window_positions` in the 07:37 UTC rerun) |
| `pred_method`, `cnn_chip_valid_frac`, `ais_recorded_hours`, `row`, `col` | str, float, int, float, float | share, h, px | live (`row`, `col` are the contact's pixel coordinates in its scene, as in the Ca Mau file; decision 8.3); `cell_id` is built from `lon`, `lat` |
| `az_time_utc` | datetime | | live (1.3.0): the contact's own azimuth time from the Sentinel-1 product annotation (geolocation grid); the matcher places every AIS vessel at this time; seconds, `Z` |
| `match_dist_uncorr_m` | float | m | live, matched (1.3.0): contact to the AIS position at `az_time_utc` before the azimuth-shift correction |
| `az_shift_m` | float | m | live, matched (1.3.0): predicted SAR azimuth shift of the matched vessel's velocity at this contact, `dx = -(R / V) x v_r` along the satellite track (live `about` `azimuth_correction`; Raney 1971); `match_dist_m` is measured after it |
| `velocity_source` | enum `sog_cog`, `track` | | live, matched (1.3.0): the vessel velocity behind `az_shift_m`: reported SOG and COG, or the track between the bracketing reports |
| `match_ambiguous` | bool | | live (1.3.0): the pairing could not tell this contact apart from another feasible AIS vessel (or the vessel from another contact) by the clearly-worse margin (live `about` `ambiguity`); an ambiguous contact is `unmatched` and never a dark lead. False on every other live contact; null outside live passes |
| `ambiguous_mmsi` | str | | live (1.3.0): the candidate MMSIs of an ambiguous contact, one MMSI or several joined by `;`, as the producer wrote them |
| `match_alt_dist_m` | float | m | live (1.3.0): distance of the closest competing pairing of an ambiguous contact |
| `review_note` | str | | live (1.3.0, R3-T7): `<grade>: <reason>` when a person hand-checked the row (`data/live/<run_id>_review_matched.csv` and `_review_unmatched.csv`), else null (live `about` `review_note`). A note never changes a match or a status. `field_prov.review_note` is `{"src": "analyst", "time": <reviewed_utc of the row>, "text": "hand check, data/live/<the review table holding the row> (reviewed_utc)"}`, for example `data/live/live_S1D_20261010T1032_review_matched.csv` and the date `2026-10-10`; a note with no row in any review table gets time null and the text "hand check, review_note of data/live/live_contacts.gpkg (no row in a hand-check table, time unknown)" |
| `review_grade` | enum `confirmed`, `plausible`, `doubtful` | | live (1.3.0): the grade part of `review_note`, derived by the backend; null without a note |
| `identity_label` | str | | 1.3.0: "live AIS relayed by aisstream.io; terms UNVERIFIED" (board D4.7) on every live contact whose record carries an aisstream identity (`mmsi` or `nearest_ais_mmsi`); null otherwise (GFW identities carry the research label instead) |
| `pass_id`, `identity_kind`, `gfw_sar_pair`, `gfw_sar_n_cand`, `gfw_sar_n_rivals`, `gfw_sar_ambiguous_cell`, `gfw_geartype`, `gfw_neural_type`, `pres_speed_kmh`, `pres_n_cells`, `pres_n_cand`, `n_gear_10km`, `ais_presence_h_day`, `ais_presence_h_window`, `nearest_ais_vessel_id`, `nearest_ais_name`, `n_gfw_gaps_50km_24h`, `nearest_gfw_gap_km`, `n_gfw_encounters_10km_24h`, `n_gfw_loitering_10km_24h` | as in `darkvessel.ais.gfw_identity.EVIDENCE_COLUMNS` (all 20, checked 13:55 UTC); `identity_kind` is `vessel`, `gear` (GFW types the matched AIS device as GEAR, a net or gear buoy, not the vessel's identity) or `unknown` | km/h, count, h, km | research only |
| `cnn_threshold`, `cnn_model_id`, `cnn_score_source`, `cnn_chip_valid_frac`, `bg_vv_db`, `bg_vh_db` | float, str, str, float, float, float | share, dB | regional: `regional_cnn.parquet`; Ca Mau: its own file (threshold and model id only); live: the `cnn_v0` source entry |
| `wind_ms`, `ctt_k`, `deep_convection` | float, float, bool | m/s, K | September run and Ca Mau: `data/weather_context.parquet` joined on `det_id`; live (1.3.0): the pass's sidecar `data/live/<run_id>_weather.parquet` joined on `det_id` (GFS 0.25 degree 10 m wind of the hour nearest the scene from the newest cycle posted, rounded to 0.01; Himawari-9 cloud-top temperature within 4 km, null where clear). `field_prov` gives the GFS valid time (cycle plus forecast hour) and file, and the Himawari scan start and file (September and Ca Mau: `himawari_start`, see below, and the GFS hour is not stored, so the wind time is null; the text names `data/weather_context.parquet`) |
| `extra.himawari_start` | datetime | | September run and Ca Mau: the Himawari scan start of `ctt_k`, ISO 8601 UTC with `Z`. `scripts/16_weather_context.py` writes it as 13 digits, the first 13 of the file name's `_s<yyyymmddhhmmss><tenth>` field (`yyyymmddHHMM` and the tens digit of the seconds, for example `2026092010402`); the backend reads them as `2026-09-20T10:40:20Z`, so the time is known to 10 s (floored). All 162,386 rows have this form (checked 18:05 UTC). The same time is `field_prov.ctt_k.time` and `field_prov.deep_convection.time` |
| `optical_object`, `optical_kind`, `s2_item`, `satlas_m` | bool, str, str, float | m | `data/optical_check.gpkg` joined on `det_id` (sample of 4,100) |
| `cell_id` | str | | derived from `lon`, `lat` on the 0.25 degree model grid (3.7) |
| `object_context` | object or null | | 1.3.0, board D5.3: the ocean context at the contact (Object context, below); null when the context table has no row for it. Full records only (`GET /contacts/{det_id}`, exports, bundle records), never in list summaries |
| `lead_ids` | list[str] | | from the Lead objects |
| `chip` | str or null | | API: `/api/v1/contacts/{det_id}/chip.webp` when cached; bundle: key into the `chips` part |
| `src`, `prov`, `field_prov` | str, map, map | | section 2; `field_prov` (1.3.0) on the live identification fields and the weather fields that hold a value |

**Identification rules of a matched contact (boards D4.7 and D6.2).** A matched contact is shown as an identification only when its `match_quality` is `high` or `medium` and its hand check (`review_grade`) is not `doubtful`. Every other `matched` contact, that is every one with `match_quality` `low` and every one graded `doubtful`, keeps its row, its MMSI, its identity strings and its quality in every file and response, and the frontend labels it "low-quality pairing, identity not confirmed" (`app/frontend/src/app/identity.ts`, `lowQualityPairing`; the label text lives in the frontend, `text.ts` `LOW_QUALITY_LABEL`). No match is removed or rewritten by the backend. On the Pearl River pass at the 18:03 UTC re-check (the R3-T7 fix rewrote the live files and review tables at 17:51 UTC): 33 matched, `high` 18 (13 confirmed, 5 plausible), `medium` 8 (5 confirmed, 2 plausible, 1 doubtful), `low` 7 (3 doubtful, 4 plausible), so 8 records carry the label (the 7 `low` and the `medium` one graded `doubtful`) and 25 are shown as identifications. Every aisstream identity carries `identity_label` (above), and so does every aisstream Vessel record (3.2) and every AIS-only vessel of a Pass (3.6).

**Object context (board D5.3).** `object_context` of a Contact or Light is null, or:

```json
{"time_utc": "2026-09-20T10:48:16Z", "cell_id": "r24c39", "region": "Gulf of Tonkin",
 "fields": {"depth_m": {"value": 60.3, "unit": "m", "time": null, "src": "gebco_2026"},
            "sst_c": {"value": 28.475, "unit": "degC", "time": "2026-09-20T09:00:00Z", "src": "mur"}, "...": "..."},
 "caveat": "<OCEAN_CAVEAT, then the table's own object sentence>"}
```

It comes from `data/ocean_context_objects.parquet` (`scripts/25_object_context.py`) keyed by (`object_type`, `object_id`): `radar` rows belong to September contacts and structures, `radar_detail` rows to the Ca Mau objects, `viirs` rows to lights. `time_utc` is the object's time, `cell_id` its 0.25 degree cell, `region` its reporting box (not a boundary). `fields` always holds the 16 names below in this order; a field without a value keeps its unit and source with `value` null, and its `time` is the table's: null when the day had no layer (for example `current_speed_ms` and `mld_m` 8,920 rows, `wave_hs_m` 4,581), the layer's valid time when the layer had no value at the object (`sst_grad` 23,308 rows, `sst_c` 6,415, `chl_log10` 184; counts of the 360,013 rows, checked 18:05 UTC). Every `time` is ISO 8601 UTC with `Z`, a `YYYY-MM-DD` date (chlorophyll is daily) or null; the backend never passes a time it cannot read as one of these (`records.prov_time`). `caveat` is `darkvessel.ocean.grid.OCEAN_CAVEAT` followed by the table's own sentence ("The context fields describe the sea at an object's position and time, not what the object is or does; a radar candidate can be clutter and a light can be a platform.") when the table's caveat extends `OCEAN_CAVEAT`, as it does today. Board D5.3 writes `"caveat": OCEAN_CAVEAT`; the added sentence is the producer's own caveat, kept so nothing it wrote is lost (as `extra.source_caveat` elsewhere), and the frontend shows either form. It awaits the PM's ratification (open item 8.23). The record's `prov.object_context` is `ocean_context`.

| Field | Unit | `time` | `src` |
|---|---|---|---|
| `depth_m` | m (positive down) | null (static layer) | `gebco_2026` |
| `dist_coast_km` | km | null | `natural_earth` |
| `dist_port_km` | km | null | `wpi` |
| `ship_presence_all`, `ship_presence_commercial`, `ship_presence_fishing`, `ship_presence_oilgas`, `ship_presence_passenger`, `ship_presence_leisure` | "presence as published, not a count" (value is bool: published value above 0) | null | `worldbank_density` |
| `sst_c` | degC | `sst_time` | `sst_source` as the table writes it (`mur`) |
| `sst_grad` | degC/km | `sst_time` | `sst_source` |
| `dist_front_km` | km | `sst_time` (fronts of the same day's SST) | `sst_source` |
| `chl_log10` | log10 mg m-3 | `chl_time` (a date) | `chl_dataset` as the table writes it (an ERDDAP dataset id) |
| `current_speed_ms` | m/s | `current_time` | `rtofs` |
| `mld_m` | m | `current_time` | `rtofs` |
| `wave_hs_m` | m | `wave_time` | `gfs_wave` |

Times are ISO 8601 UTC with `Z`; `chl_time` stays a date. A `src` that is not a registry key (`mur`, a chlorophyll dataset id) is the table's own dataset name; the frontend shows it next to the chip of the field's producer (`mur_sst`, `chl_dineof`). Values are rounded to 0.1 m (depth), 0.01 km, 0.001 degC, 0.00001 degC/km, 0.001 (chlorophyll, current) and 0.01 m. Coverage at the check: 360,013 rows (191,622 `viirs`, 162,386 `radar`, 6,005 `radar_detail`); every September contact and structure, every Ca Mau object and every light of the lean light file has a row; no live contact has one, so live contacts return null ("No ocean context for this object yet" in the product) until the table is rebuilt after a pass (R2-T6 follow-up).

**Contact sources**

| File and layer | Rows | Status | Build | Notes |
|---|---|---|---|---|
| `data/live/live_contacts.gpkg` `contacts_4326`, `contacts_utm49n` (EPSG:32649) | 8,573: `live_S1D_20261008T2258` 3,081 (all `no_coverage`), `live_S1C_20261010T1119` 780 (all `no_coverage`), `live_S1D_20261010T1032` 4,712 (33 `matched`, 1,947 `unmatched`, 2,732 `no_coverage`); 62 `match_ambiguous`, 77 with `review_note` | existing [17:51]; grows (the watcher adds each pass) | both (the research build shows the live passes too, `research_only` false) | all live passes combined; D1 order, then `darkvessel.live.schema.EXTRA_COLUMNS` (with the 1.3.0 `az_time_utc`, `match_dist_uncorr_m`, `az_shift_m`, `velocity_source`, `match_ambiguous`, `ambiguous_mmsi`, `match_alt_dist_m`), the diagnostics `pred_method`, `cnn_chip_valid_frac`, `ais_recorded_hours`, `row`, `col`, and `review_note`; also `ais_only_4326` (278, all Pearl River), `scenes_4326` (9), `about` (1) |
| `data/live/live_<mission>_<yyyymmddThhmm>.gpkg` | one per pass (3 at the check) | existing [17:51] | both | same layers; the backend reads the combined file |
| `data/live/live_summary.json` | 3 passes, 9 scenes | existing [17:51] | both | per pass counts, AIS window (`aoi`, `footprint`, `near_footprint`), CNN, length bins, match quality, recall by AIS length, hand check, `azimuth_check_by_scene` and `azimuth_check_note` (Pass, 3.6) |
| `data/live/<run_id>_weather.parquet` | 3 files, 8,573 rows (one per live contact) | existing [14:03] | both | `det_id, run_id, scene_id, scene_time_utc, wind_ms, ctt_k, deep_convection, gfs_cycle_utc, gfs_forecast_h, himawari_key, wind_source, cloud_source, fetched_utc` (R3-T1); joined on `det_id` |
| `data/live/<run_id>_review_matched.csv`, `_review_unmatched.csv` | 2 files (Pearl River): 33 and 4,679 rows, 77 graded (33 and 44), `reviewed_utc` the date 2026-10-10 | existing [17:51] | both | the hand check of R3-T7 (`grade`, `reason`, `reviewed_utc` and the evidence columns); the backend reads only `det_id` and `reviewed_utc` (the `field_prov` time of `review_note`, whose text names the table the row came from); the note itself comes from the contacts layer |
| `data/research/regional_identity.gpkg` `contacts_4326`, `contacts_utm49n`, `about` | 78,615 (matched 9,954, unmatched 68,470, no_coverage 191) | existing [13:51] | research | September run with GFW identity, D1 plus the 20 evidence columns; git-ignored (112 MB), rebuilt by `scripts/31_gfw_identity.py` |
| `data/research/regional_identity.parquet` | 78,615 | existing [13:51] | research | same rows and columns (52); the backend reads this one |
| `data/research/regional_identity_summary.json` | | existing [13:51] | research | rules, counts, hand-check sample |
| `data/detections_regional.gpkg` `detections_regional_4326` (and `_utm49n`) | 78,615 | existing [10-02 18:42] | both | September run, high 29,228, medium 49,387, `ais_status` `not_checked`; the open build uses it, the research build replaces it by `regional_identity` |
| `data/detections_regional_verified.gpkg` `detections_regional_verified_4326` (and `_utm49n`, `scenes`, `about`) | 78,615 | existing [05:55], R1-T6 | both | the same contacts with `cnn_score`, `cnn_vessel` for desktop GIS; the backend reads the parquet below instead |
| `data/structures_regional.gpkg` `structures_regional_4326` (and `_utm49n`) | 25,224 | existing [10-02 18:42] | both | fixed structures; Contacts with `confidence` `fixed` |
| `data/detections_ml.gpkg` `detections_verified_4326` (and `_utm48n`) | 6,005 | existing [10-02 16:34] | both | Ca Mau scene with CNN scores (low 4,936, medium 435, fixed 349, high 285) |
| `data/detections_baseline.gpkg` `processing_window_4326`, `about` | 1, 1 | existing [10-02 06:28] | both | Ca Mau scene window and detector settings |
| `data/ml/regional_cnn.parquet` (and `regional_cnn.json`) | 927,124 (78,615 contacts, 25,224 structures, 823,285 low objects) | existing [03:00] | both | `det_id, scene_id, mission, confidence, cnn_score, cnn_vessel, cnn_threshold, cnn_model_id, cnn_score_source, cnn_chip_valid_frac, chip_valid_frac_full, bg_vv_db, bg_vh_db, caveat`; takes precedence over `shared_cells_cnn.parquet`; the low-object run finished in round 2 (R2-T4, committed in f3acd89); low objects stay out of the regional product (section 4) |
| `data/ml/shared_cells_cnn.parquet` | 35,626 | existing [10-02 19:05] | both | superseded by `regional_cnn.parquet` for the product; kept for the papers |
| `data/weather_context.parquet` | 162,386 | existing [10-02 23:41] | both | `det_id, wind_ms, ctt_k, himawari_start, deep_convection` (September and Ca Mau objects; no live contact) |
| `data/ocean_context_objects.parquet` (and `ocean_context_objects.json`) | 360,013 (`viirs` 191,622, `radar` 162,386, `radar_detail` 6,005) | existing [01:11] | both | object context (above), `scripts/25_object_context.py` (R2-T6); loaded in the background after the server listens (section 5) |
| `data/optical_check.gpkg` `optical_check_4326` | 4,100 | existing [10-03 00:29] | both | sample only |
| Chips: `data/cache/chips/<det_id>.webp` | 1,714 | existing [16:50] (git-ignored cache) | both | written by the single-file builder (R3-T2, `app/build/scs_bundle/chips.py`) from `s1_grd`; the backend serves cached chips; `?fetch=1` still answers 404 `chip_builder_unavailable` (open item 8.18) |

### 3.2 Vessel

One AIS identity. `vessel_key`: `mmsi:<mmsi>` (aisstream) or `gfw:<vessel_id>` (GFW). In the research build a GFW vessel links to `mmsi:<mmsi>` when that MMSI was also heard by aisstream (`extra.aisstream_vessel_key`).

**Every vessel key a record links to resolves (contract 1.3.0).** The aisstream vessel snapshot (`ais_live.gpkg` `vessels_latest_4326`, 1,277 vessels last heard by 2026-10-08 23:37 UTC at the 16:24 UTC check) does not hold every MMSI the later live passes reference: of the 33 Pearl River matched MMSIs only 10 were in it. The backend therefore adds an aisstream **stub** row (`stub` true, `src` `aisstream`, `research_only` false) for every MMSI a live pass references and the snapshot lacks, in this order: matched contacts (identity strings, AIS class and hull length from the live contact row), AIS-only vessels of the pass (the same strings from `ais_only_4326`), nearest AIS vessels (MMSI only, `identity_source` "MMSI only (nearest AIS vessel)"). `flag` and `mid` come from the MMSI as for every aisstream row; `extra.stub_reason` (`matched`, `ais_only`, `nearest_ais`) and `extra.stub_note` say why the row is a stub. A snapshot row is never replaced. At the check the live passes add 139 stubs (open vessels 1,416; research 50,695). The crawl of 16:36 UTC followed 312 (open) and 313 (research) vessel links of live contacts and AIS-only vessels: all answered 200. The research build keeps its GFW `stub` rows for nearest-AIS vessels with no GFW record (contract 1.2.0).

Column names differ between the two GFW tables: `data/research/gfw_vessels.parquet` (written by `scripts/31_gfw_identity.py`) uses the D1-style names below (`mmsi`, `vessel_name`, `call_sign`), while `data/research/gfw_events_vessels.parquet` keeps the raw names of `darkvessel.ais.gfw.vessels_to_frame` (`ssvid`, `shipname`, `callsign`). The research column of this table is `gfw_vessels.parquet`; the mapping for `gfw_events_vessels.parquet` is in the last column.

| Field | Type | Unit | Null | Open source (`vessels_latest_4326`) | Research source (`gfw_vessels.parquet`) | `gfw_events_vessels.parquet` |
|---|---|---|---|---|---|---|
| `vessel_key` | str | | no | `mmsi:` + `mmsi` | `gfw:` + `vessel_id` | `gfw:` + `vessel_id` |
| `mmsi` | str (digits) | | yes | `mmsi` | `mmsi` | `ssvid` |
| `mid` | int | | yes | `mid` | first 3 digits of `mmsi` | first 3 digits of `ssvid` |
| `flag` | str | | yes | ITU country of `mid` (`mid_itu`) | `flag` (GFW, as published) | `flag` |
| `name` | str | | yes | `name` | `vessel_name` | `shipname` |
| `call_sign` | str | | yes | `callsign` | `call_sign` | `callsign` |
| `imo` | str | | yes | `imo` | `imo` | `imo` |
| `ais_class` | enum `A`, `B` | | yes | `ais_class` | null | null |
| `ship_type` | str | | yes | `ship_type_label` (code in `ship_type`) | `ship_type` (the type used for the contact identity; `shiptype` registry and `gfw_geartype` presence values kept in `extra`) | `shiptype` |
| `gear_type` | str | | yes | null | `geartype` | `geartype` |
| `identity_kind` | enum `vessel`, `gear`, `unknown` | | yes | null | `identity_kind` | null |
| `length_m`, `width_m` | float | m | yes | `length_m`, `width_m` | `length_m` (registry), null | `length_m`, null |
| `length_ais_m` | float | m | yes | `length_m` | `length_ais_m` | null |
| `tonnage_gt` | float | GT | yes | null | `tonnage_gt` | `tonnage_gt` |
| `identity_source` | str | | yes | "aisstream static message" | `identity_source` | "GFW vessels API" |
| `destination`, `eta` | str | | yes | `destination`, `eta` (self-reported text) | null | null |
| `first_seen_utc`, `last_seen_utc` | datetime | | yes | `first_seen_utc`, `last_seen_utc` | `transmission_from`, `transmission_to` | `transmission_from`, `transmission_to` |
| `static_seen_utc` | datetime | | yes | `static_seen_utc` | null | null |
| `n_positions` | int | count | yes | `n_positions` | `ais_positions` | `ais_positions` |
| `n_messages` | int | count | yes | null | `ais_messages` | `ais_messages` |
| `last_lon`, `last_lat` | float | deg | yes | point geometry, `lon`, `lat` | null | null |
| `sog_kn`, `cog_deg`, `heading` | float | kn, deg, deg | yes | last report | null | null |
| `nav_status_label` | str | | yes | `nav_status_label` | null | null |
| `in_aoi`, `ever_in_aoi` | bool | | yes | `in_aoi`, `ever_in_aoi` | null | null |
| `gear_beacon_like` | bool | | yes | `gear_beacon_like` (MMSI pattern of nets and buoys) | null | null |
| `registry_sources`, `registry_records` | str, int | | yes | null | `registry_sources`, `registry_records` (float in the file, cast to int) | same names |
| `dataset_version` | str | | yes | null | `dataset_version` | `dataset_version` |
| `stub` | bool | | no | false; true for a live-pass MMSI the snapshot lacks (above) | false; true for a nearest-AIS vessel that is not in `gfw_vessels.parquet` (only `vessel_key`, `mmsi`, `name` from `regional_identity` `nearest_ais_vessel_id`, `nearest_ais_mmsi`, `nearest_ais_name`) | false |
| `contacts_matched` | list[str] | | no | det_ids of Contacts with this `mmsi` | det_ids with this `gfw_vessel_id` | det_ids with this `gfw_vessel_id` |
| `identity_note` | str | | no | "Identity fields are self-reported by the transponder; they can be wrong, reused or spoofed." | same, plus "as published by Global Fishing Watch" | same as research |
| `identity_label` | str | | yes | 1.3.0: "live AIS relayed by aisstream.io; terms UNVERIFIED" (board D4.7) on every aisstream row, stubs included | null (the research label is in `caveat`) | null |
| `research_only`, `caveat`, `src`, `prov` | | | no | false, `PRODUCT_CAVEAT`, `aisstream` (file column `ais_note` goes to `extra.source_caveat`) | true, `PRODUCT_CAVEAT` plus the research line, `gfw_vessels` (`use`, `licence` columns feed the registry entry) | as research |

**Track** (sub-resource of Vessel, one record; decision 8.7): a GeoJSON FeatureCollection of ordered positions, feature properties `{t (datetime), sog_kn, cog_deg, msg_type}`, with `gaps` = list of `{from_utc, to_utc, minutes, note, overlaps_recorder_gap}` over 6 h, each `note` "An AIS gap is not proof of intent."; `overlaps_recorder_gap` is true when an hour of the gap has no recorder file (the recorder, not the vessel, was silent). The collection carries `n_points`, `simplified` (at most `max_points`, default 2,000), `note`, `gap_note`, the envelope fields with `caveat` = `PRODUCT_CAVEAT`, and `track_caveat` (the record caveat, with the research line in the research build); single positions carry no caveat. The aisstream hour files feed only the Track (section 5, Reloads).

| File and layer | Rows | Status | Build |
|---|---|---|---|
| `data/ais_live.gpkg` `vessels_latest_4326` (and `_utm49n`) | 1,277 [12:20], last heard by 2026-10-08 23:37 UTC (the snapshot is not refreshed with the recording; open item 8.17) | existing | both (aisstream rows; plus the live stubs above) |
| `data/ais_live.gpkg` `tracks_4326` (and `_utm49n`) | 1,094 | existing [12:20] | both; line per MMSI with `start_utc`, `end_utc` (used by the single-file page) |
| `data/ais_live.gpkg` `about`, `s1_next_passes_4326`; `data/ais_live_summary.json` | 1, 96 | existing [12:20] | both; recording period, gaps, reach, terms note. Recorder outages are listed in `docs/live_pass.md` and in the summary's `recording_gaps_over_10_min` |
| `data/cache/ais/aisstream/positions/<yyyymmdd>/<HH>.parquet` | 35 hourly files at the 18:03 check | existing [18:03] (git-ignored) | both, local app only: `mmsi, timestamp, lon, lat, sog_kn, cog_deg, heading, nav_status, msg_type, msg_id, ais_class, ship_name`; the Track of every aisstream vessel, stubs included |
| `data/cache/ais/aisstream/static/<yyyymmdd>.parquet` | 3 daily files | existing [16:24] (git-ignored) | both, local app only: identity history (E12, not served yet) |
| `data/research/gfw_vessels.parquet` | 8,924 (identity_kind: vessel 8,535, gear 286, unknown 103) | existing [13:51] | research; "GFW identity records of the vessel ids used by regional_identity": all 8,484 matched vessel ids and 5,915 of the 8,322 distinct nearest-AIS vessel ids (the other 2,407 become `stub` rows); columns `vessel_id, mmsi, vessel_name, call_sign, imo, flag, ship_type, gfw_geartype, length_ais_m, identity_source, geartype, shiptype, length_m, tonnage_gt, registry_sources, registry_records, ais_messages, ais_positions, transmission_from, transmission_to, dataset_version, identity_kind, use, licence` (24) |
| `data/research/gfw_events_vessels.parquet` | 39,999 | existing [06:43] | research; identities of the vessels in GFW events: `vessel_id, ssvid, shipname, flag, callsign, imo, geartype, shiptype, length_m, tonnage_gt, registry_sources, registry_records, ais_messages, ais_positions, transmission_from, transmission_to, dataset_version, use, licence` (19) |

For the 9,954 matched research contacts, the identity fields in `regional_identity.parquet` equal the `gfw_vessels.parquet` row of their `gfw_vessel_id` in every one of `mmsi, vessel_name, call_sign, flag, ship_type, imo, length_ais_m, identity_source` (checked 13:55 UTC). This is what lets the bundle store identity once per vessel (section 6.2).

### 3.3 Light and Light site

**Light** (`light_id`), from `data/viirs_lights.gpkg` `viirs_lights_4326` (existing, 48,692 rows, all `lit_vessel_candidate`: 32,103 clear, 16,589 under cloud), build both.

| Field | Type | Unit | Null | Source column |
|---|---|---|---|---|
| `light_id` | str | | no | `light_id` = `<SPP, N20 or N21>_<time_utc as yyyymmddThhmmss>_<6-digit index>` (holds for all 48,692 rows, checked 13:55 UTC; the bundle rebuilds it from `satellite`, `time_utc` and the index) |
| `satellite` | enum `S-NPP`, `NOAA-20`, `NOAA-21` | | no | `satellite` |
| `time_utc` | datetime | | no | `time_utc` |
| `night` | str (date) | | no | `night` (local evening date, UTC+7) |
| `lon`, `lat` | float | deg | no | `lon`, `lat` |
| `radiance_nw` | float | nW cm-2 sr-1 | no | `radiance_nw` |
| `spike_nw`, `isolation` | float | nW cm-2 sr-1, ratio | yes | `spike_nw`, `isolation` |
| `quality` | enum `clear`, `under_cloud` | | no | `quality` |
| `class` | str | | no | `class` |
| `nights_seen_500m` | int | count | no | `nights_seen_500m` |
| `clear_nights_cell` | int | count | no | `clear_nights_cell` |
| `moon_illum_pct` | float | % | yes | `moon_illum_pct` |
| `satlas_infra_m` | float | m | yes | `satlas_infra_m` |
| `s1_passes_90d` | int | count | no | `s1_passes_90d` |
| `site_id` | str | | yes | nearest `viirs_sites_4326` within 500 m (backend join) |
| `contacts_2km_same_night` | list[str] | | no | backend join to Contacts |
| `cell_id`, `caveat`, `src`, `prov` | | | no | `caveat` = `PRODUCT_CAVEAT` plus the light caveat (decision 8.15; the file's own `caveat` column goes to `extra.source_caveat`); `src` = `viirs_dnb` |
| `object_context` | object or null | | yes | 1.3.0, board D5.3: the ocean context at the light from the `viirs` rows of `data/ocean_context_objects.parquet` (section 3.1, Object context); full records only. Every light of the lean file has a row |

**Light site** (`site_id`), from `viirs_sites_4326` (existing, 2,129): `site_id, lon, lat, n_lights, nights, radiance_med_nw, radiance_max_nw, satlas_infra_m, nights_seen_max, s1_passes_90d, likely` (1,802 "other recurring light: platform, flare, island or navigation light, or anchorage"; 327 "platform or turbine (Satlas point within 1 km)"), `caveat`.

**Lights an L7 lead cites (contract 1.3.0).** The lean file holds the lit vessel candidates of 10 of the 27 nights (its `about` `lean_subset`); an L7 lead cites the 20 brightest lights of its cell on any of the 27 nights. The backend therefore reads, from `data/viirs_lights_all.gpkg` `viirs_lights_4326` (191,622 lights, every night; existing [10-03 01:35]; git-ignored, local app only), exactly the lights that lead evidence cites and the lean file lacks (20,204 of the 25,427 lights cited by the 2,137 open L7 leads at the check), prepared as the lean lights (time, night, cell, nearest site within 500 m). `GET /lights/{light_id}` and the lead evidence previews resolve them; their records carry `extra.light_file` "data/viirs_lights_all.gpkg" and `extra.light_file_note`, and the file's extra columns (`background_nw`, `snr`, `granule`, ...) under `extra`. They are not in `GET /lights`, the lights layer or the light count; `/meta` `counts.lights_evidence` gives their number. They load in the background after the server listens (section 5). The crawl of 16:36 UTC resolved every light of 15 L7 leads (300) in both builds, and an in-process check resolved all 25,427.

Also existing: `viirs_granules_4326` (904 granule footprints, for coverage), `viirs_nights` (27 nights), `about`. Read only for the lights leads cite (above): `data/viirs_lights_all.gpkg` (148.7 MB, all lights; local app may read it on demand, never the bundle).

### 3.4 Event

One observation event (MDA brief section 2, E1 to E17). Radar contacts and lights are their own object types; the Event type covers the behaviour and correlation events.

| Field | Type | Unit | Null | Meaning |
|---|---|---|---|---|
| `event_id` | str | | no | `<code>-<source>-<hash>`, stable |
| `code` | enum `E6`, `E7`, `E8`, `E9`, `E10`, `E11`, `E12`, `E13`, `E14`, `E15`, `E16` | | no | MDA brief code |
| `event_type` | enum `AIS_MATCH`, `NO_AIS_MATCH`, `AIS_SILENCE`, `AIS_RETURN`, `POSITION_JUMP`, `ENCOUNTER`, `CLOSE_APPROACH`, `LOITERING`, `SLOW_ACTIVITY`, `IDENTITY_CHANGE`, `AREA_ENTRY`, `PORT_VISIT`, `ACTIVITY_ANOMALY`, `LOOK_STATUS` | | no | name |
| `start_utc`, `end_utc` | datetime | | no, yes | instant events have `end_utc` null |
| `duration_h` | float | h | yes | |
| `lon`, `lat` | float | deg | no | event position (mean position for encounters) |
| `geometry` | geom | | yes | track segment or area when relevant |
| `mmsi`, `vessel_keys` | list[str] | | no | vessels involved |
| `det_ids`, `light_ids`, `cell_ids` | list[str] | | no | other objects involved |
| `params` | map | | no | thresholds as applied (for example `{"max_dist_m": 500, "min_hours": 2, "max_median_kn": 2, "min_coast_km": 10}`) |
| `rule_text` | str | | no | the rule in words |
| `grade` | str | | yes | E8: `silence` 6 to 12 h, `extended` 12 to 72 h, `prolonged` over 72 h |
| `source` | enum `aisstream`, `gfw`, `app` | | no | |
| `research_only` | bool | | no | |
| `caveat` | str | | no | `PRODUCT_CAVEAT` plus the type caveat (for example E10: "lawful transshipment, bunkering, pilot and supply transfers look identical"; E8: "An AIS gap is not proof of intent.") |
| `src`, `prov` | | | no | |

| File and layer | Status | Build |
|---|---|---|
| `data/events_open.gpkg` `events_4326` (and `_utm49n`, `about`), computed from the aisstream cache | missing; round 4 (board decision of 2026-10-10 14:40); `/events` answers an empty list with a note | open |
| `data/research/gfw_events.gpkg` layers `gfw_gaps_4326` (5), `gfw_gaps_off_to_on_4326` (5 lines), `gfw_encounters_4326` (13,516), `gfw_loitering_4326` (12,509 cells of 0.1 degree with event counts), `gfw_port_visits_4326` (410 anchorages with visit counts), each with `_utm49n`, and `about` (4) | existing [06:43], window 2026-09-01 to 2026-10-08 | research |
| `data/research/gfw_events_gaps.parquet` (5), `gfw_events_encounters.parquet` (13,516), `gfw_events_loitering_part{1,2,3}of3.parquet` (118,180 + 118,179 + 118,179 = 354,538), `gfw_events_port_visits_part{1,2}of2.parquet` (121,413 + 121,412 = 242,825), `gfw_events_about.csv` | existing [06:43] | research; every event, with `event_id, type, start, end, lat, lon, vessel_id, vessel_name, ssvid, flag, vessel_type`, distances to shore and port, `eez`, the type's own columns (for gaps `gap_intentional_disabling`, `gap_duration_h`, `gap_implied_speed_kn`, `gap_positions_12h_before_sat`, `gap_positions_per_day_sat_reception`; for encounters `encounter_*`), `duration_h`, `use`, `overlaps_window_only`, `starts_in_window` |
| `data/research/radar_vs_gfw.parquet` (and `radar_vs_gfw.json`) | existing [06:43], 83,472 rows (`darkvessel.ais.gfw_compare`) | research; Cell context only |

Mapping of GFW events onto Event: `event_id` from `event_id`; `type` `gap` to `AIS_SILENCE` (code E8, GFW rule: gaps of at least 12 h starting at least 50 nm from shore, `gap_intentional_disabling` is GFW's model flag, never shown as intent), `encounter` to `ENCOUNTER` (E10), `loitering` to `LOITERING` (E11), `port_visit` to `PORT_VISIT` (E14); `start_utc`, `end_utc` from `start`, `end`; `vessel_keys` = `gfw:` + `vessel_id` (and `encounter_vessel_id`); type columns go to `params`; `source` `gfw`; `research_only` true.

E6 and E7 are not stored as files: the backend derives them from Contact `ais_status` for the timeline.

### 3.5 Lead

One bundle for review (spec section 4.1).

| Field | Type | Unit | Null | Meaning |
|---|---|---|---|---|
| `lead_id` | str | | no | `<type>-<primary object id>` (for example `L1-<det_id>`), stable |
| `lead_type` | enum `L1` to `L8` | | no | spec section 4.1 |
| `title` | str | | no | plain words, no verdict ("Unmatched radar contact in AIS reach, 38 m, Gulf of Thailand"), as the lead builder wrote it |
| `state` | enum `new`, `reviewing`, `closed_explained`, `closed_unexplained`, `closed_false_alarm` | | no | latest decision, or `new` |
| `reason` | str | | yes | the picklist value of the latest closing decision |
| `priority` | int | 0 to 100 | no | review priority (not a risk score) |
| `priority_band` | enum `low`, `medium`, `high` | | no | 0 to 33, 34 to 66, 67 to 100 |
| `factors` | list[{`factor`, `value`, `points`, `max_points`, `source`}] | | no | every factor with its points; the sum (clipped to 0 to 100) is `priority`; `factor` is a code (board D5.1), `value` the producer's text |
| `priority_model_id` | str | | no | weights version |
| `calibrated` | bool | | no | false until calibrated against owner labels |
| `primary_type`, `primary_id` | str | | no | object the lead is about (`contact`, `vessel`, `cell`, `light`) |
| `evidence` | list[{`type`, `id`, `role`}] | | no | linked objects (`role`: `primary`, `pass`, `nearest_ais`, `same_night_light`, `ais_behaviour`, `persistence`, `weather`, `cell`, `light`, `recurring_site`, as the leads `about` `evidence_roles` lists them); `GET /leads/{lead_id}` adds a `preview` to each (null when the object is not in this build). Every `light` of an L7 lead resolves to a Light record (3.3) |
| `lon`, `lat` | float | deg | no | |
| `time_utc` | datetime | | no | |
| `region_box` | str | | yes | reporting box name or `other` |
| `next_look_utc` | datetime | | yes | next planned Sentinel-1 pass over the spot |
| `lawful_explanations` | list[str] | | no | codes pre-listed for the type (board D5.1), unchanged from the file |
| `change_indicators` | list[str] | | no | codes of what would change the lead (late AIS match, second look, optical view; board D5.1), unchanged from the file |
| `history` | list[Decision] | | no | from the decision log |
| `ais_status`, `cnn_score`, `length_est_m`, `pass_id` | | | yes | queue fields from the primary contact, as the leads file holds them (decision 8.4); null for a lead without a contact primary |
| `research_only`, `caveat`, `src`, `prov` | | | no | `research_only` per row (D5.5); `caveat` is `PRODUCT_CAVEAT` (section 1.1), plus the research line in the research build; the file's own caveat goes to `extra.source_caveat` |

`GET /leads` returns summaries: every field above except `extra`, cached per loaded leads file, at most 20,000 per request (decision 8.4). `GET /leads/{lead_id}` returns the full record with `extra` (the file's other columns, for example `n_lights`, `nights`, `next_look_pass`) and evidence previews. When `leads_4326` holds no evidence for a lead and `data/leads_open_detail.gpkg` exists (board D6.3), the open backend reads that lead's evidence from the detail file's `lead_evidence` table and names the file in `extra.evidence_source`; while `leads_4326` holds the evidence (as at the 18:03 UTC re-check: every one of the 2,531 open leads), the detail file is not opened.

**Decision** (append-only log entry): `{lead_id, time_utc, user, from_state, to_state, reason, note, build, app_version}`.

| File | Status | Build |
|---|---|---|
| `data/leads_open.gpkg` `leads_4326`, `about` | existing [17:34]: 2,531 leads (L1 394, all from the Pearl River pass; L7 2,137) | open; the backend reads `leads_4326` (every column above) |
| `data/leads_open_detail.gpkg` `leads_utm49n`, `lead_evidence`, `about` (board D6.3) | existing [17:34]: 2,531 and 29,646 rows | open; read only for leads whose `leads_4326` row has no evidence (above) |
| `data/research/leads_research.parquet` | existing [17:34]: 14,261 | research; the backend reads the parquet first (decision 8.11) |
| `data/research/leads_research.gpkg` (same layers, git-ignored, about 85 MB) | existing [17:34] | research; fallback when the parquet is missing |
| `data/labels/lead_decisions.jsonl` (one Decision per line, append-only, committed) | missing until the first decision (the backend creates it) | open; the research build writes `data/research/lead_decisions.jsonl` |
| `data/labels/contact_labels.csv` | missing until the first label | both; `POST /contacts/{det_id}/label` appends |
| `data/labels/owner_2026-10.csv` (contact labels `det_id,label,user,time_utc`) | missing, owner action | both |

### 3.6 Pass

One Sentinel-1 pass (past, in progress or upcoming).

| Field | Type | Unit | Null | Source |
|---|---|---|---|---|
| `pass_id` | str | | no | upcoming and plan: `pass_group` (for example `S1D_R128_20261006T1102`); processed live: `run_id`; regional: mission plus start of the first scene |
| `mission` | enum `S1C`, `S1D` | | no | |
| `relative_orbit` | int | | yes | |
| `pass_dir` | enum `ASCENDING`, `DESCENDING` | | yes | |
| `start_utc`, `stop_utc` | datetime | | no | |
| `status` | enum `past`, `in_progress`, `upcoming` | | no | at the file's `generated_utc` |
| `sources` | list[enum `esa_plan`, `repeat_cycle`, `processed`] | | no | a `repeat_cycle` prediction is not ESA's plan |
| `footprint` | geom | | yes | |
| `aoi_overlap_km2` | float | km2 | yes | |
| `aoi_parts` | list[str] | | yes | Natural Earth sea names |
| `scenes` | list[str] | | no | product ids (processed passes) |
| `processed` | bool | | no | |
| `n_contacts` | map status to int | count | yes | processed passes |
| `ais_aoi_positions`, `ais_aoi_mmsi`, `ais_footprint_positions`, `ais_footprint_mmsi`, `ais_near_footprint_mmsi` | int | count | yes | live passes: positions summed and MMSI as the maximum over the pass's scenes (`scenes_4326`); the Pass page shows the footprint counts first, because the AOI counts only say the feed was up |
| `ais_heard_share` | float | share | yes | plan file |
| `n_ais_only` | int | count | yes | 1.3.0, processed live passes: AIS vessels placed at the scene time inside the footprint that no contact matched (layer `ais_only_4326` of the live file); null for plan and regional passes and when the layer is missing |
| `ais_only` | list[AIS-only vessel] | | yes | 1.3.0: those vessels (table below), on tested sea first, then by AIS length, longest first. Filled only by `GET /passes/{pass_id}`; `GET /passes` and exports of a list return null here and the count in `n_ais_only` |
| `azimuth_check` | object | | yes | 1.3.0, live passes: `{"by_scene": [...], "note"}` from `data/live/live_summary.json` `passes.<run_id>.azimuth_check_by_scene` and `azimuth_check_note`, one entry per scene with the producer's keys (`min_shift_m`, `vessels`, `vessels_compared`, `uncorrected_median_nearest_m`, `corrected_median_nearest_m`, `sign_flipped_median_nearest_m`, the `*_within_200m` and `closest_*` counts, `median_predicted_shift_m`) plus `scene_id`: for AIS vessels with a predicted shift of at least 150 m, the median distance to the nearest contact without the shift, with it and with its sign flipped (independent of the assignment). The producer (`darkvessel.live.outputs`) writes no scene id in an entry and leaves out scenes with no shifted vessel, so `scene_id` is the entry's own when it has one, else the pass's `scene_ids` entry at the same position when every scene has an entry (both lists in the producer's scene order), else null. Pearl River: both scenes have entries, so both carry their scene id. Open item 8.24 asks the producer to write the scene id into each entry |
| `identity_label` | str | | yes | 1.3.0: the board D4.7 aisstream label on every processed live pass; null otherwise |
| `note` | str | | yes | the coverage result in words when most contacts are `no_coverage` (spec 4.7) |
| `caveat`, `src`, `prov`, `field_prov` | | | no | `field_prov` (1.3.0) gives `ais_only` (src `aisstream`, time: the pass start) and `azimuth_check` (src `det_live`, time: the live summary's `generated_utc`) |

**AIS-only vessel** (an element of `ais_only`; every field from `ais_only_4326`, written by `darkvessel.live`): `mmsi` (digits), `vessel_key` (`mmsi:<mmsi>`, always resolvable: section 3.2), `vessel_name`, `call_sign`, `imo`, `flag` (ITU wording of the MID country), `ship_type`, `ais_class`, `length_ais_m`, `sog_kn`, `lon`, `lat` (the vessel placed at the scene time), `scene_id`, `pred_method` (`interp` between bracketing reports, `extrap` by dead reckoning), `pred_dt_s`, `n_reports`, `on_tested_sea` (the placed position lies on sea the detector tested: not in the shore buffer, on land cells or outside the AOI), `dist_coast_km`, `nearest_object_m` and `nearest_object_class` (the nearest radar object of any class, low included), `ambiguous_det_id` (the contact it was held back with by the ambiguity rule), `oversized_det_id` (the oversized return it was paired with), `identity_source`, `identity_label` (board D4.7). An AIS-only vessel is one the radar did not report as a contact; it is not evidence about the vessel (spec 4.7). Pearl River at the check: 278 vessels, 49 on tested sea, 51 with `ambiguous_det_id`.

| File and layer | Rows | Status | Build |
|---|---|---|---|
| `data/s1_next_passes.json` (`passes`, `pass_groups`) | 96 passes, 43 groups (generated 2026-10-10 12:20 UTC; window 2026-10-07 12:20 to 2026-10-22 12:20 UTC); rewritten every 6 h by the AIS watchdog | existing | both |
| `data/ais_live.gpkg` `s1_next_passes_4326` (and `_utm49n`) | 96 [12:20] | existing | both (footprints) |
| `data/detections_regional.gpkg` `scenes_processed_4326` (and `_utm49n`) | 119 | existing | both |
| `data/live/live_contacts.gpkg` `scenes_4326` (and `_utm49n`) | 9 [17:51], grows | existing | both; `product_id, run_id, mission, start_utc, stop_utc, scene_time_utc, orbit_abs, orbit_rel, pass_dir, aoi_overlap_km2, tested_km2, ...` with per-scene counts by class and AIS status (`n_contacts`, `n_matched`, `n_unmatched`, `n_no_coverage`, `n_dark_leads`, `n_ais_only`, `n_cnn_scored`, `n_cnn_vessel`), the AIS counts `ais_aoi_positions`, `ais_aoi_mmsi` (heard anywhere in the AOI during the window: the feed was up), `ais_footprint_positions`, `ais_footprint_mmsi` (inside the scene footprint), `ais_near_footprint_mmsi` (within 0.3 degree: what the matcher sees), and `ais_recorded_hours`, `status` |
| `data/research/gfw_presence_passes.parquet` | 91,328 | existing [06:47] | research; GFW AIS presence per pass hour and vessel: `pass_id, lon, lat, hour_ts, hours, vessel_id, mmsi, ship_name, call_sign, imo, flag, gfw_vessel_type, gfw_geartype, use, licence, caveat` |
| `data/s1_footprints.gpkg` `s1_footprints_4326` | 1,042 (90-day archive to 2026-10-02) | existing | both (coverage history) |
| `data/live/live_contacts.gpkg` `ais_only_4326` (and `_utm49n`) | 278 [17:51] | existing | both; `darkvessel.live.schema.AIS_ONLY_COLUMNS`; feeds `ais_only` and `n_ais_only` |
| `data/live/live_summary.json` | 3 passes [17:51] | existing | both; feeds `azimuth_check` and `extra.summary` |

### 3.7 Cell context

One cell of the 0.25 degree model grid (`darkvessel.ocean.grid.model_grid`, origin 99.0E 24.0N, 109 rows x 94 columns, row 0 north; decision 8.1). `cell_id` = `r<row>c<col>`, for example `r56c23`. Cells in the product: the 5,116 rows of `data/ocean_static_cells.parquet` (cells that hold an AOI fine cell or whose centre is in the AOI).

**Static sea fields**, `data/ocean_static_cells.parquet`, existing [10-09 14:02], 5,116 rows, 35 columns, build both. Column meanings are the producer's (`data/ocean_static_summary.json` `table_columns`).

| Field | Type | Unit | Null | Meaning |
|---|---|---|---|---|
| `row`, `col` | int | | no | model-grid indices; join key to `ocean_daily_cells.parquet` |
| `lon`, `lat` | float | deg | no | cell centre |
| `region` | str | | no | reporting box of the cell centre (`darkvessel.ocean.grid.REPORTING_BOXES`), `other` outside every box; not a boundary. The API names it `region_box` |
| `aoi_centre` | bool | | no | cell centre inside the AOI polygon |
| `aoi_share` | float | share 0 to 1 | no | share of the cell's 625 fine cells (0.01 degree) inside the AOI |
| `n_sea` | int | count of 625 | no | AOI sea fine cells |
| `sea_share` | float | share | no | `n_sea / 625` |
| `sea_area_km2` | float | km2 | no | area of those fine cells |
| `depth_mean_m`, `depth_median_m`, `depth_min_m`, `depth_max_m`, `depth_std_m` | float | m, positive down | yes (no sea) | GEBCO_2026 over the AOI sea fine cells |
| `share_shallower_50m`, `share_shallower_200m`, `share_shelf_break_150_250m` | float | share | yes | depth bands |
| `slope_mean_m_per_km` | float | m/km | yes | mean depth gradient |
| `dist_coast_km`, `dist_coast_min_km` | float | km | yes | mean and minimum distance to the Natural Earth 10 m coast |
| `dist_port_km`, `dist_port_min_km` | float | km | yes | mean and minimum distance to the nearest major port (WPI size L or M, or any Natural Earth port) |
| `ship_presence_share_all`, `ship_presence_share_fishing`, `ship_presence_share_commercial`, `ship_presence_share_oilgas`, `ship_presence_share_passenger`, `ship_presence_share_leisure` | float | share 0 to 1 | yes | share of the cell's AOI sea 0.01 degree cells whose World Bank and IMF value is above 0 for that vessel type, January 2015 to February 2021: presence only, never a count (board D4.3; decision 8.2). The Cell record also carries `shipping_note` "values as published, not counts; presence only" |
| `marineregions_mrgid` | int | | yes | MRGID of the Marine Regions v12 polygon covering most of the cell's sea, as published |
| `marineregions_geoname`, `marineregions_pol_type` | str | | yes | geoname and pol_type, as published |
| `marineregions_share` | float | share | yes | share of the cell's sea in that polygon |
| `marineregions_n` | int | count | yes | distinct Marine Regions polygons in the cell's sea |
| `marineregions_overlap_share` | float | share | yes | share of the cell's sea covered by two or more published polygons (published overlaps, reported, not resolved); inside `eez` like the other `marineregions_*` fields |

**The `marineregions_*` fields are EEZ attributes.** The API returns them under a separate `eez` object of the Cell record, and the bundle keeps them in a separate `eez_attrs` block of the `cells` part. The Cell page and every tooltip show them only while the EEZ layer is on, under the heading "As published by Marine Regions" with the statement of section 3.7 Vector context; they are never a filter, a lead factor or a model feature. With the EEZ layer off they are not rendered at all.

**Other cell fields**

| Group | Fields (unit) | Source file | Status | Build |
|---|---|---|---|---|
| Key | `cell_id`, `row`, `col`, `lon`, `lat` (cell centre, deg), `region_box` | derived | | both |
| Nightly sea | `nightly` object (decision 8.5): the fields of the newest night, or of `?night=`; `nights_available` lists the nights; per `night`: `sst_mean_c`, `sst_sd_c`, `sst_grad_mean` (degC/km), `front_share`, `dist_front_km`, `chl_log10_mean` (log10 mg m-3), `chl_valid_share`, `ssh_m`, `ssh_anom_m`, `ssh_grad`, `current_speed_ms`, `mld_m`, `sbl_m`, `wave_hs_m`, `wind_ms`, `moon_illum_pct`, valid-time fields (`sst_date`, `chl_date`, `rtofs_valid_utc`, `wave_valid_utc`, `wind_valid_utc`) | `data/ocean_daily_cells.parquet` | existing [01:07], 128,979 rows (27 nights) | both |
| Per pass | `pass_context` object, only with `?scene_id=` (decision 8.5): the sea fields at that pass's time | `data/ocean_radar_pass_cells.parquet` | existing [01:07], 4,407 rows | both |
| AIS reach | `ais_reach_share`, `ais_reach_mmsi` | `data/outputs/small/ais_reach_share_4326.tif`, `ais_reach_mmsi_4326.tif` (0.25 degree) | existing | open |
| Look status | `look_prob_1d`, `look_prob_7d`, `look_prob_30d`, `passes_90d` | `data/outputs/small/s1_look_prob_{1d,7d,30d}_4326.tif`, `s1_passes_4326.tif` (0.05 degree, cell mean) | existing | both |
| Object context | not a Cell field: the object context belongs to Contacts and Lights (3.1, 3.3). The Cell record's `object_context` is always null in 1.3.0 (the cell's own static and nightly fields are its context) | | | both |
| GFW comparison | `n_ours`, `n_gfw`, `n_gfw_matched`, `n_gfw_unmatched`, `n_pair` per cell-hour (`date`, `hour`, `cx`, `cy`, `res_deg`) | `data/research/radar_vs_gfw.parquet` | existing [06:43], 83,472 rows | research |
| GFW SAR and AIS presence | `detections`, `matched` per 0.1 degree cell-hour; `neural_vessel_type` counts; AIS presence `hours`, `n_vessels` per cell and day; fishing `hours` per cell, day and gear | `data/research/gfw_sar_detections.parquet` (59,888), `gfw_sar_detections_by_neural_type.parquet` (60,110), `gfw_sar_matched_by_vessel.parquet` (20,573), `gfw_presence_daily.parquet` (557,348), `gfw_fishing_effort_daily.parquet` (144,979); rasters `gfw_ais_presence_hours_4326.tif`, `gfw_fishing_hours_4326.tif`, `gfw_sar_matched_density_4326.tif`, `gfw_sar_unmatched_density_4326.tif` (each with `_utm49n`); `gfw_summary.json` | existing [06:42] | research |
| Expected activity | `expected_activity` (board D5.4, below) | `data/expected_activity.parquet` (and `.json`, `_anomalies.gpkg`), `scripts/34_expected_activity.py` | existing [01:04], 133,386 rows (86,900 tested: VIIRS 82,751, radar 4,149) | both |
| Caveat | `caveat` = `PRODUCT_CAVEAT` plus `darkvessel.ocean.grid.OCEAN_CAVEAT` ("Ocean and weather layers describe the sea, not what any vessel does. ...") | | | both |

**Expected activity (board D5.4).** `expected_activity` of a Cell is null when the cell has no tested row, or:

```json
{"model_id": "expected_activity_v1_5bae587d", "caveat": "<OCEAN_CAVEAT, then the anomaly sentence>",
 "targets": {"radar": [{"unit_id": "S1C_IW_GRDH_...", "night": "2026-09-20", "...": "..."}],
             "viirs": [{"unit_id": "2026-09-30", "night": "2026-09-30", "time_start_utc": "2026-09-30T17:18:01Z",
                        "time_end_utc": "2026-09-30T19:44:36Z", "tested": true, "observed": 0.0, "expected": 2.1161,
                        "z": -1.455, "q_bh": 1.0, "flag": "none", "flag_robust": "none", "calm": true, "exposure_km2": 885.94}]}}
```

Rows come from `data/expected_activity.parquet` by the cell's `row` and `col`: tested rows only (`tested` true), newest first (by `time_start_utc`, then `night`, then `unit_id`, descending), every row with exactly the 13 keys `unit_id, night, time_start_utc, time_end_utc, tested, observed, expected, z, q_bh, flag, flag_robust, calm, exposure_km2` in this order. `unit_id` is the night (VIIRS) or the Sentinel-1 scene id (radar); `flag` and `flag_robust` are `none`, `high` or `low` as published (`flag_robust`: the negative binomial check, `docs/ocean_context.md` section 9); `expected` is rounded to 0.0001, `z` to 0.001, `exposure_km2` to 0.01. `targets` holds every target of the file (`radar`, `viirs`); a target with no tested row in the cell is an empty list. `model_id` and `caveat` come from the parquet's schema metadata (`model_id`, `caveat`), else from `data/expected_activity.json`; `caveat` starts with `OCEAN_CAVEAT` and adds the anomaly sentence ("An activity anomaly is a difference between a count of detections in a cell and a model's expectation ... A lead for review only."). Expected values, z and flags are model output (judgment, spec 4.7). The Cell's `prov.expected_activity` and `field_prov.expected_activity` name the `expected_activity` source; each row carries its own times. At the check 4,777 cells hold at least one tested row.

**Raster layers** (map overlays; `data/outputs/small/`, all existing, each with a `_utm49n` twin): `depth_m`, `dist_coast_km`, `dist_port_km`, `ship_density_{all,commercial,fishing,oilgas,passenger,leisure}`, `sst_mean_c`, `sst_showcase_c`, `sst_grad_mean_c_per_km`, `front_freq` (0.01 degree); `chl_mean_mg_m3`, `chl_valid_share`, `current_speed_mean_ms`, `mld_mean_m`, `ssh_grad_mean`, `s1_look_prob_{1d,7d,30d}`, `s1_passes` (0.05 degree); `wave_hs_mean_m`, `wind_mean_ms`, `ais_reach_share`, `ais_reach_mmsi` (0.25 degree); `vessel_density_regional`, `viirs_lit_density`, `viirs_lit_density_clear`. Registry entry per raster: `{name, unit, resolution_deg, valid_period, colormap, vmin, vmax, src, licence, default_on: false}`.

**Vector context** (all existing, both builds unless noted): `data/aoi.gpkg` `aoi_4326`; `data/eez_marineregions.gpkg` [07:37] `eez_4326` (12 polygons), `eez_boundaries_4326` (77 lines), `about` (2): off by default; shown with the statement "Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, CC BY 4.0, doi:10.14284/632. In this sea many zones overlap or are disputed; the source marks them. This product takes no position on any boundary or claim." plus the Marine Regions disclaimer, as quoted in `data/ocean_static_summary.json` `eez_statement` and re-read at https://www.marineregions.org/disclaimer.php ("VLIZ expresses no opinion about the legal state neither of any country, territory or area nor concerning its delimitation, frontier or borders. The data has no legal value whatsoever."); `data/ocean_context.gpkg` [07:37] `depth_contours_4326` (989), `ports_4326` (236); `data/ocean_fronts.gpkg` [01:08] `fronts_4326` (2,823 lines of the showcase night 2026-09-29); land from Natural Earth 10 m (`darkvessel.aoi.natural_earth_land`); reporting boxes from `darkvessel.ocean.grid.REPORTING_BOXES` (the six boxes of `scripts/21_viirs_regions.py`).

## 4. Required behaviour of producers and consumers

1. The backend loads every existing file at start (in parallel; the object context, the L7 evidence lights and the research events load in the background once the server listens, section 5) and re-reads a file when its modification time changes. A missing file is reported in `/meta` as `missing` and its views show "not built yet".
2. The open build never opens `data/research/`: the catalog refuses any path under it before anything is opened, and no research-only file spec is registered in the open build (contract test b; the 1.3.0 specs `live_ais_only`, `live_weather`, `live_review`, `lights_all`, `leads_open_detail`, `object_context` and `expected_activity` all lie outside it, test `test_open_build_new_specs_stay_outside_research`). The research build reads `data/research/regional_identity.*` instead of `detections_regional_4326` for the September run.
3. Field names in API responses and in the bundle are exactly the names in section 3. Unknown extra columns in source files are passed through under `extra` (a map), never renamed.
4. Contacts with `confidence` `low` appear only in the Ca Mau view (as in the demo page) and in exports; regional and live low objects stay out of the product.
5. Every record has `caveat` equal to `PRODUCT_CAVEAT` (section 1.1), followed by the type caveat where section 3 names one. Every record of the research build also carries the research line in `caveat`; `research_only` keeps its per-row meaning, true only on a row that uses GFW data (board D5.5; decision 8.6). Sub-objects of a record (`object_context`, `expected_activity`, AIS-only vessels, evidence previews) carry no product caveat of their own; `object_context` and `expected_activity` carry the ocean caveat.
6. Identity strings of a matched contact are the contact file's own. When they differ from the vessel record the contact links to (an identity change between the match and the latest static message), the contact keeps its own strings in the API and goes into the bundle's `records` (section 6.2), and the Vessel page shows the identity history.
7. Every vessel key and light id a record links to resolves to a record of the same build: live vessel keys through the aisstream stubs (3.2), L7 lights through the evidence lights (3.3). Lead evidence of type `contact`, `vessel`, `light`, `light_site` (or `site`, `recurring_site`), `event`, `pass` and `cell` gets a `preview`; type `weather` (the contact's own weather fields) has no record and its preview is null, as is the preview of an object outside the build.
8. The backend never removes, rewrites or regrades a match: `match_quality`, `review_note` and the identity strings are the producer's (board D6.2).

**Contract tests (round 2, `tests/app/`, offline, synthetic fixtures):** (a) the Contact model's first 32 field names equal the D1 list of `docs/PROJECT_BOARD.md` in order (31 in the open build, without `gfw_vessel_id`), and equal `darkvessel.live.schema.D1_COLUMNS` and `darkvessel.ais.gfw_identity.D1_COLUMNS`; (b) with `BUILD=open` the catalog raises on any path under `data/research/` and no response contains a field starting with `gfw_`; (c) every record of every endpoint has a `caveat` that starts with `PRODUCT_CAVEAT`, and `PRODUCT_CAVEAT` equals `darkvessel.config.PRODUCT_CAVEAT` once that constant exists; (d) `ais_status` only takes the four values of 3.1; (e) a decision POST appends exactly one line and rejects a closing state without its reason; (f) the bundle builder applies each part's drop rule when the part exceeds its budget (section 6.3), fails when a part is still over its budget after its drops, and fails above 15,000,000 bytes in total; (g) with `BUILD=open`, `/meta` and the bundle's `meta` hold no source entry with `research_only` true; (h) `GET /api/v1/cells/at?lon=..&lat=..` returns the containing cell, not a 404 for a cell named `at` (route order, section 5); (i) the Cell record carries `marineregions_*` only inside its `eez` object.

**Contract tests added in 1.3.0** (`tests/app/test_api_v13.py`, offline, synthetic fixtures in `tests/app/synthetic.py`): (j) `object_context` of Contacts and Lights has exactly the D5.3 keys and the 16 fields in order, every field `{value, unit, time, src}`, presence as bool with its label, the ocean caveat; null where the table has no row (live contacts, a September contact without a row, a light without a row) and when the table is missing; (k) `expected_activity` has exactly the D5.4 keys, tested rows only, newest first, every row the 13 keys in order; an empty list for a target without rows; null for a cell without rows; (l) the live identification fields of a matched, an ambiguous and a `no_coverage` contact, null outside live passes, with `field_prov` only for fields that hold a value; (m) the live weather comes from the pass's sidecar and reloads when the sidecar changes; (n) a Pass record lists its AIS-only vessels (on tested sea first) and its azimuth check, lists carry the count only, and a pass without the layer carries null; (o) every light of an L7 lead resolves, `viirs_lights_all.gpkg` is read only for cited lights, and the light list stays the lean file; (p) the leads detail file fills missing evidence and is not opened while `leads_4326` holds the evidence; (q) lead codes pass through unchanged; `research_only` per row with the research line on every research record; (r) every vessel key of a live record resolves (aisstream stubs) and every aisstream Vessel carries the D4.7 label; (s) a held background load is released by the request that needs it. The endpoint crawl of tests (b) and (c) also covers an evidence light and cells with and without expected activity.

## 5. Backend API (local app)

FastAPI on `127.0.0.1:8750` (port proposed), base path `/api/v1`. Started by `make serve` with `BUILD=open` (default) or `BUILD=research`. JSON is UTF-8. `docs_url` and `redoc_url` are off (their pages load from a CDN); `/openapi.json` is on and is generated from the same pydantic models as this contract.

**Envelope.** Every JSON response is an object:

```json
{
  "contract_version": "1.3.0",
  "build": "open",
  "build_label": "Open build. Open-licensed sources and live AIS relayed by aisstream.io.",
  "caveat": "<PRODUCT_CAVEAT, section 1.1>",
  "generated_utc": "2026-10-10T00:00:00Z",
  "item": { "...": "one record, with its own caveat" },
  "items": [ { "...": "records, each with its own caveat" } ],
  "total": 0, "limit": 100, "offset": 0
}
```

`caveat` is `PRODUCT_CAVEAT` (section 1.1). `item` for single objects, `items` plus `total`, `limit`, `offset` for lists. Errors: HTTP 4xx or 5xx with `{"error": {"code": "not_found", "message": "..."}, "caveat": "...", "build": "..."}`. Research responses add `"research_label": "Research build, noncommercial, CC BY-NC 4.0"` and `"attribution": "Powered by Global Fishing Watch."`.

**Endpoints**

| Method and path | Query parameters | Returns |
|---|---|---|
| `GET /api/v1/meta` | | build, labels, caveat (`PRODUCT_CAVEAT` in both builds, as in the envelope; the research build marks the meta by `research_label` and `attribution`, not by a research line in this caveat), `sources` registry (section 2), `files` (key, path, layer, status existing or missing, rows, mtime), `git_hash`, `app_version`, `priority_model_id`, `counts` per type (also `contacts_<view>`, `contacts_<ais_status>`, `structures`, `track_positions`, `decisions`, `chips`; 1.3.0: `lights_evidence`, `context_objects`, `expected_activity_rows`, each absent while its loader is still loading), `ais_recording`, `live_rules` (the live file's `about` rules: `dark_lead_rule`, `no_coverage_rule`, `match_quality_rule`, `ais_status_rule`, `distance_gate`, and in 1.3.0 `ais_window`, `azimuth_correction`, `ambiguity`, `review_note`, `ais_only_layer`, `weather`), `data_credit`, `load_seconds`, `loading` (loaders still loading in the background) and `reload_error` (the last failed reload, the previous data still served; decision 8.16) |
| `GET /api/v1/layers/{name}.cols` | `t0`, `t1`, `bbox` (`w,s,e,n`) | bulk columnar layer in the bundle format (section 6.2); `name` in `contacts` (`high` and `medium` contacts), `structures` (every `fixed` object), `lights`, `sites`, `vessels` (decision 8.10) |
| `GET /api/v1/contacts` | `t0`, `t1`, `bbox`, `run_id`, `view`, `ais_status` (comma list), `confidence`, `cnn_min`, `cnn_vessel`, `mmsi`, `pass_id`, `sort` (`-acq_utc` default, `length_est_m`, `cnn_score`), `limit` (default 100, max 1000), `offset` | Contact summaries: D1 fields plus `view`, `pass_id`, `lead_ids` |
| `GET /api/v1/contacts/{det_id}` | | full Contact with extensions, `object_context`, `src`, `prov`, `field_prov` |
| `GET /api/v1/contacts/{det_id}/chip.webp` | `fetch` (`1` asks for a missing chip to be built) | `image/webp`, 130 x 64 px, when cached; else 404 with the error envelope (`chip_not_cached`, or `chip_builder_unavailable` with `fetch=1`: the backend does not build chips yet, open item 8.18) |
| `POST /api/v1/contacts/{det_id}/label` | body `{label, user, note}`; `label` is vessel, structure, clutter or unsure | the stored label; appended to `data/labels/contact_labels.csv` |
| `GET /api/v1/vessels` | `q` (name, call sign, MMSI, IMO), `in_aoi`, `ais_class`, `limit`, `offset` | Vessel summaries |
| `GET /api/v1/vessels/{vessel_key}` | | full Vessel |
| `GET /api/v1/vessels/{vessel_key}/track` | `t0`, `t1`, `max_points` (default 2000) | the Track (3.2): `{"type":"FeatureCollection"}` of positions with `t`, plus `gaps`, `track_caveat` and the envelope fields |
| `GET /api/v1/lights` | `t0`, `t1`, `bbox`, `night`, `quality`, `limit`, `offset` | Light summaries |
| `GET /api/v1/lights/{light_id}` | | full Light with `object_context`; also the lights an L7 lead cites outside the lean file (3.3) |
| `GET /api/v1/sites/{site_id}` | | Light site |
| `GET /api/v1/events` | `t0`, `t1`, `bbox`, `code`, `event_type`, `vessel_key`, `det_id`, `limit`, `offset` | Events |
| `GET /api/v1/events/{event_id}` | | Event |
| `GET /api/v1/leads` | `state` (comma list, default `new,reviewing`), `lead_type`, `min_priority`, `region_box`, `pass_id`, `t0`, `t1`, `sort` (`-priority` default; `time_utc`, `lead_id`), `limit` (default 100, max 20,000), `offset` | Lead summaries (every Lead field except `extra`; decision 8.4); `note` when the leads file is missing |
| `GET /api/v1/leads/{lead_id}` | | full Lead with `history` and resolved `evidence` previews |
| `POST /api/v1/leads/{lead_id}/decision` | body `{to_state, reason, note, user}` | updated Lead; one line appended to the decision log; 409 when the transition is not allowed; 422 when a required reason or note is missing |
| `GET /api/v1/passes` | `status`, `t0`, `t1`, `mission`, `footprint` (default true), `limit` (default 1,000, max 5,000), `offset` | Passes; `ais_only` null, `n_ais_only` the count |
| `GET /api/v1/passes/{pass_id}` | | Pass with its AIS-only vessels and azimuth check (3.6) |
| `GET /api/v1/cells/at` | `lon`, `lat` | Cell context of the containing cell (declare before the next route) |
| `GET /api/v1/cells/{cell_id}` | `night`, `scene_id` | Cell context with `nightly`, `pass_context` (with `scene_id`), `expected_activity` (D5.4); `marineregions_*` only inside an `eez` object |
| `GET /api/v1/rasters` | | raster registry |
| `GET /api/v1/rasters/{name}.webp` | `theme` (`dark`, `light`) | colour-mapped overlay (EPSG:4326 bounds in the `X-Bounds` header and in the registry) |
| `GET /api/v1/rasters/{name}/value` | `lon`, `lat` | `{value, unit, valid_period, src, note}`; shipping density: `value` 1.0 or 0.0 with `presence` and `value_as_published` (decision 8.16) |
| `GET /api/v1/geo/{name}.geojson` | | `land`, `aoi`, `reporting_boxes`, `eez` (as published), `eez_boundaries`, `depth_contours`, `ports`, `fronts`, `footprints` (window) |
| `GET /api/v1/search` | `q`, `limit` (default 20) | Omnibar results as `items` `[{type, id, label, sublabel, lon, lat, score, caveat}]` (`type`: contact, vessel, light, lead, event, pass, cell, site, point), with `interpretation` (coordinates read, spec section 5) in the envelope (decision 8.9) |
| `GET /api/v1/timeline` | `t0`, `t1`, `bin` (`pass`, `hour`, `day`) | `item` with `passes`, `contactsByPass`, `aisHours`, `aisGaps`, `viirsNights`, `events` (with `events_total`, `events_shown`), `upcoming` (the frontend's names; decision 8.8) |
| `GET /api/v1/export/{format}` | `format` in `gpkg`, `geojson`, `csv`, `html`; `type`, `ids` (comma list) or the list filters of that type | file download; every export carries caveat, build and licences (spec section 8) |
| `GET /` and static paths | | the built frontend |

**Route order.** FastAPI evaluates path operations in the order they are declared, so a fixed path must be declared before a path with a parameter at the same position ("you need to make sure that the path for /users/me is declared before the one for /users/{user_id}", FastAPI path parameters tutorial). Here that means `/cells/at` before `/cells/{cell_id}`; `/layers/{name}.cols`, `/rasters/{name}.webp` and `/rasters/{name}/value` do not collide. Contract test (h) guards it.

**Background loading and reloads (1.3.0).** The server answers once the main loaders are in. Three loaders then load in one background thread, held until one second after the server listens (`serve.py`), in this order: the object context and expected activity (`context`), the L7 evidence lights (`lights_extra`), the research events. A request that needs one of them releases the hold and waits for it (for example the first Contact, Light or Cell record), so no record is ever answered without data the files hold; `/meta` never waits and names them in `loading`. A file whose modification time changes is re-read with the loaders that depend on it (live weather sidecars and hand-check tables with the contacts; the AIS-only layer with the passes and the vessels; the leads detail file with the leads; a leads or lights reload also reloads the evidence lights); the aisstream hour files feed only the Track and reload in the background at most every 300 s. A reload builds the new data on the side and swaps it in at once; a failed reload keeps serving the previous data and is reported in `reload_error`.

**Measured on the real data** (2026-10-10, 16:32 to 16:40 UTC, `nice -n 10`, both builds started together as in the R2-T3 runs, load average 1.2 to 3.0 from other tasks; scratch scripts of R3-T10): process start to the first `/meta` 200, three runs alternated with the committed 1.2.0 backend on the same data and machine: 1.3.0 open 7.07, 6.62 and 5.31 s, research 7.69, 8.31 and 7.80 s; 1.2.0 open 6.55, 6.16 and 6.16 s, research 8.87, 8.22 and 7.72 s (R2-T3 measured open 5.6 to 6.2 s and research 7.3 to 7.5 s on 2026-10-09, with 3,081 live contacts instead of 8,573). The full research queue (`GET /leads?limit=20000`, 14,261 leads, 45.8 MB) polled every 5 s for 3 minutes with the recorder writing: median 0.73 s, 0.62 to 0.95 s (31 requests); open queue median 0.13 s; one Pearl River Contact 0.03 s; the Pearl River Pass with its 278 AIS-only vessels 0.01 s. The crawl of every route (open 1,002 requests, research 1,021) found 0 problems: every answer 200 except the documented 404s (a contact without a cached chip, with and without `fetch=1`, an unknown id, `/docs`, `/redoc`) and the 403 of the EEZ export, every record with the caveat (and the research line in the research build), no GFW name or `data/research/` path in an open response. **Fix round** (18:03 to 18:40 UTC, same scripts, load average 1.0 to 4.5, a third backend of another task running on port 8781 from 18:07): first `/meta` with the final code, three runs alternated with 1.2.0: 1.3.0 open 5.40, 5.32 and 5.52 s, research 7.70, 7.75 and 8.08 s; 1.2.0 open 5.85, 5.03 and 5.42 s, research 7.76, 7.20 and 8.59 s; all inside 20 % of the R2-T3 upper bounds (open 7.44 s, research 9.0 s). Parsing `himawari_start` (36 distinct values in 162,386 rows) adds 0.015 s. The full research queue, alternated with the 1.2.0 backend every 3 s for 2 minutes: 1.3.0 median 0.68 s (0.63 to 0.92 s, 27 requests), 1.2.0 median 0.74 s (0.65 to 0.91 s); no request over 1 s. Both crawls (the R3-T10 crawl, now also checking that every `field_prov` and `object_context` time is ISO 8601 with `Z` or a date, 3,589 open and 3,611 research times; and the reviewer's crawl of 1,315 and 1,333 requests) found 0 problems in either build, apart from the reviewer's crawl reading the `/meta` item as a record without the research line (the `/meta` caveat is `PRODUCT_CAVEAT` in both builds since 1.2.0, section 5).

## 6. Single-file data bundle

### 6.1 Layout

The Vite build writes `app/frontend/dist-single/index.html` with all JavaScript and CSS inlined and no data. The bundle builder (`app/build/`) then injects one element per part before `</body>`:

```html
<script type="application/json" id="scs-part-meta">{...}</script>
<script type="application/json" id="scs-part-contacts">{...}</script>
... one per part ...
```

Parts: `meta`, `contacts`, `chips`, `lights`, `vessels`, `events`, `leads`, `passes`, `cells`, `geo`, `rasters`, `camau`. The frontend's embedded adapter parses a part on first use and answers the same queries as the HTTP adapter, returning the same record shapes (section 5, without the envelope's paging). `meta` holds the envelope fields, the source registry, the build time, the list of parts with their byte sizes, and the list of anything dropped to meet the budget.

### 6.2 Encodings

The sizes in sections 6.2 and 6.3 are the 1.2.0 measurements (2026-10-09, 13:55 UTC), before the Pearl River pass and the open L1 leads (394 at the 18:03 UTC re-check); the round 3 measurements are in `app/build/README.md` (open item 8.19).

Bulk parts (`contacts`, `lights`, `vessels`, and the point lists inside `camau`) are columnar:

```json
{"type": "contacts", "n": 78615,
 "columns": {
   "lat": {"t": "i32", "s": 100000, "b": "<base64 little-endian>"},
   "lon": {"t": "i32", "s": 100000, "b": "..."},
   "confidence": {"t": "dict8", "dict": ["high", "medium", "fixed", "low"], "b": "..."},
   "acq_utc": {"t": "time", "e": "2026-09-01T00:00:00Z", "b": "..."},
   "det_id": {"t": "detid", "pfx": ["S1C_20260920T104816", "..."], "w": [5], "p": "<u16 prefix index>", "q": "<u32 index>"},
   "cnn_score": {"t": "u8", "s": 250, "na": 255, "b": "..."},
   "vessel_ref": {"t": "ref16", "to": "vessels", "b": "<u16 row index into the vessels part>"},
   "caveat": {"t": "const", "v": "<PRODUCT_CAVEAT>"}
 },
 "records": {"<det_id>": {"...": "full record for the subset with detail"}}}
```

| `t` | Storage | Value |
|---|---|---|
| `i32`, `u32`, `i16`, `u16`, `u8` | little-endian typed array in base64 `b` | `raw / s` (`s` default 1); `raw == na` means null |
| `f32` | float32 little-endian | NaN means null |
| `bool8` | u8 | 0 false, 1 true, 255 null |
| `dict8`, `dict16` | u8 or u16 index into `dict` | `na` (255 or 65535) means null |
| `time` | u32 seconds since `e` | 4294967295 means null |
| `detid` | prefix index plus zero-padded index | `pfx[p] + "_" + pad(q, w)` |
| `lightid` | u32 index `q` | `<SPP, N20 or N21 from satellite>_<time_utc as yyyymmddThhmmss>_<pad(q, 6)>` |
| `ref16` | u16 row index into the part named in `to` | 65535 means null |
| `const` | one value in `v` | the same value for every row (for example `caveat`, `research_only` in the open build) |
| `str` | JSON array in `v` | for small parts and per-vessel identity strings |
| `geom` | `kind` (`point`, `line`, `polygon`); `xy`: i32 lon, lat pairs x 10,000, interleaved, base64; `ring`: u32 start of each line or ring in the vertex list; `feat`: u32 start of each feature in the ring list; `props`: columnar, as above | coordinates to 0.0001 degree (about 11 m), after simplification for display (section 6.2, Geo) |

The builder picks the narrowest integer type that holds a column's range at its scale (for example `nearest_ais_dt_s` as `i16` when every value is within 32,767 s) and records it in `t`; the frontend reads `t`, never assumes it.

**Identity by reference.** A contact's identity is stored once per vessel, not per contact. The `contacts` part carries `vessel_ref` (matched vessel) and `nearest_ref` (nearest AIS vessel) as `ref16` columns into the `vessels` part, which holds the identity strings (`vessel_key`, `mmsi`, `name`, `call_sign`, `imo`, `flag`, `ship_type`, `gear_type`, `identity_kind`, `length_m`, `length_ais_m`, `tonnage_gt`, `identity_source`, `registry_sources`, first and last seen, `stub`). The embedded adapter resolves the references, so a Contact from the bundle has the same D1 fields as one from the API. Open build: references point to `mmsi:<mmsi>` vessels from `vessels_latest_4326`. Research build: to `gfw:<vessel_id>` vessels from `gfw_vessels.parquet` plus 2,407 `stub` rows for nearest-AIS vessels that file does not hold (11,331 vessel rows in all, under the u16 limit of 65,535). Measured on the real files: the 9,954 matched research contacts' identity strings take 3.82 MB as JSON records, against 1.27 MB for the whole research vessel table by reference.

Fields not in the bulk columns (long text, lists, `prov`, evidence columns) are in `records`, keyed by id, only for: every lead's evidence objects, every contact with a chip, and every matched contact whose identity strings differ from its vessel row (behaviour rule 6; none in the research file at the 13:55 UTC check). Matched contacts are not in `records` for their identity: that comes from `vessel_ref`. A record holds only the fields that are not bulk columns, with nulls left out: measured 343 bytes per research September record and 472 per live record (500-row samples, 13:55 UTC). Other objects show their bulk fields with a note "full record in the local app and the GeoPackage".

Chips (`chips` part): `{"<det_id>": "data:image/webp;base64,..."}`. Each chip is one WebP image of 130 x 64 px: VV 64 x 64 px, a 2 px gap, VH 64 x 64 px, at the 10 m GRD pixel spacing (640 m), grayscale, quality 70. **Chips are capped by bytes, not by count:** the builder adds chips in the selection order (primary contacts of leads by priority, matched live contacts, CNN-accepted unmatched live contacts, the label queue sample) until the next chip would push the part past its byte budget, and lists the number embedded and left out in `meta`. Size per chip entry (key, data-URI prefix and base64), measured on 400 real 64 x 64 px GRD windows (the CNN training chips in `data/chips/*.npz`, Sentinel-1A and 1B, 10 m pixels, from 51 scenes, 2nd percentile to maximum stretch per polarisation): mean 4,006 bytes, median 4,390, 90th percentile 4,594. The earlier figure of 2,804 bytes came from upscaled 98 x 48 px demo JPEG chips and is too low. Sentinel-1C/1D chips from the live pass have not been measured yet; the byte cap makes the count follow whatever they weigh.

Rasters (`rasters` part): WebP overlays at 0.05 degree (about 462 x 540 px) with `{name, bounds, unit, colormap, vmin, vmax, src}`; measured 1 to 47 KB each. `camau`: the Ca Mau radar backdrop as WebP plus the scene grid transform and its 6,005 objects (low objects as positions only).

Geo (`geo` part), in the `geom` encoding, measured 13:55 UTC: land (Natural Earth 10 m clipped to the AOI frame plus 1 degree, simplified at 0.01 degree, 9,386 vertices) 0.10 MB; AOI outline (0.005 degree) 0.01 MB; EEZ boundary lines (`eez_boundaries_4326`, the published Marine Regions line layer "Maritime Boundaries (v12, world, 2023)", 77 lines with their `line_type` as published, 0.005 degree, 726 vertices) 0.01 MB; depth contours (989 lines at 50, 200 and 1,000 m) 0.17 MB; fronts of the showcase night (2,823 lines) 0.13 MB; the 90-day footprint archive (1,042) 0.06 MB; ports (236) and the six reporting boxes, under 0.01 MB. Total 0.49 MB. The same layers as GeoJSON text came to about 1.05 MB, which is why `geo` uses `geom`. The EEZ polygons (`eez_4326`, 44,722 vertices at 0.01 degree, 0.51 MB) are not embedded: the map draws the published boundary lines with no fill (spec section 6.2), and the cell-level polygon attributes are in the `cells` part (`eez_attrs`). The local app serves both layers. Every simplified layer says "simplified for display; the GeoPackage holds the published geometry".

Events (`events` part). Open: the aisstream events once they exist (`data/events_open.gpkg`, pending). Research, measured 13:55 UTC: the 5 GFW gaps as records (under 0.01 MB); the 952 encounters that lie within 10 km and 24 h of a September contact, that is the encounters behind `n_gfw_encounters_10km_24h`, as columnar rows with their `event_id` and both vessels' id, name and flag (0.14 MB); the other 12,564 encounters as counts and hours per 0.1 degree cell (1,770 cells, 0.02 MB); loitering as its 12,509 cells (0.27 MB); port visits as their 410 anchorages (0.03 MB). Total about 0.47 MB. All 13,516 encounters as columnar rows would take about 1.5 MB (ids 0.50 MB and vessel dictionaries 0.58 MB included). Single events outside the subset stay in the local app. The builder selects the encounter subset with the same rule that the producer used for the evidence column.

Leads (`leads` part) are columnar too: `lead_type` dict8; `primary` u32 row index into the part of the primary object (`contacts`, `lights` or `cells`); `lead_id` rebuilt as `<lead_type>-<primary id>`; `priority` u8; one u8 column of points per priority factor (spec section 4.1); `state` dict8; `region_box` dict8; `next_look_utc` time. The frontend builds `title` from the type, the radar length and the box. `evidence` and `history` go into `records` for every lead that is not `new` and for the highest-priority leads, while the part budget allows. A lead takes 17 bytes raw, about 23 in base64. The 12,124 research L1 candidates under the spec section 4.1 rule (unmatched, CNN score at least 0.5, `high`, wind below 12 m/s, no deep convection; counted 13:55 UTC) would take about 0.28 MB. The open build has no L1 candidate yet: no live contact is `unmatched`, and the September rows are `not_checked`. When leads exceed the budget, the part keeps them in priority order and counts the rest in `meta.dropped`.

### 6.3 Size budget

Limit 16 MB for the page. Hard cap in the build: **15,000,000 bytes**; the build prints the size of every part. Budgets are per part in bytes of the serialised element; MB here means 1,000,000 bytes, the unit of the cap. The "Measured" column is the size of that part encoded as section 6.2 specifies, computed from the real files at 07:00 UTC on 2026-10-09 and again at 13:55 UTC after the live pass rerun and the research identity rebuild (method in the stack decision, section 5). The budget adds room for growth: live passes and the AIS recording.

| Part | Open: measured | Open: budget | Research: measured | Research: budget | Basis |
|---|---|---|---|---|---|
| Frontend JS and CSS | 1.77 MB | 2.0 MB | 1.77 MB | 2.0 MB | shell plus Leaflet before app code |
| `meta` | | 0.05 MB | | 0.05 MB | registry, envelope, part sizes, drops |
| `contacts` | 3.35 MB | 4.4 MB | 5.14 MB | 5.4 MB | open: September 78,615 rows `not_checked` with CNN (2.73 MB), live 3,081 (0.11 MB; a pass of about 3,000 contacts adds about 0.11 MB), Ca Mau 6,005 (0.21 MB), structures 25,224 (0.30 MB). Research: September 78,615 with D1 numeric fields plus `vessel_ref` and `nearest_ref` (4.72 MB), live 0.11, structures 0.30; no Ca Mau. The budget also holds `records` for the chip contacts (about 0.24 MB open at 500 x 472 bytes, 0.09 MB research at 250 x 343 bytes); lead evidence records come out of the same headroom. Room before a drop: open about 0.81 MB (about 7 more passes of 3,000 contacts), research about 0.17 MB (one more pass, then rule 5 frees 2.50 MB) |
| `chips` | | 2.0 MB | | 1.0 MB | by bytes: about 500 (open) and 250 (research) chips at 4,006 bytes |
| `lights` | 1.36 MB | 1.4 MB | 1.36 MB | 1.4 MB | 48,692 lights: positions, time, radiance, quality, satellite, nights seen (1.10 MB) plus the `lightid` index (0.26 MB); 2,129 sites |
| `vessels` (with simplified tracks) | 0.17 MB | 0.6 MB | 1.44 MB | 1.6 MB | open: 1,277 aisstream vessels (0.11 MB) plus 1,094 tracks simplified at 0.002 degree (3,122 vertices, 0.06 MB); research adds 11,331 GFW vessel rows with identity strings (1.27 MB) |
| `events` | pending | 0.3 MB | 0.47 MB | 0.6 MB | open: aisstream events, not built yet. Research: 5 gaps, the 952 encounters within 10 km and 24 h of a September contact as rows, the other 12,564 as 1,770 cell counts, 12,509 loitering cells, 410 port anchorages (section 6.2, Events) |
| `leads` | 0 (no candidate yet) | 0.4 MB | about 0.28 MB (estimate) | 0.4 MB | not built yet; columnar (section 6.2, Leads); research estimate from the 12,124 L1 candidates; kept in priority order when over budget |
| `passes` | 0.05 MB | 0.1 MB | 0.05 MB | 0.1 MB | 88 planned passes with footprints (0.03 MB), 119 regional scenes (0.02 MB), live scenes; measured as GeoJSON, less in `geom` |
| `cells` | 0.53 MB | 0.6 MB | 0.53 MB | 0.6 MB | 5,116 cells: static fields (0.35 MB), `eez_attrs` (0.08 MB), one night of daily fields (0.10 MB) |
| `geo` | 0.49 MB | 0.6 MB | 0.49 MB | 0.6 MB | land, AOI, EEZ boundary lines (no polygons), boxes, footprint archive, contours, ports, fronts, in the `geom` encoding (section 6.2, Geo) |
| `rasters` | | 0.5 MB | | 0.6 MB | about 15 overlays at 1 to 47 KB; research adds the GFW rasters |
| `camau` | 0.49 MB | 0.6 MB | | 0 (left out) | radar backdrop, 2,562 x 2,600 px WebP q75 from `data/outputs/small/sigma0_vv_db_utm48n_40m_u8.tif` (the demo page carried it as a 1.56 MB JPEG), plus the scene grid transform; the 6,005 objects are counted in `contacts` |
| **Planned total** | | **13.55 MB** | | **14.35 MB** | cap 15.0 MB, limit 16 MB |

Totals: open 2.0 + 0.05 + 4.4 + 2.0 + 1.4 + 0.6 + 0.3 + 0.4 + 0.1 + 0.6 + 0.6 + 0.5 + 0.6 = 13.55 MB; research 2.0 + 0.05 + 5.4 + 1.0 + 1.4 + 1.6 + 0.6 + 0.4 + 0.1 + 0.6 + 0.6 + 0.6 + 0 = 14.35 MB. Version 1.1.0 planned 13.05 and 14.45 MB: its `geo` budget (0.7 MB) and research `events` budget (0.6 MB with every encounter as a row) had not been measured and would not have held.

**Drops.** Each part has its own drop rules, applied in table order when that part exceeds its budget (`contacts`: rule 4 or 5 first, then rule 7); the total cap then applies the same rules in the order below until the page fits. Every drop is listed in `meta.dropped` and in the Info tab.

| Order | Part | Rule | Size of the lever (measured) |
|---|---|---|---|
| 1 | `chips` | already capped by bytes; under total pressure the builder lowers the chip budget in 0.25 MB steps, from the end of the selection order | about 62 chips per step |
| 2 | `lights` | lights under cloud (16,589 of 48,692) become positions only | 1.36 to 1.08 MB |
| 3 | `camau` (open) | backdrop at half resolution (1,281 x 1,300 px) | 0.49 to 0.11 MB |
| 4 | `contacts` (open) | September `medium` contacts with `cnn_vessel` false (44,273 rows) leave the bulk columns; their counts stay in the cells part | September rows 2.73 to 1.19 MB |
| 5 | `contacts` (research) | September `medium` contacts with `cnn_vessel` false that are not `matched` (41,583 rows, with the CNN join of section 3.1) leave the bulk columns; their counts stay in the cells part. Every matched contact stays: identification is the core | September rows 4.72 to 2.23 MB (37,032 rows kept) |
| 6 | `vessels` (research) | `stub` rows no longer referenced by a remaining contact | 469 of 2,407 stubs after step 5 |
| 7 | `contacts` (both) | whole live passes leave the bulk columns, oldest first; passes with no `matched` or `unmatched` contact go before passes with one, and a pass with a `matched` contact goes last (identification is the core). Their per-status counts stay in the `passes` part, and the Pass page says "contacts of this pass are in the local app" | about 0.11 MB per pass of 3,000 contacts |

If a part is still over its budget after its rules, or the total is still over 15,000,000 bytes, the build fails.

The budgets and measurements above are those of 1.2.0 (2026-10-09). The bundle builder measured larger needs on the round 3 data (`app/build/README.md`: open `contacts` 6.0 MB for every contact, research `leads` 0.9 MB, `cells` 0.75 MB, `passes` 0.15 MB, research `vessels` 1.8 MB; chips about 3.3 KB on Sentinel-1C/1D). Those are proposals, not part of 1.3.0: open item 8.19.

### 6.4 Part layout (normative, board D5.2)

The part layout below is normative for contract section 6: the bundle builder (`app/build/`, R3-T12) writes exactly this layout, and the frontend's embedded adapter (`app/frontend/src/adapters/embedded.ts`) is the reference reader. The 16 readings are copied from `app/frontend/README.md`, section "Bundle readings 1 to 16", as R3-T11 left it at 16:30 UTC on 2026-10-10 (reading 11 replaces its round 2 text; readings 13, 15 and 16 carry the 1.3.0 fields). A later change to a reading is a contract change (version bump).

1. Each part is one JSON object `{"type", "n", "columns", "records", "prov", "note"}`. `prov` at part level is the default field-to-source map for every row; a record's `prov` overrides it.
2. `detid.w` is a list parallel to `pfx` (one width per prefix) so 5-digit regional and live ids and 4-digit Ca Mau ids share a column; a one-element list applies to every prefix.
3. `lightid` carries `q` (u32) and `w` (6); the id is rebuilt from the `satellite` and `time_utc` columns of the same part.
4. `dict8`/`dict16`, `bool8`, `time` and `ref16` columns carry an explicit `na`; integer columns carry `na` and `s` (omitted when 1); the frontend reads `t` and never assumes a type.
5. `vessels` holds its simplified tracks as `tracks`, a `geom` block of kind `line` with props `vessel_ref` (ref16), `start_utc`, `end_utc`, `n_positions`. Per-position times and gap detection stay in the local app. `stub` and `identity_source` are per-row columns.
6. `leads` columns: `lead_type`, `primary_type` (dict8 over `contacts`, `lights`, `cells`, `vessels`), `primary` (u32 row of that part), `priority`, `state`, `reason`, `region_box`, `time_utc`, `next_look_utc`, `lon`, `lat`, `priority_model_id` (const or dict), one u8 column per factor; a part-level `factors` list names each factor column with `max_points` and `source`; `lawful_explanations` and `change_indicators` by type at part level (a list of codes, or a map of code to sentence); `records[lead_id]` holds `title`, `evidence`, `history`, `lawful_explanations`, `change_indicators`, `prov` and, optionally, `factors`: the full factor list with each factor's value text, which wins over the point columns (the lead builder's `factors` column as written).
7. `passes` is a GeoJSON FeatureCollection whose feature properties are the Pass record (contract 3.6) and whose geometry is the footprint.
8. `geo` is `{"layers": {name: geom}}` plus `eez_statement` and `eez_disclaimer`; polygon rings drop the closing vertex; a feature's rings are drawn with the even-odd rule, so holes and multipart outers need no flag.
9. `meta` adds `caveat_short`, `ais_recording` (period, gaps, counts, from `data/ais_live_summary.json`), `live_rules` (the live file's `about` rules shown on the Contact and Pass pages), `data_credit`, `fixture` (the fixture only; the page shows a FIXTURE tag whenever it is present).
10. Contacts carry `src` as a dict8 column (`det_live`, `det_regional`, `det_camau`). A `synthetic` bool8 column, if present, marks invented rows; the round 3 fixture has none and writes none.
11. `cells`: columnar with `cell_id` (or `row` and `col`), `lon`, `lat`, `region_box` and the static fields (look probability in percent, as the API returns it); a `nightly` block `{night, n, columns, valid}` whose columns are parallel to the cells and whose `valid` holds the values that are the same for every cell (valid times, datasets); an `eez_attrs` block `{heading, statement, n, columns, src}` (Marine Regions attributes as published, returned only under `eez`); an `expected_activity` block `{n, model_id, caveat, targets: {viirs|radar: {n_tested, n_flag, n_flag_robust}}}` of counts; `records[cell_id]` may carry `expected_activity` in the board D5.4 shape (rows newest first, tested only), which wins over the counts, and `nightly`, `eez`, `prov`.
12. Lead `factor`, `lawful_explanations` and `change_indicators` hold codes, not sentences (board D5.1; the API does the same). `scripts/check_build.mjs` and `fixtures/test_text_codes.py` check that every code has a display string in `src/app/text.ts`.
13. `object_context` of contacts and lights (board D5.3): `records[id].object_context` in the D5.3 shape wins; otherwise a part-level columnar block `object_context: {n, columns, fields, caveat}` with rows parallel to the part's rows: `time_utc` (time; null means no context), `region` and `cell_id` (dict), one column per D5.3 field (numbers; `ship_presence_*` as bool8, presence only), and the dict columns that hold valid times and dataset names (`sst_time`, `sst_src`, `chl_time`, `chl_src`, `current_time`, `wave_time`). `fields[name]` gives `unit` and either `src` (a registry key) or `src_col`, and `time_col`. A field's `src` that is not a registry key (a dataset name such as `mur`) is shown as text next to the chip of the field's producer.
14. `rasters`: `{layers: [{name, unit, valid_period, colormap, vmin, vmax, bounds: [w, s, e, n], src, licence, default_on: false, note, research_only, resolution_deg, width, height, image}]}` with `image` a WebP data URI on the lon/lat grid (EPSG:4326) and an optional `image_light`. The frontend reprojects each row to Web Mercator before drawing (stretching a lon/lat image into Mercator bounds would shift it by up to about 0.36 degree of latitude over this AOI). Shipping density layers are presence: any non-transparent pixel is drawn as a hatch in one mask colour, never a magnitude (board D4.3).
15. `passes`: a processed live pass carries `scene_counts` (per scene `n_contacts`, `n_high`, `n_medium`, `n_fixed`, `n_matched`, `n_unmatched`, `n_no_coverage`, `n_dark_leads`, `n_ambiguous`, `ais_aoi_positions`, `ais_aoi_mmsi`, `ais_footprint_positions`, `ais_footprint_mmsi`, `ais_near_footprint_mmsi`, `ais_recorded_hours`), `contacts_in_bundle` (false when drop rule 7 took its contacts out), and the contract 1.3.0 fields `n_ais_only`, `ais_only` (a list of AIS-only vessel objects with the fields of the live file's `ais_only_4326` layer plus `vessel_key` and `identity_label`; it may be a subset, `n_ais_only` is the pass total), `azimuth_check` and `identity_label`; the part may carry `caveat` and `notes` for every pass. The API keeps `scene_counts` under `extra`; the HTTP adapter moves it up.
16. `contacts`: `prov_sets` with `prov_set_rule` (live, camau, research, structures, regional) give each row its provenance map; every record field that is not a bulk column, and identity strings that differ from the vessel row (contract behaviour rule 6), win over the bulk columns and the referenced vessel. For a live contact the record carries what the live file states and the columns do not: the identity of a match (`mmsi`, `vessel_name`, `call_sign`, `imo`, `flag`, `ship_type`, `length_ais_m`, `identity_source`), `review_note` (the hand check, '<grade>: <reason>'), `match_ambiguous` (only when true) and `ambiguous_mmsi` (candidate MMSIs joined by ';'), `match_alt_dist_m`, `az_time_utc`, `az_shift_m`, `match_dist_uncorr_m`, `velocity_source`, `pred_method`, `ais_sog_kn`, `identity_label` (board D4.7) and `lead_ids`. `vessel_key` and `nearest_vessel_key` are the referenced vessel row's `vessel_key` (research: `<source>:<vessel_id>`), so links reach research vessels. `leads`: part-level `change_indicators` and `prov_by_type` per lead type. `vessels`: `prov_by_src` per source. `meta`: `build_label`, `research_label` and `attribution` give the banner text; the attribution links to the origin of the first research-only source URL, so the shell itself names no research source.

**Builder readings not in the list** (`app/build/README.md`, R3-T2, "Contract (for 1.3.0)" item 3): dict columns that hold numbers, a column with no reference written as `const` null, lead times as `dict` of ISO text, light positions at 0.002 / 0.001 degree, `mission` from `det_id`, `events` with `records` as an array plus `encounters`, `encounter_cells`, `loitering_cells`, `port_anchorages`, `camau` as `{backdrop: {image, bounds, scene_grid}}`, drop rule 2 as an `under_cloud_positions` block, chip tier 4. They are open item 8.19 until R3-T12 and R3-T11 agree them with the PM; until then a reader must accept them where the embedded adapter does.

**1.3.0 fields in the bundle.** `object_context` follows reading 13 and `expected_activity` reading 11; the Pass fields `n_ais_only`, `ais_only`, `azimuth_check`, `identity_label` follow reading 15; the live identification fields follow reading 16. The backend serves all of them on the full records the builder reads (`Store.contact_rows(full=True)`, `light_rows(full=True)`, `cell()`, `pass_(pass_id)`); `pass_rows()` (lists) carries `n_ais_only` but not the vessel list, so a builder that wants the AIS-only vessels in the page calls `pass_rows(positions, ais_only=True)` or `pass_(pass_id)`. Their bytes are not in the budgets of section 6.3 (open item 8.19).

## 7. Directory layout and ownership (round 3)

```
app/
  CONTRACT.md                 this file (changes: version bump + PM approval; owner in round 3: R3-T10)
  backend/scs_api/            FastAPI app: config.py (build, paths, labels), catalog.py (file registry, build guard),
                              models.py (pydantic, this contract), records.py, envelope.py, sources.py (registry),
                              store.py (load, reload, records, queries), loaders/ (contacts, vessels, lights, events,
                              leads, passes, cells, context, rasters, geo, common), routes/ (one per resource), chips.py,
                              export.py, decisions.py, columnar.py, serve.py (uvicorn entry)
  frontend/                   React, Blueprint, Leaflet; src/adapters/{http,embedded,types,context}.ts, views/, map/, app/
                              (owner in round 3: R3-T11); README.md holds the bundle readings of section 6.4
  build/                      build_single.py and scs_bundle/ (parts, encoders, budget, chips, checks), check_bundle.mjs,
                              README.md (owner in round 3: R3-T12); out/ is git-ignored
tests/app/                    backend tests (TestClient, synthetic fixtures in synthetic.py; no network)
tests/app_build/              bundle builder tests
```

Ownership for parallel work without shared files: the backend task owns `app/backend/`, `tests/app/` and this contract; the frontend task owns `app/frontend/`; the single-file task owns `app/build/` and `tests/app_build/`. A needed change in another task's files is reported to the PM, not edited in place (board D3).

## 8. Decisions and open items (1.3.0)

R2-T3 (the round 2 backend) proposed 15 changes for a contract 1.2.1 and, in its fix round, four more 1.2.x additions; none was decided before this version. R3-T10 decides each below against what the code does and the round 3 board decisions; an item that needs the PM or another task stays open with its reason.

| # | Item | Decision |
|---|---|---|
| 8.1 | Model grid size (3.7 said 109 columns by 94 rows) | **Decided:** 109 rows by 94 columns (`scs_api.loaders.common`: `GRID_ROWS` 109, `GRID_COLS` 94; `darkvessel.ocean.grid.model_grid`). Section 3.7 corrected |
| 8.2 | `ship_density_*` and `ship_fishing_share` are not in `ocean_static_cells.parquet` | **Decided:** the Cell fields are `ship_presence_share_<type>` (share of the cell's sea with any published value above 0; board D4.3) as the file and the backend have them; `ship_fishing_share` is dropped; `marineregions_overlap_share` lives inside `eez`. Section 3.7 corrected |
| 8.3 | Contact `row` and `col` | **Decided:** pixel coordinates of the contact in its scene (live and Ca Mau files), passed through as floats; `cell_id` is computed from `lon`, `lat`. Section 3.1 corrected |
| 8.4 | Lead queue fields and list limit | **Decided:** Lead records carry `ais_status`, `cnn_score`, `length_est_m`, `pass_id` from the leads file (the primary contact's values; null otherwise); `GET /leads` allows up to 20,000 per request (the research queue holds 14,261), every other list stays at 1,000 (passes 5,000). Sections 3.5 and 5 |
| 8.5 | Cell nightly and per-pass fields | **Decided:** nested in `nightly` and `pass_context` objects (their names clash, for example `wind_ms`, `wave_hs_m`), with `nights_available` and `shipping_note`; `gfw_comparison` only in the research build. Cell `object_context` stays in the record and is always null in 1.3.0 (the object context belongs to Contacts and Lights); `expected_activity` is board D5.4 |
| 8.6 | `research_only` meaning (4.5 against 3.1) | **Decided by board D5.5:** per row; the research line is in every research record's caveat. Rule 4.5 amended |
| 8.7 | Track caveat | **Decided:** a Track is one record: the envelope `caveat` is `PRODUCT_CAVEAT`, `track_caveat` adds the build line, each gap carries the gap note, single positions carry none. Section 3.2 |
| 8.8 | Timeline field names | **Decided:** the frontend's names, as served: `passes`, `contactsByPass`, `aisHours`, `aisGaps`, `viirsNights`, `events` (plus `events_total`, `events_shown`), `upcoming`. Section 5 |
| 8.9 | Search response | **Decided:** results in `items`, `interpretation` in the envelope; result types add `cell` and `site`. Section 5. The frontend's `SearchResult.type` lacks `cell` and `site` (reported to R3-T11) |
| 8.10 | Layer splits | **Decided:** `/layers/contacts.cols` holds the `high` and `medium` contacts, `/layers/structures.cols` every `fixed` object. Section 5 |
| 8.11 | Research leads read | **Decided:** the backend reads `data/research/leads_research.parquet` first (the GeoPackage, about 85 MB and git-ignored by board D6.5, is the fallback). Section 3.5 |
| 8.12 | aisstream `ship_type` | **Decided:** `ship_type` is the label (`ship_type_label` of the file); the ITU-R M.1371 code is `extra.ship_type_code`. Section 3.2 already maps it |
| 8.13 | Research stub count | **Decided (as a measurement, not a rule):** 1,944 GFW stubs, not 2,407, because 463 nearest-AIS ids have identities in `gfw_events_vessels.parquet`; the research vessel table holds 50,556 GFW and aisstream rows plus the 139 live stubs of 1.3.0 (50,695) at the check. Section 6.2's 2,407 and 11,331 are 1.2.0 measurements |
| 8.14 | Ca Mau `pass_id` | **Decided:** null; the Ca Mau scene is one scene, not a pass of the plan or the regional run |
| 8.15 | Light caveat | **Decided:** `PRODUCT_CAVEAT` plus the light caveat of spec 4.4 (plus the research line in the research build); the file's caveat goes to `extra.source_caveat`. Section 3.3 |
| 8.16 | 1.2.x additions of the R2-T3 fix round | **Decided:** `/meta` `loading` and `reload_error`; RasterValue `presence` and `value_as_published` with `value` 1.0 or 0.0 for shipping density (board D4.3); the expected-activity file is `data/expected_activity.parquet`; an empty CSV export is one note row with the caveat, build, licences, `generated_utc` and `contract_version` |
| 8.17 | Vessel snapshot staleness | **Open (producer, not the backend):** `ais_live.gpkg` `vessels_latest_4326` holds the 1,277 vessels of the first recording period (last heard 2026-10-08 23:37 UTC) although the file was rewritten at 12:20 UTC on 2026-10-10; the backend covers the gap with live stubs (3.2), which carry the live file's strings but no last position, motion or destination. The task that owns `scripts/28_ais_reach.py` (or the vessel snapshot writer) should refresh it from the recording |
| 8.18 | Chip builder in the backend (`fetch=1`) | **Open:** the single-file builder (R3-T2) fetches and caches chips (1,714 cached at 18:03 UTC); the backend serves cached chips but does not build one on request. A round 4 task can reuse `app/build/scs_bundle/chips.py` behind `fetch=1` |
| 8.19 | Bundle budgets, builder readings and 1.3.0 bytes | **Open (R3-T12 with the PM):** the budget proposals of section 6.3, the builder's own readings (section 6.4), and the bytes the 1.3.0 fields add to the page (object context columns, expected-activity rows, AIS-only vessels) are measured and decided with the round 4 bundle (board D6.6) |
| 8.20 | Object context of live passes | **Open (producer, R2-T6 follow-up):** `ocean_context_objects.parquet` predates every live pass, so live contacts return null. Rebuilding it after a pass (`scripts/25_object_context.py`, which takes `radar` objects from `detections_regional_all.gpkg` today) needs live contacts as an input; under D5.3 they would be `radar` rows keyed by `det_id`, which the backend already looks up |
| 8.21 | Frontend types | **Open (R3-T11):** `app/frontend/src/adapters/types.ts` names every 1.3.0 Contact and Pass field as this contract does (checked 16:30 UTC); it does not yet type `field_prov`, Vessel `identity_label`, `Meta.loading` and `reload_error`, the search result types `cell` and `site` (the HTTP adapter reads `items` correctly), or the lead evidence type `light_site` that the lead builder writes for recurring light sites (112 open evidence rows; the backend previews it as a site). All are optional, index-signature or display-only, so nothing breaks today |
| 8.22 | Events of the open build (E8 gaps from aisstream) | **Open (round 4, board):** `data/events_open.gpkg` is not built; `/events` in the open build answers an empty list with a note |
| 8.23 | `object_context.caveat` | **Open (PM, board decisions log):** board D5.3 writes `"caveat": OCEAN_CAVEAT`; the backend returns `OCEAN_CAVEAT` followed by the context table's own object sentence when the table's caveat extends `OCEAN_CAVEAT` (section 3.1, Object context), so the producer's warning that a radar candidate can be clutter and a light a platform reaches the page. The frontend shows either form (`ContextSection.tsx`). If the PM rules for the literal D5.3 text, the change is one line in `ContextData._caveat` |
| 8.24 | Scene id of an azimuth-check entry | **Open (producer, R3-T7):** `live_summary.json` `azimuth_check_by_scene` entries carry no `scene_id`, and the producer leaves out scenes with no shifted vessel, so for such a pass the backend cannot name each entry's scene and returns `scene_id` null (section 3.6). `darkvessel.live.outputs` should write `scene_id` into each entry; the backend keeps an entry's own `scene_id` when present |

## 9. Sources

**Repo files and data read for 1.3.0** (R3-T10, 2026-10-10): `docs/PROJECT_BOARD.md` (D1, D4 to D6, round log), `app/CONTRACT.md` 1.2.0, `app/frontend/src/adapters/types.ts`, `app/frontend/src/adapters/context.ts`, `app/frontend/src/app/identity.ts`, `app/frontend/src/views/ContextSection.tsx`, `ObjectPages.tsx`, `PassPage.tsx`, `Provenance.tsx` and `app/frontend/README.md` (read only; owned by R3-T11), `app/build/README.md` and `app/build/scs_bundle/` (read only; owned by R3-T12), `docs/product_design.md` 1.3 (sections 4.2 to 4.9), `docs/ocean_context.md` (sections 6 to 9), `src/darkvessel/live/schema.py`, `src/darkvessel/ocean/context.py`, `src/darkvessel/ocean/grid.py` (`OCEAN_CAVEAT`); the data files of section 3 with their layers, columns, row counts and modification times (catalog re-check at 16:24 UTC, section 1), the `about` layer of `data/live/live_contacts.gpkg` (rules quoted in sections 3.1 and 3.6), `data/live/live_summary.json` (`azimuth_check_by_scene`), the live weather sidecars and hand-check tables, `data/ocean_context_objects.json`, the schema metadata of `data/expected_activity.parquet`, the `about` layers of `data/viirs_lights.gpkg` (`lean_subset`) and `data/leads_open.gpkg` (`file_layout`, `size_rule`, `evidence_roles`); the R2-T3 report (its 15 proposed changes) and fix report (its 1.2.x additions). Every count, timing and crawl result in this version was measured on those files in this session; the scratch scripts are R3-T10's.

**URLs resolved in this session** (2026-10-10, 16:10 to 16:11 UTC, `curl -L` with a browser user agent; HTTP status in brackets):
- https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice [200] (credit text and "free, full and open", section 2).
- https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits [200] (CC BY-NC 4.0; "Powered by Global Fishing Watch.").
- https://creativecommons.org/licenses/by-nc/4.0/ [200], https://creativecommons.org/licenses/by/4.0/ [200].
- https://doi.org/10.14284/632 [200] (Marine Regions World EEZ v12 record).
- https://www.marineregions.org/disclaimer.php [200] ("VLIZ expresses no opinion about the legal state ...", section 3.7).
- https://www.naturalearthdata.com/about/terms-of-use/ [200] ("public domain").
- https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use [200].
- https://datacatalog.worldbank.org/search/dataset/0037580/global-shipping-traffic-density [200] ("Creative Commons Attribution 4.0").
- https://registry.opendata.aws/noaa-jpss/ [200], https://registry.opendata.aws/noaa-himawari/ [200], https://registry.opendata.aws/noaa-gfs-bdp-pds/ [200], https://registry.opendata.aws/sentinel-1/ [200].
- https://coastwatch.pfeg.noaa.gov/erddap/info/jplMURSST41/index.json [200] (MUR SST, registry key `mur_sst`).
- https://raw.githubusercontent.com/allenai/satlas/main/GeospatialDataProducts.md [200] ("All data is released under ODC-BY").
- https://www.itu.int/en/ITU-R/terrestrial/fmd/Pages/mid.aspx [200] (MID table, flag of an MMSI).
- https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans [200] (pass plan).
- https://www.apache.org/licenses/LICENSE-2.0 [200] (CNN training labels' licence).
- https://aisstream.io/documentation [200], https://aisstream.io/privacypolicy [200], https://aisstream.io/terms [404] (no terms published: the aisstream terms stay UNVERIFIED, board D4.7).
- https://fastapi.tiangolo.com/tutorial/testing/ [200] (`TestClient`), https://fastapi.tiangolo.com/tutorial/path-params/ [200] ("Order matters", section 5).
- https://www.imo.org/en/OurWork/Safety/Pages/AIS.aspx [200] and https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/AIS/Resolution%20A.1106(29).pdf [200] (section 1.1).
- https://support.marinetraffic.com/en/articles/9552924-why-can-t-i-see-a-vessel-on-the-live-map [200] (section 1.1).
- Raney, R. (1971), Synthetic Aperture Imaging Radar and Moving Targets, IEEE Transactions on Aerospace and Electronic Systems: https://doi.org/10.1109/TAES.1971.310292 [202 at the publisher after the DOI redirect]; the Crossref record https://api.crossref.org/works/10.1109/TAES.1971.310292 [200] gives the title, journal and May 1971 (the azimuth shift behind `az_shift_m`, cited by the live file's `about` `source_azimuth_shift`).
- Benjamini, Y. and Hochberg, Y. (1995), Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing, 57, 289-300: https://doi.org/10.1111/j.2517-6161.1995.tb02031.x [403 at the publisher after the DOI redirect]; the Crossref record https://api.crossref.org/works/10.1111/j.2517-6161.1995.tb02031.x [200] gives the title and the Journal of the Royal Statistical Society Series B (the `q_bh` of section 3.7).

The 1.2.0 sources of sections 1.1, 2 and 3.7 are the same URLs; they were re-resolved above. The licences in section 2 are as the producing files and these pages state them.
