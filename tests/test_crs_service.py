"""CRS conversion service tests — RED phase.

Covers:
1. General EPSG-to-EPSG reprojection tool
2. Original CRS preservation on upload
3. Datum-shift code dedup (single source of truth)
"""
import json
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from app.services.data_parser import ParseError, _get_format, parse_vector, VECTOR_FORMATS


# ─── 1. General EPSG reprojection tool ──────────────────────────


class TestEPSGReprojection:
    """Test the reproject_coordinates tool (general EPSG-to-EPSG)."""

    def test_reproject_epsg4326_to_utm(self):
        """Reproject a point from EPSG:4326 to UTM Zone 50N (EPSG:32650)."""
        from app.tools.coord_transform import register_epsg_transform_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_epsg_transform_tools(reg)

        tool_fn = reg._tools["reproject_coordinates"]
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                "properties": {"name": "Beijing"},
            }],
        }
        result = tool_fn(geojson=geojson, from_epsg="EPSG:4326", to_epsg="EPSG:32650")
        assert result["success"] is True
        coords = result["data"]["features"][0]["geometry"]["coordinates"]
        # UTM Zone 50N for Beijing: x ~448000, y ~4418000
        assert 400000 < coords[0] < 500000
        assert 4400000 < coords[1] < 4500000
        assert result["metadata"]["from_epsg"] == "EPSG:4326"
        assert result["metadata"]["to_epsg"] == "EPSG:32650"

    def test_reproject_roundtrip_preserves_coordinates(self):
        """4326 → 32650 → 4326 should preserve original coords within tolerance."""
        from app.tools.coord_transform import register_epsg_transform_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_epsg_transform_tools(reg)
        tool_fn = reg._tools["reproject_coordinates"]

        original = {
            "type": "Point",
            "coordinates": [121.47, 31.23],
        }
        # Forward
        fwd = tool_fn(geojson=original, from_epsg="EPSG:4326", to_epsg="EPSG:32651")
        assert fwd["success"]
        # Backward
        bwd = tool_fn(geojson=fwd["data"], from_epsg="EPSG:32651", to_epsg="EPSG:4326")
        assert bwd["success"]
        lng, lat = bwd["data"]["coordinates"]
        assert abs(lng - 121.47) < 0.001
        assert abs(lat - 31.23) < 0.001

    def test_rejects_invalid_epsg(self):
        from app.tools.coord_transform import register_epsg_transform_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_epsg_transform_tools(reg)
        tool_fn = reg._tools["reproject_coordinates"]

        result = tool_fn(geojson={"type": "Point", "coordinates": [0, 0]},
                         from_epsg="EPSG:99999", to_epsg="EPSG:4326")
        assert result["success"] is False
        assert "不支持的" in result.get("error", "") or "invalid" in result.get("error", "").lower()

    def test_same_epsg_returns_unchanged(self):
        from app.tools.coord_transform import register_epsg_transform_tools
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        register_epsg_transform_tools(reg)
        tool_fn = reg._tools["reproject_coordinates"]

        geojson = {"type": "Point", "coordinates": [1.0, 2.0]}
        result = tool_fn(geojson=geojson, from_epsg="EPSG:4326", to_epsg="EPSG:4326")
        assert result["success"]
        assert result["data"]["coordinates"] == [1.0, 2.0]


# ─── 2. Original CRS preservation on upload ─────────────────────


class TestOriginalCRSPreservation:
    """Upload pipeline should store original CRS before converting to 4326."""

    def test_preserves_original_crs_from_shapefile(self, tmp_path):
        """A shapefile with a non-4326 CRS should preserve original_crs in result."""
        # Create a GeoDataFrame in EPSG:32650 (UTM Zone 50N)
        gdf = gpd.GeoDataFrame(
            {"name": ["test"]},
            geometry=[Point(448000, 4418000)],
            crs="EPSG:32650",
        )
        shp_dir = tmp_path / "shp_src"
        shp_dir.mkdir()
        gdf.to_file(shp_dir / "test.shp", engine="pyogrio")

        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for ext in [".shp", ".dbf", ".shx", ".prj"]:
                p = shp_dir / f"test{ext}"
                if p.exists():
                    zf.write(p, f"test{ext}")

        result = parse_vector(zip_path, tmp_path, "test-id")
        assert result["crs"] == "EPSG:4326"  # data is normalized
        assert result.get("original_crs") == "EPSG:32650"  # original preserved

    def test_geojson_without_crs_defaults_to_4326(self, tmp_path):
        """A GeoJSON with no CRS should not set original_crs."""
        geojson_path = tmp_path / "test.geojson"
        geojson_path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}],
        }))
        result = parse_vector(geojson_path, tmp_path, "test-id")
        assert result["crs"] == "EPSG:4326"
        # original_crs should be absent or None when input was already 4326/unknown
        assert result.get("original_crs") in (None, "EPSG:4326")


# ─── 3. Datum-shift code dedup ──────────────────────────────────


class TestDatumShiftDedup:
    """Verify the canonical datum-shift functions come from one source."""

    def test_utils_coord_transform_is_canonical(self):
        from app.utils.coord_transform import wgs84_to_gcj02, gcj02_to_wgs84
        # Should be importable and functional
        lng, lat = wgs84_to_gcj02(116.4, 39.9)
        assert isinstance(lng, float) and isinstance(lat, float)
        # Roundtrip
        lng2, lat2 = gcj02_to_wgs84(lng, lat)
        assert abs(lng2 - 116.4) < 0.001
        assert abs(lat2 - 39.9) < 0.001

    def test_tools_import_from_utils_not_core(self):
        """coord_transform tool should import from app.utils.coord_transform (canonical)."""
        import inspect
        import app.tools.coord_transform as ct_module
        source = inspect.getsource(ct_module)
        assert "from app.utils.coord_transform import" in source, \
            "Tool should import from app.utils (canonical), not app.lib.geo_processor.core"
