"""Chat 域 SSE 事件契约：词表、typed emitter、序列不变量（H03）。

本模块是 legacy chat_stream 事件载荷的**单一事实源**：

- ``CHAT_EVENT_TYPES`` —— 后端真实发出的 chat 域事件词表（文档化枚举，
  测试守卫其与实际发射一致）；
- ``TERMINAL_CHAT_EVENTS`` —— 终态事件词表，从 :mod:`app.utils.sse`
  再导出（不复制，单一来源）；
- :class:`TurnEventEmitter` —— 绑定会话/turn 身份的 typed 事件出口。
  chat_stream 的全部事件经此构造；每个方法是该事件载荷形状的定义点。

事件序列不变量（由 tests/unit/test_sse_contracts.py 与
tests/unit/test_sse_envelope_property.py 钉死）：

INV-1  ``id:`` 行在 :func:`app.utils.sse.sse_event_id_scope` 内按发射序
       严格单调 +1；scope 外无 id 行（keepalive 注释帧不占 id）。
INV-2  一个 turn 恰好一个终态事件（``done``/``task_complete``/
       ``task_error``/``task_cancelled``），且为最后一帧。
INV-3  ``plan_finalized``（若存在）先于终态事件。
INV-4  ``task_start`` 先于一切业务事件（``session`` 事件先于
       ``task_start``）。
INV-5  终态事件不经批处理延迟（SSEBatcher terminal 旁路）；结构事件前
       缓冲 token 先 flush。
INV-6  同会话 ``mutation_revision`` 单调不回退（权威在
       ``map_state._cartographic_mutation_revision``，事件仅携带）。
INV-7  错误事件（``task_error``/``step_error``/``error``）携带
       ``error_class``（见 :mod:`app.services.chat.error_taxonomy`）。

wire 兼容纪律：emitter 方法产出的载荷与既有逐字段兼容；新增字段只有
``turn_id``（发射侧关联键，前端忽略未知字段）。Pi 路径经
``pi_event_mapper`` 产出同名事件（session/task_id 语义差异见该模块）。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from app.utils.sse import (
    TERMINAL_EVENTS,
    sse_event,
)

# Chat 域事件词表。ghost 名（前端历史兼容名 message/thinking/...）不在
# 后端词表内 —— 这里只登记后端真实发射的事件。
CHAT_EVENT_TYPES: frozenset[str] = frozenset({
    "session",        # 匿名会话绑定（owner_token 下发，route 层）
    "task_start",
    "token",
    "content",
    "keep_alive",
    "tool_call",
    "step_start",
    "step_result",
    "step_error",
    "step_cancelled",
    "tool_result",
    "plan_ready",
    "plan_step_done",
    "plan_finalized",
    "task_complete",
    "task_cancelled",
    "task_error",
    "error",          # route 层 catch-all 合成
    "done",
    "resume_gap",     # resume replay 截断标记（event_resume）
    "ui_action",      # mount_widget 挂件（canvas_affordance）
})

# 终态词表：单一来源是 app.utils.sse（SSEBatcher 的 immediate-flush 判定
# 与 resume 的 ended 判定共用它），此处再导出供 chat 域消费/测试。
TERMINAL_CHAT_EVENTS: frozenset[str] = frozenset(TERMINAL_EVENTS)


# 「显式携带 null」与「键不存在」的区分哨兵：geojson_ref 在主成功分支
# 恒在场（可为 None，前端/重放消费端依赖键的稳定性），而 repeated 分支
# 历史上没有该键。默认 _OMIT = 不携带；显式传 None = 携带 null。
_OMIT = object()


class TurnEventEmitter:
    """一次 legacy turn 的 typed SSE 事件出口。

    绑定 ``session_id``（必填，逐事件盖章）与可选 ``turn_id``/``task_id``
    （additive 关联字段）。所有方法经 :func:`app.utils.sse.sse_event`
    序列化 —— 本类不碰 wire 格式，只定义载荷形状。

    用法：``TurnEventEmitter(session_id=..., turn_id=...)`` 每 turn 构造
    一次；``emitter.token("hello")`` 返回即序列化的 SSE 帧。
    """

    __slots__ = ("session_id", "turn_id")

    def __init__(
        self,
        session_id: str,
        turn_id: Optional[str] = None,
    ) -> None:
        if not session_id:
            raise ValueError("TurnEventEmitter requires a session_id")
        self.session_id = session_id
        self.turn_id = turn_id

    def _payload(self, task_id: Optional[str] = None, **fields: Any) -> dict:
        data: dict = {"session_id": self.session_id}
        if self.turn_id:
            data["turn_id"] = self.turn_id
        if task_id is not None:
            data["task_id"] = task_id
        data.update(fields)
        return data

    # ── 生命周期 ────────────────────────────────────────────────────

    def task_start(self, task_id: str, owner_token: Optional[str] = None,
                   agent_runtime: str = "chatengine") -> str:
        payload = self._payload(task_id=task_id, agent_runtime=agent_runtime)
        if owner_token:
            payload["owner_token"] = owner_token
        return sse_event("task_start", payload)

    def task_complete(self, task_id: str, step_count: int, summary: str) -> str:
        return sse_event("task_complete", self._payload(
            task_id=task_id, step_count=step_count, summary=summary,
        ))

    def task_error(self, task_id: str, error: str, error_class: Optional[str] = None) -> str:
        payload = self._payload(task_id=task_id, error=error)
        if error_class:
            payload["error_class"] = error_class
        return sse_event("task_error", payload)

    def task_cancelled(self, task_id: str) -> str:
        return sse_event("task_cancelled", self._payload(task_id=task_id))

    def done(self) -> str:
        return sse_event("done", self._payload())

    def keep_alive(self, message: str = "ping") -> str:
        return sse_event("keep_alive", {"message": message})

    # ── 内容流 ──────────────────────────────────────────────────────

    def token(self, content: str, is_reasoning: bool = False) -> str:
        return sse_event("token", self._payload(
            content=content, is_reasoning=is_reasoning,
        ))

    def content(self, text: str, streaming_done: Optional[bool] = None) -> str:
        payload = self._payload(content=text)
        if streaming_done is not None:
            payload["streaming_done"] = streaming_done
        return sse_event("content", payload)

    # ── 工具步 ──────────────────────────────────────────────────────

    def step_start(self, task_id: str, step_id: str, step_index: int, tool: str) -> str:
        return sse_event("step_start", self._payload(
            task_id=task_id, step_id=step_id, step_index=step_index, tool=tool,
        ))

    def tool_call(self, name: str, arguments: Any) -> str:
        return sse_event("tool_call", self._payload(name=name, arguments=arguments))

    def step_result(
        self,
        task_id: str,
        step_id: str,
        tool: str,
        result: Any,
        *,
        geojson_ref: Any = _OMIT,
        ref_descriptor: Optional[dict] = None,
        background_job_ids: Optional[Iterable[str]] = None,
    ) -> str:
        payload = self._payload(task_id=task_id, step_id=step_id, tool=tool, result=result)
        if geojson_ref is not _OMIT:
            # 显式传参即携带（含 None）—— 与旧 wire 的键稳定性一致
            payload["geojson_ref"] = geojson_ref
        if ref_descriptor:
            payload["ref_descriptor"] = ref_descriptor
        if background_job_ids:
            payload["background_job_ids"] = list(background_job_ids)
        return sse_event("step_result", payload)

    def step_error(
        self,
        task_id: str,
        step_id: str,
        tool: str,
        error: Any,
        *,
        failure_class: Optional[str] = None,
        recovery_action: Optional[str] = None,
        error_class: Optional[str] = None,
    ) -> str:
        payload = self._payload(task_id=task_id, step_id=step_id, tool=tool, error=error)
        if failure_class:
            payload["failure_class"] = failure_class
        if recovery_action:
            payload["recovery_action"] = recovery_action
        if error_class:
            payload["error_class"] = error_class
        return sse_event("step_error", payload)

    def step_cancelled(self, task_id: str, step_id: str, tool: str) -> str:
        return sse_event("step_cancelled", self._payload(
            task_id=task_id, step_id=step_id, tool=tool,
        ))

    def tool_result(self, name: str, result: Any) -> str:
        return sse_event("tool_result", self._payload(name=name, result=result))

    # ── 计划 ────────────────────────────────────────────────────────

    def plan_ready(self, task_id: str, intent: str, domains: list,
                   steps: list[dict]) -> str:
        return sse_event("plan_ready", self._payload(
            task_id=task_id, intent=intent, domains=domains, steps=steps,
        ))

    def plan_step_done(self, task_id: str, step_n: int) -> str:
        return sse_event("plan_step_done", self._payload(task_id=task_id, step_n=step_n))

    def plan_finalized(self, task_id: str, skipped: list[int]) -> str:
        return sse_event("plan_finalized", self._payload(task_id=task_id, skipped=skipped))
