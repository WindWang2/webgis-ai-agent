"""Feature-proposals 契约 —— 持久要素变更提案的状态机、上限与 typed 异常。

G32（ADR 对应 planning/G32-VECTOR-CHANGE-PROPOSALS）：在 G14 即时编辑之上
增加一层**跨会话/跨重启可恢复**的要素变更候选。提案治理对象（状态、决策、
审计）与 MapSpec review（ADR-0203）同生命周期模式 —— per-session JSON 文件
原子写；基础数据证据是不可变 BlobStore 快照（CAS），不复制 dataset 进提案行。

词表纪律：状态集封闭（TRANSITIONS 是唯一迁移真相）；author_kind 区分
agent/user，几何与结构性提案**恒需 owner 显式批准**（policy 与 G14 一致，
agent 无自批通道 —— decisions 端点 actor 由服务端 auth 上下文裁决）。
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional

# ── 提案状态机（闭包真相；迁移表见 TRANSITIONS）─────────────────────────────

DRAFT = "draft"
SUBMITTED = "submitted"
CHANGES_REQUESTED = "changes_requested"
APPROVED = "approved"
CONFLICTED = "submitted"  # conflicted 不是独立持久状态：有未解决冲突的提案
# 仍是 submitted（决策面投影 conflicts 非空）；这里保留别名注释而非第二个状态，
# 避免同一提案在两个词表间漂移。
MERGING = "merging"
MERGED = "merged"
REJECTED = "rejected"
WITHDRAWN = "withdrawn"
RECOVERY_REQUIRED = "recovery_required"

STATUSES = frozenset(
    {
        DRAFT, SUBMITTED, CHANGES_REQUESTED, APPROVED,
        MERGING, MERGED, REJECTED, WITHDRAWN, RECOVERY_REQUIRED,
    }
)
TERMINAL_STATUSES = frozenset({MERGED, REJECTED, WITHDRAWN})

#: 合法状态迁移（闭包：终态不可迁出）。recovery_required 是 merging 崩溃后的
#: 诚实暂停态 —— 只能经恢复判据（回执可证 → merged；可证未执行 → 回
#: approved 重试）离开，绝不因超时自动重置。
TRANSITIONS: Dict[str, FrozenSet[str]] = {
    DRAFT: frozenset({SUBMITTED, WITHDRAWN}),
    SUBMITTED: frozenset({CHANGES_REQUESTED, APPROVED, REJECTED, WITHDRAWN}),
    CHANGES_REQUESTED: frozenset({SUBMITTED, WITHDRAWN}),
    APPROVED: frozenset({MERGING, WITHDRAWN}),
    MERGING: frozenset({MERGED, RECOVERY_REQUIRED}),
    RECOVERY_REQUIRED: frozenset({MERGING, APPROVED, WITHDRAWN}),
    MERGED: frozenset(),
    REJECTED: frozenset(),
    WITHDRAWN: frozenset(),
}

AUTHOR_KINDS = frozenset({"agent", "user"})
ACTOR_KINDS = frozenset({"user", "agent", "system"})

# ── 上限（提案自身的预算；G14 plan 级上限保持不变并叠加复用）───────────────

MAX_PROPOSALS_PER_SESSION = 50          # 提案治理对象比 MapSpec review 更重，收紧
MAX_OPS_PER_PROPOSAL = 8                # 与 G14 MAX_OPS_PER_PLAN 对齐（单 ref 单 plan 提交）
MAX_PROPOSAL_TARGETS = 200              # 与 G14 MAX_PLAN_TARGETS 对齐
MAX_DECISIONS_PER_PROPOSAL = 64
MAX_COMMENTS_PER_PROPOSAL = 100
MAX_DIFF_PAGE = 100                     # 差异分页（要素级）
MAX_FIELD_DIFFS_PER_FEATURE = 64        # 单要素字段差异上限（显示面）
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024    # base 快照上限（与 RefSpillStore 单载荷上限对齐）
MAX_DRAFT_PROPOSALS_PER_REF = 8         # 同一 ref 并发 draft 数
PROPOSAL_RECOMPUTE_TIMEOUT_S = 30.0     # 单提案重算（diff/冲突）的超时预算

#: 合并租约 TTL（秒）—— 与 review MERGE_LEASE_TTL_SECONDS 同量级；提交桥是
#: 秒级事务，超过即视为进程崩溃残留，读取路径自动回收（见 store.py）。
MERGE_LEASE_TTL_SECONDS = 300.0

#: 提案 op 的允许词表 = G14 EDIT_OPS 全集。数据集级 rename_field/字段迁移
#: 属 schema 操作：首版**拒绝**进入提案（typed finding 指引用原即时入口），
#: 因为 rename 跨 base/current 的字段对齐语义与要素级三方合并不同。
PROPOSAL_ALLOWED_OPS = frozenset(
    {
        "set_fields", "repair_geometry", "move_by", "snap_to_ref",
        "create_features", "delete_features", "replace_geometry",
        "split_feature", "merge_features",
    }
)
PROPOSAL_REJECTED_OPS = frozenset({"rename_field"})


class FeatureProposalError(Exception):
    """feature_proposals typed 错误基类（code + message + detail）。"""

    def __init__(self, code: str, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_payload(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class FeatureProposalBlocked(FeatureProposalError):
    """typed 阻断（4xx 语义：状态/权限/预算/校验拒绝）。"""


class FeatureProposalConflict(FeatureProposalError):
    """冲突类（409 语义：CAS 竞态 / stale base / 未解决冲突）。"""


class FeatureProposalNotFound(FeatureProposalError):
    """提案不存在或不在本会话 scope（404；不泄露他人会话存在性）。"""


class SnapshotUnavailable(FeatureProposalError):
    """base 快照不可恢复（校验不符/已释放/超限）—— typed 阻断 rebase。"""


def can_transition(status: str, target: str) -> bool:
    return target in TRANSITIONS.get(status, frozenset())


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def validate_proposal_ops(ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """提案 op 词表校验：G14 形状之外叠加提案域约束。

    返回 findings 列表（G14 finding 同款结构；空 = 可入库）。
    """
    from app.services.feature_edit.contracts import EDIT_OPS

    findings: List[Dict[str, Any]] = []
    if not isinstance(ops, list) or not ops:
        return [{"code": "invalid_op", "message": "proposal requires at least one edit op"}]
    if len(ops) > MAX_OPS_PER_PROPOSAL:
        findings.append(
            {"code": "op_limit_exceeded", "message": "too many ops in one proposal",
             "detail": {"limit": MAX_OPS_PER_PROPOSAL}}
        )
    for op in ops:
        if not isinstance(op, dict):
            findings.append({"code": "invalid_op", "message": "op must be an object"})
            continue
        kind = op.get("op")
        if kind not in EDIT_OPS:
            findings.append({"code": "invalid_op", "message": "unknown edit op",
                             "detail": {"op": str(kind)[:32]}})
            continue
        if kind in PROPOSAL_REJECTED_OPS:
            findings.append(
                {"code": "op_not_proposable", "message": (
                    f"{kind} is a dataset-level schema operation; use the immediate "
                    "edit path (feature-edits preview/decision) — not proposable"
                ), "detail": {"op": kind}}
            )
    return findings
