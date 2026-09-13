"""COG (Cloud-Optimized GeoTIFF) Data Source Adapter (ads-v1 DS1, ADR-0171).

Metadata-only raster seam: describe/preview expose the real header (bounds,
CRS, resolution, band count, dtypes) read with rasterio; vector ``query`` is a
typed ``QueryUnsupportedError`` (raster retrieval is a materialization
concern, not a feature query — honest capability boundary). Offline/missing
asset → ``SourceUnreachableError``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.data_fabric_schema import DataFabricHealth, QueryResult
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.errors import (
    QueryUnsupportedError,
    SourceBadResponseError,
    SourceUnreachableError,
)

logger = logging.getLogger(__name__)


def _resolve_raster(endpoint: str, options: Dict[str, Any]) -> str:
    """endpoint / base_dir option → raster path-or-URL string (safe for local)."""
    raw = (endpoint or "").strip() or str(options.get("base_dir") or "").strip()
    if not raw or raw.startswith("${"):
        raise SourceUnreachableError(
            "cog source path is unset (env ref unresolved)",
            details={"hint": "declare endpoint or options.base_dir"},
        )
    if raw.startswith(("http://", "https://", "/vsi")):
        return raw
    from app.services.data_fabric.security import resolve_safe_local_path

    return str(resolve_safe_local_path(str(Path(raw).expanduser())))


class COGAdapter(GeospatialDataSourceAdapter):
    """Cloud-Optimized GeoTIFF metadata adapter (data_type=raster)."""

    def __init__(self, connection_profile):
        super().__init__(connection_profile)
        self._path: Optional[str] = None

    def _raster(self) -> str:
        if self._path is None:
            endpoint = self.profile.endpoint_url or self.profile.url or ""
            self._path = _resolve_raster(endpoint, dict(self.profile.options or {}))
        return self._path

    def _open_info(self) -> Dict[str, Any]:
        import rasterio

        path = self._raster()
        if not path.startswith(("http", "/vsi")) and not Path(path).exists():
            raise SourceUnreachableError(
                f"COG asset missing: {path}",
                details={"hint": "declare a real local COG or reachable URL"},
            )
        try:
            with rasterio.open(path) as src:
                return {
                    "bounds": list(src.bounds),
                    "crs": str(src.crs) if src.crs else None,
                    "resolution": list(src.res),
                    "bands": src.count,
                    "dtypes": list(src.dtypes),
                    "shape": [src.height, src.width],
                    "driver": src.driver,
                }
        except Exception as e:  # noqa: BLE001 — rasterio/GDAL error surface is broad
            raise SourceBadResponseError(f"rasterio failed to open {path}: {e}") from e

    # -- adapter contract ----------------------------------------------------------
    def probe(self) -> bool:
        try:
            self._open_info()
            return True
        except Exception:  # noqa: BLE001 — probe never raises
            return False

    def capabilities(self) -> List[str]:
        return ["raster_metadata"]

    def list_datasets(self) -> List[Dict[str, Any]]:
        self._open_info()  # validates the header (typed error when missing/unreadable)
        name = Path(self._raster()).stem or "cog_main"
        return [{"id": name, "name": name, "data_type": "raster", "path": self._raster()}]

    def describe(self, dataset_id: str):
        from app.schemas.data_fabric_schema import DatasetDescriptor

        info = self._open_info()
        name = Path(self._raster()).stem or "cog_main"
        bounds = info["bounds"]  # [minx, miny, maxx, maxy]
        fields = [
            {"name": f"band_{i + 1}", "type": dt} for i, dt in enumerate(info["dtypes"])
        ]
        return DatasetDescriptor(
            id=dataset_id,
            source_type="cog",
            source_id=self.profile.id,
            name=name,
            data_type="raster",
            geometry_type="Raster",
            crs=info["crs"],
            srs=info["crs"],
            bbox=[bounds[0], bounds[1], bounds[2], bounds[3]],
            fields=fields,
            query_capabilities=self.capabilities(),
            metadata={
                "adapter": "ads.cog",
                "resolution": info["resolution"],
                "bands": info["bands"],
                "shape": info["shape"],
                "driver": info["driver"],
                "path": self._raster(),
            },
        )

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Decimated header-level preview (≤16x16 decimation, real pixels)."""
        import rasterio

        path = self._raster()
        try:
            with rasterio.open(path) as src:
                window_shape = (min(16, src.height), min(16, src.width))
                data = src.read(1, out_shape=window_shape)
                return {
                    "dataset_id": dataset_id,
                    "shape": list(data.shape),
                    "dtype": str(data.dtype),
                    "bounds": list(src.bounds),
                    "crs": str(src.crs) if src.crs else None,
                    "is_demo": False,
                }
        except Exception as e:  # noqa: BLE001
            raise SourceBadResponseError(f"rasterio preview failed for {path}: {e}") from e

    def query(self, dataset_id: str, query_spec) -> QueryResult:
        raise QueryUnsupportedError(
            "COG adapter is metadata-only; raster retrieval goes through materialization",
            details={"hint": "use the materialization service for raster assets"},
        )

    def health(self) -> DataFabricHealth:
        reachable = self.probe()
        return DataFabricHealth(
            source_type="cog",
            reachable=reachable,
            latency_ms=0.0,
            details={"path": self._path},
        )
