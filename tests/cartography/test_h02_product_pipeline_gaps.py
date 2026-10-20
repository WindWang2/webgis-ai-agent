"""H02 Wave 1 — 产品管线组合一致性缺口（G3/G4/G5/G6）。

缺口登记：planning/h02-cartographic-conformance/RECON.md §5a。

G3: ``webgis_map_product`` 落组件进 MapSpec layout 时不写
    ``layout.composition`` 身份块（mapspec_store.layout_set 已有
    ``composition=`` 通道，ADR-0214 D2/D3）—— 物理 MapSpec 无法追溯
    产品组合模板身份；用户显式契约（contract_id 非空）不得被 agent
    再制图覆盖（user-wins）。
G4: 终验 required_slots 在 chapter 证据缺席时兜底 [["title"],
    ["scale_bar"]]，弱于真实模板契约 —— 身份块在物理 spec 在场时必须
    从注册表恢复真契约。
G5: 显式 export intents（非 report_product）不触发 export 槽位裁决 ——
    接线组合可能缺必备 export_layout 槽。
G6: 产品语义层不复跑 composition conformance —— export_parity_gap 等
    error 级检查在产品编译面被绕过。
"""
from __future__ import annotations

import shutil
import uuid

import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.component_abi import COMPONENT_ABI_VERSION
from app.lib.cartography.composition_templates import (
    get_composition_template_registry,
)

# ── G3: webgis_map_product → layout.composition 身份块 ───────────────────

_POINTS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.06 + i * 0.01, 30.66]},
            "properties": {"name": f"school-{i}"},
        }
        for i in range(12)
    ],
}


@pytest.fixture
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    from app.agent_pi_bridge import set_tool_registry

    r = ToolRegistry()
    init_tools(r)
    set_tool_registry(r)
    return r


@pytest.fixture
async def sid():
    from app.services.mapspec.store import (
        BASE_STORAGE_DIR,
        mapspec_store_instance as mapspec_store,
    )
    from app.services.session_data import session_data_manager

    session_id = f"h02-g3-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)
    d = BASE_STORAGE_DIR / session_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    await mapspec_store.clear_session_files(session_id)


async def _run_product(sid: str, call_id: str = "h02-product"):
    from app.agent_pi_bridge import PiToolRequest, dispatch_tool, take_session_plan_sse
    from app.services.session_data import session_data_manager

    primary_ref = await session_data_manager.store(sid, _POINTS, prefix="geojson")
    res = await dispatch_tool(
        PiToolRequest(
            toolCallId=call_id,
            name="webgis_map_product",
            arguments={
                "query": "成都市小学分布情况",
                "primary_ref": primary_ref,
                "title": "成都市小学分布",
            },
            sessionId=sid,
        )
    )
    assert not res.isError, res.content
    take_session_plan_sse(call_id, sid)
    return res


async def test_map_product_writes_composition_identity(registry, sid):
    await _run_product(sid)
    from app.services.mapspec.store import mapspec_store_instance as mapspec_store

    spec = await mapspec_store.get_mapspec(sid) or {}
    identity = (spec.get("layout") or {}).get("composition")
    assert isinstance(identity, dict) and identity.get("template_id"), (
        "webgis_map_product 落组件但未写 layout.composition 身份块 —— "
        "物理 MapSpec 无法追溯产品组合模板"
    )
    reg = get_composition_template_registry()
    assert reg.get(str(identity.get("template_id"))) is not None, (
        "身份块 template_id 必须在组合模板注册表中"
    )
    assert identity.get("component_abi_version") == COMPONENT_ABI_VERSION


async def test_map_product_preserves_user_contract_identity(registry, sid):
    """用户显式应用的组合契约（contract_id 非空）不被 agent 再制图覆盖。"""
    from app.services.mapspec_store import mapspec_store

    await _run_product(sid, call_id="h02-first")
    spec = await mapspec_store.get_mapspec(sid) or {}
    identity = dict((spec.get("layout") or {}).get("composition") or {})
    identity["contract_id"] = "user-contract-1"
    res = await mapspec_store.layout_set(sid, composition=identity)
    assert res.get("success"), res

    await _run_product(sid, call_id="h02-second")
    spec2 = await mapspec_store.get_mapspec(sid) or {}
    identity2 = (spec2.get("layout") or {}).get("composition") or {}
    assert identity2.get("contract_id") == "user-contract-1", (
        "agent 再制图覆盖了用户显式组合契约身份（user-wins 违反）"
    )


