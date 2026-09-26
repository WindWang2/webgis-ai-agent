"""Python bridge to Pi (earendil-works/pi) RPC mode.

Spawns Pi as a subprocess and communicates via JSON-RPC over stdin/stdout.
Supports both request/response and streaming event patterns.
Also owns tool dispatch logic for the Pi extension's HTTP callback.

Architecture-review F3: the subprocess lifecycle + async JSON-RPC multiplexing
(the genuinely deep core, ~150 LOC) is extracted into
:class:`app.services.chat.pi_rpc_client.PiRpcClient`. The event->SSE mapping
is extracted into :func:`app.services.chat.pi_event_mapper.map_event_to_sse`.
This module retains: the singleton + feature flag, the ADR-0022 dispatch-result
cache + the two dispatch adapters (the deliberate rendezvous between the Pi
HTTP-callback and SSE adapters), and the thin ``PiBridge`` orchestrator that
glues the RPC client + mapper together.

ADR-0022 constraint: ``_dispatch_result_cache``, ``_session_executed_sets``,
``dispatch_tool``, ``cache_dispatch_result``, ``get_cached_dispatch_result``,
and ``_clear_dispatch_cache`` stay here. The extracted modules have
zero knowledge of the cache.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import hashlib
import logging
import os
import sys
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Optional

from pydantic import BaseModel

from app.utils.sse import sse_event, sse_event_type
from app.services.chat.pi_event_mapper import map_event_to_sse, _extract_text_from_event
from app.services.chat.pi_native_surface import (
    EXECUTE_PROXY_NAME,
    NATIVE_TOOL_NAME_SET,
    resolve_pi_tool_call,
)
from app.services.jobs.cancellation import CancellationToken, OperationCancelled, use_token
from app.services.tool_dispatch_service import ToolDispatchService, normalize_tool_name
from app.lib.harness.tool_call_event import ToolCallEvent
from app.lib.runtime import context as rt_ctx
from app.lib.runtime.evidence import (
    Outcome,
    TurnEvidence,
    TURN_EVIDENCE,
    bind_turn_evidence,
    emit_turn_summary,
)

logger = logging.getLogger(__name__)

# Feature flag
# 默认 True = 仓内 vendor/pi；False = ChatEngine 回退（测试 conftest 钉 false）。
from app.core.config import settings as _app_settings

USE_NEW_AGENT = bool(_app_settings.USE_NEW_AGENT)


def _env_float_drain(name: str, default: float) -> float:
    """Env-tunable timeout with safe default (CONFIG-04 pattern, local to the bridge)."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("CONFIG-04: ignoring invalid %s=%r, using default %s", name, raw, default)
        return default


# Timeout constants for prompt/stream event draining (prompt semantics, not RPC-generic).
# These stay in the bridge because they're about draining the event queue into SSE,
# not about the RPC transport itself (which has its own PI_RPC_TIMEOUT in pi_rpc_client).
#
# #786: PI_EVENT_DRAIN_TIMEOUT is the per-event wait granularity for prompt()'s
# non-streaming drain, NOT a turn deadline. The vendor answers the ``prompt`` RPC
# at preflight, so the drain consumes the event stream live — and a GIS tool call
# is a silent window between ``tool_execution_start`` and ``tool_execution_end``
# (the extension ignores ``_onUpdate``). Bounding a SINGLE inter-event gap at 2s
# killed every turn with a >2s tool or slow first token. Failure semantics now
# mirror stream_prompt: only CONTINUOUS silence reaching PI_EVENT_STREAM_TIMEOUT
# (the stall budget, default 120s) or the whole turn exceeding PI_TURN_TOTAL_TIMEOUT fails.
PI_EVENT_DRAIN_TIMEOUT = _env_float_drain("PI_EVENT_DRAIN_TIMEOUT", 2.0)    # prompt() 单次事件等待粒度
PI_TURN_TOTAL_TIMEOUT = _env_float_drain("PI_TURN_TOTAL_TIMEOUT", 300.0)    # prompt() 非流式整回合兜底上限 (#910: 900→300, env wins)

# transport goal B-P1-3: how often to emit an SSE keepalive comment when Pi is
# silent (long GIS tool, compaction, slow first token). Keeps the connection
# alive through proxies/LBs with shorter idle timeouts than the chat-SSE
# nginx budget (3600s) and tells the browser the turn is still progressing.
PI_HEARTBEAT_INTERVAL = _env_float_drain("PI_HEARTBEAT_INTERVAL", 8.0)

# Max CONTINUOUS silence before a turn is declared stalled. Previously 30s,
# which false-failed any healthy turn whose tool phase produced no SSE event
# for >30s (the tool kept running server-side while the user saw an error +
# retried → duplicate execution). With heartbeats the connection stays alive,
# so this now only needs to catch a true Pi hang; default 120s tolerates long
# toolchains (well under PI_RPC_TIMEOUT=300s that bounds one RPC).
# #1218（audit3 A-5）：注释与默认值对齐（两处旧注释误写 180s）。
# Operators may tune via the env var.
PI_EVENT_STREAM_TIMEOUT = _env_float_drain("PI_EVENT_STREAM_TIMEOUT", 120.0)


# ── V3: turn_id 铸造 + step_result SSE 注入（Harness–Map Interaction Closed Loop）─
#
# turn_id 形如 ``turn-<uuid4hex[:12]>``，每个 Pi turn（stream_prompt）铸一个，
# 显式透传给 harness 记录与 step_result SSE 负载。绝不用全局 set_correlation ——
# 单例 harness 跨 session 累积，全局 correlation 在并发/串行交错的 session 间
# 会互相污染。
TURN_ID_PREFIX = "turn-"


def _mint_turn_id() -> str:
    """铸一个本 turn 唯一的 correlation id。"""
    return f"{TURN_ID_PREFIX}{uuid.uuid4().hex[:12]}"


def _inject_turn_id(sse_str: str, turn_id: str) -> str:
    """把 turn_id 写入 step_result SSE 负载（additive 字段）。

    pi_event_mapper 属其它 slice，不越界修改；此处仅在 bridge 层对映射结果做
    防御性改写：只处理 step_result 事件，保留原有的 event:/id: 行与编号语义
    （重建时手动改 data 行，避免 sse_event() 重新消耗 DUP-1 的 id 计数器）。
    """
    if not turn_id or sse_event_type(sse_str) != "step_result":
        return sse_str
    lines = sse_str.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("data: "):
            try:
                payload = json.loads(line[len("data: "):])
            except (TypeError, ValueError):
                return sse_str
            if isinstance(payload, dict):
                payload["turn_id"] = turn_id
                lines[i] = "data: " + json.dumps(payload, ensure_ascii=False)
            break
    return "\n".join(lines)


# ── Pi tool dispatch models (owned by bridge, not pi_tools route) ──

class PiToolRequest(BaseModel):
    """Request from Pi extension to execute a GIS tool."""
    toolCallId: str
    name: str
    arguments: dict[str, Any] = {}
    sessionId: Optional[str] = None
    turnToken: Optional[str] = None
    # Set only by the authenticated HTTP adapter after token verification.
    # The extension never supplies this field as routing authority.
    verifiedTurnId: Optional[str] = None


class PiToolResponse(BaseModel):
    """Response from tool execution back to Pi extension."""
    toolCallId: str
    content: list[dict[str, Any]]
    details: Any = None
    isError: bool = False


class PiRpcError(Exception):
    """Error from Pi RPC."""
    pass


# ── Tool registry (injected at startup, shared by bridge + route) ──

_tool_registry: Optional["ToolRegistry"] = None


def set_tool_registry(registry: "ToolRegistry") -> None:
    """Inject the live ToolRegistry so tool dispatch goes to real GIS tools."""
    global _tool_registry
    _tool_registry = registry
    # 评审修复（#1084）：注入即重编译 Runtime Manifest —— 防止 lifespan 注入前
    # 的首次访问把空工具面指纹钉死整个进程。v2 manifest 惰性缓存一次编译，
    # refresh_runtime_manifest() 强制按新注入的 registry 重编译。
    try:
        from app.lib.gis.runtime_manifest import refresh_runtime_manifest
        refresh_runtime_manifest()
    except Exception:  # noqa: BLE001 - manifest 是投影，失效失败不阻断注入
        pass
    try:
        from app.services.gis_harness.capability_graph import reset_capability_graph
        reset_capability_graph()
    except Exception:  # noqa: BLE001 — graph is a projection; never block inject
        pass
    if registry is not None:
        logger.info(f"[PiBridge] Tool registry injected ({len(registry.list_tools())} tools)")


def get_tool_registry() -> "ToolRegistry":
    """Return the injected registry, raising if not yet initialized."""
    if _tool_registry is None:
        raise PiRpcError("Tool registry not initialized")
    return _tool_registry


def try_get_tool_registry() -> "Optional[ToolRegistry]":
    """Return the lifespan-injected registry, or None before startup/tests."""
    return _tool_registry


# ── Dispatch result cache (unified-tool-dispatch 票据 02) ──
#
# dispatch_tool（HTTP 回调）调度一次后把 ToolDispatchResult 按已验证 session 与
# toolCallId 缓存；_handle_tool_execution_end（SSE 适配器）随后按同一键读取，发 step_result
# 时携带 geojson_ref。这样两条适配器共享一次 dispatch，避免 Pi 回传的 result 与服务端
# 视图（已 slim、已落 ref）不一致 -- 此前 Pi 路径从不产生 ref，前端图层挂载失效。
#
# 缓存短命（一个 turn 内）：dispatch 时写入，SSE 读取后即清除，杜绝跨任务泄漏。
#
# The Pi extension carries a signed turn capability. The HTTP route verifies it and
# supplies the immutable turn session, preventing a delayed callback or colliding
# toolCallId from crossing sessions. Direct helper callers can omit session only for
# backward-compatible, unique-key lookup; production always supplies it.
# ADR-0022: this cache + the two dispatch adapters stay here - they are the
# deliberate rendezvous between the Pi HTTP-callback and SSE adapters.
_dispatch_result_cache: dict[tuple[str, str], "ToolDispatchResult"] = {}
# Sidecar: SessionPlan SSE blobs keyed like the dispatch cache, concatenated
# onto tool_execution_end. Not part of ToolDispatchResult (ADR-0022 shape).
_session_plan_sse_cache: dict[tuple[str, str], str] = {}

# #554 defect 3: ONE long-lived ToolDispatchService shared by every Pi HTTP
# callback dispatch (mirrors the legacy engine's RUN-01 pattern — a fresh
# service per callback carried a fresh instance-level _completed_keys set, so
# a repeat call within the same turn ALWAYS got the "still in flight" message
# even after the first call had long since completed; post-success dedup
# semantics were permanently dead on the Pi path). The bridge is a singleton
# and turns are strictly serial, so a module-level instance is the correct
# scope. The registry is injected once at startup; tests replace it via
# set_tool_registry, so the cache re-keys when the registry object changes.
_dispatch_service: "Optional[ToolDispatchService]" = None
_dispatch_service_registry: "Optional[ToolRegistry]" = None


def _get_shared_dispatch_service() -> "ToolDispatchService":
    """Return the bridge-wide ToolDispatchService (rebuilt only if the
    injected registry was replaced — e.g. per-test set_tool_registry swaps)."""
    global _dispatch_service, _dispatch_service_registry
    registry = get_tool_registry()
    if _dispatch_service is None or _dispatch_service_registry is not registry:
        _dispatch_service = ToolDispatchService(registry=registry)
        _dispatch_service_registry = registry
    return _dispatch_service


_DISPATCH_CACHE_MAX = 128
_SESSION_PLAN_CACHE_MAX = 128
_SESSION_EXECUTED_SETS_MAX = 128


def _cache_session_is_active(sid: object) -> bool:
    """Eviction guard: is this cache-key session running an in-flight turn?

    V5-B production routing: the pool hosts one in-flight turn per session
    concurrently, so every ``_active_turns`` key must be protected from cache
    eviction — the old single-slot view only knew the most recent registrant
    and left the other workers' sessions unprotected.
    """
    if isinstance(sid, str) and sid in _active_turns:
        return True
    ctx = getattr(sys.modules[__name__], "_active_turn_context", None)
    return isinstance(sid, str) and ctx is not None and sid == ctx[0]


def _pop_session_entries(cache: dict, session_id: str) -> None:
    """Pop all entries matching session_id (or empty session) from a cache."""
    for key in list(cache.keys()):
        if isinstance(key, tuple):
            if key[0] == session_id or key[0] == "":
                cache.pop(key, None)
        elif key == session_id or key == "":
            cache.pop(key, None)


def _evict_cache_protect_active(
    cache: dict,
    max_cap: int,
    is_tuple_key: bool = True,
) -> None:
    """Evict oldest inactive entries; never pop in-flight ``_active_turns`` keys (#1384 H04)."""
    if len(cache) <= max_cap:
        return
    for key in list(cache.keys()):
        sid = key[0] if is_tuple_key else key
        if not _cache_session_is_active(sid):
            cache.pop(key, None)
            if len(cache) <= max_cap:
                return
    # H04 (#1384): never evict keys belonging to in-flight ``_active_turns``.
    # If the cache is still over cap (all remaining sessions are live), leave
    # them — refuse further eviction rather than dropping geojson_ref for a
    # turn whose SSE adapter has not yet consumed the result.


def _evict_dispatch_result_cache() -> None:
    _evict_cache_protect_active(_dispatch_result_cache, _DISPATCH_CACHE_MAX, is_tuple_key=True)


def _evict_session_plan_sse_cache() -> None:
    _evict_cache_protect_active(_session_plan_sse_cache, _SESSION_PLAN_CACHE_MAX, is_tuple_key=True)


def _evict_session_executed_set() -> None:
    _evict_cache_protect_active(_session_executed_sets, _SESSION_EXECUTED_SETS_MAX, is_tuple_key=False)


def cache_dispatch_result(
    tool_call_id: str,
    result: "ToolDispatchResult",
    session_id: str = "",
) -> None:
    """缓存一次 dispatch 的结果，供 SSE 适配器按 session/toolCallId 读取。"""
    _dispatch_result_cache[(session_id, tool_call_id)] = result
    _evict_dispatch_result_cache()


def cache_session_plan_sse(tool_call_id: str, sse: str, session_id: str = "") -> None:
    if not sse:
        return
    # 追加而非覆盖（review A-1/B-2）：同一 tool call 可能既缓存
    # session_plan progress 又缓存 map_finalization —— 覆盖会把前者静默
    # 丢掉（take 只 pop 一次），旗舰场景（完成 DAG 的那个工具结果）前端
    # 将丢失行进度事件。SSE 事件是行分隔文本，直接拼接即两个事件。
    key = (session_id, tool_call_id)
    _session_plan_sse_cache[key] = _session_plan_sse_cache.get(key, "") + sse
    _evict_session_plan_sse_cache()


def take_session_plan_sse(tool_call_id: str, session_id: str = "") -> str:
    """Pop SessionPlan SSE cached for this tool call (empty if none)."""
    return _session_plan_sse_cache.pop((session_id, tool_call_id), "")


def get_cached_dispatch_result(
    tool_call_id: str,
    session_id: Optional[str] = None,
) -> "Optional[ToolDispatchResult]":
    """读取并弹出缓存的 dispatch 结果（读后即清，单次消费）。"""
    if session_id is not None:
        return _dispatch_result_cache.pop((session_id, tool_call_id), None)
    # Backward-compatible test/helper path. Production always supplies the
    # turn session and therefore never performs this cross-key search.
    matches = [key for key in _dispatch_result_cache if key[1] == tool_call_id]
    if len(matches) != 1:
        return None
    return _dispatch_result_cache.pop(matches[0], None)


def _clear_dispatch_cache(session_id: Optional[str] = None) -> None:
    """清空指定 session 或所有的 dispatch 缓存（新 turn 开始时按 session 清空）。"""
    if session_id:
        _pop_session_entries(_dispatch_result_cache, session_id)
        _pop_session_entries(_session_plan_sse_cache, session_id)
    else:
        _dispatch_result_cache.clear()
        _session_plan_sse_cache.clear()


def _cleanup_turn_state(turn_sid: str) -> None:
    """Turn-end cleanup: dedup sets + dispatch cache for the ending turn's session.

    Dispatch results and SessionPlan SSE written by the HTTP callback for this
    session are dropped if never consumed. Scoped to turn_sid to prevent wiping
    unrelated sessions' state under multi-session concurrency.
    """
    _session_executed_sets.pop(turn_sid, None)
    _session_executed_sets.pop("", None)
    _pop_session_entries(_dispatch_result_cache, turn_sid)
    _pop_session_entries(_session_plan_sse_cache, turn_sid)
    from app.services.chat.pi_no_progress import clear_pi_no_progress_streak
    clear_pi_no_progress_streak(turn_sid)


