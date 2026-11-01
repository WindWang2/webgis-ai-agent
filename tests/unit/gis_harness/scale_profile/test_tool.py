"""scale_profile 工具注册与派发测试（M7）—— 注册面 + happy/sad path。"""
from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager
from app.tools import init_tools
from app.tools.registry import ToolRegistry


@pytest.fixture()
async def session():
    sid = f"msptool-{uuid.uuid4().hex[:10]}"
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(
        layer={"id": "roads", "type": "line", "source": "src-roads",
               "paint": {"line-color": "#c33"}},
        source_data={"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": [
                {"type": "Feature", "id": f"r{i}",
                 "properties": {"道路等级": cls},
                 "geometry": {"type": "LineString", "coordinates": [
                     [104.0 + i * 0.01 + k * 0.001, 30.6 + (1e-5 if k % 2 else -1e-5)]
                     for k in range(30)]}}
                for i, cls in enumerate(["国道", "省道", "县道", "乡道", "国道",
                                         "省道", "县道", "乡道"])]}},
    ))
    assert not res.is_error, res.error_msg
    yield sid
    session_data_manager._store.pop(sid, None)
    session_data_manager._map_state.pop(sid, None)
    await mapspec_store_instance.discard_mapspec(sid)


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    init_tools(registry=reg)
    return reg


class TestRegistration:
    def test_three_tools_registered(self, registry):
        for name in (
            "webgis_inspect_map_scale_profile",
            "webgis_preview_scale_representation",
            "webgis_apply_scale_representation",
        ):
            d = registry.descriptor(name)
            assert d is not None, name
        apply_d = registry.descriptor("webgis_apply_scale_representation")
        assert apply_d is not None
        assert str(apply_d.side_effect) == "SideEffectClass.STATE_MUTATION"
        assert apply_d.tier == 2  # 受控写：非 tier-3（不删数据、敏感度低）

    def test_inspect_side_effect_none(self, registry):
        d = registry.descriptor("webgis_inspect_map_scale_profile")
        assert d is not None


class TestDispatch:
    async def test_inspect_dispatch_happy(self, registry, session):
        res = await registry.dispatch(
            "webgis_inspect_map_scale_profile",
            arguments={"layer": "roads"},
            session_id=session,
        )
        payload = res.result if hasattr(res, "result") else res
        assert isinstance(payload, dict) and payload.get("status") == "ok"
        assert payload["importance"]["selected"] is not None

    async def test_inspect_dispatch_missing_session(self, registry):
        res = await registry.dispatch(
            "webgis_inspect_map_scale_profile",
            arguments={"layer": "roads"},
            session_id="",
        )
        payload = res.result if hasattr(res, "result") else res
        assert payload == {"error": "Missing session_id context"}

    async def test_apply_preview_dispatch(self, registry, session):
        res = await registry.dispatch(
            "webgis_apply_scale_representation",
            arguments={"layer": "roads", "mode": "preview"},
            session_id=session,
        )
        payload = res.result if hasattr(res, "result") else res
        assert payload.get("status") == "preview"
        assert payload.get("success") is True

    async def test_apply_commit_dispatch(self, registry, session):
        res = await registry.dispatch(
            "webgis_apply_scale_representation",
            arguments={"layer": "roads", "mode": "commit",
                       "operation_id": "tool-op-1"},
            session_id=session,
        )
        payload = res.result if hasattr(res, "result") else res
        assert payload.get("status") == "committed", payload
        assert payload.get("success") is True
        assert payload["artifact_refs"]
