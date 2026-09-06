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
    # ---- V4 additive（ADR-0101 D6）：footer/文件级事实 ----
    row_group_count: Optional[int] = Field(default=None, ge=0)
    total_bytes: Optional[int] = Field(default=None, ge=0)  # 压缩后文件字节

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
        row_group_count=_coerce_int(meta.get("num_row_groups")),
        revision_strength=meta.get("revision_strength", "weak"),
        # ADR-0101 D6：采集器由 descriptor 显式标注（geoparquet footer 生产者），
        # 缺省仍是 descriptor 收割。
        collector=meta.get("stats_collector") or "descriptor",
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


def statistics_for_request(descriptor: Any, fingerprint: Optional[str] = None) -> Optional[DatasetStatistics]:
    """请求期统计收割（G-F3 生产接线；V4 增加持久层旁路）。

    进程 TTL 缓存 → descriptor 收割 → **durable store**（advisory，
    fail-open）→ None。descriptor 有新鲜统计时回填两级缓存。统计是
    性能提示 —— 任何一级失败都静默降级（planner 落回 V2 常数）。
    """
    try:
        fp = str(fingerprint or getattr(descriptor, "id", "") or "")
        if not fp:
            return None
        cached = _store.get(fp)
        if cached is not None:
            return cached
        stats = statistics_from_descriptor(descriptor)
        if stats is not None:
            _store.put(stats)
            _durable_store.save(stats)
            return stats
        durable = _durable_store.load(fp)
        if durable is not None:
            _store.put(durable)
            return durable
        return None
    except Exception:  # noqa: BLE001 - 统计绝不阻断查询路径
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


def collect_geoparquet_statistics(path: str, dataset_fingerprint: str) -> Optional[DatasetStatistics]:
    """GeoParquet footer 统计（V4：兑现 V3 声明过的 collector，诚实生产者）。

    只读 footer/metadata，**绝不读列数据**：
    - pyarrow.parquet 可用时：num_rows、row-group 数、列名/类型、文件级
      geo metadata 的 bbox（GeoParquet 1.1 ``geo`` covering，存在才填）；
    - 否则回退 pyogrio.read_info（features_count / geometry_type / fields，
      零要素读取）。
    非本地路径诚实返回 None（footer 统计先覆盖本地文件场景）。
    """
    if not path or path.startswith(("http://", "https://", "s3://", "gs://")):
        return None
    row_count: Optional[int] = None
    geometry_type: Optional[str] = None
    extent: Optional[List[float]] = None
    columns: List[ColumnStatistics] = []
    row_group_count: Optional[int] = None
    total_bytes: Optional[int] = None
    try:
        try:
            import pyarrow.parquet as pq  # type: ignore

            pf = pq.ParquetFile(path)
            md = pf.metadata
            row_count = int(md.num_rows)
            row_group_count = int(md.num_row_groups)
            total_bytes = int(getattr(md, "serialized_size", 0) or 0) or None
            names = [md.schema.column(i).name for i in range(md.num_columns)]
            geo_extent = _geoparquet_covering_bbox(pf)
            if geo_extent is not None:
                extent = geo_extent
        except ImportError:
            import pyogrio  # type: ignore

            info = pyogrio.read_info(path)
            row_count = _coerce_int(int(info.get("features") or 0)) if info.get("features") is not None else None
            geometry_type = info.get("geometry_type")
            names = [f.get("name") for f in (info.get("fields") or [])]
    except Exception as exc:  # noqa: BLE001 - 统计收集绝不阻断查询路径
        logger.debug("[statistics] geoparquet footer unavailable for %s: %s", path, exc)
        return None
    for name in names or []:
        if isinstance(name, str) and name:
            # footer 只证明列存在；null/NDV 未知 → 每列 honest assumption。
            columns.append(ColumnStatistics(name=name, confidence="assumption"))
    stats = DatasetStatistics(
        dataset_fingerprint=str(dataset_fingerprint),
        source_type="geoparquet",
        row_count=row_count,
        extent=extent,
        geometry_type=geometry_type,
        columns=columns,
        row_group_count=row_group_count,
        total_bytes=total_bytes,
        revision_strength="strong",  # footer 是文件内容元数据：内容变 → 指纹变
        collector="geoparquet_footer",
    )
    return stats


def _geoparquet_covering_bbox(parquet_file: Any) -> Optional[List[float]]:
    """读 GeoParquet 文件级 ``geo`` metadata 的 bbox（存在才填，绝不猜）。"""
    try:
        kv = parquet_file.metadata.metadata or {}
        raw = kv.get(b"geo") or kv.get("geo")
        if not raw:
            return None
        import json

        geo = json.loads(raw)
        bbox = geo.get("bbox") if isinstance(geo, dict) else None
        return _coerce_bbox(bbox)
    except Exception:  # noqa: BLE001 - metadata 解析失败 = 没有这个事实
        return None


