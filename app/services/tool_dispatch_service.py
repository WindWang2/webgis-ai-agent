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

import asyncio
import base64
import json
import logging
import re
import uuid
import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional

from app.services.session_data import session_data_manager
from app.services.session_data_protocol import is_unavailable_ref
from app.tools.registry import ToolRegistry
from app.utils.security import sanitize_error_msg
from app.utils.geojson import geojson_bbox

from app.services.jobs.cancellation import OperationCancelled
from app.lib.runtime.evidence import current_turn_evidence

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
    # remove_layer 不在别名表：#516 其已是 registry 现役工具
    # (app/tools/layer_manager.py，参数 layer_ref)。别名改写会把合法调用
    # 重定向到 webgis_layer_remove (layer_id schema)，导致校验失败——
    # LLM 按目录可见名调用即命中现役工具。
    "delete_layer": "webgis_layer_remove",
    # 视角与导航
    "set_view": "webgis_view_set",
    "move_view": "webgis_view_set",
    # zoom_to_layer 不在别名表：#516 其已是 registry 现役工具
    # (app/tools/map_view.py，参数 layer_ref/padding)。改写为全可选
    # webgis_view_set 会吞掉 layer_ref 参数使缩放命令永不触发。
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


# ─── 结果脱敏与元数据提取 (重定向至 app.services.llm_result_formatter) ───

from app.services.llm_result_formatter import (
    MSG_MAX_CHARS,
    VALUE_MAX_CHARS,
    SAMPLE_FEATURES,
    PROPERTY_KEYS_MAX,
    _PRESERVED_META_KEYS,
    _truncate_value,
    _truncate_properties,
    normalize_tool_args,
    is_error_dict,
    is_error_like_result,
    is_tool_error_result,
    wrap_error_dict_for_llm,
    slim_tool_result,
    slim_event_result,
)


DispatchStatus = Literal["ok", "repeated", "error"]

# ─── V3 地图动作 action_id 铸造（Harness–Map Interaction Closed Loop）─────
# 动作 id 形如 ``ma-<uuid4hex[:16]>``，写入工具结果的 command/commands[] dict，
# 随 SSE step_result 直达前端；前端 ack 时原样回传，供后端按 action_id 精确匹配。
MAP_ACTION_ID_PREFIX = "ma-"
REQUESTED_SNAPSHOT_MAX_BYTES = 2048


def _mint_map_action_id() -> str:
    """铸一个唯一的地图动作 id。"""
    return f"{MAP_ACTION_ID_PREFIX}{uuid.uuid4().hex[:16]}"


