"""GOAL 05 恢复语料（M7，ADR-0281）—— 每条含输入状态、允许动作、
绝不允许动作列表的确定性回放案例库。

三个 oracle 族（全部驱动**生产纯函数**，非影子实现）：
- ``tool_failure``：``recovery_policy.wiring.build_recovery_payload``
  （TDS 单 seam 同一入口）→ 断言 reason/disposition/allowed/forbidden/
  auto_retry；
- ``goal_verification``：``goal_verification.reconcile_goal_verification``
  → 断言 overall/矛盾；
- ``continuation``：``recovery_policy.continuation.resolve_retry_directive``
  → 断言 verdict（跨会话 ref fail-closed 等）。

与既有 reliability_corpus（行为不变量）互补：本库锁定**恢复裁决语义**。
类别覆盖 HTTP 429 / 网络抖动 / 用户取消 / 重试幂等 / CRS unknown / 输入
质量 / 图层缺失 / 错会话 ref / 权限拒绝 / 旧 MapSpec generation / 显示
成功但目标未达成 / 多子图失败 / no-progress / 自恢复后回归验证 等。
"""
from __future__ import annotations

from pydantic import JsonValue
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ORACLE_TOOL_FAILURE = "tool_failure"
ORACLE_GOAL_VERIFICATION = "goal_verification"
ORACLE_CONTINUATION = "continuation"


@dataclass(frozen=True)
class RecoveryCase:
    case_id: str
    category: str
    description: str
    oracle: str
    input_state: Dict[str, Any]
    expected_reason: str = ""
    expected_disposition: str = ""
    allowed_actions: Tuple[str, ...] = ()
    forbidden_actions: Tuple[str, ...] = ()
    expect_auto_retry: Optional[bool] = None
    expect_overall: str = ""
    expect_contradiction: bool = False
    expect_verdict: str = ""


@dataclass(frozen=True)
class RecoveryCaseVerdict:
    case_id: str
    passed: bool
    failures: Tuple[str, ...] = field(default_factory=tuple)


def _tf_input(message: str = "", *, code: str = "", status: str = "",
              error_type: str = "", tool: str = "query_pois",
              hf_class: str = "tool_error", attempts: int = 0,
              max_attempts: int = 3, retry_allowed: bool = True,
              side_effect: str = "pure", tier: int = 1,
              ref_origin: Optional[str] = None,
              session: str = "sess-cur") -> Dict[str, Any]:
    return {
        "message": message, "code": code, "status": status,
        "error_type": error_type, "tool": tool,
        "harness_failure": {"class": hf_class,
                            "attempts": attempts, "max_attempts": max_attempts,
                            "retry_allowed": retry_allowed},
        "risk": {"side_effect_class": side_effect, "tier": tier},
        "ref_origin_session": ref_origin, "session_id": session,
    }


def _gv_input(**kw: JsonValue) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "goal_report": {
            "verdict": "satisfied",
            "requirements": [
                {"requirement_id": "r-map", "kind": "map",
                 "verdict": "fulfilled"},
                {"requirement_id": "r-sci", "kind": "analysis",
                 "verdict": "fulfilled"},
                {"requirement_id": "r-exp", "kind": "export",
                 "verdict": "fulfilled"},
            ],
            "missing_summary": [],
        },
        "observation_health": {
            "layers": {"l1": "data_present"},
            "aggregate": "data_present", "health": "ok",
            "observed_revision": 2,
        },
        "chapter": {"workflow_contract": {"roles": [
            {"role": "a", "required": True, "status": "bound"}]}},
        "task_complete": True,
        "quality_review_present": True,
    }
    base.update(kw)
    return base


def _cont_input(**kw: JsonValue) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "progress_rows": [
            {"capability": "ingest", "status": "complete",
             "bound_ref": "ref:d1"},
            {"capability": "analyze", "status": "failed", "bound_ref": None},
        ],
        "last_recovery": {
            "tool": "analyze",
            "diagnostic": {"reason": "transient_timeout",
                           "disposition": "retryable",
                           "retry": {"attempts": 1, "max_attempts": 2,
                                     "retry_allowed": True}},
        },
        "chapter": {"workflow_contract": {"workflow_id": "wf-1"}},
        "user_ref_hints": [],
        "ref_owner_sessions": None,
        "current_session_id": "sess-cur",
    }
    base.update(kw)
    return base


