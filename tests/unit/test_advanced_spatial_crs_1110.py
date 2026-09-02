"""#1110: attribute_filter / central_feature must preserve FeatureCollection CRS.

The two adapters were the last #765 stragglers handing down
``data.get("features", [])`` — a bare list. ``to_feature_collection()`` then
rebuilt a CRS-less FeatureCollection, so declared projected CRS (3857/4490)
silently fell back to EPSG:4326: metres read as degrees, wrong centers.

Walks the TOOL ROUTE (ToolRegistry.dispatch) so the adapter boundary is
exercised, mirroring tests/unit/test_advanced_spatial_audit_764_765.py.
"""
import pytest
from pyproj import Transformer

from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


_TR_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def _points_fc_wgs84(n: int = 4):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"idx": i, "cat": "a" if i % 2 == 0 else "b"},
                "geometry": {"type": "Point", "coordinates": [116.0 + 0.001 * i, 39.9]},
            }
            for i in range(n)
        ],
    }


def _to_3857(fc: dict) -> dict:
    out = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
        "features": [],
    }
    for f in fc["features"]:
        x, y = f["geometry"]["coordinates"]
        out["features"].append(
            {
                "type": "Feature",
                "properties": dict(f["properties"]),
                "geometry": {"type": "Point", "coordinates": list(_TR_3857.transform(x, y))},
            }
        )
    return out


def _to_4490(fc: dict) -> dict:
    # CGCS2000 geographic — numerically ~identical to WGS84 for test purposes,
    # the point is the declared member must survive untouched.
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4490"}},
        "features": [dict(f) for f in fc["features"]],
    }


def _declared_crs(data) -> str | None:
    if not isinstance(data, dict):
        return None
    crs = data.get("crs")
    if isinstance(crs, dict):
        return crs.get("properties", {}).get("name")
    return crs


# ─── attribute_filter ───────────────────────────────────────────────────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_4326_passthrough(registry):
    fc = _points_fc_wgs84()
    result = await registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "cat == 'a'"}
    )
    assert result.get("success") is True, result
    data = result["data"]
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 2
    # WGS84 must NOT emit a crs member (RFC 7946 default, declare_crs contract)
    assert _declared_crs(data) is None


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_3857_preserves_crs_and_coordinates(registry):
    fc = _to_3857(_points_fc_wgs84())
    result = await registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "cat == 'a'"}
    )
    assert result.get("success") is True, result
    data = result["data"]
    assert len(data["features"]) == 2
    assert _declared_crs(data) == "EPSG:3857", (
        f"declared CRS lost at tool boundary: {data.get('crs')}"
    )
    # Metre-domain coordinates must pass through unchanged (no degree misread).
    kept = [f for f in fc["features"] if f["properties"]["cat"] == "a"]
    for f_in, f_out in zip(kept, data["features"]):
        assert f_out["geometry"]["coordinates"] == pytest.approx(
            f_in["geometry"]["coordinates"], abs=1e-6
        )


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_4490_preserves_crs(registry):
    fc = _to_4490(_points_fc_wgs84())
    result = await registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "cat == 'b'"}
    )
    assert result.get("success") is True, result
    data = result["data"]
    assert len(data["features"]) == 2
    assert _declared_crs(data) == "EPSG:4490"


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_attribute_filter_string_crs_member(registry):
    """String-form crs member (legacy writer) must also survive."""
    fc = _to_3857(_points_fc_wgs84())
    fc["crs"] = "EPSG:3857"
    result = await registry.dispatch(
        "attribute_filter", {"geojson": fc, "query": "idx >= 0"}
    )
    assert result.get("success") is True, result
    assert _declared_crs(result["data"]) == "EPSG:3857"


# ─── central_feature ────────────────────────────────────────────────────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_central_feature_4326_mean_center(registry):
    result = await registry.dispatch(
        "central_feature", {"geojson": _points_fc_wgs84(4), "method": "mean_center"}
    )
    assert result.get("success") is True, result
    geom = result["data"]["geometry"]["coordinates"]
    # Mean of 116.0..116.003 / 39.9 — in WGS84 degrees.
    assert geom[0] == pytest.approx(116.0015, abs=1e-4)
    assert geom[1] == pytest.approx(39.9, abs=1e-4)


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_central_feature_3857_not_misread_as_degrees(registry):
    """With CRS honored, 3857 metres are reprojected to UTM correctly and the
    output center lands back near the true WGS84 mean — NOT near (0,0)-ish
    garbage from metres-as-degrees."""
    fc = _to_3857(_points_fc_wgs84(4))
    result = await registry.dispatch(
        "central_feature", {"geojson": fc, "method": "mean_center"}
    )
    assert result.get("success") is True, result
    geom = result["data"]["geometry"]["coordinates"]
    assert geom[0] == pytest.approx(116.0015, abs=1e-3), (
        f"3857 input misread: center at {geom} — CRS stripped at boundary?"
    )
    assert geom[1] == pytest.approx(39.9, abs=1e-3)


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_central_feature_3857_central_feature_method(registry):
    fc = _to_3857(_points_fc_wgs84(5))
    result = await registry.dispatch(
        "central_feature", {"geojson": fc, "method": "central_feature"}
    )
    assert result.get("success") is True, result
    geom = result["data"]["geometry"]["coordinates"]
    # The central of 5 evenly spaced points on a line is the middle one (idx 2).
    assert geom[0] == pytest.approx(116.002, abs=1e-3)
    assert geom[1] == pytest.approx(39.9, abs=1e-3)


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_central_feature_invalid_crs_does_not_crash(registry):
    fc = _points_fc_wgs84()
    fc["crs"] = "EPSG:99999"
    result = await registry.dispatch(
        "central_feature", {"geojson": fc, "method": "mean_center"}
    )
    # Must not raise through dispatch; a graceful failure with correction info
    # is acceptable, a 4326 fallback success is acceptable too.
    assert isinstance(result, dict)
    assert "success" in result
