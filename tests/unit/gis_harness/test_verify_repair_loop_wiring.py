"""验证→修复闭环收口（方向 5 W-C/W-D）finalization 级回归锁。

不变式：
1. W-C 环内 recurrence 硬停：同一次终验运行内同一 finding 复现且索要
   同一修复 → 不再重复对抗，loop_stop=no_progress，诚实 needs_repair；
   修复真正收敛的路径不受影响（loop_stop 为空、status 如实）；
2. W-C no-progress 判定不改变 status 词表语义（needs_repair 不变 failed）；
3. W-D 视觉评估：未配置 = 零行为变化；配置后 findings 恒 degradation_only
   （披露 severity 封顶 warning，永不改写 status/READY 档位）。
"""

from __future__ import annotations

import shutil
import uuid

import pytest

from app.services.gis_harness.completion.contracts import (
    F_COMPONENT_DISABLED,
    LOOP_STOP_NO_PROGRESS,
    MAX_FINALIZATION_PASSES,
    STATUS_COMPLETE,
    STATUS_NEEDS_REPAIR,
    MapCompletionResult,
)
from app.services.gis_harness.completion.pipeline import (
    _assemble_visual_snapshot,
    _maybe_run_visual_evaluation,
    run_map_finalization,
)
from app.services.gis_harness.completion.contracts import MapCompletionFinding
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager


# ── fixtures / helpers（与 test_map_completion 同构的最小场景）───────────


@pytest.fixture
async def clean_session():
    sid = f"vrl-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                "properties": {},
            },
        ],
    }


def _chapter(bound_ref: str):
    return {
        "plan_id": "plan-test",
        "query": "成都小学分布",
        "recipe_id": "poi_density",
        "data_requirements": [
            {
                "capability": "poi_query",
                "purpose": "POI",
                "status": "available",
                "bound_ref": bound_ref,
                "optional": False,
            }
        ],
        "analysis_steps": [
            {
                "capability": "density_surface",
                "purpose": "density",
                "status": "done",
                "bound_ref": bound_ref,
                "optional": False,
            }
        ],
        "map_layers": [{"role": "primary", "layer_id": "poi-main", "enabled": True}],
        "components": [],
        "template_selection": {},
    }


async def _seed_mapspec(
    sid: str, *, scale_bar_enabled: bool = True, seed_title: bool = True
):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={
                "id": "poi-main",
                "source": "s-poi-main",
                "type": "circle",
                "paint": {"circle-color": "#00f"},
                "layout": {"visibility": "visible"},
            },
            source_data=_geojson(),
        ),
    )
    comps = [
        {
            "id": "scale-bar",
            "type": "scale_bar",
            "position": "bottom-right",
            "enabled": scale_bar_enabled,
        }
    ]
    if seed_title:
        comps.insert(
            0,
            {
                "id": "title",
                "type": "title",
                "position": "top-center",
                "options": {"text": "成都小学分布"},
            },
        )
    for comp in comps:
        await engine.apply_mutation(
            sid,
            PatchComponentIntent(
                component_id=comp["id"],
                component_type=comp["type"],
                enabled=comp.get("enabled", True),
                position=comp.get("position", "none"),
                upsert=True,
            ),
        )
    from app.services.mapspec_store import mapspec_store

    return await mapspec_store.get_mapspec(sid)


async def _store_ref(sid: str):
    return await session_data_manager.store(sid, _geojson(), prefix="geojson")


# ── W-C：环内 recurrence / no-progress 硬停 ─────────────────────────────


@pytest.mark.asyncio
async def test_recurring_repair_stops_with_no_progress(clean_session, monkeypatch):
    """修复通道假成功（spec 未变）→ 第二轮同 finding 复现 → no_progress
    停止，不再重复索要同一修复；status 如实 needs_repair（不是 failed）。"""
    ref = await _store_ref(clean_session)
    chapter = _chapter(ref)
    await _seed_mapspec(clean_session, scale_bar_enabled=False)
    from app.services.mapspec_store import mapspec_store

    async def _fake_patch_component(*args, **kwargs):
        return {"success": True}  # 声明成功但不改 spec → 修复不生效

    monkeypatch.setattr(mapspec_store, "patch_component", _fake_patch_component)
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.loop_stop == LOOP_STOP_NO_PROGRESS
    assert result.status == STATUS_NEEDS_REPAIR
    assert any(f.code == F_COMPONENT_DISABLED for f in result.findings)
    assert result.passes <= MAX_FINALIZATION_PASSES
    # 同一修复只申请过一次（不重复对抗）
    assert result.repairs_applied.count("enable_component:scale_bar") <= 1


@pytest.mark.asyncio
async def test_converging_repairs_do_not_set_no_progress(clean_session):
    """回归：真实收敛路径（修复生效）→ loop_stop 为空、status complete。"""
    ref = await _store_ref(clean_session)
    chapter = _chapter(ref)
    await _seed_mapspec(clean_session, scale_bar_enabled=False)
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    assert result.loop_stop == ""
    assert any(r.startswith("enable_component:") for r in result.repairs_applied)


