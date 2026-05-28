"""工具调度（M1 深水区拆出）：从 ChatEngine 抽出 _dispatch_tool 与
_detect_suspicious_result，做成可独立测试的自由函数。

工具调度的职责（保持不变）：
1. 重复调用拦截（同 session 内同名同参数只执行一次）
2. 通过 registry.dispatch 执行（含 ref 解析、参数校验、异常包装）
3. 错误自愈消息构造（同时识别 std_error_response 字典与异常抛出两条路径）
4. 大型 GeoJSON 入 session_data_manager → 返回 ref 游标
5. 把工具动作回写到 event_log，让下一轮 [环境感知] 反映最新地图变化
6. 启动 WS 实时图层广播（fire-and-forget）

依赖通过参数显式传入：
- registry: ToolRegistry
- fire_broadcast: 可选回调 (session_id, event_type, data) -> None，
  让 ChatEngine 注入它自己的 _fire_and_forget + broadcast_ws_event 组合。
  传 None 时跳过广播（用于纯单测）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from app.services.session_data import session_data_manager
from app.services.task_tracker import detect_geojson
from app.services.chat.prompt import construct_self_healing_message
from app.services.chat.sse_helpers import (
    is_error_dict,
    normalize_tool_args,
    slim_event_result,
    slim_tool_result,
    wrap_error_dict_for_llm,
)
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def is_suspicious_result(result: Any) -> bool:
    """检测工具返回的结果是否"可疑"（空数据 / 错误响应），用于触发自愈提示。

    纯函数无副作用，方便单测枚举所有可疑形状。
    """
    if not result:
        return True
    if isinstance(result, dict):
        if result.get("success") is False:
            return True
        if result.get("type") == "FeatureCollection" and not result.get("features"):
            return True
        if "data" in result and isinstance(result["data"], list) and not result["data"]:
            return True
        if "poi_count" in result and result["poi_count"] == 0:
            return True
    if isinstance(result, list) and not result:
        return True
    return False


async def dispatch_tool(
    tc: dict,
    session_id: str,
    executed_tools: set[tuple[str, str]],
    *,
    registry: ToolRegistry,
    fire_broadcast: Optional[Callable[[str, str, dict], None]] = None,
) -> dict:
    """统一的工具执行入口。返回 dict 字段：

        - result: 原始工具返回（可能是 dict/str/list/error_dict）
        - llm_payload: 给 LLM 看的字符串（已压缩、已附加自愈提示）
        - slim_event: 给前端 SSE 用的脱敏版本
        - geojson_ref: 若工具产出新图层数据则非空
        - has_geojson: bool
        - repeated: 是否被重复调用拦截
        - is_error: 是否是错误（影响前端 step_error 派发）
        - error_msg: 错误消息字符串（仅 is_error 时有值）
    """
    tool_name = tc["function"]["name"]
    tool_args_raw = tc["function"]["arguments"]

    tool_key = (tool_name, normalize_tool_args(tool_args_raw))
    if tool_key in executed_tools:
        note = (
            f"[重复调用拦截] {tool_name} 已在本任务中以相同参数成功执行，"
            f"结果已生效。请直接基于既有结果汇报，不要再次调用。"
        )
        return {
            "result": {"success": True, "note": "Loop blocked"},
            "llm_payload": note,
            "slim_event": {"success": True, "note": "Loop blocked"},
            "geojson_ref": None,
            "has_geojson": False,
            "repeated": True,
            "is_error": False,
            "error_msg": "",
        }
    executed_tools.add(tool_key)

    is_error = False
    error_msg = ""
    try:
        result = await registry.dispatch(tool_name, tool_args_raw, session_id=session_id)
    except Exception as e:
        # 这里只有 _resolve_references 抛 ValueError 才会走到（其余路径都返回 std_error_response dict）
        error_type = "参数校验失败" if isinstance(e, ValueError) and "失败" in str(e) else "执行出错"
        error_msg = str(e)
        logger.error(f"Tool {tool_name} error: {e}")
        llm_payload = construct_self_healing_message(tool_name, error_msg, error_type)
        return {
            "result": {"success": False, "code": error_type, "message": error_msg, "data": None},
            "llm_payload": llm_payload,
            "slim_event": {"success": False, "code": error_type, "message": error_msg},
            "geojson_ref": None,
            "has_geojson": False,
            "repeated": False,
            "is_error": True,
            "error_msg": error_msg,
        }

    # registry 返回 std_error_response dict 的统一路径
    if is_error_dict(result):
        is_error = True
        error_msg = result.get("message", "")
        llm_payload = wrap_error_dict_for_llm(tool_name, result)
        await session_data_manager.append_event(
            session_id,
            "tool_failed",
            {"tool": tool_name, "code": result.get("code"), "message": error_msg[:200]},
        )
        return {
            "result": result,
            "llm_payload": llm_payload,
            "slim_event": slim_event_result(result),
            "geojson_ref": None,
            "has_geojson": False,
            "repeated": False,
            "is_error": True,
            "error_msg": error_msg,
        }

    # 正常路径：把大型 GeoJSON 存为 ref，热力图等元数据落地
    geojson_ref: Optional[str] = None
    target_data = None
    if isinstance(result, dict):
        if isinstance(result.get("geojson"), (dict, list)):
            target_data = result["geojson"]
        elif result.get("type") == "FeatureCollection" and "features" in result:
            target_data = result
        if target_data is not None:
            geojson_ref = await session_data_manager.store(session_id, target_data, prefix="geojson")
        if result.get("type") == "heatmap_raster":
            await session_data_manager.store(session_id, result, prefix="heatmap")

    result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
    llm_payload = slim_tool_result(result, result_str, geojson_ref) or result_str

    if is_suspicious_result(result):
        llm_payload += (
            "\n\n(注意: 此操作未返回任何空间要素或有效数据。请检查查询范围、关键词或图层名称，"
            "并根据需要尝试不同的参数。不要重复完全相同的调用。)"
        )

    # 把工具动作回写到事件日志：下一轮 [环境感知] 会看到最新地图变化
    event_payload: dict = {"tool": tool_name}
    if geojson_ref:
        event_payload["ref"] = geojson_ref
        # 实时 WS 推送：让地图在对话流还在生成时就开始渲染
        if fire_broadcast is not None:
            fire_broadcast(
                session_id,
                "geojson_update",
                {"step_id": tc.get("id"), "geojson": geojson_ref, "tool": tool_name},
            )

    if isinstance(result, dict):
        for k in ("layer_id", "bbox", "feature_count", "alias", "command", "status"):
            v = result.get(k)
            if v is not None:
                event_payload[k] = v
    if is_error:
        event_payload["is_error"] = True
        if error_msg:
            event_payload["error_msg"] = str(error_msg)[:200]
    await session_data_manager.append_event(session_id, "tool_executed", event_payload)

    return {
        "result": result,
        "llm_payload": llm_payload,
        "slim_event": slim_event_result(result),
        "geojson_ref": geojson_ref,
        "has_geojson": detect_geojson(result),
        "repeated": False,
        "is_error": is_error,
        "error_msg": error_msg,
    }
