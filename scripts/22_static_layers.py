"""Static ocean context layers over the South China Sea AOI box: depth, distance to coast, ports, EEZ, shipping density.

Method (details and sources in src/darkvessel/ocean/static.py):
  Depth: GEBCO_2026 (15 arc-second) rows for the AOI bounds plus 0.5 degree, fetched as one byte range of the CEDA
      netCDF, averaged over below-sea-level cells to the 0.01 degree fine grid, metres positive down. Contours at
      50, 200 and 1,000 m from the grid smoothed with a gaussian of 1 cell, simplified at 0.003 degree, lines
      shorter than 0.1 degree dropped, lengths on the WGS 84 ellipsoid.
  Sea mask: fine cell whose centre is not Natural Earth 10 m land and whose GEBCO mean is below sea level.
  Distance to coast: great-circle distance to the Natural Earth coastline densified to 0.002 degree (3-D KD-tree).
  Ports: Natural Earth 10 m ports and the NGA World Port Index within the bounds plus 1 degree; dist_port_km is the
      distance to the nearest major port (WPI Large or Medium, or any Natural Earth port).
  EEZ: Marine Regions v12 polygons and boundary lines from the VLIZ WFS, clipped to the raster box (the AOI bounds),
      every published attribute kept, written to their own file. Boundary lines keep the published vertices; polygons
      are simplified at 10 m in UTM 49N (the clipped published polygons hold about 0.95 million vertices, over 30 MB
      in two CRS). The per-cell lookup uses the unsimplified polygons. No position is taken on any line.
  Shipping density: World Bank/IMF layers of 0.005 degree cells (Jan 2015 to Feb 2021), values as published summed
      to the fine grid, one zip at a time (read through GDAL /vsizip/, zip deleted after the crop). The readme calls
      them AIS position counts, but in five of the six layers a large share of the values cannot be counts
      (st.SHIP_DENSITY_WARNING), so the COGs keep the values as published with that warning and the table holds only
      presence shares.
  Model-grid table: one row per 0.25 degree model cell that holds an AOI fine cell or whose centre is in the AOI,
      sea statistics over the AOI sea fine cells, shipping presence shares, the reporting box, and the Marine Regions
      polygon that covers most of the cell's sea, labelled as published, with the share of the sea where published
      polygons overlap.
Rasters cover the whole AOI bounding box and are NaN on land, so contours and distances stay continuous at the
AOI edge; the model-grid table and the key numbers use sea cells inside the AOI polygon only.

Inputs: network (hosts in static.SOURCES, no keys), data/raw/natural_earth/ne_10m_land.geojson (cached by aoi.py).
Output: data/outputs/small/{depth_m,dist_coast_km,dist_port_km,ship_density_<type>}_{4326,utm49n}.tif
        data/ocean_context.gpkg: depth_contours_*, ports_*, about
        data/eez_marineregions.gpkg: eez_*, eez_boundaries_*, about
        data/ocean_static_cells.parquet, data/ocean_static_summary.json, docs/figures/ocean_static.png
Usage: python scripts/22_static_layers.py [--layers all,fishing,commercial,oilgas,passenger,leisure] [--keep-zips]
Reruns skip finished downloads and fine-grid layers (data/cache/ocean/fine/*.npy); COGs, vectors and tables are
rewritten from them in about three minutes.
"""

import argparse
import json
import time
from pathlib import Path

import darkvessel  # noqa: F401  sets PROJ_DATA before rasterio is imported
import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
from rasterio import features

from darkvessel.aoi import aoi_gdf, natural_earth_land
from darkvessel.config import CRS_GEO, CRS_UTM_REGIONAL, DARK_CAVEAT, DATA_DIR, DEFAULT_AOI, FIG_DIR
from darkvessel.coverage import cell_area_km2
from darkvessel.io import write_dual_crs
from darkvessel.ocean import static as st
from darkvessel.ocean.grid import (NODATA, OCEAN_CACHE, OCEAN_CAVEAT, REPORTING_BOXES, SMALL_DIR, aoi_mask, centres,
                                   fine_grid, model_grid, region_of, write_dual_cog)

EEZ_SIMPLIFY_M = 10.0

ap = argparse.ArgumentParser()
ap.add_argument("--layers", default=",".join(st.WB_FILES), help="shipping density layers to build")
ap.add_argument("--keep-zips", action="store_true", help="keep the World Bank zips after cropping")
args = ap.parse_args()
LAYERS = [x for x in args.layers.split(",") if x]

