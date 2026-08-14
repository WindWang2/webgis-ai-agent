"""LLM Result Formatter (app/services/llm_result_formatter.py).

Deep domain module for formatting tool execution results, payload slimming,
GeoJSON feature property sampling, self-healing error formatting, and event log trimming.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.utils.geojson import geojson_bbox

logger = logging.getLogger(__name__)

# ─── 结果脱敏与元数据提取常量 ─────────────────────────

MSG_MAX_CHARS = 2500
VALUE_MAX_CHARS = 80
SAMPLE_FEATURES = 5
PROPERTY_KEYS_MAX = 15


_PRESERVED_META_KEYS = (
    "bbox",
    "layer_id",
    "feature_count",
    "alias",
    "command",
    "status",
    "ref_id",
    "result_ref",
    "resolved_layer_id",
    "message",
    "mapspec_fingerprint",
    "runtime_observation_seq",
)


def _slim_cartographic_review(value: Any) -> Optional[dict]:
    """Keep actionable review evidence bounded for the agent/frontend."""
    if not isinstance(value, dict):
        return None
    review = value.get("review") if isinstance(value.get("review"), dict) else {}
    checks = []
    for check in (review.get("checks") or []):
        if not isinstance(check, dict) or check.get("status") == "pass":
            continue
        projected = {
            key: check.get(key)
            for key in (
                "rule", "status", "severity", "message", "layer_id",
                "source_id", "evidence_class", "evidence", "repairability",
                "suggested_fix",
            )
            if check.get(key) is not None
        }
        try:
            if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= 4096:
                checks.append(projected)
        except (TypeError, ValueError):
            continue
        if len(checks) >= 12:
            break
    return {
        "stage": value.get("stage"),
        "status": value.get("status"),
        "termination_reason": value.get("termination_reason"),
        "repair_count": value.get("repair_count", 0),
        "checks": checks,
    }


def _truncate_value(v: Any, limit: int = VALUE_MAX_CHARS) -> Any:
    if isinstance(v, str) and len(v) > limit:
        return v[: limit - 1] + "…"
    return v


def _truncate_properties(props: dict, value_limit: int = VALUE_MAX_CHARS, max_keys: int = PROPERTY_KEYS_MAX) -> dict:
    if not isinstance(props, dict):
        return props
    out: dict = {}
    for i, (k, v) in enumerate(props.items()):
        if i >= max_keys:
            out["__more_keys__"] = len(props) - max_keys
            break
        out[k] = _truncate_value(v, value_limit)
    return out


def normalize_tool_args(raw: Any) -> str:
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        return str(raw)


def is_error_dict(result: Any) -> bool:
    return isinstance(result, dict) and result.get("success") is False and "code" in result


def wrap_error_dict_for_llm(tool_name: str, result: dict) -> str:
    from app.services.chat.prompt import construct_self_healing_message
    code = result.get("code", "TOOL_ERROR")
    message = result.get("message", "")
    error_type = result.get("error_type", code)
    hint = result.get("correction_hint")
    if hint and hint not in message:
        message = f"{message}\n({hint})"
    return construct_self_healing_message(tool_name, message, error_type)


def slim_tool_result(result: Any, result_str: str, session_geojson_ref: Optional[str]) -> str:
    if isinstance(result, dict) and "summary" in result:
        slim = {"summary": result["summary"]}
        if session_geojson_ref:
            slim["ref_id"] = session_geojson_ref
        for k in _PRESERVED_META_KEYS:
            v = result.get(k)
            if v is not None and k not in slim:
                slim[k] = _truncate_value(v) if isinstance(v, str) else v
        if "error_type" in result and result["error_type"]:
            slim["error_type"] = result["error_type"]
        if "correction_hint" in result and result["correction_hint"]:
            slim["correction_hint"] = result["correction_hint"]
        review = _slim_cartographic_review(result.get("cartographic_review"))
        if review is not None:
            slim["cartographic_review"] = review
        return json.dumps(slim, ensure_ascii=False)

    if len(result_str) <= MSG_MAX_CHARS:
        return result_str

    if isinstance(result, dict):
        geojson = result.get("geojson")
        is_direct_fc = result.get("type") == "FeatureCollection" and "features" in result
        if is_direct_fc:
            geojson = result

        slim = {k: v for k, v in result.items() if k not in ("geojson", "image", "features")}

        if isinstance(geojson, dict) and "features" in geojson:
            features = geojson["features"]
            feature_count = len(features)
            from app.utils.geojson import summarize_feature_properties
            typed_properties, raw_samples = summarize_feature_properties(
                features,
                sample_size=max(SAMPLE_FEATURES, 10),
                max_keys=PROPERTY_KEYS_MAX,
                ignored_keys=set(),
            )
            sample = []
            for props in raw_samples[:SAMPLE_FEATURES]:
                sample.append({"properties": _truncate_properties(props)})

            ref_hint = (
                f"如需进一步空间分析，请调用工具并将 geojson 参数设为 \"{session_geojson_ref}\"。"
                if session_geojson_ref
                else ""
            )
            slim["geojson_summary"] = {
                "feature_count": feature_count,
                "typed_properties": typed_properties,
                "sample_properties": sample,
                "note": f"数据已推送至前端（共 {feature_count} 个要素）。{ref_hint}",
            }
        elif result.get("type") == "heatmap_raster":
            slim["note"] = "栅格热力图已推送至前端，bbox=" + str(result.get("bbox"))

        return json.dumps(slim, ensure_ascii=False)

    return result_str


def slim_event_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result

    bbox = result.get("bbox")
    if not bbox:
        if "geojson" in result:
            bbox = geojson_bbox(result["geojson"])
        elif result.get("type") == "FeatureCollection" and "features" in result:
            bbox = geojson_bbox(result)

    if isinstance(bbox, str) and bbox:
        parts = [float(x) for x in bbox.split(",") if x.strip()]
        if len(parts) == 4:
            # GIS-P3-6: every producer in the repo emits [w,s,e,n] — parse in
            # that canonical order (the old s,w,n,e unpacking transposed a
            # canonical string into [s,w,n,e]).
            west, south, east, north = parts
            bbox = [west, south, east, north]

    exclude = {"geojson", "features", "data_list", "grid"}
    slim = {k: v for k, v in result.items() if k not in exclude}

    if isinstance(result.get("mapspec"), dict):
        # Preserve only the same metadata projection used for deterministic
        # review; feature bodies never ride the SSE evidence path.
        from app.lib.cartography.quality_loop import cartographic_projection
        slim["mapspec"] = cartographic_projection(result["mapspec"])
    review = _slim_cartographic_review(result.get("cartographic_review"))
    if review is not None:
        slim["cartographic_review"] = review

    if bbox:
        slim["bbox"] = bbox

    if "geojson" in result or "features" in result:
        slim["_streaming_note"] = "大体积要素数据已过滤，仅保留元数据。完整图层已自动加载。"

    return slim
