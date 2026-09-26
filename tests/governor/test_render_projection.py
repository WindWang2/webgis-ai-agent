"""F13 render 通道生产接线测试（ADR-0214 D2）。

锁定：会话投影缓存与 fail-open、dispatch adapter 仅对 render 族工具
供给 render_input、estimate 细化真实生效（RENDER_WORK_UNITS 维在位）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.render_work_projection import (
    PROJECTION_SCHEMA_VERSION,
    project_render_work,
)
from app.services.governor.contract import Dimension, Subsystem
from app.services.governor.render_budget import RenderWorkInput
from app.services.governor.render_projection import (
    get_render_work_projection,
    render_input_for_session,
    reset_render_projection_cache_for_tests,
)

# _MIN_SPEC 形状对齐 MapSpecDocument（layers/sources/profile 计数）。
_MIN_SPEC = {
    "version": "1.2",
    "sources": {
        "s": {"type": "geojson", "profile": {"featureCount": 5000}},
    },
    "layers": [
        {"id": "l1", "source": "s", "type": "fill"},
        {"id": "l2", "source": "s", "type": "symbol",
         "label": {"field": "name"}},
    ],
    "layout": {"components": [{"id": "c1", "type": "title"}]},
}


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_render_projection_cache_for_tests()
    yield
    reset_render_projection_cache_for_tests()


class _FakeSessionData:
    """get_map_state 桩：返回携带 spec + revision 的最小 map_state。"""

    def __init__(self, state):
        self._state = state
        self.calls = 0

    async def get_map_state(self, session_id):
        self.calls += 1
        return self._state


@pytest.mark.asyncio
async def test_projection_from_session_state(monkeypatch):
    from app.services.session_data import session_data_manager

    fake = _FakeSessionData({
        "mapspec": _MIN_SPEC,
        "_cartographic_mutation_revision": 5,
    })
    monkeypatch.setattr(session_data_manager, "get_map_state", fake.get_map_state)

    proj = await get_render_work_projection("sess-1")
    assert proj is not None
    assert proj.schema_version == PROJECTION_SCHEMA_VERSION
    assert proj.mapspec_revision == 5
    assert proj.work_input.feature_count == 5000
    assert proj.work_input.layer_count == 2

    # 缓存命中：同 (fingerprint, revision) 复用同一投影对象。state 读取
    # 仍会发生（revision 只能从 state 发现 —— 正确性成本），缓存省的是
    # fingerprint 哈希 + 投影计算。
    proj2 = await get_render_work_projection("sess-1")
    assert proj2 is proj
    assert fake.calls == 2

    # revision 前进 → 身份键变化 → 重新投影
    fake._state["_cartographic_mutation_revision"] = 6
    proj3 = await get_render_work_projection("sess-1")
    assert proj3.mapspec_revision == 6
    assert proj3 is not proj


@pytest.mark.asyncio
async def test_failopen_on_store_error(monkeypatch):
    from app.services.session_data import session_data_manager

    async def _boom(_sid):
        raise RuntimeError("redis down")

    monkeypatch.setattr(session_data_manager, "get_map_state", _boom)
    assert await get_render_work_projection("sess-x") is None
    assert await render_input_for_session("sess-x") is None


@pytest.mark.asyncio
async def test_failopen_on_bad_state(monkeypatch):
    from app.services.session_data import session_data_manager

    for bad in ({}, {"_cartographic_deleted": True}, None):
        fake = _FakeSessionData(bad)
        monkeypatch.setattr(session_data_manager, "get_map_state", fake.get_map_state)
        assert await get_render_work_projection("sess-y") is None


@pytest.mark.asyncio
async def test_deleted_session_returns_none(monkeypatch):
    from app.services.session_data import session_data_manager

    fake = _FakeSessionData({"_cartographic_deleted": True, "mapspec": _MIN_SPEC})
    monkeypatch.setattr(session_data_manager, "get_map_state", fake.get_map_state)
    assert await get_render_work_projection("sess-d") is None


class _FakeDecision:
    allowed = True
    reasons: list = []
    suggestions: list = []
    degrade_hint = {}


class _FakeGovernor:
    """observe 模式 governor 桩（enforce=False → 照常执行 + complete）。"""

    def __init__(self):
        from app.services.governor.config import GovernorMode

        class _Cfg:
            mode = GovernorMode.OBSERVE

        self.config = _Cfg()
        self.demands = []

    async def admit_and_reserve(self, demand):
        self.demands.append(demand)
        return _FakeDecision(), object(), object()

    async def complete(self, reservation, ticket, *, usage=None,
                       actual=None, estimate=None):
        return None


class TestDispatchRenderChannel:
    """dispatch adapter：render 族工具喂投影，其余不喂（#1408 模式）。"""

    def _adapter(self):
        from app.services.governor.dispatch_adapter import GovernorDispatchAdapter

        return GovernorDispatchAdapter(
            governor=None, metadata_fn=lambda _: {"cost": "medium"})

    def test_render_tool_demand_carries_render_input(self):
        adapter = self._adapter()
        ri = RenderWorkInput(layer_count=3, feature_count=90_000)
        demand = adapter._build_demand(
            "render_map_screenshot", {}, session_id="s", turn_id="t",
            render_input=ri,
        )
        assert demand.subsystem in (
            Subsystem.RENDER, Subsystem.BROWSER)
        work = demand.estimate.dims.get(Dimension.RENDER_WORK_UNITS)
        assert work is not None
        assert work.expected > 0
        assert "render_formula" in (demand.estimate.source or "")
        assert demand.estimate.browser_required is True

    def test_non_render_tool_ignores_render_input_shape(self):
        adapter = self._adapter()
        demand = adapter._build_demand(
            "query_poi", {"limit": 5}, session_id="s", turn_id="t",
            render_input=None,
        )
        assert demand.subsystem == Subsystem.DATA_FABRIC
        assert demand.estimate.dims.get(Dimension.RENDER_WORK_UNITS) is None

    @pytest.mark.asyncio
    async def test_run_supplies_render_input_for_browser_tools(
        self, monkeypatch,
    ):
        """run() 端到端：screenshot 类工具 → 会话投影进入 demand。"""
        from app.services.governor import dispatch_adapter as da
        from app.services.session_data import session_data_manager

        fake = _FakeSessionData({
            "mapspec": _MIN_SPEC,
            "_cartographic_mutation_revision": 3,
        })
        monkeypatch.setattr(
            session_data_manager, "get_map_state", fake.get_map_state)

        governor = _FakeGovernor()
        adapter = da.GovernorDispatchAdapter(governor=governor)
        result = await adapter.run(
            tool_name="screenshot_map",
            tool_args={},
            session_id="sess-run",
            dispatch_inner=_async_ok_payload,
        )
        assert result == {"success": True}
        assert len(governor.demands) == 1
        est = governor.demands[0].estimate
        assert est.dims.get(Dimension.RENDER_WORK_UNITS) is not None
        assert est.browser_required is True
        assert "render_formula" in (est.source or "")

    @pytest.mark.asyncio
    async def test_run_skips_render_input_for_data_tools(
        self, monkeypatch,
    ):
        from app.services.governor import dispatch_adapter as da
        from app.services.session_data import session_data_manager

        fake = _FakeSessionData({"mapspec": _MIN_SPEC})
        monkeypatch.setattr(
            session_data_manager, "get_map_state", fake.get_map_state)

        governor = _FakeGovernor()
        adapter = da.GovernorDispatchAdapter(governor=governor)
        await adapter.run(
            tool_name="query_poi",
            tool_args={},
            session_id="sess-run",
            dispatch_inner=_async_ok_payload,
        )
        assert len(governor.demands) == 1
        # DF 通道（df_cost）可能细化 FEATURE_COUNT，但 render 细化缺席
        assert governor.demands[0].estimate.dims.get(
            Dimension.RENDER_WORK_UNITS) is None


async def _async_ok_payload():
    return {"success": True}


class TestProjectionPure:
    def test_project_uses_prior_for_missing_profile(self):
        spec = {
            "version": "1.2",
            "sources": {"s": {"type": "geojson"}},
            "layers": [{"id": "l1", "source": "s", "type": "fill"}],
        }
        proj = project_render_work(spec, revision=1, fingerprint="fp")
        assert proj.features_estimated is True
        assert proj.work_input.feature_count > 0