FINE_CACHE = OCEAN_CACHE / "fine"
FINE_CACHE.mkdir(parents=True, exist_ok=True)
CTX_GPKG = DATA_DIR / "ocean_context.gpkg"
EEZ_GPKG = DATA_DIR / "eez_marineregions.gpkg"
CELLS = DATA_DIR / "ocean_static_cells.parquet"
t0 = time.time()
log = lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True)  # noqa: E731
SRC = {s["key"]: s for s in st.SOURCES}
GEBCO, NE_LAND, NE_PORTS, WPI, MR, WB = (SRC[k] for k in ("gebco", "ne_land", "ne_ports", "wpi", "marineregions", "worldbank"))


def cached(name, fn):
    """Fine-grid layer checkpoint: data/cache/ocean/fine/<name>.npy."""
    p = FINE_CACHE / f"{name}.npy"
    if p.exists():
        return np.load(p)
    a = fn()
    np.save(p, a)
    return a


def cog(arr, name, units, source, **tags):
    base = {"licence": source.get("licence", ""), "licence_url": source.get("licence_url", ""),
            "version": source.get("version", ""), "access_date": st.ACCESS_DATE,
            "nodata": f"{NODATA} = land, no data or outside the grid",
            "coverage": "AOI bounding box (South China Sea, Gulf of Tonkin, Gulf of Thailand); sea cells only",
            "dark_caveat": DARK_CAVEAT}
    base.update(tags)
    paths = write_dual_cog(arr, tr, name, units, source["name"] + "; " + source["url"], tags=base)
    log(f"wrote {name}: " + ", ".join(f"{p.name} {p.stat().st_size / 1e6:.1f} MB" for p in paths))
    return paths


def fresh_gpkg(path: Path):
    """Remove a GeoPackage before it is rebuilt, so no stale layer survives a rerun."""
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------- grids and masks
aoi = aoi_gdf(DEFAULT_AOI)
bounds = tuple(aoi.geometry.iloc[0].bounds)
box1 = (bounds[0] - 1, bounds[1] - 1, bounds[2] + 1, bounds[3] + 1)
tr, shape = fine_grid()
mtr, mshape = model_grid()
rbox = (tr.c, tr.f + shape[0] * tr.e, tr.c + shape[1] * tr.a, tr.f)  # raster box = fine grid extent
lon2, lat2 = centres(tr, shape)
in_aoi = aoi_mask(tr, shape)
aoi_centre = aoi_mask(mtr, mshape)
land_gdf = natural_earth_land(bbox=box1)
land = features.rasterize(((g, 1) for g in land_gdf.geometry), out_shape=shape, transform=tr, dtype="uint8").astype(bool)
area = cell_area_km2(tr, shape)
log(f"fine grid {shape}, AOI cells {in_aoi.sum()}, Natural Earth land cells {land.sum()}; model grid {mshape}, "
    f"AOI centres {aoi_centre.sum()}")

# ---------------------------------------------------------------- 1 depth
elev, etr, gattrs = st.gebco_subset(bounds, OCEAN_CACHE / "gebco")
log(f"GEBCO window {elev.shape}: {gattrs.get('title')}; {gattrs.get('id')}; created {gattrs.get('date_created')}")
depth = cached("depth_m", lambda: st.depth_on_grid(elev, etr, tr, shape))
sea = ~land & np.isfinite(depth)
depth = np.where(sea, depth, np.nan).astype(np.float32)
log(f"sea cells {sea.sum()} ({sea.mean():.1%} of the box), AOI sea cells {(sea & in_aoi).sum()}")
cog(np.round(depth, 1), "depth_m", "m below mean sea level (positive down)", GEBCO,
    method="mean of GEBCO_2026 15 arc-second cells below sea level whose centres fall in the 0.01 degree cell; "
           "land = Natural Earth 10 m land or no cell below sea level", citation=GEBCO["citation"],
    gebco_file_date_created=str(gattrs.get("date_created")), use_limit="GEBCO terms: not for navigation")
contours = st.depth_contours(depth, tr)
contours["source"] = "GEBCO_2026 Grid via CEDA; " + GEBCO["url"]
contours["licence"] = "Public domain (GEBCO terms of use), attribution required; not for navigation"
fresh_gpkg(CTX_GPKG)
write_dual_crs(contours, CTX_GPKG, "depth_contours", utm_crs=CRS_UTM_REGIONAL)
log(f"contours: {contours.groupby('depth_m').size().to_dict()} lines, {contours.length_km.sum():.0f} km")
slope = cached("slope_m_per_km", lambda: st.slope_m_per_km(depth, tr))


