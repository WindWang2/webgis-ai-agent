"""SEC-01 regression: local file adapters must enforce declared allowed roots.

Deep-review swarm 2026-09-19 (SEC-01): ``local_file``/``cog``/``geopackage``
adapters called ``resolve_safe_local_path`` without the configured
``DATA_FABRIC_LOCAL_FILE_ROOTS``/``_MAX_BYTES``, so an authenticated account
could register a source pointing at any readable server file (e.g. the
platform sqlite). The geopackage directory branch skipped the guard entirely.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters import (
    cog_adapter as cog_mod,
    geopackage_adapter as gpkg_mod,
    local_file_adapter as lf_mod,
)
from app.services.data_fabric.adapters.cog_adapter import COGAdapter
from app.services.data_fabric.adapters.geopackage_adapter import GeoPackageAdapter
from app.services.data_fabric.adapters.local_file_adapter import LocalFileAdapter
from app.services.data_fabric.errors import SecurityBlockedError


def _profile(source_type: str, options: dict) -> ConnectionProfile:
    return ConnectionProfile(source_type=source_type, name="sec01", options=options)


def _pin_roots(monkeypatch, module, root: Path) -> None:
    monkeypatch.setattr(module, "_local_file_roots_from_settings", lambda: [str(root)])
    monkeypatch.setattr(module, "_local_file_max_bytes_from_settings", lambda: 1 << 30)


def _geojson(path: Path, name: str = "x") -> Path:
    path.write_text(
        json.dumps({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                 "properties": {"name": name}},
            ],
        }),
        encoding="utf-8",
    )
    return path


# ── local_file ────────────────────────────────────────────────────────────────


def test_local_file_outside_root_rejected(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    secret = _geojson(tmp_path / "secret.geojson")
    _pin_roots(monkeypatch, lf_mod, root)

    adapter = LocalFileAdapter(_profile("local_file", {"base_dir": str(secret)}))
    with pytest.raises(SecurityBlockedError):
        adapter.list_datasets()


def test_local_file_inside_root_accepted(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    asset = _geojson(root / "ok.geojson")
    _pin_roots(monkeypatch, lf_mod, root)

    adapter = LocalFileAdapter(_profile("local_file", {"base_dir": str(asset)}))
    assert adapter.probe() is True
    datasets = adapter.list_datasets()
    assert datasets and datasets[0]["name"] == "ok"
    assert adapter.describe("ok").feature_count == 1


# ── COG ───────────────────────────────────────────────────────────────────────


def test_cog_outside_root_rejected(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside.tif"
    outside.write_bytes(b"\x00")
    _pin_roots(monkeypatch, cog_mod, root)

    adapter = COGAdapter(_profile("cog", {"base_dir": str(outside)}))
    with pytest.raises(SecurityBlockedError):
        adapter.describe("outside")


def test_cog_inside_root_accepted(monkeypatch, tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from rasterio.transform import from_origin

    root = tmp_path / "data"
    root.mkdir()
    path = root / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=8, height=8, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(100.0, 50.0, 0.1, 0.1),
    ) as dst:
        dst.write(np.zeros((1, 8, 8), dtype="float32"))
    _pin_roots(monkeypatch, cog_mod, root)

    adapter = COGAdapter(_profile("cog", {"base_dir": str(path)}))
    assert adapter.probe() is True
    assert adapter.describe("dem").data_type == "raster"


# ── GeoPackage ────────────────────────────────────────────────────────────────


def test_geopackage_dir_glob_outside_root_rejected(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "pois.gpkg").write_bytes(b"SQLite format 3\x00")
    _pin_roots(monkeypatch, gpkg_mod, root)

    adapter = GeoPackageAdapter(_profile("geopackage", {"base_dir": str(outside_dir)}))
    with pytest.raises(SecurityBlockedError):
        adapter.list_datasets()


def test_geopackage_direct_outside_root_rejected(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "pois.gpkg"
    outside.write_bytes(b"SQLite format 3\x00")
    _pin_roots(monkeypatch, gpkg_mod, root)

    adapter = GeoPackageAdapter(_profile("geopackage", {"base_dir": str(outside)}))
    with pytest.raises(SecurityBlockedError):
        adapter.list_datasets()


def test_geopackage_inside_root_accepted(monkeypatch, tmp_path):
    gpd = pytest.importorskip("geopandas")
    import pandas as pd
    from shapely.geometry import Point

    root = tmp_path / "data"
    root.mkdir()
    path = root / "pois.gpkg"
    gpd.GeoDataFrame(
        pd.DataFrame({"name": ["a"]}),
        geometry=[Point(116.4, 39.9)],
        crs="EPSG:4326",
    ).to_file(path, driver="GPKG", layer="pois")
    _pin_roots(monkeypatch, gpkg_mod, root)

    adapter = GeoPackageAdapter(_profile("geopackage", {"base_dir": str(root)}))
    assert adapter.probe() is True
    assert any(d["id"] == "pois" for d in adapter.list_datasets())
