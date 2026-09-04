"""数据源统计模型（Data Fabric V3，ADR-0096 D3）。

「诚实有界」统计：只收集来源真实暴露的量，未知保持 None 并标注
confidence；绝不虚构精度（ADR-0094 honest-unknown 文化的延伸）。
统计以 **descriptor fingerprint 为键** —— 数据集修订变化后旧统计自然
失配（配合 planner 的 revision warning），缓存只是性能优化不是真相。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: 统计 TTL（进程内；与 describe 30s / postgis meta 60s 属不同新鲜度域，
#: ADR-0096 D3：弱/陈旧语义由 revision_strength 表达，不做跨进程广播）。
STATISTICS_TTL_S = 60.0
_STAT_MAX_ENTRIES = 1024


class ColumnStatistics(BaseModel):
    """单列统计（全部可未知；confidence 标注来源强度）。"""

    name: str
    null_fraction: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ndv: Optional[int] = Field(default=None, ge=0)  # number of distinct values
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    confidence: str = "assumption"  # measured | estimated | assumption


class DatasetStatistics(BaseModel):
    """数据集级统计快照（按 descriptor fingerprint 寻址）。"""

    dataset_fingerprint: str
    source_type: Optional[str] = None
    row_count: Optional[int] = Field(default=None, ge=0)
    extent: Optional[List[float]] = None          # [minx, miny, maxx, maxy]
    geometry_type: Optional[str] = None
    has_spatial_index: Optional[bool] = None
    resolution: Optional[float] = None            # 栅格源（米/度每像素）
    overview_levels: Optional[int] = None
    columns: List[ColumnStatistics] = Field(default_factory=list)
    revision_strength: str = "weak"               # strong | weak
    collected_at: Optional[str] = None
    collector: str = "descriptor"                 # descriptor | postgis_pgstats | geoparquet_footer

    def column(self, name: str) -> Optional[ColumnStatistics]:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    @property
    def confidence(self) -> str:
        """整体置信度 = 最弱维度（任一 assumption 列污染整体）。"""
        if not self.columns:
            return "estimated" if self.row_count is not None else "assumption"
        levels = {"measured": 0, "estimated": 1, "assumption": 2}
        worst = max((levels.get(c.confidence, 2) for c in self.columns), default=2)
        return {0: "measured", 1: "estimated", 2: "assumption"}[worst]


def statistics_from_descriptor(descriptor: Any) -> Optional[DatasetStatistics]:
    """从 descriptor 的 metadata 尽力收集统计（纯函数，绝无 IO）。

    PostGIS meta profile（count/bbox/gist）与 GeoParquet footer（num_rows/
    row-group 统计）都会把已知量写进 descriptor.metadata —— 这里统一收割。
    """
    meta = getattr(descriptor, "metadata", None)
    if not isinstance(meta, dict):
        return None
    fp = getattr(descriptor, "id", None) or meta.get("dataset_fingerprint")
    if not fp:
        return None
    stats = DatasetStatistics(
        dataset_fingerprint=str(fp),
        source_type=getattr(descriptor, "source_type", None),
        row_count=meta.get("row_count") or _coerce_int(getattr(descriptor, "feature_count", None)),
        extent=_coerce_bbox(getattr(descriptor, "bbox", None)) or _coerce_bbox(meta.get("bbox")),
        geometry_type=getattr(descriptor, "geometry_type", None),
        has_spatial_index=_coerce_bool(meta.get("has_geometry_index")),
        resolution=_coerce_float(meta.get("resolution")),
        overview_levels=_coerce_int(meta.get("overview_levels")),
        revision_strength=meta.get("revision_strength", "weak"),
        collector="descriptor",
    )
    col_stats = meta.get("column_statistics")
    if isinstance(col_stats, list):
        stats.columns = [
            ColumnStatistics(**c) for c in col_stats
            if isinstance(c, dict) and isinstance(c.get("name"), str)
        ][:128]
    if stats.row_count is None and not stats.columns:
        return None  # 没有任何真实统计 —— 诚实返回 None
    return stats


def _coerce_int(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _coerce_float(v: Any) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _coerce_bool(v: Any) -> Optional[bool]:
    return v if isinstance(v, bool) else None


def _coerce_bbox(v: Any) -> Optional[List[float]]:
    if isinstance(v, (list, tuple)) and len(v) == 4 and all(
        isinstance(x, (int, float)) for x in v
    ):
        return [float(x) for x in v]
    return None


class StatisticsStore:
    """进程内有界统计缓存（TTL + LRU 双界）。缓存失效 = TTL 过期或显式
    指纹失效；**绝不以缓存寿命做正确性机制**（统计弱新鲜度由 planner
    的 revision warning 表达）。"""

    def __init__(self, ttl_s: float = STATISTICS_TTL_S, max_entries: int = _STAT_MAX_ENTRIES):
        self._ttl = ttl_s
        self._max = max_entries
        self._entries: OrderedDict[str, tuple[float, DatasetStatistics]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, fingerprint: str) -> Optional[DatasetStatistics]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(fingerprint)
            if entry is None:
                return None
            ts, stats = entry
            if now - ts > self._ttl:
                del self._entries[fingerprint]
                return None
            self._entries.move_to_end(fingerprint)
            return stats

    def put(self, stats: DatasetStatistics) -> None:
        with self._lock:
            self._entries[stats.dataset_fingerprint] = (time.monotonic(), stats)
            self._entries.move_to_end(stats.dataset_fingerprint)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def invalidate(self, fingerprint: Optional[str] = None) -> None:
        with self._lock:
            if fingerprint is None:
                self._entries.clear()
            else:
                self._entries.pop(fingerprint, None)


_store = StatisticsStore()


def get_statistics(fingerprint: str) -> Optional[DatasetStatistics]:
    return _store.get(fingerprint)


def put_statistics(stats: DatasetStatistics) -> None:
    _store.put(stats)


def invalidate_statistics(fingerprint: Optional[str] = None) -> None:
    _store.invalidate(fingerprint)


def collect_postgis_statistics(fetch_all: Any, schema: str, table: str, limit_columns: int = 64) -> Dict[str, Dict[str, Any]]:
    """pg_stats 轻量探针：每列 n_distinct/null_frac（单条有界查询）。

    ``fetch_all(sql, params) -> rows`` 由调用方注入（adapter 的连接上下文），
    便于离线测试。返回 ``{column: {ndv, null_fraction}}``（未注明的是估计值
    —— pg_stats 的 n_distinct 对非常驻列本身就是估计）。
    """
    sql = (
        "SELECT attname, n_distinct, null_frac FROM pg_stats "
        "WHERE schemaname = %s AND tablename = %s LIMIT %s"
    )
    try:
        rows = fetch_all(sql, (schema, table, limit_columns))
    except Exception as exc:  # noqa: BLE001 - 统计收集绝不阻断查询路径
        logger.info("[statistics] pg_stats probe unavailable: %s", exc)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        try:
            name = str(row[0])
            ndv = row[1]
            out[name] = {
                "ndv": int(ndv) if ndv is not None and float(ndv) >= 0 else None,
                "null_fraction": float(row[2]) if row[2] is not None else None,
                "confidence": "estimated",
            }
        except (TypeError, ValueError, IndexError):
            continue
    return out
