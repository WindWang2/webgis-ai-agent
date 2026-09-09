"""Workflow Runtime V5 —— durable 实例存储（实例行 + 每节点行，两级 CAS）。

并发模型（架构 §3 [R1-C3]）：

- **节点级 CAS**：``UPDATE ... WHERE instance_id=? AND node_id=? AND
  state_revision=?``；0 行更新 = 乐观锁冲突 → 重读重评。SQLite
  ``database is locked``（OperationalError）与 CAS 冲突分道：前者退避
  重试（安全），后者重读后按当前状态重新裁决；
- **完成类转移永不放弃** [R1-C3]：claim token 持有者对 RUNNING→终态的
  转移在 CAS 冲突后重读——若目标状态已达成（幂等重复完成）按成功返回；
  若被他人认领才拒绝；
- **租约**：driver 持 run lease（每波次续期）；孤儿清扫把 RUNNING 且
  租约过期的节点复位 READY（attempts 保留）；
- owner 隔离：全部读路径按 owner_scope 过滤（他人 404 语义 → None）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.fingerprints import canonical_fingerprint

logger = logging.getLogger(__name__)

#: CAS/锁冲突退避（秒）；SQLite busy 与乐观锁冲突共用该退避序列。
_BACKOFF_S = (0.01, 0.02, 0.05, 0.1)
#: 默认 run 租约 TTL（秒）；driver 每波次续期。
DEFAULT_LEASE_TTL_S = 120.0


def _default_session_factory():
    from app.core.database import SessionLocal

    return SessionLocal()


#: 可注入会话工厂（测试替换临时 SQLite 工厂）；用法 ``with f() as db:``。
session_factory: Callable[[], Any] = _default_session_factory


def new_instance_id() -> str:
    return f"wi-{uuid.uuid4().hex[:20]}"


def new_run_token() -> str:
    return f"rt-{uuid.uuid4().hex[:16]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TransitionResult:
    """节点转移结果（typed；绝不静默）。"""

    __slots__ = ("ok", "code", "state", "state_revision", "detail")

    def __init__(self, ok: bool, code: str, state: str = "",
                 state_revision: int = 0, detail: str = ""):
        self.ok = ok
        self.code = code
        self.state = state
        self.state_revision = state_revision
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - 调试面
        return f"TransitionResult(ok={self.ok}, code={self.code!r})"


def _row_to_node(row: Any) -> Dict[str, Any]:
    return {
        "instance_id": row.instance_id,
        "node_id": row.node_id,
        "state": row.state,
        "state_revision": row.state_revision,
        "claimed_by": row.claimed_by or "",
        "attempts": row.attempts,
        "error_code": row.error_code or "",
        "bound_ref": row.bound_ref or "",
        "output_ref": row.output_ref or "",
        "output_fingerprint": row.output_fingerprint or "",
        "binding": dict(row.binding or {}),
        "reuse": dict(row.reuse or {}),
        "attempts_log": list(row.attempts_log or [])[:C.MAX_NODE_ATTEMPTS],
        "transitions": list(row.transitions or [])[:C.MAX_NODE_TRANSITIONS],
    }


def _row_to_instance(row: Any) -> Dict[str, Any]:
    return {
        "instance_id": row.instance_id,
        "package_id": row.package_id,
        "package_version": row.package_version,
        "package_fingerprint": row.package_fingerprint,
        "status": row.status,
        "revision": row.revision,
        "owner_scope": row.owner_scope,
        "session_id": row.session_id or "",
        "project_id": row.project_id or "",
        "parent_instance_id": row.parent_instance_id or "",
        "parent_node_id": row.parent_node_id or "",
        "run_lease_owner": row.run_lease_owner or "",
        "run_lease_expires_at": row.run_lease_expires_at.isoformat()
        if row.run_lease_expires_at else "",
        "cancel_requested": bool(row.cancel_requested),
        "pending_changes": list(row.pending_changes or [])[:C.MAX_PENDING_CHANGES],
        "decisions": list(row.decisions or [])[:C.MAX_INSTANCE_DECISIONS],
        "visited_packages": list(row.visited_packages or [])[:8],
        "error_code": row.error_code or "",
        "error_detail": row.error_detail or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "terminal_at": row.terminal_at.isoformat() if row.terminal_at else "",
    }


class InstanceStore:
    """实例 + 节点持久化（同步 SQLAlchemy；异步调用方经 to_thread 卸载）。"""

    def __init__(self, factory: Optional[Callable[[], Any]] = None):
        self._factory = factory or session_factory

    # ── 创建 / 读取 ───────────────────────────────────────────────────

    def create_instance(
        self,
        *,
        package_id: str,
        package_version: str,
        package_fingerprint: str,
        owner_scope: str,
        session_id: str = "",
        project_id: str = "",
        parent_instance_id: str = "",
        parent_node_id: str = "",
        node_specs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """创建实例 + 全节点 PENDING 行（原子单事务）。

        ``node_specs``: [{node_id, optional}]，≤ MAX_INSTANCE_NODES。
        """
        if not node_specs:
            raise ValueError("instance requires at least one node")
        if len(node_specs) > C.MAX_INSTANCE_NODES:
            raise ValueError(
                f"instance node count {len(node_specs)} exceeds cap "
                f"{C.MAX_INSTANCE_NODES}")
        instance_id = new_instance_id()
        now = _utcnow()
        with self._factory() as db:
            db.add(WorkflowInstanceRow(
                instance_id=instance_id,
                package_id=package_id[:64],
                package_version=package_version[:16],
                package_fingerprint=package_fingerprint[:64],
                status=C.InstanceStatus.RUNNING,
                revision=1,
                owner_scope=owner_scope[:40],
                session_id=(session_id or "")[:255],
                project_id=(project_id or "")[:255] or None,
                parent_instance_id=(parent_instance_id or "")[:64] or None,
                parent_node_id=(parent_node_id or "")[:64] or None,
                pending_changes=[],
                decisions=[],
                visited_packages=[],
                created_at=now,
                updated_at=now,
            ))
            for spec in node_specs[:C.MAX_INSTANCE_NODES]:
                db.add(WorkflowInstanceNodeRow(
                    instance_id=instance_id,
                    node_id=str(spec["node_id"])[:64],
                    state=C.NodeState.PENDING,
                    state_revision=1,
                    binding={},
                    reuse={},
                    attempts_log=[],
                    transitions=[],
                    updated_at=now,
                ))
            db.commit()
        return self.get_instance(instance_id, owner_scope) or {}

    def get_instance(
        self, instance_id: str, owner_scope: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """读实例（owner 过滤：他人实例 → None，防存在性预言机）。"""
        try:
            with self._factory() as db:
                q = db.query(WorkflowInstanceRow).filter(
                    WorkflowInstanceRow.instance_id == instance_id)
                if owner_scope is not None:
                    q = q.filter(WorkflowInstanceRow.owner_scope == owner_scope)
                row = q.first()
                return _row_to_instance(row) if row is not None else None
        except OperationalError:
            logger.warning("[WorkflowRuntime] get_instance db busy",
                           exc_info=True)
            return None

    def get_node_states(self, instance_id: str) -> Dict[str, str]:
        """{node_id: state} 轻投影（调度热路径；单查询）。"""
        with self._factory() as db:
            rows = db.query(
                WorkflowInstanceNodeRow.node_id,
                WorkflowInstanceNodeRow.state,
            ).filter(WorkflowInstanceNodeRow.instance_id == instance_id).all()
            return {r.node_id: r.state for r in rows}

    def get_nodes(self, instance_id: str) -> List[Dict[str, Any]]:
        with self._factory() as db:
            rows = db.query(WorkflowInstanceNodeRow).filter(
                WorkflowInstanceNodeRow.instance_id == instance_id).all()
            return [_row_to_node(r) for r in rows]

    def get_node(self, instance_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        with self._factory() as db:
            row = db.query(WorkflowInstanceNodeRow).filter(
                WorkflowInstanceNodeRow.instance_id == instance_id,
                WorkflowInstanceNodeRow.node_id == node_id).first()
            return _row_to_node(row) if row is not None else None

    # ── 节点级 CAS 转移 ───────────────────────────────────────────────

    def transition_node(
        self,
        instance_id: str,
        node_id: str,
        to_state: str,
        *,
        expected_from: Optional[str] = None,
        reason: str = "",
        event: str = "",
        claim: bool = False,
        claimed_by: str = "",
        require_claim: bool = False,
        patch: Optional[Dict[str, Any]] = None,
        complete: bool = False,
    ) -> TransitionResult:
        """节点状态 CAS 转移（乐观锁 + 冲突重读重评 + busy 退避）。

        - ``expected_from``：要求当前状态（调度安全门）；
        - ``claim``：READY→RUNNING 时写 claimed_by；
        - ``require_claim``：完成类转移校验认领者；
        - ``complete``：完成类语义 —— CAS 冲突后重读，目标状态已达成按
          幂等成功返回（**完成永不放弃** [R1-C3]）；
        - ``patch``：随转移写入的列（bound_ref/output_ref/binding/reuse/
          error_code/output_fingerprint/attempts+1 等，白名单键）。
        """
        patch = dict(patch or {})
        # 幂等完成入口：目标状态已达成 → OK_IDEMPOTENT（重复完成事件
        # 绝不二次副作用；[R1-C3] 完成类转移永不放弃）。
        if complete:
            fresh0 = self.get_node(instance_id, node_id)
            if fresh0 is not None and fresh0["state"] == to_state:
                if require_claim and (fresh0.get("claimed_by") or "") \
                        != (claimed_by or ""):
                    return TransitionResult(
                        False, "CLAIM_MISMATCH", state=to_state,
                        state_revision=fresh0["state_revision"])
                return TransitionResult(
                    True, "OK_IDEMPOTENT", state=to_state,
                    state_revision=fresh0["state_revision"])
        for attempt_idx, backoff in enumerate((0.0,) + _BACKOFF_S):
            if backoff:
                import time

                time.sleep(backoff)
            try:
                result = self._try_transition(
                    instance_id, node_id, to_state,
                    expected_from=expected_from, reason=reason, event=event,
                    claim=claim, claimed_by=claimed_by,
                    require_claim=require_claim, patch=patch,
                )
            except OperationalError:
                # SQLite busy：安全重试（事务未提交）
                if attempt_idx == len(_BACKOFF_S):
                    logger.warning(
                        "[WorkflowRuntime] transition db busy %s/%s",
                        instance_id, node_id)
                    return TransitionResult(
                        False, "DB_BUSY", detail="database locked")
                continue
            if result.code != "CAS_CONFLICT":
                return result
            # CAS 冲突 → 重读重评（下一轮 _try_transition 读新状态）
            if complete:
                fresh = self.get_node(instance_id, node_id)
                if fresh is not None and fresh["state"] == to_state:
                    return TransitionResult(
                        True, "OK_IDEMPOTENT", state=to_state,
                        state_revision=fresh["state_revision"])
        fresh = self.get_node(instance_id, node_id)
        cur = fresh["state"] if fresh else "?"
        return TransitionResult(
            False, "CAS_CONFLICT",
            state=cur, detail=f"node now {cur}")

    def _try_transition(
        self, instance_id: str, node_id: str, to_state: str, *,
        expected_from: Optional[str], reason: str, event: str,
        claim: bool, claimed_by: str, require_claim: bool,
        patch: Dict[str, Any],
    ) -> TransitionResult:
        with self._factory() as db:
            row = db.query(WorkflowInstanceNodeRow).filter(
                WorkflowInstanceNodeRow.instance_id == instance_id,
                WorkflowInstanceNodeRow.node_id == node_id,
            ).first()
            if row is None:
                return TransitionResult(False, "NO_SUCH_NODE")
            if require_claim and (row.claimed_by or "") != (claimed_by or ""):
                return TransitionResult(
                    False, "CLAIM_MISMATCH", state=row.state,
                    detail=f"claimed_by={row.claimed_by!r}")
            illegal = C.validate_transition(row.state, to_state)
            if illegal is not None:
                return TransitionResult(
                    False, illegal.split(":")[0], state=row.state,
                    detail=illegal)
            if expected_from is not None and row.state != expected_from:
                return TransitionResult(
                    False, "CAS_CONFLICT", state=row.state,
                    detail=f"expected {expected_from} got {row.state}")
            new_rev = row.state_revision + 1
            values: Dict[str, Any] = {
                "state": to_state,
                "state_revision": new_rev,
                "updated_at": _utcnow(),
            }
            if claim:
                values["claimed_by"] = (claimed_by or "")[:64] or None
            if to_state in (C.NodeState.READY, C.NodeState.CANCELLED,
                            C.NodeState.SKIPPED):
                values["claimed_by"] = None
            col_map = {
                "bound_ref": "bound_ref", "output_ref": "output_ref",
                "output_fingerprint": "output_fingerprint",
                "error_code": "error_code", "binding": "binding",
                "reuse": "reuse",
            }
            for key, col in col_map.items():
                if key in patch:
                    values[col] = patch[key]
            if patch.get("attempts_increment"):
                values["attempts"] = row.attempts + 1
            new_log = list(row.attempts_log or [])
            if patch.get("attempt_log"):
                new_log.append(patch["attempt_log"])
                values["attempts_log"] = new_log[-C.MAX_NODE_ATTEMPTS:]
            new_trans = list(row.transitions or [])
            new_trans.append(C.StateTransitionRecord(
                revision=new_rev, from_state=row.state, to_state=to_state,
                reason_code=str(reason)[:96], event=str(event)[:32],
            ).to_bounded_dict())
            values["transitions"] = new_trans[-C.MAX_NODE_TRANSITIONS:]

            updated = db.execute(
                sa.update(WorkflowInstanceNodeRow)
                .where(
                    WorkflowInstanceNodeRow.instance_id == instance_id,
                    WorkflowInstanceNodeRow.node_id == node_id,
                    WorkflowInstanceNodeRow.state_revision == row.state_revision,
                )
                .values(**values)
            )
            if updated.rowcount == 0:
                return TransitionResult(False, "CAS_CONFLICT", state=row.state)
            db.commit()
            return TransitionResult(True, "OK", state=to_state,
                                    state_revision=new_rev)

    # ── 实例级操作 ────────────────────────────────────────────────────

    def update_instance(
        self, instance_id: str, *,
        owner_scope: Optional[str] = None,
        expected_revision: Optional[int] = None,
        fields: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """实例级 CAS 更新（revision +1；0 行 = 冲突返回 None）。"""
        allowed = {
            "status", "cancel_requested", "pending_changes", "decisions",
            "visited_packages", "error_code", "error_detail", "started_at",
            "terminal_at", "run_lease_owner", "run_lease_expires_at",
        }
        values = {k: v for k, v in fields.items() if k in allowed}
        if not values:
            return self.get_instance(instance_id, owner_scope)
        try:
            with self._factory() as db:
                q = db.query(WorkflowInstanceRow).filter(
                    WorkflowInstanceRow.instance_id == instance_id)
                if owner_scope is not None:
                    q = q.filter(WorkflowInstanceRow.owner_scope == owner_scope)
                row = q.first()
                if row is None:
                    return None
                if expected_revision is not None \
                        and row.revision != expected_revision:
                    return None
                values["revision"] = row.revision + 1
                values["updated_at"] = _utcnow()
                db.execute(
                    sa.update(WorkflowInstanceRow)
                    .where(WorkflowInstanceRow.instance_id == instance_id,
                           WorkflowInstanceRow.revision == row.revision)
                    .values(**values)
                )
                db.commit()
            return self.get_instance(instance_id, owner_scope)
        except OperationalError:
            logger.warning("[WorkflowRuntime] update_instance db busy",
                           exc_info=True)
            return None

    def acquire_run_lease(
        self, instance_id: str, *, owner_scope: str, token: str,
        ttl_s: float = DEFAULT_LEASE_TTL_S,
    ) -> bool:
        """获取/续期 run 租约（过期或本人持有才授予；调度互斥）。"""
        now = _utcnow()
        expires = now + timedelta(seconds=max(1.0, float(ttl_s)))
        try:
            with self._factory() as db:
                row = db.query(WorkflowInstanceRow).filter(
                    WorkflowInstanceRow.instance_id == instance_id,
                    WorkflowInstanceRow.owner_scope == owner_scope,
                ).first()
                # 终态 cancelled/superseded 不可再驱动；
                # succeeded/failed 允许再入（增量重算驱动的合法路径）。
                if row is None or row.status in (
                        C.InstanceStatus.CANCELLED,
                        C.InstanceStatus.SUPERSEDED):
                    return False
                held = row.run_lease_expires_at or datetime.min
                same_owner = (row.run_lease_owner or "") == token
                if held > now and not same_owner:
                    return False
                db.execute(
                    sa.update(WorkflowInstanceRow)
                    .where(WorkflowInstanceRow.instance_id == instance_id,
                           WorkflowInstanceRow.revision == row.revision)
                    .values(run_lease_owner=token[:64],
                            run_lease_expires_at=expires,
                            revision=row.revision + 1,
                            updated_at=now)
                )
                db.commit()
                return True
        except OperationalError:
            return False

    def release_run_lease(
        self, instance_id: str, *, owner_scope: str, token: str,
    ) -> bool:
        """释放租约（仅持有人可释；run 正常收尾调用）。"""
        try:
            with self._factory() as db:
                updated = db.execute(
                    sa.update(WorkflowInstanceRow)
                    .where(
                        WorkflowInstanceRow.instance_id == instance_id,
                        WorkflowInstanceRow.owner_scope == owner_scope,
                        WorkflowInstanceRow.run_lease_owner == token[:64],
                    )
                    .values(run_lease_owner=None, run_lease_expires_at=None,
                            updated_at=_utcnow())
                )
                db.commit()
                return bool(updated.rowcount)
        except OperationalError:
            return False

    def find_orphan_running_nodes(
        self, instance_id: str, *, current_token: str = "",
    ) -> List[str]:
        """孤儿 RUNNING 节点清单（恢复复位）。

        liveness 以实例租约为准：租约被**其他** token 活持 → 有主不扫；
        租约过期/无主/由当前 token 持有（本 driver 刚接管）→ RUNNING 且
        claimed_by ≠ 当前 token 的节点即孤儿（claimed_by == 当前 token 的
        在飞节点由本 driver 自己的 cancel/deadline 管辖）。
        """
        inst = self.get_instance(instance_id)
        if inst is None or inst["status"] != C.InstanceStatus.RUNNING:
            return []
        try:
            expires = datetime.fromisoformat(inst["run_lease_expires_at"]) \
                if inst["run_lease_expires_at"] else None
        except (TypeError, ValueError):
            expires = None
        lease_held_elsewhere = (
            expires is not None and expires > _utcnow()
            and (inst["run_lease_owner"] or "")
            and inst["run_lease_owner"] != current_token)
        if lease_held_elsewhere:
            return []
        with self._factory() as db:
            rows = db.query(WorkflowInstanceNodeRow).filter(
                WorkflowInstanceNodeRow.instance_id == instance_id,
                WorkflowInstanceNodeRow.state == C.NodeState.RUNNING,
            ).all()
            return [r.node_id for r in rows
                    if (r.claimed_by or "") != current_token]

    def list_session_instances(
        self, session_id: str, *, owner_scope: Optional[str] = None,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """会话实例清单（supersede/attach 判定用；有界 ≤16）。"""
        with self._factory() as db:
            q = db.query(WorkflowInstanceRow).filter(
                WorkflowInstanceRow.session_id == session_id)
            if owner_scope is not None:
                q = q.filter(WorkflowInstanceRow.owner_scope == owner_scope)
            if active_only:
                q = q.filter(WorkflowInstanceRow.status
                             == C.InstanceStatus.RUNNING)
            rows = q.order_by(WorkflowInstanceRow.created_at.desc()) \
                .limit(16).all()
            return [_row_to_instance(r) for r in rows]

    def list_owner_instances(
        self, owner_scope: str, *, limit: int = 32,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        with self._factory() as db:
            q = db.query(WorkflowInstanceRow).filter(
                WorkflowInstanceRow.owner_scope == owner_scope)
            if statuses:
                q = q.filter(WorkflowInstanceRow.status.in_(statuses))
            rows = q.order_by(WorkflowInstanceRow.created_at.desc()) \
                .limit(max(1, min(int(limit), 128))).all()
            return [_row_to_instance(r) for r in rows]

    def count_active_subworkflows(
        self, owner_scope: str,
    ) -> int:
        """每 owner 活跃子实例计数（≤32 上界的判定输入 [R1-M7]）。"""
        with self._factory() as db:
            return db.query(WorkflowInstanceRow).filter(
                WorkflowInstanceRow.owner_scope == owner_scope,
                WorkflowInstanceRow.status == C.InstanceStatus.RUNNING,
                WorkflowInstanceRow.parent_instance_id.isnot(None),
                WorkflowInstanceRow.parent_instance_id != "",
            ).count()

    def instance_content_fingerprint(self, instance_id: str) -> str:
        """实例证据内容指纹（确定性测试 oracle；不含时间戳/revision）。"""
        inst = self.get_instance(instance_id)
        if inst is None:
            return ""
        nodes = self.get_nodes(instance_id)
        return canonical_fingerprint({
            "pkg": inst["package_fingerprint"],
            "nodes": [
                {k: n[k] for k in ("node_id", "state", "attempts",
                                   "error_code", "bound_ref", "output_ref",
                                   "output_fingerprint")}
                for n in sorted(nodes, key=lambda x: x["node_id"])
            ],
            "decisions": inst["decisions"],
        })


#: 模型延迟绑定（与 db_model 保持单一定义）。
from app.models.db_model import (  # noqa: E402
    WorkflowInstanceNodeRow,
    WorkflowInstanceRow,
)