def _slim_pi_details_payload(result: Any) -> Any:
    """Cap details payload to 64KB and strip large images/features."""
    details_payload: Any = getattr(result, "raw_result", result)
    if isinstance(details_payload, dict):
        details_payload = dict(details_payload)
        # Raster data URL is not needed — frontend mounts via result_ref / imageRef
        if "image" in details_payload and isinstance(details_payload.get("image"), str):
            img = details_payload["image"]
            if img.startswith("data:image/") and len(img) > 1024:
                details_payload.pop("image", None)
                ref = getattr(result, "geojson_ref", None) or details_payload.get("result_ref")
                if ref:
                    details_payload["imageRef"] = ref
        # GeoJSON body already stored as ref — strip from details too
        if "geojson" in details_payload:
            details_payload.pop("geojson", None)
        if isinstance(details_payload.get("data"), dict) and details_payload["data"].get("type") == "FeatureCollection":
            # keep summary, drop heavy features array
            data_fc = details_payload["data"]
            if isinstance(data_fc.get("features"), list) and len(data_fc["features"]) > 100:
                details_payload["data"] = {k: v for k, v in data_fc.items() if k != "features"}
                details_payload["data"]["feature_count"] = len(data_fc["features"])
        # Cap details to 64KB JSON
        import json as _json
        try:
            encoded = _json.dumps(details_payload, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 65536:
                # Keep only essential keys
                keep = {k: v for k, v in details_payload.items() if k in ("type", "result_ref", "imageRef", "mapspec_fingerprint", "success", "summary", "status", "feature_count", "bbox", "command", "layer_id", "source_id")}
                if keep:
                    details_payload = keep
                else:
                    details_payload = {"summary": str(details_payload.get("summary", ""))[:2000], "result_ref": details_payload.get("result_ref") or details_payload.get("imageRef")}
        except Exception:  # noqa: BLE001
            logger.debug("[PiBridge] slim details payload failed", exc_info=True)
    return details_payload


def _record_cancelled_tracker_step(request, tool_name: str, arguments: dict) -> None:
    """#1069(A-6): 取消的 dispatch 也记 tracker 步骤（cancelled）。

    此前取消/异常路径在 tracker 记录块之前 early-return/re-raise —— /tasks
    对该工具完全无显示。与主记录块同款迟到回调守卫（#993）。
    """
    try:
        from app.services.chat.engine_instance import try_get_chat_engine
        engine = try_get_chat_engine()
        if engine is None:
            return
        callback_turn = request.verifiedTurnId
        active_turn, _run, _sid = active_turn_correlation(request.sessionId or "")
        if callback_turn is not None and callback_turn != active_turn:
            return
        tasks = engine.tracker.list_by_session(request.sessionId or "")
        if tasks:
            latest_task = tasks[-1]
            if latest_task.status.value == "running":
                step = engine.tracker.start_step(latest_task.id, tool_name, arguments)
                engine.tracker.cancel_step(latest_task.id, step.id)
    except Exception:  # noqa: BLE001
        logger.debug("[PiBridge] cancelled tracker step record failed", exc_info=True)


async def dispatch_tool(request: PiToolRequest) -> PiToolResponse:
    """Dispatch a GIS tool call via ToolDispatchService, cache the result for the SSE adapter.

    Pi 边界安全检查（工具存在性、tier>=3 拒绝）保留在此处--它们是 Pi 路径特有的
    守卫（candidate #5，本批工作 Out of Scope），不属于统一调度的核心职责。
    实际调度（ref 存储、自愈、广播、去重）全部委托给 ToolDispatchService。

    HTTP 回调适配器：把 service 返回的 llm_payload 翻译成 PiToolResponse.content。
    结果按 (session_id, toolCallId) 缓存，供 SSE 适配器读取并发携带 geojson_ref 的 step_result。
    """
    registry = get_tool_registry()
    tool_name = normalize_tool_name(request.name)
    arguments = dict(request.arguments or {})
    # The HTTP route verifies a signed turn token and writes its immutable sid
    # here. Direct in-process callers/tests may still supply sessionId.
    session_id = request.sessionId or ""
    # V4 Wave 8（ADR-0104）：调度全程绑定 turn RuntimeContext —— 调度面/
    # planner / registry 内的 18 阶段链发射（emit_chain*）据此解析 turn_id；
    # 上下文缺席时发射面静默跳过，绝不伪造链。
    _verified_turn = ""
    try:
        _verified_turn = str(getattr(request, "verifiedTurnId", "") or "")
    except Exception:  # noqa: BLE001
        _verified_turn = ""
    if _verified_turn:
        try:
            from app.lib.runtime import context as rt_ctx

            _turn_binding = rt_ctx.bind_runtime_context(
                turn_id=_verified_turn, session_id=session_id,
            )
        except Exception:  # noqa: BLE001 — 绑定失败按无上下文降级
            _turn_binding = None
    else:
        _turn_binding = None
    try:
        return await _dispatch_tool_bound(request, registry, tool_name, arguments, session_id)
    finally:
        if _turn_binding is not None:
            try:
                _turn_binding.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


async def _dispatch_tool_bound(
    request: "PiToolRequest",
    registry,
    tool_name: str,
    arguments: dict,
    session_id: str,
) -> "PiToolResponse":
    """dispatch_tool 的主体（turn 上下文已绑定后进入）。"""
    # The HTTP route verifies a signed turn token and writes its immutable sid
    # here. Direct in-process callers/tests may still supply sessionId. Never
    # fall back to the bridge's mutable active turn: a delayed callback could
    # otherwise mutate the next session.
    if not session_id:
        raise PiRpcError("Pi tool callback has no verified turn session")

    # Unknown bare names reject with discover guidance. ADR-0103: names on the
    # dynamic registered surface (spawn dump == extension registration) dispatch
    # straight through the shared pipeline — same tier/confirm gates. The
    # classification set is the *registered surface* (model-visible, non-tier-3),
    # not the full registry: hidden/tier-3 names stay proxy-only or rejected,
    # matching the extension's registration reality.
    try:
        from app.services.chat.pi_native_surface import registered_surface_names

        _registered = set(registered_surface_names(registry)) | NATIVE_TOOL_NAME_SET
    except Exception:  # noqa: BLE001 — 分类退化为冻结面行为
        _registered = None
    resolved = resolve_pi_tool_call(
        tool_name, arguments, allow_passthrough=False, registered_surface=_registered
    )
    if resolved.kind == "reject":
        # ADR-0180 D5：面外裸名 / wrap-reject 计数（invalid tool-name rate）。
        try:
            from app.services.chat.pi_surface_metrics import record_invalid_tool_name

            record_invalid_tool_name(tool_name, reason=resolved.error[:160])
        except Exception:  # noqa: BLE001 — 指标绝不阻断
            pass
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{"type": "text", "text": resolved.error}],
            details={"error": "native_surface_reject", "tool": tool_name},
            isError=True,
        )
    # ADR-0180 D5：proxy 长尾 vs 注册面直调分类计数（proxy fallback rate）。
    # 记录点在存在性/tier/闸检查**之后**（review nit）：进入真实 dispatch
    # 的调用才计入 fallback 分母，避免未派发调用稀释 proxy_fallback_rate。
    _proxied = request.name == EXECUTE_PROXY_NAME and resolved.kind == "execute"
    tool_name = normalize_tool_name(resolved.name)
    arguments = dict(resolved.arguments)

    if session_id:
        try:
            from app.services.session_plan import ensure_session_plan_slot
            await ensure_session_plan_slot(session_id)
        except Exception:
            logger.exception("[PiBridge] SessionPlan slot open failed session=%s", session_id)
        # ADR-0180：dispatch 前把将服务的能力步标 running（只对命中计划能力
        # 的工具产生写；崩溃后留下诚实的 in-flight 标记供 K7 恢复判定）。
        try:
            from app.services.harness_kernel import get_runtime

            _cb_turn_id, _cb_run_id, _ = active_turn_correlation(session_id)
            await get_runtime(session_id).begin_step(
                tool_name=tool_name,
                tool_call_id=request.toolCallId,
                turn_id=_cb_turn_id or "",
                host="pi",
            )
        except Exception:
            logger.debug(
                "[PiBridge] kernel begin_step failed session=%s tool=%s",
                session_id, tool_name, exc_info=True,
            )

    # Validate tool exists
    available = set(registry.list_tools())
    if tool_name not in available:
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{
                "type": "text",
                "text": (
                    f"Tool '{tool_name}' not found. "
                    f"Available tools: {', '.join(sorted(available)[:20])}"
                ),
            }],
            isError=True,
        )

    # 审计 SEC-01/SEC-02：拒绝 tier>=3 工具（如 create_new_skill = RCE）。
    # Pi 扩展是半信任的（共享密钥保护），但仍不应能触发写盘 + exec_module。
    tier = registry.metadata(tool_name).get("tier", 1)
    if tier >= 3:
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{
                "type": "text",
                "text": f"Tool '{tool_name}' is tier-{tier} (destructive) and cannot be dispatched via Pi bridge.",
            }],
            isError=True,
        )

    # #1395/#1477 → ADR-0204 D3：capability dispatch bind 已迁入
    # ToolDispatchService.dispatch（唯一调用点，四条 agent 路径同语义）。
    # 本桥不再预检 —— 拒绝经 dispatch 的 typed error 结果原样流达：
    # content = denial_text（llm_payload），details = CAPABILITY_INELIGIBLE
    # details（raw_result），与下方 PiToolResponse 组装同一条链。

    # ADR-0180 D3：pre-dispatch 严格校验闸（dedup / wave 排队 / ref 解析
    # 之前）。机器可读 typed error（schema_validation_rejected），不伪装成
    # 工具业务失败；registry dispatch 内部校验保持原样（本闸是前置快路径，
    # 零误拒优先 —— 漏拒由 registry 兜底）。闸自身异常内部已吞（放行）。
    try:
        from app.services.chat.pi_input_gate import (
            GATE_ERROR_CODE,
            GATE_ERROR_KEY,
            gate_reject_response_text,
            validate_pi_tool_arguments,
        )
        from app.services.chat.pi_surface_metrics import record_validation_reject

        _gate_report = validate_pi_tool_arguments(registry, tool_name, arguments)
        if _gate_report is not None:
            record_validation_reject(tool_name, _gate_report.get("issues", []))
            # v3(Phase E) 契约保持：拒绝对计划可见 —— 校验非法 ≠ 能力未尝试，
            # 命中的能力行仍标 failed（可重试），不能停留 pending。与 dispatch
            # error 分支同款 best-effort 记账（绝不阻断 typed 拒绝返回）。
            try:
                from app.services.session_plan import apply_tool_result, events_to_sse

                _gate_raw = {
                    "success": False,
                    "code": GATE_ERROR_CODE,
                    "message": gate_reject_response_text(tool_name, _gate_report)[:300],
                    "schema_issues": _gate_report["issues"],
                }
                _gate_events = await apply_tool_result(
                    session_id, tool_name, _gate_raw, success=False
                )
                if _gate_events:
                    cache_session_plan_sse(
                        request.toolCallId,
                        events_to_sse(_gate_events, session_id),
                        session_id,
                    )
            except (TimeoutError, asyncio.TimeoutError):
                # review P2：与 dispatch error 分支同款锁竞争重试一次 ——
                # 丢 failed 标记 = 行停留 pending，DAG 下游不被阻塞。
                try:
                    from app.services.session_plan import apply_tool_result, events_to_sse

                    _gate_events = await apply_tool_result(
                        session_id, tool_name, _gate_raw, success=False
                    )
                    if _gate_events:
                        cache_session_plan_sse(
                            request.toolCallId,
                            events_to_sse(_gate_events, session_id),
                            session_id,
                        )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "[PiBridge] gate reject plan-mark retry failed session=%s tool=%s",
                        session_id, tool_name, exc_info=True,
                    )
            except Exception:  # noqa: BLE001 — 记账是披露面，typed 拒绝优先
                logger.debug(
                    "[PiBridge] gate reject plan-mark failed session=%s tool=%s",
                    session_id, tool_name, exc_info=True,
                )
            return PiToolResponse(
                toolCallId=request.toolCallId,
                content=[{
                    "type": "text",
                    "text": gate_reject_response_text(tool_name, _gate_report),
                }],
                details={
                    "error": GATE_ERROR_KEY,
                    "code": GATE_ERROR_CODE,
                    "tool": tool_name,
                    "issues": _gate_report["issues"],
                    "normalized_repairs": _gate_report["normalized_repairs"],
                    "retryable": True,
                },
                isError=True,
            )
    except Exception:  # noqa: BLE001 — 闸装配故障绝不阻断 dispatch
        logger.debug("[PiBridge] input gate unavailable tool=%s", tool_name, exc_info=True)

    # ADR-0180 D5：proxy 长尾 vs 注册面直调分类计数（proxy fallback rate）——
    # 已通过存在性/tier/闸，进入真实 dispatch 的调用才计数。
    try:
        from app.services.chat.pi_surface_metrics import record_surface_call

        record_surface_call(proxied=_proxied, tool=tool_name)
    except Exception:  # noqa: BLE001 — 指标绝不阻断
        pass

    # 统一调度：委托给 ToolDispatchService（票据 01 引入）。
    # executed_tools 复用一个 session 级 set，让重复调用拦截在 service 内生效。
    # 服务实例按 bridge 复用（#554 缺陷 3）：每回调新建实例会让 _completed_keys
    # 永远为空，同回合内已完成调用的重复调用被误报为"仍在执行中"。
    service = _get_shared_dispatch_service()
    tc = {"id": request.toolCallId, "function": {"name": tool_name, "arguments": arguments}}
    executed = _session_executed_sets.setdefault(session_id, set())
    _evict_session_executed_set()

    # Runtime observability (W3): recover the active turn's correlation.
    # V5-B: resolve by the callback's verified session (pool-safe); the
    # cross-session guard below still drops mismatched attributions.
    turn_id, run_id, active_session = active_turn_correlation(session_id)
    if (
        request.sessionId
        and active_session
        and request.sessionId != active_session
    ):
        turn_id, run_id = None, None
    rt_ev = TURN_EVIDENCE.get(turn_id)

    with contextlib.ExitStack() as _rt_stack:
        _rt_stack.enter_context(rt_ctx.bind_runtime_context(
            turn_id=turn_id, run_id=run_id, session_id=request.sessionId
        ))
        if rt_ev is not None:
            _rt_stack.enter_context(bind_turn_evidence(rt_ev))
        t0 = time.monotonic()
        try:
            # V5-B: resolve the turn's cancellation token by SESSION from the
            # active-turn table (pool-safe).
            _entry = get_active_turn_entry(session_id) if session_id else None
            if _entry is not None:
                dispatch_token = _entry.token
            elif not session_id and len(_active_turns) == 1:
                dispatch_token = next(iter(_active_turns.values())).token
            else:
                dispatch_token = None
            # Pi 兼容（ADR-0052 parity）：legacy tool_pipeline 在 dispatch 外
            # 建 JobOrigin（contextvar），工具内创建的 durable job 由此带上
            # session/turn/step 关联、且 created_job_ids 可回读成
            # background_job_ids。Pi 直调 service.dispatch 此前没有 origin ——
            # job 关联断链 + SSE 永不携带。同款包裹（turn 关联来自在飞 turn
            # 的 correlation，与上面 evidence 同源）。
            from app.services.jobs.context import JobOrigin, use_origin

            _job_origin = JobOrigin(
                session_id=session_id or None,
                owner_id=None,
                owner_token=None,
                run_id=run_id,
                turn_id=turn_id,
                agent_task_id=None,
                agent_step_id=None,
                tool_call_id=request.toolCallId or None,
                tool_name=tool_name,
            )
            with use_token(dispatch_token), use_origin(_job_origin):
                result = await service.dispatch(tc, session_id, executed)
                if _job_origin.created_job_ids:
                    # 结果携带（SSE mapper 读取；ToolDispatchResult 字段为
                    # additive 默认空）。
                    try:
                        result.background_job_ids = list(_job_origin.created_job_ids)
                    except Exception:  # noqa: BLE001 — 披露是增值，不阻断
                        pass
        except OperationCancelled:
            # audit #821: 协作取消 ≠ 工具故障（ADR-0052 / legacy tool_pipeline 同义）
            # —— 记 cancelled 证据并返回结构化取消响应，而不是落入 catch-all
            # 的 is_error=True + HTTP 500。
            # #1069(A-6): isError=True 与 legacy 对齐（tool_pipeline 返回
            # status=error + step_cancelled）—— 模型此前看到「成功」而文本说
            # 取消，可能当作数据已到达继续推理。同时补记 tracker 步骤（此前
            # 取消/异常 dispatch 完全不出现在 /tasks）。
            duration_ms = int((time.monotonic() - t0) * 1000)
            harness = _get_session_harness(session_id)
            if harness is not None:
                harness.record_event(ToolCallEvent(
                    tool_call_id=request.toolCallId,
                    tool_name=tool_name,
                    arguments=arguments,
                    duration_ms=duration_ms,
                    is_error=False,
                    error_msg="",
                    result={"cancelled": True},
                    session_id=session_id,
                ))
            _record_cancelled_tracker_step(request, tool_name, arguments)
            return PiToolResponse(
                toolCallId=request.toolCallId,
                content=[{"type": "text", "text": "工具执行已被用户取消"}],
                details={"cancelled": True},
                isError=True,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            harness = _get_session_harness(session_id)
            if harness is not None:
                harness.record_event(ToolCallEvent(
                    tool_call_id=request.toolCallId,
                    tool_name=tool_name,
                    arguments=arguments,
                    duration_ms=duration_ms,
                    is_error=True,
                    error_msg=str(exc)[:200],
                    result={},
                    session_id=session_id,
                ))
            raise

        if result.status == "ok" and rt_ev is not None:
            for ma in result.map_actions:
                rt_ev.record_map_action_issued(ma["action_id"])

    duration_ms = int((time.monotonic() - t0) * 1000)

    # 记录到 TaskTracker 以支持 /api/tasks/* 观察
    # #993: 迟到回调不得把 step 记到 tasks[-1] —— route 的 is_active_pi_turn
    # 检查发生在 dispatch 之前，工具执行期间本 turn 可能被取消/结算而新 turn
    # 已建 task（tasks[-1] 已归属新 turn）。以回调自带的 verifiedTurnId（签名
    # turn token 解出）对当前活跃 turn：不匹配 → 丢弃迟到 step + warning，
    # 不错记。未经 route 验证的直连调用（verifiedTurnId=None）保持旧行为。
    try:
        from app.services.chat.engine_instance import try_get_chat_engine
        engine = try_get_chat_engine()
        if engine is None:
            raise RuntimeError("ChatEngine not initialized")
        callback_turn = request.verifiedTurnId
        active_turn, _run, _sid = active_turn_correlation(session_id)
        if callback_turn is not None and callback_turn != active_turn:
            logger.warning(
                "[PiBridge] drop late tool step (tool=%s, turn=%s, session=%s): "
                "callback's turn is no longer active; latest task belongs to a newer turn",
                tool_name, callback_turn, session_id,
            )
        else:
            tasks = engine.tracker.list_by_session(session_id)
            if tasks:
                latest_task = tasks[-1]
                # #1069(A-6): TaskStatus 无 "pending"（running/completed/
                # failed/cancelled）—— 旧条件里的 "pending" 是死分支。
                if latest_task.status.value == "running":
                    step = engine.tracker.start_step(latest_task.id, tool_name, arguments)
                    # ADR-0052 parity：durable job 挂到该 tool step（与 legacy
                    # tool_pipeline 的 step.background_job_ids 同义）。
                    try:
                        step.background_job_ids = list(getattr(result, "background_job_ids", []) or [])
                    except Exception:  # noqa: BLE001
                        pass
                    if result.status == "ok":
                        engine.tracker.complete_step(latest_task.id, step.id, result.raw_result)
                    else:
                        engine.tracker.fail_step(latest_task.id, step.id, result.llm_payload[:200])
    except Exception:
        pass

    # 缓存供 SSE 适配器按已验证 turn session 读取。
    cache_dispatch_result(request.toolCallId, result, session_id)

    # #1407: late callbacks must not attribute plan evidence / finalize to a
    # successor turn. Gate on verifiedTurnId vs active (same as TaskTracker).
    _callback_turn = getattr(request, "verifiedTurnId", None)
    _active_turn_for_evidence, _, _ = active_turn_correlation(session_id)
    _late_for_plan = (
        _callback_turn is not None
        and _callback_turn != _active_turn_for_evidence
    )
    if _late_for_plan:
        logger.warning(
            "[PiBridge] skip late plan evidence (tool=%s, turn=%s, active=%s)",
            tool_name, _callback_turn, _active_turn_for_evidence,
        )
        # ADR-0208: the kernel ledger keeps the late callback attributed to
        # the ORIGINAL turn (idempotent per tool_call_id) — fire-and-forget,
        # never blocks the callback path.
        try:
            _late_task = asyncio.get_running_loop().create_task(
                _hk_record_late_callback(
                    session_id,
                    tool_name=tool_name,
                    tool_call_id=str(request.toolCallId or ""),
                    callback_turn=str(_callback_turn or ""),
                    active_turn=str(_active_turn_for_evidence or ""),
                )
            )
            _late_task.add_done_callback(
                lambda _t: _t.cancelled() or _t.exception()
            )
        except (RuntimeError, TypeError):  # no loop / scheduling refused
            pass

    # 方向 09（ADR-0210）：GIS 后置披露管线收敛为 typed 模块
    # app/services/chat/pi_post_dispatch.py —— 证据→投影→终验→cartography
    # 证据→证据链的顺序即权威序，逐段 never-raise。bridge 保持 rendezvous
    # 职责：把 DisclosureOutcome 翻译成 PiToolResponse / ADR-0022 SSE 缓存。
    from app.services.chat.pi_post_dispatch import (
        DispatchDisclosure,
        apply_post_dispatch_disclosure,
    )

    _disclosure = DispatchDisclosure(
        session_id=session_id,
        tool_call_id=request.toolCallId,
        tool_name=tool_name,
        arguments=arguments,
        status=result.status,
        raw_result=result.raw_result if isinstance(result.raw_result, dict) else {},
        llm_payload=result.llm_payload,
        geojson_ref=(result.geojson_ref or ""),
        map_actions=tuple(result.map_actions or ()),
        turn_id=str(_callback_turn or ""),
        active_turn_id=str(_active_turn_for_evidence or ""),
        late_for_plan=_late_for_plan,
        duration_ms=duration_ms,
    )
    _outcome = await apply_post_dispatch_disclosure(_disclosure)
    if _outcome.cache_plan_sse_unconditionally:
        cache_session_plan_sse(request.toolCallId, _outcome.plan_sse, session_id)
    elif _outcome.plan_sse:
        cache_session_plan_sse(request.toolCallId, _outcome.plan_sse, session_id)
    if _outcome.finalization_payload is not None:
        # pending 不披露（DAG 未终态是 turn 中段常态，[GIS Plan] 行投影
        # 已表达）。repair 改写 desired state 时负载附带 mapspec + revision
        # —— 前端通用 spec 提交通道同步到 live chrome/exporter。
        cache_session_plan_sse(
            request.toolCallId,
            sse_event("map_finalization", _outcome.finalization_payload),
            session_id,
        )
    if _outcome.stale_generation:
        # The GIS result is still returned, but a completion from an
        # older MapSpec revision cannot enter or evaluate the current
        # process-local harness.
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{"type": "text", "text": result.llm_payload}],
            details=_slim_pi_details_payload(result),
            isError=(result.status == "error"),
        )

    details_payload = _slim_pi_details_payload(result)

    # ADR-0103（§九）：GIS-aware 无进展诊断 —— 每次真实 dispatch 后观测
    # mapspec 指纹与 SessionPlan 进度代数；达到停滞阈值时把 reason codes
    # 以 no_progress_hints 附进 details（模型可读的诚实诊断）。
    # H03 (#1384)：hints 达 ChatEngine 同款连续阈值时 HARD STOP 本轮，
    # 而不是只把诊断塞进 details 让 tool→LLM 自旋烧满 PI_TURN_TOTAL_TIMEOUT。
    _hints: list[str] = []
    try:
        _hints = await _record_gis_progress(
            session_id, tool_name, arguments,
            outcome=("ok" if result.status == "ok" else "error"),
        )
        if _hints:
            details_payload = dict(details_payload or {})
            details_payload["no_progress_hints"] = _hints
    except Exception:  # noqa: BLE001 — 诊断绝不阻断工具返回
        logger.debug("[PiBridge] gis progress diagnose failed", exc_info=True)

    from app.services.chat.pi_no_progress import (
        pi_no_progress_should_stop,
        pi_no_progress_streak,
    )
    if pi_no_progress_should_stop(session_id, _hints):
        details_payload = dict(details_payload or {})
        details_payload["failure_class"] = "no_progress"
        details_payload.setdefault("no_progress_hints", _hints)
        _hard_stop_pi_turn_for_no_progress(session_id)
        streak = pi_no_progress_streak(session_id)
        stop_note = (
            f"连续 {streak} 次工具调用无进展，已终止本轮"
            f"（{', '.join(_hints) or 'no_progress'}）。"
        )
        payload = result.llm_payload or ""
        text = f"{payload}\n[no_progress] {stop_note}" if payload else stop_note
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{"type": "text", "text": text}],
            details=details_payload,
            isError=True,
        )

    return PiToolResponse(
        toolCallId=request.toolCallId,
        content=[{"type": "text", "text": result.llm_payload}],
        details=details_payload,
        isError=(result.status == "error"),
    )


