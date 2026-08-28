"""G6 golden 场景（C6，cartography lane）：用户手动隐藏图层 → 继续与 Agent
对话 → Agent 不覆盖用户决定 → reload 后仍隐藏。

端到端链路（不经 LLM，工具层 + 引擎层真实执行）：
1. 用户经 mutation 门面 durable 隐藏图层（origin=user，provenance 记录）；
2. Agent 轮次：finalize_display 收口试图展示该层 → UserPresentationGuard 拒绝，
   结果如实携带 user_hidden_respected，本轮成图不被阻断；
3. Agent 的逐层 set_layer_status（origin=agent patch）同样被守卫拒绝；
4. reload 模拟：presentationFromMapSpec 的后端等价投影（读 spec 的
   layout.visibility）→ 仍为 none；
5. provenance 解释该决策（最后主人是 user）。
"""
import uuid

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
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager
from app.tools.layer_manager import register_layer_management_tools
from app.tools.registry import ToolRegistry


@pytest.mark.cartography
@pytest.mark.asyncio
async def test_g6_user_hidden_layer_survives_agent_turn_and_reload():
    sid = f"g6-e2e-{uuid.uuid4().hex[:8]}"
    engine = MapSpecLifecycleEngine()

    # ── 会话建立：agent 产出两个结果层 ─────────────────────────────
    for idx, layer_id in enumerate(("product-choropleth", "product-points")):
        res = await engine.apply_mutation(
            sid,
            UpsertLayerIntent(
                layer={
                    "id": layer_id,
                    "type": "fill" if idx == 0 else "circle",
                    "source": f"src-{layer_id}",
                    "paint": {},
                },
                source_data={
                    "type": "geojson",
                    "inlineData": {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {
                                    "type": "Point",
                                    "coordinates": [104.0 + idx * 0.1, 30.6],
                                },
                                "properties": {"v": float(idx)},
                            }
                        ],
                    },
                },
            ),
        )
        assert not res.is_error
    revision = int(
        (await session_data_manager.get_map_state(sid)).get(
            "_cartographic_mutation_revision", 0
        )
        or 0
    )

    # ── 用户手动隐藏 product-choropleth（durable + provenance） ──────
    hide = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="product-choropleth", visible=False),
        origin="user",
        actor="mapspec_route",
        expected_revision=revision,
    )
    assert not hide.is_error

    # ── Agent 下一轮：finalize_display 想把它作为最终成图展示 ────────
    registry = ToolRegistry()
    register_layer_management_tools(registry)
    result = await registry.dispatch(
        "finalize_display",
        {"show_refs": ["product-choropleth", "product-points"]},
        session_id=sid,
    )
    payload = result.result if hasattr(result, "result") else result
    if hasattr(payload, "llm_payload"):
        payload = payload.llm_payload
    assert payload.get("success") is True
    final = payload.get("final_display") or {}
    # 守卫拒绝被如实上报；未受保护的层正常收口
    assert final.get("user_hidden_respected") == ["product-choropleth"]
    assert "product-points" in final.get("desired_state_patched", [])

    # ── Agent 逐层 patch 同样被守卫拦截（不只 finalize 路径） ────────
    direct = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="product-choropleth", visible=True),
        origin="agent",
        actor="set_layer_status",
    )
    assert direct.is_error
    assert "不覆盖用户显式操作" in direct.error_msg

    # ── reload 模拟：desired spec 是恢复的真相源 ────────────────────
    spec = await mapspec_store_instance.get_mapspec(sid)
    choropleth = next(lyr for lyr in spec["layers"] if lyr["id"] == "product-choropleth")
    points = next(lyr for lyr in spec["layers"] if lyr["id"] == "product-points")
    assert choropleth["layout"]["visibility"] == "none"  # 用户决策存续
    assert points["layout"].get("visibility", "visible") != "none"  # agent 收口不受影响

    # ── world state / provenance 可解释该决策 ──────────────────────
    world = await build_world_state(sid)
    assert "product-choropleth" in world["interaction"]["user_hidden_layers"]
    owner = last_presentation_owner(await get_provenance(sid), "product-choropleth")
    assert owner is not None and owner["origin"] == "user"
    assert owner["detail"]["visible"] is False
