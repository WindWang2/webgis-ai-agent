"""Workflow Runtime V5 —— 节点复用索引（缓存，fail-open）。

边界（与 geocompute reuse_index 同纪律）：

- **缓存，不是第二真相**：本表无状态迁移；DB 不可用 → 未命中 → 重算
  （诚实但变慢）；写入方 fail-open；
- owner 域隔离：``(owner_scope, reuse_fingerprint)`` 唯一；anonymous 域
  内附加 session 同域约束 [R1-MINOR-5]；绝不跨 owner 共享；
- 有界：每 owner LRU ≤128 条（写入时按 created_at 剪枝）；
- ``fingerprint_level='shape'`` 只记录不复用 [R1-M3]。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy.exc import OperationalError

from app.services.workflow_runtime import fingerprints as F

logger = logging.getLogger(__name__)

#: 每 owner 条目上限（写入时 LRU 剪枝）。
MAX_ENTRIES_PER_OWNER = 128


def _default_session_factory():
    from app.core.database import SessionLocal

    return SessionLocal()


session_factory: Callable[[], Any] = _default_session_factory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ReuseRecord:
    """一条复用索引条目（内存形态）。"""

    __slots__ = ("owner_scope", "reuse_fingerprint", "session_scope",
                 "node_id", "package_fingerprint", "artifact_ref",
                 "artifact_session_id", "fingerprint_level",
                 "input_fingerprints", "algorithm_id", "params_fp",
                 "env_fp", "source_instance_id")

    def __init__(self, *, owner_scope: str, reuse_fingerprint: str,
                 session_scope: str, node_id: str,
                 package_fingerprint: str, artifact_ref: str,
                 artifact_session_id: str, fingerprint_level: str,
                 input_fingerprints: Dict[str, Any], algorithm_id: str,
                 params_fp: str, env_fp: str, source_instance_id: str = ""):
        self.owner_scope = owner_scope
        self.reuse_fingerprint = reuse_fingerprint
        self.session_scope = session_scope
        self.node_id = node_id
        self.package_fingerprint = package_fingerprint
        self.artifact_ref = artifact_ref
        self.artifact_session_id = artifact_session_id
        self.fingerprint_level = fingerprint_level
        self.input_fingerprints = input_fingerprints
        self.algorithm_id = algorithm_id
        self.params_fp = params_fp
        self.env_fp = env_fp
        self.source_instance_id = source_instance_id

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "owner_scope": self.owner_scope[:40],
            "reuse_fingerprint": self.reuse_fingerprint[:32],
            "node_id": self.node_id[:64],
            "package_fingerprint": self.package_fingerprint[:64],
            "artifact_ref": self.artifact_ref[:96],
            "fingerprint_level": self.fingerprint_level[:24],
            "algorithm_id": self.algorithm_id[:64],
            "source_instance_id": self.source_instance_id[:64],
        }


class ReuseIndex:
    """复用索引持久化 + eligibility 裁决。"""

    def __init__(self, factory: Optional[Callable[[], Any]] = None):
        self._factory = factory or session_factory

    def record(self, rec: ReuseRecord,
               max_per_owner: int = MAX_ENTRIES_PER_OWNER) -> bool:
        """记录/刷新条目（upsert + 每 owner LRU 剪枝）。fail-open。"""
        if not rec.owner_scope or not rec.reuse_fingerprint \
                or not rec.artifact_ref:
            return False
        fp = rec.reuse_fingerprint[:32]  # 与列宽同规（写入/查询一致性）
        try:
            with self._factory() as db:
                from app.core import tenancy
                from app.models.db_model import (
                    WorkflowInstanceRow,
                    WorkflowNodeReuseRow,
                )

                # ADR-0139：org 锚定 owner 域 → 既有实例行，兜底 default 桶。
                org_id = db.query(WorkflowInstanceRow.org_id).filter(
                    WorkflowInstanceRow.owner_scope == rec.owner_scope[:40],
                ).limit(1).scalar() \
                    or tenancy.get_or_create_default_org_id_sync(db)

                existing = db.query(WorkflowNodeReuseRow).filter(
                    WorkflowNodeReuseRow.owner_scope == rec.owner_scope,
                    WorkflowNodeReuseRow.reuse_fingerprint == fp,
                ).first()
                if existing is not None:
                    db.delete(existing)
                    db.flush()
                db.add(WorkflowNodeReuseRow(
                    owner_scope=rec.owner_scope[:40],
                    org_id=str(org_id)[:255],
                    reuse_fingerprint=fp,
                    session_scope=(rec.session_scope or "")[:40],
                    node_id=rec.node_id[:64],
                    package_fingerprint=rec.package_fingerprint[:64],
                    artifact_ref=rec.artifact_ref[:96],
                    artifact_session_id=(rec.artifact_session_id or "")[:255],
                    fingerprint_level=(rec.fingerprint_level or "")[:24],
                    input_fingerprints=_bounded_inputs(rec.input_fingerprints),
                    algorithm_id=(rec.algorithm_id or "")[:64],
                    params_fp=(rec.params_fp or "")[:64],
                    env_fp=(rec.env_fp or "")[:64],
                    source_instance_id=(rec.source_instance_id or "")[:64],
                    created_at=_utcnow(),
                    last_verified_at=_utcnow(),
                ))
                db.flush()
                _prune_owner(db, rec.owner_scope, max_per_owner)
                db.commit()
                return True
        except Exception:  # noqa: BLE001 — 缓存写入绝不倒灌执行路径
            logger.debug("[WorkflowRuntime] reuse record failed",
                         exc_info=True)
            return False

    def find(self, owner_scope: str, reuse_fingerprint: str,
             *, session_scope: str = "") -> Optional[ReuseRecord]:
        """寻址（不裁 eligibility —— 调用方做输入一致性/存活校验）。

        ``session_scope``：anonymous 域内的同域约束 [R1-M2]——命中必须
        session_scope 为空（真实用户域，跨会话共享）或与当前作用域相等；
        绝不命中其他匿名会话的条目。
        """
        if not owner_scope or not reuse_fingerprint:
            return None
        try:
            with self._factory() as db:
                from app.models.db_model import WorkflowNodeReuseRow

                q = db.query(WorkflowNodeReuseRow).filter(
                    WorkflowNodeReuseRow.owner_scope == owner_scope,
                    WorkflowNodeReuseRow.reuse_fingerprint
                    == reuse_fingerprint[:32],
                )
                if session_scope:
                    q = q.filter(WorkflowNodeReuseRow.session_scope.in_(
                        ("", session_scope[:40])))
                row = q.first()
                if row is None:
                    return None
                return ReuseRecord(
                    owner_scope=row.owner_scope,
                    reuse_fingerprint=row.reuse_fingerprint,
                    session_scope=row.session_scope or "",
                    node_id=row.node_id,
                    package_fingerprint=row.package_fingerprint,
                    artifact_ref=row.artifact_ref,
                    artifact_session_id=row.artifact_session_id or "",
                    fingerprint_level=row.fingerprint_level or "",
                    input_fingerprints=dict(row.input_fingerprints or {}),
                    algorithm_id=row.algorithm_id or "",
                    params_fp=row.params_fp or "",
                    env_fp=row.env_fp or "",
                    source_instance_id=row.source_instance_id or "",
                )
        except OperationalError:
            logger.debug("[WorkflowRuntime] reuse lookup busy")
            return None
        except Exception:  # noqa: BLE001 — 缓存读取 fail-open
            logger.debug("[WorkflowRuntime] reuse lookup failed",
                         exc_info=True)
            return None

    def invalidate(self, owner_scope: str, reuse_fingerprint: str) -> bool:
        """删除条目（ref 失效/内容损坏自愈）；fail-open。"""
        try:
            with self._factory() as db:
                from app.models.db_model import WorkflowNodeReuseRow

                deleted = db.query(WorkflowNodeReuseRow).filter(
                    WorkflowNodeReuseRow.owner_scope == owner_scope,
                    WorkflowNodeReuseRow.reuse_fingerprint
                    == reuse_fingerprint[:32],
                ).delete()
                db.commit()
                return bool(deleted)
        except Exception:  # noqa: BLE001
            return False


def evaluate_eligibility(
    rec: ReuseRecord,
    *,
    package_fingerprint: str,
    current_inputs: Dict[str, Dict[str, str]],
    descriptor_probe: Optional[Callable[[str, str], Optional[Dict[str, Any]]]],
) -> "tuple[bool, str]":
    """eligibility 裁决（架构 §6；返回 (可复用, 原因码)）。

    - shape 级指纹 → 永不复用（宁假 miss，杜绝同 ref 覆写假命中）；
    - 包指纹 / env_fp 不一致 → miss；
    - 逐端口输入指纹+revision 不一致 → miss；
    - artifact 存活探测失败（ref 不可解析）→ miss + 索引自愈删除由调用方执行。
    """
    if rec.fingerprint_level not in F.REUSABLE_LEVELS:
        return False, f"level_{rec.fingerprint_level or 'empty'}_not_reusable"
    if rec.package_fingerprint != package_fingerprint:
        return False, "package_changed"
    for port, cur in current_inputs.items():
        stored = rec.input_fingerprints.get(port) or {}
        if not str(cur.get("fp") or ""):
            # 身份缺席（descriptor 缺席/shape 不可判定）→ 复用必 miss
            # [R1-M4]：descriptor 缺席 = 复用必 miss 的红线守卫。
            return False, f"input_identity_absent:{port}"
        if str(stored.get("fp") or "") != str(cur.get("fp") or ""):
            return False, f"input_changed:{port}"
        stored_rev = str(stored.get("rev")
                         or stored.get("content_revision") or "")
        if stored_rev != str(cur.get("content_revision") or ""):
            return False, f"input_revision_changed:{port}"
    if descriptor_probe is not None:
        probe = descriptor_probe(rec.artifact_session_id, rec.artifact_ref)
        if not isinstance(probe, dict):
            return False, "artifact_unresolvable"
    return True, "ok"


def anonymous_session_scope(owner_scope: str, session_id: str) -> str:
    """anonymous 域内的复用作用域 [R1-MINOR-5]：真实用户域 = ""（跨会话
    共享），anonymous 域 = session 哈希（同会话内才可复用）。"""
    if owner_scope == "anonymous" and session_id:
        return F.canonical_fingerprint(session_id)[:32]
    return ""


def _bounded_inputs(inputs: Any) -> Dict[str, Any]:
    if not isinstance(inputs, dict):
        return {}
    out: Dict[str, Any] = {}
    for port, ident in list(inputs.items())[:16]:
        if isinstance(ident, dict):
            out[str(port)[:48]] = {
                "level": str(ident.get("level") or "")[:24],
                "fp": str(ident.get("fp") or "")[:64],
                "rev": str(ident.get("content_revision")
                           or ident.get("rev") or "")[:32],
            }
    return out


def _prune_owner(db: Any, owner_scope: str, max_per_owner: int) -> None:
    from sqlalchemy import select

    from app.models.db_model import WorkflowNodeReuseRow

    stale_ids = list(
        db.execute(
            select(WorkflowNodeReuseRow.id)
            .where(WorkflowNodeReuseRow.owner_scope == owner_scope)
            .order_by(WorkflowNodeReuseRow.created_at.desc(),
                      WorkflowNodeReuseRow.id.desc())
            .offset(max(0, int(max_per_owner)))
        ).scalars()
    )
    if stale_ids:
        db.query(WorkflowNodeReuseRow).filter(
            WorkflowNodeReuseRow.id.in_(stale_ids)
        ).delete(synchronize_session=False)
