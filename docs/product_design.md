# SCS Vessel Watch: product specification

Version 1.2, 2026-10-09 (UTC). Task R1-T4. Version 1.1 was revised after review (one product caveat, chips by bytes, guarded browser storage, third-party strings, check PW-16). Version 1.2 has the first live pass as rerun with five scenes, the Pass page AIS counts split by footprint and AOI, EEZ boundary lines only in the single-file page, and the bundle of contract 1.2.0. Status: ready for round 2 (backend, frontend, single-file build, checks).

Companion files: `app/CONTRACT.md` (objects, fields, sources, API, bundle), `docs/research/stack_decision.md` (packages, versions, measurements), `docs/research/interface_design_brief.md` and `docs/research/mda_products_brief.md` (the research this spec is built on; cited below as "interface brief" and "MDA brief" with section numbers). Shared decisions D1 to D3 are in `docs/PROJECT_BOARD.md`.

> **"Dark" does not mean illegal.** 'Dark' means only that no AIS position was matched to this radar contact. It does not mean illegal. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and both satellite and terrestrial AIS have blind spots: satellite AIS misses messages in busy coastal waters, and shore receivers cover only the waters within their radio range. Treat every unmatched contact as a lead for review, not as evidence of wrongdoing. An AIS gap is not proof of intent.

## 1. Purpose and users

**Purpose.** For every Sentinel-1C/1D radar contact over the South China Sea AOI (Natural Earth South China Sea, Gulf of Tonkin and Gulf of Thailand; Ca Mau as the detail area), the product answers two questions, in this order:

1. **Detection.** Is it a vessel? Radar contact, detector class, clutter and fixed-structure rules, persistence, CNN verifier score, optical and Satlas checks where they exist.
2. **Identification.** Who is it? Either an AIS match to an MMSI with its identity (name, call sign, IMO, type, AIS length, flag as claimed), or, when nothing matches, a dark lead with all its evidence: radar length and class, nearest AIS vessels and their distance and time offset, AIS reach at the spot, VIIRS light the same night, location and sea context.

Everything else (ocean layers, expected activity, coverage) is context for those two answers. It is never the headline.

**Users.**

| User | Form | Main jobs |
|---|---|---|
| The analyst (the owner) | local app, both builds | triage the leads queue; open a contact and judge the evidence; label contacts; record lead decisions; export to desktop GIS |
| A reviewer (Viettel) | shared single-file page, open build | read the queue and the lead pages; check provenance; understand the limits |
| Paper co-authors | local app or single-file page, research build | inspect identity rates, misses by length, GFW comparison |

**Non-goals.** No verdicts of wrongdoing, no "illegal", "suspicious", "violation" or "threat" wording, no positions on boundaries or claims, no satellite tasking, no user accounts or access control in version 1, no external map tiles, fonts or scripts in the single-file page.

## 2. The caveat and the build labels

### 2.1 The dark caveat

