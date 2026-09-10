"""GeoCompute V7 数据局部性模型（wave 7-8，01-architecture.md §2.3）。

三件事，全部纯函数或有界注册表操作：

1. **LocalityKey 派生**（``locality_keys``）：durable 节点输入身份的有界集合
   （dataset 指纹 / data_object_id / provenance 源身份）。纯函数、确定性。
2. **缓存注册表 API**（``WorkerCacheRegistry``）：``geocompute_worker_cache``
   的登记/命中/打分投影。位置**声明**而非真相 —— 一切不一致的失败方向
   都是 miss → 走 session/BlobStore 重物化（性能损失非正确性损失）。
3. **跨 owner 不泄漏**：cache_key = sha256(owner_scope + ":" + locality_key)；
   查找/登记/打分全部按 owner 派生键精确匹配 —— 同一公共数据集的两个
   owner 在注册表与 worker 本地盘（缓存键同绑定 owner 域）互不可见。

登记失败 / TTL 竞态 / run 行缺席一律 fail-open：注册表是尽力而为的
放置优化输入，绝不倒灌执行路径。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import delete, func, select

from app.models.db_model import GeoComputeWorkerCache as _Cache
from app.services.geocompute.cluster.store import (
    _utcnow,
    hash_scope_key,
)

logger = logging.getLogger(__name__)

#: per-worker 容量闸（写入侧 LRU 逐出 —— 注册表行数有界，防注册表被刷爆）。
MAX_CACHE_ENTRIES_PER_WORKER = 64
MAX_CACHE_BYTES_PER_WORKER = 4 * 1024 ** 3

#: locality key 词边界：DataObject id（sha256 hex 64）。
_DATA_OBJECT_ID_RE = re.compile(r"^[a-f0-9]{64}\Z")

#: 参数中可作 locality 键的 provenance 键名（有界白名单；与 api._LINEAGE_PARAM_HINTS
#: 同族但独立维护 —— locality 键要求内容身份可证，不接受 artifact 任意词）。
_LOCALITY_PARAM_KEYS: tuple[str, ...] = (
    "data_object_id",
    "dataset_fingerprint",
    "source_fingerprint",
)

#: 单节点 locality 键上界（有界输入 → 有界打分计算）。
_MAX_KEYS_PER_NODE = 16


def locality_keys(node: Any) -> frozenset[str]:
    """durable 节点 → 内容身份键集（确定性纯函数；≤16 个，超界截断）。

    来源（全部**可证身份**，无自由串）：
    - ``dataset_fingerprints`` 的**值**（内容指纹，owner 无关）；
    - 参数中 data_object_id / dataset_fingerprint / source_fingerprint
      （sha256 词表校验或非空串钳制）。
    """
    keys: set[str] = set()
    for fp in (getattr(node, "dataset_fingerprints", None) or {}).values():
        text = str(fp or "").strip()
        if text:
            keys.add(text[:128])
    params = getattr(node, "parameters", None) or {}
    for key in _LOCALITY_PARAM_KEYS:
        value = params.get(key)
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text:
                continue
            if key == "data_object_id" and not _DATA_OBJECT_ID_RE.match(text):
                continue  # 非 sha256 词表 → 不是可证内容身份，拒绝（无注入面）
            keys.add(text[:128])
            if len(keys) >= _MAX_KEYS_PER_NODE:
                return frozenset(sorted(keys)[:_MAX_KEYS_PER_NODE])
    return frozenset(sorted(keys)[:_MAX_KEYS_PER_NODE])


def cache_key_for(owner_scope: str, locality_key: str) -> str:
    """(owner 域, 内容键) → 注册表键（跨 owner 永不共享寻址）。"""
    return hashlib.sha256(
        f"{owner_scope}:{locality_key}".encode("utf-8")
    ).hexdigest()


def _default_factory():
    # 调用时动态解析（与 events 同纪律：缓存注册表必须与 run 行同库；
    # 静态绑定会让测试 monkeypatch / 多库部署失效）。
    from app.services.geocompute.cluster import store as _store_mod

    return _store_mod.session_factory()


class WorkerCacheRegistry:
    """``geocompute_worker_cache`` 门面（登记/命中/打分投影；全部有界）。"""

    def __init__(self, factory: Optional[Callable[[], Any]] = None):
        self._factory = factory or _default_factory

    # ------------------------------------------------------------ write

    def record_put(
        self,
        worker_id: str,
        owner_scope: str,
        locality_key: str,
        *,
        size_bytes: int = 0,
    ) -> bool:
        """成功物化**之后**的位置登记（upsert；失败 fail-open）。"""
        if not worker_id or not owner_scope or not locality_key:
            return False
        key = cache_key_for(owner_scope, locality_key)
        scope_hash = hash_scope_key(owner_scope, "o:") or "o:anonymous"
        size = max(0, min(int(size_bytes or 0), MAX_CACHE_BYTES_PER_WORKER))
        try:
            with self._factory() as db:
                existing = db.execute(
                    select(_Cache).where(
                        _Cache.worker_id == worker_id, _Cache.cache_key == key
                    )
                ).scalar_one_or_none()
                now = _utcnow()
                if existing is None:
                    count = db.execute(
                        select(func.count())
                        .select_from(_Cache)
                        .where(_Cache.worker_id == worker_id)
                    ).scalar_one()
                    if int(count) >= MAX_CACHE_ENTRIES_PER_WORKER:
                        # LRU 逐出：最久未命中的条目让位（有界批 1 条/次）
                        oldest = db.execute(
                            select(_Cache.worker_id, _Cache.cache_key)
                            .where(_Cache.worker_id == worker_id)
                            .order_by(_Cache.last_hit_at.asc())
                            .limit(1)
                        ).first()
                        if oldest is not None:
                            db.execute(
                                delete(_Cache).where(
                                    _Cache.worker_id == oldest[0],
                                    _Cache.cache_key == oldest[1],
                                )
                            )
                    # round1 m5：字节总闸（4GiB）—— 逐出最久未命中直到
                    # 新条目能放下（SUM 查询 O(entries≤64)，写入非热路径）
                    while True:
                        total = int(db.execute(
                            select(func.coalesce(func.sum(_Cache.size_bytes), 0))
                            .where(_Cache.worker_id == worker_id)
                        ).scalar_one())
                        if total + size <= MAX_CACHE_BYTES_PER_WORKER:
                            break
                        victim = db.execute(
                            select(_Cache.worker_id, _Cache.cache_key)
                            .where(_Cache.worker_id == worker_id)
                            .order_by(_Cache.last_hit_at.asc())
                            .limit(1)
                        ).first()
                        if victim is None:
                            break
                        db.execute(
                            delete(_Cache).where(
                                _Cache.worker_id == victim[0],
                                _Cache.cache_key == victim[1],
                            )
                        )
                    db.add(_Cache(
                        worker_id=worker_id, cache_key=key,
                        owner_scope=scope_hash, size_bytes=size,
                        cached_at=now, last_hit_at=now,
                    ))
                else:
                    existing.last_hit_at = now
                    existing.size_bytes = size
                db.commit()
                return True
        except Exception:  # noqa: BLE001 - 登记失败绝不抛进节点执行
            return False

    def record_hit(
        self, worker_id: str, owner_scope: str, locality_key: str
    ) -> bool:
        """命中刷新（last_hit_at；LRU 依据；失败静默）。"""
        key = cache_key_for(owner_scope, locality_key)
        try:
            with self._factory() as db:
                rowcount = db.execute(
                    _Cache.__table__.update()
                    .where(_Cache.worker_id == worker_id, _Cache.cache_key == key)
                    .values(last_hit_at=_utcnow())
                ).rowcount
                db.commit()
                return bool(rowcount)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------- read

    def worker_holds(
        self,
        worker_id: str,
        owner_scope: str,
        locality_keys: Iterable[str],
    ) -> int:
        """该 worker 是否持有本 owner 的这些内容键（命中数；打分输入）。"""
        keys = [cache_key_for(owner_scope, k) for k in locality_keys]
        if not keys:
            return 0
        try:
            with self._factory() as db:
                held = db.execute(
                    select(func.count())
                    .select_from(_Cache)
                    .where(_Cache.worker_id == worker_id,
                           _Cache.cache_key.in_(keys))
                ).scalar_one()
                return int(held)
        except Exception:  # noqa: BLE001 - 注册表不可用 → 局部性信息缺席（rank 退化）
            return 0

    def worker_cached_bytes(self, worker_id: str) -> int:
        try:
            with self._factory() as db:
                total = db.execute(
                    select(func.coalesce(func.sum(_Cache.size_bytes), 0))
                    .where(_Cache.worker_id == worker_id)
                ).scalar_one()
                return int(total)
        except Exception:  # noqa: BLE001
            return 0

    def worker_entries(self, worker_id: str) -> int:
        try:
            with self._factory() as db:
                return int(db.execute(
                    select(func.count())
                    .select_from(_Cache)
                    .where(_Cache.worker_id == worker_id)
                ).scalar_one())
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------ sweep

    def purge_older_than(self, *, older_than_s: float, limit: int = 64) -> int:
        """TTL 清理（每 tick 有界批；失败下轮再试）。"""
        cutoff = _utcnow() - timedelta(seconds=max(60.0, float(older_than_s)))
        try:
            with self._factory() as db:
                keys = db.execute(
                    select(_Cache.worker_id, _Cache.cache_key)
                    .where(_Cache.cached_at < cutoff)
                    .order_by(_Cache.cached_at.asc())
                    .limit(max(1, int(limit)))
                ).all()
                if not keys:
                    return 0
                # round2 Rn3：合并为单条 tuple-IN 删除（PG 每语句往返省
                # ≤64 次/tick）
                from sqlalchemy import or_, and_ as _and

                conds = [
                    _and(_Cache.worker_id == wid, _Cache.cache_key == key)
                    for wid, key in keys
                ]
                deleted = db.execute(
                    delete(_Cache).where(or_(*conds))
                ).rowcount
                db.commit()
                return int(deleted)
        except Exception:  # noqa: BLE001
            return 0

    def drop_worker(self, worker_id: str) -> int:
        """worker 注销/prune 时删除其全部位置声明（防幽灵位置）。"""
        try:
            with self._factory() as db:
                deleted = db.execute(
                    delete(_Cache).where(_Cache.worker_id == worker_id)
                ).rowcount
                db.commit()
                return int(deleted or 0)
        except Exception:  # noqa: BLE001
            return 0
