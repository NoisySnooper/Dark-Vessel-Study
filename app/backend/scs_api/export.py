"""Exports (spec section 8): GeoPackage (local app only), GeoJSON, CSV and an HTML report. Every export carries the
caveat, the build and the licences of the sources behind each record; the research build adds its stamp and licence
URL. The EEZ layers are shown as published and are never offered for export (board D4.6).
"""

from __future__ import annotations

import html
import json
import tempfile
from pathlib import Path

import darkvessel  # noqa: F401
import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.geometry import Point, mapping, shape

from . import CONTRACT_VERSION
from .config import (CRS_NOTE, DATA_CREDIT, EXPORT_RESEARCH_LICENCE_URL, EXPORT_RESEARCH_STAMP, PRODUCT_CAVEAT,
                     Settings)
from .envelope import ApiError
from .records import utc_now
from .sources import licence_text, registry

TYPES = ("contacts", "vessels", "lights", "events", "leads", "passes")
NOT_EXPORTABLE = {"eez": "EEZ polygons", "eez_boundaries": "EEZ boundary lines"}
FORMATS = ("gpkg", "geojson", "csv", "html")
MAX_ROWS = 200_000


def check_type(typ: str):
    if typ in NOT_EXPORTABLE:
        raise ApiError(403, "not_exportable", f"{NOT_EXPORTABLE[typ]} are shown as published by Marine Regions and are "
                                              "never offered for export.")
    if typ not in TYPES:
        raise ApiError(422, "bad_type", f"type must be one of {', '.join(TYPES)}")


def _licence(rec: dict, build: str) -> str:
    keys = [rec.get("src")] + list((rec.get("prov") or {}).values())
    return licence_text([k for k in keys if k], build)


def stamp(settings: Settings, rec: dict) -> dict:
    out = dict(rec)
    out["caveat"] = rec.get("caveat") or settings.caveat()
    out["build"] = settings.build
    out["licence"] = _licence(rec, settings.build)
    if settings.research:
        out["research_use"] = EXPORT_RESEARCH_STAMP
        out["research_licence_url"] = EXPORT_RESEARCH_LICENCE_URL
    return out


def _geometry(typ: str, rec: dict):
    if typ == "passes":
        return shape(rec["footprint"]) if rec.get("footprint") else None
    if typ == "events" and rec.get("geometry"):
        return shape(rec["geometry"])
    lon, lat = (rec.get("last_lon"), rec.get("last_lat")) if typ == "vessels" else (rec.get("lon"), rec.get("lat"))
    return Point(lon, lat) if lon is not None and lat is not None else None


