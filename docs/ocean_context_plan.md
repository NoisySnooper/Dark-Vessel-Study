# Ocean context and expected-activity model: build plan

Date: 2026-10-03 (UTC). Status: approved, waiting on access. The build starts when network access and keys are in place (`docs/OWNER_ACTIONS.md`, actions 1 to 3).

> **"Dark" does not mean illegal.** An expected-activity map shows where boats usually are, given the sea and the weather. A place with more or fewer boats than expected is a lead for review, not evidence of anything. Every output keeps the caveat.

## Decisions (owner, 2026-10-03)

| Question | Decision |
|---|---|
| Scope | Build everything at once after full access: open layers, chlorophyll, shipping density and fishing effort, plus the expected-activity model. Nothing partial before then. |
| Boundaries | EEZ lines as an optional layer, off by default, labelled as the source publishes them. All other maps stay without boundaries. |
| Licences | Commercial-clean sources for the product. Global Fishing Watch fishing effort (noncommercial) for the papers only, kept out of the product and flagged on every output. |
| Demo page | A few key layers: depth contours, SST with fronts, expected activity and anomalies, and the EEZ toggle. Everything else goes to the GIS files. |

## 1. Layers

Reachability was tested from this environment on 2026-10-03: "open" means anonymous HTTPS reads worked; "blocked" means the proxy refused the host (HTTP 403 on CONNECT).

| Layer | Why it matters for boats | Source | Licence | Today |
|---|---|---|---|---|
| Depth | Gear type, where small boats can work, shelf break | NOAA ETOPO1 (1 arc-minute) inside the AWS Terrain Tiles bucket `elevation-tiles-prod`; the tile documentation says ETOPO1 supplies ocean depths at all zooms | Attribution required per the tile documentation; the attribution file itself was not read (UNVERIFIED) | Open |
| Distance to coast | Range of small boats | Natural Earth 10 m land, already in use | Public domain | Open |
| Major ports | Merchant traffic, port approaches | Natural Earth ports: 37 inside the AOI box, major ports only | Public domain | Open |
| Sea surface temperature, 0.25 degree | Fish follow temperature | NOAA OISST v2.1, daily, gap-free (bucket `noaa-cdr-sea-surface-temp-optimum-interpolation-pds`; preliminary files to 24 September 2026 seen on 2 October) | NOAA: "can be used as desired", attribution requested (registry text) | Open |
| Sea surface temperature, 1 km | Fronts need the finer grid | MUR, daily, gap-free, through NASA PO.DAAC (Earthdata login). The AWS Zarr copy (`mur-sst/zarr-v1`) stops in early 2020: its time axis holds 6,443 days from 2002-06-01 | "No restrictions" (AWS registry text for MUR) | Blocked for 2026 (needs `EARTHDATA_TOKEN` and the PO.DAAC hosts) |
| SST fronts | Fish gather on fronts | Computed: gradient magnitude and front frequency per cell, from MUR when available, else from OISST (coarse: large fronts only) | Derived | Open at 0.25 degree |
| Sea level, eddies, currents, mixed layer | Eddy edges and drift | NOAA RTOFS daily diagnostics (1/12 degree; bucket `noaa-nws-rtofs-pds`): sea surface height, depth-averaged u and v, mixed-layer and boundary-layer thickness (variables read on 2026-10-03; files to 2 October 2026) | NOAA, as above | Open |
| Wind and waves | Whether small boats go out (the Gulf of Tonkin drop, `docs/viirs_lights.md`) | NOAA GFS 0.25 degree (in use) and GFS-Wave | NOAA, as above | Open |
| Chlorophyll-a | Productive water | NASA ocean colour Level 3 (VIIRS, MODIS, PACE) through Earthdata, or Copernicus Marine ocean colour | NASA open with citation; Copernicus Marine free with registration (both UNVERIFIED for these products) | Blocked (oceancolor.gsfc.nasa.gov, data.marine.copernicus.eu). The AWS Sentinel-3 mirror carries OLCI water products only to 2024 (latest OL_2_WRR folder 2024-03-08), usable for a September climatology at most |
| Shipping density | Separates passing traffic from fishing | World Bank and IMF Global Shipping Traffic Density: hourly AIS positions January 2015 to February 2021 on a 0.005 degree grid, six layers (commercial, fishing, oil and gas, passenger, leisure, all) (search snippet) | Listed as "Public"; the exact licence is UNVERIFIED | Blocked (datacatalog.worldbank.org, datacatalogfiles.worldbank.org) |
| Fishing effort, AIS | Papers only: who carries AIS, compared with lights and radar | Global Fishing Watch 4Wings API | CC BY-NC 4.0 (`docs/data_landscape.md`) | Blocked (needs token and host) |
| EEZ lines | Optional layer, off by default | Marine Regions EEZ (VLIZ) | CC BY 4.0 (UNVERIFIED) | Blocked (marineregions.org) |
| Fishing routes | | No open dataset exists. Lit fishing grounds come from the project's own VIIRS lights; AIS fishing tracks only from Global Fishing Watch (papers only) | | |

