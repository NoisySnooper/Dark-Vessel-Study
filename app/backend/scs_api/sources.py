"""Source registry of app/CONTRACT.md section 2: one entry per source key, licences as published.

URLs are the ones the contract lists in its section 8 or that the producing files cite; each was re-requested when this
module was written (2026-10-09, task R2-T3). `research_only` entries never reach an open-build response (check PW-06).
"""

from __future__ import annotations

ACCESS = "2026-10-09"
_CC_BY = "https://creativecommons.org/licenses/by/4.0/"
_CC_BY_NC = "https://creativecommons.org/licenses/by-nc/4.0/"
_NOAA = "NOAA open data: 'open to the public and can be used as desired'"
_S1_LICENCE = "Copernicus Sentinel Data Legal Notice: free, full and open"
_S1_NOTICE = "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice"


def _e(key, name, method, script, licence, licence_url, url, credit=None, research_only=False, access=ACCESS):
    return {"key": key, "name": name, "method": method, "script": script, "licence": licence, "licence_url": licence_url,
            "url": url, "access_date": access, "credit": credit, "research_only": research_only}


REGISTRY = [
    _e("s1_grd", "Copernicus Sentinel-1C/1D IW GRD, AWS Open Data mirror sentinel-s1-l1c", "input to every radar product",
       None, _S1_LICENCE, _S1_NOTICE, "https://registry.opendata.aws/sentinel-1/",
       "Contains modified Copernicus Sentinel data 2026"),
    _e("det_regional", "September regional run, 119 scenes, 2026-09-20 to 2026-10-01",
       "CA-CFAR, PFA 1e-6, VV/VH fusion, clutter-zone and near-fixed rules, persistence", "scripts/09_run_regional.py",
       "derived from s1_grd", _S1_NOTICE, None, "Contains modified Copernicus Sentinel data 2026"),
    _e("det_live", "Live passes", "same detector settings, matched to recorded live AIS", "scripts/30_live_pass.py",
       "derived from s1_grd", _S1_NOTICE, None, "Contains modified Copernicus Sentinel data 2026"),
    _e("det_camau", "Ca Mau detail scene, Sentinel-1D, 2026-09-29", "CA-CFAR baseline", "scripts/03_run_baseline.py",
       "derived from s1_grd", _S1_NOTICE, None, "Contains modified Copernicus Sentinel data 2026"),
    _e("cnn_v0", "CNN verifier verifier_v0 (model id verifier_v0_356af0ca, threshold 0.631783)",
       "chip classifier on 64 x 64 px VV/VH windows",
       "scripts/06_apply_verifier.py, scripts/14_cnn_shared_cells.py, scripts/32_cnn_regional.py",
       "trained on Allen Institute for AI Sentinel-1A/1B vessel point labels, Apache-2.0",
       "https://www.apache.org/licenses/LICENSE-2.0", None),
    _e("aisstream", "aisstream.io websocket relay of shore receivers", "recorder; matching darkvessel.live",
       "scripts/26_ais_record.py", "UNVERIFIED: the operator publishes no terms of use (only a privacy policy)",
       "https://aisstream.io/privacypolicy", "https://aisstream.io/documentation",
       "live AIS relayed by aisstream.io; terms UNVERIFIED"),
    _e("mid_itu", "ITU Table of Maritime Identification Digits", "MID to flag administration", "darkvessel.live.mid",
       "ITU public table", None, "https://www.itu.int/en/ITU-R/terrestrial/fmd/Pages/mid.aspx"),
    _e("gfw_4wings", "Global Fishing Watch 4Wings AIS presence and SAR detections", "cell-hour identity",
       "scripts/27_gfw_pull.py, scripts/31_gfw_identity.py", "CC BY-NC 4.0, noncommercial", _CC_BY_NC,
       "https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits", "Powered by Global Fishing Watch.",
       research_only=True),
    _e("gfw_vessels", "GFW vessels API identity and registry fields", "identity of matched and nearest vessels",
       "scripts/31_gfw_identity.py", "CC BY-NC 4.0", _CC_BY_NC,
       "https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits", "Powered by Global Fishing Watch.",
       research_only=True),
    _e("gfw_events", "GFW gaps, encounters, loitering and port-visit events", "events API pull", "scripts/27_gfw_pull.py",
       "CC BY-NC 4.0", _CC_BY_NC, "https://globalfishingwatch.org/our-apis/documentation/docs/license-rate-limits",
       "Powered by Global Fishing Watch.", research_only=True),
    _e("viirs_dnb", "VIIRS Day/Night Band SDR and GEO, JRR cloud mask (S-NPP, NOAA-20, NOAA-21), NOAA JPSS on AWS",
       "lit vessel candidates", "scripts/15_viirs_lights.py", _NOAA, "https://registry.opendata.aws/noaa-jpss/",
       "https://registry.opendata.aws/noaa-jpss/"),
    _e("gfs_wind", "NOAA GFS 0.25 degree 10 m wind", "nearest analysis to each contact", "scripts/16_weather_context.py",
       _NOAA, "https://registry.opendata.aws/noaa-gfs-bdp-pds/", "https://registry.opendata.aws/noaa-gfs-bdp-pds/"),
    _e("himawari_ctt", "Himawari-9 AHI L2 cloud-top temperature (noaa-himawari9)", "deep convection flag",
       "scripts/16_weather_context.py",
       "Himawari data is produced and managed by JMA. NOAA has rights to distribute this data freely and openly to the public.",
       "https://registry.opendata.aws/noaa-himawari/", "https://registry.opendata.aws/noaa-himawari/"),
    _e("s2_optical", "Sentinel-2 L2A COGs (sentinel-cogs)", "optical check of a 4,100 contact sample",
       "scripts/19_optical_check.py", "Copernicus; contains modified Copernicus Sentinel data 2026", _S1_NOTICE, None,
       "Contains modified Copernicus Sentinel data 2026"),
    _e("satlas", "AI2 Satlas marine infrastructure points", "distance to the nearest point", "scripts/20_satlas_check.py",
       "ODC-BY", "https://raw.githubusercontent.com/allenai/satlas/main/GeospatialDataProducts.md", None),
    _e("worldcover", "ESA WorldCover 2021 v200 (sea mask)", "detector land mask", None, "CC BY 4.0", _CC_BY, None),
    _e("natural_earth", "Natural Earth 10 m land, marine areas, ports", "AOI, coast, land", None, "public domain",
       "https://www.naturalearthdata.com/about/terms-of-use/", "https://www.naturalearthdata.com/"),
    _e("gebco_2026", "GEBCO_2026 Grid", "cell mean depth", "scripts/22_static_layers.py",
       "public domain, acknowledge the source, not for navigation",
       "https://www.gebco.net/data-products/gridded-bathymetry/terms-of-use", None),
    _e("wpi", "NGA World Port Index (Pub 150)", "major ports", "scripts/22_static_layers.py",
       "NGA claims no copyright in posted products; no endorsement", None, None),
    _e("marineregions_v12", "Marine Regions World EEZ v12 (Flanders Marine Institute, VLIZ), doi:10.14284/632",
       "as published, off by default, no position taken", "scripts/22_static_layers.py", "CC BY 4.0", _CC_BY,
       "https://doi.org/10.14284/632"),
    _e("worldbank_density", "World Bank and IMF Global Shipping Traffic Density",
       "presence only: values as published, not counts", "scripts/22_static_layers.py", "CC BY 4.0", _CC_BY,
       "https://datacatalog.worldbank.org/search/dataset/0037580/global-shipping-traffic-density"),
    _e("mur_sst", "MUR v4.1 SST (JPL PO.DAAC) via NOAA CoastWatch ERDDAP", "daily SST, gradient and fronts",
       "scripts/23_daily_ocean.py", "free of charge under the PO.DAAC data policy",
       "https://coastwatch.pfeg.noaa.gov/erddap/info/jplMURSST41/index.json", None),
    _e("chl_dineof", "NOAA CoastWatch chlorophyll-a, DINEOF gap-filled", "daily chlorophyll",
       "scripts/23_daily_ocean.py", "per dataset in data/ocean_daily_summary.json (CC0-1.0 or NOAA no-copyright text)",
       None, None),
    _e("rtofs", "NOAA RTOFS global nowcast 2-D diagnostics", "currents, mixed layer, sea surface height",
       "scripts/23_daily_ocean.py", "NOAA open data", None, None),
    _e("gfs_wave", "NOAA GFS-Wave 0.25 degree significant wave height", "daily wave height", "scripts/23_daily_ocean.py",
       "NOAA open data", "https://registry.opendata.aws/noaa-gfs-bdp-pds/", None),
    _e("ocean_context", "Ocean context at radar objects and VIIRS lights (data/ocean_context_objects.parquet)",
       "static and daily sea fields sampled at each object's position and time; every field names its own source and "
       "valid time", "scripts/25_object_context.py", "derived; each field carries the licence of its own source", None, None,
       access="2026-10-10"),
    _e("expected_activity", "Expected-activity model (data/expected_activity.parquet, data/expected_activity.json)",
       "expected counts of lit and radar vessel candidates per cell and night or pass from sea and weather fields; "
       "observed against expected, z, Benjamini-Hochberg q and flags are model output", "scripts/34_expected_activity.py",
       "derived", None, None, access="2026-10-10"),
    _e("esa_acq_plan", "ESA Sentinel-1 acquisition plan KML files", "pass plan; repeat predictions are not ESA's plan",
       "darkvessel.ais.s1_passes", "ESA public plan", None,
       "https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans"),
    _e("analyst", "Owner labels and lead decisions", "the app", "app/backend", "the owner's", None, None),
    _e("app", "Values computed by the product (review priority, evidence counts, links)", "app/backend and scripts/33_leads.py",
       "app/backend", "derived", None, None),
]
BY_KEY = {e["key"]: e for e in REGISTRY}


def registry(build: str, git_hash: str | None) -> list[dict]:
    """Registry entries for one build: the open build drops every research_only entry (contract 2)."""
    return [{**e, "git_hash": git_hash} for e in REGISTRY if build == "research" or not e["research_only"]]


def licence_text(keys, build: str) -> str:
    """The licences of the sources behind a record, for exports (spec 8)."""
    seen = []
    for k in keys:
        e = BY_KEY.get(k)
        if e is None or (e["research_only"] and build != "research"):
            continue
        t = f"{e['key']}: {e['licence']}"
        if t not in seen:
            seen.append(t)
    return "; ".join(seen)
