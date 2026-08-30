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


# ── P9 场景 H–M：render-observed product closure ─────────────────────


def _matching_observation(sid: str, revision: int, *, layer_count: int = 2,
                          visible: bool = True, seq: int = 1, components=None):
    """与 _seed_full_product 的结果层匹配的 render observation（有界）。"""
    return {
        "session_id": sid,
        "sequence": seq,
        "client_generation": 1000 + seq,
        "source": "frontend_runtime",
        "mapspec_fingerprint": "fp-scenario-aaaaaaaa",
        "mapspec_revision": revision,
        "layer_count": 2,
        "layers": [
            {"id": "poi-heatmap", "runtime_layer_count": layer_count,
             "visible": visible, "source_converged": True, "style_converged": True},
            {"id": "district-aggregate", "runtime_layer_count": layer_count,
             "visible": visible, "source_converged": True, "style_converged": True},
        ],
        "viewport": {"zoom": 9},
        "style_loaded": True,
        "reconcile_error": "",
        "components": components if components is not None else [
            {"id": "title", "type": "title", "enabled": True, "mounted": True},
            {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True, "mounted": True},
            {"id": "north-arrow", "type": "north_arrow", "enabled": True, "mounted": True},
            {"id": "scale-bar", "type": "scale_bar", "enabled": True, "mounted": True},
            {"id": "attribution", "type": "attribution", "enabled": True, "mounted": True},
            {"id": "statistics", "type": "statistics_panel", "enabled": True, "mounted": True},
            {"id": "chart-panel", "type": "chart_panel", "enabled": True, "mounted": True},
        ],
        "runtime_errors": [],
        "map_idle": True,
    }


async def _current_revision(sid: str) -> int:
    state = await session_data_manager.get_map_state(sid)
    return int(state.get("_cartographic_mutation_revision") or 0)


@pytest.mark.asyncio
async def test_scenario_h_render_observed_success(clean_session):
    """H：MapSpec committed → frontend 挂载全部结果层 → observation 匹配
    revision → facet 全完成 → map product final（render verified）。"""
    chapter = await _seed_full_product(clean_session)
    rev = await _current_revision(clean_session)
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_observation",
        _matching_observation(clean_session, rev),
    )

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_h")

    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    assert result.render_status == "verified"
    render_codes = {f.code for f in result.findings if f.code.startswith("render_")}
    assert not render_codes
    # facet 级：全部产品 facet complete（render 证据参与）
    from app.services.gis_harness.product_graph import (
        FS_COMPLETE,
        KIND_MAP_LAYER,
        build_facet_completion,
    )
    obs = await session_data_manager.get_map_state(clean_session)
    facets = build_facet_completion(
        chapter,
        await mapspec_store_instance.get_mapspec(clean_session),
        observation=obs.get("_cartographic_observation"),
        current_revision=rev,
    )
    layer_facets = [f for f in facets if f.kind == KIND_MAP_LAYER]
    assert layer_facets and all(
        f.status == FS_COMPLETE and f.render_status == "verified" for f in layer_facets
    )


@pytest.mark.asyncio
async def test_scenario_i_mapspec_correct_runtime_layer_missing(clean_session):
    """I：MapSpec/armed/组件全部正确，但 MapLibre 层未挂载 → render
    finding → 产品不得静默宣称 fully verified（needs_repair）。"""
    chapter = await _seed_full_product(clean_session)
    rev = await _current_revision(clean_session)
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_observation",
        _matching_observation(clean_session, rev, layer_count=0),
    )

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_i")

    # desired-state 全过，但渲染级缺席 → needs_repair（非 complete 非 failed）
    assert result.status == STATUS_NEEDS_REPAIR
    assert result.render_status == "issues"
    assert any(f.code == "render_layer_missing" for f in result.findings)


@pytest.mark.asyncio
async def test_scenario_j_stale_observation_cannot_validate(clean_session):
    """J：spec rev N、observation rev N-1 → stale → 不 false verified。"""
    chapter = await _seed_full_product(clean_session)
    rev = await _current_revision(clean_session)
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_observation",
        _matching_observation(clean_session, rev - 1),
    )

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_j")

    assert result.status == STATUS_COMPLETE  # desired-state 仍通过（向后兼容）
    assert result.render_status == "stale"
    assert any(f.code == "render_revision_stale" for f in result.findings)
    assert result.projection_line() == "Map product: final (render:stale)"


