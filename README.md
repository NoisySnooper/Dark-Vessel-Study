# Dark Vessel Study

A sovereign, sensor-agnostic pipeline that detects vessels in satellite SAR imagery, correlates them with AIS, and flags the ones that do not broadcast. First area of interest: the waters off Ca Mau, Vietnam.

> **"Dark" does not mean illegal.** A dark detection only means no AIS position was matched to a radar return. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite AIS misses messages in busy coastal waters. Every vessel product in this repo carries this caveat.

## Why not just use Global Fishing Watch
Three reasons, each with its source and verification status in `docs/data_landscape.md`: GFW data carries a noncommercial license, its SAR detections lag acquisition by days, and Sentinel-1A, the satellite behind most published SAR vessel work, ended operations in June 2026 (the constellation is now Sentinel-1C and 1D). This project builds its own detector and tests how it transfers to the new satellites.

## Status
See `docs/STATUS.md` for what is done, what is blocked, and the next tasks.

## Layout
```
src/darkvessel/      package: s1/ (search, read, calibrate), detect/ (CFAR, post-processing),
                     ais/ (matching), landmask.py, pipeline.py, viz/, io.py, config.py
scripts/             01_make_aoi.py, 02_search_scenes.py, 03_run_baseline.py, ...
tests/               offline unit tests (pytest)
data/                small derived outputs (GeoPackage, CSV) are committed; raw data is gitignored
docs/                reports, figures, status
notebooks/           exploration
```

## Run
```bash
conda env create -f environment.yml && conda activate darkvessel
python scripts/01_make_aoi.py            # data/aoi.gpkg
python scripts/02_search_scenes.py       # data/s1_footprints.gpkg, last 90 days, Sentinel-1C/1D
python scripts/03_run_baseline.py        # data/detections_baseline.gpkg, docs/figures/baseline_*.png
pytest                                   # offline tests
```
Imagery is read with HTTP range requests from the AWS Open Data mirror of Sentinel-1 (`sentinel-s1-l1c`); no full-scene download and no account needed. Secrets (OpenAlex, Copernicus, GFW) go in `.env` (see `.env.example`); `.env` is gitignored.

## Outputs for ArcGIS Pro
Every vector product is a GeoPackage with two layers per dataset: `<name>_4326` (WGS 84) and `<name>_utm48n` (EPSG:32648). Rasters are Cloud-Optimized GeoTIFFs in UTM 48N.

## Data credits
Contains modified Copernicus Sentinel data 2026. Land mask: ESA WorldCover 2021 v200 (CC BY 4.0). Training labels: AI2 Skylight vessel-detection-sentinels (Apache-2.0).
