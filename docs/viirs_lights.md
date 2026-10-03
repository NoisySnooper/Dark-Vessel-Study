# Night lights at sea from VIIRS (South China Sea AOI)

Date: 2026-10-03 (UTC). Code: `src/darkvessel/viirs/`, `scripts/15_viirs_lights.py`. Products: `data/viirs_lights.gpkg`, `data/outputs/small/viirs_lit_density_*.tif`, `data/viirs_summary.json`, `docs/figures/viirs_lights.png`.

> **"Dark" does not mean illegal, and a light at sea is not proof of a vessel.** A VIIRS light is something bright at the sea surface at about 00:00 to 03:00 local time: a lit fishing boat, a ship, a platform, a gas flare, an island or navigation light. A lit boat may or may not carry AIS. No AIS source is connected yet, so no light in this product is called dark.

## Why this layer

Sentinel-1 never imaged 45 % of the AOI in 90 days, including the whole central sea and the Spratly area, and sees 6.3 % of the AOI on an average day (`docs/scs_regional.md`). VIIRS images the whole AOI every night from three satellites. It sees only boats that show lights, but it is the only open sensor that watches the central sea every night (`docs/data_additions.md`, N01). The two layers answer different questions: radar sees hulls by day or night in a coastal ring; VIIRS sees lit activity everywhere at night.

## Data

| Input | Source | Use |
|---|---|---|
| VIIRS Day/Night Band SDR (`VIIRS-DNB-SDR`, SVDNB) | NOAA JPSS buckets on AWS: `noaa-nesdis-snpp-pds` (S-NPP), `noaa-nesdis-n20-pds` (NOAA-20), `noaa-nesdis-n21-pds` (NOAA-21); anonymous HTTPS | Radiance, W cm-2 sr-1, 768 x 4064 pixels per granule of about 86 s |
| VIIRS DNB geolocation (`VIIRS-DNB-GEO`, GDNBO) | Same buckets | Latitude, longitude, lunar zenith angle, moon illumination fraction, granule outline (G-Ring), night flag |
| VIIRS JRR cloud mask (`VIIRS-JRR-CloudMask`) | Same buckets | Clear, probably clear, probably cloudy, cloudy at each light |
| Satlas marine infrastructure points | AI2, `latest.geojson`, ODC-BY | Distance from each light to the nearest predicted platform or wind turbine |
| Natural Earth 10 m land | Public domain | Sea mask: AOI minus land buffered by 0.02 degree (about 2 km) |
| Sentinel-1 passes per cell, radar vessel density | This project (`scripts/08_coverage.py`, `scripts/10_regional_density.py`) | Radar comparison |

Access and licence facts are in `docs/data_additions.md` (N01, N02) and `docs/data_landscape.md` (B08), with the sources resolved in the session that wrote them. The registry text for the NOAA JPSS data says it can be used as desired, with attribution requested, no implied endorsement, and no presenting modified data as original; commercial use is allowed (N01). Satlas points are model predictions, not ground truth.

## Method

1. **Find the night passes.** For each satellite and UTC day, list the geolocation granules between 16:00 and 21:00 UTC, read the outline of every third granule, then the neighbours of every hit, and keep descending (night) granules whose outline touches the AOI (`find_aoi_granules`). The index is cached per satellite-night in `data/cache/viirs/index/`.
2. **Read only what is needed.** Granules are opened in place with HTTP range requests (`RangeFile` in `src/darkvessel/viirs/access.py`); no account and no full download.
3. **Mask the sea.** Pixels over open sea inside the AOI, more than about 2 km from Natural Earth land.
4. **Detect point lights.** A spike detector after Elvidge et al. (2015, Remote Sensing 7(3), 3020-3036, doi:10.3390/rs70303020; summary in `docs/bibliometrics.md`): background is the 7 x 7 median; local noise is 1.4826 times the 15 x 15 median absolute spike; a light needs a spike of at least 5 times the local noise and at least 1.5 nW cm-2 sr-1, a peak at least twice the background, and an isolation ratio (spike over the brightest pixel in the 5 x 5 ring around the 3 x 3 core) of at least 2. The local noise term and the isolation test were added after the first run fired about 6,800 times per granule on moonlit cloud edges.
5. **Screen cloud.** Lights where the VIIRS cloud mask says clear or probably clear are kept. Under probable or certain cloud a light is kept only if its spike is at least 5 nW cm-2 sr-1 and its isolation at least 8, because boat lights shine through thin cloud while moonlit cloud texture makes weak, poorly isolated spikes. Each light carries `quality` = clear or under_cloud.
6. **Separate recurring lights.** A light is `persistent_light` when lights fall within 500 m of it on at least 30 % of the nights on which its 0.25 degree cell was mostly clear (half or more of its searched sea clear in the cloud mask; column `clear_nights_cell`), and on at least 3 nights; everything else is `lit_vessel_candidate`. Counting clear nights, not all nights, keeps a platform under a cloudy patch from being mistaken for a boat because cloud hid it on most nights. Recurring lights are platforms, flares, island and navigation lights, and anchorages; linked within 500 m they form the sites of the lean file. Each light also carries the distance to the nearest Satlas platform or turbine (`satlas_infra_m`).
7. **Compare with the radar.** Each light carries `s1_passes_90d`, the number of Sentinel-1 IW passes over its 0.05 degree cell in the 90-day window (0 = never imaged). The density raster below is compared with the radar vessel density on the same 0.25 degree grid (Spearman rank correlation over cells observed by both).
8. **Clear sea.** For every granule the VIIRS cloud mask is read again on its own grid, and the clear and probably clear pixels over searched sea are counted per 0.25 degree cell (`--clear`). Lights under thick cloud never reach the sensor, so the clear sea seen is the fair denominator.
9. **Density.** Clear-sky lit vessel candidates on 0.25 degree cells, two ways: per 1,000 km2 of searched sea per satellite pass (`viirs_lit_density_*`), and per 1,000 km2 of clear searched sea per pass (`viirs_lit_density_clear_*`). Nightly rates per satellite use the clear sea that satellite saw that night.

