# Leads queue: dark-lead scoring for SCS Vessel Watch (L1 and L7, open and research builds)

Updated: 2026-10-10 02:00 UTC (review fixes of task R2-T1). Code: `src/darkvessel/leads/` and `scripts/33_leads.py`; tests: `tests/test_leads.py`. Schema: `app/CONTRACT.md` section 3.5; rules and weights: `docs/product_design.md` section 4.1 and `docs/research/mda_products_brief.md` sections 2.5 to 2.7; shared decisions D1 to D4 in `docs/PROJECT_BOARD.md`.

> 'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent.

This is the product caveat (`PRODUCT_CAVEAT`, contract 1.1, now also `darkvessel.config.PRODUCT_CAVEAT`; a test asserts the two are equal). It is 475 bytes. Every lead row, every evidence row and the about layer of both builds carry it; research rows append "Research build, noncommercial, CC BY-NC 4.0. Contains Global Fishing Watch data. Powered by Global Fishing Watch." (589 bytes in all).

## 1. What a lead is, and is not

A lead is one object (a radar contact) or one cell, the evidence on it, a review priority and the caveat. It is a work item for the analyst's queue. It is not a finding: the product never says illegal, suspicious or violation, and the priority is a review priority, not a risk score and not a probability of anything. "No AIS match" is the gate that makes an L1 lead; it adds 0 points by itself, because an absence of AIS is weak evidence in waters where most boats need not carry it and shore receivers hear only part of the sea.

Every lead is `new` and its history is empty. Decisions (reviewing, closed_explained with a reason from the picklist, closed_unexplained, closed_false_alarm) come from the app's append-only log, never from this script. Every lead carries the lawful explanations and the change indicators pre-listed for its type (section 5), so the card can say what else would explain it and what would change it.

## 2. Lead types built

### 2.1 L1, unmatched radar contact in AIS reach

A contact passes the L1 gate when all of these hold (`darkvessel.leads.rules.l1_gate`):

| Condition | Rule | Why |
|---|---|---|
| unmatched | `ais_status` = `unmatched` | AIS was heard near the contact (open: in its 0.25 degree cell or within 20 km; research: in the 0.3 degree block), so an absence of a match means something. `no_coverage` contacts never make a lead; `matched` ones have an identity |
| vessel_class | `confidence` high or medium | low and fixed objects stay out of the product (contract behaviour rule 4) |
| cnn | `cnn_score` >= 0.5 | below the verifier threshold 0.632 but above chance; `cnn_vessel` is never used as a filter because acceptance for contacts under 25 m is 0.9 % (`docs/ml_verifier.md`) |
| both_channels | `pol_class` = `VV+VH` where known; else the regional class high (which means VV and VH) | one-channel returns are more often clutter; unknown polarisation is kept |
| not_clutter | `n_low_1km` < 5 and `low_reason` not `clutter_zone`, where the columns exist | the detector's clutter-zone rule; the regional run already downgraded such objects to low, so no regional row fails here |
| not_near_fixed | `near_fixed_m` > 250 and `low_reason` not `near_fixed`, where known | platforms, turbines and sidelobes |
| weather | gated per part: excluded when `wind_ms` is known and at least 12, or when `deep_convection` is known and true | wind-roughened sea and rain cells make false returns (`docs/scs_regional.md`, `data/weather_context.json`: 39 % of the objects the clutter rule removes sit under deep convection, against 23 % of the both-channel candidates it keeps). When one part is missing and the known part passes, or both are missing, the lead is kept and its factor list carries "weather unknown" with 0 points, naming the missing part (board D4.5) |

The weather rule gates each part on its own. A known failure always excludes the contact, even when the other part is missing: a contact under deep convection with no wind value is not an L1 lead. The 5 weather points of evidence quality need both parts known and calm. The column `weather_missing` says which part is missing (`wind`, `deep convection`, or both).

