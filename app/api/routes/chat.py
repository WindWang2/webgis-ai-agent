"""Chat API Route - SSE 流式对话"""
import json
import logging
import uuid
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    authorize_session_write,
    get_current_user_optional,
    get_owner_token,
    require_admin,
    require_owned_session,
)
from app.core.database import get_async_db
from app.core.rate_limiter import get_rate_limiter
from app.models.db_model import Conversation
from app.services.chat.event_resume import TurnEventBuffer, TurnResumeRegistry
from app.services.chat_engine import ChatEngine
from app.services.history_service_async import AsyncHistoryService
from app.services.distributed_lock import session_lock_registry
from app.services.session_data import session_data_manager
from app.tools._utils import async_db_session
from app.tools.registry import ToolRegistry

from app.utils.sse import SSEBatcher, sse_event, sse_event_id, sse_event_id_scope, sse_event_type

from app.agent_pi_bridge import PiRpcError, USE_NEW_AGENT
from app.lib.runtime import context as rt_ctx
from app.lib.runtime.evidence import record_map_action_acked_by_turn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 由 lifespan 在启动时注入
registry: ToolRegistry = None  # type: ignore[assignment]
engine: ChatEngine = None  # type: ignore[assignment]

# DUP-1: process-local SSE turn-resume buffer. A small bounded list of recent
# turn buffers per session key (LRU-capped), filled by the route as events are
# emitted. A POST with Last-Event-ID / last_event_id replays from it without
# starting a new turn. Cross-restart persistence is a non-goal.
_turn_resume_registry = TurnResumeRegistry()

# F20: a resume that races a still-live (or queued, never-started) turn holds
# for the real terminal instead of fabricating ``done`` — capped so a stalled
# turn can't pin the reconnect forever; on expiry the client gets a truthful
# terminal error and may reconnect again with a newer Last-Event-ID.
# Hold is DISABLED for anonymous (''-key) sessions (P1): the shared anonymous
# key skips ownership checks, so holding would let anyone who knows the
# victim's message + Last-Event-ID tail a stranger's live turn — anonymous
# resumes of live turns get the truthful pending error immediately instead.
_RESUME_LIVE_HOLD_S = 30.0

# SSE 流式响应头（Pi 和 legacy 路径共用）
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# D-F6: Pi 路径的 token 批处理（P1）。Pi 桥逐事件产出，若逐条 yield 到
# StreamingResponse 就是每 token 一次 HTTP write（~200 writes/turn）。这里在
# 路由边界用 SSEBatcher 合并高频事件（token/content），与 legacy 引擎一致；
# 结构事件（task_start/step_*/done/...）与 keepalive 注释仍逐条透传，缓冲的
# token 总是先于结构事件 flush，保证前端事件顺序不变。
_PI_BATCHABLE_EVENTS = frozenset({"token", "content"})
_PI_BATCH_MAX_EVENTS = 32
_PI_BATCH_MAX_DELAY_S = 0.08

# Feature flag: 通过环境变量切换新旧 Agent 系统
# true/1/yes 时使用 Pi 开源 agent (vendor/pi) 通过 RPC 调用
# (imported from app.agent_pi_bridge to avoid duplication)
pi_bridge = None  # type: ignore[assignment]  # 由 lifespan 初始化

# map-action-ack 限速（per client IP，同 ws.py/auth.py 模式）：前端 500ms
# debounce 批量上报，正常单会话峰值远低于此；防单 IP 轮换 session_id 的遥测洪泛。
_ACK_RATE_LIMIT_MAX = 300
_ACK_RATE_LIMIT_WINDOW = 60


def get_engine() -> ChatEngine:
    """Return the ChatEngine instance, raising 503 if not yet initialized by lifespan.

    审计 S47：之前 raise RuntimeError -> 全局 exception handler 返回 500 +
    可能泄漏内部模块名。改为 503 让客户端知道是临时不可用（启动窗口）。
    """
    if engine is None:
        raise HTTPException(status_code=503, detail="Service starting up, please retry")
    return engine


_FRONTEND_OBSERVATION_MAX_LAYERS = 128
_FRONTEND_OBSERVATION_MAX_FRAGMENT_BYTES = 16_384
_FRONTEND_OBSERVATION_MAX_TOTAL_BYTES = 262_144
_FRONTEND_OBSERVATION_MAX_NODES = 32_768
_FRONTEND_OBSERVATION_MAX_DEPTH = 8
_FRONTEND_OBSERVATION_MAX_DICT_KEYS = 64
_FRONTEND_LAYER_KEYS = (
    "id", "name", "type", "visible", "opacity", "group", "_refId",
    "_descriptor", "featureCount", "style", "legend_spec",
    "style_converged", "source_converged", "runtime_layer_count",
    "runtime_layer_ids", "runtime_store_id", "projection_fingerprint",
    "repair_action_id", "intent_generation",
    "raster_image", "raster_bbox",
)
_OBSERVATION_DATA_KEYS = frozenset({
    "data", "features", "geojson", "inlineData", "source", "source_data",
})
_OBSERVATION_SECRET_KEYS = frozenset({
    "authorization", "api_key", "apikey", "access_token", "refresh_token",
    "owner_token", "password", "secret", "cookie",
})


