# Dark Vessel Study

A sovereign, sensor-agnostic pipeline that detects vessels in satellite SAR imagery, correlates them with AIS, and flags the ones that do not broadcast. Area of interest: the South China Sea, Gulf of Tonkin and Gulf of Thailand (3.58 million km2), with the waters off Ca Mau, Vietnam, as the scene-detail sub-area.

> **"Dark" does not mean illegal.** A dark detection only means no AIS position was matched to a radar return. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite AIS misses messages in busy coastal waters. Every vessel product in this repo carries this caveat; the product views carry the fuller product caveat (`app/CONTRACT.md` section 1.1).

## Why not just use Global Fishing Watch
Three reasons, each with its source and verification status in `docs/data_landscape.md`: GFW data carries a noncommercial license, its SAR detections lag acquisition by days, and Sentinel-1A, the satellite behind most published SAR vessel work, ended operations in June 2026 (the constellation is now Sentinel-1C and 1D). This project builds its own detector and tests how it transfers to the new satellites.

## Product
**SCS Vessel Watch** is the product built on this pipeline. Its core is dark vessel detection and identification: a leads triage queue, object pages (Contact, Vessel, Light, Event, Lead), a map, a timeline and search, with the source of every field shown. Two builds: **open** (commercial-clean, no Global Fishing Watch data; live AIS relayed by aisstream.io, terms UNVERIFIED) and **research** (adds Global Fishing Watch data, CC BY-NC 4.0, noncommercial, labelled as such). Run it locally with `make serve` (`BUILD=open` or `research`, `PORT=8750`) after `make app-build` (Node 22). The same frontend also builds a shareable single-file page. Spec: `docs/product_design.md`; data contract and the product caveat: `app/CONTRACT.md`; package choices: `docs/research/stack_decision.md`. Every view carries the product caveat of `app/CONTRACT.md` section 1.1: shore AIS receivers also have blind spots, and an AIS gap is not proof of intent.

## Status
See `docs/STATUS.md` for what is done, what is blocked, and the next tasks. Actions only the owner can take (network access, keys, labels, decisions) are in `docs/OWNER_ACTIONS.md`.

## Layout
```
src/darkvessel/      package: s1/ (search, read, calibrate), detect/ (CFAR, post-processing),
                     ais/ (matching), viirs/ (night lights), weather.py (GFS wind, Himawari-9 cloud tops),
                     s2.py (Sentinel-2 on AWS), satlas.py (offshore infrastructure points),
                     landmask.py, pipeline.py, viz/, io.py, config.py
scripts/             01 AOI, 02 scene search, 03 Ca Mau baseline, 04-06 ML verifier,
                     07 demo page, 08 coverage, 09 regional detection, 10 regional density,
                     11 clutter-rule check, 12 label scoring, 13 noise floor, 14 CNN on shared 1C/1D sea,
                     15 VIIRS night lights, 16 weather context, 17 look probability,
                     18 VIIRS and radar of one night, 19 Sentinel-2 optical check, 20 Satlas check,
                     21 VIIRS nightly rates by region, 22-23 ocean static and daily layers,
                     25 object context, 26 aisstream recorder, 27 GFW pull (research), 28 AIS reach
                     and pass plan, 29 watchdog, 30 live passes, 31 GFW identity (research),
                     32 CNN on the regional run, 33 leads, 34 expected activity
app/                 SCS Vessel Watch: backend/ (FastAPI), frontend/ (React, Blueprint), CONTRACT.md
tests/               offline unit tests (pytest)
data/                small derived outputs (GeoPackage, CSV) are committed; raw data is gitignored
docs/                reports, figures, status
notebooks/           exploration
```

## Run
The `Makefile` runs everything in dependency order: `make test`, `make regional`, `make context` (weather, VIIRS, optical and Satlas checks), `make camau`, `make demo OUT=page.html`, or `make all`. Each script checkpoints, so a rerun skips finished work. Further targets (the Makefile header describes each):

- live AIS and live passes: `make ais-watchdog`, `make ais-status`, `make ais-reach`, `make ais-passes`, `make live`, `make live-watch` (only the recorder that `make ais-watchdog` starts needs `AISSTREAM_API_KEY`; the others use the recording);
- CNN verification of the regional run: `make cnn-regional`, `make cnn-regional-build`, `make cnn-regional-status`;
- context and model: `make ocean`, `make object-context`, `make expected`;
- leads: `make leads` (open), `make leads-research` (research);
- research only, never in `make all`: `make gfw` (needs `GFW_API_TOKEN`; outputs under `data/research/`, CC BY-NC 4.0);
- product: `make serve`, `make app-build`.