# ---------------------------------------------------------------- 2 distance to coast
def _coast():
    pts = st.coast_points(land_gdf, box1)
    out = np.full(shape, np.nan, np.float32)
    out[sea] = st.sphere_distance_km(lon2[sea], lat2[sea], pts[:, 0], pts[:, 1])
    log(f"coast points {len(pts)}")
    return out


dist_coast = cached("dist_coast_km", _coast)
cog(np.round(dist_coast, 2), "dist_coast_km", "km", NE_LAND,
    method="great-circle distance from the cell centre to the nearest vertex of the Natural Earth 10 m coastline "
           "densified to 0.002 degree (about 220 m), computed on 3-D unit vectors (no projection distortion); coast "
           "taken within the AOI bounds plus 1 degree")

# ---------------------------------------------------------------- 3 ports
wpi_csv = st.download(st.WPI_URL, OCEAN_CACHE / "wpi" / "world_port_index.csv")
ne_path = st.download(st.NE_PORTS_URL, OCEAN_CACHE / "natural_earth" / "ne_10m_ports.geojson")
ports = pd.concat([st.wpi_ports(wpi_csv, box1), st.ne_ports(ne_path, box1)], ignore_index=True)
ports = gpd.GeoDataFrame(ports, geometry="geometry", crs=CRS_GEO)
ports["major"] = st.is_major_port(ports)
ports["major_rule"] = st.MAJOR_PORT_RULE
write_dual_crs(ports, CTX_GPKG, "ports", utm_crs=CRS_UTM_REGIONAL)
major = ports[ports.major]
log(f"ports {len(ports)} (WPI {ports.source.str.startswith('NGA').sum()}, Natural Earth "
    f"{(~ports.source.str.startswith('NGA')).sum()}), major {len(major)}")


def _port():
    out = np.full(shape, np.nan, np.float32)
    out[sea] = st.sphere_distance_km(lon2[sea], lat2[sea], major.lon.values, major.lat.values)
    return out


dist_port = cached("dist_port_km", _port)
cog(np.round(dist_port, 2), "dist_port_km", "km", WPI,
    method="great-circle distance from the cell centre to the nearest major port; " + st.MAJOR_PORT_RULE,
    ports_source="NGA World Port Index (msi.nga.mil) and Natural Earth 10 m ports (public domain)",
    n_major_ports=int(len(major)))

about_ctx = pd.DataFrame([
    {"layer": "depth_contours_4326 / depth_contours_utm49n", "attributes": "depth_m (50, 200, 1000), length_km (WGS 84 geodesic)",
     "source": GEBCO["name"], "url": GEBCO["url"], "version": GEBCO["version"], "licence": GEBCO["licence"],
     "licence_url": GEBCO["licence_url"], "citation": GEBCO["citation"], "access_date": st.ACCESS_DATE,
     "method": "contours of the 0.01 degree depth grid (depth_m COG) after a gaussian smoothing of 1 cell, "
               "simplified at 0.003 degree, lines shorter than 0.1 degree dropped; not for navigation",
     "caveat": OCEAN_CAVEAT, "dark_caveat": DARK_CAVEAT},
    {"layer": "ports_4326 / ports_utm49n",
     "attributes": "name, country (as published; Natural Earth has none), harbour_size (WPI L/M/S/V), "
                   "harbour_size_label, harbour_type (WPI code), wpi_port_number, unlocode, ne_scalerank, major, source, licence",
     "source": WPI["name"] + " and " + NE_PORTS["name"], "url": WPI["url"] + " ; " + NE_PORTS["url"],
     "version": "WPI: " + WPI["version"] + "; Natural Earth: " + NE_PORTS["version"],
     "licence": "WPI: " + WPI["licence"] + " Natural Earth: public domain", "licence_url": WPI["licence_url"],
     "citation": "", "access_date": st.ACCESS_DATE, "method": st.MAJOR_PORT_RULE, "caveat": OCEAN_CAVEAT,
     "dark_caveat": DARK_CAVEAT},
])
pyogrio.write_dataframe(about_ctx, CTX_GPKG, layer="about", driver="GPKG")

