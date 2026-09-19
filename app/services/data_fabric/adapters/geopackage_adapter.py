"""GeoPackage Data Source Adapter (ads-v1 DS1, ADR-0171) — local GPKG assets.

Serves declared local GeoPackage files (``local_osm`` themes, ``local_poi``,
any future GPKG asset) through the unified adapter seam. Honest semantics:

- missing file / unreadable GPKG → typed ``SourceUnreachableError`` /
  ``SourceBadResponseError`` — **never** an empty-but-successful result, never
  fabricated features (registry.py:78-86 principle; geojson-alias #767 lesson);
- schema comes from ``pyogrio.read_info`` (zero-row read: fields, geometry
  type, CRS, feature count);
- bbox pushdown via ``pyogrio`` bbox filtering, projection via column
  selection, pagination via offset/limit on the materialized subset;
- path access is funnelled through ``resolve_safe_local_path`` (declared roots
  only, traversal blocked).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.data_fabric_schema import DataFabricHealth, QueryResult
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    InvalidQueryError,
    SecurityBlockedError,
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.security import (
    DataFabricSecurityError,
    _local_file_max_bytes_from_settings,
    _local_file_roots_from_settings,
    resolve_safe_local_path,
)

logger = logging.getLogger(__name__)

_PREVIEW_LIMIT = 10
#: Upper bound on features materialized for one query (bounded reads only).
_MAX_QUERY_FEATURES = 50_000


def _resolve_gpkg_path(endpoint: str, options: Dict[str, Any]) -> Path:
    """endpoint/base_dir option → a concrete .gpkg path (safe, declared only)."""
    raw = (endpoint or "").strip() or str(options.get("base_dir") or "").strip()
    if not raw or raw.startswith("${"):
        raise SourceUnreachableError(
            "geopackage source path is unset (env ref unresolved)",
            details={
                "hint": "set LOCAL_GEODATA_DIR (or the declared env var) and ingest "
                "local data first; an unset local asset is 'unavailable', never empty"
            },
        )
    theme_root = str(options.get("theme_root") or "").strip()
    base = Path(raw).expanduser()
    if theme_root and base.suffix.lower() != ".gpkg":
        base = base / theme_root
    roots = _local_file_roots_from_settings()
    max_bytes = _local_file_max_bytes_from_settings()
    if base.is_dir():
        candidates = sorted(base.glob("*.gpkg"))
        if not candidates:
            raise SourceUnreachableError(
                f"no .gpkg files under {base}",
                details={"hint": "ingest local data first"},
            )
        try:
            resolved = [
                Path(resolve_safe_local_path(str(c), roots, max_bytes))
                for c in candidates
            ]
        except DataFabricSecurityError as e:
            raise SecurityBlockedError(str(e)) from e
        return resolved[0]
    try:
        return Path(resolve_safe_local_path(str(base), roots, max_bytes))
    except DataFabricSecurityError as e:
        raise SecurityBlockedError(str(e)) from e


class GeoPackageAdapter(GeospatialDataSourceAdapter):
    """Local GPKG asset adapter (data_type=vector)."""

    def __init__(self, connection_profile):
        super().__init__(connection_profile)
        self._path: Optional[Path] = None

    # -- internals ---------------------------------------------------------------
    def _gpkg_path(self) -> Path:
        if self._path is None:
            endpoint = self.profile.endpoint_url or self.profile.url or ""
            self._path = _resolve_gpkg_path(endpoint, dict(self.profile.options or {}))
        return self._path

    def _layers(self) -> List[str]:
        import pyogrio

        layers = pyogrio.list_layers(str(self._gpkg_path()))
        # pyogrio.list_layers → ndarray of (layer, geometry_type) rows
        return [str(row[0]) for row in layers]

    def _require_existing(self) -> Path:
        path = self._gpkg_path()
        if not path.exists():
            raise SourceUnreachableError(
                f"local GeoPackage missing: {path}",
                details={"hint": "ingest local data into LOCAL_GEODATA_DIR first"},
            )
        return path

    # -- adapter contract ----------------------------------------------------------
    def probe(self) -> bool:
        try:
            return self._gpkg_path().exists()
        except Exception:  # noqa: BLE001 — probe never raises
            return False

    def capabilities(self) -> List[str]:
        return ["bbox", "projection", "limit"]

    def list_datasets(self) -> List[Dict[str, Any]]:
        path = self._require_existing()
        datasets = [
            {"id": layer, "name": layer, "data_type": "vector", "path": str(path)}
            for layer in self._layers()
        ]
        if len(datasets) == 1 and datasets[0]["id"] != path.stem:
            # single-layer file: expose under the file stem as an alias too
            datasets.append(
                {"id": path.stem, "name": path.stem, "data_type": "vector", "alias_of": datasets[0]["id"]}
            )
        return datasets

    def describe(self, dataset_id: str):
        import pyogrio
        from app.schemas.data_fabric_schema import DatasetDescriptor

        path = self._require_existing()
        layer = self._resolve_layer(dataset_id)
        try:
            info = pyogrio.read_info(str(path), layer=layer)
        except Exception as e:  # noqa: BLE001 — pyogrio error surface is broad
            raise SourceBadResponseError(f"pyogrio failed to read info for '{layer}': {e}") from e
        fields = [
            {"name": str(name), "type": str(dtype)}
            for name, dtype in zip(info["fields"].tolist(), info["dtypes"].tolist())
        ]
        return DatasetDescriptor(
            id=dataset_id,
            source_type="geopackage",
            source_id=self.profile.id,
            name=layer,
            data_type="vector",
            feature_count=int(info["features"]),
            geometry_type=str(info.get("geometry_type") or "Unknown"),
            crs=str(info.get("crs") or "") or None,
            srs=str(info.get("crs") or "") or None,
            fields=fields,
            query_capabilities=self.capabilities(),
            metadata={"path": str(path), "layer": layer, "adapter": "ads.geopackage"},
        )

    def preview(self, dataset_id: str, limit: int = _PREVIEW_LIMIT) -> Dict[str, Any]:
        import pyogrio

        path = self._require_existing()
        layer = self._resolve_layer(dataset_id)
        try:
            gdf = pyogrio.read_dataframe(
                str(path), layer=layer, max_features=max(1, min(int(limit), _PREVIEW_LIMIT))
            )
        except Exception as e:  # noqa: BLE001
            raise SourceBadResponseError(f"GeoPackage read failed for '{layer}': {e}") from e
        return {
            "dataset_id": dataset_id,
            "columns": list(gdf.columns),
            "features": gdf.to_geo_dict()["features"],
            "is_demo": False,
        }

    def query(self, dataset_id: str, query_spec) -> QueryResult:
        import pyogrio

        path = self._require_existing()
        layer = self._resolve_layer(dataset_id)
        limit = max(0, min(int(query_spec.limit or 0), _MAX_QUERY_FEATURES))
        offset = max(0, int(query_spec.offset or 0))
        columns = list(query_spec.columns or []) or None
        bbox = query_spec.bbox
        try:
            if bbox:
                minx, miny, maxx, maxy = (float(v) for v in bbox)
                gdf = pyogrio.read_dataframe(
                    str(path), layer=layer, bbox=(minx, miny, maxx, maxy),
                    columns=columns, max_features=_MAX_QUERY_FEATURES,
                )
            else:
                gdf = pyogrio.read_dataframe(
                    str(path), layer=layer, columns=columns, max_features=_MAX_QUERY_FEATURES,
                )
        except Exception as e:  # noqa: BLE001
            raise SourceBadResponseError(f"GeoPackage read failed for '{layer}': {e}") from e
        page = gdf.iloc[offset: offset + limit] if limit else gdf.iloc[offset:]
        return QueryResult(
            dataset_id=dataset_id,
            query_spec=query_spec,
            features=page.to_geo_dict()["features"],
            metadata={
                "adapter": "ads.geopackage",
                "source": "local_gpkg",
                "is_demo": False,
                "count": int(len(page)),
                "total_matched": int(len(gdf)),
            },
        )

    def health(self) -> DataFabricHealth:
        reachable = self.probe()
        return DataFabricHealth(
            source_type="geopackage",
            reachable=reachable,
            latency_ms=0.0,
            details={"path": str(self._path) if self._path else None},
        )

    # -- helpers -------------------------------------------------------------------
    def _resolve_layer(self, dataset_id: str) -> str:
        layers = self._layers()
        if dataset_id in layers:
            return dataset_id
        stem = self._gpkg_path().stem
        if dataset_id == stem and len(layers) == 1:
            return layers[0]
        raise InvalidQueryError(
            f"unknown layer '{dataset_id}'",
            details={"known_layers": layers},
        )
