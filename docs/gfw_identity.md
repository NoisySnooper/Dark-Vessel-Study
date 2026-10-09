# AIS status and identity of the September regional contacts from Global Fishing Watch (research build)

Updated: 2026-10-09 13:55 UTC. Data accessed 2026-10-08 (reports and events) and 2026-10-08 to 2026-10-09 (vessel identities). Research build only. Nothing in this document or in `data/research/` may go into the open (commercial-clean) build.

> **"Dark" does not mean illegal.** It means only that no AIS position was matched to a radar contact. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite and terrestrial AIS have blind spots. An AIS gap is not proof of intent. Every contact row in the GeoPackage and the parquet carries this caveat (`darkvessel.config.DARK_CAVEAT`).

## 1. Research-only notice and licence

Every input of this step comes from the Global Fishing Watch (GFW) API v3 and is licensed CC BY-NC 4.0 (Attribution-NonCommercial 4.0 International, https://creativecommons.org/licenses/by-nc/4.0/ , resolved 2026-10-09). GFW's terms of use (https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits , resolved 2026-10-09) say the APIs are for noncommercial use, ask for attribution in a fixed format, and set limits of 50,000 requests a day and 1,500,000 a month.

Consequences for this project:

- All GFW-derived files live under `data/research/` and the response cache under `data/cache/gfw/` (git-ignored). Every parquet file carries `use`, `licence`, `licence_url`, `terms_url`, `attribution`, `dataset` and `caveat` in its file metadata (readable with `pyarrow.parquet.read_schema(path).metadata`), the GeoPackages carry them in an `about` layer, the COGs in their tags, the JSON summaries as fields. The parquet rows also carry `use` and `licence` columns where the table is small enough for that.
- The product "SCS Vessel Watch" reads these files only in its research build, labelled noncommercial. The open build must never read `data/research/`.
- Nothing here can be shown to Viettel as a commercial deliverable. A commercial AIS feed is needed for that (docs/OWNER_ACTIONS.md).

Attribution format used (GFW terms, section A.3): "Global Fishing Watch. 2026, updated daily. <dataset>, <date range>. Data set accessed <date> at https://globalfishingwatch.org/our-apis/ ." The access date is the fetch date read from the cached responses (`darkvessel.ais.gfw.cache_fetch_dates`), not the day of an offline rebuild: SAR, presence and events 2026-10-08, vessel identities 2026-10-08 to 2026-10-09.

## 2. What GFW data is, and is not

- **GFW SAR detections** (`public-global-sar-presence:v4.0`) are GFW's own detections on the same Sentinel-1 scenes, with GFW's own AIS match. They are another model's output, not ground truth. GFW's data caveats page (https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats , resolved 2026-10-09) says: "we miss most vessels under 15 m in length", and that detection of vessels under 25 m also depends on wind and sea state. Paolo et al. 2024 (Nature, "Satellite mapping reveals extensive industrial activity at sea", doi:10.1038/s41586-023-06825-8, resolved 2026-10-09) report a detection rate above 70 % for 25 m vessels and above 90 % for vessels of 50 m and longer, with the rate falling steeply below 25 m. The same paper finds that 72 to 76 % of the world's industrial fishing vessels are not publicly tracked, much of it in South and Southeast Asia.
- **GFW's "matched" flag** is GFW's AIS match for its own detection. Paolo et al. 2024 (Methods, "SAR and AIS integration") describe the method: AIS positions are interpolated to the moment of the image and a SAR detection is scored against each nearby AIS vessel with probability rasters of where a vessel is likely to be minutes before and after an AIS position; matches are assigned iteratively, best score first. Whether the API's v4.0 dataset uses exactly that code is not stated in the API documentation we read (UNVERIFIED); the flag is in any case GFW's matcher's output, not ours. We take it as the strongest identity evidence available, because GFW has the AIS positions and we do not.
- **GFW AIS presence** (`public-global-presence:v4.0`) is hours of AIS presence per cell and hour per vessel, from GFW's satellite and terrestrial AIS feeds. It shows where AIS was heard; it says nothing about vessels without AIS. In the HOURLY report grouped by vessel id, **a vessel has exactly one 0.01 degree cell per hour** (91,328 rows over 29 passes, never two cells for one vessel and hour). A moving vessel's hourly cell is therefore one sample of a track that can run tens of kilometres in that hour.
- **GFW events** (gaps, encounters, loitering, port visits, all `v4.0`) are GFW's own rule-based or model-based events. GFW calls the gaps dataset a prototype.
- **The 4Wings report endpoint returns cell sums, not positions.** Each row is a 0.01 degree cell (HIGH) or 0.1 degree cell (LOW) with a time step and, when the cell holds one vessel, that vessel's identity. The report's `lat` and `lon` are "the center of the grid cell" (report response fields, https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report), so cells are centred on whole multiples of the cell size. Nothing here is a position match; every match is by cell and hour.
- **GFW's vessels endpoint** gives the AIS self-reported identity (MMSI, name, call sign, IMO, flag) and, for a minority of vessels, registry fields such as length. In our cache, registry lengths exist for a small share of vessels, so the radar-against-AIS length check has a small sample.
- **GFW vessel type GEAR** marks AIS devices on fishing gear (net and gear buoys that transmit AIS; many carry a battery level in the name, such as `04146-3-99%`). A GEAR match places an AIS device near the contact, often on or beside the vessel tending it, but it is not that vessel's identity. The table marks these `identity_kind = gear` with quality low, and the headline counts below give the vessel-identity matches separately.

## 3. Inputs

| Input | Content | Source step |
|---|---|---|
| `data/detections_regional.gpkg` | 78,615 radar candidates of the September regional run (high and medium classes), 119 scenes, 29 passes, 2026-09-20 to 2026-10-01 | scripts/09 |
| GFW SAR reports, one per day and filter | matched true and false at HIGH and HOURLY; neural vessel type at HIGH and DAILY; matched grouped by vessel id | scripts/27 `--steps sar` |
| GFW AIS presence per pass | HIGH, HOURLY, grouped by vessel id, over the pass footprint, pass hour plus one hour each side | scripts/31 `--steps presence` |
| GFW AIS presence per day | LOW, DAILY, whole AOI, summed per cell (GFW returns one row per vessel and cell, about 150,000 rows and 75 MB a day) | scripts/27 `--steps grids` |
| GFW events | gaps, encounters, loitering as parquet tables | scripts/27 `--steps outputs` |
| GFW vessels | identity records of every vessel id the identity needs, batches of 100; ids already present in any cached batch are served from the cache | scripts/31 `--steps vessels` |
| `data/ml/regional_cnn.parquet` | CNN verifier scores (`verifier_v0`) of every regional contact, read-only; 51,816 scored by scripts/32 and 26,799 reused from the shared-cell run (`data/ml/shared_cells_cnn.parquet`, the fallback when the regional file is absent) | scripts/32 |

The pull respects the API's one-concurrent-report rule: reports run in sequence from one process. The client waits on the 429 "one concurrent report" refusal instead of failing (the earlier pull died on it because two processes sent reports at once), recovers a report after a 524 or a client-side timeout through `GET /4wings/last-report` only when that report is the one requested (same dataset and date range; a running report's uri is compared too), and backs off on rate limits honouring Retry-After. Every response is cached by a hash of the request; the token is never part of a key, a file or a log. The SAR report cache is complete for every day from 2026-09-01 to 2026-10-08 (data to 2026-10-05; 10-06 to 10-08 held no cells at pull time and can be refetched with `--refresh-empty`).

