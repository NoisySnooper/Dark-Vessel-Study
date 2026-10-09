"""Static ocean layer helpers on small synthetic grids (no network)."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from rasterio.transform import from_origin
from shapely.geometry import Polygon, box

from darkvessel.ocean import static as st


def test_dms_to_deg_parses_wpi_strings_and_hemispheres():
    assert st.dms_to_deg("30°20'00\"N") == pytest.approx(30 + 20 / 60)
    assert st.dms_to_deg("48°17'30\"E") == pytest.approx(48 + 17 / 60 + 30 / 3600)
    assert st.dms_to_deg("3°30'00\"S") == pytest.approx(-3.5)
    assert st.dms_to_deg("100°00'00\"W") == pytest.approx(-100.0)
    assert np.isnan(st.dms_to_deg("not a coordinate")) and np.isnan(st.dms_to_deg(None))


def test_gebco_window_indices_and_transform():
    r0, r1, c0, c1 = st.gebco_window((99.16, -3.22, 122.27, 23.76), pad=0.5)
    # 240 cells per degree from -90 / -180; padded box is -3.72..24.26 by 98.66..122.77
    assert (r0, r1) == (int(np.floor(86.28 * 240)), int(np.ceil(114.26 * 240)))
    assert (c0, c1) == (int(np.floor(278.66 * 240)), int(np.ceil(302.77 * 240)))
    tr = st.gebco_window_transform(r1, c0)
    assert tr.c == pytest.approx(-180 + c0 / 240) and tr.f == pytest.approx(-90 + r1 / 240)
    assert tr.a == pytest.approx(1 / 240) and tr.e == pytest.approx(-1 / 240)
    # the window is clipped to the global grid
    assert st.gebco_window((170, 80, 190, 95), pad=1.0)[1] == 43200


def test_bin_to_grid_sums_counts_and_respects_validity():
    # source: 0.5 degree cells, destination: 1 degree cells, both north-up, origin 100E 10N
    src_tr, dst_tr = from_origin(100.0, 10.0, 0.5, 0.5), from_origin(100.0, 10.0, 1.0, 1.0)
    src = np.arange(16, dtype=float).reshape(4, 4)  # 2 x 2 destination cells, 4 source cells each
    s, n = st.bin_to_grid(src, src_tr, dst_tr, (2, 2))
    assert n.tolist() == [[4, 4], [4, 4]]
    assert s[0, 0] == 0 + 1 + 4 + 5 and s[1, 1] == 10 + 11 + 14 + 15
    valid = src % 2 == 0
    s2, n2 = st.bin_to_grid(src, src_tr, dst_tr, (2, 2), valid=valid, chunk=1)
    assert n2.tolist() == [[2, 2], [2, 2]] and s2[0, 0] == 0 + 4
    # source cells outside the destination grid are ignored
    s3, n3 = st.bin_to_grid(src, from_origin(99.0, 10.0, 0.5, 0.5), dst_tr, (2, 2))
    assert n3.sum() == 8 and n3[:, 1].sum() == 0


def test_depth_on_grid_uses_only_below_sea_level_cells():
    src_tr, dst_tr = from_origin(100.0, 10.0, 0.5, 0.5), from_origin(100.0, 10.0, 1.0, 1.0)
    elev = np.array([[-10, -30, 5, 20], [-20, -40, 8, 1], [-100, -100, -100, -100], [-100, -100, -100, -100]], np.int16)
    d = st.depth_on_grid(elev, src_tr, dst_tr, (2, 2))
    assert d[0, 0] == pytest.approx(25.0)  # mean of 10, 30, 20, 40
    assert np.isnan(d[0, 1])  # all land
    assert d[1, 0] == pytest.approx(100.0) and d[1, 1] == pytest.approx(100.0)


def test_sphere_distance_km_handles_longitude_convergence():
    # one degree of longitude is about 111.3 km at the equator and half that at 60 degrees north
    d_eq = st.sphere_distance_km([1.0], [0.0], [0.0], [0.0])[0]
    d_60 = st.sphere_distance_km([1.0], [60.0], [0.0], [60.0])[0]
    assert d_eq == pytest.approx(111.19, abs=0.1)
    assert d_60 == pytest.approx(55.6, abs=0.2)
    # nearest of several points, and empty inputs
    d = st.sphere_distance_km([10.0, 10.0], [0.0, 2.0], [10.0, 10.0], [0.0, 4.0])
    assert d[0] == pytest.approx(0.0, abs=1e-6) and d[1] == pytest.approx(222.39, abs=0.2)
    assert np.isnan(st.sphere_distance_km([1.0], [1.0], [], [])).all()


def test_coast_points_do_not_include_clip_box_edges():
    # a land square 0..4 lon/lat clipped to a box that cuts it: the clip edge x = 2 must not appear as coast
    land = gpd.GeoDataFrame(geometry=[box(0, 0, 4, 4)], crs="EPSG:4326")
    pts = st.coast_points(land, (-1, -1, 2, 5), step_deg=0.1)
    assert len(pts) > 0
    assert not np.any(np.isclose(pts[:, 0], 2.0) & (pts[:, 1] > 0.1) & (pts[:, 1] < 3.9))
    assert np.any(np.isclose(pts[:, 0], 0.0))  # the true west coast is there, densified
    assert len(st.coast_points(land, (10, 10, 11, 11))) == 0


def test_depth_contours_positions_levels_and_drops_short_rings():
    # depth increases eastward 0..300 m across 60 columns at 0.1 degree; the 200 m contour sits near column 40
    tr = from_origin(100.0, 10.0, 0.1, 0.1)
    depth = np.tile(np.linspace(0, 300, 60), (40, 1)).astype(np.float32)
    depth[:, :3] = np.nan  # land strip in the west
    depth[20, 10] = 250.0  # one-pixel bump: a tiny ring at 200 m that must be dropped
    g = st.depth_contours(depth, tr, levels=(50, 200), sigma=0, simplify_deg=0.01, min_len_deg=1.0)
    assert set(g.depth_m) == {50, 200} and g.crs.to_epsg() == 4326
    lon200 = np.array([x for geom in g[g.depth_m == 200].geometry for x in geom.xy[0]])
    assert lon200.min() > 103.9 and lon200.max() < 104.2
    assert (g.length_km > 0).all()
    assert len(g[g.depth_m == 200]) == 1  # the bump ring was shorter than min_len_deg


def test_slope_units_scale_with_latitude():
    tr = from_origin(100.0, 60.0, 0.01, 0.01)
    depth = np.tile(np.arange(10, dtype=float) * 10.0, (6, 1))  # 10 m per 0.01 degree of longitude
    s = st.slope_m_per_km(depth, tr)
    # at 60 N a 0.01 degree step is about 0.557 km, so the slope is about 18 m per km
    assert s[3, 5] == pytest.approx(10.0 / (0.01 * 111.32 * np.cos(np.radians(59.965))), rel=1e-3)
    depth[2, 2] = np.nan
    assert np.isnan(st.slope_m_per_km(depth, tr)[2, 3])


def test_major_port_rule():
    p = pd.DataFrame({"source": ["NGA World Port Index", "NGA World Port Index", "NGA World Port Index",
                                 "NGA World Port Index", "Natural Earth 10 m ports"],
                      "harbour_size": ["L", "M", "S", None, None]})
    assert st.is_major_port(p).tolist() == [True, True, False, False, True]


def test_density_on_grid_treats_nodata_as_missing():
    src_tr, dst_tr = from_origin(100.0, 10.0, 0.5, 0.5), from_origin(100.0, 10.0, 1.0, 1.0)
    counts = np.array([[1, 2, 7, 7], [3, 4, 7, 7], [0, 0, 0, 0], [0, 0, 0, 0]], np.int32)
    d = st.density_on_grid(counts, src_tr, 7, dst_tr, (2, 2))
    assert d[0, 0] == 10 and np.isnan(d[0, 1]) and d[1, 0] == 0


def test_model_cell_table_aggregates_nested_fine_cells():
    # model cells 1 degree, fine cells 0.25 degree (16 per model cell), origin 100E 10N, 2 x 2 model cells
    ftr, mtr = from_origin(100.0, 10.0, 0.25, 0.25), from_origin(100.0, 10.0, 1.0, 1.0)
    shape = (8, 8)
    sea = np.ones(shape, bool)
    sea[:4, :4] = False  # model cell (0, 0) is all land
    sea[0:4, 4:6] = False  # model cell (0, 1) has 8 sea fine cells of 16
    depth = np.full(shape, 100.0, np.float32)
    depth[4:, :4] = 30.0  # model cell (1, 0) shallow
    dist = np.full(shape, 10.0, np.float32)
    dist[4:, 4:] = np.arange(16, dtype=np.float32).reshape(4, 4)
    dens = np.full(shape, 2.0, np.float32)
    dens[4:6, 4:] = 0.0  # model cell (1, 1): 8 of its 16 sea cells have no shipping value
    fish = np.zeros(shape, np.float32)
    fish[7, 7] = 21428360.0  # an ID-like magnitude: presence counts it once, like any other nonzero value
    fish[6, 7] = np.nan  # unknown: left out of the share
    fine = {"depth_m": depth, "dist_coast_km": dist, "dist_port_km": dist, "ship_density_all": dens,
            "ship_density_fishing": fish}
    t = st.model_cell_table(fine, sea, ftr, mtr, (2, 2), cell_area_km2=np.ones(shape))
    assert len(t) == 3 and {(0, 1), (1, 0), (1, 1)} == set(zip(t.row, t.col, strict=True))
    r01 = t[(t.row == 0) & (t.col == 1)].iloc[0]
    assert r01.n_sea == 8 and r01.sea_share == pytest.approx(0.5) and r01.sea_area_km2 == 8
    assert r01.lon == pytest.approx(101.5) and r01.lat == pytest.approx(9.5)
    r10 = t[(t.row == 1) & (t.col == 0)].iloc[0]
    assert r10.depth_mean_m == 30 and r10.share_shallower_50m == 1.0 and r10.share_shallower_200m == 1.0
    assert r10.share_shelf_break_150_250m == 0.0
    r11 = t[(t.row == 1) & (t.col == 1)].iloc[0]
    assert r11.dist_coast_km == pytest.approx(7.5) and r11.dist_coast_min_km == 0
    assert r11.dist_port_km == pytest.approx(7.5) and r11.dist_port_min_km == 0  # same mean/min naming for both
    # many World Bank magnitudes cannot be counts (st.SHIP_DENSITY_WARNING): the table keeps presence shares only
    assert not [c for c in t.columns if c.startswith("ship_density_")] and "ship_fishing_share" not in t.columns
    assert r11.ship_presence_share_all == pytest.approx(0.5) and r10.ship_presence_share_all == 1.0
    assert r11.ship_presence_share_fishing == pytest.approx(1 / 15) and r10.ship_presence_share_fishing == 0.0


def test_model_cell_table_keeps_every_aoi_cell_and_labels_the_majority_polygon():
    ftr, mtr = from_origin(100.0, 10.0, 0.25, 0.25), from_origin(100.0, 10.0, 1.0, 1.0)
    shape = (8, 8)
    in_aoi = np.zeros(shape, bool)
    in_aoi[:, :6] = True  # model cells (r, 0) fully in the AOI, (r, 1) half in
    sea = in_aoi.copy()
    sea[:4, :4] = False  # model cell (0, 0): in the AOI but all land
    code = np.full(shape, -1, np.int32)
    code[4:, :3] = 0  # polygon 0 covers 12 of the 16 sea cells of (1, 0)
    code[4:, 3] = 1  # polygon 1 covers the other 4
    lookup = pd.DataFrame({"mrgid": [11, 22], "geoname": ["Zone A", "Zone B"]})
    centre = np.array([[True, False], [True, True]])
    overlap = np.zeros(shape, np.float32)
    overlap[4:6, :2] = 1.0  # 4 of the 16 sea cells of (1, 0) lie where two published polygons overlap
    t = st.model_cell_table({"depth_m": np.full(shape, 40.0, np.float32)}, sea, ftr, mtr, (2, 2), in_aoi=in_aoi,
                            aoi_centre=centre, labels={"mr": (code, lookup)}, shares={"mr_overlap_share": overlap})
    assert len(t) == 4  # every cell with an AOI fine cell or an AOI centre, land-only ones included
    r00 = t[(t.row == 0) & (t.col == 0)].iloc[0]
    assert r00.n_sea == 0 and r00.aoi_share == 1.0 and np.isnan(r00.depth_mean_m) and r00.mr_n == 0
    assert pd.isna(r00.mr_geoname)
    r01 = t[(t.row == 0) & (t.col == 1)].iloc[0]
    assert r01.aoi_share == 0.5 and r01.n_sea == 8 and not r01.aoi_centre
    r10 = t[(t.row == 1) & (t.col == 0)].iloc[0]
    assert r10.mr_mrgid == 11 and r10.mr_geoname == "Zone A" and r10.mr_share == pytest.approx(0.75) and r10.mr_n == 2
    assert r10.mr_overlap_share == pytest.approx(0.25) and np.isnan(r00.mr_overlap_share)
    r11 = t[(t.row == 1) & (t.col == 1)].iloc[0]
    assert pd.isna(r11.mr_mrgid) and r11.mr_n == 0 and r11.depth_mean_m == 40.0


def test_label_raster_marks_containing_feature_or_minus_one():
    tr = from_origin(100.0, 10.0, 1.0, 1.0)
    g = gpd.GeoDataFrame({"name": ["a", "b"]}, geometry=[box(100, 8, 102, 10), box(101, 8, 104, 9)], crs="EPSG:4326")
    lab = st.label_raster(g, tr, (3, 4))
    assert lab[0, 0] == 0 and lab[0, 3] == -1 and lab[1, 1] == 1 and lab[1, 3] == 1 and lab[2, 0] == -1
    assert (st.label_raster(g.iloc[:0], tr, (3, 4)) == -1).all()


def test_contour_lengths_are_geodesic():
    from shapely.geometry import LineString

    # one degree of longitude at 20 N on WGS 84 is 104.6 km; a planar equal-area projection would be off by several %
    km = st.geodesic_length_km([LineString([(110, 20), (111, 20)]), LineString([(110, 0), (110, 1)])])
    assert km[0] == pytest.approx(104.6, abs=0.2) and km[1] == pytest.approx(110.6, abs=0.2)


def test_eez_layer_clips_simplifies_and_keeps_attributes(tmp_path):
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    g = gpd.GeoDataFrame({"id": ["eez.1"], "geoname": ["Test Zone"], "pol_type": ["200NM"], "sovereign1": ["X"]},
                         geometry=[poly], crs="EPSG:4326")
    p = tmp_path / "eez.geojson"
    g.to_file(p, driver="GeoJSON")
    out, stats = st.eez_layer(p, (5, 5, 20, 20), simplify_m=100.0, version="vTest", licence="CC BY 4.0")
    assert len(out) == 1 and out.geometry.iloc[0].bounds == pytest.approx((5, 5, 10, 10), abs=1e-6)
    assert {"wfs_id", "geoname", "pol_type", "sovereign1", "source", "version", "licence", "access_date"} <= set(out.columns)
    assert out.version.iloc[0] == "vTest" and out.crs.to_epsg() == 4326
    assert stats["features"] == 1 and stats["simplify_m"] == 100.0 and stats["max_relative_area_change"] < 1e-6
    # without simplification the published vertices are kept exactly (only the clip changes the geometry)
    wiggly = Polygon([(0, 0), (6, 0), (6, 0.00001), (6.00001, 3), (6, 6), (0, 6)])
    g2 = gpd.GeoDataFrame({"id": ["eez.2"], "geoname": ["Wiggly"]}, geometry=[wiggly], crs="EPSG:4326")
    p2 = tmp_path / "eez2.geojson"
    g2.to_file(p2, driver="GeoJSON")
    raw, st2 = st.eez_layer(p2, (-1, -1, 20, 20), simplify_m=0)
    assert raw.geometry.iloc[0].equals_exact(wiggly, tolerance=0) or raw.geometry.iloc[0].equals(wiggly)
    assert st2["vertices_written"] == st2["vertices_published_clipped"]


def test_sources_have_licence_and_url_and_no_dashes():
    for s in st.SOURCES:
        assert s["url"].startswith("https://") and s["licence"] and s["licence_url"].startswith("https://")
        for v in s.values():
            assert "\u2014" not in v and "\u2013" not in v
    text = st.SHIP_DENSITY_UNIT + st.MAJOR_PORT_RULE + st.SHIP_DENSITY_WARNING + st.SHIP_PRESENCE_RULE
    assert "\u2014" not in text and "\u2013" not in text


def test_shipping_text_says_presence_only_and_unverified():
    assert "UNVERIFIED" in st.SHIP_DENSITY_WARNING and "cannot be counts" in st.SHIP_DENSITY_WARNING
    assert "value > 0" in st.SHIP_DENSITY_UNIT and "presence" in st.SHIP_DENSITY_UNIT
    assert st.WB_PERIOD_HOURS == 54024  # 2015-01-01 to 2021-03-01, one position per ship per hour
