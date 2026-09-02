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

from app.services.distributed_lock import LockDegradedError, LockLostError
import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Optional

from pydantic import BaseModel

from app.utils.sse import sse_event, sse_event_type
from app.services.chat.pi_event_mapper import map_event_to_sse, _extract_text_from_event
from app.services.chat.pi_native_surface import resolve_pi_tool_call
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
# (the stall budget) or the whole turn exceeding PI_TURN_TOTAL_TIMEOUT fails.
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
# so this now only needs to catch a true Pi hang; bumped to 180s to tolerate
# long toolchains (still well under PI_RPC_TIMEOUT=300s that bounds one RPC).
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
    logger.info(f"[PiBridge] Tool registry injected ({len(registry.list_tools())} tools)")


def get_tool_registry() -> "ToolRegistry":
    """Return the injected registry, raising if not yet initialized."""
    if _tool_registry is None:
        raise PiRpcError("Tool registry not initialized")
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


def _get_active_session_id() -> str:
    """Return the currently active turn's session_id, or empty string if none."""
    if _active_turn_context and len(_active_turn_context) >= 1:
        return _active_turn_context[0] or ""
    return ""


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
    """Evict oldest entries when cache exceeds max_cap, protecting the active session."""
    if len(cache) <= max_cap:
        return
    active_sid = _get_active_session_id()
    for key in list(cache.keys()):
        sid = key[0] if is_tuple_key else key
        if not active_sid or sid != active_sid:
            cache.pop(key, None)
            if len(cache) <= max_cap:
                return
    # If all entries belong to active session (extreme overload), enforce hard bound
    while len(cache) > max_cap:
        cache.pop(next(iter(cache)), None)


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
        except Exception:
            pass
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
        active_turn, _run, _sid = active_turn_correlation()
        if callback_turn is not None and callback_turn != active_turn:
            return
        tasks = engine.tracker.list_by_session(request.sessionId or "")
        if tasks:
            latest_task = tasks[-1]
            if latest_task.status.value == "running":
                step = engine.tracker.start_step(latest_task.id, tool_name, arguments)
                engine.tracker.cancel_step(latest_task.id, step.id)
    except Exception:
        pass


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
    # here. Direct in-process callers/tests may still supply sessionId. Never
    # fall back to the bridge's mutable active turn: a delayed callback could
    # otherwise mutate the next session.
    session_id = request.sessionId or ""
    if not session_id:
        raise PiRpcError("Pi tool callback has no verified turn session")

    # Unknown bare names reject with discover guidance — the extension only
    # sends the 7 natives + webgis_execute, so no legitimate call crosses this.
    resolved = resolve_pi_tool_call(tool_name, arguments, allow_passthrough=False)
    if resolved.kind == "reject":
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{"type": "text", "text": resolved.error}],
            details={"error": "native_surface_reject", "tool": tool_name},
            isError=True,
        )
    tool_name = normalize_tool_name(resolved.name)
    arguments = dict(resolved.arguments)

    if session_id:
        try:
            from app.services.session_plan import ensure_session_plan_slot
            await ensure_session_plan_slot(session_id)
        except Exception:
            logger.exception("[PiBridge] SessionPlan slot open failed session=%s", session_id)

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

    # 统一调度：委托给 ToolDispatchService（票据 01 引入）。
    # executed_tools 复用一个 session 级 set，让重复调用拦截在 service 内生效。
    # 服务实例按 bridge 复用（#554 缺陷 3）：每回调新建实例会让 _completed_keys
    # 永远为空，同回合内已完成调用的重复调用被误报为"仍在执行中"。
    service = _get_shared_dispatch_service()
    tc = {"id": request.toolCallId, "function": {"name": tool_name, "arguments": arguments}}
    executed = _session_executed_sets.setdefault(session_id, set())
    _evict_session_executed_set()

    # Runtime observability (W3): recover the active turn's correlation.
    turn_id, run_id, active_session = active_turn_correlation()
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
            dispatch_token = (
                _active_turn_token
                if _active_turn_context is not None
                and session_id == _active_turn_context[0]
                else None
            )
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
        active_turn, _run, _sid = active_turn_correlation()
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

    if result.status == "ok":
        try:
            from app.services.session_plan import apply_tool_result, events_to_sse
            plan_events = await apply_tool_result(
                session_id,
                tool_name,
                result.raw_result,
                success=True,
                geojson_ref=result.geojson_ref,
            )
            cache_session_plan_sse(
                request.toolCallId,
                events_to_sse(plan_events, session_id),
                session_id,
            )
        except (TimeoutError, asyncio.TimeoutError):
            # Session-lock contention (e.g. a long cartographic evaluation
            # holding the per-session lock): retry once so the envelope
            # update — possibly a supersede — is not silently lost.
            logger.warning(
                "[PiBridge] SessionPlan apply lock contention session=%s tool=%s — retrying once",
                session_id, tool_name,
            )
            try:
                plan_events = await apply_tool_result(
                    session_id,
                    tool_name,
                    result.raw_result,
                    success=True,
                    geojson_ref=result.geojson_ref,
                )
                cache_session_plan_sse(
                    request.toolCallId,
                    events_to_sse(plan_events, session_id),
                    session_id,
                )
            except Exception:
                logger.exception(
                    "[PiBridge] SessionPlan apply retry failed session=%s tool=%s",
                    session_id, tool_name,
                )
        except Exception:
            logger.exception(
                "[PiBridge] SessionPlan apply failed session=%s tool=%s",
                session_id, tool_name,
            )
        # ADR-0081：DAG 完成 ≠ 地图成品完成 —— 工具结果落账后运行完成度
        # 终验（幂等、有界；DAG 未终态时立即返回 pending，毫秒级）。结果
        # 以独立 SSE 事件披露给前端 finalizer（视口校验/修复在前端）。
        try:
            from app.services.gis_harness.map_completion import (
                current_mapspec_for_disclosure as _finalization_spec_snapshot,
                finalization_sse_payload,
                maybe_finalize_map_product,
            )
            completion = await maybe_finalize_map_product(
                session_id, reason=f"tool_result:{tool_name}"
            )
            # pending 不披露（DAG 未终态是 turn 中段常态，[GIS Plan] 行投影
            # 已表达；每个工具结果一条 pending SSE 是纯噪声 + 前端空转）。
            # repair 改写 desired state 时附带 mapspec + revision —— 前端
            # 通用 spec 提交通道同步到 live chrome/exporter。
            if completion is not None and completion.status != "pending":
                spec_snapshot = (None, None)
                if completion.repairs_applied:
                    spec_snapshot = await _finalization_spec_snapshot(session_id)
                cache_session_plan_sse(
                    request.toolCallId,
                    sse_event(
                        "map_finalization",
                        finalization_sse_payload(
                            completion,
                            session_id,
                            mapspec=spec_snapshot[0],
                            mutation_revision=spec_snapshot[1],
                        ),
                    ),
                    session_id,
                )
        except Exception:  # noqa: BLE001 — 终验是增值信号，绝不阻断工具返回
            logger.exception(
                "[PiBridge] map finalization failed session=%s tool=%s",
                session_id, tool_name,
            )
    elif result.status == "error":
        # v3(Phase E)：失败对计划可见 —— 数据/分析工具 error 时，其命中的
        # 能力行标 failed（可重试；DAG 下游阻塞到重试成功）。best-effort：
        # 标记失败不阻断错误结果的正常返回。
        try:
            from app.services.session_plan import apply_tool_result, events_to_sse
            plan_events = await apply_tool_result(
                session_id,
                tool_name,
                result.raw_result,
                success=False,
            )
            if plan_events:
                cache_session_plan_sse(
                    request.toolCallId,
                    events_to_sse(plan_events, session_id),
                    session_id,
                )
        except (TimeoutError, asyncio.TimeoutError):
            # 与成功分支同款的锁竞争重试（delta review P1）：并行 tool 回调
            # 下 erroring 回调可能与持锁的成功回调竞争 —— 丢掉 failed 标记
            # 意味着披露静默消失（行停留 pending，下游不阻塞）。
            logger.warning(
                "[PiBridge] SessionPlan failure-mark lock contention session=%s tool=%s — retrying once",
                session_id, tool_name,
            )
            try:
                plan_events = await apply_tool_result(
                    session_id,
                    tool_name,
                    result.raw_result,
                    success=False,
                )
                if plan_events:
                    cache_session_plan_sse(
                        request.toolCallId,
                        events_to_sse(plan_events, session_id),
                        session_id,
                    )
            except Exception:
                logger.exception(
                    "[PiBridge] SessionPlan failure-mark retry failed session=%s tool=%s",
                    session_id, tool_name,
                )
        except Exception:
            logger.exception(
                "[PiBridge] SessionPlan failure mark failed session=%s tool=%s",
                session_id, tool_name,
            )

    raw = result.raw_result if isinstance(result.raw_result, dict) else {}
    has_cartographic_generation = bool(raw.get("mapspec_fingerprint"))
    harness = _get_session_harness(
        session_id,
        create=has_cartographic_generation,
    )
    if harness is not None:
        # V3: 记录本次 dispatch 发出的地图动作（issued 侧证据）。仅 status=="ok" ——
        # error/repeated 不产生可执行的地图命令。turn_id 在此不可得（HTTP 回调无
        # turn 上下文）；turn 级 correlation 由前端 ack 的 correlation 侧补齐。
        is_error = result.status == "error"
        # HARNESS-V2: forward real MapSpec mutation evidence (is_compiled /
        # success / warnings / checkpoint_id) from raw_result so the validity
        # ladder isn't starved in production. Slim to evidence fields only — the
        # full mapspec is fetched via fetch-on-demand, never logged wholesale.
        ev = {"status": result.status, "llm_payload_len": len(result.llm_payload)}
        # ADR-0078: also forward cartography_findings so the Harness surfaces
        # thematic drift (paint↔legend equivalence) in semantic_errors.
        from app.lib.harness.evidence import CARTOGRAPHIC_RESULT_EVIDENCE_KEYS
        for k in CARTOGRAPHIC_RESULT_EVIDENCE_KEYS:
            if k in raw:
                ev[k] = raw[k]
        event = ToolCallEvent(
            tool_call_id=request.toolCallId,
            # #789: keep the REAL tool name. Relabeling every fingerprint-
            # carrying call to "webgis_layer_upsert" falsified the audit trail
            # and under-counted ToolChoiceAccuracy; the mutation ledger is now
            # classified structurally (result carries mapspec_fingerprint) in
            # PiAgentHarness.record_tool_result.
            tool_name=tool_name,
            arguments=arguments,
            duration_ms=duration_ms,
            is_error=is_error,
            # P1 fix: truncate to a short message rather than the full payload.
            error_msg=(result.llm_payload[:200] if is_error else ""),
            result=ev,
            session_id=session_id,
        )
        if has_cartographic_generation and result.status == "ok":
            # v2(review R1-P2-6)：工具已成功提交，此处降级锁（F2 fail-closed
            # 引入）不得把成功调用标成裸 500 —— 兜底为跳过本帧 harness
            # context（下一事件源会重建），与下方 evaluate 的兜底同款。
            try:
                generation_current = await _persist_cartographic_harness_context(
                    session_id, event, result.map_actions
                )
            except (LockDegradedError, LockLostError) as lock_err:
                logger.warning(
                    "[PiBridge] harness context skipped (lock unavailable) "
                    "session=%s tool=%s: %s",
                    session_id, tool_name, lock_err,
                )
                generation_current = False
            if not generation_current:
                # The GIS result is still returned, but a completion from an
                # older MapSpec revision cannot enter or evaluate the current
                # process-local harness.
                return PiToolResponse(
                    toolCallId=request.toolCallId,
                    content=[{"type": "text", "text": result.llm_payload}],
                    details=_slim_pi_details_payload(result),
                    isError=(result.status == "error"),
                )
        if result.status == "ok":
            for ma in result.map_actions:
                harness.record_map_action_issued(
                    session_id=session_id,
                    tool_call_id=request.toolCallId,
                    turn_id="",
                    action_id=ma["action_id"],
                    command=ma["command"],
                    requested=ma["requested"],
                    mapspec_fingerprint=ma.get("mapspec_fingerprint"),
                )
        harness.record_event(event)

        # Desired-state evidence is available immediately.  Runtime PASS is
        # deliberately impossible until a matching live observation and ACK
        # arrive; those event endpoints invoke the same session evaluator.
        if has_cartographic_generation:
            try:
                await evaluate_cartographic_session(session_id)
            except Exception as review_error:  # noqa: BLE001 - GIS success is immutable
                logger.warning(
                    "[PiBridge] cartographic evaluation unavailable for %s: %s",
                    session_id,
                    review_error,
                )

    details_payload = _slim_pi_details_payload(result)

    return PiToolResponse(
        toolCallId=request.toolCallId,
        content=[{"type": "text", "text": result.llm_payload}],
        details=details_payload,
        isError=(result.status == "error"),
    )


