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
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional

from app.services.session_data import session_data_manager
from app.lib.numpy_json import numpy_json_default as _numpy_json_default
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
    # ADR-0052 parity（Pi 兼容）：本次执行期间创建的 durable job id ——
    # legacy 由 tool_pipeline 从 JobOrigin.created_job_ids 回读；Pi dispatch
    # 直调后由桥接层写入。SSE step_result 携带（前端 job 关联）。
    background_job_ids: list = field(default_factory=list)


# 重复调用拦截的 LLM 提示（独立常量，避免 ok/error 分支误用）
# audit4 #984: 同参重复拦截的诚实措辞 —— 结果是**先前调用**的产物，地图/数据
# 上下文在本回合内可能已变化（图层增删、map_state 更新）。旧文案的「结果已
# 生效/直接汇报」成功口吻会让模型把陈旧前提当现势数据引用。
_REPEAT_LLMPAYLOAD = (
    "[重复调用拦截] {tool} 已在本任务中以相同参数执行过，本次未重新执行。"
    "注意：结果是先前调用时的状态，若其后地图/图层/数据已变化，请微调参数"
    "（如调整范围、更换引用）后重新调用；否则可直接引用既有结果。"
)

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


class _MultiSlotAcquire:
    """#1062: 在一个 asyncio.Semaphore 上按槽位数获取/释放的 async 上下文。

    asyncio.Semaphore 没有参数化 acquire(n)；heavy 工具（cost=heavy）占
    2 槽、其余 1 槽，逐槽获取语义等价。异常路径由 __aexit__ 统一释放
    已获取的槽，保证不泄漏。
    """

    def __init__(self, sem: asyncio.Semaphore, slots: int = 1):
        self._sem = sem
        self._slots = max(1, int(slots))
        self._held = 0

    async def __aenter__(self) -> "_MultiSlotAcquire":
        for _ in range(self._slots):
            await self._sem.acquire()
            self._held += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        for _ in range(self._held):
            self._sem.release()
        self._held = 0


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
        # #909 wave concurrency bound: ASYNC + THREAD both burst through here
        # under an LLM round that emits 12-20 concurrent tool_calls. Registry's
        # _TOOL_THREAD_LIMIT only gates sync work; this gate bounds the wave.
        _wave_limit = max(1, int(os.getenv("TOOL_WAVE_CONCURRENCY") or os.getenv("TOOL_CONCURRENCY_LIMIT") or "5"))
        self._wave_semaphore = asyncio.Semaphore(_wave_limit)
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
        # Pi 兼容审查（NIT 修复）：共享 service 实例跨会话（Pi 桥接层 +
        # legacy 引擎同款）—— completed 标记必须带会话维，否则另一会话的
        # 同参调用会把「并发在飞」误报成「已成功执行」（executed_tools 由
        # 调用方按会话/回合自持，不受影响）。
        completed_key = (session_id or "", tool_key)
        async with self._dedup_lock:
            if tool_key in executed_tools:
                # P2-9：区分「并发在飞」与「已完成」——在飞时绝不谎报成功。
                if completed_key in self._completed_keys:
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

        # 1.5 (V2 P10) analysis reuse —— artifact 层确定性复用。ref: 参数
        # 是 cached_tool 的正确性盲区（ref 可变 → 拒缓存），本层靠
        # ArtifactRegistry 状态 + descriptor 形状指纹补齐复用语义。仅对
        # registry 认定的分析工具生效；任何不可判定 → miss 照常执行
        # （纯加速，绝不因复用逻辑改变失败语义）。GIS_ANALYSIS_REUSE=0
        # 整体关闭（复用查询只读，关闭只影响命中）。
        analysis_key: Optional[str] = None
        input_shapes: Optional[Dict[str, dict]] = None
        reused_result: Optional[Dict[str, Any]] = None
        if os.getenv("GIS_ANALYSIS_REUSE", "1") != "0":
            try:
                from app.lib.gis.algorithm_registry import get_algorithm_registry
                from app.lib.gis.analysis_reuse import (
                    compute_analysis_key,
                    find_reusable_artifact,
                    snapshot_input_shapes,
                )

                if tool_name in get_algorithm_registry().tool_to_capability():
                    try:
                        _parsed = (
                            tool_args_raw
                            if isinstance(tool_args_raw, (dict, list))
                            else json.loads(tool_args_raw)
                        )
                    except (json.JSONDecodeError, TypeError):
                        _parsed = None
                    if _parsed is not None:
                        analysis_key = compute_analysis_key(tool_name, _parsed)
                        if analysis_key is not None:
                            input_shapes = await snapshot_input_shapes(session_id, _parsed)
                            raster_fps = None
                            try:
                                from app.lib.gis.analysis_reuse import (
                                    snapshot_raster_fingerprints,
                                )

                                raster_fps = snapshot_raster_fingerprints(_parsed) or None
                            except Exception:  # noqa: BLE001
                                raster_fps = None
                            reuse = await find_reusable_artifact(
                                session_id,
                                analysis_key=analysis_key,
                                input_shapes=input_shapes,
                                raster_fingerprints=raster_fps,
                            )
                            if reuse:
                                _fc = reuse.get("feature_count")
                                reused_result = {
                                    "success": True,
                                    "summary": (
                                        f"分析复用：本调用的工具、参数与输入引用与既有产物 "
                                        f"{reuse['artifact_id']} 完全一致（analysis key 相同且输入未变），"
                                        f"本次未重复计算，直接复用该产物。"
                                    ),
                                    "ref_id": reuse["artifact_id"],
                                    "reused": True,
                                    "feature_count": _fc,
                                }
                                if reuse.get("bbox"):
                                    reused_result["bbox"] = reuse["bbox"]
            except Exception:  # noqa: BLE001 — 复用是纯加速：查找失败绝不阻塞执行
                logger.debug(
                    "[AnalysisReuse] lookup skipped tool=%s", tool_name, exc_info=True
                )

        # 2. 执行（registry 内部全权处理 ref 解析、校验、异常捕获与自愈）
        # #909 wave bound: the registry call (+ store + MapSpec authoring below) is
        # the blast radius. Guard it behind _wave_semaphore so an LLM round that
        # emits 12-20 concurrent tool_calls cannot burst the thread pool / Redis /
        # external APIs. _dedup_lock is already released, so only execution is gated.
        if reused_result is not None:
            result: Dict[str, Any] = reused_result
        else:
            try:
                # #1062: 消费 ToolCost 元数据（#996 承诺的「wave 并发可按档分桶」
                # 的最小落地）—— heavy 工具占 2 个并发槽，避免同波多个 heavy
                # 分析挤占全部 wave 预算让轻工具排队。light/medium 占 1 槽不变。
                # registry duck-typing（workflow 测试的 _FakeRegistry 等不实现
                # metadata —— 视为 light，与 metadata() 的未注册兜底一致）。
                _meta_fn = getattr(self._registry, "metadata", None)
                _cost = _meta_fn(tool_name).get("cost") if callable(_meta_fn) else None
                _wave_slots = 2 if _cost == "heavy" else 1
                async with _MultiSlotAcquire(self._wave_semaphore, _wave_slots):
                    result = await self._registry.dispatch(tool_name, tool_args_raw, session_id=session_id)
            except OperationCancelled:
                # ADR-0052：取消上抛给工具管道处理（它会记成「已取消」而非工具故障）。
                # 取消的调用不占用 dedup 槽位（本轮后续重试不被“已成功”谎言拦截）。
                self._release_key(executed_tools, tool_key, session_id or "")
                raise
            except asyncio.CancelledError:
                # #946/#1060：asyncio.CancelledError 是 BaseException，不会命中上面
                # 两个 handler —— 硬取消（task.cancel()）若不在此外释放占位键，
                # 本 turn 内同参重试会一直收到「并发在飞」谎言（实际无结果）。
                self._release_key(executed_tools, tool_key, session_id or "")
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
                self._release_key(executed_tools, tool_key, session_id or "")

        # 3. (#529/#589) 归一化：~160 个工具点以失败形状正常返回（不抛异常、
        # 不返回 std_error_response 形状），此前只有 std_error_response 被
        # is_error_dict 识别 → 被当成功处理：标记 completed、同参重试被
        # "已成功执行"谎言拦截、计划跨失败推进。在统一错误分支之前把失败
        # 形状折叠成 canonical 失败形状（success=False + code），让下面的
        # 错误路径统一接管（释放 dedup 槽位 → 诚实重试 + 自愈消息）。
        # 识别三族错误形态（is_error_like_result）：{"error": <str>}（#529）、
        # {"type": "error", ...} 与 {"status": "error"|"failed", ...}（#589，
        # network/temporal/spatial_decision/project 等站点的正常返回失败）。
        # message 从各形态的 message/error 字段取——type/status 形态不带
        # "error" 键，不得下标访问。
        if is_error_like_result(result):
            result = dict(result)
            result.setdefault("code", "tool_error")
            # summary 兜底：GeoAnalysisResult 失败族把失败原因放在 summary
            # （无 error/message 键）—— 不回填则 LLM 收到空失败原因。
            _summary = result.get("summary")
            result.setdefault(
                "message",
                result.get("error")
                or result.get("error_message")
                or (_summary if isinstance(_summary, str) and _summary else None)
                or f"{tool_name} 执行失败",
            )
            result["success"] = False

        # 3. registry 返回 std_error_response dict 的统一错误路径
        if is_error_dict(result):
            # 失败调用不占用 dedup 槽位：同参重试放行，且不会谎报“已成功执行”。
            self._release_key(executed_tools, tool_key, session_id or "")
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
        if reused_result is not None:
            # 复用命中：产物 ref 已在 find_reusable_artifact 内探测存活，
            # 直接作为 geojson_ref 走既有 descriptor/事件通道（不重跑
            # MapSpec 显示授权 —— 该产物的显示已由首次执行授权过，重放
            # 会重复挂层）。
            geojson_ref = reused_result["ref_id"]
        elif isinstance(result, dict):
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
                self._release_key(executed_tools, tool_key, session_id or "")
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
            # P1（ADR-0082）：产物铸造即登记（dispatch seam 只登记 ref 与
            # 工具名；plan-apply seam 稍后补充 capability/lineage 并 upsert）。
            try:
                from app.services.artifact_registry import register_tool_artifact

                raster_fps = None
                if _parsed is not None:
                    from app.lib.gis.analysis_reuse import snapshot_raster_fingerprints

                    try:
                        raster_fps = snapshot_raster_fingerprints(_parsed) or None
                    except Exception:  # noqa: BLE001 — 指纹是增值记录，绝不阻塞
                        raster_fps = None
                for minted in (geojson_ref, heatmap_ref):
                    if isinstance(minted, str) and minted.startswith("ref:"):
                        await register_tool_artifact(
                            session_id,
                            minted,
                            tool=tool_name,
                            result=result if isinstance(result, dict) else None,
                            analysis_key=analysis_key,
                            input_shapes=input_shapes,
                            raster_fingerprints=raster_fps,
                        )
            except Exception:  # noqa: BLE001 — 登记失败不影响产物本身
                logger.debug(
                    "[ArtifactRegistry] dispatch registration skipped tool=%s",
                    tool_name,
                    exc_info=True,
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
                self._release_key(executed_tools, tool_key, session_id or "")
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
                # #1061(b): 此处若不守卫，损坏的 descriptor（JSONDecodeError 等
                # 非 RedisError 异常）会在工具已成功执行、ref 已落库之后令整个
                # dispatch 抛错 —— LLM 被告知重试一个副作用已发生的工具。
                # 与 :469-474 一致按「descriptor 缺失」处理。
                try:
                    ref_descriptor = await session_data_manager.get_ref_descriptor(
                        session_id, geojson_ref
                    )
                except Exception:
                    ref_descriptor = None
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
                result_str = json.dumps(
                    result, ensure_ascii=False, default=_numpy_json_default
                )
            elif isinstance(result, dict) and "summary" in result:
                # slim_tool_result's summary branch never reads result_str.
                result_str = ""
            else:
                # Oversized without a summary: serialize OFF the event loop
                # (review P3 — the space-marker fallback fed the LLM blanks
                # for oversized non-dict results like bare lists).
                result_str = await asyncio.to_thread(
                    json.dumps, result, ensure_ascii=False,
                    default=_numpy_json_default,
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
        self._mark_completed(tool_key, session_id or "")

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
        from app.services.spatial_meta_profiler import profile_geojson_source, profile_from_descriptor
        from app.tools.cartography_tools import _fingerprint_metadata, _runtime_patch

        # #688：descriptor 命中则 O(1) 派生 profile（store 时已算好的
        # bbox/feature_count/geometry_types——授权消费面：view 注入/图层
        # 类型/指纹），零全量遍历；descriptor 缺失或不完整才降级全量
        # profile_geojson_source（字段直方图等富信息场景）。payload 与
        # source_data 共用同一份（converter 据此零遍历推断几何/点数）。
        profile = (
            await asyncio.to_thread(profile_from_descriptor, descriptor)
            if descriptor
            else None
        )
        if profile is None:
            profile = await asyncio.to_thread(profile_geojson_source, target_data)

        safe_call_id = re.sub(r"[^A-Za-z0-9_-]+", "-", tool_call_id).strip("-")[:48]
        layer_id = f"result-{safe_call_id or hashlib.sha256(tool_call_id.encode()).hexdigest()[:12]}"
        source_id = f"{layer_id}-source"
        analysis_payload = {
            "geojson": target_data,
            # #688 收尾：把已派生的 profile 传入 converter——几何类别/点数
            # 零遍历可得，converter 不再对大 FC 自行遍历（原扫描 3）。
            # 复用上方 profile 局部量（descriptor 派生或全量降级的结果，
            # 均携带 featureCount/geometryTypes）。
            "profile": profile,
            "legend_spec": result.get("legend_spec"),
            "algorithm": tool_name,
            "result_ref": result_ref,
            # 热力图等带渲染意图的工具：type_hint 驱动图层类型推断（点要素
            # 默认推断 circle，type_hint=heatmap 才落 heatmap 层）；metadata
            # 携带 palette/radius 供官方范式 paint 授权。
            "type_hint": result.get("type_hint"),
            "metadata": result.get("metadata")
            if isinstance(result.get("metadata"), dict) else None,
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
            # profile 已在 analysis_payload 构建前派生——payload 与
            # source_data 共用同一份（#688 收尾）。
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
            elif isinstance(result, dict) and result.get("data") is target_data:
                # #798: the ~29 to_llm_response tools returning the #517
                # data-wrapped FC shape fell through both branches above, so
                # the full feature body rode into raw_result → PiToolResponse
                # .details (~1MiB per callback at the 5000-feature cap) and
                # into the dispatch cache. The ref stays the carrier here too;
                # SSE's slim_event_result already excludes "data".
                result = {k: v for k, v in result.items() if k != "data"}
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
                # #735: the authoritative doc must ride the step_result —
                # slim_event_result projects it to bounded metadata, and the
                # frontend commits that projection, advancing the committed
                # MapSpec. Without it, every plain-analysis layer was
                # source-only and invisible in any session holding a
                # committed spec (agent narrated success, map unchanged).
                "mapspec": mapspec,
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
            elif isinstance(result, dict) and result.get("data") is target_data:
                # #1061(a): #798 只修了成功路径——data-wrapped FC（#517 形族）
                # 在 authoring 失败时全量要素体此前照旧进入 raw_result /
                # dispatch 缓存 / tracker。镜像成功分支的剥离，并让 LLM
                # 知道结果落在哪个 ref（失败分支其余形状均有 result_ref）。
                result = {k: v for k, v in result.items() if k != "data"}
                result["result_ref"] = result_ref
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
            # #716: the analysis succeeded but the cartographic half did NOT —
            # surface it to the LLM (correction_hint + warnings) instead of a
            # bare success whose map was never mounted, so the model can retry
            # authoring explicitly and the result is marked suspicious-adjacent
            # for downstream consumers.
            result["success"] = True  # L1 analysis remains available…
            result.setdefault("warnings", [])
            if isinstance(result["warnings"], list):
                result["warnings"].append(
                    "cartographic authoring failed — the layer was NOT mounted; "
                    "retry with webgis_layer_upsert"
                )
            result["correction_hint"] = (
                f"地图挂载失败（{type(exc).__name__}）：分析结果已保存为 ref，"
                "但图层未写入 MapSpec。请调用 webgis_layer_upsert 以完成挂载。"
            )
            result["cartographic_authoring_failed"] = True
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
            # P1（ADR-0082）：栅格产物登记（ref:raster/*；capability 上下文
            # 由 plan-apply seam 的 density 行补充）。
            try:
                from app.services.artifact_registry import register_tool_artifact

                if isinstance(image_ref, str) and image_ref.startswith("ref:"):
                    await register_tool_artifact(
                        session_id, image_ref, tool=tool_name, result=result
                    )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "[ArtifactRegistry] raster registration skipped tool=%s",
                    tool_name,
                    exc_info=True,
                )
            # #533: 把可寻址的 image URL 放回 authored + 命令 params。此前
            # authoring 把 producer 的 data-URL image 剥掉后，命令 params 里
            # 没有 image → 前端 add_heatmap_raster 校验器拒绝（invalid_params）
            # 且 auto-mount gate 不满足，图层永不挂载。复用 webgis_layer_upsert
            # 的同一 seam：ref:raster/<id> → /api/v1/sessions/{sid}/raster/<id>.png
            # （匿名会话的 owner_token 以查询参数附加 —— MapLibre 图片请求带不了
            # 请求头，路由要求所有权校验）。
            image_url = None
            if isinstance(image_ref, str) and image_ref.startswith("ref:raster/"):
                raster_id = image_ref[len("ref:raster/"):]
                image_url = f"/api/v1/sessions/{session_id}/raster/{raster_id}.png"
                try:
                    from app.services.session_ownership import lookup_session_owner_token
                    session_token = await lookup_session_owner_token(session_id)
                except Exception:
                    session_token = None
                if session_token:
                    image_url = f"{image_url}?token={session_token}"
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
            if image_url is not None:
                authored["image"] = image_url
            authored.update({
                "type": result.get("type") or "heatmap_raster",
                "success": True,
                "result_ref": image_ref,
                "layer_id": layer_id,
                # #735: same committed-doc advancement as the vector path.
                "mapspec": mapspec,
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
                        **({"image": image_url} if image_url is not None else {}),
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
            # #716: same honesty contract as the vector path — the raster
            # analysis result is preserved but the layer was NOT mounted.
            authored.setdefault("warnings", [])
            if isinstance(authored["warnings"], list):
                authored["warnings"].append(
                    "cartographic authoring failed — the layer was NOT mounted; "
                    "retry with webgis_layer_upsert"
                )
            authored["correction_hint"] = (
                f"栅格图层挂载失败（{type(exc).__name__}）：结果已保存为 ref，"
                "但图层未写入 MapSpec。请调用 webgis_layer_upsert 以完成挂载。"
            )
            authored["cartographic_authoring_failed"] = True
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

    def _release_key(self, executed_tools: set, tool_key: tuple[str, str], session_id: str = "") -> None:
        """失败/取消的调用释放 dedup 槽位（同参可重试），并清掉 completed 标记。

        ``executed_tools.discard`` / ``_completed_keys.discard`` 均为 set 原子
        操作（GIL），无需持锁；check-and-add 的原子性由 _dedup_lock 保证。
        """
        executed_tools.discard(tool_key)
        self._completed_keys.discard((session_id or "", tool_key))

    def _mark_completed(self, tool_key: tuple[str, str], session_id: str = "") -> None:
        """成功完成的调用标记为 completed（post-success dedup 语义；会话维键）。"""
        self._completed_keys.add((session_id or "", tool_key))
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
        # MapSpec 封装会剥掉 features、只留 result_ref + feature_count。
        # 若仍按「无 features 即空」判断，本地 OSM/行政区成功也会被标 empty，
        # 模型就会再去调高德 search_poi。
        ref = result.get("result_ref") or result.get("ref")
        n = result.get("feature_count")
        if n is None:
            n = result.get("count")
        if isinstance(ref, str) and ref.startswith("ref:") and isinstance(n, (int, float)) and n > 0:
            return False
        if result.get("type") == "FeatureCollection" and not result.get("features"):
            return True
        if "data" in result and isinstance(result["data"], list) and not result["data"]:
            return True
        if "poi_count" in result and result["poi_count"] == 0:
            return True
    if isinstance(result, list) and not result:
        return True
    return False