class DurableStatisticsStore:
    """统计的 **advisory** DB 持久层（ADR-0101 D6，fail-open）。

    - 读：按 dataset 指纹取最新行；过期行（expires_at 已过）拒绝并视为无。
    - 写：INSERT 新行（append-only；旧行由 prune 有界清理），绝不 UPDATE
      既有行的 stats —— stale 语义 = 时间戳比较，不是改写。
    - 所有 DB 故障吞掉并回 None/False：统计绝不阻断查询，绝不成为真相。
    """

    def __init__(
        self,
        *,
        ttl_s: float = 3600.0,
        max_rows: int = 10_000,
        prune_batch: int = 500,
    ):
        self._ttl_s = ttl_s
        self._max_rows = max_rows
        self._prune_batch = prune_batch

    def _session(self) -> Any:
        from app.core.database import SessionLocal

        return SessionLocal()

    def load(self, dataset_fingerprint: str, *, now: Optional[Any] = None) -> Optional[DatasetStatistics]:
        from datetime import datetime, timezone

        try:
            from app.models.data_fabric import DatasetStatisticsRecord

            cutoff = now or datetime.now(timezone.utc).replace(tzinfo=None)
            with self._session() as db:
                row = (
                    db.query(DatasetStatisticsRecord)
                    .filter(DatasetStatisticsRecord.dataset_fingerprint == str(dataset_fingerprint))
                    .order_by(DatasetStatisticsRecord.collected_at.desc())
                    .limit(1)
                    .first()
                )
                if row is None:
                    return None
                if row.expires_at is not None and row.expires_at < cutoff:
                    return None  # 显式过期 = 无统计（诚实 stale 语义）
                stats = _stats_from_row(row)
                return stats
        except Exception as exc:  # noqa: BLE001 - advisory 层 fail-open
            logger.debug("[statistics] durable load unavailable: %s", exc)
            return None

    def save(self, stats: DatasetStatistics) -> bool:
        from datetime import datetime, timedelta, timezone

        try:
            from app.models.data_fabric import DatasetStatisticsRecord

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            with self._session() as db:
                db.add(DatasetStatisticsRecord(
                    dataset_fingerprint=stats.dataset_fingerprint[:64],
                    source_type=stats.source_type,
                    collector=stats.collector,
                    confidence=stats.confidence,
                    revision_strength=stats.revision_strength,
                    stats_json=stats.model_dump(mode="json"),
                    collected_at=now,
                    expires_at=now + timedelta(seconds=self._ttl_s),
                ))
                db.commit()
            return True
        except Exception as exc:  # noqa: BLE001 - advisory 层 fail-open
            logger.debug("[statistics] durable save unavailable: %s", exc)
            return False

    def prune(self, *, now: Optional[Any] = None) -> int:
        """有界清理：过期行 + 超出保留上限的旧行（best-effort）。"""
        from datetime import datetime, timezone

        try:
            from app.core.database import SessionLocal
            from app.models.data_fabric import DatasetStatisticsRecord
            from sqlalchemy import delete

            cutoff = now or datetime.now(timezone.utc).replace(tzinfo=None)
            removed = 0
            with SessionLocal() as db:
                expired = [
                    r[0] for r in db.query(DatasetStatisticsRecord.id).filter(
                        DatasetStatisticsRecord.expires_at.isnot(None),
                        DatasetStatisticsRecord.expires_at < cutoff,
                    ).limit(self._prune_batch).all()
                ]
                if expired:
                    db.execute(delete(DatasetStatisticsRecord).where(
                        DatasetStatisticsRecord.id.in_(expired)))
                    removed += len(expired)
                # 保留上限：按 collected_at 倒序保留 max_rows，多余删除。
                extra = [
                    r[0] for r in db.query(DatasetStatisticsRecord.id).order_by(
                        DatasetStatisticsRecord.collected_at.desc()
                    ).offset(self._max_rows).limit(self._prune_batch).all()
                ]
                if extra:
                    db.execute(delete(DatasetStatisticsRecord).where(
                        DatasetStatisticsRecord.id.in_(extra)))
                    removed += len(extra)
                db.commit()
            return removed
        except Exception as exc:  # noqa: BLE001 - advisory 层 fail-open
            logger.debug("[statistics] durable prune unavailable: %s", exc)
            return 0


def _stats_from_row(row: Any) -> Optional[DatasetStatistics]:
    try:
        payload = row.stats_json
        if not isinstance(payload, dict):
            return None
        stats = DatasetStatistics.model_validate(payload)
        # 采集器/置信度以行为准（行是持久事实）。
        stats.collector = str(getattr(row, "collector", stats.collector))
        return stats
    except Exception:  # noqa: BLE001
        return None


#: 进程级 durable store 单例（与 _store 同一惯例；TTL 为 DB 侧行保留期）。
_durable_store = DurableStatisticsStore()