def _bounded_observation_fragment(
    value: Any,
    *,
    _depth: int = 0,
    _budget: Optional[list[int]] = None,
) -> Any:
    """Copy a small metadata fragment while stripping dataset payload keys."""
    budget = _budget if _budget is not None else [_FRONTEND_OBSERVATION_MAX_NODES]
    if _depth > _FRONTEND_OBSERVATION_MAX_DEPTH or budget[0] <= 0:
        return None
    budget[0] -= 1
    if isinstance(value, dict):
        projected = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _FRONTEND_OBSERVATION_MAX_DICT_KEYS or budget[0] <= 0:
                break
            normalized_key = str(key).lower()
            if normalized_key in _OBSERVATION_SECRET_KEYS:
                projected[str(key)] = "[redacted]"
            elif key not in _OBSERVATION_DATA_KEYS:
                projected[str(key)] = _bounded_observation_fragment(
                    item, _depth=_depth + 1, _budget=budget
                )
    elif isinstance(value, (list, tuple)):
        projected = [
            _bounded_observation_fragment(
                item, _depth=_depth + 1, _budget=budget
            )
            for item in value[:128]
            if budget[0] > 0
        ]
    elif isinstance(value, str):
        projected = (
            value.split("?", 1)[0] + "?[redacted]"
            if "://" in value and "?" in value
            else value
        )
    elif isinstance(value, (int, float, bool)) or value is None:
        projected = value
    else:
        return None
    try:
        encoded = json.dumps(projected, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return None
    if len(encoded.encode("utf-8")) > _FRONTEND_OBSERVATION_MAX_FRAGMENT_BYTES:
        return None
    return projected


def _project_frontend_layers(raw_layers: Any) -> list[dict[str, Any]]:
    """Return bounded metadata-only runtime layers (never feature payloads)."""
    layers: list[dict[str, Any]] = []
    node_budget = [_FRONTEND_OBSERVATION_MAX_NODES]
    total_bytes = 0
    candidates = raw_layers if isinstance(raw_layers, list) else []
    for raw in candidates[:_FRONTEND_OBSERVATION_MAX_LAYERS]:
        if not isinstance(raw, dict) or not raw.get("id") or node_budget[0] <= 0:
            continue
        layer = {
            key: _bounded_observation_fragment(raw[key], _budget=node_budget)
            for key in _FRONTEND_LAYER_KEYS
            if key in raw
        }
        try:
            layer_bytes = len(
                json.dumps(layer, ensure_ascii=False, allow_nan=False).encode("utf-8")
            )
        except (TypeError, ValueError):
            continue
        if layer_bytes > _FRONTEND_OBSERVATION_MAX_FRAGMENT_BYTES:
            continue
        if total_bytes + layer_bytes > _FRONTEND_OBSERVATION_MAX_TOTAL_BYTES:
            break
        total_bytes += layer_bytes
        layers.append(layer)
    return layers


async def _record_frontend_cartographic_observation(
    session_id: Optional[str], map_state: Optional[dict]
) -> None:
    """Persist bounded pre-turn context without claiming runtime convergence.

    This user-supplied snapshot runs once at turn start.  It is useful context,
    but it predates the next mutation and must neither overwrite canonical map
    state nor certify the post-reconcile runtime.  The authoritative runtime
    endpoint writes ``_cartographic_observation`` separately.
    """
    if not session_id or not isinstance(map_state, dict):
        return
    layers = _project_frontend_layers(map_state.get("layers"))
    node_budget = [_FRONTEND_OBSERVATION_MAX_NODES]
    viewport = _bounded_observation_fragment(
        map_state.get("viewport") or {}, _budget=node_budget
    )
    base_layer = _bounded_observation_fragment(
        map_state.get("base_layer"), _budget=node_budget
    )

    async with session_lock_registry.lock(session_id):
        session_data_manager.invalidate_local_cache(session_id)
        current = await session_data_manager.get_map_state(session_id)
        previous = current.get("_cartographic_context_observation")
        try:
            sequence = int(previous.get("sequence", 0)) + 1 if isinstance(previous, dict) else 1
        except (TypeError, ValueError):
            sequence = 1
        await session_data_manager.set_map_state(
            session_id,
            "_cartographic_context_observation",
            {
                "session_id": session_id,
                "sequence": sequence,
                "source": "frontend_pre_turn",
                "layer_count": len(layers),
                "layers": layers,
                "viewport": viewport if isinstance(viewport, dict) else {},
                "base_layer": base_layer,
            },
        )


def get_registry() -> ToolRegistry:
    """Return the ToolRegistry instance, raising 503 if not yet initialized."""
    if registry is None:
        raise HTTPException(status_code=503, detail="Service starting up, please retry")
    return registry


def _use_pi_bridge() -> bool:
    """Return True when the Pi agent path is enabled and the subprocess is alive.

    C-F15: the Pi subprocess can die (crash, OOM, bad extension). Previously
    ``_use_pi_bridge`` only checked ``USE_NEW_AGENT and pi_bridge is not None``
    and ignored ``process_died``, so after a Pi crash every /chat request
    yielded task_error forever (PiRpcError "Pi process not started") — the
    legacy ChatEngine fallback the docstrings claimed did not exist. Now a dead
    bridge falls through to the always-initialised legacy ChatEngine so the
    service degrades instead of hard-failing until a restart.
    """
    return (
        USE_NEW_AGENT
        and pi_bridge is not None
        and not getattr(pi_bridge, "_process_died", False)
    )


async def _sse_batched(stream, max_events=_PI_BATCH_MAX_EVENTS, max_delay_s=_PI_BATCH_MAX_DELAY_S):
    """Wrap an SSE event stream with route-boundary token batching (D-F6).

    Coalesces high-frequency events (``token`` / ``content``) through a shared
    :class:`SSEBatcher`, mirroring the legacy engine. Structural events
    (``task_start`` / ``step_*`` / ``tool_*`` / ``task_complete`` / ``done`` /
    ``error``) and keepalive comments (``: ...``) pass through in order, with
    any buffered tokens flushed first so the frontend event order is
    preserved.

    Lifecycle: on normal stream end the tail is flushed; on an upstream
    exception buffered tokens are flushed and the exception is re-raised (the
    caller yields the error event after them). A client disconnect
    (GeneratorExit / CancelledError — BaseException) deliberately bypasses the
    exception flush: the connection is gone, so buffered tokens are
    deterministically dropped with the generator frame instead of attempting
    writes to a dead connection, and the underlying bridge stream still
    unwinds (its finally runs abort/drain/cleanup).

    Batching stays at this route boundary on purpose — ``stream_prompt`` owns
    bridge lifecycle concerns (lock, stall, abort) and must not coalesce.
    """
    batcher = SSEBatcher(max_events=max_events, max_delay_s=max_delay_s)
    try:
        async for event in stream:
            if sse_event_type(event) in _PI_BATCHABLE_EVENTS:
                batcher.push(event)
                async for chunk in batcher.drain():
                    yield chunk
            else:
                for chunk in batcher.flush():
                    yield chunk
                yield event
        for chunk in batcher.flush():
            yield chunk
    except Exception:
        # Upstream failure: emit buffered tokens before the error the caller
        # yields, preserving order. Disconnect (BaseException) skips this.
        for chunk in batcher.flush():
            yield chunk
        raise


def _split_sse_events(chunk: str) -> list[str]:
    """Split a (possibly coalesced) SSE chunk into individual event blocks.

    Every block built by :func:`sse_event` ends with ``\\n\\n`` and its JSON
    ``data:`` payload never contains a literal blank line (json.dumps escapes
    newlines), so splitting on ``\\n\\n`` is lossless — for both the legacy
    engine's internal SSEBatcher coalescing and the route-boundary batcher.
    """
    return [part for part in chunk.split("\n\n") if part]


async def _recorded(stream, buffer: TurnEventBuffer):
    """Wrap an SSE event stream, recording each event into the resume buffer
    before it is yielded (DUP-1). Handles coalesced chunks transparently so the
    buffer holds individual events (WITH their ``\\n\\n`` block terminator — the
    buffer stores exact wire blocks, and a resume replays them verbatim) in
    emission order, ids and all.
    """
    async for chunk in stream:
        for event in _split_sse_events(chunk):
            # _split_sse_events strips the terminator; restore it so a replay
            # yields spec-correct, independently-framed SSE events.
            buffer.record(event + "\n\n")
        yield chunk


async def _resume_generator(
    session_key: str,
    last_event_id: int,
    message: str,
    request_id: Optional[str] = None,
):
    """Resume wrapper: bind request-scoped correlation, then delegate to the impl."""
    with rt_ctx.bind_runtime_context(request_id=request_id, session_id=session_key or None):
        async for evt in _resume_generator_impl(session_key, last_event_id, message):
            yield evt


async def _resume_generator_impl(
    session_key: str,
    last_event_id: int,
    message: str,
):
    """DUP-1 resume stream: replay missed events, then terminate cleanly.

    A POST /chat/stream carrying ``Last-Event-ID`` / ``last_event_id`` is a
    RESUME — a read, never a new execution. The route sends no prompt RPC and
    dispatches no tool; it replays what the dropped connection missed from the
    per-session ring buffers and then ends:

      * unique buffer hit (message match + Last-Event-ID valid for that turn)
        → replay every buffered event with ``id > last_event_id`` in order;
        then terminate with the buffered terminal event if it was replayed, a
        synthesized ``done {resumed: true}`` if the client already consumed
        the terminal (``last_event_id >= terminal id`` — the stream must never
        hang), or a synthesized ``error`` if the turn was interrupted (aborted
        on disconnect; no terminal exists).
      * the matched turn is still LIVE (or queued, never started) → after the
        replay the stream HOLDS (F20): new events are forwarded as the turn
        records them, until the real terminal arrives or the turn ends. A live
        turn NEVER yields a fabricated ``done {resumed: true}``. If the turn
        outlives ``_RESUME_LIVE_HOLD_S`` (stalled), the resume ends with a
        truthful terminal ``error {resumed: true, pending: true}`` — the
        client may reconnect again with a newer Last-Event-ID. Anonymous
        (``""``-key) sessions never hold (P1): the shared anonymous key skips
        ownership checks, so holding would let anyone who knows the message +
        Last-Event-ID tail a stranger's live turn — an anonymous resume of a
        live turn replays the already-buffered tail and immediately ends with
        that truthful pending error instead.
      * ambiguous hit (multiple concurrent turns on this session key match) →
        terminal ``error {resumed: false}``: fail safe, never replay the WRONG
        turn's events into this client's stream (cross-turn leak).
      * buffer miss (server restart / LRU eviction / message mismatch) → a
        terminal ``error {resumed: false}``; no new turn starts, so a stale
        reconnect can never double-execute the message. The client stops
        auto-retrying on any terminal event.

    Synthesized terminal events carry no ``id:`` (they are not part of the
    original turn's id sequence); the client consumes them for status and
    stops, so ids are not needed for dedup there.

    Anonymous ``""``-key resumes are always refused. Last-Event-ID is an
    ordering cursor, not an ownership capability; treating it as one permits
    cross-user replay. Pi turns receive a canonical UUID before execution and
    can resume by that id. Legacy anonymous turns are deliberately
    non-resumable until the client has a canonical session identity.
    """
    if session_key == "":
        yield sse_event("error", {
            "session_id": session_key,
            "error": (
                "匿名会话不能安全续传；请使用服务端签发的会话编号重新连接。"
            ),
            "resumed": False,
        })
        return
    buffered, ambiguous = _turn_resume_registry.find(session_key, message, last_event_id)
    if buffered is None:
        if ambiguous:
            logger.warning(
                "ambiguous resume for session key %r: multiple concurrent turns "
                "match — refusing to replay (possible cross-turn leak)",
                session_key,
            )
        yield sse_event("error", {
            "error": (
                "Resume unavailable: the turn buffer is gone (server restarted "
                "or the session expired)."
                if not ambiguous else
                "Resume ambiguous: multiple concurrent turns on this session "
                "match this request — refusing to replay the wrong one. Wait "
                "for the in-flight turn to finish and reconnect, or start a "
                "new message."
            ),
            "resumed": False,
        })
        return

    # Replay (catching up to the live tail), then — while the turn is still
    # live — hold for newly recorded events instead of terminating (F20).
    # Single-threaded asyncio: replay + ended check are await-free, so no
    # recorded event can be missed between iterations.
    cursor = last_event_id
    while True:
        for event in buffered.replay_after(cursor):
            yield event
        if buffered.last_id is not None and buffered.last_id > cursor:
            cursor = buffered.last_id
        if buffered.ended:
            break
        if not await buffered.wait_for_update(timeout=_RESUME_LIVE_HOLD_S):
            # Stalled live turn: end truthfully — the turn is still in
            # progress and can be picked up by another resume. Never a fake done.
            yield sse_event("error", {
                "session_id": session_key,
                "error": (
                    "本轮仍在执行中（续传等待超时）；请携带 Last-Event-ID "
                    "重新连接继续接收。"
                ),
                "resumed": True,
                "pending": True,
            })
            return

    if buffered.terminal_event is not None:
        terminal_id = sse_event_id(buffered.terminal_event)
        if terminal_id is None or terminal_id <= last_event_id:
            # Client already consumed the terminal; reconnect for nothing but
            # a clean close — synthesize one so the stream terminates.
            yield sse_event("done", {"session_id": session_key, "resumed": True})
        # Otherwise the terminal was replayed above — nothing more to add.
    else:
        # Ended without a terminal: the turn was interrupted (client
        # disconnect → server aborted the prompt). Replayed partial content
        # must NOT look like a complete answer.
        yield sse_event("error", {
            "session_id": session_key,
            "error": "连接已中断，本轮未完成且不会自动续跑；请重新发送消息。",
            "resumed": True,
        })


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None
    map_state: Optional[dict] = Field(None, description="当前的地图状态（视角、图层等）")
    skill_name: Optional[str] = Field(None, description="要激活的技能名称")
    project_id: Optional[str] = Field(
        None,
        description=(
            "Active project workspace id; when set, the chat context "
            "assembler renders the project-summary block (datasets + "
            "workflows) for this project. The session metadata store "
            "does not yet persist project_id, so the request body is "
            "the only way to associate a chat turn with a project."
        ),
    )


class ChatResponse(BaseModel):
    """聊天响应"""
    session_id: str
    content: str
    # SEC-08：新建匿名会话时由服务端签发，前端需存储并在后续请求头里回传。
    owner_token: Optional[str] = None


async def _guard_body_session(
    db: AsyncSession,
    session_id: Optional[str],
    user_id: Optional[str],
    owner_token: Optional[str],
) -> None:
    """S31/S32/SEC-08：校验请求体携带的 session_id 属于当前调用者。

    不能直接复用 `require_owned_session`：chat 的 session_id 来自 body 而非
    路径参数，且首条消息会新建会话，所以"库中不存在"必须放行、只有"存在但
    属于他人"才拒绝。与既有守卫一致统一返回 404，避免泄漏会话存在性。

    复用请求注入的 `db`（与 require_owned_session 同一模式）而非另开
    async_db_session：后者在 asyncpg 下会与同连接上进行中的操作冲突
    （InterfaceError: another operation is in progress）。
    """
    if not session_id:
        return

    session_data_manager.invalidate_local_cache(session_id)
    deleted_state = await session_data_manager.get_map_state(session_id)
    if deleted_state.get("_cartographic_deleted") is True:
        raise HTTPException(status_code=404, detail="Session not found")

    owned = await AsyncHistoryService(db).get_session(
        session_id, user_id=user_id, owner_token=owner_token
    )
    if owned is not None:
        return

    # get_session 对"不存在"和"越权"都返回 None，需区分两者。
    if await db.get(Conversation, session_id) is not None:
        raise HTTPException(status_code=404, detail="Session not found")


def _pi_stream_capability(
    conversation,
    created: bool,
    user_id: Optional[str],
    owner_token: Optional[str],
) -> Optional[str]:
    """SSE ``owner_token`` for a Pi stream, or 404 if the caller must stop.

    A newly created row mints a token the caller does not yet have — emit it
    once. An existing row is a TOCTOU against ``_guard_body_session`` (absent
    at guard time, present at get-or-create): require the same ownership
    rules as ``authorize_session_write`` and never emit a token the caller
    did not already present.
    """
    if created:
        return getattr(conversation, "owner_token", None)
    if not authorize_session_write(conversation, user_id, owner_token):
        raise HTTPException(status_code=404, detail="Session not found")
    return None


@router.post("/completions", response_model=ChatResponse)
async def chat_completions(
    req: ChatRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
):
    """非流式对话接口"""
    user_id = _user.get("user_id")
    await _guard_body_session(db, req.session_id, user_id, owner_token)

    # Runtime observability (W1): mint a request id and bind request-scoped
    # correlation. The Pi bridge / legacy engine bind turn_id/run_id on top of
    # this (merged), so a full identity chain reaches metrics + job rows.
    request_id = rt_ctx.new_request_id()
    with rt_ctx.bind_runtime_context(request_id=request_id, session_id=req.session_id):
        if _use_pi_bridge():
            try:
                await _record_frontend_cartographic_observation(req.session_id, req.map_state)
                result = await pi_bridge.prompt(req.message, session_id=req.session_id)
                return ChatResponse(session_id=result.get("sessionId", req.session_id or ""), content=result.get("content", ""))
            except PiRpcError as e:
                logger.error(f"Pi bridge error: {e}", exc_info=True)
                raise HTTPException(status_code=502, detail="Agent bridge error")
            except Exception as e:
                logger.error(f"Pi bridge unexpected error: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail="Internal server error")


        # Legacy path: 使用 ChatEngine
        try:
            result = await get_engine().chat(
                req.message,
                session_id=req.session_id,
                map_state=req.map_state,
                skill_name=req.skill_name,
                user_id=user_id,
                project_id=req.project_id,
            )
            return ChatResponse(**result)
        except Exception as e:
            logger.error(f"Chat error: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/stream", response_model=None)
async def chat_stream(
    req: ChatRequest,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
    # DUP-1 resume contract: the client re-POSTs the same turn with the last
    # event id it received. The id comes back as the SSE-spec `Last-Event-ID`
    # header (preferred) or the `last_event_id` query param. Annotated + real
    # None default keeps direct (non-FastAPI) callers working — a plain
    # `= Header(None)` default would leak the Header marker object into
    # route-level tests that invoke the function directly.
    last_event_id_header: Annotated[Optional[int], Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[Optional[int], Query(alias="last_event_id")] = None,
):
    """SSE 流式对话接口

    DB-session lifecycle (transport goal C-F2): FastAPI keeps yield-dependencies
    alive until the response body completes, which for a StreamingResponse is
    the end of a multi-second LLM turn. That used to pin a Postgres connection
    for the whole turn doing nothing after the ownership guard. With
    ``pool_size=10 + max_overflow=20``, ~15 concurrent streams exhausted the
    pool (30s ``pool_timeout`` → 500s on unrelated requests) and
    ``_save_msg_async`` silently dropped history.

    We still take ``Depends(get_async_db)`` — it is the override point tests use
    to isolate the DB — but close it *immediately* after the guard so the
    connection returns to the pool before any event is yielded. ``close()`` is
    idempotent; the dependency's later teardown ``close()`` is a no-op. Neither
    streaming path needs a session across the stream: the legacy engine
    persists via its own short-lived ``_save_msg_async`` sessions and the Pi
    path writes nothing during the stream. The inner ``async with
    async_db_session()`` that used to wrap each generator was never consumed
    either, so it is removed (that change alone cuts held connections 2→1 per
    stream; the early close takes it to 0 during streaming).
    """
    user_id = _user.get("user_id")
    use_pi = _use_pi_bridge()
    last_event_id = (
        last_event_id_header
        if last_event_id_header is not None
        else last_event_id_query
    )
    pi_session_id = req.session_id
    pi_owner_token: Optional[str] = None
    try:
        await _guard_body_session(db, req.session_id, user_id, owner_token)
        if use_pi and last_event_id is None:
            pi_session_id = req.session_id or str(uuid.uuid4())
            # Direct route tests use a minimal close-only DB stand-in. Real
            # requests always carry AsyncSession and persist the capability.
            if db is not None and hasattr(db, "execute"):
                conversation, created = await AsyncHistoryService(
                    db
                ).get_or_create_conversation_with_created(
                    pi_session_id, user_id=user_id
                )
                pi_owner_token = _pi_stream_capability(
                    conversation, created, user_id, owner_token
                )
    finally:
        # Release the connection NOW, not when the stream ends. The guard is
        # the only consumer; nothing downstream uses ``db``.
        if db is not None:
            await db.close()

    session_key = (pi_session_id if use_pi else req.session_id) or ""

    # Runtime observability (W1): request-scoped correlation, bound INSIDE the
    # streaming generators (a `with` in the route body would exit before the
    # stream starts). stream_prompt / chat_stream bind turn_id/run_id on top of
    # this, so the full identity chain reaches tool metrics + durable job rows.
    request_id = rt_ctx.new_request_id()

    if last_event_id is not None:
        # DUP-1: a POST carrying Last-Event-ID is a RESUME (read) — never a
        # new execution. Replay the buffered turn's missed events and
        # terminate; do NOT send a prompt RPC.
        return StreamingResponse(
            _resume_generator(session_key, last_event_id, req.message, request_id=request_id),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    if use_pi:
        assert pi_session_id
        await _record_frontend_cartographic_observation(pi_session_id, req.map_state)
        async def pi_event_generator():
            buffer = TurnEventBuffer(session_key, req.message)
            _turn_resume_registry.register(session_key, buffer)
            # One id scope per turn: ids stay monotonic across batched token
            # events and structural events, in emission order (see sse.py).
            with sse_event_id_scope(), rt_ctx.bind_runtime_context(
                request_id=request_id, session_id=req.session_id
            ):
                try:
                    if pi_owner_token:
                        session_event = sse_event("session", {
                            "session_id": pi_session_id,
                            "owner_token": pi_owner_token,
                        })
                        buffer.record(session_event)
                        yield session_event
                    async for chunk in _sse_batched(
                        _recorded(
                            pi_bridge.stream_prompt(
                                req.message, session_id=pi_session_id
                            ),
                            buffer,
                        )
                    ):
                        yield chunk
                except Exception as e:
                    logger.error(f"Pi bridge stream error: {e}", exc_info=True)
                    err = sse_event("error", {"error": "Internal server error"})
                    buffer.record(err, force_terminal=True)
                    yield err
                finally:
                    if not buffer.ended:
                        buffer.mark_ended(aborted=True)

        return StreamingResponse(
            pi_event_generator(),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    # Legacy path: 使用 ChatEngine
    async def event_generator():
        buffer = TurnEventBuffer(session_key, req.message)
        _turn_resume_registry.register(session_key, buffer)
        with sse_event_id_scope(), rt_ctx.bind_runtime_context(
            request_id=request_id, session_id=req.session_id
        ):
            try:
                async for event in _recorded(
                    get_engine().chat_stream(
                        req.message,
                        session_id=req.session_id,
                        map_state=req.map_state,
                        skill_name=req.skill_name,
                        user_id=user_id,
                        project_id=req.project_id,
                    ),
                    buffer,
                ):
                    yield event
            except Exception as e:
                logger.error(f"Stream error: {e}", exc_info=True)
                err = sse_event("error", {"error": "Internal server error"})
                buffer.record(err, force_terminal=True)
                yield err
            finally:
                if not buffer.ended:
                    buffer.mark_ended(aborted=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    _user: dict = Depends(get_current_user_optional),
):
    """列出当前用户的历史会话；匿名调用方返回空列表（A2）。

    审计 A5：加 limit (默认 50, 上限 200) + offset 分页，直接传递至 DB 查询。
    """
    user_id = _user.get("user_id")
    async with async_db_session() as db:
        sessions = await AsyncHistoryService(db).list_sessions(limit=limit, offset=offset, user_id=user_id)
        return {
            "total": len(sessions),
            "limit": limit,
            "offset": offset,
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "createdAt": s.created_at.timestamp() * 1000,
                    "updatedAt": s.updated_at.timestamp() * 1000,
                }
                for s in sessions
            ],
        }


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: str,
    conv: Conversation = Depends(require_owned_session),
):
    """获取会话详情（只读）— 受所有权检查保护（A2 + SEC-08）。"""
    return {
        "id": conv.id,
        "title": conv.title,
        "createdAt": conv.created_at.timestamp() * 1000,
        "updatedAt": conv.updated_at.timestamp() * 1000,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "timestamp": m.created_at.timestamp() * 1000,
            }
            for m in conv.messages
            if m.role in ("user", "assistant")
        ],
    }


