"""Workflow Runtime V5/V6 —— durable 实例存储（实例行 + 每节点行，两级 CAS）。

并发模型（架构 §3 [R1-C3]）：

- **节点级 CAS**：``UPDATE ... WHERE instance_id=? AND node_id=? AND
  state_revision=?``；0 行更新 = 乐观锁冲突 → 重读重评。SQLite
  ``database is locked``（OperationalError）与 CAS 冲突分道：前者退避
  重试（安全），后者重读后按当前状态重新裁决；
- **完成类转移永不放弃** [R1-C3]：claim token 持有者对 RUNNING→终态的
  转移在 CAS 冲突后重读——若目标状态已达成（幂等重复完成）按成功返回；
  若被他人认领才拒绝；
- **租约（两级）**：run 租约（driver 持有，波次续期）管「谁在驱动实例」；
  节点租约（V6，执行者 claim 时写入 ``lease_expires_at``）管「谁在执行
  该节点」—— coordinator 存活不等于 worker 存活，孤儿判定以节点租约
  过期为准（NULL 租约的旧式同步认领回退 run 租约语义）；
- **事件日志（V6）**：``workflow_events`` append-only journal，与状态转移
  **同事务**写入（atomic truth）；取消/恢复/重试/补偿经 ``append_event``；
- owner 隔离：全部读路径按 owner_scope 过滤（他人 404 语义 → None）。
"""
from __future__ import annotations

import logging
import time
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
#: 默认节点租约 TTL（秒）；claim 时写入，heartbeat 续期。
DEFAULT_NODE_LEASE_TTL_S = 120.0
#: journal 单次查询上界（inspect API 分页；防全量拖库）。
MAX_JOURNAL_QUERY = 200


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


