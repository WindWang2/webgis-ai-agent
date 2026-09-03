"""
Geospatial Data Fabric: Base Adapter Seam & Unified Architecture
"""
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any

logger = logging.getLogger(__name__)
from app.schemas.data_fabric_schema import (
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
    ConnectionProfile,
)


class GeospatialDataSourceAdapter(ABC):
    """
    Unified contract for all geospatial data source adapters.
    Encapsulates protocol details (PostGIS, OGC, WFS, WMS, ArcGIS, STAC, GeoParquet, FlatGeobuf, PMTiles, S3).
    """

    def __init__(self, connection_profile: ConnectionProfile):
        self.profile = connection_profile

    @abstractmethod
    def probe(self) -> bool:
        """Lightweight reachability and connection probe."""
        pass

    @abstractmethod
    def capabilities(self) -> List[str]:
        """List adapter capability flags (e.g. pushdown_bbox, pushdown_filter, raster_tile, vector_features)."""
        pass

    @abstractmethod
    def list_datasets(self) -> List[Dict[str, Any]]:
        """Discover available collections / tables / layers / items in the data source."""
        pass

    @abstractmethod
    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Fetch full DatasetDescriptor metadata contract for a specific dataset."""
        pass

    @abstractmethod
    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Fetch bounded sample data preview (schema, bounding box, sample features)."""
        pass

    @abstractmethod
    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """Execute pushdown query or selective fetch according to capability."""
        pass

    @abstractmethod
    def health(self) -> DataFabricHealth:
        """Return diagnostic health check object."""
        pass

    def sync(self, owner=None) -> Dict[str, Any]:
        """Metadata sync / cache refresh routine registering discovered datasets into SpatialCatalogService.

        ``owner``（ADR-0094 §10 / 审计 C4）：目录条目的会话作用域；None=全局
        legacy 语义。describe 失败的条目跳过（不再吞异常后注册残缺 stub）。
        """
        from app.services.data_fabric.spatial_catalog import spatial_catalog_service
        datasets = self.list_datasets()
        synced_count = 0
        for d in datasets:
            did = d.get("id") if isinstance(d, dict) else str(d)
            if did:
                try:
                    desc = self.describe(did)
                    if desc:
                        spatial_catalog_service.register_dataset(
                            desc, profile_id=self.profile.id, owner=owner
                        )
                        synced_count += 1
                except Exception as e:
                    logger.warning(
                        "[DataFabricAdapter] sync describe failed for '%s': %s", did, e
                    )
        return {"status": "synced", "count": synced_count, "profile_id": self.profile.id}
