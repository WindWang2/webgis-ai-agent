"""P9 Render Observation Runtime 单元测试。

覆盖：
- observation 解析/守卫（跨会话、非 frontend_runtime、缺失）；
- validate_render_observation 的 revision 防护（match / stale / absent /
  pre-revision / not_applicable）；
- 结果层 / required 组件 / runtime error 的渲染级 findings；
- 架构守卫（RenderObservation ≠ 第二 MapSpec：observation 载荷键面固定、
  不携带 mapspec/feature 载荷；组件观察类型必须在 catalog 支持面内）。
"""
import shutil
import uuid

import pytest

from app.services.gis_harness.map_completion import (
    STATUS_COMPLETE,
    F_RENDER_COMPONENT_MISSING,
    F_RENDER_ERROR,
    F_RENDER_LAYER_MISSING,
    F_RENDER_REVISION_STALE,
    F_RENDER_SOURCE_MISSING,
    F_RENDER_UNVERIFIED,
    gather_completion_inputs,
    run_map_finalization,
)
from app.services.gis_harness.render_observation import (
    OBSERVATION_STATE_KEY,
    RENDER_ISSUES,
    RENDER_NOT_APPLICABLE,
    RENDER_STALE,
    RENDER_UNKNOWN,
    RENDER_VERIFIED,
    load_render_observation,
    observation_revision,
    observation_sequence,
    validate_render_observation,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"ro-obs-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _observation(revision: int, *, layers=None, components=None, errors=None, seq: int = 1,
                 session_id: str = "sid-test"):
    return {
        "session_id": session_id,
        "sequence": seq,
        "client_generation": 1000 + seq,
        "source": "frontend_runtime",
        "mapspec_fingerprint": "fp-aaaaaaaaaaaaaaaa",
        "mapspec_revision": revision,
        "layer_count": len(layers or []),
        "layers": layers or [],
        "viewport": {},
        "style_loaded": True,
        "reconcile_error": "",
        "components": components or [],
        "runtime_errors": errors or [],
        "map_idle": True,
        "observed_at": 1_756_000_000_000,
    }


def _chapter_and_spec():
    chapter = {
        "map_layers": [
            {"role": "primary", "layer_id": "poi-heatmap", "enabled": True},
        ],
    }
    mapspec = {
        "layers": [
            {"id": "poi-heatmap", "source": "s-poi", "type": "heatmap",
             "layout": {"visibility": "visible"}},
        ],
        "sources": {"s-poi": {"type": "geojson"}},
    }
    return chapter, mapspec


# ── 解析/守卫 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_render_observation_requires_frontend_source():
    assert await session_data_manager.set_map_state(
        "sid-x", OBSERVATION_STATE_KEY, {"source": "hud", "sequence": 1}
    ) is not False
    assert await load_render_observation("sid-x") is None


@pytest.mark.asyncio
async def test_load_render_observation_rejects_cross_session():
    await session_data_manager.set_map_state(
        "sid-x", OBSERVATION_STATE_KEY, _observation(1)
    )
    # observation["session_id"]="sid-test" ≠ "sid-x" → 防御性拒绝
    assert await load_render_observation("sid-x") is None


def test_observation_accessors():
    obs = _observation(7, seq=3)
    assert observation_revision(obs) == 7
    assert observation_sequence(obs) == 3
    assert observation_revision({"sequence": 1}) is None  # pre-revision
    assert observation_sequence(None) == 0


# ── revision 防护（P9 §9 / Scenario J）────────────────────────────────


def test_validate_stale_observation_cannot_validate_newer_spec():
    chapter, mapspec = _chapter_and_spec()
    status, findings = validate_render_observation(
        chapter, mapspec, _observation(9), current_revision=10
    )
    assert status == RENDER_STALE
    assert any(f.code == F_RENDER_REVISION_STALE for f in findings)
    # warning 不产生 error —— 不 false complete 也不 false failed
    assert all(f.severity == "warning" for f in findings)


def test_validate_absent_observation_is_unverified_not_error():
    chapter, mapspec = _chapter_and_spec()
    status, findings = validate_render_observation(
        chapter, mapspec, None, current_revision=10
    )
    assert status == RENDER_UNKNOWN
    codes = {f.code for f in findings}
    assert codes == {F_RENDER_UNVERIFIED}
    assert all(f.severity == "warning" for f in findings)


