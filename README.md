# Dark Vessel Study

A sovereign, sensor-agnostic pipeline that detects vessels in satellite SAR imagery, correlates them with AIS, and flags the ones that do not broadcast. Area of interest: the South China Sea, Gulf of Tonkin and Gulf of Thailand (3.58 million km2), with the waters off Ca Mau, Vietnam, as the scene-detail sub-area.

> **"Dark" does not mean illegal.** A dark detection only means no AIS position was matched to a radar return. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite AIS misses messages in busy coastal waters. Every vessel product in this repo carries this caveat.

## Why not just use Global Fishing Watch
Three reasons, each with its source and verification status in `docs/data_landscape.md`: GFW data carries a noncommercial license, its SAR detections lag acquisition by days, and Sentinel-1A, the satellite behind most published SAR vessel work, ended operations in June 2026 (the constellation is now Sentinel-1C and 1D). This project builds its own detector and tests how it transfers to the new satellites.

## Status
See `docs/STATUS.md` for what is done, what is blocked, and the next tasks. Actions only the owner can take (network access, keys, labels, decisions) are in `docs/OWNER_ACTIONS.md`.

## Layout
```
src/darkvessel/      package: s1/ (search, read, calibrate), detect/ (CFAR, post-processing),
                     ais/ (matching), viirs/ (night lights), weather.py (GFS wind, Himawari-9 cloud tops),
                     landmask.py, pipeline.py, viz/, io.py, config.py
scripts/             01 AOI, 02 scene search, 03 Ca Mau baseline, 04-06 ML verifier,
                     07 demo page, 08 coverage, 09 regional detection, 10 regional density,
                     11 clutter-rule check, 12 label scoring, 13 noise floor, 14 CNN on shared 1C/1D sea,
                     15 VIIRS night lights, 16 weather context, 17 look probability
tests/               offline unit tests (pytest)
data/                small derived outputs (GeoPackage, CSV) are committed; raw data is gitignored
docs/                reports, figures, status
notebooks/           exploration
```

## Run
The `Makefile` runs everything in dependency order: `make test`, `make regional`, `make context` (weather and VIIRS), `make camau`, `make demo OUT=page.html`, or `make all`. Each script checkpoints, so a rerun skips finished work. Step by step:
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
| `data/outputs/small/s1_passes_*.tif` | Sentinel-1 passes per cell in 90 days |
| `data/outputs/small/s1_look_prob_{1,7,30}d_*.tif` | Chance of a Sentinel-1 look within 1, 7 and 30 days, percent |
| `data/outputs/small/vessel_density_regional_*.tif` | Radar vessel candidates per 1,000 km2 per look |
| `data/outputs/small/viirs_lit_density_*.tif` | Clear-sky lit vessel candidates per 1,000 km2 per satellite pass |

## Data credits
Contains modified Copernicus Sentinel data 2026. Land mask: ESA WorldCover 2021 v200 (CC BY 4.0). Training labels: AI2 Skylight vessel-detection-sentinels (Apache-2.0). Night lights: VIIRS Day/Night Band SDR, geolocation and JRR cloud mask from NOAA JPSS on the AWS Open Data Registry. Wind: NOAA GFS 0.25 degree. Cloud tops: Himawari-9 AHI (JMA, distributed by NOAA). Offshore platforms and turbines: Satlas marine infrastructure (AI2, ODC-BY). AOI and land: Natural Earth (public domain). Sources and licence checks: `docs/data_landscape.md` and `docs/data_additions.md`.
