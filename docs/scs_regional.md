# South China Sea: Sentinel-1 coverage and regional detection

> **"Dark" does not mean illegal.** A dark detection only means no AIS position was matched to it. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite AIS misses messages in busy coastal waters. No AIS is connected yet, so nothing in this product is labeled dark.

Run date: 2026-10-02. Scripts: `scripts/01_make_aoi.py`, `02_search_scenes.py`, `08_coverage.py`, `09_run_regional.py`.

## 1. Area of interest

The AOI is the union of three Natural Earth 10 m marine areas: "South China Sea", "Gulf of Tonkin" and "Gulf of Thailand" (public domain; `data/aoi.gpkg`). Area 3,583,486 km2 (EPSG:6933, equal area); extent 99.2 to 122.3 E, 3.2 S to 23.8 N. Natural Earth was chosen over a hand-drawn box because it is a citable, neutral definition; no claim lines or maritime boundaries are drawn in any product. Regional layers are delivered in EPSG:4326 and UTM 49N (EPSG:32649, the zone at the centre of the sea); Vietnamese coastal detail stays in UTM 48N.

## 2. Where Sentinel-1 looked, 4 July to 2 October 2026

Source: AWS Open Data mirror of Sentinel-1 GRD, all IW products of Sentinel-1C and 1D whose footprint touches the AOI (`data/s1_footprints.gpkg`, `data/s1_scenes.csv`). Slices of one acquisition are merged into one pass before counting (`src/darkvessel/coverage.py`), on a 0.05 degree grid with exact spherical cell areas.

| Item | Result |
|---|---|
| IW GRD products | 1,042 (Sentinel-1D 835, Sentinel-1C 207), all dual VV + VH |
| Passes (merged acquisitions) | 280 (1D 190, 1C 90); 139 ascending, 141 descending |
| AOI imaged at least once | 55 % |
| AOI imaged 3 / 6 / 10 or more times | 49 % / 42 % / 25 % |
| AOI never imaged | 1,605,966 km2 (45 %) |
| Where imaged: mean passes, typical revisit | 10.3 passes, about one every 8.8 days (maximum 38) |
| Share of the AOI imaged on an average day | 6.3 % (pass area summed over the window, divided by 90 days and by the AOI area) |

![Coverage](figures/coverage.png)

Rasters: `data/outputs/small/s1_passes_4326.tif` and `s1_passes_utm49n.tif` (COG, passes per cell; 65535 = outside AOI). Statistics: `data/s1_coverage.json`.

What the map shows: Sentinel-1 images a coastal ring (Vietnam, Gulf of Thailand, Malaysia, Borneo, Palawan, Luzon, south China) and leaves the central South China Sea, including the Spratly Islands area, unimaged in IW mode for the whole 90 days. A 12-day check (18 to 29 September) found no EW-mode and no stripmap products over the AOI either. The bucket holds GRD products only, so ocean wave-mode (WV) vignettes, which are not usable for ship detection, are not counted.

Why it matters:
- For the flagship paper, "how many vessels does SAR miss" has a spatial term before any detector term: nearly half of the sea is never looked at by free wide-swath SAR.
- For the product pitch, open Sentinel-1 cannot monitor the central sea at all. Covering it needs tasked SAR (commercial, or a national satellite). This is the strongest argument for a sensor-agnostic pipeline.
- Sentinel-1C does image parts of this AOI (90 passes), even though it took none over Ca Mau. 1C imagery for the transfer letter can come from inside the region.

Verification status: counts come from the AWS mirror and were not cross-checked against Copernicus Data Space (blocked from this environment). Whether the gap reflects the Sentinel-1 observation scenario is UNVERIFIED until the ESA scenario page can be opened.

## 3. Regional detection, 26 September to 1 October 2026

Scripts: `scripts/09_run_regional.py --days 6` (detection, checkpointed per scene), `--merge` (products and persistence), `scripts/10_regional_density.py` (density raster and map), `scripts/11_clutter_zone_check.py` (cost of the clutter rule).

