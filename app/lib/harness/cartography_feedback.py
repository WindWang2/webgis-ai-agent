"""Unified cartography feedback verdict (visual × template/codegen × GIS semantics).

Extends the existing Pi harness / verdict-injection path — does **not** invent a
second agent host. Consumes:

- visual evidence already attached by ``visual_evaluator.attach_visual_judgement``
- GIS semantics from ``CartographicReviewEvidence.desired_status`` / desired_review
- template/codegen from ``template_codegen_evaluator.evaluate_template_codegen``

The unified blob lands on ``CartographicReviewEvidence.feedback`` and is
projected into ``[CARTOGRAPHY_VERDICT]`` so the next Pi turn sees both visual
and template scores alongside the three-state token.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.lib.harness.template_codegen_evaluator import (
    TemplateCodegenReport,
    evaluate_template_codegen,
)

_STATUS_PASS = "pass"
_STATUS_FAIL = "fail"
_STATUS_NE = "not_evaluated"
_MAX_AXIS_FINDINGS = 3


def _clip(value: Any, limit: int = 160) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class FeedbackAxis:
    name: str
    status: str = _STATUS_NE
    score: Optional[float] = None
    evaluated: bool = False
    reason: str = ""
    findings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        findings = self.findings[:_MAX_AXIS_FINDINGS]
        return {
            "name": self.name,
            "status": self.status,
            "score": self.score,
            "evaluated": self.evaluated,
            "reason": _clip(self.reason, 120),
            "findings": findings,
            "findings_omitted": max(0, len(self.findings) - len(findings)),
        }


@dataclass
class UnifiedCartographyFeedback:
    visual: FeedbackAxis = field(default_factory=lambda: FeedbackAxis(name="visual"))
    template_codegen: FeedbackAxis = field(
        default_factory=lambda: FeedbackAxis(name="template_codegen")
    )
    gis_semantics: FeedbackAxis = field(
        default_factory=lambda: FeedbackAxis(name="gis_semantics")
    )
    overall_status: str = _STATUS_NE
    overall_reason: str = "not_built"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "overall_status": self.overall_status,
            "overall_reason": self.overall_reason,
            "axes": {
                "visual": self.visual.to_dict(),
                "template_codegen": self.template_codegen.to_dict(),
                "gis_semantics": self.gis_semantics.to_dict(),
            },
            # Compact scores for verdict injection (agent-facing).
            "scores": {
                "visual": self.visual.score,
                "template_codegen": self.template_codegen.score,
                "gis_semantics": self.gis_semantics.score,
            },
        }


def _axis_from_visual_evidence(cartography: Any) -> FeedbackAxis:
    """Project visual_judge evidence into the visual feedback axis."""
    visual_evidence = getattr(cartography, "visual_evidence", None)
    if visual_evidence is None and isinstance(cartography, dict):
        visual_evidence = cartography.get("visual_evidence")
    summaries = [
        item for item in (visual_evidence or [])
        if isinstance(item, dict) and item.get("source") == "visual_judge"
    ]
    summary = summaries[-1] if summaries else None
    if not isinstance(summary, dict):
        return FeedbackAxis(
            name="visual",
            status=_STATUS_NE,
            score=None,
            evaluated=False,
            reason="no_visual_judgement",
        )
    status_raw = str(summary.get("status") or "")
    if status_raw != "evaluated":
        return FeedbackAxis(
            name="visual",
            status=_STATUS_NE,
            score=None,
            evaluated=False,
            reason=str(summary.get("reason") or "visual_not_evaluated"),
        )
    error_count = int(summary.get("error_count") or 0)
    warning_count = int(summary.get("warning_count") or 0)
    findings: List[Dict[str, Any]] = []
    for critique in summary.get("critiques") or []:
        if not isinstance(critique, dict):
            continue
        if critique.get("severity") not in ("error", "warning"):
            continue
        findings.append({
            "dimension": _clip(critique.get("dimension"), 40),
            "severity": critique.get("severity"),
            "suggestion": _clip(critique.get("suggestion"), 120),
        })
        if len(findings) >= _MAX_AXIS_FINDINGS:
            break
    if error_count > 0:
        # Bounded penalty — never invent a green score when errors exist.
        score = max(0.0, round(1.0 - 0.35 * error_count - 0.1 * warning_count, 3))
        return FeedbackAxis(
            name="visual",
            status=_STATUS_FAIL,
            score=score,
            evaluated=True,
            reason="visual_error_critique",
            findings=findings,
        )
    return FeedbackAxis(
        name="visual",
        status=_STATUS_PASS,
        score=1.0,
        evaluated=True,
        reason="visual_judge_concurred",
        findings=findings,
    )


def _axis_from_gis_semantics(cartography: Any) -> FeedbackAxis:
    desired_status = getattr(cartography, "desired_status", None)
    if desired_status is None and isinstance(cartography, dict):
        desired_status = cartography.get("desired_status")
    desired_status = str(desired_status or _STATUS_NE)

    desired_review = getattr(cartography, "desired_review", None)
    if desired_review is None and isinstance(cartography, dict):
        desired_review = cartography.get("desired_review")
    findings: List[Dict[str, Any]] = []
    if isinstance(desired_review, dict):
        for check in desired_review.get("checks") or []:
            if not isinstance(check, dict):
                continue
            if check.get("status") not in (_STATUS_FAIL, _STATUS_NE):
                continue
            findings.append({
                "rule": _clip(check.get("rule") or check.get("check"), 60),
                "status": str(check.get("status")),
                "message": _clip(check.get("message"), 120),
            })
            if len(findings) >= _MAX_AXIS_FINDINGS:
                break

    if desired_status in ("pass", "passed", "passed_with_warnings"):
        return FeedbackAxis(
            name="gis_semantics",
            status=_STATUS_PASS,
            score=1.0,
            evaluated=True,
            reason="desired_semantics_passed",
            findings=findings,
        )
    if desired_status == "fail":
        return FeedbackAxis(
            name="gis_semantics",
            status=_STATUS_FAIL,
            score=0.0,
            evaluated=True,
            reason="desired_semantics_failed",
            findings=findings,
        )
    return FeedbackAxis(
        name="gis_semantics",
        status=_STATUS_NE,
        score=None,
        evaluated=False,
        reason=f"desired_status_{desired_status}",
        findings=findings,
    )


def _axis_from_template_report(report: TemplateCodegenReport) -> FeedbackAxis:
    findings = [
        {
            "check": f.check,
            "status": f.status,
            "message": _clip(f.message, 120),
        }
        for f in report.findings
        if f.status in (_STATUS_FAIL, _STATUS_NE)
    ][:_MAX_AXIS_FINDINGS]
    return FeedbackAxis(
        name="template_codegen",
        status=report.status,
        score=report.score,
        evaluated=report.evaluated,
        reason=report.reason,
        findings=findings,
    )


def _overall(axes: List[FeedbackAxis]) -> tuple[str, str]:
    statuses = [a.status for a in axes]
    if _STATUS_FAIL in statuses:
        failed = [a.name for a in axes if a.status == _STATUS_FAIL]
        return _STATUS_FAIL, "failed_axes:" + ",".join(failed)
    if all(s == _STATUS_PASS for s in statuses):
        return _STATUS_PASS, "all_axes_passed"
    if all(s == _STATUS_NE for s in statuses):
        return _STATUS_NE, "all_axes_not_evaluated"
    return _STATUS_NE, "axes_evidence_incomplete"


def build_unified_feedback(
    cartography: Any,
    mapspec: Optional[Dict[str, Any]] = None,
    *,
    composition_template_id: str = "",
    is_compiled: Optional[bool] = None,
    template_report: Optional[TemplateCodegenReport] = None,
) -> UnifiedCartographyFeedback:
    """Build the three-axis feedback blob for one cartography generation."""
    visual = _axis_from_visual_evidence(cartography)
    gis = _axis_from_gis_semantics(cartography)
    report = template_report or evaluate_template_codegen(
        mapspec,
        composition_template_id=composition_template_id,
        is_compiled=is_compiled,
    )
    template = _axis_from_template_report(report)
    overall_status, overall_reason = _overall([visual, template, gis])
    return UnifiedCartographyFeedback(
        visual=visual,
        template_codegen=template,
        gis_semantics=gis,
        overall_status=overall_status,
        overall_reason=overall_reason,
    )


def attach_unified_feedback(
    cartography: Any,
    mapspec: Optional[Dict[str, Any]] = None,
    *,
    composition_template_id: str = "",
    is_compiled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Compute feedback and attach to cartography evidence (mutates in place).

    Works with ``CartographicReviewEvidence`` (attribute) or a plain dict
    (``feedback`` key). Also appends a record-only check row so status tools
    and inject projection can surface template failures without inventing a
    second harness host.
    """
    feedback = build_unified_feedback(
        cartography,
        mapspec,
        composition_template_id=composition_template_id,
        is_compiled=is_compiled,
    )
    payload = feedback.to_dict()

    if hasattr(cartography, "feedback"):
        cartography.feedback = payload
    elif isinstance(cartography, dict):
        cartography["feedback"] = payload

    # Record-only check rows (do not rewrite L4 three-state by themselves).
    checks = getattr(cartography, "checks", None)
    if checks is None and isinstance(cartography, dict):
        checks = cartography.setdefault("checks", [])
    if isinstance(checks, list):
        template_axis = payload["axes"]["template_codegen"]
        checks.append({
            "rule": "TEMPLATE_CODEGEN_FITNESS",
            "status": template_axis["status"],
            "evidence_class": "template",
            "severity": (
                "error" if template_axis["status"] == _STATUS_FAIL
                else "info"
            ),
            "repairability": "not_repairable",
            "evidence": {
                "score": template_axis.get("score"),
                "reason": template_axis.get("reason"),
                "checks": (
                    feedback.template_codegen.findings
                    and payload["axes"]["template_codegen"]
                ),
            },
            "message": (
                f"Template/codegen axis: {template_axis['status']} "
                f"({template_axis.get('reason') or 'n/a'})."
            ),
        })
        checks.append({
            "rule": "CARTOGRAPHY_FEEDBACK_AXES",
            "status": payload["overall_status"],
            "evidence_class": "feedback",
            "severity": "info",
            "repairability": "not_repairable",
            "evidence": {
                "scores": payload["scores"],
                "overall_reason": payload["overall_reason"],
            },
            "message": (
                "Unified cartography feedback axes "
                f"(visual={payload['scores']['visual']}, "
                f"template={payload['scores']['template_codegen']}, "
                f"gis={payload['scores']['gis_semantics']})."
            ),
        })
    return payload


