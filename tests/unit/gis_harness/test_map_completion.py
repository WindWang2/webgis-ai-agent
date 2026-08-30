"""Map Product Completion Runtime（ADR-0081）单元/契约测试。

覆盖：
- DAG 终态门（mandatory pending/failed → needs_execution；optional 不阻塞）；
- artifact 校验（未绑定 / ref 过期 / 空结果语义）；
- layer 校验（结果层缺失 / source 缺失 / 结果层隐藏[可修复]）；
- component 校验（required 缺失→可修复 / 禁用→可修复 / 单例重复 / 孤儿绑定）；
- layout 校验（floating 重叠 warning；user-pinned 只披露不挪动）；
- bbox 派生（并集 / 缺失 / 退化跳过）；
- 有界 repair 回路（补组件→complete；不可修复→failed 且轮数有界）；
- show_layer repair 走 GISMutationBatch（user-wins 守卫生效）；
- maybe_finalize 幂等门 + 章节 map_product 块 + 投影行；
- SSE payload 有界。
"""
import shutil
import uuid

import pytest

from app.services.gis_harness.map_completion import (
    F_ARTIFACT_EXPIRED,
    F_ARTIFACT_MISSING,
    F_COMPONENT_DISABLED,
    F_COMPONENT_MISSING,
    F_EMPTY_RESULT,
    F_EXECUTION_BLOCKED,
    F_LAYER_HIDDEN,
    F_LAYER_MISSING,
    F_LAYOUT_CONFLICT,
    F_NEEDS_EXECUTION,
    F_NO_RESULT_LAYER,
    F_ORPHAN_BINDING,
    F_SOURCE_MISSING,
    F_VIEWPORT_NO_BBOX,
    MAX_FINALIZATION_PASSES,
    R_ADD_COMPONENT,
    R_ENABLE_COMPONENT,
    R_SHOW_LAYER,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    STATUS_PENDING,
    MapCompletionFinding,
    MapCompletionResult,
    assess_export_parity,
    derive_result_bbox,
    finalization_sse_payload,
    map_product_block,
    maybe_finalize_map_product,
    read_stored_map_product,
    run_map_finalization,
    validate_artifacts,
    validate_components,
    validate_execution,
    validate_layers,
    validate_layout,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    SessionPlan,
    format_session_plan_projection,
    save_session_plan,
)


@pytest.fixture
async def clean_session():
    sid = f"mcf-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _geojson(coords=None):
    feats = []
    if coords is None:
        coords = [[104.0, 30.6], [104.1, 30.7]]
    for c in coords:
        feats.append(
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": c},
             "properties": {}}
        )
    return {"type": "FeatureCollection", "features": feats}


def _chapter(
    *,
    req_status="available",
    step_status="done",
    bound_ref="ref:geojson-x",
    layer_id="poi-main",
    optional=False,
):
    return {
        "plan_id": "plan-test",
        "query": "成都小学分布",
        "recipe_id": "poi_density",
        "data_requirements": [
            {
                "capability": "poi_query",
                "purpose": "POI",
                "status": req_status,
                "bound_ref": bound_ref if req_status == "available" else "",
                "optional": optional,
            }
        ],
        "analysis_steps": [
            {
                "capability": "density_surface",
                "purpose": "density",
                "status": step_status,
                "bound_ref": bound_ref if step_status == "done" else "",
                "optional": optional,
            }
        ],
        "map_layers": [
            {"role": "primary", "layer_id": layer_id, "enabled": True}
        ],
        "components": [],
        "template_selection": {},
    }


async def _seed_mapspec(
    sid: str,
    *,
    layer_id="poi-main",
    visibility="visible",
    components=None,
):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={
                "id": layer_id,
                "source": f"s-{layer_id}",
                "type": "circle",
                "paint": {"circle-color": "#00f"},
                "layout": {"visibility": visibility},
            },
            source_data=_geojson(),
        ),
    )
    for comp in (components if components is not None else _default_components()):
        await engine.apply_mutation(sid, PatchComponentIntent(
            component_id=comp["id"],
            component_type=comp["type"],
            enabled=comp.get("enabled", True),
            position=comp.get("position", "none"),
            upsert=True,
        ))
    from app.services.mapspec_store import mapspec_store

    return await mapspec_store.get_mapspec(sid)


