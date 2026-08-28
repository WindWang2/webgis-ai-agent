"""GISWorldState 基础（C2）：统一读模型 + GISMutation 门面 + user-wins 守卫。

G6 场景的服务端锁定：用户 durable 隐藏图层 → Agent finalize_display 收口
不得把该层翻回可见（user interaction wins）；重载后仍隐藏。
"""
import pytest

from app.services.gis_world_state import (
    apply_gis_mutation,
    build_world_state,
    get_provenance,
)
from app.services.gis_world_state.provenance import last_presentation_owner
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    UpsertLayerIntent,
    RemoveLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager


async def _seed_layer(session_id: str, layer_id: str = "poi-main") -> int:
    """Seed a layer; returns the post-seed revision (user-origin CAS needs it)."""
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(
            layer={
                "id": layer_id,
                "type": "circle",
                "source": f"src-{layer_id}",
                "paint": {"circle-color": "#ff0000"},
            },
            source_data={
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                            "properties": {"name": "p1", "score": 1.0},
                        }
                    ],
                },
            },
        ),
    )
    assert not res.is_error
    return res.mutation_revision


@pytest.mark.asyncio
async def test_mutation_facade_records_provenance():
    sid = "gws-prov-1"
    revision = await _seed_layer(sid)
    await session_data_manager.set_map_state(sid, "_gis_provenance", [])
    engine = MapSpecLifecycleEngine()

    res = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="poi-main", visible=False, opacity=0.4),
        origin="user",
        actor="test",
        engine=engine,
        expected_revision=revision,
    )
    assert not res.is_error

    entries = await get_provenance(sid)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["origin"] == "user"
    assert entry["kind"] == "PatchLayerPresentationIntent"
    assert entry["target"] == "poi-main"
    assert entry["revision"] == res.mutation_revision
    assert entry["detail"]["visible"] is False
    assert entry["detail"]["opacity"] == 0.4

    owner = last_presentation_owner(entries, "poi-main")
    assert owner is not None and owner["origin"] == "user"


@pytest.mark.asyncio
async def test_user_wins_guard_blocks_agent_reversal():
    """G6 核心：用户隐藏后，agent 不得翻回可见；同值幂等重放允许。"""
    sid = "gws-guard-1"
    revision = await _seed_layer(sid)
    await session_data_manager.set_map_state(sid, "_gis_provenance", [])
    engine = MapSpecLifecycleEngine()

    # 用户隐藏（durable，走门面记录 provenance）
    user_hide = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="poi-main", visible=False),
        origin="user",
        actor="test",
        engine=engine,
        expected_revision=revision,
    )
    assert not user_hide.is_error

    # Agent 反转 → 拒绝（is_error + correction_hint，不抛异常）
    agent_show = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="poi-main", visible=True),
        origin="agent",
        actor="finalize_display",
        engine=engine,
    )
    assert agent_show.is_error
    assert "不覆盖用户显式操作" in agent_show.error_msg
    assert agent_show.correction_hint

    # 服务端 desired state 保持用户决策
    spec = await mapspec_store_instance.get_mapspec(sid)
    layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "poi-main")
    assert layer["layout"]["visibility"] == "none"

    # Agent 同值重放（visible=False）→ 允许（幂等，无对抗）
    agent_same = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="poi-main", visible=False),
        origin="agent",
        actor="finalize_display",
        engine=engine,
    )
    assert not agent_same.is_error

    # Agent 对"无用户决策记录"的层隐藏 → 允许（正常收口语义）
    await _seed_layer(sid, layer_id="mid-buffer")
    agent_hide = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="mid-buffer", visible=False),
        origin="agent",
        actor="finalize_display",
        engine=engine,
    )
    assert not agent_hide.is_error


@pytest.mark.asyncio
async def test_guard_does_not_block_after_user_reshows():
    """用户自己重新打开后，agent 后续 presentation 操作恢复正常语义。"""
    sid = "gws-guard-2"
    revision = await _seed_layer(sid)
    await session_data_manager.set_map_state(sid, "_gis_provenance", [])
    engine = MapSpecLifecycleEngine()
    hide = await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="poi-main", visible=False),
        origin="user", actor="test", engine=engine, expected_revision=revision,
    )
    await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="poi-main", visible=True),
        origin="user", actor="test", engine=engine,
        expected_revision=hide.mutation_revision,
    )
    # 用户最后决策 = visible=True；agent 重放 visible=True 允许
    agent_show = await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="poi-main", visible=True),
        origin="agent", actor="finalize_display", engine=engine,
    )
    assert not agent_show.is_error


@pytest.mark.asyncio
async def test_world_state_snapshot_bounded_and_payload_free():
    sid = "gws-snapshot-1"
    revision = await _seed_layer(sid)
    await session_data_manager.set_map_state(sid, "_gis_provenance", [])
    engine = MapSpecLifecycleEngine()
    hide = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="poi-main", visible=False),
        origin="user",
        actor="test",
        engine=engine,
        expected_revision=revision,
    )
    await apply_gis_mutation(
        sid, RemoveLayerIntent(layer_id="poi-main"), origin="user", actor="test",
        engine=engine, expected_revision=hide.mutation_revision,
    )

    snapshot = await build_world_state(sid)
    assert snapshot["session_id"] == sid
    assert isinstance(snapshot["revision"], int)
    # payload 不变量：快照文本里绝不出现坐标数组 / FeatureCollection
    import json

    text = json.dumps(snapshot, ensure_ascii=False)
    assert "FeatureCollection" not in text
    # provenance 尾部包含用户的 presentation 决策
    kinds = [e.get("kind") for e in snapshot["provenance"]]
    assert "PatchLayerPresentationIntent" in kinds
    assert "RemoveLayerIntent" in kinds


@pytest.mark.asyncio
async def test_finalize_display_respects_user_hidden_layer():
    """G6 端到端（工具层）：finalize_display 对用户隐藏层的收口 show 被守卫拒绝，
    结果如实携带 user_hidden_respected，工具整体仍成功（不阻断本轮成图）。"""
    from app.tools.registry import ToolRegistry
    from app.tools.layer_manager import register_layer_management_tools

    sid = "gws-finalize-1"
    await _seed_layer(sid)
    revision = await _seed_layer(sid, layer_id="mid-buffer")
    # 全部 seed 完成后清一次决策链（seed 走 engine 直连，不产生 provenance，
    # 此处只是防御性重置）
    await session_data_manager.set_map_state(sid, "_gis_provenance", [])
    engine = MapSpecLifecycleEngine()
    await apply_gis_mutation(
        sid, PatchLayerPresentationIntent(layer_id="poi-main", visible=False),
        origin="user", actor="test", engine=engine, expected_revision=revision,
    )

    registry = ToolRegistry()
    register_layer_management_tools(registry)
    result = await registry.dispatch(
        "finalize_display",
        {"show_refs": ["poi-main", "mid-buffer"]},
        session_id=sid,
    )
    payload = result.result if hasattr(result, "result") else result
    if hasattr(payload, "llm_payload"):
        payload = payload.llm_payload
    assert payload.get("success") is True
    final = payload.get("final_display") or {}
    assert "poi-main" in final.get("user_hidden_respected", [])
    assert "poi-main" not in final.get("desired_state_patched", [])

    # 服务端 desired：用户隐藏决策原样保留
    spec = await mapspec_store_instance.get_mapspec(sid)
    layer = next(lyr for lyr in spec["layers"] if lyr["id"] == "poi-main")
    assert layer["layout"]["visibility"] == "none"
