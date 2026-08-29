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
import math
from typing import Any, Callable, Dict, List, Optional

from app.lib.cartography.semantic_checks import (
    _paint_methods,
    _paint_output_colors,
    evaluate_cartography_semantics,
)


MAX_REPAIR_ITERATIONS = 2
_SOURCE_METADATA_KEYS = (
    "type",
    "ref",
    "ref_id",
    # Raw URL/dataPath values may contain userinfo, signed query strings, or
    # private object names. ``data_fingerprint`` carries stable source identity
    # without putting credentials into review evidence.
    "bounds",
    "imageSize",
    "profile",
    "profile_fingerprint",
    "data_fingerprint",
)
_LAYER_METADATA_KEYS = (
    "id",
    "source",
    "type",
    "visible",
    "minzoom",
    "maxzoom",
    "layout",
    "paint",
    "filter",
    "legend_spec",
    "provenance",
    "cartographic_intent",
    "cartographic_profile",
)
_MAX_METADATA_NODES = 4_096
_MAX_METADATA_DEPTH = 8
_MAX_METADATA_ITEMS = 256
_MAX_METADATA_STRING = 512


def _bounded_metadata(
    value: Any,
    *,
    depth: int = 0,
    budget: Optional[List[int]] = None,
) -> Any:
    """Copy structured evidence into a finite, credential-safe projection."""
    if budget is None:
        budget = [_MAX_METADATA_NODES]
    if budget[0] <= 0 or depth > _MAX_METADATA_DEPTH:
        return {"truncated": True}
    budget[0] -= 1
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return {"non_finite": str(value)}
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_METADATA_STRING:
            return value
        digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()
        return {"sha256": digest, "length": len(value)}
    if isinstance(value, dict):
        projected = {
            str(key): _bounded_metadata(item, depth=depth + 1, budget=budget)
            for key, item in list(value.items())[:_MAX_METADATA_ITEMS]
        }
        if len(value) > _MAX_METADATA_ITEMS:
            omitted = list(value.items())[_MAX_METADATA_ITEMS:]
            payload = json.dumps(
                omitted, ensure_ascii=False, sort_keys=True, default=str,
                separators=(",", ":"),
            ).encode("utf-8")
            projected["__omitted_metadata__"] = {
                "count": len(omitted),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        return projected
    if isinstance(value, (list, tuple)):
        projected = [
            _bounded_metadata(item, depth=depth + 1, budget=budget)
            for item in value[:_MAX_METADATA_ITEMS]
        ]
        if len(value) > _MAX_METADATA_ITEMS:
            omitted = value[_MAX_METADATA_ITEMS:]
            payload = json.dumps(
                omitted, ensure_ascii=False, sort_keys=True, default=str,
                separators=(",", ":"),
            ).encode("utf-8")
            projected.append({
                "__omitted_metadata__": len(omitted),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
        return projected
    return {"unsupported_type": type(value).__name__}


def _profile_metadata(value: Any, budget: List[int]) -> Any:
    if not isinstance(value, dict):
        return {"profile_type": type(value).__name__}
    projected = {
        key: _bounded_metadata(value.get(key), budget=budget)
        for key in (
            "featureCount", "geometryTypes", "bbox", "crs", "crs_status",
            "fields_status",
        )
        if key in value
    }
    fields = value.get("fields")
    if isinstance(fields, dict):
        projected["fields"] = {
            str(field_name): {
                key: _bounded_metadata(field_info.get(key), budget=budget)
                for key in (
                    "type", "min", "max", "mean", "null_count", "sampleValues",
                )
                if isinstance(field_info, dict) and key in field_info
            }
            for field_name, field_info in list(fields.items())[:_MAX_METADATA_ITEMS]
        }
        if len(fields) > _MAX_METADATA_ITEMS:
            digest = hashlib.sha256()
            for field_name, field_info in list(fields.items())[_MAX_METADATA_ITEMS:]:
                item = {
                    str(field_name): {
                        key: field_info.get(key)
                        for key in (
                            "type", "min", "max", "mean", "null_count", "sampleValues",
                        )
                        if isinstance(field_info, dict) and key in field_info
                    }
                }
                digest.update(json.dumps(
                    item, ensure_ascii=False, sort_keys=True, default=str,
                    separators=(",", ":"),
                ).encode("utf-8"))
            projected["fields_omitted"] = {
                "count": len(fields) - _MAX_METADATA_ITEMS,
                "sha256": digest.hexdigest(),
            }
    return projected


def _project_source(source: Any, budget: List[int]) -> Dict[str, Any]:
    if not isinstance(source, dict):
        return {"descriptor_type": type(source).__name__}
    projected = {
        key: (
            _profile_metadata(source[key], budget)
            if key == "profile"
            else _bounded_metadata(source[key], budget=budget)
        )
        for key in _SOURCE_METADATA_KEYS
        if key in source
        and not (
            key in ("ref", "ref_id")
            and (
                not isinstance(source[key], str)
                or not source[key].startswith("ref:")
            )
        )
    }
    return projected


def _project_layer(layer: Any, budget: List[int]) -> Optional[Dict[str, Any]]:
    if not isinstance(layer, dict):
        return None
    return {
        key: _bounded_metadata(layer[key], budget=budget)
        for key in _LAYER_METADATA_KEYS
        if key in layer
    }


def _omitted_digest(items: Any, projector: Callable[[Any, List[int]], Any]) -> str:
    digest = hashlib.sha256()
    for identity, value in items:
        projected = projector(value, [_MAX_METADATA_NODES])
        digest.update(json.dumps(
            [identity, projected], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8"))
    return digest.hexdigest()


def cartographic_projection(mapspec: Dict[str, Any]) -> Dict[str, Any]:
    """Return the bounded metadata that can affect cartographic review.

    Source payloads are deliberately excluded. The profile and stable source
    descriptor are sufficient for the deterministic rules and keep review O(1)
    with respect to feature count.
    """
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    projected_sources: Dict[str, Any] = {}
    layers = mapspec.get("layers") if isinstance(mapspec.get("layers"), list) else []
    budget = [_MAX_METADATA_NODES]
    source_items = list(sources.items())
    for source_id, source in source_items[:_MAX_METADATA_ITEMS]:
        projected_sources[str(source_id)] = _project_source(source, budget)
    omitted_sources = source_items[_MAX_METADATA_ITEMS:]
    if omitted_sources:
        projected_sources["__omitted_sources__"] = {
            "count": len(omitted_sources),
            "sha256": _omitted_digest(omitted_sources, _project_source),
        }
    projected_layers = []
    for layer in layers[:_MAX_METADATA_ITEMS]:
        projected = _project_layer(layer, budget)
        if projected is not None:
            projected_layers.append(projected)
    omitted_layers = list(enumerate(layers[_MAX_METADATA_ITEMS:], _MAX_METADATA_ITEMS))
    if omitted_layers:
        projected_layers.append({
            "__omitted_layers__": len(omitted_layers),
            "sha256": _omitted_digest(omitted_layers, _project_layer),
        })
    return {
        "version": mapspec.get("version"),
        "cartographic_profile": _bounded_metadata(
            mapspec.get("cartographic_profile"), budget=budget
        ),
        "view": _bounded_metadata(mapspec.get("view") or {}, budget=budget),
        "layout": _bounded_metadata(mapspec.get("layout") or {}, budget=budget),
        "time": _bounded_metadata(mapspec.get("time") or {}, budget=budget),
        "sources": projected_sources,
        "layers": projected_layers,
        "source_count": len(sources),
        "layer_count": len(layers),
    }


def cartographic_fingerprint(mapspec: Dict[str, Any]) -> str:
    payload = json.dumps(
        cartographic_projection(mapspec),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"carto-sha256:{hashlib.sha256(payload).hexdigest()}"


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        _bounded_metadata(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
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


def _apply_palette_change(layer: Dict[str, Any], colors: List[Any]) -> None:
    """AUTO_SAFE change_palette：按位替换图例色与 paint 输出色（长度一致才动）。

    只换呈现色，不触碰分类断点/类别键——分类语义由 legend_spec 的
    min/max/breaks/categories[].key 承载，颜色仅是呈现。长度不匹配时
    不动（下一次评审仍失败 → repair_exhausted，诚实终止）。
    """
    if not isinstance(colors, list) or not colors:
        return
    legend_spec = layer.get("legend_spec")
    if isinstance(legend_spec, dict):
        ramp_key = (
            "palette_colors"
            if isinstance(legend_spec.get("palette_colors"), list)
            else "colors" if isinstance(legend_spec.get("colors"), list)
            else None
        )
        if ramp_key and len(legend_spec[ramp_key]) == len(colors):
            legend_spec[ramp_key] = list(colors)
        categories = legend_spec.get("categories")
        if isinstance(categories, list) and len(categories) == len(colors):
            for category, color in zip(categories, colors):
                if isinstance(category, dict):
                    category["color"] = color
    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return
    for _prop, spec in _paint_methods(paint):
        outputs = _paint_output_colors(spec)
        if len(outputs) != len(colors):
            continue
        method = spec.get("method")
        if method == "step":
            if spec.get("default") is not None:
                spec["default"] = colors[0]
                rest = colors[1:]
            else:
                rest = colors
            stops = spec.get("stops") or []
            for stop, color in zip(stops, rest):
                if isinstance(stop, (list, tuple)) and len(stop) >= 2:
                    stop[1] = color
        elif method == "interpolate":
            for stop, color in zip(spec.get("stops") or [], colors):
                if isinstance(stop, (list, tuple)) and len(stop) >= 2:
                    stop[1] = color
        elif method == "match":
            for case, color in zip(spec.get("cases") or [], colors):
                if isinstance(case, (list, tuple)) and len(case) >= 2:
                    case[1] = color


def _apply_repairs(
    mapspec: Dict[str, Any], repairs: List[Dict[str, Any]]
) -> Dict[str, Any]:
    candidate = _presentation_copy(mapspec)
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
            "change_palette",
            "set_map_legend_visibility",
        ):
            continue
        if operation == "set_map_legend_visibility":
            layout = candidate.get("layout")
            if not isinstance(layout, dict):
                layout = {}
                candidate["layout"] = layout
            legend = layout.get("legend")
            if not isinstance(legend, dict):
                legend = {}
                layout["legend"] = legend
            legend["visible"] = bool(repair.get("value"))
            continue
        if operation == "change_palette":
            layer = by_id.get(repair.get("layer_id"))
            value = repair.get("value")
            if isinstance(layer, dict) and isinstance(value, dict):
                _apply_palette_change(layer, value.get("colors"))
            continue
        layer = by_id.get(repair.get("layer_id"))
        if operation == "set_layer_visibility":
            if isinstance(layer, dict):
                layer["visible"] = bool(repair.get("visible"))
                layout = layer.get("layout")
                if isinstance(layout, dict) and layout.get("visibility") == "none":
                    layout["visibility"] = "visible"
                # v2(audit H5): 修复翻转可见性后 intent 印记必须同步 ——
                # expected_visible/presentation_owner 描述状态的来历，不
                # 更新会让 agent/repair 溯源失真（结构性维持 user-wins，
                # 不再依赖"修复只计划于 expected_visible=True"的偶然前提）。
                intent = layer.get("cartographic_intent")
                if isinstance(intent, dict):
                    intent["expected_visible"] = bool(repair.get("visible"))
                    if intent.get("presentation_owner") != "user":
                        intent["presentation_owner"] = "system"
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


def _presentation_copy(mapspec: Dict[str, Any]) -> Dict[str, Any]:
    """Copy mutable presentation fields while sharing immutable data bodies.

    Review and AUTO_SAFE repair never edit source datasets.  Deep-copying a
    100k-feature inlineData payload merely to change opacity defeats the
    metadata-first contract, so source/unknown payload values remain shared
    and only their small containers plus presentation fields are copied.
    """
    candidate = dict(mapspec)
    sources = mapspec.get("sources")
    if isinstance(sources, dict):
        candidate["sources"] = {
            source_id: dict(source) if isinstance(source, dict) else source
            for source_id, source in sources.items()
        }
    layers = mapspec.get("layers")
    if isinstance(layers, list):
        copied_layers = []
        for layer in layers:
            if not isinstance(layer, dict):
                copied_layers.append(layer)
                continue
            copied = dict(layer)
            for key in (
                "layout", "paint", "legend_spec", "provenance",
                "cartographic_intent",
            ):
                if key in layer:
                    copied[key] = copy.deepcopy(layer[key])
            copied_layers.append(copied)
        candidate["layers"] = copied_layers
    for key in ("view", "layout"):
        if key in mapspec:
            candidate[key] = copy.deepcopy(mapspec[key])
    return candidate


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
    current = _presentation_copy(mapspec)
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
    current = _presentation_copy(mapspec)
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

        next_mapspec = repair_executor(_presentation_copy(current), copy.deepcopy(repairs))
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
        current = _presentation_copy(next_mapspec)

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