# ── G4: required_slots 从物理 spec 身份块恢复真契约 ──────────────────────


def test_required_slots_recover_from_physical_spec_identity():
    from app.services.gis_harness.completion.inputs import (
        resolve_required_component_slots,
    )

    reg = get_composition_template_registry()
    # 任一含必需槽多于 title/scale_bar 的模板都可验证恢复语义
    tpl_id = "composition.standard_analysis"
    tpl = reg.get(tpl_id)
    assert tpl is not None
    chapter: dict = {"template_selection": {}}  # chapter 证据缺席
    spec: dict = {"layout": {"composition": {
        "template_id": tpl_id,
        "template_version": "1",
        "component_abi_version": COMPONENT_ABI_VERSION,
    }}}
    slots = resolve_required_component_slots(chapter, spec)
    expected = [
        [str(t) for t in (s.allowed_component_types or [])] or [str(s.id)]
        for s in tpl.component_slots if s.cardinality == "required"
    ]
    assert slots == expected, (
        "chapter 证据缺席时必须从物理 spec 身份块恢复真实必需槽契约，"
        f"而非弱兜底；期望 {expected}，实际 {slots}"
    )


def test_required_slots_fallback_when_identity_absent():
    from app.services.gis_harness.completion.inputs import (
        resolve_required_component_slots,
    )

    slots = resolve_required_component_slots({"template_selection": {}}, {})
    assert slots == [["title"], ["scale_bar"]], (
        "身份块与 chapter 证据都缺席时保留最小契约兜底（旧章节兼容）"
    )


# ── G5: 显式 export intents 触发 export 槽位裁决 ─────────────────────────


def test_explicit_export_intent_reselects_export_capable_composition():
    from app.services.gis_harness.intent import MapRequestIntent
    from app.services.gis_harness.planner_runtime import get_planner_runtime

    intent = MapRequestIntent(query="成都小学看看", export_intents=["pdf"])
    planner = get_planner_runtime()
    plan = planner.plan_from_intent(intent, template_id="simple_poi_view")
    plan = planner.finalize_with_profile(
        plan, {"geometry": "point", "feature_count": 50, "crs": "EPSG:4326"},
    )
    compo_id = str((plan.template_selection or {}).get("composition_template_id") or "")
    reg = get_composition_template_registry()
    tpl = reg.get(compo_id)
    assert tpl is not None, f"组合模板未注册：{compo_id}"
    assert any(
        s.id == "export_layout" and s.cardinality == "required"
        for s in tpl.component_slots
    ), (
        f"用户显式要求 pdf（export_intents）但接线组合 {compo_id} 缺必备 "
        "export_layout 槽 —— 非 report 产品也需要 export 槽位裁决"
    )


# ── G6: 产品语义层复跑 composition conformance ──────────────────────────


def _wiring_fixture(query: str = "成都各区小学数量统计"):
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlan, PlannedLayer
    from app.services.gis_harness.product_templates import (
        get_product_template_registry,
    )

    intent = resolve_map_request_intent(query)
    reg = get_product_template_registry()
    template = reg.find_for_recipe("administrative_choropleth", "")
    plan = MapProductPlan(
        plan_id="plan-h02-g6",
        query=query,
        intent=intent,
        recipe_id="administrative_choropleth",
        template_id=template.id if template else "",
        statistics=["admin_aggregation"],
        map_layers=[
            PlannedLayer(role="primary", layer_type="fill",
                         cartography="administrative_choropleth",
                         source_capability="admin_aggregation",
                         layer_id="lyr-admin", bound_ref="ref:primary"),
        ],
    )
    return plan, intent, template


def test_product_layer_surfaces_composition_conformance():
    from app.services.gis_harness.product_runtime import produce_product_layer

    plan, intent, template = _wiring_fixture()
    out = produce_product_layer(plan=plan, intent=intent, template=template,
                                primary_ref="ref:primary")
    assert "product_compile_fallback" not in out, out.get("product_compile_fallback")
    conformance = out.get("product_conformance")
    assert isinstance(conformance, dict), (
        "产品语义层不复跑 composition conformance —— export_parity_gap 等 "
        "error 级检查在产品编译面被绕过"
    )
    assert conformance.get("status") in {
        "pass", "fail", "warning", "not_evaluated",
    }, f"conformance 状态必须用既有词表，实际 {conformance.get('status')!r}"