# 每个 session 的已执行工具集合，供重复调用拦截（service 接受外部 set）。
_session_executed_sets: dict[str, set[tuple[str, str]]] = {}

# ADR-0103：per-session GIS 无进展诊断器（有界；只存代数与签名，无内容）。
# ARCH-18: the registry lives in ``app.services.chat.no_progress`` so the Pi
# reason-code tracker and the pi_no_progress hard-stop streak share ONE state.

_SIDE_EFFECT_MUTATION = {"state_mutation", "external_side_effect", "destructive", "artifact_creation"}
_SIDE_EFFECT_READ = {"pure", "deterministic_compute", "cacheable_read"}



def _stable_state_epoch(map_epoch: str, workflow_epoch: str) -> int:
    """(map, workflow) 双维指纹 → 稳定 int 状态代（确定性；跨进程可复现）。"""
    import hashlib as _h

    return int(_h.sha256(f"{map_epoch}|{workflow_epoch}".encode("utf-8")).hexdigest()[:8], 16)


async def _record_gis_progress(
    session_id: str,
    tool_name: str,
    arguments: dict,
    *,
    outcome: str,
) -> list[str]:
    """记录一次 dispatch 的进程观测，返回达到阈值的 no-progress reason codes。

    map_epoch = mapspec 指纹（session_data_manager 定向读）；
    workflow_epoch = SessionPlan capability 进度的内容 hash。
    任一读取失败按空串处理（该维度本轮不参与停滞判定 —— 诚实缺省）。
    """
    from app.services.chat.no_progress import get_session_progress_tracker

    # ARCH-18: shared bounded LRU — same tracker the pi_no_progress hard-stop
    # circuit mutates, so reason codes and streak cannot drift apart.
    tracker = get_session_progress_tracker(session_id)

    registry = get_tool_registry()
    try:
        side_effect = registry.descriptor(tool_name).side_effect.value
    except Exception:  # noqa: BLE001
        side_effect = ""
    is_mutation = side_effect in _SIDE_EFFECT_MUTATION
    is_read_only = side_effect in _SIDE_EFFECT_READ

    map_epoch = ""
    try:
        from app.services.session_data import session_data_manager
        fp = await session_data_manager.get_map_spec_fingerprint(session_id)
        map_epoch = str(fp) if fp else ""
    except Exception:  # noqa: BLE001
        map_epoch = ""

    workflow_epoch = ""
    try:
        from app.services.session_plan import load_session_plan
        plan = await load_session_plan(session_id)
        if plan is not None:
            rows = tuple(sorted(
                (r.capability, r.status) for r in (plan.progress or ())
            ))
            import hashlib as _hashlib
            workflow_epoch = _hashlib.sha256(
                repr(rows).encode("utf-8")
            ).hexdigest()[:12] if rows else ""
    except Exception:  # noqa: BLE001
        workflow_epoch = ""

    reasons = tracker.record_call(
        tool_name, arguments, outcome,
        # review R2 MAJOR：state_epoch 用 (map_epoch, workflow_epoch) 的稳定
        # 摘要 —— 形态级 reason codes（exact_repeat_failure / repeated_read /
        # repeated_mutation_no_state_change）据此识别「真实状态已变化」，
        # 消除假阳性（tracker 的设计前提：state_epoch 由调用方传入）。
        state_epoch=_stable_state_epoch(map_epoch, workflow_epoch),
        map_epoch=map_epoch,
        workflow_epoch=workflow_epoch,
        is_read_only=is_read_only,
        is_mutation=is_mutation,
    )
    if reasons:
        logger.warning(
            "[PiBridge] no-progress detected session=%s tool=%s reasons=%s diagnose=%s",
            session_id, tool_name, reasons, tracker.diagnose(),
        )
    return reasons


def _hard_stop_pi_turn_for_no_progress(session_id: str) -> None:
    """Cancel the in-flight turn token and abort Pi (best-effort, non-blocking).

    F03：看门狗属 policy 发起中止 —— abort(source="policy") 使单结算 seam
    把该 turn 记成 ``aborted``（此前会被误结算成 completed）。
    """
    entry = get_active_turn_entry(session_id) if session_id else None
    if entry is None:
        return
    token = getattr(entry, "token", None)
    if token is not None:
        try:
            token.cancel("no_progress")
        except Exception:  # noqa: BLE001 — stop path must not raise
            logger.debug("[PiBridge] no-progress token cancel failed", exc_info=True)
    bridge = getattr(entry, "bridge", None)
    if bridge is None:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(bridge.abort(session_id=session_id, source="policy"))
    except Exception:  # noqa: BLE001
        logger.debug("[PiBridge] no-progress abort schedule failed", exc_info=True)


