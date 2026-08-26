"""finalize_display 终态确认证据（Goal C / Slice 6）。

覆盖：
- 结果携带 mapspec_fingerprint → dispatch 进入 cartographic gate
  （has_cartographic_generation 判据）；
- 展示集落服务端 desired state（MapSpec layout.visibility 持久化）；
- final_display 证据块（show_layer_ids / desired_state_patched / 前端验证）；
- 普通无 fingerprint 工具不触发 gate（map-producing turn 判据仍是
  fingerprint/mutation 驱动——普通对话零开销）。
"""
import shutil
import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"fin-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
async def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {}},
        ],
    }


async def _seed_layer(sid: str, layer_id: str):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    return await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": layer_id, "source": f"s-{layer_id}", "type": "circle",
                   "paint": {"circle-color": "#00f"}, "layout": {"visibility": "none"}},
            source_data=_geojson(),
        ),
    )


@pytest.mark.asyncio
async def test_finalize_display_carries_fingerprint_and_persists_desired(registry, clean_session):
    layer_id = "result-final-heat"
    await _seed_layer(clean_session, layer_id)
    # ref 注册（resolve_layer_ref 会话所有权门）
    ref_id = await session_data_manager.store(clean_session, _geojson(), prefix="geojson")
    await session_data_manager.set_alias(clean_session, ref_id, layer_id)
    map_state = await session_data_manager.get_map_state(clean_session)
    map_state.setdefault("layers", []).append({"id": layer_id, "name": "final"})
    await session_data_manager.set_map_state(clean_session, "layers", map_state["layers"])

    res = await registry.dispatch(
        "finalize_display",
        {"show_refs": [layer_id]},
        session_id=clean_session,
    )
    assert res.get("success") is True, res
    # 门禁判据：fingerprint 非空 → has_cartographic_generation
    assert res.get("mapspec_fingerprint")
    assert res["final_display"]["show_layer_ids"] == [layer_id]
    assert res["final_display"]["desired_state_patched"] == [layer_id]
    assert res["final_display"]["verification"] == "frontend_runtime"

    # 服务端 desired：展示层 visible（reload 不丢）
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    stored = next(l for l in spec["layers"] if l["id"] == layer_id)
    assert stored["layout"]["visibility"] == "visible"


@pytest.mark.asyncio
async def test_finalize_display_fingerprint_routes_into_cartographic_gate(registry, clean_session):
    """Pi bridge 门禁：携带 fingerprint 的 tool result 创建/进入 session harness。"""
    layer_id = "result-gate-layer"
    await _seed_layer(clean_session, layer_id)
    ref_id = await session_data_manager.store(clean_session, _geojson(), prefix="geojson")
    await session_data_manager.set_alias(clean_session, ref_id, layer_id)
    map_state = await session_data_manager.get_map_state(clean_session)
    map_state.setdefault("layers", []).append({"id": layer_id, "name": "gate"})
    await session_data_manager.set_map_state(clean_session, "layers", map_state["layers"])

    res = await registry.dispatch(
        "finalize_display",
        {"show_refs": [layer_id]},
        session_id=clean_session,
    )
    # 判据复刻 agent_pi_bridge.has_cartographic_generation
    has_cartographic_generation = bool(res.get("mapspec_fingerprint"))
    assert has_cartographic_generation is True


@pytest.mark.asyncio
async def test_finalize_display_hud_only_layer_skips_desired_patch(registry, clean_session):
    """HUD-only 层（不在 MapSpec）：命令照发，服务端 desired 跳过（前端收敛）。"""
    map_state = await session_data_manager.get_map_state(clean_session)
    map_state.setdefault("layers", []).append({"id": "hud-only-poi", "name": "poi"})
    await session_data_manager.set_map_state(clean_session, "layers", map_state["layers"])

    res = await registry.dispatch(
        "finalize_display",
        {"show_refs": ["hud-only-poi"]},
        session_id=clean_session,
    )
    assert res.get("success") is True
    assert res["final_display"]["desired_state_patched"] == []
    assert "hud-only-poi" in res["params"]["show_layer_ids"]


@pytest.mark.asyncio
async def test_finalize_display_unknown_ref_rejected_before_mutation(registry, clean_session):
    res = await registry.dispatch(
        "finalize_display",
        {"show_refs": ["ref:not-exist"]},
        session_id=clean_session,
    )
    assert res.get("success") is not True
    assert "error" in res or res.get("message")


@pytest.mark.asyncio
async def test_ordinary_tool_without_fingerprint_does_not_gate(registry, clean_session):
    """普通对话零开销：无 fingerprint 的工具结果不满足 gate 判据。"""
    from app.tools.chart import generate_chart
    res = generate_chart(
        chart_type="bar", title="t", data='[{"name":"a","value":1}]',
    )
    assert bool(res.get("mapspec_fingerprint")) is False
