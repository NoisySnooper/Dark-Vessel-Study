# SCS Vessel Watch: data contract

Contract version **1.1.0**, 2026-10-09 (UTC); 1.1.0 is the reviewed version (identity by reference in the bundle, measured budgets, file status re-checked, one product caveat). Task R1-T4. Binding on the round 2 backend, frontend and single-file build tasks. The product spec is `docs/product_design.md`; package choices are in `docs/research/stack_decision.md`.

> 'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent.

This is the product caveat (section 1.1). Every record this contract defines carries it in a `caveat` field.

## 1. Conventions

- **Types.** `str`, `int`, `float`, `bool`, `datetime` (ISO 8601 in UTC with a `Z` suffix in the API, for example `2026-09-20T10:48:16Z`; source files may write `+00:00`, the backend normalises), `enum` (one of the listed strings), `list[...]`, `geom` (GeoJSON geometry, EPSG:4326 lon/lat). Null is JSON `null`; a column listed as not nullable never holds null.
- **Units** are in the field name where the repo already does so (`_m`, `_km`, `_s`, `_db`, `_deg`, `_kn`, `_ms`, `_c`, `_nw`, `_pct`) and in the Unit column below.
- **Positions.** `lon`, `lat` in decimal degrees, WGS 84 (EPSG:4326). Exports add UTM 49N (EPSG:32649) for regional objects and UTM 48N (EPSG:32648) for Ca Mau objects.
- **Builds.** `open` or `research`. The Build column says which build may read a field or file: `both`, `open` (open build only), `research` (research build only). The open build never opens a path under `data/research/` (D2); the backend catalog refuses such paths when `build=open`, and a test asserts it.
- **File status** (checked 2026-10-09 07:00 UTC with `pyogrio.list_layers`, `pyogrio.read_info` and `pyarrow.parquet.read_metadata`; the time in brackets is the file's modification time, UTC, 2026-10-09 unless stated): **existing** (layer present, row count shown) or **pending, produced by ...** (the producing task or round; the consumer must handle its absence: the view shows a `NonIdealState` "not built yet", never an error). Row counts of growing files (live passes, AIS recording) are the counts at the check.
- **Ids.** Object ids are strings and stable across reruns: `det_id`, `vessel_key`, `light_id`, `site_id`, `event_id`, `lead_id`, `pass_id`, `cell_id`.

### 1.1 The product caveat

One string, used unchanged in the API `caveat` field of every record and envelope, in every export, on every lead card and lead page, and behind the short banner text (the banner links to it):

`PRODUCT_CAVEAT` = "'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent."

- It extends `darkvessel.config.DARK_CAVEAT`, which names only the satellite blind spot, although the open build's AIS is terrestrial (aisstream relays shore receivers). `config.py` is a shared file (D3), so the change is reported to the PM: add `PRODUCT_CAVEAT` with this exact text. Until then the backend holds the same text in `app/backend/scs_api/config.py`, and a contract test asserts the two are equal once the config constant exists.
- The short banner text stays `darkvessel.config.DARK_CAVEAT_SHORT`: "Dark = no AIS match. Not evidence of illegal activity."
- Research build records append: "Research build, noncommercial, CC BY-NC 4.0. Contains Global Fishing Watch data. Powered by Global Fishing Watch."
- The `caveat` column of a source row (today `DARK_CAVEAT` in the live and research files) is not shown in its place. When it differs from `PRODUCT_CAVEAT`, the backend passes it through as `extra.source_caveat`, so nothing the producer wrote is lost.
- Sources of the claims in it: AIS carriage applies to "all ships of 300 gross tonnage and upwards engaged on international voyages, cargo ships of 500 gross tonnage and upwards not engaged on international voyages and all passenger ships" (IMO AIS page), so the IMO requirement does not cover smaller vessels such as most fishing boats on domestic voyages (national rules can add duties; not checked for this contract); lawful switch-off is in IMO Resolution A.1106(29) paragraph 22 ("If the master believes that the continual operation of AIS might compromise the safety or security of his/her ship or where security incidents are imminent, the AIS may be switched off."); terrestrial AIS covers "only specific coastal areas where a land-based AIS receiver is installed" (MarineTraffic support article); the satellite clause is the existing config text. URLs in section 8.

## 2. Sources and provenance

Every record carries `src` (the default source key of the record) and `prov` (a map from field name to source key, only for fields whose source differs from `src`). `GET /api/v1/meta` and the bundle's `meta` part hold the source registry. The UI builds the per-field provenance chip from these (spec section 4.8).

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
| `esa_acq_plan` | ESA Sentinel-1 acquisition plan KML files | `darkvessel.ais.s1_passes` | ESA public plan; repeat predictions are not ESA's plan | both |
| `analyst` | Owner labels and lead decisions | the app (local: `data/labels/`; single-file: browser) | the owner's | both |
| `app` | values computed by the product (review priority, evidence counts) | `app/backend` (`priority_model_id`) | derived | both |

The licences above are copied from the `about` layers and summaries the producing scripts wrote, and were re-read at their URLs in this session where listed in the Sources section.

## 3. Object types

### 3.1 Contact

One radar contact. **The fields are the D1 columns of `docs/PROJECT_BOARD.md`, with the same names, in the same order**, followed by extension fields. Live and research files carry D1 natively (checked 07:00 UTC: the first 31 columns of `data/live/live_contacts.gpkg` `contacts_4326` equal `darkvessel.live.schema.D1_COLUMNS`, the first 32 of `data/research/regional_identity.parquet` equal `darkvessel.ais.gfw_identity.D1_COLUMNS`); the two older files without identity are mapped onto D1 by the backend as shown in the last two columns.

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

`no_coverage` is used only with the stated rule of the producing file (open live rule: nothing heard during the window in the contact's 0.25 degree cell or within 20 km). It never means dark. All 813 contacts of the first live pass (`live_S1D_20261008T2258`, Gulf of Thailand and west Vietnam) are `no_coverage`, although 18,076 AIS positions were recorded in its window (`data/live/live_summary.json`): the free terrestrial feed is thin there. The product must show such a pass as a coverage result, not as 813 dark leads.

**CNN score join.** For every September row, in both builds, `cnn_score` and `cnn_vessel` come from `data/ml/regional_cnn.parquet` (model `verifier_v0_356af0ca`, threshold 0.631783; 68,213 rows scored by `scripts/32_cnn_regional.py`, 35,626 reused unchanged from `shared_cells_cnn.parquet`). The research file carries scores only for the 26,799 shared-cell rows (51,816 null); where both exist they are identical (checked 07:00 UTC, maximum difference 0.0). `regional_identity.gpkg` stores `cnn_vessel` as Int16 (1, 0, null) and the parquet as bool; the API always returns bool or null.

**Extension fields** (allowed by D1; null when the source lacks them)

| Field | Type | Unit | Source (open live / research / regional / Ca Mau) |
|---|---|---|---|
| `view` | enum `regional`, `live`, `camau` | | derived from `run_id` |
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
| `match_gate_m`, `ais_sog_kn`, `length_ratio`, `ais_class`, `mmsi_mid`, `ais_window_positions` | float, float, float, str, int, int | m, kn, ratio | live |
| `pred_method`, `cnn_chip_valid_frac`, `ais_recorded_hours`, `row`, `col` | str, float, int, int, int | share, h | live (`row`, `col` are model-grid indices; `cell_id` is built from them) |
| `pass_id`, `identity_kind`, `gfw_sar_pair`, `gfw_sar_n_cand`, `gfw_sar_n_rivals`, `gfw_sar_ambiguous_cell`, `gfw_geartype`, `gfw_neural_type`, `pres_speed_kmh`, `pres_n_cells`, `pres_n_cand`, `n_gear_10km`, `ais_presence_h_day`, `ais_presence_h_window`, `nearest_ais_vessel_id`, `nearest_ais_name`, `n_gfw_gaps_50km_24h`, `nearest_gfw_gap_km`, `n_gfw_encounters_10km_24h`, `n_gfw_loitering_10km_24h` | as in `darkvessel.ais.gfw_identity.EVIDENCE_COLUMNS` (all 20, checked 07:00 UTC); `identity_kind` is `vessel`, `gear` (GFW types the matched AIS device as GEAR, a net or gear buoy, not the vessel's identity) or `unknown` | km/h, count, h, km | research only |
| `cnn_threshold`, `cnn_model_id`, `cnn_score_source`, `cnn_chip_valid_frac`, `bg_vv_db`, `bg_vh_db` | float, str, str, float, float, float | share, dB | regional: `regional_cnn.parquet`; Ca Mau: its own file (threshold and model id only); live: the `cnn_v0` source entry |
| `wind_ms`, `ctt_k`, `deep_convection` | float, float, bool | m/s, K | `data/weather_context.parquet` joined on `det_id` |
| `optical_object`, `optical_kind`, `s2_item`, `satlas_m` | bool, str, str, float | m | `data/optical_check.gpkg` joined on `det_id` (sample of 4,100) |
| `cell_id` | str | | derived from `lon`, `lat` on the 0.25 degree model grid (3.7) |
| `lead_ids` | list[str] | | from the Lead objects |
| `chip` | str or null | | API: `/api/v1/contacts/{det_id}/chip.webp` when cached; bundle: key into the `chips` part |
| `src`, `prov` | str, map | | section 2 |

**Contact sources**

| File and layer | Rows | Status | Build | Notes |
|---|---|---|---|---|
| `data/live/live_contacts.gpkg` `contacts_4326`, `contacts_utm49n` (EPSG:32649) | 813 | existing [06:31], one pass so far; grows | open | all live passes combined; D1 order then extensions (`darkvessel.live.schema`); also `ais_only_4326` (0), `scenes_4326` (2), `about` (1) |
| `data/live/live_<mission>_<yyyymmddThhmm>.gpkg` | 813 in `live_S1D_20261008T2258.gpkg` | existing [06:31] | open | one per pass, same layers; the backend reads the combined file |
| `data/live/live_summary.json` | 1 pass | existing [06:31] | open | per pass counts, AIS window, CNN, length bins |
| `data/research/regional_identity.gpkg` `contacts_4326`, `contacts_utm49n`, `about` | 78,615 (matched 9,954, unmatched 68,470, no_coverage 191) | existing [06:54] | research | September run with GFW identity, D1 plus the 20 evidence columns |
| `data/research/regional_identity.parquet` | 78,615 | existing [06:54] | research | same rows and columns (52); the backend reads this one |
| `data/research/regional_identity_summary.json` | | existing [06:54] | research | rules, counts, hand-check sample |
| `data/detections_regional.gpkg` `detections_regional_4326` (and `_utm49n`) | 78,615 | existing [10-02 18:42] | both | September run, high 29,228, medium 49,387, `ais_status` `not_checked`; the open build uses it, the research build replaces it by `regional_identity` |
| `data/detections_regional_verified.gpkg` `detections_regional_verified_4326` (and `_utm49n`, `scenes`, `about`) | 78,615 | existing [05:55], R1-T6 | both | the same contacts with `cnn_score`, `cnn_vessel` for desktop GIS; the backend reads the parquet below instead |
| `data/structures_regional.gpkg` `structures_regional_4326` (and `_utm49n`) | 25,224 | existing [10-02 18:42] | both | fixed structures; Contacts with `confidence` `fixed` |
| `data/detections_ml.gpkg` `detections_verified_4326` (and `_utm48n`) | 6,005 | existing [10-02 16:34] | both | Ca Mau scene with CNN scores (low 4,936, medium 435, fixed 349, high 285) |
| `data/detections_baseline.gpkg` `processing_window_4326`, `about` | 1, 1 | existing [10-02 06:28] | both | Ca Mau scene window and detector settings |
| `data/ml/regional_cnn.parquet` (and `regional_cnn.json`) | 103,839 (78,615 contacts plus 25,224 structures) | existing [05:55], R1-T6 | both | `det_id, scene_id, mission, confidence, cnn_score, cnn_vessel, cnn_threshold, cnn_model_id, cnn_score_source, cnn_chip_valid_frac, chip_valid_frac_full, bg_vv_db, bg_vh_db, caveat`; takes precedence over `shared_cells_cnn.parquet`. R1-T6 is still scoring `low` objects (run log 06:59 UTC); that output is not part of this contract (low objects stay out of the regional product, section 4) |
| `data/ml/shared_cells_cnn.parquet` | 35,626 | existing [10-02 19:05] | both | superseded by `regional_cnn.parquet` for the product; kept for the papers |
| `data/weather_context.parquet` | 162,386 | existing [10-02 23:41] | both | `det_id, wind_ms, ctt_k, himawari_start, deep_convection` |
| `data/optical_check.gpkg` `optical_check_4326` | 4,100 | existing [10-03 00:29] | both | sample only |
| Chips: `data/cache/chips/<det_id>.webp` | | pending, round 2 backend | both | git-ignored cache; built from `s1_grd` by the backend chip builder |

### 3.2 Vessel

One AIS identity. `vessel_key`: `mmsi:<mmsi>` (aisstream) or `gfw:<vessel_id>` (GFW). In the research build a GFW vessel links to `mmsi:<mmsi>` when that MMSI was also heard by aisstream.

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
| `stub` | bool | | no | false | false; true for a nearest-AIS vessel that is not in `gfw_vessels.parquet` (only `vessel_key`, `mmsi`, `name` from `regional_identity` `nearest_ais_vessel_id`, `nearest_ais_mmsi`, `nearest_ais_name`) | false |
| `contacts_matched` | list[str] | | no | det_ids of Contacts with this `mmsi` | det_ids with this `gfw_vessel_id` | det_ids with this `gfw_vessel_id` |
| `identity_note` | str | | no | "Identity fields are self-reported by the transponder; they can be wrong, reused or spoofed." | same, plus "as published by Global Fishing Watch" | same as research |
| `research_only`, `caveat`, `src`, `prov` | | | no | false, `PRODUCT_CAVEAT`, `aisstream` (file column `ais_note` goes to `extra.source_caveat`) | true, `PRODUCT_CAVEAT` plus the research line, `gfw_vessels` (`use`, `licence` columns feed the registry entry) | as research |

**Track** (sub-resource of Vessel): ordered positions `{t (datetime), lon, lat, sog_kn, cog_deg, msg_type}` with `gaps` = list of `{from_utc, to_utc, minutes}` over 6 h, each with "An AIS gap is not proof of intent."

| File and layer | Rows | Status | Build |
|---|---|---|---|
| `data/ais_live.gpkg` `vessels_latest_4326` (and `_utm49n`) | 1,277 [05:38], grows | existing | open |
| `data/ais_live.gpkg` `tracks_4326` (and `_utm49n`) | 1,094 | existing [05:38] | open; line per MMSI with `start_utc`, `end_utc` (used by the single-file page) |
| `data/ais_live.gpkg` `about`, `s1_next_passes_4326`; `data/ais_live_summary.json` | 1, 87 | existing [05:38] | open; recording period, gaps, reach, terms note |
| `data/cache/ais/aisstream/positions/<yyyymmdd>/<HH>.parquet` | hourly files | existing (git-ignored) | open, local app only: `mmsi, timestamp, lon, lat, sog_kn, cog_deg, heading, nav_status, msg_type, msg_id, ais_class, ship_name` |
| `data/cache/ais/aisstream/static/<yyyymmdd>.parquet` | daily files | existing (git-ignored) | open, local app only: identity history (E12) |
| `data/research/gfw_vessels.parquet` | 8,924 (identity_kind: vessel 8,535, gear 286, unknown 103) | existing [06:54] | research; "GFW identity records of the vessel ids used by regional_identity": all 8,484 matched vessel ids and 5,915 of the 8,322 distinct nearest-AIS vessel ids (the other 2,407 become `stub` rows); columns `vessel_id, mmsi, vessel_name, call_sign, imo, flag, ship_type, gfw_geartype, length_ais_m, identity_source, geartype, shiptype, length_m, tonnage_gt, registry_sources, registry_records, ais_messages, ais_positions, transmission_from, transmission_to, dataset_version, identity_kind, use, licence` (24) |
| `data/research/gfw_events_vessels.parquet` | 39,999 | existing [06:43] | research; identities of the vessels in GFW events: `vessel_id, ssvid, shipname, flag, callsign, imo, geartype, shiptype, length_m, tonnage_gt, registry_sources, registry_records, ais_messages, ais_positions, transmission_from, transmission_to, dataset_version, use, licence` (19) |

For the 9,954 matched research contacts, the identity fields in `regional_identity.parquet` equal the `gfw_vessels.parquet` row of their `gfw_vessel_id` in every one of `mmsi, vessel_name, call_sign, flag, ship_type, imo, length_ais_m, identity_source` (checked 07:00 UTC). This is what lets the bundle store identity once per vessel (section 6.2).

### 3.3 Light and Light site

**Light** (`light_id`), from `data/viirs_lights.gpkg` `viirs_lights_4326` (existing, 48,692 rows, all `lit_vessel_candidate`: 32,103 clear, 16,589 under cloud), build both.

| Field | Type | Unit | Null | Source column |
|---|---|---|---|---|
| `light_id` | str | | no | `light_id` = `<SPP, N20 or N21>_<time_utc as yyyymmddThhmmss>_<6-digit index>` (holds for all 48,692 rows, checked 07:00 UTC; the bundle rebuilds it from `satellite`, `time_utc` and the index) |
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
| `cell_id`, `caveat`, `src`, `prov` | | | no | `caveat` column; `src` = `viirs_dnb` |

**Light site** (`site_id`), from `viirs_sites_4326` (existing, 2,129): `site_id, lon, lat, n_lights, nights, radiance_med_nw, radiance_max_nw, satlas_infra_m, nights_seen_max, s1_passes_90d, likely` (1,802 "other recurring light: platform, flare, island or navigation light, or anchorage"; 327 "platform or turbine (Satlas point within 1 km)"), `caveat`.

Also existing: `viirs_granules_4326` (904 granule footprints, for coverage), `viirs_nights` (27 nights), `about`. Not used: `data/viirs_lights_all.gpkg` (148.7 MB, all lights; local app may read it on demand, never the bundle).

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
| `data/events_open.gpkg` `events_4326` (and `_utm49n`, `about`), computed from the aisstream cache | pending, round 2 events task (name proposed here) | open |
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
| `title` | str | | no | plain words, no verdict ("Unmatched radar contact in AIS reach, 38 m, Gulf of Thailand") |
| `state` | enum `new`, `reviewing`, `closed_explained`, `closed_unexplained`, `closed_false_alarm` | | no | latest decision, or `new` |
| `reason` | str | | yes | the picklist value of the latest closing decision |
| `priority` | int | 0 to 100 | no | review priority (not a risk score) |
| `priority_band` | enum `low`, `medium`, `high` | | no | 0 to 33, 34 to 66, 67 to 100 |
| `factors` | list[{`factor`, `value`, `points`, `max_points`, `source`}] | | no | every factor with its points; the sum (clipped to 0 to 100) is `priority` |
| `priority_model_id` | str | | no | weights version |
| `calibrated` | bool | | no | false until calibrated against owner labels |
| `primary_type`, `primary_id` | str | | no | object the lead is about (`contact`, `vessel`, `cell`, `light`) |
| `evidence` | list[{`type`, `id`, `role`}] | | no | linked objects (`role`: `primary`, `nearest_ais`, `same_night_light`, `silence`, `weather`, `cell`, ...) |
| `lon`, `lat` | float | deg | no | |
| `time_utc` | datetime | | no | |
| `region_box` | str | | yes | reporting box name or `other` |
| `next_look_utc` | datetime | | yes | next planned Sentinel-1 pass over the spot |
| `lawful_explanations` | list[str] | | no | pre-listed for the type |
| `change_indicators` | list[str] | | no | what would change the lead (late AIS match, second look, optical view) |
| `history` | list[Decision] | | no | from the decision log |
| `research_only`, `caveat`, `src`, `prov` | | | no | `caveat` is `PRODUCT_CAVEAT` (section 1.1) |

**Decision** (append-only log entry): `{lead_id, time_utc, user, from_state, to_state, reason, note, build, app_version}`.

| File | Status | Build |
|---|---|---|
| `data/leads_open.gpkg` `leads_4326` (and `_utm49n`), `lead_evidence` (table), `about` | pending, round 2 lead-scoring task (path from the MDA brief) | open |
| `data/research/leads_research.gpkg` (same layers) | pending, round 2 | research |
| `data/labels/lead_decisions.jsonl` (one Decision per line, append-only, committed) | pending, written by the round 2 backend | open; the research build writes `data/research/lead_decisions.jsonl` |
| `data/labels/owner_2026-10.csv` (contact labels `det_id,label,user,time_utc`) | pending, owner action | both |

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
| `ais_window_positions`, `ais_window_mmsi` | int | count | yes | live passes |
| `ais_heard_share` | float | share | yes | plan file |
| `caveat`, `src`, `prov` | | | no | |

| File and layer | Rows | Status | Build |
|---|---|---|---|
| `data/s1_next_passes.json` (`passes`, `pass_groups`) | 87 passes, 39 groups (generated 2026-10-08 23:37 UTC) | existing | both |
| `data/ais_live.gpkg` `s1_next_passes_4326` (and `_utm49n`) | 87 | existing | both (footprints) |
| `data/detections_regional.gpkg` `scenes_processed_4326` (and `_utm49n`) | 119 | existing | both |
| `data/live/live_contacts.gpkg` `scenes_4326` (and `_utm49n`) | 2 [06:31], grows | existing | both; `product_id, run_id, mission, start_utc, stop_utc, scene_time_utc, orbit_abs, orbit_rel, pass_dir, aoi_overlap_km2, tested_km2, ...` with per-scene counts by AIS status and `ais_window_positions`, `ais_window_mmsi`, `ais_recorded_hours` |
| `data/research/gfw_presence_passes.parquet` | 91,328 | existing [06:47] | research; GFW AIS presence per pass hour and vessel: `pass_id, lon, lat, hour_ts, hours, vessel_id, mmsi, ship_name, call_sign, imo, flag, gfw_vessel_type, gfw_geartype, use, licence, caveat` |
| `data/s1_footprints.gpkg` `s1_footprints_4326` | 1,042 (90-day archive to 2026-10-02) | existing | both (coverage history) |

### 3.7 Cell context

One cell of the 0.25 degree model grid (`darkvessel.ocean.grid.model_grid`, origin 99.0E 24.0N, 109 columns x 94 rows, row 0 north). `cell_id` = `r<row>c<col>`, for example `r56c23`. Cells in the product: the 5,116 rows of `data/ocean_static_cells.parquet` (cells that hold an AOI fine cell or whose centre is in the AOI).

**Static sea fields**, `data/ocean_static_cells.parquet`, existing [00:56], 5,116 rows, 35 columns, build both. Column meanings are the producer's (`data/ocean_static_summary.json` `table_columns`).

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
| `ship_density_all`, `ship_density_fishing`, `ship_density_commercial`, `ship_density_oilgas`, `ship_density_passenger`, `ship_density_leisure` | float | AIS positions per fine cell, January 2015 to February 2021 (World Bank/IMF count) | yes | relative intensity of shipping, moving and stationary alike |
| `ship_fishing_share` | float | share | yes | `ship_density_fishing / ship_density_all`, null where all is 0 |
| `marineregions_mrgid` | int | | yes | MRGID of the Marine Regions v12 polygon covering most of the cell's sea, as published |
| `marineregions_geoname`, `marineregions_pol_type` | str | | yes | geoname and pol_type, as published |
| `marineregions_share` | float | share | yes | share of the cell's sea in that polygon |
| `marineregions_n` | int | count | yes | distinct Marine Regions polygons in the cell's sea |

**The `marineregions_*` fields are EEZ attributes.** The API returns them under a separate `eez` object of the Cell record, and the bundle keeps them in a separate `eez_attrs` block of the `cells` part. The Cell page and every tooltip show them only while the EEZ layer is on, under the heading "As published by Marine Regions" with the statement of section 3.7 Vector context; they are never a filter, a lead factor or a model feature. With the EEZ layer off they are not rendered at all.

**Other cell fields**

| Group | Fields (unit) | Source file | Status | Build |
|---|---|---|---|---|
| Key | `cell_id`, `row`, `col`, `lon`, `lat` (cell centre, deg), `region_box` | derived | | both |
| Nightly sea | per `night`: `sst_mean_c`, `sst_sd_c`, `sst_grad_mean` (degC/km), `front_share`, `dist_front_km`, `chl_log10_mean` (log10 mg m-3), `chl_valid_share`, `ssh_m`, `ssh_anom_m`, `ssh_grad`, `current_speed_ms`, `mld_m`, `sbl_m`, `wave_hs_m`, `wind_ms`, `moon_illum_pct`, valid-time fields (`sst_date`, `chl_date`, `rtofs_valid_utc`, `wave_valid_utc`, `wind_valid_utc`) | `data/ocean_daily_cells.parquet` | existing [01:07], 128,979 rows (27 nights) | both |
| Per pass | per `scene_id`: the sea fields at the pass time | `data/ocean_radar_pass_cells.parquet` | existing [01:07], 4,407 rows | both |
| AIS reach | `ais_reach_share`, `ais_reach_mmsi` | `data/outputs/small/ais_reach_share_4326.tif`, `ais_reach_mmsi_4326.tif` (0.25 degree) | existing | open |
| Look status | `look_prob_1d`, `look_prob_7d`, `look_prob_30d`, `passes_90d` | `data/outputs/small/s1_look_prob_{1d,7d,30d}_4326.tif`, `s1_passes_4326.tif` (0.05 degree, cell mean) | existing | both |
| Object context | per object: the static and daily fields at the object | `data/ocean_context_objects.parquet` | pending, round 2 (`scripts/25_object_context.py`) | both |
| GFW comparison | `n_ours`, `n_gfw`, `n_gfw_matched`, `n_gfw_unmatched`, `n_pair` per cell-hour (`date`, `hour`, `cx`, `cy`, `res_deg`) | `data/research/radar_vs_gfw.parquet` | existing [06:43], 83,472 rows | research |
| GFW SAR and AIS presence | `detections`, `matched` per 0.1 degree cell-hour; `neural_vessel_type` counts; AIS presence `hours`, `n_vessels` per cell and day; fishing `hours` per cell, day and gear | `data/research/gfw_sar_detections.parquet` (59,888), `gfw_sar_detections_by_neural_type.parquet` (60,110), `gfw_sar_matched_by_vessel.parquet` (20,573), `gfw_presence_daily.parquet` (557,348), `gfw_fishing_effort_daily.parquet` (144,979); rasters `gfw_ais_presence_hours_4326.tif`, `gfw_fishing_hours_4326.tif`, `gfw_sar_matched_density_4326.tif`, `gfw_sar_unmatched_density_4326.tif` (each with `_utm49n`); `gfw_summary.json` | existing [06:42] | research |
| Expected activity | `expected`, `observed`, `z` per cell and night or pass | `data/expected_activity*` | pending, later round (`docs/ocean_context_plan.md`) | both |
| Caveat | `caveat` = `PRODUCT_CAVEAT` plus `darkvessel.ocean.grid.OCEAN_CAVEAT` ("Ocean and weather layers describe the sea, not what any vessel does. ...") | | | both |

**Raster layers** (map overlays; `data/outputs/small/`, all existing, each with a `_utm49n` twin): `depth_m`, `dist_coast_km`, `dist_port_km`, `ship_density_{all,commercial,fishing,oilgas,passenger,leisure}`, `sst_mean_c`, `sst_showcase_c`, `sst_grad_mean_c_per_km`, `front_freq` (0.01 degree); `chl_mean_mg_m3`, `chl_valid_share`, `current_speed_mean_ms`, `mld_mean_m`, `ssh_grad_mean`, `s1_look_prob_{1d,7d,30d}`, `s1_passes` (0.05 degree); `wave_hs_mean_m`, `wind_mean_ms`, `ais_reach_share`, `ais_reach_mmsi` (0.25 degree); `vessel_density_regional`, `viirs_lit_density`, `viirs_lit_density_clear`. Registry entry per raster: `{name, unit, resolution_deg, valid_period, colormap, vmin, vmax, src, licence, default_on: false}`.

**Vector context** (all existing, both builds unless noted): `data/aoi.gpkg` `aoi_4326`; `data/eez_marineregions.gpkg` [00:55] `eez_4326` (12 polygons), `eez_boundaries_4326` (77 lines), `about` (2): off by default; shown with the statement "Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, CC BY 4.0, doi:10.14284/632. In this sea many zones overlap or are disputed; the source marks them. This product takes no position on any boundary or claim." plus the Marine Regions disclaimer, as quoted in `data/ocean_static_summary.json` `eez_statement` and re-read at https://www.marineregions.org/disclaimer.php ("VLIZ expresses no opinion about the legal state neither of any country, territory or area nor concerning its delimitation, frontier or borders. The data has no legal value whatsoever."); `data/ocean_context.gpkg` [00:55] `depth_contours_4326` (989), `ports_4326` (236); `data/ocean_fronts.gpkg` [01:08] `fronts_4326` (2,823 lines of the showcase night 2026-09-29); land from Natural Earth 10 m (`darkvessel.aoi.natural_earth_land`); reporting boxes from `darkvessel.ocean.grid.REPORTING_BOXES` (the six boxes of `scripts/21_viirs_regions.py`).

## 4. Required behaviour of producers and consumers

1. The backend loads every existing file at start (about 1.5 s for all GeoPackage layers, measured) and re-reads a file when its modification time changes. A pending file is reported in `/meta` as `missing` and its views show "not built yet".
2. The open build never opens `data/research/`. The research build reads `data/research/regional_identity.*` instead of `detections_regional_4326` for the September run.
3. Field names in API responses and in the bundle are exactly the names in section 3. Unknown extra columns in source files are passed through under `extra` (a map), never renamed.
4. Contacts with `confidence` `low` appear only in the Ca Mau view (as in the demo page) and in exports; regional and live low objects stay out of the product.
5. Every record has `caveat` equal to `PRODUCT_CAVEAT` (section 1.1), followed by the type caveat where section 3 names one. Records from the research build also carry `research_only: true` and the research line in `caveat`.
6. Identity strings of a matched contact are the contact file's own. When they differ from the vessel record the contact links to (an identity change between the match and the latest static message), the contact keeps its own strings in the API and goes into the bundle's `records` (section 6.2), and the Vessel page shows the identity history.

**Contract tests (round 2, `tests/app/`, offline, synthetic fixtures):** (a) the Contact model's first 32 field names equal the D1 list of `docs/PROJECT_BOARD.md` in order (31 in the open build, without `gfw_vessel_id`), and equal `darkvessel.live.schema.D1_COLUMNS` and `darkvessel.ais.gfw_identity.D1_COLUMNS`; (b) with `BUILD=open` the catalog raises on any path under `data/research/` and no response contains a field starting with `gfw_`; (c) every record of every endpoint has a `caveat` that starts with `PRODUCT_CAVEAT`, and `PRODUCT_CAVEAT` equals `darkvessel.config.PRODUCT_CAVEAT` once that constant exists; (d) `ais_status` only takes the four values of 3.1; (e) a decision POST appends exactly one line and rejects a closing state without its reason; (f) the bundle builder applies each part's drop rule when the part exceeds its budget (section 6.3), fails when a part is still over its budget after its drops, and fails above 15,000,000 bytes in total; (g) with `BUILD=open`, `/meta` and the bundle's `meta` hold no source entry with `research_only` true; (h) `GET /api/v1/cells/at?lon=..&lat=..` returns the containing cell, not a 404 for a cell named `at` (route order, section 5); (i) the Cell record carries `marineregions_*` only inside its `eez` object.

## 5. Backend API (local app)

FastAPI on `127.0.0.1:8750` (port proposed), base path `/api/v1`. Started by `make serve` with `BUILD=open` (default) or `BUILD=research`. JSON is UTF-8. `docs_url` and `redoc_url` are off (their pages load from a CDN); `/openapi.json` is on and is generated from the same pydantic models as this contract.

**Envelope.** Every JSON response is an object:

```json
{
  "contract_version": "1.0.0",
  "build": "open",
  "build_label": "Open build. Open-licensed sources and live AIS relayed by aisstream.io.",
  "caveat": "<PRODUCT_CAVEAT, section 1.1>",
  "generated_utc": "2026-10-09T00:00:00Z",
  "item": { "...": "one record, with its own caveat" },
  "items": [ { "...": "records, each with its own caveat" } ],
  "total": 0, "limit": 100, "offset": 0
}
```

`caveat` is `PRODUCT_CAVEAT` (section 1.1). `item` for single objects, `items` plus `total`, `limit`, `offset` for lists. Errors: HTTP 4xx or 5xx with `{"error": {"code": "not_found", "message": "..."}, "caveat": "...", "build": "..."}`. Research responses add `"research_label": "Research build, noncommercial, CC BY-NC 4.0"` and `"attribution": "Powered by Global Fishing Watch."`.

**Endpoints**

| Method and path | Query parameters | Returns |
|---|---|---|
| `GET /api/v1/meta` | | build, labels, caveat, `sources` registry (section 2), `files` (path, layer, status existing or missing, rows, mtime), `git_hash`, `priority_model_id`, counts per type |
| `GET /api/v1/layers/{name}.cols` | `t0`, `t1`, `bbox` (`w,s,e,n`) | bulk columnar layer in the bundle format (section 6.2); `name` in `contacts`, `structures`, `lights`, `sites`, `vessels` |
| `GET /api/v1/contacts` | `t0`, `t1`, `bbox`, `run_id`, `view`, `ais_status` (comma list), `confidence`, `cnn_min`, `cnn_vessel`, `mmsi`, `pass_id`, `sort` (`-acq_utc` default, `length_est_m`, `cnn_score`), `limit` (default 100, max 1000), `offset` | Contact summaries: D1 fields plus `view`, `pass_id`, `lead_ids` |
| `GET /api/v1/contacts/{det_id}` | | full Contact with extensions, `src`, `prov` |
| `GET /api/v1/contacts/{det_id}/chip.webp` | `fetch` (`1` builds a missing chip from `s1_grd`) | `image/webp`, 130 x 64 px; 404 with the error envelope when absent |
| `POST /api/v1/contacts/{det_id}/label` | body `{label, user, note}`; `label` is vessel, structure, clutter or unsure | the stored label; appended to `data/labels/contact_labels.csv` |
| `GET /api/v1/vessels` | `q` (name, call sign, MMSI, IMO), `in_aoi`, `ais_class`, `limit`, `offset` | Vessel summaries |
| `GET /api/v1/vessels/{vessel_key}` | | full Vessel |
| `GET /api/v1/vessels/{vessel_key}/track` | `t0`, `t1`, `max_points` (default 2000) | `{"type":"FeatureCollection"}` of positions with `t`, plus `gaps`, plus envelope fields |
| `GET /api/v1/lights` | `t0`, `t1`, `bbox`, `night`, `quality`, `limit`, `offset` | Light summaries |
| `GET /api/v1/lights/{light_id}` | | full Light |
| `GET /api/v1/sites/{site_id}` | | Light site |
| `GET /api/v1/events` | `t0`, `t1`, `bbox`, `code`, `event_type`, `vessel_key`, `det_id`, `limit`, `offset` | Events |
| `GET /api/v1/events/{event_id}` | | Event |
| `GET /api/v1/leads` | `state` (comma list, default `new,reviewing`), `lead_type`, `min_priority`, `region_box`, `pass_id`, `t0`, `t1`, `sort` (`-priority` default), `limit`, `offset` | Lead summaries |
| `GET /api/v1/leads/{lead_id}` | | full Lead with `history` and resolved `evidence` previews |
| `POST /api/v1/leads/{lead_id}/decision` | body `{to_state, reason, note, user}` | updated Lead; one line appended to the decision log; 409 when the transition is not allowed; 422 when a required reason or note is missing |
| `GET /api/v1/passes` | `status`, `t0`, `t1`, `mission` | Passes |
| `GET /api/v1/passes/{pass_id}` | | Pass |
| `GET /api/v1/cells/at` | `lon`, `lat` | Cell context of the containing cell (declare before the next route) |
| `GET /api/v1/cells/{cell_id}` | `night`, `scene_id` | Cell context; `marineregions_*` only inside an `eez` object |
| `GET /api/v1/rasters` | | raster registry |
| `GET /api/v1/rasters/{name}.webp` | `theme` (`dark`, `light`) | colour-mapped overlay (EPSG:4326 bounds in the `X-Bounds` header and in the registry) |
| `GET /api/v1/rasters/{name}/value` | `lon`, `lat` | `{value, unit, valid_period, src}` |
| `GET /api/v1/geo/{name}.geojson` | | `land`, `aoi`, `reporting_boxes`, `eez` (as published), `eez_boundaries`, `depth_contours`, `ports`, `fronts`, `footprints` (window) |
| `GET /api/v1/search` | `q`, `limit` (default 20) | Omnibar results: `[{type, id, label, sublabel, lon, lat, score}]` plus `interpretation` for coordinates (spec section 5) |
| `GET /api/v1/timeline` | `t0`, `t1`, `bin` (`pass`, `hour`, `day`) | rows of the timeline (passes, contact counts by status, AIS recording hours and gaps, VIIRS nights, events, upcoming passes) |
| `GET /api/v1/export/{format}` | `format` in `gpkg`, `geojson`, `csv`, `html`; `type`, `ids` (comma list) or the list filters of that type | file download; every export carries caveat, build and licences (spec section 8) |
| `GET /` and static paths | | the built frontend |

**Route order.** FastAPI evaluates path operations in the order they are declared, so a fixed path must be declared before a path with a parameter at the same position ("you need to make sure that the path for /users/me is declared before the one for /users/{user_id}", FastAPI path parameters tutorial). Here that means `/cells/at` before `/cells/{cell_id}`; `/layers/{name}.cols`, `/rasters/{name}.webp` and `/rasters/{name}/value` do not collide. Contract test (h) guards it.

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

The builder picks the narrowest integer type that holds a column's range at its scale (for example `nearest_ais_dt_s` as `i16` when every value is within 32,767 s) and records it in `t`; the frontend reads `t`, never assumes it.

**Identity by reference.** A contact's identity is stored once per vessel, not per contact. The `contacts` part carries `vessel_ref` (matched vessel) and `nearest_ref` (nearest AIS vessel) as `ref16` columns into the `vessels` part, which holds the identity strings (`vessel_key`, `mmsi`, `name`, `call_sign`, `imo`, `flag`, `ship_type`, `gear_type`, `identity_kind`, `length_m`, `length_ais_m`, `tonnage_gt`, `identity_source`, `registry_sources`, first and last seen, `stub`). The embedded adapter resolves the references, so a Contact from the bundle has the same D1 fields as one from the API. Open build: references point to `mmsi:<mmsi>` vessels from `vessels_latest_4326`. Research build: to `gfw:<vessel_id>` vessels from `gfw_vessels.parquet` plus 2,407 `stub` rows for nearest-AIS vessels that file does not hold (11,331 vessel rows in all, under the u16 limit of 65,535). Measured on the real files: the 9,954 matched research contacts' identity strings take 3.82 MB as JSON records, against 1.27 MB for the whole research vessel table by reference.

Fields not in the bulk columns (long text, lists, `prov`, evidence columns) are in `records`, keyed by id, only for: every lead's evidence objects, every contact with a chip, and every matched contact whose identity strings differ from its vessel row (behaviour rule 6; none in the research file at the 07:00 UTC check). Matched contacts are not in `records` for their identity: that comes from `vessel_ref`. A record holds only the fields that are not bulk columns, with nulls left out: measured 338 bytes per research September record and 458 per live record (500-row samples, 07:00 UTC). Other objects show their bulk fields with a note "full record in the local app and the GeoPackage".

Chips (`chips` part): `{"<det_id>": "data:image/webp;base64,..."}`. Each chip is one WebP image of 130 x 64 px: VV 64 x 64 px, a 2 px gap, VH 64 x 64 px, at the 10 m GRD pixel spacing (640 m), grayscale, quality 70. **Chips are capped by bytes, not by count:** the builder adds chips in the selection order (primary contacts of leads by priority, matched live contacts, CNN-accepted unmatched live contacts, the label queue sample) until the next chip would push the part past its byte budget, and lists the number embedded and left out in `meta`. Size per chip entry (key, data-URI prefix and base64), measured on 400 real 64 x 64 px GRD windows (the CNN training chips in `data/chips/*.npz`, Sentinel-1A and 1B, 10 m pixels, from 51 scenes, 2nd percentile to maximum stretch per polarisation): mean 4,006 bytes, median 4,390, 90th percentile 4,594. The earlier figure of 2,804 bytes came from upscaled 98 x 48 px demo JPEG chips and is too low. Sentinel-1C/1D chips from the live pass have not been measured yet; the byte cap makes the count follow whatever they weigh.

Rasters (`rasters` part): WebP overlays at 0.05 degree (about 462 x 540 px) with `{name, bounds, unit, colormap, vmin, vmax, src}`; measured 1 to 47 KB each. `camau`: the Ca Mau radar backdrop as WebP plus the scene grid transform and its 6,005 objects (low objects as positions only).

### 6.3 Size budget

Limit 16 MB for the page. Hard cap in the build: **15,000,000 bytes**; the build prints the size of every part. Budgets are per part in bytes of the serialised element; MB here means 1,000,000 bytes, the unit of the cap. The "Measured" column is the size of that part encoded as section 6.2 specifies, computed at 07:00 UTC on 2026-10-09 from the real files (method in the stack decision, section 5); the budget adds room for growth (live passes, AIS recording).

| Part | Open: measured | Open: budget | Research: measured | Research: budget | Basis |
|---|---|---|---|---|---|
| Frontend JS and CSS | 1.77 MB | 2.0 MB | 1.77 MB | 2.0 MB | shell plus Leaflet before app code |
| `meta` | | 0.05 MB | | 0.05 MB | registry, envelope, part sizes, drops |
| `contacts` | 3.27 MB | 3.8 MB | 5.05 MB | 5.4 MB | open: September 78,615 rows `not_checked` with CNN (2.73 MB), live 813 (0.03 MB; a pass of about 800 contacts adds about 0.03 MB), Ca Mau 6,005 (0.21 MB), structures 25,224 (0.30 MB). Research: September 78,615 with D1 numeric fields plus `vessel_ref` and `nearest_ref` (4.72 MB), live 0.03, structures 0.30; no Ca Mau. The budget also holds `records` for the chip contacts (about 0.23 MB open at 500 x 458 bytes, 0.09 MB research at 250 x 338 bytes); lead evidence records come out of the same headroom |
| `chips` | | 2.0 MB | | 1.0 MB | by bytes: about 500 (open) and 250 (research) chips at 4,006 bytes |
| `lights` | 1.36 MB | 1.4 MB | 1.36 MB | 1.4 MB | 48,692 lights: positions, time, radiance, quality, satellite, nights seen (1.10 MB) plus the `lightid` index (0.26 MB); 2,129 sites |
| `vessels` (with simplified tracks) | 0.17 MB | 0.6 MB | 1.44 MB | 1.6 MB | open: 1,277 aisstream vessels (0.11 MB) plus 1,094 tracks simplified at 0.002 degree (3,122 vertices, 0.06 MB); research adds 11,331 GFW vessel rows with identity strings (1.27 MB) |
| `events` | | 0.3 MB | | 0.6 MB | research adds the 5 GFW gaps and the 13,516 encounters as columnar rows, and loitering and port visits only as their cell and anchorage counts (12,509 and 410 points); single loitering and port-visit events stay in the local app |
| `leads` | | 0.4 MB | | 0.4 MB | not built yet |
| `passes` | | 0.1 MB | | 0.1 MB | 87 planned plus processed |
| `cells` | 0.53 MB | 0.6 MB | 0.53 MB | 0.6 MB | 5,116 cells: static fields (0.35 MB), `eez_attrs` (0.08 MB), one night of daily fields (0.10 MB) |
| `geo` | | 0.7 MB | | 0.7 MB | land, AOI, EEZ, boxes, footprints, contours, ports, fronts |
| `rasters` | | 0.5 MB | | 0.6 MB | about 15 overlays at 1 to 47 KB; research adds the GFW rasters |
| `camau` | 0.49 MB | 0.6 MB | | 0 (left out) | radar backdrop, 2,562 x 2,600 px WebP q75 from `data/outputs/small/sigma0_vv_db_utm48n_40m_u8.tif` (the demo page carried it as a 1.56 MB JPEG), plus the scene grid transform; the 6,005 objects are counted in `contacts` |
| **Planned total** | | **13.05 MB** | | **14.45 MB** | cap 15.0 MB, limit 16 MB |

Totals: open 2.0 + 0.05 + 3.8 + 2.0 + 1.4 + 0.6 + 0.3 + 0.4 + 0.1 + 0.6 + 0.7 + 0.5 + 0.6 = 13.05 MB; research 2.0 + 0.05 + 5.4 + 1.0 + 1.4 + 1.6 + 0.6 + 0.4 + 0.1 + 0.6 + 0.7 + 0.6 + 0 = 14.45 MB.

**Drops.** Each part has its own drop rule, applied when that part exceeds its budget; the total cap then applies the same rules in the order below until the page fits. Every drop is listed in `meta.dropped` and in the Info tab.

| Order | Part | Rule | Size of the lever (measured) |
|---|---|---|---|
| 1 | `chips` | already capped by bytes; under total pressure the builder lowers the chip budget in 0.25 MB steps, from the end of the selection order | about 62 chips per step |
| 2 | `lights` | lights under cloud (16,589 of 48,692) become positions only | 1.36 to 1.08 MB |
| 3 | `camau` (open) | backdrop at half resolution (1,281 x 1,300 px) | 0.49 to 0.11 MB |
| 4 | `contacts` (open) | September `medium` contacts with `cnn_vessel` false (44,273 rows) leave the bulk columns; their counts stay in the cells part | September rows 2.73 to 1.19 MB |
| 5 | `contacts` (research) | September `medium` contacts with `cnn_vessel` false that are not `matched` (41,583 rows, with the CNN join of section 3.1) leave the bulk columns; their counts stay in the cells part. Every matched contact stays: identification is the core | September rows 4.72 to 2.23 MB (37,032 rows kept) |
| 6 | `vessels` (research) | `stub` rows no longer referenced by a remaining contact | 469 of 2,407 stubs after step 5 |

If a part is still over its budget after its rules, or the total is still over 15,000,000 bytes, the build fails.

## 7. Directory layout and ownership (round 2)

```
app/
  CONTRACT.md                 this file (changes: version bump + PM approval)
  backend/scs_api/            FastAPI app: config.py (build, paths, PRODUCT_CAVEAT until darkvessel.config has it),
                              catalog.py (file registry, build guard), models.py (pydantic,
                              this contract), loaders/ (one module per object type), routes/ (one per resource),
                              chips.py, export.py, decisions.py, serve.py (uvicorn entry)
  frontend/                   package.json, package-lock.json (generated with npm --before), vite.config.ts,
                              index.html, src/ (app/, adapters/http.ts, adapters/embedded.ts, map/, views/,
                              objects/, search/, timeline/, theme/, hotkeys/), public/ (none in the single-file form)
  build/                      build_single.py: runs the Vite single-file build, writes the parts with the backend's
                              loaders and serialisers, applies the drop rules, injects the parts, checks the budget
  checks/                     Playwright scripts (spec section 16), run with the preinstalled Playwright
tests/app/                    backend unit tests (TestClient, synthetic fixtures; no network)
```

Ownership for parallel work without shared files: backend task owns `app/backend/` and `tests/app/`; frontend task owns `app/frontend/`; single-file task owns `app/build/` and `app/checks/`. All three read this contract; a needed change is reported to the PM, not edited in place. The frontend develops against a fixture bundle that the single-file task publishes first (`app/build/fixtures/bundle_small.json`, a few hundred records per type in the section 6 format) and against `/openapi.json` once the backend serves it.

## 8. Sources (resolved in this session, 2026-10-08/09; review fixes 2026-10-09 07:00 to 08:00 UTC)

Repo files and data read in this session: `docs/PROJECT_BOARD.md` (D1 to D3), `scripts/32_cnn_regional.py` outputs (`data/ml/regional_cnn.parquet`, `regional_cnn.json`, `data/detections_regional_verified.gpkg` `about`), `data/live/live_summary.json` and the `about` layer of `live_contacts.gpkg`, `data/ocean_static_summary.json` (`table_columns`, `eez_statement`, `reporting_boxes`), `src/darkvessel/ocean/grid.py` (`REPORTING_BOXES`, `OCEAN_CAVEAT`), `scripts/31_gfw_identity.py` (what `gfw_vessels.parquet` holds), `docs/live_pass.md`, `src/darkvessel/config.py`, `src/darkvessel/live/schema.py`, `src/darkvessel/live/rules.py`, `src/darkvessel/live/identity.py`, `src/darkvessel/live/outputs.py`, `src/darkvessel/ais/gfw.py`, `src/darkvessel/ais/gfw_identity.py`, `scripts/31_gfw_identity.py`, `scripts/22_static_layers.py`, `scripts/21_viirs_regions.py`, `src/darkvessel/ocean/static.py`, `src/darkvessel/ocean/context.py`, `src/darkvessel/weather.py`; the layer lists, row counts, columns and modification times of every file in section 3 (pyogrio and pyarrow, re-checked 2026-10-09 07:00 UTC); the bundle sizes of section 6.3 and the chip sizes of section 6.2 (measured on the real files, 07:00 to 07:40 UTC); `about` layers of `detections_regional.gpkg`, `detections_ml.gpkg`, `viirs_lights.gpkg`, `ais_live.gpkg`, `eez_marineregions.gpkg`, `ocean_context.gpkg`, `optical_check.gpkg`; `data/ocean_daily_summary.json` sources; `data/s1_next_passes.json`.

URLs resolved in this session:
- https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice (PDF, "free, full and open"; credit text).
- https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits (CC BY-NC 4.0, attribution "Powered by Global Fishing Watch.").
- https://creativecommons.org/licenses/by-nc/4.0/ , https://creativecommons.org/licenses/by/4.0/ .
- https://doi.org/10.14284/632 (Marine Regions dataset record, Creative Commons licence).
- https://www.marineregions.org/disclaimer.php (the VLIZ disclaimer quoted in section 3.7).
- https://www.naturalearthdata.com/about/terms-of-use/ (public domain).
- https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use ("placed in the public domain and may be used free of charge").
- https://datacatalog.worldbank.org/search/dataset/0037580 ("Creative Commons Attribution 4.0").
- https://registry.opendata.aws/noaa-jpss/ and https://registry.opendata.aws/noaa-himawari/ (NOAA open data texts quoted in section 2).
- https://registry.opendata.aws/sentinel-1/ ("free, full and open").
- https://raw.githubusercontent.com/allenai/satlas/main/GeospatialDataProducts.md ("All data is released under ODC-BY").
- https://aisstream.io/documentation (limits; no direct browser connections), https://aisstream.io/privacypolicy (200), https://aisstream.io/terms (404).
- https://fastapi.tiangolo.com/tutorial/testing/ (`TestClient`).
- https://fastapi.tiangolo.com/tutorial/path-params/ ("Order matters": the fixed path must be declared before the one with a parameter).
- https://www.imo.org/en/OurWork/Safety/Pages/AIS.aspx (carriage: "all ships of 300 gross tonnage and upwards engaged on international voyages, cargo ships of 500 gross tonnage and upwards not engaged on international voyages and all passenger ships irrespective of size").
- https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/AIS/Resolution%20A.1106(29).pdf (paragraph 22, the switch-off text quoted in section 1.1).
- https://support.marinetraffic.com/en/articles/9552924-why-can-t-i-see-a-vessel-on-the-live-map ("does not cover 100% of the world's seas, but only specific coastal areas where a land-based AIS receiver is installed").
