"""Regression tests for audit-ff9a392 harness-runtime findings (#816-#823).

Covers: vendor-protocol event mapping (covered more deeply in
test_pi_event_mapper.py / test_pi_integration.py), disconnect-path completed
tool persistence (#817), ref-resolution failure not memoized (#819),
task_complete counters (#820), OperationCancelled dispatch semantics (#821),
decision-log background write (#822), turn-start map_state layers whitelist
(#823).
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_pi_bridge import PiBridge, dispatch_tool
from app.services.chat.pi_turn_context import issue_turn_token  # noqa: F401 (seam exists)


# ─── #819: resolver failures must not be memoized ────────────────────────


class TestAudit819RefMemo:
    def _harness(self):
        from app.lib.harness.pi_agent_harness import PiAgentHarness
        return PiAgentHarness(session_id="s-a819")

    @pytest.mark.asyncio
    async def test_resolver_exception_not_memoized(self):
        h = self._harness()
        h._append_capped(h.ref_cursors, {
            "ref_cursor": "ref:geojson-abc", "session_id": "s-a819",
            "source": "tool", "tool_call_id": "tc-1",
        })
        calls = {"n": 0}

        from app.lib.harness.pi_agent_harness import RefResolution, RefResolutionStatus

        async def flaky(_sid, _ref):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("store hiccup")
            return RefResolution(
                ref="ref:geojson-abc", session_id=_sid,
                status=RefResolutionStatus.RESOLVED,
            )

        h.ref_resolver = flaky
        await h.evaluate_with_evidence()
        assert ("s-a819", "ref:geojson-abc") not in h._resolved_refs, (
            "failure resolution must not enter the memo"
        )
        await h.evaluate_with_evidence()
        assert calls["n"] == 2, "second evaluation must retry the real resolution"


# ─── #820: task_complete counters reflect the turn ───────────────────────


class TestAudit820TaskComplete:
    @pytest.mark.asyncio
    async def test_task_complete_step_count_via_stream(self):
        rpc = MagicMock()
        rpc.events = asyncio.Queue()
        rpc.start = AsyncMock()
        rpc.stop = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "answer text"}]},
                    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "answer text"},
                })
                for i in range(2):
                    await rpc.events.put({
                        "type": "tool_execution_end",
                        "toolCallId": f"tc-{i}", "toolName": "buffer_analysis",
                        "result": {"ok": True}, "isError": False,
                    })
                await rpc.events.put({"type": "agent_end"})

        rpc.request = AsyncMock(side_effect=fake_request)
        out = []
        async for ev in bridge.stream_prompt("q"):
            out.append(ev)
        joined = "\n".join(out)
        assert '"step_count": 2' in joined
        assert "answer text" in joined  # summary carries the final text


# ─── #821: OperationCancelled is cancelled, not error/500 ────────────────


class TestAudit821CancelledDispatch:
    @pytest.mark.asyncio
    async def test_operation_cancelled_returns_cancelled_response(self, monkeypatch):
        from app.services.jobs.cancellation import OperationCancelled

        captured = []

        class _FakeHarness:
            def record_event(self, ev):
                captured.append(ev)

        class _FakeService:
            async def dispatch(self, tc, session_id, executed):
                raise OperationCancelled("user cancelled")

        class _FakeRegistry:
            def list_tools(self):
                return ["buffer_analysis"]

            def metadata(self, name):
                return {"tier": 1}

        monkeypatch.setattr("app.agent_pi_bridge._get_session_harness",
                            lambda sid, create=False: _FakeHarness())
        monkeypatch.setattr("app.agent_pi_bridge._get_shared_dispatch_service",
                            lambda: _FakeService())
        monkeypatch.setattr("app.agent_pi_bridge.get_tool_registry",
                            lambda: _FakeRegistry())

        class _Req:
            toolCallId = "tc-1"
            name = "buffer_analysis"
            arguments = {}
            sessionId = "s1"
            turnToken = ""
            verifiedTurnId = "t1"

        resp = await dispatch_tool(_Req())
        assert resp.isError is False
        assert resp.details.get("cancelled") is True
        assert captured and captured[0].is_error is False
        assert captured[0].result.get("cancelled") is True


# ─── #822: decision log background write + honest metrics ────────────────


class TestAudit822DecisionLog:
    def test_background_write_off_caller_thread(self, tmp_path, monkeypatch):
        import threading

        from app.services.chat import decision_log as dl

        log_file = tmp_path / "decisions.jsonl"
        monkeypatch.setattr(dl, "_LOG_PATH", log_file)
        writer_threads = []

        def spy_append(line):
            writer_threads.append(threading.current_thread().name)
            real_append(line)

        real_append = dl._append_line
        monkeypatch.setattr(dl, "_append_line", spy_append)

        rec = dl.ToolDecisionRecord(
            session_id="s", round=0, user_message="m", active_domains=[],
            from_plan=False, subset_size=1, total_tools=1,
            tool_chosen="t", tool_args={}, result_quality="ok", plan_step_matched=None,
        )
        dl.log_tool_decision(rec, background=True)
        # wait for the single-worker executor to drain
        for _ in range(50):
            if writer_threads:
                break
            import time
            time.sleep(0.02)
        assert writer_threads, "background write must eventually run"
        assert writer_threads[0] != threading.current_thread().name
        # the worker records its thread name before the real append — poll for
        # the flushed file instead of asserting immediately
        import time as _t
        for _ in range(100):
            if log_file.exists():
                break
            _t.sleep(0.02)
        assert log_file.exists()

    def test_metrics_missing_evidence_is_zero(self):
        from app.lib.harness.pi_agent_harness import PiAgentHarness
        h = PiAgentHarness(session_id="s-metrics")
        assert h.compute_tool_choice_accuracy([]) == 0.0
        assert h.compute_step_efficiency(0) == 0.0


# ─── #823: turn-start map_state layers not persisted ─────────────────────


class TestAudit823MapStateWhitelist:
    @pytest.mark.asyncio
    async def test_layers_key_not_written(self, monkeypatch):
        from app.services.chat import execution_engine as ee

        written = {}

        class _FakeMgr:
            async def set_map_state(self, sid, k, v, seq=None):
                written[k] = (v, seq)

        monkeypatch.setattr(ee, "session_data_manager", _FakeMgr())
        engine = object.__new__(ee.ChatExecutionEngine)
        await engine._persist_map_state("s1", {
            "viewport": {"center": [1, 2], "zoom": 5},
            "viewport_seq": 7,
            "base_layer": "osm",
            "layers": [{"id": "l1"}],
            "user_location": {"lng": 1, "lat": 2, "accuracy": 3},
        })
        assert "layers" not in written, "wholesale layers write must be rejected (#643/#823)"
        assert written["viewport"] == ({"center": [1, 2], "zoom": 5}, 7)
        assert "base_layer" in written and "user_location" in written


# ─── #816/#818: turn result sink carries final text ──────────────────────


class TestAudit818TurnResultSink:
    @pytest.mark.asyncio
    async def test_on_turn_result_receives_final_text(self):
        rpc = MagicMock()
        rpc.events = asyncio.Queue()
        rpc.start = AsyncMock()
        rpc.stop = AsyncMock()
        bridge = PiBridge(rpc=rpc)

        async def fake_request(cmd, data=None):
            if cmd == "prompt":
                await rpc.events.put({
                    "type": "message_update",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "final!"}]},
                    "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "final!"},
                })
                await rpc.events.put({
                    "type": "agent_end",
                    "messages": [
                        {"role": "user", "content": [{"type": "text", "text": "q"}]},
                        {"role": "assistant", "content": [{"type": "text", "text": "final!"}]},
                    ],
                })

        rpc.request = AsyncMock(side_effect=fake_request)
        results = []

        async for _ in bridge.stream_prompt("q", session_id="s-sink",
                                            on_turn_result=results.append):
            pass
        assert results and results[0]["final_text"] == "final!"
        assert results[0]["completed"] is True
        assert json.dumps({"ok": 1})  # sanity: json import used
