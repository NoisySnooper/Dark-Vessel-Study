"""STAC search against Microsoft Planetary Computer or Copernicus Data Space (CDSE).

Both endpoints were blocked by this project's network policy on 2026-10-02, so this
module is untested against live services. The mapping to the common footprint schema
(s1.footprints.stac_items_to_records) is unit tested with fixtures.
"""

from __future__ import annotations

import datetime as dt

ENDPOINTS = {
    "planetary_computer": {
        "url": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "collection": "sentinel-1-grd",
    },
    "cdse": {
        "url": "https://stac.dataspace.copernicus.eu/v1",
        "collection": "sentinel-1-grd",
    },
}


def search_stac(
    bbox: tuple[float, float, float, float],
    start: dt.date,
    end: dt.date,
    endpoint: str = "planetary_computer",
    platforms: tuple[str, ...] = ("sentinel-1c", "sentinel-1d"),
    max_items: int = 500,
) -> list[dict]:
    """Return STAC item dicts for Sentinel-1 GRD scenes over `bbox` from the given platforms.

    Platform filtering is done client side (case-insensitive) because catalogues differ
    in how they write the platform name.
    """
    from pystac_client import Client

    cfg = ENDPOINTS[endpoint]
    modifier = None
    if endpoint == "planetary_computer":
        import planetary_computer

        modifier = planetary_computer.sign_inplace
    client = Client.open(cfg["url"], modifier=modifier)
    search = client.search(
        collections=[cfg["collection"]],
        bbox=bbox,
        datetime=f"{start.isoformat()}/{end.isoformat()}",
        max_items=max_items,
    )
    wanted = {p.lower() for p in platforms}
    return [it.to_dict() for it in search.items() if str(it.properties.get("platform", "")).lower() in wanted]