## 4. Method and rules

The rules are in `src/darkvessel/ais/gfw_identity.py` and repeated in the `about` layer of the output.

**Cells.** GFW's cell value is the cell centre. Every cell operation here (pairing blocks, status blocks, the comparison in `radar_vs_gfw`, the rasters) uses GFW's grid: cell k is centred on k times the cell size and spans half a cell each side. The data confirm the convention: of 23,088 contacts paired with a GFW SAR detection of the same pass, 69.4 % lie within half a cell of the GFW coordinate in both axes with a median distance of 511 m when the value is read as the centre, against 28.2 % and 854 m when it is read as the south-west corner (`cell_alignment_check` in the summary). An earlier version of the comparison floored GFW centres onto a corner-anchored grid, which put every GFW cell half a cell off; `radar_vs_gfw.json` keeps that number (`corner_anchored_floor (old, wrong)`: 10.8 % of our candidates in a 0.01 degree cell-hour with a GFW detection) next to the correct one (27.5 %) so the change is visible.

**Passes.** The 119 processed scenes group into 29 passes (same mission, consecutive start times less than 20 minutes apart). A contact belongs to the pass of its scene.

**(a) `gfw_sar_cell_hour`, the primary match.** A contact and a GFW SAR detection pair when the detection lies in the contact's 0.01 degree cell or one of its 8 neighbours and GFW's detection time (entryTimestamp) is within 30 minutes of the contact's scene time. Pairs are made one to one, cheapest first; the cost is the distance to the GFW cell centre in km plus |log2(radar length / AIS length)| when GFW publishes a registry length. A pair with a GFW-matched detection gives the identity: vessel id, MMSI, ship name, call sign, IMO, flag and GFW vessel type from the report row, registry length and ship type from the vessels endpoint. A pair with a GFW-unmatched detection is evidence, not a match: GFW saw the same object and found no AIS for it.

