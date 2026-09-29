"""sse_contracts：chat 域事件词表与 TurnEventEmitter 契约测试（H03）。

钉死两件事：
1. emitter 各事件的载荷形状与 chat_stream 既有 wire 逐字段兼容
   （session_id 盖章、task_id 位点、error_class/turn_id additive）；
2. 词表是单一事实源（TERMINAL_CHAT_EVENTS ≡ app.utils.sse 公开词表）。
"""
from __future__ import annotations

import json

import pytest

from app.services.chat import sse_contracts
from app.services.chat.error_taxonomy import TurnErrorClass
from app.services.chat.sse_contracts import (
    CHAT_EVENT_TYPES,
    TERMINAL_CHAT_EVENTS,
    TurnEventEmitter,
)
from app.utils.sse import (
    TERMINAL_EVENTS,
    sse_event_id,
    sse_event_type,
)


def _data(frame: str) -> dict:
    for line in frame.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError(f"no data line in frame: {frame!r}")


def _etype(frame: str) -> str:
    return sse_event_type(frame)


@pytest.fixture()
def em() -> TurnEventEmitter:
    return TurnEventEmitter(session_id="s-1", turn_id="t-1")


class TestVocabulary:
    def test_terminal_is_single_source(self):
        assert TERMINAL_CHAT_EVENTS == TERMINAL_EVENTS

    def test_vocabulary_covers_all_emitters(self):
        # 词表必须覆盖 emitter 的每个事件方法（方法名 ≡ 事件名）；
        # session_id/turn_id 是绑定字段不是事件。
        non_event = {"session_id", "turn_id"}
        methods = {
            name
            for name in dir(TurnEventEmitter)
            if not name.startswith("_") and name not in non_event
        }
        # 方法名即事件名（task_start/token/.../plan_finalized）
        unknown = methods - CHAT_EVENT_TYPES
        assert not unknown, f"emitter 方法不在词表内: {unknown}"

    def test_core_names_present(self):
        for name in (
            "session", "task_start", "token", "content", "tool_call",
            "step_start", "step_result", "step_error", "step_cancelled",
            "tool_result", "plan_ready", "plan_step_done", "plan_finalized",
            "task_complete", "task_cancelled", "task_error", "error", "done",
        ):
            assert name in CHAT_EVENT_TYPES


class TestEmitterPayloadShapes:
    """载荷与既有 chat_stream wire 的逐字段兼容（新增字段仅 turn_id）。"""

    def test_task_start_with_owner_token(self, em):
        frame = em.task_start("task-1", owner_token="ot-1")
        assert _etype(frame) == "task_start"
        d = _data(frame)
        assert d == {
            "session_id": "s-1",
            "turn_id": "t-1",
            "task_id": "task-1",
            "agent_runtime": "chatengine",
            "owner_token": "ot-1",
        }

    def test_token(self, em):
        d = _data(em.token("你", is_reasoning=True))
        assert d == {
            "session_id": "s-1", "turn_id": "t-1",
            "content": "你", "is_reasoning": True,
        }

    def test_content_streaming_done(self, em):
        d = _data(em.content("", streaming_done=True))
        assert d["streaming_done"] is True
        d2 = _data(em.content("\n"))
        assert "streaming_done" not in d2

    def test_step_error_full_fields(self, em):
        frame = em.step_error(
            "task-1", "step-1", "buffer", "boom",
            failure_class="validation", recovery_action="fix_arguments",
            error_class=TurnErrorClass.tool_error.value,
        )
        d = _data(frame)
        assert d["failure_class"] == "validation"
        assert d["recovery_action"] == "fix_arguments"
        assert d["error_class"] == "tool_error"
        assert d["task_id"] == "task-1" and d["step_id"] == "step-1"

    def test_step_error_omits_absent_optionals(self, em):
        d = _data(em.step_error("task-1", "step-1", "t", "e"))
        for absent in ("failure_class", "recovery_action", "error_class"):
            assert absent not in d

    def test_step_result_optionals(self, em):
        d = _data(em.step_result("task-1", "s", "t", {"ok": True}))
        assert d["result"] == {"ok": True}
        assert "geojson_ref" not in d
        d2 = _data(em.step_result(
            "task-1", "s", "t", {}, geojson_ref="ref:x",
            ref_descriptor={"a": 1}, background_job_ids=["j1", "j2"],
        ))
        assert d2["geojson_ref"] == "ref:x"
        assert d2["ref_descriptor"] == {"a": 1}
        assert d2["background_job_ids"] == ["j1", "j2"]

    def test_done_and_keep_alive_shapes(self, em):
        assert _data(em.done()) == {"session_id": "s-1", "turn_id": "t-1"}
        # keep_alive 保持极简 ping 形状（无 turn_id —— 与旧 wire 等价）
        assert _data(em.keep_alive()) == {"message": "ping"}

    def test_plan_family(self, em):
        assert _data(em.plan_ready("task-1", "i", ["d"], [{"n": 1}])) == {
            "session_id": "s-1", "turn_id": "t-1", "task_id": "task-1",
            "intent": "i", "domains": ["d"], "steps": [{"n": 1}],
        }
        assert _data(em.plan_step_done("task-1", 2))["step_n"] == 2
        assert _data(em.plan_finalized("task-1", [3]))["skipped"] == [3]

    def test_turn_id_omitted_when_not_bound(self):
        em = TurnEventEmitter(session_id="s-1")
        assert "turn_id" not in _data(em.done())

    def test_requires_session_id(self):
        with pytest.raises(ValueError):
            TurnEventEmitter(session_id="")

    def test_task_cancelled_shape(self, em):
        assert _data(em.task_cancelled("task-1")) == {
            "session_id": "s-1", "turn_id": "t-1", "task_id": "task-1",
        }


class TestEmitterInScopeIds:
    def test_ids_monotonic_in_scope(self, em):
        from app.utils.sse import sse_event_id_scope

        frames = [em.token("a"), em.content("b"), em.done()]
        with sse_event_id_scope():
            frames = [em.token("a"), em.content("b"), em.done()]
        ids = [sse_event_id(f) for f in frames]
        assert ids == [1, 2, 3]

    def test_no_id_outside_scope(self, em):
        assert sse_event_id(em.done()) is None

    def test_scope_reset_after_exit(self, em):
        from app.utils.sse import sse_event_id_scope

        with sse_event_id_scope():
            pass
        assert sse_event_id(em.done()) is None


def test_contracts_module_exports_public_names():
    assert callable(sse_contracts.TurnEventEmitter)
    assert isinstance(CHAT_EVENT_TYPES, frozenset)