class StoreUnavailable(RuntimeError):
    """存储暂不可用（SQLite busy / 连接池耗尽）—— 与 not_found 分道。"""


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
        "lease_expires_at": row.lease_expires_at.isoformat()
        if row.lease_expires_at else "",
        "heartbeat_at": row.heartbeat_at.isoformat()
        if row.heartbeat_at else "",
        "cancel_requested": bool(row.cancel_requested),
        "next_ready_at": row.next_ready_at.isoformat()
        if row.next_ready_at else "",
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
        """读实例（owner 过滤：他人实例 → None，防存在性预言机）。

        DB busy（OperationalError）上抛 StoreUnavailable —— 与「不存在」
        是两种语义，混同会让 API 以成功形态返回 not_found（R2-m-3）。
        """
        try:
            with self._factory() as db:
                q = db.query(WorkflowInstanceRow).filter(
                    WorkflowInstanceRow.instance_id == instance_id)
                if owner_scope is not None:
                    q = q.filter(WorkflowInstanceRow.owner_scope == owner_scope)
                row = q.first()
                return _row_to_instance(row) if row is not None else None
        except OperationalError as exc:
            raise StoreUnavailable(str(exc)[:120]) from exc

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
        lease_ttl_s: Optional[float] = None,
    ) -> TransitionResult:
        """节点状态 CAS 转移（乐观锁 + 冲突重读重评 + busy 退避）。

        - ``expected_from``：要求当前状态（调度安全门）；
        - ``claim``：READY→RUNNING 时写 claimed_by **并写节点租约**
          （V6：``lease_ttl_s`` 缺省用 ``DEFAULT_NODE_LEASE_TTL_S``）；
        - ``require_claim``：完成类转移校验认领者；
        - ``complete``：完成类语义 —— CAS 冲突后重读，目标状态已达成按
          幂等成功返回（**完成永不放弃** [R1-C3]）；
        - ``patch``：随转移写入的列（bound_ref/output_ref/binding/reuse/
          error_code/output_fingerprint/attempts+1/next_ready_at 等，
          白名单键）；
        - 每次成功转移**同事务**写一行 ``workflow_events`` journal。
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
                time.sleep(backoff)
            try:
                result = self._try_transition(
                    instance_id, node_id, to_state,
                    expected_from=expected_from, reason=reason, event=event,
                    claim=claim, claimed_by=claimed_by,
                    require_claim=require_claim, patch=patch,
                    lease_ttl_s=lease_ttl_s,
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
                    if require_claim and (fresh.get("claimed_by") or "")                             != (claimed_by or ""):
                        # 被接管后迟到完成：目标已达成但认领者非本 token
                        # —— 拒绝（fencing 与入口预检同一纪律）
                        return TransitionResult(
                            False, "CLAIM_MISMATCH", state=to_state,
                            state_revision=fresh["state_revision"])
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
        patch: Dict[str, Any], lease_ttl_s: Optional[float] = None,
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
            now = _utcnow()
            # SQLA 2.0 session.execute(update()) 默认 synchronize_session
            # 会就地刷新会话内 ORM 属性 —— journal 的 from_state 必须**先**
            # 捕获，否则写进的是 to_state（事件序污化）。
            from_state = str(row.state)
            values: Dict[str, Any] = {
                "state": to_state,
                "state_revision": new_rev,
                "updated_at": now,
            }
            if claim:
                values["claimed_by"] = (claimed_by or "")[:64] or None
                # V6 节点租约：认领即持约（worker 死亡 → 租约过期 → 孤儿）
                ttl = float(lease_ttl_s) if lease_ttl_s is not None \
                    else DEFAULT_NODE_LEASE_TTL_S
                values["lease_expires_at"] = now + timedelta(
                    seconds=max(1.0, ttl))
                values["heartbeat_at"] = now
            if to_state in (C.NodeState.READY, C.NodeState.CANCELLED,
                            C.NodeState.SKIPPED):
                values["claimed_by"] = None
            if to_state in (C.NodeState.SUCCEEDED, C.NodeState.FAILED,
                            C.NodeState.CANCELLED):
                # 节点执行生命周期结束：清租约（孤儿判定不再看它）
                values["lease_expires_at"] = None
                values["heartbeat_at"] = None
            col_map = {
                "bound_ref": "bound_ref", "output_ref": "output_ref",
                "output_fingerprint": "output_fingerprint",
                "error_code": "error_code", "binding": "binding",
                "reuse": "reuse", "next_ready_at": "next_ready_at",
            }
            for key, col in col_map.items():
                if key in patch:
                    values[col] = patch[key]
            if patch.get("attempts_increment"):
                values["attempts"] = row.attempts + 1
            new_attempt = int(values.get("attempts", row.attempts) or 0)
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
            # V6 journal：与转移同事务 append（atomic truth；绝不丢事件）
            db.add(WorkflowEventRow(
                instance_id=instance_id,
                node_id=node_id[:64],
                kind=C.EventKind.STATE_TRANSITION,
                from_state=from_state[:16],
                to_state=to_state[:16],
                reason=str(reason)[:96],
                actor=str(event)[:64],
                attempt=new_attempt,
                payload=_bounded_event_payload(patch),
            ))
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

        V6 两级租约语义：

        - 节点租约**已写**（V6 claim 路径）：``lease_expires_at`` 过期即孤儿
          —— 执行者死亡与 coordinator 死亡解耦（run 租约活着但 worker 死了
          也能被本 driver/清扫接管）；
        - 节点租约 NULL（旧式同步认领，如 chat 通道）：回退 run 租约语义 —
          租约被**其他** token 活持 → 有主不扫；租约过期/无主/由当前 token
          持有（本 driver 刚接管）→ RUNNING 且 claimed_by ≠ 当前 token 的
          节点即孤儿（claimed_by == 当前 token 的在飞节点由本 driver 自己
          的 cancel/deadline 管辖）。
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
        now = _utcnow()
        with self._factory() as db:
            rows = db.query(WorkflowInstanceNodeRow).filter(
                WorkflowInstanceNodeRow.instance_id == instance_id,
                WorkflowInstanceNodeRow.state == C.NodeState.RUNNING,
            ).all()
            out: List[str] = []
            for r in rows:
                if (r.claimed_by or "") == current_token:
                    continue
                if r.lease_expires_at is not None:
                    if r.lease_expires_at <= now:
                        out.append(r.node_id)
                    # 未过期的节点租约 = 活 worker 持有，绝不接管
                    continue
                # NULL 节点租约：旧式认领，run 租约已判过期/无主 → 孤儿
                out.append(r.node_id)
            return out

    # ── 节点租约 / 心跳 / 节点级取消（V6）────────────────────────────

    def heartbeat_node(
        self, instance_id: str, node_id: str, *, token: str,
        ttl_s: float = DEFAULT_NODE_LEASE_TTL_S,
    ) -> bool:
        """续期节点租约（仅 RUNNING 且持有人匹配；worker 活性证据）。

        rowcount=0 = 节点已不再由该 token 执行（被接管/完成）—— 调用方应
        停止执行并重读状态（fencing：迟到的 worker 不能覆盖新 attempt）。
        """
        now = _utcnow()
        try:
            with self._factory() as db:
                updated = db.execute(
                    sa.update(WorkflowInstanceNodeRow)
                    .where(
                        WorkflowInstanceNodeRow.instance_id == instance_id,
                        WorkflowInstanceNodeRow.node_id == node_id,
                        WorkflowInstanceNodeRow.state == C.NodeState.RUNNING,
                        WorkflowInstanceNodeRow.claimed_by == (token or "")[:64],
                    )
                    .values(
                        lease_expires_at=now + timedelta(
                            seconds=max(1.0, float(ttl_s))),
                        heartbeat_at=now,
                        updated_at=now,
                    )
                )
                db.commit()
                return bool(updated.rowcount)
        except OperationalError:
            return False

    def request_node_cancel(
        self, instance_id: str, node_ids: List[str], *,
        actor: str = "api",
    ) -> List[str]:
        """节点级取消请求（持久旗标；执行者经心跳探针/波界观察）。

        只对**非终态**节点置位；终态节点是既成事实，不追改。
        返回实际置位成功的 node_id（条件更新，多写手安全）。
        """
        wanted = [str(n)[:64] for n in node_ids if n]
        if not wanted:
            return []
        now = _utcnow()
        out: List[str] = []
        with self._factory() as db:
            for nid in wanted:
                updated = db.execute(
                    sa.update(WorkflowInstanceNodeRow)
                    .where(
                        WorkflowInstanceNodeRow.instance_id == instance_id,
                        WorkflowInstanceNodeRow.node_id == nid,
                        WorkflowInstanceNodeRow.state.in_([
                            C.NodeState.PENDING, C.NodeState.READY,
                            C.NodeState.RUNNING, C.NodeState.BLOCKED,
                            C.NodeState.STALE,
                        ]),
                        WorkflowInstanceNodeRow.cancel_requested.is_(False),
                    )
                    .values(cancel_requested=True, updated_at=now)
                )
                if updated.rowcount:
                    out.append(nid)
            db.add_all(WorkflowEventRow(
                instance_id=instance_id,
                node_id=nid[:64],
                kind=C.EventKind.NODE_CANCEL_REQUESTED,
                reason="NODE_CANCEL_REQUESTED",
                actor=str(actor)[:64],
                payload={},
            ) for nid in out)
            db.commit()
        return out

    def get_node_cancel_flags(self, instance_id: str) -> Dict[str, bool]:
        """{node_id: cancel_requested}（driver 波界在飞取消观察；单查询）。"""
        with self._factory() as db:
            rows = db.query(
                WorkflowInstanceNodeRow.node_id,
                WorkflowInstanceNodeRow.cancel_requested,
            ).filter(WorkflowInstanceNodeRow.instance_id == instance_id,
                     WorkflowInstanceNodeRow.cancel_requested.is_(True)).all()
            return {r.node_id: True for r in rows}

    def clear_node_cancel(
        self, instance_id: str, node_id: str,
    ) -> bool:
        """清除节点取消旗标（显式 retry/resume 前置；与置位同条件更新风格）。"""
        with self._factory() as db:
            updated = db.execute(
                sa.update(WorkflowInstanceNodeRow)
                .where(
                    WorkflowInstanceNodeRow.instance_id == instance_id,
                    WorkflowInstanceNodeRow.node_id == node_id,
                )
                .values(cancel_requested=False, updated_at=_utcnow())
            )
            db.commit()
            return bool(updated.rowcount)

    # ── 事件日志（V6 journal）────────────────────────────────────────

    def append_event(
        self, instance_id: str, *, kind: str, node_id: str = "",
        reason: str = "", actor: str = "", attempt: int = 0,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """非转移类事实入 journal（取消请求/租约/恢复/重试/补偿）。"""
        with self._factory() as db:
            db.add(WorkflowEventRow(
                instance_id=instance_id,
                node_id=(node_id or "")[:64],
                kind=str(kind)[:40],
                reason=str(reason)[:96],
                actor=str(actor)[:64],
                attempt=int(attempt or 0),
                payload=dict(payload or {}),
            ))
            db.commit()

    def get_events(
        self, instance_id: str, *, limit: int = MAX_JOURNAL_QUERY,
        after_id: int = 0, kind: str = "",
    ) -> List[Dict[str, Any]]:
        """journal 顺序读（id 升序 = 因果序；分页有界）。"""
        limit = max(1, min(int(limit), MAX_JOURNAL_QUERY))
        with self._factory() as db:
            q = db.query(WorkflowEventRow).filter(
                WorkflowEventRow.instance_id == instance_id,
                WorkflowEventRow.id > int(after_id))
            if kind:
                q = q.filter(WorkflowEventRow.kind == str(kind)[:40])
            rows = q.order_by(WorkflowEventRow.id.asc()).limit(limit).all()
            return [
                {
                    "id": r.id,
                    "instance_id": r.instance_id,
                    "node_id": r.node_id or "",
                    "kind": r.kind,
                    "from_state": r.from_state or "",
                    "to_state": r.to_state or "",
                    "reason": r.reason or "",
                    "actor": r.actor or "",
                    "attempt": r.attempt or 0,
                    "payload": dict(r.payload or {}),
                    "created_at": r.created_at.isoformat()
                    if r.created_at else "",
                }
                for r in rows
            ]

    # ── 恢复扫描（V6 startup/periodic sweep）────────────────────────

    def list_recoverable_instances(
        self, *, now: Optional[datetime] = None,
        ttl_s: float = DEFAULT_LEASE_TTL_S, limit: int = 16,
    ) -> List[Dict[str, Any]]:
        """RUNNING 且需要恢复的实例（租约过期 / 无主超时 / 挂了取消旗标）。

        - run 租约过期 → driver 死亡，节点按孤儿处理；
        - 无租约且 created_at 早于 ttl → 创建后从未被驱动（提交即崩）；
        - cancel_requested → 旗标置位后 driver 死亡，取消语义未消费完。
        刚创建（< ttl）的 NULL 租约实例**不在列**——正常驱动可能尚未起步。
        """
        now = now or _utcnow()
        cutoff = now - timedelta(seconds=max(1.0, float(ttl_s)))
        with self._factory() as db:
            rows = db.query(WorkflowInstanceRow).filter(
                WorkflowInstanceRow.status == C.InstanceStatus.RUNNING,
                sa.or_(
                    sa.and_(
                        WorkflowInstanceRow.run_lease_expires_at.isnot(None),
                        WorkflowInstanceRow.run_lease_expires_at < now,
                    ),
                    sa.and_(
                        WorkflowInstanceRow.run_lease_expires_at.is_(None),
                        WorkflowInstanceRow.created_at < cutoff,
                    ),
                    WorkflowInstanceRow.cancel_requested.is_(True),
                ),
            ).order_by(WorkflowInstanceRow.created_at.asc()) \
                .limit(max(1, min(int(limit), 64))).all()
            return [_row_to_instance(r) for r in rows]

    def count_running_nodes(self, instance_id: str) -> int:
        with self._factory() as db:
            return db.query(WorkflowInstanceNodeRow).filter(
                WorkflowInstanceNodeRow.instance_id == instance_id,
                WorkflowInstanceNodeRow.state == C.NodeState.RUNNING,
            ).count()

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
        """每 owner 活跃子实例计数（≤32 上界的判定输入 [R1-M7]）。

        只计**租约未过期**的 running 子实例 —— 父进程崩溃遗留的泄漏
        RUNNING 行不永久占用上限（租约过期即让位；行本身的清扫为
        follow-up，R2-M5 披露）。
        """
        with self._factory() as db:
            rows = db.query(WorkflowInstanceRow).filter(
                WorkflowInstanceRow.owner_scope == owner_scope,
                WorkflowInstanceRow.status == C.InstanceStatus.RUNNING,
                WorkflowInstanceRow.parent_instance_id.isnot(None),
                WorkflowInstanceRow.parent_instance_id != "",
            ).all()
            now = _utcnow()
            return sum(
                1 for r in rows
                if r.run_lease_expires_at is not None
                and r.run_lease_expires_at > now)

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
    WorkflowEventRow,
    WorkflowInstanceNodeRow,
    WorkflowInstanceRow,
)


def _bounded_event_payload(patch: Dict[str, Any]) -> Dict[str, Any]:
    """转移事件的 bounded payload（证据键投影；绝不内嵌大载荷）。"""
    keys = ("error_code", "output_ref", "bound_ref", "output_fingerprint")
    out = {k: str(patch[k])[:96] for k in keys if patch.get(k)}
    if patch.get("attempts_increment"):
        out["attempts_increment"] = True
    return out