@router.get("/sessions/{session_id}/map-state")
async def get_session_map_state(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
):
    """Return persisted map state (viewport, layers) for session restoration.

    审计 S31：之前 _user 注入但未做所有权校验 —— 任何认证用户知道 session_id
    就能读取他人的 viewport/layers。复用 get_session_detail 的同款检查。
    SEC-08：匿名会话需匹配 owner_token。
    """
    from app.services.session_data import session_data_manager
    state = await session_data_manager.get_map_state(session_id)
    # Restoration may use a prior runtime observation only when it belongs to
    # the current authoritative desired state. Expose the bounded cartographic
    # fingerprint alongside the state so the browser never revives an older
    # MapSpec generation after a reload race.
    response_state = dict(state)
    mapspec = state.get("mapspec")
    if isinstance(mapspec, dict):
        from app.lib.cartography.quality_loop import cartographic_fingerprint

        response_state["_current_cartographic_fingerprint"] = (
            cartographic_fingerprint(mapspec)
        )
    return {"session_id": session_id, "map_state": response_state}


class MapStatePushRequest(BaseModel):
    viewport: Optional[dict] = None
    layers: Optional[list] = None
    base_layer: Optional[str] = None
    # F4: monotonic client seq for the viewport write — an out-of-order older
    # POST landing after the turn-start write is rejected as stale.
    seq: Optional[int] = None