def _cap_requested_snapshot(
    params: Any, max_bytes: int = REQUESTED_SNAPSHOT_MAX_BYTES
) -> Dict[str, Any]:
    """请求目标参数快照（~2KB 上限）：按序保留键，直到序列化超限为止。

    供 harness 记录 issued 证据（requested 侧），避免把超大 params 全量落盘。
    """
    if not isinstance(params, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in params.items():
        probe = {**out, k: v}
        try:
            size = len(json.dumps(probe, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            continue
        if size > max_bytes:
            break
        out[k] = v
    return out


@dataclass
class ToolDispatchResult:
    """dispatch 的判别式结果。

    status 是唯一判别量：调用方对它做 match，而不是从一组平行布尔/字符串里
    重新拼装出结论（旧 dispatcher 的 8 字段 dict 正是这种浅形态）。

    - status=="ok"       → geojson_ref 可能为 None（无几何工具）或 ref 串
    - status=="repeated" → 重复调用拦截，geojson_ref 必为 None
    - status=="error"    → error_msg 必有值，geojson_ref 必为 None

    map_actions（V3）：本结果携带的地图动作元数据 [{action_id, command, requested}]，
    action_id 已写回 raw_result 的 command/commands[] dict（SSE 由此携带）。
    """

    status: DispatchStatus
    llm_payload: str            # 给 LLM 看的字符串（任意分支都有值）
    slim_event: dict            # 给前端 SSE 用的脱敏版本（任意分支都有值）
    geojson_ref: Optional[str]  # 仅 status=="ok" 且产出图层时非空
    raw_result: Any             # 原始工具返回，供 step 完成追踪使用
    error_msg: Optional[str]    # 仅 status=="error" 时有值
    map_actions: list = field(default_factory=list)  # V3: [{action_id, command, requested}]
    ref_descriptor: Optional[dict] = None  # V3 Performance: pre-computed alongside geojson_ref


# 重复调用拦截的 LLM 提示（独立常量，避免 ok/error 分支误用）
_REPEAT_LLMPAYLOAD = "[重复调用拦截] {tool} 已在本任务中以相同参数成功执行，结果已生效。请直接基于既有结果汇报，不要再次调用。"

# 并发在飞去重（adversarial P2-9 / recovery P2）：同一波次出现两条相同调用时，
# 原调用可能仍在执行中——此时**绝不谎报"已成功执行"**。软化措辞：告知原调用
# 已发起，让 LLM 以原调用的结果为准。原调用完成后（_completed_keys 命中）仍走
# 上面的成功语义消息（post-success dedup 文案不变）。
_REPEAT_INFLIGHT_LLMPAYLOAD = (
    "[重复调用拦截] {tool} 的同参数调用已在本任务中发起（原调用仍在执行中），"
    "请以原调用的结果为准并据此汇报，不要重复调用。"
)

_DISPLAY_RESULT_METADATA_KEYS = (
    "type", "summary", "algorithm", "analysis_type", "computed_at",
    "warnings", "legend_spec",
)


def _decode_data_url_png(image: str) -> Optional[bytes]:
    """Decode a data-URL PNG. Non-data URLs return None (caller uses the ref)."""
    if not image.startswith("data:image"):
        return None
    _, _, encoded = image.partition(",")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded, validate=False)
    except Exception:
        return None


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
        # Dedup set is shared across concurrent dispatches in the parallel path
        # (chat() gathers multiple tool calls). The in/add on executed_tools must
        # be atomic or two identical calls can both pass the check before either
        # adds. Lock is held only for the microsecond check-and-add, released
        # before the heavy registry dispatch, so it never serializes execution.
        self._dedup_lock = asyncio.Lock()
        # P2-9（adversarial P2-9 / recovery P2）：已完成（非在飞）的同参调用集合。
        # 重复命中时若 key ∈ _completed_keys → post-success 语义（原文案）；
        # 否则视为"并发在飞"→ 不谎报成功的软化文案。有界：超限整体清空只会
        # 把个别后序重复从"成功文案"降级为"在飞文案"，去重本身不受影响。
        self._completed_keys: set[tuple[str, str]] = set()
        self._COMPLETED_KEYS_MAX = 4096

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

        # 1. 重复调用拦截 (并发安全：check-and-add 在锁内原子完成，否则两条并行
        #    dispatch 都会通过 in 检查后才 add，重复调用逃逸拦截)。
        #    design-v3 §2（R-dedup）：先“占位”保证并发同参互斥，但**失败**的调用
        #    会在下方释放占位 —— 失败的同参调用可被 LLM 重试，且绝不会收到
        #    “已成功执行”的重复提示。
        tool_key = (tool_name, normalize_tool_args(tool_args_raw))
        async with self._dedup_lock:
            if tool_key in executed_tools:
                # P2-9：区分「并发在飞」与「已完成」——在飞时绝不谎报成功。
                if tool_key in self._completed_keys:
                    note = _REPEAT_LLMPAYLOAD.format(tool=tool_name)
                else:
                    note = _REPEAT_INFLIGHT_LLMPAYLOAD.format(tool=tool_name)
                # OBSERVABILITY (F5): deduped/repeated calls bypass registry.dispatch
                # and emit no tool_metrics row, so wasted-work from repeats was
                # invisible. Count them on the live turn's evidence accumulator.
                _ev = current_turn_evidence()
                if _ev is not None:
                    _ev.add_deduped_tool_call()
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
        except OperationCancelled:
            # ADR-0052：取消上抛给工具管道处理（它会记成「已取消」而非工具故障）。
            # 取消的调用不占用 dedup 槽位（本轮后续重试不被“已成功”谎言拦截）。
            self._release_key(executed_tools, tool_key)
            raise
        except Exception as e:
            from app.tools._utils import std_error_response
            error_msg = sanitize_error_msg(str(e))
            result = std_error_response(
                error_msg,
                code="TOOL_ERROR",
                error_type=type(e).__name__,
                correction_hint=f"Execution error: {error_msg}",
            )
            self._release_key(executed_tools, tool_key)

        # 3. (#529) 归一化：~139 个工具点以 {"error": "<msg>"} 正常返回失败
        # （不抛异常、不返回 std_error_response 形状），此前只有后者被
        # is_error_dict 识别 → 被当成功处理：标记 completed、同参重试被
        # "已成功执行"谎言拦截、计划跨失败推进。在统一错误分支之前把该形状
        # 折叠成 canonical 失败形状（success=False + code），让下面的错误路径
        # 统一接管（释放 dedup 槽位 → 诚实重试 + 自愈消息）。只折叠 error 为
        # 字符串且非显式 success=True 的结果（is_error_like_result），业务
        # 载荷里嵌套/非字符串的 error 键不受影响。
        if is_error_like_result(result):
            result = dict(result)
            result.setdefault("code", "tool_error")
            result.setdefault("message", result["error"])
            result["success"] = False

        # 3. registry 返回 std_error_response dict 的统一错误路径
        if is_error_dict(result):
            # 失败调用不占用 dedup 槽位：同参重试放行，且不会谎报“已成功执行”。
            self._release_key(executed_tools, tool_key)
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
                map_actions=self._mint_map_action_ids(result),
            )

        # 4. 正常路径：大型 GeoJSON 存为 ref；热力图等元数据落地
        geojson_ref: Optional[str] = None
        heatmap_ref: Optional[str] = None
        ref_descriptor: Optional[dict] = None
        target_data = None
        if isinstance(result, dict):
            if isinstance(result.get("geojson"), (dict, list)):
                target_data = result["geojson"]
            elif result.get("type") == "FeatureCollection" and "features" in result:
                target_data = result
            elif (
                isinstance(result.get("data"), dict)
                and result["data"].get("type") == "FeatureCollection"
                and "features" in result["data"]
            ):
                # #517：to_llm_response() 工具族（~29 站点）返回
                # {success, summary, data: FeatureCollection, ...} 形状，
                # data 包裹的 FC 同样要入 ref store —— 否则分析结果永远
                # 不挂载到地图上，LLM 载荷被裁剪到只剩 summary。
                # 与 kde_contours（顶层 FC）保持同一挂载契约。
                target_data = result["data"]
            if target_data is not None:
                geojson_ref = await session_data_manager.store(session_id, target_data, prefix="geojson")
            if result.get("type") == "heatmap_raster":
                heatmap_ref = await session_data_manager.store(
                    session_id, result, prefix="heatmap"
                )
                result = dict(result)
                result.setdefault("result_ref", heatmap_ref)
            # H-1: a Redis outage makes store() return the unavailable-ref
            # sentinel (``ref:redis-unavailable-…``) per the session-data
            # protocol. Treating that sentinel as a real ref authored a MapSpec
            # layer pointing at a ref with NO payload, marked the call
            # completed, and made the failure unrecoverable by the LLM. Detect
            # it and fail the dispatch truthfully instead.
            if is_unavailable_ref(geojson_ref) or is_unavailable_ref(heatmap_ref):
                self._release_key(executed_tools, tool_key)
                return ToolDispatchResult(
                    status="error",
                    llm_payload=(
                        "会话存储暂时不可用，无法保存分析结果；请稍后重试，无需改变参数。"
                    ),
                    slim_event={
                        "type": "tool_error",
                        "name": tool_name,
                        "error": "session store unavailable",
                    },
                    geojson_ref=None,
                    raw_result=result,
                    error_msg="session store unavailable",
                )
            # Canonical MapSpec layer authoring points at an existing
            # session-owned analysis ref instead of returning the dataset
            # again.  Preserve that stable identity through the existing SSE
            # mount channel without materializing/copying its feature body.
            result_ref = result.get("result_ref")
            is_raster_result = (
                result.get("type") == "heatmap_raster"
                or (
                    isinstance(result_ref, str)
                    and result_ref.startswith("ref:raster/")
                )
            )
            # H-1 (R2-2): tools that store data themselves (e.g.
            # webgis_layer_upsert's inline branch) can hand back the
            # unavailable-ref sentinel as ``result_ref`` — the dispatch-level
            # store() check above cannot see it. Fail truthfully before the
            # sentinel is promoted to geojson_ref and a MapSpec layer /
            # event-log entry / dedup-completed marking latch onto a phantom
            # ref with no payload anywhere.
            if is_unavailable_ref(result_ref):
                self._release_key(executed_tools, tool_key)
                return ToolDispatchResult(
                    status="error",
                    llm_payload=(
                        "会话存储暂时不可用，无法保存分析结果；请稍后重试，无需改变参数。"
                    ),
                    slim_event={
                        "type": "tool_error",
                        "name": tool_name,
                        "error": "session store unavailable",
                    },
                    geojson_ref=None,
                    raw_result=result,
                    error_msg="session store unavailable",
                )
            if (
                isinstance(result_ref, str)
                and result_ref.startswith("ref:")
                and not is_raster_result
            ):
                # The opaque result identity is authoritative even if its
                # optional descriptor cache is temporarily unavailable. The
                # frontend can still mount the owned ref; descriptor absence
                # must not silently drop a successfully authored MapSpec layer.
                # Raster refs are deliberately excluded: their image URL/bbox
                # stay in ``raw_result`` and the frontend raster mount path.
                # Advertising one as ``geojson_ref`` would mount an empty
                # FeatureCollection and falsely ACK the raster as displayed.
                geojson_ref = result_ref
                try:
                    ref_descriptor = await session_data_manager.get_ref_descriptor(
                        session_id, result_ref
                    )
                except Exception:
                    ref_descriptor = None

        # A display-producing GIS result must enter the existing MapSpec
        # lifecycle before the frontend mounts it. This is presentation
        # authoring only: the source analysis is never re-run and the dataset
        # remains behind its session-owned ref.
        if (
            target_data is not None
            and geojson_ref
            and tool_name != "webgis_layer_upsert"
            and isinstance(result, dict)
        ):
            if ref_descriptor is None:
                ref_descriptor = await session_data_manager.get_ref_descriptor(
                    session_id, geojson_ref
                )
            result = await self._author_display_result(
                session_id=session_id,
                tool_call_id=str(tc.get("id") or "result"),
                tool_name=tool_name,
                result=result,
                target_data=target_data,
                result_ref=geojson_ref,
                descriptor=ref_descriptor,
            )
        elif (
            tool_name != "webgis_layer_upsert"
            and isinstance(result, dict)
            and (
                result.get("type") == "heatmap_raster"
                or (
                    isinstance(result.get("result_ref"), str)
                    and result["result_ref"].startswith("ref:raster/")
                    and (result.get("bbox") or result.get("bounds"))
                )
            )
        ):
            # Raster/heatmap display results stay off the GeoJSON mount path
            # (an empty FeatureCollection must never ACK them). They still
            # enter the same MapSpec desired-state review as vector results.
            result = await self._author_raster_display_result(
                session_id=session_id,
                tool_call_id=str(tc.get("id") or "result"),
                tool_name=tool_name,
                result=result,
            )

        # Map actions are minted only after automatic MapSpec authoring has
        # attached its canonical runtime commands.
        map_actions = self._mint_map_action_ids(result)

        # 5. 给 LLM 的载荷（压缩 + 可选自愈提示）
        # PERF-F1: the full json.dumps ran unconditionally on the event loop —
        # 0.6-4s for 100k-feature results, and slim_tool_result discards it
        # whenever the payload exceeds MSG_MAX_CHARS anyway. Gate on the cheap
        # structural size estimate; only small payloads pay the real dumps.
        if isinstance(result, str):
            result_str = result
        else:
            from app.tools.registry import _estimate_json_bytes

            _est = _estimate_json_bytes(result)
            if _est <= 4096:  # comfortably under MSG_MAX_CHARS even with slack
                result_str = json.dumps(result, ensure_ascii=False)
            elif isinstance(result, dict) and "summary" in result:
                # slim_tool_result's summary branch never reads result_str.
                result_str = ""
            else:
                # Oversized without a summary: serialize OFF the event loop
                # (review P3 — the space-marker fallback fed the LLM blanks
                # for oversized non-dict results like bare lists).
                result_str = await asyncio.to_thread(
                    json.dumps, result, ensure_ascii=False
                )
        llm_payload = slim_tool_result(result, result_str, geojson_ref) or result_str
        if is_suspicious_result(result):
            llm_payload += (
                "\n\n(注意: 此操作未返回任何空间要素或有效数据。请检查查询范围、关键词或图层名称，"
                "并根据需要尝试不同的参数。不要重复完全相同的调用。)"
            )

        # 6. event_log 回写 + WS 广播
        await self._record_event(tc, session_id, tool_name, result, geojson_ref)

        # V3 Performance: compute the descriptor once, here, inside the async
        # dispatch path — this is the ONLY place both call sites (ChatEngine's
        # execution_engine.py and the Pi bridge's pi_event_mapper.py) can reach
        # without a redundant async round trip. Fetching it here means
        # pi_event_mapper never needs asyncio.run() (which would raise inside
        # the already-running FastAPI event loop and get silently swallowed).
        if geojson_ref and ref_descriptor is None:
            try:
                ref_descriptor = await session_data_manager.get_ref_descriptor(session_id, geojson_ref)
            except Exception:
                pass  # non-fatal: frontend falls back to full download

        # P2-9：成功完成 → 标记 completed（后续同参重复走 post-success 文案）。
        self._mark_completed(tool_key)

        return ToolDispatchResult(
            status="ok",
            llm_payload=llm_payload,
            slim_event=slim_event_result(result),
            geojson_ref=geojson_ref,
            raw_result=result,
            error_msg=None,
            map_actions=map_actions,
            ref_descriptor=ref_descriptor,
        )

    async def _author_display_result(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Dict[str, Any],
        target_data: Any,
        result_ref: str,
        descriptor: Optional[dict],
    ) -> Dict[str, Any]:
        """Project one already-computed vector result into canonical MapSpec.

        Failure is additive evidence: L1 GIS execution remains successful, but
        the result is explicitly not cartographically evaluated and receives no
        fabricated runtime attestation.
        """
        from app.lib.cartography.quality_loop import cartographic_fingerprint
        from app.services.analysis_cartography_converter import (
            convert_analysis_to_mapspec_layer,
        )
        from app.services.mapspec_store import mapspec_store
        from app.services.spatial_meta_profiler import profile_geojson_source
        from app.tools.cartography_tools import _fingerprint_metadata, _runtime_patch

        safe_call_id = re.sub(r"[^A-Za-z0-9_-]+", "-", tool_call_id).strip("-")[:48]
        layer_id = f"result-{safe_call_id or hashlib.sha256(tool_call_id.encode()).hexdigest()[:12]}"
        source_id = f"{layer_id}-source"
        analysis_payload = {
            "geojson": target_data,
            "legend_spec": result.get("legend_spec"),
            "algorithm": tool_name,
            "result_ref": result_ref,
        }
        try:
            converted_layer, _, conversion_warnings = await asyncio.to_thread(
                convert_analysis_to_mapspec_layer,
                analysis_payload,
                {
                    "id": layer_id,
                    "source": source_id,
                    "provenance": {
                        "tool_call_id": tool_call_id,
                        "result_ref": result_ref,
                    },
                },
            )
            profile = await asyncio.to_thread(profile_geojson_source, target_data)
            source_data = {
                "type": "geojson",
                "ref_id": result_ref,
                "profile": profile,
                "profile_fingerprint": _fingerprint_metadata(profile, "profile"),
                "data_fingerprint": _fingerprint_metadata(
                    {"ref_id": result_ref, "descriptor": descriptor or {}}, "data"
                ),
            }
            lifecycle = await mapspec_store.layer_upsert(
                session_id, converted_layer, source_data
            )
            if not lifecycle.get("success"):
                raise RuntimeError(
                    str(lifecycle.get("message") or "MapSpec authoring rejected")
                )
            mapspec = lifecycle.get("mapspec") if isinstance(lifecycle.get("mapspec"), dict) else {}
            reviewed_layer = next(
                (
                    layer for layer in mapspec.get("layers", [])
                    if isinstance(layer, dict) and layer.get("id") == layer_id
                ),
                converted_layer,
            )
            attempts = (
                (lifecycle.get("cartographic_review") or {}).get("attempts", [])
                if isinstance(lifecycle.get("cartographic_review"), dict) else []
            )
            patch = _runtime_patch(
                reviewed_layer,
                result_ref,
                lifecycle.get("mapspec_fingerprint"),
                attempts if isinstance(attempts, list) else [],
            )
            # The ref is now the sole carrier. Keeping the raw body in the tool
            # outcome would serialize and retain a second large copy.
            if result.get("geojson") is target_data:
                result = {k: v for k, v in result.items() if k != "geojson"}
            elif result is target_data:
                result = {
                    key: result[key]
                    for key in _DISPLAY_RESULT_METADATA_KEYS
                    if key in result
                }
                result["type"] = "FeatureCollection"
                result["feature_count"] = profile.get("featureCount")
            result.update({
                "success": True,
                "result_ref": result_ref,
                "layer_id": layer_id,
                "runtime_patch": patch,
                "runtime_projection_fingerprint": patch["projection_fingerprint"],
                "commands": [{
                    "command": "add_layer",
                    "params": {
                        "layerId": layer_id,
                        "result_ref": result_ref,
                        "mapspec_fingerprint": lifecycle.get("mapspec_fingerprint"),
                    },
                }],
                "conversion_warnings": conversion_warnings,
            })
            for key in (
                "is_compiled", "warnings", "checkpoint_id", "cartography_findings",
                "cartographic_review", "mapspec_fingerprint",
                "runtime_observation_seq", "mutation_revision",
            ):
                if lifecycle.get(key) is not None:
                    result[key] = lifecycle[key]
            # Defensive consistency check: never attach a runtime generation
            # whose fingerprint is not the just-persisted desired state.
            if result.get("mapspec_fingerprint") != cartographic_fingerprint(mapspec):
                raise RuntimeError("persisted MapSpec fingerprint mismatch")
            return result
        except Exception as exc:  # noqa: BLE001 - preserve completed analysis
            logger.warning(
                "Cartographic authoring unavailable for %s/%s: %s",
                session_id,
                tool_call_id,
                type(exc).__name__,
            )
            # L1 execution remains available, but never keep/serialize a
            # second full feature body merely because presentation authoring
            # failed.  The already-stored owned ref remains the carrier.
            if result.get("geojson") is target_data:
                result = {k: v for k, v in result.items() if k != "geojson"}
            elif result is target_data:
                feature_count = (
                    len(target_data.get("features", []))
                    if isinstance(target_data.get("features"), list)
                    else None
                )
                result = {
                    key: result[key]
                    for key in _DISPLAY_RESULT_METADATA_KEYS
                    if key in result
                }
                result.update({
                    "type": "FeatureCollection",
                    "feature_count": feature_count,
                    "result_ref": result_ref,
                })
            else:
                result = dict(result)
            result["cartographic_review"] = self._authoring_unavailable_review(exc)
            return result

    async def _author_raster_display_result(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Project a rendered raster/heatmap into canonical MapSpec review.

        The GIS image/bbox is already computed. This never re-runs density
        analysis and never advertises the raster as a GeoJSON mount.
        """
        from app.lib.cartography.quality_loop import cartographic_fingerprint
        from app.services.mapspec.store import mapspec_store_instance
        from app.services.mapspec_store import mapspec_store
        from app.services.raster_store import save_png
        from app.tools.cartography_tools import _runtime_patch

        safe_call_id = re.sub(r"[^A-Za-z0-9_-]+", "-", tool_call_id).strip("-")[:48]
        layer_id = f"raster-{safe_call_id or hashlib.sha256(tool_call_id.encode()).hexdigest()[:12]}"
        source_id = f"{layer_id}-source"
        bounds = result.get("bbox") or result.get("bounds")
        image = result.get("image") or result.get("imageRef")
        result_ref = result.get("result_ref")
        try:
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                raise RuntimeError("raster display result has no truthful bounds")
            png = _decode_data_url_png(image) if isinstance(image, str) else None
            if png is not None:
                session_dir = mapspec_store_instance.get_session_dir(session_id)
                image_ref = save_png(session_dir, source_id, png)
            elif isinstance(image, str) and image.startswith("ref:"):
                image_ref = image
            elif isinstance(result_ref, str) and result_ref.startswith("ref:"):
                image_ref = result_ref
            else:
                raise RuntimeError("raster display result has no addressable image")
            layer = {
                "id": layer_id,
                "source": source_id,
                "type": "raster",
                "paint": {"raster-opacity": 0.85},
                "provenance": {
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "result_ref": image_ref,
                },
            }
            if isinstance(result.get("legend_spec"), dict):
                layer["legend_spec"] = result["legend_spec"]
            source_data = {
                "imageRef": image_ref,
                "bounds": [float(v) for v in bounds],
            }
            lifecycle = await mapspec_store.layer_upsert(
                session_id, layer, source_data
            )
            if not lifecycle.get("success"):
                raise RuntimeError(
                    str(lifecycle.get("message") or "MapSpec raster authoring rejected")
                )
            mapspec = lifecycle.get("mapspec") if isinstance(lifecycle.get("mapspec"), dict) else {}
            reviewed_layer = next(
                (
                    candidate for candidate in mapspec.get("layers", [])
                    if isinstance(candidate, dict) and candidate.get("id") == layer_id
                ),
                layer,
            )
            attempts = (
                (lifecycle.get("cartographic_review") or {}).get("attempts", [])
                if isinstance(lifecycle.get("cartographic_review"), dict) else []
            )
            patch = _runtime_patch(
                reviewed_layer,
                image_ref,
                lifecycle.get("mapspec_fingerprint"),
                attempts if isinstance(attempts, list) else [],
            )
            authored = {
                key: result[key]
                for key in (*_DISPLAY_RESULT_METADATA_KEYS, "bbox", "bounds", "total_points")
                if key in result
            }
            authored.update({
                "type": result.get("type") or "heatmap_raster",
                "success": True,
                "result_ref": image_ref,
                "layer_id": layer_id,
                "runtime_patch": patch,
                "runtime_projection_fingerprint": patch["projection_fingerprint"],
                "command": result.get("command") or "add_heatmap_raster",
                "commands": [{
                    "command": "add_heatmap_raster",
                    "params": {
                        "layerId": layer_id,
                        "result_ref": image_ref,
                        "mapspec_fingerprint": lifecycle.get("mapspec_fingerprint"),
                        "bbox": authored.get("bbox") or authored.get("bounds"),
                    },
                }],
            })
            for key in (
                "is_compiled", "warnings", "checkpoint_id", "cartography_findings",
                "cartographic_review", "mapspec_fingerprint",
                "runtime_observation_seq", "mutation_revision",
            ):
                if lifecycle.get(key) is not None:
                    authored[key] = lifecycle[key]
            if authored.get("mapspec_fingerprint") != cartographic_fingerprint(mapspec):
                raise RuntimeError("persisted MapSpec fingerprint mismatch")
            return authored
        except Exception as exc:  # noqa: BLE001 - preserve completed raster analysis
            logger.warning(
                "Cartographic raster authoring unavailable for %s/%s: %s",
                session_id,
                tool_call_id,
                type(exc).__name__,
            )
            authored = {
                key: result[key]
                for key in (*_DISPLAY_RESULT_METADATA_KEYS, "bbox", "bounds", "result_ref", "command")
                if key in result
            }
            authored["type"] = result.get("type") or "heatmap_raster"
            authored["cartographic_review"] = self._authoring_unavailable_review(exc)
            return authored

    @staticmethod
    def _authoring_unavailable_review(exc: Exception) -> Dict[str, Any]:
        return {
            "stage": "desired_state",
            "status": "not_evaluated",
            "review": {
                "status": "not_evaluated",
                "passed": False,
                "complete": False,
                "checks": [{
                    "rule": "MAPSPEC_AUTHORING",
                    "status": "not_evaluated",
                    "severity": "error",
                    "evidence_class": "deterministic",
                    "evidence": {"error_type": type(exc).__name__},
                    "repairability": "not_repairable",
                }],
            },
            "termination_reason": "mapspec_authoring_unavailable",
        }

    # ── P2-9: dedup 槽位生命周期 ─────────────────────────────────────

    def _release_key(self, executed_tools: set, tool_key: tuple[str, str]) -> None:
        """失败/取消的调用释放 dedup 槽位（同参可重试），并清掉 completed 标记。

        ``executed_tools.discard`` / ``_completed_keys.discard`` 均为 set 原子
        操作（GIL），无需持锁；check-and-add 的原子性由 _dedup_lock 保证。
        """
        executed_tools.discard(tool_key)
        self._completed_keys.discard(tool_key)

    def _mark_completed(self, tool_key: tuple[str, str]) -> None:
        """成功完成的调用标记为 completed（post-success dedup 语义）。"""
        self._completed_keys.add(tool_key)
        if len(self._completed_keys) > self._COMPLETED_KEYS_MAX:
            self._completed_keys.clear()

    # ── V3: 地图动作 action_id 铸造 ──────────────────────────────────────

    def _mint_map_action_ids(self, result: Any) -> list:
        """为工具结果中的地图命令铸 action_id（写入 command dict，SSE 携带）。

        两种形态（前端 useMapBridge 消费契约一致）：
        - ``result.command``（单命令：result 本身即 command dict，如 fly_to /
          export_map / set_map_view）→ 在 result 顶层写入 action_id；
        - ``result.commands[]``（批量命令：如 export_batch_maps）→ 逐条写入。

        同时构造尺寸受限的 requested 参数快照（~2KB），供 harness 记录 issued
        证据。返回 [{action_id, command, requested}]。
        """
        if not isinstance(result, dict):
            return []
        minted: list = []
        commands = result.get("commands")
        if isinstance(commands, list):
            for cmd in commands:
                if isinstance(cmd, dict):
                    entry = self._mint_one_map_action(cmd)
                    if entry is not None:
                        entry["mapspec_fingerprint"] = result.get("mapspec_fingerprint")
                        minted.append(entry)
            return minted
        if result.get("command"):
            entry = self._mint_one_map_action(result)
            if entry is not None:
                entry["mapspec_fingerprint"] = result.get("mapspec_fingerprint")
                minted.append(entry)
        return minted

    @staticmethod
    def _mint_one_map_action(command_dict: Dict[str, Any]) -> Optional[dict]:
        """给单个 command dict 铸 action_id（每次派发都铸新 id），返回元数据。

        P2 (F25): 不再复用 command dict 里已存在的 action_id。原实现「已存在则
        复用」在工具返回缓存/持久化的 command 对象跨 turn 重放时会复用旧 id：
        两次真实执行（如重新飞一次/重新导出）共用一个 action_id，ACK store 的
        first-terminal-wins 会把第二次执行的真实终态当作重复丢弃 —— 下游遥测
        无法区分两次真实副作用。每次派发铸新 id 后：同 turn 内去重（
        `_session_executed_sets`）保证同一 tool_call 不会重复派发；跨 turn 的
        陈旧 id 不再可能碰撞。前端以 SSE 事件里携带的 action_id 为准，行为不变。
        """
        cmd_name = command_dict.get("command") or command_dict.get("type") or ""
        if not cmd_name:
            return None
        action_id = _mint_map_action_id()
        command_dict["action_id"] = action_id
        requested = _cap_requested_snapshot(command_dict.get("params") or {})
        return {
            "action_id": action_id,
            "command": str(cmd_name),
            "requested": requested,
        }

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
        # #529: the {"error": <str>} shape is a failure, not an empty/suspicious
        # success — classify it so standalone consumers (engine
        # _detect_suspicious_result / decision logging) don't treat it as
        # success. (The dispatch error branch handles it before this point.)
        if is_tool_error_result(result):
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