def _default_components():
    return [
        {"id": "title", "type": "title", "position": "top-center",
         "options": {"text": "成都小学分布"}},
        {"id": "scale-bar", "type": "scale_bar", "position": "bottom-right"},
    ]


async def _store_ref(sid: str, payload=None):
    return await session_data_manager.store(
        sid, payload if payload is not None else _geojson(), prefix="geojson"
    )


# ── DAG 终态门 ────────────────────────────────────────────────────────


def test_pending_mandatory_blocks_finalization():
    findings = validate_execution(_chapter(req_status="pending"))
    assert findings and findings[0].code == F_NEEDS_EXECUTION


def test_failed_mandatory_blocks_and_discloses_retry():
    # blocked（failed/unavailable 终态行）与 open（pending）分码披露 ——
    # blocked 不会被 pending 吞掉（review H-3）。
    findings = validate_execution(_chapter(step_status="failed"))
    assert any(f.code == F_EXECUTION_BLOCKED for f in findings)
    assert not any(f.code == F_NEEDS_EXECUTION for f in findings)


def test_optional_unavailable_does_not_block():
    # optional 行 unavailable/skipped 不产生 needs_execution（图投影里
    # optional 节点 skipped 级联，不阻塞 mandatory 图）
    chapter = _chapter(req_status="available", step_status="skipped", optional=True)
    chapter["analysis_steps"][0]["status"] = "unavailable"
    # optional=True 的行不进 mandatory 断言
    findings = validate_execution(chapter)
    assert findings == []


def test_terminal_chapter_passes_gate():
    assert validate_execution(_chapter()) == []


# ── artifact 校验 ─────────────────────────────────────────────────────


def test_artifact_missing_when_complete_row_has_no_ref():
    chapter = _chapter()
    chapter["data_requirements"][0]["bound_ref"] = ""
    findings = validate_artifacts(chapter, {})
    assert any(f.code == F_ARTIFACT_MISSING for f in findings)


def test_artifact_expired_when_ref_absent_from_store():
    findings = validate_artifacts(_chapter(), {"ref:geojson-x": None})
    assert any(f.code == F_ARTIFACT_EXPIRED for f in findings)


def test_empty_result_has_explicit_semantics():
    findings = validate_artifacts(
        _chapter(), {"ref:geojson-x": {"feature_count": 0, "bbox": None}}
    )
    assert any(f.code == F_EMPTY_RESULT for f in findings)


def test_optional_row_artifact_gap_not_fatal():
    chapter = _chapter(optional=True)
    chapter["data_requirements"][0]["bound_ref"] = ""
    findings = validate_artifacts(chapter, {})
    assert findings == []


# ── layer 校验 ────────────────────────────────────────────────────────


def test_layer_missing_when_planned_result_layer_absent():
    mapspec = {"layers": [], "sources": []}
    findings = validate_layers(_chapter(), mapspec)
    assert any(f.code == F_LAYER_MISSING for f in findings)


def test_source_missing_finding():
    mapspec = {
        "layers": [{"id": "poi-main", "source": "s-poi-main", "type": "circle"}],
        "sources": [],
    }
    findings = validate_layers(_chapter(), mapspec)
    assert any(f.code == F_SOURCE_MISSING for f in findings)


def test_hidden_result_layer_is_repairable():
    mapspec = {
        "layers": [
            {
                "id": "poi-main",
                "source": "s-poi-main",
                "type": "circle",
                "layout": {"visibility": "none"},
            }
        ],
        "sources": [{"id": "s-poi-main"}],
    }
    findings = validate_layers(_chapter(), mapspec)
    hidden = [f for f in findings if f.code == F_LAYER_HIDDEN]
    assert hidden and hidden[0].repair == R_SHOW_LAYER


def test_layer_missing_for_bound_planned_layer():
    mapspec = {
        "layers": [{"id": "other", "source": "s-other", "type": "circle"}],
        "sources": [{"id": "s-other"}],
    }
    findings = validate_layers(_chapter(layer_id="poi-main"), mapspec)
    assert any(f.code == F_LAYER_MISSING for f in findings)


# ── component 校验 ────────────────────────────────────────────────────


def _spec_with_components(components):
    return {"layers": [{"id": "poi-main", "source": "s", "type": "circle"}],
            "sources": [{"id": "s"}],
            "layout": {"components": components}}


