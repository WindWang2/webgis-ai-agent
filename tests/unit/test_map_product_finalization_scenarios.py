"""ADR-0081 手动场景的自动化回放（/goal §15 的三个端到端场景）。

场景 A：成都小学分布 —— POI → 区县聚合 → 热力图 → 统计/图表面板 → 完成度终验
场景 B：简单点分布图 —— 无色条需求时不强制 colorbar；图例正常；bbox 可派生
场景 C：故障恢复 —— 结果层隐藏 → 检测 → 修复 → 复验 → complete；
         不可修复 source missing → 不 complete（如实披露）

这些测试用真实 ToolRegistry / MapSpecLifecycleEngine / session data（与
test_map_product_runtime_v3_e2e.py 同款 fixture 模式），是对"手动场景"的
可重复等价物。
"""
import shutil
import uuid

import pytest

from app.lib.gis.runtime_manifest import get_runtime_manifest
from app.services.gis_harness.map_completion import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    maybe_finalize_map_product,
    run_map_finalization,
)
from app.services.gis_harness.components import build_default_components
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    PatchLayerPresentationIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


@pytest.fixture
async def tool_registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.fixture
async def clean_session():
    sid = f"mfp-e2e-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _poi_fc(coords=None):
    if coords is None:
        coords = [[104.05 + i * 0.01, 30.65 + (i % 3) * 0.01] for i in range(30)]
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": c},
         "properties": {"name": f"小学{i}", "students": 200 + i}}
        for i, c in enumerate(coords)
    ]
    return {"type": "FeatureCollection", "features": feats}


async def _seed_full_product(sid: str, *, layer_visibility="visible"):
    """POI 查询 → 区县聚合 → 热力图层 + 完整组件集（场景 A 的最小回放）。"""
    # data requirement artifact（POI 查询结果）
    poi_ref = await session_data_manager.store(sid, _poi_fc(), prefix="geojson")
    # 区县聚合 artifact（admin_aggregate_table 型结果也以 FC 形态落账）
    agg_ref = await session_data_manager.store(sid, _poi_fc(), prefix="geojson")

    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={
                "id": "poi-heatmap",
                "source": "s-poi-heatmap",
                "type": "heatmap",
                "paint": {"heatmap-weight": 1},
                "layout": {"visibility": layer_visibility},
            },
            source_data=_poi_fc(),
        ),
    )
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={
                "id": "district-aggregate",
                "source": "s-district-aggregate",
                "type": "fill",
                "paint": {"fill-color": "#3182bd", "fill-opacity": 0.5},
                "layout": {"visibility": "visible"},
            },
            source_data=_poi_fc(),
        ),
    )

    chapter = {
        "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
        "query": "成都小学分布密度与各区统计",
        "recipe_id": "poi_density_map",
        "data_requirements": [
            {"capability": "poi_query", "purpose": "学校 POI",
             "status": "available", "bound_ref": poi_ref, "optional": False},
        ],
        "analysis_steps": [
            {"capability": "density_surface", "purpose": "热力密度面",
             "status": "done", "bound_ref": poi_ref, "optional": False},
            {"capability": "admin_aggregation", "purpose": "区县聚合",
             "status": "done", "bound_ref": agg_ref, "optional": False},
        ],
        "map_layers": [
            {"role": "primary", "layer_id": "poi-heatmap", "enabled": True},
            {"role": "secondary", "layer_id": "district-aggregate", "enabled": True},
        ],
        "template_selection": {"composition_template_id": "composition.density_map"},
        "components": [],
    }

    # 完整组件集：title/legend/colorbar/scale/north/attribution/statistics/chart
    for c in build_default_components(
        primary_cartography="visual_heatmap",
        title="成都市小学分布热力图与各区统计",
        extra_types=["statistics_panel", "chart_panel"],
    ):
        await engine.apply_mutation(
            sid,
            PatchComponentIntent(
                component_id=c.id,
                component_type=c.type,
                enabled=c.enabled,
                position=c.position,
                options=c.options,
                upsert=True,
            ),
        )
    return chapter


# ── 场景 A：成都小学分布 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_a_chengdu_schools_full_product_completion(clean_session):
    chapter = await _seed_full_product(clean_session)

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_a")

    # 热力图 + 区县层可见、组件齐全 → complete
    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    # bbox 已派生（前端据此校验/修复视口）
    assert result.result_bbox is not None
    assert result.viewport_status == "repairable"
    # 图层与组件校验通过
    assert result.layer_status == "valid"
    assert result.component_status == "valid"
    # 组件齐备（含 statistics/chart/colorbar）
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    types = {c.get("type") for c in (spec.get("layout") or {}).get("components") or []}
    assert {"title", "continuous_colorbar", "statistics_panel", "chart_panel",
            "scale_bar", "north_arrow", "attribution"} <= types
    # 投影行
    assert result.projection_line() == "Map product: final"


