# Ocean context layers: static and daily

Date: 2026-10-09 (UTC). Code: `src/darkvessel/ocean/grid.py`, `static.py`, `daily.py`, `fronts.py`; `scripts/22_static_layers.py`, `scripts/23_daily_ocean.py`. Products: `data/outputs/small/*_{4326,utm49n}.tif` (listed below), `data/ocean_context.gpkg`, `data/eez_marineregions.gpkg`, `data/ocean_fronts.gpkg`, `data/ocean_static_cells.parquet`, `data/ocean_daily_cells.parquet`, `data/ocean_radar_pass_cells.parquet`, `data/ocean_static_summary.json`, `data/ocean_daily_summary.json`, `docs/figures/ocean_static.png`, `docs/figures/ocean_showcase_fronts.png`. Plan: `docs/ocean_context_plan.md`.

> **"Dark" does not mean illegal.** "Dark" means only that no AIS position was matched to a radar contact. Many vessels are not required to carry AIS, AIS can be off for lawful reasons, and satellite and terrestrial AIS have blind spots. An AIS gap is not proof of intent. The ocean layers describe the sea, not what any vessel does: a contact in deep water, near a front or far from port is context for an analyst, never evidence of anything.

## 1. Purpose and status

These layers say what the sea was like where and when a radar contact, a VIIRS light or a dark lead was seen: depth, distance to coast and to a major port, whether AIS traffic was ever recorded there (2015 to 2021), sea surface temperature and fronts, chlorophyll, currents, sea level, mixed layer, waves and wind. In the product they are context on the Contact and Lead pages and optional map layers (priority P2, after detection and identification). In paper 2 they are the covariates of the expected-activity model.

Status on 2026-10-09: all static and daily layers are built, checked and documented here. The per-object context (`src/darkvessel/ocean/context.py`, `scripts/25_object_context.py`) and the expected-activity model with anomalies (`src/darkvessel/ocean/model.py`) are not part of this build; they come in a later round and read the files described here.

## 2. Grids, CRS and file conventions

| Grid | Resolution | Origin (north-west corner) | Shape (rows x columns) | Used for |
|---|---|---|---|---|
| Fine | 0.01 degree (about 1.1 km) | 99.16 E, 23.76 N | 2,698 x 2,311 | depth, distances, shipping density, SST, fronts |
| 0.05 degree | 0.05 degree (about 5.6 km) | 99.15 E, 23.80 N | 541 x 463 | chlorophyll, RTOFS currents, mixed layer, SSH gradient |
| Model | 0.25 degree (about 28 km) | 99.0 E, 24.0 N | 109 x 94 | cell tables, waves, wind; the cell-night grid of `scripts/15_viirs_lights.py` |

All three come from `darkvessel.coverage.grid_for` over the bounds of the AOI polygon (Natural Earth 10 m marine areas South China Sea, Gulf of Tonkin and Gulf of Thailand; bounds 99.16 E to 122.27 E, 3.22 S to 23.76 N). Model-cell edges are whole multiples of 0.01 degree, so every fine cell lies in exactly one model cell (625 fine cells per full model cell). Rasters cover the whole bounding box (sea only); tables and key numbers use the sea inside the AOI polygon.

Every raster is a COG (deflate, internal overviews), written twice by `darkvessel.ocean.grid.write_dual_cog`: EPSG:4326 on its native grid (`*_4326.tif`) and UTM 49N, EPSG:32649 (`*_utm49n.tif`, bilinear, cell size 1,113 m, 5,566 m or 27,830 m). Nodata is -9999 (land, no data or outside the AOI). Every COG carries tags for units, source, licence, licence URL, version, access date, method, the ocean caveat and the dark caveat, so the files explain themselves in ArcGIS Pro. Every GeoPackage holds each layer twice (`_4326` and `_utm49n`, `darkvessel.io.write_dual_crs`) plus an `about` table with source, version, licence, access date, method, the ocean caveat and the full dark caveat (`dark_caveat`). All files are under 20 MB (largest: `ship_density_all_utm49n.tif`, 16.6 MB; `eez_marineregions.gpkg`, 15.0 MB).

## 3. Static layers (`scripts/22_static_layers.py`)

Downloads on 2026-10-08; rebuilt from the caches on 2026-10-09. No keys are needed.

### 3.1 Depth and depth contours