# ---------------------------------------------------------------- 4 EEZ (own file), as published
bb = ",".join(f"{v:.2f}" for v in box1)
eez_parts, eez_stats, eez_pub = {}, {}, None
fresh_gpkg(EEZ_GPKG)
for layer, tol in (("eez", EEZ_SIMPLIFY_M), ("eez_boundaries", 0.0)):
    url = (f"{st.VLIZ_WFS}?service=WFS&version=2.0.0&request=GetFeature&typeNames=MarineRegions:{layer}"
           f"&bbox={bb},EPSG:4326&outputFormat=application/json&count=1000")
    path = st.download(url, OCEAN_CACHE / "marineregions" / f"{layer}_v12_box.geojson")
    g, stats = st.eez_layer(path, rbox, simplify_m=tol, version=st.MR_VERSION)
    if layer == "eez":
        eez_pub, _ = st.eez_layer(path, rbox, simplify_m=0.0)
    write_dual_crs(g, EEZ_GPKG, layer, utm_crs=CRS_UTM_REGIONAL)
    eez_parts[layer], eez_stats[layer] = g, {**stats, "wfs_request": url}
    log(f"{layer}: {stats}")

# published polygons that overlap one another (reported, not resolved)
pub_utm = eez_pub.to_crs(CRS_UTM_REGIONAL)
overlaps = []
for i, j in zip(*pub_utm.sindex.query(pub_utm.geometry, predicate="intersects"), strict=True):
    if i < j:
        a = pub_utm.geometry.iloc[i].intersection(pub_utm.geometry.iloc[j]).area / 1e6
        if a > 0.01:
            overlaps.append({"a": pub_utm.geoname.iloc[i], "b": pub_utm.geoname.iloc[j], "area_km2": round(a, 1)})
log(f"published polygon overlaps: {overlaps}")
eez_statement = ("Lines and polygons as published by Marine Regions (Flanders Marine Institute, VLIZ), Maritime Boundaries "
                 "v12, with every published attribute. In this sea many zones overlap or are disputed; the source marks "
                 "them with pol_type (eez) and line_type (eez_boundaries). This project takes no position on any boundary "
                 "or claim, and the product shows this layer off by default. Marine Regions states: 'VLIZ expresses no "
                 "opinion about the legal state neither of any country, territory or area nor concerning its delimitation, "
                 "frontier or borders. The data has no legal value whatsoever.' Geometry is clipped to the AOI bounding box "
                 "(the straight box edges are clip edges, not boundaries). Boundary lines keep the published vertices; "
                 f"polygons are simplified at {EEZ_SIMPLIFY_M:.0f} m in UTM 49N to keep the file small (largest area change "
                 f"{eez_stats['eez'].get('max_relative_area_change', 0):.1e} of a polygon).")
about_eez = pd.DataFrame([
    {"layer": f"{k}_4326 / {k}_utm49n", "features": int(len(v)), "vertices_published_clipped": eez_stats[k]["vertices_published_clipped"],
     "vertices_written": eez_stats[k]["vertices_written"], "simplify_m": eez_stats[k]["simplify_m"],
     "attributes": ", ".join(c for c in v.columns if c != "geometry"),
     "source": MR["name"], "url": MR["url"], "wfs_request": eez_stats[k]["wfs_request"], "version": MR["version"],
     "licence": MR["licence"], "licence_url": MR["licence_url"], "citation": MR["citation"], "access_date": st.ACCESS_DATE,
     "statement": eez_statement, "published_overlaps": json.dumps(overlaps), "caveat": OCEAN_CAVEAT,
     "dark_caveat": DARK_CAVEAT}
    for k, v in eez_parts.items()])
pyogrio.write_dataframe(about_eez, EEZ_GPKG, layer="about", driver="GPKG")

# ---------------------------------------------------------------- 5 shipping density
density = {}
for name in LAYERS:
    def _dens(name=name):
        zp = st.download(st.WB_FILES[name], OCEAN_CACHE / "worldbank" / Path(st.WB_FILES[name]).name)
        log(f"shipping {name}: cropping {zp.name} ({zp.stat().st_size / 1e6:.0f} MB zip)")
        counts, ctr, nd = st.shipping_window(zp, rbox)
        out = st.density_on_grid(counts, ctr, nd, tr, shape)
        if not args.keep_zips:
            zp.unlink()
        return out

    d = cached(f"ship_density_{name}", _dens)
    d = np.where(sea, d, np.nan).astype(np.float32)
    density[name] = d
    cog(d, f"ship_density_{name}", st.SHIP_DENSITY_UNIT, WB, vessel_types=f"World Bank layer '{name}': {st.WB_TYPES[name]} "
        f"({st.WB_TYPES_README})", readme=st.WB_README, citation=WB["citation"],
        method="sum of the 0.005 degree source values (as published) whose centres fall in the 0.01 degree cell; land "
               "cells NaN", warning=st.SHIP_DENSITY_WARNING, use="presence only: value > 0; do not use the magnitude, "
        "rank or log of the values", encoding_status="UNVERIFIED (in five of the six layers many values cannot be counts; docs/ocean_context.md 3.4)")