def test_no_progress_marker_serializes_additively() -> None:
    result = MapCompletionResult(status=STATUS_NEEDS_REPAIR, loop_stop="no_progress")
    d = result.to_dict()
    assert d["loop_stop"] == "no_progress"
    # 旧形状键不受影响；缺席时不写键
    assert MapCompletionResult().to_dict().get("loop_stop") is None


# ── W-D：visual seam 生产接线 ────────────────────────────────────────────


def test_visual_evaluation_unconfigured_is_noop(monkeypatch):
    monkeypatch.delenv("GIS_VISUAL_EVALUATOR", raising=False)
    out = _maybe_run_visual_evaluation({}, None, [])
    assert out == []


def test_visual_snapshot_is_bounded_projection():
    mapspec = {
        "layers": [{"id": "poi-main", "paint": {"circle-color": "#fff"}}],
        "sources": {"s1": {"ref": "ref:x"}},
    }
    findings = [
        MapCompletionFinding(
            code="layer_hidden", severity="warning", target="poi-main", detail="d"
        )
    ]
    snap = _assemble_visual_snapshot(mapspec, None, findings)
    assert snap["trigger"] == "finalization"
    # 有界投影：图层投影只含白名单元数据键（无 inline 数据体/payload）
    _LAYER_PROJECTION_KEYS = {
        "id",
        "source",
        "type",
        "visible",
        "minzoom",
        "maxzoom",
        "layout",
        "paint",
        "filter",
        "legend_spec",
        "provenance",
        "cartographic_intent",
        "cartographic_profile",
    }
    projected = snap["mapspec_projection"]
    assert projected["layer_count"] == 1
    for entry in projected["layers"]:
        if isinstance(entry, dict) and "__omitted_layers__" not in entry:
            assert set(entry.keys()) <= _LAYER_PROJECTION_KEYS
    assert snap["deterministic_findings"] == [
        {"code": "layer_hidden", "severity": "warning", "target": "poi-main"}
    ]
    assert snap["observation_summary"]["layers_present"] == []


@pytest.mark.asyncio
async def test_configured_visual_evaluation_discloses_capped_warning(
    clean_session, monkeypatch
):
    """配置评估器 → finalization 触发；error 级视觉发现披露为 warning +
    visual_ 命名空间，不改写 COMPLETE；唯一裁决效应 = READY 诚实降档为
    READY_WITH_WARNINGS（锁定，review #12）；UnifiedFinding 保真进
    result.visual_findings。"""
    from app.services.gis_harness.visual_evaluator import UnifiedFinding

    def _fake_evaluator(snapshot):
        assert snapshot["trigger"] == "finalization"
        return [
            {
                "code": "V_TOP_HEAVY",
                "severity": "error",
                "evidence": "visual weight concentrated top-left",
                "affected_entity": "map",
                "source": "stub",
            }
        ]

    monkeypatch.setattr(
        "app.services.gis_harness.visual_evaluator.get_visual_evaluator",
        lambda: _fake_evaluator,
    )
    ref = await _store_ref(clean_session)
    chapter = _chapter(ref)
    await _seed_mapspec(clean_session)
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == STATUS_COMPLETE  # degradation_only 永不产生 error
    assert result.visual_findings and len(result.visual_findings) == 1
    vf = result.visual_findings[0]
    assert isinstance(vf, UnifiedFinding)
    assert vf.degradation_only is True and vf.blocks_completion is False
    # 披露面 severity 封顶 warning + visual_ 命名空间（review #7）
    disclosed = [f for f in result.findings if f.code == "visual_V_TOP_HEAVY"]
    assert disclosed and disclosed[0].severity == "warning"
    # 序列化 additive
    d = result.to_dict()
    assert d["visual_findings"][0]["finding_class"] == "visual"
    # 唯一裁决效应锁定：READY → READY_WITH_WARNINGS（不是 READY，更不是 BLOCKED）
    assert result.product_verdict == "READY_WITH_WARNINGS"


@pytest.mark.asyncio
async def test_visual_finding_never_blocks_failed_status_semantics(
    clean_session, monkeypatch
):
    """视觉发现叠加在真实 failed 之上：failed 语义不被 visual 改写。"""
    chapter = _chapter("ref:missing-x")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(clean_session, InitProjectIntent())

    def _fake_evaluator(snapshot):
        return [
            {
                "code": "V_CLUTTER",
                "severity": "warning",
                "evidence": "clutter",
                "affected_entity": "map",
            }
        ]

    monkeypatch.setattr(
        "app.services.gis_harness.visual_evaluator.get_visual_evaluator",
        lambda: _fake_evaluator,
    )
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.status == "failed"  # 结构性缺口仍是 failed
    assert result.visual_findings  # 视觉披露并存


# ── #15 集成接线：plan_repairs_for_chapter 消费评审与视觉发现 ────────────