## Outputs (ArcGIS Pro ready)

- `data/viirs_lights.gpkg`
  - Committed lean file `data/viirs_lights.gpkg`: `viirs_sites_4326` and `_utm49n` (recurring-light sites: position, lights, nights, radiance, Satlas distance, Sentinel-1 passes, likely kind), `viirs_lights_4326` and `_utm49n` (lit vessel candidates of the darkest nights, fewer fields), `viirs_granules_*`, `viirs_nights` (table per night: moon, lit candidates, clear-sky share, in lean file or not) and `about`.
  - Full file `data/viirs_lights_all.gpkg` (gitignored, rebuilt by `--merge`): every light. Fields: `light_id`, `satellite`, `time_utc`, `night` (local evening date, UTC+7), `granule`, `radiance_nw`, `background_nw`, `spike_nw`, `snr`, `isolation`, `neighbour_share`, `sharp`, `moon_illum_pct`, `lunar_zenith_deg`, `cloud_mask` (-1 = no mask value within 2 km), `quality`, `nights_seen_500m`, `clear_nights_cell`, `class`, `satlas_infra_m`, `s1_passes_90d`, `caveat`; plus granule outlines and `about` (caveat, detector settings, cloud rule, persistence rule, data credits).
- `data/outputs/small/viirs_lit_density_4326.tif` and `_utm49n.tif`: per km2 of searched sea per pass; `viirs_lit_density_clear_4326.tif` and `_utm49n.tif`: per km2 of clear searched sea per pass. COG, float32, nodata -1.
- `data/viirs_summary.json`: counts, rules, per-night and per-satellite table, radar checks, single-pixel check.
- `data/viirs_nightly_by_region.csv` and `.json` (`scripts/21_viirs_regions.py`): nightly rate per 1,000 km2 of clear sea by sub-region, with the GFS 10 m wind of the night.
- `docs/figures/viirs_lights.png`: density map with recurring lights, lights per night against the moon.

Rerun: `python scripts/15_viirs_lights.py --start 2026-09-05 --end 2026-10-01 --workers 3`, then `--retry` if any granule failed, `--clear`, and `--merge`.

## Results

RESULTS PENDING (the 27-night run is in progress).

## Limits

- **A light is not a vessel, and a lit vessel is not a dark vessel.** Platforms, flares and island lights recur and are separated by the persistence rule, but a platform seen on fewer than the threshold nights, a new platform or a lit boat moored on the same spot every night can land in the wrong class.
- **Unlit and dimly lit boats are invisible.** VIIRS counts boats that use lights at night (light-luring fisheries above all). It says nothing about unlit boats, so it bounds lit activity, not the fleet.
- **Different time from the radar.** VIIRS night passes over the AOI fall at about 00:00 to 03:00 UTC+7; the 119 Sentinel-1 scenes of the regional run start between 04:00 and 07:00 or between 16:00 and 19:00 UTC+7 (`scenes_processed_4326` in `data/detections_regional.gpkg`). Lights and radar contacts are hours apart, so they are compared by area, never matched one to one.
- **Moon and cloud.** A bright moon raises the background and hides dim lights; thick cloud hides lights altogether; the cloud rule keeps only bright, isolated lights under cloud. Raw nightly counts follow cloud more than anything else (on the new-moon nights of 10 to 12 September only 4 to 20 % of the sea in some granules was clear), so nights are compared per km2 of clear sea, not by raw counts.
- **Near-shore water is not tested.** Pixels within about 2 km of Natural Earth land are masked to keep shore lights out, so boats in harbours, river mouths and the first 2 km off the coast are not counted. Small islands and reefs missing from Natural Earth stay in the sea mask; their lights recur and land in the recurring class.
- **Double counts.** Where swaths of two passes or two satellites overlap, a boat can be counted more than once in a night. The density raster divides by passes; raw per-night totals do not.
- **Persistence threshold.** 30 % of clear nights, at least 3, was set by judgement, not calibrated against truth. Where few nights were clear the rule falls back to 3 nights, and a fleet that works the same spot (a fixed fishing ground, a lit trap) can reach that; such sites show far from Satlas points in the sites layer.
- **Cloud-mask retention.** The registry keeps the JRR cloud mask on AWS for 90 days only; the SDR and geolocation files are kept without that limit (`docs/data_additions.md`, N01). A rerun after about late December 2026 will find no cloud mask for these nights and will keep only bright, isolated lights. The product and the local cache keep the mask values used here.
- **No validation yet.** No labelled lights exist for this AOI. The planned check is EOG's VIIRS Boat Detection product for the same nights (owner action 3, `docs/OWNER_ACTIONS.md`).