**Input.** Every Sentinel-1C/1D IW product of the last 6 days of the search window whose footprint holds at least 300 km2 of the AOI: 64 of the 65 products that touch it (44 Sentinel-1D, 20 Sentinel-1C; 37 ascending, 27 descending). One 149 km2 sliver was skipped.

**Method.** The detector is the Ca Mau baseline (`docs/gis_baseline.md`), run over whole scenes:
- Each scene is read in place over HTTPS in 2048-pixel blocks. Only blocks holding testable sea are read: WorldCover water inside the AOI, beyond a 1 km shore buffer.
- CA-CFAR runs on calibrated VV and VH: false-alarm rate 1e-6, guard 81 px, background 161 px, one ENL per scene and channel. VV and VH objects within 30 m are fused.
- Classes:
  - high = both channels.
  - medium = VH only, or strong VV only.
  - low = weak VV only, or longer than 450 m.
- Persistence: each high or medium object is checked on up to two earlier passes of the same orbit, 1 to 30 days before (20 m overview, contrast of at least 7 dB within about 60 m). A return on every pass checked makes it "fixed".
- Clutter zone: a remaining high or medium object with 5 or more low objects within 1 km of the same scene is downgraded to low (low_reason `clutter_zone`).

Median processing time was 167 s per scene on a shared 4-core machine.

| Item | Result |
|---|---|
| Sea tested (sum over scenes) | 1,190,147 km2 (Sentinel-1D 904,668; Sentinel-1C 285,479) |
| CFAR objects | 518,745 |
| Vessel candidates | **43,944**: 16,807 in both channels (high), 27,137 in one channel (medium) |
| Fixed structures | 13,560 |
| Low | 461,241: 426,505 weak VV only, 32,040 clutter zone, 2,696 longer than 450 m |
| Candidates per 1,000 km2 tested | 36.9 (Sentinel-1D 42.1, Sentinel-1C 20.7) |
| Estimated length of candidates | median 40 m; 36 % at 25 m or less, 23 % 25 to 50 m, 23 % 50 to 100 m, 18 % over 100 m |
| Persistence | 98.6 % of high, medium and fixed objects checked on 2 earlier passes, 1.3 % on none |

![Regional detections](figures/regional_detections.png)

**Density.**
- Candidates per 1,000 km2 of sea per look, on a 0.25 degree grid (`data/outputs/small/vessel_density_regional_4326.tif` and `_utm49n.tif`, `data/regional_density.json`).
- Median over the 1,307 observed cells: 21. 90th percentile: 103. 99th percentile: 348.
- The densest cells are near ports and fishing harbours:
  - the northern Gulf of Tonkin (21.1 to 21.4 N, 108.1 to 109.6 E; 430 to 535)
  - the Pearl River mouth (22.4 to 22.6 N, 113.6 to 113.9 E; about 420)
  - the Shanwei and Shantou coast (22.6 to 23.4 N, 116.4 to 117.1 E; 445 to 495)
- One look is a 25-second snapshot, so a boat seen on two passes counts twice, against two looks of area.

**Hot spots that were not vessels.** Quicklooks of the densest cells before the clutter rule showed three false sources:
- Convective rain cells off Brunei (6.4 N, 114.5 E; one scene, 3,307 candidates in a 0.85 x 0.6 degree box).
- Rain cells in the central Gulf of Thailand (10.5 N, 101.8 E).
- Aquaculture rafts and stake lines in Zhanjiang Bay (20.9 N, 110.4 E).

In each case the detector fired thousands of times on bright, textured patches in both channels. The clutter-zone rule targets this pattern. Measured on the AI2-labelled Sentinel-1A/1B candidates of the ML stage (`data/clutter_zone_check.json`), the 1 km and 5 rule:
- flags 16 % of candidates;
- removes 26 % of clutter candidates;
- loses 2.3 % of candidates within 50 m of a labelled vessel;
- raises the share of both-channel candidates near a labelled vessel from 51 % to 54 %, and of one-channel candidates from 14 % to 17 %.

