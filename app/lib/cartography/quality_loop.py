"""Bounded cartographic desired-state review and AUTO_SAFE repair.

This module composes the existing semantic evaluator; it is not a second
validator or an agent loop. It only edits an immutable MapSpec candidate's
presentation fields and leaves runtime ACK/convergence to the harness stage.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


MAX_REPAIR_ITERATIONS = 2
_SOURCE_METADATA_KEYS = (
    "type",
    "url",
    "ref",
    "ref_id",
    "imageRef",
    "bounds",
    "imageSize",
    "profile",
    "profile_fingerprint",
)
_LAYER_DATA_KEYS = {"data", "geojson", "features", "inlineData", "source_data"}


def _without_data(value: Any) -> Any:
    """Project nested layer metadata without copying feature/data bodies."""
    if isinstance(value, dict):
        return {
            str(key): _without_data(item)
            for key, item in value.items()
            if key not in _LAYER_DATA_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_without_data(item) for item in value]
    return value


def cartographic_projection(mapspec: Dict[str, Any]) -> Dict[str, Any]:
    """Return the bounded metadata that can affect cartographic review.

    Source payloads are deliberately excluded. The profile and stable source
    descriptor are sufficient for the deterministic rules and keep review O(1)
    with respect to feature count.
    """
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    projected_sources: Dict[str, Any] = {}
    for source_id, source in sources.items():
        if not isinstance(source, dict):
            projected_sources[str(source_id)] = {"descriptor_type": type(source).__name__}
            continue
        projected_sources[str(source_id)] = {
            key: _without_data(source[key])
            for key in _SOURCE_METADATA_KEYS
            if key in source
        }
    layers = mapspec.get("layers") if isinstance(mapspec.get("layers"), list) else []
    return {
        "version": mapspec.get("version"),
        "view": _without_data(mapspec.get("view") or {}),
        "layout": _without_data(mapspec.get("layout") or {}),
        "sources": projected_sources,
        "layers": [_without_data(layer) for layer in layers if isinstance(layer, dict)],
    }


def cartographic_fingerprint(mapspec: Dict[str, Any]) -> str:
    payload = json.dumps(
        cartographic_projection(mapspec),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")
    return f"carto-sha256:{hashlib.sha256(payload).hexdigest()}"


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _failure_fingerprint(review: Dict[str, Any]) -> str:
    failures = [
        {
            "rule": check.get("rule"),
            "status": check.get("status"),
            "layer_id": check.get("layer_id"),
            "source_id": check.get("source_id"),
            "evidence": check.get("evidence") or {},
        }
        for check in review.get("checks", [])
        if check.get("status") == "fail"
    ]
    return _fingerprint(failures)


def _plan_auto_safe_repairs(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    repairs: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for check in review.get("checks", []):
        if check.get("status") != "fail" or check.get("repairability") != "auto_safe":
            continue
        fix = check.get("suggested_fix")
        if not isinstance(fix, dict) or not fix.get("operation"):
            continue
        fingerprint = _fingerprint(fix)
        if fingerprint not in seen:
            seen.add(fingerprint)
            repairs.append(copy.deepcopy(fix))
    return repairs


def _apply_repairs(
    mapspec: Dict[str, Any], repairs: List[Dict[str, Any]]
) -> Dict[str, Any]:
    candidate = copy.deepcopy(mapspec)
    layers = candidate.get("layers") if isinstance(candidate.get("layers"), list) else []
    by_id = {
        layer.get("id"): layer for layer in layers
        if isinstance(layer, dict) and layer.get("id")
    }
    for repair in repairs:
        operation = repair.get("operation")
        if operation not in (
            "normalize_opacity",
            "refresh_style_from_legend",
            "set_layer_visibility",
        ):
            continue
        layer = by_id.get(repair.get("layer_id"))
        if operation == "set_layer_visibility":
            if isinstance(layer, dict):
                layer["visible"] = bool(repair.get("visible"))
                layout = layer.get("layout")
                if isinstance(layout, dict) and layout.get("visibility") == "none":
                    layout["visibility"] = "visible"
            continue
        prop = repair.get("property")
        value = repair.get("value")
        if not isinstance(layer, dict) or not isinstance(prop, str):
            continue
        paint = layer.setdefault("paint", {})
        if isinstance(paint, dict):
            paint[prop] = value
    return candidate


RepairExecutor = Callable[[Dict[str, Any], List[Dict[str, Any]]], Dict[str, Any]]
CurrentGuard = Callable[[str], bool]


@dataclass
class CartographicLoopResult:
    mapspec: Dict[str, Any]
    status: str
    review: Dict[str, Any]
    initial_fingerprint: str
    final_fingerprint: str
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    termination_reason: str = ""
    counters: Dict[str, int] = field(default_factory=dict)

    @property
    def repair_count(self) -> int:
        return len(self.attempts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": "desired_state",
            "status": self.status,
            "review": self.review,
            "initial_fingerprint": self.initial_fingerprint,
            "final_fingerprint": self.final_fingerprint,
            "attempts": self.attempts,
            "repair_count": self.repair_count,
            "termination_reason": self.termination_reason,
            "counters": self.counters,
        }


def review_cartography(
    mapspec: Dict[str, Any],
    source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
) -> CartographicLoopResult:
    """Read-only desired-state review with explicit repairability semantics."""
    current = copy.deepcopy(mapspec)
    fingerprint = cartographic_fingerprint(current)
    review = evaluate_cartography_semantics(current, source_profiles).to_dict()
    if review["status"] == "pass":
        status = "passed"
    elif review["status"] == "warning" and review.get("passed") is True:
        status = "passed_with_warnings"
    elif review["status"] == "warning":
        status = "partial"
    elif review["status"] == "not_evaluated":
        status = "not_evaluated"
    else:
        repairable = any(
            check.get("status") == "fail"
            and check.get("repairability") in ("auto_safe", "auto_with_semantic_risk")
            for check in review.get("checks", [])
        )
        status = "failed_repairable" if repairable else "failed_unrepairable"
    return CartographicLoopResult(
        mapspec=current,
        status=status,
        review=review,
        initial_fingerprint=fingerprint,
        final_fingerprint=fingerprint,
        attempts=[],
        termination_reason="review_only",
        counters={
            "review_invocations": 1,
            "rule_invocations": len(review.get("checks", [])),
            "metadata_sources": len(mapspec.get("sources") or {}),
            "full_data_loads": 0,
            "repair_attempts": 0,
        },
    )


def review_and_repair_cartography(
    mapspec: Dict[str, Any],
    source_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    *,
    max_iterations: int = MAX_REPAIR_ITERATIONS,
    repair_executor: RepairExecutor = _apply_repairs,
    is_current: Optional[CurrentGuard] = None,
) -> CartographicLoopResult:
    """Review and repair an immutable desired MapSpec with hard termination.

    This stage does not claim frontend convergence. ``is_current`` is a
    generation guard for callers that reconcile against mutable state; a false
    result terminates as ``superseded`` before a patch is applied.
    """
    bounded_iterations = max(0, min(int(max_iterations), MAX_REPAIR_ITERATIONS))
    current = copy.deepcopy(mapspec)
    initial_fingerprint = cartographic_fingerprint(current)
    attempts: List[Dict[str, Any]] = []
    seen_failures: set[str] = set()
    seen_patches: set[str] = set()
    review_invocations = 0
    rule_invocations = 0

    while True:
        report = evaluate_cartography_semantics(current, source_profiles)
        review = report.to_dict()
        review_invocations += 1
        rule_invocations += len(review.get("checks", []))

        if review["status"] == "pass":
            status, reason = "passed", "quality_converged"
            break
        if review["status"] == "warning" and review.get("passed") is True:
            status, reason = "passed_with_warnings", "quality_converged_with_warnings"
            break
        if review["status"] == "warning":
            status, reason = "partial", "deterministic_evidence_incomplete"
            break
        if review["status"] == "not_evaluated":
            status, reason = "not_evaluated", "missing_evidence"
            break

        failure_fp = _failure_fingerprint(review)
        if failure_fp in seen_failures:
            status, reason = "repair_exhausted", "repeated_failure"
            break
        seen_failures.add(failure_fp)

        repairs = _plan_auto_safe_repairs(review)
        if not repairs:
            has_semantic_risk = any(
                check.get("status") == "fail"
                and check.get("repairability") == "auto_with_semantic_risk"
                for check in review.get("checks", [])
            )
            status = "failed_repairable" if has_semantic_risk else "failed_unrepairable"
            reason = "semantic_risk_requires_explicit_intent" if has_semantic_risk else "no_auto_safe_repair"
            break
        if len(attempts) >= bounded_iterations:
            status, reason = "repair_exhausted", "max_iterations"
            break

        patch_fp = _fingerprint(repairs)
        if patch_fp in seen_patches:
            status, reason = "repair_exhausted", "repeated_patch"
            break
        seen_patches.add(patch_fp)

        current_fingerprint = cartographic_fingerprint(current)
        if is_current is not None and not is_current(current_fingerprint):
            status, reason = "superseded", "stale_generation"
            break

        next_mapspec = repair_executor(copy.deepcopy(current), copy.deepcopy(repairs))
        if not isinstance(next_mapspec, dict):
            next_mapspec = current
        next_fingerprint = cartographic_fingerprint(next_mapspec)
        attempts.append({
            "iteration": len(attempts) + 1,
            "input_fingerprint": current_fingerprint,
            "failure_fingerprint": failure_fp,
            "patch_fingerprint": patch_fp,
            "repairs": repairs,
            "output_fingerprint": next_fingerprint,
            "state_changed": next_fingerprint != current_fingerprint,
        })
        current = copy.deepcopy(next_mapspec)

    final_fingerprint = cartographic_fingerprint(current)
    return CartographicLoopResult(
        mapspec=current,
        status=status,
        review=review,
        initial_fingerprint=initial_fingerprint,
        final_fingerprint=final_fingerprint,
        attempts=attempts,
        termination_reason=reason,
        counters={
            "review_invocations": review_invocations,
            "rule_invocations": rule_invocations,
            "metadata_sources": len((mapspec.get("sources") or {})),
            "full_data_loads": 0,
            "repair_attempts": len(attempts),
        },
    )


__all__ = [
    "CartographicLoopResult",
    "MAX_REPAIR_ITERATIONS",
    "cartographic_fingerprint",
    "cartographic_projection",
    "review_cartography",
    "review_and_repair_cartography",
]