Optional upgrades if the hosts open: GEBCO 2024 bathymetry (15 arc-seconds) instead of ETOPO1; Copernicus Marine physics reanalysis for currents at the surface rather than depth-averaged.

## 2. Expected-activity model

**Question.** Given the sea and the weather, how many boats should be in a cell on a given night or radar pass, and where do the observations depart from that?

- **Units.** 0.25 degree cell by night for VIIRS (27 nights so far), and cell by radar pass for Sentinel-1 (119 scenes).
- **Targets.**
  - Clear-sky lit vessel candidates, with the clear sea seen as exposure (`scripts/15_viirs_lights.py --clear`).
  - Radar vessel candidates after the clutter rules, with the sea tested as exposure.
- **Features.**
  - Depth (mean, share shallower than 200 m), distance to coast and to a major port.
  - SST, SST gradient and front frequency, chlorophyll and its gradient.
  - Sea-level anomaly and current speed, mixed-layer depth.
  - 10 m wind, significant wave height.
  - Moon illumination, and for radar the time of day and incidence angle.
  - Region (the six reporting boxes of `scripts/21_viirs_regions.py`).
- **Model.** Gradient-boosted trees with a Poisson loss and the exposure as weight. scikit-learn's histogram gradient boosting is already in the conda environment (1.9.1), so no new heavy dependency. Baselines are a cell climatology and a weather-free model, so the gain from each layer group is measured, not assumed.
- **Validation.** Leave-one-week-out and leave-one-region-out cross-validation: the model must predict nights and seas it has not seen. Report Poisson deviance against the baselines, calibration, and permutation importance per region.
- **Outputs.**
  1. Expected activity for every night, including the central sea that Sentinel-1 never images.
  2. Anomaly maps: observed against expected as a Poisson z-score, both high (a fleet moved in) and low (weather, or activity the sensors miss).
  3. Driver panels per region, so effects such as the Gulf of Tonkin wind response become measured results.
  4. Context per radar contact and VIIRS light: depth, SST, distance to the nearest front, inside a shipping lane or not.
- **Papers.**
  - Paper 2 gets an expected-activity term for the miss budget (`docs/paper2_design.md`).
  - The Global Fishing Watch comparison (AIS fishing effort against lights and radar per cell) stays in research outputs only.

## 3. Products

- COG rasters, EPSG:4326 and UTM 49N, in `data/outputs/small/`:
  - depth and distance to coast;
  - SST mean and front frequency for the window;
  - chlorophyll mean;
  - shipping density (all and fishing);
  - mean expected activity and mean anomaly.
- GeoPackage `data/ocean_context.gpkg`: depth contours (50, 200 and 1,000 m), front lines of the showcase night, major ports, and the EEZ layer as its own file with its source labels.
- Per-object context tables: the environment at every radar candidate and VIIRS light, next to `data/weather_context.parquet`.
- Demo page: depth contours, SST with fronts for the showcase night, expected activity and anomalies, and the EEZ toggle off by default. The footer will say where the EEZ lines come from and that the project takes no position on them.
- Report: `docs/ocean_context.md` with method, results and limits; a short section in the paper 2 design.

## 4. Build order once access is in place

| Step | Work | Estimate |
|---|---|---|
| 1 | Access check of every host and key; add xarray to `environment.yml` for the NetCDF layers | 15 min |
| 2 | Static layers: depth, coast distance, ports, EEZ, shipping density | 1 to 2 h |
| 3 | Daily layers for 5 September to 1 October: SST, fronts, RTOFS, waves, chlorophyll | 2 to 3 h, mostly compute |
| 4 | Cell-night table, model, cross-validation, anomaly maps | 2 to 3 h |
| 5 | Demo layers, report, STATUS | 1 to 2 h |
| 6 | Research only: Global Fishing Watch comparison | 1 h |

## 5. What it needs from the owner

Detailed steps are in `docs/OWNER_ACTIONS.md`. In short:
- **Network access.** "Full" is simplest. Otherwise add the hosts listed there under "Ocean context".
- **Keys.** `EARTHDATA_TOKEN` (chlorophyll and 1 km SST), `CMEMS_USERNAME` and `CMEMS_PASSWORD` (Copernicus Marine, the alternative source for both), and `GFW_API_TOKEN` (fishing effort, papers only).

## 6. Limits known now

- **Expected is not true.** The model learns where lit boats and radar candidates were in 27 nights and 12 days of September. It will miss seasonal shifts until a season of data is in, and it inherits the sensors' blind spots: unlit boats for VIIRS, small boats for radar.
- **Dates of the layers differ.** Shipping density is a 2015 to 2021 AIS average, chlorophyll is cloud-gapped, and SST and currents are daily. Each feature carries its date in the products.
- **EEZ lines are the source's lines.** In this sea many overlap or are disputed. The layer shows them as published, labelled with their source, and the project takes no position.
