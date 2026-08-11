"""Chat API Route - SSE 流式对话"""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional, get_owner_token, require_admin, require_owned_session
from app.core.database import get_async_db
from app.models.db_model import Conversation
from app.services.chat.event_resume import TurnEventBuffer, TurnResumeRegistry
from app.services.chat_engine import ChatEngine
from app.services.history_service_async import AsyncHistoryService
from app.tools._utils import async_db_session
from app.tools.registry import ToolRegistry

from app.utils.sse import SSEBatcher, sse_event, sse_event_id, sse_event_id_scope, sse_event_type

from app.agent_pi_bridge import PiRpcError, USE_NEW_AGENT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])

# 由 lifespan 在启动时注入
registry: ToolRegistry = None  # type: ignore[assignment]
engine: ChatEngine = None  # type: ignore[assignment]

# DUP-1: process-local SSE turn-resume buffer. One ring buffer per session key
# (most recent turn only; LRU-capped), filled by the route as events are
# emitted. A POST with Last-Event-ID / last_event_id replays from it without
# starting a new turn. Cross-restart persistence is a non-goal.
_turn_resume_registry = TurnResumeRegistry()

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


def get_engine() -> ChatEngine:
    """Return the ChatEngine instance, raising 503 if not yet initialized by lifespan.

    审计 S47：之前 raise RuntimeError -> 全局 exception handler 返回 500 +
    可能泄漏内部模块名。改为 503 让客户端知道是临时不可用（启动窗口）。
    """
    if engine is None:
        raise HTTPException(status_code=503, detail="Service starting up, please retry")
    return engine


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
):
    """DUP-1 resume stream: replay missed events, then terminate cleanly.

    A POST /chat/stream carrying ``Last-Event-ID`` / ``last_event_id`` is a
    RESUME — a read, never a new execution. The route sends no prompt RPC and
    dispatches no tool; it replays what the dropped connection missed from the
    per-session ring buffer and then ends:

      * buffer hit + message match → replay every buffered event with
        ``id > last_event_id`` in order; then terminate with the buffered
        terminal event if it was replayed, a synthesized ``done {resumed:
        true}`` if the client already consumed the terminal (``last_event_id >=
        terminal id`` — the stream must never hang), or a synthesized ``error``
        if the turn was interrupted (aborted on disconnect; no terminal exists).
      * buffer miss (server restart / LRU eviction / message mismatch) → a
        terminal ``error {resumed: false}``; no new turn starts, so a stale
        reconnect can never double-execute the message. The client stops
        auto-retrying on any terminal event.

    Synthesized terminal events carry no ``id:`` (they are not part of the
    original turn's id sequence); the client consumes them for status and
    stops, so ids are not needed for dedup there.
    """
    buffered = _turn_resume_registry.get(session_key)
    if buffered is None or buffered.message != message:
        yield sse_event("error", {
            "error": (
                "Resume unavailable: the turn buffer is gone (server restarted "
                "or the session expired). Please resend your message."
            ),
            "resumed": False,
        })
        return

    for event in buffered.replay_after(last_event_id):
        yield event

    if buffered.terminal_event is not None:
        terminal_id = sse_event_id(buffered.terminal_event)
        if terminal_id is None or terminal_id <= last_event_id:
            # Client already consumed the terminal; reconnect for nothing but
            # a clean close — synthesize one so the stream terminates.
            yield sse_event("done", {"session_id": session_key, "resumed": True})
    elif buffered.aborted:
        # Turn was cut by a client disconnect (server aborted the prompt):
        # replaying the partial content must NOT look like a complete answer.
        yield sse_event("error", {
            "session_id": session_key,
            "error": "连接已中断，本轮未完成且不会自动续跑；请重新发送消息。",
            "resumed": True,
        })
    else:
        # Buffered turn still streaming (a resume racing the live turn):
        # nothing more to add — terminate cleanly.
        yield sse_event("done", {"session_id": session_key, "resumed": True})


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, max_length=5000)
    session_id: Optional[str] = None
    map_state: Optional[dict] = Field(None, description="当前的地图状态（视角、图层等）")
    skill_name: Optional[str] = Field(None, description="要激活的技能名称")


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

    owned = await AsyncHistoryService(db).get_session(
        session_id, user_id=user_id, owner_token=owner_token
    )
    if owned is not None:
        return

    # get_session 对"不存在"和"越权"都返回 None，需区分两者。
    if await db.get(Conversation, session_id) is not None:
        raise HTTPException(status_code=404, detail="Session not found")


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

    if _use_pi_bridge():
        try:
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
    try:
        await _guard_body_session(db, req.session_id, user_id, owner_token)
    finally:
        # Release the connection NOW, not when the stream ends. The guard is
        # the only consumer; nothing downstream uses ``db``.
        if db is not None:
            await db.close()

    session_key = req.session_id or ""
    last_event_id = (
        last_event_id_header
        if last_event_id_header is not None
        else last_event_id_query
    )

    if last_event_id is not None:
        # DUP-1: a POST carrying Last-Event-ID is a RESUME (read) — never a
        # new execution. Replay the buffered turn's missed events and
        # terminate; do NOT send a prompt RPC.
        return StreamingResponse(
            _resume_generator(session_key, last_event_id, req.message),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    if _use_pi_bridge():
        async def pi_event_generator():
            buffer = TurnEventBuffer(session_key, req.message)
            _turn_resume_registry.register(session_key, buffer)
            # One id scope per turn: ids stay monotonic across batched token
            # events and structural events, in emission order (see sse.py).
            with sse_event_id_scope():
                try:
                    async for chunk in _sse_batched(
                        _recorded(
                            pi_bridge.stream_prompt(req.message, session_id=req.session_id),
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
        with sse_event_id_scope():
            try:
                async for event in _recorded(
                    get_engine().chat_stream(
                        req.message,
                        session_id=req.session_id,
                        map_state=req.map_state,
                        skill_name=req.skill_name,
                        user_id=user_id,
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
    return {"session_id": session_id, "map_state": state}


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
    if _use_pi_bridge():
        try:
            await pi_bridge.abort()
        except Exception as e:  # noqa: BLE001 — abort 失败不能阻塞删除流程
            logger.warning("Pi bridge abort failed for session %s: %s", session_id, e)

    ok = await get_engine().clear_session(
        session_id, user_id=user_id, owner_token=owner_token
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
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
