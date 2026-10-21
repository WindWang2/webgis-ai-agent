"""
Geospatial Data Fabric: Connection Manager & Generic Adapter Reference Implementation
Manages connected data source profiles and active adapter instances.
"""
import ipaddress
import logging
import time
from typing import Callable, Dict, Any, List, Optional, Tuple
from app.schemas.data_fabric_schema import (
    ConnectionProfile,
    DatasetDescriptor,
    QuerySpec,
    QueryResult,
    DataFabricHealth,
)
from app.services.data_fabric.base_adapter import GeospatialDataSourceAdapter
from app.services.data_fabric.security import DataFabricSecurity
from app.services.data_fabric.spatial_catalog import spatial_catalog_service

logger = logging.getLogger(__name__)


class GenericDataSourceAdapter(GeospatialDataSourceAdapter):
    """
    Generic Data Source Adapter implementation for Data Fabric endpoints.
    Handles PostGIS, OGC, WFS, WMS, ArcGIS, GeoJSON, and other data protocols.
    """

    def __init__(self, connection_profile: ConnectionProfile):
        super().__init__(connection_profile)
        # In-memory mock feature cache / datasets store if provided in options or dynamically created
        self._datasets_cache: Dict[str, DatasetDescriptor] = {}
        self._features_cache: Dict[str, List[Dict[str, Any]]] = {}
        self._init_from_profile()

    def _init_from_profile(self):
        """Initialize default dataset descriptors and sample features from connection options."""
        options = self.profile.options or {}
        custom_datasets = options.get("datasets") or options.get("layers") or []

        if isinstance(custom_datasets, list) and custom_datasets:
            for item in custom_datasets:
                if isinstance(item, dict):
                    ds_id = item.get("id") or item.get("name") or self.profile.id
                    desc = DatasetDescriptor(
                        id=ds_id,
                        title=item.get("title") or ds_id,
                        description=item.get("description") or f"Layer from {self.profile.id}",
                        source_type=self.profile.source_type,
                        geometry_type=item.get("geometry_type") or "Polygon",
                        srs=item.get("srs") or "EPSG:4326",
                        bbox=item.get("bbox") or [-180.0, -90.0, 180.0, 90.0],
                        feature_count=item.get("feature_count") or 10,
                        fields=item.get("fields") or [{"name": "id", "type": "string"}, {"name": "name", "type": "string"}],
                        metadata=item.get("metadata") or {},
                    )
                    self._datasets_cache[ds_id] = desc
                    self._features_cache[ds_id] = item.get("features") or self._generate_sample_features(ds_id, desc.feature_count or 10)
        else:
            default_id = self.profile.id
            desc = DatasetDescriptor(
                id=default_id,
                title=self.profile.name or default_id,
                description=f"Geospatial data layer for source {self.profile.source_type}",
                source_type=self.profile.source_type,
                geometry_type=options.get("geometry_type", "Polygon"),
                srs=options.get("srs", "EPSG:4326"),
                bbox=options.get("bbox", [-180.0, -90.0, 180.0, 90.0]),
                feature_count=options.get("feature_count", 10),
                fields=options.get("fields", [{"name": "id", "type": "string"}, {"name": "name", "type": "string"}]),
                metadata=options.get("metadata", {}),
            )
            self._datasets_cache[default_id] = desc
            self._features_cache[default_id] = options.get("features") or self._generate_sample_features(default_id, desc.feature_count or 10)

    def _generate_sample_features(self, dataset_id: str, count: int) -> List[Dict[str, Any]]:
        features = []
        for i in range(1, count + 1):
            features.append({
                "type": "Feature",
                "id": i,
                "geometry": {
                    "type": "Point",
                    "coordinates": [116.40 + (i * 0.01), 39.90 + (i * 0.01)],
                },
                "properties": {
                    "id": str(i),
                    "name": f"Feature {i} from {dataset_id}",
                    "dataset_id": dataset_id,
                },
            })
        return features

    def probe(self) -> bool:
        """Reachability probe."""
        if self.profile.url:
            DataFabricSecurity.validate_url(self.profile.url, allow_private=self.profile.allow_private)
        return True

    def capabilities(self) -> List[str]:
        """Capability flags."""
        caps = ["pushdown_bbox", "pushdown_filter", "vector_features", "materialization"]
        if self.profile.source_type.lower() in ("wms", "wmts", "raster"):
            caps.append("raster_tile")
        return caps

    def list_datasets(self) -> List[Dict[str, Any]]:
        """List datasets."""
        return [desc.model_dump() for desc in self._datasets_cache.values()]

    def describe(self, dataset_id: str) -> DatasetDescriptor:
        """Describe dataset metadata contract."""
        if dataset_id in self._datasets_cache:
            return self._datasets_cache[dataset_id]

        # Dynamic fallback descriptor
        desc = DatasetDescriptor(
            id=dataset_id,
            title=f"Dataset {dataset_id}",
            description=f"Layer {dataset_id} from profile {self.profile.id}",
            source_type=self.profile.source_type,
            geometry_type="Point",
            srs="EPSG:4326",
            bbox=[-180.0, -90.0, 180.0, 90.0],
            feature_count=10,
            fields=[{"name": "id", "type": "string"}, {"name": "name", "type": "string"}],
        )
        self._datasets_cache[dataset_id] = desc
        self._features_cache[dataset_id] = self._generate_sample_features(dataset_id, 10)
        return desc

    def preview(self, dataset_id: str, limit: int = 10) -> Dict[str, Any]:
        """Bounded preview sample data."""
        desc = self.describe(dataset_id)
        features = self._features_cache.get(dataset_id, [])[:limit]
        return {
            "dataset_id": dataset_id,
            "schema": desc.fields,
            "bbox": desc.bbox,
            "geometry_type": desc.geometry_type,
            "features": features,
            "limit": limit,
        }

    def query(self, dataset_id: str, query_spec: QuerySpec) -> QueryResult:
        """Execute pushdown query or selective fetch."""
        desc = self.describe(dataset_id)
        features = self._features_cache.get(dataset_id, [])

        # Apply BBox filter if specified
        if query_spec.bbox and len(query_spec.bbox) >= 4:
            q_minx, q_miny, q_maxx, q_maxy = query_spec.bbox[:4]
            filtered = []
            for feat in features:
                geom = feat.get("geometry", {})
                coords = geom.get("coordinates", [])
                if geom.get("type") == "Point" and len(coords) >= 2:
                    x, y = coords[0], coords[1]
                    if q_minx <= x <= q_maxx and q_miny <= y <= q_maxy:
                        filtered.append(feat)
                else:
                    filtered.append(feat)
            features = filtered

        # Apply offset and limit
        offset = query_spec.offset or 0
        limit = query_spec.limit or 100
        sliced_features = features[offset : offset + limit]

        # Field selection if specified
        selected_fields = query_spec.fields or query_spec.columns
        if selected_fields:
            fields_set = set(selected_fields)
            projected = []
            for feat in sliced_features:
                props = feat.get("properties", {})
                new_props = {k: v for k, v in props.items() if k in fields_set}
                new_feat = dict(feat)
                new_feat["properties"] = new_props
                projected.append(new_feat)
            sliced_features = projected


        return QueryResult(
            dataset_id=dataset_id,
            features=sliced_features,
            total_count=len(features),
            schema_info={"fields": desc.fields, "geometry_type": desc.geometry_type},
            metadata={
                "query_spec": query_spec.model_dump(),
                "source_type": self.profile.source_type,
                # R1-m11：demo adapter 的结果在 payload 自带 demo 标（物化后的
                # ref 不再依赖工具层重建 flag）。
                "is_demo": True,
            },
        )

    def health(self) -> DataFabricHealth:
        """Diagnostic health status."""
        try:
            self.probe()
            return DataFabricHealth(
                status="healthy",
                message="Endpoint reachable and operational",
                details={"source_type": self.profile.source_type},
            )
        except Exception as e:
            return DataFabricHealth(
                status="unreachable",
                message=f"Health probe error: {e}",
                details={"error": str(e)},
            )

    def sync(self, owner: Optional[str] = None) -> Dict[str, Any]:
        """Sync metadata and update Spatial Catalog（owner 作用域传播）。"""
        synced_count = 0
        for desc in self._datasets_cache.values():
            spatial_catalog_service.register_dataset(
                desc, profile_id=self.profile.id, owner=owner
            )
            synced_count += 1
        return {"status": "synced", "count": synced_count, "profile_id": self.profile.id}


