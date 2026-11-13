"""Pi 原生运行中交互 REST 面（G29 / WP04+WP09+WP10）。

端点分两组：

**用户面**（``require_owned_session`` —— 登录归属或匿名 owner_token，
与 clarification/display-confirmation 同族）：

- ``GET  /chat/sessions/{sid}/runtime-interactions`` —— pending + 近期终态 +
  fire-and-forget 投影（断线/刷新水合；同进程代际内可继续作答）。
- ``POST /chat/sessions/{sid}/runtime-interactions/{iid}/answer`` —— 提交
  回答（value/confirmed/cancelled；白名单/类型校验在服务层）。
- ``POST /chat/sessions/{sid}/runtime-interactions/{iid}/cancel`` —— 用户
  取消（合法 cancelled:true 写出）。
- ``POST /chat/sessions/{sid}/turns/{tid}/messages`` —— 运行中追加指令
  （mode=adjust → steer / mode=queue → follow_up；G22 control gate 内
  「身份校验+写出」原子段；真实 disposition 返回）。
- ``GET  /chat/sessions/{sid}/turns/{tid}/queue`` —— 队列可见性（vendor
  get_state 投影 + 本会话追加指令台账）。

**扩展回调面**（``X-Pi-Bridge-Secret`` + 签名 turnToken + 活跃 turn，
与 ``/pi-tools/execute`` 同款信任链）：

- ``POST /pi-tools/interaction-resolve`` —— 扩展把 ctx.ui 的结果交回服务端
  受权消费（选项表反解 + 可得证据分类），返回工具结果 content。

纪律：不 auto-start 任何 turn（ADR-0222 D-4）；答案只流向**当前活跃**
turn 的原生请求；越权/迟到/跨代际一律拒绝且不泄漏内容。
"""
from __future__ import annotations

import logging
from collections import OrderedDict, deque
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.client_ip import client_ip_from
from app.core.rate_limiter import get_rate_limiter
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["AI对话"])
pi_router = APIRouter(prefix="/pi-tools", tags=["pi-tools"])

_RATE_LIMIT_MAX = 120
_RATE_LIMIT_WINDOW = 60

#: 每 session 追加指令台账（有界；队列可见性用）。review P3-8：会话键
#: 本身也有全局 LRU 上限 —— 否则进程生命期内每会话一条永不回收。
_TURN_MESSAGES: "OrderedDict[str, deque]" = OrderedDict()
_TURN_MESSAGES_MAX = 16
_TURN_MESSAGES_SESSIONS_MAX = 512


def _turn_message_ledger(sid: str) -> deque:
    ledger = _TURN_MESSAGES.get(sid)
    if ledger is None:
        ledger = deque(maxlen=_TURN_MESSAGES_MAX)
        _TURN_MESSAGES[sid] = ledger
        while len(_TURN_MESSAGES) > _TURN_MESSAGES_SESSIONS_MAX:
            _TURN_MESSAGES.popitem(last=False)
    else:
        _TURN_MESSAGES.move_to_end(sid)
    return ledger


async def _raise_if_deleted(session_id: str) -> None:
    from app.services.session_data import session_data_manager

    if await session_data_manager.get_state_field(session_id, "_cartographic_deleted") is True:
        raise HTTPException(status_code=404, detail="Session not found")


async def _record_ratelimit(request: Request) -> None:
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"pi-interactions:{client_ip_from(request)}",
        _RATE_LIMIT_MAX,
        _RATE_LIMIT_WINDOW,
    ):
        raise HTTPException(status_code=429, detail="Too many interaction requests")


def _resolve_rpc(record):
    """按记录的 (session, turn) 解析活跃 worker 的 RPC 客户端。

    返回 (rpc, alive)。校验：会话的在飞 turn 仍是记录的 turn，且其 worker
    子进程仍活着 —— 否则一律不允许写出（旧卡不消费，后继 turn 不受扰）。
    """
    from app.agent_pi_bridge import get_active_turn_entry

    entry = get_active_turn_entry(record.session_id)
    if entry is None or entry.turn_id != record.turn_id or entry.bridge is None:
        return None, False
    rpc = entry.bridge.rpc
    alive = rpc.is_alive() if hasattr(rpc, "is_alive") else True
    return (rpc, alive) if alive else (None, False)


