"""Pi 原生运行中交互 —— 身份、生命周期与有界投影（G29 / WP03+WP07+WP10+WP11）。

职责（单一模块，不建第二事件总线/工具注册表）：

- **登记**：``PiRpcClient`` 读循环把 ``extension_ui_request`` 交给本服务 sink；
  归属来自 ``bridge._current_turn``（G22 的已验证 turn 身份），绝不从标题/
  选项文字猜测会话或工具。
- **状态机**：``pending → answer_written → resolved``（观察到当前任务继续）
  及终态 ``cancelled / timed_out / invalidated``。四个事实分开记录：用户提交
  被接纳（pending→answer_written 的翻转点）、响应字节已写 stdin（send_ui_response
  返回）、扩展等待结束并继续（resolve 回调 / turn 活动观察）、领域结果完成
  （G28 既有事件，不在本模块伪造）。
- **幂等与竞态**：每记录一把 asyncio.Lock；双标签页至多一次有效翻转，重复
  提交返回当前终态；迟到提交返回 ``late`` 语义（不写第二次 stdin）。
- **失效**：turn 注销 / abort / 进程死亡 → ``invalidated``（SSE 更新事件）；
  断线重连在**同进程代际且同 turn** 时可恢复作答，跨代际旧卡只读终止。
- **等待记账（WP07）**：``waiting_for_user(turn_id)`` 供 bridge 把「等待用户」
  从 stall/total 预算中记为可信等待（heartbeat 仍发）。
- **有界（WP11）**：每 session pending 上限、正文/选项界（协议层）、终态保留
  条数上限、fire-and-forget last-value 合并（1s 窗口折叠）。
- **不跨进程复活**：视图为进程内事实 + SSE ring 重放 + GET 水合；进程重启后
  旧记录按 boot 代际只读终止（绝不承诺恢复 Node 内存中的 Promise）。

模块顶层不 import ``agent_pi_bridge``（防环）；桥侧在调用点惰性解析。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from app.services.chat.pi_ui_protocol import (
    DIALOG_METHODS,
    FIRE_FORGET_METHODS,
    ParsedUiRequest,
    UiProtocolError,
    build_ui_response,
    parse_ui_request,
)

logger = logging.getLogger(__name__)

#: 进程代际（boot id）：重启后旧快照只读终止（WP10）。
WORKER_BOOT_ID = uuid.uuid4().hex[:12]

INTERACTION_ID_PREFIX = "iax-"

#: 每 session 同时 pending 的 dialog 上限（WP11；封套侧另有 per-turn 上限）。
MAX_PENDING_PER_SESSION = int(__import__("os").environ.get("PI_UI_MAX_PENDING_SESSION", "3"))
#: 终态记录保留条数（内存 + 快照）。
MAX_TERMINAL_HISTORY = 20
#: 宿主等待策略上限（对无原生 timeout 的方法设界；原生 timeout 以原生为准）。
PI_INTERACTION_MAX_WAIT_S = float(__import__("os").environ.get("PI_INTERACTION_MAX_WAIT_S", "900"))
#: 原生 timeout 之后的宽限（宿主侧标记 timed_out；期间 vendor 已自行 resolve）。
NATIVE_TIMEOUT_GRACE_S = 5.0
#: fire-and-forget 投影合并窗口与上限。
FIRE_MERGE_WINDOW_S = 1.0
MAX_NOTIFY_TTL_S = 120.0
MAX_NOTIFY_KEEP = 5

_DIALOG_WAITING_STATES = ("pending", "answer_written")


class InteractionState(str, Enum):
    PENDING = "pending"
    ANSWER_WRITTEN = "answer_written"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INVALIDATED = "invalidated"

    @property
    def terminal(self) -> bool:
        return self in (
            InteractionState.RESOLVED,
            InteractionState.CANCELLED,
            InteractionState.TIMED_OUT,
            InteractionState.INVALIDATED,
        )


@dataclass
class RuntimeInteraction:
    """一条原生交互请求的宿主侧记录（内存权威）。"""

    interaction_id: str
    native_id: str
    method: str
    session_id: str
    turn_id: str
    worker_key: str
    boot_id: str = WORKER_BOOT_ID
    state: InteractionState = InteractionState.PENDING
    created_at: float = field(default_factory=time.monotonic)
    answered_at: Optional[float] = None
    resolved_at: Optional[float] = None
    deadline_monotonic: Optional[float] = None
    #: select 的显示选项表（服务器保存；回答按 display 精确反解）。
    options: list[str] = field(default_factory=list)
    title: str = ""
    message: str = ""
    placeholder: str = ""
    prefill: str = ""
    native_timeout_ms: Optional[int] = None
    #: 发起来源（受信封套或原生孤儿）。
    source_tool: str = ""
    source_tool_call_id: str = ""
    envelope_id: str = ""
    business: dict[str, Any] = field(default_factory=dict)
    #: 观察到的回答（bounded display；领域值不进记录）。
    answer_display: str = ""
    answer_kind: str = ""  # value | confirmed | cancelled
    resolve_evidence: str = ""
    #: 目标 RPC 客户端（SSE 更新事件投递用；随 worker 生命周期，不序列化）。
    rpc: Any = field(default=None, repr=False, compare=False)
    #: 配对成功的封套全文（含 option_values/exp —— resolve 消费用；不出投影）。
    envelope: dict = field(default_factory=dict, repr=False, compare=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def public_payload(self) -> dict[str, Any]:
        """SSE/GET 投影（不含 native_id —— 宿主内部身份不外泄作答凭据）。"""
        out: dict[str, Any] = {
            "interaction_id": self.interaction_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "method": self.method,
            "kind": "dialog" if self.method in DIALOG_METHODS else "fire",
            "state": self.state.value,
            "title": self.title,
            "deadline_at": _iso_from_monotonic(self.deadline_monotonic),
            "source": {
                "tool": self.source_tool,
                "tool_call_id": self.source_tool_call_id,
                "envelope_id": self.envelope_id,
                "business": {k: self.business[k] for k in list(self.business)[:6]},
            },
        }
        if self.method == "select":
            out["options"] = list(self.options)
        if self.message:
            out["message"] = self.message
        if self.placeholder:
            out["placeholder"] = self.placeholder
        if self.prefill:
            out["prefill"] = self.prefill
        if self.native_timeout_ms is not None:
            out["native_timeout_ms"] = self.native_timeout_ms
        if self.answer_kind:
            out["answer_kind"] = self.answer_kind
            out["answer_display"] = self.answer_display[:200]
        if self.resolve_evidence:
            out["resolve_evidence"] = self.resolve_evidence[:120]
        return out


def _iso_from_monotonic(monotonic_value: Optional[float]) -> Optional[str]:
    if monotonic_value is None:
        return None
    delta = monotonic_value - time.monotonic()
    wall = datetime.now(timezone.utc).timestamp() + delta
    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _FireProjection:
    """fire-and-forget 方法的 last-value 有界投影（notify 保留短 TTL 列表）。"""

    def __init__(self) -> None:
        self.status: dict[str, dict[str, Any]] = {}
        self.widgets: dict[str, dict[str, Any]] = {}
        self.title: str = ""
        self.editor_suggestion: str = ""
        self.notifies: list[dict[str, Any]] = []
        self._last_emit: dict[str, float] = {}

    def project(self, req: ParsedUiRequest) -> Optional[dict[str, Any]]:
        """返回投影像（含 1s 合并窗）；None = 窗口内合并丢弃。"""
        key = req.method
        now = time.monotonic()
        if now - self._last_emit.get(key, 0.0) < FIRE_MERGE_WINDOW_S:
            return None
        self._last_emit[key] = now
        if req.method == "notify":
            item = {
                "message": req.message,
                "notify_type": req.notify_type,
                "at": _now_iso(),
            }
            self.notifies.append(item)
            self.notifies = self.notifies[-MAX_NOTIFY_KEEP:]
            return {"kind": "notify", **item}
        if req.method == "setStatus":
            entry = {"key": req.status_key, "text": req.status_text}
            if req.status_text is None:
                self.status.pop(req.status_key, None)
            else:
                self.status[req.status_key] = entry
            return {"kind": "status", **entry}
        if req.method == "setWidget":
            entry = {
                "key": req.widget_key,
                "lines": req.widget_lines,
                "placement": req.widget_placement,
            }
            if req.widget_lines is None:
                self.widgets.pop(req.widget_key, None)
            else:
                self.widgets[req.widget_key] = entry
            return {"kind": "widget", **entry}
        if req.method == "setTitle":
            self.title = req.set_title
            return {"kind": "title", "title": self.set_title}
        if req.method == "set_editor_text":
            # 建议输入 —— 只读投影，前端绝不静默覆盖用户草稿（WP08）。
            self.editor_suggestion = req.editor_text
            return {"kind": "editor_suggestion", "text": req.editor_text}
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": list(self.status.values()),
            "widgets": list(self.widgets.values()),
            "title": self.title,
            "editor_suggestion": self.editor_suggestion,
            "notifies": self.notifies[-3:],
        }


class RuntimeInteractionService:
    """进程内单例（与 bridge pool 同生命周期）。"""

    def __init__(self) -> None:
        self._by_id: dict[str, RuntimeInteraction] = {}
        self._by_session: dict[str, list[str]] = {}
        # 封套-native 配对表（review P2-1：实例级 —— 类级 dict 会跨
        # reset_interaction_service 换装共享泄漏；过期/失效时修剪，见
        # _prune_expired_envelopes / invalidate_turn）。
        self._awaiting_envelopes: dict[tuple[str, str], list[dict]] = {}
        self._fire: dict[str, _FireProjection] = {}
        #: 同步上下文（sink）里排队的单向写出 —— 由 bridge 心跳/下一次异步
        #: 触点 flush（sink 绝不做 I/O）。
        self._deferred_writes: list[tuple[Any, dict]] = []
        #: 待写回 SSE 队列的合成事件由调用方（sink）直接投递 rpc.events。
        self._metrics = {"dropped_unknown": 0, "orphan": 0, "cap_rejected": 0}

    # ── sink（PiRpcClient 读循环内调用；同步、非阻塞） ─────────────────

    def make_sink(self, rpc, bridge):
        """构造绑定 (rpc, bridge) 的 UI 请求 sink。

        rpc: PiRpcClient（事件队列投递 + 进程死亡查询）
        bridge: PiBridge（``_current_turn`` 归属 + worker 身份）
        """
        worker_key = f"bridge-{id(bridge):x}"

        def sink(obj: dict) -> None:
            try:
                req = parse_ui_request(obj)
            except UiProtocolError as exc:
                self._metrics["dropped_unknown"] += 1
                logger.warning(
                    "[PiInteraction] malformed extension_ui_request dropped "
                    "(code=%s: %s)", exc.code, exc.detail[:160],
                )
                return
            if req.method in FIRE_FORGET_METHODS:
                self._handle_fire(rpc, bridge, req)
            else:
                self._handle_dialog(rpc, bridge, req, worker_key)

        return sink

    def _handle_fire(self, rpc, bridge, req: ParsedUiRequest) -> None:
        turn = getattr(bridge, "_current_turn", None)
        session_id = getattr(turn, "session_id", "") or ""
        projection = self._fire.setdefault(session_id or "_", _FireProjection())
        payload = projection.project(req)
        if payload is None:
            return
        event = {
            "type": "extension_ui_fire",
            "session_id": session_id,
            "turn_id": getattr(turn, "turn_id", "") or "",
            "_webgis_fire": payload,
        }
        self._enqueue(rpc, event)

    def _handle_dialog(self, rpc, bridge, req: ParsedUiRequest, worker_key: str) -> None:
        turn = getattr(bridge, "_current_turn", None)
        session_id = getattr(turn, "session_id", "") or ""
        turn_id = getattr(turn, "turn_id", "") or ""

        # review P3-6：先判上限再配对 —— 拒收路径不消费 awaiting 封套
        # （配对一次语义保持：拒收的请求没有宿主记录，扩展侧 ctx.ui 以
        # cancelled/超时收口；awaiting 条目由失效/过期修剪回收）。
        pending_ids = self._by_session.get(session_id, [])
        pending = [
            self._by_id[i]
            for i in pending_ids
            if self._by_id[i].state is InteractionState.PENDING
        ]
        if len(pending) >= MAX_PENDING_PER_SESSION:
            # 上限拒绝：立即合法取消（vendor 侧 promise 以 undefined 收口，
            # 不悬挂）；封套铸造侧另有 per-turn 上限，正常不会到这。
            self._metrics["cap_rejected"] += 1
            try:
                response = build_ui_response(req.native_id, cancelled=True)
                self._deferred_writes.append((rpc, response))
            except UiProtocolError:
                pass
            logger.warning(
                "[PiInteraction] session pending cap hit (%d); cancelling "
                "native request %s (method=%s)",
                MAX_PENDING_PER_SESSION, req.native_id, req.method,
            )
            return

        envelope = self._pop_awaiting_envelope(session_id, turn_id, req)
        now = time.monotonic()
        record = RuntimeInteraction(
            interaction_id=INTERACTION_ID_PREFIX + uuid.uuid4().hex[:12],
            native_id=req.native_id,
            method=req.method,
            session_id=session_id,
            turn_id=turn_id,
            worker_key=worker_key,
            options=list(req.options),
            title=req.title,
            message=req.message,
            placeholder=req.placeholder,
            prefill=req.prefill,
            native_timeout_ms=req.native_timeout_ms,
            source_tool=(envelope or {}).get("tool", ""),
            source_tool_call_id=(envelope or {}).get("tool_call_id", ""),
            envelope_id=(envelope or {}).get("envelope_id", ""),
            business=(envelope or {}).get("business", {}),
        )
        if req.native_timeout_ms is not None:
            record.deadline_monotonic = now + req.native_timeout_ms / 1000.0 + NATIVE_TIMEOUT_GRACE_S
        else:
            # 无原生 timeout（editor / 未带 timeout 的 dialog）：宿主策略期限。
            record.deadline_monotonic = now + PI_INTERACTION_MAX_WAIT_S
        if not session_id or not turn_id:
            # 无 turn 归属（协议异常 —— dialog 只会在工具执行中出现）：只登记
            # 展示，禁止作答消费（submit 会因 turn 校验失败拒绝）。
            self._metrics["orphan"] += 1
            logger.warning(
                "[PiInteraction] dialog request without active turn attribution "
                "(method=%s id=%s)", req.method, req.native_id,
            )
        record.rpc = rpc
        if envelope:
            # 封套等待已闭合：native 请求与封套同轮配对；全文挂到记录
            # （awaiting 队列弹出后，resolve 消费仍可读 option_values/exp）。
            envelope["native_id"] = req.native_id
            envelope["interaction_id"] = record.interaction_id
            record.envelope = envelope
        self._register(record)
        event = {
            "type": "extension_ui_request",
            "session_id": session_id,
            "turn_id": turn_id,
            "_webgis_interaction": record.public_payload(),
        }
        self._enqueue(rpc, event)

    # ── 封套对接（pi_interaction_envelope 调用） ──────────────────────

    def register_envelope_wait(self, session_id: str, turn_id: str, envelope: dict) -> None:
        """封套已随工具结果发回扩展，等待其 native 请求到达（同轮配对）。"""
        self._awaiting_envelopes.setdefault((session_id, turn_id), []).append(envelope)

    def _pop_awaiting_envelope(self, session_id: str, turn_id: str, req: ParsedUiRequest) -> Optional[dict]:
        queue = self._awaiting_envelopes.get((session_id, turn_id))
        if not queue:
            return None
        # 方法匹配的最早封套出队（同轮顺序排队；不同方法的封套不误配）。
        for index, env in enumerate(queue):
            if env.get("method") == req.method:
                return queue.pop(index)
        return None

    def _prune_expired_envelopes(self) -> int:
        """修剪过期未配对封套（review P2-1：防无限增长；返回修剪数）。"""
        now = time.time()
        pruned = 0
        for key in list(self._awaiting_envelopes.keys()):
            queue = self._awaiting_envelopes[key]
            kept = [e for e in queue if float(e.get("exp", 0) or 0) > now]
            pruned += len(queue) - len(kept)
            if kept:
                self._awaiting_envelopes[key] = kept
            else:
                self._awaiting_envelopes.pop(key, None)
        return pruned

    # ── 查询 ──────────────────────────────────────────────────────────

    def get(self, interaction_id: str) -> Optional[RuntimeInteraction]:
        return self._by_id.get(interaction_id)

    def session_view(self, session_id: str) -> dict[str, Any]:
        """GET 水合：pending + 近期终态 + fire 投影（有界）。"""
        ids = self._by_session.get(session_id, [])
        records = [self._by_id[i] for i in ids if i in self._by_id]
        pending = [r.public_payload() for r in records if not r.state.terminal]
        terminal = [r.public_payload() for r in records if r.state.terminal][-MAX_TERMINAL_HISTORY:]
        fire = self._fire.get(session_id)
        return {
            "boot_id": WORKER_BOOT_ID,
            "pending": pending,
            "recent": terminal,
            "fire": fire.snapshot() if fire else {},
            "metrics": dict(self._metrics),
        }

    def waiting_for_user(self, turn_id: str) -> bool:
        """WP07：该 turn 是否处于「等待用户（或答案已写出待续接）」状态。"""
        for record in self._by_id.values():
            if (
                record.turn_id == turn_id
                and record.method in DIALOG_METHODS
                and record.state.value in _DIALOG_WAITING_STATES
            ):
                return True
        return False

    # ── 回答 / 取消（WP04 入口调用；owner 已由路由校验） ─────────────

    async def submit_answer(
        self,
        session_id: str,
        interaction_id: str,
        answer: dict[str, Any],
        rpc_resolver,
    ) -> dict[str, Any]:
        """提交回答。answer: {value} | {confirmed} | {cancelled: true}。

        rpc_resolver(record) -> (rpc, valid)：按记录的 worker/turn 解析目标
        RPC 客户端并校验活性（由路由层注入，避免本模块 import 桥）。
        返回 {"state": ..., "interaction": payload, "result": "accepted"|
        "already_answered"|"duplicate"|"rejected", "reason": code?}。
        """
        record = self._by_id.get(interaction_id)
        if record is None or record.session_id != session_id:
            return {"result": "rejected", "reason": "NOT_FOUND", "state": None}
        async with record.lock:
            if record.state is not InteractionState.PENDING:
                # 幂等：重复提交/迟到提交返回当前态，不写第二次 stdin。
                # - already_answered：已有回答被接纳（answer_written）。
                # - duplicate：已终态（resolved/cancelled/timed_out/invalidated）。
                return {
                    "result": (
                        "already_answered"
                        if record.state is InteractionState.ANSWER_WRITTEN
                        else "duplicate"
                    ),
                    "state": record.state.value,
                    "interaction": record.public_payload(),
                }
            validated = self._validate_answer(record, answer)
            if validated is None:
                return {"result": "rejected", "reason": "ANSWER_INVALID",
                        "state": record.state.value}
            rpc, alive = rpc_resolver(record)
            if not alive or rpc is None:
                record.state = InteractionState.INVALIDATED
                record.resolve_evidence = "worker-gone-at-answer"
                return {"result": "rejected", "reason": "WORKER_GONE",
                        "state": record.state.value,
                        "interaction": record.public_payload()}
            try:
                await rpc.send_ui_response(validated)
            except Exception as exc:  # noqa: BLE001 — PiRpcError 等
                logger.warning(
                    "[PiInteraction] answer stdin write failed (%s): %s",
                    interaction_id, exc,
                )
                # 写失败：保持 pending（用户可重试）—— 不假定 vendor 收到。
                return {"result": "rejected", "reason": "WRITE_FAILED",
                        "state": record.state.value}
            record.answer_kind = (
                "confirmed" if "confirmed" in validated
                else "cancelled" if validated.get("cancelled")
                else "value"
            )
            record.answer_display = str(validated.get("value") or validated.get("confirmed") or "")[:200]
            record.state = InteractionState.ANSWER_WRITTEN
            record.answered_at = time.monotonic()
            self._prune_history(record.session_id)
            self._emit_update(record)
            return {
                "result": "accepted",
                "state": record.state.value,
                "interaction": record.public_payload(),
            }

    async def cancel_interaction(
        self, session_id: str, interaction_id: str, rpc_resolver, *, source: str = "user",
    ) -> dict[str, Any]:
        answer = {"cancelled": True}
        outcome = await self.submit_answer(session_id, interaction_id, answer, rpc_resolver)
        if outcome.get("result") == "accepted":
            record = self._by_id.get(interaction_id)
            if record is not None:
                record.state = InteractionState.CANCELLED
                record.resolve_evidence = f"cancelled:{source}"
                outcome["state"] = record.state.value
                outcome["interaction"] = record.public_payload()
                self._emit_update(record)
        return outcome

    def _validate_answer(self, record: RuntimeInteraction, answer: dict[str, Any]) -> Optional[dict[str, Any]]:
        """白名单/类型校验 → 构造 vendor 响应（协议层再校验互斥）。"""
        try:
            if answer.get("cancelled") is True:
                return build_ui_response(record.native_id, cancelled=True)
            if record.method == "select":
                value = answer.get("value")
                if not isinstance(value, str) or value not in record.options:
                    return None  # 选项白名单之外 —— 拒绝且不污染状态
                return build_ui_response(record.native_id, value=value)
            if record.method in ("input", "editor"):
                value = answer.get("value")
                if not isinstance(value, str):
                    return None
                if not value.strip():
                    # 空字符串与取消语义分开留痕（§5）：空输入按取消走显式语义。
                    return None
                if len(value) > 8000:
                    return None
                return build_ui_response(record.native_id, value=value)
            if record.method == "confirm":
                confirmed = answer.get("confirmed")
                if not isinstance(confirmed, bool):
                    return None
                return build_ui_response(record.native_id, confirmed=confirmed)
        except UiProtocolError:
            return None
        return None

    # ── 续接观察 / 失效 / 过期（bridge 与 resolve 回调调用） ──────────

    def observe_turn_activity(self, turn_id: str, evidence: str) -> None:
        """answer_written 的记录观察到当前任务继续 → resolved（一次）。"""
        for record in self._by_id.values():
            if (
                record.turn_id == turn_id
                and record.state is InteractionState.ANSWER_WRITTEN
            ):
                record.state = InteractionState.RESOLVED
                record.resolved_at = time.monotonic()
                record.resolve_evidence = evidence[:120]
                self._emit_update(record)

    def mark_resolved_by_callback(self, interaction_id: str, evidence: str) -> bool:
        record = self._by_id.get(interaction_id)
        if record is None:
            return False
        if record.state in (InteractionState.PENDING, InteractionState.ANSWER_WRITTEN):
            record.state = InteractionState.RESOLVED
            record.resolved_at = time.monotonic()
            record.resolve_evidence = evidence[:120]
            self._emit_update(record)
            return True
        return False

    def expire_due(self, session_id: str) -> list[dict[str, Any]]:
        """过期处理（bridge 心跳轮内 + GET 懒触发）。

        - native timeout 已过 + 宽限：vendor 已自行 resolve 默认值 —— 宿主只
          标 ``timed_out``，不再写 stdin（迟到写对 vendor 无害但状态不诚实）。
        - 宿主策略期限（无原生 timeout 的方法）：写 ``cancelled:true`` 合法
          结束（§WP07：宿主期限是宿主策略，经合法取消路径收口）。
        """
        expired: list[RuntimeInteraction] = []
        now = time.monotonic()
        self._prune_expired_envelopes()
        for i in list(self._by_session.get(session_id, [])):
            record = self._by_id.get(i)
            if record is None or record.state.terminal or record.deadline_monotonic is None:
                continue
            if record.lock.locked():
                # review P3-5：submit_answer 在飞（持锁）—— 不得无锁翻转其
                # 状态；下一心跳再处理。
                continue
            if now < record.deadline_monotonic:
                continue
            if record.state is InteractionState.PENDING:
                record.state = InteractionState.TIMED_OUT
                record.resolve_evidence = (
                    "native-timeout-default" if record.native_timeout_ms is not None
                    else "host-policy-deadline"
                )
                expired.append(record)
            elif record.state is InteractionState.ANSWER_WRITTEN:
                # 已作答但迟迟未观察到续接 —— 保留 answer_written 事实，只标注
                # 观察窗到期（不覆盖 answered 语义）。
                record.resolve_evidence = "observe-window-expired"
        for record in expired:
            self._emit_update(record)
            if record.native_timeout_ms is None:
                # 宿主策略期限：经合法取消收口（vendor promise 以 undefined 结束）。
                if record.rpc is not None:
                    try:
                        response = build_ui_response(record.native_id, cancelled=True)
                        self._deferred_writes.append((record.rpc, response))
                    except UiProtocolError:
                        pass
        return [r.public_payload() for r in expired if r.state is InteractionState.TIMED_OUT]

    def invalidate_turn(self, session_id: str, turn_id: str, reason: str) -> int:
        """turn 注销 / abort → 该 turn 的 pending/answer_written 全部失效。"""
        self._awaiting_envelopes.pop((session_id, turn_id), None)
        count = 0
        for i in list(self._by_session.get(session_id, [])):
            record = self._by_id.get(i)
            if record is None or record.turn_id != turn_id:
                continue
            if record.state in (InteractionState.PENDING, InteractionState.ANSWER_WRITTEN):
                record.state = InteractionState.INVALIDATED
                record.resolve_evidence = reason[:120]
                count += 1
                self._emit_update(record)
        return count

    # ── 内部 ──────────────────────────────────────────────────────────

    def _emit_update(self, record: RuntimeInteraction) -> None:
        """状态翻转 → 投递 SSE 更新合成事件（当前 turn 的流会翻译发射）。"""
        rpc = record.rpc
        if rpc is None:
            return
        self._enqueue(rpc, {
            "type": "extension_ui_update",
            "session_id": record.session_id,
            "turn_id": record.turn_id,
            "_webgis_interaction": record.public_payload(),
        })

    def _register(self, record: RuntimeInteraction) -> None:
        self._by_id[record.interaction_id] = record
        bucket = self._by_session.setdefault(record.session_id, [])
        bucket.append(record.interaction_id)
        self._prune_history(record.session_id)

    def _prune_history(self, session_id: str) -> None:
        ids = self._by_session.get(session_id)
        if not ids:
            return
        kept: list[str] = []
        terminal: list[str] = []
        for i in ids:
            record = self._by_id.get(i)
            if record is None:
                continue
            if record.state.terminal:
                terminal.append(i)
            else:
                kept.append(i)
        drop = terminal[:-MAX_TERMINAL_HISTORY] if len(terminal) > MAX_TERMINAL_HISTORY else []
        terminal = terminal[-MAX_TERMINAL_HISTORY:]
        for i in drop:
            self._by_id.pop(i, None)
        self._by_session[session_id] = kept + terminal

    def _enqueue(self, rpc, event: dict) -> None:
        """投递合成事件到事件队列（map_event_to_sse 翻译为 SSE）。

        队列满：非终态投影可丢（fire/卡片与 turn 终态相比可牺牲），不触发
        队列的终态保位逻辑（那是 turn 结算事件的专用压力阀）。
        """
        try:
            rpc.events.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "[PiInteraction] event queue full; dropping projection %s",
                event.get("type"),
            )

    async def _safe_send(self, rpc, response: dict, *, reason: str) -> None:
        try:
            await rpc.send_ui_response(response)
        except Exception:  # noqa: BLE001 — 尽力而为
            logger.debug("[PiInteraction] safe_send(%s) failed", reason, exc_info=True)

    async def flush_deferred_writes(self) -> int:
        """冲刷 sink 排队的单向写出（bridge 心跳调用；返回写出条数）。"""
        if not self._deferred_writes:
            return 0
        items, self._deferred_writes = self._deferred_writes[:], []
        for rpc, response in items:
            await self._safe_send(rpc, response, reason="deferred")
        return len(items)


#: 进程内单例（bridge pool 初始化时接线；测试可换装）。
_service: Optional[RuntimeInteractionService] = None


def get_interaction_service() -> RuntimeInteractionService:
    global _service
    if _service is None:
        _service = RuntimeInteractionService()
    return _service


def reset_interaction_service() -> RuntimeInteractionService:
    """测试钩子：换装全新服务。"""
    global _service
    _service = RuntimeInteractionService()
    return _service
