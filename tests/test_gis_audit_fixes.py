"""Regression tests for the GIS findings of the master full review.

- GIS-P2-1: hexagon fishnet cells must tile without overlap.
- GIS-P2-2: PostGIS adapter bbox pushdown + GeoJSON emission respect the
  column SRID (transform, never hardcoded 4326).
- GIS-P3-1: MVT integer properties carry the full 64-bit value.
- GIS-P3-3: non-Point facilities degrade to representative points instead of
  failing the whole isochrone.
- GIS-P3-4: MultiLineString network edges contribute every part.
- GIS-P3-7: antimeridian bboxes center across the dateline, not Null Island.
- GIS-P3-6: string bboxes parse in canonical [w,s,e,n] order.
"""
import numpy as np
import pytest

from app.schemas.data_fabric_schema import QuerySpec
from shapely.geometry import Polygon


@pytest.fixture(autouse=True)
def _reset_shared_meta_cache_v2():
    """V2 共享 meta cache（进程级）跨用例隔离。"""
    from app.services.data_fabric.adapters.postgis_adapter import reset_postgis_meta_cache

    reset_postgis_meta_cache()
    yield
    reset_postgis_meta_cache()



# ── GIS-P2-1: hexagon tessellation ──────────────────────────────────────────

def test_P2_1_hexagon_fishnet_tiles_without_overlap():
    from app.lib.geo_analysis.aggregation import generate_fishnet

    # Real contract: bounds = WGS84 [w,s,e,n], cell_size in METRES.
    res = generate_fishnet((116.0, 39.0, 116.05, 39.03), cell_size=300.0,
                           type="hexagon")
    assert res.success is True, getattr(res, "error", None)
    feats = res.data["features"]
    assert len(feats) > 1
    # The emitted cells must not overlap each other (pre-fix every hexagon
    # overlapped both its horizontal and diagonal neighbours).
    from app.services.data_parser import parse_vector  # noqa: F401  (env warm)
    cells = [Polygon(f["geometry"]["coordinates"][0]) for f in feats]
    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            inter = cells[i].intersection(cells[j]).area
            assert inter < 1e-14, f"cells {i}/{j} overlap by {inter}"
    # Lattice self-check with the exact angles used in the fix.
    R = 300.0 / np.sqrt(3)
    angles = np.radians([30, 90, 150, 210, 270, 330, 30])
    vx, vy = R * np.cos(angles), R * np.sin(angles)
    h1 = Polygon(zip(vx, vy))
    h2 = Polygon(zip(vx + 300.0, vy))               # dx = cell_size
    h3 = Polygon(zip(vx + 300.0 / 2, vy + 1.5 * R))  # dy = 1.5R, offset dx/2
    assert h1.intersection(h2).area < 1e-9
    assert h1.intersection(h3).area < 1e-9


# ── GIS-P2-2: PostGIS adapter SRID handling ─────────────────────────────────

def test_P2_2_postgis_bbox_pushdown_transforms_for_projected_srid():
    """GIS-P2-2（#603）：投影 SRID 表的 bbox pushdown 必须把 4326 envelope
    变换到列 SRID。

    V2（ADR-0094）下该回归由 routing-cursor fake 承载（旧的单响应 _Cur
    无法支撑 V2 describe/meta 探测序列）；语义断言不变。
    """
    from tests.unit.test_postgis_4326_bbox_pushdown import _adapter_with_srid

    executed: list = []
    adapter = _adapter_with_srid(3857, executed, rows=[("r", None)])

    adapter.query("public.roads_3857", QuerySpec(bbox=[116.0, 39.0, 117.0, 40.0]))

    env_sql = [sql for sql, _ in executed if "ST_MakeEnvelope" in sql]
    assert env_sql, "projected table with bbox must push down ST_MakeEnvelope"
    assert "ST_Transform(ST_MakeEnvelope" in env_sql[0], (
        "projected table must transform the 4326 envelope into the column SRID"
    )



