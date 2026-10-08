# Maritime domain awareness products: views, event definitions, triage, exports, and what SCS Vessel Watch should adopt

Research brief for the lead. Date 2026-10-08. No code was written. Owner rules applied: plain English, no em or en dashes, every claim tagged with its source status.

## 0. How to read the source tags

- (R) = URL fetched and read in this session. Trustworthy.
- (S) = URL surfaced by web search and its content seen only as search-result excerpts, because the egress proxy blocks the host (HTTP 403 on CONNECT). Treat as UNVERIFIED until the owner opens the host. Blocked hosts hit in this session: skylight.global and support.skylight.global, globalfishingwatch.org and its github.io and readthedocs mirrors, seavision.volpe.dot.gov and info.seavision.volpe.dot.gov, windward.ai and developer.windward.ai, kpler.com, help.kpler.com, support.marinetraffic.com, imo.org and wwwcdn.imo.org, fao.org and openknowledge.fao.org, imcsnet.org, science.org, nature.com, frontiersin.org, arxiv.org, ebi.ac.uk, navcen.uscg.gov, iumi.com, hellenicshippingnews.com.
- (U) = UNVERIFIED: no source resolved or excerpted.
- What did resolve: github.com and raw.githubusercontent.com (code, READMEs, licences), the repo's own files.

Consequence: the only threshold definitions resolved from primary code this session are Global Fishing Watch's pipeline parameters on GitHub; every vendor help-centre number is (S).

## 1. Product by product

### 1.1 AI2 Skylight (Allen Institute for AI)

Access and data: free "to government, regional, and non-governmental organizations worldwide"; near-real-time AIS bought from ORBCOMM; free imagery (Sentinel-1, Sentinel-2, Landsat 8-9, VIIRS night lights) plus commercial partners (Maxar, Spire); GraphQL API for integration with SeaVision, YARIS, IORIS (S: support.skylight.global/what-is-skylight, support.skylight.global/data-sources, www.skylight.global/platform). Detection code is open source under Apache-2.0: allenai/vessel-detection-sentinels (R: LICENSE, README; predicts vessel_length_m, vessel_width_m, heading buckets of 22.5 degrees, vessel_speed_k, is_fishing_vessel probability), allenai/vessel-detection-viirs (R: repo page shows Apache-2.0; readme.md: Suomi-NPP, NOAA-20, NOAA-21; "The largest source of error occurs around full moons due to the interaction of moonlight and clouds", handled by measuring cloud background glow "on and around full moons (+/- 2 days)"; data.md lists the false-positive filters: auroral lit clouds, moonlit clouds, bowtie and noise smiles, edge noise, near-shore detections, non-max suppression, lightning, gas flares), allenai/sar_vessel_detect (R: xView3 model; "most of their labels come automatically from AIS tracks").

Views: map with Events panel, Filters tab, Areas of Interest, vessel search by name, MMSI, call sign or IMO, vessel page with event history, event details card with imagery chip, saved filters that send email alerts, rule-based satellite tasking ("Skylight can automatically trigger satellite images when certain vessel behaviors are detected") (S: support.skylight.global/event-details-card, /saved-filters-alerts, /introduction-to-areas, /vessel-details, www.skylight.global/platform).

Event types and definitions (all S, support.skylight.global):
- Standard Rendezvous: two AIS vessels "within 250 m of each other", "30 minutes or more", speed "under 4 knots", "more than 10 km from the coast"; AIS signals identified as buoys or fishing gear are excluded; average latency 1 hour (/standard-rendezvous).
- Dark Rendezvous: one AIS vessel "displays rendezvous-like behavior for at least 15 minutes"; the page says "This is not a guarantee that a vessel is rendezvousing with another vessel" and that routine manoeuvres (waiting for port entry, maintenance) trigger it (/dark-rendezvous).
- Fishing: machine-learning model trained on "over 10,000 months" of AIS tracks; October 2024 audit of 497 events: 71 % precision overall, about 90 % for known fishing types, about 50 % for unknown types; latency as low as 15 to 20 minutes (/fishing).
- Entry: rules-based, fires on the first AIS position inside a user area; card shows dwell up to 72 hours; areas above 1,000,000 km2 cannot generate Entry or Speed Range events; no backfill (/entry, /enable-entry-speed-range-events).
- Speed Range: AIS vessel inside an area within a user band, example "within 1-4 knots for more than 1 hour"; average latency 1 hour; used as a fishing proxy where the fishing model was not trained (/speed-range).
- Vessel detections (Satellite Radar, Optical, Night Lights): Sentinel-1 at 10 m resolution with a 1,280 m chip per detection; "An offline audit from 2023 showed a precision of 84%"; length filter applies only to Sentinel-2 and commercial optical (/satellite-radar, /vessel-detection-filters, /vessel-attribute-estimations).
- AIS correlation and "dark": Skylight "searches for AIS messages within 1500 meters of the detection location and matches the detection to the AIS message that is closest in time and space"; no match means "Dark Vessel" and no vessel information; a dark label can mean no AIS carried, a common gap, AIS switched off, or a low-confidence match where several vessels are nearby; it re-checks "every 3 hours for the first 24 hours, and then once a day for the next 9 days"; "We always recommend checking dark detections in Skylight with data you have in other tools" (/ais-correlation-dark-vessels).
- Night Lights card fields: clear-sky confidence 0 to 1, moonlight 0 to 100; South Atlantic Anomaly noise removed by erosion then dilation (/night-lights).

Risk display: none. Skylight surfaces "Events" and lets the analyst filter; it does not score vessels (S: /what-is-skylight). Triage: saved filters plus email, event cards, areas, tasking rules. Exports: event history CSV from the vessel page, downsampled KML track, "AIS tracks are not available for downloading", GraphQL API (S: /vessel-details, /what-is-skylight).

### 1.2 Global Fishing Watch (map, Vessel Viewer, Marine Manager, APIs)

