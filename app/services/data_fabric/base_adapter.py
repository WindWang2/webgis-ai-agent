"""
Geospatial Data Fabric: Base Adapter Seam & Unified Architecture
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any
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

    def sync(self) -> Dict[str, Any]:
        """Optional metadata sync / cache refresh routine."""
        return {"status": "synced"}