# ---------------------------------------------------------------- 6 model-grid table
fine = {"depth_m": depth, "slope_m_per_km": slope, "dist_coast_km": dist_coast, "dist_port_km": dist_port}
fine.update({f"ship_density_{k}": v for k, v in density.items()})
eez_code = st.label_raster(eez_pub, tr, shape)
eez_lookup = eez_pub[["mrgid", "geoname", "pol_type"]].copy()
eez_lookup["mrgid"] = pd.to_numeric(eez_lookup.mrgid).astype("Int64")
# fine cells covered by two or more published polygons (published overlaps; reported, not resolved)
eez_cover = sum(features.rasterize([(geom, 1)], out_shape=shape, transform=tr, fill=0, dtype="uint8").astype(np.int16)
                for geom in eez_pub.geometry.values)
eez_overlap = (eez_cover >= 2).astype(np.float32)
table = st.model_cell_table(fine, sea & in_aoi, tr, mtr, mshape, in_aoi=in_aoi, cell_area_km2=area, aoi_centre=aoi_centre,
                            labels={"marineregions": (eez_code, eez_lookup)},
                            shares={"marineregions_overlap_share": eez_overlap})
table.insert(4, "region", region_of(table.lon.to_numpy(), table.lat.to_numpy()))
for c in [c for c in table.columns if table[c].dtype == np.float64 and c not in ("lon", "lat")]:
    table[c] = table[c].astype(np.float32)
tbl = pa.Table.from_pandas(table, preserve_index=False)
meta = {b"caveat": OCEAN_CAVEAT.encode(), b"dark_caveat": DARK_CAVEAT.encode(),
        b"grid": b"0.25 degree model grid (origin 99.0E 24.0N, 109 x 94), the cell-night grid of scripts/15_viirs_lights.py",
        b"eez": ("marineregions_* columns: the Marine Regions v12 polygon (as published, unsimplified) covering most of the "
                 "cell's AOI sea; marineregions_overlap_share is the share of that sea where two or more published "
                 "polygons overlap; no position taken; display and lookup only, not a model feature").encode(),
        b"shipping": ("ship_presence_share_<type>: " + st.SHIP_PRESENCE_RULE + ". " + st.SHIP_DENSITY_WARNING).encode()}
pq.write_table(tbl.replace_schema_metadata({**(tbl.schema.metadata or {}), **meta}), CELLS, compression="zstd")
log(f"model cells: {len(table)} rows ({int(table.aoi_centre.sum())} with the centre in the AOI, "
    f"{int((table.n_sea > 0).sum())} with AOI sea)")