Counts, research build (September regional run, 78,615 contacts with GFW identity, `data/research/regional_identity.parquet`, weather from `data/weather_context.parquet`): 68,470 unmatched; among them 47,073 fail the CNN condition, 45,397 the both-channels condition and 19,094 the weather condition (the conditions overlap; each count is against all unmatched rows). **12,124 pass**: 11,997 with wind and deep convection both known and calm, and 127 with the wind value missing and deep convection known false. No passing contact lacks the convection value. This equals the 12,124 candidates counted in contract section 6.2.

Counts, open build: 0. All 3,081 contacts of the first live pass (`live_S1D_20261008T2258`) are `no_coverage`: no AIS was heard inside or near the footprints (`docs/live_pass.md`). The open L1 path runs on every rerun and produces leads as soon as a live pass has `unmatched` contacts. Live passes have no weather join yet, so open L1 leads will carry "wind and deep convection unknown (no weather sample joined for this pass yet)".

Of the 12,124 research L1 leads, 7 are in the high band, 8,830 medium and 3,287 low (priority minimum 11, quartiles 33, 39 and 43, maximum 74; the 10th to 90th percentile of the medium band is 36 to 53). By reporting box: Gulf of Tonkin 4,381, North shelf 3,251, Gulf of Thailand 1,660, Southern sea 1,074, South Vietnam shelf 803, Central sea 52, outside the boxes 903. 27 leads have a radar length under 25 m. 1,584 (13 %) have an unmatched contact within 2 km on another pass within 72 h. 485 (4 %) have a GFW loitering or encounter event within 2 km and 3 h.

### 2.2 L7, lit activity where radar does not look

A coverage statement for tasking, not a vessel lead: it names no vessel. One lead per 0.25 degree model-grid cell (`cell_id` = `r<row>c<col>`, contract 3.7) that holds at least one VIIRS Day/Night Band light of class `lit_vessel_candidate` with `quality` clear and `s1_passes_90d` = 0 (no Sentinel-1 pass over its 0.05 degree coverage cell between 2026-07-04 and 2026-10-02), more than 1 km from a Satlas marine-infrastructure point. The lead's position is the mean of its lights, its time the last light. Its evidence lists the 20 brightest lights (by radiance; `n_lights` holds the full count) and every recurring light site within 500 m of one of its lights.

Lights come from `data/viirs_lights_all.gpkg`, which holds every night from 2026-09-05 to 2026-10-01 (27 nights). The lean `data/viirs_lights.gpkg` (10 of those nights) is the fallback when the full file is missing, and it is always the source of the 2,129 recurring light sites. The about layer's `input_notes` says which file a build used.

Counts (both builds): 191,622 lights, of which 164,333 are lit-vessel candidates and 27,289 persistent lights (not used); 123,756 clear lit-vessel candidates; 41,801 of those never imaged in 90 days; 41 excluded as within 1 km of Satlas infrastructure; **41,760 kept in 2,137 cells**. Nights per cell: one night 295 cells, two 256, three 248, four to six 655, seven or more 683. 221 of the lights lie within 500 m of a recurring light site. 25,427 light evidence rows (the 20-per-cell cap). By reporting box: Central sea 1,207, North shelf 293, South Vietnam shelf 250, Southern sea 95, Gulf of Tonkin 17, Gulf of Thailand 0, outside the boxes 275. In none of the 2,137 cells does the live AIS feed hear anything (`ais_reach_share` 0 everywhere), and only 44 cells (2.1 %) lie under a pass in the current plan window, which is the point of the lead. Every L7 lead is in the low band (section 3).

### 2.3 Types not built in this round

L2 (AIS silence open at a radar look), L3 (possible meeting), L4 (encounter), L5 (identity or position anomaly), L6 (activity anomaly cell) and L8 (area entry) need event tables that do not exist yet in the open build (`data/events_open.gpkg` is pending) or, in the research build, were left for a later round. The research build has 5 GFW gap events in the window and 13,516 encounters; a lead per encounter would flood the queue with AIS-only events, so L4 waits for the product's event pages. The lead builder is typed so that a new type is one more `build_<type>` function.

