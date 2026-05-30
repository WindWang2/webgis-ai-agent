"""Shapefile (.zip) upload validation and parsing tests.

RED phase: these should fail until data_parser.py is updated.
"""
import json
import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.services.data_parser import (
    VECTOR_FORMATS,
    ParseError,
    _get_format,
    parse_vector,
)


# ─── helpers ────────────────────────────────────────────────────


def _make_shapefile_zip(
    dest: Path,
    *,
    include_shp: bool = True,
    include_dbf: bool = True,
    include_shx: bool = True,
    extra_files: dict | None = None,
    shp_content: bytes = b"",
    dbf_content: bytes = b"\x03" + b"\x00" * 31 + b"\r",
    shx_content: bytes = b"\x00" * 100,
    compression: int = zipfile.ZIP_STORED,
) -> Path:
    """Build a zip with shapefile components. Returns path to the zip."""
    with zipfile.ZipFile(dest, "w", compression=compression) as zf:
        if include_shp:
            zf.writestr("roads.shp", shp_content)
        if include_dbf:
            zf.writestr("roads.dbf", dbf_content)
        if include_shx:
            zf.writestr("roads.shx", shx_content)
        if extra_files:
            for name, data in extra_files.items():
                zf.writestr(name, data)
    return dest


# ─── format registry ────────────────────────────────────────────


class TestFormatRegistry:
    def test_zip_in_vector_formats(self):
        assert ".zip" in VECTOR_FORMATS

    def test_get_format_zip_returns_shapefile(self):
        file_type, fmt = _get_format(".zip")
        assert file_type == "vector"
        assert fmt == "shapefile"


# ─── zip validation ─────────────────────────────────────────────


class TestZipValidation:
    def test_rejects_zip_without_shp(self, tmp_path):
        zip_path = _make_shapefile_zip(tmp_path / "test.zip", include_shp=False)
        with pytest.raises(ParseError, match="缺少.*shp"):
            parse_vector(zip_path, tmp_path, "test-id")

    def test_rejects_zip_without_dbf(self, tmp_path):
        zip_path = _make_shapefile_zip(tmp_path / "test.zip", include_dbf=False)
        with pytest.raises(ParseError, match="缺少.*dbf"):
            parse_vector(zip_path, tmp_path, "test-id")

    def test_rejects_zip_with_path_traversal(self, tmp_path):
        zip_path = _make_shapefile_zip(
            tmp_path / "evil.zip",
            extra_files={"../../etc/passwd": "root:x:0:0"},
        )
        with pytest.raises(ParseError, match="路径"):
            parse_vector(zip_path, tmp_path, "test-id")

    def test_rejects_zip_bomb(self, tmp_path):
        """A zip whose uncompressed size exceeds MAX_VECTOR_SIZE should be rejected."""
        from app.services.data_parser import MAX_VECTOR_SIZE

        # Create a zip with one huge entry that exceeds the limit
        zip_path = tmp_path / "bomb.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            # Write a compressed entry that decompresses to > MAX_VECTOR_SIZE
            zf.writestr("roads.shp", b"\x00" * (MAX_VECTOR_SIZE + 1))
            zf.writestr("roads.dbf", b"\x00" * 100)
            zf.writestr("roads.shx", b"\x00" * 100)
        with pytest.raises(ParseError, match="大小|size|超过"):
            parse_vector(zip_path, tmp_path, "test-id")


# ─── valid shapefile parsing ────────────────────────────────────


class TestValidShapefile:
    def test_parses_valid_shapefile_zip(self, tmp_path):
        """End-to-end: create a minimal valid shapefile zip and parse it."""
        import geopandas as gpd
        from shapely.geometry import Point

        # Build a real shapefile zip via geopandas
        gdf = gpd.GeoDataFrame(
            {"name": ["foo", "bar"]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326",
        )
        shp_dir = tmp_path / "shp_src"
        shp_dir.mkdir()
        shp_path = shp_dir / "test.shp"
        gdf.to_file(shp_path, engine="pyogrio")

        # Zip the shapefile components
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for ext in [".shp", ".dbf", ".shx", ".prj", ".cpg"]:
                p = shp_dir / f"test{ext}"
                if p.exists():
                    zf.write(p, f"test{ext}")

        result = parse_vector(zip_path, tmp_path, "test-id")
        assert result["file_type"] == "vector"
        assert result["format"] == "shapefile"
        assert result["feature_count"] == 2
        assert result["crs"] == "EPSG:4326"
        assert "output_path" in result
        assert Path(result["output_path"]).exists()

    def test_output_is_valid_geojson(self, tmp_path):
        """The parsed output should be valid GeoJSON."""
        import geopandas as gpd
        from shapely.geometry import Point

        gdf = gpd.GeoDataFrame(
            {"val": [42]},
            geometry=[Point(116.4, 39.9)],
            crs="EPSG:4326",
        )
        shp_dir = tmp_path / "shp_src"
        shp_dir.mkdir()
        gdf.to_file(shp_dir / "data.shp", engine="pyogrio")

        zip_path = tmp_path / "data.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for ext in [".shp", ".dbf", ".shx", ".prj"]:
                p = shp_dir / f"data{ext}"
                if p.exists():
                    zf.write(p, f"data{ext}")

        result = parse_vector(zip_path, tmp_path, "test-id")
        geojson_path = Path(result["output_path"])
        with open(geojson_path) as f:
            geojson = json.load(f)
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) == 1