def extract_is_compiled_from_results(
    results_by_id: Optional[Dict[str, Dict[str, Any]]],
    source_tool_call_id: Optional[str] = None,
) -> Optional[bool]:
    """Pull lifecycle ``is_compiled`` from the mutation tool result if present."""
    if not isinstance(results_by_id, dict):
        return None
    entry: Dict[str, Any] = {}
    if source_tool_call_id and source_tool_call_id in results_by_id:
        entry = results_by_id.get(source_tool_call_id) or {}
    else:
        # Fall back to any result that carries is_compiled.
        for candidate in results_by_id.values():
            if isinstance(candidate, dict) and isinstance(candidate.get("result"), dict):
                if "is_compiled" in candidate["result"]:
                    entry = candidate
                    break
    result = entry.get("result") if isinstance(entry, dict) else None
    if not isinstance(result, dict) or "is_compiled" not in result:
        return None
    value = result.get("is_compiled")
    if isinstance(value, bool):
        return value
    return None


def extract_composition_template_id_from_results(
    results_by_id: Optional[Dict[str, Dict[str, Any]]],
    source_tool_call_id: Optional[str] = None,
) -> str:
    """Pull composition_template_id from map_product_evidence when present."""
    if not isinstance(results_by_id, dict):
        return ""
    entries: List[Dict[str, Any]] = []
    if source_tool_call_id and source_tool_call_id in results_by_id:
        entries.append(results_by_id[source_tool_call_id] or {})
    entries.extend(results_by_id.values())
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        evidence = result.get("map_product_evidence")
        if not isinstance(evidence, dict):
            continue
        # Common shapes: top-level, recipe_selection, component path.
        for key in ("composition_template_id",):
            value = evidence.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        recipe = evidence.get("recipe_selection")
        if isinstance(recipe, dict):
            value = recipe.get("composition_template_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        completeness = evidence.get("completeness")
        if isinstance(completeness, dict):
            value = completeness.get("composition_template_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


__all__ = [
    "FeedbackAxis",
    "UnifiedCartographyFeedback",
    "attach_unified_feedback",
    "build_unified_feedback",
    "extract_composition_template_id_from_results",
    "extract_is_compiled_from_results",
]