def build_recovery_corpus() -> List[RecoveryCase]:
    cases: List[RecoveryCase] = []
    add = cases.append

    # ── HTTP 429 / 5xx / 网络 ────────────────────────────────────────
    add(RecoveryCase(
        "rc/http-429-read", "http_429", "读工具 429 → 退避重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("HTTP 429 Too Many Requests", tool="query_pois"),
        expected_reason="rate_limited", expected_disposition="retryable",
        allowed_actions=("retry_with_backoff",),
        forbidden_actions=("owner_confirmation", "abort_with_disclosure")))
    add(RecoveryCase(
        "rc/http-429-mutating", "http_429",
        "状态变更工具 429 → 绝不自动重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("HTTP 429", tool="commit_edit",
                  side_effect="state_mutation"),
        expected_reason="rate_limited",
        forbidden_actions=("retry", "retry_with_backoff"),
        expect_auto_retry=False))
    add(RecoveryCase(
        "rc/upstream-503", "network_jitter", "上游 503 → 退避重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("503 Service Unavailable"),
        expected_reason="upstream_server_error",
        expected_disposition="retryable",
        allowed_actions=("retry_with_backoff",),
        forbidden_actions=("abort_with_disclosure",)))
    add(RecoveryCase(
        "rc/network-timeout", "network_jitter", "连接超时 → 退避重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("connection timed out", hf_class="timeout"),
        expected_reason="transient_timeout",
        expected_disposition="retryable",
        allowed_actions=("retry_with_backoff",),
        forbidden_actions=("owner_confirmation",)))

    # ── 取消 / 预算 / no-progress ────────────────────────────────────
    add(RecoveryCase(
        "rc/user-cancel", "user_cancel", "用户取消 → terminal 零动作",
        ORACLE_TOOL_FAILURE,
        _tf_input("cancelled", hf_class="cancelled", retry_allowed=False,
                  max_attempts=0),
        expected_reason="cancelled", expected_disposition="terminal",
        allowed_actions=("abort_with_disclosure",),
        forbidden_actions=("retry", "retry_with_backoff", "replan")))
    add(RecoveryCase(
        "rc/budget-exhausted", "retry_idempotency",
        "重试预算耗尽 → 诚实终止",
        ORACLE_TOOL_FAILURE,
        _tf_input("timeout again", hf_class="timeout", attempts=2,
                  max_attempts=2, retry_allowed=False),
        # 诊断面仍如实归类 retryable（失败种类可重试）；终止发生在
        # decision 层（预算耗尽 → abort_with_disclosure）。
        expected_disposition="retryable",
        allowed_actions=("abort_with_disclosure",),
        forbidden_actions=("retry",),
        expect_auto_retry=False))
    add(RecoveryCase(
        "rc/no-progress", "no_progress", "无进展 → terminal",
        ORACLE_TOOL_FAILURE,
        _tf_input("same empty result again", hf_class="unknown"),
        expected_reason="unknown_failure", expected_disposition="repairable",
        allowed_actions=("replan",),
        forbidden_actions=("retry", "retry_with_backoff")))
    add(RecoveryCase(
        "rc/retry-idempotent-key", "retry_idempotency",
        "幂等键在场的 state_mutation → 有界自动重试",
        ORACLE_TOOL_FAILURE,
        dict(_tf_input("transient", hf_class="timeout", tool="upsert_layer",
                       side_effect="state_mutation"),
             idempotency_key_present=True),
        expected_disposition="retryable", expect_auto_retry=True,
        allowed_actions=("retry_with_backoff",),
        forbidden_actions=("owner_confirmation",)))

    # ── CRS / 输入质量 ───────────────────────────────────────────────
    add(RecoveryCase(
        "rc/crs-unknown", "crs_unknown", "CRS 歧义 → 必须问用户",
        ORACLE_TOOL_FAILURE,
        _tf_input("ambiguous CRS: cannot determine source projection",
                  hf_class="crs_error"),
        expected_reason="crs_ambiguous",
        expected_disposition="needs_user_data",
        allowed_actions=("ask_user_data",),
        forbidden_actions=("retry", "retry_with_backoff")))
    add(RecoveryCase(
        "rc/crs-plain", "crs_unknown", "明确 CRS 失败 → 换投影路径",
        ORACLE_TOOL_FAILURE,
        _tf_input("cannot transform to EPSG:9999", hf_class="crs_error"),
        expected_reason="crs_error", expected_disposition="repairable",
        allowed_actions=("substitute_operator",),
        forbidden_actions=("ask_user_data",)))
    add(RecoveryCase(
        "rc/eligibility-blocked", "input_quality",
        "数据缺字段（成都小学缺 score）→ 问用户",
        ORACLE_TOOL_FAILURE,
        _tf_input("", hf_class="data_error"),
        expected_reason="data_invalid", expected_disposition="needs_user_data",
        allowed_actions=("ask_user_data",),
        forbidden_actions=("retry",)))
    add(RecoveryCase(
        "rc/eligibility-degraded", "input_quality",
        "数据可转换 → 修复路径",
        ORACLE_TOOL_FAILURE,
        _tf_input("dataset needs reprojection before use",
                  hf_class="data_error"),
        expected_reason="data_invalid", expected_disposition="needs_user_data",
        forbidden_actions=("retry",)))

    # ── 图层 / 渲染 / MapSpec generation ─────────────────────────────
    add(RecoveryCase(
        "rc/layer-missing", "layer_missing",
        "结果层缺失（分析完成但没显示）→ 重观察修复",
        ORACLE_TOOL_FAILURE,
        _tf_input("render_layer_missing", hf_class="renderer_failure"),
        expected_reason="renderer_failure", expected_disposition="repairable",
        allowed_actions=("reobserve",),
        forbidden_actions=("retry", "ask_user_data")))
    add(RecoveryCase(
        "rc/stale-mapspec-generation", "stale_mapspec_generation",
        "旧 MapSpec generation 观察 → 重观察",
        ORACLE_TOOL_FAILURE,
        _tf_input("render_revision_stale", hf_class="renderer_failure"),
        expected_reason="renderer_failure", expected_disposition="repairable",
        allowed_actions=("reobserve",)))
    add(RecoveryCase(
        "rc/output-expired", "output_expired", "产物过期 → 重算",
        ORACLE_TOOL_FAILURE,
        _tf_input("output expired for ref", hf_class="stale_ref"),
        expected_reason="output_expired", expected_disposition="repairable",
        allowed_actions=("replan",),
        forbidden_actions=("retry", "ask_user_data")))

    # ── 权限 / 副作用面 ──────────────────────────────────────────────
    add(RecoveryCase(
        "rc/permission-denied", "permission_denied",
        "403 → 需 owner 批准，绝不盲目重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("403 permission denied"),
        expected_reason="permission_denied",
        expected_disposition="needs_user_approval",
        allowed_actions=("owner_confirmation",),
        forbidden_actions=("retry", "retry_with_backoff")))
    add(RecoveryCase(
        "rc/tier3-destructive", "destructive_refusal",
        "tier-3 破坏性工具失败 → 永不自动重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("tool failed", tool="delete_dataset",
                  side_effect="destructive", tier=3),
        expected_disposition="repairable", expect_auto_retry=False,
        forbidden_actions=("retry", "retry_with_backoff")))
    add(RecoveryCase(
        "rc/paid-external", "paid_external",
        "付费外部调用失败 → 不自动重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("provider timeout", hf_class="timeout", tool="geocode_batch",
                  side_effect="external_side_effect", tier=2),
        expected_disposition="retryable", expect_auto_retry=False,
        forbidden_actions=("retry",)))
    add(RecoveryCase(
        "rc/unclassified-conservative", "unclassified_tool",
        "未分类工具 → 保守不自动重试",
        ORACLE_TOOL_FAILURE,
        _tf_input("weird failure", side_effect="unclassified"),
        expected_disposition="repairable", expect_auto_retry=False,
        forbidden_actions=("retry",)))
    add(RecoveryCase(
        "rc/provider-unavailable", "provider_unavailable",
        "provider 断路 → 降级换工具",
        ORACLE_TOOL_FAILURE,
        _tf_input("", code="PROVIDER_UNAVAILABLE", tool="x"),
        expected_reason="tool_execution_failed",
        expected_disposition="repairable",
        forbidden_actions=("retry",)))

    # ── 跨会话 ref ───────────────────────────────────────────────────
    add(RecoveryCase(
        "rc/cross-session-tool", "wrong_session_ref",
        "工具参数引用他会话 ref → unsafe",
        ORACLE_TOOL_FAILURE,
        _tf_input("ref not found: ref:x", code="NOT_FOUND",
                  ref_origin="sess-OTHER"),
        expected_reason="cross_session_ref",
        expected_disposition="unsafe_to_retry",
        allowed_actions=("abort_with_disclosure",),
        forbidden_actions=("retry", "requery_profile")))
    add(RecoveryCase(
        "rc/cross-session-continuation", "wrong_session_ref",
        "多轮重试指令遇到跨会话 ref → fail-closed",
        ORACLE_CONTINUATION,
        _cont_input(user_ref_hints=["ref:foreign"],
                    ref_owner_sessions={"ref:foreign": "sess-OTHER"}),
        expect_verdict="fail_closed"))

    # ── goal 对账（显示成功但目标未达成等） ──────────────────────────
    add(RecoveryCase(
        "rc/display-ok-goal-unmet", "display_success_goal_unmet",
        "渲染 OK 但 goal 缺观察证据 → 矛盾披露",
        ORACLE_GOAL_VERIFICATION,
        _gv_input(observation_health=None),
        expect_overall="not_evaluated", expect_contradiction=True))
    add(RecoveryCase(
        "rc/export-unfulfilled", "display_success_goal_unmet",
        "导出需求 partial → fail（不能宣称完成）",
        ORACLE_GOAL_VERIFICATION,
        _gv_input(goal_report={
            "verdict": "partial",
            "requirements": [
                {"id": "r-map", "kind": "map", "verdict": "fulfilled"},
                {"id": "r-exp", "kind": "export", "verdict": "partial"}],
            "missing_summary": ["export:pdf"]}),
        expect_overall="fail", expect_contradiction=True))
    add(RecoveryCase(
        "rc/science-not-evaluated", "science_unverified",
        "科学需求未评估 → 不 pass",
        ORACLE_GOAL_VERIFICATION,
        _gv_input(goal_report={
            "verdict": "partial",
            "requirements": [
                {"id": "r-map", "kind": "map", "verdict": "fulfilled"},
                {"id": "r-sci", "kind": "analysis",
                 "verdict": "not_evaluated"}],
            "missing_summary": ["analysis:coverage"]}),
        expect_overall="not_evaluated"))
    add(RecoveryCase(
        "rc/user-hidden-layer", "user_wins",
        "用户显式隐藏层 → warning + 不判 fail",
        ORACLE_GOAL_VERIFICATION,
        _gv_input(
            user_hidden_layers=["l1"],
            observation_health={
                "layers": {"l1": "unknown"},
                "aggregate": "unknown", "health": "degraded",
                "observed_revision": 2}),
        expect_overall="warning", expect_contradiction=False))
    add(RecoveryCase(
        "rc/all-green", "recovery_regression",
        "自恢复后全绿 → pass 零矛盾（回归验证）",
        ORACLE_GOAL_VERIFICATION, _gv_input(),
        expect_overall="pass", expect_contradiction=False))
    add(RecoveryCase(
        "rc/multi-subgraph-retry", "multi_subgraph_failure",
        "多能力行：只重试失败行、保持已验证",
        ORACLE_CONTINUATION,
        _cont_input(progress_rows=[
            {"capability": "ingest", "status": "complete",
             "bound_ref": "ref:d1"},
            {"capability": "step_a", "status": "failed", "bound_ref": None},
            {"capability": "step_b", "status": "unavailable",
             "bound_ref": None},
            {"capability": "step_c", "status": "pending",
             "bound_ref": None}]),
        expect_verdict="retry_failed_subgraph"))
    add(RecoveryCase(
        "rc/post-recovery-fresh-budget", "recovery_regression",
        "成功后账本清零 → 同工具再失败仍有预算（回归验证）",
        ORACLE_TOOL_FAILURE,
        _tf_input("connection reset", hf_class="timeout", attempts=0,
                  max_attempts=2, retry_allowed=True),
        expected_reason="transient_timeout",
        expected_disposition="retryable",
        allowed_actions=("retry_with_backoff",),
        forbidden_actions=("abort_with_disclosure",)))

    return cases