def _flat(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def meta_block(settings: Settings, git_hash: str | None, typ: str, n: int) -> dict:
    m = {"product": "SCS Vessel Watch export", "type": typ, "rows": n, "build": settings.build,
         "build_label": settings.build_label, "caveat": settings.caveat(), "product_caveat": PRODUCT_CAVEAT,
         "generated_utc": utc_now(), "git_hash": git_hash, "contract_version": CONTRACT_VERSION, "data_credit": DATA_CREDIT,
         "crs": CRS_NOTE, "licences": "; ".join(f"{e['key']}: {e['licence']}" for e in registry(settings.build, git_hash)),
         "sources": registry(settings.build, git_hash)}
    if settings.research:
        m["research_use"] = EXPORT_RESEARCH_STAMP
        m["research_licence_url"] = EXPORT_RESEARCH_LICENCE_URL
    return m


def to_geojson(settings, git_hash, typ, records) -> bytes:
    feats = []
    for r in records:
        g = _geometry(typ, r)
        props = stamp(settings, {k: v for k, v in r.items() if k not in ("footprint", "geometry")})
        feats.append({"type": "Feature", "geometry": None if g is None else mapping(g), "properties": props})
    fc = {"type": "FeatureCollection", "meta": meta_block(settings, git_hash, typ, len(records)), "features": feats}
    return json.dumps(fc, ensure_ascii=False, allow_nan=False).encode("utf-8")


def _frame(settings, typ, records) -> pd.DataFrame:
    rows = [{k: _flat(v) for k, v in stamp(settings, {k: v for k, v in r.items() if k not in ("footprint", "geometry")}).items()}
            for r in records]
    return pd.DataFrame(rows)


def to_csv(settings, git_hash, typ, records) -> bytes:
    if not records:  # no rows: one note row, so the file still carries the caveat, the build and the licences
        m = meta_block(settings, git_hash, typ, 0)
        row = {"note": f"No {typ} matched the filters of this export.", "caveat": m["caveat"], "build": settings.build,
               "licence": m["licences"], "generated_utc": m["generated_utc"], "contract_version": m["contract_version"]}
        if settings.research:
            row.update(research_use=m["research_use"], research_licence_url=m["research_licence_url"])
        return pd.DataFrame([row]).to_csv(index=False).encode("utf-8")
    return _frame(settings, typ, records).to_csv(index=False).encode("utf-8")


def to_gpkg(settings, git_hash, typ, records) -> bytes:
    df = _frame(settings, typ, records)
    geoms = [_geometry(typ, r) for r in records]
    gdf = gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"scs_{typ}.gpkg"
        gdf.to_file(p, layer=f"{typ}_4326", driver="GPKG", engine="pyogrio")
        camau = df["view"].eq("camau") if "view" in df else pd.Series(False, index=df.index)
        valid = gdf.geometry.notna()
        reg, cm = gdf[valid & ~camau], gdf[valid & camau]
        if len(reg):
            reg.to_crs("EPSG:32649").to_file(p, layer=f"{typ}_utm49n", driver="GPKG", engine="pyogrio")
        if len(cm):
            cm.to_crs("EPSG:32648").to_file(p, layer=f"{typ}_utm48n", driver="GPKG", engine="pyogrio")
        about = meta_block(settings, git_hash, typ, len(records))
        about["sources"] = json.dumps(about["sources"], ensure_ascii=False)
        pyogrio.write_dataframe(pd.DataFrame([about]), p, layer="about", driver="GPKG")
        return p.read_bytes()


def to_html(settings, git_hash, typ, records, store=None) -> bytes:
    m = meta_block(settings, git_hash, typ, len(records))
    e = html.escape
    banner = (f'<div class="banner">{e(settings.build_label)} {e("Dark = no AIS match. Not evidence of illegal activity.")}</div>')
    parts = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
             "content=\"width=device-width, initial-scale=1\"><title>SCS Vessel Watch report</title><style>"
             "body{font:14px/1.45 system-ui,sans-serif;margin:16px;color:#1c2127;background:#fff}"
             ".banner{background:#252a31;color:#f6f7f9;padding:6px 10px;margin:0 0 12px}"
             "table{border-collapse:collapse;margin:8px 0 16px;width:100%}td,th{border:1px solid #c5cbd3;padding:3px 6px;"
             "text-align:left;vertical-align:top;font-size:12px}th{background:#f6f7f9}.cav{border-left:3px solid #8f99a8;"
             "padding:6px 10px;background:#f6f7f9}@media print{.banner{color:#000;background:#eee}}</style></head><body>",
             banner, f"<h1>SCS Vessel Watch: {e(typ)} report</h1>", f'<p class="cav">{e(settings.caveat())}</p>',
             f"<p>Build {e(settings.build)}, generated {e(m['generated_utc'])}, git {e(str(git_hash))}, contract {CONTRACT_VERSION}. "
             f"{e(DATA_CREDIT)}.</p>"]
    if settings.research:
        parts.append(f"<p><strong>{e(EXPORT_RESEARCH_STAMP)}</strong> {e(EXPORT_RESEARCH_LICENCE_URL)}. "
                     "Powered by Global Fishing Watch.</p>")
    for r in records:
        rid = r.get("lead_id") or r.get("det_id") or r.get("vessel_key") or r.get("light_id") or r.get("event_id") or r.get("pass_id")
        parts.append(f"<h2>{e(str(rid))}</h2>")
        if typ == "leads":
            parts.append(f"<p>{e(str(r.get('title')))}. Review priority {r.get('priority')} ({e(str(r.get('priority_band')))}), "
                         f"model {e(str(r.get('priority_model_id')))}, calibrated {r.get('calibrated')}. State {e(str(r.get('state')))}.</p>")
            if store is not None and r.get("primary_type") == "contact" and r.get("primary_id") in store.chips:
                import base64

                b = (store.settings.chips_dir / f"{r['primary_id']}.webp").read_bytes()
                parts.append(f'<img alt="radar chip, VV and VH, radar geometry, not north-up" src="data:image/webp;base64,{base64.b64encode(b).decode()}">')
            parts.append("<h3>Factors</h3><table><tr><th>factor</th><th>value</th><th>points</th><th>max</th><th>source</th></tr>")
            for f in r.get("factors") or []:
                parts.append("<tr>" + "".join(f"<td>{e(str(f.get(k)))}</td>" for k in ("factor", "value", "points", "max_points", "source")) + "</tr>")
            parts.append("</table><h3>Evidence</h3><table><tr><th>type</th><th>id</th><th>role</th></tr>")
            for ev in r.get("evidence") or []:
                parts.append(f"<tr><td>{e(str(ev.get('type')))}</td><td>{e(str(ev.get('id')))}</td><td>{e(str(ev.get('role')))}</td></tr>")
            parts.append("</table><h3>Lawful explanations</h3><ul>" + "".join(f"<li>{e(str(x))}</li>" for x in r.get("lawful_explanations") or []) + "</ul>")
            parts.append("<h3>Decision history</h3><ul>" + ("".join(
                f"<li>{e(str(h.get('time_utc')))}: {e(str(h.get('user')))} set {e(str(h.get('from_state')))} to {e(str(h.get('to_state')))}"
                f"{', reason: ' + e(str(h.get('reason'))) if h.get('reason') else ''}{', note: ' + e(str(h.get('note'))) if h.get('note') else ''}</li>"
                for h in r.get("history") or []) or "<li>no decisions yet</li>") + "</ul>")
        prov = r.get("prov") or {}
        parts.append("<h3>Fields and provenance</h3><table><tr><th>field</th><th>value</th><th>source</th></tr>")
        for k, v in r.items():
            if k in ("factors", "evidence", "history", "prov", "footprint", "geometry", "caveat"):
                continue
            parts.append(f"<tr><td>{e(k)}</td><td>{e(str(_flat(v)))}</td><td>{e(str(prov.get(k, r.get('src'))))}</td></tr>")
        parts.append(f"</table><p>Licences: {e(_licence(r, settings.build))}</p>")
    parts += [f'<p class="cav">{e(settings.caveat())}</p>', banner, "</body></html>"]
    return "".join(parts).encode("utf-8")


def export(settings, git_hash, fmt: str, typ: str, records: list[dict], store=None) -> tuple[bytes, str, str]:
    if fmt not in FORMATS:
        raise ApiError(422, "bad_format", f"format must be one of {', '.join(FORMATS)}")
    if len(records) > MAX_ROWS:
        raise ApiError(413, "too_many_rows", f"{len(records)} rows; narrow the filters (at most {MAX_ROWS:,} per export)")
    name = f"scs_{settings.build}_{typ}_{utc_now().replace(':', '').replace('-', '')}"
    if fmt == "geojson":
        return to_geojson(settings, git_hash, typ, records), "application/geo+json", name + ".geojson"
    if fmt == "csv":
        return to_csv(settings, git_hash, typ, records), "text/csv; charset=utf-8", name + ".csv"
    if fmt == "gpkg":
        return to_gpkg(settings, git_hash, typ, records), "application/geopackage+sqlite3", name + ".gpkg"
    return to_html(settings, git_hash, typ, records, store), "text/html; charset=utf-8", name + ".html"
