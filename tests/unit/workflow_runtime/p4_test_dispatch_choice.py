"""Workflow V6 — dispatch 派发决策与并发槽位（P4 补强：W6）。

choose_dispatch 纯读面：显式 mode 优先 / auto 重 profile 才上 durable /
隔离开启的超阈值强制 + 无合格 worker 的 typed 拒绝。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.workflow_runtime import dispatch as DP


class _FakeRegistry:
    def __init__(self, active: bool):
        self._active = active

    def list_active(self, *, profile: str = "", backend: str = "") -> list:
        return [object()] if self._active else []


@pytest.fixture(autouse=True)
def _local_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "local")
    monkeypatch.setenv("GIS_WORKFLOW_ISOLATE_LARGE_TASKS", "0")


def _heavy_node() -> dict:
    return {"node_id": "n:heavy", "resources": {"profile": "raster"}}


def test_explicit_local_wins_over_heavy_profile(monkeypatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "local")
    assert DP.choose_dispatch(_heavy_node(), input_rows=1,
                              registry=_FakeRegistry(True)) == "local"


def test_explicit_durable_requires_capable_worker(monkeypatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "durable")
    with pytest.raises(DP.NoCapableWorker):
        DP.choose_dispatch(_heavy_node(), input_rows=1,
                           registry=_FakeRegistry(False))
    assert DP.choose_dispatch(_heavy_node(), input_rows=1,
                              registry=_FakeRegistry(True)) == "durable"


def test_auto_sends_heavy_to_durable_only_with_workers(monkeypatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "auto")
    assert DP.choose_dispatch(_heavy_node(), input_rows=1,
                              registry=_FakeRegistry(True)) == "durable"
    assert DP.choose_dispatch(_heavy_node(), input_rows=1,
                              registry=_FakeRegistry(False)) == "local"


def test_auto_keeps_light_profile_local(monkeypatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "auto")
    node = {"node_id": "n:light", "resources": {"profile": "light_cpu"}}
    assert DP.choose_dispatch(node, input_rows=1,
                              registry=_FakeRegistry(True)) == "local"


def test_isolation_forces_durable_over_threshold(monkeypatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_ISOLATE_LARGE_TASKS", "1")
    node = {"node_id": "n:light"}
    with pytest.raises(DP.NoCapableWorker):
        DP.choose_dispatch(node, input_rows=DP.DEFAULT_ISOLATION_ROW_THRESHOLD + 1,
                           registry=_FakeRegistry(False))
    assert DP.choose_dispatch(node, input_rows=DP.DEFAULT_ISOLATION_ROW_THRESHOLD + 1,
                              registry=_FakeRegistry(True)) == "durable"
    # 阈值内不隔离：local。
    assert DP.choose_dispatch(node, input_rows=1,
                              registry=_FakeRegistry(False)) == "local"


def test_dispatch_mode_env_vocabulary(monkeypatch) -> None:
    for raw, expected in [("local", "local"), ("durable", "durable"),
                          ("auto", "auto"), ("junk", "local")]:
        monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", raw)
        assert DP.dispatch_mode() == expected


def test_dispatch_slots_env_and_floor(monkeypatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH_SLOTS", "7")
    assert DP.dispatch_slots() == 7
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH_SLOTS", "garbage")
    assert DP.dispatch_slots() == DP.DEFAULT_DISPATCH_SLOTS


@pytest.mark.asyncio
async def test_slots_semaphore_is_per_loop_and_bounded() -> None:
    sem = DP.get_slots_semaphore()
    assert isinstance(sem, asyncio.Semaphore)
    # 同 loop 复用同一信号量；容量来自 dispatch_slots()。
    assert sem is DP.get_slots_semaphore()
