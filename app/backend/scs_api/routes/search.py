"""Omnibar search (spec section 5): ids, vessel names, call signs, MMSI, IMO and coordinates."""

from __future__ import annotations

import re

from fastapi import APIRouter, Query

from ..envelope import list_response
from ..models import ListEnvelope, SearchResult

DD = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([NSEW])?\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*([NSEW])?\s*$", re.I)
DMS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:°|\s)\s*(\d+(?:\.\d+)?)?\s*(?:'|\s)?\s*(\d+(?:\.\d+)?)?\s*(?:\"|'')?\s*([NSEW])", re.I)


def parse_coordinates(q: str):
    """(lon, lat, interpretation) or None. A bare pair is latitude, longitude unless the first number is above 90."""
    m = DD.match(q)
    if m:
        a, ha, b, hb = float(m.group(1)), (m.group(2) or "").upper(), float(m.group(3)), (m.group(4) or "").upper()
        if ha in ("E", "W") or hb in ("N", "S"):
            lon, lat = a * (-1 if ha == "W" else 1), b * (-1 if hb == "S" else 1)
        elif ha in ("N", "S") or hb in ("E", "W"):
            lat, lon = a * (-1 if ha == "S" else 1), b * (-1 if hb == "W" else 1)
        elif abs(a) > 90:
            lon, lat = a, b
        else:
            lat, lon = a, b
        if abs(lat) <= 90 and abs(lon) <= 180:
            return lon, lat, f"read as {abs(lat):g} {'N' if lat >= 0 else 'S'}, {abs(lon):g} {'E' if lon >= 0 else 'W'}"
    parts = DMS.findall(q)
    if len(parts) == 2:
        vals = {}
        for d, mnt, s, h in parts:
            v = float(d) + float(mnt or 0) / 60 + float(s or 0) / 3600
            h = h.upper()
            vals["lat" if h in "NS" else "lon"] = -v if h in "SW" else v
        if "lat" in vals and "lon" in vals:
            lat, lon = vals["lat"], vals["lon"]
            return lon, lat, f"read as {abs(lat):.5f} {'N' if lat >= 0 else 'S'}, {abs(lon):.5f} {'E' if lon >= 0 else 'W'} (DMS or DDM)"
    compact = q.replace(" ", "").upper()
    if re.fullmatch(r"\d{1,2}[C-HJ-NP-X][A-HJ-NP-Z]{2}(\d{2}){1,5}", compact):
        try:
            import mgrs  # optional; the frontend parses MGRS with mgrs 2.2.0

            lat, lon = mgrs.MGRS().toLatLon(compact)
            return lon, lat, f"read as MGRS {compact}"
        except Exception:  # noqa: BLE001
            return None
    return None


def router(store) -> APIRouter:
    r = APIRouter(tags=["search"])

    @r.get("/search", response_model=ListEnvelope[SearchResult])
    def search(q: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=200)):
        q = q.strip()
        cav = store.cav()
        out, interp = [], None
        if not q:
            return list_response(store.settings, [], 0, limit, 0, interpretation=None)
        c = parse_coordinates(q)
        if c:
            lon, lat, interp = c
            out.append({"type": "point", "id": f"{lat:.5f},{lon:.5f}", "label": f"Go to point {lat:.5f}, {lon:.5f}",
                        "sublabel": interp, "lon": lon, "lat": lat, "score": 1.0, "caveat": cav})

        def add(typ, ids, label_fn, score):
            for i in ids:
                if len(out) >= limit * 3:
                    return
                rec = label_fn(i)
                if rec:
                    out.append({"type": typ, "id": i, "score": score, "caveat": cav, **rec})

        def prefix(space, s, n=10):
            ix = store.prefix_index(space)
            hit = ix.find(s, n)
            if len(s) < 6:  # short queries: exact ids only
                return [h for h in hit if h == s]
            return hit

        cd, ld, ed, lights = store.data["contacts"], store.data["leads"], store.data["events"], store.data["lights"]
        up = q.upper()

        def contact_label(i):
            row = cd.df.iloc[int(cd.pos[i])]
            return {"label": i, "sublabel": f"radar contact, {row['confidence']}, {row['ais_status']}, {row['acq_utc']}",
                    "lon": float(row["lon"]), "lat": float(row["lat"])}

        add("contact", prefix("contacts", up), contact_label, 0.95)

        def light_label(i):
            row = lights.lights.iloc[int(lights.pos[i])]
            return {"label": i, "sublabel": f"VIIRS light, {row['quality']}, {row['time_utc']}", "lon": float(row["lon"]),
                    "lat": float(row["lat"])}

        add("light", prefix("lights", up), light_label, 0.95)

        def lead_label(i):
            row = ld.df.iloc[int(ld.pos[i])]
            return {"label": i, "sublabel": f"{row['title']} (priority {int(row['priority'])})", "lon": float(row["lon"]),
                    "lat": float(row["lat"])}

        add("lead", prefix("leads", q if q[:1].upper() == "L" else up), lead_label, 0.95)

        def event_label(i):
            row = ed.df.iloc[int(ed.pos[i])]
            return {"label": i, "sublabel": f"{row['event_type']} ({row['code']})", "lon": float(row["lon"]), "lat": float(row["lat"])}

        add("event", prefix("events", q), event_label, 0.9)
        pdf = store.data["passes"]
        if len(pdf):
            hit = pdf[pdf["pass_id"].astype(str).str.upper().str.startswith(up)] if len(q) >= 3 else pdf.iloc[0:0]
            for _, row in hit.head(10).iterrows():
                out.append({"type": "pass", "id": row["pass_id"], "label": row["pass_id"],
                            "sublabel": f"Sentinel-1 pass, {row['status']}, {row['start_utc']}", "lon": None, "lat": None,
                            "score": 0.9, "caveat": cav})
        if re.fullmatch(r"r\d+c\d+", q):
            try:
                cell = store.cell(q)
                out.append({"type": "cell", "id": q, "label": f"cell {q}", "sublabel": f"0.25 degree cell, {cell['region_box']}",
                            "lon": cell["lon"], "lat": cell["lat"], "score": 0.9, "caveat": cav})
            except Exception:  # noqa: BLE001
                pass
        if not c:
            pos, total = store.vessels_query({"q": q})
            for v in store.vessel_rows(pos[:limit], full=False):
                exact = q.upper() in {str(v.get("mmsi") or ""), str(v.get("name") or "").upper(), str(v.get("call_sign") or "").upper(),
                                      "IMO" + str(v.get("imo") or ""), str(v.get("imo") or "")}
                out.append({"type": "vessel", "id": v["vessel_key"], "label": v.get("name") or v.get("mmsi") or v["vessel_key"],
                            "sublabel": f"MMSI {v.get('mmsi') or 'unknown'}, {v.get('ship_type') or 'type unknown'}, {v.get('flag') or 'flag unknown'}",
                            "lon": v.get("last_lon"), "lat": v.get("last_lat"), "score": 1.0 if exact else 0.6, "caveat": cav})
        out.sort(key=lambda x: -x["score"])
        out = out[:limit]
        return list_response(store.settings, out, len(out), limit, 0, interpretation=interp)

    return r
