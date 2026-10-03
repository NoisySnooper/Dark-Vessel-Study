"""Nightly lit-vessel rate by sub-region, with the wind of the same night (what drives night-to-night change).

For each night and sub-region: clear-sky lit vessel candidates (all satellites pooled) per 1,000 km2 of clear
searched sea (cloud-mask counts from scripts/15_viirs_lights.py --clear), and the GFS 0.25 degree 10 m wind at
18 UTC averaged over the sub-region's sea cells. Rates on less than 5,000 km2 of clear sea are left empty.
Sub-regions are plain boxes for reporting, not boundaries or claims.

Inputs: data/viirs_lights_all.gpkg and the VIIRS cache (scripts/15_viirs_lights.py --merge, --clear).
Output: data/viirs_nightly_by_region.csv, data/viirs_nightly_by_region.json (Spearman of rate against wind)
Usage: python scripts/21_viirs_regions.py
"""

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyogrio
from scipy.stats import spearmanr

from darkvessel import weather
from darkvessel.config import DATA_DIR
from darkvessel.coverage import cell_area_km2

spec = importlib.util.spec_from_file_location("v15", Path(__file__).with_name("15_viirs_lights.py"))
v15 = importlib.util.module_from_spec(spec)
argv, sys.argv = sys.argv, [sys.argv[0]]
spec.loader.exec_module(v15)
sys.argv = argv

REGIONS = {"Gulf of Tonkin": (105.5, 17.0, 110.0, 22.5), "North shelf": (110.0, 18.0, 118.0, 23.5),
           "Gulf of Thailand": (99.0, 6.0, 105.0, 14.0), "South Vietnam shelf": (105.0, 6.0, 110.0, 12.0),
           "Central sea": (110.0, 6.0, 118.0, 17.0), "Southern sea": (102.0, -3.5, 110.0, 6.0)}

tr, shape = v15._grid()
sg = v15.sea_grid()
sr, sc = np.nonzero(sg.mask)
r25 = np.floor((sg.north - (sr + 0.5) * sg.res - tr.f) / tr.e).astype(int)
c25 = np.floor((sg.west + (sc + 0.5) * sg.res - tr.c) / tr.a).astype(int)
ok = (r25 >= 0) & (r25 < shape[0]) & (c25 >= 0) & (c25 < shape[1])
sea_cells = np.zeros(shape)
np.add.at(sea_cells, (r25[ok], c25[ok]), 1)
sea_km2 = (cell_area_km2(tr, shape) * sea_cells / (tr.a / sg.res) ** 2).ravel()
CX, CY = np.meshgrid(tr.c + (np.arange(shape[1]) + 0.5) * tr.a, tr.f + (np.arange(shape[0]) + 0.5) * tr.e)
CX, CY = CX.ravel(), CY.ravel()
in_region = {k: (CX >= w) & (CX < e) & (CY >= s) & (CY < n) & (sea_km2 > 0) for k, (w, s, e, n) in REGIONS.items()}

rows = []
for m in (json.loads(p.read_text()) for p in sorted(v15.CACHE.glob("*.json"))):
    pth = v15.CACHE / f"{m['granule']}.clear.npz"
    if not pth.exists():
        continue
    z = np.load(pth)
    km2 = np.zeros(len(sea_km2))
    km2[z["cells"]] = z["clear_n"] / np.maximum(z["sea_n"], 1) * sea_km2[z["cells"]]
    night = str(v15.night_of(pd.to_datetime(pd.Series([m["start_utc"]]), utc=True))[0])
    rows += [{"night": night, "region": k, "clear_km2": float(km2[msk].sum())} for k, msk in in_region.items()]
clear = pd.DataFrame(rows).groupby(["night", "region"]).clear_km2.sum()

lights = pyogrio.read_dataframe(DATA_DIR / "viirs_lights_all.gpkg", layer="viirs_lights_4326", read_geometry=False,
                                columns=["night", "lat", "lon", "moon_illum_pct"],
                                where="class = 'lit_vessel_candidate' AND quality = 'clear'")
out = []
for night in sorted(lights.night.unique()):
    spd, gtr = weather.gfs_wind(dt.datetime.fromisoformat(night).replace(hour=18, tzinfo=dt.timezone.utc),
                                cache_dir=DATA_DIR / "cache" / "weather")
    ln = lights[lights.night == night]
    for k, (w, s, e, n) in REGIONS.items():
        ckm2 = float(clear.get((night, k), 0.0))
        cnt = int(((ln.lon >= w) & (ln.lon < e) & (ln.lat >= s) & (ln.lat < n)).sum())
        wind = float(np.nanmean(weather.sample_grid(spd, gtr, CX[in_region[k]], CY[in_region[k]])))
        out.append({"night": night, "region": k, "lit_clear": cnt, "clear_sea_km2": round(ckm2),
                    "lit_per_1000km2_clear": round(1000 * cnt / ckm2, 3) if ckm2 >= 5000 else None,
                    "wind_mean_ms": round(wind, 1), "moon_illum_pct": float(ln.moon_illum_pct.median())})
tab = pd.DataFrame(out)
tab.to_csv(DATA_DIR / "viirs_nightly_by_region.csv", index=False)
stats = {}
for k, g in tab.dropna(subset=["lit_per_1000km2_clear"]).groupby("region"):
    stats[k] = {"nights": int(len(g)), "rate_median": round(float(g.lit_per_1000km2_clear.median()), 3),
                "spearman_rate_vs_wind": round(float(spearmanr(g.wind_mean_ms, g.lit_per_1000km2_clear)[0]), 3),
                "spearman_rate_vs_moon": round(float(spearmanr(g.moon_illum_pct, g.lit_per_1000km2_clear)[0]), 3)}
(DATA_DIR / "viirs_nightly_by_region.json").write_text(json.dumps(stats, indent=1))
print(tab.pivot(index="night", columns="region", values="lit_per_1000km2_clear").round(2).to_string())
print(json.dumps(stats, indent=1))
