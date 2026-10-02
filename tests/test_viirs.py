"""VIIRS light detector on synthetic Day/Night Band radiance (no network)."""

import numpy as np

from darkvessel.viirs.detect import spike_detect

NW = 1e-9  # nW cm-2 sr-1 in SDR units (W cm-2 sr-1)


def _scene(seed=0):
    rng = np.random.default_rng(seed)
    rad = (1.0 + 0.05 * rng.standard_normal((200, 300))) * NW  # dark sea, small noise
    sea = np.ones(rad.shape, bool)
    return rad, sea


def test_point_lights_are_found_and_land_is_ignored():
    rad, sea = _scene()
    rad[50, 60] += 40 * NW           # bright boat
    rad[120, 200] += 4 * NW          # dim boat
    rad[150, 250] += 80 * NW         # light on land
    sea[140:160, 240:260] = False
    det = spike_detect(rad, sea)
    found = set(zip(det.row, det.col))
    assert (50, 60) in found and (120, 200) in found
    assert (150, 250) not in found
    assert len(det) == 2


def test_cloud_edge_ridge_is_not_a_light():
    rad, sea = _scene(1)
    # a moonlit cloud: bright block with a textured edge (ridge of similar values along the edge)
    rad[:, 150:] += 6 * NW
    rad[:, 150] += 3 * NW
    det = spike_detect(rad, sea)
    assert det.empty or not ((det.col >= 148) & (det.col <= 152)).any()


def test_fill_values_are_ignored():
    rad, sea = _scene(2)
    rad[:, :40] = -999.3              # bow-tie deletion / fill
    rad[100, 100] += 20 * NW
    det = spike_detect(rad, sea)
    assert list(zip(det.row, det.col)) == [(100, 100)]


def test_weather_grid_sampling_picks_the_containing_cell():
    from rasterio.transform import from_origin

    from darkvessel.weather import sample_grid

    arr = np.arange(721 * 1440, dtype=float).reshape(721, 1440)
    for origin in (-180.125, -0.125):  # GDAL may present GFS as -180..180 or 0..360
        tr = from_origin(origin, 90.125, 0.25, 0.25)
        v = sample_grid(arr, tr, np.array([114.5, -70.0]), np.array([6.4, -10.1]))
        col = int(np.floor((np.mod(114.5 - origin, 360)) / 0.25))
        assert v[0] == arr[int(np.floor((90.125 - 6.4) / 0.25)), col]
        col2 = int(np.floor((np.mod(-70.0 - origin, 360)) / 0.25))
        assert v[1] == arr[int(np.floor((90.125 + 10.1) / 0.25)), col2]


def _decode(sp):
    """Decode one base64 typed column the way the demo page does."""
    import base64

    a = np.frombuffer(base64.b64decode(sp["b"]), dtype={"i32": "<i4", "u8": "<u1", "u16": "<u2", "i16": "<i2"}[sp["t"]])
    if not sp["s"]:
        return a.astype(float)
    out = a / sp["s"]
    return np.where(a == sp["na"], np.nan, out) if sp["na"] is not None else out


def test_demo_viirs_layer_picks_dark_night_and_packs_columns(tmp_path, monkeypatch):
    import geopandas as gpd
    import pandas as pd

    from darkvessel.viz import demo

    rows = []
    # night A: bright moon, many clear lights; night B: dark moon, fewer; night C: dark moon, more (should win)
    for night, moon, n in (("2026-09-10", 80.0, 30), ("2026-09-12", 5.0, 10), ("2026-09-13", 12.0, 20)):
        for i in range(n):
            rows.append({"light_id": f"{night}_{i}", "satellite": "NOAA-20", "time_utc": f"{night}T18:{i % 60:02d}:00Z",
                         "night": night, "lat": 10 + i * 0.01, "lon": 110 + i * 0.01, "radiance_nw": 12.3,
                         "quality": "clear", "class": "lit_vessel_candidate", "nights_seen_500m": 1,
                         "moon_illum_pct": moon, "satlas_infra_m": np.nan})
    for k, dist in enumerate((450.0, np.nan)):  # two recurring lights, one near a Satlas point, one with no distance
        rows.append({"light_id": f"p{k}", "satellite": "S-NPP", "time_utc": "2026-09-12T18:30:00Z", "night": "2026-09-12",
                     "lat": 8.0 + k, "lon": 105.0, "radiance_nw": 250.0, "quality": "clear", "class": "persistent_light",
                     "nights_seen_500m": 3, "moon_illum_pct": 5.0, "satlas_infra_m": dist})
    df = pd.DataFrame(rows)
    gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326").to_file(
        tmp_path / "viirs_lights.gpkg", layer="viirs_lights_4326", driver="GPKG")
    monkeypatch.setattr(demo, "DATA_DIR", tmp_path)

    v = demo.viirs_data(max_moon_pct=30.0)
    assert v["night"] == "2026-09-13" and v["one"]["n"] == 20 and v["nights"] == 3
    assert v["window_utc_min"] == [18 * 60, 18 * 60 + 19]
    lat = _decode(v["one"]["colz"]["lat"])
    assert np.allclose(lat, 10 + np.arange(20) * 0.01, atol=1e-5)
    assert v["persistent"]["n"] == 2
    infra = _decode(v["persistent"]["colz"]["infra"])  # km, NaN when not available
    assert np.isclose(np.nanmax(infra), 0.45) and np.isnan(infra).sum() == 1