def create_adapter_for_profile(profile: ConnectionProfile) -> GeospatialDataSourceAdapter:
    """Factory mapping ConnectionProfile.source_type → concrete adapter.

    Routes through the canonical ``AdapterRegistry``. An unregistered source
    type raises ``UnsupportedSourceError`` — it is NEVER silently mapped to
    mock data. (Callers that want the explicit demo adapter must request
    source_type ``generic``/``mock``/``sample`` — ``geojson`` is no longer an
    alias, #767.)
    """
    from app.services.data_fabric.registry import build_adapter

    return build_adapter(profile)


def _ssrf_validate_profile(profile: ConnectionProfile) -> None:
    """Run DataFabricSecurity.validate_url for url *or* host/port profiles.

    Host/port-only PostGIS connections previously skipped the SSRF gate when
    ``profile.url`` was empty (#1107). Construct a synthetic postgresql:// URL
    so the same private/loopback/metadata IP checks apply.
    """
    if profile.url:
        DataFabricSecurity.validate_url(profile.url, allow_private=profile.allow_private)
        return
    if not profile.host:
        return

    host = profile.host.strip()
    # Bracket IPv6 literals so urlparse yields a correct hostname.
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        if ip.version == 6:
            host = f"[{ip}]"
    except ValueError:
        pass
    port = profile.port if profile.port is not None else 5432
    DataFabricSecurity.validate_url(
        f"postgresql://{host}:{port}",
        allow_private=profile.allow_private,
    )