| | |
|---|---|
| Source | GEBCO_2026 Grid, netCDF held at CEDA/BODC: https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.nc (resolved, HTTP 206 on a range request) |
| Version | GEBCO_2026, file attribute `date_created` 2026-04-17, doi:10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa (https://doi.org/10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa resolves to the BODC catalogue page) |
| Licence as published | GEBCO terms of use (https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use, resolved): "The GEBCO Grid is placed in the public domain and may be used free of charge." Users may copy, adapt and commercially exploit it and must acknowledge the source. "The GEBCO Grid should NOT be used for navigation or for any other purpose involving safety at sea." |
| Resolution | 15 arc-second source (about 460 m), averaged to the 0.01 degree fine grid |
| Method | The file is one contiguous int16 array, so the AOI rows (bounds plus 0.5 degree) are fetched as one byte range and cached as a compressed array. Depth of a fine cell = mean of the source cells below sea level whose centres fall in it, metres positive down. Sea mask = fine cell centre not on Natural Earth 10 m land and GEBCO mean below sea level. Slope = magnitude of the depth gradient in m per km (dx scaled by the cosine of latitude). Contours at 50, 200 and 1,000 m after a gaussian smoothing of one cell, simplified at 0.003 degree, lines shorter than 0.1 degree dropped, lengths on the WGS 84 ellipsoid. |
| Files | `depth_m_{4326,utm49n}.tif` (11.1 and 14.4 MB); `ocean_context.gpkg` layers `depth_contours_4326`, `depth_contours_utm49n` |
| Result | AOI sea 3.56 million km2; 52.1 % shallower than 200 m, 25.6 % shallower than 50 m, 40.8 % deeper than 1,000 m; median depth 138 m, maximum 5,217 m. Contours: 627 lines at 50 m (52,916 km), 168 at 200 m (24,330 km), 194 at 1,000 m (26,511 km). |
| Limits | GEBCO states that the grid "is created by interpolating, applying algorithms and mathematical techniques to bathymetric data" and does not ship the underlying soundings, so its local accuracy cannot be judged here. Not for navigation. |

### 3.2 Distance to coast

| | |
|---|---|
| Source | Natural Earth 10 m land: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson (resolved) |
| Version | natural-earth-vector master branch, VERSION file "5.2.0-pre" (https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/VERSION, resolved) |
| Licence as published | Public domain: "All versions of Natural Earth raster + vector map data found on this website are in the public domain" (https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/LICENSE.md and https://www.naturalearthdata.com/about/terms-of-use/, both resolved) |
| Method | Great-circle distance from each sea fine-cell centre to the nearest vertex of the coastline, densified to 0.002 degree (about 220 m), by nearest neighbour on 3-D unit vectors (no projection distortion). Coast taken within the AOI bounds plus 1 degree. |
| Files | `dist_coast_km_{4326,utm49n}.tif` (8.7 and 11.5 MB) |
| Result | 11.2 % of AOI sea lies within 20 km of the coast, 27.0 % within 50 km; the farthest point is 427 km from land. |
| Limits | Distances are to Natural Earth 10 m land only; reefs and islets that the layer does not hold do not count (how many are missing was not checked). |

### 3.3 Ports and distance to the nearest major port

| | |
|---|---|
| Sources | NGA World Port Index (Pub 150) through the MSI API: https://msi.nga.mil/api/publications/world-port-index?output=csv (resolved). Natural Earth 10 m ports: https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_ports.geojson (resolved) |
| Version | WPI as served on the access date (the API output carries no edition number); Natural Earth 5.2.0-pre |
| Licence as published | MSI "Commercial Use Warning" (https://msi.nga.mil/api/pageContent/getByTitle?pageTitle=Commercial%20Use%20Warning, resolved): "NGA claims no copyright or other intellectual property right in the nautical products posted on this website for public use"; the NGA name, seal or initials may not be used to imply endorsement (10 U.S.C. 425). Natural Earth: public domain. |
| Method | Ports within the AOI bounds plus 1 degree. Major = WPI harbour size L (Large) or M (Medium), or any Natural Earth port; WPI S and V stay in the layer but do not count for distance. Distance as in 3.2. |
| Files | `ocean_context.gpkg` layers `ports_4326`, `ports_utm49n`; `dist_port_km_{4326,utm49n}.tif` (6.5 and 9.2 MB) |
| Result | 236 ports (WPI 195: Large 9, Medium 24, Small 39, Very Small 123; Natural Earth 41), 74 major. 11.0 % of AOI sea lies within 100 km of a major port; the farthest point is 742 km away. |
| Limits | The major-port rule is this project's choice. WPI Small and Very Small harbours and harbours absent from both sources do not count, so distance to a major port is not distance to the nearest harbour or fishing base. |

### 3.4 Shipping density (World Bank / IMF)

| | |
|---|---|
| Source | World Bank Data Catalog, Global Shipping Traffic Density, dataset 0037580: https://datacatalog.worldbank.org/search/dataset/0037580 (resolved); files `https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR00454xx/*.zip` (resolved) |
| Version | Catalogue version 5, metadata last updated 2023-01-18; zips listed as last updated 2021-05-03 on the catalogue page; HTTP Last-Modified 2025-02-27 on the file server |
| Licence as published | "This dataset is licensed under Creative Commons Attribution 4.0" (catalogue page; licence page https://datacatalog.worldbank.org/public-licenses?fragment=cc, resolved). Classification: Public. |
| What the values are | Readme (https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR0084213/readme.txt, resolved): "The raster layers were created using IMF's analysis of hourly AIS positions received between Jan-2015 and Feb-2021 and represent the total number of AIS positions that have been reported by ships in each grid cell with dimensions of 0.005 degree by 0.005 degree ... The AIS positions may have been transmitted by both moving and stationary ships within each grid cell, therefore the density is analogous to the general intensity of shipping activity." Vessel types per layer (https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR0045407/readme_ddh.txt, resolved): all; commercial (cargo, tankers, tugs, supply, research, patrol and other working ships); fishing (FISHING VESSEL, TRAWLER); oil and gas (PLATFORM, FLOATING STORAGE/PRODUCTION, DRILLING JACK UP, DRILLING RIG, WELL STIMULATION VESSEL); passenger (PASSENGER SHIP, RO-RO/PASSENGER SHIP); leisure, called Pleasure in the readme (YACHT, SAILING VESSEL). The values check below shows that most of the values do not fit this description. |
| Method | Each zip is opened through GDAL `/vsizip/`, the AOI window is read, the four 0.005 degree source cells whose centres fall in a fine cell are summed, and the zip is deleted. Land cells are NaN. The COGs keep these sums of the values as published, with the warning below in their tags. The cell table holds only presence: `ship_presence_share_<type>` = share of a model cell's AOI sea fine cells whose value is above 0. |
| Files | `ship_density_{all,commercial,fishing,oilgas,passenger,leisure}_{4326,utm49n}.tif` (all 12.6 and 16.6 MB, commercial 12.5 and 16.6, fishing 1.7 and 2.4, oil and gas 1.9 and 2.4, passenger 3.4 and 5.5, leisure 1.4 and 1.8) |
| Values check (why the magnitudes are not used) | In five of the six layers a large share of the values cannot be counts of AIS positions. (1) Raw leisure GeoTIFF (`ShipDensity_Leisure.zip`, downloaded again on 2026-10-09; int32, 0.005 degree, nodata 2147483647), window 103.5 to 104.5 E, 1.0 to 1.5 N (Singapore Strait): all 1,857 nonzero cells lie between 151,669 and 430,408, every value is distinct and none is below 100,000. The file's own histogram (`.aux.xml`, 256 buckets of 11,706 from 0 to 2,996,838) has two flat plateaus, about 19,000 cells per bucket up to 0.69 million and about 10,100 per bucket up to 2.29 million, then almost none; counts would thin out as the value rises. (2) Over AOI sea fine cells the nonzero values form two modes with a gap between them: values above 10,000 are 50 % of the nonzero cells in `all` and `commercial` and 100 % in `oilgas` (minimum 23,514) and `leisure` (minimum 138,549); `all` has 155 of 1,976,693 nonzero cells between 10^2.5 and 10^4; `fishing` has 158 of 22,317 nonzero cells above 10^5.5 and none between 10^3 and 10^5.5. Only `passenger` has a continuous tail (1 to 133,847). (3) Along the fine row 11.99 to 12.00 N, 111.0 to 112.5 E of `all`, 97 of 150 cells hold 1 to 10, while 15 separate cells hold near-identical values from 10,709,621 to 10,711,838 and one holds 21,423,646, exactly 2 x 10,711,823. Counts cannot repeat the same 8-digit value in separate cells. (4) 46 % of the nonzero `all` cells more than 100 km from a major port exceed 54,024, the number of hours from January 2015 to February 2021; at one position per ship per hour that needs more than one ship in that 1 km cell for every hour of six years. The diagnostics are recomputed by the script and stored in `data/ocean_static_summary.json` (`ship_values_*`). |
| Result (presence) | Share of AOI sea fine cells with any value above 0: all 66.9 % (2.38 million km2), commercial 66.5 %, passenger 3.5 %, fishing 0.75 %, oil and gas 0.65 %, leisure 0.25 %. |
| Limits | Only presence (value above 0) is used, for every layer. The magnitude, its rank and its log are not usable, and the encoding is UNVERIFIED until the World Bank data team confirms it (an email to them is the way to settle it). Presence itself assumes that a zero means no AIS record of that type in 2015 to 2021. The fishing layer is above 0 in only 0.75 % of AOI sea cells, so it cannot stand for fishing activity in this sea; the source does not say why it is so sparse here (UNVERIFIED). The data end in February 2021. The open build must not show these layers as traffic intensity or vessel counts. |

### 3.5 EEZ and maritime boundaries (Marine Regions), own file, off by default

| | |
|---|---|
| Source | Flanders Marine Institute (VLIZ), Marine Regions Maritime Boundaries Geodatabase, WFS https://geo.vliz.be/geoserver/MarineRegions/wfs (GetCapabilities resolved): layers `MarineRegions:eez`, titled "Exclusive Economic Zones (200 NM) (v12, world, 2023)", and `MarineRegions:eez_boundaries`, titled "Maritime Boundaries (v12, world, 2023)". The two GetFeature requests are stored in the `about` table. |
| Version | Version 12; dataset record (https://doi.org/10.14284/632, resolved to the VLIZ record) dated 2023-10-25 |
| Licence as published | Dataset record: "This dataset is licensed under a Creative Commons Attribution 4.0 International License." Terms on https://www.marineregions.org/disclaimer.php (resolved): products licensed CC-BY since version 11; "We kindly request our users not to make our products available for download elsewhere"; "Marine Regions is not meant to be used for legal, economical (in the sense of exploration of natural resources) or navigational purposes"; "VLIZ expresses no opinion about the legal state neither of any country, territory or area nor concerning its delimitation, frontier or borders. The data has no legal value whatsoever."; where these terms differ from the CC licence, the CC licence prevails. |
| Citation | Flanders Marine Institute (2023). Maritime Boundaries Geodatabase: Maritime Boundaries and Exclusive Economic Zones (200NM), version 12. Available online at https://www.marineregions.org/. https://doi.org/10.14284/632 |
| Method | Features clipped to the raster box (the AOI bounds); every published attribute kept (MRGID, geoname, pol_type, line_type, territories, sovereigns, sources) plus `source`, `version`, `licence`, `access_date`. Boundary lines keep every published vertex (28,916). The clipped published polygons hold 948,627 vertices, which in two CRS would exceed 30 MB, so polygons are simplified at 10 m in UTM 49N (425,671 vertices; largest relative area change 2.5 x 10^-5). The per-cell lookup in the static table uses the unsimplified published polygons. |
| Files | `data/eez_marineregions.gpkg` (15.0 MB): `eez_4326`, `eez_utm49n` (12 polygons: 9 with pol_type 200NM, 3 Overlapping claim), `eez_boundaries_4326`, `eez_boundaries_utm49n` (77 lines: Connection line 19, Straight baseline 15, Median line 14, Treaty 13, Unsettled (land) 5, Archipelagic baseline 4, Unsettled median line (land) 4, Unsettled (maritime) 3), `about` |
| Published overlaps | The published polygons overlap in four places; they are reported, not resolved: Malaysian EEZ and "Overlapping claim: South China Sea" 67.5 km2, Malaysian and Bruneian EEZ 4.5 km2, Indonesian and Malaysian EEZ 0.4 km2, Thailand and Malaysian EEZ 0.1 km2. |

**EEZ statement.** The lines and polygons are shown as Marine Regions publishes them, with its own labels. In this sea many zones overlap or are disputed; the source marks them with `pol_type` and `line_type`. This project takes no position on any boundary or claim. The product shows the layer off by default, with its source in the legend. The straight edges of the clipped polygons are clip edges, not boundaries. The EEZ fields in the cell table are for display and lookup only; they are not a model feature. No other layer in this build carries any boundary; region names are reporting boxes.

## 4. Daily layers (`scripts/23_daily_ocean.py`)

Window: the 27 VIIRS nights 2026-09-05 to 2026-10-01 (a night is the local evening date in Vietnam, UTC+7; the VIIRS passes fall near 18 UTC) and the 11 UTC dates with a regional Sentinel-1 scene (2026-09-20 to 2026-10-01 except 09-25), 27 dates in all. Every source was found for every date: MUR 27 of 27, gap-filled chlorophyll 27, daily chlorophyll 27, RTOFS 27, GFS-Wave 27 plus 28 scene hours, GFS wind 27. The RTOFS file for 2026-09-18 answered 404 on 2026-10-08 and was fetched on 2026-10-09.

### 4.1 Sea surface temperature (SST)

| | |
|---|---|
| Source | MUR v4.1 analysed_sst through NOAA CoastWatch ERDDAP, dataset `jplMURSST41`: https://coastwatch.pfeg.noaa.gov/erddap/info/jplMURSST41/index.json (resolved) |
| Version | MUR-JPL-L4-GLOB-v04.1, product_version 04.1, doi:10.5067/GHGMR-4FJ04 (https://doi.org/10.5067/GHGMR-4FJ04 resolves to the NASA Earthdata catalogue page); daily analysis valid 09 UTC; the dataset notes that the most recent 7 days are usually revised every day |
| Licence as published | ERDDAP `license` attribute: "These data are available free of charge under the JPL PO.DAAC data policy. The data may be used and redistributed for free but is not intended for legal use, since it may contain inaccuracies." Acknowledgement requested: "These data were provided by JPL under support by NASA MEaSUREs program." |
| Method | ERDDAP axis values are cell centres on whole hundredths; the fine grid has centres on half hundredths, so each fine cell is the mean of the 2 x 2 MUR cells on its corners. Sea = all four MUR cells open sea (mask bit 1 without the land bit 2 or the lake bit 4); ice = bit 8 or 16. Fallback for a missing day: NOAA OISST v2.1 (https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com/?list-type=2&prefix=data/v2.1/avhrr/202609/, resolved); not needed in this window. |
| Files | `sst_mean_c_{4326,utm49n}.tif` (window mean, 3.9 and 7.1 MB), `sst_showcase_c_{4326,utm49n}.tif` (2026-09-29, 5.4 and 8.5 MB) |
| Result | AOI sea pixels 25.65 to 32.15 C over all days, daily mean 29.58 C; window mean per pixel 26.9 to 30.9 C. |

### 4.2 SST fronts

| | |
|---|---|
| Method (`src/darkvessel/ocean/fronts.py`) | 3 x 3 median filter (NaN filled from the nearest valid pixel first; every pixel whose 3 x 3 window touched NaN is dropped again). Gradient magnitude in C per km with dy = 0.01 degree x 111.32 km and dx scaled by the cosine of latitude. Hysteresis mask: a pixel is a front if its gradient exceeds the high threshold, or exceeds the low threshold and is 8-connected to one above the high threshold; components under 3 pixels dropped; nothing within 2 km of Natural Earth land or on MUR land or ice. Thresholds are the 90th and 97th percentiles of the pooled gradient of every fifth AOI sea pixel on all 27 days: low 0.0393, high 0.0578 C per km. Distance to front is great-circle distance to the nearest front pixel centre. Lines: skeleton of the mask, 8-neighbour pixels joined and merged, lines under 10 km dropped. |
| References | Belkin, I. M. and O'Reilly, J. E. (2009). An algorithm for oceanic front detection in chlorophyll and SST satellite imagery. Journal of Marine Systems 78, 319-326. https://doi.org/10.1016/j.jmarsys.2008.11.018 (resolved; title, journal, volume and pages checked in the Crossref record https://api.crossref.org/works/10.1016/j.jmarsys.2008.11.018). Cayula, J.-F. and Cornillon, P. (1992). Edge detection algorithm for SST images. Journal of Atmospheric and Oceanic Technology 9, 67-80. https://doi.org/10.1175/1520-0426(1992)009<0067:EDAFSI>2.0.CO;2 (resolved; Crossref record checked). The full texts were not read in this session; the method here is a plain gradient and hysteresis rule in the spirit of those papers, not either published algorithm. |
| Files | `sst_grad_mean_c_per_km_{4326,utm49n}.tif` (11.1 and 11.4 MB), `front_freq_{4326,utm49n}.tif` (share of days a pixel is a front; 6.1 and 11.1 MB), `data/ocean_fronts.gpkg` (`fronts_4326`, `fronts_utm49n`: the 2,823 front lines, 57,452 km, of 2026-09-29, the VIIRS night with the most clear sea; `about`), `docs/figures/ocean_showcase_fronts.png` |
| Result | On average 8.3 % of AOI sea pixels are front pixels on a day; 4.2 % of pixels are fronts on at least a quarter of the days. Median window-mean gradient 0.0186 C per km. Median distance from a cell centre to the nearest front 20 km. |
| Limits | The thresholds are relative: they mark the strongest tenth of the gradients in this sea in this month, not an absolute oceanographic front strength. MUR is a merged multi-sensor analysis (ERDDAP summary; comment: "Multi-Resolution Variational Analysis (MRVA) method for interpolation"), so where cloud hides the sea the 1 km detail is interpolated and fronts on such days are likely smoother than reality (not quantified here). |

### 4.3 Chlorophyll-a

| | |
|---|---|
| Sources (all NOAA CoastWatch ERDDAP, info pages resolved) | Gap-filled (DINEOF): `noaacwNPPN20S3ASCIDINEOF2kmDaily` (2 km, science quality, VIIRS S-NPP, NOAA-20 and Sentinel-3A OLCI; used 2026-09-05 to 09-27), `nesdisNPPN20S3ASCIDINEOFDaily` (9 km science quality; fallback, not used), `nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily` (9 km near-real-time; used 2026-09-28 to 10-01). Not gap-filled, for the valid-day share: `nesdisVHNSQchlaDaily` (4 km science quality, 2026-09-05 to 09-29) and `nesdisVHNchlaDaily` (4 km near-real-time, 2026-09-30 and 10-01). The script takes the science-quality day when ERDDAP serves it: the rebuild of 2026-10-09 14:00 UTC found 09-29 in science quality, which the run at 01:00 UTC had taken from near-real-time. Info URLs: https://coastwatch.pfeg.noaa.gov/erddap/info/<dataset>/index.json |
| Licence as published | 2 km and 9 km science quality: "These data were produced by NOAA and are not subject to copyright protection in the United States. NOAA waives any potential copyright and related rights in these data worldwide through the Creative Commons Zero 1.0 Universal Public Domain Dedication (CC0-1.0)". 9 km near-real-time: "The data may be used and redistributed for free but is not intended for legal use". 4 km daily: the attribute points to the NASA Earth science data policy (the URL it gives, https://science.nasa.gov/earth-science/earth-science-data/data-information-policy/, answered 404 on 2026-10-09; the NASA Earthdata page https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-information-guidance resolves and states "NASA promotes the full and open sharing of data") and asks for the acknowledgement "These data were provided by NOAA's Center for Satellite Applications and Research (STAR) and the CoastWatch program." |
| Method | log10 of chlor_a (mg m-3). Onto the 0.25 degree cells: mean of the log values of the source pixels in the cell. Window raster: 10 to the mean log over the days with a value (geometric mean) on the 0.05 degree grid (2 km days bin-averaged, 9 km days sampled). Valid-day share: share of days with a direct retrieval, counted only over retrieval pixels whose centre is AOI sea outside the 2 km coast buffer. |
| Files | `chl_mean_mg_m3_{4326,utm49n}.tif` (0.4 MB each), `chl_valid_share_{4326,utm49n}.tif` (0.3 and 0.4 MB) |
| Result | Cell-day values 0.045 to 32 mg m-3, median 0.18 mg m-3; the distribution is close to log-normal (skewness 7.6 on the raw values, 1.7 on log10, the remaining tail being coastal plumes). Regional medians: Gulf of Tonkin 0.55, Southern sea 0.30, Gulf of Thailand 0.29, South Vietnam shelf 0.28, Central sea 0.15, North shelf 0.14 mg m-3. The mean daily share of a cell's sea pixels with a direct retrieval was 15 %. |
| Limits | The DINEOF product is gap-filled (dataset title); on most days most of its pixels are not anchored by a direct retrieval (valid-day share above). Window means up to 70 mg m-3 at river mouths are far above the open-sea values; they are kept as published and should not be read as chlorophyll without a check (their cause was not verified in this session). |

### 4.4 Currents, sea level and mixed layer (RTOFS)

| | |
|---|---|
| Source | NOAA Global RTOFS nowcast diagnostics on AWS, bucket `noaa-nws-rtofs-pds`, files `rtofs.YYYYMMDD/rtofs_glo_2ds_nHHH_diag.nc` (listing https://noaa-nws-rtofs-pds.s3.amazonaws.com/?list-type=2&prefix=rtofs.20260921/rtofs_glo_2ds_n resolved); the record nHHH of folder D is the nowcast valid at HHH UTC of D minus one day, checked against each file's `Date` variable |
| Licence as published | Registry of Open Data on AWS (https://registry.opendata.aws/noaa-rtofs/, resolved): "NOAA data disseminated through NODD are open to the public and can be used as desired." NOAA requests attribution, no implied endorsement, and modified data may not be presented as original. |
| Variables | `ssh` (m), `u_barotropic_velocity` and `v_barotropic_velocity` (m/s, depth-averaged), `mixed_layer_thickness` and `surface_boundary_layer_thickness` (m); SSH gradient magnitude in m per km computed on the native 1/12 degree grid; SSH anomaly = SSH minus the window mean of the cell |
| Method | Valid 18 UTC each date. Only the AOI rows and columns are read (HTTP ranges). Onto 0.25 degree cells: mean of the native cells inside; onto the 0.05 degree rasters: nearest native cell. Each window raster pixel is averaged over the days it has a value. |
| Files | `current_speed_mean_ms_{4326,utm49n}.tif`, `mld_mean_m_{4326,utm49n}.tif`, `ssh_grad_mean_{4326,utm49n}.tif` (0.2 to 0.4 MB each) |
| Result | Cell-day current speed 0.001 to 0.84 m/s, median 0.048; mixed layer 1.4 to 77 m, median 19 m; SSH anomaly standard deviation 0.046 m. |
| Limits | The currents are depth-averaged (barotropic), not surface currents, and are model output. The SSH gradient is a proxy for eddy edges, not an eddy detection. |

### 4.5 Waves and wind (GFS-Wave, GFS)

| | |
|---|---|
| Sources | NOAA GFS-Wave global 0.25 degree, significant height of combined wind waves and swell (HTSGW), bucket `noaa-gfs-bdp-pds` (example index https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20260920/18/wave/gridded/gfswave.t18z.global.0p25.f000.grib2.idx, resolved), fetched by byte range and decoded with ecCodes (Python package `eccodes` 2.49.0, Apache License 2.0, https://pypi.org/project/eccodes/, resolved). NOAA GFS 0.25 degree 10 m wind through `darkvessel.weather.gfs_wind`. |
| Licence as published | NODD text as in 4.4 (https://registry.opendata.aws/noaa-gfs-bdp-pds/, resolved) |
| Method | The 18 UTC analysis (f000) for each date; for each radar scene the cycle and step nearest the scene start. Model-cell centres sit on the shared corner of four GFS cells, so the value is the bilinear (here the plain mean of the four), with NaN neighbours (land) dropped. |
| Files | `wave_hs_mean_m_{4326,utm49n}.tif`, `wind_mean_ms_{4326,utm49n}.tif` (0.25 degree, under 0.1 MB) |
| Result | Cell-night Hs 0.01 to 3.23 m, median 0.67 m; wind 0.1 to 15.4 m/s, median 4.5 m/s. |
| Limits | Model fields at 0.25 degree; sheltered bays and the lee of islands are not resolved. |

## 5. Value checks (rebuilt tables, 2026-10-09)

| Field | Found (cell level, all 27 dates) | Physical expectation | Null share | Verdict |
|---|---|---|---|---|
| `sst_mean_c` | 25.8 to 31.9 C (pixels 25.65 to 32.15) | 20 to 32 C (task check range) | 0 | in range; a few pixels exceed 32 C by up to 0.15 C on single days |
| `wave_hs_m` | 0.01 to 3.23 m | 0 to 8 m (task check range) | 0.06 % | in range |
| `chl_log10_mean` | -1.35 to 1.51 (0.045 to 32 mg m-3) | log-normal (task check) | 0 | close to log-normal: skewness 7.6 raw, 1.7 in log10 |
| `current_speed_ms` | 0.001 to 0.84 m/s | under 3 m/s (task check range) | 0.27 % | in range |
| `mld_m` | 1.4 to 77 m | positive, tens of metres | 0.27 % | plausible |
| `ssh_grad` | 0.00001 to 0.0124 m per km | small and positive | 0.92 % | plausible; null next to the coast by construction |
| `wind_ms` | 0.1 to 15.4 m/s | non-negative | 0 | plausible |
| `chl_valid_share` | 0 to 1 per cell-day; window maximum 0.63 per 0.05 degree pixel | 0 to 1 | 0.02 % | in range |

Fixed in this round (the earlier build had these errors): the window mean of the SSH gradient was 0 instead of NaN on coastal pixels (one count was shared by three fields); the chlorophyll valid-day share counted land pixels as "no retrieval"; the MUR open-lake flag counted as sea; distance to front used one local plane for the whole AOI (up to 7 % error east-west); daily SST cell statistics included fine pixels outside the AOI polygon; waves and wind took one of four equidistant GFS cells (now the mean of the four, which also cut empty wave values from 2.95 % to 0.06 % of cell-nights); RTOFS for 2026-09-18 was missing; the daily COGs had no licence tags; contour lengths were measured in an equal-area projection (now geodesic).

## 6. Cell tables

All three tables use the 0.25 degree model grid; join them on `row`, `col` (and the date). Parquet metadata carries the ocean caveat, the dark caveat and the grid.

### 6.1 `data/ocean_static_cells.parquet`

5,116 rows: every model cell that holds a fine cell inside the AOI polygon or whose centre lies in it (4,778 with the centre in the AOI, 5,095 with AOI sea, 21 all land). Every row of the daily table has a static row.

| Column | Meaning |
|---|---|
| `row`, `col` | model-grid indices, row 0 at the north |
| `lon`, `lat` | cell centre, degrees |
| `region` | reporting box of the cell centre (`scripts/21_viirs_regions.py`), `other` outside every box; not a boundary |
| `aoi_centre` | the cell centre lies in the AOI polygon (the definition of a cell in the daily table) |
| `aoi_share` | share of the cell's 625 fine cells whose centres lie in the AOI polygon |
| `n_sea`, `sea_share`, `sea_area_km2` | number, share (of 625) and spherical area of fine cells that are sea and inside the AOI |
| `depth_mean_m`, `depth_median_m`, `depth_min_m`, `depth_max_m`, `depth_std_m` | GEBCO_2026 depth over those sea cells, m positive down |
| `share_shallower_50m`, `share_shallower_200m`, `share_shelf_break_150_250m` | share of those sea cells in each depth class |
| `slope_mean_m_per_km` | mean depth-gradient magnitude |
| `dist_coast_km`, `dist_coast_min_km` | mean and minimum great-circle distance to the coast, km |
| `dist_port_km`, `dist_port_min_km` | mean and minimum distance to the nearest major port, km |
| `ship_presence_share_all`, `_commercial`, `_fishing`, `_oilgas`, `_passenger`, `_leisure` | share of the cell's AOI sea fine cells whose World Bank/IMF value for that vessel type is above 0 (any AIS record of that type, 2015 to 2021); presence only, because many of the published values cannot be counts (3.4); NaN where the cell has no AOI sea |
| `marineregions_mrgid`, `marineregions_geoname`, `marineregions_pol_type` | as published, the Marine Regions v12 EEZ polygon that covers most of the cell's AOI sea (unsimplified polygons; where two published polygons overlap, the later in the published order); empty where none covers the sea; display and lookup only, not a model feature |
| `marineregions_share`, `marineregions_n` | that polygon's share of the cell's AOI sea; number of distinct polygons in the cell |
| `marineregions_overlap_share` | share of the cell's AOI sea covered by two or more published polygons (the published overlaps of 3.5, reported, not resolved); display only |

Column changes against the earlier draft of `scripts/22_static_layers.py`: `dist_port_km` is now the mean (it was the minimum) and `dist_port_mean_km` became `dist_port_min_km`, so coast and port follow one naming rule; `region`, `aoi_centre`, `aoi_share` and the `marineregions_*` columns are new; `ship_density_<type>` (mean of the published values) and `ship_fishing_share` are gone and `ship_presence_share_<type>` (six types, leisure included) replaces them, because the published magnitudes are not usable (3.4). The EEZ columns are prefixed `marineregions_`, not `eez_`, because `src/darkvessel/ocean/model.py` treats any column starting with `eez` as a static model feature.

### 6.2 `data/ocean_daily_cells.parquet`

128,979 rows: 27 dates x 4,777 cells (model cells whose centre is in the AOI and that hold MUR sea outside the 2 km coast buffer).

| Column | Meaning |
|---|---|
| `night` | date key: the local evening date for VIIRS nights; daily layers of that UTC date |
| `row`, `col`, `lon`, `lat`, `region` | as above |
| `is_viirs_night`, `is_s1_date` | the date is a VIIRS night of the window / a UTC date with a regional Sentinel-1 scene |
| `sst_mean_c`, `sst_sd_c` | mean and spatial standard deviation of SST over the cell's AOI pixels, C |
| `sst_grad_mean` | mean SST gradient magnitude, C per km |
| `front_share` | share of the cell's valid pixels that are front pixels |
| `dist_front_km` | great-circle distance from the cell centre to the nearest front pixel of that day, km |
| `chl_log10_mean` | mean log10 chlorophyll-a (mg m-3), gap-filled product |
| `chl_valid_share` | share of the cell's sea retrieval pixels with a direct (not gap-filled) retrieval that day |
| `ssh_m`, `ssh_anom_m`, `ssh_grad` | RTOFS sea surface height (m), its anomaly against the window mean of the cell (m), its gradient magnitude (m per km) |
| `current_speed_ms` | RTOFS depth-averaged current speed, m/s |
| `mld_m`, `sbl_m` | RTOFS mixed-layer and surface boundary-layer thickness, m |
| `wave_hs_m`, `wind_ms` | GFS-Wave significant wave height (m) and GFS 10 m wind (m/s) at 18 UTC |
| `moon_illum_pct` | median moon illumination of the night's VIIRS lights, percent (NaN for dates that are not VIIRS nights) |
| `sst_source`, `sst_date`, `chl_dataset`, `chl_date`, `chl_obs_dataset`, `rtofs_valid_utc`, `wave_valid_utc`, `wind_valid_utc` | provenance and valid time of each field |
| `caveat` | the ocean caveat with the dark caveat |

### 6.3 `data/ocean_radar_pass_cells.parquet`

4,407 rows: the sea cells of the daily table touched by each of the 119 regional Sentinel-1 scene footprints (`all_touched`). Columns: `scene_id`, `mission`, `acq_utc`, `utc_date`, `row`, `col`, `lon`, `lat`, `region`; `wave_hs_m`, `wave_valid_utc`, `wind_ms`, `wind_valid_utc` at the GFS cycle and step nearest the scene start; and the daily fields of the scene's UTC date from 6.2 (`sst_mean_c`, `sst_sd_c`, `sst_grad_mean`, `front_share`, `dist_front_km`, `chl_log10_mean`, `chl_valid_share`, `ssh_m`, `ssh_anom_m`, `ssh_grad`, `current_speed_ms`, `mld_m`, `sst_source`, `chl_dataset`, `rtofs_valid_utc`); `caveat`.

## 7. How the layers serve radar contacts and dark leads

The layers add context to a contact; none of them makes a contact dark, suspicious or innocent.

- **Is this a plausible place for a vessel of this size?** Depth and distance to coast separate inshore small-boat water from the open sea. A 15 m contact in 20 m of water 10 km from shore is ordinary; the same contact 300 km offshore is worth a closer look at its length estimate and the radar clutter rules.
- **Did AIS traffic ever use this water?** The World Bank/IMF layers can say only whether any AIS record of a vessel type touched the contact's 0.01 degree cell in 2015 to 2021 (value above 0); they cannot say how busy the cell was, because the published values do not behave like counts (section 3.4). So there is no "dense lane" test in this build. The draft object-context code for the next round still defines one (`LANE_RULE` in `src/darkvessel/ocean/context.py`, the 90th percentile of `ship_density_all`); it must be changed to presence or dropped before use. An unmatched contact in water that AIS traffic used may be a vessel the AIS feed missed (the live terrestrial feed is thin off Vietnam, `docs/PROJECT_BOARD.md`); an unmatched contact in water with no AIS record at all is a different kind of lead. Neither is proof of anything.
- **Is it near a port, a front or productive water?** Distance to a major port marks approaches and anchorages. Fronts and chlorophyll are the plan's candidate covariates of fishing activity (`docs/ocean_context_plan.md`); whether they predict where contacts and lights are in this sea is what the expected-activity model will test, so for now they are descriptions, not explanations.
- **What was the weather?** Wind and waves at the pass hour sit next to each contact. In this project's VIIRS record the lit fleet in the Gulf of Tonkin all but vanished from 10 to 15 September 2026, and the mean 18 UTC wind over the gulf's sea was 7.3 to 9.7 m/s on 10 to 13 September (`docs/viirs_lights.md`); weather is therefore a first check when a pass or a night shows few vessels. These fields also feed the detection side of paper 2.
- **What is expected here?** In a later round the expected-activity model will turn these layers into an expected number of contacts or lights per cell and date; a cell far above or below expectation is a lead for review, not evidence.

## 8. Licences and the open build

No source in this build restricts commercial use in its published terms: public domain (GEBCO, which also says users may commercially exploit it; Natural Earth), no copyright claimed (NGA WPI), CC0 or NODD "can be used as desired" (CoastWatch science-quality chlorophyll, RTOFS, GFS), CC BY 4.0 (World Bank, Marine Regions). Two notes: the MUR and near-real-time chlorophyll texts say the data "may be used and redistributed for free" without addressing commercial use, and the Marine Regions terms describe the data as developed for scientific, educational and research purposes while stating that the CC licence prevails. No Global Fishing Watch data are used here; GFW stays in the research build under `data/research/`. Attribution lines for the product footer: GEBCO Compilation Group (2026) GEBCO_2026 Grid; Natural Earth; NGA World Port Index; World Bank and IMF Global Shipping Traffic Density; Flanders Marine Institute (2023) Maritime Boundaries v12; JPL MUR (NASA MEaSUREs); NOAA CoastWatch, NOAA RTOFS and GFS.

Marine Regions asks users "not to make our products available for download elsewhere". The CC BY 4.0 licence permits redistribution and, by Marine Regions' own text, prevails; still, committing `data/eez_marineregions.gpkg` to a public repository or offering it as a download from the shareable page goes against that request. The PM decides whether the file is committed or rebuilt from the WFS by the script.

## 9. Known limits

- Different dates: shipping presence covers 2015 to 2021, GEBCO and Natural Earth are static, the daily layers cover 2026-09-05 to 2026-10-01 only. Each field carries its date or valid time.
- The daily window is one month in one monsoon season; nothing here describes other seasons.
- Fronts are relative to this month's gradient distribution (section 4.2).
- Many World Bank/IMF shipping values cannot be counts, so only presence (value above 0) is used, and its encoding is UNVERIFIED until the World Bank confirms it; the fishing layer is almost empty in this sea (section 3.4).
- Waves and wind are 0.25 degree model fields; RTOFS currents are depth-averaged model output.
- The EEZ polygons are simplified at 10 m for size; the boundary lines and the per-cell lookup use the published vertices. Where two published polygons overlap, the per-cell label takes the later one in the published order; `marineregions_overlap_share` shows where that happens.

## 10. Sources resolved in this session

Every URL below was requested on 2026-10-09 (curl, and again by the build scripts, whose results with HTTP status and time are stored in the `sources` lists of `data/ocean_static_summary.json` and `data/ocean_daily_summary.json`). Status 206 is a successful range request.

| Source | URL | HTTP |
|---|---|---|
| GEBCO_2026 netCDF | https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.nc | 206 |
| GEBCO DOI | https://doi.org/10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa | 206 (BODC page) |
| GEBCO terms of use | https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use | 200 |
| Natural Earth land, ports, licence, version | https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson, .../ne_10m_ports.geojson, .../LICENSE.md, .../VERSION | 206 |
| Natural Earth terms | https://www.naturalearthdata.com/about/terms-of-use/ | 200 |
| NGA World Port Index API | https://msi.nga.mil/api/publications/world-port-index?output=csv | 206 |
| NGA commercial use warning | https://msi.nga.mil/api/pageContent/getByTitle?pageTitle=Commercial%20Use%20Warning | 206 |
| Marine Regions WFS | https://geo.vliz.be/geoserver/MarineRegions/wfs?service=WFS&version=2.0.0&request=GetCapabilities | 200 |
| Marine Regions DOI | https://doi.org/10.14284/632 | 200 (VLIZ record) |
| Marine Regions licence and terms | https://www.marineregions.org/disclaimer.php | 206 |
| World Bank dataset page, licence page | https://datacatalog.worldbank.org/search/dataset/0037580, https://datacatalog.worldbank.org/public-licenses?fragment=cc | 200 |
| World Bank readmes | https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR0084213/readme.txt, .../DR0045407/readme_ddh.txt | 206 |
| World Bank zips | https://datacatalogfiles.worldbank.org/ddh-published/0037580/5/DR0045406/shipdensity_global.zip, .../DR0045401/ShipDensity_Leisure.zip | 206 |
| MUR on ERDDAP | https://coastwatch.pfeg.noaa.gov/erddap/info/jplMURSST41/index.json | 200 |
| MUR DOI | https://doi.org/10.5067/GHGMR-4FJ04 | 200 (NASA Earthdata) |
| Chlorophyll on ERDDAP (5 datasets) | https://coastwatch.pfeg.noaa.gov/erddap/info/{noaacwNPPN20S3ASCIDINEOF2kmDaily, nesdisNPPN20S3ASCIDINEOFDaily, nesdisVHNnoaaSNPPnoaa20NRTchlaGapfilledDaily, nesdisVHNSQchlaDaily, nesdisVHNchlaDaily}/index.json | 200 |
| NASA data policy (as cited by the 4 km chlorophyll) | https://science.nasa.gov/earth-science/earth-science-data/data-information-policy/ | 404 (UNVERIFIED as cited; replaced by the next row) |
| NASA Earthdata data and information guidance | https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-information-guidance | 200 |
| OISST bucket listing | https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com/?list-type=2&prefix=data/v2.1/avhrr/202609/ | 200 |
| RTOFS bucket listing | https://noaa-nws-rtofs-pds.s3.amazonaws.com/?list-type=2&prefix=rtofs.20260921/rtofs_glo_2ds_n | 200 |
| GFS-Wave index | https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20260920/18/wave/gridded/gfswave.t18z.global.0p25.f000.grib2.idx | 206 |
| NODD licence text | https://registry.opendata.aws/noaa-cdr-oceanic/, https://registry.opendata.aws/noaa-rtofs/, https://registry.opendata.aws/noaa-gfs-bdp-pds/ | 206 |
| Belkin and O'Reilly (2009) | https://doi.org/10.1016/j.jmarsys.2008.11.018; https://api.crossref.org/works/10.1016/j.jmarsys.2008.11.018 | 200 |
| Cayula and Cornillon (1992) | https://doi.org/10.1175/1520-0426(1992)009<0067:EDAFSI>2.0.CO;2; Crossref record | 200 |
| ecCodes (PyPI) | https://pypi.org/project/eccodes/ (JSON https://pypi.org/pypi/eccodes/json) | 206 |

## 11. Rebuild

```
python scripts/22_static_layers.py     # about 3 minutes from the caches; a first run reads about 2.4 GB (GEBCO rows 1.2 GB, World Bank zips 1.2 GB)
python scripts/23_daily_ocean.py       # about 5 minutes from the caches; a first run reads about 1 GB
```

Both scripts are checkpointed (`data/cache/ocean/`): finished downloads, fine-grid layers, SST days, gradients and front masks are reused. The proposed Makefile target is `ocean` (section in the task report). The daily build needs the `eccodes` Python package (not yet in `environment.yml`).
