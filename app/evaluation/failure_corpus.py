"""Live-failure evaluation corpus（Harness V5 — ADR-0118 决策 D9）。

V4 基线缺口：runtime corpus 全部 deterministic simulation（plan-tier
query→plan 期望），**无真实运行时故障注入类目** —— provider 超时、DB
抖动、空/部分数据、地图源失败、进程重启、stale workspace、取消、重试
耗尽这些真实失败模式没有期望契约（typed class + remediation + 有界终态）。

本语料逐类声明**确定性期望**并驱动真实分类/裁决入口：

- 分类：``classify_harness_failure``（W2 taxonomy —— 真实生产入口，
  dispatch 错误 seam 即调它）；
- 裁决：``remediation_for``（严格预算表）；
- 预算阶梯：attempts 0→max 逐次推进必须最终收敛到
  ``abort_with_disclosure``（无无限循环的结构性证明）。

全部离线、确定、无 LLM、无 sleep（timeout 类用消息标记而非真实等待）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from app.services.gis_harness.failure_taxonomy import (
    HarnessFailureClass,
    classify_harness_failure,
    remediation_for,
)


@dataclass(frozen=True)
class FailureCase:
    """一条故障注入案例：故障签名 → 确定性期望。"""

    case_id: str
    category: str          # fault-injection 类目（见 FAILURE_CATEGORIES）
    # 触发签名（喂给真实 classify_harness_failure 的 kwargs）
    code: str = ""
    error_type: str = ""
    message: str = ""
    exception_kind: str = ""   # "" | "timeout" | "cancelled" | "crs"
    # 确定性期望
    expected_class: HarnessFailureClass = HarnessFailureClass.UNKNOWN
    expected_action: str = "replan"
    #: 耗尽预算后的终态动作（默认 abort_with_disclosure —— 无限循环禁止）
    exhausted_action: str = "abort_with_disclosure"


def _exc(kind: str) -> Any:
    if kind == "timeout":
        return TimeoutError("timed out")
    if kind == "crs":
        try:
            from pyproj.exceptions import CRSError

            return CRSError("invalid projection")
        except Exception:  # noqa: BLE001 — pyproj 缺席退化为标记匹配
            return None
    if kind == "cancelled":
        try:
            from app.services.jobs.cancellation import OperationCancelled

            return OperationCancelled("client gone")
        except Exception:  # noqa: BLE001
            return None
    return None


FAILURE_CATEGORIES = (
    "provider_timeout",
    "transient_db",
    "empty_partial",
    "map_source_error",
    "process_restart",
    "stale_workspace",
    "cancellation",
    "retry_exhaustion",
)


def build_failure_corpus() -> List[FailureCase]:
    """确定性构建（顺序稳定；每类 ≥1 条，覆盖 Epic 八类故障）。"""
    return [
        # ── provider_timeout ────────────────────────────────────
        FailureCase(
            case_id="FL-TIMEOUT-1", category="provider_timeout",
            message="upstream request timed out after 30s",
            expected_class=HarnessFailureClass.TIMEOUT,
            expected_action="retry_with_backoff",
        ),
        FailureCase(
            case_id="FL-TIMEOUT-2", category="provider_timeout",
            exception_kind="timeout", message="fetch failed",
            expected_class=HarnessFailureClass.TIMEOUT,
            expected_action="retry_with_backoff",
        ),
        # ── transient_db ────────────────────────────────────────
        FailureCase(
            case_id="FL-DB-1", category="transient_db",
            message="connection reset by peer during query",
            expected_class=HarnessFailureClass.TIMEOUT,
            expected_action="retry_with_backoff",
        ),
        FailureCase(
            case_id="FL-DB-2", category="transient_db",
            message="database 连接超时（pool exhausted）",
            expected_class=HarnessFailureClass.TIMEOUT,
            expected_action="retry_with_backoff",
        ),
        # ── empty_partial ───────────────────────────────────────
        FailureCase(
            case_id="FL-EMPTY-1", category="empty_partial",
            message="query returned no features within bbox",
            expected_class=HarnessFailureClass.EMPTY_RESULT,
            expected_action="fallback_tool",
        ),
        FailureCase(
            case_id="FL-PARTIAL-1", category="empty_partial",
            message="partial materialization: 半写产物需清理后重试",
            expected_class=HarnessFailureClass.PARTIAL_COMPLETION,
            expected_action="reobserve",
        ),
        # ── map_source_error ────────────────────────────────────
        FailureCase(
            case_id="FL-SOURCE-1", category="map_source_error",
            message="map source load failed for layer product-heat",
            expected_class=HarnessFailureClass.RENDERER_FAILURE,
            expected_action="reobserve",
        ),
        FailureCase(
            case_id="FL-SOURCE-2", category="map_source_error",
            message="瓦片请求失败：图层源加载错误",
            expected_class=HarnessFailureClass.RENDERER_FAILURE,
            expected_action="reobserve",
        ),
        # ── process_restart ─────────────────────────────────────
        # （重启后的 session/ref 缺失 → stale_ref 语义：retry（reload
        # 路径）或披露，绝不静默假成功）
        FailureCase(
            case_id="FL-RESTART-1", category="process_restart",
            message="ref 已过期（session store restarted）",
            expected_class=HarnessFailureClass.STALE_REF,
            expected_action="retry",
        ),
        FailureCase(
            case_id="FL-RESTART-2", category="process_restart",
            message="workspace state expired after restart",
            expected_class=HarnessFailureClass.STALE_REF,
            expected_action="retry",
        ),
        # ── stale_workspace ─────────────────────────────────────
        FailureCase(
            case_id="FL-STALE-1", category="stale_workspace",
            message="artifact stale: source fingerprint mismatch",
            expected_class=HarnessFailureClass.STALE_REF,
            expected_action="retry",
        ),
        FailureCase(
            case_id="FL-STALE-2", category="stale_workspace",
            message="stale ref binding: 源数据已更新，需重新绑定",
            expected_class=HarnessFailureClass.STALE_REF,
            expected_action="retry",
        ),
        # ── cancellation ────────────────────────────────────────
        FailureCase(
            case_id="FL-CANCEL-1", category="cancellation",
            exception_kind="cancelled", message="cancelled",
            expected_class=HarnessFailureClass.CANCELLED,
            expected_action="abort_with_disclosure",
        ),
        FailureCase(
            case_id="FL-CANCEL-2", category="cancellation",
            code="cancelled", message="user aborted turn",
            expected_class=HarnessFailureClass.CANCELLED,
            expected_action="abort_with_disclosure",
        ),
        # ── retry_exhaustion ────────────────────────────────────
        FailureCase(
            case_id="FL-EXHAUST-1", category="retry_exhaustion",
            message="connection refused (attempt 3/3)",
            expected_class=HarnessFailureClass.TIMEOUT,
            expected_action="retry_with_backoff",
        ),
        FailureCase(
            case_id="FL-EXHAUST-2", category="retry_exhaustion",
            code="UNKNOWN_TOOL", message="no such tool after replan",
            expected_class=HarnessFailureClass.TOOL_ERROR,
            expected_action="retry_with_backoff",
        ),
        # ── CRS（W3 修复回归位：不再逃逸泛化 TOOL_ERROR）────────
        FailureCase(
            case_id="FL-CRS-1", category="map_source_error",
            exception_kind="crs", message="reproject failed",
            expected_class=HarnessFailureClass.CRS_ERROR,
            expected_action="substitute_operator",
        ),
    ]


_CORPUS: List[FailureCase] = []


def get_failure_corpus() -> List[FailureCase]:
    global _CORPUS
    if not _CORPUS:
        _CORPUS = build_failure_corpus()
    return list(_CORPUS)


@dataclass(frozen=True)
class FailureVerdict:
    case_id: str
    classified: HarnessFailureClass
    first_action: str
    exhausted_action: str
    budget_ladder: List[Dict[str, Any]] = field(default_factory=list)


def evaluate_failure_case(case: FailureCase) -> FailureVerdict:
    """驱动真实分类/裁决入口并走完整预算阶梯（确定性）。"""
    fc = classify_harness_failure(
        code=case.code or None,
        error_type=case.error_type or None,
        message=case.message or None,
        exception=_exc(case.exception_kind) if case.exception_kind else None,
    )
    decision = remediation_for(fc, attempts=0)
    max_attempts = decision.max_attempts
    ladder: List[Dict[str, Any]] = [decision.to_payload()]
    final_action = decision.action
    for attempt in range(1, max_attempts + 2):  # 走到耗尽为止
        d = remediation_for(fc, attempts=attempt)
        ladder.append(d.to_payload())
        final_action = d.action
        if not d.retry_allowed:
            break
    return FailureVerdict(
        case_id=case.case_id,
        classified=fc,
        first_action=decision.action,
        exhausted_action=final_action,
        budget_ladder=ladder,
    )


def category_coverage(cases: Sequence[FailureCase]) -> Dict[str, int]:
    out: Dict[str, int] = {c: 0 for c in FAILURE_CATEGORIES}
    for case in cases:
        out[case.category] = out.get(case.category, 0) + 1
    return out


__all__ = [
    "FailureCase",
    "FailureVerdict",
    "FAILURE_CATEGORIES",
    "build_failure_corpus",
    "get_failure_corpus",
    "evaluate_failure_case",
    "category_coverage",
]
