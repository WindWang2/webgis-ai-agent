"""SSE / 工具结果脱敏纯函数（M1：从 chat_engine.py 抽离）。

这里都是无状态 helper，方便单独单测：
- `LRUCache` 通用 LRU dict
- `parse_minimax_xml_tool_calls` 解析 MiniMax XML 风格 tool_calls
- `normalize_tool_args` 工具参数稳定 key（去重判定用）
- `is_error_dict` / `wrap_error_dict_for_llm` 错误结构识别 + 自愈包装
- `slim_tool_result` 把工具结果压成 LLM 友好摘要（消息流体积控制）
- `calculate_bbox` 从 GeoJSON 推 bbox
- `slim_event_result` SSE 传输前的脱敏：去掉大几何字段保留导航元数据

公开 API 没有前导下划线；旧 chat_engine 内的下划线版本会 re-export 保持兼容。
"""
from __future__ import annotations

import json
import re
import uuid
from collections import OrderedDict
from typing import Any, Optional


# ─── LRU cache（容量受限的 OrderedDict） ───────────────────────


class LRUCache(OrderedDict):
    """Simple LRU Cache to bound memory usage."""

    def __init__(self, capacity: int = 100):
        super().__init__()
        self.capacity = capacity

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self.capacity:
            oldest = next(iter(self))
            del self[oldest]


# ─── 工具结果体积控制 ──────────────────────────────────────────


MSG_MAX_CHARS = 3000  # 存入 messages 的工具结果最大字符数


def normalize_tool_args(raw: Any) -> str:
    """规范化工具参数为稳定 key，避免 LLM 拼 JSON 字段顺序差异绕过重复调用拦截。"""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        return str(raw)


def is_error_dict(result: Any) -> bool:
    """识别 std_error_response 形状的错误返回。"""
    return isinstance(result, dict) and result.get("success") is False and "code" in result


# ─── XML tool-call 解析（MiniMax 风格） ─────────────────────────


_INVOKE_PAT = re.compile(
    r'minimax:tool_call\s+<invoke\s+name="([^"]+)">(.*?)(?:</invoke>|$)',
    re.DOTALL,
)
_PARAM_PAT = re.compile(r'<parameter\s+name="([^"]+)">(.*?)</parameter>', re.DOTALL)


def parse_minimax_xml_tool_calls(content: str) -> list[dict]:
    """Parse MiniMax XML-format tool calls from content field.

    Handles: minimax:tool_call <invoke name="tool"> <parameter name="p">v</parameter> </invoke>
    """
    tool_calls: list[dict] = []
    for tool_name, body in _INVOKE_PAT.findall(content):
        params: dict = {}
        for p_name, p_value in _PARAM_PAT.findall(body):
            v = p_value.strip()
            try:
                params[p_name] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                params[p_name] = v
        if tool_name.strip():
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "function": {"name": tool_name.strip(), "arguments": params},
            })
    return tool_calls


# ─── 工具结果脱敏 ──────────────────────────────────────────────


