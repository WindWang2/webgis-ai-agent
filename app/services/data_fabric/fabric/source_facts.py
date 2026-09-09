"""SourceFacts V7（ADR-0119 W4）：作用域化数据源事实（Epic 03 Must-have C）。

与 ``query/statistics.py``（DatasetStatistics，fingerprint 键）的关系：
- DatasetStatistics **扩展**（additive），不建平行模型 —— SourceFacts 把
  它作为 ``stats`` 载荷的一部分，外加作用域/新鲜度/采样标注/provenance；
- 采集纪律（R-C3）：**plumb, not scrape** —— 只收割响应本就携带的事实
  （无过滤 count / descriptor 元数据 / 有界采样页），绝不发起新的探测扫描；
  过滤后的命中数绝不冒充 row_count；
- 持久层 advisory fail-open（与 DurableStatisticsStore 同文化）：DB 写入
  旁路线程 + 硬超时；首次失败升 warning + 失败计数（R-minor 收口）；
- scope 进记录（owner/org/project），进程缓存键含 scope —— 跨租户不串。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.data_fabric.query.statistics import DatasetStatistics

logger = logging.getLogger(__name__)


class FactsProvenance(BaseModel):
    """事实来源（诚实披露：测量/观察/采样/描述符）。"""

    collector: str = "descriptor"  # descriptor | observed_count | sampled_pages | pg_stats | footer
    sampled: bool = False
    #: 采集路径备注（例如 "numberMatched of unfiltered request"）。
    note: Optional[str] = None


class SourceFactsRecord(BaseModel):
    """一条作用域化的数据源事实快照。"""

    dataset_fingerprint: str
    scope_key: str
    profile_id: str
    source_type: Optional[str] = None
    row_count: Optional[int] = Field(default=None, ge=0)
    #: exact（源真实总量）| observed（无过滤请求观察计数）| estimate | unknown
    row_count_basis: str = "unknown"
    extent: Optional[List[float]] = None
    crs: Optional[str] = None
    geometry_family: Optional[str] = None  # point|line|polygon|...
    #: 列 → NDV（pg_stats 等测量来源；估计值放 estimate_ndv）。
    ndv: Dict[str, int] = Field(default_factory=dict)
    null_fraction: Dict[str, float] = Field(default_factory=dict)
    spatial_histogram: Optional[Dict[str, Any]] = None
    avg_geometry_complexity: Optional[float] = Field(default=None, gt=0)
    temporal_extent: Optional[List[str]] = None  # [start, end] ISO
    freshness_collected_at: float = Field(default_factory=time.time)
    provenance: FactsProvenance = Field(default_factory=FactsProvenance)
    #: weak | strong（沿 DatasetStatistics 语义）。
    revision_strength: str = "weak"
    expires_at: Optional[float] = None

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at

    def to_dataset_statistics(self) -> DatasetStatistics:
        """→ 既有统计模型（V6 costing/spatial_stats 直接消费）。"""
        return DatasetStatistics(
            dataset_fingerprint=self.dataset_fingerprint,
            source_type=self.source_type,
            row_count=self.row_count,
            extent=list(self.extent) if self.extent else None,
            geometry_type=self.geometry_family,
            columns=[
                # NDV/null fraction 进列统计（measured/estimated 按 basis）。
                _col_stats(name, ndv, self.null_fraction.get(name))
                for name, ndv in self.ndv.items()
            ],
            crs=self.crs,
            avg_vertices=self.avg_geometry_complexity,
            spatial_histogram=self.spatial_histogram,
            revision_strength=self.revision_strength,
            collected_at=datetime.fromtimestamp(
                self.freshness_collected_at, tz=timezone.utc
            ).isoformat(),
            collector=self.provenance.collector,
        )


def _col_stats(name: str, ndv: Optional[int], null_fraction: Optional[float]):
    from app.services.data_fabric.query.statistics import ColumnStatistics

    confidence = "measured" if ndv is not None else "assumption"
    return ColumnStatistics(
        name=name, ndv=ndv, null_fraction=null_fraction, confidence=confidence
    )


def facts_from_descriptor(
    descriptor: Any,
    *,
    fingerprint: str,
    scope_key: str,
    profile_id: str,
) -> Optional[SourceFactsRecord]:
    """descriptor → SourceFacts（纯函数，无 IO；诚实 None）。"""
    if not fingerprint:
        return None
    meta = getattr(descriptor, "metadata", None)
    meta = meta if isinstance(meta, dict) else {}
    row_count = meta.get("row_count")
    if row_count is None:
        fc = getattr(descriptor, "feature_count", None)
        row_count = fc if isinstance(fc, int) and fc >= 0 else None
    extent = getattr(descriptor, "bbox", None)
    extent = [float(x) for x in extent] if (
        isinstance(extent, (list, tuple)) and len(extent) == 4
    ) else None
    crs = meta.get("srs") or meta.get("crs") or getattr(descriptor, "srs", None)
    hist = meta.get("spatial_histogram") if isinstance(meta.get("spatial_histogram"), dict) else None
    avg_v = meta.get("avg_vertices")
    if not any(v is not None for v in (row_count, extent, hist)):
        return None  # 没有任何事实 —— 诚实 None
    return SourceFactsRecord(
        dataset_fingerprint=fingerprint,
        scope_key=scope_key,
        profile_id=profile_id,
        source_type=getattr(descriptor, "source_type", None),
        row_count=row_count,
        row_count_basis="estimate" if row_count is not None else "unknown",
        extent=extent,
        crs=crs if isinstance(crs, str) and crs else None,
        geometry_family=getattr(descriptor, "geometry_type", None),
        spatial_histogram=hist,
        avg_geometry_complexity=float(avg_v) if isinstance(avg_v, (int, float)) and avg_v > 0 else None,
        provenance=FactsProvenance(collector="descriptor"),
    )


# ── 持久层（advisory fail-open）─────────────────────────────────────────


class DurableSourceFactsStore:
    """source_facts 的 DB 持久层（append-only + prune；fail-open）。

    - 表 ``data_fabric_source_facts``（migration 0034）；
    - 读：scope+fingerprint 最新行，过期拒绝；
    - 写：旁路线程 + 硬超时（DurableStatisticsStore 同款）；首次失败 warning。
    """

    DB_TIMEOUT_S = 3.0

    def __init__(self, *, ttl_s: float = 3600.0, max_rows: int = 20_000, prune_batch: int = 500):
        self._ttl_s = float(ttl_s)
        self._max_rows = int(max_rows)
        self._prune_batch = int(prune_batch)
        self._pool = None
        self._pool_lock = threading.Lock()
        self._failure_count = 0
        self._warned = False

    def _executor(self):
        from concurrent.futures import ThreadPoolExecutor

        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="facts-db")
            return self._pool

    def _call_with_timeout(self, fn, *args):
        return self._executor().submit(fn, *args).result(timeout=self.DB_TIMEOUT_S)

    def _on_failure(self, exc: Exception, op: str) -> None:
        self._failure_count += 1
        if not self._warned:
            logger.warning("[source-facts] durable %s unavailable (fail-open): %s", op, exc)
            self._warned = True

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def load(self, scope_key: str, dataset_fingerprint: str) -> Optional[SourceFactsRecord]:
        from datetime import datetime as _dt

        try:
            from app.models.data_fabric import SourceFactsRecordModel

            cutoff = _dt.now(timezone.utc).replace(tzinfo=None)

            def _query():
                from app.core.database import SessionLocal

                with SessionLocal() as db:
                    return (
                        db.query(SourceFactsRecordModel)
                        .filter(
                            SourceFactsRecordModel.scope_key == scope_key,
                            SourceFactsRecordModel.dataset_fingerprint
                            == str(dataset_fingerprint)[:64],
                        )
                        .order_by(SourceFactsRecordModel.collected_at.desc())
                        .limit(1)
                        .first()
                    )

            row = self._call_with_timeout(_query)
            if row is None:
                return None
            if row.expires_at is not None and row.expires_at < cutoff:
                return None
            payload = row.facts_json
            if not isinstance(payload, dict):
                return None
            return SourceFactsRecord.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - advisory fail-open
            self._on_failure(exc, "load")
            return None

    def save(self, facts: SourceFactsRecord) -> bool:
        try:
            from datetime import datetime as _dt

            from app.models.data_fabric import SourceFactsRecordModel

            now = _dt.now(timezone.utc).replace(tzinfo=None)

            def _insert():
                from app.core.database import SessionLocal

                with SessionLocal() as db:
                    db.add(
                        SourceFactsRecordModel(
                            scope_key=facts.scope_key[:128],
                            dataset_fingerprint=facts.dataset_fingerprint[:64],
                            profile_id=facts.profile_id[:64],
                            collector=facts.provenance.collector[:32],
                            facts_json=facts.model_dump(mode="json"),
                            collected_at=now,
                            expires_at=now + timedelta(seconds=self._ttl_s),
                        )
                    )
                    db.commit()

            self._call_with_timeout(_insert)
            return True
        except Exception as exc:  # noqa: BLE001 - advisory fail-open
            self._on_failure(exc, "save")
            return False

    def prune(self) -> int:
        """过期行 + 超限旧行的有界清理（best-effort）。"""
        try:
            from datetime import datetime as _dt

            from sqlalchemy import delete

            from app.core.database import SessionLocal
            from app.models.data_fabric import SourceFactsRecordModel

            cutoff = _dt.now(timezone.utc).replace(tzinfo=None)
            removed = 0
            with SessionLocal() as db:
                expired = [
                    r[0]
                    for r in db.query(SourceFactsRecordModel.id)
                    .filter(SourceFactsRecordModel.expires_at.isnot(None),
                            SourceFactsRecordModel.expires_at < cutoff)
                    .limit(self._prune_batch)
                    .all()
                ]
                if expired:
                    db.execute(delete(SourceFactsRecordModel).where(
                        SourceFactsRecordModel.id.in_(expired)))
                    removed += len(expired)
                extra = [
                    r[0]
                    for r in db.query(SourceFactsRecordModel.id)
                    .order_by(SourceFactsRecordModel.collected_at.desc())
                    .offset(self._max_rows)
                    .limit(self._prune_batch)
                    .all()
                ]
                if extra:
                    db.execute(delete(SourceFactsRecordModel).where(
                        SourceFactsRecordModel.id.in_(extra)))
                    removed += len(extra)
                db.commit()
            return removed
        except Exception as exc:  # noqa: BLE001 - advisory fail-open
            self._on_failure(exc, "prune")
            return 0


# ── 进程内服务 ──────────────────────────────────────────────────────────


class SourceFactsService:
    """作用域事实服务：进程缓存 → durable → descriptor 采集（fail-open）。"""

    def __init__(
        self,
        *,
        durable: Optional[DurableSourceFactsStore] = None,
        ttl_s: float = 300.0,
        max_entries: int = 2048,
    ):
        self._durable = durable or DurableSourceFactsStore()
        self._ttl = float(ttl_s)
        self._max = int(max_entries)
        self._entries: "OrderedDict[tuple, SourceFactsRecord]" = OrderedDict()
        self._lock = threading.Lock()

    def get(
        self,
        *,
        scope_key: str,
        fingerprint: str,
        descriptor: Any = None,
        profile_id: str = "",
    ) -> Optional[SourceFactsRecord]:
        """读事实（缓存 → durable → descriptor 现场采集；回填两级）。"""
        key = (scope_key, str(fingerprint))
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None and not cached.is_expired():
                self._entries.move_to_end(key)
                return cached
            if cached is not None:
                self._entries.pop(key, None)
        facts: Optional[SourceFactsRecord] = None
        durable_row = self._durable.load(scope_key, fingerprint)
        if durable_row is not None:
            facts = durable_row
        if facts is None and descriptor is not None:
            facts = facts_from_descriptor(
                descriptor, fingerprint=str(fingerprint),
                scope_key=scope_key, profile_id=profile_id,
            )
            if facts is not None:
                self._durable.save(facts)
        if facts is not None:
            facts.expires_at = time.time() + self._ttl
            with self._lock:
                self._entries[key] = facts
                self._entries.move_to_end(key)
                while len(self._entries) > self._max:
                    self._entries.popitem(last=False)
        return facts

    def observe_unfiltered_count(
        self,
        *,
        scope_key: str,
        fingerprint: str,
        profile_id: str,
        source_type: Optional[str],
        count: int,
        note: Optional[str] = None,
    ) -> bool:
        """无过滤请求的观察计数（R-C3 契约：调用方必须保证请求无过滤）。"""
        try:
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                return False
            if not fingerprint:
                return False
            existing = self.get(scope_key=scope_key, fingerprint=fingerprint,
                                profile_id=profile_id)
            base = existing.model_copy(deep=True) if existing else SourceFactsRecord(
                dataset_fingerprint=str(fingerprint), scope_key=scope_key,
                profile_id=profile_id, source_type=source_type,
            )
            base.row_count = count
            base.row_count_basis = "observed"
            base.provenance = FactsProvenance(
                collector="observed_count", sampled=False,
                note=note or "count observed from unfiltered request",
            )
            base.freshness_collected_at = time.time()
            base.expires_at = time.time() + self._ttl
            key = (scope_key, str(fingerprint))
            with self._lock:
                self._entries[key] = base
                self._entries.move_to_end(key)
            self._durable.save(base)
            return True
        except Exception as exc:  # noqa: BLE001 - 事实收割绝不阻断查询
            logger.debug("[source-facts] observe count failed: %s", exc)
            return False

    def invalidate(self, fingerprint: Optional[str] = None) -> int:
        removed = 0
        with self._lock:
            for key in list(self._entries.keys()):
                if fingerprint is None or key[1] == str(fingerprint):
                    self._entries.pop(key, None)
                    removed += 1
        return removed

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "durable_failures": self._durable.failure_count,
            }


_service: Optional[SourceFactsService] = None
_service_lock = threading.Lock()


def get_source_facts_service() -> SourceFactsService:
    global _service
    with _service_lock:
        if _service is None:
            _service = SourceFactsService()
        return _service


def reset_source_facts_service() -> None:
    global _service
    with _service_lock:
        _service = None


__all__ = [
    "DurableSourceFactsStore",
    "FactsProvenance",
    "SourceFactsRecord",
    "SourceFactsService",
    "facts_from_descriptor",
    "get_source_facts_service",
    "reset_source_facts_service",
]