@router.post("/sessions/{session_id}/map-state", status_code=204)
async def push_session_map_state(
    session_id: str,
    req: MapStatePushRequest,
    _conv: Conversation = Depends(require_owned_session),
):
    """Persist live map state pushed by the frontend during agent execution.

    审计 S31：同 get_session_map_state，跨租户写入必须拒绝。
    SEC-08：匿名会话需匹配 owner_token。
    """
    from app.services.session_data import session_data_manager
    if req.viewport:
        await session_data_manager.set_map_state(session_id, "viewport", req.viewport, seq=req.seq)
    if req.layers is not None:
        await session_data_manager.set_map_state(session_id, "layers", req.layers)
    if req.base_layer:
        await session_data_manager.set_map_state(session_id, "base_layer", req.base_layer)


class CartographicRuntimeObservationRequest(BaseModel):
    """Bounded actual MapLibre evidence for one MapSpec generation."""

    # Client-minted wall-clock/monotonic hybrid. Arrival order is not state
    # order: a slow stale POST must not overwrite a newer live observation.
    client_generation: int = Field(ge=1, le=9_007_199_254_740_991)
    mapspec_fingerprint: str = Field(min_length=16, max_length=96)
    layers: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    viewport: dict[str, Any] = Field(default_factory=dict)
    style_loaded: bool
    reconcile_error: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _cap_serialized_size(self):
        if len(self.model_dump_json().encode("utf-8")) > 256 * 1024:
            raise ValueError("serialized cartographic observation exceeds 256KB")
        return self