# ---------------------------------------------------------------- 7 summary
aoi_sea = sea & in_aoi
A = float(area[aoi_sea].sum())
wpi_rows = ports[ports.source.str.startswith("NGA")]
key = {
    "aoi_sea_area_km2": round(A),
    "aoi_sea_area_shallower_200m_km2": round(float(area[aoi_sea & (depth < 200)].sum())),
    "aoi_sea_share_shallower_200m": round(float(area[aoi_sea & (depth < 200)].sum()) / A, 4),
    "aoi_sea_share_shallower_50m": round(float(area[aoi_sea & (depth < 50)].sum()) / A, 4),
    "aoi_sea_share_deeper_1000m": round(float(area[aoi_sea & (depth >= 1000)].sum()) / A, 4),
    "aoi_sea_depth_median_m": round(float(np.median(depth[aoi_sea])), 1),
    "aoi_sea_depth_max_m": round(float(np.max(depth[aoi_sea])), 1),
    "aoi_sea_share_within_20km_of_coast": round(float(area[aoi_sea & (dist_coast <= 20)].sum()) / A, 4),
    "aoi_sea_share_within_50km_of_coast": round(float(area[aoi_sea & (dist_coast <= 50)].sum()) / A, 4),
    "aoi_sea_dist_coast_max_km": round(float(np.max(dist_coast[aoi_sea])), 1),
    "aoi_sea_share_within_100km_of_major_port": round(float(area[aoi_sea & (dist_port <= 100)].sum()) / A, 4),
    "aoi_sea_dist_port_max_km": round(float(np.max(dist_port[aoi_sea])), 1),
    "ports_total_in_bounds_plus_1deg": int(len(ports)),
    "ports_wpi_by_harbour_size": {st.WPI_HARBOUR_SIZE.get(k, str(k)): int(v) for k, v in
                                  wpi_rows.harbour_size.value_counts(dropna=False).items()},
    "ports_natural_earth": int(len(ports) - len(wpi_rows)),
    "ports_major": int(ports.major.sum()),
    "depth_contours_lines_by_level": {int(k): int(v) for k, v in contours.groupby("depth_m").size().items()},
    "depth_contours_km_by_level": {int(k): round(float(v)) for k, v in contours.groupby("depth_m").length_km.sum().items()},
    "eez_features": {k: int(len(v)) for k, v in eez_parts.items()},
    "eez_pol_types": {str(k): int(v) for k, v in eez_parts["eez"].pol_type.value_counts().items()},
    "eez_line_types": {str(k): int(v) for k, v in eez_parts["eez_boundaries"].line_type.value_counts().items()},
    "eez_published_overlaps": overlaps,
    "model_cells_rows": int(len(table)), "model_cells_aoi_centre": int(table.aoi_centre.sum()),
    "model_cells_with_aoi_sea": int((table.n_sea > 0).sum()),
    "model_cells_by_region": {str(k): int(v) for k, v in table.region.value_counts().items()},
}
key["eez_overlap_aoi_sea_km2"] = round(float(area[aoi_sea & (eez_overlap > 0)].sum()), 1)
key["model_cells_with_eez_overlap"] = int((table.marineregions_overlap_share > 0).sum())
# shipping: presence only, plus the diagnostics behind st.SHIP_DENSITY_WARNING (values as published; many cannot be counts)
far = aoi_sea & (dist_port > 100)
for k, d in density.items():
    v = d[aoi_sea]
    nz = v[np.isfinite(v) & (v > 0)]
    fnz = d[far][np.isfinite(d[far]) & (d[far] > 0)]
    key[f"ship_presence_{k}_share_aoi_sea_cells"] = round(float(np.mean(v > 0)), 4)
    key[f"ship_presence_{k}_aoi_sea_km2"] = round(float(area[aoi_sea & (d > 0)].sum()))
    key[f"ship_values_{k}_nonzero_cells"] = int(nz.size)
    key[f"ship_values_{k}_share_of_nonzero_above_1e4"] = round(float(np.mean(nz > 1e4)), 4) if nz.size else None
    key[f"ship_values_{k}_nonzero_between_10e2.5_and_10e4"] = int(((nz > 10 ** 2.5) & (nz < 1e4)).sum())
    key[f"ship_values_{k}_nonzero_between_10e3_and_10e5.5"] = int(((nz > 1e3) & (nz < 10 ** 5.5)).sum())
    key[f"ship_values_{k}_nonzero_above_10e5.5"] = int((nz > 10 ** 5.5).sum())
    key[f"ship_values_{k}_nonzero_min_max"] = [float(nz.min()), float(nz.max())] if nz.size else None
    key[f"ship_values_{k}_share_above_period_hours_beyond_100km_of_major_port"] = (
        round(float(np.mean(fnz > st.WB_PERIOD_HOURS)), 4) if fnz.size else None)

