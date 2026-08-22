"""Canonical adapter registry tests.

Verifies the registry is the single source of truth: all real adapters are
reachable, aliases resolve, capabilities are declared, and unknown types raise
UnsupportedSourceError (never a silent mock fallback).
"""
import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters import (
    ArcGISAdapter,
    FlatGeobufAdapter,
    GeoParquetAdapter,
    OGCAPIAdapter,
    PMTilesAdapter,
    PostGISAdapter,
    S3StorageAdapter,
    STACAdapter,
    WFSAdapter,
    WMSWMTSAdapter,
)
from app.services.data_fabric.connection_manager import GenericDataSourceAdapter
from app.services.data_fabric.errors import UnsupportedSourceError
from app.services.data_fabric.registry import get_registry


def test_all_ten_real_adapters_registered_and_reachable():
    """Every concrete adapter must resolve from the canonical registry."""
    reg = get_registry()
    expected = {
        "postgis": PostGISAdapter,
        "ogc_api": OGCAPIAdapter,
        "wfs": WFSAdapter,
        "wms": WMSWMTSAdapter,
        "arcgis": ArcGISAdapter,
        "stac": STACAdapter,
        "geoparquet": GeoParquetAdapter,
        "flatgeobuf": FlatGeobufAdapter,
        "pmtiles": PMTilesAdapter,
        "s3": S3StorageAdapter,
    }
    for canonical, cls in expected.items():
        spec = reg.resolve(canonical)
        assert spec.adapter_cls is cls, f"{canonical} -> {spec.adapter_cls}, expected {cls}"


def test_aliases_resolve_to_same_canonical():
    reg = get_registry()
    for alias in ("postgres", "postgresql"):
        assert reg.resolve(alias).canonical == "postgis"
    assert reg.resolve("parquet").canonical == "geoparquet"
    assert reg.resolve("fgb").canonical == "flatgeobuf"
    assert reg.resolve("wmts").canonical == "wms"
    assert reg.resolve("featureserver").canonical == "arcgis"
    assert reg.resolve("ogc").canonical == "ogc_api"


def test_unknown_source_type_raises_not_mock_fallback():
    """The P0 contract: unknown types raise, never produce mock data."""
    reg = get_registry()
    with pytest.raises(UnsupportedSourceError) as ei:
        reg.resolve("unknown_custom")
    assert "supported" in str(ei.value.details)  # actionable hint


def test_build_adapter_unknown_raises():
    with pytest.raises(UnsupportedSourceError):
        get_registry().build_adapter(ConnectionProfile(id="x", source_type="nope"))


def test_demo_adapter_is_explicit_opt_in():
    """generic/mock/sample resolve to the demo adapter only when explicitly asked.

    #767: the 'geojson' alias was removed — it routed requests for real remote
    GeoJSON URLs to the synthetic-feature demo adapter. It must now raise like
    any other unregistered type (previously it resolved to 'generic').
    """
    reg = get_registry()
    assert reg.resolve("generic").adapter_cls is GenericDataSourceAdapter
    assert reg.resolve("mock").canonical == "generic"
    assert reg.resolve("sample").canonical == "generic"
    assert reg.resolve("generic").is_demo is True
    with pytest.raises(UnsupportedSourceError):
        reg.resolve("geojson")


def test_geojson_alias_removed_767():
    """#767: source_type='geojson' must NOT build the demo adapter — a real
    remote GeoJSON URL would be 'served' synthetic Beijing features."""
    with pytest.raises(UnsupportedSourceError) as ei:
        get_registry().build_adapter(
            ConnectionProfile(id="x", source_type="geojson", url="https://example.org/parks.geojson")
        )
    assert "supported" in str(ei.value.details)  # actionable hint
    assert ei.value.code == "UNSUPPORTED_SOURCE"


def test_raster_tile_capability_flags():
    reg = get_registry()
    assert reg.resolve("wms").is_raster_tile is True
    assert reg.resolve("pmtiles").is_raster_tile is True
    assert reg.resolve("postgis").is_raster_tile is False


def test_pushdown_capability_flags_declared():
    reg = get_registry()
    pg = reg.resolve("postgis")
    assert pg.supports_bbox and pg.supports_filter and pg.supports_pagination
    ogc = reg.resolve("ogc_api")
    assert ogc.supports_bbox and ogc.supports_datetime and ogc.supports_pagination


def test_supported_source_types_complete():
    types = get_registry().supported_source_types()
    for required in (
        "postgis", "ogc_api", "wfs", "wms", "arcgis", "stac",
        "geoparquet", "flatgeobuf", "pmtiles", "s3", "generic",
    ):
        assert required in types, f"{required} missing from registry"