Keys live only in the git-ignored `.env` at the repo root. The rest of the open pipeline needs none. Step by step:
```bash
conda env create -f environment.yml && conda activate darkvessel
python scripts/01_make_aoi.py                  # data/aoi.gpkg
python scripts/02_search_scenes.py             # data/s1_footprints.gpkg, last 90 days, Sentinel-1C/1D
python scripts/08_coverage.py                  # passes per cell: COGs, docs/figures/coverage.png
python scripts/09_run_regional.py --days 12    # regional detection, checkpointed per scene
python scripts/09_run_regional.py --merge      # data/detections_regional.gpkg + persistence check
python scripts/10_regional_density.py          # density COGs, docs/figures/regional_detections.png
python scripts/02_search_scenes.py --aoi ca_mau && python scripts/03_run_baseline.py   # Ca Mau detail
python scripts/07_build_demo_page.py --out demo.html   # self-contained demo page
python scripts/12_score_labels.py              # score labels exported from the demo page (data/labels/*.csv)
python scripts/13_nesz_compare.py              # noise floor of 1A, 1C and 1D from product annotation
python scripts/15_viirs_lights.py --start 2026-09-05 --end 2026-10-01   # VIIRS lights per granule (checkpointed)
python scripts/15_viirs_lights.py --merge      # data/viirs_lights.gpkg, density COGs, docs/figures/viirs_lights.png
python scripts/16_weather_context.py           # GFS wind and Himawari-9 cloud tops at every regional object
python scripts/17_look_probability.py          # chance of a Sentinel-1 look within 1, 7 and 30 days per cell
python scripts/18_viirs_radar_pair.py --pass <S1 product prefix> --night <date> --tag <name>   # one night, two sensors
python scripts/19_optical_check.py             # Sentinel-2 check of the radar classes (and --gallery for example chips)
python scripts/20_satlas_check.py              # fixed structures against Satlas platforms and turbines
python scripts/21_viirs_regions.py             # nightly lit-vessel rate by sub-region, with the wind of the night
pytest                                         # offline tests
# ML (scripts 04-06, 14) needs PyTorch: use the conda environment from environment.yml
```
Imagery is read with HTTP range requests from the AWS Open Data mirror of Sentinel-1 (`sentinel-s1-l1c`); no full-scene download and no account needed. Secrets (OpenAlex, Copernicus, GFW) go in `.env` (see `.env.example`); `.env` is gitignored.

## Outputs for ArcGIS Pro
Every vector product is a GeoPackage with two layers per dataset: `<name>_4326` (WGS 84) and `<name>_utm49n` (EPSG:32649, regional products) or `<name>_utm48n` (EPSG:32648, Ca Mau detail). Rasters are Cloud-Optimized GeoTIFFs in EPSG:4326 plus the same UTM zone. No maritime boundaries or claim lines are drawn.

| Product | What it holds |
|---|---|
| `data/detections_regional.gpkg` | Radar vessel candidates, 12-day regional run, with the scenes processed |
| `data/structures_regional.gpkg` | Fixed structures from the persistence test |
| `data/detections_baseline.gpkg`, `data/detections_ml.gpkg` | Ca Mau scene: all detections, and CNN scores |
| `data/viirs_lights.gpkg` | VIIRS night lights: recurring-light sites, lit vessel candidates of the darkest nights, granule outlines, per-night table |
| `data/optical_check.gpkg` | Sentinel-2 optical check of a random sample of fixed structures, vessel candidates and open-sea controls |
| `data/outputs/small/s1_passes_*.tif` | Sentinel-1 passes per cell in 90 days |
| `data/outputs/small/s1_look_prob_{1,7,30}d_*.tif` | Chance of a Sentinel-1 look within 1, 7 and 30 days, percent |
| `data/outputs/small/vessel_density_regional_*.tif` | Radar vessel candidates per 1,000 km2 per look |
| `data/outputs/small/viirs_lit_density_*.tif` | Clear-sky lit vessel candidates per 1,000 km2 of searched sea per satellite pass |
| `data/outputs/small/viirs_lit_density_clear_*.tif` | The same per 1,000 km2 of clear sea (cloud masks), the fair basis across nights and areas |

## Data credits
Contains modified Copernicus Sentinel data 2026 (Sentinel-1 GRD and Sentinel-2 L2A from the AWS Open Data mirrors). Land mask: ESA WorldCover 2021 v200 (CC BY 4.0). Training labels: AI2 Skylight vessel-detection-sentinels (Apache-2.0). Night lights: VIIRS Day/Night Band SDR, geolocation and JRR cloud mask from NOAA JPSS on the AWS Open Data Registry. Wind: NOAA GFS 0.25 degree. Cloud tops: Himawari-9 AHI (JMA, distributed by NOAA). Offshore platforms and turbines: Satlas marine infrastructure (AI2, ODC-BY). AOI and land: Natural Earth (public domain). Sources and licence checks: `docs/data_landscape.md` and `docs/data_additions.md`.