## 3. Review priority: factors and weights

`priority_model_id` = `lead_priority_v0_20261009`, `calibrated` = false. Five factors with fixed maximum points, summed and clipped to 0 to 100. Bands: low 0 to 33, medium 34 to 66, high 67 to 100. Each lead stores the factor list (`factors`, JSON: factor, value, points, max_points, source) and one integer column per factor (`pts_evidence_quality`, `pts_corroboration`, `pts_ais_reach`, `pts_persistence`, `pts_area_weight`) for the bundle; the clipped sum of the five columns equals `priority` on every row (tested, and checked on both output files). A factor's `source` is one or more source keys of the contract section 2 registry, joined by "; " (for example `cnn_v0; det_regional; gfs_wind; himawari_ctt`).

**L1 (one radar contact)**

| Factor | Max | Start rule |
|---|---|---|
| evidence_quality | 30 | 20 x (cnn_score - 0.5) / 0.5, clipped; plus 5 when both channels; plus 5 when wind and deep convection are both known, wind below 12 m/s and no deep convection (0 otherwise, with the extra entry "weather unknown" when a part is missing) |
| corroboration | 25 | 15 when a VIIRS lit-vessel candidate lies within 2 km and 3 h of the contact; 10 when an AIS behaviour event does (research: GFW loitering or encounter whose span, padded by 3 h, contains the contact time; open: no event source yet). A nearby AIS vessel that did not match is evidence on the card and adds nothing |
| ais_reach | 20 | 12 x `ais_reach` (share of hours, open; share of window days with GFW presence in the cell, research) plus 8 x min(1, `n_ais_10km` / 10): how much an absence of AIS can mean here |
| persistence | 15 | 15 when an unmatched high or medium contact lies within 2 km on another pass (more than 10 minutes apart, a different pass id) within 72 h |
| area_weight | 10 | analyst-set per reporting box (`--area-weights` JSON), default 0 |

**L7 (one cell, a coverage lead), scored within the same factor meanings**

| Factor | Max (spec max) | Start rule |
|---|---|---|
| evidence_quality | 10 (30) | 10 x min(1, ln(1 + n_lights) / ln(31)): 2 points for one light, 10 at 30 or more. A light shows lit activity, not a vessel at a radar look (no length, no class, no time match), so L7 uses a third of the L1 scale |
| corroboration | 0 (25) | none by construction: radar did not image the cell in 90 days and the lights are not matched to AIS |
| ais_reach | 0 (20) | an L7 lead makes no claim about AIS; the cell's `ais_reach_share` is shown as context |
| persistence | 10 (15) | lights on another night, the L7 reading of "seen again": 10 x min(1, (n_nights - 1) / 6), so one night scores 0 and seven or more nights score 10 |
| area_weight | 10 | as L1 |

Without an analyst area weight an L7 lead scores at most 20, always in the low band. The factor list keeps the spec maximum in `max_points` and says "L7 scale" in `value`. Why the ceiling: the owner's first priority is vessel detection and identification, and the queue's default sort is priority. A first version scored L7 corroboration as the number of nights and L7 persistence as the share never imaged (the selection criterion itself); the review counted 102 coverage cells in the high band and 112 L7 leads among the research top 120. A second step capped L7 at 30, but open L1 leads have no weather join and no event source yet, and 34 % of research L1 leads score 30 or less once their weather and corroboration points are removed. At 20, 93 % of research L1 leads stay above every L7 lead on that open-build reading, and every L1 lead with CNN 0.75, both channels and an AIS reach of 0.5 scores 21 or more (tested: `test_l7_points_within_spec_meaning_and_ceiling`).

Queue order now: in the research build the first 100 and the first 1,000 leads by priority are all L1. In the open build all 2,137 current leads are L7, because no live contact is unmatched yet. A simulated open pass (the 600 random live contacts set to unmatched, given an AIS reach between 0.1 and 0.9 and 0 to 5 AIS vessels within 10 km, weather unknown) gave 139 L1 leads with priority quartiles 20, 26 and 30 and a maximum of 37; 103 of them score above 20, and the first 100 of the queue were all L1.