@router.post("/sessions/{session_id}/cartographic-observation")
async def push_cartographic_runtime_observation(
    session_id: str,
    req: CartographicRuntimeObservationRequest,
    _conv: Conversation = Depends(require_owned_session),
):
    """Persist live post-reconcile evidence and re-run the trusted gate.

    This endpoint is event-driven by MapSpec reconciliation. It is not called
    for token streaming or ordinary pans, and it never replaces canonical
    desired MapSpec/session layers.
    """
    layers = _project_frontend_layers(req.layers)
    viewport = _bounded_observation_fragment(req.viewport) or {}
    async with session_lock_registry.lock(session_id):
        session_data_manager.invalidate_local_cache(session_id)
        state = await session_data_manager.get_map_state(session_id)
        previous = state.get("_cartographic_observation")
        from app.lib.cartography.quality_loop import cartographic_fingerprint
        from app.services.mapspec.store import mapspec_store_instance

        current_mapspec = await mapspec_store_instance.get_mapspec(session_id)
        current_fingerprint = (
            cartographic_fingerprint(current_mapspec)
            if isinstance(current_mapspec, dict)
            else ""
        )
        previous_generation = (
            previous.get("client_generation")
            if isinstance(previous, dict) else None
        )
        rejection_reason = (
            "stale_mapspec_fingerprint"
            if not current_fingerprint
            or req.mapspec_fingerprint != current_fingerprint
            else "stale_client_generation"
            if (
            isinstance(previous_generation, int)
            and req.client_generation <= previous_generation
            )
            else ""
        )
        if rejection_reason:
            # Never echo an old repair action: callers dispatch a returned
            # action immediately, so a stale response must be an inert view of
            # the newest trusted result.
            stored_review = state.get("_cartographic_review")
            if isinstance(stored_review, dict):
                review = dict(stored_review)
                review.pop("repair_action", None)
            else:
                review = {
                    "session_id": session_id,
                    "cartography": {
                        "status": "not_evaluated",
                        "trusted": False,
                        "evaluated": False,
                        "passed": False,
                        "termination_reason": "stale_runtime_observation",
                    },
                    "overall_passed": False,
                }
            return {
                "observation_sequence": int(
                    previous.get("sequence") or 0
                ) if isinstance(previous, dict) else 0,
                "observation_accepted": False,
                "observation_rejection_reason": rejection_reason,
                **review,
            }
        try:
            sequence = (
                int(previous.get("sequence", 0)) + 1
                if isinstance(previous, dict) else 1
            )
        except (TypeError, ValueError):
            sequence = 1
        observation = {
            "session_id": session_id,
            "sequence": sequence,
            "client_generation": req.client_generation,
            "source": "frontend_runtime",
            "mapspec_fingerprint": req.mapspec_fingerprint,
            "layer_count": len(layers),
            "layers": layers,
            "viewport": viewport if isinstance(viewport, dict) else {},
            "style_loaded": req.style_loaded,
            "reconcile_error": req.reconcile_error,
        }
        persisted = await session_data_manager.set_map_state(
            session_id, "_cartographic_observation", observation
        )
        if persisted is False:
            raise HTTPException(
                status_code=503,
                detail="Cartographic observation could not be persisted",
            )
        from app.agent_pi_bridge import evaluate_cartographic_session

        try:
            review = await evaluate_cartographic_session(
                session_id, session_lock_held=True
            )
        except Exception as review_error:  # noqa: BLE001 - observation remains accepted
            logger.warning(
                "Cartographic observation evaluation unavailable for %s: %s",
                session_id,
                review_error,
            )
            review = {
                "session_id": session_id,
                "cartography": {
                    "status": "not_evaluated",
                    "trusted": False,
                    "evaluated": False,
                    "passed": False,
                    "termination_reason": "evaluation_unavailable",
                },
                "overall_passed": False,
            }
    return {
        "observation_sequence": sequence,
        "observation_accepted": True,
        **review,
    }