# F24/V5-B: active-turn registry keyed by SESSION (was: one module-global
# token slot). A single process can now host multiple concurrent in-flight
# turns (bridge pool, V5-B) because every lookup (dispatch_tool token binding,
# abort, correlation) resolves by session id instead of "the" global slot.
# The module-level aliases below stay as the single-active-turn view for
# back-compat readers; with pool size 1 exactly one table entry exists and
# behavior is byte-identical to the pre-V5 singleton.
class _ActiveTurnEntry:
    """Per-session in-flight turn identity (V5-B).

    ``token`` is the turn's CancellationToken (checkpoint() cooperative
    cancellation for tools dispatched via the HTTP callback); ``bridge`` is
    the worker whose subprocess owns the turn (abort RPC routing).
    """

    __slots__ = ("session_id", "turn_id", "run_id", "token", "bridge", "context")

    def __init__(
        self,
        session_id: str,
        turn_id: str,
        token: Optional[CancellationToken],
        bridge: Optional["PiBridge"] = None,
        run_id: Optional[str] = None,
        context: Optional[tuple[str, str]] = None,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.run_id = run_id
        self.token = token
        self.bridge = bridge
        self.context = context  # (session_id, turn_id) capability pair


_active_turns: dict[str, _ActiveTurnEntry] = {}


async def register_active_pi_turn(
    session_id: str,
    turn_id: str,
    token: Optional[CancellationToken] = None,
    bridge: Optional["PiBridge"] = None,
    run_id: Optional[str] = None,
) -> None:
    """Register active turn in local process memory and Redis (if available)."""
    entry = _ActiveTurnEntry(
        session_id=session_id,
        turn_id=turn_id,
        token=token,
        bridge=bridge,
        run_id=run_id,
        context=(session_id, turn_id),
    )
    _active_turns[session_id] = entry
    if bridge is not None:
        bridge._current_turn = entry
    from app.services.chat.pi_turn_context import pi_turn_registry
    await pi_turn_registry.register_turn(session_id, turn_id)


async def unregister_active_pi_turn(session_id: str, turn_id: str) -> None:
    """Unregister active turn in local memory and Redis (if owned)."""
    entry = _active_turns.get(session_id)
    if entry is not None and entry.turn_id == turn_id:
        _active_turns.pop(session_id, None)
        if entry.bridge is not None and entry.bridge._current_turn is entry:
            entry.bridge._current_turn = None
    from app.services.chat.pi_turn_context import pi_turn_registry
    await pi_turn_registry.unregister_turn(session_id, turn_id)


def get_active_turn_entry(session_id: str) -> Optional[_ActiveTurnEntry]:
    """V5-B: resolve the in-flight turn entry for a session (None if none)."""
    return _active_turns.get(session_id)


async def is_active_pi_turn(session_id: str, turn_id: str) -> bool:
    """Return whether ``(session, turn)`` owns the live Pi prompt (local or cross-pod Redis)."""
    entry = _active_turns.get(session_id)
    if entry is not None and entry.turn_id == turn_id:
        return True
    from app.services.chat.pi_turn_context import pi_turn_registry
    return await pi_turn_registry.is_active(session_id, turn_id)


def active_turn_correlation(
    session_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """返回在飞 turn 的 (turn_id, run_id, session_id)。

    无在飞 turn 或已被清理时返回 (None, None, None)。供 dispatch_tool（独立 HTTP
    回调 task）恢复 turn 级关联使用，并据 session_id 守卫串号回调。

    V5-B: 传 session_id 时按 active-turn 表解析该会话的在飞 turn（bridge pool
    下同进程可有多个在飞 turn）；不传时仅在单一在飞 turn 时返回该 turn 视图，多在飞 turn
    时返回 None 防止并发串号。
    """
    if session_id:
        entry = _active_turns.get(session_id)
        if entry is not None:
            return entry.turn_id, entry.run_id, entry.session_id
        return None, None, None
    if len(_active_turns) == 1:
        entry = next(iter(_active_turns.values()))
        return entry.turn_id, entry.run_id, entry.session_id
    return None, None, None


#: F03（ADR-0204-f03 D3）：turn 级 abort 来源台账（进程内有界）。
#: ``PiBridge.abort`` 在命中目标 turn 时记录 (turn_id → source)；单结算 seam
#: 在 finally 消费并清除。来源词表：``user``（用户任务/会话取消）、
#: ``system``（会话删除等系统清理）、``policy``（看门狗/预算中止）。
_TURN_ABORT_SOURCES: "OrderedDict[str, str]" = OrderedDict()
_TURN_ABORT_SOURCES_MAX = 64

#: abort source → TurnStatus（结算映射的单表；user 停止属 cancelled 语义，
#: system/policy 停止属 aborted 语义 —— models.TurnStatus 词表注释）。
_ABORT_SOURCE_STATUS = {"user": "cancelled", "system": "aborted", "policy": "aborted"}


def record_turn_abort_source(turn_id: str, source: str) -> None:
    """Record why a turn is being aborted (bounded; latest source wins).

    Only TERMINAL-meaningful sources are recorded (user/system/policy).
    ``cleanup`` aborts — the finally's abort-on-disconnect for an already
    failing/cancelled turn — are mechanical consequences, not an independent
    terminal cause, so they must never override the failure family.
    """
    if not turn_id or source not in _ABORT_SOURCE_STATUS:
        return
    _TURN_ABORT_SOURCES[turn_id] = str(source)[:24]
    while len(_TURN_ABORT_SOURCES) > _TURN_ABORT_SOURCES_MAX:
        _TURN_ABORT_SOURCES.popitem(last=False)


def pop_turn_abort_source(turn_id: str) -> str:
    """Consume-and-clear the abort source recorded for ``turn_id``."""
    return _TURN_ABORT_SOURCES.pop(turn_id, "")


def _hk_turn_status(
    *,
    cancelled: bool,
    timed_out: bool,
    send_failed: bool,
    process_died: bool,
    error: bool = False,
    abort_source: str = "",
) -> str:
    """Turn settle flags → kernel TurnStatus (ADR-0180/0204, single mapping).

    Shared by the streaming and non-streaming settle paths. Precedence
    (F03): an explicit asyncio cancellation wins (client disconnect);
    a recorded abort source comes next — user → ``cancelled``,
    system/policy → ``aborted`` (deliberate stops are not failures); then
    the failure family (stall/total timeout / prompt send error / Pi
    process death / unclassified exception) → ``failed``; everything else
    → ``completed``.
    """
    if cancelled:
        return "cancelled"
    if abort_source:
        return _ABORT_SOURCE_STATUS.get(abort_source, "aborted")
    if timed_out or send_failed or process_died or error:
        return "failed"
    return "completed"


async def _hk_record_late_callback(
    session_id: str,
    *,
    tool_name: str,
    tool_call_id: str,
    callback_turn: str,
    active_turn: str,
) -> None:
    """ADR-0208: journal a late tool callback on the kernel ledger.

    #1407 skips late plan evidence (no successor-turn attribution); this
    adds the missing observability — an idempotent ``tool_late`` event on
    the ORIGINAL turn. Never raises (the callback path must not be
    disturbed by ledger failures).
    """
    try:
        from app.services.harness_kernel import get_runtime

        await get_runtime(session_id).record_late_callback(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            callback_turn_id=callback_turn,
            active_turn_id=active_turn,
            host="pi",
        )
    except Exception:  # noqa: BLE001 — 台账绝不阻断回调路径
        logger.debug(
            "[PiBridge] kernel late-callback journal failed session=%s tool=%s",
            session_id, tool_name, exc_info=True,
        )


def __getattr__(name: str) -> Any:
    """Dynamic resolution for legacy module globals to preserve back-compat without concurrency crosstalk."""
    if name == "_active_turn_context":
        if len(_active_turns) == 1:
            return next(iter(_active_turns.values())).context
        return None
    if name == "_active_turn_token":
        if len(_active_turns) == 1:
            return next(iter(_active_turns.values())).token
        return None
    if name == "_active_turn_turn_id":
        if len(_active_turns) == 1:
            return next(iter(_active_turns.values())).turn_id
        return None
    if name == "_active_turn_run_id":
        if len(_active_turns) == 1:
            return next(iter(_active_turns.values())).run_id
        return None
    if name == "_active_turn_session_id":
        if len(_active_turns) == 1:
            return next(iter(_active_turns.values())).session_id
        return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")



# ── Cartography session runtime（AH-P1-1 收敛迁移）─────────────────────
# 制图会话评估/harness 注册表/上下文持久化已整体迁至
# app/services/cartography_runtime.py（legacy 与 Pi 共享的 desired-state
# 评估器）。此处保留 re-export：dispatch 流程与存量 importer（含测试）
# 继续经本模块名访问；新代码请直接 import cartography_runtime。
from app.services.cartography_runtime import (  # noqa: F401
    evaluate_cartographic_session,
    record_cartographic_dispatch_evidence,
    get_harness,
    get_harness_telemetry_summary,
    clear_cartographic_session_state,
    restore_cartographic_session_state,
    is_cartographic_session_deleted,
    _build_session_harness,
    _get_session_harness,
    _discard_session_harness,
    _persist_cartographic_harness_context,
    _hydrate_cartographic_harness,
)

# ── PiBridge: thin orchestrator (holds PiRpcClient + delegates mapping) ──────


class _TurnLease:
    """Explicit ownership handle for the singleton bridge's turn lock (#1108).

    Lock invariants (V5 §18 precursor):
      INV-P1 every successful acquire is released exactly once (``released``).
      INV-P2 a turn can never release another turn's acquisition
             (owner-checked in ``PiBridge._release_turn_lease``).
      INV-P3 a cancelled/failed register cannot leak the lock (register runs
             inside the try whose finally releases the lease).
      INV-P4 a cancelled/failed unregister cannot leak the lock (release runs
             BEFORE the post-lock unregister await).
    """

    __slots__ = ("turn_id", "session_id", "acquired_at", "released")

    def __init__(self, turn_id: str, session_id: Optional[str]) -> None:
        self.turn_id = turn_id
        self.session_id = session_id
        self.acquired_at = time.monotonic()
        self.released = False


class PiBridge:
    """Bridge to Pi agent via RPC mode.

    Thin orchestrator: holds a :class:`PiRpcClient` (the deep RPC core),
    owns the prompt/stream_prompt turn lifecycle (including the ADR-0022 cache
    clearing), and delegates event->SSE mapping to the pure
    :func:`map_event_to_sse` function.

    The ADR-0022 dispatch-result cache + the two dispatch adapters
    (``dispatch_tool`` HTTP callback + ``_handle_tool_execution_end`` SSE
    adapter) stay at module level above - they are the deliberate rendezvous
    between the two adapters and cannot move.
    """
    _current_turn: Optional[_ActiveTurnEntry] = None

    # Session id of the turn currently holding the bridge lock (set/cleared by
    # stream_prompt). Class-level default so tests that bypass __init__
    # (PiBridge.__new__) still read None. None = no turn in flight.
    _active_turn_sid: Optional[str] = None
    # #1108: current lock owner lease (class-level default for __new__ tests).
    _lock_lease: Optional["_TurnLease"] = None

    def __init__(
        self,
        pi_rpc_entry: Optional[Path] = None,
        session_dir: Optional[Path] = None,
        cwd: Optional[Path] = None,
        extension_paths: Optional[list[str]] = None,
        rpc: Optional["PiRpcClient"] = None,
    ):
        # Lazy import to avoid a circular import: pi_rpc_client.py imports
        # PiRpcError from this module. The bridge creates the client internally
        # by default; tests can inject one.
        if rpc is not None:
            self._rpc = rpc
        else:
            from app.services.chat.pi_rpc_client import PiRpcClient
            self._rpc = PiRpcClient(
                pi_rpc_entry=pi_rpc_entry,
                session_dir=session_dir,
                cwd=cwd,
                extension_paths=extension_paths or [],
            )
        self._lock = asyncio.Lock()
        self._respawn_lock = asyncio.Lock()
        self._active_turn_sid: Optional[str] = None
        self._current_turn: Optional[_ActiveTurnEntry] = None
        # #1108: explicit lock ownership. ``_lock_lease`` names the turn that
        # currently owns ``_lock``; release is owner-checked and idempotent so
        # one turn can never release another turn's acquisition (INV-P2) and
        # a double release is structurally impossible (INV-P1).
        self._lock_lease: Optional["_TurnLease"] = None
        self._last_respawn_attempt: float = 0.0
        self._respawn_backoff_s: float = 1.0
        self._consecutive_respawn_failures: int = 0

    async def _acquire_turn_lease(self, turn_id: str, session_id: Optional[str]) -> "_TurnLease":
        """Acquire the turn lock and bind it to an explicit ownership lease.

        The lease is the ONLY sanctioned way to release the lock afterwards
        (see ``_release_turn_lease``); plain ``self._lock.release()`` calls are
        forbidden outside these helpers (#1108).
        """
        await self._lock.acquire()
        lease = _TurnLease(turn_id=turn_id, session_id=session_id)
        self._lock_lease = lease
        return lease

    def _release_turn_lease(self, lease: "_TurnLease") -> None:
        """Release the turn lock iff ``lease`` is still the registered owner.

        Idempotent (double release is a no-op) and ownership-checked (a turn
        can never release a later turn's acquisition). Synchronous on purpose:
        it must run to completion inside a finally even when the surrounding
        task is being cancelled.
        """
        if lease is None or lease.released:
            return
        if self._lock_lease is not lease:
            logger.error(
                "[PiBridge] refusing cross-turn lock release: caller lease "
                "turn=%s session=%s but lock is owned by turn=%s — leak elsewhere?",
                lease.turn_id, lease.session_id,
                self._lock_lease.turn_id if self._lock_lease else None,
            )
            return
        lease.released = True
        self._lock_lease = None
        self._lock.release()

    async def _safe_unregister_active_pi_turn(self, session_id: str, turn_id: str) -> None:
        """Best-effort active-turn unregister, hardened against cancellation.

        #1108 INV-P4: callers invoke this AFTER releasing the turn lease, and
        the underlying Redis eval only catches ``Exception`` (CancelledError
        is a BaseException and would otherwise propagate out of the turn's
        finally). Mirrors the abort-on-disconnect discipline: shield + budget
        + swallow, so a re-delivered cancellation during teardown can never
        skip subsequent cleanup.
        """
        try:
            await asyncio.wait_for(
                asyncio.shield(unregister_active_pi_turn(session_id, turn_id)),
                timeout=5.0,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("[PiBridge] turn unregister failed (turn=%s): %s", turn_id, e)

    async def _safe_kernel_end_turn(self, session_id: str, turn_id: str, status: str) -> None:
        """ADR-0180: kernel turn settle, hardened like the unregister above.

        Shield + budget + swallow (review R5): the finally must never be
        interrupted mid-cleanup by a re-delivered cancellation, and the
        settle must land BEFORE the turn lease is released (review S1) so a
        successor turn's ``begin_turn`` never sees this turn as still
        ``running`` and mis-flags it ``interrupted``.
        """
        try:
            from app.services.harness_kernel import get_runtime

            await asyncio.wait_for(
                asyncio.shield(
                    get_runtime(session_id).end_turn(turn_id, host="pi", status=status)
                ),
                timeout=5.0,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[PiBridge] kernel end_turn failed (turn=%s session=%s): %s",
                turn_id, session_id, e,
            )

    async def _settle_turn_outcome(
        self,
        *,
        session_id: str,
        turn_id: str,
        tracker_task_id: Optional[str],
        cancelled: bool,
        timed_out: bool,
        send_failed: bool,
        process_died: bool,
        error: bool = False,
        error_detail: str = "",
        timeout_reason: str = "",
        projections_settled: bool,
    ) -> str:
        """Single turn settlement seam (F03 / ADR-0204-f03 D1).

        The ONE settlement sequence for every way a turn can end —
        stream/non-stream × clean agent_settled / client cancel / stall /
        total budget / process death / send failure / unclassified
        exception / abort (user|system|policy). Replaces the duplicated
        finally choreographies; idempotent per turn (kernel end_turn
        re-settle is a no-op; projections are gated by
        ``projections_settled`` on paths that already ran them in-try).

        Order: status mapping (abort-source aware) → refusal downgrade
        (clean settles only) → outcome-aware projection pipeline (reduced
        on non-clean) → kernel end_turn → tracker settle. Each leg is
        best-effort; settlement must complete even when a leg fails.
        Returns the kernel TurnStatus that was recorded.
        """
        abort_source = pop_turn_abort_source(turn_id)
        status = _hk_turn_status(
            cancelled=cancelled,
            timed_out=timed_out,
            send_failed=send_failed,
            process_died=process_died,
            error=error,
            abort_source=abort_source,
        )
        from app.services.chat.pi_post_dispatch import TurnSettleOutcome

        failure_class = ""
        if timed_out:
            failure_class = "pi_turn_budget" if timeout_reason == "total" else "pi_stall"
        elif process_died:
            failure_class = "pi_process_died"
        elif send_failed:
            failure_class = "pi_send_error"
        elif error:
            failure_class = "pi_unclassified_error"
        elif abort_source in ("system", "policy"):
            failure_class = f"pi_abort_{abort_source}"
        outcome = TurnSettleOutcome(
            settle_class=(
                "clean" if status == "completed" else
                "cancelled" if status == "cancelled" else
                "aborted" if status == "aborted" else "failed"
            ),
            kernel_status=status,
            failure_class=failure_class,
            failure_detail=error_detail[:200],
        )
        # Refusal downgrade (clean settles only): zero execution activity +
        # an open clarification question → the turn ended before anything
        # was at stake (ADR-0208). Failure/cancel/abort never downgrade.
        refusal: Optional[dict] = None
        if session_id and outcome.is_clean:
            from app.services.chat.pi_post_dispatch import resolve_turn_refusal

            refusal = await resolve_turn_refusal(session_id, turn_id)
            if refusal:
                status = "refused"
                outcome = TurnSettleOutcome(
                    settle_class="clean",
                    kernel_status="refused",
                    failure_class="",
                    failure_detail=str(refusal.get("question") or "")[:200],
                )
        if session_id and not projections_settled:
            try:
                from app.services.chat.pi_post_dispatch import settle_turn_projections

                await settle_turn_projections(session_id, turn_id, outcome=outcome)
            except Exception:  # noqa: BLE001 — 增值披露，绝不阻断结算
                logger.exception(
                    "[PiBridge] turn settle pipeline failed session=%s turn=%s",
                    session_id, turn_id,
                )
        if session_id:
            await self._safe_kernel_end_turn(session_id, turn_id, status)
            # F03 D5（review P2-2）：parity 行必须在 kernel end_turn 之后读
            # —— 终态未落时 terminal parity 恒为 None（生产死代码）。
            from app.services.chat.pi_post_dispatch import log_lifecycle_parity

            await log_lifecycle_parity(session_id, turn_id)
        if tracker_task_id:
            try:
                from app.services.chat.engine_instance import try_get_chat_engine

                engine = try_get_chat_engine()
                if engine is None:
                    raise RuntimeError("ChatEngine not initialized")
                if status == "cancelled":
                    engine.tracker.cancel(tracker_task_id)
                elif status in ("failed", "aborted"):
                    engine.tracker.fail_task(
                        tracker_task_id,
                        (
                            "turn aborted (system/policy stop)"
                            if status == "aborted"
                            else (outcome.failure_detail or "turn failed or timed out")
                        ),
                    )
                else:
                    engine.tracker.complete_task(tracker_task_id)
            except Exception:
                logger.debug(
                    "[agent_pi_bridge] tracker task settle failed session=%s",
                    session_id, exc_info=True,
                )
        if refusal:
            logger.info(
                "[PiBridge] turn settled as refused session=%s turn=%s "
                "reason=%s question=%s",
                session_id, turn_id,
                refusal.get("reason_code", ""), refusal.get("question", "")[:80],
            )
        return status

    @property
    def _process_died(self) -> bool:
        """Delegate to the RPC client (back-compat for _use_pi_bridge)."""
        return self._rpc.process_died

    def is_alive(self) -> bool:
        """Return True if the underlying RPC client process is alive and healthy."""
        is_alive_fn = getattr(self._rpc, "is_alive", None)
        if is_alive_fn and callable(is_alive_fn):
            return is_alive_fn()
        return not self._rpc.process_died

    async def respawn_if_dead(self) -> bool:
        """Attempt lazy respawn of the Pi subprocess with exponential backoff.

        Returns True if the bridge is currently alive or was successfully respawned;
        False if respawn is on cooldown or failed.
        """
        if self.is_alive():
            return True

        async with self._respawn_lock:
            # Double check under lock in case another coroutine just finished respawning
            if self.is_alive():
                return True

            now = time.monotonic()
            if now - self._last_respawn_attempt < self._respawn_backoff_s:
                logger.warning(
                    "[PiBridge] Lazy respawn on cooldown (%.1fs remaining of %.1fs backoff); degrading to ChatEngine",
                    self._respawn_backoff_s - (now - self._last_respawn_attempt),
                    self._respawn_backoff_s,
                )
                return False

            # V5-B-3: a turn still in flight on this dead worker parks its
            # death watcher on ``process_died_event``; ``start()`` CLEARS that
            # event, which would strand the dying turn on the full stall
            # budget instead of failing promptly. Let the turn unwind first —
            # the next availability check performs the respawn.
            if self._active_turn_sid is not None or self._lock.locked():
                logger.warning(
                    "[PiBridge] Deferring respawn: a turn is still unwinding on "
                    "the dead worker (session=%s); it must observe the death "
                    "signal before the event is cleared",
                    self._active_turn_sid,
                )
                return False

            self._last_respawn_attempt = now
            try:
                logger.info("[PiBridge] Attempting lazy respawn of Pi subprocess...")
                await self.start()
                if self.is_alive():
                    logger.info("[PiBridge] Pi subprocess successfully respawned and healthy")
                    self._respawn_backoff_s = 1.0
                    self._consecutive_respawn_failures = 0
                    return True
                else:
                    raise PiRpcError("Pi subprocess was not alive after start()")
            except Exception as e:
                self._consecutive_respawn_failures += 1
                # Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
                self._respawn_backoff_s = min(30.0, 1.0 * (2 ** (self._consecutive_respawn_failures - 1)))
                logger.error(
                    "[PiBridge] Lazy respawn failed (attempt %d, next backoff %.1fs): %s",
                    self._consecutive_respawn_failures,
                    self._respawn_backoff_s,
                    e,
                )
                return False

    # ── Public API ──────────────────────────────────────────────

    async def start(self) -> None:
        """Start the Pi subprocess (delegates to the RPC client)."""
        await self._rpc.start()

    async def stop(self) -> None:
        """Stop the Pi subprocess (delegates to the RPC client)."""
        await self._rpc.stop()

    async def abort(self, session_id: Optional[str] = None, *, source: str = "user") -> dict:
        """Abort the currently-running Pi prompt (fire-and-forget from callers).

        ``source`` (F03/ADR-0204-f03 D3): who ordered the stop — ``user``
        (task/session cancel), ``system`` (session deletion cleanup) or
        ``policy`` (watchdog/budget). Recorded per turn and consumed by the
        settle seam so the kernel records ``cancelled`` (user) vs ``aborted``
        (system/policy) instead of a false ``completed`` when the vendor
        honors the abort with a clean ``agent_settled``.

        BUG-18 fix re-added: ADR-0031 F3 extraction removed the abort wrapper,
        leaving chat.py:320's `await pi_bridge.abort()` to AttributeError into
        a swallowed `except Exception` — the in-flight prompt kept consuming
        tokens and writing files after session deletion.

        Session scoping (F5): the abort RPC and ``fail_all_pending`` are
        GLOBAL — they hit whatever turn currently owns the singleton
        subprocess. When a turn for a DIFFERENT session is in flight, an
        abort meant for an already-dead session would kill the wrong
        session's turn; skip the RPC + fail_all_pending and log a warning
        instead. When ``session_id`` matches the active turn, is None
        (legacy single-slot semantics — all production callers now pass a
        session id; a bare None under a pool would ignite whichever worker's
        turn registered LAST), or no turn is active, behave as before.

        The RPC client's `request("abort")` matches vendor/pi's rpc-mode.js
        `case "abort"` handler. Failures (no process, RPC error) propagate;
        callers wrap in try/except because abort must never block the cleanup
        path that follows.

        Deliberately does NOT acquire ``self._lock``: ``prompt``/``stream_prompt``
        hold the lock across the whole turn (send + drain) to serialize turns
        on the singleton bridge, and an abort that blocked on the lock would
        deadlock against the in-flight turn it is trying to cancel. The
        ``request("abort")`` RPC + ``fail_all_pending`` below provide
        cancellation without the lock.

        Knock-on note (fail_all_pending scoping): ``fail_all_pending`` fails
        EVERY pending future, which looks over-broad — but on this singleton
        client requests are serialized by the turn lock, so the only futures
        that can be pending at abort time belong to the in-flight turn (the
        abort RPC's own future has already resolved, and the F5 guard above
        skips this call entirely when the in-flight turn is another
        session's). The failed futures are therefore already exactly the
        abort-relevant ones; a per-command pending registry would add
        bookkeeping for no additional safety.
        """
        # V5-B: with a bridge pool, the targeted session's turn may live on a
        # DIFFERENT worker than ``self`` — route the abort there.
        _entry = get_active_turn_entry(session_id) if session_id else None
        if _entry is not None and _entry.bridge is not None and _entry.bridge is not self:
            return await _entry.bridge.abort(session_id)
        active = self._active_turn_sid
        if session_id is not None and active is not None and session_id != active:
            logger.warning(
                "[PiBridge] abort(session_id=%s) skipped: turn for session %s "
                "is in flight on the bridge; a global abort would "
                "kill the wrong session's turn",
                session_id, active,
            )
            return {}
        # CONC-F1: snapshot the turn identity + pending futures BEFORE the
        # (potentially slow) abort RPC. The same-session successor case —
        # page close → immediate resend is the most common disconnect pattern —
        # leaves _active_turn_sid UNCHANGED while the token and pending
        # futures belong to the NEW turn; sid equality alone would kill it.
        # V5-B: resolve token from session table or instance turn state.
        if _entry is not None:
            abort_token = _entry.token
        elif self._current_turn is not None and (
            session_id is None or self._current_turn.session_id == session_id
        ):
            abort_token = self._current_turn.token
        else:
            abort_token = None
        abort_pending_ids = self._rpc.pending_request_ids()
        result = await self._rpc.request("abort")
        # P1 (TOCTOU) / R2-5: the active sid above was read lock-free BEFORE
        # the abort RPC was awaited. In that gap the matching turn can end and
        # a DIFFERENT session's turn start (notably when a shielded
        # abort-on-disconnect outlives its turn's finally). In that case the
        # abort RPC itself has already flown — nothing can un-send it — but we
        # must NOT also ignite the NEW turn's token or fail its pending
        # futures, so skip both global effects and log the flip.
        active_after = self._active_turn_sid
        if (
            session_id is not None
            and active_after is not None
            and active_after != session_id
        ):
            logger.warning(
                "[PiBridge] abort(session_id=%s) TOCTOU: the active turn "
                "flipped %s -> %s while the abort RPC was in flight; skipping "
                "token-cancel/fail_all_pending so the new turn is not killed",
                session_id, active, active_after,
            )
            return result or {}
        # F24: ignite the SNAPSHOT turn's cancellation token so checkpoint()-
        # cooperative tool dispatches (HTTP callback path) stop promptly too,
        # not just the Pi subprocess. CONC-F1: cancel only the token the abort
        # was AIMED at — if a same-session successor turn replaced it, the
        # current token belongs to the new turn and must not be ignited
        # (cancelling the stale snapshot token is correct and harmless: its
        # turn is already gone).
        if abort_token is not None:
            abort_token.cancel("abort requested")
        # F03: remember WHO ordered this stop so the settle seam records the
        # honest terminal (user → cancelled / system|policy → aborted) even
        # though the vendor will answer the abort with a clean agent_settled.
        # review P3-4：fallback 与上方 token 解析同款会话守卫 —— 决不给
        # 别的会话的 turn 记结算来源（否则会把外来 turn 的终态翻转）。
        _abort_turn_id = getattr(_entry, "turn_id", "")
        if not _abort_turn_id and self._current_turn is not None and (
            session_id is None or self._current_turn.session_id == session_id
        ):
            _abort_turn_id = self._current_turn.turn_id
        record_turn_abort_source(_abort_turn_id, source)
        current_token = self._current_turn.token if self._current_turn else None
        if current_token is not abort_token and current_token is not None:
            logger.warning(
                "[PiBridge] abort TOCTOU: active turn token was replaced while "
                "the abort RPC was in flight; cancelled only the stale snapshot "
                "token and failed only the snapshot futures"
            )
        # Cancel pending futures from the SNAPSHOT only (CONC-F1): failing
        # everything would kill a successor turn's freshly registered prompt.
        self._rpc.fail_pending_ids(abort_pending_ids, "abort requested")
        return result or {}

    async def _drain_stale_events(self) -> int:
        """Drop any events left in the shared queue from a prior turn.

        Called at the start of each turn (under ``self._lock``) so that a
        previous turn's residual events — left behind after a consumer timeout
        or client disconnect — cannot be dequeued and attributed to this turn.
        Returns the number of events dropped. Bounded by the queue maxsize so
        a runaway producer can't make this loop unbounded.
        """
        dropped = 0
        last_type = ""
        events = self._rpc.events
        for _ in range(events.maxsize or 1024):
            try:
                event = events.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped += 1
            last_type = event.get("type", "") if isinstance(event, dict) else ""
        if dropped:
            logger.warning(
                "[PiBridge] drained %d stale event(s) from prior turn before "
                "starting this one (last type=%s); they would otherwise have "
                "been attributed to the new session",
                dropped, last_type,
            )
        return dropped

    async def _drain_remaining_turn_events(self) -> None:
        """Best-effort drain of this turn's leftover events after timeout/disconnect.

        Without this, a timed-out or cancelled stream leaves the turn's
        remaining events (up to ``agent_settled``, the sole turn terminator per
        #855 — ``agent_end`` alone may be followed by an auto-retry or
        continuation run) in the shared queue, where the next turn's drain loop
        would pick them up. Bounded and non-blocking so a stuck producer cannot
        block cleanup.
        """
        events = self._rpc.events
        for _ in range(events.maxsize or 1024):
            try:
                event = events.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(event, dict) and event.get("type") == "agent_settled":
                break

    async def _abort_on_disconnect(self, turn_sid: str) -> None:
        """Send the abort RPC after a client disconnect (transport goal B-P0-1).

        Without this, a client disconnect (page close / new send / session
        switch) leaves the Pi subprocess generating tokens and — critically —
        executing GIS tools via the ``/pi-tools/execute`` HTTP callback (DB/ref
        writes, possibly destructive ops) against a session the user has
        already left. ``abort()`` is lock-free by design (see its docstring) so
        it is safe to call from the turn's finally while the lock is still
        held. Best-effort: the vendor must honour the abort; a failure here
        must never raise into the generator-cleanup path.
        """
        try:
            # Scope the abort to THIS turn's session: the F5 guard inside
            # abort() then skips the global RPC/fail_all_pending when another
            # session's turn is already active (the shielded caller may resume
            # long after this turn ended and the lock was released).
            # F03：source="cleanup" —— 这是失败/断开 turn 的机械清理，不是
            # 独立终态成因；结算 seam 不消费它（否则会抢占 stall/total 的
            # 失败分类，或把超时 turn 误记成 cancelled）。
            await self.abort(session_id=turn_sid, source="cleanup")
            logger.info("[PiBridge] abort sent on client disconnect (turn=%s)", turn_sid)
        except Exception as e:  # noqa: BLE001 — abort failure must not break cleanup
            logger.warning("[PiBridge] abort-on-disconnect failed (turn=%s): %s", turn_sid, e)

    async def prompt(
        self,
        message: str,
        session_id: Optional[str] = None,
        cartography_context: Optional[str] = None,
        env_block: Optional[str] = None,
        *,
        context_org_id: str = "",
        context_project_id: str = "",
        context_user_id: str = "",
        context_query_text: str = "",
    ) -> dict:
        """Send a prompt to Pi agent (non-streaming).

        Args:
            message: User message
            session_id: Optional session ID
            cartography_context: Optional bounded harness verdict block,
                prepended ahead of the turn marker (see attach_turn_context).
                F04: the typed assembly path derives these blocks from the
                authorities directly; the structured ``context_*`` params
                below are the preferred input (the string is a compat input).
            context_org_id / context_project_id / context_user_id /
                context_query_text: typed provider inputs (tenant scoping +
                memory retrieval keys).

        Returns:
            Response dict with session_id and content

        Raises:
            PiRpcError: If the Pi agent returns an error or the request fails.
        """
        # Turn-scoped session id: attribution uses this local, never the
        # mutable self._session_id field (which a concurrent/preceding turn
        # could overwrite). Pi events carry no session of their own.
        turn_sid = session_id or ""
        from app.api.routes.pi_tools import get_bridge_secret
        from app.services.chat.pi_turn_context import bind_turn_prompt, issue_turn_token

        turn_id = _mint_turn_id()
        turn_token = issue_turn_token(get_bridge_secret(), turn_sid, turn_id)
        data: dict[str, Any] = {}
        if turn_sid:
            data["sessionId"] = turn_sid

        # Runtime observability: prompt() now mints a real turn_id (previously)
        # its cancellation token carried the *session* id as job_id, so the
        # non-stream path had no turn identity). run_id is a first-class turn
        # handle (the prior "run_id == session_id" reuse is retired here).
        # Do NOT remint: the signed turn token above is bound to turn_id.
        run_id = rt_ctx.new_run_id()
        rt_ev = TurnEvidence(
            request_id=rt_ctx.current_runtime_context().request_id if rt_ctx.current_runtime_context() else None,
            session_id=turn_sid or None,
            turn_id=turn_id,
            run_id=run_id,
        )

        # Hold the lock across send + drain so turns are strictly serial on the
        # singleton bridge. Pi processes one prompt at a time, so this matches
        # the real execution model and prevents two turns' events interleaving
        # in the shared queue. Abort bypasses the lock (see abort()).
        # #1108: acquire returns an explicit ownership lease; the outer finally
        # below is the absolute backstop that releases it (idempotent +
        # owner-checked), so no cancellation re-delivery can strand the lock.
        lease = await self._acquire_turn_lease(turn_id, turn_sid)
        try:
            # Clear caches only after the lock so a concurrent stream_prompt
            # cannot wipe another in-flight turn's dispatch results / dedup set.
            _session_executed_sets.pop(turn_sid, None)
            _clear_dispatch_cache(turn_sid)
            with rt_ctx.bind_runtime_context(turn_id=turn_id, run_id=run_id), bind_turn_evidence(rt_ev):
                TURN_EVIDENCE.register(rt_ev)
                cancelled = False
                timed_out = False
                send_failed = False
                # 方向 09（parity D6）：与 stream_prompt 同款超时分类
                # （"total" = 整回合预算；"stall" = 连续静默）。
                timeout_reason = ""
                # G/parity: initialized with the other flags so the finally's
                # process_died branch is safe even when an exception fires
                # before the drain loop assigns it.
                process_died = False
                # F03（P1 修复）：未分类异常必须结算成 failed —— 此前 flags
                # 全 False 让 _hk_turn_status 把崩溃 turn 记成 completed
                # （与 rt_ev 的 FAILED 自相矛盾，错误伪装成完成）。
                unclassified_error = False
                _error_detail = ""
                # F03（单结算 seam）：clean 收口的投影管线已在 try 内跑过时
                # 置位，seam 不再重复（幂等去重）。
                _projections_settled = False
                # #1108: initialize BEFORE register — a register failure must
                # not leave the finally referencing an unbound local.
                tracker_task_id = None
                # 方向 09（parity D2/D7）：非流式 turn 收口的完成度负载
                # （settle 管线产出，finally 的轨迹录制使用 —— 与流式对齐）。
                _turn_map_product = None
                try:
                    # F5/F24: publish the active turn's identity + cancellation token
                    # while the lock is held — abort() reads the sid for session
                    # scoping, dispatch_tool binds the token via use_token. Same as
                    # stream_prompt; cleared in the finally. P1: prompt() previously
                    # never set these, so abort(session_id=other) saw active=None and
                    # the GLOBAL abort killed this turn, and HTTP-callback tool
                    # dispatches ran unbounded (F24 no-op) on this path.
                    self._active_turn_sid = turn_sid
                    # V5-B: mint the token BEFORE registering so the session-
                    # keyed table carries it (dispatch_tool/abort resolve via
                    # the table, not the singleton slot).
                    _turn_token = CancellationToken(job_id=turn_id)
                    await register_active_pi_turn(
                        turn_sid, turn_id, token=_turn_token, bridge=self, run_id=run_id
                    )
                    # ADR-0180（Harness Kernel）：非流式 turn 同样记账（与
                    # stream_prompt 同语义；best-effort 绝不阻断）。
                    if turn_sid:
                        try:
                            from app.services.harness_kernel import get_runtime

                            await get_runtime(turn_sid).begin_turn(
                                turn_id, host="pi", message=message or ""
                            )
                        except Exception:
                            logger.warning(
                                "[PiBridge] kernel begin_turn (non-stream) failed session=%s turn=%s",
                                turn_sid, turn_id, exc_info=True,
                            )

                    try:
                        from app.services.chat.engine_instance import try_get_chat_engine
                        engine = try_get_chat_engine()
                        if engine is None:
                            raise RuntimeError("ChatEngine not initialized")
                        tracker_task = engine.tracker.create(turn_sid, message)
                        tracker_task_id = tracker_task.id
                    except Exception:
                        logger.debug(
                            "[agent_pi_bridge] tracker task create failed session=%s",
                            turn_sid, exc_info=True,
                        )

                    # Drop any residual events from a prior turn before sending, so they
                    # cannot be attributed to this turn.
                    await self._drain_stale_events()
                    data["message"] = await bind_turn_prompt(
                        message, turn_token, turn_sid, cartography_context or "",
                        env_block=env_block or "",
                        turn_id=turn_id,
                        org_id=context_org_id or "",
                        project_id=context_project_id or "",
                        user_id=context_user_id or "",
                        query_text=context_query_text or "",
                    )
                    try:
                        await self._rpc.request("prompt", data)
                    except PiRpcError as send_exc:
                        # #790 (B-6 parity with stream_prompt's ``send_failed``): the
                        # prompt RPC itself raised. Pi may already have started the
                        # turn and its tools, so the finally MUST abort — without it
                        # a retry duplicates side effects while this caller already
                        # returned an error.
                        send_failed = True
                        rt_ev.settle(
                            Outcome.FAILED,
                            failure_class="pi_send_error",
                            detail=str(send_exc)[:200],
                        )
                        raise

                    # Drain events from the queue (non-streaming mode).
                    # audit #816: message_update carries the ACCUMULATED partial
                    # snapshot — appending per event duplicated content O(n²).
                    # Keep only the latest snapshot; a non-retrying agent_end's
                    # messages[] list is the authoritative final text and overrides
                    # it. #855: agent_end is NOT the turn terminator — the vendor
                    # auto-retries behind willRetry=true and may continue even a
                    # willRetry=false run (compaction overflow recovery / queued
                    # follow-ups). The turn ends on agent_settled only, mirroring
                    # the vendor RpcClient.waitForIdle contract.
                    final_text = ""
                    _drained_complete = False
                    drain_started = time.monotonic()
                    last_event_at = drain_started
                    # #1069(A-5): 与 stream_prompt 同款 MagicMock 容错 —— 测试桩的
                    # process_died_event 可能不是 asyncio.Event，回退为永不置位。
                    process_died_event = getattr(self._rpc, "process_died_event", None)
                    if not isinstance(process_died_event, asyncio.Event):
                        process_died_event = asyncio.Event()
                    while True:
                        # #786: keep draining while events keep arriving. A single
                        # expired wait is NOT a turn failure — declare a stall only
                        # after CONTINUOUS silence reaching the stream path's stall
                        # budget (PI_EVENT_STREAM_TIMEOUT) or the bounded total-turn
                        # deadline, mirroring stream_prompt's silence accumulation.
                        now = time.monotonic()
                        remaining_stall = PI_EVENT_STREAM_TIMEOUT - (now - last_event_at)
                        remaining_total = PI_TURN_TOTAL_TIMEOUT - (now - drain_started)
                        wait_budget = min(
                            PI_EVENT_DRAIN_TIMEOUT, remaining_stall, remaining_total
                        )
                        if wait_budget <= 0:
                            timed_out = True
                            timeout_reason = "total" if remaining_total <= 0 else "stall"
                            break
                        # #1069(A-5): 与 stream_prompt 同款进程死亡看护 —— 此前
                        # 非流式 prompt 在子进程崩溃后只能挂满 stall 预算才报错。
                        get_task = asyncio.ensure_future(self._rpc.events.get())
                        died_task = asyncio.ensure_future(process_died_event.wait())
                        try:
                            done, _ = await asyncio.wait(
                                {get_task, died_task},
                                timeout=wait_budget,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if died_task in done:
                                process_died = True
                                break
                            if get_task in done:
                                event = get_task.result()
                                last_event_at = time.monotonic()
                                # 方向 09（parity D5）：非流式 drain 同样标记
                                # 首个真实 Pi 事件（first_event/TTFT-proxy）。
                                rt_ev.mark_first_event()
                                event_type = event.get("type")
                                if event_type == "agent_settled":
                                    _drained_complete = True
                                    break
                                if event_type == "agent_end":
                                    # #855: stash the authoritative final text only from
                                    # a non-retrying agent_end; a willRetry=true end
                                    # carries the transient error message, not the turn
                                    # outcome — the retried run supersedes it.
                                    if not event.get("willRetry"):
                                        end_text = _extract_text_from_event(event)
                                        if end_text:
                                            final_text = end_text
                                else:
                                    text = _extract_text_from_event(event)
                                    if text:
                                        final_text = text
                            else:
                                # 超时且两任务都未完成 —— 连续静默/总预算检查
                                if (
                                    time.monotonic() - last_event_at >= PI_EVENT_STREAM_TIMEOUT
                                    or time.monotonic() - drain_started >= PI_TURN_TOTAL_TIMEOUT
                                ):
                                    timed_out = True
                                    timeout_reason = (
                                        "total"
                                        if time.monotonic() - drain_started >= PI_TURN_TOTAL_TIMEOUT
                                        else "stall"
                                    )
                                    break
                        finally:
                            for _t in (get_task, died_task):
                                if not _t.done():
                                    _t.cancel()
                    # Best-effort: drain any leftover events so the next turn starts clean.
                    await self._drain_remaining_turn_events()
                    if process_died:
                        # 子进程死亡：reader 的 finally 已失败所有 pending future；
                        # 无需 abort RPC（进程已不在），外层 finally 会按需 respawn。
                        rt_ev.settle(Outcome.FAILED, failure_class="process_died")
                        raise PiRpcError(
                            "Pi subprocess died mid-turn (non-streaming prompt); "
                            "the turn did not complete."
                        )
                    if timed_out:
                        # #554 defect 2: a drain that timed out without agent_settled
                        # is a PARTIAL turn. Previously the caller received a 200 with
                        # truncated content and NO abort RPC — Pi kept generating
                        # tokens and executing tools (up to the 300s RPC timeout)
                        # while the client believed the turn succeeded. Surface an
                        # error instead; the finally below sends the abort RPC
                        # (mirrors stream_prompt's stall handling). 方向 09
                        # （parity D6）：failure_class 与流式同 taxonomy
                        # （pi_turn_budget / pi_stall），不再用私有 drain_timeout。
                        rt_ev.settle(
                            Outcome.FAILED,
                            failure_class=(
                                "pi_turn_budget" if timeout_reason == "total" else "pi_stall"
                            ),
                        )
                        raise PiRpcError(
                            f"Pi agent did not emit agent_settled (continuous silence exceeded "
                            f"{PI_EVENT_STREAM_TIMEOUT}s or the turn exceeded "
                            f"{PI_TURN_TOTAL_TIMEOUT}s; event-drain timeout); "
                            "the turn has been aborted."
                        )
                    # Only a clean agent_settled reaches this point — a timeout
                    # already raised above, so PARTIAL is no longer reachable.
                    # F03：若本 turn 已被 abort（user/system/policy），SUCCEEDED
                    # 不在此预结算 —— 单结算 seam 按 abort 来源落诚实终态
                    # （first-wins，这里抢结算会掩盖 cancel/abort）。
                    if not _TURN_ABORT_SOURCES.get(turn_id):
                        rt_ev.settle(Outcome.SUCCEEDED)
                    # 方向 09（parity D2/D3/D4）：非流式清洁收口与流式
                    # agent_settled 共用同一 turn 结算披露管线（完成度终验
                    # final gate → WorkflowInstance → RuntimeState(turn_settled)
                    # → 上下文 checkpoint → 证据链 USER_OUTPUT + 持久化）。
                    # 幂等门兜底；逐段 never-raise，绝不阻断响应返回。
                    # F03：本 turn 已被 abort（user/system/policy）时不在此
                    # 预跑 clean 投影（完成度奖励不属于被中止的 turn）——
                    # 单结算 seam 会以 reduced outcome 收口。
                    if turn_sid and not _TURN_ABORT_SOURCES.get(turn_id):
                        try:
                            from app.services.chat.pi_post_dispatch import (
                                settle_turn_projections,
                            )
                            _turn_map_product = await settle_turn_projections(
                                turn_sid, turn_id, reason="turn_settled",
                            )
                            _projections_settled = True
                        except Exception:  # noqa: BLE001 — 增值披露，绝不阻断返回
                            logger.exception(
                                "[PiBridge] turn settle pipeline failed session=%s",
                                turn_sid,
                            )
                except asyncio.CancelledError:
                    cancelled = True
                    rt_ev.settle(Outcome.CANCELLED)
                    raise
                except Exception as exc:  # noqa: BLE001
                    # F03（P1 修复）：崩溃 turn 结算成 failed，绝不伪装成
                    # completed（rt_ev 同帧记 FAILED，此前两者自相矛盾）。
                    unclassified_error = True
                    _error_detail = str(exc)[:200]
                    rt_ev.settle(Outcome.FAILED, failure_class=type(exc).__name__, detail=str(exc)[:200])
                    raise
                finally:
                    if process_died:
                        # G-parity with stream_prompt: the subprocess is dead
                        # (reader already failed pending futures; an abort RPC
                        # would only raise) — still ignite THIS turn's token so
                        # in-flight HTTP-callback tool dispatches bound via
                        # use_token stop at their next checkpoint instead of
                        # running to completion against a dead turn.
                        if _turn_token is not None:
                            _turn_token.cancel("pi process exited unexpectedly")
                    elif cancelled or timed_out or send_failed:
                        # #554 defect 2: tell Pi to stop generating tokens / executing
                        # tools when the non-streaming turn ends without a clean
                        # agent_settled (drain timeout) or is cancelled mid-drain — a
                        # retry otherwise duplicates side effects. #790: a failed
                        # prompt RPC (send_failed) must abort for the same reason
                        # (B-6, mirroring stream_prompt). Mirrors stream_prompt's
                        # finally guard; abort() is lock-free by design, so it is
                        # safe to call while the lock is still held.
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(self._abort_on_disconnect(turn_sid)),
                                timeout=5.0,
                            )
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            pass
                        except Exception as e:  # noqa: BLE001
                            logger.warning("[PiBridge] abort-on-failed-turn (turn=%s): %s", turn_sid, e)
                    _cleanup_turn_state(turn_sid)
                    # Clear the active-turn markers before releasing the lock.
                    self._active_turn_sid = None
                    # F03（ADR-0204-f03 D1）：单结算 seam —— abort 来源消费、
                    # 拒答降级、outcome-aware 投影（error/cancel/abort 也收口）、
                    # kernel end_turn 与 tracker 结算全部收敛于这一处，与
                    # stream_prompt 共用同一实现。必须在释放 turn lease 之前
                    # （review S1：并发下一 turn 的 begin_turn 不得看到本
                    # turn 仍 running）。shield + 吞异常在 seam 内部保证。
                    _hk_status = ""
                    if turn_sid:
                        _hk_status = await self._settle_turn_outcome(
                            session_id=turn_sid,
                            turn_id=turn_id,
                            tracker_task_id=tracker_task_id,
                            cancelled=cancelled,
                            timed_out=timed_out,
                            send_failed=send_failed,
                            process_died=process_died,
                            error=unclassified_error,
                            error_detail=_error_detail,
                            timeout_reason=timeout_reason,
                            projections_settled=_projections_settled,
                        )
                    # F03：诊断面与权威终态对齐 —— drain 退出点因 abort 让位
                    # 未结算的，由 seam 记录的 status 补结算（first-wins，
                    # 已结算者为 no-op）。
                    if _hk_status == "cancelled":
                        rt_ev.settle(Outcome.CANCELLED)
                    elif _hk_status in ("failed", "aborted"):
                        if unclassified_error:
                            _failure_class = "pi_unclassified_error"
                        elif _hk_status == "aborted":
                            _failure_class = "pi_aborted"
                        else:
                            _failure_class = "pi_turn_error"
                        rt_ev.settle(
                            Outcome.FAILED,
                            failure_class=_failure_class,
                            detail=_error_detail[:200],
                        )
                    if self._current_turn is not None and self._current_turn.turn_id == turn_id:
                        self._current_turn = None
                    # #1108 INV-P4: release the lease BEFORE the unregister await —
                    # the release is synchronous (uncancellable) and the unregister
                    # is a best-effort shielded call, so a re-delivered
                    # CancelledError during Redis I/O can no longer skip the
                    # release. The outer finally's backstop release is a no-op then.
                    self._release_turn_lease(lease)
                    await self._safe_unregister_active_pi_turn(turn_sid, turn_id)
                    rt_ev.mark_ended()
                    emit_turn_summary(rt_ev)
                    # R10（ADR-0183）：env-gated 轨迹录制（默认关闸 no-op；
                    # never-raises；记录面绝不阻断 settle）——非流式 prompt 路径。
                    try:
                        from app.lib.harness.replay.recorder import (
                            maybe_record_turn,
                        )
                        maybe_record_turn(
                            session_id=turn_sid,
                            turn_id=turn_id,
                            final_text=final_text,
                            map_product=(
                                _turn_map_product
                                if isinstance(_turn_map_product, dict)
                                else None
                            ),
                        )
                    except Exception:  # noqa: BLE001 — 记录面绝不阻断 settle
                        pass
                    TURN_EVIDENCE.remove(turn_id)
        finally:
            # #1108 INV-P1: backstop — whatever happened above (including a
            # cancellation delivered inside the inner finally before its own
            # release), the lease is released here exactly once.
            self._active_turn_sid = None
            if self._current_turn is not None and self._current_turn.turn_id == turn_id:
                self._current_turn = None
            self._release_turn_lease(lease)
            await self._safe_unregister_active_pi_turn(turn_sid, turn_id)
            TURN_EVIDENCE.remove(turn_id)
            _cleanup_turn_state(turn_sid)

        return {
            "sessionId": turn_sid,
            "content": final_text,
        }

    async def stream_prompt(
        self,
        message: str,
        session_id: Optional[str] = None,
        cartography_context: Optional[str] = None,
        on_turn_result: Optional[Callable[[dict], Any]] = None,
        env_block: Optional[str] = None,
        *,
        context_org_id: str = "",
        context_project_id: str = "",
        context_user_id: str = "",
        context_query_text: str = "",
    ) -> AsyncGenerator[str, None]:
        """Stream a prompt to Pi agent, yielding SSE events.

        Args:
            message: User message
            session_id: Optional session ID
            cartography_context: Optional bounded harness verdict block,
                prepended ahead of the turn marker (see attach_turn_context).
            on_turn_result: Optional callback invoked once in the turn's
                finally with {"session_id", "turn_id", "final_text",
                "completed"} (audit #818: lets the route persist the turn
                transcript without re-parsing SSE strings). May be sync or
                async.

        Yields:
            SSE-formatted event strings
        """
        # Turn-scoped session id: every SSE payload is stamped with this local
        # value, not the mutable self._session_id field. Pi events carry no
        # session of their own, so attribution must come from the request that
        # owns this turn — not bridge instance state a prior turn could have
        # overwritten.
        turn_sid = session_id or ""
        from app.api.routes.pi_tools import get_bridge_secret
        from app.services.chat.pi_turn_context import bind_turn_prompt, issue_turn_token

        turn_id = _mint_turn_id()
        turn_token = issue_turn_token(get_bridge_secret(), turn_sid, turn_id)
        data: dict[str, Any] = {}
        if turn_sid:
            data["sessionId"] = turn_sid

        # V3: 每个 Pi turn 铸一个 turn_id，显式透传给 harness 记录与 SSE 负载
        # （绝不用全局 set_correlation —— 单例 harness 跨 session 累积会互相污染）。
        # Runtime observability: first-class run_id (retires "run_id == session_id").
        # turn_id is already minted above so the signed turn token stays consistent.
        run_id = rt_ctx.new_run_id()
        _parent_ctx = rt_ctx.current_runtime_context()
        rt_ev = TurnEvidence(
            request_id=_parent_ctx.request_id if _parent_ctx else None,
            session_id=turn_sid or None,
            turn_id=turn_id,
            run_id=run_id,
        )

        # Hold the lock across send + drain + cleanup so turns are strictly
        # serial on the singleton bridge (Pi processes one prompt at a time).
        # try/finally ensures a client-disconnect GeneratorExit/CancelledError
        # at the yield still releases the lock, drains residual events, AND
        # (transport goal B-P0-1) sends an abort RPC so Pi stops generating
        # tokens and executing tools against an abandoned session.
        # Abort deliberately bypasses this lock (see abort()).
        with rt_ctx.bind_runtime_context(turn_id=turn_id, run_id=run_id), bind_turn_evidence(rt_ev):
            TURN_EVIDENCE.register(rt_ev)
            # #554 defect 1: the turn lock is held across the ENTIRE previous
            # turn (send + drain + cleanup), so a concurrent second stream
            # blocks here for up to the holder's whole turn (PI_RPC_TIMEOUT=300s
            # worst case). The SSE headers have already flushed by this point
            # (StreamingResponse starts iterating), yet ZERO bytes flowed while
            # waiting — proxies/LBs with shorter idle timeouts dropped user 2's
            # connection before their turn even started. Poll the lock and emit
            # a keepalive SSE comment (ignored by the client parser, bytes on
            # the wire) at the heartbeat cadence until acquired.
            # #1108: acquisition mints an explicit ownership lease; everything
            # after it (register included — INV-P3) sits inside a try whose
            # outer-finally backstop releases the lease, so a cancellation
            # delivered at ANY await below can no longer strand the lock.
            while True:
                try:
                    lease = await asyncio.wait_for(
                        self._acquire_turn_lease(turn_id, turn_sid),
                        timeout=PI_HEARTBEAT_INTERVAL,
                    )
                    break
                except asyncio.TimeoutError:
                    # MINOR (phase-E A): a client disconnect at this yield
                    # (GeneratorExit) precedes the lease-protected try — the
                    # evidence registered above would never be removed. Park
                    # the registration removal here for that early-exit path.
                    try:
                        yield ": keepalive\n\n"
                    except (GeneratorExit, asyncio.CancelledError):
                        rt_ev.settle(Outcome.CANCELLED)
                        rt_ev.mark_ended()
                        emit_turn_summary(rt_ev)
                        TURN_EVIDENCE.remove(turn_id)
                        raise
                except asyncio.CancelledError:
                    # Same early-exit discipline for a cancellation delivered
                    # AT the wait_for await (disconnect while queued behind
                    # another same-session turn): the evidence registered
                    # above must not outlive the abandoned generator.
                    rt_ev.settle(Outcome.CANCELLED)
                    rt_ev.mark_ended()
                    emit_turn_summary(rt_ev)
                    TURN_EVIDENCE.remove(turn_id)
                    raise
            try:
                _session_executed_sets.pop(turn_sid, None)
                _clear_dispatch_cache(turn_sid)
                cancelled = False
                timed_out = False
                send_failed = False
                # 方向 09（parity D6）：超时分类（"total" | "stall" | ""）。
                # 必须与其他 flags 同点初始化 —— send_failed 早退/注册期取消
                # 的 finally 会经单结算 seam 读取它（此前在 send 之后才初始化，
                # 早退路径 UnboundLocalError）。
                timeout_reason = ""
                # G: set True when the pump observes the Pi subprocess dying mid-stream
                # (see the process_died_event watcher below). Initialized here so the
                # finally can read it even on the early-return send-failure path.
                process_died = False
                # F03：finally 读取的 turn 统计面（轨迹录制）与 flags 同点
                # 初始化 —— send-failed 早退路径此前未绑定（被 except 吞掉，
                # 录制静默丢失），现在所有早退路径都有诚实缺省值。
                _turn_map_product = None
                _turn_final_text = ""
                _turn_tool_steps = 0
                # F03（P1 修复）：stream 路径此前没有 generic except —— 任何
                # 异常穿透 try 落进 finally，flags 全 False 被结算成 completed。
                # 显式捕获置位后 re-raise，finally 的单结算 seam 记 failed。
                unclassified_error = False
                _error_detail = ""
                # F03（单结算 seam）：clean agent_settled 的投影管线在事件循环
                # 内已跑过时置位，seam 不再重复（幂等去重）。
                _projections_settled = False
                # #1108: initialize BEFORE register — a register failure must
                # not leave the finally referencing an unbound local.
                tracker_task_id = None
                # F5/F24: publish the active turn's identity + cancellation token while
                # the lock is held — abort() reads the sid for session scoping,
                # dispatch_tool binds the token via use_token. Cleared in the finally.
                self._active_turn_sid = turn_sid
                # #1108 INV-P3: register sits INSIDE the lease-protected try —
                # a cancellation/Redis failure during registration is funneled
                # into the finally (abort + release) instead of leaking the lock.
                # V5-B: token minted first, registered WITH the session table.
                _turn_token = CancellationToken(job_id=turn_id)
                await register_active_pi_turn(
                    turn_sid, turn_id, token=_turn_token, bridge=self, run_id=run_id
                )
                # ADR-0180（Harness Kernel）：turn 开始 → GIS 会话运行时记账
                # （turn 台账 + 上一 turn 中断检测 + resume 计数）。增值披露，
                # 绝不阻断 turn；锁竞争沿用 SessionPlan 语义由 runtime 内处理。
                if turn_sid:
                    try:
                        from app.services.harness_kernel import get_runtime

                        await get_runtime(turn_sid).begin_turn(
                            turn_id, host="pi", message=message or ""
                        )
                    except Exception:
                        logger.warning(
                            "[PiBridge] kernel begin_turn failed session=%s turn=%s",
                            turn_sid, turn_id, exc_info=True,
                        )

                try:
                    from app.services.chat.engine_instance import try_get_chat_engine
                    engine = try_get_chat_engine()
                    if engine is None:
                        raise RuntimeError("ChatEngine not initialized")
                    tracker_task = engine.tracker.create(turn_sid, message)
                    tracker_task_id = tracker_task.id
                except Exception:
                    logger.debug(
                        "[agent_pi_bridge] tracker task create failed session=%s",
                        turn_sid, exc_info=True,
                    )

                try:
                    # Drop residual events from a prior turn so they can't be dequeued
                    # and attributed to this session.
                    await self._drain_stale_events()
                    data["message"] = await bind_turn_prompt(
                        message, turn_token, turn_sid, cartography_context or "",
                        env_block=env_block or "",
                        turn_id=turn_id,
                        org_id=context_org_id or "",
                        project_id=context_project_id or "",
                        user_id=context_user_id or "",
                        query_text=context_query_text or "",
                    )

                    # Send prompt command
                    try:
                        await self._rpc.request("prompt", data)
                    except PiRpcError as e:
                        logger.error(f"[PiBridge] stream_prompt send failed: {e}")
                        send_failed = True
                        yield sse_event("task_error", {
                            "task_id": turn_sid,
                            "session_id": turn_sid,
                            "turn_id": turn_id,
                            "error": str(e),
                        })
                        yield sse_event("done", {"session_id": turn_sid})
                        return

                    # Stream events from Pi. task_start is a bridge-emitted structural
                    # event (not a Pi event), so first_event is NOT marked here — it is
                    # marked on the first REAL Pi event below, so first_event/TTFT-proxy
                    # measures prompt→first-Pi-event, not lock+drain+RPC-send.
                    yield sse_event("task_start", {
                        "task_id": turn_sid,
                        "session_id": turn_sid,
                        "turn_id": turn_id,
                        "agent_runtime": "pi",
                    })

                    # G: watch the subprocess death signal alongside the event queue so
                    # a mid-stream Pi crash ends the turn promptly (error + done +
                    # no abort) instead of parking on heartbeat silence for the whole
                    # PI_EVENT_STREAM_TIMEOUT stall budget (default 120s) and then retrying with
                    # duplicate side effects. Bare MagicMock rpc fakes (used by other
                    # test suites) auto-create a MagicMock for ``process_died_event``,
                    # which is not awaitable — fall back to a never-set event so those
                    # fakes degrade to the old heartbeat-only behavior.
                    process_died_event = getattr(self._rpc, "process_died_event", None)
                    if not isinstance(process_died_event, asyncio.Event):
                        process_died_event = asyncio.Event()

                    silence_seconds = 0.0
                    # #982: whole-turn wall-clock budget. The stall budget below only
                    # covers CONTINUOUS silence — a drip-feed event stream (e.g.
                    # tool_execution_start/end pairs every <PI_EVENT_STREAM_TIMEOUT)
                    # could previously keep a dead-looped turn alive forever. Same
                    # env constant as the non-streaming drain (PI_TURN_TOTAL_TIMEOUT).
                    turn_started = time.monotonic()
                    # "" | "stall" | "total" — distinguishes the two timeout budgets
                    # in the error payload and the failure classification.
                    timeout_reason = ""
                    # audit #820: turn-scoped counters（_turn_tool_steps /
                    # _turn_final_text / _turn_map_product）已随 flags 块
                    # 前置初始化 —— send-failed 早退路径也有诚实缺省值。
                    get_task = asyncio.ensure_future(self._rpc.events.get())
                    died_task = asyncio.ensure_future(process_died_event.wait())
                    pending = {get_task, died_task}
                    try:
                        while True:
                            done, pending = await asyncio.wait(
                                pending, timeout=PI_HEARTBEAT_INTERVAL,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            # #982: the total-budget check runs EVERY iteration, not
                            # only in the silence branch — a steady drip of events
                            # (each resetting silence_seconds) must not exempt a turn
                            # from the whole-turn deadline.
                            if time.monotonic() - turn_started >= PI_TURN_TOTAL_TIMEOUT:
                                timed_out = True
                                timeout_reason = "total"
                                break
                            if died_task in done:
                                # Pi subprocess died mid-stream: the reader's finally
                                # already failed every pending future; end the turn now
                                # (error + done below), no abort RPC needed.
                                process_died = True
                                break
                            if get_task in done:
                                event = get_task.result()
                                silence_seconds = 0.0
                                rt_ev.mark_first_event()
                                # audit #820: truthful task_complete counters — count
                                # executed tool steps and accumulate the final text
                                # the vendor streams (message.content snapshots).
                                if event.get("type") == "tool_execution_end":
                                    _turn_tool_steps += 1
                                if event.get("type") == "message_update":
                                    _snap = _extract_text_from_event(event)
                                    if _snap:
                                        _turn_final_text = _snap
                                # ADR-0081：turn 收尾终验 —— 在 task_complete 映射
                                # 之前运行，兜住不经 SessionPlan 的展示类工具与
                                # finalization repair 之后的新 desired state；结果
                                # 经 turn_stats 进入 task_complete 负载（additive），
                                # 并以独立 map_finalization SSE 事件披露给前端
                                # finalizer（视口校验/修复在前端）。
                                _turn_map_product = None
                                if event.get("type") == "agent_settled":
                                    # 方向 09（ADR-0210）：turn 收口披露管线与
                                    # 非流式 prompt 清洁收口共用 —— 完成度终验
                                    # （final gate）→ WorkflowInstance →
                                    # RuntimeState(turn_settled) → 上下文
                                    # checkpoint → 证据链 USER_OUTPUT + 持久化。
                                    # 幂等门兜底；逐段 never-raise。
                                    # F03：已 abort 的 turn 不在此预跑 clean
                                    # 投影（完成度奖励不属于被中止的 turn）。
                                    if not _TURN_ABORT_SOURCES.get(turn_id):
                                        from app.services.chat.pi_post_dispatch import (
                                            settle_turn_projections,
                                        )
                                        _turn_map_product = await settle_turn_projections(
                                            turn_sid, turn_id, reason="turn_settled",
                                        )
                                        _projections_settled = True
                                sse = map_event_to_sse(
                                    event,
                                    turn_sid,
                                    cache_lookup=lambda tool_call_id, _sid=turn_sid: get_cached_dispatch_result(
                                        tool_call_id, _sid
                                    ),
                                    turn_stats=lambda: {
                                        "tool_step_count": _turn_tool_steps,
                                        "final_text": _turn_final_text,
                                        "map_product": _turn_map_product,
                                    },
                                )
                                # V3: 给原始 SSE 事件记录补 turn/run correlation（显式透传）。
                                # B-8: record against THIS turn's session harness, not the
                                # module-global "most recently created" harness — otherwise
                                # two interleaved sessions misattribute one session's events
                                # to the other (record_sse_event stamps self.session_id).
                                turn_harness = _get_session_harness(turn_sid)
                                if turn_harness is not None:
                                    turn_harness.record_sse_event({
                                        **event, "run_id": run_id, "turn_id": turn_id,
                                    })
                                if event.get("type") == "tool_execution_end":
                                    extra_plan = take_session_plan_sse(
                                        str(event.get("toolCallId") or ""), turn_sid,
                                    )
                                    if extra_plan:
                                        sse = (sse or "") + extra_plan
                                if sse:
                                    rt_ev.inc_sse_event()
                                    # V3: step_result 负载加 additive turn_id 字段（前端 ack 时
                                    # 通过 correlation 回传，闭环才完整）。
                                    yield _inject_turn_id(sse, turn_id)
                                if event.get("type") == "agent_end":
                                    # #855: agent_end is NOT final — willRetry=true
                                    # precedes a vendor auto-retry, and even
                                    # willRetry=false may be followed by a compaction
                                    # or queued-message continuation run. Stash the
                                    # authoritative final text only from a
                                    # non-retrying agent_end (audit #818/#816); the
                                    # stream ends on agent_settled below, matching
                                    # vendor RpcClient.waitForIdle. task_complete is
                                    # emitted by the mapper on agent_settled.
                                    if not event.get("willRetry"):
                                        _end_text = _extract_text_from_event(event)
                                        if _end_text:
                                            _turn_final_text = _end_text
                                elif event.get("type") == "agent_settled":
                                    if _turn_map_product is not None:
                                        yield sse_event(
                                            "map_finalization", _turn_map_product
                                        )
                                    break
                                # Re-arm the queue waiter for the next event; the
                                # completed waiter task is dropped from `pending` by
                                # the next asyncio.wait round.
                                get_task = asyncio.ensure_future(self._rpc.events.get())
                                pending.add(get_task)
                            else:
                                # Neither an event nor a death within one heartbeat
                                # interval -> accumulate silence, keepalive.
                                silence_seconds += PI_HEARTBEAT_INTERVAL
                                if silence_seconds >= PI_EVENT_STREAM_TIMEOUT:
                                    # True stall: no Pi event for the whole stall budget.
                                    timed_out = True
                                    timeout_reason = "stall"
                                    break
                                # Keepalive: an SSE comment line (``: ...``) is ignored by
                                # the client parser and never enters chat history or the
                                # LLM context, but it produces bytes on the wire so proxies
                                # and browsers see activity and don't drop the connection.
                                yield ": keepalive\n\n"
                    finally:
                        # G: cancel the parked wait tasks so no queue-get / death-wait
                        # task outlives the turn; await their completion so nothing
                        # lingers past loop teardown. Also covers generator
                        # cancellation (client disconnect) mid-await.
                        for task in pending:
                            task.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)

                    if timed_out:
                        if timeout_reason == "total":
                            yield sse_event("error", {
                                "session_id": turn_sid,
                                "error": (
                                    f"Pi agent turn exceeded the total budget of "
                                    f"{int(PI_TURN_TOTAL_TIMEOUT)}s — events were still "
                                    "flowing but the whole turn ran too long. The turn "
                                    "has been aborted; please retry with a narrower request."
                                ),
                            })
                        else:
                            yield sse_event("error", {
                                "session_id": turn_sid,
                                "error": f"Pi agent stalled — no events for {int(PI_EVENT_STREAM_TIMEOUT)}s. The agent may be stuck; please retry.",
                            })
                    if process_died:
                        yield sse_event("error", {
                            "session_id": turn_sid,
                            "error": "Pi agent process exited unexpectedly mid-stream. The agent's tools may have run partially; please retry.",
                        })

                    yield sse_event("done", {"session_id": turn_sid})
                except Exception as exc:
                    # F03（P1 修复）：stream 路径的未分类异常此前直接穿透到
                    # finally 且 flags 全 False —— 崩溃 turn 被结算成
                    # completed。置位后 re-raise，单结算 seam 记 failed。
                    unclassified_error = True
                    _error_detail = str(exc)[:200]
                    raise
                except (asyncio.CancelledError, GeneratorExit):
                    # Client disconnected (page close / new send / session switch /
                    # network drop). Re-raise after flagging so the finally sends the
                    # abort RPC — otherwise Pi keeps running against an abandoned turn.
                    cancelled = True
                    raise
                finally:
                    if process_died:
                        # G: the Pi subprocess is dead — the reader's finally already
                        # failed every pending future, so an abort RPC would only
                        # raise 'Pi process not started' (duplicate of the fast-fail).
                        # Still ignite the turn's cancellation token so in-flight
                        # HTTP-callback tool dispatches (bound via use_token) stop at
                        # their next checkpoint() instead of running to completion
                        # against a dead turn.
                        logger.error(
                            "[PiBridge] Pi subprocess exited mid-stream (turn=%s); "
                            "skipping abort RPC (reader already failed pending futures)",
                            turn_sid,
                        )
                        # V5-B: cancel THIS turn's token (session-table
                        # accurate under a pool), not the global slot.
                        if _turn_token is not None:
                            _turn_token.cancel("pi process exited unexpectedly")
                    elif cancelled or timed_out or send_failed:
                        # Tell Pi to stop generating tokens / executing tools. F10:
                        # this now covers the stall-timeout path too — previously a
                        # stalled turn yielded error+done and returned WITHOUT the
                        # abort RPC, so Pi kept executing tools (up to the 300s RPC
                        # timeout) and a user retry duplicated side effects.
                        # B-6: ``send_failed`` (the prompt RPC raised) must also
                        # abort — Pi may already have started executing the prompt
                        # and its tools, so without the abort a retry duplicates the
                        # side effects (same class of bug F10 fixed for the stall).
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(self._abort_on_disconnect(turn_sid)),
                                timeout=5.0,
                            )
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            pass
                        except Exception as e:  # noqa: BLE001
                            logger.warning("[PiBridge] abort-on-disconnect failed (turn=%s): %s", turn_sid, e)
                    # Drain any leftover events so a timeout/disconnect doesn't poison
                    # the next turn. (On a normal agent_settled the queue is already
                    # empty; this is a no-op there.)
                    await self._drain_remaining_turn_events()
                    # NB: _drain_remaining_turn_events contains no suspension
                    # points (get_nowait loop only), so a re-delivered
                    # cancellation cannot interrupt it mid-cleanup (#1108).
                    # Turn-end cleanup: drop this turn's dedup sets (incl. the "" bucket
                    # where sessionId-less Pi dispatches land) and orphaned dispatch
                    # results (written by the HTTP callback, never consumed after a
                    # disconnect). Without this they accumulate across sessions.
                    _cleanup_turn_state(turn_sid)
                    # Clear the active-turn markers AFTER the abort above (abort reads
                    # them to cancel the token) and before releasing the lock.
                    self._active_turn_sid = None
                    if self._current_turn is not None and self._current_turn.turn_id == turn_id:
                        self._current_turn = None
                    # F03（ADR-0204-f03 D1）：单结算 seam —— abort 来源消费、
                    # 拒答降级、outcome-aware 投影（error/cancel/abort 也收口）、
                    # kernel end_turn 与 tracker 结算全部收敛于这一处，与
                    # non-stream prompt 共用同一实现。必须在释放 turn lease
                    # 之前（review S1：并发下一 turn 的 begin_turn 不得看到
                    # 本 turn 仍 running）。shield + 预算 + 吞异常在 seam 内。
                    _hk_status = ""
                    if turn_sid:
                        _hk_status = await self._settle_turn_outcome(
                            session_id=turn_sid,
                            turn_id=turn_id,
                            tracker_task_id=tracker_task_id,
                            cancelled=cancelled,
                            timed_out=timed_out,
                            send_failed=send_failed,
                            process_died=process_died,
                            error=unclassified_error,
                            error_detail=_error_detail,
                            timeout_reason=timeout_reason,
                            projections_settled=_projections_settled,
                        )
                    # #1108 INV-P4: release the lease BEFORE the unregister
                    # await — the release is synchronous (uncancellable) and
                    # the unregister is shielded best-effort, so a re-delivered
                    # CancelledError during Redis I/O can no longer skip the
                    # release and hang every session on the singleton bridge.
                    self._release_turn_lease(lease)
                    await self._safe_unregister_active_pi_turn(turn_sid, turn_id)
                    # Runtime observability: settle turn outcome from the SAME
                    # status the seam recorded（F03：诊断面与权威终态不再各有
                    # 各的判断 —— abort/未分类异常此前在此显示 SUCCEEDED）。
                    # review P3-6：aborted 终态的 failure_class 统一为
                    # ``pi_aborted``（seam 的 TurnSettleOutcome 侧保留更细的
                    # ``pi_abort_<source>`` —— 单一定义点在 _ABORT_SOURCE_STATUS）。
                    if _hk_status == "cancelled":
                        rt_ev.settle(Outcome.CANCELLED)
                    elif _hk_status in ("failed", "aborted"):
                        if timed_out:
                            # #982: distinguish whole-turn budget exhaustion from a
                            # heartbeat stall in the failure classification.
                            failure_class = (
                                "pi_turn_budget" if timeout_reason == "total" else "pi_stall"
                            )
                        elif process_died:
                            failure_class = "pi_process_died"
                        elif send_failed:
                            failure_class = "pi_send_error"
                        elif unclassified_error:
                            failure_class = "pi_unclassified_error"
                        elif _hk_status == "aborted":
                            failure_class = "pi_aborted"
                        else:
                            failure_class = "pi_turn_error"
                        rt_ev.settle(Outcome.FAILED, failure_class=failure_class)
                    else:
                        rt_ev.settle(Outcome.SUCCEEDED)
                    rt_ev.mark_ended()
                    emit_turn_summary(rt_ev)
                    # R10（ADR-0183）：env-gated 轨迹录制（默认关闸 no-op；
                    # never-raises；只读收集本 turn 的 chain/summary/product
                    # 打包为离线重放 artifact）。记录面绝不阻断 settle。
                    try:
                        from app.lib.harness.replay.recorder import (
                            maybe_record_turn,
                        )
                        maybe_record_turn(
                            session_id=turn_sid,
                            turn_id=turn_id,
                            final_text=_turn_final_text,
                            map_product=(
                                _turn_map_product
                                if isinstance(_turn_map_product, dict)
                                else None
                            ),
                        )
                    except Exception:  # noqa: BLE001 — 记录面绝不阻断 settle
                        pass
                    TURN_EVIDENCE.remove(turn_id)
                    # F03：tracker 结算已收敛进单结算 seam（kernel end_turn
                    # 同一实现；cancelled→cancel、failed/aborted→fail、其余
                    # →complete），此处不再重复第二份 finally 记账。
                    # audit #818: surface the turn's final transcript state to the
                    # route (persistence parity with the legacy path). Best-effort —
                    # a sink failure must never mask the stream outcome.
                    if on_turn_result is not None:
                        try:
                            _res = {
                                "session_id": turn_sid,
                                "turn_id": turn_id,
                                "final_text": _turn_final_text,
                                "completed": rt_ev.outcome.outcome == Outcome.SUCCEEDED,
                            }
                            _r = on_turn_result(_res)
                            if asyncio.iscoroutine(_r):
                                await _r
                        except Exception as e:  # noqa: BLE001
                            logger.warning(
                                "[PiBridge] on_turn_result sink failed (turn=%s): %s", turn_id, e
                            )
            finally:
                # #1108 INV-P1: backstop — whatever happened above
                # (including a cancellation delivered inside the inner
                # finally before its own release), the lease is released
                # here exactly once. Owner-checked, so a stale lease from a
                # long-gone turn can never release the CURRENT turn's lock.
                self._active_turn_sid = None
                if self._current_turn is not None and self._current_turn.turn_id == turn_id:
                    self._current_turn = None
                self._release_turn_lease(lease)
                # INV-P3 corollary (review V5-B-3): a cancellation delivered
                # while ``register_active_pi_turn`` awaits its Redis I/O
                # propagates out BEFORE the inner finally ever runs — but the
                # session table entry was already written synchronously, so
                # without this backstop the ghost entry (and its
                # eviction-protection) leaks until process restart. The call
                # is turn-id-checked (no-op for a successor's entry) and
                # idempotent on the normal path where the inner finally
                # already unregistered.
                await self._safe_unregister_active_pi_turn(turn_sid, turn_id)
                TURN_EVIDENCE.remove(turn_id)
                _cleanup_turn_state(turn_sid)



# Global bridge instance
_pi_bridge: Optional[PiBridge] = None


async def get_pi_bridge(
    extension_paths: Optional[list[str]] = None,
    session_id: Optional[str] = None,
) -> PiBridge:
    """Return the bridge that should run a turn (V5-B pool-aware).

    ``PI_BRIDGE_POOL_SIZE`` (default 1) controls how many Pi subprocesses the
    process hosts. With the default of 1 this is the historical singleton —
    byte-identical behavior. With N>1, sessions get STABLE AFFINITY
    (hash(session_id) % N) so a session's turns stay strictly ordered on its
    worker while different sessions can execute in parallel on different
    subprocesses. Omitting ``session_id`` returns worker 0 (management paths:
    health, abort-all, respawn).
    """
    pool = await _ensure_bridge_pool(extension_paths)
    if session_id:
        return pool.bridge_for_session(session_id)
    return pool.bridges[0]


class PiBridgePool:
    """Bounded set of PiBridge workers with session-affinity routing (V5-B).

    Why affinity instead of a shared queue: the vendor Pi subprocess processes
    one prompt at a time and all turn events land on that worker's own queue —
    events are not routable across workers. Affinity preserves per-session
    ordering (A1 → A2 on the same worker) while disjoint sessions genuinely
    parallelize across subprocesses. All cross-worker coordination (active
    turn identity, abort routing, dispatch callbacks) goes through the
    session-keyed ``_active_turns`` table, which is worker-agnostic by design.
    """

    def __init__(self, bridges: list[PiBridge]) -> None:
        self.bridges = bridges

    @property
    def size(self) -> int:
        return len(self.bridges)

    def bridge_for_session(self, session_id: str) -> PiBridge:
        if len(self.bridges) == 1:
            return self.bridges[0]
        # Stable affinity: hashlib (not hash()) so routing is deterministic
        # across process restarts despite PYTHONHASHSEED. MD5 is fine here —
        # it is a routing bucket, not a security primitive.
        idx = int(
            hashlib.md5(session_id.encode("utf-8"), usedforsecurity=False).hexdigest(),
            16,
        ) % len(self.bridges)
        return self.bridges[idx]


_bridge_pool: Optional[PiBridgePool] = None
# Serializes lazy pool creation so two concurrent first-callers cannot each
# spawn a full set of workers (the loser's subprocesses would leak, running
# and unreferenced). Lifespan is the only production caller today; the lock
# makes the seam safe for future per-request lazy acquisition.
_bridge_pool_init_lock = asyncio.Lock()


def get_bridge_pool() -> Optional[PiBridgePool]:
    """Return the existing pool WITHOUT creating one (None if not started).

    Production turn routing resolves through this: a pool only exists after
    the lifespan started it, so test seams that inject a mock
    ``chat.pi_bridge`` never trigger subprocess creation here.
    """
    return _bridge_pool


async def _ensure_bridge_pool(extension_paths: Optional[list[str]] = None) -> PiBridgePool:
    """Create the pool lazily; start only the workers a pool size requires."""
    global _bridge_pool, _pi_bridge
    if _bridge_pool is not None:
        return _bridge_pool
    import os as _os

    async with _bridge_pool_init_lock:
        if _bridge_pool is not None:  # double-check under the init lock
            return _bridge_pool
        size = max(1, int(_os.getenv("PI_BRIDGE_POOL_SIZE", "1")))
        bridges: list[PiBridge] = []
        try:
            for _ in range(size):
                bridge = PiBridge(extension_paths=extension_paths or [])
                await bridge.start()
                bridges.append(bridge)
        except Exception:
            for b in bridges:
                try:
                    await b.stop()
                except Exception:  # noqa: BLE001
                    pass
            raise
        _bridge_pool = PiBridgePool(bridges)
        _pi_bridge = bridges[0]  # legacy singleton alias (worker 0)
        return _bridge_pool


async def shutdown_pi_bridge() -> None:
    """Shutdown the global Pi bridge instance (or every pool worker)."""
    global _pi_bridge, _bridge_pool
    if _bridge_pool is not None:
        for bridge in _bridge_pool.bridges:
            try:
                await bridge.stop()
            except Exception:  # noqa: BLE001 — best-effort shutdown
                pass
        _bridge_pool = None
        _pi_bridge = None
    if _pi_bridge is not None:
        await _pi_bridge.stop()
        _pi_bridge = None


# ─────────────────────────── Swarm 受保护网关（ADR-0187 D7） ───────────────────────────

#: Swarm 集群委派特性开关（默认关 —— 关闭路径零行为漂移、零 agent_swarm 加载）。
_SWARM_ORCHESTRATOR_ENV = "GIS_SWARM_ORCHESTRATOR"
_SWARM_OFF_VALUES = {"0", "false", "no", "off"}


class SwarmBridge:
    """Master(Pi) → Specialist 集群的受保护委派网关（ADR-0187 §D7）。

    守卫序（任一不满足 → ``{"delegated": False, "reason": ...}`` 诚实拒绝）：
    1. 特性开关（``GIS_SWARM_ORCHESTRATOR``，默认关）；
    2. goal 有界（空 / 超 2000 字符拒绝）；
    3. 主 turn 互斥（该 session 存在在飞 Pi turn 时拒绝，防并发写会话态）。

    通过守卫后：惰性构建 ``WorldStateProjection`` 切片（fail-open 最小面）→
    ``SwarmOrchestrator.run_swarm`` → 返回有界摘要（仅 run_id/终态/计数/
    ref 提货券清单，零 payload）。agent_swarm 全部惰性 import —— 开关关闭
    时不加载任何集群模块。
    """

    def __init__(
        self,
        session_id: str,
        *,
        orchestrator_factory: Optional[Callable[[str], Any]] = None,
        projection_builder: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._session_id = session_id
        self._orchestrator_factory = orchestrator_factory
        self._projection_builder = projection_builder

    @classmethod
    def enabled(cls) -> bool:
        return (
            os.getenv(_SWARM_ORCHESTRATOR_ENV, "0").strip().lower()
            not in _SWARM_OFF_VALUES
        )

    async def delegate_compound_task(
        self,
        root_goal: str,
        *,
        cartography_context: Optional[dict] = None,
        mission_id: str = "",
        org_id: str = "",
    ) -> dict:
        if not self.enabled():
            return {"delegated": False, "reason": "swarm_disabled"}
        goal = (root_goal or "").strip()
        if not goal or len(goal) > 2000:
            return {"delegated": False, "reason": "invalid_goal"}
        if get_active_turn_entry(self._session_id) is not None:
            return {"delegated": False, "reason": "master_turn_active"}
        from app.services.agent_swarm.delegation_contracts import (
            MAX_GOAL_CHARS,
            WorldStateProjection,
        )
        from app.services.agent_swarm.dispatcher import SpecialistDispatcher
        from app.services.agent_swarm.orchestrator import SwarmOrchestrator

        if self._projection_builder is not None:
            projection = self._projection_builder(goal, cartography_context)
        else:
            projection = WorldStateProjection(
                session_id=self._session_id, goal_summary=goal[:MAX_GOAL_CHARS]
            )
        if self._orchestrator_factory is not None:
            orchestrator = self._orchestrator_factory(self._session_id)
        else:
            orchestrator = SwarmOrchestrator(
                self._session_id, dispatcher=SpecialistDispatcher()
            )
        try:
            status = await orchestrator.run_swarm(goal, projection=projection)
        except Exception as exc:  # noqa: BLE001 — 网关诚实折算，绝不击穿主通道
            logger.exception(
                "[SwarmBridge] swarm run failed session=%s", self._session_id
            )
            return {"delegated": False, "reason": f"swarm_error: {exc}"[:200]}
        manifest = status.manifest
        refs = [entry.ref_id for entry in manifest.entries] if manifest else []
        out = {
            "delegated": True,
            "run_id": status.run_id,
            "state": status.state,
            "counts": dict(status.counts),
            "refs": refs[:24],
            "manifest_ref": status.manifest_ref,
        }
        # D04: optional Mission create/reuse when GIS_MISSION_HOTPATH=1 (default off).
        try:
            from app.services.gis_harness.hotpath_convergence import (
                maybe_bind_mission_for_turn,
                set_mission_id,
            )
            _bind = maybe_bind_mission_for_turn(
                session_id=self._session_id,
                org_id=str(org_id or ""),
                root_goal=goal,
                mission_id=mission_id,
            )
            if _bind.mission_id:
                mission_id = _bind.mission_id
                set_mission_id(self._session_id, mission_id)
                out["mission_bind"] = _bind.to_bounded_dict()
        except Exception:  # noqa: BLE001 — mission bind must not break swarm
            pass
        # ADR-0197 D4: optional durable swarm mirror under a Mission (fail-open).
        if mission_id:
            try:
                from app.services.mission_runtime.service import mission_runtime_enabled
                from app.services.mission_runtime.swarm_bridge import DurableSwarmBridge
                if mission_runtime_enabled():
                    bridge = DurableSwarmBridge()
                    descriptors = []
                    task_map = getattr(status, "tasks", None) or {}
                    if isinstance(task_map, dict) and task_map:
                        for tid, tstate in task_map.items():
                            if isinstance(tstate, dict):
                                side = tstate.get("side_effect", "pure")
                            else:
                                side = getattr(tstate, "side_effect", "pure")
                            descriptors.append({
                                "task_id": str(tid),
                                "side_effect": side,
                            })
                    else:
                        descriptors = [{"task_id": f"swarm:{status.run_id}", "side_effect": "pure"}]
                    run = bridge.begin_run(
                        mission_id,
                        org_id=str(org_id or "0"),
                        goal_slice=goal[:2000],
                        task_descriptors=descriptors,
                    )
                    # Aggregate fallback only when fine-grained map unavailable.
                    # partial/degraded must settle as failed — never SKIPPED→COMPLETED (#1323).
                    agg_status = "succeeded"
                    state_val = getattr(status.state, "value", status.state)
                    state_l = str(state_val).lower()
                    if state_l in ("failed", "failure", "cancelled", "canceled"):
                        agg_status = state_l
                    elif state_l in ("partial", "degraded"):
                        agg_status = "failed"
                    for d in descriptors:
                        tid = d["task_id"]
                        tstate = task_map.get(tid) if isinstance(task_map, dict) else None
                        if isinstance(tstate, dict):
                            task_status = str(tstate.get("status") or agg_status)
                            produced = list(tstate.get("produced_refs") or [])[:12] or refs[:12]
                            assignment_id = str(tstate.get("assignment_id") or status.run_id)
                            error_code = str(tstate.get("error_code") or "")
                            summary = str(tstate.get("summary") or "")
                            attempt = int(tstate.get("attempt") or 0)
                            side = tstate.get("side_effect", d.get("side_effect", "pure"))
                        elif tstate is not None:
                            task_status = str(
                                getattr(tstate, "status", None)
                                or getattr(getattr(tstate, "status", None), "value", None)
                                or agg_status
                            )
                            produced = list(getattr(tstate, "produced_refs", None) or [])[:12] or refs[:12]
                            assignment_id = str(getattr(tstate, "assignment_id", "") or status.run_id)
                            error_code = str(getattr(tstate, "error_code", "") or "")
                            summary = str(getattr(tstate, "summary", "") or "")
                            attempt = int(getattr(tstate, "attempt", 0) or 0)
                            side = getattr(tstate, "side_effect", d.get("side_effect", "pure"))
                        else:
                            task_status = agg_status
                            produced = refs[:12]
                            assignment_id = str(status.run_id)
                            error_code = ""
                            summary = ""
                            attempt = 0
                            side = d.get("side_effect", "pure")
                        bridge.settle(
                            run.swarm_run_id,
                            task_id=tid,
                            status=task_status,
                            produced_refs=produced,
                            assignment_id=assignment_id,
                            side_effect=side,
                            error_code=error_code,
                            summary=summary,
                            attempt=attempt,
                        )
                    out["mission_swarm_run_id"] = run.swarm_run_id
            except Exception:  # noqa: BLE001 — durability mirror must not break swarm path
                logger.warning(
                    "[SwarmBridge] mission durable mirror failed session=%s mission=%s",
                    self._session_id, mission_id, exc_info=True,
                )
        return out


def get_swarm_bridge(session_id: str) -> SwarmBridge:
    """Swarm 网关工厂（每会话一实例；无全局态）。"""
    return SwarmBridge(session_id)