def test_validate_pre_revision_observation_is_unknown():
    chapter, mapspec = _chapter_and_spec()
    obs = _observation(10)
    obs.pop("mapspec_revision")
    status, _ = validate_render_observation(chapter, mapspec, obs, current_revision=10)
    assert status == RENDER_UNKNOWN


def test_validate_not_applicable_without_assertions():
    status, findings = validate_render_observation(
        {"map_layers": []}, {"layers": []}, None, current_revision=5
    )
    assert status == RENDER_NOT_APPLICABLE
    assert findings == []


# ── 渲染级 findings（Scenario I / 组件 / 错误）────────────────────────


def test_matched_revision_missing_layer_is_error():
    chapter, mapspec = _chapter_and_spec()
    obs = _observation(10, layers=[])  # 未观察到任何层
    status, findings = validate_render_observation(
        chapter, mapspec, obs, current_revision=10
    )
    assert status == RENDER_ISSUES
    missing = [f for f in findings if f.code == F_RENDER_LAYER_MISSING]
    assert missing and missing[0].severity == "error"
    assert missing[0].target == "poi-heatmap"


def test_matched_revision_present_layer_verified():
    chapter, mapspec = _chapter_and_spec()
    obs = _observation(
        10,
        layers=[{
            "id": "poi-heatmap",
            "runtime_layer_count": 2,
            "visible": True,
            "source_converged": True,
            "style_converged": True,
        }],
    )
    status, findings = validate_render_observation(
        chapter, mapspec, obs, current_revision=10
    )
    assert status == RENDER_VERIFIED
    assert findings == []


def test_mounted_but_hidden_layer_is_error():
    chapter, mapspec = _chapter_and_spec()
    obs = _observation(
        10,
        layers=[{
            "id": "poi-heatmap",
            "runtime_layer_count": 1,
            "visible": False,
            "source_converged": True,
        }],
    )
    status, findings = validate_render_observation(
        chapter, mapspec, obs, current_revision=10
    )
    assert status == RENDER_ISSUES
    assert any(f.code == F_RENDER_LAYER_MISSING for f in findings)


def test_source_not_converged_is_warning_not_error():
    chapter, mapspec = _chapter_and_spec()
    obs = _observation(
        10,
        layers=[{
            "id": "poi-heatmap",
            "runtime_layer_count": 1,
            "visible": True,
            "source_converged": False,
        }],
    )
    status, findings = validate_render_observation(
        chapter, mapspec, obs, current_revision=10
    )
    assert status == RENDER_VERIFIED  # warning 不降级 verified 状态
    assert any(f.code == F_RENDER_SOURCE_MISSING and f.severity == "warning"
               for f in findings)


def test_required_component_slot_not_observed():
    chapter, mapspec = _chapter_and_spec()
    obs = _observation(
        10,
        layers=[{
            "id": "poi-heatmap", "runtime_layer_count": 1, "visible": True,
        }],
        components=[
            {"id": "title", "type": "title", "enabled": True, "mounted": True},
            {"id": "scale-bar", "type": "scale_bar", "enabled": True, "mounted": True},
        ],
    )
    status, findings = validate_render_observation(
        chapter, mapspec, obs, current_revision=10,
        required_slots=[["title"], ["scale_bar"], ["legend"]],
    )
    missing = [f for f in findings if f.code == F_RENDER_COMPONENT_MISSING]
    assert missing and missing[0].target == "legend"


def test_runtime_errors_disclosed_bounded_as_warning():
    chapter, mapspec = _chapter_and_spec()
    obs = _observation(
        10,
        layers=[{
            "id": "poi-heatmap", "runtime_layer_count": 1, "visible": True,
        }],
        errors=[{"message": "tiles failed to load", "target": "s-poi"}],
    )
    status, findings = validate_render_observation(
        chapter, mapspec, obs, current_revision=10
    )
    assert status == RENDER_VERIFIED  # 瞬态错误不判失败（层在场是判据）
    assert any(f.code == F_RENDER_ERROR and f.severity == "warning"
               for f in findings)


# ── 架构守卫：RenderObservation ≠ 第二 MapSpec ─────────────────────────


