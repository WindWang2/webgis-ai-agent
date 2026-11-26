"""paper_scale 工具注册与派发测试（WP5）—— 注册面 + happy/sad path。"""
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
    sid = f"psctool-{uuid.uuid4().hex[:10]}"
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(sid, UpsertLayerIntent(
        layer={"id": "districts", "type": "fill", "source": "src-districts",
               "paint": {"fill-color": "#4a90d9"}},
        source_data={"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": [
                {"type": "Feature", "id": fid,
                 "properties": {"name": fid},
                 "geometry": {"type": "Polygon", "coordinates": [ring]}}
                for fid, ring in [
                    ("a", [[115.0, 38.0], [116.0, 38.0], [116.0, 39.0],
                           [115.0, 39.0], [115.0, 38.0]]),
                    ("b", [[116.0, 38.0], [117.0, 38.0], [117.0, 39.0],
                           [116.0, 39.0], [116.0, 38.0]]),
                ]]}} ,
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
            "webgis_inspect_paper_scale_generalization",
            "webgis_preview_paper_scale_generalization",
            "webgis_apply_paper_scale_generalization",
        ):
            d = registry.descriptor(name)
            assert d is not None, name
        apply_d = registry.descriptor("webgis_apply_paper_scale_generalization")
        assert apply_d is not None
        assert str(apply_d.side_effect) == "SideEffectClass.STATE_MUTATION"
        assert apply_d.tier == 2

    def test_qa_blocked_listed_in_failure_modes(self, registry):
        d = registry.descriptor("webgis_apply_paper_scale_generalization")
        modes = [str(m) for m in (d.failure_modes or [])]
        assert any("qa_blocked" in m for m in modes)


class TestDispatch:
    async def test_inspect_happy(self, registry, session):
        res = await registry.dispatch(
            "webgis_inspect_paper_scale_generalization",
            arguments={"layer": "districts", "denominator_text": "1:50万",
                       "page_text": "A4横版", "purpose": "overview"},
            session_id=session,
        )
        payload = res.result if hasattr(res, "result") else res
        assert isinstance(payload, dict) and payload.get("status") == "ok"
        assert payload["target_denominator"] == 500_000
        assert payload["supported_family"] is True

    async def test_inspect_missing_session(self, registry):
        res = await registry.dispatch(
            "webgis_inspect_paper_scale_generalization",
            arguments={"layer": "districts", "denominator": 500_000},
            session_id="",
        )
        payload = res.result if hasattr(res, "result") else res
        assert "error" in payload

    async def test_preview_dispatch(self, registry, session):
        res = await registry.dispatch(
            "webgis_preview_paper_scale_generalization",
            arguments={"layer": "districts", "denominator": 500_000,
                       "page_text": "A4横版", "purpose": "overview"},
            session_id=session,
        )
        payload = res.result if hasattr(res, "result") else res
        assert payload.get("status") == "preview"
        assert payload["qa_summary"]["blocking"] == 0
        # 零突变
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"]
                     if lyr["id"] == "districts")
        assert not (layer.get("scale_profile") or {}).get("paper_plans")

    async def test_apply_commit_end_to_end(self, registry, session):
        res = await registry.dispatch(
            "webgis_apply_paper_scale_generalization",
            arguments={"layer": "districts", "mode": "commit",
                       "denominator_text": "1:50万", "page_text": "A4横版",
                       "purpose": "overview", "operation_id": "op-tool-1",
                       "note": "1:50万 出版"},
            session_id=session,
        )
        payload = res.result if hasattr(res, "result") else res
        assert payload.get("success") is True, payload
        assert payload["status"] == "committed"
        spec = await mapspec_store_instance.get_mapspec(session)
        layer = next(lyr for lyr in spec["layers"]
                     if lyr["id"] == "districts")
        plans = layer["scale_profile"]["paper_plans"]
        assert len(plans) == 1
        assert next(iter(plans.values()))["status"] == "frozen"

    async def test_apply_bad_scale_text_structured_error(self, registry,
                                                         session):
        res = await registry.dispatch(
            "webgis_apply_paper_scale_generalization",
            arguments={"layer": "districts", "mode": "commit",
                       "denominator_text": "无穷大"},
            session_id=session,
        )
        payload = res.result if hasattr(res, "result") else res
        assert "error" in payload
        assert "无法解析比例尺" in payload["error"]
