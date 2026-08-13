"""Pure planner for bounded, presentation-only runtime cartographic repairs."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional


MAX_RUNTIME_REPAIR_ITERATIONS = 2
AUTO_SAFE_RUNTIME_RULES = {
    "RUNTIME_RESULT_VISIBILITY",
    "RUNTIME_OPACITY_CONVERGENCE",
    "RUNTIME_LEGEND_CONVERGENCE",
    "RUNTIME_STYLE_CONVERGENCE",
}


def _finite_opacity(layer: Dict[str, Any]) -> Optional[float]:
    paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
    for key in (
        "opacity", "fill-opacity", "line-opacity", "circle-opacity", "raster-opacity"
    ):
        value = paint.get(key)
        if (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
        ):
            return float(value)
    return None


def _style_projection(layer: Dict[str, Any]) -> Dict[str, Any]:
    paint = layer.get("paint") if isinstance(layer.get("paint"), dict) else {}
    style: Dict[str, Any] = {}
    for key in ("color", "fill-color", "line-color", "circle-color"):
        if isinstance(paint.get(key), str):
            style["color"] = paint[key]
            break
    for source_key, target_key in (
        ("fill-outline-color", "strokeColor"),
        ("line-width", "strokeWidth"),
        ("circle-radius", "pointSize"),
    ):
        value = paint.get(source_key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            style[target_key] = value
    return style


def repair_patch_fingerprint(patches: List[Dict[str, Any]]) -> str:
    # Intent generation is a concurrency precondition, not repair semantics.
    # Excluding it ensures the same failed presentation patch is recognized
    # after a newer observation instead of becoming an unbounded new attempt.
    canonical = []
    for patch in patches:
        item = dict(patch)
        before = dict(item.get("before") or {})
        before.pop("_intentGeneration", None)
        item["before"] = before
        canonical.append(item)
    payload = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "repair-sha256:" + hashlib.sha256(payload).hexdigest()


def plan_runtime_repairs(
    mapspec: Dict[str, Any],
    observation: Dict[str, Any],
    cartography: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return one deterministic AUTO_SAFE presentation patch, if justified.

    The plan contains no source bodies and cannot change analysis parameters,
    classification breaks, fields, filters, or datasets.
    """
    failed_by_layer: Dict[str, set[str]] = {}
    runtime_ids: Dict[str, str] = {}
    for check in cartography.get("checks") or []:
        if not isinstance(check, dict) or check.get("status") != "fail":
            continue
        rule = str(check.get("rule") or "")
        if rule not in AUTO_SAFE_RUNTIME_RULES:
            continue
        evidence = check.get("evidence") if isinstance(check.get("evidence"), dict) else {}
        layer_id = evidence.get("layer_id")
        runtime_id = evidence.get("runtime_layer_id")
        if not isinstance(layer_id, str) or not isinstance(runtime_id, str):
            continue
        failed_by_layer.setdefault(layer_id, set()).add(rule)
        runtime_ids[layer_id] = runtime_id
    if not failed_by_layer:
        return None

    observed_by_id: Dict[str, Dict[str, Any]] = {}
    for observed in observation.get("layers") or []:
        if not isinstance(observed, dict):
            continue
        for identity in (observed.get("id"), observed.get("runtime_store_id")):
            if identity:
                observed_by_id[str(identity)] = observed
    patches: List[Dict[str, Any]] = []
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        layer_id = str(layer.get("id") or "")
        runtime_id = runtime_ids.get(layer_id)
        if not runtime_id:
            continue
        observed = observed_by_id.get(runtime_id)
        if observed is None:
            continue
        intent_generation = observed.get("intent_generation")
        if not isinstance(intent_generation, int) or isinstance(intent_generation, bool):
            # Without a generation precondition, a delayed repair could
            # overwrite a newer user edit that happens to share the old value.
            continue
        rules = failed_by_layer[layer_id]
        desired: Dict[str, Any] = {}
        before: Dict[str, Any] = {"_intentGeneration": intent_generation}
        if "RUNTIME_RESULT_VISIBILITY" in rules:
            desired["visible"] = (
                layer.get("visible") is not False
                and (layer.get("layout") or {}).get("visibility") != "none"
            )
            before["visible"] = observed.get("visible")
        if "RUNTIME_OPACITY_CONVERGENCE" in rules:
            opacity = _finite_opacity(layer)
            if opacity is not None:
                desired["opacity"] = opacity
                before["opacity"] = observed.get("opacity")
        if "RUNTIME_LEGEND_CONVERGENCE" in rules:
            desired["legend_spec"] = layer.get("legend_spec")
            before["legend_spec"] = observed.get("legend_spec")
        if "RUNTIME_STYLE_CONVERGENCE" in rules:
            style = _style_projection(layer)
            if style:
                desired["style"] = style
                before["style"] = observed.get("style")
        if desired:
            patches.append({
                "layer_id": runtime_id,
                "mapspec_layer_id": layer_id,
                "before": before,
                "desired": desired,
                "rules": sorted(rules),
            })
    if not patches:
        return None
    patches.sort(key=lambda item: (item["mapspec_layer_id"], item["layer_id"]))
    return {
        "repairability": "auto_safe",
        "patches": patches,
        "patch_fingerprint": repair_patch_fingerprint(patches),
    }