def test_required_component_missing_is_repairable():
    findings = validate_components(
        _spec_with_components([{"id": "title", "type": "title"}]),
        required_slots=[["title"], ["scale_bar"]],
        layer_ids=["poi-main"],
    )
    missing = [f for f in findings if f.code == F_COMPONENT_MISSING]
    assert missing and missing[0].target == "scale_bar"
    assert missing[0].repair == R_ADD_COMPONENT


def test_required_component_disabled_is_repairable():
    findings = validate_components(
        _spec_with_components(
            [{"id": "title", "type": "title", "enabled": False}]
        ),
        required_slots=[["title"]],
        layer_ids=["poi-main"],
    )
    disabled = [f for f in findings if f.code == F_COMPONENT_DISABLED]
    assert disabled and disabled[0].repair == "enable_component"


def test_optional_component_missing_does_not_fail():
    findings = validate_components(
        _spec_with_components([{"id": "title", "type": "title"}]),
        required_slots=[["title"]],
        layer_ids=["poi-main"],
    )
    assert [f for f in findings if f.severity == "error"] == []


def test_singleton_duplicate_warns():
    findings = validate_components(
        _spec_with_components(
            [
                {"id": "t1", "type": "title", "options": {"text": "a"}},
                {"id": "t2", "type": "title", "options": {"text": "b"}},
            ]
        ),
        required_slots=[["title"]],
        layer_ids=[],
    )
    assert any(f.code == F_LAYOUT_CONFLICT and f.severity == "warning" for f in findings)


def test_orphan_layer_binding_warns():
    findings = validate_components(
        _spec_with_components(
            [{"id": "legend-main", "type": "legend",
              "options": {"layerId": "gone"}}]
        ),
        required_slots=[],
        layer_ids=["poi-main"],
    )
    assert any(f.code == F_ORPHAN_BINDING for f in findings)


# ── layout 校验 ───────────────────────────────────────────────────────


def test_floating_overlap_warns():
    mapspec = {
        "layers": [],
        "layout": {
            "components": [
                {"id": "chart", "type": "chart_panel",
                 "placement": {"mode": "floating", "x": 10, "y": 10, "width": 200, "height": 150}},
                {"id": "stats", "type": "statistics_panel",
                 "placement": {"mode": "floating", "x": 50, "y": 50, "width": 200, "height": 150}},
            ]
        },
    }
    findings = validate_layout(mapspec)
    assert any(f.code == F_LAYOUT_CONFLICT for f in findings)


def test_floating_disjoint_no_finding():
    mapspec = {
        "layers": [],
        "layout": {
            "components": [
                {"id": "chart", "type": "chart_panel",
                 "placement": {"mode": "floating", "x": 0, "y": 0, "width": 100, "height": 100}},
                {"id": "stats", "type": "statistics_panel",
                 "placement": {"mode": "floating", "x": 500, "y": 500, "width": 100, "height": 100}},
            ]
        },
    }
    assert validate_layout(mapspec) == []


def test_layout_repair_never_moves_user_pinned():
    # user-pinned（floating）重叠只披露 —— finding 不携带 repair
    mapspec = {
        "layers": [],
        "layout": {
            "components": [
                {"id": "a", "type": "chart_panel",
                 "placement": {"mode": "floating", "x": 0, "y": 0, "width": 100, "height": 100}},
                {"id": "b", "type": "statistics_panel",
                 "placement": {"mode": "floating", "x": 50, "y": 50, "width": 100, "height": 100}},
            ]
        },
    }
    findings = validate_layout(mapspec)
    assert all(f.repair is None for f in findings)


# ── bbox 派生 ─────────────────────────────────────────────────────────


def test_derive_result_bbox_unions_descriptors():
    chapter = _chapter(bound_ref="ref:a")
    descriptors = {
        "ref:a": {"feature_count": 2, "bbox": [104.0, 30.6, 104.1, 30.7]},
        "ref:b": {"feature_count": 1, "bbox": [103.0, 30.0, 103.5, 30.2]},
    }
    # 只有 bound ref 参与
    assert derive_result_bbox(chapter, descriptors) == [104.0, 30.6, 104.1, 30.7]


