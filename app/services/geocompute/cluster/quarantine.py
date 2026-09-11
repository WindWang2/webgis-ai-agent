"""GeoCompute V8 poison task quarantine（Phase F，ADR-0130 §6）。

同一 owner 域内、同一节点语义指纹的 run 反复非瞬态失败 → 计数并在
冷却窗内**快失败**（``POISON_QUARANTINED``）—— 防「毒任务」在不同
run 间反复占用集群槽位与重试预算。

诚实边界：
- 隔离是 **per-owner** 事实（与复用索引同纪律：毒不跨 owner 域传染）；
- 窗口过期 = 查询侧惰性解封（无后台清扫依赖；下一次判定自动放行）；
- 只对**非瞬态**终局失败计数（WORKER_LOSS / 瞬态类不计 —— 那是
  reclaim 与重试的职责，不是毒）；
- 全部持久化在 ``geocompute_task_quarantine``（owner_scope + 指纹复合
  主键，CAS 单语句 upsert）—— coordinator 崩溃后状态可重建。
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import select, update

from app.services.geocompute.cluster.store import _utcnow

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


#: 失败计数达到该值 → 进入隔离窗（env 可调；≥2 才有「反复」语义）。
def poison_threshold() -> int:
    return max(2, _env_int("WEBGIS_POISON_THRESHOLD", 3))


#: 隔离冷却窗（秒；env。0 = 不隔离，只记数）。
def poison_cooldown_s() -> float:
    return max(0.0, _env_float("WEBGIS_POISON_COOLDOWN_S", 1800.0))


def _default_factory():
    from app.services.geocompute.cluster.store import (
        session_factory as _store_factory,
    )

    return _store_factory()


class TaskQuarantine:
    """毒任务隔离登记（geocompute_task_quarantine 的唯一写读门面）。"""

    def __init__(self, *, factory: Optional[Any] = None,
                 threshold: Optional[int] = None,
                 cooldown_s: Optional[float] = None):
        self._factory = factory or _default_factory
        self._threshold = threshold if threshold is not None else poison_threshold()
        self._cooldown_s = cooldown_s if cooldown_s is not None else poison_cooldown_s()

    def is_quarantined(self, owner_scope: str, task_fingerprint: str,
                       *, factory: Optional[Any] = None) -> bool:
        """该 (owner, 指纹) 是否在隔离窗内（惰性解封：过期即放行）。"""
        if not owner_scope or not task_fingerprint or self._cooldown_s <= 0:
            return False
        target = factory or self._factory
        try:
            from app.models.db_model import GeoComputeTaskQuarantine

            with target() as db:
                row = db.execute(
                    select(GeoComputeTaskQuarantine.quarantined_until).where(
                        GeoComputeTaskQuarantine.owner_scope == owner_scope,
                        GeoComputeTaskQuarantine.task_fingerprint
                        == task_fingerprint,
                    )
                ).scalar_one_or_none()
                return row is not None and row > _utcnow()
        except Exception:  # noqa: BLE001 - 登记缺席/DB 抖动 → 放行（fail-open）
            return False

    def record_failure(self, owner_scope: str, task_fingerprint: str,
                       *, run_id: Optional[str] = None,
                       error_code: Optional[str] = None,
                       factory: Optional[Any] = None) -> bool:
        """记录一次非瞬态终局失败；返回是否因此进入隔离窗。

        诚实边界：实现是 select-then-update（复合主键行，**非**单语句
        upsert）—— 并发同 (owner, fp) 失败会丢失个别计数（隔离推迟，
        方向保守无害）。``failure_count`` **不随冷却窗重置**（语义 =
        「每满 threshold 次失败进入一段冷却期」，非「解封后重新计数」）
        —— 对毒任务这是刻意设计：反复失败的对象就该持续被限流。
        fail-open：任何 DB 故障返回 False（不隔离）—— 隔离是保护机制，
        绝不倒灌执行路径。
        """
        if not owner_scope or not task_fingerprint:
            return False
        target = factory or self._factory
        try:
            from app.models.db_model import GeoComputeTaskQuarantine

            now = _utcnow()
            quarantine_until = (
                now + timedelta(seconds=self._cooldown_s)
                if self._cooldown_s > 0 else None
            )
            with target() as db:
                existing = db.execute(
                    select(GeoComputeTaskQuarantine.failure_count).where(
                        GeoComputeTaskQuarantine.owner_scope == owner_scope,
                        GeoComputeTaskQuarantine.task_fingerprint
                        == task_fingerprint,
                    )
                ).scalar_one_or_none()
                if existing is None:
                    count = 1
                    quarantined = (
                        count >= self._threshold and quarantine_until is not None
                    )
                    db.add(GeoComputeTaskQuarantine(
                        owner_scope=owner_scope,
                        task_fingerprint=task_fingerprint,
                        failure_count=count,
                        last_error_code=error_code,
                        last_run_id=run_id,
                        quarantined_until=quarantine_until if quarantined else None,
                        updated_at=now,
                    ))
                else:
                    count = int(existing) + 1
                    quarantined = (
                        count >= self._threshold and quarantine_until is not None
                    )
                    db.execute(
                        update(GeoComputeTaskQuarantine)
                        .where(
                            GeoComputeTaskQuarantine.owner_scope == owner_scope,
                            GeoComputeTaskQuarantine.task_fingerprint
                            == task_fingerprint,
                        )
                        .values(
                            failure_count=count,
                            last_error_code=error_code,
                            last_run_id=run_id,
                            quarantined_until=(
                                quarantine_until if quarantined else None
                            ),
                            updated_at=now,
                        )
                    )
                db.commit()
                return quarantined
        except Exception:  # noqa: BLE001
            logger.debug("[geocompute-v8] quarantine record failed",
                         exc_info=True)
            return False

    def snapshot(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """隔离视图（metrics 用；有界）。"""
        try:
            from app.models.db_model import GeoComputeTaskQuarantine

            with self._factory() as db:
                rows = db.execute(
                    select(GeoComputeTaskQuarantine)
                    .where(GeoComputeTaskQuarantine.quarantined_until
                           .is_not(None))
                    .order_by(GeoComputeTaskQuarantine.updated_at.desc())
                    .limit(max(1, int(limit)))
                ).scalars().all()
                now = _utcnow()
                return [
                    {
                        "owner_scope": r.owner_scope[:16] + "…"  # 伪名化
                        if len(r.owner_scope) > 16 else r.owner_scope,
                        "fingerprint": r.task_fingerprint[:16],
                        "failure_count": r.failure_count,
                        "active": bool(r.quarantined_until
                                       and r.quarantined_until > now),
                        "last_error_code": r.last_error_code,
                    }
                    for r in rows
                ]
        except Exception:  # noqa: BLE001
            return []


#: 进程内单例（executor 侧懒取；测试可注入 factory 构造独立实例）。
_default_quarantine: Optional[TaskQuarantine] = None


def get_quarantine() -> TaskQuarantine:
    global _default_quarantine
    if _default_quarantine is None:
        _default_quarantine = TaskQuarantine()
    return _default_quarantine


def reset_quarantine_for_tests() -> None:
    global _default_quarantine
    _default_quarantine = None
