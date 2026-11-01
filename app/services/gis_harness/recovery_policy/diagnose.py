"""诊断构建器（GOAL 05 / M1）—— 原始信号 → GISRecoveryDiagnostic。

信号来源五族：
1. 工具失败（TDS 错误结果 + 既有 harness_failure payload）；
2. capability 拒绝（CAPABILITY_INELIGIBLE / PROVIDER_UNAVAILABLE）；
3. 数据资格（data_qualification 五态）；
4. no-progress（Pi/legacy streak）；
5. 任务级差距（render 校验 findings / goal_satisfaction 报告）。

纪律：纯函数、绝不抛出、绝不猜归因（缺证据 → unknown_failure /
按保守处置）；检测优先级固定保证确定性。跨会话 ref 判定不在此层
猜 —— 调用方提供 ``ref_origin_session``（由既有 session store 解析），
本层只做比较。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from app.services.gis_harness.recovery_policy.contracts import (
    DiagnosticAttribution,
    GISRecoveryDiagnostic,
    GISRecoveryReason,
    MAX_HINTS,
    _VALID_HINTS,
)

# ── 消息标记词表（lowercase 子串匹配；顺序即优先级） ────────────────────

_RATE_LIMIT_MARKERS = (
    "429", "rate limit", "ratelimit", "too many requests", "限流", "请求过于频繁",
    "throttl",
)
_SERVER_ERROR_MARKERS = (
    "500", "502", "503", "504", "bad gateway", "service unavailable",
    "internal server error", "server error", "上游错误", "服务不可用",
)
_PERMISSION_MARKERS = (
    "403", "401", "forbidden", "unauthorized", "permission denied",
    "permission", "access denied", "权限", "授权失败", "未授权", "凭证无效",
)
_CRS_AMBIGUOUS_MARKERS = (
    "ambiguous", "cannot determine", "unknown crs", "unknown coordinate",
    "无法确定", "歧义", "未知坐标系",
)
_OUTPUT_EXPIRED_MARKERS = (
    "output expired", "result expired", "产物已过期", "输出已过期",
)
_CROSS_SESSION_MARKERS = (
    "cross-session", "belongs to another session", "另一会话", "跨会话",
)


def _has_any(markers: Sequence[str], text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in markers)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _text_of(*parts: Any) -> str:
    """把散落的信号源折叠成单一小写检测文本（确定性拼接序）。"""
    return " ".join(_norm(p) for p in parts if _norm(p)).lower()


def _build(
    reason: GISRecoveryReason,
    *,
    detail: str,
    attribution: Optional[DiagnosticAttribution],
    harness_failure: Any = None,
    evidence_refs: Optional[Sequence[str]] = None,
    override_hints: Optional[Sequence[str]] = None,
) -> GISRecoveryDiagnostic:
    d = GISRecoveryDiagnostic.from_harness_failure_payload(
        harness_failure if isinstance(harness_failure, dict) else None,
        reason=reason,
        attribution=attribution,
        detail=detail,
        evidence_refs=evidence_refs,
    )
    if override_hints:
        d.hints = [h for h in override_hints
                   if h in _VALID_HINTS][:MAX_HINTS]
    return d


# ── 1. 工具失败诊断 ──────────────────────────────────────────────────────

def diagnose_tool_failure(
    *,
    status: Optional[str] = None,
    code: Optional[str] = None,
    error_type: Optional[str] = None,
    message: Optional[str] = None,
    tool_name: str = "",
    harness_failure: Any = None,
    session_id: str = "",
    attribution: Optional[DiagnosticAttribution] = None,
    ref_origin_session: Optional[str] = None,
    evidence_refs: Optional[Sequence[str]] = None,
) -> GISRecoveryDiagnostic:
    """TDS 统一错误分支的单点细化：既有粗类 + 消息/码信号 → reason。

    检测优先级（固定，先匹配先赢）：
    cancelled（粗类）→ 跨会话 ref → 权限 → 429 → 5xx →
    budget（粗类，未被上面细化的真预算耗尽）→ CRS 歧义 → CRS →
    输出过期 → 粗类默认映射。

    注：HTTP 429 会被既有 planning 委托折叠成粗类 ``budget_exhausted``
    （resource_limit 族）—— 本层在尊重该粗类之前先做 429/5xx/权限
    细化，外部限流是可退避重试、不是内部预算耗尽（terminal）。
    """
    attr = attribution or DiagnosticAttribution(
        session_id=session_id[:64], tool_name=tool_name[:64])
    hf = harness_failure if isinstance(harness_failure, dict) else {}
    coarse = _norm(hf.get("class")) or "unknown"
    text = _text_of(status, code, error_type, message)
    detail = _norm(message) or (coarse + " failure") or "tool failure"

    def build(reason: GISRecoveryReason, det: str = "") -> GISRecoveryDiagnostic:
        return _build(reason, detail=det or detail, attribution=attr,
                      harness_failure=hf, evidence_refs=evidence_refs)

    # 粗类已裁决的取消（真取消不可细化覆盖）
    if coarse == "cancelled":
        return build(GISRecoveryReason.CANCELLED)

    # 跨会话 ref：调用方已解析 ref 归属（本层不猜）→ fail-closed
    if ref_origin_session and ref_origin_session != (session_id or ""):
        return build(GISRecoveryReason.CROSS_SESSION_REF)
    if _has_any(_CROSS_SESSION_MARKERS, text):
        return build(GISRecoveryReason.CROSS_SESSION_REF)

    # 权限族：绝不盲目重试（needs_user_approval）
    if _has_any(_PERMISSION_MARKERS, text):
        return build(GISRecoveryReason.PERMISSION_DENIED)

    # 限流 / 上游 5xx：瞬态但必须退避（retryable + backoff hint）——
    # 先于粗类 budget_exhausted（429 会被 planning 委托误折叠）。
    if _has_any(_RATE_LIMIT_MARKERS, text):
        return build(GISRecoveryReason.RATE_LIMITED)
    if _has_any(_SERVER_ERROR_MARKERS, text):
        return build(GISRecoveryReason.UPSTREAM_SERVER_ERROR)

    # 真预算耗尽（未被上面细化的粗类裁决）
    if coarse == "budget_exhausted":
        return build(GISRecoveryReason.BUDGET_EXHAUSTED)

    # CRS 族：歧义 → 问用户；否则可替换投影路径
    if coarse == "crs_error" or _has_any(
            ("crs", "pyproj", "epsg", "坐标系", "投影"), text):
        if _has_any(_CRS_AMBIGUOUS_MARKERS, text):
            return build(GISRecoveryReason.CRS_AMBIGUOUS)
        return build(GISRecoveryReason.CRS_ERROR)

    # 输出/产物过期（运行产物 TTL 到期）
    if _has_any(_OUTPUT_EXPIRED_MARKERS, text):
        return build(GISRecoveryReason.OUTPUT_EXPIRED)

    # 粗类默认映射（不发明新语义）
    default_by_class = {
        "tool_error": GISRecoveryReason.TOOL_EXECUTION_FAILED,
        "data_error": GISRecoveryReason.DATA_INVALID,
        "crs_error": GISRecoveryReason.CRS_ERROR,
        "empty_result": GISRecoveryReason.EMPTY_RESULT,
        "stale_ref": GISRecoveryReason.STALE_REF,
        "renderer_failure": GISRecoveryReason.RENDERER_FAILURE,
        "timeout": GISRecoveryReason.TRANSIENT_TIMEOUT,
        "partial_completion": GISRecoveryReason.PARTIAL_COMPLETION,
        "unknown": GISRecoveryReason.UNKNOWN_FAILURE,
    }
    reason = default_by_class.get(coarse, GISRecoveryReason.UNKNOWN_FAILURE)
    # NOT_FOUND + ref 形态 → 会话内 stale_ref（可安全再获取）
    if (code or "").upper() == "NOT_FOUND" and "ref:" in text:
        reason = GISRecoveryReason.STALE_REF
    return build(reason)


# ── 2. capability 拒绝诊断 ───────────────────────────────────────────────

def diagnose_capability_refusal(
    *, code: str = "",
    attribution: Optional[DiagnosticAttribution] = None,
    detail: str = "",
) -> GISRecoveryDiagnostic:
    c = (code or "").upper()
    reason = (
        GISRecoveryReason.PROVIDER_UNAVAILABLE
        if c == "PROVIDER_UNAVAILABLE"
        else GISRecoveryReason.CAPABILITY_INELIGIBLE
    )
    return _build(
        reason, detail=detail or f"capability refusal: {c or 'unknown'}",
        attribution=attribution)


# ── 3. 数据资格诊断 ──────────────────────────────────────────────────────

def diagnose_eligibility(
    *, state: str, reason_code: str = "",
    attribution: Optional[DiagnosticAttribution] = None,
) -> Optional[GISRecoveryDiagnostic]:
    """data_qualification 五态 → 诊断；eligible/unknown 返回 None。

    ``reason_code`` 原样进 detail（如 ``missing_field:score``）——
    场景 A（成都小学分析缺字段）的 typed 缺口入口。
    """
    s = (state or "").strip().lower()
    if s in ("eligible", "unknown", ""):
        return None
    reason = (
        GISRecoveryReason.ELIGIBILITY_BLOCKED
        if s == "blocked"
        else GISRecoveryReason.ELIGIBILITY_DEGRADED
    )
    return _build(
        reason,
        detail=(f"data qualification {s}: {reason_code or 'no reason code'}"),
        attribution=attribution)


# ── 4. no-progress 诊断 ──────────────────────────────────────────────────

def diagnose_no_progress(
    *, streak: int, hints: Sequence[str] = (),
    attribution: Optional[DiagnosticAttribution] = None,
) -> GISRecoveryDiagnostic:
    return _build(
        GISRecoveryReason.NO_PROGRESS,
        detail=f"no progress streak={int(streak)}",
        attribution=attribution,
        evidence_refs=[f"no_progress_hint:{h}" for h in list(hints)[:4]],
    )


# ── 5a. 渲染/MapSpec 收敛差距诊断 ───────────────────────────────────────

def diagnose_render_gap(
    *, render_status: str, finding_codes: Sequence[str],
    attribution: Optional[DiagnosticAttribution] = None,
) -> Optional[GISRecoveryDiagnostic]:
    """render_observation 校验结果 → 收敛差距诊断。

    - ``RENDER_VERIFIED`` / ``not_applicable`` → None（无差距）；
    - ``unknown`` / ``stale`` → 未收敛（reobserve）；
    - ``RENDER_ISSUES``：源错误族 → renderer_failure，其余 → 未收敛。
    """
    status = (render_status or "").strip().upper()
    if status in ("RENDER_VERIFIED", "NOT_APPLICABLE", ""):
        return None
    codes = [str(c) for c in finding_codes or []]
    detail = f"render {status}: {','.join(codes[:6]) or 'no findings'}"
    if status == "RENDER_ISSUES" and any(
            "source" in c or "error" in c for c in codes):
        return _build(GISRecoveryReason.RENDERER_FAILURE, detail=detail,
                      attribution=attribution,
                      evidence_refs=[f"finding:{c}" for c in codes[:8]])
    return _build(GISRecoveryReason.MAPSPEC_NOT_CONVERGED, detail=detail,
                  attribution=attribution,
                  evidence_refs=[f"finding:{c}" for c in codes[:8]])


# ── 5b. goal 差距诊断 ────────────────────────────────────────────────────

def diagnose_goal_gap(
    goal_report: Optional[Dict[str, Any]],
    *,
    attribution: Optional[DiagnosticAttribution] = None,
) -> Optional[GISRecoveryDiagnostic]:
    """ADR-0183 GoalSatisfactionReport（bounded dict）→ 任务级差距诊断。

    - 无报告 / verdict==satisfied → None；
    - missing 码按前缀归因：export→GOAL_GAP_EXPORT；
      render/observation→GOAL_GAP_RENDERING；data/science/qualification→
      GOAL_GAP_SCIENCE；其余（含 not_evaluated）→ GOAL_GAP_SCIENCE
      （保守：证据不足按科学面欠账处理，不静默 pass）。
    """
    if not isinstance(goal_report, dict):
        return None
    if str(goal_report.get("verdict") or "") == "satisfied":
        return None
    missing = [str(m) for m in (goal_report.get("missing") or
                                goal_report.get("missing_summary") or [])]
    blob = ";".join(missing).lower()
    if any(m.startswith("export") or "export" in m for m in missing):
        reason = GISRecoveryReason.GOAL_GAP_EXPORT
    elif "render" in blob or "observation" in blob or "layer" in blob:
        reason = GISRecoveryReason.GOAL_GAP_RENDERING
    else:
        reason = GISRecoveryReason.GOAL_GAP_SCIENCE
    return _build(
        reason,
        detail=f"goal {goal_report.get('verdict')}: {', '.join(missing[:4]) or 'gaps unlisted'}",
        attribution=attribution,
        evidence_refs=[f"goal_missing:{m}" for m in missing[:8]],
    )


__all__ = [
    "diagnose_tool_failure",
    "diagnose_capability_refusal",
    "diagnose_eligibility",
    "diagnose_no_progress",
    "diagnose_render_gap",
    "diagnose_goal_gap",
]
