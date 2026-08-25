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
    # audit4 #979: harness 计划类工具的有界裁决投影（capability→resolved_tool
    # 表）。summary 分支此前把它整包丢弃 —— 计划骨架永远到不了 LLM。
    "guidance",
)


# ── #439: 最终体积闸（Zero-Big-Data-Context 的最后一道防线） ──────────────────
# slim_tool_result 是工具结果进入 LLM 上下文前的唯一闸口
# (tool_dispatch_service.llm_payload)。旧实现只对特定形状/键
# (geojson/image/features) 做了削减：
#   (a) 纯字符串结果超长时原样返回（return result_str 兜底）；
#   (b) dict 分支里 data/rows/chart 等非 geojson 键全量保留
#       （web_search 每次就这样塞进 ~10KB snippets）。
# 这里加一道与形状/键名无关的总量钳制：任何 LLM-bound payload ≤ MSG_MAX_CHARS。
_SLIM_KEY_VALUE_MAX_CHARS = 600   # 非保留字符串值的初始钳制长度
_SLIM_LIST_MAX_ITEMS = 20         # 列表初始保留条数
_SLIM_DICT_MAX_KEYS = 30          # dict 初始保留键数（与 _truncate_properties 同思路）
_SLIM_HARD_FLOOR = 40             # 折半下限；到顶仍超预算则硬切并打标
_SLIM_TRUNCATION_SUFFIX = "…[已截断]"
_SLIM_MAX_DEPTH = 8


def _clamp_sized_value(v: Any, str_limit: int, list_limit: int, depth: int = 0) -> Any:
    """递归钳制一个 JSON-ish 值：字符串→str_limit，列表→list_limit 条
    （省略数打标），dict→前 list 个键（省略数打标）。数值/布尔/None 原样。
    正常大小的元数据（bbox 四元组、feature_count、短 message 等）在任何
    轮次都不会被触碰。
    """
    if depth > _SLIM_MAX_DEPTH:
        return _SLIM_TRUNCATION_SUFFIX
    if isinstance(v, str):
        if len(v) > str_limit:
            return v[: str_limit - 1] + "…"
        return v
    if isinstance(v, list):
        out = [
            _clamp_sized_value(item, str_limit, list_limit, depth + 1)
            for item in v[:list_limit]
        ]
        omitted = len(v) - list_limit
        if omitted > 0:
            out.append(f"(…另有 {omitted} 项因上下文预算省略)")
        return out
    if isinstance(v, dict):
        out = {}
        for i, (k, val) in enumerate(v.items()):
            if i >= _SLIM_DICT_MAX_KEYS:
                out["__more_keys__"] = len(v) - _SLIM_DICT_MAX_KEYS
                break
            out[k] = _clamp_sized_value(val, str_limit, list_limit, depth + 1)
        return out
    return v


def _serialize_under_budget(value: Any) -> str:
    """序列化并强制 ≤ MSG_MAX_CHARS（#439 总量闸）。

    策略：先用宽松钳制（600 字符/20 条/30 键）序列化——常规结果完全不受
    影响；仍超预算则对字符串/列表限额逐轮折半（JSON 始终保持合法），
    到下限仍超（病态宽而浅的结构）才硬切并追加截断标记，保证不变量
    绝对成立。
    """
    str_limit = _SLIM_KEY_VALUE_MAX_CHARS
    list_limit = _SLIM_LIST_MAX_ITEMS
    while True:
        try:
            payload = json.dumps(
                _clamp_sized_value(value, str_limit, list_limit),
                ensure_ascii=False,
            )
        except (TypeError, ValueError):
            return str(value)[: MSG_MAX_CHARS - len(_SLIM_TRUNCATION_SUFFIX)] + _SLIM_TRUNCATION_SUFFIX
        if len(payload) <= MSG_MAX_CHARS:
            return payload
        if str_limit <= _SLIM_HARD_FLOOR and list_limit <= 1:
            return payload[: MSG_MAX_CHARS - len(_SLIM_TRUNCATION_SUFFIX)] + _SLIM_TRUNCATION_SUFFIX
        str_limit = max(_SLIM_HARD_FLOOR, str_limit // 2)
        list_limit = max(1, list_limit // 2)


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


def is_error_like_result(result: Any) -> bool:
    """#529/#589: normal-return failure shapes.

    Tool sites return failure dicts instead of raising or returning the
    canonical ``std_error_response`` shape; only the canonical shape was
    recognized, so these were classified as success (marked completed, same-args
    retries intercepted with a fabricated "已成功执行", plans advanced past
    failures).

    Recognized error-as-value key shapes:
      - ``{"error": <str>}``                — the #529 family (~139 sites),
      - ``{"type": "error", ...}``          — network/temporal/spatial_decision,
      - ``{"status": "error"|"failed"}``    — project/workflow/explorer,
      - ``{"success": False, "message": <str>}`` — audit4 #984: the
        cartography_tools/webgis_component_update family (~17 sites).
        ``success is False`` 是无歧义的失败信号（与 registry metrics 的判定
        口径一致），此前仅因缺 code 键逃过归一 —— 被标 completed、同参重试
        被「已成功执行」拦截。

    Conservative by design (mirrors ``chinese_maps/http.py``'s
    ``_is_provider_error_dict``): only a **string** ``error`` value classifies,
    so business payloads that merely carry an ``error`` key (``None``, numeric,
    nested error info) are not reclassified. The string may be empty — an
    ``{"error": ""}`` result is still an error channel, not a success. ``type``
    and ``status`` only classify at their canonical error values, so legitimate
    uses of those keys (``{"type": "FeatureCollection"}``, ``{"status": "ok"}``,
    ``{"status": "template_applied"}``, ...) are never reclassified. An explicit
    ``success=True`` shields partial-success payloads (e.g. geocode returning
    results alongside an error note).
    """
    if not isinstance(result, dict) or result.get("success") is True:
        return False
    if isinstance(result.get("error"), str):
        return True
    if result.get("type") == "error":
        return True
    if result.get("status") in ("error", "failed"):
        return True
    if result.get("success") is False and isinstance(result.get("message"), str):
        return True
    return False


def is_tool_error_result(result: Any) -> bool:
    """#529: unified tool-failure classification — canonical
    ``std_error_response`` shape OR the ``{"error": <str>}`` normal-return
    shape. Use this everywhere a consumer classifies a tool result as failed.
    """
    return is_error_dict(result) or is_error_like_result(result)


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
        # #439：summary 分支同样过总量闸 —— 病态超长的 summary/保留键也不能
        # 突破上下文预算。
        return _serialize_under_budget(slim)

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

        # #439：非 geojson 键（data/rows/chart/untrusted blocks…）不再全量放行，
        # 统一过总量闸；geojson 摘要路径行为不变（正常大小不触发钳制）。
        return _serialize_under_budget(slim)

    # #439 (a)：纯字符串/裸列表兜底 —— 超长时也必须截断到预算内。
    if isinstance(result, str):
        return result[: MSG_MAX_CHARS - len(_SLIM_TRUNCATION_SUFFIX)] + _SLIM_TRUNCATION_SUFFIX
    return _serialize_under_budget(result)


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

    exclude = {"geojson", "features", "data_list", "grid", "data"}
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
