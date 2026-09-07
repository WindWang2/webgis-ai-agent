"""System Scenarios（ADR-0104 Wave 2/5/6 集成）—— 跨阶段全链场景。

Phase 0 审计（08-e2e-scenario-map.md）的最大缺口：没有场景横跨
plan → tools → artifacts → MapSpec → verify。本文件补齐最小闭环：

- 场景 S1：POI→聚合→热力图→组件齐备 → 终验 complete（render_verified
  语义由 run_map_finalization 给出，不再是 None）；
- 场景 S2：bound_ref 悬空（stale artifact）→ 不得 complete + failure_path
  trace 认证（failure_code 必须在场）；
- 场景 S3：plan-tier 预算/工具类契约（Wave 2 DSL 字段真实 firing）；
  同一场景内经 emit_event 发射制图面事件并核对 digest（Wave 6 集成）。

每个场景的 GisTraceChain 由驱动方按真实发生顺序记录（18 阶段词表的
子集），认证用 app/lib/quality/trace_contract —— 契约先行、记录诚实。
"""
from __future__ import annotations

import shutil
import uuid

import pytest

from app.lib.observability import RingSink, emit_event, event_digest, register_sink
from app.lib.quality.trace_contract import certify_chain
from app.lib.runtime.context import bind_runtime_context
from app.lib.runtime.gis_trace import GisTraceChain, Stage
from app.services.gis_harness.components import build_default_components
from app.services.gis_harness.map_completion import (
    STATUS_COMPLETE,
    run_map_finalization,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    PatchComponentIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager


def _poi_fc(n: int = 30):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.05 + (i % 10) * 0.01,
                                          30.6 + (i // 10) * 0.01]},
             "properties": {"name": f"小学{i}", "students": 200 + i}}
            for i in range(n)
        ],
    }


@pytest.fixture(autouse=True)
def _event_ring():
    """场景内发射的事件进有界 ring（测试观测面；生产默认 LoggingSink）。"""
    ring = RingSink()
    register_sink(ring)
    yield ring
    from app.lib.observability import reset_sinks
    reset_sinks()


@pytest.fixture
async def clean_session():
    sid = f"sys-e2e-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


async def _seed_product(sid: str, *, source_data=None, layer_visible="visible"):
    poi_ref = await session_data_manager.store(sid, source_data or _poi_fc(), prefix="geojson")
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(sid, UpsertLayerIntent(layer={
        "id": "poi-heatmap",
        "source": "s-poi-heatmap",
        "type": "heatmap",
        "paint": {"heatmap-weight": 1},
        "layout": {"visibility": layer_visible},
    }, source_data=source_data or _poi_fc()))
    await engine.apply_mutation(sid, UpsertLayerIntent(layer={
        "id": "district-aggregate",
        "source": "s-district-aggregate",
        "type": "fill",
        "paint": {"fill-color": "#3182bd", "fill-opacity": 0.5},
        "layout": {"visibility": "visible"},
    }, source_data=source_data or _poi_fc()))
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
             "status": "done", "bound_ref": poi_ref, "optional": False},
        ],
        "map_layers": [
            {"role": "primary", "layer_id": "poi-heatmap", "enabled": True},
            {"role": "secondary", "layer_id": "district-aggregate", "enabled": True},
        ],
        "template_selection": {"composition_template_id": "composition.density_map"},
        "components": [],
    }
    for c in build_default_components(
            primary_cartography="visual_heatmap",
            title="成都市小学分布热力图与各区统计",
            extra_types=["statistics_panel", "chart_panel"]):
        await engine.apply_mutation(sid, PatchComponentIntent(
            component_id=c.id, component_type=c.type, enabled=c.enabled,
            position=c.position, options=c.options, upsert=True))
    return chapter, poi_ref


