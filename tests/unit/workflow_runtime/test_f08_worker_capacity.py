"""F08 / ADR-0214 D4：worker 容量二值化（NO_CAPABLE_WORKER vs RESOURCE_EXHAUSTED）。

验收面：
- profile_capacity：声明槽位/占用求和；load 未上报 → fail-open（占用 0）；
- choose_dispatch：无合格 worker → NoCapableWorker（语义不变）；
  worker 在但槽满 → WorkerCapacityExhausted（新 typed）；
  槽未满 → durable；
- 重试语义分界：NO_CAPABLE_WORKER 不可重试；RESOURCE_EXHAUSTED 可重试
  （retry 词表锁定）；
- AutoDispatcher：两种 typed 失败都映射为诚实 outcome，不烧重试预算面。
"""
from __future__ import annotations

import pytest

from app.services.workflow_runtime import dispatch as DP
from app.services.workflow_runtime import retry as RT
from app.services.workflow_runtime.adapters_geocompute import (
    GeoComputeNodeOutcome,
)
from app.services.workflow_runtime.dispatch import (
    AutoDispatcher,
    choose_dispatch,
    profile_capacity,
)


class RegStub:
    def __init__(self, rows):
        self._rows = rows

    def list_active(self, **kw):
        rows = self._rows
        if kw.get("profile"):
            rows = [r for r in rows
                    if (r.get("capabilities", {}).get("profiles", {})
                        .get(kw["profile"], 0) > 0)]
        if kw.get("backend"):
            rows = [r for r in rows
                    if kw["backend"] in (r.get("capabilities", {})
                                         .get("backends", []))]
        return rows


def test_profile_capacity_sums_slots_and_in_flight():
    rows = [
        {"capabilities": {"profiles": {"raster": 2}},
         "load": {"in_flight": 2}},
        {"capabilities": {"profiles": {"raster": 1}}, "load": {}},
    ]
    assert profile_capacity(rows, profile="raster") == (3, 2)


def test_profile_capacity_fails_open_without_any_load_report():
    """没有任何 worker 上报过 load → 占用未知按 0（fail-open）。"""
    rows = [
        {"capabilities": {"profiles": {"raster": 2}}, "load": {}},
        {"capabilities": {"profiles": {"raster": 1}}},
    ]
    assert profile_capacity(rows, profile="raster") == (3, 0)


def test_profile_capacity_negative_in_flight_clamped():
    rows = [{"capabilities": {"profiles": {"raster": 2}},
             "load": {"in_flight": -5}}]
    assert profile_capacity(rows, profile="raster") == (2, 0)


def test_choose_dispatch_no_capable_worker_unchanged(monkeypatch):
    reg = RegStub([])
    monkeypatch.setattr(DP, "dispatch_mode", lambda: "durable")
    # 经模块属性引用（v6 套件的 importlib.reload 会重建异常类身份）
    with pytest.raises(DP.NoCapableWorker):
        choose_dispatch({"resources": {"profile": "raster"}},
                        input_rows=0, registry=reg)


def test_choose_dispatch_capacity_exhausted_when_full():
    rows = [{"capabilities": {"profiles": {"raster": 2},
                              "backends": ["durable"]},
             "load": {"in_flight": 2}}]
    old = DP.dispatch_mode
    DP.dispatch_mode = lambda: "durable"
    try:
        with pytest.raises(DP.WorkerCapacityExhausted) as ei:
            choose_dispatch({"resources": {"profile": "raster"}},
                            input_rows=0, registry=RegStub(rows))
        assert ei.value.profile == "raster"
        assert ei.value.slots == 2
        assert ei.value.in_flight == 2
    finally:
        DP.dispatch_mode = old


def test_choose_dispatch_durable_when_slots_free():
    rows = [{"capabilities": {"profiles": {"raster": 2},
                              "backends": ["durable"]},
             "load": {"in_flight": 1}}]
    old = DP.dispatch_mode
    DP.dispatch_mode = lambda: "durable"
    try:
        assert choose_dispatch({"resources": {"profile": "raster"}},
                               input_rows=0,
                               registry=RegStub(rows)) == "durable"
    finally:
        DP.dispatch_mode = old


def test_retry_semantics_boundary():
    """方向红线：无 worker（确定性）不可重试；槽满（瞬时）可重试。"""
    assert RT.error_retryable("NO_CAPABLE_WORKER") is False
    assert RT.error_retryable("RESOURCE_EXHAUSTED") is True
    assert RT.error_retryable("RESOURCE_EXHAUSTED",
                              "deterministic_unsupported") is True


@pytest.mark.asyncio
async def test_auto_dispatcher_maps_capacity_exhausted(monkeypatch):
    rows = [{"capabilities": {"profiles": {"raster": 1},
                              "backends": ["durable"]},
             "load": {"in_flight": 1}}]

    class CapReg:
        def list_active(self, **kw):
            return rows

    monkeypatch.setattr(DP, "dispatch_mode", lambda: "durable")
    d = AutoDispatcher(owner_scope="u:a", registry=CapReg())
    outcome = await d.execute(
        node={"node_id": "n", "resources": {"profile": "raster"}},
        dag={}, input_refs=[], params={}, session_id="s",
        port_idents={}, cancel_token=None,
    )
    assert isinstance(outcome, GeoComputeNodeOutcome)
    assert outcome.ok is False
    assert outcome.error_code == "RESOURCE_EXHAUSTED"
    assert outcome.failure_class == "transient_remote"


@pytest.mark.asyncio
async def test_auto_dispatcher_maps_no_capable_worker(monkeypatch):
    class EmptyReg:
        def list_active(self, **kw):
            return []

    monkeypatch.setattr(DP, "dispatch_mode", lambda: "durable")
    d = AutoDispatcher(owner_scope="u:a", registry=EmptyReg())
    outcome = await d.execute(
        node={"node_id": "n", "resources": {"profile": "raster"}},
        dag={}, input_refs=[], params={}, session_id="s",
        port_idents={}, cancel_token=None,
    )
    assert outcome.ok is False
    assert outcome.error_code == "NO_CAPABLE_WORKER"
    assert outcome.failure_class == "deterministic_unsupported"
