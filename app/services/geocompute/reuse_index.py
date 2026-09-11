"""durable 节点的跨进程 checkpoint 复用索引（audit 06 §6.1 step 3，V5）。

V4 的 NodeResultStore 是进程内 LRU —— durable 分支的产物（最贵的节点）
从不进入复用：跨 worker / 跨 restart 的 checkpoint 复用不存在。V5 在
durable 节点**完成后**把 ``{owner_scope, node_semantic_fingerprint,
result_ref, session_id, upstream_fingerprints, created_at}`` upsert 进
``geocompute_node_results``（表真相见 app/models/db_model.py，迁移 0029）；
执行器在**派发前**查本索引：命中 + 上游输出指纹一致 + result_ref 仍可
解析 → 复用，跳过派发。

边界（诚实声明）：
- **缓存，不是第二真相**：job 真相仍在 analysis_tasks；本表无状态迁移，
  写入方 fail-open（DB 不可用 → 视为未命中，重算 —— 诚实但变慢）；
- owner 域沿用 ``executor.owner_scope_for`` 的哈希域，绝不跨 owner 共享；
- 有界：(owner, fingerprint) 唯一（重写即刷新 created_at = LRU 触点），
  每 owner 只保留最近 ``MAX_RESULTS_PER_OWNER``（64）条（写入时剪枝）；
- result_ref 解析随行的 ``session_id`` —— ref 是会话存储指针，离开
  session_id 无法解析。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

logger = logging.getLogger(__name__)

#: 每 owner 的结果条目上限（写入时按 created_at LRU 剪枝，audit step 3）。
MAX_RESULTS_PER_OWNER = 64


def _default_session_factory():
    # 返回 **Session 实例**（jobs 层同一纪律；sessionmaker 在 SQLAlchemy 2.0
    # 无上下文协议，``with session_factory()`` 会 TypeError → 记录被
    # fail-open 静默丢弃 —— V6 round2 review C3 修复）。
    from app.core.database import SessionLocal

    return SessionLocal()


#: 可注入的会话工厂（测试替换为临时 SQLite 工厂）；用法与 durable.session_factory
#: 一致：``with session_factory() as db:``。
session_factory: Callable[[], Any] = _default_session_factory


def _utcnow() -> datetime:
    # 与 jobs/store.py 同一约定：naive UTC（DB 列是 naive DateTime）。
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record_result(
    *,
    owner_scope: str,
    node_fingerprint: str,
    result_ref: str,
    session_id: str,
    upstream_fingerprints: Optional[dict[str, str]],
    max_per_owner: int = MAX_RESULTS_PER_OWNER,
) -> bool:
    """记录（或刷新）一条复用索引；写入时把该 owner 剪枝到 ``max_per_owner``。

    fail-open：任何 DB 异常返回 False（调用方只当「索引没写上」，绝不影响
    执行结果）。
    """
    if not owner_scope or not node_fingerprint or not result_ref or not session_id:
        return False
    try:
        with session_factory() as db:
            from app.core import tenancy
            from app.models.db_model import GeoComputeClusterRun, GeoComputeNodeResult

            # ADR-0139：org 锚定 owner 域 → 关联 run 行真相（同 owner 同 org），
            # 兜底 default 隔离桶 —— 复用索引行永不为 NULL。
            org_id = db.execute(
                select(GeoComputeClusterRun.org_id)
                .where(GeoComputeClusterRun.owner_scope == owner_scope)
                .order_by(GeoComputeClusterRun.id.desc())
                .limit(1)
            ).scalar_one_or_none() or tenancy.get_or_create_default_org_id_sync(db)

            existing = (
                db.query(GeoComputeNodeResult)
                .filter(
                    GeoComputeNodeResult.owner_scope == owner_scope,
                    GeoComputeNodeResult.node_fingerprint == node_fingerprint,
                )
                .first()
            )
            if existing is not None:
                # 重写即刷新（LRU 触点 = created_at）
                db.delete(existing)
                db.flush()
            db.add(GeoComputeNodeResult(
                org_id=org_id,
                owner_scope=owner_scope[:40],
                node_fingerprint=node_fingerprint[:32],
                result_ref=result_ref[:512],
                session_id=session_id[:255],
                upstream_fingerprints=_bounded_upstream(upstream_fingerprints),
                created_at=_utcnow(),
            ))
            db.flush()
            _prune_owner(db, owner_scope, max_per_owner)
            db.commit()
            return True
    except Exception:  # noqa: BLE001 - 缓存写入绝不倒灌执行路径
        logger.debug("[geocompute] reuse index record failed", exc_info=True)
        return False


def find_result(owner_scope: str, node_fingerprint: str) -> Optional[dict[str, Any]]:
    """按 (owner, 节点语义指纹) 查最新条目；未命中/DB 不可用 → None。

    返回 ``{result_ref, session_id, upstream_fingerprints, created_at}``；
    上游一致性校验与 ref 存活探测由调用方（executor）执行 —— 本模块只寻址。
    """
    if not owner_scope or not node_fingerprint:
        return None
    try:
        with session_factory() as db:
            from app.models.db_model import GeoComputeNodeResult

            row = (
                db.query(GeoComputeNodeResult)
                .filter(
                    GeoComputeNodeResult.owner_scope == owner_scope,
                    GeoComputeNodeResult.node_fingerprint == node_fingerprint,
                )
                .first()
            )
            if row is None:
                return None
            return {
                "result_ref": row.result_ref,
                "session_id": row.session_id,
                "upstream_fingerprints": dict(row.upstream_fingerprints or {}),
                "created_at": row.created_at,
            }
    except Exception:  # noqa: BLE001 - 缓存读取 fail-open
        logger.debug("[geocompute] reuse index lookup failed", exc_info=True)
        return None


def delete_result(owner_scope: str, node_fingerprint: str) -> bool:
    """移除死条目（result_ref 探测未命中时的索引卫生）；fail-open。"""
    if not owner_scope or not node_fingerprint:
        return False
    try:
        with session_factory() as db:
            from app.models.db_model import GeoComputeNodeResult

            deleted = (
                db.query(GeoComputeNodeResult)
                .filter(
                    GeoComputeNodeResult.owner_scope == owner_scope,
                    GeoComputeNodeResult.node_fingerprint == node_fingerprint,
                )
                .delete()
            )
            db.commit()
            return bool(deleted)
    except Exception:  # noqa: BLE001
        logger.debug("[geocompute] reuse index delete failed", exc_info=True)
        return False


def _bounded_upstream(upstream: Optional[dict[str, str]]) -> dict[str, str]:
    """有界投影：{node_id: fp}，≤64 项、值为字符串（与节点字段上限一致）。"""
    if not isinstance(upstream, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in list(upstream.items())[:64]:
        out[str(key)[:128]] = str(value)[:64]
    return out


def _prune_owner(db: Any, owner_scope: str, max_per_owner: int) -> None:
    """按 created_at LRU 把该 owner 的条目剪到 ``max_per_owner``。"""
    from sqlalchemy import select

    from app.models.db_model import GeoComputeNodeResult

    stale_ids = list(
        db.execute(
            select(GeoComputeNodeResult.id)
            .where(GeoComputeNodeResult.owner_scope == owner_scope)
            .order_by(
                GeoComputeNodeResult.created_at.desc(), GeoComputeNodeResult.id.desc()
            )
            .offset(max(0, int(max_per_owner)))
        ).scalars()
    )
    if stale_ids:
        db.query(GeoComputeNodeResult).filter(
            GeoComputeNodeResult.id.in_(stale_ids)
        ).delete(synchronize_session=False)