Licence: APIs and data "available for noncommercial use only in accordance with the CC BY-NC 4.0 license"; 50,000 requests per day; "Powered by Global Fishing Watch" attribution required; governments using it "in free and open tools available to the public" count as noncommercial; commercial integration needs a custom licence (S: globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits, /faqs/can-i-use-global-fishing-watch-apis-for-commercial-purposes). The Python and R clients confirm the event families: "encounters, loitering, port visits, fishing events, and AIS off (aka GAPs)"; SAR detections "between 2017 to ~5 days ago"; fixed infrastructure to about 3 months ago (R: gfw-api-python-client README, gfwr README). A third-party wrapper repeats "GFW data is free for non-commercial use and commercial use needs their permission" (R: pipeworx-io/mcp-global-fishing-watch README). The repo's own gfw.py already encodes dataset names public-global-gaps-events, -encounters-events, -loitering-events, -port-visits-events and the licence text (R: src/darkvessel/ais/gfw.py).

Event definitions:
- Encounter: "two vessels are detected within 500 meters of one another, for a duration of at least 2 hours, traveling at a median speed of less than 2 knots, and located at least 10 kilometers from a coastal anchorage"; positions are interpolated to a 10-minute grid so the pair "may not have been physically within 500 meters of each other for the entire 2-hour period"; the event location is the average of all positions (S: /faqs/what-is-a-vessel-encounter, /user-guide). The pipeline exposes these as parameters: `--max_encounter_dist_km 0.5` and `--min_encounter_time_minutes 120` in the README examples (R: GlobalFishingWatch/pipe-encounters README). An older GFW post used 3 hours and a 20 nm shore filter (S: globalfishingwatch.org/?p=3513). Original method: Miller et al. 2018, doi 10.3389/fmars.2018.00240 (R: paper-identifying-global-patterns-of-transshipment README gives the citation; the Methods text itself is blocked).
- Loitering: one vessel at "average speed of < two knots, while at least an average of 20 nautical miles from shore"; shown only above one hour in the carrier portal; "loitering events are not displayed for fishing vessels, due to the challenge in distinguishing loitering from other low speed operations related to fishing activity" (S: /faqs/what-is-loitering-event, data-caveats).
- Port visit: PORT ENTRY within 3 km of an anchorage point; PORT STOP begins below 0.2 knots and ends above 0.5 knots; PORT GAP = AIS gap above 4 hours inside port; PORT EXIT beyond 4 km; confidence 2 (stop or gap only), 3 (entry or exit plus stop or gap), 4 (entry, stop or gap, and exit) (S: data-caveats). The pipeline README confirms the parameters live in YAML config, not in the README (R: pipe-anchorages README).
- AIS gap (AIS off): "The gap event must be at least 12 hours. The gap must start at least 50 nautical miles from shore. The gap must start in an area with a satellite reception quality greater than 10 positions per day. The vessel must have at least 14 satellite positions in the 12 hours prior to the gap"; v3 adds a "potentially intentional" true or false flag; nearshore satellite reception is treated as unreliable (S: data-caveats). Primary code confirms the structure: OFF and ON events are created when the time between consecutive positions exceeds a runtime parameter `min_gap_length` ("minimum length of a closed event to be included"), only on `good_seg` segments, with satellite-position counts in the 6, 12, 18 and 24 hours before the gap, monthly satellite reception as `positions_per_day` at the gap start and end, distance from shore, implied speed, and vessel class restricted to fishing vessels as classification features (R: AIS-disabling-high-seas data_production/gaps/ais_off_on_events.sql.j2, ais_gap_events.sql.j2, ais_gap_events_features.sql.j2). The generic gaps pipeline documents `min_gap_length` (sample config 6 hours) and `n_hours_before` (sample 12) (R: pipe-gaps README).
- Welch et al. 2022, Science Advances, doi 10.1126/sciadv.abq2109: more than 55,000 suspected disabling events beyond 50 nm, more than 40 % of fishing vessels in those waters, up to 6 % of activity (more than 4.9 M hours) hidden; hot spots NW Pacific 13 %, off Argentina 16 %, West Africa 8 %, Alaska 3 % of hours lost (S: science.org, phys.org, PMC9629714). Code repo exists (R: github.com/GlobalFishingWatch/AIS-disabling-high-seas).
- SAR layer: an experimental filter marks detections "matched" or "unmatched" to AIS; unmatched ones get an experimental classifier output "Likely Fishing", "Likely non-fishing" or "Unknown" (inputs described inconsistently: thumbnails in the API docs, length, bathymetry and distance from port in the map docs); unmatched means "vessels that cannot be tracked with AIS", some because of "low AIS reception" (S: /platform-update/new-detections-from-synthetic-aperture-radar, /user-guide, /fisheries/mediterranean). Paolo et al. 2024 (doi 10.1038/s41586-023-06825-8): "72-76% of the world's industrial fishing vessels are not publicly tracked", 21 to 30 % of transport and energy vessel activity missing; "Not publicly tracked" includes small boats and poor-coverage areas, not only switched-off AIS (S: ESA and press pages). The code repo is dual-licensed "Apache 2.0 and CC BY-NC 4.0" (R: paper-industrial-activity README). VIIRS layer: "More than 85% of the detections come from vessels without AIS or publicly shared VMS transponders" (S: /faqs/how-do-i-view-different-types-of-data-ais-vms-viirs).

Views and entity pages: vessel profile with REGISTRY and AIS identity tabs and an ACTIVITY timeline grouped by apparent fishing, encounters, loitering, port visits, grouped by voyage (port visit to port visit); area reports with vessel breakdown by flag, type and gear, an Events tab since 1 October 2025 with duration, flag, next-port and region filters (S: /user-guide, /platform-update/introducing-global-reports). Vessel Viewer (with TMT): search "up to 750,000" vessels, vessel groups for monitoring, risk analysis on identity, AIS coverage and events, 72-hour AIS delay (S: /vessel-viewer-tool). Marine Manager: private workspaces, SST, salinity, chlorophyll-a, wind, currents, habitat layers, upload own point data, two-dataset comparison charts (S: /platform-update/marine-manager-new-analysis-and-environmental-features). Risk: an IUU risk insights dataset with 11 indicators, one of which counts close-proximity events (S: /platform-update/iuu-fishing-risk-insights-dataset-release). Triage advice: "visually inspect vessel tracks, always refer to additional data source and/or information, and request records from a vessel to confirm any findings" (S: /user-guide). Exports: CSV of identity, of all events, and per voyage; track as CSV or GeoJSON for the timebar window (the same guide also says tracks cannot be downloaded, an internal conflict); area report CSV, multi-format list, PDF print, share link; Vessel Viewer CSV and PDF; 13 layers on ArcGIS Online; bulk download portal (S: /user-guide, /platform-update/explore-global-fishing-watch-data-via-esris-arcgis-online).

