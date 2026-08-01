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
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from app.services.session_data import session_data_manager
from app.services.chat.prompt import construct_self_healing_message
from app.tools.registry import ToolRegistry
from app.utils.security import sanitize_error_msg
from app.utils.geojson import geojson_bbox

logger = logging.getLogger(__name__)


# ─── 规范化工具名称映射表 ─────────────────────────────
# 把遗留工具名规范化为 canonical webgis_* 名称。两个调用点，均刻意保留：
# (1) dispatch 入口 (本模块 dispatch()) — 规范化来自 ChatEngine LLM 输出 / Pi bridge
#     客户端请求的 *实时* tool_call，避免模型或客户端发出遗留名时 registry 找不到工具。
# (2) history replay (history_service_async) — 重放存储的历史 tool_call 前翻译遗留名。
# 见 ADR-0010：架构评审曾提议移除 (1)，但两条实时路径都可能收到遗留名，(1) 并非冗余；
# 由 test_dispatch_normalizes_legacy_tool_names ("Seam B") 锁定。

LEGACY_TOOL_NAME_MAP: dict[str, str] = {
    # 图层管理与样式设置
    "add_layer": "webgis_layer_upsert",
    "set_layer_style": "webgis_layer_upsert",
    "update_layer": "webgis_layer_upsert",
    "remove_layer": "webgis_layer_remove",
    "delete_layer": "webgis_layer_remove",
    # 视角与导航
    "set_view": "webgis_view_set",
    "move_view": "webgis_view_set",
    "zoom_to_layer": "webgis_view_set",
    # MapSpec 生命周期与检验
    "init_project": "webgis_project_init",
    "get_state": "webgis_state_get",
    "get_map_state": "webgis_state_get",
    "profile_source": "webgis_source_profile",
    "get_source_profile": "webgis_source_profile",
    "set_layout": "webgis_layout_set",
    "validate_spec": "webgis_validate",
    "compile_maplibre": "webgis_compile_maplibre",
    "runtime_validate": "webgis_runtime_validate",
    "checkpoint": "webgis_checkpoint",
    "create_checkpoint": "webgis_checkpoint",
}


def normalize_tool_name(name: str) -> str:
    """规范化遗留工具名为 canonical webgis_* 名称 (供 dispatch 入口与历史重放两处调用，见 ADR-0010)。"""
    return LEGACY_TOOL_NAME_MAP.get(name, name)


# ─── 结果脱敏与元数据提取常量 ─────────────────────────

MSG_MAX_CHARS = 3000
VALUE_MAX_CHARS = 120
SAMPLE_FEATURES = 3
PROPERTY_KEYS_MAX = 20

_PRESERVED_META_KEYS = (
    "bbox",
    "layer_id",
    "feature_count",
    "alias",
    "command",
    "status",
    "ref_id",
    "resolved_layer_id",
    "message",
)


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
            south, west, north, east = parts
            bbox = [west, south, east, north]

    exclude = {"geojson", "features", "data_list", "grid"}
    slim = {k: v for k, v in result.items() if k not in exclude}

    if bbox:
        slim["bbox"] = bbox

    if "geojson" in result or "features" in result:
        slim["_streaming_note"] = "大体积要素数据已过滤，仅保留元数据。完整图层已自动加载。"

    return slim


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
        raw_tool_name = tc["function"]["name"]
        tool_name = normalize_tool_name(raw_tool_name)
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

        # 2. 执行（registry 内部全权处理 ref 解析、校验、异常捕获与自愈）
        try:
            result = await self._registry.dispatch(tool_name, tool_args_raw, session_id=session_id)
        except Exception as e:
            from app.tools._utils import std_error_response
            error_msg = sanitize_error_msg(str(e))
            result = std_error_response(
                error_msg,
                code="TOOL_ERROR",
                error_type=type(e).__name__,
                correction_hint=f"Execution error: {error_msg}",
            )

        # 3. registry 返回 std_error_response dict 的统一错误路径
        if is_error_dict(result):
            error_msg = sanitize_error_msg(result.get("message", ""))
            result["message"] = error_msg
            if "correction_hint" in result and result["correction_hint"]:
                result["correction_hint"] = sanitize_error_msg(result["correction_hint"])
            correction_hint = result.get("correction_hint")
            llm_payload = correction_hint if correction_hint else wrap_error_dict_for_llm(tool_name, result)
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
        if is_suspicious_result(result):
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
        # Inlined from SessionStore.commit_dispatch (ADR-0018 Trigger 2): that
        # method was an anemic wrapper that rebuilt this same payload and called
        # append_event("tool_executed", ...). event_payload already carries tool
        # + ref + result fields, so append directly. The granular surface owns
        # this; the deep-method layer is gone.
        await session_data_manager.append_event(
            session_id, "tool_executed", event_payload
        )


def is_suspicious_result(result: Any) -> bool:
    """检测工具返回是否"可疑"（空数据 / 错误响应），用于触发自愈提示尾。

    纯函数无副作用。本函数为单一权威定义（unified-tool-dispatch 票据 04 contract：
    旧 chat/dispatcher.py 已删除，收敛至此）。供 ToolDispatchService 内部与
    ChatEngine._log_tool_decision / _detect_suspicious_result 共用。
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