def slim_tool_result(result: Any, result_str: str, session_geojson_ref: Optional[str]) -> str:
    """将大型工具结果压缩为 LLM 友好的摘要版本。

    完整 GeoJSON 已通过 SSE 推送给前端，messages 里只保留摘要。
    """
    # 新版 GeoAnalysisResult：summary 优先
    if isinstance(result, dict) and "summary" in result:
        slim = {"summary": result["summary"]}
        if session_geojson_ref:
            slim["ref_id"] = session_geojson_ref
        if "error_type" in result and result["error_type"]:
            slim["error_type"] = result["error_type"]
        if "correction_hint" in result and result["correction_hint"]:
            slim["correction_hint"] = result["correction_hint"]
        return json.dumps(slim, ensure_ascii=False)

    if len(result_str) <= MSG_MAX_CHARS:
        return result_str

    if isinstance(result, dict):
        # 1. 识别 GeoJSON
        geojson = result.get("geojson")
        is_direct_fc = result.get("type") == "FeatureCollection" and "features" in result
        if is_direct_fc:
            geojson = result

        # 2. 保留重要元数据
        slim = {k: v for k, v in result.items() if k not in ("geojson", "image", "features")}

        # 3. 地理要素摘要
        if isinstance(geojson, dict) and "features" in geojson:
            features = geojson["features"]
            feature_count = len(features)
            property_keys: set[str] = set()
            for f in features[:10]:
                if isinstance(f, dict):
                    property_keys.update(f.get("properties", {}).keys())
            sample = []
            for f in features[:3]:
                if isinstance(f, dict):
                    sample.append({"properties": f.get("properties", {})})
            ref_hint = (
                f"如需进一步空间分析，请调用工具并将 geojson 参数设为 \"{session_geojson_ref}\"。"
                if session_geojson_ref
                else ""
            )
            slim["geojson_summary"] = {
                "feature_count": feature_count,
                "available_properties": list(property_keys),
                "sample_properties": sample,
                "note": f"数据已推送至前端（共 {feature_count} 个要素）。{ref_hint}",
            }
        elif result.get("type") == "heatmap_raster":
            slim["note"] = "栅格热力图已推送至前端，bbox=" + str(result.get("bbox"))

        return json.dumps(slim, ensure_ascii=False)

    # result 不是 dict — 直接返回原文（理论上前面已经检查过 ≤MSG_MAX_CHARS）
    return result_str


# ─── BBox 计算 + 事件脱敏 ─────────────────────────────────────


def calculate_bbox(geojson: Any) -> Optional[list]:
    """从 GeoJSON 推 [west, south, east, north] BBox；空集返回 None。"""
    if not isinstance(geojson, dict):
        return None
    features = geojson.get("features", [])
    if not features:
        return None
    min_lat, min_lon = float("inf"), float("inf")
    max_lat, max_lon = float("-inf"), float("-inf")
    found = False

    def process(c):
        nonlocal min_lat, min_lon, max_lat, max_lon, found
        if isinstance(c, (list, tuple)) and len(c) >= 2 and isinstance(c[0], (int, float)):
            lng, lat = float(c[0]), float(c[1])
            min_lon, max_lon = min(min_lon, lng), max(max_lon, lng)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
            found = True
        elif isinstance(c, list):
            for item in c:
                process(item)

    for f in features:
        geom = f.get("geometry")
        if not geom:
            continue
        coords = geom.get("coordinates")
        if not coords:
            continue
        process(coords)

    return [min_lon, min_lat, max_lon, max_lat] if found else None


def slim_event_result(result: Any) -> Any:
    """SSE 传输前的工具结果脱敏：去大几何字段、保留导航 / 渲染元数据。"""
    if not isinstance(result, dict):
        return result

    # 提取或计算 bbox
    bbox = result.get("bbox")
    if not bbox:
        if "geojson" in result:
            bbox = calculate_bbox(result["geojson"])
        elif result.get("type") == "FeatureCollection" and "features" in result:
            bbox = calculate_bbox(result)

    # 规范化 bbox 字符串 → 数组
    if isinstance(bbox, str) and bbox:
        parts = [float(x) for x in bbox.split(",") if x.strip()]
        if len(parts) == 4:
            south, west, north, east = parts
            bbox = [west, south, east, north]

    # 移除大数据字段
    exclude = {"geojson", "features", "data_list", "grid"}
    slim = {k: v for k, v in result.items() if k not in exclude}

    if bbox:
        slim["bbox"] = bbox

    if "geojson" in result or "features" in result:
        slim["_streaming_note"] = "大体积要素数据已过滤，仅保留元数据。完整图层已自动加载。"

    return slim


# ─── 自愈包装（导入避免循环：prompt 模块也要用） ─────────────


def wrap_error_dict_for_llm(tool_name: str, result: dict) -> str:
    """将 std_error_response dict 包装为统一的自愈消息字符串。

    需要 prompt 模块的 construct_self_healing_message — 在函数内 import 避免循环。
    """
    from app.services.chat.prompt import construct_self_healing_message

    code = result.get("code", "TOOL_ERROR")
    message = result.get("message", "")
    error_type = result.get("error_type", code)
    return construct_self_healing_message(tool_name, message, error_type)