# 每个 session 的已执行工具集合，供重复调用拦截（service 接受外部 set）。
_session_executed_sets: dict[str, set[tuple[str, str]]] = {}

# F24: 当前在飞 turn 的 CancellationToken。stream_prompt 在持锁期间设置/清除；
# dispatch_tool（Pi HTTP 回调适配器）通过 use_token() 绑定它，使 checkpoint()
# 协作式工具在 abort/disconnect 后及时停止，而不是对已被抛弃的 turn 跑到底。
# 模块级而非实例级：dispatch_tool 是模块函数（ADR-0022 rendezvous），拿不到
# bridge 实例；turn 在单例 bridge 上严格串行，全局唯一在飞 turn 语义安全。
_active_turn_token: Optional[CancellationToken] = None
# Exact active capability for the singleton Pi subprocess.  A valid HMAC is
# necessary but insufficient: once its turn ends, a delayed callback must not
# remain executable for the token's remaining clock lifetime.
_active_turn_context: Optional[tuple[str, str]] = None


async def register_active_pi_turn(session_id: str, turn_id: str) -> None:
    """Register active turn in local process memory and Redis (if available)."""
    global _active_turn_context
    _active_turn_context = (session_id, turn_id)
    from app.services.chat.pi_turn_context import pi_turn_registry
    await pi_turn_registry.register_turn(session_id, turn_id)