@pytest.mark.asyncio
async def test_plan_repairs_for_chapter_consumes_cartographic_review(
    clean_session,
):
    """map_state['_cartographic_review'] 的 deterministic fail 规则 →
    统一投影 → plan 出 quality_loop 动作 + W11 账本落账（review #15）。"""
    from app.services.gis_harness.completion.contracts import (
        MapCompletionResult,
    )
    from app.services.gis_harness.repair_planner import (
        finding_fingerprint,
        plan_repairs_for_chapter,
        REPAIR_LOOP_KEY,
    )

    review = {
        "session_id": clean_session,
        "cartography": {
            "checks": [
                {
                    "rule": "RUNTIME_LEGEND_CONVERGENCE",
                    "status": "fail",
                    "severity": "error",
                    "message": "legend missing",
                    "layer_id": "poi-main",
                    "repairability": "auto_safe",
                    "suggested_fix": {
                        "operation": "refresh_runtime_legend",
                        "layer_id": "poi-main",
                    },
                }
            ],
        },
        "gate": {},
        "overall_passed": False,
    }
    await session_data_manager.set_map_state(
        clean_session, "_cartographic_review", review
    )
    result = MapCompletionResult(status=STATUS_COMPLETE)  # 无确定性 error
    plan = await plan_repairs_for_chapter(clean_session, {}, result)
    assert plan is not None
    actions = plan["actions"] + plan["deferred"] + plan["refused"]
    hit = [
        a
        for a in actions
        if a["domain"] == "semantic_check" and a["code"] == "RUNTIME_LEGEND_CONVERGENCE"
    ]
    assert hit, plan
    assert hit[0]["executor"] == "quality_loop"
    assert hit[0]["repair_class"] == "regenerate_legend"
    # W11 账本已为该规则落账（指纹与投影面同构同值）
    from app.services.gis_harness.completion.unified_findings import (
        from_cartographic_check,
    )

    uf = from_cartographic_check(review["cartography"]["checks"][0])
    state = await session_data_manager.get_map_state(clean_session)
    assert finding_fingerprint(uf) in (state.get(REPAIR_LOOP_KEY) or {})


@pytest.mark.asyncio
async def test_visual_error_finding_lands_deferred_warning_skipped():
    """plan 面视觉语义（review #2/#15）：error 级软发现 → deferred
    （requires_user_approval）；warning 级纯披露，不产生 plan 动作。"""
    from app.services.gis_harness.completion.contracts import (
        MapCompletionResult,
    )
    from app.services.gis_harness.completion.unified_findings import (
        UnifiedFinding,
    )
    from app.services.gis_harness.repair_planner import plan_repairs

    err = UnifiedFinding(
        domain="visual",
        code="V_BROKEN_CONTRAST",
        severity="error",
        source="visual_evaluator",
        degradation_only=True,
        blocks_completion=False,
    )
    warn = UnifiedFinding(
        domain="visual",
        code="V_TOP_HEAVY",
        severity="warning",
        source="visual_evaluator",
        degradation_only=True,
        blocks_completion=False,
    )
    result = MapCompletionResult(status=STATUS_NEEDS_REPAIR)
    result.visual_findings = [err, warn]
    # 模拟 plan_repairs_for_chapter 的 blocking 过滤 + 分类
    from app.services.gis_harness.completion.unified_findings import (
        collect_unified_findings,
    )

    unified = collect_unified_findings(
        result=result, visual_findings=result.visual_findings
    )
    blocking = [u for u in unified if u.blocks_completion or u.severity == "error"]
    assert [u.code for u in blocking] == ["V_BROKEN_CONTRAST"]
    plan = plan_repairs(blocking)
    assert [a.code for a in plan.deferred] == ["V_BROKEN_CONTRAST"]
    assert all(a.safety == "requires_user_approval" for a in plan.deferred)
    assert all(a.target != "V_TOP_HEAVY" for a in plan.actions + plan.deferred)


@pytest.mark.asyncio
async def test_partial_convergence_then_all_attempted_stops_no_progress(
    clean_session,
    monkeypatch,
):
    """review #16：findings 集合变化（title 修复真实收敛）但剩余可修复
    项（scale_bar）索要的仍是本运行已申请过的同一修复 → no_progress 硬停。"""
    ref = await _store_ref(clean_session)
    chapter = _chapter(ref)
    # 只播 disabled 的 scale_bar，不播 title → 两个可修复发现：
    # component_missing(title) + component_disabled(scale-bar)
    await _seed_mapspec(clean_session, seed_title=False, scale_bar_enabled=False)
    from app.services.mapspec_store import mapspec_store

    real_patch = mapspec_store.patch_component.__func__

    async def _selective_patch(*args, **kwargs):
        # title 修复真实落盘（部分收敛）；scale_bar 假成功（不生效）
        if kwargs.get("component_type") == "title":
            return await real_patch(mapspec_store, *args, **kwargs)
        return {"success": True}

    monkeypatch.setattr(mapspec_store, "patch_component", _selective_patch)
    result = await run_map_finalization(clean_session, chapter=chapter)
    assert result.loop_stop == LOOP_STOP_NO_PROGRESS
    assert result.status == STATUS_NEEDS_REPAIR
    assert result.passes <= MAX_FINALIZATION_PASSES
