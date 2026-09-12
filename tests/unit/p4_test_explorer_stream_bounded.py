"""Explorer — stream_progress 有界兜底（P4 补强 E2：#593 不静默挂住）。"""
from __future__ import annotations


import pytest

from app.services.explorer import orchestrator as OC


class _StubOrchestrator(OC.ExplorerOrchestrator):
    def __init__(self, status: dict):
        super().__init__()
        self._status = status

    async def get_task_status(self, task_id: str) -> dict:  # noqa: D102
        return self._status


@pytest.mark.asyncio
async def test_stream_terminates_on_terminal_state() -> None:
    orch = _StubOrchestrator({"status": "SUCCESS", "stage": "validate"})
    events = []
    async for ev in orch.stream_progress("task-x"):
        events.append(ev)
    assert events, "终态必须发出显式事件后关闭"
    assert '"SUCCESS"' in events[-1] or "SUCCESS" in events[-1]


@pytest.mark.asyncio
async def test_stream_deadline_emits_explicit_failed(monkeypatch) -> None:
    # 链停滞在 PENDING：到 deadline 必须显式 failed 并关闭（#593）。
    orch = _StubOrchestrator({"status": "PENDING", "stage": "pending"})
    monkeypatch.setattr(OC, "_EXPLORER_STREAM_MAX_SECONDS", 0.05)
    events = []
    async for ev in orch.stream_progress("task-stuck"):
        events.append(ev)
    assert events
    assert "failed" in events[-1]
    assert "stream timeout" in events[-1]


@pytest.mark.asyncio
async def test_stream_failure_names_stage() -> None:
    orch = _StubOrchestrator({"status": "FAILURE", "stage": "geocode"})
    events = []
    async for ev in orch.stream_progress("task-f"):
        events.append(ev)
    assert events
    assert "geocode" in events[-1]
