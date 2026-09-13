"""ads-v1 local asset adapter tests (DS1, ADR-0171).

Real round-trips against temporary real files (GPKG via pyogrio, GeoJSON,
sqlite, GeoTIFF via rasterio): the adapters must read what the file actually
contains and fail typed when it is missing — never fabricate.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
from app.services.data_fabric.adapters.cog_adapter import COGAdapter
from app.services.data_fabric.adapters.geopackage_adapter import GeoPackageAdapter
from app.services.data_fabric.adapters.local_file_adapter import LocalFileAdapter


def _profile(source_type: str, endpoint: str = "", options: dict | None = None) -> ConnectionProfile:
    return ConnectionProfile(
        source_type=source_type, endpoint_url=endpoint, name="ads_local_test",
        options=options or {},
    )


@pytest.fixture
def gpkg_path(tmp_path: Path) -> Path:
    """A real single-layer GeoPackage with two points."""
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point

    gdf = gpd.GeoDataFrame(
        pd.DataFrame({"name": ["a", "b"], "value": [1, 2]}),
        geometry=[Point(116.4, 39.9), Point(121.4, 31.2)],
        crs="EPSG:4326",
    )
    path = tmp_path / "themes" / "pois.gpkg"
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GPKG", layer="pois")
    return path


@pytest.fixture
def geojson_path(tmp_path: Path) -> Path:
    path = tmp_path / "export.geojson"
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "1", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
             "properties": {"name": "x", "value": 10}},
            {"type": "Feature", "id": "2", "geometry": {"type": "Point", "coordinates": [5.0, 5.0]},
             "properties": {"name": "y", "value": 20}},
        ],
    }), encoding="utf-8")
    return path


@pytest.fixture
def sqlite_path(tmp_path: Path) -> Path:
    path = tmp_path / "yearbook.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE county_stats (county TEXT, year INTEGER, gdp REAL)")
    conn.executemany("INSERT INTO county_stats VALUES (?,?,?)", [("a", 2020, 100.0), ("b", 2021, 200.0)])
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def cog_path(tmp_path: Path) -> Path:
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "dem.tif"
    profile = {
        "driver": "GTiff", "width": 32, "height": 32, "count": 1,
        "dtype": "float32", "crs": "EPSG:4326",
        "transform": from_origin(100.0, 50.0, 0.01, 0.01),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.zeros((1, 32, 32), dtype="float32"))
    return path


# ── GeoPackage ───────────────────────────────────────────────────────────────


def test_geopackage_roundtrip(gpkg_path):
    adapter = GeoPackageAdapter(_profile("geopackage", options={"base_dir": str(gpkg_path.parent)}))
    assert adapter.probe() is True
    datasets = adapter.list_datasets()
    assert any(d["id"] == "pois" for d in datasets)
    desc = adapter.describe("pois")
    assert desc.feature_count == 2
    assert {"name": "name", "type": "object"} in [dict(f) for f in desc.fields] or \
        any(f["name"] == "name" for f in desc.fields)
    assert desc.crs and "4326" in desc.crs


def test_geopackage_bbox_query(gpkg_path):
    adapter = GeoPackageAdapter(_profile("geopackage", options={"base_dir": str(gpkg_path.parent)}))
    result = adapter.query("pois", QuerySpec(bbox=[116.0, 39.0, 117.0, 40.0], limit=10))
    assert len(result.features) == 1
    assert result.metadata["source"] == "local_gpkg"


def test_geopackage_missing_is_typed_unreachable(tmp_path):
    adapter = GeoPackageAdapter(_profile("geopackage", options={"base_dir": str(tmp_path / "void")}))
    assert adapter.probe() is False
    from app.services.data_fabric.errors import SourceUnreachableError

    with pytest.raises(SourceUnreachableError):
        adapter.list_datasets()


def test_geopackage_unresolved_env_is_unavailable_not_empty():
    adapter = GeoPackageAdapter(_profile("geopackage", options={"base_dir": "${LOCAL_GEODATA_DIR}"}))
    from app.services.data_fabric.errors import SourceUnreachableError

    with pytest.raises(SourceUnreachableError):
        adapter.list_datasets()


# ── Local file ───────────────────────────────────────────────────────────────


def test_local_file_geojson_roundtrip(geojson_path):
    adapter = LocalFileAdapter(_profile("local_file", options={"base_dir": str(geojson_path)}))
    assert adapter.probe() is True
    desc = adapter.describe("export")
    assert desc.feature_count == 2
    result = adapter.query("export", QuerySpec(bbox=[115.0, 38.0, 117.0, 40.0], limit=5))
    assert len(result.features) == 1
    assert result.features[0]["properties"]["name"] == "x"


def test_local_file_sqlite_roundtrip(sqlite_path):
    adapter = LocalFileAdapter(_profile("local_file", options={"base_dir": str(sqlite_path)}))
    datasets = adapter.list_datasets()
    assert any(d["id"] == "yearbook:county_stats" for d in datasets)
    result = adapter.query(
        "yearbook:county_stats",
        QuerySpec(columns=["county", "gdp"], limit=10, offset=0),
    )
    assert len(result.features) == 2
    assert result.features[0]["county"] == "a"


def test_local_file_missing_is_typed(tmp_path):
    adapter = LocalFileAdapter(_profile("local_file", options={"base_dir": str(tmp_path / "nope.geojson")}))
    from app.services.data_fabric.errors import SourceUnreachableError

    with pytest.raises(SourceUnreachableError):
        adapter.list_datasets()


def test_local_file_rejects_unknown_format(tmp_path):
    p = tmp_path / "asset.parquet"
    p.write_bytes(b"PAR1")
    adapter = LocalFileAdapter(_profile("local_file", options={"base_dir": str(p)}))
    from app.services.data_fabric.errors import InvalidQueryError

    with pytest.raises(InvalidQueryError):
        adapter.capabilities()


# ── COG ──────────────────────────────────────────────────────────────────────


def test_cog_describe_and_query_unsupported(cog_path):
    adapter = COGAdapter(_profile("cog", options={"base_dir": str(cog_path)}))
    assert adapter.probe() is True
    desc = adapter.describe("dem")
    assert desc.data_type == "raster"
    assert desc.bbox == pytest.approx([100.0, 49.68, 100.32, 50.0])
    from app.services.data_fabric.errors import QueryUnsupportedError

    with pytest.raises(QueryUnsupportedError):
        adapter.query("dem", QuerySpec(limit=1))