Distributions (research build): L1 evidence_quality median 25, ais_reach median 12, corroboration mean 0.4, persistence mean 2.0; L7 priority quartiles 8, 14 and 19, evidence_quality median 7, persistence median 7.

## 4. Evidence and the corroboration window

The `lead_evidence` layer and the `evidence` JSON column hold the same rows: `{type, id, role}` with roles `primary`, `pass`, `nearest_ais`, `same_night_light`, `ais_behaviour`, `persistence` (at most 5 contacts), `weather` (when a weather sample exists for the contact), `cell` for L1 and `primary`, `light` (the 20 brightest), `recurring_site` for L7. Vessel ids are `mmsi:<mmsi>` (open) or `gfw:<vessel_id>` (research). Research: 93,499 evidence rows; open: 27,676.

How often can the 3 h corroboration window hit? Sentinel-1 passes over the AOI start at 09 to 11 UTC and 21 to 23 UTC; the VIIRS overpasses in the light file fall at 16 to 19 UTC. By time of day, 11 of the 32 September pass times lie within 3 h of a VIIRS overpass time (the smallest gap is 1.85 h). In absolute time, 6 of the 32 passes lie within 3 h of a VIIRS light (smallest gap 2.4 h): the VIIRS nights (2026-09-05 to 2026-10-01) cover the whole radar run (2026-09-20 to 2026-10-01). Still, no research L1 lead has a lit-vessel candidate within 2 km and 3 h, so `n_lights_2km_3h` is 0 on every lead. The window is what limits it, not missing nights: within 2 km, 153 L1 leads have a light within 6 h and 1,393 within 12 h. A wider window would score a light seen hours before or after the radar look, which is weaker evidence that it is the same object; the 3 h rule stays until calibration says otherwise. The AIS behaviour part hits on 485 research leads.

Context columns on every lead (location context only, never a factor): `cell_id`, `depth_mean_m`, `dist_coast_km`, `dist_port_km` from `data/ocean_static_cells.parquet`. The Marine Regions attributes are not read and are never a factor.

## 5. Lawful explanations and change indicators

Rows carry the codes (`lawful_explanations`, `change_indicators`: JSON lists of strings); the about layer and this section carry the sentence of each code.

L1 lawful explanations:
- `no_carriage_requirement`: the IMO AIS carriage rule covers "all ships of 300 gross tonnage and upwards engaged on international voyages, cargo ships of 500 gross tonnage and upwards not engaged on international voyages and all passenger ships irrespective of size"; most fishing boats and small craft on domestic voyages are outside it.
- `vms_fleet`: Vietnamese fishing vessels of 15 m and over report by VMS, which this product does not see. UNVERIFIED.
- `class_b_out_of_range`: class B transponders transmit at 5 W (2 W for carrier-sense units) against 12.5 W for class A (USCG NAVCEN class comparison), so receivers hear them over a shorter range (our inference from the power figures; the source gives the powers, not ranges); satellite AIS misses messages in busy coastal waters.
- `lawful_switch_off`: IMO Resolution A.1106(29) paragraph 22: "If the master believes that the continual operation of AIS might compromise the safety or security of his/her ship or where security incidents are imminent, the AIS may be switched off."
- `detector_false_positive`: the CNN verifier's held-out precision is 0.77 on Sentinel-1A/1B labels; sea clutter, rain cells, fixed structures, sidelobes and ambiguities remain possible.

L1 change indicators: `late_ais_match`, `next_radar_look` (`next_look_utc`), `optical_view`, `chip_false_alarm`, `owner_label`.

