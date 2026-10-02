# Status

Updated: 2026-10-02. Owner reviews; the assistant does the heavy lifting.

> **"Dark" does not mean illegal.** It only means no AIS position was matched to a radar detection. Every vessel product in this repo says so.

## Scope change (2026-10-02)
AOI moved from Ca Mau to the whole South China Sea (owner request: a bigger area makes a better product). AOI = Natural Earth marine areas "South China Sea", "Gulf of Tonkin" and "Gulf of Thailand", 3.58 million km2. Ca Mau stays as the scene-detail sub-area.

## Done

### Workstream 3: GIS (details: `docs/scs_regional.md`, `docs/gis_baseline.md`)
- Repo scaffold, `environment.yml` verified by a real install (micromamba, conda-forge: torch 2.13 CPU, GDAL 3.12, rasterio 1.4), `.env.example`, secret-safe `.gitignore`, walkthrough notebook.
- South China Sea, last 90 days: 1,042 Sentinel-1C/1D IW products in 280 passes (1D 190, 1C 90). 55 % of the AOI imaged at least once; **1.61 million km2 (45 %), the whole central sea including the Spratly area, never imaged**; typical revisit about 9 days where imaged. Coverage rasters (COG, EPSG:4326 and UTM 49N) and map.
- Ca Mau detail run on one scene (2026-09-29, 18:10 local): 720 vessel candidates (285 in both channels), 349 fixed structures, 4,936 low-confidence objects, over about 17,700 km2 of open sea.
- Regional detector that streams whole scenes block by block over sea only, plus a per-candidate persistence check. Regional run over the most recent 6 days: see `docs/scs_regional.md` section 3.
- Demo web page (private artifact): regional map and Ca Mau radar view, inspector with radar chips, in-browser labeling with CSV export.
- AIS matching module (interface, synthetic generator, tests). No real AIS used.
- Tests: offline `pytest` suite passes.

### Workstreams 1 and 2, ML stage, data landscape
Running in background agents; this section is filled when they report.

## Blocked

| Blocker | Effect | Fix (owner action) |
|---|---|---|
| Network policy denies most hosts (api.openalex.org, planetarycomputer.microsoft.com, Copernicus Data Space, scimagojr.com, elsevier.com, retractionwatch.com, docs.google.com, doi.org, globalfishingwatch.org, esa.int, huggingface.co, zenodo.org) | No live OpenAlex API, no STAC search, no SJR file, no Scopus discontinued list, no hijacked-journal check, no DOI resolution; many facts stay UNVERIFIED | Cloud environment menu > Edit > Network access: Full, or allow the listed hosts |
| No keys | OpenAlex API (key-only since 2026), Copernicus S3, GFW API unavailable | Add `OPENALEX_API_KEY`, `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY`, `GFW_API_TOKEN` as environment variables (never in chat or git) |
| No AIS source | Nothing can be labeled dark; no AIS-based recall by length on 1C/1D scenes | GFW token (noncommercial) for research, or a commercial feed for anything Viettel-facing |
| Compute | Only the most recent 6 days were processed regionally on this shared 4-core machine | Run `scripts/09_run_regional.py --days 90` on a bigger machine (a scene takes minutes; the code is checkpointed) |

Worked around: OpenAlex via its public S3 snapshot; Sentinel-1 via the AWS Open Data mirror; land mask via ESA WorldCover on S3; AOI and coastline via Natural Earth on GitHub.

## Defaults applied (say the word to change any)
- Map naming: "South China Sea" as written in your request. For a Vietnamese audience you may prefer "East Sea (Bien Dong)"; it is a one-line change.
- No maritime boundaries or claim lines are drawn anywhere.
- Display: files in the repo, short chat summaries, plus one private demo page.
- Commits: one per workstream (plus labelled work-in-progress commits), no Co-Authored-By or session trailers. Commit author is the container default; give a name and email to switch.
- Training data for the letter: AI2 Skylight labels (Apache-2.0) first; xView3 only if its license allows your use.

## Next 3 tasks (smallest first, each fits a 5 h week)
To be finalised when the background workstreams report.

## Could not verify
To be consolidated from all reports.