### 1.3 SeaVision (US DOT Volpe Center)

Built by Volpe for US Naval Forces Africa; browser-based, unclassified, non-PKI; free to US government staff and partner nations in the MSSIS network; "Community Managers" vet applicants and control data access (S: info.seavision.volpe.dot.gov, /support, volpe.dot.gov/news/seavision-improves-africas-maritime-picture). Layers: AIS from MSSIS (shared by "more than 75 nations" tracking "70,000+ vessels"), satellite radar and electro-optical detections matched to AIS, partner coastal radar, commercial RF (HawkEye 360), third-party event monitoring for rendezvous and unusual activity (S: info.seavision.volpe.dot.gov, transportation.gov). Alerts (user-defined rules; S: /releases): Proximity (0.1 to 0.5 nm radius, needs a valid MMSI); Dark Vessel (8.3.0, 2025): "Trigger alerts when a vessel has its AIS transponder off for a specified duration while within a defined area", optionally again when it resumes; vessel-age filter dark indicator (6.3.0, 2023); Near Shape and Slow Down for critical infrastructure; speed over or under a threshold (6.0.0); no port call in a period; draft change above a threshold; HawkEye 360 alerts (9.2.0, May 2026); non-AIS alerts batched by 15 minutes; custom vessel lists usable in alerts (5.3.0). Triage: mark vessels "visited" (dims them, 9.1.0), share saved queries, rule sets, alert definitions, drawn areas and vessel lists within a community; AIS transmission chart now 5 years (9.3.0, 11 August 2026). Exports: map vessels CSV and JSON (6.0.14), KML and KMZ history trails (7.4.0), compressed CSV of positions and attributes in vessel export (8.1.0). No published risk score. The version numbers and dates are (S) only.

### 1.4 Windward

Commercial risk platform. Definitions (S, windward.ai): AIS gap = "a period during which a vessel's AIS transmission is not received"; dark activity = the vessel "intentionally stops broadcasting"; "most gaps are innocent" (coverage limits, congestion, equipment faults, weather, GPS jamming); Q4 2025 claim: "more than 650,000 AIS signal losses globally, yet only around 1% were linked to sanctions evasion"; "prolonged dark activity" = outages "of three days or longer" because that can hide a port call, STS transfer or route deviation; a "two-hour gap beginning immediately before entry into a known ship-to-ship transfer zone may warrant investigation"; "AIS gaps are an investigative signal, not an automatic violation"; AIS handshake = two ships "trade AIS transponders"; GNSS spoofing = reported position differs from the real one with a plausible-looking track (/glossary/what-are-ais-gaps-and-ais-handshakes, /blog/what-is-dark-activity-and-why-is-it-surging-in-2026, /glossary/what-is-ais-spoofing). Behavioral Analysis API lists dark activities, ship-to-ship meetings, port calls, deviation from pattern of life, loitering, and "vessel security risk scores" (S: /api-hub/behavioral-analysis-api). Risk display: Low, Moderate, High tiers (June 2026 narcotics report: 3,462 vessels Moderate or High, 2,936 Moderate, 526 High; identity or location manipulation was always High; MMSI changes on 140 vessels, about 17 % High); Organization Defined Risk lets the customer assign "high, medium, or indication" to scenarios; AI-generated explanations of why a vessel was flagged (S: /knowledge-base/windward-counter-narcotics-intelligence-report-june-2026, /knowledge-base/your-business-your-risk-your-rules, /solutions/vessel-screening). Scoring method and thresholds are not public (U).

### 1.5 Kpler and MarineTraffic