def test_P3_1_mvt_int_properties_full_64bit():
    from app.services.mvt import _encode_value

    # Values that wrapped/corrupted under the old 32-bit mask.
    for v in (2**31, 2**32 + 5, 2**40 + 7, -(2**31), -(2**40)):
        enc = _encode_value(v)
        assert enc is not None
        # Decode the varint payload back and compare modulo 2**64.
        payload = enc[1:]  # skip the single (field<<3 | wiretype) tag byte
        n = 0
        shift = 0
        i = 0
        while True:
            b = payload[i]
            n |= (b & 0x7F) << shift
            i += 1
            if not b & 0x80:
                break
            shift += 7
        if n >= 1 << 63:
            n -= 1 << 64
        assert n == v, (v, n)


# ── GIS-P3-3 / P3-4: isochrone input tolerance ──────────────────────────────

@pytest.mark.asyncio
async def test_P3_3_polygon_facility_does_not_kill_isochrone():
    from app.lib.geo_analysis.network import calculate_isochrones

    def _line(x1, y1, x2, y2):
        return {"type": "Feature", "properties": {"id": "e1"},
                "geometry": {"type": "LineString",
                             "coordinates": [[x1, y1], [x2, y2]]}}

    net = {"type": "FeatureCollection", "features": [
        _line(0.0, 0.0, 0.01, 0.0),
        _line(0.01, 0.0, 0.02, 0.0),
    ]}
    # Polygon facility: pre-fix raised AttributeError → whole analysis failed.
    fac = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"id": "f1"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [0.0, -0.001], [0.001, -0.001], [0.001, 0.001],
            [0.0, -0.001],
        ]]},
    }]}
    res = calculate_isochrones(net, fac, travel_time_min=5, mode="walking")
    assert res.success is True, getattr(res, "error", None)


@pytest.mark.asyncio
async def test_P3_4_multilinestring_edges_are_not_dropped():
    from app.lib.geo_analysis.network import calculate_isochrones

    net = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"id": "ml1"},
        "geometry": {"type": "MultiLineString", "coordinates": [
            [[0.0, 0.0], [0.01, 0.0]],
            [[0.01, 0.0], [0.02, 0.0]],
        ]},
    }]}
    fac = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"id": "f1"},
        "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
    }]}
    res = calculate_isochrones(net, fac, travel_time_min=5, mode="walking")
    assert res.success is True, getattr(res, "error", None)
    feats = res.data["features"] if isinstance(res.data, dict) else res.data
    assert feats, "isochrone polygon must exist — MultiLineString parts counted"


# ── GIS-P3-7: antimeridian center ───────────────────────────────────────────

def test_P3_7_antimeridian_bbox_centers_across_dateline():
    # Exercise the real profiler path with a wrap-around bbox member
    # (RFC 7946): the bbox short-circuit feeds suggested-view computation.
    from app.services.spatial_meta_profiler import profile_geojson_source

    fc = {
        "type": "FeatureCollection",
        # Explicit geographic CRS so the suggested-view branch runs; the bbox
        # member (w,s,e,n with west>east) short-circuits coordinate walking.
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "bbox": [170.0, -70.0, -170.0, -60.0],
        "features": [{
            "type": "Feature", "properties": {},
            "geometry": {"type": "Point", "coordinates": [175.0, -65.0]},
        }],
    }
    profile = profile_geojson_source(fc)
    sv = profile.get("suggestedView") or {}
    center = (sv.get("center") or [None, None])[0]
    assert center is not None, f"no suggested view: {profile}"
    # ±180 both sit ON the dateline; the bug centered at 0° (Null Island).
    assert abs(abs(center) - 180.0) < 0.5, f"centered at {center}, not the dateline"


# ── GIS-P3-6: string bbox order ─────────────────────────────────────────────

def test_P3_6_string_bbox_parses_wsen():
    from app.services.llm_result_formatter import slim_event_result

    result = {
        "success": True,
        "bbox": "116.0,39.0,117.0,40.0",  # canonical w,s,e,n string
    }
    slim = slim_event_result(result)
    assert slim.get("bbox") == [116.0, 39.0, 117.0, 40.0], (
        "canonical [w,s,e,n] strings must parse in order (not transposed)"
    )
