"""Workflow Runtime V5 —— typed contracts（节点状态机词表/证据/决策）。

本模块定义运行时的全部类型化契约：节点状态词表、合法转移表（唯一裁决）、
绑定裁决、attempt/复用/重算证据、pending 变更。全部 pydantic、有界、
canonical 指纹可算。纯数据与纯函数；I/O 归 store/driver/service。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 节点状态词表（V5 运行时执行态；与 workflow_instance.StageState 投影词表
#: 不同域 —— 本表是 CAS 控制的执行事实，不是派生投影）。
class NodeState:
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    STALE = "STALE"


NODE_STATES: Tuple[str, ...] = (
    NodeState.PENDING, NodeState.READY, NodeState.RUNNING,
    NodeState.SUCCEEDED, NodeState.FAILED, NodeState.BLOCKED,
    NodeState.SKIPPED, NodeState.CANCELLED, NodeState.STALE,
)

#: 实例级状态（superseded = 被同 plan 新实例取代的终态）。
class InstanceStatus:
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


INSTANCE_TERMINAL_STATUSES = frozenset({
    InstanceStatus.SUCCEEDED, InstanceStatus.FAILED,
    InstanceStatus.CANCELLED, InstanceStatus.SUPERSEDED,
})

#: 合法节点转移表（唯一裁决；转移表外一律 typed 拒绝）。
#: 键 = from_state，值 = frozenset(to_state)。
LEGAL_TRANSITIONS: Dict[str, frozenset] = {
    NodeState.PENDING: frozenset({
        NodeState.READY, NodeState.BLOCKED, NodeState.SKIPPED,
        NodeState.CANCELLED,
    }),
    NodeState.READY: frozenset({
        NodeState.RUNNING, NodeState.BLOCKED, NodeState.STALE,
        NodeState.CANCELLED,
    }),
    NodeState.RUNNING: frozenset({
        NodeState.SUCCEEDED, NodeState.FAILED, NodeState.CANCELLED,
        # 恢复专用：孤儿 RUNNING（claim 者租约已死）复位 READY。
        # 仅 driver 恢复清扫（实例租约门控）调用，绝非通用旁路。
        NodeState.READY,
    }),
    NodeState.FAILED: frozenset({NodeState.READY, NodeState.SKIPPED}),
    NodeState.BLOCKED: frozenset({NodeState.READY, NodeState.SKIPPED}),
    NodeState.SUCCEEDED: frozenset({NodeState.STALE}),
    NodeState.STALE: frozenset({NodeState.READY, NodeState.SUCCEEDED}),
    NodeState.SKIPPED: frozenset({NodeState.READY}),
    NodeState.CANCELLED: frozenset(),
}

#: 转移所需 reason code 前缀（可读阻断/推进语义；空 = 无要求）。
TRANSITION_REASON_REQUIRED = frozenset({
    f"{NodeState.PENDING}>{NodeState.BLOCKED}",
    f"{NodeState.READY}>{NodeState.BLOCKED}",
    f"{NodeState.RUNNING}>{NodeState.FAILED}",
    f"{NodeState.STALE}>{NodeState.SUCCEEDED}",
    f"{NodeState.SUCCEEDED}>{NodeState.STALE}",
})

#: 每节点转移环形记录上限；实例级 RecomputeDecision 环上限。
MAX_NODE_TRANSITIONS = 8
MAX_INSTANCE_DECISIONS = 16
#: pending changes 上限（实例行）；单次 apply 的 changes 上限。
MAX_PENDING_CHANGES = 16
MAX_APPLY_CHANGES = 16
#: 每节点 attempts 上界（RetryPolicy 上界一致）。
MAX_NODE_ATTEMPTS = 3
#: 实例节点数硬上限（继承 typed DAG cap 64）。
MAX_INSTANCE_NODES = 64

#: 绑定裁决动作（义务 on_violation 同词表语义）。
BINDING_ACTIONS = ("pass", "blocked", "degraded")


#: 事件日志词表（Workflow V6 append-only journal；kind 列的唯一真相）。
class EventKind:
    STATE_TRANSITION = "state_transition"
    INSTANCE_CANCEL_REQUESTED = "instance_cancel_requested"
    NODE_CANCEL_REQUESTED = "node_cancel_requested"
    RECOVERY_ORPHAN_RESET = "recovery_orphan_reset"
    RECOVERY_FINALIZE = "recovery_finalize"
    RETRY_SCHEDULED = "retry_scheduled"
    RETRY_EXHAUSTED = "retry_exhausted"
    COMPENSATION = "compensation"
    COMPENSATION_FAILED = "compensation_failed"
    CLONE = "clone"
    NODES_REQUEUED = "nodes_requeued"


#: journal 实际会发出的词表（review 修正：裁掉从未埋点的虚词 ——
#: LEASE_*/NODE_HEARTBEAT/ATTEMPT_STARTED/DUPLICATE_SUPPRESSED/DISPATCH/
#: WORKER_LOSS；这些事实分别在节点行租约列 / attempts_log / durable 通道
#: 有自己的单一真相，不在 journal 重复记录）。
EVENT_KINDS: Tuple[str, ...] = (
    EventKind.STATE_TRANSITION, EventKind.INSTANCE_CANCEL_REQUESTED,
    EventKind.NODE_CANCEL_REQUESTED, EventKind.RECOVERY_ORPHAN_RESET,
    EventKind.RECOVERY_FINALIZE, EventKind.RETRY_SCHEDULED,
    EventKind.RETRY_EXHAUSTED, EventKind.COMPENSATION,
    EventKind.COMPENSATION_FAILED, EventKind.CLONE,
    EventKind.NODES_REQUEUED,
)


def event_kind_for_transition(to_state: str) -> str:
    """状态转移 → 事件 kind（journal 词表与状态机词表解耦的映射点）。"""
    return EventKind.STATE_TRANSITION


def is_terminal(node_state: str) -> bool:
    """「已决」状态（执行序不再推进；SUCCEEDED 仍可被 STALE 失效，
    FAILED/SKIPPED 仍可重新资格化 —— 终态语义见 LEGAL_TRANSITIONS）。"""
    return node_state in (
        NodeState.SUCCEEDED, NodeState.FAILED, NodeState.SKIPPED,
        NodeState.CANCELLED,
    )


def validate_transition(from_state: str, to_state: str) -> Optional[str]:
    """转移合法性裁决（纯函数）。合法 → None；非法 → reason code。

    LEGAL_TRANSITIONS 是**唯一**裁决（CANCELLED 转移集为空 = 唯一吸收态）；
    不做终态早退 —— 否则会与 SUCCEEDED→STALE / SKIPPED→READY 等表内
    合法转移冲突。
    """
    if from_state not in NODE_STATES or to_state not in NODE_STATES:
        return f"ILLEGAL_STATE:{from_state}:{to_state}"
    if to_state not in LEGAL_TRANSITIONS.get(from_state, frozenset()):
        return f"ILLEGAL_TRANSITION:{from_state}>{to_state}"
    return None


class StateTransitionRecord(BaseModel):
    """节点转移环形记录（无时间戳 —— state_revision 即序）。"""
    revision: int
    from_state: str
    to_state: str
    reason_code: str = ""
    event: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "rev": self.revision,
            "from": self.from_state[:16],
            "to": self.to_state[:16],
            "reason": self.reason_code[:96],
            "event": self.event[:32],
        }


class PortViolation(BaseModel):
    """单个 typed port 校验违规（机器可读 reason code）。"""
    port: str
    code: str                       # ARTIFACT_TYPE_MISMATCH / CRS_CLASS_MISMATCH / ...
    detail: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "port": self.port[:48],
            "code": self.code[:48],
            "detail": self.detail[:200],
        }


class BindingVerdict(BaseModel):
    """运行时 typed port 校验裁决（PASS/阻断证据都落库）。"""
    node_id: str
    ok: bool
    action: str = "pass"            # pass | blocked | degraded
    violations: List[PortViolation] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id[:64],
            "ok": self.ok,
            "action": self.action[:16],
            "violations": [v.to_bounded_dict()
                           for v in self.violations[:8]],
            "disclosures": [d[:200] for d in self.disclosures[:4]],
        }


class ReuseEvidence(BaseModel):
    """复用证明（「为什么没重算」的机器可读答案）。"""
    reused: bool = False
    reuse_fingerprint: str = ""
    artifact_ref: str = ""
    source_instance_id: str = ""
    verified_inputs: List[str] = Field(default_factory=list)  # port:fp
    fingerprint_level: str = ""     # content | profile_digest
    skipped_reason: str = ""        # 未复用时的类型化原因

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "reused": self.reused,
            "reuse_fingerprint": self.reuse_fingerprint[:32],
            "artifact_ref": self.artifact_ref[:96],
            "source_instance_id": self.source_instance_id[:64],
            "verified_inputs": [v[:96] for v in self.verified_inputs[:8]],
            "fingerprint_level": self.fingerprint_level[:24],
            "skipped_reason": self.skipped_reason[:96],
        }


class NodeAttempt(BaseModel):
    """一次执行尝试的摘要证据（无载荷）。"""
    attempt: int
    status: str                     # succeeded | failed | cancelled
    error_code: str = ""
    error_message: str = ""
    duration_ms: Optional[int] = None
    backend: str = ""               # geocompute_inprocess | geocompute_durable | harness_record
    output_ref: str = ""

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "attempt": self.attempt,
            "status": self.status[:16],
            "error_code": self.error_code[:64],
            "error_message": self.error_message[:200],
            "duration_ms": self.duration_ms,
            "backend": self.backend[:32],
            "output_ref": self.output_ref[:96],
        }


class PendingChange(BaseModel):
    """RUNNING 期间到达的变更（quiescence 门排队，完成边界补评）。"""
    dimension: str
    target_kind: str
    target: str = ""
    detail: str = ""
    source: str = ""                # api | style_hook | data_hook

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension[:24],
            "target_kind": self.target_kind[:24],
            "target": self.target[:64],
            "detail": self.detail[:200],
            "source": self.source[:24],
        }


class RecomputeDecision(BaseModel):
    """一次增量重算决策的完整证据（可解释）。"""
    seq: int
    changes_fingerprint: str
    dimensions: List[str] = Field(default_factory=list)
    marked_stale: List[str] = Field(default_factory=list)
    reuse_resolved: List[str] = Field(default_factory=list)   # STALE→SUCCEEDED
    recomputed: List[str] = Field(default_factory=list)
    reused: List[str] = Field(default_factory=list)
    style_only: bool = False
    explanations: List[str] = Field(default_factory=list)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "changes_fp": self.changes_fingerprint[:32],
            "dimensions": [d[:24] for d in self.dimensions[:5]],
            "marked_stale": [n[:64] for n in self.marked_stale[:32]],
            "reuse_resolved": [n[:64] for n in self.reuse_resolved[:32]],
            "recomputed": [n[:64] for n in self.recomputed[:32]],
            "reused": [n[:64] for n in self.reused[:32]],
            "style_only": self.style_only,
            "explanations": [e[:200] for e in self.explanations[:6]],
        }


class NodeRuntimeState(BaseModel):
    """单节点运行态（store 节点行的 schema；每节点独立 CAS）。"""
    instance_id: str
    node_id: str
    state: str = NodeState.PENDING
    state_revision: int = 1
    claimed_by: str = ""            # READY→RUNNING 认领者（run token）
    attempts: int = 0
    error_code: str = ""
    bound_ref: str = ""
    output_ref: str = ""
    output_fingerprint: str = ""
    binding: Dict[str, Any] = Field(default_factory=dict)     # BindingVerdict
    reuse: Dict[str, Any] = Field(default_factory=dict)       # ReuseEvidence
    attempts_log: List[Dict[str, Any]] = Field(default_factory=list)
    transitions: List[Dict[str, Any]] = Field(default_factory=list)
    updated_seq: int = 0

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id[:64],
            "state": self.state,
            "state_revision": self.state_revision,
            "attempts": self.attempts,
            "error_code": self.error_code[:64],
            "bound_ref": self.bound_ref[:96],
            "output_ref": self.output_ref[:96],
            "output_fingerprint": self.output_fingerprint[:32],
            "binding": _bounded_json(self.binding),
            "reuse": _bounded_json(self.reuse),
            "attempts_log": self.attempts_log[:MAX_NODE_ATTEMPTS],
            "transitions": self.transitions[:MAX_NODE_TRANSITIONS],
        }


def _bounded_json(payload: Any, max_bytes: int = 4096) -> Any:
    """节点行内嵌 JSON 的有界投影（超界诚实截断标记）。"""
    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          default=str)
    except Exception:  # noqa: BLE001
        return {"_truncated": "unserializable"}
    if len(text) <= max_bytes:
        return payload
    return {"_truncated": True, "_bytes": len(text)}


def changes_fingerprint(changes: List[PendingChange]) -> str:
    """变更集 canonical 指纹（决策证据/幂等键用；同变更集同指纹，
    与书写顺序无关）。"""
    items = sorted(
        json.dumps(
            c.to_bounded_dict(), sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), default=str,
        )
        for c in changes[:MAX_APPLY_CHANGES]
    )
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8"),
                          usedforsecurity=False).hexdigest()[:32]


def instance_status_from_nodes(
    statuses: Dict[str, str], *, optional_nodes: Dict[str, bool],
) -> str:
    """实例级状态裁决（架构 §4 [R1-MINOR-3]，纯函数）。

    - 全部节点 SUCCEEDED/SKIPPED → succeeded；
    - 任一非 optional 节点 FAILED（重试耗尽）→ failed；
    - 任一非 optional 节点 BLOCKED（不可解除）→ failed（诚实终态）；
    - 其余（存在 PENDING/READY/RUNNING/STALE）→ running。
    """
    if not statuses:
        return InstanceStatus.RUNNING
    if any(s == NodeState.RUNNING for s in statuses.values()):
        return InstanceStatus.RUNNING
    # 取消优先于成功：任一节点被取消 → 实例 cancelled（不洗成 succeeded）
    if any(s == NodeState.CANCELLED for s in statuses.values()):
        return InstanceStatus.CANCELLED
    if all(s in (NodeState.SUCCEEDED, NodeState.SKIPPED)
           for s in statuses.values()):
        return InstanceStatus.SUCCEEDED
    for node_id, s in statuses.items():
        required = not optional_nodes.get(node_id, False)
        if required and s in (NodeState.FAILED, NodeState.BLOCKED):
            return InstanceStatus.FAILED
    return InstanceStatus.RUNNING