Risk & Compliance Methodology (S: support.marinetraffic.com/en/articles/11172276): "Sanctioned" above three tiers; High = sanctioned ownership, high-risk cargo, sanctioned or false flag, previously sanctioned; Medium = AIS spoofing, dark STS transfers, dark port calls, high-risk port calls and STS, AIS identity manipulation (first type: "zombie vessels" broadcasting a scrapped ship's identity), PSC bans; Low = everything else. "Every Dark STS event is confirmed using satellite imagery"; each dark port call "is confirmed using satellite imagery"; spoofing is found by predicting the next position and comparing with the reported one, for tankers and dry bulk; spoofed signals stay on the map "as they offer valuable intelligence" but are excluded from analytics. Dark Port Calls went live on web app and API in July 2026 (S: kpler.com/blog/from-dark-port-calls-to-dead-zones-julys-marinetraffic-updates). The "dead zones" definition could not be retrieved (U). Kpler cargo "Dark activity" tag: applied to port calls of vessels carrying sanctioned or previously sanctioned cargo once "vetted sources confirm the load or discharge"; silence from "poor reception or by security precautions against piracy falls outside the tag"; otherwise an "analyst-assumption" label (S: help.kpler.com/en/articles/9672698). Views and triage: Compliance Workspace (15 February 2026) to "Screen and prioritise vessels across your fleet", filter by flag, risk status, type, ownership; notifications for high-risk port calls, high-risk STS, dark STS, high-risk AIS gaps, AIS spoofing; Vessel Notes shared between users; general event codes AIS_OFF and AIS_ON (S: /articles/14639204, /articles/9552694, /articles/9552766). Coverage caveat from the vendor itself: terrestrial network "does not cover 100% of the world's seas, but only specific coastal areas where a land-based AIS receiver is installed" (S: /articles/9552924). No published hour threshold for an AIS gap (U).

### 1.6 Starboard Maritime Intelligence (added, Pacific fisheries context)

Vessel histories "highlight significant events" (fishing, port visits, vessel meetings, satellite detections), check events since the last port call to "verify self-reported events, such as transshipments", tag vessels and add shared notes, sort by key events such as fishing duration and time at sea (S: starboard.nz/solutions/fisheries-monitoring, /software/roadmap). Spoofing help page: deliberate false AIS including "multiple MMSI's simultaneously or successively", but AIS "can often be unintentionally spoofed through faulty GPS signals, software and installation errors, or signal interference" (S: help.starboard.nz/en/articles/11175215).

### 1.7 Carriage rules and the right to switch off (the basis of rule 3)

- SOLAS V/19: AIS on "all ships of 300 gross tonnage and upwards engaged on international voyages, cargo ships of 500 gross tonnage and upwards not engaged on international voyages and all passenger ships irrespective of size"; IMO guidance says small vessels "(e.g. leisure craft, fishing boats)" are exempt (S: imo.org/en/OurWork/Safety/Pages/AIS.aspx). EU Directive 2011/15/EU extends carriage to fishing vessels over 15 m (S: eur-lex). Vietnam's compliance stream for fishing vessels of 15 m and longer is VMS, not open AIS (R: docs/STATUS.md, which marks the detail as snippet-level UNVERIFIED).
- IMO Resolution A.1106(29), 2015, paragraph 22: "AIS should always be in operation when ships are underway or at anchor. If the master believes that the continual operation of AIS might compromise the safety or security of his/her ship or where security incidents are imminent, the AIS may be switched off", with a logbook entry and, in a mandatory reporting system, a report to the authority (S: irclass.org technical circular 044/2022, iumi.com, maritime-mutual.com, casualnavigation.com; the IMO PDF is blocked).

### 1.8 FAO and UN guidance on IUU indicators

- IPOA-IUU 2001, paragraph 3: illegal (3.1: in a state's waters without permission or against its laws; RFMO measures breached), unreported (3.2), unregulated (3.3: stateless or non-party vessels, or unregulated areas and stocks); 3.4: "certain unregulated fishing may take place in a manner which is not in violation of applicable international law" (S: fao.org/3/Y0772E/Y0772E.htm, cil.nus.edu.sg copy). Being outside a tracking system is not one of the three definitions.
- Voluntary Guidelines for Transshipment: adopted by the Technical Consultation 7 July 2022, endorsed by COFI35 September 2022, published 2023, doi 10.4060/cc5602t; flag states should authorise transshipment only for vessels with "an approved functional vessel monitoring system (VMS)"; advance notice of date, time and location, declarations of fish on board, verification of VMS and observer or electronic monitoring before clearance; no fixed hour deadline in the text (CCAMLR uses 72 hours, Pew proposes 24 hours) (S: openknowledge.fao.org bitstream 225b1de1, un.org oceancapacity fao_vgt.pdf, asoc.org analysis, stopillegalfishing.com).
- FAO Methodologies and indicators for the estimation of IUU fishing, Volume 1.4 "Developing and using indicators of performance" (2024): a framework of detection, coverage and investment indicators that "provides insight into, but does not actually estimate, the level of IUU fishing" (S: fao.org/iuu-fishing/resources/detail/en/c/1697526). A 2020 FAO COFI paper notes satellites can detect vessels but "beyond size it cannot separate fishing craft from other vessels or confirm that fishing is happening" (S: fao.org/3/cb3175en).
- UNODC: Rotten Fish (2019) is a corruption guide; the 2024 legislative guide holds 32 model provisions; no UNODC AIS red-flag list was found (S: unodc.org). The commonly cited at-sea indicators come from C4ADS (2019): "AIS dark activity", identity alterations, flag manipulation, unregulated transshipment at sea, with the advice that several together narrow a pool of vessels "that warrant further examination" (S: c4ads.org/reports/strings-attached).

### 1.9 Patterns shared by all of them

1. Map first, with a layer switcher, a time bar, and an event list beside the map.
2. Entity pages: vessel (identity tabs, activity timeline, voyages), event card (sensor chip, time, position, partner vessel, duration), area (report with vessel breakdown and event counts).
3. Areas of interest with rule-based alerts (entry, speed band, proximity, silence for N hours, dwell), delivered by email or in-app.
4. "Dark" always means "no AIS match", and every serious product adds the reasons it might be innocent (Skylight, GFW, Windward, Kpler, Starboard all do).
5. Risk, where shown, is tiered and explained by the triggering event type (MarineTraffic), configurable by the customer (Windward ODR), or absent (Skylight, SeaVision, GFW map).
6. Triage tools: saved filters, vessel lists and groups, notes, mark as visited, shared workspaces, imagery tasking.
7. Exports: CSV everywhere, KML or KMZ for tracks, GeoJSON (GFW), PDF reports, and an API.

## 2. Proposed event and lead definitions for SCS Vessel Watch

Design rules: events are observations, leads are bundles for review, nothing is called a violation. Every record carries `caveat` = DARK_CAVEAT from config.py (R: src/darkvessel/config.py) and, for research-build records, GFW_CAVEAT and RESEARCH_TAG (R: src/darkvessel/ais/gfw.py). Files: GeoPackage and GeoJSON in EPSG:4326 (UTM 49N copies where written), COG for rasters, matching the existing products. Load each parallel-workstream file only if present: data/ais_live.gpkg, data/s1_next_passes.json, data/leads_open.gpkg, data/expected_activity*, data/ocean_context.gpkg (exists now), data/eez_marineregions.gpkg (exists now), data/research/*.

### 2.1 Observation events

E1 RADAR_CONTACT. One Sentinel-1C or 1D vessel candidate from the CFAR plus CNN pipeline. Fields: det_id, scene, time, lon, lat, class (both-channel, one-channel, low), cnn_score, clutter flags (5 or more weak returns within 1 km; within 250 m of a fixed structure), wind_10m, cloud_top_temperature, length_est_m if present, chip. Caveat: CFAR plus CNN precision 0.77 and recall 0.75 on AI2 labels with a 50 m rule; nothing under 25 m kept off Ca Mau; rain cells, rafts and wind farms were false sources (R: docs/STATUS.md).

E2 LIGHT_CONTACT. One VIIRS lit vessel candidate. Fields: night, clear_sky_confidence, moon_illumination, recurring_site flag, wind. Caveat: lights are mostly lawful fishing (squid and purse seine fleets use lights, GFW (S)); moonlit clouds, lightning, gas flares, aurora and sensor artefacts are known false sources (R: allenai/vessel-detection-viirs data.md); the Gulf of Tonkin lit fleet follows the wind (R: docs/STATUS.md).

E3 FIXED_STRUCTURE. Persistent radar object confirmed by the Sentinel-2 persistence check or within 250 m of a Satlas platform or turbine (R: docs/STATUS.md). Used to suppress contacts, never as a lead.

E4 AIS_POSITION and AIS_STATIC from aisstream (R: src/darkvessel/ais/aisstream.py: positions from message types 1, 2, 3, 18, 19, 27; static from 5 and 24; ais_class A or B). Caveat to show on every AIS layer: aisstream relays volunteer shore receivers, so reach is a few tens of kilometres offshore; a third-party write-up claims terrestrial plus satellite but the service documentation is blocked, so coverage is UNVERIFIED (S: mintlify.wiki GeoSentinel page; R: aisstream GitHub README gives no source statement).

E5 AIS_REACH. Per 0.05 degree cell per UTC hour: share of recorded hours with at least one position and distinct MMSI count (R: aisstream.py `reach_grids`). This is the product's analogue of GFW's "satellite reception quality greater than 10 positions per day" (S) and Welch's 50 nm rule (S). Rule: a cell-hour is "in reach" when the trailing 7-day share of hours with any position is at least 0.5 and at least 3 distinct MMSI were heard; otherwise "AIS not heard here". Thresholds to be calibrated on the recorded data; start values only.

### 2.2 Correlation events

E6 AIS_MATCH. A radar or light contact matched one-to-one to an AIS position predicted at sensor time (interpolation within 1,800 s, dead reckoning within 600 s), gate 1,000 m (R: src/darkvessel/ais/match.py MatchConfig). Comparators: Skylight gates at 1,500 m (S); xView3 scores detections within 200 m (R: DIUx-xView/xview3-reference, `--distance_tolerance 200`); GFW's SAR match is probabilistic and labelled "experimental" (S). Record match_dist_m, ais_dt_s, ais_method, mmsi, and keep the 50 m and 150 m or 0.75 x length rules for scoring against labels (R: docs/STATUS.md).

E7 NO_AIS_MATCH (the product's "dark" label), three states, never two:
- matched;
- no AIS match, AIS in reach: unmatched contact in a cell-hour that is "in reach" (E5), and no AIS vessel predicted within 2,000 m (double gate to absorb azimuth shift and timing error);
- unmatched, AIS not heard here: unmatched contact where E5 says the receivers do not reach. Shown in a neutral colour, excluded from dark counts.
Caveat on the card and in the legend: DARK_CAVEAT_SHORT "Dark = no AIS match. Not evidence of illegal activity." (R: config.py) plus the carriage facts: SOLAS applies from 300 GT international and 500 GT domestic cargo, fishing boats generally exempt, Vietnamese 15 m fishing vessels report by VMS which this product does not see (S: imo.org; R: STATUS.md). Late matches: re-run the match when AIS arrives late, every 3 hours for 24 hours then daily to day 10 (Skylight practice (S)); keep a status history so a flipped label is auditable.

### 2.3 AIS behaviour events (computed only inside reach)

E8 AIS_SILENCE. Last position in an in-reach cell, no position for at least 6 hours while the dead-reckoned track (SOG, COG from the last report) stays inside reach the whole time. 6 hours is the pipe-gaps sample `min_gap_length` (R); GFW uses 12 hours plus the 50 nm and reception filters for satellite data (S); followthemoney used 8 hours or 200 km for well-covered waters (R: followthemoney/ais_gaps README). Grades: 6 to 12 h "silence", 12 to 72 h "extended silence", over 72 h "prolonged" (Windward's 3-day convention (S)). Never output "intentional". Caveat text: A.1106(29) allows the master to switch off for safety or security (S); Class B transmits at lower power and less often (S: GFW blog); receivers drop messages in congestion (S: Kpler AIS fundamentals).

E9 AIS_RETURN and POSITION_JUMP. On the first position after E8, compute implied speed between last and first positions (GFW field gap_implied_speed_knots (R: gfw.py parser)). Flag "position jump" when implied speed exceeds 50 kn or the position is on land (land mask exists). Caveat: GNSS faults, installation errors and interference produce the same signature (S: Starboard, MarineTraffic spoofing pages).

E10 ENCOUNTER (two AIS vessels). Strict: within 500 m for at least 2 hours, median speed below 2 kn, at least 10 km from a Natural Earth major port or anchorage (GFW (S), pipe-encounters parameters (R)). Short: within 250 m for at least 30 minutes, speed below 4 kn, more than 10 km from the coast (Skylight (S)), labelled "close approach". Exclude AIS aids to navigation and gear buoys (MMSI prefix 99, Skylight's exclusion (S)). Caveat: lawful transshipment, bunkering, pilot and supply transfers look identical (Skylight, GFW (S)); FAO's guidelines regulate transshipment, they do not forbid it (S).

E11 LOITERING and SLOW_ACTIVITY. Non-fishing AIS types: average speed below 2 kn for at least 2 hours, at least 20 nm from shore (GFW (S)). Fishing types: the same signature is labelled SLOW_ACTIVITY, not loitering, because GFW hides loitering for fishing vessels for exactly this reason (S). Speed band event: 1 to 4 kn for more than 1 hour inside a user area as a fishing proxy (Skylight (S)).

E12 IDENTITY_CHANGE. Same MMSI with a changed name, call sign, IMO or dimensions in static messages; two simultaneous positions for one MMSI more than 50 km apart; one IMO seen under two MMSI. Caveat: sales and re-registration legitimately change identity (S: GFW reflagging study via dialogue.earth); faulty static data is common (S: Starboard).

E13 AREA_ENTRY and DWELL. AIS vessel's first position inside a user reporting box; dwell up to 72 hours (Skylight (S)). For the EEZ layer (off by default): entry events are labelled "crossed a line as published by Marine Regions (VLIZ); many lines here overlap or are disputed; this project takes no position" and never carry priority on their own. Region boxes are reporting boxes, not claims.

E14 PORT_VISIT (research build only, from GFW events) using GFW's 3 km, 0.2 and 0.5 kn, 4 h, 4 km, confidence 2 to 4 (S). Open build: a coarse "near major port" flag from Natural Earth ports within 3 km.

### 2.4 Context events

E15 ACTIVITY_ANOMALY. Per 0.25 degree cell per night (VIIRS) or per pass (radar): Poisson z-score of observed against expected from the gradient-boosted model, both high and low (R: docs/ocean_context_plan.md). Caveat on the layer: "A place with more or fewer boats than expected is a lead for review, not evidence of anything" (R: ocean_context_plan.md).

E16 LOOK_STATUS. Per cell: last Sentinel-1 look, probability of a look within 1, 7 and 30 days, "never imaged in 90 days" flag (45 % of the AOI) (R: STATUS.md); next predicted pass from data/s1_next_passes.json with its source (acquisition plan or 12-day repeat) and the note that repeat predictions are not ESA's plan (R: src/darkvessel/ais/s1_passes.py).

E17 ENVIRONMENT at each contact: depth class, distance to coast and port, SST and distance to the nearest front, current speed, wave height, shipping-lane membership when the density layer is available, with each layer's date (R: ocean_context_plan.md).

### 2.5 Leads (what the analyst queue holds)

A lead is one object or cell plus the events on it, a review priority, and the caveat. Lead types, each with its plain rule and caveat:

L1 Unmatched radar contact in reach. E7 state "no AIS match, AIS in reach", cnn_score at least 0.5, both-channel, no clutter flag, not within 250 m of E3, wind below 12 m/s and no deep convection at the contact. Caveat: carriage exemptions, VMS-only fishing fleet, Class B reach, detector precision 0.77.

L2 Silence with a radar look. E8 open at the time of a Sentinel-1 pass, and an unmatched contact (E7, either unmatched state) within the dead-reckoned envelope (last position plus SOG x elapsed time, capped at 60 nm) at pass time. Caveat: the contact may be another vessel; the silence may be lawful.

L3 Possible unlit or dark meeting. E11 loitering or slow activity by an AIS vessel, and an unmatched radar or light contact within 1 km during the event (Skylight "dark rendezvous" idea, with the same warning that it "is not a guarantee" (S)). Caveat: fishing, fuel and supply transfers are lawful; pairs are judged by eye on the chip.

L4 Encounter. E10 strict or short between two AIS vessels. Caveat: lawful transshipment and bunkering; FAO VGT authorises and documents transshipment, it does not ban it (S).

L5 Identity or position anomaly. E9 jump or E12 change. Caveat: GNSS and installation faults, legitimate re-registration.

L6 Activity anomaly cell. E15 z-score at least 3 (high) in calm weather, or a jump in the dark share (E7 in-reach unmatched over all contacts) of at least 20 points against the cell's 30-day baseline. Caveat: model error, weather, fleet migration; counts, not vessels.

L7 Lit activity where no one looks. E2 clear-sky lights in cells with E16 "never imaged" (34 % of clear-sky lit candidates (R: STATUS.md)). Caveat: lights are mostly lawful fishing; this is a coverage gap statement, used to argue for tasking, not a vessel lead.

L8 Area entry (user boxes only). E13 inside a user-drawn box, optionally combined with any of L1 to L5. EEZ crossings alone never make a lead.

### 2.6 Review priority (not a risk score)

Call it "review priority", 0 to 100, with the factors listed on the card, in the style of MarineTraffic's tiers that name the triggering event (S) and Windward's organisation-defined thresholds (S). Factors: evidence quality (cnn_score, both-channel, clear sky), corroboration count (radar plus light plus AIS behaviour on the same object within 3 hours and 2 km), AIS reach quality at the spot (E5), weather sanity (wind, convection), persistence across passes, and an analyst-set area weight per reporting box. No factor may be "unmatched to AIS" alone; the whole point of rule 3 is that absence of AIS is weak evidence. Calibrate the weights against the owner's labels (data/labels/owner_2026-10.csv when it exists) and report the false-alarm rate per lead type in the docs; GFW, Skylight and Windward all publish or imply precision figures and this product should too.

### 2.7 Triage workflow to build

Queue sorted by priority with states new, reviewing, closed-explained (pick a lawful reason from a list: no carriage requirement, VMS fleet, AIS reach, weather, fixed structure, fishing lights, pilot or supply transfer), closed-unexplained, closed-false-alarm; notes shared per object (MarineTraffic Vessel Notes, Starboard notes (S)); mark visited (SeaVision (S)); saved filters with email or in-app notification (Skylight, MarineTraffic (S)); watchlists and vessel groups (GFW Vessel Viewer, SeaVision custom lists (S)); a per-lead evidence card with the radar chip, the light, the AIS track and the environment; a "next look" field from E16 so the analyst knows when radar can check again; tasking request export (lat, lon, time window) in the style of Skylight's rule-based tasking (S).

### 2.8 Exports

GeoPackage and GeoJSON (EPSG:4326, plus UTM 49N) for events and leads with caveat, source and licence columns; COG for anomaly, expected activity and look-probability rasters; CSV of labels and of the lead queue; PDF or HTML lead report (GFW print report pattern (S)); a read-only API that returns the caveat field with every record; the open build must never include data/research/* and the research build must stamp RESEARCH_TAG and the CC BY-NC 4.0 URL on screen (R: gfw.py).

## 3. UI toolkit note

Blueprint is "a React-based UI toolkit for the web" for "complex, data-dense web interfaces for desktop applications", packages @blueprintjs/colors, core, datetime, icons, select, table, "made available under the Apache 2.0 License" (R: raw.githubusercontent.com/palantir/blueprint/develop/README.md and LICENSE, "Apache License Version 2.0, January 2004"). Using it is legitimate; the product keeps its own name and never shows the vendor's name, logos or product names.

## 4. Open items for the owner

1. Open the blocked hosts (at minimum support.skylight.global, globalfishingwatch.org, info.seavision.volpe.dot.gov, support.marinetraffic.com, imo.org, fao.org, science.org, nature.com) so every (S) item above can be re-read and the UNVERIFIED tags removed.
2. Confirm aisstream.io terms and receiver sources from its documentation page; until then the AIS layer footer says coverage is UNVERIFIED.
3. Decide the E5 reach thresholds after the first week of recordings; the values above are starting points, not measurements.
4. Decide whether the open build may show any GFW-derived number at all (the FAQ says governments in free public tools count as noncommercial, but a Viettel product does not).

SOURCES
RESOLVED (fetched and read): https://raw.githubusercontent.com/palantir/blueprint/develop/LICENSE
RESOLVED: https://raw.githubusercontent.com/palantir/blueprint/develop/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/gfwr/master/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/gfw-api-python-client/main/README.md
RESOLVED: https://raw.githubusercontent.com/pipeworx-io/mcp-global-fishing-watch/main/README.md
RESOLVED: https://raw.githubusercontent.com/followthemoney/ais_gaps/main/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/pipe-encounters/master/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/pipe-gaps/develop/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/pipe-anchorages/master/README.md
RESOLVED: https://github.com/GlobalFishingWatch/pipe-anchorages
RESOLVED: https://github.com/GlobalFishingWatch/AIS-disabling-high-seas
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/AIS-disabling-high-seas/main/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/AIS-disabling-high-seas/main/data_production/gaps/ais_off_on_events.sql.j2
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/AIS-disabling-high-seas/main/data_production/gaps/ais_gap_events.sql.j2
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/AIS-disabling-high-seas/main/data_production/gaps/ais_gap_events_features.sql.j2
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/paper-industrial-activity/main/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/paper-identifying-global-patterns-of-transshipment/master/README.md
RESOLVED: https://raw.githubusercontent.com/GlobalFishingWatch/paper-dark-fishing-fleets-in-north-korea/master/README.md
RESOLVED: https://github.com/orgs/GlobalFishingWatch/repositories?q=paper
RESOLVED: https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/README.md
RESOLVED: https://raw.githubusercontent.com/allenai/vessel-detection-sentinels/main/LICENSE
RESOLVED: https://github.com/allenai/vessel-detection-viirs
RESOLVED: https://raw.githubusercontent.com/allenai/vessel-detection-viirs/main/readme.md
RESOLVED: https://raw.githubusercontent.com/allenai/vessel-detection-viirs/main/data.md
RESOLVED: https://raw.githubusercontent.com/allenai/sar_vessel_detect/main/README.md
RESOLVED: https://github.com/orgs/allenai/repositories?q=vessel
RESOLVED: https://github.com/DIUx-xView/xview3-reference
RESOLVED: https://github.com/aisstream/aisstream
RESOLVED: https://github.com/aisstream
RESOLVED (repo files): /home/user/Dark-Vessel-Study/docs/STATUS.md
RESOLVED (repo files): /home/user/Dark-Vessel-Study/docs/ocean_context_plan.md
RESOLVED (repo files): /home/user/Dark-Vessel-Study/src/darkvessel/ais/match.py
RESOLVED (repo files): /home/user/Dark-Vessel-Study/src/darkvessel/ais/aisstream.py
RESOLVED (repo files): /home/user/Dark-Vessel-Study/src/darkvessel/ais/gfw.py
RESOLVED (repo files): /home/user/Dark-Vessel-Study/src/darkvessel/ais/s1_passes.py
RESOLVED (repo files): /home/user/Dark-Vessel-Study/src/darkvessel/config.py
SNIPPET ONLY, host blocked: https://support.skylight.global/en_US/events
SNIPPET ONLY, host blocked: https://support.skylight.global/en_US/standard-rendezvous
SNIPPET ONLY, host blocked: https://support.skylight.global/vessel-behavior-events/dark-rendezvous
SNIPPET ONLY, host blocked: https://support.skylight.global/vessel-behavior-events/fishing
SNIPPET ONLY, host blocked: https://support.skylight.global/vessel-behavior-events/entry
SNIPPET ONLY, host blocked: https://support.skylight.global/en_US/vessel-behavior-events/speed-range
SNIPPET ONLY, host blocked: https://support.skylight.global/areas-of-interest/enable-entry-speed-range-events
SNIPPET ONLY, host blocked: https://support.skylight.global/ais-correlation-dark-vessels
SNIPPET ONLY, host blocked: https://support.skylight.global/en_US/satellite-radar
SNIPPET ONLY, host blocked: https://support.skylight.global/en_US/vessel-detection-filters
SNIPPET ONLY, host blocked: https://support.skylight.global/vessel-detection-features-capabilities/vessel-attribute-estimations
SNIPPET ONLY, host blocked: https://support.skylight.global/en_US/vessel-detection-events/night-lights
SNIPPET ONLY, host blocked: https://support.skylight.global/event-details-card
SNIPPET ONLY, host blocked: https://support.skylight.global/saved-filters-alerts
SNIPPET ONLY, host blocked: https://support.skylight.global/en_US/vessel-details
SNIPPET ONLY, host blocked: https://support.skylight.global/what-is-skylight
SNIPPET ONLY, host blocked: https://support.skylight.global/data-sources
SNIPPET ONLY, host blocked: https://support.skylight.global/release-notes
SNIPPET ONLY, host blocked: https://www.skylight.global/platform
SNIPPET ONLY, host blocked: https://www.skylight.global/case-studies/uruguay-tip-cue
SNIPPET ONLY, host blocked: https://www.skylight.global/news/argentina-success-story
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/our-apis/documentation/docs/v3/general-api-doc/data-caveats
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/faqs/what-is-a-vessel-encounter/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/faqs/what-is-loitering-event/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/user-guide/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/faqs/can-i-use-global-fishing-watch-apis-for-commercial-purposes/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/vessel-viewer-tool/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/platform-update/marine-manager-new-analysis-and-environmental-features/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/platform-update/new-detections-from-synthetic-aperture-radar/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/platform-update/introducing-global-reports-on-the-global-fishing-watch-map/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/platform-update/explore-global-fishing-watch-data-via-esris-arcgis-online/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/platform-update/iuu-fishing-risk-insights-dataset-release/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/faqs/how-do-i-view-different-types-of-data-ais-vms-viirs/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/fisheries/mediterranean/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/research/hotspots-of-unseen-fishing-vessels-qa/
SNIPPET ONLY, host blocked: https://globalfishingwatch.org/?p=3513
SNIPPET ONLY, host blocked: https://www.science.org/doi/10.1126/sciadv.abq2109
SNIPPET ONLY, host blocked: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9629714/
SNIPPET ONLY: https://phys.org/news/2022-11-global-analysis-fishing-vessels-identification.html
SNIPPET ONLY, host blocked: https://www.nature.com/articles/s41586-023-06825-8
SNIPPET ONLY, host blocked: https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-1/Sentinel-1_and_AI_reveal_75_of_fishing_vessels_not_tracked
SNIPPET ONLY, host blocked: https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2018.00240/full
SNIPPET ONLY, host blocked: https://info.seavision.volpe.dot.gov/
SNIPPET ONLY, host blocked: https://info.seavision.volpe.dot.gov/releases/
SNIPPET ONLY, host blocked: https://info.seavision.volpe.dot.gov/support/
SNIPPET ONLY: https://www.volpe.dot.gov/news/seavision-improves-africas-maritime-picture
SNIPPET ONLY: https://www.volpe.dot.gov/our-work/infrastructure-systems-and-technology/situational-awareness-and-logistics
SNIPPET ONLY: https://www.navy.mil/DesktopModules/ArticleCS/Print.aspx?PortalId=1&ModuleId=523&Article=3772870
SNIPPET ONLY, host blocked: https://windward.ai/glossary/what-are-ais-gaps-and-ais-handshakes/
SNIPPET ONLY, host blocked: https://windward.ai/blog/what-is-dark-activity-and-why-is-it-surging-in-2026/
SNIPPET ONLY, host blocked: https://windward.ai/glossary/what-is-ais-spoofing/
SNIPPET ONLY, host blocked: https://windward.ai/api-hub/behavioral-analysis-api/
SNIPPET ONLY, host blocked: https://windward.ai/solutions/vessel-screening/
SNIPPET ONLY, host blocked: https://windward.ai/knowledge-base/windward-counter-narcotics-intelligence-report-june-2026/
SNIPPET ONLY, host blocked: https://windward.ai/knowledge-base/your-business-your-risk-your-rules/
SNIPPET ONLY, host blocked: https://windward.ai/blog/mind-the-ais-gap/
SNIPPET ONLY, host blocked: https://support.marinetraffic.com/en/articles/11172276-risk-compliance-methodology
SNIPPET ONLY, host blocked: https://support.marinetraffic.com/en/articles/16124021-29-july-2026-four-new-risk-compliance-capabilities-to-strengthen-vessel-screening-in-marinetraffic
SNIPPET ONLY, host blocked: https://support.marinetraffic.com/en/articles/14639204-15-february-2026-compliance-workspace-now-available-in-marinetraffic
SNIPPET ONLY, host blocked: https://support.marinetraffic.com/en/articles/9552694-types-of-notification
SNIPPET ONLY, host blocked: https://support.marinetraffic.com/en/articles/9552766-vessel-notes
SNIPPET ONLY, host blocked: https://support.marinetraffic.com/en/articles/9552924-why-can-t-i-see-a-vessel-on-the-live-map
SNIPPET ONLY, host blocked: https://support.marinetraffic.com/en/articles/11588240-understanding-ais-gnss-spoofing
SNIPPET ONLY, host blocked: https://help.kpler.com/en/articles/9672698-dark-activity-tag
SNIPPET ONLY, host blocked: https://www.kpler.com/blog/from-dark-port-calls-to-dead-zones-julys-marinetraffic-updates
SNIPPET ONLY, host blocked: https://www.kpler.com/product/maritime/ship-tracking
SNIPPET ONLY, host blocked: https://servicedocs-sm.kpler.com/ais-fundamentals/
SNIPPET ONLY: https://starboard.nz/solutions/fisheries-monitoring
SNIPPET ONLY: https://starboard.nz/software/roadmap/
SNIPPET ONLY: https://help.starboard.nz/en/articles/11175215-spotting-ais-spoofing-in-starboard
SNIPPET ONLY, host blocked: https://www.imo.org/en/OurWork/Safety/Pages/AIS.aspx
SNIPPET ONLY, host blocked: https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/AIS/Resolution%20A.1106(29).pdf
SNIPPET ONLY: https://www.irclass.org/media/6214/technical-circular-no044.pdf
SNIPPET ONLY, host blocked: https://iumi.com/news/iumi-eye-newsletter-december-2019/regulatory-framework-for-ais-going-dark-and-switching-off
SNIPPET ONLY: https://maritime-mutual.com/risk-bulletins/automatic-identification-system-ais-cloaking-and-consequences
SNIPPET ONLY: https://casualnavigation.com/when-can-you-switch-off-ais
SNIPPET ONLY: https://eur-lex.europa.eu/eli/dir/2011/15/oj
SNIPPET ONLY, host blocked: https://www.fao.org/3/Y0772E/Y0772E.htm
SNIPPET ONLY: https://cil.nus.edu.sg/wp-content/uploads/2017/08/2001-FAO-International-Plan-of-Action-to-PreventDeter-and-Eliminate-IllegalUnreported-and-Unregulated-Fishing.pdf
SNIPPET ONLY, host blocked: https://www.fao.org/iuu-fishing/tools-and-initiatives/transshipment/en
SNIPPET ONLY, host blocked: https://openknowledge.fao.org/server/api/core/bitstreams/225b1de1-7452-45e3-a51a-42a449ad458d/content
SNIPPET ONLY: https://www.un.org/oceancapacity/sites/www.un.org.oceancapacity/files/files/Projects/UNFSA/docs/fao_vgt.pdf
SNIPPET ONLY: https://www.asoc.org/wp-content/uploads/2024/12/An-analysis-of-FAO-Voluntary-Guidelines-for-Transshipment-and-CCAMLR-transshipment-regulations.pdf
SNIPPET ONLY: https://stopillegalfishing.com/news/cofi-35-endorses-voluntary-guidelines-for-transshipment
SNIPPET ONLY, host blocked: https://www.fao.org/iuu-fishing/resources/detail/en/c/1697526/
SNIPPET ONLY, host blocked: https://www.fao.org/iuu-fishing/tools-and-initiatives/quantifying-iuu-fishing/en/
SNIPPET ONLY, host blocked: http://www.fao.org/3/cb3175en/cb3175en.pdf
SNIPPET ONLY: https://www.pew.org/-/media/assets/2017/11/gtc_best_practices_for_transshipment.pdf
SNIPPET ONLY: https://unodc.org/unodc/en/environment-climate/webstories/legislative-guide-fisheries.html
SNIPPET ONLY: https://www.worldwildlife.org/pages/tnrc-external-resource-rotten-fish-unodc
SNIPPET ONLY: https://c4ads.org/reports/strings-attached/
SNIPPET ONLY: https://dialogue.earth/en/fisheries/study-sheds-light-on-murky-world-of-reflagging/
SNIPPET ONLY, UNVERIFIED claim of terrestrial plus satellite sources: https://mintlify.wiki/danizd/GeoSentinel/data-sources/ais-vessels
UNVERIFIED (blocked, not excerpted): https://aisstream.io/documentation
UNVERIFIED (blocked): https://developer.windward.ai/page/use-case-2-area-monitoring-and-investigation-government-mission-planning
UNVERIFIED (blocked): https://arxiv.org/abs/2206.00897
UNVERIFIED (blocked): https://arxiv.org/abs/2312.03207