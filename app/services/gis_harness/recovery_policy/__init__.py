"""Harness 恢复策略包（GOAL 05 / ADR-0279）。

M1 诊断合同 → M2 纯决策 RecoveryPolicy → M5 重试指令 → M6 解释面。
全部纯函数、有界、封闭词表；执行永远归既有通道（LLM 自愈 /
runtime_repair / finalizer / analysis workflow recompute / owner 确认）。
"""
from app.services.gis_harness.recovery_policy.contracts import (
    DIAGNOSTIC_SCHEMA,
    DiagnosticAttribution,
    GISRecoveryDiagnostic,
    GISRecoveryReason,
    ReasonSpec,
    REASON_REGISTRY,
    RetryBudgetSnapshot,
    RecoveryDisposition,
)
from app.services.gis_harness.recovery_policy.diagnose import (
    diagnose_capability_refusal,
    diagnose_eligibility,
    diagnose_goal_gap,
    diagnose_no_progress,
    diagnose_render_gap,
    diagnose_tool_failure,
)

__all__ = [
    "DIAGNOSTIC_SCHEMA",
    "DiagnosticAttribution",
    "GISRecoveryDiagnostic",
    "GISRecoveryReason",
    "ReasonSpec",
    "REASON_REGISTRY",
    "RetryBudgetSnapshot",
    "RecoveryDisposition",
    "diagnose_capability_refusal",
    "diagnose_eligibility",
    "diagnose_goal_gap",
    "diagnose_no_progress",
    "diagnose_render_gap",
    "diagnose_tool_failure",
]
