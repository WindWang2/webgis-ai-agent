"""
Geospatial Data Fabric: Spatial Catalog Service
Provides lightweight catalog search, indexing, filtering by keyword, bbox, CRS, tags, and source_type.
"""
import logging
from typing import Dict, Any, List, Optional
from app.schemas.data_fabric_schema import DatasetDescriptor

logger = logging.getLogger(__name__)


def _bbox_intersects(box1: List[float], box2: List[float]) -> bool:
    """
    Check if two 2D bounding boxes [minx, miny, maxx, maxy] intersect.
    """
    if len(box1) < 4 or len(box2) < 4:
        return False
    return (
        box1[0] <= box2[2]
        and box1[2] >= box2[0]
        and box1[1] <= box2[3]
        and box1[3] >= box2[1]
    )


class SpatialCatalogService:
    """
    Lightweight Spatial Catalog Service for storing, indexing, and searching
    dataset descriptors in the Data Fabric.
    """

    def __init__(self):
        self._catalog: Dict[str, DatasetDescriptor] = {}
        self._tags: Dict[str, List[str]] = {}
        self._profile_map: Dict[str, str] = {}

    def register_dataset(
        self,
        descriptor: DatasetDescriptor,
        tags: Optional[List[str]] = None,
        profile_id: Optional[str] = None,
    ) -> DatasetDescriptor:
        """Register or update a dataset descriptor in the catalog."""
        self._catalog[descriptor.id] = descriptor
        if tags is not None:
            self._tags[descriptor.id] = list(tags)
        elif descriptor.id not in self._tags:
            self._tags[descriptor.id] = []

        if profile_id:
            self._profile_map[descriptor.id] = profile_id

        logger.info(f"[SpatialCatalog] Registered dataset '{descriptor.id}'")
        return descriptor

    def unregister_dataset(self, dataset_id: str) -> bool:
        """Remove a dataset descriptor from the catalog."""
        if dataset_id in self._catalog:
            del self._catalog[dataset_id]
            self._tags.pop(dataset_id, None)
            self._profile_map.pop(dataset_id, None)
            logger.info(f"[SpatialCatalog] Unregistered dataset '{dataset_id}'")
            return True
        return False

    def get_dataset(self, dataset_id: str) -> Optional[DatasetDescriptor]:
        """Retrieve a dataset descriptor by ID."""
        return self._catalog.get(dataset_id)

    def get_profile_id(self, dataset_id: str) -> Optional[str]:
        """Retrieve the profile_id associated with a dataset_id."""
        return self._profile_map.get(dataset_id)

    def list_datasets(self) -> List[DatasetDescriptor]:
        """List all datasets in the catalog."""
        return list(self._catalog.values())

    def search(
        self,
        query: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        crs: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Search datasets matching optional filters:
        - query: keyword match against dataset id, title, description, or tags (case-insensitive)
        - bbox: spatial extent bounding box filter [minx, miny, maxx, maxy]
        - crs: CRS/SRS code filter (e.g. 'EPSG:4326')
        - tags: list of tags to filter against
        - source_type: data source type filter (e.g. 'postgis', 'wfs')
        """
        matched: List[DatasetDescriptor] = []

        query_lower = query.lower().strip() if query else None
        source_type_lower = source_type.lower().strip() if source_type else None
        crs_normalized = crs.upper().replace("EPSG:", "") if crs else None

        for dataset_id, descriptor in self._catalog.items():
            dataset_tags = self._tags.get(dataset_id, [])

            # 1. Source type filter
            if source_type_lower:
                if descriptor.source_type.lower() != source_type_lower:
                    continue

            # 2. CRS / SRS filter
            if crs_normalized:
                desc_srs = (descriptor.srs or "").upper().replace("EPSG:", "")
                if crs_normalized not in desc_srs:
                    continue

            # 3. Tags filter
            if tags:
                requested_tags = [t.lower() for t in tags]
                existing_tags_lower = [t.lower() for t in dataset_tags]
                if not any(t in existing_tags_lower for t in requested_tags):
                    continue

            # 4. Keyword query filter
            if query_lower:
                title_match = query_lower in (descriptor.title or "").lower()
                desc_match = query_lower in (descriptor.description or "").lower()
                name_match = query_lower in (descriptor.name or "").lower()
                id_match = query_lower in descriptor.id.lower()
                tag_match = any(query_lower in t.lower() for t in dataset_tags)
                if not (title_match or desc_match or name_match or id_match or tag_match):
                    continue

            # 5. Spatial BBox filter
            if bbox:
                if not descriptor.bbox or not _bbox_intersects(descriptor.bbox, bbox):
                    continue

            matched.append(descriptor)

        total = len(matched)
        page_items = matched[offset : offset + limit]

        items_dict = []
        for d in page_items:
            dump = d.model_dump()
            dump["tags"] = self._tags.get(d.id, [])
            dump["profile_id"] = self._profile_map.get(d.id)
            items_dict.append(dump)

        return {
            "total": total,
            "items": items_dict,
            "limit": limit,
            "offset": offset,
        }

    def clear(self) -> None:
        """Clear all entries in catalog."""
        self._catalog.clear()
        self._tags.clear()
        self._profile_map.clear()


# Global singleton instance
spatial_catalog_service = SpatialCatalogService()