async def unregister_active_pi_turn(session_id: str, turn_id: str) -> None:
    """Unregister active turn in local memory and Redis (if owned)."""
    global _active_turn_context
    if _active_turn_context == (session_id, turn_id):
        _active_turn_context = None
    from app.services.chat.pi_turn_context import pi_turn_registry
    await pi_turn_registry.unregister_turn(session_id, turn_id)


async def is_active_pi_turn(session_id: str, turn_id: str) -> bool:
    """Return whether ``(session, turn)`` owns the live Pi prompt (local or cross-pod Redis)."""
    from app.services.chat.pi_turn_context import pi_turn_registry
    return await pi_turn_registry.is_active(session_id, turn_id)

# Runtime observability: 当前在飞 turn 的关联身份（turn_id / run_id / session_id）。
# 与 ``_active_turn_token`` 同源——dispatch_tool 是独立的 HTTP 回调 task，看不到流
# 任务的 ContextVar；故按「单例 bridge 严格串行 turn」这一既有不变量（cancel
# token 已依赖它）暴露在飞 turn 的关联身份，让 dispatch_tool 能把工具证据按
# turn_id 写进 TurnEvidence 注册表、并把 run_id/turn_id 透传给 tool_metrics /
# JobOrigin。abort/超时的 finally 会先清 token 再清这里——迟到的回调读到 None
# 时回退为空（graceful，不伪造关联）。session_id 用于 dispatch_tool 的归属守卫：
# 回调携带的 session 与在飞 turn 的 session 不一致时，认定是迟到/跨 worker 的串号
# 回调，不把它的工具证据误归属到当前 turn（仍执行工具，只放弃关联）。
_active_turn_turn_id: Optional[str] = None
_active_turn_run_id: Optional[str] = None
_active_turn_session_id: Optional[str] = None