There is one full caveat, `PRODUCT_CAVEAT`, quoted above and defined in `app/CONTRACT.md` section 1.1. It extends `darkvessel.config.DARK_CAVEAT`, which names only the satellite blind spot, with the terrestrial one (the open build's AIS comes from shore receivers) and with "An AIS gap is not proof of intent." The same string goes into the API `caveat` field, every export, every lead card and lead page. Adding it to `darkvessel.config` is a change to a shared file, reported to the PM. The short text is `DARK_CAVEAT_SHORT`: "Dark = no AIS match. Not evidence of illegal activity." Both banners link the short text to the full caveat (a `Popover` on desktop, a full-screen sheet on phone).

| Where | Text | Form |
|---|---|---|
| Top banner, every view | short caveat plus the build line (2.2) | full-width strip above the navbar, 28 px, one line on desktop, wraps on phone |
| Bottom banner, every view | the same | full-width strip under the timeline |
| Every object page header | short caveat | muted line under the title |
| Every lead card and lead page | `PRODUCT_CAVEAT` plus the lawful explanations for that lead type (section 4.1) | callout |
| Every unmatched or no-coverage contact tooltip | short caveat | last line |
| Every AIS gap or silence display | "An AIS gap is not proof of intent." | last line |
| Every export | `PRODUCT_CAVEAT` in a `caveat` column or field, in the file metadata, and at the top and bottom of HTML reports | text |
| Every API record | `caveat` field = `PRODUCT_CAVEAT` (contract sections 1.1 and 5) | text |

The banners are handling banners, not security markings. The product never prints a classification word.

**Status words shown to users** (the contract's `ais_status` values):

| `ais_status` | Label | Map colour token | Meaning |
|---|---|---|---|
| `matched` | AIS matched | `--status-matched` | paired one-to-one with an AIS vessel under the stated gate |
| `unmatched` | No AIS match (AIS heard nearby) | `--status-unmatched` | no pair, but AIS was heard near the contact during the window (open build: in its 0.25 degree cell or within 20 km; research build: rule in the file's `about` layer); the only state that can become a dark lead |
| `no_coverage` | No AIS coverage here | `--status-nocov` | no pair and nothing heard near the contact during the window (rule stated in each file's `about` layer); says nothing about the contact |
| `not_checked` | AIS not checked | `--status-notchecked` | no AIS source was applied to this run in this build (September run in the open build, Ca Mau scene) |

Rules: the word "dark" appears only as "dark lead", always next to the caveat. Danger red is never used for any AIS status; it is reserved for errors. Colour is never the only cue: every status also has a text label and a marker shape (section 6.3).

### 2.2 Build labels

| Build | Navbar tag | Banner line (both banners, every view) |
|---|---|---|
| Open | `OPEN BUILD` (neutral) | "Open build. Open-licensed sources and live AIS relayed by aisstream.io. Dark = no AIS match. Not evidence of illegal activity." |
| Research | `RESEARCH BUILD` (violet) | "Research build, noncommercial, CC BY-NC 4.0. Contains Global Fishing Watch data. Powered by Global Fishing Watch. Dark = no AIS match. Not evidence of illegal activity." |

"Powered by Global Fishing Watch." is linked to https://globalfishingwatch.org, as the GFW terms of use require for websites and visualisations. The research label appears on every view of the research build and never in the open build (Playwright checks PW-05 and PW-06).

The footer of every view carries the data credit: "Contains modified Copernicus Sentinel data 2026", plus the per-layer credits of the layers in view (contract section 2).

## 3. Information architecture

### 3.1 Desktop console (1280 px and wider)

```
+----------------------------------------------------------------------------------+
| caveat banner (28)                                                               |
+----------------------------------------------------------------------------------+
| navbar (50): SCS Vessel Watch | build tag | [ Omnibar: search ... mod+K ] | UTC/ICT | km/nm | theme | ? |
+----------------------------------------------------------------------------------+
| filter bar (36): active filter pills  x  ...                        Clear filters |
+-------------------+--------------------------------------+-----------------------+
| left rail (320)   | map (flexible)                       | inspector (360)       |
| tabs: Leads       |                                      | tabs: Summary         |
|       Layers      |                                      |       Evidence        |
|       Find        |                                      |       Links           |
|       Histogram   |                                      |       Provenance      |
|       Info        |                                      | (opens on selection)  |
+-------------------+--------------------------------------+-----------------------+
| timeline (160, collapsible to 32)                                                |
+----------------------------------------------------------------------------------+
| caveat banner (28)                                                               |
+----------------------------------------------------------------------------------+
```

At 1440 x 900 the map is 760 x 598 px. Sizes follow the interface brief section 3 and Blueprint 6.20.0: navbar 50 px with 0 16px padding, inspector at the Drawer SMALL width (360px), 4 px spacing unit, 4 px radius, buttons 30 px (24 small, 40 large).

- **Navbar.** At most five primary actions (interface brief section 3): Omnibar, Leads, Map, Export, Help. Right side: time zone (UTC default, ICT = UTC+7), units (km default, nautical miles), theme, the `?` shortcuts dialog.
- **Filter bar.** Every active filter is a removable pill (Blueprint `Tag` with remove); "Clear filters" on the right. Filters fade non-matching objects on the map and hide them in tables (interface brief section 3).
- **Left rail.** `Leads` (the queue, default tab), `Layers` (tree with toggles, legend, licence per layer), `Find` (objects in view, coordinates), `Histogram` (counts by type, then by property; click to filter), `Info` (data freshness, sources, coverage summary, about).
- **Inspector.** Opens on selection with the panel form of the object page (section 4); "Open full page" switches the map area to the full form. Several selected objects show a list; one shows details.
- **Timeline.** One timeline for the whole console (section 7).

### 3.2 Routes

Hash routes, so the single-file page works without a server and links can be shared inside the local app.

| Route | View |
|---|---|
| `#/` or `#/leads` | console, Leads tab, queue |
| `#/map` | console, map focus, inspector closed |
| `#/contact/<det_id>` | Contact page |
| `#/vessel/<vessel_key>` | Vessel page (`mmsi:<mmsi>` open build, `gfw:<vessel_id>` research build) |
| `#/light/<light_id>` | Light page |
| `#/event/<event_id>` | Event page |
| `#/lead/<lead_id>` | Lead page |
| `#/pass/<pass_id>` | Pass page |
| `#/cell/<cell_id>` | Cell context page |
| `#/about` | method, sources, licences, limits |

Query part (after `?`) keeps view state: `t0`, `t1` (time window, ISO 8601 UTC), `z`, `lat`, `lon` (map), `layers` (comma list), `f` (filters). Object pages open as the panel form when the console is visible and as the full form on phone and when the route is opened directly.

## 4. Views and object pages

Every object page has the same frame: header (`EntityTitle`: id, type, subtitle with time and sensor, status tags), the short caveat, tabbed sections, and a provenance chip on every field (section 4.8). The panel form shows the first three sections; the full form shows all of them with the map and timeline of the object.

### 4.1 Leads queue (home)

The core of the product. A lead is one object or cell plus the events on it, a review priority and the caveat (MDA brief section 2.5).

**Lead types** (MDA brief 2.5; v1 builds L1 and L7; L2 to L6 and L8 follow when their events exist):

| Type | Name in the UI | Rule (short) | Lawful explanations pre-listed on the card |
|---|---|---|---|
| L1 | Unmatched radar contact in AIS reach | `unmatched`, CNN score at least 0.5, both channels, no clutter or near-fixed flag, wind below 12 m/s, no deep convection | no carriage requirement, VMS fleet, class B out of range, detector false positive (precision 0.77 on 1A/1B) |
| L2 | AIS silence at a radar look | an AIS silence (E8) open at pass time and an unmatched or no-coverage contact inside the dead-reckoned envelope | the contact may be another vessel; the silence may be lawful |
| L3 | Possible meeting with an unidentified contact | AIS loitering or slow activity and an unmatched radar or light contact within 1 km | fishing, fuel and supply transfers are lawful |
| L4 | Encounter between two AIS vessels | E10 strict or short | lawful transshipment and bunkering |
| L5 | Identity or position anomaly | E9 position jump or E12 identity change | GNSS and installation faults, re-registration |
| L6 | Activity anomaly cell | E15 z-score at least 3 in calm weather, or a dark-share jump of 20 points | model error, weather, fleet migration; counts, not vessels |
| L7 | Lit activity where radar does not look | clear-sky VIIRS lights in cells never imaged in 90 days | mostly lawful fishing; a coverage statement for tasking, not a vessel lead |
| L8 | Area entry | entry into a user reporting box, alone or with L1 to L5 | EEZ crossings alone never make a lead |

**States** (MDA brief 2.7):

| State | Meaning | Required input |
|---|---|---|
| `new` | not opened | none |
| `reviewing` | opened or claimed | none (set on first open, or press R) |
| `closed_explained` | a lawful or technical explanation fits | one reason from: no carriage requirement (size or type); VMS fleet (in Vietnam fishing vessels of 15 m and over report by VMS, which this product does not see; UNVERIFIED, snippet-level in `docs/STATUS.md`); outside AIS reach; weather or sea clutter; fixed structure; fishing lights; pilot or supply transfer; other (note required) |
| `closed_unexplained` | evidence reviewed, no explanation found; this is not a finding of wrongdoing | a note |
| `closed_false_alarm` | the radar object is not a vessel | one of: sea clutter, rain cell, fixed structure, ambiguity or sidelobe, duplicate, other (note) |

Any closed lead can be reopened to `reviewing` with a note. Every transition is appended to the decision log with user, time (UTC), from, to, reason and note (contract section 3.5). Nothing is ever deleted.

**Review priority, 0 to 100.** Not a risk score. Shown as a number and a band (low 0 to 33, medium 34 to 66, high 67 to 100), always with the factor list and each factor's points. Start values, uncalibrated, versioned as `priority_model_id`:

| Factor | Points | Inputs |
|---|---|---|
| Evidence quality | 0 to 30 | CNN score above the model threshold (scaled), both channels, clear sky and wind below 12 m/s |
| Corroboration | 0 to 25 | radar plus light plus AIS behaviour on the same object within 3 h and 2 km |
| AIS reach quality at the spot | 0 to 20 | `ais_reach` of the cell, AIS vessels heard within 10 km (how much an absence of AIS can mean here) |
| Persistence across passes | 0 to 15 | an unmatched contact again within 2 km on another pass within 72 h |
| Area weight | 0 to 10 | analyst-set per reporting box, default 0 |

"No AIS match" is the gate that makes an L1 lead; it adds 0 points by itself (MDA brief 2.6). The live files' `dark_lead` flag (an unmatched `high` or `medium` contact) marks an L1 candidate only. The L1 rule above adds the CNN, weather and clutter conditions, and the queue shows Lead objects, never the raw flag (contract section 3.1). The card shows a tag "uncalibrated" until the weights are calibrated against the owner's labels (`data/labels/owner_2026-10.csv` when it exists); the About view then reports the false-alarm rate per lead type.

**Queue table** (Blueprint `Table2`; columns): priority (bar and number), type, state, primary object (det_id or MMSI), time (UTC or ICT), reporting box, AIS status, CNN score, radar length (m), evidence count, next radar look. Default sort: priority, high first. Default filter: state is `new` or `reviewing`. Left rail filters: type, state, priority band, reporting box, pass, AIS status, CNN accepted, time window.

**Lead card** (row expansion and inspector panel): radar chip; factor list with points; the full caveat; the lawful explanations for the type; "what would change this": an AIS match on the late re-check, the next Sentinel-1 look at this spot (time from the pass plan), an optical view; decision buttons (Reviewing, Explained, Unexplained, False alarm) with the reason picker.

Keyboard: J and K next and previous row, N next `new` lead, Enter open the lead page, R reviewing, E explained, U unexplained, X false alarm (section 9).

### 4.2 Contact page

The detection and identification record of one radar contact. Sections in order:

1. **Header.** det_id; subtitle in the form "Radar contact, Sentinel-1D IW, 08 OCT 2026 22:58:12 UTC" with the DTG form `082258Z OCT 26`; tags: detector class, CNN verdict, AIS status, `research` when `research_only` is true.
2. **Radar chip.** VV and VH side by side, 64 x 64 px each at the 10 m GRD pixel spacing (640 m, the window the CNN scores), labelled "radar geometry, not north-up". When no chip is embedded or cached: a `NonIdealState` with "Fetch chip" (local app only).
3. **Detection.** Class (`high` both channels, `medium` one channel, `fixed`, `low`) with the rule outcomes: clutter zone (5 or more weak returns within 1 km), near fixed (within 250 m of a fixed structure), persistence (bright on N of M earlier passes). Radar length estimate with the note "pixel extent: crude and biased upward, a 2-pixel object reads 20 m". Signal-to-clutter VV and VH (dB), incidence angle, polarisation class.
4. **Verification.** CNN score with the threshold of its model (`verifier_v0`, 0.6318), held-out precision 0.77 and recall 0.75 on Sentinel-1A/1B labels (50 m rule), and the knowledge-limits flag "trained on 1A/1B labels; 1C/1D transfer not yet scored; no training label under 15 m" (`docs/STATUS.md`, `docs/ml_verifier.md`). Optical check and Satlas distance when the contact was in those samples.
5. **Identification.** The block that answers "who is it":
   - `matched`: MMSI, vessel name, call sign, IMO, flag (the MID country, "as claimed by the transponder"), ship type, AIS length against radar length (ratio), match distance (m), time offset (s), gate used, method, quality (`high`, `medium`, `low`) with its rule text, identity source; link to the Vessel page. Note: "every identity field is a transponder self-report".
   - `unmatched`: "No AIS match (AIS heard nearby)" then the evidence: nearest AIS vessel (MMSI, name, distance, time offset; link), AIS vessels within 10 km, AIS reach of the cell (share of recorded hours), VIIRS light within 2 km the same night (link), in the research build GFW evidence (gap events within 50 km and 24 h, encounters and loitering within 10 km and 24 h, GFW neural vessel type); link to the lead when one exists.
   - `no_coverage`: "No AIS coverage here: nothing was heard in this cell during the window", the reach number and the rule.
   - `not_checked`: "AIS not checked for this run in this build."
6. **Context.** Weather at the contact (GFS 10 m wind, Himawari-9 cloud-top temperature, deep convection flag); ocean (depth, distance to coast and to the nearest major port, SST and distance to the nearest front, current speed, wave height, shipping density), each with its valid time; look status (last Sentinel-1 look, next planned pass).
7. **Links.** Pass and scene, leads citing it, contacts within 2 km on the same pass, lights within 2 km the same night, the cell.
8. **Provenance.** Table of every field with its source (section 4.8).

Actions: Label (vessel, structure, clutter, unsure; keys 1 to 4), Open or create lead, Export (GeoJSON, CSV row, HTML report), Copy position (DD, DMS, MGRS), Centre map.

### 4.3 Vessel page

One AIS identity. Open build: `mmsi:<mmsi>` from aisstream. Research build: `gfw:<vessel_id>` from the GFW vessels API, linked to the MMSI.

- **Identity.** MMSI, name, call sign, IMO, flag (MID country, as claimed), ship type, length and width, AIS class (A or B), destination and ETA (self-reported), first and last heard; research build adds GFW vessel id, registry length, gear, tonnage and registry sources. Note on the header: "All identity fields are self-reported by the transponder or published by the data source; they can be wrong, reused or spoofed."
- **Identity history.** Static message changes over the recording (feeds E12).
- **Track.** Map and timeline of positions in the window; gaps longer than 6 h drawn as dashed segments with their duration and "An AIS gap is not proof of intent."
- **Contacts matched.** Table: det_id, time, match distance, time offset, quality.
- **Events.** Silences, encounters, loitering, port visits (research), identity changes.
- **AIS reach along the track.**

### 4.4 Light page

One VIIRS Day/Night Band light at sea: light_id, satellite, time (UTC), night (local evening date), radiance (nW cm-2 sr-1), quality (clear or under cloud), class, moon illumination, nights seen within 500 m, recurring site (link), Satlas infrastructure distance, Sentinel-1 passes in 90 days over the spot, radar contacts within 2 km the same night. Caveat on the page: a light is not a vessel identity, and lit vessels are mostly fishing (GFW's VIIRS note: the layer "is likely to show vessels associated with activities like squid fishing, which use bright lights and fish at night"); auroral and moonlit clouds, image artefacts, near-shore lights, lightning and gas flares are known false sources (AI2 vessel-detection-viirs `data.md`), and platforms show as recurring lights.

### 4.5 Event page

One observation event of the MDA brief section 2 (E1 to E17), only for types the data hold: type and name, start and end (UTC), duration, position or track segment, objects involved (links), the rule with its thresholds as applied, source (aisstream or GFW), the type's caveat (for example "lawful transshipment, bunkering, pilot and supply transfers look identical" for encounters), and `research_only`. EEZ entry events carry the Marine Regions statement (section 6.2) and never a priority.

### 4.6 Lead page

Full form of the lead card: id, type, priority with factors and model id, state and the full decision history, evidence list grouped by type (contacts, lights, events, vessels, cells; each with a preview), the map of the evidence, the timeline of the evidence, "what would change this", next radar look, notes, decision buttons, and "Export lead report" (HTML). A small graph tab shows lead, contact, pass, light, weather sample and candidate AIS vessels with their links (interface brief section 8, kept small).

### 4.7 Pass and Cell pages

- **Pass page.** One Sentinel-1 pass: mission, relative orbit, direction, start and stop, status (past, in progress, upcoming), source (ESA acquisition plan or 12-day repeat prediction, with the note that a repeat prediction is not ESA's plan), footprint, scenes, AIS recorded during the window in three counts, in this order: inside the footprint, within 0.3 degree of it (what the matcher sees), and anywhere in the AOI (which only shows that the feed was up). Then contacts by AIS status and the link to the leads. When most contacts of a pass are `no_coverage`, the page leads with that coverage result in words. Example, the first live pass `live_S1D_20261008T2258` (five scenes, east side of the Gulf of Thailand): "No AIS was heard inside or within 0.3 degree of any of the 5 scenes, so none of the 3,081 contacts could be checked against AIS. The feed was up: each scene window recorded 8,812 to 9,074 positions elsewhere in the AOI." (`data/live/live_summary.json`, `scenes_4326`, `docs/live_pass.md`). `no_coverage` contacts never form L1 leads.
- **Cell page.** One 0.25 degree model-grid cell: static sea fields, the nightly and per-pass ocean fields, AIS reach, look probability, and (when the model exists) expected and observed counts. Caveat: "Ocean and weather layers describe the sea, not what any vessel does." The cell's Marine Regions attributes (`marineregions_*`: MRGID, geoname, pol_type, share, count) are hidden while the EEZ layer is off; with it on they appear in their own block headed "As published by Marine Regions", with the statement of section 6.2. They are never a filter or a lead factor.

### 4.8 Provenance on every field

Every displayed value has a source chip (Blueprint `Tooltip` on hover and focus, `Popover` on click or tap) with: source name, product or file and layer, acquisition or valid time (UTC), method and script with git hash, licence, access date. Measured values ("information") are in normal text; model and analyst outputs ("judgment": CNN score, match quality, priority, labels) are in muted text with a model id (interface brief section 11). Likelihood words, if any are used, come only from the ICD 203 list (almost no chance, very unlikely, unlikely, roughly even chance, likely, very likely, almost certain) and never share a sentence with a confidence word.

## 5. Search: the Omnibar

Opened with mod+K, `/` or shift+O (Blueprint `Omnibar`). Results are grouped by type with counts; arrows move, Enter opens, Esc closes; the last 10 queries are kept in the browser.

| Input | Example | Result |
|---|---|---|
| MMSI | 9 digits | Vessel |
| IMO | `IMO` followed by 7 digits, or 7 digits alone | Vessel |
| det_id | `S1C_20260920T104816_00051` (regional and live: mission, scene start, 5-digit index), `S1D_20260929T1110_0002` (Ca Mau) | Contact |
| light_id | `SPP_20260906T170505_009581` (`SPP`, `N20`, `N21` prefixes) | Light |
| lead_id, event_id, pass_id | `L1-...`, `E8-...`, `S1D_R128_20261006T1102` | Lead, Event, Pass |
| Vessel name or call sign | any text (case-insensitive prefix match, then fuzzy) | Vessel |
| Decimal degrees | `10.25, 107.5`, `10.25N 107.5E`, `107.5E 10.25N` | Go to point |
| Degrees, minutes, seconds | `10°15'00"N 107°30'00"E`, `10 15 00 N 107 30 00 E` | Go to point |
| Degrees and decimal minutes | `10°15.000'N 107°30.000'E` (the demo's display format) | Go to point |
| MGRS | `48PVQ8899650631`, `48P VQ 88996 50631`, `48PVQ889506` (any precision; this example is 104.9E 8.6N, off Ca Mau) | Go to point |

Coordinates: a bare pair is read as latitude, longitude unless the first number is above 90; the result row states the reading ("read as 10.25 N, 107.5 E"). "Go to point" centres the map, drops a marker, and lists objects within 5 km. Every coordinate display offers DD, DMS, DDM and MGRS (MGRS from `mgrs` 2.2.0 `forward`).

## 6. Map

### 6.1 Base and projection

Leaflet 1.9.4 in Web Mercator, no tile layer. Base: land from Natural Earth 10 m (public domain), the AOI outline, an optional 1 km shore buffer; map sea and land colours are theme tokens. The Ca Mau scene view shows the radar image in its own grid (UTM 48N, as in the demo) with coordinates converted for display. Bulk point layers use the custom typed-array canvas layer (stack decision section 4.1). Tools: zoom buttons, `0` fit to selection, scale bar (km or nm), measure distance, cursor readout in DD, DMS and MGRS, rectangle "search within".

### 6.2 Layers and defaults

On by default:

| Layer | Source | Notes |
|---|---|---|
| Land, AOI outline | Natural Earth 10 m | base |
| Radar contacts in the time window | contract Contact | styled by AIS status (6.3) |
| Leads | contract Lead | ring around the primary object |
| AIS vessels, last position in the window | aisstream (open), GFW presence-derived (research) | class B drawn smaller |
| Footprints of passes in the window | contract Pass | dashed outline |

Off by default: fixed structures; VIIRS lights; VIIRS recurring sites; AIS tracks; AIS reach (share of hours heard, 0.25 degree); Sentinel-1 coverage (90 days); look probability (1, 7, 30 days); next passes; reporting boxes; ports; and every ocean layer (depth, depth contours, distance to coast, distance to port, SST, SST gradient, front frequency, front lines of the showcase night, chlorophyll, current speed, mixed-layer depth, wave height, wind, shipping density all, commercial, fishing, oil and gas, passenger, leisure); expected activity and anomalies when they exist. No ocean layer is on by default.

**EEZ (Marine Regions), off by default.** Layer name: "Maritime boundaries as published by Marine Regions". Legend and first-enable notice: "Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), World EEZ v12, CC BY 4.0, doi:10.14284/632. In this sea many zones overlap or are disputed; the source marks them. This product takes no position on any boundary or claim." followed by the source's own disclaimer: "VLIZ expresses no opinion about the legal state neither of any country, territory or area nor concerning its delimitation, frontier or borders. The data has no legal value whatsoever." (https://www.marineregions.org/disclaimer.php). Drawn as thin neutral lines with no fill and no labels on the map; the attribute table shows the source's own fields. The single-file page carries only the published boundary lines (Marine Regions "Maritime Boundaries (v12, world, 2023)", 77 lines in the AOI, with `line_type` as published, for example "Median line", "Treaty", "Unsettled (maritime)"), simplified for display; the local app also serves the polygons (contract section 6.2, Geo). EEZ entry never creates a lead by itself.

**Reporting boxes.** The six boxes of `darkvessel.ocean.grid.REPORTING_BOXES`, the same as in `scripts/21_viirs_regions.py` (west, south, east, north): Gulf of Tonkin (105.5, 17.0, 110.0, 22.5), North shelf (110.0, 18.0, 118.0, 23.5), Gulf of Thailand (99.0, 6.0, 105.0, 14.0), South Vietnam shelf (105.0, 6.0, 110.0, 12.0), Central sea (110.0, 6.0, 118.0, 17.0), Southern sea (102.0, -3.5, 110.0, 6.0). Labelled "reporting box: for statistics only, not a boundary".

Every layer row in the Layers tab shows its legend, its licence and its provenance chip.

### 6.3 Marker styling

| Property | Encoding |
|---|---|
| AIS status | colour token (`--status-*`) and a text label in tooltips and tables |
| Detector class | filled circle = `high` (both channels), ring = `medium` (one channel), square = `fixed`, small dot = `low` (Ca Mau only) |
| CNN rejected (`cnn_vessel` false) | 50 % opacity |
| Radar length | 3 sizes: under 25 m, 25 to 100 m, over 100 m |
| Lead | 2 px ring in the text colour, 6 px outside the marker |
| Selection | 2 px ring in the focus colour plus a halo |

At AOI scale contacts draw as 3 px dots; from zoom 7 as full markers. Every marker has a 1 px outline in the map background colour so it keeps 3:1 contrast where it overlaps land.

## 7. Timeline

One timeline, bottom panel, 160 px (collapsible to 32 px). All map layers and tables respect the time window even when the panel is collapsed.

- **Default window:** the 12 days ending at the latest processed pass (one Sentinel-1 repeat cycle, 12 days per the Sentinel-1 mission page).
- **Rows:** Sentinel-1 passes (bars per scene, colour by mission); radar contacts per pass (stacked bars by AIS status); AIS recording (hours with data; recorder gaps shaded and labelled "recorder gap", for example 15:20 to 22:38 UTC on 2026-10-08 and 08:21 to 13:33 UTC on 2026-10-09, `docs/live_pass.md`); VIIRS nights; events (bars from start to end); upcoming passes (dashed, from the acquisition plan).
- **Interactions:** Shift+drag sets a time filter; double-click sets the cursor; wheel zooms; mod+wheel pans; `[` and `]` step to the previous and next pass; Space plays through passes; "Latest" jumps to the newest data (local app polls for new files every 60 s).
- **Time display:** UTC by default, ICT (UTC+7) by toggle; DTG option `DDHHMMZ MON YY`.

## 8. Exports

| Format | Form | Content |
|---|---|---|
| GeoPackage | local app only (backend writes it) | selected objects; layers `<type>_4326` and `<type>_utm49n` (Ca Mau objects `<type>_utm48n`), plus an `about` table with the caveat, build, sources, licences, git hash, generated time |
| GeoJSON | both | RFC 7946, EPSG:4326, one FeatureCollection per type; `caveat`, `build`, `licence` on every feature and in the collection's top-level `meta` |
| CSV | both | UTF-8, one row per object, `caveat`, `build` and `licence` columns; lat and lon in decimal degrees |
| HTML lead report | both | one self-contained printable file: banners top and bottom, chip, evidence table, factor list, provenance table, decision history |
| Labels CSV | both | `det_id,label,user,time_utc` as the demo page wrote them (`data/labels/owner_2026-10.csv` format) |

The research build stamps `research build only, noncommercial (Global Fishing Watch data, CC BY-NC 4.0)` and the licence URL on every export. The open build never exports a GFW field.

## 9. Keyboard shortcuts

Blueprint `useHotkeys`; `?` opens the generated shortcuts dialog. Single-key shortcuts are off while typing in an input.

| Keys | Action |
|---|---|
| mod+K, `/`, shift+O | Omnibar |
| `?` | shortcuts dialog |
| Q, L, F, H | left rail tab: Leads (queue), Layers, Find, Histogram |
| I | toggle the inspector |
| T | toggle the timeline |
| J, K | next, previous row |
| N | next `new` lead (or next unlabelled contact in label mode, as on the demo page) |
| Enter | open the selected object page |
| R, E, U, X | lead: reviewing, closed explained (opens reasons), closed unexplained, closed false alarm |
| 1, 2, 3, 4 | contact label: vessel, structure, clutter, unsure |
| `+`, `-`, arrows | zoom, pan (map focused) |
| 0 | fit the map to the selection |
| D | measure distance |
| `[`, `]` | previous, next pass |
| Space | play or pause the timeline |
| Esc | clear selection, close overlay |
| mod+E | export dialog |

## 10. Themes

Dark is the default; light is one click away (navbar). The theme sets `data-theme` on `<html>` before first paint and toggles Blueprint's `bp6-dark` class on `<body>` in step; the choice is kept in the browser. Colour tokens follow the artifact rule: light values on `:root`, dark values under `@media (prefers-color-scheme: dark)` guarded by `:root:not([data-theme="light"])` and again under `:root[data-theme="dark"]`; the startup script writes `data-theme="dark"` unless the viewer chose light, so dark is the default whatever the system setting. `body` always has an explicit background. Tokens are plain hex (no relative colour syntax).

Token values come from the Blueprint 6 palette (`@blueprintjs/colors` 5.1.16). Contrast ratios were computed with the WCAG 2.1 relative-luminance formula.

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg-app` | #1c2127 dark-gray1 | #f6f7f9 light-gray5 | body, map frame |
| `--bg-panel` | #252a31 dark-gray2 | #ffffff white | rail, inspector, navbar |
| `--bg-elevated` | #2f343c dark-gray3 | #edeff2 light-gray4 | cards, popovers |
| `--map-sea` | #111418 black | #e5e8eb light-gray3 | map background |
| `--map-land` | #383e47 dark-gray4 | #c5cbd3 gray5 | land |
| `--text` | #f6f7f9 | #1c2127 | body text |
| `--text-muted` | #abb3bf gray4 | #5f6b7c gray1 | judgments, secondary text |
| `--link` | #8abbff blue5 | #215db0 blue2 | links |
| `--caveat-bg`, `--caveat-text` | #3e3224, #fbb360 orange5 | #f0e8de, #77450d orange1 | caveat banners |
| `--research-bg`, `--research-text` | #3c2844, #d69fd6 | #ebe1ee, #5c255c | research label |
| `--status-matched` | #4c90f0 blue4 | #215db0 blue2 | AIS matched |
| `--status-unmatched` | #ec9a3c orange4 | #935610 orange2 | no AIS match |
| `--status-nocov` | #8f99a8 gray3 | #5f6b7c gray1 | no AIS coverage |
| `--status-notchecked` | #738091 gray2, hollow | #738091 gray2, hollow | AIS not checked |
| `--fixed` | #13c9ba turquoise4 | #007067 turquoise2 | fixed structures |
| `--light` | #fbd065 gold5 | #866103 gold2 | VIIRS lights |

Computed contrast (dark / light): text on app background 15.11 / 15.11; text on panel 13.47 / 16.20; muted text on app background 7.66 / 5.05, on elevated 5.92 / 4.70; link on app background 8.20 / 6.01; caveat text on caveat background 6.95 / 6.55; research text on research background 6.20 / 8.82. Markers against map sea: matched 5.76 / 5.24, unmatched 8.14 / 4.77, no coverage 6.41 / 4.40, not checked 4.60 / 3.27, fixed 8.87 / 4.86, light 12.59 / 4.58. "Not checked" falls below 3:1 against land (2.68 / 2.46), which the 1 px outline (section 6.3) covers; contacts are on sea by construction.

## 11. Accessibility targets (WCAG 2.1 AA)

- Text contrast at least 4.5:1, large text 3:1 (SC 1.4.3); markers, focus rings and control borders at least 3:1 (SC 1.4.11). Section 10 lists the computed values.
- Reflow: no horizontal scrolling at 320 CSS px width (SC 1.4.10); the phone layout is tested at 390 px.
- Keyboard: every action reachable without a mouse; focus always visible (SC 2.4.7); the map has a list equivalent (Find tab and tables), so no object is reachable only by pointing.
- Targets: at least 30 px on desktop (Blueprint default button height); on phone at least 44 x 44 px for primary actions, following SC 2.5.5 (AAA in 2.1) as a design target.
- Status never by colour alone (label and shape, section 6.3).
- `aria-live="polite"` on the inspector title so selection changes are announced; `prefers-reduced-motion` turns off map and panel animation.

## 12. Responsive rules

| Width | Layout |
|---|---|
| 1280 px and wider | full console (section 3.1) |
| 768 to 1279 px | left rail collapses to a 48 px icon strip that opens as an overlay; inspector opens as an overlay drawer over the map; timeline stays |
| under 768 px (phone) | single column with a bottom tab bar: **Queue** (default, first), Map, Search, Info. 16 px side gutter. Object pages open full screen with a back button. Tables become card lists. The timeline becomes a 48 px strip under the map with pass stepping. Banners wrap to two lines. No horizontal page scroll. |

## 13. Two forms from one frontend

| | Local app (`make serve`) | Single-file page |
|---|---|---|
| Start | `make serve`: FastAPI on 127.0.0.1, serves the built frontend and `/api/v1` | open the HTML file, or the private artifact link |
| Data | every source file in `app/CONTRACT.md`, read at start, re-read when a file changes | the embedded bundle (contract section 6), a subset within the size budget; the Info tab lists what was left out (for example older live passes, contract drop rule 7) |
| Chips | cached WebP, "Fetch chip" for any contact | embedded WebP up to a byte budget: 2.0 MB open (about 500 chips), 1.0 MB research (about 250), at 4,006 bytes per chip measured on real 64 px GRD windows (contract section 6.2) |
| Lead decisions and labels | appended to `data/labels/` files by the backend | held in page memory; "Export decisions" downloads a JSONL file to hand back (the reliable path); a copy goes to browser storage only when it works (rules below) |
| Exports | GeoPackage, GeoJSON, CSV, HTML | GeoJSON, CSV, HTML (no GeoPackage) |
| Live refresh | polls file changes every 60 s ("Latest") | static snapshot; the Info tab states its build time |

The frontend chooses its data adapter at start: an embedded bundle present means the single-file adapter; otherwise the HTTP adapter. Both adapters return the same object shapes (contract section 5).

**Browser storage in the single-file page.** Storage can be missing, empty or throw: MDN documents that `localStorage` throws a `SecurityError` when the origin is not a valid scheme, host and port tuple ("This can happen if the origin uses the file: or data: schemes") or when the user blocks persistent data, and the owner's artifact rules say storage can come back empty or throw in private windows, previews and with cleared or blocked site data. Rules:

1. Every read and write of `localStorage` (decisions, labels, theme, last tab, recent searches) is wrapped in try/catch behind one small module; a failure never breaks a view.
2. Decisions and labels live in page memory first. The JSONL export is the record of work; storage is a convenience copy.
3. On load the page tests storage once. When it fails, the Info tab and the lead card show "Decisions are not kept in this browser. Use Export decisions before closing the page." and the export button is highlighted after each decision.
4. No state that must be shared or kept for others is put in browser storage (owner question 4 covers a shared store).

## 14. Open and research builds

| | Open build | Research build |
|---|---|---|
| Purpose | the product, commercial-clean as far as the sources allow | the papers; noncommercial |
| AIS | aisstream live recording, matched on live passes (`data/live/`) | adds GFW AIS presence, GFW SAR matches and GFW vessel identity for the September run |
| Contacts | live passes (D1), September run (`not_checked`), Ca Mau scene | the September run with GFW identity (D1, `research_only` true) replaces the `not_checked` rows |
| Vessels | aisstream static and positions | adds GFW vessel records |
| Events | aisstream-derived events when built (round 2 onward) | adds GFW gaps, encounters, loitering, port visits |
| Reads `data/research/` | never; enforced by the backend catalog and by a test | yes |
| Label | `OPEN BUILD` | `RESEARCH BUILD` and "Research build, noncommercial, CC BY-NC 4.0" on every view |
| Licence of the output | each source's own licence (contract section 2) | CC BY-NC 4.0 for the whole build (the NC term carries through) |

Open question: aisstream.io publishes no terms of use (section 18).

## 15. Ocean and other context layers (P2)

The ocean layers (depth, SST and fronts, chlorophyll, currents, waves, shipping density) enter the product in three places only: the Context section of object pages (values at the object, with valid time), the Cell page, and optional map overlays (off by default). The expected-activity model and its anomalies (`docs/ocean_context_plan.md`) appear as the L6 lead type and an overlay once the model exists; until then they are absent, not empty. Every ocean view carries: "Ocean and weather layers describe the sea, not what any vessel does."

## 16. Playwright acceptance checks

Run with the preinstalled Playwright 1.56.1 and Chromium (`NODE_PATH=/opt/node-tools/node_modules PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`), never `playwright install`. Matrix: {local app, single-file page} x {open build, research build} x {desktop 1440 x 900, phone 390 x 844} x {dark, light}. Routes visited in each run: `#/leads`, `#/map`, one Contact of each AIS status present, one Vessel, one Light, one Lead, one Pass, `#/about`.

| Id | Check | Pass when |
|---|---|---|
| PW-01 | Console | no `console.error`, no `pageerror`, no failed request, on every route |
| PW-02 | Network (single-file) | no request leaves `file:`, `data:` or `blob:` |
| PW-03 | Caveat | both banners present and visible with `DARK_CAVEAT_SHORT` on every route; every object page shows it under the title |
| PW-04 | EEZ default | the EEZ layer toggle is off and no EEZ geometry is in the map DOM or canvas layer list after load; enabling it shows the Marine Regions statement |
| PW-05 | Research label present | research build: "Research build, noncommercial, CC BY-NC 4.0" and "Powered by Global Fishing Watch" visible on every route |
| PW-06 | Research label absent | open build: neither string anywhere in the DOM, and no GFW field name (`gfw_`) in the embedded bundle or any API response |
| PW-07 | Reflow | `document.documentElement.scrollWidth <= innerWidth` on every route at 390 px; side gutter 16 px |
| PW-08 | Phone order | at 390 px the first tab is Queue and is selected on load |
| PW-09 | Themes | dark on first load; toggle to light changes `data-theme` and `bp6-dark`; `body` has a non-transparent background in both |
| PW-10 | Contrast | computed styles of banner text, body text, muted text and status chips meet section 10 ratios (computed in the test from the rendered colours) |
| PW-11 | Keyboard | mod+K opens the Omnibar; MMSI, det_id, DD, DMS and MGRS queries each return the expected first result; Esc closes |
| PW-12 | Provenance | hovering or focusing any field in the Contact page Identification section shows a source chip with licence and access date |
| PW-13 | Status words | no rendered text contains "illegal", "suspicious", "violation" or "threat" except inside the caveat sentence |
| PW-14 | Size (single-file) | file at most 15.0 MB; per-type sizes within the contract budgets |
| PW-15 | Lead decision | local app: a decision writes one JSON line and survives a reload. Single-file, two runs: with storage working, the decision survives a reload and exports as JSONL; with storage blocked (an init script that makes `localStorage` throw), no page error, the decision still exports as JSONL, and the "Decisions are not kept in this browser" notice is visible |
| PW-16 | No vendor name | no rendered text on any route (except the third-party licence texts under About, Licences), no export file and no embedded data part contains the name of the toolkit's copyright holder, case-insensitive. The check reads that name at run time from the `author` field of `@blueprintjs/core/package.json`, so the name is never written into the product's own code |

## 17. Do not copy

From the interface brief section 14 and the owner's rules:

- No name, logo, product name or module name of the design-reference vendor anywhere in the product, its code comments, its docs or its exports. The research briefs keep them as references only. Generic terms are fine: object view, search around, inbox, timeline, histogram.
- About, Licences shows each third-party package's name, version and licence text unmodified (Apache-2.0 section 4(a): "You must give any other recipients of the Work or Derivative Works a copy of this License"). The toolkit's `LICENSE` files are the plain Apache-2.0 text with no copyright line filled in, and none of its five packages (core 6.20.0, icons, select, table, colors) ships a NOTICE file (checked in the trial install), so no rendered view needs to name the copyright holder; the package `author` field is not displayed.
- Compiled third-party code can still carry the holder's name in strings that are never rendered. Measured: each of the four trial single-file builds held exactly one, a repository URL inside a developer warning of the toolkit's overlay component. `build_single.py` counts such strings and prints the count; it does not edit third-party code, because a changed file would fall under Apache-2.0 section 4(b) ("You must cause any modified files to carry prominent notices stating that You changed the files"). Whether to strip it anyway, with that notice, is owner question 7. Check PW-16 covers rendered text, exports and data parts.
- No screenshots, figures, icon art or text from the vendor's documentation or from public procurement documents.
- No hosted base maps or geocoders that need an account key; no external tiles at all in the single-file page.
- No classification words (UNCLASSIFIED, OFFICIAL and the like): the banner is a handling banner.
- No risk scores, red "threat" styling or verdict language copied from commercial maritime products: review priority only, with its factors.

## 18. Open questions for the owner

1. **aisstream terms.** aisstream.io has no terms-of-use page (only a privacy policy). Until the operator confirms in writing that relayed AIS may be shown and shared, should the shared open-build page show aisstream identity fields (name, call sign, IMO) or only MMSI and match status?
2. **`not_checked` status.** D1 fixes `matched`, `unmatched` and `no_coverage`. The contract adds `not_checked` for runs with no AIS source in a build (September in the open build, Ca Mau). Confirm, or name another value.
3. **Default theme.** Dark always (this spec), or follow the system setting?
4. **Viewer decisions in the shared page.** Browser-only with JSONL export (this spec), or shared state through the artifact runtime so several reviewers see the same decisions?
5. **Reporting boxes.** Keep the six boxes of `darkvessel.ocean.grid.REPORTING_BOXES` (the same as `scripts/21_viirs_regions.py`) as the one set? The live AIS summary uses a different set (Pearl River mouth and Hong Kong and others).
6. **Priority calibration.** Which labels count as truth for the false-alarm rate per lead type: your contact labels only, or also lead decisions?
7. **Third-party string.** The compiled toolkit code carries the holder's name once, in a non-rendered warning URL (section 17). Leave it (the default in this spec), or strip it in `build_single.py` and add the Apache-2.0 section 4(b) modification notice?

## Sources (resolved in this session, 2026-10-08/09)

Repo files read: `docs/PROJECT_BOARD.md`, `docs/STATUS.md`, `docs/live_pass.md`, `docs/research/interface_design_brief.md`, `docs/research/mda_products_brief.md`, `src/darkvessel/config.py`, `src/darkvessel/live/schema.py`, `src/darkvessel/ais/gfw.py`, `src/darkvessel/ais/gfw_identity.py`, `scripts/21_viirs_regions.py` (via `src/darkvessel/ocean/model.py`, which mirrors it), `src/darkvessel/viz/demo_template.html`.

- Blueprint 6.20.0 and `@blueprintjs/colors` 5.1.16 package files from the npm registry: navbar 50 px and 0 16px padding, Drawer SMALL 360px, `bp6-dark`, palette hex values (stack decision section 7).
- https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits : noncommercial use under CC BY-NC 4.0; "Powered by Global Fishing Watch." linked to https://globalfishingwatch.org for websites and visuals; 50,000 requests per day.
- https://creativecommons.org/licenses/by-nc/4.0/ (Attribution-NonCommercial 4.0 International) and https://creativecommons.org/licenses/by/4.0/ .
- https://doi.org/10.14284/632 (resolves to the Marine Regions dataset record; licensed under Creative Commons).
- https://www.marineregions.org/disclaimer.php : the VLIZ disclaimer quoted in section 6.2.
- https://sentiwiki.copernicus.eu/web/s1-mission : "12 day repeat cycle", 693 km altitude.
- https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice : "free, full and open" access; credit "Contains modified Copernicus Sentinel data [Year]".
- https://aisstream.io/documentation : 3 subscribed connections per account; direct browser connections are not permitted. https://aisstream.io/privacypolicy (200); /terms, /tos, /terms-of-service (404).
- https://www.dni.gov/files/documents/ICD/ICD-203.pdf : the likelihood terms "almost no chance ... almost certain".
- https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html (4.5:1), https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html (3:1), https://www.w3.org/WAI/WCAG21/Understanding/reflow.html (320 CSS pixels), https://www.w3.org/WAI/WCAG21/Understanding/target-size.html (44 by 44, AAA), https://www.w3.org/WAI/WCAG21/Understanding/focus-visible.html .
- https://unpkg.com/mgrs@2.2.0/README.md : `forward` and `inverse`.
- https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage : `SecurityError` cases, including file: and data: origins and blocked persistent data.
- https://www.apache.org/licenses/LICENSE-2.0.txt : section 4(a) and 4(b) text quoted in section 17.
- https://www.imo.org/en/OurWork/Safety/Pages/AIS.aspx , https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/AIS/Resolution%20A.1106(29).pdf (paragraph 22) and https://support.marinetraffic.com/en/articles/9552924-why-can-t-i-see-a-vessel-on-the-live-map : the carriage, lawful switch-off and terrestrial coverage clauses of the caveat (quotes in contract section 1.1).
- `data/live/live_summary.json`, the `scenes_4326` layer of `data/live/live_contacts.gpkg` and `docs/live_pass.md` (read 2026-10-09 13:55 UTC): the first live pass as rerun at 07:37 UTC (5 scenes, 3,081 contacts, all `no_coverage`, 0 AIS positions inside or within 0.3 degree of the footprints, 8,812 to 9,074 AOI positions per scene window) and the recorder outage of 08:21 to 13:33 UTC.
- Trial builds in the scratchpad (stack decision section 4.1): one occurrence of the holder's name per build, in the overlay warning string; `@blueprintjs/core` 6.20.0 package: `LICENSE` is the Apache-2.0 text, no NOTICE file, `author` field set.
- https://leafletjs.com/reference.html : default CRS EPSG:3857, `preferCanvas`.
- https://globalfishingwatch.org/faqs/how-do-i-view-different-types-of-data-ais-vms-viirs/ : the VIIRS layer "is likely to show vessels associated with activities like squid fishing, which use bright lights and fish at night".
- https://raw.githubusercontent.com/allenai/vessel-detection-viirs/main/data.md : default filters for auroral lit clouds, moonlit clouds, image artifacts, near-shore detections, lightning and gas flares.
- MGRS example computed with `mgrs` 2.2.0: `forward([104.9, 8.6], 5)` = `48PVQ8899650631`.
