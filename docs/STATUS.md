# Status

Updated: 2026-10-02. Owner reviews; the assistant does the heavy lifting.

> **"Dark" does not mean illegal.** It only means no AIS position was matched to a radar detection. Every vessel product in this repo says so.

## Done

### Workstream 3: GIS baseline (details: `docs/gis_baseline.md`)
- Repo scaffold: `src/darkvessel` package, `scripts/`, `tests/`, `docs/`, `notebooks/`, `data/` (raw and heavy files gitignored), `.env.example`, `.gitignore` with secret patterns.
- `environment.yml` (conda-forge, includes `pytorch-cpu`) solved and installed in this container with micromamba 2.9.0: torch 2.13.0 CPU, torchvision 0.28.0, GDAL 3.12.3, rasterio 1.4.4.
- AOI written: `data/aoi.gpkg` (Ca Mau, both coasts, 69,946 km2; Gulf of Tonkin kept as an alternative layer).
- Scene search, last 90 days: 50 Sentinel-1D IW GRDH scenes on 27 dates, 30 ascending and 20 descending, all VV + VH, relative orbits 18, 26, 91, 99, median revisit 4 days. Sentinel-1C: zero. `data/s1_footprints.gpkg`, `data/s1_scenes.csv`.
- Baseline on one real scene (2026-09-29, 18:10 local) by windowed reads only: 779 vessel candidates (302 seen in both channels, 477 in one), 331 fixed structures (recur on two earlier dates), 4,895 low-confidence clutter objects, over about 17,700 km2 of open sea. `data/detections_baseline.gpkg`, `docs/figures/baseline_map.png`, `docs/figures/baseline_chips.png`, COG rasters in `data/outputs/`.
- AIS matching module with documented interface, synthetic data generator (including the SAR along-track shift of moving ships) and tests. No real AIS used.
- Tests: `pytest` passes (offline).

### Workstreams 1 and 2, ML stage, data landscape
In progress at the time of writing; see the sections below once filled.

## Blocked

| Blocker | Effect | Fix (owner action) |
|---|---|---|
| Network policy denies most hosts (api.openalex.org, planetarycomputer.microsoft.com, stac/catalogue.dataspace.copernicus.eu, scimagojr.com, elsevier.com, retractionwatch.com, docs.google.com, doi.org, globalfishingwatch.org, esa.int, huggingface.co, zenodo.org) | No live OpenAlex API, no STAC search, no SJR file, no Scopus discontinued list, no hijacked-journal check, no DOI resolution; web pages can be found by search but not opened, so many facts stay UNVERIFIED | Cloud environment menu > Edit > Network access: choose Full, or allow the hosts listed in the first assistant message |
| No keys yet | OpenAlex API (now key-only), Copernicus S3 reads, GFW API all unavailable | Add `OPENALEX_API_KEY`, `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY`, `GFW_API_TOKEN` as environment variables (never in chat or git); they load in a new session |
| No AIS source | Nothing can be labeled dark; recall by length cannot use real AIS for the Ca Mau scene | GFW token (noncommercial terms) for research use, or a commercial AIS feed for anything Viettel-facing |

Worked around: OpenAlex via its public S3 snapshot; Sentinel-1 via the AWS Open Data mirror; land mask via ESA WorldCover on S3; coastline via Natural Earth on GitHub.

## Defaults applied (you did not answer these; say the word to change any)
- AOI: Ca Mau waters (option a).
- Display: files in the repo, short chat summaries, plus one private demo web page.
- Commits: one per workstream, no Co-Authored-By or session trailers. Commit author is the container default; tell me a name and email if you want your own.
- Training data for the letter: AI2 Skylight labels (Apache-2.0) first; xView3 only if its license allows your use.

## Next 3 tasks (smallest first, each fits a 5 h week)
To be finalised when the running workstreams report back.

## Could not verify
To be consolidated from all reports.