def test_derive_result_bbox_none_when_missing():
    assert derive_result_bbox(_chapter(), {"ref:geojson-x": {"feature_count": 1, "bbox": None}}) is None
    assert derive_result_bbox(_chapter(bound_ref=""), {}) is None


def test_derive_result_bbox_skips_degenerate():
    descriptors = {"ref:geojson-x": {"feature_count": 1, "bbox": [10.0, 10.0, 9.0, 11.0]}}
    assert derive_result_bbox(_chapter(), descriptors) is None


# ── export parity 判定 ────────────────────────────────────────────────


def test_export_parity_uses_support_matrix():
    assert assess_export_parity({}) == "parity"
    spec = _spec_with_components([{"id": "title", "type": "title"}])
    assert assess_export_parity(spec) == "parity"


# ── 有界 repair 回路（端到端，真实 MapSpec 突变通道）──────────────────


@pytest.mark.asyncio
async def test_repair_adds_missing_component_then_completes(clean_session):
    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session, components=[_default_components()[0]])  # 只有 title
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    assert any(r.startswith(f"{R_ADD_COMPONENT}:") for r in result.repairs_applied)
    # 组件确实落盘
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(clean_session)
    types = {c.get("type") for c in (spec.get("layout") or {}).get("components") or []}
    assert "scale_bar" in types


@pytest.mark.asyncio
async def test_unrepairable_finding_fails_with_bounded_passes(clean_session):
    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    # 有 planned result layer 但 spec 无此层（source_missing/layer_missing 不可修复）
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_FAILED
    codes = {f.code for f in result.findings}
    assert F_LAYER_MISSING in codes or F_NO_RESULT_LAYER in codes
    assert result.passes <= MAX_FINALIZATION_PASSES


@pytest.mark.asyncio
async def test_hidden_result_layer_repaired_via_mutation_batch(clean_session):
    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session, visibility="none")
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    assert any(r.startswith(f"{R_SHOW_LAYER}:") for r in result.repairs_applied)
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(clean_session)
    layer = next(ly for ly in spec["layers"] if ly["id"] == "poi-main")
    assert (layer.get("layout") or {}).get("visibility") != "none"