columns = {
    "row, col": "model grid (0.25 degree, origin 99.0E 24.0N, 109 x 94) indices, row 0 at the north; same grid as "
                "scripts/15_viirs_lights.py and data/ocean_daily_cells.parquet (join on row, col)",
    "lon, lat": "cell centre, degrees",
    "region": "reporting box of the cell centre (scripts/21_viirs_regions.py), 'other' outside every box; not a boundary",
    "aoi_centre": "True when the cell centre lies in the AOI polygon (the definition of an AOI cell in the daily table)",
    "aoi_share": "share of the cell's 625 fine cells (0.01 degree) whose centres lie in the AOI polygon",
    "n_sea": "number of 0.01 degree fine cells in the cell that are sea and inside the AOI polygon (of 625)",
    "sea_share": "n_sea / 625",
    "sea_area_km2": "spherical area of those fine cells, km2",
    "depth_mean_m, depth_median_m, depth_min_m, depth_max_m, depth_std_m":
        "GEBCO_2026 depth over the AOI sea fine cells, m positive down",
    "share_shallower_50m, share_shallower_200m": "share of AOI sea fine cells with depth below the threshold",
    "share_shelf_break_150_250m": "share of AOI sea fine cells with 150 <= depth < 250 m",
    "slope_mean_m_per_km": "mean magnitude of the depth gradient (m per km) over AOI sea fine cells with sea neighbours",
    "dist_coast_km, dist_coast_min_km":
        "mean and minimum great-circle distance to the Natural Earth 10 m coastline over AOI sea fine cells, km",
    "dist_port_km, dist_port_min_km": "mean and minimum distance to the nearest major port (" + st.MAJOR_PORT_RULE + "), km",
    "ship_presence_share_<type>": st.SHIP_PRESENCE_RULE + "; types: " + ", ".join(density) + ". The World Bank magnitudes "
                                  "are not in the table: in five of the six layers many of them cannot be counts (shipping_density_warning)",
    "marineregions_mrgid, marineregions_geoname, marineregions_pol_type":
        "MRGID, geoname and pol_type, as published, of the Marine Regions v12 EEZ polygon that covers most of the cell's "
        "AOI sea fine cells (unsimplified published polygons; where two published polygons overlap, the later in the "
        "published order); empty where no polygon covers the sea. No position taken; for display and lookup only, "
        "not a model feature",
    "marineregions_share": "share of the cell's AOI sea fine cells in that polygon",
    "marineregions_n": "number of distinct Marine Regions polygons among the cell's AOI sea fine cells",
    "marineregions_overlap_share": "share of the cell's AOI sea fine cells covered by two or more published Marine Regions "
                                   "polygons (published overlaps, reported, not resolved; display only)",
}
outputs = {}
for p in sorted(list(SMALL_DIR.glob("depth_m_*.tif")) + list(SMALL_DIR.glob("dist_*_km_*.tif")) +
                list(SMALL_DIR.glob("ship_density_*.tif")) + [CTX_GPKG, EEZ_GPKG, CELLS]):
    outputs[str(p.relative_to(DATA_DIR.parent))] = {"bytes": p.stat().st_size, "mb": round(p.stat().st_size / 1e6, 2)}
oversize = [k for k, v in outputs.items() if v["bytes"] > 20e6]

summary = {
    "generated_utc": st.utc_now(), "script": "scripts/22_static_layers.py", "aoi": DEFAULT_AOI,
    "aoi_bounds": [round(b, 4) for b in bounds], "raster_box": [round(b, 4) for b in rbox],
    "fine_grid": {"res_deg": 0.01, "shape": list(shape), "origin": [round(tr.c, 4), round(tr.f, 4)]},
    "model_grid": {"res_deg": 0.25, "shape": list(mshape), "origin": [mtr.c, mtr.f]},
    "sea_mask": "fine cell centre not in Natural Earth 10 m land and GEBCO_2026 mean below sea level; rasters cover the "
                "whole AOI bounding box, the table and key numbers use AOI sea cells only",
    "gebco_file_attributes": {k: gattrs.get(k) for k in ("title", "id", "date_created", "license", "comment")},
    "sources": st.check_sources([*st.SOURCES,
                                 {"key": "worldbank_readme", "name": "World Bank shipping density readme (units)",
                                  "url": st.WB_README, "licence": WB["licence"], "licence_url": WB["licence_url"]},
                                 {"key": "worldbank_types", "name": "World Bank shipping density readme (vessel types per layer)",
                                  "url": st.WB_TYPES_README, "licence": WB["licence"], "licence_url": WB["licence_url"]}]),
    "outputs": outputs, "outputs_over_20mb": oversize,
    "key_numbers": key, "table_columns": columns, "shipping_density_unit": st.SHIP_DENSITY_UNIT,
    "shipping_density_warning": st.SHIP_DENSITY_WARNING, "shipping_presence_rule": st.SHIP_PRESENCE_RULE,
    "shipping_period_hours": st.WB_PERIOD_HOURS,
    "shipping_density_types": st.WB_TYPES, "eez_statement": eez_statement, "eez_stats": eez_stats,
    "major_port_rule": st.MAJOR_PORT_RULE, "reporting_boxes": REPORTING_BOXES,
    "caveat": OCEAN_CAVEAT, "dark_caveat": DARK_CAVEAT,
    "blocked": ["none of the static layers needed a key; download.gebco.net (client-side app) was not used, the GEBCO file was "
                "read directly from CEDA"],
}
(DATA_DIR / "ocean_static_summary.json").write_text(json.dumps(summary, indent=1, default=str))
print(json.dumps(key, indent=1, default=str))
if oversize:
    log(f"WARNING outputs over 20 MB: {oversize}")