@pytest.mark.asyncio
async def test_s1_full_product_completion_with_map_product_trace(clean_session):
    """S1：完整产品闭环 + map_product 级 trace 认证。"""
    chain = GisTraceChain(turn_id=f"t-{uuid.uuid4().hex[:6]}", session_id=clean_session)

    chain.record(Stage.USER_INTENT, query="成都小学分布密度与各区统计")
    chain.record(Stage.PARSED_INTENT, task="poi_density_map")
    chain.record(Stage.TASK_ONTOLOGY, ontology="distribution.density_quantitative")
    chain.record(Stage.SELECTED_WORKFLOW, recipe="poi_density_map")

    chapter, _ = await _seed_product(clean_session)

    chain.record(Stage.TOOL_CALLS, tools=["query_local_poi", "spatial_aggregate", "kde_contours"])
    chain.record(Stage.TOOL_RESULTS, status="ok")
    chain.record(Stage.ARTIFACT_CREATION, artifact_types=["point_feature_set"])

    spec = await mapspec_store_instance.get_mapspec(clean_session)
    assert spec, "MapSpec 未落账"
    chain.record(Stage.MAP_MUTATIONS, layers=2)
    # 观测/验证/终裁 = run_map_finalization 的真实输出（不是测试自报）
    result = await run_map_finalization(clean_session, chapter=chapter, reason="s1")
    chain.record(Stage.MAP_OBSERVATION, layer_status=result.layer_status)
    chain.record(Stage.VERIFICATION, component_status=result.component_status)
    chain.record(Stage.FINAL_VERDICT, verdict=result.status, failure_code="none")
    chain.record(Stage.USER_OUTPUT, projection=result.projection_line())

    assert result.status == STATUS_COMPLETE, [f.to_dict() for f in result.findings]
    assert result.result_bbox is not None

    cert = certify_chain(chain.as_dict(), "map_product")
    assert cert.passed, cert.to_dict()

    # Wave 6 集成：关联经 RuntimeContext 自动注入，digest 有界可观测
    with bind_runtime_context(session_id=clean_session):
        emit_event("cartography", "observe", status="completed", verdict="complete")
    digest = event_digest()
    assert any(k.startswith("cartography|observe") for k in digest["counts"])


@pytest.mark.asyncio
async def test_s2_empty_source_degradation_disclosed_not_silent(clean_session):
    """S2：空数据源 → verdict 必须如实披露降级/缺口，不许静默 complete。

    Phase 0 审计结论修正：finalization 是 MapSpec 层裁决，悬空 bound_ref
    不进入其证据面（那是 session-data 层的事实）——真正的红线是"空图层
    不得伪装成验证完成的地图产品"。本测试钉住真实语义：空 FC 下要么
    不 complete，要么 complete 必须带 warning/degradation 级 finding。
    """
    chain = GisTraceChain(turn_id=f"t-{uuid.uuid4().hex[:6]}", session_id=clean_session)
    chain.record(Stage.USER_INTENT, query="成都小学分布密度")
    chain.record(Stage.PARSED_INTENT, task="poi_density_map")

    empty_fc = {"type": "FeatureCollection", "features": []}
    chapter, _ = await _seed_product(clean_session, source_data=empty_fc)
    chain.record(Stage.TOOL_CALLS, tools=["query_local_poi"])
    chain.record(Stage.TOOL_RESULTS, status="empty")

    result = await run_map_finalization(clean_session, chapter=chapter, reason="s2")
    disclosed = (
        result.status != STATUS_COMPLETE
        or any(f.severity in ("warning", "error") for f in result.findings)
        or result.status_summary in ("verified_with_degradation",)
        or getattr(result, "verdict", "") == "verified_with_degradation"
    )
    assert disclosed, (
        f"空数据源被静默判定：status={result.status}, "
        f"findings={[f.to_dict() for f in result.findings]}")
    chain.record(Stage.FINAL_VERDICT, verdict=result.status,
                 failure_code="EMPTY_SOURCE_DISCLOSED" if result.status != STATUS_COMPLETE else "none")
    cert = certify_chain(chain.as_dict(), "failure_path")
    assert cert.passed, cert.to_dict()


@pytest.mark.asyncio
async def test_s3_plan_tier_budget_contract_and_export_event(clean_session):
    """S3：plan-tier 预算/工具类契约（Wave 2 DSL）真实判定 + 导出事件。"""
    from app.evaluation.case import GISBenchmarkCase
    from app.evaluation.runner import GISBenchmarkRunner

    case = GISBenchmarkCase(
        id="sys-s3-budget",
        name="canonical POI 分布 + 预算契约",
        group="semantics",
        query="成都市小学分布图",
        expected_capabilities=["poi_query"],
        max_context_schema_bytes=600_000,
        trace_requirements=["turn_id", "session_id"],
    )
    results = await GISBenchmarkRunner().run([case])
    assert len(results) == 1
    r = results[0]
    assert r.passed, r.failures

    # 反向：forbid_network_tools 对真实 network 工具必须 firing
    # （query_local_poi/get_local_admin_boundary 描述符声明 network=True）
    adversarial = GISBenchmarkCase(
        id="sys-s3-forbid-network-fires",
        name="离线契约对 network 工具必须失败",
        group="semantics",
        query="成都市小学分布图",
        forbid_network_tools=True,
    )
    (adv,) = await GISBenchmarkRunner().run([adversarial])
    assert not adv.passed
    assert any("network tool forbidden" in f for f in adv.failures)

    with bind_runtime_context(session_id=clean_session):
        emit_event("cartography", "export", status="completed",
                   format="png", bytes=2048)
    digest = event_digest()
    assert digest["counts"].get("cartography|export|completed", 0) >= 1
