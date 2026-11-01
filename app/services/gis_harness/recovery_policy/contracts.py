"""GISRecoveryDiagnostic.v1 契约（GOAL 05 / M1，ADR-0279）。

定位（防重复施工红线）：

- 本层**不是第二个失败分类器**。粗类仍是 ``HarnessFailureClass`` 11 类
  （ADR-0248 D2，词表被 5 个评测语料钉死）；本层在其上补三件既有系统
  没有的事：
  1. **细粒度封闭 reason 码**——HTTP 429/5xx/权限、ref 跨会话、CRS 歧义、
     eligibility、no-progress、MapSpec 未收敛、goal 差距等真实运维语义；
  2. **六处置裁决面**（retryable/repairable/needs_user_data/
     needs_user_approval/unsafe_to_retry/terminal）——回答"下一步允许
     自动做什么/什么必须问人"，而非仅"重试什么动作"（RemediationAction，
     动作面）或"安全级别"（SAFETY_CLASSES，安全面）。三层各自封闭正交；
  3. **归因 ID**（session/turn/step/capability/ref/revision），全部
     本会话已有 ID，不含跨租户信息。
- 纯函数纪律（同 ADR-0183）：零 IO、零时钟 —— 同输入同输出，可确定性
  回放；所有字段有界。
- 消费方：ToolDispatchService 错误分支（additive ``recovery`` 键）、
  RecoveryPolicy（M2）、explain（M6）、continuation（M5）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

DIAGNOSTIC_SCHEMA = "gis_recovery_diagnostic.v1"

#: 六处置（任务书词表；封闭，测试钉死）。
RECOVERY_DISPOSITIONS = frozenset({
    "retryable",              # 同参重试安全（读/幂等 + 预算内 + 无副作用门）
    "repairable",             # 需要修复动作（换参/换路径/重观察/降级/重算子图）
    "needs_user_data",        # 缺数据/字段/数据源决策，必须用户提供
    "needs_user_approval",    # 需 owner 确认（写/付费/权限恢复）
    "unsafe_to_retry",        # 同参重试不安全或违规（跨会话 ref/副作用未决）
    "terminal",               # 本任务内不可恢复（取消/预算耗尽/无进展）
})


class RecoveryDisposition(str, Enum):
    RETRYABLE = "retryable"
    REPAIRABLE = "repairable"
    NEEDS_USER_DATA = "needs_user_data"
    NEEDS_USER_APPROVAL = "needs_user_approval"
    UNSAFE_TO_RETRY = "unsafe_to_retry"
    TERMINAL = "terminal"


class GISRecoveryReason(str, Enum):
    """诊断层封闭 reason 码（28 码；每个映射回既有 HarnessFailureClass）。

    新码只能整组加入并在 REASON_REGISTRY 登记（词表封闭性由
    tests/.../test_contracts.py 钉死：registry ↔ enum 双射）。
    """

    # ── 工具执行族（细化为既有 11 类） ────────────────────────────────
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    DATA_INVALID = "data_invalid"
    CRS_ERROR = "crs_error"
    CRS_AMBIGUOUS = "crs_ambiguous"
    EMPTY_RESULT = "empty_result"
    STALE_REF = "stale_ref"
    REF_EXPIRED = "ref_expired"
    OUTPUT_EXPIRED = "output_expired"
    CROSS_SESSION_REF = "cross_session_ref"
    RENDERER_FAILURE = "renderer_failure"
    MAPSPEC_NOT_CONVERGED = "mapspec_not_converged"
    TRANSIENT_TIMEOUT = "transient_timeout"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_SERVER_ERROR = "upstream_server_error"
    PERMISSION_DENIED = "permission_denied"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CAPABILITY_INELIGIBLE = "capability_ineligible"
    PARTIAL_COMPLETION = "partial_completion"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNKNOWN_FAILURE = "unknown_failure"
    # ── 任务级族（跨执行阶段的归因） ──────────────────────────────────
    ELIGIBILITY_BLOCKED = "eligibility_blocked"
    ELIGIBILITY_DEGRADED = "eligibility_degraded"
    NO_PROGRESS = "no_progress"
    GOAL_GAP_RENDERING = "goal_gap_rendering"
    GOAL_GAP_SCIENCE = "goal_gap_science"
    GOAL_GAP_EXPORT = "goal_gap_export"


#: 合法 hint 词表（既有 RemediationAction + 用户面两码；封闭）。
_VALID_HINTS = frozenset({
    "retry", "retry_with_backoff", "fallback_tool", "substitute_operator",
    "requery_profile", "replan", "reobserve",
    "ask_user_data", "owner_confirmation", "abort_with_disclosure",
})


@dataclass(frozen=True)
class ReasonSpec:
    """一个 reason 码的确定性裁决要素。"""
    harness_class: str                       # 既有 HarnessFailureClass 值
    disposition: RecoveryDisposition
    hints: Tuple[str, ...]                   # 建议动作（合法 hint 子集）


#: reason → 裁决要素的唯一事实源（封闭；新码必须在此登记）。
REASON_REGISTRY: Dict[str, ReasonSpec] = {
    # 工具执行族
    "tool_execution_failed": ReasonSpec(
        "tool_error", RecoveryDisposition.REPAIRABLE, ("replan",)),
    "data_invalid": ReasonSpec(
        "data_error", RecoveryDisposition.NEEDS_USER_DATA, ("ask_user_data",)),
    "crs_error": ReasonSpec(
        "crs_error", RecoveryDisposition.REPAIRABLE, ("substitute_operator",)),
    "crs_ambiguous": ReasonSpec(
        "crs_error", RecoveryDisposition.NEEDS_USER_DATA,
        ("ask_user_data", "substitute_operator")),
    "empty_result": ReasonSpec(
        "empty_result", RecoveryDisposition.REPAIRABLE, ("fallback_tool",)),
    "stale_ref": ReasonSpec(
        "stale_ref", RecoveryDisposition.RETRYABLE, ("retry", "requery_profile")),
    "ref_expired": ReasonSpec(
        "stale_ref", RecoveryDisposition.REPAIRABLE, ("requery_profile", "replan")),
    "output_expired": ReasonSpec(
        "stale_ref", RecoveryDisposition.REPAIRABLE, ("replan",)),
    "cross_session_ref": ReasonSpec(
        "stale_ref", RecoveryDisposition.UNSAFE_TO_RETRY, ("abort_with_disclosure",)),
    "renderer_failure": ReasonSpec(
        "renderer_failure", RecoveryDisposition.REPAIRABLE, ("reobserve",)),
    "mapspec_not_converged": ReasonSpec(
        "renderer_failure", RecoveryDisposition.REPAIRABLE,
        ("reobserve", "replan")),
    "transient_timeout": ReasonSpec(
        "timeout", RecoveryDisposition.RETRYABLE, ("retry_with_backoff",)),
    "rate_limited": ReasonSpec(
        "timeout", RecoveryDisposition.RETRYABLE, ("retry_with_backoff",)),
    "upstream_server_error": ReasonSpec(
        "timeout", RecoveryDisposition.RETRYABLE, ("retry_with_backoff",)),
    "permission_denied": ReasonSpec(
        "tool_error", RecoveryDisposition.NEEDS_USER_APPROVAL,
        ("owner_confirmation",)),
    "provider_unavailable": ReasonSpec(
        "tool_error", RecoveryDisposition.REPAIRABLE, ("fallback_tool",)),
    "capability_ineligible": ReasonSpec(
        "tool_error", RecoveryDisposition.REPAIRABLE, ("fallback_tool", "replan")),
    "partial_completion": ReasonSpec(
        "partial_completion", RecoveryDisposition.REPAIRABLE, ("reobserve",)),
    "cancelled": ReasonSpec(
        "cancelled", RecoveryDisposition.TERMINAL, ("abort_with_disclosure",)),
    "budget_exhausted": ReasonSpec(
        "budget_exhausted", RecoveryDisposition.TERMINAL,
        ("abort_with_disclosure",)),
    "unknown_failure": ReasonSpec(
        "unknown", RecoveryDisposition.REPAIRABLE, ("replan",)),
    # 任务级族
    "eligibility_blocked": ReasonSpec(
        "data_error", RecoveryDisposition.NEEDS_USER_DATA, ("ask_user_data",)),
    "eligibility_degraded": ReasonSpec(
        "data_error", RecoveryDisposition.REPAIRABLE,
        ("requery_profile", "replan")),
    "no_progress": ReasonSpec(
        "unknown", RecoveryDisposition.TERMINAL, ("abort_with_disclosure",)),
    "goal_gap_rendering": ReasonSpec(
        "renderer_failure", RecoveryDisposition.REPAIRABLE, ("reobserve",)),
    "goal_gap_science": ReasonSpec(
        "data_error", RecoveryDisposition.REPAIRABLE,
        ("requery_profile", "replan")),
    "goal_gap_export": ReasonSpec(
        "tool_error", RecoveryDisposition.REPAIRABLE, ("retry", "replan")),
}


# ── 有界常量 ─────────────────────────────────────────────────────────────

MAX_DETAIL = 200
MAX_EVIDENCE_REFS = 8
MAX_HINTS = 4
MAX_ID = 64


# ── 模型 ─────────────────────────────────────────────────────────────────

class DiagnosticAttribution(BaseModel):
    """归因 ID（全部本会话已有 ID；缺省空串 = 未归因，不猜）。"""

    session_id: str = ""
    turn_id: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    capability: str = ""
    ref: str = ""
    workflow_id: str = ""
    step_id: str = ""
    plan_revision: str = ""
    mapspec_revision: str = ""

    def to_bounded_dict(self) -> Dict[str, str]:
        return {k: str(getattr(self, k) or "")[:MAX_ID] for k in (
            "session_id", "turn_id", "tool_call_id", "tool_name",
            "capability", "ref", "workflow_id", "step_id",
            "plan_revision", "mapspec_revision",
        )}


class RetryBudgetSnapshot(BaseModel):
    """预算快照（来自既有 RemediationLedger/RecoveryLedger 记账；只读）。"""
    attempts: int = 0
    max_attempts: int = 0
    retry_allowed: bool = False


class GISRecoveryDiagnostic(BaseModel):
    """一次失败/差距的统一诊断（纯数据；零 IO 零时钟）。"""
    schema_version: str = DIAGNOSTIC_SCHEMA
    reason: GISRecoveryReason
    # 处置以 REASON_REGISTRY 为权威（model_post_init 覆写）；
    # 默认值只是 pydantic 必填豁免，不构成裁决来源。
    disposition: RecoveryDisposition = RecoveryDisposition.REPAIRABLE
    failure_class: str = ""                 # 既有 HarnessFailureClass 值
    detail: str = ""
    attribution: DiagnosticAttribution = Field(
        default_factory=DiagnosticAttribution)
    retry: Optional[RetryBudgetSnapshot] = None
    evidence_refs: List[str] = Field(default_factory=list)
    hints: List[str] = Field(default_factory=list)

    # ── 构造纪律 ────────────────────────────────────────────────────
    def model_post_init(self, __context) -> None:
        spec = REASON_REGISTRY.get(self.reason.value)
        if spec is not None:
            # 处置与 hint 以 registry 为权威：构造方不能私自放宽
            # （ unsafe 细化由 diagnose 层做，不允许在数据面漂移）。
            self.disposition = spec.disposition
            if not self.hints:
                self.hints = list(spec.hints)
        if not self.failure_class:
            self.failure_class = spec.harness_class if spec else "unknown"
        self.hints = [h for h in self.hints if h in _VALID_HINTS][:MAX_HINTS]

    @classmethod
    def from_harness_failure_payload(
        cls, payload: Optional[Dict], *, reason: GISRecoveryReason,
        attribution: Optional[DiagnosticAttribution] = None,
        detail: str = "",
        evidence_refs: Optional[Sequence[str]] = None,
    ) -> "GISRecoveryDiagnostic":
        """从 TDS 既有 ``harness_failure`` dict 构建（键：class/attempts/
        max_attempts/retry_allowed）；payload 异常时预算缺席（不猜）。"""
        retry: Optional[RetryBudgetSnapshot] = None
        if isinstance(payload, dict) and any(
                k in payload for k in
                ("attempts", "max_attempts", "retry_allowed")):
            try:
                retry = RetryBudgetSnapshot(
                    attempts=int(payload.get("attempts", 0) or 0),
                    max_attempts=int(payload.get("max_attempts", 0) or 0),
                    retry_allowed=bool(payload.get("retry_allowed", False)),
                )
            except (TypeError, ValueError):
                retry = None
        return cls(
            reason=reason,
            attribution=attribution or DiagnosticAttribution(),
            retry=retry,
            detail=detail,
            evidence_refs=list(evidence_refs or [])[:MAX_EVIDENCE_REFS],
        )

    def to_bounded_dict(self) -> Dict:
        return {
            "schema_version": DIAGNOSTIC_SCHEMA,
            "reason": self.reason.value,
            "disposition": self.disposition.value,
            "failure_class": str(self.failure_class or "unknown")[:32],
            "detail": str(self.detail or "")[:MAX_DETAIL],
            "attribution": self.attribution.to_bounded_dict(),
            "retry": (None if self.retry is None else {
                "attempts": self.retry.attempts,
                "max_attempts": self.retry.max_attempts,
                "retry_allowed": self.retry.retry_allowed,
            }),
            "evidence_refs": [
                str(e)[:64] for e in self.evidence_refs[:MAX_EVIDENCE_REFS]],
            "hints": list(self.hints[:MAX_HINTS]),
        }


__all__ = [
    "DIAGNOSTIC_SCHEMA",
    "RECOVERY_DISPOSITIONS",
    "RecoveryDisposition",
    "GISRecoveryReason",
    "ReasonSpec",
    "REASON_REGISTRY",
    "DiagnosticAttribution",
    "RetryBudgetSnapshot",
    "GISRecoveryDiagnostic",
]