class MapActionAck(BaseModel):
    """单条地图动作终态 ACK（V3 闭环：前端上报命令执行终态）。"""

    action_id: str = Field(min_length=1, max_length=64)
    command: str = Field(max_length=64)
    status: Literal["succeeded", "failed", "cancelled", "superseded"]
    error: str = Field(default="", max_length=500)
    started_at: str = Field(default="", max_length=64)
    finished_at: str = Field(default="", max_length=64)
    # le=1e15 拒绝 Infinity/超大值（json.loads("1e999") 会解析成 inf，存进
    # duration_ms 会在序列化/聚合时产生非有限值）。
    duration_ms: Optional[float] = Field(default=None, ge=0, le=1e15)
    correlation: Optional[dict] = None
    requested: Optional[dict] = None
    actual: Optional[dict] = None

    @model_validator(mode="after")
    def _cap_serialized_size(self):
        # 单条 ACK 序列化上限 16KB —— requested/actual 是无界 dict，防超大上报
        # 撑爆会话存储。超限抛 ValueError -> FastAPI 返回 422。
        if len(self.model_dump_json().encode("utf-8")) > 16 * 1024:
            raise ValueError("serialized ack exceeds 16KB")
        return self


class MapActionAckRequest(BaseModel):
    """地图动作 ACK 批量上报体（V3 闭环），单批 ≤50 条。"""

    acks: list[MapActionAck] = Field(max_length=50)