def test_observation_payload_cannot_carry_mapspec():
    """端点构造的 observation 键面固定 —— 客户端多发的 mapspec/feature 载荷
    不得进入持久化观察（防止 observation 变成第二 spec 真相）。"""
    from app.api.routes.chat import CartographicRuntimeObservationRequest

    req = CartographicRuntimeObservationRequest(
        client_generation=1,
        mapspec_fingerprint="fp-aaaaaaaaaaaaaaaa",
        layers=[],
        viewport={},
        style_loaded=True,
        mapspec_revision=3,
        components=[{"id": "title", "type": "title", "mounted": True}],
        runtime_errors=[{"message": "x"}],
        map_idle=True,
        # 越界字段：Pydantic 默认忽略 —— 端点 observation dict 是显式构造，
        # 这里锁定该契约：DTO 上没有 mapspec 字段可用。
        model_config={"extra": "ignore"},
    )
    dumped = req.model_dump()
    assert "mapspec" not in dumped
    assert "features" not in dumped
    assert dumped["components"][0]["mounted"] is True


def test_observation_component_types_must_be_known_to_backend_catalog():
    """架构守卫：observation 上报的组件类型必须存在于后端组件支持面
    （component_renderers registry）—— 未知类型不进 facet/finding 语义。"""
    from app.lib.cartography.component_renderers import (
        get_component_renderer_registry,
    )

    registry = get_component_renderer_registry()
    frontend_chrome_types = {
        "title", "subtitle", "north_arrow", "scale_bar", "attribution",
        "continuous_colorbar", "legend", "categorical_legend",
        "annotation", "statistics_panel", "chart_panel", "map_border",
        "graticule",
    }
    for t in frontend_chrome_types:
        assert registry.support_for(t) is not None, (
            f"frontend chrome type '{t}' missing from backend renderer support matrix"
        )


# ── finalizer 集成（gather + run 的渲染级输入）─────────────────────────


@pytest.mark.asyncio
async def test_gather_completion_inputs_carries_render_evidence(clean_session):
    chapter, _ = _chapter_and_spec()
    await session_data_manager.set_map_state(
        clean_session, OBSERVATION_STATE_KEY, _observation(4, session_id=clean_session)
    )
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_mutation_revision", 4
    )
    inputs = await gather_completion_inputs(clean_session, chapter)
    assert inputs["mapspec_revision"] == 4
    assert inputs["render_observation"]["mapspec_revision"] == 4


@pytest.mark.asyncio
async def test_finalizer_complete_with_stale_observation_discloses(tmp_path, clean_session):
    """Scenario J（finalizer 级）：observation rev 9 / spec rev 10 →
    desired-state 全过 → complete 但 render_status=stale（不 false verified）。"""
    from app.services.mapspec.lifecycle_engine import (
        InitProjectIntent,
        MapSpecLifecycleEngine,
        UpsertLayerIntent,
    )
    from app.services.session_plan import SessionPlan, save_session_plan

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
         "properties": {}}]}
    ref = await session_data_manager.store(clean_session, fc, prefix="geojson")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())
    await engine.apply_mutation(
        clean_session,
        UpsertLayerIntent(
            layer={"id": "poi-heatmap", "source": "s-poi", "type": "heatmap",
                   "paint": {}, "layout": {"visibility": "visible"}},
            source_data=fc,
        ),
    )
    chapter = {
        "query": "成都小学分布情况",
        "recipe_id": "poi_density_map",
        "data_requirements": [
            {"capability": "poi_query", "status": "available",
             "bound_ref": ref, "optional": False},
        ],
        "analysis_steps": [],
        "map_layers": [{"role": "primary", "layer_id": "poi-heatmap", "enabled": True}],
        "template_selection": {"composition_template_id": "composition.density_map"},
    }
    plan = SessionPlan(
        envelope_id=f"env-{uuid.uuid4().hex[:8]}",
        session_id=clean_session,
        user_goal="成都小学分布情况",
        gis_chapter=chapter,
    )
    await save_session_plan(plan)

    # spec revision（两次 mutation）≠ observation revision
    state = await session_data_manager.get_map_state(clean_session)
    current_rev = int(state.get("_cartographic_mutation_revision") or 0)
    await session_data_manager.set_map_state(
        clean_session, OBSERVATION_STATE_KEY,
        _observation(current_rev - 1, session_id=clean_session),
    )

    result = await run_map_finalization(clean_session, chapter=chapter, reason="test")
    assert result.status == STATUS_COMPLETE
    assert result.render_status == RENDER_STALE
    assert any(f.code == F_RENDER_REVISION_STALE for f in result.findings)