@pytest.mark.asyncio
async def test_user_hidden_layer_is_respected_not_overridden(clean_session):
    """user-wins：用户显式隐藏的结果层，隐藏就是期望状态 —— 降级 warning
    披露、不发起注定被拒的修复突变（否则每个触发点都重放一次被守卫拒绝
    的 GISMutationBatch + 永续 needs_repair + 重复 toast，review B-6/H-6）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation_batch
    from app.services.mapspec.lifecycle_engine import PatchLayerPresentationIntent

    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session, visibility="visible")
    # 用户显式隐藏（值翻转 → 提交并落 presentation_owner="user" 印记；
    # user 路径要求 CAS expected_revision）
    state = await session_data_manager.get_map_state(clean_session)
    await apply_gis_mutation_batch(
        clean_session,
        [PatchLayerPresentationIntent(layer_id="poi-main", visible=False)],
        origin="user",
        actor="test-user",
        expected_revision=int(state.get("_cartographic_mutation_revision") or 0),
    )
    result = await run_map_finalization(clean_session, chapter=chapter)
    hidden = [f for f in result.findings if f.code == F_LAYER_HIDDEN]
    assert hidden and hidden[0].severity == "warning"
    assert hidden[0].repair is None  # 用户决策：不修复、不对抗
    assert not any(r.startswith(f"{R_SHOW_LAYER}:") for r in result.repairs_applied)
    # 用户的隐藏即产品状态 → warning 不阻塞 complete
    assert result.status == STATUS_COMPLETE


@pytest.mark.asyncio
async def test_agent_hidden_layer_still_repaired(clean_session):
    """system/agent 隐藏（无 user owner 印记）仍是可修复 error ——
    user-wins 降级只认 presentation_owner=="user"。"""
    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    spec = await _seed_mapspec(clean_session, visibility="none")
    layer = next(ly for ly in spec["layers"] if ly["id"] == "poi-main")
    assert (layer.get("cartographic_intent") or {}).get("presentation_owner") != "user"
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert any(r.startswith(f"{R_SHOW_LAYER}:") for r in result.repairs_applied)


@pytest.mark.asyncio
async def test_pending_dag_returns_pending_without_validation(clean_session):
    chapter = _chapter(req_status="pending")
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_PENDING
    assert all(f.code == F_NEEDS_EXECUTION for f in result.findings)


@pytest.mark.asyncio
async def test_failed_capability_does_not_finalize_then_retry_completes(clean_session):
    chapter = _chapter(step_status="failed")
    result = await run_map_finalization(clean_session, chapter=chapter)
    # blocked-only（failed 终态、无 open 行）：DAG 不会自愈 —— 按 failed
    # 披露（pending 会被静默吞掉，turn 结束零产品级披露，review H-3）。
    assert result.status == STATUS_FAILED
    assert any(f.code == F_EXECUTION_BLOCKED for f in result.findings)
    assert "Map product: incomplete" in result.projection_line()

    # 重试成功：行翻 complete + artifact 绑定 → 可终验
    ref = await _store_ref(clean_session)
    chapter["analysis_steps"][0]["status"] = "done"
    chapter["analysis_steps"][0]["bound_ref"] = ref
    chapter["data_requirements"][0]["bound_ref"] = ref
    await _seed_mapspec(clean_session)
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]


@pytest.mark.asyncio
async def test_open_and_blocked_rows_stay_pending(clean_session):
    """open（pending）+ blocked（failed）并存 → 仍 pending 抑制（turn 中段，
    blocked 披露等行终态后由 settle 触发）。"""
    chapter = _chapter(req_status="pending", step_status="failed")
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_PENDING
    assert any(f.code == F_NEEDS_EXECUTION for f in result.findings)
    assert any(f.code == F_EXECUTION_BLOCKED for f in result.findings)


@pytest.mark.asyncio
async def test_empty_result_disclosed_as_warning_not_failure(clean_session):
    """合法空结果（范围内无匹配）是正确答案 —— 披露但不 failed、不诱导
    重试（review B-7/H-4）；Pi 依据 finding 决定向用户如何说明。"""
    ref = await _store_ref(clean_session, payload=_geojson(coords=[]))
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    result = await run_map_finalization(clean_session, chapter=chapter)
    empty = [f for f in result.findings if f.code == F_EMPTY_RESULT]
    assert empty and empty[0].severity == "warning"
    assert result.status == STATUS_COMPLETE


# ── maybe_finalize：幂等门 + 章节块 + 投影 ───────────────────────────


async def _save_plan(sid: str, chapter: dict) -> SessionPlan:
    from app.services.session_plan import _init_progress

    plan = SessionPlan(
        envelope_id=f"env-{uuid.uuid4().hex[:8]}",
        session_id=sid,
        user_goal=chapter.get("query") or "q",
        gis_chapter=chapter,
        progress=_init_progress(chapter),
    )
    for row in plan.progress:
        row.status = "complete"
    await save_session_plan(plan)
    return plan


@pytest.mark.asyncio
async def test_maybe_finalize_persists_block_and_projection_line(clean_session):
    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)

    result = await maybe_finalize_map_product(clean_session, reason="tool_result:t")
    assert result is not None
    assert result.status == STATUS_COMPLETE

    from app.services.session_plan import load_session_plan

    plan = await load_session_plan(clean_session)
    block = plan.gis_chapter.get("map_product")
    assert isinstance(block, dict) and block["status"] == STATUS_COMPLETE
    assert block["projection"] == "Map product: final"
    projection = format_session_plan_projection(plan)
    assert "Map product: final" in projection

    # 幂等门：同一 desired state（revision 未变）不重复终验
    again = await maybe_finalize_map_product(clean_session, reason="tool_result:t2")
    assert again is None


@pytest.mark.asyncio
async def test_maybe_finalize_no_chapter_returns_none(clean_session):
    assert await maybe_finalize_map_product(clean_session) is None


@pytest.mark.asyncio
async def test_maybe_finalize_reruns_after_spec_mutation(clean_session):
    """spec 变化（revision 推进）后，去重门失效 → 重新终验。"""
    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)

    first = await maybe_finalize_map_product(clean_session)
    assert first is not None and first.status == STATUS_COMPLETE

    # 用户把 title 组件禁用 → revision 变化 → 重新终验发现 component_disabled 并修复
    from app.services.mapspec_store import mapspec_store

    await mapspec_store.patch_component(
        clean_session, component_id="title", component_type="title", enabled=False
    )
    second = await maybe_finalize_map_product(clean_session, reason="turn_settled")
    assert second is not None
    assert second.status == STATUS_COMPLETE
    assert any("enable_component" in r for r in second.repairs_applied)


# ── P0 稳定化：secondary 角色 / raster 通道 / 降级 / 修复记忆 / 指纹门 ─


def test_secondary_role_result_layer_validated():
    """planner 的 role 词表是 primary|secondary|reference —— secondary 是
    结果通道（行政区 choropleth 等）；漏掉会让副结果层逃逸校验（H-1）。"""
    chapter = _chapter()
    chapter["map_layers"].append(
        {"role": "secondary", "layer_id": "district-choropleth", "enabled": True}
    )
    mapspec = {
        "layers": [{"id": "poi-main", "source": "s", "type": "circle"}],
        "sources": [{"id": "s"}],
    }
    findings = validate_layers(chapter, mapspec)
    assert any(
        f.code == F_LAYER_MISSING and f.target == "district-choropleth"
        for f in findings
    )


def test_density_surface_without_ref_is_not_missing_artifact():
    """density_surface 的 raster 渲染通道（density.visual.heatmap →
    heatmap_data）不落 FC ref —— 无 bound_ref 是合法完成形态，不得误判
    artifact_missing → 假 failed（review C-1）。绑定了 ref 时仍校验。"""
    chapter = _chapter()
    chapter["analysis_steps"][0]["bound_ref"] = ""  # raster 通道：无 FC ref
    findings = validate_artifacts(chapter, {})
    assert not any(f.code == F_ARTIFACT_MISSING for f in findings)

    # vector 通道：绑了 ref 但 store 已无 descriptor → 照常过期披露
    chapter["analysis_steps"][0]["bound_ref"] = "ref:geojson-x"
    findings = validate_artifacts(chapter, {"ref:geojson-x": None})
    assert any(f.code == F_ARTIFACT_EXPIRED for f in findings)


@pytest.mark.asyncio
async def test_pending_regression_demotes_stored_complete_block(clean_session):
    """已存 complete 的章节出现新的 open 行（新增需求/重试重置）→ 陈旧
    "final" 投影必须收回：落降级 pending 块（review A-2/B-3）。"""
    from app.services.session_plan import load_session_plan

    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)
    first = await maybe_finalize_map_product(clean_session)
    assert first is not None and first.status == STATUS_COMPLETE

    # 新增 open 行（新数据需求，pending）→ 回归（在含 map_product 块的
    # 最新章节上改，避免覆写丢块）
    from app.services.session_plan import load_session_plan as _load

    plan = await _load(clean_session)
    plan.gis_chapter["data_requirements"].append(
        {"capability": "admin_boundary", "purpose": "district", "status": "pending"}
    )
    await save_session_plan(plan)
    regressed = await maybe_finalize_map_product(clean_session, reason="turn_settled")
    assert regressed is not None and regressed.status == STATUS_PENDING

    plan = await load_session_plan(clean_session)
    block = plan.gis_chapter["map_product"]
    assert block["status"] == STATUS_PENDING
    assert block["projection"] == "Map product: pending"
    projection = format_session_plan_projection(plan)
    assert "Map product: final" not in projection


@pytest.mark.asyncio
async def test_failed_regression_overwrites_stored_complete_block(clean_session):
    """重试失败（行标 failed，无 revision 变化）→ blocked 披露覆盖陈旧
    complete 块（不再持有 "final" 投影）。"""
    from app.services.session_plan import load_session_plan

    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)
    assert (await maybe_finalize_map_product(clean_session)).status == STATUS_COMPLETE

    chapter["analysis_steps"][0]["status"] = "failed"  # 重试失败（行不推 revision）
    await _save_plan(clean_session, chapter)
    regressed = await maybe_finalize_map_product(clean_session, reason="turn_settled")
    assert regressed is not None and regressed.status == STATUS_FAILED

    plan = await load_session_plan(clean_session)
    assert plan.gis_chapter["map_product"]["status"] == STATUS_FAILED


@pytest.mark.asyncio
async def test_repair_memory_union_survives_reruns(clean_session):
    """one-shot 修复记忆跨轮存活（review B-4）：prior ∪ applied 持久化。
    旧实现只写本轮 repairs —— 无修复的下一轮把记忆清零，finalizer 隔轮
    重新对抗用户决策（enable→user disable→enable→user disable…）。"""
    from app.services.session_plan import load_session_plan
    from app.services.mapspec_store import mapspec_store

    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)
    assert (await maybe_finalize_map_product(clean_session)).status == STATUS_COMPLETE

    # 第一轮：用户禁用 title → finalizer 修复启用（记录修复记忆）
    await mapspec_store.patch_component(
        clean_session, component_id="title", component_type="title", enabled=False
    )
    second = await maybe_finalize_map_product(clean_session, reason="turn_settled")
    assert any(r.startswith(f"{R_ENABLE_COMPONENT}:") for r in second.repairs_applied)

    # 第二轮：用户再次禁用 → one-shot（prior 记忆在场）不再对抗 →
    # needs_repair 披露；关键是持久化块保留修复记忆（union）。
    await mapspec_store.patch_component(
        clean_session, component_id="title", component_type="title", enabled=False
    )
    third = await maybe_finalize_map_product(clean_session, reason="turn_settled")
    assert third.status == STATUS_NEEDS_REPAIR
    assert not any(r.startswith(f"{R_ENABLE_COMPONENT}:") for r in third.repairs_applied)

    plan = await load_session_plan(clean_session)
    stored_repairs = plan.gis_chapter["map_product"]["repairs"]
    assert any(r.startswith(f"{R_ENABLE_COMPONENT}:") for r in stored_repairs)

    # 第三轮（同态再触发，force 绕过指纹门）：记忆仍在 → 仍不对抗
    fourth = await maybe_finalize_map_product(
        clean_session, reason="turn_settled", force=True
    )
    assert not any(
        r.startswith(f"{R_ENABLE_COMPONENT}:") for r in fourth.repairs_applied
    )


@pytest.mark.asyncio
async def test_fingerprint_gate_skips_unchanged_abnormal_state(clean_session):
    """去重门覆盖全部终态（review F-4）：卡在 needs_repair 的会话在
    「revision + 行指纹」双无变化时跳过重验，不再每个工具结果重放整轮。"""
    from app.services.session_plan import load_session_plan
    from app.services.mapspec_store import mapspec_store

    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)
    assert (await maybe_finalize_map_product(clean_session)).status == STATUS_COMPLETE

    # 制造持久的 needs_repair：两次禁用 title（第二次被 one-shot 拒绝修复）
    for _ in range(2):
        await mapspec_store.patch_component(
            clean_session, component_id="title", component_type="title", enabled=False
        )
        await maybe_finalize_map_product(clean_session, reason="turn_settled")
    plan = await load_session_plan(clean_session)
    assert plan.gis_chapter["map_product"]["status"] == STATUS_NEEDS_REPAIR

    # 无任何变化（revision / 行指纹一致）→ 跳过重验
    assert await maybe_finalize_map_product(clean_session) is None


def test_map_product_block_contract():
    result = MapCompletionResult(status=STATUS_COMPLETE, summary="ok")
    block = map_product_block(
        result,
        7,
        all_repairs=["enable_component:title", "enable_component:title", "add_component:legend"],
        rows_fingerprint="poi_query:available:ref:a|density_surface:done:",
    )
    # union 去重、有界
    assert block["repairs"] == ["enable_component:title", "add_component:legend"]
    assert block["checked_revision"] == 7
    assert block["rows_fingerprint"] == "poi_query:available:ref:a|density_surface:done:"
    assert block["projection"] == "Map product: final"


@pytest.mark.asyncio
async def test_read_stored_map_product_carries_session_id(clean_session):
    """stored 兜底披露必须带 session_id —— 前端 INV-2 跨会话守卫读的
    就是这个字段（review B-P3：缺 sid 的载荷绕过守卫）。"""
    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)
    await maybe_finalize_map_product(clean_session)

    stored = await read_stored_map_product(clean_session)
    assert stored is not None
    assert stored["session_id"] == clean_session
    assert stored["status"] == STATUS_COMPLETE


# ── 契约 ──────────────────────────────────────────────────────────────


def test_result_serializable_and_bounded():
    result = MapCompletionResult(
        status=STATUS_NEEDS_REPAIR,
        findings=[
            MapCompletionFinding(code=F_LAYER_HIDDEN, severity="error", target=f"L{i}", repair=R_SHOW_LAYER)
            for i in range(50)
        ],
        repairs_applied=[f"r{i}" for i in range(50)],
    )
    d = result.to_dict()
    assert len(d["issues"]) <= 12
    assert len(d["repairs"]) <= 6
    payload = finalization_sse_payload(result)
    assert len(payload["issues"]) <= 4 and len(payload["repairs"]) <= 4


def test_viewport_status_derivation():
    # derive_result_bbox 是纯函数（review G-2：此前断言是恒真式）：
    # 并集、缺 bbox 跳过、退化 bbox（w>e，跨日期变更线）跳过。
    chapter = _chapter(bound_ref="ref:a")
    chapter["analysis_steps"][0]["bound_ref"] = "ref:b"
    descriptors = {
        "ref:a": {"feature_count": 2, "bbox": [1.0, 2.0, 3.0, 4.0]},
        "ref:b": {"feature_count": 2, "bbox": [0.0, 1.5, 10.0, 3.5]},
        "ref:c": {"feature_count": 2, "bbox": [170.0, 1.0, -170.0, 2.0]},  # w>e 跳过
        "ref:d": {"feature_count": 2},
    }
    assert derive_result_bbox(chapter, descriptors) == [0.0, 1.5, 10.0, 4.0]
    assert derive_result_bbox(_chapter(), {"ref:geojson-x": {"feature_count": 1}}) is None


def test_projection_line_mapping():
    assert MapCompletionResult(status=STATUS_COMPLETE).projection_line() == "Map product: final"
    assert "needs repair" in MapCompletionResult(status=STATUS_NEEDS_REPAIR).projection_line()
    assert "incomplete" in MapCompletionResult(status=STATUS_FAILED).projection_line()
    assert MapCompletionResult(status=STATUS_PENDING).projection_line() == "Map product: pending"


def test_viewport_no_bbox_warning_code_exists():
    assert F_VIEWPORT_NO_BBOX == "viewport_no_bbox"


# ── PR 终审回归 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fingerprint_gate_survives_truncation(clean_session):
    """行指纹 >512 字符（≥8 行长 ref）时去重门仍生效（终审 F2：存储侧
    截断、比较侧全量 → 永不匹配 → 门失效、每触发点重跑）。"""
    from app.services.session_plan import load_session_plan

    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    # 长度超 512：附加带长 ref 的行
    for i in range(8):
        chapter["analysis_steps"].append(
            {
                "capability": f"cap_{i}",
                "purpose": "pad",
                "status": "done",
                "bound_ref": f"ref:geojson-pad-{i}-{'x' * 40}",
                "optional": True,
            }
        )
    await _seed_mapspec(clean_session)
    await _save_plan(clean_session, chapter)
    first = await maybe_finalize_map_product(clean_session)
    assert first is not None and first.status == STATUS_COMPLETE
    plan = await load_session_plan(clean_session)
    block = plan.gis_chapter["map_product"]
    assert len(block["rows_fingerprint"]) == 512  # 存储侧确实截断
    # 门在截断下仍跳过（修复前此处会重跑）
    assert await maybe_finalize_map_product(clean_session) is None


@pytest.mark.asyncio
async def test_transient_descriptor_error_is_not_expiry(clean_session):
    """descriptor 探测持续异常 = unknown（终审 F4）：跳过不判 —— 不把
    存储抖动持久化成假 failed。确认缺失（None）才是 expired。"""
    from app.services import session_data as sd_module

    ref = await _store_ref(clean_session)
    chapter = _chapter(bound_ref=ref)
    await _seed_mapspec(clean_session)

    original = sd_module.session_data_manager.get_ref_descriptor

    async def _flaky(sid, ref_id):
        raise RuntimeError("transient store error")

    # 持续异常 → unknown → 不产 expired finding、不 failed
    sd_module.session_data_manager.get_ref_descriptor = _flaky  # type: ignore[assignment]
    try:
        result = await run_map_finalization(clean_session, chapter=chapter)
    finally:
        sd_module.session_data_manager.get_ref_descriptor = original  # type: ignore[assignment]
    assert not any(f.code == F_ARTIFACT_EXPIRED for f in result.findings)
    assert result.status != STATUS_FAILED