async def _persist_map_action_acks_locked(
    session_id: str, req: MapActionAckRequest
) -> dict[str, Any]:
    """Persist and evaluate ACKs while the session delete lock is held."""
    from app.agent_pi_bridge import (
        evaluate_cartographic_session,
        is_cartographic_session_deleted,
    )
    # Resolve at call time so deployments/tests that swap the configured
    # session-store adapter use the same source of truth as the route.
    from app.services.session_data import session_data_manager as ack_store

    if is_cartographic_session_deleted(session_id):
        raise HTTPException(status_code=410, detail="Session was deleted")
    # Process-local tombstone misses other replicas. The Redis/map_state
    # flag is the cross-replica contract written on session delete.
    deleted_state = await ack_store.get_map_state(session_id)
    if deleted_state.get("_cartographic_deleted") is True:
        raise HTTPException(status_code=410, detail="Session was deleted")

    accepted = 0
    duplicates = 0
    dropped = 0
    stored = await ack_store.get_map_action_events(session_id)
    snapshot_stale = False
    for ack in req.acks:
        if await ack_store.append_map_action_event(
            session_id, ack.model_dump(exclude_none=True)
        ):
            accepted += 1
            snapshot_stale = True
            _ack_turn_id = (ack.correlation or {}).get("turn_id")
            if _ack_turn_id:
                record_map_action_acked_by_turn(_ack_turn_id, ack.action_id, ack.status)
            continue
        if snapshot_stale:
            stored = await ack_store.get_map_action_events(session_id)
            snapshot_stale = False
        if any(event.get("action_id") == ack.action_id for event in stored):
            duplicates += 1
        else:
            dropped += 1

    result: dict[str, Any] = {"accepted": accepted, "duplicates": duplicates}
    if dropped:
        result["dropped"] = dropped
    if accepted:
        try:
            evaluation = await evaluate_cartographic_session(
                session_id, session_lock_held=True
            )
        except Exception as review_error:  # noqa: BLE001 - ACK is already durable
            logger.warning(
                "Cartographic ACK evaluation unavailable for %s: %s",
                session_id,
                review_error,
            )
            evaluation = None
        # Preserve the established telemetry-only ACK response when this ACK
        # is unrelated to a cartographic loop. The evaluator still always ran,
        # so a fresh replica can rehydrate a real pending repair.
        if evaluation is not None and (
            (evaluation.get("cartography") or {}).get("termination_reason")
            != "no_session_harness"
        ):
            result["cartography"] = evaluation
            if evaluation.get("repair_action"):
                result["repair_action"] = evaluation["repair_action"]
    return result