@pytest.mark.asyncio
async def test_scenario_k_style_reload_replay_regenerates_observation(clean_session):
    """K：style reload → RuntimeLayerRegistry 重放 → observation 再生成
    （seq 前进、同 revision、同可见性）→ 幂等门被打破 → 重验 complete。"""
    import uuid as _uuid

    from app.services.gis_harness.map_completion import maybe_finalize_map_product
    from app.services.session_plan import (
        SessionPlan,
        load_session_plan,
        save_session_plan,
    )

    chapter = await _seed_full_product(clean_session)
    await save_session_plan(SessionPlan(
        envelope_id=f"env-{_uuid.uuid4().hex[:8]}",
        session_id=clean_session,
        user_goal=str(chapter.get("query") or "scenario-k"),
        gis_chapter=chapter,
    ))
    rev = await _current_revision(clean_session)
    obs1 = _matching_observation(clean_session, rev, seq=1)
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_observation", obs1
    )
    first = await maybe_finalize_map_product(clean_session, reason="scenario_k_pre")
    assert first is not None and first.status == STATUS_COMPLETE
    plan = await load_session_plan(clean_session)
    stored = plan.gis_chapter["map_product"]
    assert stored["render_observation_seq"] == 1

    # style reload → 重放 → 前端重新采集（同内容、seq 前进）
    obs2 = _matching_observation(clean_session, rev, seq=2)
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_observation", obs2
    )
    # 无其它变化：仅 render seq 变化也必须打破去重门（P9 升级路径）
    second = await maybe_finalize_map_product(clean_session, reason="scenario_k_post")
    assert second is not None, "render seq gate must break dedup on new observation"
    plan = await load_session_plan(clean_session)
    assert plan.gis_chapter["map_product"]["render_observation_seq"] == 2
    assert plan.gis_chapter["map_product"]["status"] == STATUS_COMPLETE


@pytest.mark.asyncio
async def test_scenario_l_user_hidden_layer_no_repair_fight(clean_session):
    """L：用户隐藏层 → observation 如实报告 hidden → user-wins：不自动
    重开、render 校验不与用户对抗、无修复循环。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation_batch

    chapter = await _seed_full_product(clean_session, layer_visibility="visible")
    state = await session_data_manager.get_map_state(clean_session)
    await apply_gis_mutation_batch(
        clean_session,
        [PatchLayerPresentationIntent(layer_id="poi-heatmap", visible=False)],
        origin="user",
        actor="manual-scenario-l",
        expected_revision=int(state.get("_cartographic_mutation_revision") or 0),
    )
    rev = await _current_revision(clean_session)
    # observation 与新 revision 匹配：热力层 runtime 也如实隐藏
    obs = _matching_observation(clean_session, rev)
    obs["layers"][0]["visible"] = False
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_observation", obs
    )

    result = await run_map_finalization(clean_session, chapter=chapter, reason="scenario_l")

    # user-wins：隐藏是合法期望态（warning 披露），无渲染层缺席误报
    assert not any(f.code == "render_layer_missing" for f in result.findings)
    hidden = [f for f in result.findings if f.code == "layer_hidden"]
    assert hidden and hidden[0].severity == "warning"
    assert not any(r.startswith("show_layer:") for r in result.repairs_applied)
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    heat = next(ly for ly in spec["layers"] if ly["id"] == "poi-heatmap")
    assert (heat.get("layout") or {}).get("visibility") == "none"


@pytest.mark.asyncio
async def test_scenario_m_missing_chart_facet_next_action(clean_session):
    """M：地图 facets 全完成、chart required 但缺席 → 产品图显示 chart
    owed → next action = chart 产出通道（capability 层语义，不 shortcut
    到 tool）。"""
    from app.services.gis_harness.product_action import advise_next_product_action
    from app.services.gis_harness.product_graph import (
        FS_PENDING,
        KIND_CHART,
        build_facet_completion,
    )

    chapter = await _seed_full_product(clean_session)
    chapter["template_selection"]["export_profile"] = {"formats": ["png"], "chart": True}
    spec = await mapspec_store_instance.get_mapspec(clean_session)
    # chart 组件缺席（场景：agent 尚未产出图表）
    spec["layout"]["components"] = [
        c for c in spec["layout"]["components"] if c.get("type") != "chart_panel"
    ]

    facets = build_facet_completion(chapter, spec)
    chart = [f for f in facets if f.kind == KIND_CHART]
    assert chart and chart[0].facet_id == "chart:required"
    assert chart[0].status == FS_PENDING

    rec = advise_next_product_action(chapter, facets)
    assert rec is not None
    assert rec.action == "produce_chart"
    assert rec.capability == ""  # 不把 tool id 冒充 capability

    # [Products] 投影行点名 chart owed
    from app.services.gis_harness.product_graph import build_product_graph
    line = build_product_graph(chapter, spec).summary_line()
    assert "chart 0/1" in line and "chart owed" in line
