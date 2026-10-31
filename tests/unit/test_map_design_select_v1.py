"""webgis_map_design_select —— 候选选择 user-wins pin 测试（M3）。

真实 dispatch 缝（ToolRegistry.dispatch）覆盖：
1. 有效选择：建档（与 webgis_map_intent 内部同参 ensure_document）→ select
   → pin 落档（读回校验）→ 下一轮产品 design_candidates 标记
   userRequested/honored（自动重规划不丢用户选择）；
2. typed 拒绝：归一化改道（dot_density_map）与未注册 id；
3. 无档案；幂等重放。
"""
import uuid

import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.requirement_ir import service as _req_svc
from app.services.session_data import session_data_manager
from app.services.gis_harness.tools import register_gis_harness_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.cartography

_QUERY = "成都市小学分布情况"


def _point_fc(n: int = 40):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.06 + i * 0.008, 30.66]},
             "properties": {"name": f"模拟小学-{i}"}}
            for i in range(n)
        ],
    }


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_gis_harness_tools(reg)
    return reg


@pytest.fixture
async def sid():
    session_id = f"sess-design-select-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


async def _ensure_doc(sid: str):
    """与 webgis_map_intent 内部 seam 完全同参的建档（生产同一路径）。"""
    intent = resolve_map_request_intent(_QUERY)
    res = await _req_svc.ensure_document(
        session_data_manager, sid, _QUERY, core=intent, turn=1)
    assert res["summary"], res
    return res


@pytest.mark.asyncio
async def test_select_pins_and_next_product_honors(registry, sid):
    await _ensure_doc(sid)
    res = await registry.dispatch(
        "webgis_map_design_select",
        {"query": _QUERY, "session_id": sid, "map_model_id": "point_cluster",
         "reason": "用户要聚合表达缓解过密"},
        session_id=sid,
    )
    assert res["success"] is True, res
    assert res["pinned_model"] == "point_cluster"
    assert res["user_wins"] is True

    # 下一轮产品：候选面把 pin 作为用户显式要求（保留席位 + honored）
    ref = await session_data_manager.store(sid, _point_fc(), prefix="geojson")
    prod = await registry.dispatch(
        "webgis_map_product",
        {"query": _QUERY, "session_id": sid, "primary_ref": ref,
         "title": "成都市小学分布（模拟数据）"},
        session_id=sid,
    )
    assert prod["success"] is True
    dc = prod["design_candidates"]
    pinned_cand = [c for c in dc["candidates"]
                   if c["mapModel"] == "point_cluster"]
    assert pinned_cand, "pin 的表达必须进入候选面（保留席位）"
    assert pinned_cand[0]["userRequested"] is True
    assert "point_cluster" in dc["userRequestHandling"]["honored"]
    # pin 事实行进入决策 trace（可解释面）
    facts = prod["design_decision_trace"]["dataFacts"]
    assert any("point_cluster" in f for f in facts)


@pytest.mark.asyncio
async def test_select_idempotent(registry, sid):
    await _ensure_doc(sid)
    res = None
    for _ in range(2):
        res = await registry.dispatch(
            "webgis_map_design_select",
            {"query": _QUERY, "session_id": sid,
             "map_model_id": "administrative_choropleth"},
            session_id=sid,
        )
        assert res["success"] is True, res
        # 别名（choropleth）经 bridge/alias 解析回注册 id，稳定不漂移
        assert res["pinned_model"] == "administrative_choropleth"
    assert res["previous_pin"] in ("", "administrative_choropleth")


@pytest.mark.asyncio
async def test_select_refuses_retargeting_and_unknown(registry, sid):
    await _ensure_doc(sid)
    # R2 F2 后：精确注册 id（含 dot_density_map）如实 pin 原模型——
    # 不再因归一化子串塌缩被 typed 拒绝（静默改道的反面是诚实精确）。
    exact = await registry.dispatch(
        "webgis_map_design_select",
        {"query": _QUERY, "session_id": sid, "map_model_id": "dot_density_map"},
        session_id=sid,
    )
    assert exact["success"] is True, exact
    assert exact["pinned_model"] == "dot_density_map"

    # 别名输入经 registry.resolve 先行 canonical 化 → 同样如实 pin 其
    # canonical 模型（"ratio_choropleth" 是 normalized_choropleth 别名；
    # F2 精确 id 预检后塌缩只发生在 NL 层，工具层不可达——mismatch 分支
    # 保留为防御性守卫）。
    alias = await registry.dispatch(
        "webgis_map_design_select",
        {"query": _QUERY, "session_id": sid,
         "map_model_id": "ratio_choropleth"},
        session_id=sid,
    )
    assert alias["success"] is True, alias
    assert alias["pinned_model"] == "normalized_choropleth"

    unknown = await registry.dispatch(
        "webgis_map_design_select",
        {"query": _QUERY, "session_id": sid, "map_model_id": "no_such_model"},
        session_id=sid,
    )
    assert unknown["success"] is False
    assert unknown["error_code"] == "unknown_model"


@pytest.mark.asyncio
async def test_select_requires_document(registry, sid):
    res = await registry.dispatch(
        "webgis_map_design_select",
        {"query": _QUERY, "session_id": sid, "map_model_id": "point_cluster"},
        session_id=sid,
    )
    assert res["success"] is False
    assert res["error_code"] == "requirement_document_absent"


# ── 评审清偿回归（R1 轮 P1-1/P1-2）───────────────────────────────────


@pytest.mark.asyncio
async def test_select_visual_heatmap_roundtrip_via_ir_bridge(registry, sid):
    """P1-1：visual_heatmap（norm→heatmap 经 IR 桥回 visual_heatmap）必须可 pin。"""
    await _ensure_doc(sid)
    res = await registry.dispatch(
        "webgis_map_design_select",
        {"query": _QUERY, "session_id": sid,
         "map_model_id": "visual_heatmap"},
        session_id=sid,
    )
    assert res["success"] is True, res
    assert res["pinned_model"] == "visual_heatmap"


@pytest.mark.asyncio
async def test_select_normalized_choropleth_pins_after_exact_id_precheck(
        registry, sid):
    """R2 F2：normalize 精确注册 id 预检后，率语义模型可 pin（此前
    "normalized_choropleth" 含 "choropleth" 子串被塌缩改道而 typed 拒绝，
    整个率语义 user-wins 路线不可达）。"""
    await _ensure_doc(sid)
    res = await registry.dispatch(
        "webgis_map_design_select",
        {"query": _QUERY, "session_id": sid,
         "map_model_id": "normalized_choropleth"},
        session_id=sid,
    )
    assert res["success"] is True, res
    assert res["pinned_model"] == "normalized_choropleth"