L7 lawful explanations:
- `lawful_fishing_lights`: the GFW VIIRS layer "is likely to show vessels associated with activities like squid fishing, which use bright lights and fish at night".
- `recurring_light_not_vessel`: recurring lights can be platforms, flares, islands, navigation lights or anchorages.
- `dnb_false_sources`: the AI2 VIIRS detector filters "auroral lit clouds, moonlit clouds, image artifacts (bowtie/noise smiles, edge noise), near shore detections, non-max suppression, lightning, and gas flares" by default.
- `coverage_statement_only`: the lead states a radar coverage gap for tasking; it is not a vessel lead and names no vessel.

L7 change indicators: `radar_acquisition`, `more_clear_nights`, `ais_reach_improves`.

## 6. Next radar look

`next_look_utc` is the start of the first planned Sentinel-1 pass that starts after both the lead's time and the plan's `generated_utc`, and whose footprint contains the lead point, from `data/s1_next_passes.json` (footprint polygons from `data/ais_live.gpkg` layer `s1_next_passes_4326`, else the plan's bounding box). Passes with status `past` are skipped. The plan window starts 72 h before the plan is generated, so without the plan-time rule a September lead would have been given a pass that had already flown (the review counted 4,651 research leads with such a value in the first build). The plan time, not the wall clock, is the reference, so reruns stay byte-identical; between plan refreshes (every 6 h, by the AIS watchdog) a value can be up to 6 h stale. `next_look_pass` names the pass group and `next_look_source` is `esa_plan` or `repeat_cycle`, which is a prediction from the 12-day repeat and not ESA's plan (Sentinel-1 flies a "12 day repeat cycle and 175 orbits per cycle for a single satellite"). For September leads the value is the next look from the plan window, not the first look after the lead. 12,117 of the 12,124 research L1 leads (11,760 from ESA's plan, 357 from the repeat prediction) and 44 of the 2,137 L7 cells have a next look (plan generated 2026-10-10 00:20 UTC, window to 2026-10-22).

## 7. Outputs

| File | Layers or content | Rows | Size |
|---|---|---|---|
| `data/leads_open.gpkg` | `leads_4326`, `leads_utm49n` (EPSG:32649), `lead_evidence` (table), `about` | 2,137 leads, 27,676 evidence rows | 13.9 MB |
| `data/leads_open_summary.json` | counts by type, band, box; factor distributions; gate counts; corroboration window; top of queue by type; inputs with mtimes | | 8 KB |
| `data/research/leads_research.gpkg` | the same four layers, no spatial index | 14,261 leads, 93,499 evidence rows | 85.2 MB, keep out of git |
| `data/research/leads_research.parquet` | the leads table (zstd), GFW licence, attribution, access dates, caveat, rules and weights in the file metadata; the product loader's copy | 14,261 | 1.75 MB |
| `data/research/leads_research_summary.json` | as the open summary plus the GFW stamps | | 13 KB |
| `docs/figures/leads_priority.png` | priority histogram by type and build (L7 as an outline), caveat and attribution in the caption | | |

Columns: contract 3.5 fields first (`lead_id`, `lead_type`, `title`, `state`, `reason`, `priority`, `priority_band`, the five `pts_*` columns, `factors`, `priority_model_id`, `calibrated`, `primary_type`, `primary_id`, `evidence`, `n_evidence`, `lon`, `lat`, `time_utc`, `region_box`, `next_look_utc`, `next_look_pass`, `next_look_source`, `lawful_explanations`, `change_indicators`, `history`, `research_only`, `src`, `prov`), then the L1 detail (`det_id`, `run_id`, `pass_id`, `mission`, `acq_utc`, `confidence`, `cnn_score`, `length_est_m`, `ais_status`, `ais_source`, `channels`, `weather_known`, `weather_missing`, `wind_ms`, `deep_convection` as 1, 0 or null, `nearest_ais_key`, `nearest_ais_dist_m`, `nearest_ais_dt_s`, `n_ais_10km`, `ais_reach`, `n_lights_2km_3h`, `n_ais_events_2km_3h`, `n_persist_72h`), the context (`cell_id`, `depth_mean_m`, `dist_coast_km`, `dist_port_km`), the L7 detail (`n_lights`, `n_nights`, `nights`, `first_light_utc`, `last_light_utc`, `n_lights_at_sites`, `radiance_med_nw`, `share_never_imaged`, `ais_reach_share`, the last two as context) and, research only, `identity_kind`, `gfw_neural_type`, `n_gfw_gaps_50km_24h`, `nearest_gfw_gap_km`, `n_gfw_encounters_10km_24h`, `n_gfw_loitering_10km_24h`. In the GeoPackages `caveat` is the last column of every table. List columns are JSON strings: `factors`, `evidence`, `lawful_explanations`, `change_indicators`, `history`, `prov`, `nights` (the about layer's `list_columns` names them; `columns` documents every column, with build-specific text so the open file names no research-only source). The about layer holds every rule, weight, explanation sentence, the inputs with their modification times, the plan time, the counts and the caveat.

**How the GeoPackages are stored.** The caveat is the same on every row of a build, so in the GeoPackages it is stored as a SQLite column default: the rows are written without it, then `ALTER TABLE ... ADD COLUMN caveat TEXT NOT NULL DEFAULT '<caveat>'` adds it (and `research_only` on `lead_evidence`). Every row reads the full caveat in any SQLite reader (GDAL and pyogrio, checked in the tests; ArcGIS Pro and QGIS read GeoPackages through SQLite, not tested here). The SQLite file format says so: "Missing values at the end of the record are filled in using the default value for the corresponding columns defined in the table schema." The file is then rebuilt with `VACUUM` at a 16 KB page size: lead rows are 2 to 3 KB, so on GDAL's default 4 KB pages one row filled a page and about a third of the file was empty space. The two steps took the research GeoPackage from 162.7 MB to 85.2 MB with every column kept, and the open one from 16.9 MB (1,150 leads, 11,726 evidence rows) to 13.9 MB (2,137 leads, 27,676 evidence rows). Every file is written under a temporary name in the same directory (`.<name>.writing.<ext>`) and moved into place with `os.replace`, so the app backend never reads a missing or half-written file while the script runs after a live pass.

**Size and git.** The research GeoPackage (85.2 MB) is over the project's 20 MB limit for committed files; it must stay out of git like `data/research/regional_identity.gpkg` (112 MB, ignored by name). `.gitignore` is owned by R2-T5 and needs the line `data/research/leads_research.gpkg`; the script warns on every run while the file is over 20 MB. The committed research copy is `data/research/leads_research.parquet` plus the summary; the app reads the parquet first. The GeoPackage cannot reach 20 MB without dropping per-row content: the factor list, the evidence list and the provenance map alone are about 2 KB per lead in each of the two lead layers (57 MB for 14,261 leads). The open GeoPackage is 13.9 MB. Each open L1 lead adds about 5.8 KB (measured by writing a simulated pass of 722 L1 leads to a scratch file), so the file reaches 20 MB at about 1,000 open L1 leads; before that, `lead_evidence` and the long JSON columns of `leads_utm49n` should move to a second file, or the open GeoPackage should be ignored too, with the summary committed and the file rebuilt in seconds.

**Reruns.** The script is checkpoint-free and runs both builds in about 20 s (`nice -n 10 python scripts/33_leads.py --build both`; inputs about 2 s per build, the research L1 joins about 6 s, writing and `VACUUM` about 4 s). A rerun with unchanged inputs writes byte-identical GeoPackages, parquet, summaries and figure (md5sum on two consecutive runs): rows are sorted by type, priority and id, JSON keys are fixed, and the about layer's `generated_utc`, which is also the GeoPackage timestamp (GDAL `OGR_CURRENT_DATE`), is kept from the previous file while the inputs' modification times and sizes are unchanged (`inputs_signature`).

## 8. Build rules

The open build never opens `data/research/`: every read goes through `darkvessel.leads.guard.checked_path`, which raises `OpenBuildGuardError` for a research path (literal or resolved) in the open build; `tests/test_leads.py::test_open_build_guard` proves it. The open GeoPackage and summary contain no `GFW`, `gfw`, `Global Fishing Watch` or `CC BY-NC` bytes (checked with grep on the files, and in `test_gpkg_round_trip`). The research build stamps `research_only` true on every row, appends the research line to the caveat, and copies the GFW `use`, `licence` (CC BY-NC 4.0), `licence_url`, `terms_url`, `attribution` and `accessed_by_dataset` fields from the metadata of `data/research/regional_identity.parquet` into the about layer and the parquet metadata, the way `scripts/31_gfw_identity.py` stamps its files. Attribution format from the GFW terms: "Global Fishing Watch. 2026, updated daily. <dataset>, <date range>. Data set accessed <date> at https://globalfishingwatch.org/our-apis/ ."

## 9. Calibration

The weights are start values. Calibration needs the owner's contact labels (`data/labels/owner_2026-10.csv`: `det_id,label,user,time_utc` with labels vessel, structure, clutter, unsure) and the lead decisions from the app's log (`data/labels/lead_decisions.jsonl`). With a few hundred labels the plan is: (1) the false-alarm rate per lead type and band (share of L1 leads whose contact is labelled structure or clutter, or closed as false alarm); (2) one logistic fit of the label on the five factor inputs to re-weight the points, keeping the maxima, the 0 for "no AIS match" and the L7 ceiling below the L1 range; (3) a new `priority_model_id` and `calibrated` true, with the rates reported in the About view and in this file. The 3 h corroboration window and the L7 ceiling of 20 are the two rules most likely to move. Until then every card shows "uncalibrated".

## 10. Limits

- The open build has no L1 lead because no live pass has an `unmatched` contact yet; the first chance is the S1D pass of 2026-10-10 10:32 UTC near the Pearl River mouth (`docs/PROJECT_BOARD.md`).
- Live contacts have no weather join yet, so every open L1 lead will carry "weather unknown" and miss the 5 weather points until `scripts/16_weather_context.py` runs on live passes.
- The research L1 set is large (12,124) because the gate is deliberately loose and the weights are uncalibrated; most leads are medium (the middle 80 % of the medium band scores 36 to 53). The queue sorts by priority and the band filter narrows it.
- Light corroboration is 0 everywhere: the radar passes (09 to 11 and 21 to 23 UTC) and the VIIRS overpasses (16 to 19 UTC) rarely fall within 3 h of each other, and when they do no light lies within 2 km of a lead.
- `ais_reach` for research leads is GFW's AIS presence (satellite and terrestrial), for open leads the terrestrial aisstream feed; the two are not the same quantity, and the about layer says which one a file used.
- L7 counts lights, not vessels; lit fishing is mostly lawful, and recurring lights can be platforms or anchorages. `n_lights_at_sites` shows how many lights sit at recurring sites. The L7 ceiling is a policy choice to keep vessel leads first, not a measured value.
- Persistence pairs any unmatched high or medium contact within 2 km on another pass; anchored boats and moored objects pair as readily as a returning vessel.
- Radar length estimates are crude and biased upward (median radar to AIS length ratio 1.59, `docs/gfw_identity.md`); the title's length is the pixel extent.
- `next_look_utc` uses the plan's generation time as "now", so it can name a pass that flew in the up to 6 h since the plan was last refreshed.

## 11. Make target

R2-T5 owns the Makefile. Its current targets `leads` (`$(PY) scripts/33_leads.py --build open`) and `leads-research` (`--build research`) work; the recommended form is `nice -n 10 $(PY) scripts/33_leads.py --build both` in one target (one run writes the figure with both panels; running the builds separately draws a one-panel figure each time). It depends on `regional` and `viirs` (and on the live and research identity files when they exist). The test subset is `$(PY) -m pytest -q tests/test_leads.py`.

## 12. Changes after the review of 2026-10-09

- Weather gate per part (was: a lead with any missing part counted as "weather unknown", which let 19 contacts under known deep convection through). Research L1 went from 12,143 to 12,124 and now equals the contract 6.2 count; the earlier note that the 12,124 could not be reproduced was wrong.
- L7 reads every VIIRS night (`viirs_lights_all.gpkg`, 27 nights) instead of the lean 10-night file: 2,137 cells and 41,760 lights instead of 1,150 and 10,515. The earlier statement that the VIIRS nights do not overlap the radar run was wrong; the zero light corroboration comes from the 3 h window (section 4).
- L7 is scored within the spec meaning of each factor and capped by its scale at 20 (section 3); it was up to 70, with 102 cells in the high band.
- `next_look_utc` skips passes that were already past when the plan was generated.
- The open about layer and summary name no research-only source; factor sources are registry keys; the figure caption carries the GFW attribution and draws L7 as an outline.
- Atomic writes, the caveat as a column default, and `VACUUM` at 16 KB pages (section 7); a test now checks that a changed input moves `generated_utc` and that an unchanged one keeps it.

## Sources (resolved in this session, 2026-10-10 01:40 to 01:52 UTC)

- https://www.imo.org/en/OurWork/Safety/Pages/AIS.aspx (200; the carriage sentence quoted in section 5 matched on the page).
- https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/AIS/Resolution%20A.1106(29).pdf (200; the paragraph 22 sentence quoted in section 5 matched in the PDF text).
- https://support.marinetraffic.com/en/articles/9552924-why-can-t-i-see-a-vessel-on-the-live-map (200; terrestrial AIS covers "only specific coastal areas where a land-based AIS receiver is installed").
- https://www.navcen.uscg.gov/sites/default/files/pdf/AIS_Comparison_By_Class.pdf (200; "TRANSMIT POWER 12.5 Watts (1 W low-power) 5 Watts (2 W low-power)" and "2 Watts only" for the carrier-sense class B unit), linked from https://www.navcen.uscg.gov/ais-frequently-asked-questions (200).
- https://globalfishingwatch.org/faqs/how-do-i-view-different-types-of-data-ais-vms-viirs/ (200; the squid-fishing sentence quoted in section 5).
- https://raw.githubusercontent.com/allenai/vessel-detection-viirs/main/data.md (200; the default false-source filters quoted in section 5).
- https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits (200; non-commercial use and the "Powered by Global Fishing Watch" attribution) and https://creativecommons.org/licenses/by-nc/4.0/ (200).
- https://sentiwiki.copernicus.eu/web/s1-mission (200; "12 day repeat cycle and 175 orbits per cycle for a single satellite").
- https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans (200; the plan KML files behind `data/s1_next_passes.json`).
- https://www.sqlite.org/fileformat2.html (200; the record-default sentence quoted in section 7) and https://www.sqlite.org/lang_altertable.html (200; "If a NOT NULL constraint is specified, then the column must have a default value other than NULL").
- Elvidge, C. D. et al. (2015), Automatic Boat Identification System for VIIRS Low Light Imaging Data, Remote Sensing, doi:10.3390/rs70303020: https://api.crossref.org/works/10.3390/rs70303020 returned 200 (title, journal and first author confirmed); https://doi.org/10.3390/rs70303020 returned 403 to this session, so the article text is UNVERIFIED here (the detector is described in `docs/viirs_lights.md`).
- Paolo, F. S. et al. (2024), Satellite mapping reveals extensive industrial activity at sea, Nature, https://doi.org/10.1038/s41586-023-06825-8 (200; title matched; cited through `darkvessel.ais.gfw.GFW_CAVEAT`).
- Vietnam's VMS duty for fishing vessels of 15 m and over: UNVERIFIED (snippet-level, `docs/STATUS.md`).
- Repo files read: `app/CONTRACT.md` (1.1, 2, 3.5, 6.2, 6.3), `docs/product_design.md` (4.1), `app/backend/scs_api/loaders/leads.py`, the about layers of `data/viirs_lights.gpkg` and `data/viirs_lights_all.gpkg`, `data/s1_next_passes.json`, `data/live/live_contacts.gpkg`.