# ── 用户面：水合 / 回答 / 取消 ─────────────────────────────────────────


@router.get("/sessions/{session_id}/runtime-interactions")
async def get_runtime_interactions(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """pending + 近期终态 + 只读投影（重连/刷新水合；WP10）。"""
    await _raise_if_deleted(session_id)
    from app.services.chat.pi_runtime_interaction import get_interaction_service

    service = get_interaction_service()
    service.expire_due(session_id)
    return service.session_view(session_id)


class InteractionAnswerRequest(BaseModel):
    value: Optional[str] = None
    confirmed: Optional[bool] = None
    cancelled: bool = False


_ANSWER_STATUS = {
    "NOT_FOUND": 404,
    "ANSWER_INVALID": 400,
    "WORKER_GONE": 409,
    "WRITE_FAILED": 503,
}


@router.post("/sessions/{session_id}/runtime-interactions/{interaction_id}/answer")
async def answer_runtime_interaction(
    session_id: str,
    interaction_id: str,
    req: InteractionAnswerRequest,
    request: Request,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """提交回答（owner 校验 → 白名单校验 → 单向 stdin 写出 → 状态翻转）。"""
    await _raise_if_deleted(session_id)
    await _record_ratelimit(request)
    from app.services.chat.pi_runtime_interaction import get_interaction_service

    answer: dict[str, Any] = {}
    if req.cancelled:
        answer["cancelled"] = True
    elif req.value is not None:
        answer["value"] = req.value
    elif req.confirmed is not None:
        answer["confirmed"] = req.confirmed
    else:
        raise HTTPException(status_code=400, detail="answer requires value/confirmed/cancelled")

    service = get_interaction_service()
    service.expire_due(session_id)
    outcome = await service.submit_answer(session_id, interaction_id, answer, _resolve_rpc)
    result = outcome.get("result")
    if result == "rejected":
        code = outcome.get("reason", "REJECTED")
        raise HTTPException(
            status_code=_ANSWER_STATUS.get(code, 400),
            detail={"error": code, "interaction_id": interaction_id},
        )
    # accepted / duplicate / late —— duplicate/late 返回当前真实状态（幂等）。
    return {
        "result": result,
        "state": outcome.get("state"),
        "interaction": outcome.get("interaction"),
    }


@router.post("/sessions/{session_id}/runtime-interactions/{interaction_id}/cancel")
async def cancel_runtime_interaction(
    session_id: str,
    interaction_id: str,
    request: Request,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """用户取消当前问题（合法 cancelled:true；等待结束，无副作用续行）。"""
    await _raise_if_deleted(session_id)
    await _record_ratelimit(request)
    from app.services.chat.pi_runtime_interaction import get_interaction_service

    service = get_interaction_service()
    outcome = await service.cancel_interaction(
        session_id, interaction_id, _resolve_rpc, source="user",
    )
    result = outcome.get("result")
    if result == "rejected":
        code = outcome.get("reason", "REJECTED")
        raise HTTPException(
            status_code=_ANSWER_STATUS.get(code, 400),
            detail={"error": code, "interaction_id": interaction_id},
        )
    return {
        "result": result,
        "state": outcome.get("state"),
        "interaction": outcome.get("interaction"),
    }


# ── 用户面：运行中追加指令 + 队列可见性（WP09） ────────────────────────


class TurnMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    mode: str = Field(pattern="^(adjust|queue)$")


@router.post("/sessions/{session_id}/turns/{turn_id}/messages")
async def send_turn_message(
    session_id: str,
    turn_id: str,
    req: TurnMessageRequest,
    request: Request,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """运行中追加指令：adjust → steer（下一可响应位置调整）/ queue → follow_up。

    G22 control gate 内完成「turn 身份校验 + stdin 写出」原子段（与 abort
    同款）；写出后收响应，真实 disposition 如实返回并投递 SSE。接纳回执
    只证明队列状态 —— 实际消费经队列可见性观察（get_state + 台账）。
    """
    await _raise_if_deleted(session_id)
    await _record_ratelimit(request)
    import asyncio

    from app.agent_pi_bridge import get_active_turn_entry, is_active_pi_turn

    if not await is_active_pi_turn(session_id, turn_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "TURN_NOT_ACTIVE",
                "message": "该回合已结束，未送达。请作为新消息发送。",
            },
        )
    entry = get_active_turn_entry(session_id)
    if entry is None or entry.turn_id != turn_id or entry.bridge is None:
        # 跨 pod：turn 不在本进程（affinity 缺失）—— 诚实拒绝。
        raise HTTPException(
            status_code=409,
            detail={
                "error": "TURN_NOT_LOCAL",
                "message": "该回合运行在其它实例上；请经原会话流重试。",
            },
        )
    bridge = entry.bridge
    command = "steer" if req.mode == "adjust" else "follow_up"
    gate = bridge._control_gate
    try:
        await asyncio.wait_for(gate.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail={"error": "CONTROL_GATE_BUSY", "message": "控制面忙，请稍后重试。"},
        )
    issued = None
    try:
        # 门内身份复核（对象同一性）：交接后绝不把追加指令写给后继 turn。
        if bridge._current_turn is not entry:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "TURN_HANDED_OFF",
                    "message": "回合已交接，追加指令未送达。",
                },
            )
        issued = await bridge.rpc.issue(command, {"message": req.message})
    finally:
        gate.release()
    disposition: Any = None
    rpc_error = ""
    try:
        disposition = await asyncio.wait_for(bridge.rpc.collect(issued), timeout=15.0)
    except asyncio.TimeoutError:
        rpc_error = "response-timeout"
    except Exception as exc:  # noqa: BLE001 — PiRpcError 等
        rpc_error = str(exc)[:160]
    entry_record = {
        "turn_id": turn_id,
        "mode": req.mode,
        "command": command,
        "message_excerpt": req.message[:80],
        "disposition": disposition,
        "error": rpc_error or None,
    }
    _turn_message_ledger(session_id).append(entry_record)
    # SSE 披露（当前 turn 的流会翻译发射 pi_turn_message）。
    try:
        bridge.rpc.events.put_nowait({
            "type": "pi_steer_disposition",
            "session_id": session_id,
            "turn_id": turn_id,
            "_webgis_steer": {
                "session_id": session_id,
                "turn_id": turn_id,
                "mode": req.mode,
                "command": command,
                "disposition": disposition,
                "error": rpc_error or None,
            },
        })
    except Exception:  # noqa: BLE001 — 队列满：丢弃投影（台账仍有记录）
        logger.debug("[pi-interactions] steer disposition SSE dropped", exc_info=True)
    # 用户输入进会话转录（best-effort；provenance = owner 校验过的本路由）。
    try:
        from app.core.database import async_db_session
        from app.services.history_service_async import AsyncHistoryService

        async with async_db_session() as db:
            await AsyncHistoryService(db).save_message(session_id, "user", req.message)
    except Exception:  # noqa: BLE001 — 转录绝不阻断指令
        logger.debug("[pi-interactions] steer transcript save failed", exc_info=True)
    if rpc_error:
        return {"accepted": False, "error": rpc_error, "entry": entry_record}
    return {"accepted": True, "disposition": disposition, "entry": entry_record}


@router.get("/sessions/{session_id}/turns/{turn_id}/queue")
async def get_turn_queue(
    session_id: str,
    turn_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """队列可见性：vendor get_state 投影（pendingMessageCount 等）+ 台账。"""
    await _raise_if_deleted(session_id)
    import asyncio

    from app.agent_pi_bridge import get_active_turn_entry

    entry = get_active_turn_entry(session_id)
    state: dict[str, Any] = {}
    error = ""
    if entry is not None and entry.turn_id == turn_id and entry.bridge is not None:
        try:
            state = await asyncio.wait_for(
                entry.bridge.rpc.request("get_state", {}), timeout=10.0,
            ) or {}
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:160]
    return {
        "turn_id": turn_id,
        "vendor_state": {
            "is_streaming": state.get("isStreaming"),
            "pending_message_count": state.get("pendingMessageCount"),
            "steering_mode": state.get("steeringMode"),
            "follow_up_mode": state.get("followUpMode"),
        },
        "messages": list(_turn_message_ledger(session_id)),
        "error": error or None,
    }


# ── 扩展回调面：interaction-resolve（受权消费） ────────────────────────


class InteractionResolveRequest(BaseModel):
    toolCallId: str = Field(min_length=1, max_length=128)
    turnToken: str = Field(min_length=1, max_length=512)
    envelopeId: str = Field(min_length=1, max_length=64)
    nativeId: str = Field(default="", max_length=80)
    answerKind: str = Field(pattern="^(value|confirmed|no_answer|cancelled|timeout)$")
    display: str = Field(default="", max_length=8000)


@pi_router.post("/interaction-resolve")
async def resolve_interaction(
    req: InteractionResolveRequest,
    _request: Request,
) -> dict[str, Any]:
    """扩展把 ctx.ui 结果交回服务端（bridge secret + turn token 校验同 execute）。"""
    import hmac as _hmac

    from app.agent_pi_bridge import is_active_pi_turn
    from app.core.bridge_secret import get_bridge_secret
    from app.services.chat.pi_interaction_envelope import consume_answer
    from app.services.chat.pi_runtime_interaction import get_interaction_service
    from app.services.chat.pi_turn_context import verify_turn_token

    secret = get_bridge_secret()
    # 与 /pi-tools/execute 同款双重校验（路由层手工执行 —— 共用依赖会引入
    # Header 参数形状差异；逻辑一致：secret + 签名 turn token + 活跃 turn）。
    header_secret = _request.headers.get("X-Pi-Bridge-Secret", "")
    if not header_secret or not _hmac.compare_digest(header_secret, secret):
        raise HTTPException(status_code=401, detail="Invalid or missing bridge secret")
    verified = verify_turn_token(secret, req.turnToken or "")
    if verified is None:
        raise HTTPException(status_code=401, detail="Invalid, missing, or expired Pi turn context")
    session_id = str(verified["session_id"])
    turn_id = str(verified["turn_id"])
    if not await is_active_pi_turn(session_id, turn_id):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "TURN_CONTEXT_INACTIVE",
                "session_id": session_id,
                "turn_id": turn_id,
                "guidance": "The turn owning this interaction has completed or been superseded.",
            },
        )

    service = get_interaction_service()
    record = None
    for candidate in list(service._by_id.values()):
        if candidate.envelope_id == req.envelopeId:
            record = candidate
            break
    interaction_state = record.state.value if record is not None else ""
    record_answer_kind = record.answer_kind if record is not None else ""
    record_envelope = dict(record.envelope) if record is not None and record.envelope else None

    outcome = consume_answer(
        session_id=session_id,
        turn_id=turn_id,
        tool_call_id=req.toolCallId,
        envelope_id=req.envelopeId,
        native_id=req.nativeId,
        answer_kind=req.answerKind,
        display=req.display,
        interaction_state=interaction_state,
        record_answer_kind=record_answer_kind,
        envelope=record_envelope,
    )
    if not outcome.get("ok"):
        raise HTTPException(
            status_code=409,
            detail={"error": outcome.get("code", "RESOLVE_REJECTED")},
        )
    return {
        "content": outcome.get("content"),
        "code": outcome.get("code"),
        "domain": outcome.get("domain"),
    }
