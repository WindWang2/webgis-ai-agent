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
    """Lightweight Spatial Catalog Service（agent 工具面会话级投影）。

    C4 修复（ADR-0094 §10 / 审计）：注册与查询支持 ``owner`` 作用域。owner=None
    表示全局/legacy 条目（单租户语义，旧行为保留）；带 owner 的查询只看到
    自己的 + 全局条目——跨会话（跨租户）数据不再互相可见。
    并发：注册/查询在锁内进行（connect/search 并行时 dict 迭代不再 race）。
    """

    def __init__(self):
        import threading

        self._catalog: Dict[str, DatasetDescriptor] = {}
        self._tags: Dict[str, List[str]] = {}
        self._profile_map: Dict[str, str] = {}
        self._owner_map: Dict[str, Optional[str]] = {}
        self._lock = threading.RLock()

    def register_dataset(
        self,
        descriptor: DatasetDescriptor,
        tags: Optional[List[str]] = None,
        profile_id: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> DatasetDescriptor:
        """Register or update a dataset descriptor in the catalog."""
        with self._lock:
            self._catalog[descriptor.id] = descriptor
            if tags is not None:
                self._tags[descriptor.id] = list(tags)
            elif descriptor.id not in self._tags:
                self._tags[descriptor.id] = []
            if profile_id:
                self._profile_map[descriptor.id] = profile_id
            # 重复注册以最近一次 owner 为准（会话重连）
            self._owner_map[descriptor.id] = owner

        logger.info("[SpatialCatalog] Registered dataset '%s' (owner=%s)", descriptor.id, owner)
        return descriptor

    def unregister_dataset(self, dataset_id: str) -> bool:
        """Remove a dataset descriptor from the catalog."""
        with self._lock:
            if dataset_id in self._catalog:
                del self._catalog[dataset_id]
                self._tags.pop(dataset_id, None)
                self._profile_map.pop(dataset_id, None)
                self._owner_map.pop(dataset_id, None)
                logger.info("[SpatialCatalog] Unregistered dataset '%s'", dataset_id)
                return True
            return False

    def _visible(self, dataset_id: str, owner: Optional[str]) -> bool:
        """owner 作用域可见性：owner=None（legacy/全局视图）看全部；
        带 owner 看自己 + owner=None 注册的全局条目。"""
        if owner is None:
            return True
        entry_owner = self._owner_map.get(dataset_id, None)
        return entry_owner is None or entry_owner == owner

    def get_dataset(
        self, dataset_id: str, owner: Optional[str] = None
    ) -> Optional[DatasetDescriptor]:
        """Retrieve a dataset descriptor by ID（owner 作用域过滤）。"""
        with self._lock:
            if not self._visible(dataset_id, owner):
                return None
            return self._catalog.get(dataset_id)

    def get_profile_id(
        self, dataset_id: str, owner: Optional[str] = None
    ) -> Optional[str]:
        """Retrieve the profile_id associated with a dataset_id（owner 过滤）。"""
        with self._lock:
            if not self._visible(dataset_id, owner):
                return None
            return self._profile_map.get(dataset_id)

    def list_datasets(self, owner: Optional[str] = None) -> List[DatasetDescriptor]:
        """List datasets in the catalog（owner 作用域过滤）。"""
        with self._lock:
            return [
                d for did, d in self._catalog.items()
                if self._visible(did, owner)
            ]

    def search(
        self,
        query: Optional[str] = None,
        bbox: Optional[List[float]] = None,
        crs: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        owner: Optional[str] = None,
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

        with self._lock:
            catalog_snapshot = list(self._catalog.items())

        for dataset_id, descriptor in catalog_snapshot:
            if not self._visible(dataset_id, owner):
                continue
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
        with self._lock:
            self._catalog.clear()
            self._tags.clear()
            self._profile_map.clear()
            self._owner_map.clear()


# Global singleton instance
spatial_catalog_service = SpatialCatalogService()