def _connection_setting(name: str, default: Any) -> Any:
    """延迟读取 settings（未登记配置回落默认 —— 与 fabric registry 同语义）。"""
    try:
        from app.core.config import settings

        return getattr(settings, name, default)
    except Exception:  # noqa: BLE001 - 配置层不可用时用默认值
        return default


class DataFabricConnectionManager:
    """Connection Manager for Data Fabric profiles and adapters.

    C4 修复（ADR-0094 §10 / 审计）：``owner`` 作用域键控 —— 连接按
    (owner, profile_id) 隔离存储；不同会话（租户）不再互相覆盖/访问连接。
    owner=None 保留 legacy 全局语义（单租户/测试路径）。并发访问加锁。

    #1715：``_profiles``/``_adapters`` 此前只增不删（``disconnect``/``clear``
    无生产调用点）—— 长生命周期 API worker 随会话连接线性泄漏 adapter
    （含 ``requests.Session`` 连接池）直至 OOM。现按 (owner, profile) 键做
    LRU + idle TTL（与 fabric ConnectionRegistry 共用同一对设置），逐出/
    断连时级联清理该 profile 在 SpatialCatalog 的目录足迹。
    """

    def __init__(
        self,
        max_entries: Optional[int] = None,
        idle_ttl_s: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        import threading

        self._profiles: Dict[Tuple[Optional[str], str], ConnectionProfile] = {}
        self._adapters: Dict[Tuple[Optional[str], str], GeospatialDataSourceAdapter] = {}
        #: (owner, profile_id) → 最近访问时刻（LRU 序 + idle TTL 判定）。
        self._last_access: Dict[Tuple[Optional[str], str], float] = {}
        self._lock = threading.RLock()
        self._max_entries = max(
            1,
            int(max_entries if max_entries is not None
                else _connection_setting("DATA_FABRIC_V7_CONNECTION_MAX_ENTRIES", 1024)),
        )
        self._idle_ttl_s = max(
            0.0,
            float(idle_ttl_s if idle_ttl_s is not None
                  else _connection_setting("DATA_FABRIC_V7_CONNECTION_IDLE_TTL_S", 1800.0)),
        )
        self._clock = clock

    @staticmethod
    def _key(profile_id: str, owner: Optional[str]) -> Tuple[Optional[str], str]:
        return (owner, profile_id)

    # -- 生命周期维护（#1715） --------------------------------------------------

    def _drop_locked(self, key: Tuple[Optional[str], str]) -> Optional[str]:
        """移除一条连接（调用方持锁）；返回其 profile_id（级联清理用）。"""
        profile = self._profiles.pop(key, None)
        self._adapters.pop(key, None)
        self._last_access.pop(key, None)
        return profile.id if profile is not None else None

    def _maintain_locked(self) -> List[str]:
        """idle 清扫 + LRU 逐出（调用方持锁）；返回被逐出的 profile_id。"""
        evicted: List[str] = []
        if self._idle_ttl_s > 0:
            now = self._clock()
            for key in [
                k for k, ts in self._last_access.items() if now - ts > self._idle_ttl_s
            ]:
                pid = self._drop_locked(key)
                if pid is not None:
                    evicted.append(pid)
        while len(self._adapters) > self._max_entries and self._last_access:
            victim = min(self._last_access.items(), key=lambda kv: (kv[1], kv[0]))[0]
            pid = self._drop_locked(victim)
            if pid is not None:
                evicted.append(pid)
        return evicted

    def _cascade_catalog_cleanup(self, profile_id: str) -> None:
        """某 profile 无存活连接后回收其 SpatialCatalog 目录足迹。

        仍有其它 (owner, profile) 连接引用同一 profile 时不清场 —— 它们的
        目录条目仍然有效。
        """
        with self._lock:
            still_live = any(pid == profile_id for (_o, pid) in self._adapters)
        if not still_live:
            spatial_catalog_service.unregister_by_profile(profile_id)

    def _sweep(self) -> None:
        """idle 清扫 + LRU 逐出 + 目录足迹级联清理（#1715）。"""
        with self._lock:
            evicted = self._maintain_locked()
        for pid in evicted:
            self._cascade_catalog_cleanup(pid)

    def stats(self) -> Dict[str, Any]:
        """诊断视图（条目数/上限/idle TTL）。"""
        with self._lock:
            return {
                "entries": len(self._adapters),
                "max_entries": self._max_entries,
                "idle_ttl_s": self._idle_ttl_s,
            }

    def connect(
        self,
        profile: ConnectionProfile,
        owner: Optional[str] = None,
    ) -> Tuple[ConnectionProfile, GeospatialDataSourceAdapter]:
        """Connect a data source profile（owner 作用域）。

        SSRF validation applies to both URL endpoints and host/port-only
        database connections (e.g. PostGIS without a url field).
        """
        _ssrf_validate_profile(profile)

        adapter = create_adapter_for_profile(profile)
        adapter.sync(owner=owner)  # Registers dataset descriptors in catalog（同 owner 注册）

        with self._lock:
            key = self._key(profile.id, owner)
            self._profiles[key] = profile
            self._adapters[key] = adapter
            self._last_access[key] = self._clock()
            evicted = self._maintain_locked()

        for pid in evicted:
            self._cascade_catalog_cleanup(pid)

        logger.info(
            "[ConnectionManager] Connected profile '%s' (owner=%s, source_type=%s, adapter=%s)",
            profile.id, owner, profile.source_type, adapter.__class__.__name__,
        )
        return profile, adapter

    def get_adapter(
        self, profile_id: str, owner: Optional[str] = None
    ) -> Optional[GeospatialDataSourceAdapter]:
        self._sweep()
        with self._lock:
            key = self._key(profile_id, owner)
            adapter = self._adapters.get(key)
            if adapter is not None:
                self._last_access[key] = self._clock()
                return adapter
            if owner is None:
                return None
            # 全局（legacy）连接对会话可见；其它会话的连接不可见
            fallback = self._key(profile_id, None)
            adapter = self._adapters.get(fallback)
            if adapter is not None:
                self._last_access[fallback] = self._clock()
            return adapter

    def get_profile(
        self, profile_id: str, owner: Optional[str] = None
    ) -> Optional[ConnectionProfile]:
        self._sweep()
        with self._lock:
            key = self._key(profile_id, owner)
            profile = self._profiles.get(key)
            if profile is not None:
                self._last_access[key] = self._clock()
                return profile
            if owner is None:
                return None
            fallback = self._key(profile_id, None)
            profile = self._profiles.get(fallback)
            if profile is not None:
                self._last_access[fallback] = self._clock()
            return profile

    def list_profiles(self, owner: Optional[str] = None) -> List[ConnectionProfile]:
        self._sweep()
        with self._lock:
            return [
                p for (o, _pid), p in self._profiles.items()
                if owner is None or o is None or o == owner
            ]

    def get_all_adapters(self) -> Dict[str, GeospatialDataSourceAdapter]:
        self._sweep()
        with self._lock:
            return {f"{o or '_global'}:{pid}": a for (o, pid), a in self._adapters.items()}

    def disconnect(self, profile_id: str, owner: Optional[str] = None) -> bool:
        with self._lock:
            key = self._key(profile_id, owner)
            if key not in self._profiles:
                return False
            self._drop_locked(key)
            still_live = any(pid == profile_id for (_o, pid) in self._adapters)
        # 级联清理（#1715）：无存活连接引用该 profile → 清其全部目录足迹；
        # 仍有其它会话/全局连接在用 → 仅清本次 (owner, profile) 注册的条目。
        if still_live:
            spatial_catalog_service.unregister_by_profile(profile_id, owner=owner)
        else:
            spatial_catalog_service.unregister_by_profile(profile_id)
        return True

    def clear(self):
        with self._lock:
            profile_ids = {pid for (_o, pid) in self._adapters}
            self._profiles.clear()
            self._adapters.clear()
            self._last_access.clear()
        # 级联清理：仅回收本管理器自己连接过的 profile 的目录足迹（#1715）。
        for pid in profile_ids:
            spatial_catalog_service.unregister_by_profile(pid)


# Global singleton instance
connection_manager = DataFabricConnectionManager()