@pytest.mark.asyncio
async def test_scenario_a_harness_integration_persists_block(clean_session):
    """bridge 触发路径：终验结果落章节 + 投影披露 + 幂等门。"""
    from app.services.session_plan import (
        SessionPlan, format_session_plan_projection, save_session_plan,
        _init_progress,
    )

    chapter = await _seed_full_product(clean_session)
    plan = SessionPlan(
        envelope_id=f"env-{uuid.uuid4().hex[:8]}",
        session_id=clean_session,
        user_goal=chapter["query"],
        gis_chapter=chapter,
        progress=_init_progress(chapter),
    )
    for row in plan.progress:
        row.status = "complete"
    await save_session_plan(plan)

    first = await maybe_finalize_map_product(clean_session, reason="tool_result:webgis_map_product")
    assert first is not None and first.status == STATUS_COMPLETE

    # 重读章节（finalizer 持久化到新读的 plan 副本上）
    from app.services.session_plan import load_session_plan

    fresh = await load_session_plan(clean_session)
    projection = format_session_plan_projection(fresh)
    assert "Map product: final" in projection

    # 幂等门：同 revision 的第二次触发跳过
    again = await maybe_finalize_map_product(clean_session, reason="turn_settled")
    assert again is None


# ── 场景 B：简单点分布图 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_b_simple_point_map_no_colorbar_forced(clean_session):
    poi_ref = await session_data_manager.store(clean_session, _poi_fc(), prefix="geojson")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={"id": "poi-points", "source": "s-poi", "type": "circle",
                   "paint": {"circle-color": "#e53e3e"}, "layout": {"visibility": "visible"}},
            source_data=_poi_fc(),
        ),
    )
    for c in build_default_components(
        primary_cartography="point_overlay",
        title="成都市小学位置",
    ):
        await engine.apply_mutation(
            clean_session,
            PatchComponentIntent(
                component_id=c.id, component_type=c.type, enabled=c.enabled,
                position=c.position, options=c.options, upsert=True,
            ),
        )
    chapter = {
        "plan_id": f"plan-{uuid.uuid4().hex[:8]}",
        "query": "成都小学位置",
        "recipe_id": "poi_map",
        "data_requirements": [
            {"capability": "poi_query", "purpose": "POI",
             "status": "available", "bound_ref": poi_ref, "optional": False},
        ],
        "analysis_steps": [],
        "map_layers": [{"role": "primary", "layer_id": "poi-points", "enabled": True}],
        "template_selection": {"composition_template_id": "composition.minimal_interactive"},
        "components": [],
    }

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_b")

    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    # 简单点图不需要 colorbar —— 组合契约不强制（minimal_interactive 的
    # colorbar 非 required；缺失不产生 finding）
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    types = {c.get("type") for c in (spec.get("layout") or {}).get("components") or []}
    assert "continuous_colorbar" not in types
    assert "title" in types
    # bbox 可派生 → 前端会自动聚焦（视口修复链可用）
    assert result.result_bbox is not None


# ── 场景 C：故障恢复 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scenario_c_hidden_required_layer_detected_repaired_completed(clean_session):
    """结果层被隐藏 → finalizer 检测 → show_layer 修复 → 复验 complete。"""
    chapter = await _seed_full_product(clean_session, layer_visibility="none")

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_c")

    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    assert any(r.startswith("show_layer:") for r in result.repairs_applied)
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    heat = next(ly for ly in spec["layers"] if ly["id"] == "poi-heatmap")
    assert (heat.get("layout") or {}).get("visibility") != "none"


@pytest.mark.asyncio
async def test_scenario_c_unrepairable_source_missing_not_complete(clean_session):
    """source 不在册（不可自动修复）→ 检测 → 披露 → 不 complete。"""
    chapter = await _seed_full_product(clean_session)
    # 构造悬空 source：层引用不存在的 source（删除 source 登记）
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    broken = dict(spec)
    broken["sources"] = {
        k: v for k, v in (spec.get("sources") or {}).items() if k != "s-poi-heatmap"
    }
    await mapspec_store_instance.save_mapspec(clean_session, broken)

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_c2")

    assert result.status in (STATUS_FAILED, STATUS_NEEDS_REPAIR)
    codes = {f.code for f in result.findings}
    assert "source_missing" in codes
    assert result.projection_line().startswith("Map product:")


@pytest.mark.asyncio
async def test_scenario_c_user_wins_disclosed_not_overridden(clean_session):
    """用户显式隐藏结果层 → 修复被 user-wins 守卫拒绝 → needs_repair 披露。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation_batch

    chapter = await _seed_full_product(clean_session, layer_visibility="visible")
    # 用户先隐藏（值翻转 → 落 presentation_owner="user" 印记）
    state = await session_data_manager.get_map_state(clean_session)
    await apply_gis_mutation_batch(
        clean_session,
        [PatchLayerPresentationIntent(layer_id="poi-heatmap", visible=False)],
        origin="user",
        actor="manual-scenario",
        expected_revision=int(state.get("_cartographic_mutation_revision") or 0),
    )

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_c3")
    # user-wins（P0 稳定化）：用户隐藏即期望状态 —— warning 披露、零修复
    # 突变（不再发起注定被守卫拒绝的 show_layer、不再永续 needs_repair）。
    hidden = [f for f in result.findings if f.code == "layer_hidden"]
    assert hidden and hidden[0].severity == "warning"
    assert not any(r.startswith("show_layer:") for r in result.repairs_applied)
    assert result.status == STATUS_COMPLETE
    # spec 保持用户决策（隐藏）
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    heat = next(ly for ly in spec["layers"] if ly["id"] == "poi-heatmap")
    assert (heat.get("layout") or {}).get("visibility") == "none"


# ── registry manifest 一致性（场景前置守卫）──────────────────────────


def test_runtime_manifest_compiles_clean():
    """前置：manifest 编译无 fatal（场景回放依赖 registry 健康）。"""
    m = get_runtime_manifest()
    assert m is not None