@router.post("/sessions/{session_id}/map-action-ack")
async def push_map_action_acks(
    session_id: str,
    req: MapActionAckRequest,
    request: Request,
    _conv: Conversation = Depends(require_owned_session),
):
    """接收前端上报的地图动作终态 ACK（V3 闭环）。

    鉴权与 map-state 推送完全一致（require_owned_session：登录用户归属或
    SEC-08 匿名 owner_token 匹配）。存储按 action_id 幂等 —— 首达终态获胜，
    重复 ACK 计入 duplicates。fire-and-forget 遥测：存储层自带降级（Redis
    不可达仅丢事件），端点成败不影响地图交互。harness 评估时直接从 session
    存储读取 ACK（单一事实源），此处不再另写 harness。

    F26: 存储层 append 返回 False 有两种含义 —— 真重复（首达终态获胜，事件
    已在库里）与降级丢弃（Redis 不可达，事件根本没入库）。之前两者都计入
    duplicates，客户端无法区分"丢了"与"重了"、也就不会重试真正的丢失。现在
    对 False 的 ACK 回读一次：action_id 在库 → duplicate；不在 → 计入 additive
    的 ``dropped`` 字段（仅在有丢弃时出现，响应形状向后兼容）。客户端可对
    dropped 重试。回读竞态（判重后被并发淘汰/并发首达）只影响计数归类，不
    影响存储正确性。P2 (round-2 review): 回读只做一次前置快照 + 仅在刚有
    append 成功（快照可能过期）时刷新，不再逐条被拒 ACK 全量回读（O(n^2)）。

    限速 per client IP（同 ws.py/auth.py）：Redis 不可达时 is_allowed fail-open
    放行，不阻断正常交互。
    """
    client_ip = request.client.host if request.client else "unknown"
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"map_action_ack:{client_ip}", _ACK_RATE_LIMIT_MAX, _ACK_RATE_LIMIT_WINDOW
    ):
        raise HTTPException(status_code=429, detail="Too many map action acks")
    # Serialize with deletion and observation. An ACK that arrives after the
    # delete tombstone cannot recreate bounded event/review state.
    async with session_lock_registry.lock(session_id):
        session_data_manager.invalidate_local_cache(session_id)
        return await _persist_map_action_acks_locked(session_id, req)


@router.get("/skills")
async def list_skills_api(_user: dict = Depends(get_current_user_optional)):
    """列出可用的 .md 技能 — 需要认证（技能列表属于内部元数据）。"""
    from app.tools.skills import list_md_skills
    return {"skills": list_md_skills()}


@router.delete("/sessions/{session_id}")
async def clear_session(
    session_id: str,
    _user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
    _conv: Conversation = Depends(require_owned_session),
):
    """清除会话（内存 + DB）— 受所有权检查保护（A2 + SEC-08）。"""
    user_id = _user.get("user_id")

    # BUG-18：Pi 路径下 clear_session 之前是个 dead TODO —— 只删了 DB 行，
    # 但 Pi agent 子进程里该 session 的在途 prompt 不会被中断，会继续消耗
    # token / 写文件。现在先对 Pi bridge 发 abort（fire-and-forget RPC，
    # 失败仅记日志，不影响后续 DB 清理），再走 legacy 清理。
    # F5: abort 携带被删会话的 id —— session-blind 的 abort 会把*别的*会话的
    # 在途 turn 一起杀掉；bridge 据此在其它会话的 turn 在途时跳过并记日志。
    if _use_pi_bridge():
        try:
            await pi_bridge.abort(session_id=session_id)
        except Exception as e:  # noqa: BLE001 — abort 失败不能阻塞删除流程
            logger.warning("Pi bridge abort failed for session %s: %s", session_id, e)

    from app.agent_pi_bridge import (
        clear_cartographic_session_state,
        restore_cartographic_session_state,
    )

    async with session_lock_registry.lock(session_id):
        session_data_manager.invalidate_local_cache(session_id)
        # Tombstone first: an evaluation already in flight must not recreate
        # review/repair state after the authoritative session is removed.
        clear_cartographic_session_state(session_id)
        ok = await get_engine().clear_session(
            session_id, user_id=user_id, owner_token=owner_token
        )
        if not ok:
            restore_cartographic_session_state(session_id)
        else:
            from app.services.mapspec.store import mapspec_store_instance
            try:
                await mapspec_store_instance.clear_session_files(session_id)
            except FileNotFoundError:
                pass
            except Exception as purge_error:  # noqa: BLE001
                logger.error(
                    "Unable to purge durable MapSpec state for %s: %s",
                    session_id,
                    purge_error,
                )
            # Cross-replica tombstone. The Redis session-state adapter applies
            # its normal bounded TTL; late work from another replica therefore
            # cannot recreate a deleted session's cartographic evidence.
            persisted = await session_data_manager.set_map_state(
                session_id, "_cartographic_deleted", True
            )
            if persisted is False:
                raise HTTPException(
                    status_code=503,
                    detail="Session deleted but deletion tombstone persistence failed",
                )
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    # P2 (round-2 review): purge the deleted session's resume buffers. Without
    # this, anyone holding session_id + the message could keep replaying the
    # deleted session's buffered SSE events from the process-local resume
    # registry until LRU eviction.
    _turn_resume_registry.clear_session(session_id)
    return {"status": "ok"}


@router.get("/tools")
async def list_tools(_user: dict = Depends(get_current_user_optional)):
    """列出可用工具 — 需要认证（工具 schema 含 tier-3 危险工具）。"""
    return {"tools": get_registry().get_schemas()}


class ToolExecuteRequest(BaseModel):
    tool: str
    arguments: dict = {}
    session_id: Optional[str] = None
    # tier-3 工具（如 create_new_skill —— 写盘 + importlib.exec_module 等同 RCE）
    # 必须显式确认才执行（审计 S30）。
    confirm_destructive: bool = False


@router.post("/tools/execute", response_model=None)
async def execute_tool_direct(req: ToolExecuteRequest, _user: dict = Depends(require_admin)):
    """直接执行单个工具（非流式通过 chat）

    审计 S30：原本任何登录用户都能 dispatch 任意工具，包括 tier-3 的
    `create_new_skill`（写盘 + importlib.exec_module 等同 RCE）和
    `what_if_simulate`。改为 admin-only + tier-3 必须显式 confirm_destructive。
    """
    tool_name = req.tool
    args = req.arguments
    if not tool_name:
        raise HTTPException(status_code=400, detail="missing tool name")

    # tier 校验：catalog 把工具分为 1/2/3 层，3 = rare / heavy / destructive
    registry = get_registry()
    tier = registry.metadata(tool_name).get("tier", 1)
    if tier >= 3 and not req.confirm_destructive:
        raise HTTPException(
            status_code=403,
            detail=f"Tier-{tier} 工具 {tool_name} 需要显式 confirm_destructive=true",
        )

    try:
        result = await registry.dispatch(tool_name, args, session_id=req.session_id)
        return result
    except Exception as e:
        logger.error(f"Tool execute error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Tool execution failed")