# ---------------------------------------------------------------- 8 figure
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402

ext = (rbox[0], rbox[2], rbox[1], rbox[3])
outline = aoi.boundary
# shipping panel: presence share per 0.05 degree block (5 x 5 fine cells), the only reading of the World Bank values used
B = 5
d_all = density.get("all", np.zeros(shape, np.float32))
pres = np.where(sea & np.isfinite(d_all), (d_all > 0).astype(np.float32), np.nan)
ph, pw = -(-shape[0] // B) * B, -(-shape[1] // B) * B
pad = np.full((ph, pw), np.nan, np.float32)
pad[:shape[0], :shape[1]] = pres
blocks = pad.reshape(ph // B, B, pw // B, B)
n_b = np.isfinite(blocks).sum(axis=(1, 3))
pres_b = np.where(n_b > 0, np.nansum(blocks, axis=(1, 3)) / np.maximum(n_b, 1), np.nan)
ext_b = (tr.c, tr.c + pw * tr.a, tr.f + ph * tr.e, tr.f)
fig, axes = plt.subplots(1, 3, figsize=(16.5, 6.6), constrained_layout=True)
panels = [
    ("Depth (GEBCO_2026)", np.where(sea, depth, np.nan), "Blues", LogNorm(10, 6000), "depth, m below sea level (log scale)"),
    ("Distance to the nearest major port", np.where(sea, dist_port, np.nan), "Purples", None, "km (great circle)"),
    ("AIS shipping presence, all vessel types, 2015 to 2021", pres_b, "Oranges", None,
     "share of 0.01 degree sea cells with a nonzero World Bank value, per 0.05 degree block\n"
     "(presence only; many values as published cannot be counts, docs/ocean_context.md 3.4)"),
]
for ax, (title, arr, cmap, norm, label) in zip(axes, panels, strict=True):
    ax.set_facecolor("#e4e4e4")  # land and no data
    im = ax.imshow(arr, extent=ext_b if title.startswith("AIS") else ext, cmap=cmap, norm=norm, interpolation="nearest",
                   **({"vmin": 0, "vmax": 1} if norm is None and title.startswith("AIS") else {}))
    outline.plot(ax=ax, color="#333333", linewidth=0.7)
    if title.startswith("Depth"):
        for lev, lw in ((200, 0.5), (1000, 0.4)):
            contours[contours.depth_m == lev].plot(ax=ax, color="#1b1b1b", linewidth=lw, alpha=0.6)
    if title.startswith("Distance"):
        ax.scatter(major.lon, major.lat, s=8, c="#1b1b1b", marker="o", linewidths=0, label=f"major port ({len(major)})")
        ax.legend(loc="lower left", fontsize=8, frameon=True)
    ax.set_xlim(rbox[0], rbox[2]); ax.set_ylim(rbox[1], rbox[3])
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("longitude, degrees E"); ax.set_ylabel("latitude, degrees N")
    fig.colorbar(im, ax=ax, shrink=0.8, label=label)
fig.suptitle("Static ocean context over the South China Sea AOI (outline: Natural Earth South China Sea, Gulf of Tonkin, "
             "Gulf of Thailand)", fontsize=12)
fig.text(0.01, -0.03, "Sources: GEBCO_2026 Grid (public domain, not for navigation); NGA World Port Index and Natural Earth "
         "ports; World Bank/IMF Global Shipping Traffic Density (CC BY 4.0), shown as presence only because many of its values "
         "cannot be counts. Depth panel lines: 200 m and 1,000 m contours. "
         "Grey: land or no data. No boundaries shown. Ocean context describes the sea, not what any vessel does; "
         "'dark' means only no AIS match, not illegal.", fontsize=7.5, wrap=True)
FIG_DIR.mkdir(parents=True, exist_ok=True)
fig.savefig(FIG_DIR / "ocean_static.png", dpi=130, bbox_inches="tight")
plt.close(fig)
log(f"figure {FIG_DIR / 'ocean_static.png'}")
log("done")
