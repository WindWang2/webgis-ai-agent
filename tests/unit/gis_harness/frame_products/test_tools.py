"""G15 WP09 —— frame_product_* 工具面（ToolRegistry dispatch 真实旅程）。"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.tools.frame_product_tools import register_frame_product_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.unit


def _fc() -> Dict[str, Any]:
    feats = []
    for y in (2022, 2024):
        for d in range(3):
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [104.0 + d * 0.1, 30.6], [104.1 + d * 0.1, 30.6],
                    [104.1 + d * 0.1, 30.7], [104.0 + d * 0.1, 30.7],
                    [104.0 + d * 0.1, 30.6]]]},
                "properties": {"year": y, "district": f"d{d}",
                               "per_10k": 20.0 + d * 3 + (y - 2022)},
            })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
async def ref_store(monkeypatch):
    fcs = {"ref:ind": _fc()}

    async def _resolve_alias(session_id, ref_or_alias):
        return ref_or_alias

    async def _get_ref_descriptor(session_id, resolved):
        return {"content_revision": 3, "feature_count": 6}

    async def _get_ref_data_shared(session_id, resolved, owner_token=None):
        if resolved not in fcs:
            return SimpleNamespace(success=False, error="nf", data=None)
        return SimpleNamespace(success=True, data=fcs[resolved])

    import app.services.session_data as sd

    monkeypatch.setattr(sd.session_data_manager, "resolve_alias", _resolve_alias)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_descriptor", _get_ref_descriptor)
    monkeypatch.setattr(sd.session_data_manager, "get_ref_data_shared", _get_ref_data_shared)
    return fcs


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_frame_product_tools(r)
    return r


async def _seed_layer(sid: str) -> None:
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent, UpsertSourceIntent

    await apply_gis_mutation(sid, UpsertSourceIntent(
        source_id="src", source={"type": "geojson", "inlineData": _fc()}),
        origin="agent", actor="test")
    await apply_gis_mutation(sid, UpsertLayerIntent(
        layer={"id": "lyr", "type": "fill", "source": "src"}),
        origin="agent", actor="test")


async def test_plan_is_readonly_with_evidence(registry, ref_store):
    out = await registry.dispatch("frame_product_plan", session_id="s-x", arguments={
        "ref_id": "ref:ind", "product_kind": "time_series",
        "field": "per_10k",
    })
    assert out["ok"] is True
    assert [f["frame_id"] for f in out["frames"]] == ["fp-t-2022", "fp-t-2024"]
    # refs-only：无要素载荷。
    assert all("shared_fc" not in f for f in out["frames"])


async def test_plan_single_period_refused(registry, ref_store):
    # district 有 3 值 → ok（此处不消费）；改用不存在的字段验证拒绝
    out2 = await registry.dispatch("frame_product_plan", session_id="s-x", arguments={
        "ref_id": "ref:ind", "product_kind": "category_compare",
        "split_field": "ghost",
    })
    assert out2["status"] == "refused"
    assert out2["code"] == "category_values_insufficient"


async def test_bind_status_update_unbind_journey(registry, ref_store):
    sid = f"tool-{uuid.uuid4().hex[:10]}"
    await _seed_layer(sid)

    bind = await registry.dispatch("frame_product_bind", session_id=sid, arguments={
        "ref_id": "ref:ind", "product_kind": "time_series",
        "field": "per_10k", "title": "两年对比",
    })
    assert bind["status"] == "ok", bind
    assert bind["shared_expression"]["domain_status"] == "complete"

    status = await registry.dispatch("frame_product_status", session_id=sid, arguments={})
    assert status["bound"] is True
    frames = status["frames"]
    assert frames[0]["frame_id"] == "fp-t-2022"

    # 「只改第二幅标题」：键级 patch，frame_id 身份稳定。
    committed = await _committed_product_frames(sid)
    new_frames = [dict(f) for f in committed]
    new_frames[1]["title"] = "2024（改名）"
    upd = await registry.dispatch("frame_product_update", session_id=sid, arguments={
        "patch": {"frames": new_frames, "active_frame": "fp-t-2024"},

    })
    assert upd["status"] == "ok", upd

    status2 = await registry.dispatch("frame_product_status", session_id=sid, arguments={})
    by_id = {f["frame_id"]: f for f in status2["frames"]}
    assert by_id["fp-t-2024"]["title"] == "2024（改名）"
    assert by_id["fp-t-2022"]["title"] == "2022"
    assert status2["active_frame"] == "fp-t-2024"

    un = await registry.dispatch("frame_product_unbind", session_id=sid, arguments={})
    assert un["status"] == "ok"


async def _committed_product_frames(sid: str):
    from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

    engine = MapSpecLifecycleEngine()
    spec = await engine.store.get_mapspec(sid) or {}
    return spec.get("frame_product", {}).get("frames", [])
