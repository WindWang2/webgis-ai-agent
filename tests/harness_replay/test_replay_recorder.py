"""ReplayRecorder 契约测试（B2）：env-gated、never-raises、有界保留。"""
from __future__ import annotations

import json

import pytest

from app.lib.harness.replay import recorder as recorder_module
from app.lib.harness.replay.recorder import (
    MAX_TRACES_PER_SESSION,
    collect_turn,
    maybe_record_turn,
    write_trace,
)
from app.lib.runtime.evidence import Outcome, TurnEvidence
from app.lib.runtime.gis_trace import GisTraceChain, Stage, get_gis_trace_registry

pytestmark = pytest.mark.cartography


@pytest.fixture()
def record_env(monkeypatch, tmp_path):
    """开闸 + 隔离输出目录。"""
    monkeypatch.setenv("HARNESS_REPLAY_RECORD", "1")
    monkeypatch.setenv("HARNESS_REPLAY_DIR", str(tmp_path / "replays"))
    return tmp_path / "replays"


def _register_chain(turn_id: str, session_id: str) -> GisTraceChain:
    chain = GisTraceChain(turn_id=turn_id, session_id=session_id)
    chain.record(Stage.USER_INTENT, prompt="replay recorder probe")
    chain.record(Stage.FINAL_VERDICT, status="complete")
    get_gis_trace_registry()._chains[turn_id] = chain
    get_gis_trace_registry()._pinned[turn_id] = chain
    return chain


class TestMaybeRecordTurn:
    def test_disabled_is_noop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HARNESS_REPLAY_RECORD", "0")
        monkeypatch.setenv("HARNESS_REPLAY_DIR", str(tmp_path / "x"))
        assert maybe_record_turn(session_id="s1", turn_id="t1") is None
        assert not (tmp_path / "x").exists()

    def test_enabled_writes_valid_trace(self, record_env):
        _register_chain("turn-rec01", "sess-rec")
        ev = TurnEvidence(request_id="r", session_id="sess-rec",
                          turn_id="turn-rec01", run_id="run")
        ev.settle(Outcome.SUCCEEDED)
        from app.lib.runtime.evidence import TURN_EVIDENCE

        TURN_EVIDENCE.register(ev)
        try:
            path = maybe_record_turn(
                session_id="sess-rec", turn_id="turn-rec01",
                final_text="done",
                map_product={"status": "complete", "task_complete": True},
            )
        finally:
            TURN_EVIDENCE.remove("turn-rec01")
            get_gis_trace_registry().drop("turn-rec01")
        assert path is not None
        data = json.loads(open(path, encoding="utf-8").read())
        assert data["schema_version"] == 1
        assert data["session_id"] == "sess-rec"
        assert data["verdict"]["map_product"]["task_complete"] is True
        assert data["outcome"]["outcome"] == "succeeded"
        assert data["behavior_digest"]

    def test_never_raises_on_collector_failure(self, record_env, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("injected")

        monkeypatch.setattr(recorder_module, "collect_turn", _boom)
        assert maybe_record_turn(session_id="s", turn_id="t") is None

    def test_degraded_collect_still_records(self, record_env):
        """chain/evidence 缺席 → 降级录制（degraded=true），绝不抛。"""
        data = collect_turn(session_id="sess-none", turn_id="turn-none")
        assert data is not None
        assert data["recording"]["degraded"] is True

    def test_invalid_session_id_rejected(self, record_env):
        data = collect_turn(session_id="bad/id!", turn_id="t")
        assert write_trace(data, session_id="bad/id!", turn_id="t",
                           root=record_env) is None

    def test_bounded_retention(self, record_env):
        for i in range(MAX_TRACES_PER_SESSION + 4):
            data = collect_turn(session_id="sess-cap", turn_id=f"turn-cap{i:04d}")
            write_trace(data, session_id="sess-cap", turn_id=f"turn-cap{i:04d}",
                        root=record_env)
        files = list((record_env / "sess-cap").glob("*.json"))
        assert len(files) == MAX_TRACES_PER_SESSION
        # 最旧的被淘汰（FIFO by mtime）。
        names = {p.name for p in files}
        assert "turn-cap0000.json" not in names
        assert "turn-cap0003.json" not in names