match_quality for (a): **high** when the pair is one to one (the contact had one candidate and the detection one rival), within 1 km, and the lengths do not disagree by more than a factor 2 (or no AIS length exists); **low** when both sides were ambiguous (several candidates and several rivals), the lengths disagree by more than a factor 3, the identity came from a cell with several GFW-matched detections, or the identity is a GEAR buoy; **medium** otherwise.

**(b) `gfw_presence_cell_hour`, the secondary match**, only for contacts with no GFW SAR pair. A vessel is a candidate when one of its hourly cells in the pass hour, or the hour before or after, lies within 6 km of the contact. Its position at the scene time is interpolated along its hourly cells (each placed at the hour's midpoint) and its speed proxy is the largest displacement between consecutive hourly cells. The candidate is kept when the interpolated position is within 3 km of the contact and the vessel has at least two hourly cells with a speed proxy of at most 10 km/h. GEAR buoys are not candidates; vessels that GFW's SAR matcher already placed in the same pass are excluded, because GFW has already accounted for them. One to one per vessel, cheapest first (interpolated distance in km plus speed/10). match_quality: **medium** when one to one, within 1 km, speed at most 3 km/h and the vessel has a cell in the contact's hour; **low** otherwise. `match_dist_m` is the interpolated distance; `match_dt_s` the hour offset of the nearest cell (0 or 3600 s).

The thresholds come from a calibration on this run's data (`presence_calibration` in the summary): for the 4,613 contacts that rule (a) identified through GFW's matcher and whose vessel also appears in the pass's presence report, the table below says how far the vessel's presence track is from where GFW's SAR match put it at the scene time.

| Vessel speed proxy | n | Interpolated position within 1 km | within 3 km | Hour-h cell within 1 km |
|---|---|---|---|---|
| 0 to 3 km/h | 1,608 | 85.6 % | 96.8 % | 79.8 % |
| 3 to 10 km/h | 796 | 32.8 % | 82.0 % | 31.5 % |
| over 10 km/h | 1,527 | 10.5 % | 38.6 % | 8.8 % |
| single cell (no speed) | 682 | 25.7 % | 44.3 % | 9.8 % |
| all | 4,613 | 42.7 % | 67.2 % | 37.6 % |

Over 10 km/h the hourly cell says almost nothing about where the vessel was at the scene time, and a single cell is nearly as bad (a vessel seen in only one of three hours over the footprint is usually one that moved in or out of it), so both are rejected. Between 3 and 10 km/h the interpolated position is within 3 km in 82 % of cases: good enough for a low-quality candidate, not for more. Under 3 km/h the cell is the position within 1 km in 86 % of cases: the medium class.

**(c) ais_status.** `matched` when (a) or (b) gives a vessel. Otherwise `no_coverage` when GFW's AIS presence shows zero hours in the 0.3 x 0.3 degree block around the contact (its 0.1 degree cell and the 8 neighbours, on GFW's grid) over the whole window 2026-09-01 to 2026-10-04 (the presence dataset's end date at pull time); otherwise `unmatched`. `no_coverage` never means dark: it means GFW's AIS feed heard nothing there in five weeks. The task text asked for the pass day; the rule here is wider on purpose, so that a day with a receiving outage does not become `no_coverage`. The middle case is kept as evidence: `ais_presence_h_day` is the block's AIS hours on the pass day itself, and 4,749 unmatched contacts have zero there (AIS heard in the block in the window, none on the pass day). `ais_presence_h_window` keeps the window hours so the rule can be re-cut.

**(d) Evidence on every row.** Nearest GFW AIS vessel of the pass window (`nearest_ais_mmsi`, distance to its cell centre, hour-bucket offset; GEAR buoys excluded), distinct AIS vessels within 10 km in the pass hour (`n_ais_10km`) and GEAR buoys within 10 km (`n_gear_10km`), the number of presence candidates the rule (b) thresholds rejected (`pres_n_cand` on unmatched rows), GFW gap events within 50 km and 24 h, encounters and loitering within 10 km and 24 h, GFW's neural vessel type within 1 km on the same date, and `ais_reach` = share of the window's days with any AIS presence in the contact's 0.25 degree cell. `identity_kind` on matched rows: vessel, gear or unknown (no type published).

## 5. Results

All 78,615 contacts of the run carry a status: **matched 9,954** (12.7 %), **unmatched 68,470** (87.1 %), **no_coverage 191** (0.2 %). Of the matched contacts, **9,572 carry a vessel identity**, 278 a GEAR buoy identity (271 through GFW's SAR match, 7 through presence where the vessels endpoint typed the device GEAR) and 104 a GFW vessel id of unknown type.

**Matches by method and quality**

| Method | Quality | Contacts | of which vessel / gear / unknown |
|---|---|---|---|
| `gfw_sar_cell_hour` | high | 1,220 | all vessel (gear and ambiguous identities are never high) |
| `gfw_sar_cell_hour` | medium | 1,677 | |
| `gfw_sar_cell_hour` | low | 2,581 | includes all 271 gear identities of this method |
| `gfw_sar_cell_hour` | all | 5,478 | 5,187 / 271 / 20 |
| `gfw_presence_cell_hour` | medium | 59 | |
| `gfw_presence_cell_hour` | low | 4,417 | |
| `gfw_presence_cell_hour` | all | 4,476 | 4,385 / 7 / 84 |

GFW SAR pairs: 5,478 contacts paired with a GFW-matched detection that carries an identity, 133 with a GFW-matched detection whose identity the reports do not give (rule (b) is tried for them), 6,361 with a detection GFW found no AIS for (GFW's own dark count, kept as evidence), 66,643 with no GFW detection in the block within 30 minutes. GFW SAR detected 17,911 objects on the run's dates inside the AOI, 6,205 of them AIS-matched by GFW, 269 of those to GEAR devices.

Presence matches: 4,476 contacts, all with at least two hourly cells (3,195 with three, 1,281 with two), median speed proxy 0 km/h (75th percentile 3.3, 90th 6.5), median interpolated distance 879 m, 54 % within 1 km. Only 59 reach medium, because medium needs a near-stationary vessel alone in the block within 1 km in the contact's own hour. On the unmatched side, 16,723 contacts had at least one presence candidate within 6 km that the thresholds rejected (too fast, a single cell, or farther than 3 km once interpolated).

Identity completeness of the 9,954 matched contacts: MMSI on 9,850 (99.0 %), name on 9,470 (95.1 %), flag on 9,028 (90.7 %), GFW vessel type on 9,850 (99.0 %), registry length on 161 (1.6 %). The 104 matched contacts without an MMSI (20 SAR, 84 presence) carry only a GFW vessel id: GFW publishes no MMSI, name, flag or type for those ids and the vessels endpoint returns no record for them (9,893 records came back for the 10,018 ids requested). They stay matched, because GFW placed an AIS track there; the identity is GFW's internal id alone and `identity_kind` is unknown.

**By mission, class and radar length**

| Group | Contacts | Matched | Unmatched | No coverage | Matched share |
|---|---|---|---|---|---|
| Mission: S1C | 13,283 | 1,609 | 11,642 | 32 | 12.1 % |
| Mission: S1D | 65,332 | 8,345 | 56,828 | 159 | 12.8 % |
| Detector class: high | 29,228 | 6,107 | 23,073 | 48 | 20.9 % |
| Detector class: medium | 49,387 | 3,847 | 45,397 | 143 | 7.8 % |
| Radar length: 0 to 25 m | 29,324 | 1,517 | 27,698 | 109 | 5.2 % |
| Radar length: 25 to 50 m | 16,232 | 1,383 | 14,813 | 36 | 8.5 % |
| Radar length: 50 to 100 m | 18,088 | 2,253 | 15,799 | 36 | 12.5 % |
| Radar length: 100 m and longer | 14,971 | 4,801 | 10,160 | 10 | 32.1 % |

**By sub-region** (plain reporting boxes, not boundaries)

| Region | Contacts | Matched | Unmatched | No coverage | Matched share |
|---|---|---|---|---|---|
| Gulf of Thailand | 18,796 | 1,151 | 17,488 | 157 | 6.1 % |
| Gulf of Tonkin | 17,965 | 1,944 | 16,021 | 0 | 10.8 % |
| North shelf | 13,330 | 3,157 | 10,173 | 0 | 23.7 % |
| other | 10,635 | 1,369 | 9,256 | 10 | 12.9 % |
| Southern sea | 8,690 | 1,193 | 7,496 | 1 | 13.7 % |
| South Vietnam shelf | 7,934 | 1,113 | 6,821 | 0 | 14.0 % |
| Central sea | 1,265 | 27 | 1,215 | 23 | 2.1 % |

**CNN verifier.** Every contact has a verifier score (`data/ml/regional_cnn.parquet`, scripts/32: 51,816 scored there, 26,799 reused from the shared-cell run; `verifier_v0`, threshold 0.632). The CNN accepts 24,114 of the 78,615 contacts. Among the accepted, 26.9 % are matched (high class 28.0 %, medium 22.6 %); among the rejected, 6.4 %. The verifier and GFW's AIS match agree on what a vessel looks like, which is the expected direction, and 6,484 of the 9,954 matched contacts (65.1 %) are CNN-accepted. The verifier was trained on Sentinel-1A/1B labels and applied to 1C/1D without retraining (docs/ml_verifier.md), so acceptance is a prior for the lead score, not truth.

**Radar length against AIS length.** GFW publishes a registry length for 161 of the 9,954 matched contacts (1.6 %). On that sample the Spearman rank correlation is 0.376 (p = 9.0e-07, n = 161); the median ratio radar length over AIS length is 1.59, and 51.6 % of the pairs agree within a factor 2 (median radar length 200.8 m, median AIS length 128.0 m). By AIS length bin (n, median radar length): 0 to 25 m (7, 40.0 m), 25 to 50 m (8, 81.8 m), 50 to 100 m (34, 123.7 m), 100 m and longer (112, 262.1 m). The radar estimate runs long at every size: the CFAR blob includes sidelobes and wake at 20 m pixels, and the registered vessels are the large ones.

**Evidence on the unmatched contacts** (68,470): 68,285 have a nearest GFW AIS vessel in the pass window (median distance 8.4 km); 6,359 were also detected by GFW's SAR with no AIS match by GFW; 16,723 had a presence candidate that the rule (b) thresholds rejected; 7,136 have a GEAR buoy within 10 km in the pass hour; 4,749 had no AIS heard in their 0.3 degree block on the pass day; 0 lie within 50 km and 24 h of a GFW gap event (the window holds 5 gap events in the whole AOI); 2,654 within 10 km and 24 h of a GFW encounter; 15,511 within 10 km and 24 h of a GFW loitering event. GFW's neural vessel type at the cell: none 49,210, Unknown 8,402, Likely Fishing 7,658, Likely non-fishing 2,369, mixed 831.

Median `n_ais_10km` by status: matched 17, no_coverage 0, unmatched 0. Median `ais_reach`: matched 1.0, no_coverage 0.0, unmatched 1.0. Median `ais_presence_h_window`: matched 39,988 h, no_coverage 0, unmatched 2,150 h.

**Inputs actually used.** GFW AIS presence per pass: 91,328 vessel-cell-hour rows over 29 passes. Daily 0.1 degree presence: 34 days with data (2026-09-01 to 2026-10-04), 557,348 cell-days. No GFW-matched cell held several detections on the run's dates, so the by-vessel identity recovery (rule (a), ambiguous cells) was not needed; the 133 GFW-matched detections without identity are rows whose vessel id GFW left empty.

## 6. Hand-checked sample

Twenty-one matches checked against the raw cached GFW report rows by a scratch script that reads the cache files directly, not through the project's parsers (2026-10-09): the ten SAR matches of quality high (one per pass where possible), three SAR low, three presence medium, three presence low, one GEAR identity and one presence match of a moving vessel. For each SAR match the script finds the matched=true HOURLY row with that vessel id on that date and checks that the contact lies inside the cell (offsets within 0.005 degree in both axes), that GFW's detection time is within 30 minutes, and that MMSI, name and type equal the table's. For each presence match it reads the vessel's hourly cells from the per-pass report, recomputes the interpolated position and the speed proxy, and checks the 3 km, 10 km/h, two-cell and GEAR rules and the table's distance. All 21 hold.

| # | Group | Contact | Scene time (UTC) | Contact lon, lat | Radar length (m) | Quality / kind | GFW cell(s) | dist (m) | speed (km/h) | MMSI | Name | Flag | GFW type |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | sar_high | `S1C_20260920T104816_01590` | 10:48:16 | 109.1343, 18.2734 | 381.2 | high / vessel | cell 109.13, 18.27; 10:48:31 | 592 | none | 413210760 | ZHONG CHUAN HAIGONG6 | CHN | OTHER |
| 2 | sar_high | `S1C_20260922T224255_00246` | 22:42:55 | 107.8199, 17.6977 | 95.2 | high / vessel | cell 107.82, 17.70; 22:43:10 | 252 | none | 574933325 | none | VNM | FISHING |
| 3 | sar_high | `S1C_20260926T095911_01970` | 09:59:11 | 122.2609, 19.5212 | 40.0 | high / vessel | cell 122.26, 19.52; 09:59:18 | 169 | none | 241669000 | WOODSIDE REES WITHER | GRC | OTHER |
| 4 | sar_high | `S1C_20260927T225047_00155` | 22:50:47 | 106.2655, 18.8708 | 161.3 | high / vessel | cell 106.27, 18.87; 22:51:02 | 485 | none | 574943903 | DAIDUONG69 | VNM | FISHING |
| 5 | sar_high | `S1C_20260928T111904_00008` | 11:19:04 | 102.2337, 10.1851 | 320.1 | high / vessel | cell 102.23, 10.19; 11:19:19 | 681 | none | 314082000 | SYNVAL | BRB | CARGO |
| 6 | sar_high | `S1C_20260928T215153_00097` | 21:51:53 | 120.1242, 22.8833 | 135.7 | high / vessel | cell 120.12, 22.88; 21:52:06 | 569 | none | 416001869 | LONG YU FA NO1 | TWN | FISHING |
| 7 | sar_high | `S1C_20260929T223510_02006` | 22:35:10 | 108.5086, 15.9977 | 103.7 | high / vessel | cell 108.51, 16.00; 22:35:25 | 292 | none | 574015439 | THUAN DAT 126 | VNM | OTHER |
| 8 | sar_high | `S1C_20261001T100536_01695` | 10:05:36 | 120.3801, 12.4831 | 403.5 | high / vessel | cell 120.38, 12.48; 10:05:49 | 344 | none | 414005000 | LI DIAN 5 | CHN | CARGO |
| 9 | sar_high | `S1D_20260920T113435_00219` | 11:34:35 | 100.7958, 7.2494 | 30.0 | high / vessel | cell 100.80, 7.25; 11:34:48 | 472 | none | 567222227 | SOR.PORN 27 CH 15B | THA | FISHING |
| 10 | sar_high | `S1D_20260920T221343_01434` | 22:13:43 | 113.0161, 4.9857 | 92.2 | high / vessel | cell 113.02, 4.99; 22:13:58 | 640 | none | 533133123 | CENTUS SOFFIA | MYS | OTHER |
| 11 | sar_low | `S1C_20260920T104816_01595` | 10:48:16 | 109.1195, 18.2926 | 151.2 | low / vessel | cell 109.12, 18.29; 10:48:31 | 300 | none | 412464470 | NANHAI218 | CHN | OTHER |
| 12 | sar_low | `S1C_20260922T224255_00196` | 22:42:55 | 107.8541, 17.6986 | 95.3 | low / vessel | cell 107.85, 17.70; 22:43:10 | 465 | none | 574586837 | none | VNM | NA |
| 13 | sar_low | `S1C_20260926T095951_00918` | 09:59:51 | 120.6719, 21.9631 | 90.5 | low / vessel | cell 120.67, 21.96; 10:00:06 | 398 | none | 416230800 | SING MAN YI NO3 | TWN | FISHING |
| 14 | presence_medium | `S1C_20260920T104816_02352` | 10:48:16 | 108.6289, 18.9918 | 20.0 | medium / vessel | 09:00 and 10:00 at 108.63, 19.00 | 923 | 0.00 | 413390480 | SHUN DA 2608 | CHN | OTHER |
| 15 | presence_medium | `S1C_20260922T224255_00277` | 22:42:55 | 107.7088, 17.6609 | 41.1 | medium / vessel | 21:00 at 107.71, 17.65; 22:00 at 107.70, 17.66 | 939 | 1.54 | 574226810 | none | VNM | NA |
| 16 | presence_medium | `S1D_20260920T113525_04128` | 11:35:25 | 99.9763, 9.9057 | 41.6 | medium / vessel | 10:00 and 11:00 at 99.98, 9.91; 12:00 at 99.97, 9.93 | 744 | 2.48 | 686098609 | RUNGWAREERAT 3 | none | OTHER |
| 17 | presence_low | `S1C_20260920T104816_00977` | 10:48:16 | 109.5180, 18.2048 | 63.8 | low / vessel | 09:00 and 11:00 at 109.53, 18.22 | 2,109 | 0.00 | 500812345 | CCG 5008 | none | OTHER |
| 18 | presence_low | `S1C_20260920T230037_00185` | 23:00:37 | 101.2472, 12.6227 | 216.9 | low / vessel | 22:00, 23:00 and 00:00 at 101.24, 12.61 | 1,616 | 0.00 | 567004317 | VENUS 23 | THA | OTHER |
| 19 | presence_low | `S1C_20260922T224255_00295` | 22:42:55 | 107.5840, 17.6561 | 100.0 | low / vessel | 21:00 and 22:00 at 107.61, 17.65 | 2,837 | 0.00 | 574204069 | none | VNM | FISHING |
| 20 | gear_identity | `S1C_20260920T104845_00408` | 10:48:45 | 109.0393, 19.8206 | 40.0 | low / gear | cell 109.04, 19.82; 10:49:02 | 102 | none | 41463333 | 04146-3-99% | none | GEAR |
| 21 | presence_moving | `S1C_20260920T104816_01675` | 10:48:16 | 109.1565, 18.2644 | 59.5 | low / vessel | 09:00 at 109.14, 18.31; 11:00 at 109.15, 18.25 | 1,274 | 3.38 | 413054670 | NAN HAI JIU 102 | CHN | OTHER |

What the sample shows. The SAR matches (1 to 13) put the contact inside GFW's cell with GFW's detection time 7 to 17 s after the scene start (the time the beam reached that cell); the low ones are low because of length disagreement or a crowded cell, not geometry. Samples 2, 12, 15 and 19 show a limit of GFW's identity: an MMSI with no published name. Sample 20 is a GEAR match: GFW's SAR matcher tied the detection to an AIS buoy with a battery-level name; the contact is 102 m from the cell centre, so something was there, but the buoy's MMSI is not a vessel's identity and the row says so. Samples 17 to 19 are stationary vessels (speed 0) 1.6 to 2.8 km from the contact: consistent, not confirmed, hence low. Sample 21 is a vessel moving at 3.4 km/h whose two cells (09:00 and 11:00) bracket the scene and whose interpolated position is 1.3 km from the contact; it is low because it moved and because its cell in the contact's hour is missing. None of the 21 carries a registry length, which is the normal case (1.6 % of matched contacts have one).

## 7. Limits

- GFW reports are cell sums. Even a high-quality match says "a GFW-matched detection of this vessel sits in the same 0.01 degree cell at the same scene time", not "this pixel is that vessel". Two vessels in one cell at the same time cannot be told apart; many-to-one cells get `low` quality.
- GFW SAR misses most vessels under 15 m and many under 25 m, and our detector has its own misses, so the SAR pairing covers mainly the larger contacts. The unmatched share by length bin says more about detectability than about AIS carriage.
- GFW's presence hours are rounded to whole hours per cell and hour, and a vessel has one cell per hour. Rule (b) accepts only vessels with two or more hourly cells and a speed proxy under 10 km/h, because the calibration above shows the hourly cell of a faster or single-cell vessel is usually kilometres from where the vessel was. The consequence: an AIS-carrying vessel under way at cruising speed that GFW's SAR did not pair stays `unmatched`. Near shipping lanes, `unmatched` without a GFW SAR pair is therefore weak evidence; `pres_n_cand` greater than zero on such a row says a candidate was there and was rejected for being too fast or too far.
- The presence match is a candidate by consistency, not an identification: 4,417 of the 4,476 presence matches are low. The product should show them as "AIS vessel consistent with this contact", never as a confirmed identity.
- A GEAR identity is an AIS device on fishing gear, not a vessel. The 278 gear matches are kept because GFW's matcher or the vessels endpoint produced them, flagged `identity_kind = gear` and low.
- GFW AIS lags days to weeks (the presence and events datasets ended on 2026-10-04 at pull time; SAR detections on 2026-10-05). Days re-pulled later can gain data; `--refresh-empty` refetches cached empty days.
- Registry lengths exist for a small share of vessels, so the length check is small and biased toward registered industrial vessels.
- `no_coverage` depends on GFW's AIS receiving network and on the five-week window; a block with no AIS heard in five weeks is a receiving gap or empty sea, not evidence about any vessel.
- The AOI polygon sent to GFW is simplified to under 1,000 vertices (tolerance 0.02 degree), so a thin coastal strip may be cut or added at the edges.

## 8. Files

| File | Content |
|---|---|
| `data/research/regional_identity.parquet` (4.0 MB) | the canonical table: all 78,615 contacts, D1 columns plus evidence columns (CNN score and verdict on every row), the full dark caveat on every row, `research_only` true, licence, attribution, dataset versions, access dates and caveats in the parquet file metadata |
| `data/research/regional_identity.gpkg` (112.5 MB) | layers `contacts_4326`, `contacts_utm49n` (the same table with the full caveat on every row, as D1 requires) and `about` (method, rules, dataset versions, access dates, licence, attribution, caveats, cell alignment and presence calibration). Far over the 20 MB commit limit and over GitHub's 100 MB limit: SQLite stores the 323-character caveat 157,230 times. It is rebuilt in about two minutes from the cache with `python scripts/31_gfw_identity.py --steps outputs --offline`, so it must be git-ignored (`data/research/regional_identity.gpkg`); the parquet is the committed copy |
| `data/research/regional_identity_summary.json` | counts by status, method and quality, identity kind, mission, length bin, sub-region, CNN verdict (`cnn_source` names the score file); length check; cell alignment check; presence calibration; attribution per dataset; hand-check sample with the raw GFW rows |
| `data/research/gfw_vessels.parquet` (0.7 MB) | identity records of the vessel ids used by the table (report fields plus vessels endpoint fields, `identity_kind`) |
| `data/research/gfw_presence_passes.parquet` (2.2 MB) | GFW AIS presence rows per pass (vessel, cell, hour) with identity, 91,328 rows |
| `data/research/gfw_presence_daily.parquet` (1.3 MB) | AIS presence hours and vessel counts per 0.1 degree cell and day, whole AOI, 557,348 cell-days |
| `data/research/gfw_ais_presence_hours_*.tif` | AIS presence hours per 0.1 degree cell over the window on GFW's grid, EPSG:4326 and UTM 49N |
| `data/research/radar_vs_gfw.{json,parquet}` | our candidates against GFW SAR cell counts on GFW's grid (scripts/27 `--steps compare`): 27.5 % of our candidates share a 0.01 degree cell-hour with a GFW detection, 88.4 % of GFW's detections share one with ours; 60.0 % and 98.3 % at 0.1 degree |

The other GFW products (SAR detection tables and densities, fishing effort, events, vessels in events) are written by `scripts/27_gfw_pull.py` and listed in its docstring; all of them carry the same stamps.

## 9. Sources resolved in this session (curl, 2026-10-09)

- CC BY-NC 4.0: https://creativecommons.org/licenses/by-nc/4.0/
- GFW terms of use, licence and rate limits: https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits
- GFW 4Wings report endpoint (parameters, response fields with "center of the grid cell", one concurrent report, 524 and last-report): https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/report
- GFW last-report endpoint (running response with the report uri): https://globalfishingwatch.org/our-apis/documentation/docs/v3/4wings/last-report
- GFW data caveats (SAR misses most vessels under 15 m): https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats
- GFW vessels endpoint: https://globalfishingwatch.org/our-apis/documentation/docs/v3/vessels/get-vessels
- GFW datasets endpoint (dataset ids, report groupings): https://globalfishingwatch.org/our-apis/documentation/docs/v3/datasets
- GFW documentation as one text file (quotes checked against it): https://globalfishingwatch.org/our-apis/documentation/llms-full.txt
- Paolo, F. S. et al. (2024). Satellite mapping reveals extensive industrial activity at sea. Nature. https://doi.org/10.1038/s41586-023-06825-8 (detection rates by length; Methods on AIS interpolation and SAR-AIS matching)