On this regional run it flagged 32,040 of 75,984 remaining candidates (42 %), because late-September storms covered large parts of the scenes. The rule can also flag a very dense fleet of small boats with weak returns, and the AI2 labels contain few such fleets. Flagged objects are therefore kept in the full product (`data/detections_regional_all.gpkg`, low_reason `clutter_zone`). Run `09_run_regional.py --merge --no-clutter-zone` to keep them as candidates.

**How good are the candidates?** There is no Sentinel-1C/1D ground truth yet. The same detector on 321 Sentinel-1A/1B scenes in Southeast Asia with AI2 expert labels (`data/ml/candidates.parquet`) gives these shares within 50 m of a labelled vessel:
- 51 % of both-channel candidates (65 % within 150 m);
- 14 % of one-channel candidates (25 % within 150 m).

The labels miss some real ships, so these are lower bounds on precision. Read one-channel candidates as leads. 56 % of all candidates are seen in VH only. The median length of one-channel candidates is 24 m, which is 2 to 3 pixels, close to the speckle correlation length.

**First look for the transfer letter.** Across the whole run, Sentinel-1C gives half the candidate density of 1D (20.7 against 42.1 per 1,000 km2) and fewer both-channel candidates (27 % against 40 %). On the 93 cells of 0.25 degree that both satellites imaged in the window, the two agree:

| | Sentinel-1C | Sentinel-1D |
|---|---|---|
| Area | Gulf of Thailand and the central Vietnamese coast, 101.75 to 110 E, 10.25 to 18.75 N | same |
| Candidates per 1,000 km2 per look | 33.4 | 31.6 |
| Both-channel share | 33 % | 31 % |

The median per-cell ratio is 0.96 (interquartile range 0.53 to 1.62). The mission-wide gap is geography: 1D imaged the busier northern Gulf of Tonkin and south China coast. This is a consistency check, not a transfer result: the dates and traffic differ, and nothing is scored against truth.

**Products.**
- `data/detections_regional.gpkg`:
  - `detections_regional_4326` and `_utm49n`: vessel candidates and fixed structures, 57,504 rows. Columns:
    - det_id, scene_idx, mission, acq_utc, confidence, lat, lon
    - length_est_m, scr_vv_db, scr_vh_db, inc_angle_deg
    - persist_dates, persist_dates_checked
    - ais_status = not_checked
    - caveat
  - `scenes_processed_4326` and `_utm49n`: footprint, product id, sea tested and class counts per scene.
  - `about`: method, settings, full caveat and data credit.
  - The point layers carry no R-tree index, to keep the file small; in ArcGIS Pro, run Add Spatial Index if panning is slow.
- `data/detections_regional_all.gpkg`: every object, all columns (490 MB, gitignored). Regenerate it with `--merge` from the per-scene cache, or from scratch with `--days 6` (about 3 hours on 4 cores).
- `data/regional_summary.json`, `data/regional_density.json`.

**Fix made during this run.** WorldCover codes nearshore sea inside its land tiles as permanent water (80). The first version of the sea mask kept water only when it connected to a code-0 (open ocean) cell in the scene frame, so 6 coastal scenes lost all their sea and 3 lost part of it:
- the Gulf of Thailand off Kien Giang, Cambodia and eastern Thailand;
- the Mekong mouths;
- the south-central Vietnamese coast;
- the northern Gulf of Tonkin.

The mask now also counts water connected to cells 0.05 degree inside the marine-area AOI (`darkvessel.landmask.sea_mask_on_grid`, test `tests/test_landmask.py`). The 9 scenes were reprocessed, adding 22,400 km2 of tested sea. The Ca Mau detail scene was not affected (same mask area under both rules).

**Limits.**
- No AIS is connected, so nothing is labeled dark.
- Six days of a 90-day window, a coastal ring only (section 2).
- Single looks, not tracks.
- Lengths are pixel extents.
- The 1 km shore buffer leaves out harbours and river mouths.
- A boat moored at the same spot on every pass checked becomes "fixed".
