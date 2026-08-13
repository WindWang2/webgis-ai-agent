"""Canonical adapter registry — single source of truth for source_type → adapter.

Replaces the six divergent source-type enumerations that existed across the
codebase (the manager ``if/elif`` chain, the connection_manager ``adapter_map``,
the route/tool docstrings, ``mapspec_source``…). ``DataFabricManager``,
``DataFabricConnectionManager``, the tool layer and routes ALL resolve through
``resolve_adapter_spec`` / ``build_adapter``.

Contract (Data Fabric V3 / ADR-0053):
- A source type not in the registry raises ``UnsupportedSourceError`` — it is
  NEVER silently mapped to mock data. This kills the "fabricated features for
  unknown source types" P0.
- Each spec carries explicit capability flags so the query planner / materializer
  can negotiate pushdown vs local fallback (Section 26) without re-deriving them.
- ``GenericDataSourceAdapter`` is registered explicitly as the ``generic`` demo
  adapter (aliases ``geojson``/``mock``/``sample``). It is opt-in, not a fallback.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Type

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import UnsupportedSourceError


@dataclass(frozen=True)
class AdapterSpec:
    """A registered source type: aliases, adapter class, capability flags."""

    canonical: str
    adapter_cls: Optional[Type[GeospatialDataSourceAdapter]]
    aliases: Tuple[str, ...] = ()
    # Pushdown capability negotiation (Section 26). False means the materializer
    # must NOT assume the remote honors that clause.
    supports_bbox: bool = False
    supports_filter: bool = False
    supports_pagination: bool = False
    supports_datetime: bool = False
    supports_projection: bool = False
    # raster/tile services cannot answer vector feature queries.
    is_raster_tile: bool = False
    # Production-capable vs explicit demo/sample (GenericDataSourceAdapter).
    is_demo: bool = False
    notes: str = ""

    @property
    def names(self) -> Tuple[str, ...]:
        return (self.canonical,) + self.aliases


class AdapterRegistry:
    """Append-only registry keyed by lowercased canonical name + aliases."""

    def __init__(self) -> None:
        self._by_name: Dict[str, AdapterSpec] = {}
        self._by_canonical: Dict[str, AdapterSpec] = {}

    def register(self, spec: AdapterSpec) -> None:
        for name in spec.names:
            key = name.lower().strip()
            existing = self._by_name.get(key)
            if existing and existing.canonical != spec.canonical:
                raise ValueError(
                    f"adapter alias '{name}' already registered to "
                    f"'{existing.canonical}', cannot rebind to '{spec.canonical}'"
                )
            self._by_name[key] = spec
        self._by_canonical[spec.canonical] = spec

    def resolve(self, source_type: Optional[str]) -> AdapterSpec:
        key = (source_type or "").lower().strip()
        spec = self._by_name.get(key)
        if spec is None:
            raise UnsupportedSourceError(
                f"unsupported data source type '{source_type}'",
                details={
                    "source_type": source_type,
                    "supported": self.supported_source_types(),
                },
            )
        return spec

    def is_supported(self, source_type: Optional[str]) -> bool:
        return bool((source_type or "").lower().strip() in self._by_name)

    def supported_source_types(self) -> List[str]:
        return sorted(self._by_canonical.keys())

    def build_adapter(self, profile: ConnectionProfile) -> GeospatialDataSourceAdapter:
        spec = self.resolve(profile.source_type)
        if spec.adapter_cls is None:
            raise UnsupportedSourceError(
                f"source type '{profile.source_type}' has no adapter implementation",
                details={"source_type": profile.source_type},
            )
        return spec.adapter_cls(profile)


# ── Registration table (the one source of truth) ────────────────────────────
def _build_registry() -> AdapterRegistry:
    # Imported lazily to avoid import cycles (adapters import the package).
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

    reg = AdapterRegistry()
    for spec in [
        AdapterSpec("postgis", PostGISAdapter,
                    aliases=("postgres", "postgresql"),
                    supports_bbox=True, supports_filter=True,
                    supports_pagination=True, supports_projection=True),
        AdapterSpec("ogc_api", OGCAPIAdapter,
                    aliases=("ogc_api_features", "ogc", "ogcapi"),
                    supports_bbox=True, supports_filter=True,
                    supports_pagination=True, supports_datetime=True,
                    supports_projection=True),
        AdapterSpec("wfs", WFSAdapter,
                    aliases=("wfs1", "wfs2"),
                    supports_bbox=True),
        AdapterSpec("wms", WMSWMTSAdapter,
                    aliases=("wmts", "wms_wmts"),
                    is_raster_tile=True,
                    notes="raster/tile service; no vector feature query"),
        AdapterSpec("arcgis", ArcGISAdapter,
                    aliases=("arcgis_rest", "featureserver", "mapserver"),
                    supports_bbox=True, supports_filter=True,
                    supports_pagination=True, supports_projection=True),
        AdapterSpec("stac", STACAdapter,
                    supports_bbox=True, supports_datetime=True,
                    notes="metadata/search; asset materialization is separate"),
        AdapterSpec("geoparquet", GeoParquetAdapter,
                    aliases=("parquet",),
                    supports_bbox=True, supports_projection=True),
        AdapterSpec("flatgeobuf", FlatGeobufAdapter,
                    aliases=("fgb",),
                    supports_bbox=True),
        AdapterSpec("pmtiles", PMTilesAdapter,
                    is_raster_tile=True,
                    notes="external tile archive; metadata-only in catalog"),
        AdapterSpec("s3", S3StorageAdapter,
                    aliases=("minio", "object_storage"),
                    notes="object storage seam; metadata-only in catalog"),
        # Explicit demo/sample adapter. Opt-in only — the factory never falls
        # back to this for an unregistered source type.
        AdapterSpec("generic", GenericDataSourceAdapter,
                    aliases=("geojson", "mock", "sample"),
                    supports_bbox=True, supports_filter=True,
                    supports_pagination=True, supports_projection=True,
                    is_demo=True,
                    notes="in-memory demo/sample adapter; generates synthetic features"),
    ]:
        reg.register(spec)
    return reg


_REGISTRY: Optional[AdapterRegistry] = None


def get_registry() -> AdapterRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def resolve_adapter_spec(source_type: Optional[str]) -> AdapterSpec:
    """Module-level convenience accessor."""
    return get_registry().resolve(source_type)


def build_adapter(profile: ConnectionProfile) -> GeospatialDataSourceAdapter:
    """Module-level convenience accessor."""
    return get_registry().build_adapter(profile)


__all__ = [
    "AdapterSpec",
    "AdapterRegistry",
    "get_registry",
    "resolve_adapter_spec",
    "build_adapter",
]
