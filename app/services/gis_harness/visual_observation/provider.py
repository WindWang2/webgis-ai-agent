"""生产视觉观察 provider（F15/ADR-0214 决策二）—— seam 的首个仓库内 callable。

部署形态（未配置 = 零行为变化，m1 语义逐字节保留）::

    GIS_VISUAL_EVALUATOR=app.services.gis_harness.visual_observation.provider:evaluate
    GIS_VISUAL_PROVIDER_MODE=rules_only|vlm|hybrid      # 默认 rules_only
    GIS_VISUAL_PROVIDER_TIMEOUT_S=20                    # 上限 60

- ``evaluate(snapshot)`` 是 seam 契约（sync、返回 findings 列表）；
  ``evaluate_observation(input)`` 是全保真内部入口（含 fail-closed
  reason 面），测试/诊断直接消费；
- 三模式见 ADR-0214：rules_only（像素判据、离线）/ vlm（ADR-0185 引擎，
  截图字节经 ref 解析）/ hybrid（融合）；
- async 引擎在 sync seam 下经一次性 worker thread + ``asyncio.run`` 桥接
  （不污染调用方事件循环），墙钟预算到点即放弃（返回 not_evaluated
  ``provider_timeout``——绝不阻断 deterministic verifier）；
- 全部输出再经 seam ``_sanitize_finding`` 白名单二次消毒——provider 端
  任何越权（mutation 意图 / 非 visual domain）在结构上不可进入披露面。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.completion.unified_findings import UnifiedFinding
from app.services.gis_harness.visual_observation.contracts import (
    VisualObservationInput,
    VisualObservationResult,
)
from app.services.gis_harness.visual_observation.fusion import (
    fuse_visual_with_deterministic,
)
from app.services.gis_harness.visual_observation.rules import (
    evaluate_pixel_rules,
)
from app.services.gis_harness.visual_observation.store import (
    resolve_visual_screenshot,
)
from app.services.gis_harness.visual_observation.taxonomy import (
    finding_code,
    normalize_to_taxonomy,
    taxonomy_counts_of,
)

logger = logging.getLogger(__name__)

MODE_RULES = "rules_only"
MODE_VLM = "vlm"
MODE_HYBRID = "hybrid"
_PROVIDER_MODES = (MODE_RULES, MODE_VLM, MODE_HYBRID)

DEFAULT_TIMEOUT_S = 20.0
_MAX_TIMEOUT_S = 60.0
_MAX_VLM_CRITIQUES = 12
_SOURCE = "visual_observation_provider"


def provider_mode() -> str:
    """实时读 env（与 seam 同纪律：导入期快照会让运维改变量后必须重启）。"""
    mode = os.getenv("GIS_VISUAL_PROVIDER_MODE", MODE_RULES).strip()
    return mode if mode in _PROVIDER_MODES else MODE_RULES


def provider_timeout_s() -> float:
    raw = os.getenv("GIS_VISUAL_PROVIDER_TIMEOUT_S", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return min(max(value, 1.0), _MAX_TIMEOUT_S)


# ── seam 契约（sync）──────────────────────────────────────────────────────

def evaluate(snapshot: Any) -> List[UnifiedFinding]:
    """``GIS_VISUAL_EVALUATOR`` 指向的生产 callable（seam 契约）。

    任何失败路径都返回空列表（= 「无视觉发现」，绝非「视觉合格」）；
    deterministic verifier 永不被本函数阻断。
    """
    result = evaluate_observation(VisualObservationInput.from_snapshot(snapshot))
    return list(result.findings)


def evaluate_observation(
    obs: Optional[VisualObservationInput],
) -> VisualObservationResult:
    """全保真评估入口（含 fail-closed reason；同步、有墙钟预算）。"""
    import time

    start = time.monotonic()
    if obs is None or not obs.trigger_known:
        return VisualObservationResult.not_evaluated(
            "invalid_input", provider=provider_mode())
    mode = provider_mode()

    if mode == MODE_VLM:
        result = _eval_vlm(obs)
    elif mode == MODE_HYBRID:
        result = _eval_hybrid(obs)
    else:
        result = _eval_rules(obs)
    if result.duration_ms == 0:
        result = replace_duration(
            result, int((time.monotonic() - start) * 1000))
    return result


def replace_duration(
    result: VisualObservationResult, duration_ms: int
) -> VisualObservationResult:
    return VisualObservationResult(
        status=result.status, reason=result.reason, findings=result.findings,
        provider=result.provider, taxonomy_counts=result.taxonomy_counts,
        screenshot_sha256=result.screenshot_sha256,
        duration_ms=int(duration_ms),
    )


# ── 模式实现 ───────────────────────────────────────────────────────────────

def _eval_rules(obs: VisualObservationInput) -> VisualObservationResult:
    """像素判据模式（离线；无截图 → not_evaluated 诚实缺席）。"""
    entry = obs.screenshot
    if entry is None:
        return VisualObservationResult.not_evaluated(
            "no_screenshot", provider=MODE_RULES)
    data = resolve_visual_screenshot(entry)
    if data is None:
        return VisualObservationResult.not_evaluated(
            "screenshot_unresolvable", provider=MODE_RULES,
            screenshot_sha256=entry.sha256)
    findings = fuse_visual_with_deterministic(
        evaluate_pixel_rules(data), _deterministic_proxies(obs)).kept
    categories = [
        _taxonomy_of(vf) for vf in findings
    ]
    return VisualObservationResult(
        status="evaluated",
        findings=tuple(findings),
        provider=MODE_RULES,
        taxonomy_counts=taxonomy_counts_of(categories),
        screenshot_sha256=entry.sha256,
    )


def _eval_vlm(obs: VisualObservationInput) -> VisualObservationResult:
    """VLM 模式（ADR-0185 引擎；ref-only 输入，字节只在本函数内存里）。"""
    entry = obs.screenshot
    if entry is None:
        return VisualObservationResult.not_evaluated(
            "no_screenshot", provider=MODE_VLM)
    data = resolve_visual_screenshot(entry)
    if data is None:
        return VisualObservationResult.not_evaluated(
            "screenshot_unresolvable", provider=MODE_VLM,
            screenshot_sha256=entry.sha256)
    report = _run_engine_with_timeout(obs, data)
    if report is None:
        return VisualObservationResult.not_evaluated(
            "provider_timeout", provider=MODE_VLM,
            screenshot_sha256=entry.sha256)
    if not getattr(report, "evaluated", False):
        reason = str(getattr(report, "reason", "") or "not_evaluated")
        return VisualObservationResult.not_evaluated(
            reason, provider=MODE_VLM, screenshot_sha256=entry.sha256)
    findings = _critiques_to_findings(report)
    fused = fuse_visual_with_deterministic(
        findings, _deterministic_proxies(obs)).kept
    categories = [_taxonomy_of(vf) for vf in fused]
    return VisualObservationResult(
        status="evaluated",
        findings=tuple(fused),
        provider=MODE_VLM,
        taxonomy_counts=taxonomy_counts_of(categories),
        screenshot_sha256=entry.sha256,
    )


def _eval_hybrid(obs: VisualObservationInput) -> VisualObservationResult:
    """rules ∪ vlm（同 entity+taxonomy 合并；全失败 → not_evaluated）。"""
    entry = obs.screenshot
    if entry is None:
        return VisualObservationResult.not_evaluated(
            "no_screenshot", provider=MODE_HYBRID)
    data = resolve_visual_screenshot(entry)
    if data is None:
        return VisualObservationResult.not_evaluated(
            "screenshot_unresolvable", provider=MODE_HYBRID,
            screenshot_sha256=entry.sha256)
    rule_findings = evaluate_pixel_rules(data)
    report = _run_engine_with_timeout(obs, data)
    vlm_findings: List[UnifiedFinding] = []
    evaluated = False
    if report is not None and getattr(report, "evaluated", False):
        evaluated = True
        vlm_findings = _critiques_to_findings(report)
    merged = _merge_by_entity_category(rule_findings, vlm_findings)
    fused = fuse_visual_with_deterministic(
        merged, _deterministic_proxies(obs)).kept
    if not fused and not evaluated and not rule_findings:
        # vlm not_evaluated 且 rules 无发现：诚实缺席（有截图但画面干净
        # 时 rules 已 evaluated——只有引擎缺席才落这里）。
        reason = str(getattr(report, "reason", "") or "not_evaluated")
        return VisualObservationResult.not_evaluated(
            reason, provider=MODE_HYBRID, screenshot_sha256=entry.sha256)
    categories = [_taxonomy_of(vf) for vf in fused]
    return VisualObservationResult(
        status="evaluated",
        findings=tuple(fused),
        provider=MODE_HYBRID,
        taxonomy_counts=taxonomy_counts_of(categories),
        screenshot_sha256=entry.sha256,
    )


def _merge_by_entity_category(
    rules: List[UnifiedFinding], vlm: List[UnifiedFinding],
) -> List[UnifiedFinding]:
    """同 (entity, taxonomy) 合并：规则条目优先，VLM 证据并入。"""
    from app.services.gis_harness.visual_observation.fusion import (
        visual_taxonomy_of,
    )

    out: List[UnifiedFinding] = list(rules)
    seen = {(str(f.affected_entity or "map"), visual_taxonomy_of(f))
            for f in out}
    for vf in vlm[:_MAX_VLM_CRITIQUES]:
        key = (str(vf.affected_entity or "map"), visual_taxonomy_of(vf))
        if key in seen:
            continue
        seen.add(key)
        out.append(vf)
    return out[:_MAX_VLM_CRITIQUES]


# ── VLM critique → finding（taxonomy 归一；消毒由 seam 二次执行）──────────

def _critiques_to_findings(report: Any) -> List[UnifiedFinding]:
    out: List[UnifiedFinding] = []
    for critique in tuple(getattr(report, "critiques", ()) or ())[:_MAX_VLM_CRITIQUES]:
        raw_dimension = getattr(critique, "dimension", "")
        # v2 契约是 enum；先取 .value 再 str（顺序反了会得到
        # "VisualDimension.X" —— 测试锁定）。
        dimension = str(getattr(raw_dimension, "value", raw_dimension))
        defect_type = str(getattr(critique, "defect_type", "") or "")
        evidence = str(getattr(critique, "evidence", "") or "")
        category = normalize_to_taxonomy(
            dimension, defect_type=defect_type, evidence=evidence)
        if not category:
            continue
        code = finding_code(category)
        if not code:
            continue
        bbox = getattr(critique, "bbox", None)
        entity = _entity_from_bbox_or_map(bbox)
        out.append(UnifiedFinding(
            domain="visual",
            code=code,
            severity=str(getattr(critique, "severity", "") or "warning"),
            source=_SOURCE,
            scope="map",
            affected_entity=entity,
            evidence=str(getattr(critique, "suggestion", "") or "")[:200],
            repair_class="",
            retryable=False,
            blocks_completion=False,
            degradation_only=True,
        ))
    return out


def _entity_from_bbox_or_map(bbox: Any) -> str:
    """bbox 归一为有界 entity 摘要（``map`` 为诚实缺省——不猜组件）。"""
    if bbox is None:
        return "map"
    try:
        ymin, xmin, ymax, xmax = bbox.to_list()
    except Exception:  # noqa: BLE001 — 定位是增值信息，不是门禁
        return "map"
    return f"bbox[{int(ymin)},{int(xmin)},{int(ymax)},{int(xmax)}]"


def _taxonomy_of(finding: Any) -> str:
    from app.services.gis_harness.visual_observation.fusion import (
        visual_taxonomy_of,
    )

    return visual_taxonomy_of(finding)


def _deterministic_proxies(obs: VisualObservationInput) -> Tuple[Any, ...]:
    """snapshot 里的确定性 findings → 融合判定的鸭子对象（code/target）。"""
    from types import SimpleNamespace

    return tuple(
        SimpleNamespace(code=str(f.get("code") or ""), target=str(f.get("target") or ""))
        for f in obs.deterministic_findings
    )


def _run_engine_with_timeout(
    obs: VisualObservationInput, image: bytes
) -> Optional[Any]:
    """一次性 worker thread 里的 async 引擎调用（墙钟预算；不碰调用方 loop）。

    返回 None = 超时/线程失败（fail-closed ``provider_timeout`` 由调用方
    落 not_evaluated）。线程内再无派生 loop —— httpx 自身超时兜底线程寿命。
    """
    outcome: Dict[str, Any] = {"report": None}

    def _worker() -> None:
        async def _run() -> None:
            from app.lib.harness.visual_judge.critic_engine import (
                build_critic_engine,
            )

            engine = build_critic_engine()
            outcome["report"] = await engine.evaluate(
                session_id=obs.session_id,
                mapspec_fingerprint=obs.mapspec_fingerprint,
                image=image,
                deterministic_summary={
                    "trigger": obs.trigger,
                    "mapspec_revision": obs.mapspec_revision,
                    "deterministic_findings": list(obs.deterministic_findings),
                },
                mode="record_only",
            )

        try:
            import asyncio

            asyncio.run(_run())
        except Exception:  # noqa: BLE001 — 线程内一切失败 = 无报告
            logger.warning("[VisualProvider] engine thread failed",
                           exc_info=True)

    thread = threading.Thread(target=_worker, daemon=True,
                              name="visual-observation-vlm")
    thread.start()
    thread.join(provider_timeout_s())
    if thread.is_alive():
        logger.warning("[VisualProvider] evaluation timed out after %.1fs",
                       provider_timeout_s())
        return None
    return outcome["report"]


__all__ = [
    "MODE_RULES",
    "MODE_VLM",
    "MODE_HYBRID",
    "evaluate",
    "evaluate_observation",
    "provider_mode",
    "provider_timeout_s",
]
