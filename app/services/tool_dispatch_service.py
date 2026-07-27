"""ToolDispatchService —— 统一工具调度（unified-tool-dispatch expand 阶段）。

这是整个工具调度链的唯一拥有者。它把旧 `app/services/chat/dispatcher.py` 的
六项横切职责收到一个判别式结果接口背后，让 ChatEngine（legacy 路径）与
agent_pi_bridge（Pi 路径）共用同一份实现，从而封杀两条路径静默漂移导致的回归
（典型表现：Pi 路径从不产生 ref_id，前端图层挂载失效）。

调度的完整职责（与旧 dispatcher 一致）：
1. 重复调用拦截（同 session 内同名同参数只执行一次）
2. 通过 registry.dispatch 执行（含 ref 解析、参数校验、异常包装）
3. 错误自愈消息构造（同时识别 std_error_response dict 与抛出的异常两条路径）
4. 大型 GeoJSON 入 session_data_manager → 返回 ref 游标（Fetch-on-Demand）
5. 把工具动作回写到 event_log，让下一轮 [环境感知] 反映最新地图变化
6. 启动 WS 实时图层广播（fire-and-forget）

接口形态（见 /improve-codebase-architecture 烤问决议）：判别式结果 dataclass，
而非携带方法的对象。dispatch 只回答「发生了什么」；调用方负责「如何告诉外界」
（发 SSE、推进 task tracker）。刻意拒绝了更深的「结果对象自带 SSE/tracker 方法」
方案——那会把调度耦合到展示层，破坏 locality。

依赖通过构造函数显式注入：registry 必传；fire_broadcast 可选（None 时跳过广播，
用于纯测试 / subagent 场景）。session_data_manager 是唯一的非纯依赖，已有
SessionDataProtocol 端口与内存测试替身（ADR-0004），故本模块为 local-substitutable：
测试时用真实内存 session_data_manager 跑即可，无需 mock。

这是 expand 阶段：本服务与旧 dispatcher.py 并存，尚无生产调用方。
legacy 路径在 03 票据迁移、Pi 路径在 02 票据迁移，最后 04 票据删除旧模块。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from app.services.session_data import session_data_manager
from app.services.chat.prompt import construct_self_healing_message
from app.services.chat.sse_helpers import (
    is_error_dict,
    normalize_tool_args,
    slim_event_result,
    slim_tool_result,
    wrap_error_dict_for_llm,
)
from app.tools.registry import ToolRegistry
from app.utils.security import sanitize_error_msg

logger = logging.getLogger(__name__)

DispatchStatus = Literal["ok", "repeated", "error"]


@dataclass
class ToolDispatchResult:
    """dispatch 的判别式结果。

    status 是唯一判别量：调用方对它做 match，而不是从一组平行布尔/字符串里
    重新拼装出结论（旧 dispatcher 的 8 字段 dict 正是这种浅形态）。

    - status=="ok"       → geojson_ref 可能为 None（无几何工具）或 ref 串
    - status=="repeated" → 重复调用拦截，geojson_ref 必为 None
    - status=="error"    → error_msg 必有值，geojson_ref 必为 None
    """

    status: DispatchStatus
    llm_payload: str            # 给 LLM 看的字符串（任意分支都有值）
    slim_event: dict            # 给前端 SSE 用的脱敏版本（任意分支都有值）
    geojson_ref: Optional[str]  # 仅 status=="ok" 且产出图层时非空
    raw_result: Any             # 原始工具返回，供 step 完成追踪使用
    error_msg: Optional[str]    # 仅 status=="error" 时有值


# 重复调用拦截的 LLM 提示（独立常量，避免 ok/error 分支误用）
_REPEAT_LLMPAYLOAD = "[重复调用拦截] {tool} 已在本任务中以相同参数成功执行，结果已生效。请直接基于既有结果汇报，不要再次调用。"


class ToolDispatchService:
    """工具调度的单一拥有者。两条 agent 路径都应经由本服务。"""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        fire_broadcast: Optional[Callable[[str, str, dict], None]] = None,
    ) -> None:
        self._registry = registry
        self._fire_broadcast = fire_broadcast

    async def dispatch(
        self,
        tc: dict,
        session_id: str,
        executed_tools: set[tuple[str, str]],
    ) -> ToolDispatchResult:
        """执行一次工具调度，返回判别式结果。

        tc: OpenAI 风格的 tool_call（{"id", "function": {"name", "arguments"}}）。
        executed_tools: 同一任务内已执行过的 (tool_name, normalized_args) 集合，
                        会被本调用按需更新（重复拦截语义）。
        """
        tool_name = tc["function"]["name"]
        tool_args_raw = tc["function"]["arguments"]

        # 1. 重复调用拦截
        tool_key = (tool_name, normalize_tool_args(tool_args_raw))
        if tool_key in executed_tools:
            note = _REPEAT_LLMPAYLOAD.format(tool=tool_name)
            return ToolDispatchResult(
                status="repeated",
                llm_payload=note,
                slim_event={"success": True, "note": "Loop blocked"},
                geojson_ref=None,
                raw_result={"success": True, "note": "Loop blocked"},
                error_msg=None,
            )
        executed_tools.add(tool_key)

        # 2. 执行（registry 内部已含 ref 解析、参数校验、异常包装）
        try:
            result = await self._registry.dispatch(tool_name, tool_args_raw, session_id=session_id)
        except Exception as e:
            # 这里只有 _resolve_references 抛 ValueError 才会走到
            # （其余路径都返回 std_error_response dict）
            error_type = "参数校验失败" if isinstance(e, ValueError) and "失败" in str(e) else "执行出错"
            error_msg = sanitize_error_msg(str(e))
            logger.error(f"Tool {tool_name} error: {error_msg}")
            llm_payload = construct_self_healing_message(tool_name, error_msg, error_type)
            return ToolDispatchResult(
                status="error",
                llm_payload=llm_payload,
                slim_event={"success": False, "code": error_type, "message": error_msg},
                geojson_ref=None,
                raw_result={"success": False, "code": error_type, "message": error_msg, "data": None},
                error_msg=error_msg,
            )

        # 3. registry 返回 std_error_response dict 的统一路径
        if is_error_dict(result):
            error_msg = sanitize_error_msg(result.get("message", ""))
            result["message"] = error_msg
            llm_payload = wrap_error_dict_for_llm(tool_name, result)
            await session_data_manager.append_event(
                session_id,
                "tool_failed",
                {"tool": tool_name, "code": result.get("code"), "message": error_msg[:200]},
            )
            return ToolDispatchResult(
                status="error",
                llm_payload=llm_payload,
                slim_event=slim_event_result(result),
                geojson_ref=None,
                raw_result=result,
                error_msg=error_msg,
            )

        # 4. 正常路径：大型 GeoJSON 存为 ref；热力图等元数据落地
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

        # 5. 给 LLM 的载荷（压缩 + 可选自愈提示）
        result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
        llm_payload = slim_tool_result(result, result_str, geojson_ref) or result_str
        if _is_suspicious_result(result):
            llm_payload += (
                "\n\n(注意: 此操作未返回任何空间要素或有效数据。请检查查询范围、关键词或图层名称，"
                "并根据需要尝试不同的参数。不要重复完全相同的调用。)"
            )

        # 6. event_log 回写 + WS 广播
        await self._record_event(tc, session_id, tool_name, result, geojson_ref)

        return ToolDispatchResult(
            status="ok",
            llm_payload=llm_payload,
            slim_event=slim_event_result(result),
            geojson_ref=geojson_ref,
            raw_result=result,
            error_msg=None,
        )

    async def _record_event(
        self,
        tc: dict,
        session_id: str,
        tool_name: str,
        result: Any,
        geojson_ref: Optional[str],
    ) -> None:
        """把工具动作回写 event_log；产出 ref 时 fire-and-forget WS 广播。"""
        event_payload: dict = {"tool": tool_name}
        if geojson_ref:
            event_payload["ref"] = geojson_ref
            if self._fire_broadcast is not None:
                self._fire_broadcast(
                    session_id,
                    "geojson_update",
                    {"step_id": tc.get("id"), "geojson": geojson_ref, "tool": tool_name},
                )
        if isinstance(result, dict):
            for k in ("layer_id", "bbox", "feature_count", "alias", "command", "status"):
                v = result.get(k)
                if v is not None:
                    event_payload[k] = v
        await session_data_manager.append_event(session_id, "tool_executed", event_payload)


def _is_suspicious_result(result: Any) -> bool:
    """检测工具返回是否"可疑"（空数据 / 错误响应），用于触发自愈提示尾。

    纯函数无副作用。与旧 dispatcher.is_suspicious_result 行为一致；此处为内联副本，
    因为旧 dispatcher.py 在 contract 阶段（04 票据）才会被删除，期间避免循环依赖。
    03 票据迁移 legacy 路径时会统一收敛到单一处定义。
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