def active_turn_correlation() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """返回在飞 turn 的 (turn_id, run_id, session_id)。

    无在飞 turn 或已被清理时返回 (None, None, None)。供 dispatch_tool（独立 HTTP
    回调 task）恢复 turn 级关联使用，并据 session_id 守卫串号回调。单例 bridge 严格
    串行 turn，故全局唯一在飞 turn 语义安全（同 ``_active_turn_token``）。
    """
    return _active_turn_turn_id, _active_turn_run_id, _active_turn_session_id



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

    async def abort(self, session_id: Optional[str] = None) -> dict:
        """Abort the currently-running Pi prompt (fire-and-forget from callers).

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
        (back-compat: existing callers pass nothing), or no turn is active,
        behave as before.

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
        active = self._active_turn_sid
        if session_id is not None and active is not None and session_id != active:
            logger.warning(
                "[PiBridge] abort(session_id=%s) skipped: turn for session %s "
                "is in flight on the singleton bridge; a global abort would "
                "kill the wrong session's turn",
                session_id, active,
            )
            return {}
        # CONC-F1: snapshot the turn identity + pending futures BEFORE the
        # (potentially slow) abort RPC. The same-session successor case —
        # page close → immediate resend is the most common disconnect pattern —
        # leaves _active_turn_sid UNCHANGED while the token and pending
        # futures belong to the NEW turn; sid equality alone would kill it.
        abort_token = _active_turn_token
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
        if _active_turn_token is not abort_token and _active_turn_token is not None:
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
            await self.abort(session_id=turn_sid)
            logger.info("[PiBridge] abort sent on client disconnect (turn=%s)", turn_sid)
        except Exception as e:  # noqa: BLE001 — abort failure must not break cleanup
            logger.warning("[PiBridge] abort-on-disconnect failed (turn=%s): %s", turn_sid, e)

    async def prompt(
        self,
        message: str,
        session_id: Optional[str] = None,
        cartography_context: Optional[str] = None,
        env_block: Optional[str] = None,
    ) -> dict:
        """Send a prompt to Pi agent (non-streaming).

        Args:
            message: User message
            session_id: Optional session ID
            cartography_context: Optional bounded harness verdict block,
                prepended ahead of the turn marker (see attach_turn_context).

        Returns:
            Response dict with session_id and content

        Raises:
            PiRpcError: If the Pi agent returns an error or the request fails.
        """
        global _active_turn_token, _active_turn_turn_id, _active_turn_run_id, _active_turn_session_id, _active_turn_context
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
                # #1108: initialize BEFORE register — a register failure must
                # not leave the finally referencing an unbound local.
                tracker_task_id = None
                try:
                    # F5/F24: publish the active turn's identity + cancellation token
                    # while the lock is held — abort() reads the sid for session
                    # scoping, dispatch_tool binds the token via use_token. Same as
                    # stream_prompt; cleared in the finally. P1: prompt() previously
                    # never set these, so abort(session_id=other) saw active=None and
                    # the GLOBAL abort killed this turn, and HTTP-callback tool
                    # dispatches ran unbounded (F24 no-op) on this path.
                    self._active_turn_sid = turn_sid
                    await register_active_pi_turn(turn_sid, turn_id)
                    _active_turn_token = CancellationToken(job_id=turn_id)
                    _active_turn_turn_id = turn_id
                    _active_turn_run_id = run_id
                    _active_turn_session_id = turn_sid or None

                    try:
                        from app.services.chat.engine_instance import try_get_chat_engine
                        engine = try_get_chat_engine()
                        if engine is None:
                            raise RuntimeError("ChatEngine not initialized")
                        tracker_task = engine.tracker.create(turn_sid, message)
                        tracker_task_id = tracker_task.id
                    except Exception:
                        pass

                    # Drop any residual events from a prior turn before sending, so they
                    # cannot be attributed to this turn.
                    await self._drain_stale_events()
                    data["message"] = await bind_turn_prompt(
                        message, turn_token, turn_sid, cartography_context or "",
                        env_block=env_block or "",
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
                    process_died = False
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
                        # (mirrors stream_prompt's stall handling). First-win settle
                        # keeps this "drain_timeout" classification over the generic
                        # failure_class in the except handler below.
                        rt_ev.settle(Outcome.FAILED, failure_class="drain_timeout")
                        raise PiRpcError(
                            f"Pi agent did not emit agent_settled (continuous silence exceeded "
                            f"{PI_EVENT_STREAM_TIMEOUT}s or the turn exceeded "
                            f"{PI_TURN_TOTAL_TIMEOUT}s; event-drain timeout); "
                            "the turn has been aborted."
                        )
                    # Only a clean agent_settled reaches this point — a timeout
                    # already raised above, so PARTIAL is no longer reachable.
                    rt_ev.settle(Outcome.SUCCEEDED)
                except asyncio.CancelledError:
                    cancelled = True
                    rt_ev.settle(Outcome.CANCELLED)
                    raise
                except Exception as exc:  # noqa: BLE001
                    rt_ev.settle(Outcome.FAILED, failure_class=type(exc).__name__, detail=str(exc)[:200])
                    raise
                finally:
                    if cancelled or timed_out or send_failed:
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
                    if tracker_task_id:
                        try:
                            from app.services.chat.engine_instance import try_get_chat_engine
                            engine = try_get_chat_engine()
                            if engine is None:
                                raise RuntimeError("ChatEngine not initialized")
                            if cancelled:
                                engine.tracker.cancel(tracker_task_id)
                            elif timed_out or send_failed:
                                engine.tracker.fail_task(tracker_task_id, "turn failed or timed out")
                            else:
                                engine.tracker.complete_task(tracker_task_id)
                        except Exception:
                            pass
                    _cleanup_turn_state(turn_sid)
                    # Clear the active-turn markers before releasing the lock.
                    self._active_turn_sid = None
                    _active_turn_token = None
                    _active_turn_turn_id = None
                    _active_turn_run_id = None
                    _active_turn_session_id = None
                    # #1108 INV-P4: release the lease BEFORE the unregister await —
                    # the release is synchronous (uncancellable) and the unregister
                    # is a best-effort shielded call, so a re-delivered
                    # CancelledError during Redis I/O can no longer skip the
                    # release. The outer finally's backstop release is a no-op then.
                    self._release_turn_lease(lease)
                    await self._safe_unregister_active_pi_turn(turn_sid, turn_id)
                    rt_ev.mark_ended()
                    emit_turn_summary(rt_ev)
                    TURN_EVIDENCE.remove(turn_id)
        finally:
            # #1108 INV-P1: backstop — whatever happened above (including a
            # cancellation delivered inside the inner finally before its own
            # release), the lease is released here exactly once.
            self._release_turn_lease(lease)

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
        global _active_turn_token, _active_turn_turn_id, _active_turn_run_id, _active_turn_session_id, _active_turn_context
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
            try:
                _session_executed_sets.pop(turn_sid, None)
                _clear_dispatch_cache(turn_sid)
                cancelled = False
                timed_out = False
                send_failed = False
                # G: set True when the pump observes the Pi subprocess dying mid-stream
                # (see the process_died_event watcher below). Initialized here so the
                # finally can read it even on the early-return send-failure path.
                process_died = False
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
                await register_active_pi_turn(turn_sid, turn_id)
                _active_turn_token = CancellationToken(job_id=turn_id)
                _active_turn_turn_id = turn_id
                _active_turn_run_id = run_id
                _active_turn_session_id = turn_sid or None

                try:
                    from app.services.chat.engine_instance import try_get_chat_engine
                    engine = try_get_chat_engine()
                    if engine is None:
                        raise RuntimeError("ChatEngine not initialized")
                    tracker_task = engine.tracker.create(turn_sid, message)
                    tracker_task_id = tracker_task.id
                except Exception:
                    pass

                try:
                    # Drop residual events from a prior turn so they can't be dequeued
                    # and attributed to this session.
                    await self._drain_stale_events()
                    data["message"] = await bind_turn_prompt(
                        message, turn_token, turn_sid, cartography_context or "",
                        env_block=env_block or "",
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
                    # PI_EVENT_STREAM_TIMEOUT=180s stall budget and then retrying with
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
                    # audit #820: turn-scoped counters injected into the mapper's
                    # agent_end handler (task_complete step_count/summary).
                    _turn_tool_steps = 0
                    _turn_final_text = ""
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
                                    try:
                                        from app.services.gis_harness.map_completion import (
                                            current_mapspec_for_disclosure as _finalization_spec_snapshot,
                                            finalization_sse_payload,
                                            maybe_finalize_map_product,
                                            read_stored_map_product,
                                        )
                                        _completion = await maybe_finalize_map_product(
                                            turn_sid, reason="turn_settled"
                                        )
                                        if _completion is not None and _completion.status != "pending":
                                            _spec_snapshot = (None, None)
                                            if _completion.repairs_applied:
                                                _spec_snapshot = await _finalization_spec_snapshot(turn_sid)
                                            _turn_map_product = finalization_sse_payload(
                                                _completion,
                                                turn_sid,
                                                mapspec=_spec_snapshot[0],
                                                mutation_revision=_spec_snapshot[1],
                                            )
                                        elif _completion is None:
                                            # 幂等门跳过（complete+revision 一致）→
                                            # task_complete 仍披露已存储的完成态。
                                            _turn_map_product = await read_stored_map_product(
                                                turn_sid
                                            )
                                    except Exception:  # noqa: BLE001 — 增值信号
                                        logger.exception(
                                            "[PiBridge] turn-settle finalization failed session=%s",
                                            turn_sid,
                                        )
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
                        if _active_turn_token is not None:
                            _active_turn_token.cancel("pi process exited unexpectedly")
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
                    _active_turn_token = None
                    _active_turn_turn_id = None
                    _active_turn_run_id = None
                    _active_turn_session_id = None
                    # #1108 INV-P4: release the lease BEFORE the unregister
                    # await — the release is synchronous (uncancellable) and
                    # the unregister is shielded best-effort, so a re-delivered
                    # CancelledError during Redis I/O can no longer skip the
                    # release and hang every session on the singleton bridge.
                    self._release_turn_lease(lease)
                    await self._safe_unregister_active_pi_turn(turn_sid, turn_id)
                    # Runtime observability: settle turn outcome (cancelled ≠ failed),
                    # emit the diagnostic summary, and unregister the evidence. Order:
                    # outcome settled from flags captured above.
                    if cancelled:
                        rt_ev.settle(Outcome.CANCELLED)
                    elif timed_out or send_failed or process_died:
                        if timed_out:
                            # #982: distinguish whole-turn budget exhaustion from a
                            # heartbeat stall in the failure classification.
                            failure_class = (
                                "pi_turn_budget" if timeout_reason == "total" else "pi_stall"
                            )
                        elif process_died:
                            failure_class = "pi_process_died"
                        else:
                            failure_class = "pi_send_error"
                        rt_ev.settle(Outcome.FAILED, failure_class=failure_class)
                    else:
                        rt_ev.settle(Outcome.SUCCEEDED)
                    rt_ev.mark_ended()
                    emit_turn_summary(rt_ev)
                    TURN_EVIDENCE.remove(turn_id)
                    # audit #818: surface the turn's final transcript state to the
                    # route (persistence parity with the legacy path). Best-effort —
                    # a sink failure must never mask the stream outcome.
                    if tracker_task_id:
                        try:
                            from app.services.chat.engine_instance import try_get_chat_engine
                            engine = try_get_chat_engine()
                            if engine is None:
                                raise RuntimeError("ChatEngine not initialized")
                            if cancelled:
                                engine.tracker.cancel(tracker_task_id)
                            elif timed_out or send_failed or process_died:
                                engine.tracker.fail_task(tracker_task_id, "stream turn failed or timed out")
                            else:
                                engine.tracker.complete_task(tracker_task_id)
                        except Exception:
                            pass
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
                self._release_turn_lease(lease)



# Global bridge instance
_pi_bridge: Optional[PiBridge] = None


async def get_pi_bridge(extension_paths: Optional[list[str]] = None) -> PiBridge:
    """Get or create the global Pi bridge instance.

    Note: extension_paths is only honored on the first call. Subsequent calls
    ignore the parameter because the bridge singleton is already initialized.
    If you need to change extensions, call shutdown_pi_bridge() first.
    """
    global _pi_bridge
    if _pi_bridge is None:
        bridge = PiBridge(extension_paths=extension_paths or [])
        try:
            await bridge.start()
        except Exception:
            # Don't leave a half-started singleton: a later get_pi_bridge()
            # must retry the spawn (or fail loud again), not return a dead
            # bridge whose rpc client never came up (native dump fail-fast).
            _pi_bridge = None
            raise
        _pi_bridge = bridge
    return _pi_bridge


async def shutdown_pi_bridge() -> None:
    """Shutdown the global Pi bridge instance."""
    global _pi_bridge
    if _pi_bridge is not None:
        await _pi_bridge.stop()
        _pi_bridge = None