RECOVERY_CATEGORIES = (
    "http_429", "network_jitter", "user_cancel", "retry_idempotency",
    "crs_unknown", "input_quality", "layer_missing", "wrong_session_ref",
    "permission_denied", "stale_mapspec_generation",
    "display_success_goal_unmet", "multi_subgraph_failure", "no_progress",
    "recovery_regression", "destructive_refusal", "paid_external",
    "unclassified_tool", "provider_unavailable", "output_expired",
    "science_unverified", "user_wins",
)


def category_coverage(cases: List[RecoveryCase]) -> Dict[str, int]:
    cov: Dict[str, int] = {}
    for c in cases:
        cov[c.category] = cov.get(c.category, 0) + 1
    return cov


def evaluate_recovery_case(case: RecoveryCase) -> RecoveryCaseVerdict:
    """驱动生产纯函数的 oracle（与 TDS/终验同一入口，无影子实现）。"""
    failures: List[str] = []
    state = case.input_state
    try:
        if case.oracle == ORACLE_TOOL_FAILURE:
            from app.services.gis_harness.recovery_policy.wiring import (
                build_recovery_payload,
            )

            payload = build_recovery_payload(
                status=state.get("status"), code=state.get("code"),
                error_type=state.get("error_type"),
                message=state.get("message"),
                tool_name=str(state.get("tool") or ""),
                harness_failure=state.get("harness_failure"),
                session_id=str(state.get("session_id") or ""),
                descriptor=state.get("risk") or None,
                ref_origin_session=state.get("ref_origin_session"),
                idempotency_key_present=bool(
                    state.get("idempotency_key_present")),
            )
            diag = payload.get("diagnostic") or {}
            dec = payload.get("decision") or {}
            if case.expected_reason and diag.get("reason") != \
                    case.expected_reason:
                failures.append(
                    f"reason {diag.get('reason')!r} != {case.expected_reason!r}")
            if case.expected_disposition and diag.get("disposition") != \
                    case.expected_disposition:
                failures.append(
                    f"disposition {diag.get('disposition')!r} != "
                    f"{case.expected_disposition!r}")
            allowed = set(dec.get("allowed_actions") or [])
            for a in case.allowed_actions:
                if a not in allowed:
                    failures.append(f"allowed action missing: {a}")
            for a in case.forbidden_actions:
                if a in allowed:
                    failures.append(f"forbidden action allowed: {a}")
            if case.expect_auto_retry is not None and \
                    bool(dec.get("auto_retry")) != case.expect_auto_retry:
                failures.append(
                    f"auto_retry {dec.get('auto_retry')} != "
                    f"{case.expect_auto_retry}")
        elif case.oracle == ORACLE_GOAL_VERIFICATION:
            from app.lib.harness.goal_verification import (
                reconcile_goal_verification,
            )

            rep = reconcile_goal_verification(
                goal_report=state.get("goal_report"),
                observation_health=state.get("observation_health"),
                chapter=state.get("chapter"),
                task_complete=state.get("task_complete"),
                quality_review_present=state.get("quality_review_present"),
                user_hidden_layers=state.get("user_hidden_layers"))
            if rep is None:
                failures.append("no report")
            else:
                d = rep.to_bounded_dict()
                if case.expect_overall and d.get("overall") != \
                        case.expect_overall:
                    failures.append(
                        f"overall {d.get('overall')!r} != "
                        f"{case.expect_overall!r}")
                if bool(d.get("contradictions")) != case.expect_contradiction:
                    failures.append(
                        f"contradiction {bool(d.get('contradictions'))} != "
                        f"{case.expect_contradiction}")
        elif case.oracle == ORACLE_CONTINUATION:
            from app.services.gis_harness.recovery_policy.continuation import (
                resolve_retry_directive,
            )

            d = resolve_retry_directive(
                progress_rows=state.get("progress_rows"),
                last_recovery=state.get("last_recovery"),
                chapter=state.get("chapter"),
                user_ref_hints=state.get("user_ref_hints"),
                ref_owner_sessions=state.get("ref_owner_sessions"),
                current_session_id=str(
                    state.get("current_session_id") or ""))
            if case.expect_verdict and d.verdict != case.expect_verdict:
                failures.append(
                    f"verdict {d.verdict!r} != {case.expect_verdict!r}")
        else:
            failures.append(f"unknown oracle {case.oracle!r}")
    except Exception as e:  # noqa: BLE001 — oracle 崩溃即案例失败
        failures.append(f"exception: {e}")
    return RecoveryCaseVerdict(
        case_id=case.case_id, passed=not failures,
        failures=tuple(failures[:6]))


__all__ = [
    "ORACLE_TOOL_FAILURE", "ORACLE_GOAL_VERIFICATION", "ORACLE_CONTINUATION",
    "RecoveryCase", "RecoveryCaseVerdict", "RECOVERY_CATEGORIES",
    "build_recovery_corpus", "category_coverage", "evaluate_recovery_case",
]
