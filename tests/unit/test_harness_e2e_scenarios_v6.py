"""Contextual Cartographic Harness V6 —— Wave 16b §57 十场景 E2E。

一文件十测试，对应 §57 Scenario 1–10（全部确定性：无 LLM、无网络、无随机，
fake 载荷全内联确定性）：

1. 专题图全链 READY（fake telemetry → verified → READY）
2. CRS 错误 → 重投影修复分类（validate_semantics → reproject）
3. 改参只重算子图（compute_affected_subgraph 闭包精度）
4. 改色带零重跑（style 变更不进科学重算）
5. source 失败 → render finding → 修复通道 → verified
6. 空图表不得 READY（chart_data_missing error → NEEDS_REPAIR）
7. 视觉重叠 → layout finding → relayout 修复 → 重渲零 finding
8. 锁图层（W15 模式薄封装：统一 guard + planner user-wins）
9. 中断恢复（W14 Scenario 9 模式薄封装：建锚 → 中断 → resume → 跑完）
10. 重复 repair 无进展自动停止（W11 账本口：no_progress → exhausted）

词汇纪律：finding domain/verdict/repair class/lock 披露词全部复用既有常量
（contracts / unified_findings / repair_planner / lifecycle_engine），不自创；
执行通道只断言既有 executor（pi_recompute / runtime_repair / finalizer /
quality_loop / user / none），不建第二通道。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.services.gis_harness.completion.contracts import (
    F_CHART_DATA_MISSING,
    F_CRS_NOT_WGS84,
    F_LAYOUT_CONFLICT,
    F_RENDER_SOURCE_MISSING,
    RUNTIME_RENDER_CODES,
    STATUS_COMPLETE,
    STATUS_NEEDS_REPAIR,
    VERDICT_NEEDS_REPAIR,
    VERDICT_READY,
    MapCompletionFinding,
    MapCompletionResult,
    derive_product_verdict,
)
from app.services.gis_harness.completion.unified_findings import (
    UnifiedFinding,
    collect_unified_findings,
    from_completion_finding,
)
from app.services.gis_harness.completion.validators.semantics import (
    validate_semantics,
)
from app.services.gis_harness.render_observation import (
    derive_component_layout_findings,
    validate_render_observation,
)
from app.services.gis_harness.repair_planner import (
    LOOP_EXHAUSTED,
    LOOP_NO_PROGRESS,
    LOOP_OK,
    MAX_REPAIR_ATTEMPTS_PER_FINDING,
    classify_repair,
    evaluate_repair_loop,
    finding_fingerprint,
    plan_repairs,
)
from app.services.gis_harness.workflow_v4.recompute import (
    WorkflowChange,
    compute_affected_subgraph,
)
from app.services.mapspec.lifecycle_engine import (
    LOCK_CONFLICT_CODE,
    guard_locked_partitions,
)


# ── 共享 fake 载荷（确定性内联夹具；遥测形状复用 test_render_telemetry_v5 模式）──


def _chapter(layer_ids: List[str] | None = None) -> Dict[str, Any]:
    return {
        "map_layers": [
            {"layer_id": lid, "role": "primary", "enabled": True, "visible": True}
            for lid in (layer_ids or ["lyr-thematic"])
        ],
    }


def _mapspec(layer_ids: List[str] | None = None) -> Dict[str, Any]:
    return {
        "layers": [
            {"id": lid, "type": "fill", "source": f"src-{lid}",
             "paint": {}, "layout": {"visibility": "visible"}}
            for lid in (layer_ids or ["lyr-thematic"])
        ],
    }


def _observation(layer_ids: List[str] | None = None, **extra: Any) -> Dict[str, Any]:
    lids = layer_ids or ["lyr-thematic"]
    return {
        "source": "frontend_runtime",
        "sequence": 1,
        "mapspec_revision": 3,
        "layers": [
            {"id": lid, "runtime_store_id": lid, "visible": True,
             "runtime_layer_count": 1, "source_converged": True,
             "render_complete": True, "style_converged": True,
             **extra}
            for lid in lids
        ],
        "components": [{"id": "title-main", "type": "title", "mounted": True}],
    }


def _ready_result() -> MapCompletionResult:
    return MapCompletionResult(
        status=STATUS_COMPLETE,
        findings=[],
        layer_status="valid",
        component_status="valid",
        render_status="verified",
    )


# ── Scenario 1：专题图全链 READY ──────────────────────────────────────────


def test_scenario1_thematic_full_chain_ready() -> None:
    """NL 专题图 → 分析 → MapSpec → fake telemetry → READY 全链不断裂。"""
    chapter = _chapter()
    mapspec = _mapspec()
    obs = _observation()
    status, findings = validate_render_observation(
        chapter, mapspec, obs, 3, [["title"]])
    assert status == "verified"
    assert findings == []
    result = _ready_result()
    assert collect_unified_findings(result=result) == []
    out = derive_product_verdict(result, [], chapter={})
    assert out["verdict"] == VERDICT_READY
    assert out["finding_counts"] == {"errors": 0, "warnings": 0}


# ── Scenario 2：CRS 错误重投影 ────────────────────────────────────────────


def test_scenario2_crs_mismatch_reproject_and_continue() -> None:
    """EPSG:3857 产物 → crs_not_wgs84 → reproject（pi_recompute，语义保持）。"""
    mapspec = _mapspec()
    mapspec["layers"][0]["provenance"] = {"result_ref": "ref:3857"}
    records = {"ref:3857": SimpleNamespace(crs="EPSG:3857", status="valid")}
    findings = validate_semantics(_chapter(), mapspec, [["title"]], records=records)
    crs = [f for f in findings if f.code == F_CRS_NOT_WGS84]
    assert len(crs) == 1 and crs[0].severity == "warning"
    action = classify_repair(from_completion_finding(crs[0]))
    assert action.repair_class == "reproject"
    assert action.safety == "semantics_preserving"
    assert action.executor == "pi_recompute"
    # 重投影后（记录回到 4326）→ 零 finding，可继续终验。
    records_ok = {"ref:3857": SimpleNamespace(crs="EPSG:4326", status="valid")}
    assert validate_semantics(_chapter(), mapspec, [["title"]],
                              records=records_ok) == []


# ── Scenario 3：改参只重算子图 ────────────────────────────────────────────


def _param_dag() -> Dict[str, Any]:
    return {
        "nodes": [
            {"node_id": "data:schools", "kind": "data_input",
             "depends_on": []},
            {"node_id": "cap:density_mapping", "kind": "analysis",
             "parameters": [{"name": "radius"}], "depends_on": []},
            {"node_id": "cap:summary_stats", "kind": "analysis",
             "depends_on": []},
            {"node_id": "output:density_surface", "kind": "output",
             "depends_on": []},
        ],
        "edges": [
            {"from": "data:schools.output", "to": "cap:density_mapping.input"},
            {"from": "cap:density_mapping.output",
             "to": "output:density_surface.product"},
        ],
    }


def test_scenario3_param_change_recomputes_affected_subgraph_only() -> None:
    """改 radius 参数 → 密度节点 + 下游输出重算，无关分支复用。"""
    plan = compute_affected_subgraph(
        _param_dag(),
        [WorkflowChange(dimension="parameter", target_kind="parameter",
                        target="radius")],
    )
    assert "cap:density_mapping" in plan.recompute
    assert "output:density_surface" in plan.recompute
    assert "parameter" in plan.changed_dimensions
    assert "cap:summary_stats" not in plan.recompute
    assert "cap:summary_stats" in plan.reuse
    assert "data:schools" in plan.reuse  # 上游证据未动，可复用


# ── Scenario 4：改色带零重跑 ──────────────────────────────────────────────


def test_scenario4_style_only_change_zero_scientific_recompute() -> None:
    """仅改色带（呈现态 revision 推进、行零变化）→ 科学计算零重跑。

    复用 W5 ``test_style_inert_revision_bump_no_recompute`` 模式：style-only
    变化根本不产生 WorkflowChange（changes == []），无 analysis 节点 stale。
    """
    from app.services.gis_harness.runtime_bridge import derive_runtime_block

    dag = {
        "nodes": [
            {"node_id": "data:schools", "kind": "data_input", "role": "schools",
             "capability": "", "depends_on": []},
            {"node_id": "cap:density_mapping", "kind": "analysis",
             "capability": "density_mapping", "role": "", "depends_on": []},
            {"node_id": "output:density_surface", "kind": "output",
             "capability": "", "role": "", "depends_on": []},
        ],
        "edges": [
            {"from": "data:schools.data", "to": "cap:density_mapping.input"},
            {"from": "cap:density_mapping.output",
             "to": "output:density_surface.product"},
        ],
        "primary_output": "output:density_surface",
        "validation_violations": [],
    }

    class _FakeCompilation:
        methodology_family = "distribution_mapping"
        method_qualification = {"selected_id": "m1"}
        typed_dag = dag
        package_fingerprint = "pkg-fp-w16"
        compiler_version = "4.0.0"

    def _compile(query: str, **_: Any) -> _FakeCompilation:
        return _FakeCompilation()

    chapter = {
        "plan_id": "plan-w16-s4",
        "recipe_id": "",
        "query": "成都小学的分布情况",
        "status": "finalized",
        "data_requirements": [
            {"capability": "schools_data", "purpose": "schools",
             "status": "available", "bound_ref": "ref:schools-1",
             "resolved_algorithm": "", "resolved_tool": "",
             "depends_on": [], "params": {}},
        ],
        "analysis_steps": [
            {"capability": "density_mapping", "purpose": "密度图",
             "status": "done", "bound_ref": "ref:density-1",
             "resolved_algorithm": "kernel_density", "resolved_tool": "",
             "depends_on": ["schools_data"], "optional": False, "params": {}},
        ],
        "map_layers": [],
        "components": [],
    }
    first = derive_runtime_block(chapter, compile_fn=_compile)
    assert first is not None
    # 仅改色带 = mapspec revision 推进、行零变化 → 科学重算零触碰。
    block = derive_runtime_block(
        chapter, stored=first, mapspec_revision=7, render_seq=3,
        compile_fn=_compile)
    assert block is not None
    assert block["changes"] == []
    assert block["recompute_plan"] == {}
    assert not [n for n in block["nodes"]
                if n.get("kind") == "analysis" and n.get("state") == "stale"]


# ── Scenario 5：source 失败修复 ───────────────────────────────────────────


def test_scenario5_source_failure_repair_then_verified() -> None:
    """source 加载失败 → render finding（自愈词表）→ 修复通道 → verified。"""
    bad = _observation(source_status="error")
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), bad, 3, [])
    assert status == "issues"
    codes = {(f.code, f.severity) for f in findings}
    assert (F_RENDER_SOURCE_MISSING, "error") in codes
    assert F_RENDER_SOURCE_MISSING in RUNTIME_RENDER_CODES
    action = classify_repair(from_completion_finding(
        next(f for f in findings if f.code == F_RENDER_SOURCE_MISSING)))
    assert action.executor == "runtime_repair"  # 既有 runtime 通道，无第三通道
    assert action.safety != "not_allowed"
    # source 恢复后重观测 → verified，可继续终验。
    status_ok, findings_ok = validate_render_observation(
        _chapter(), _mapspec(), _observation(), 3, [])
    assert status_ok == "verified"
    assert findings_ok == []


# ── Scenario 6：空图表不得 READY ──────────────────────────────────────────


def test_scenario6_empty_chart_never_ready() -> None:
    """chart data_points=0 → error finding → 裁决 NEEDS_REPAIR，绝不 READY。"""
    obs = _observation()
    obs["components"] = [{"id": "chart-1", "type": "chart_panel",
                          "mounted": True}]
    obs["charts"] = [{"id": "chart-1", "rendered": False, "data_points": 0}]
    status, findings = validate_render_observation(
        _chapter(), _mapspec(), obs, 3, [["chart_panel"]])
    assert status == "issues"
    assert any(f.code == F_CHART_DATA_MISSING and f.severity == "error"
               for f in findings)
    result = MapCompletionResult(
        status=STATUS_NEEDS_REPAIR,
        findings=[MapCompletionFinding(code=F_CHART_DATA_MISSING,
                                       severity="error", target="chart_panel",
                                       detail="chart render still pending")],
        layer_status="valid",
        component_status="valid",
        render_status="issues",
    )
    out = derive_product_verdict(result, [], chapter={})
    assert out["verdict"] == VERDICT_NEEDS_REPAIR
    assert out["verdict"] != VERDICT_READY


# ── Scenario 7：视觉重叠修复重渲 ──────────────────────────────────────────


def _overlap_comps() -> List[Dict[str, Any]]:
    return [
        {"id": "chart-1", "type": "chart_panel", "enabled": True,
         "mounted": True, "anchor": "floating", "floating": True,
         "rect": {"x": 10, "y": 10, "width": 100, "height": 80}},
        {"id": "stats-1", "type": "statistics_panel", "enabled": True,
         "mounted": True, "anchor": "floating", "floating": True,
         "rect": {"x": 50, "y": 40, "width": 100, "height": 80}},
    ]


def test_scenario7_visual_overlap_relayout_and_rerender() -> None:
    """组件重叠 → layout_conflict → relayout 修复 → 重渲后零 finding。"""
    findings = derive_component_layout_findings(
        {"mapspec_revision": 3, "components": _overlap_comps(),
         "layers": [], "runtime_errors": []})
    assert any(f.code == F_LAYOUT_CONFLICT and f.severity == "warning"
               for f in findings)
    action = classify_repair(from_completion_finding(
        next(f for f in findings if f.code == F_LAYOUT_CONFLICT)))
    assert action.repair_class == "relayout_component"
    assert action.safety == "presentation_only"
    assert action.executor == "quality_loop"
    # 修复（拖开 stats-1）后重渲 → 零 finding。
    fixed = _overlap_comps()
    fixed[1] = {**fixed[1], "rect": {"x": 400, "y": 300,
                                     "width": 100, "height": 80}}
    assert derive_component_layout_findings(
        {"mapspec_revision": 3, "components": fixed,
         "layers": [], "runtime_errors": []}) == []


# ── Scenario 8：锁图层（W15 test_lock_guard 模式薄封装）───────────────────


def _uf(code: str, *, entity: str = "") -> UnifiedFinding:
    return UnifiedFinding(
        domain="harness_finalizer", code=code, severity="warning", source="t",
        scope="layer", affected_entity=entity, evidence="ev",
        blocks_completion=False,
    )


def test_scenario8_locked_layer_repair_refused() -> None:
    """用户锁图层 → 统一 guard 分区 + planner user-wins，agent 不得修改。"""
    part = guard_locked_partitions(
        {"workbench": {"version": 5, "lockedLayerIds": ["locked-lyr"]}},
        layer_ids=["locked-lyr", "free-lyr"])
    assert part.has_locked
    assert part.locked_layer_ids == ["locked-lyr"]
    assert part.allowed_layer_ids == ["free-lyr"]
    assert part.disclosure()["code"] == LOCK_CONFLICT_CODE == "layer_locked"
    action = classify_repair(
        _uf("layer_hidden", entity="locked-lyr"),
        locked_entities=frozenset({"locked-lyr"}))
    assert action.safety == "not_allowed"
    assert action.executor == "none"
    assert "layer_locked" in action.detail
    assert "user-wins" in action.detail
    plan = plan_repairs(
        [_uf("layer_hidden", entity="locked-lyr"),
         _uf("layer_hidden", entity="free-lyr")],
        locked_entities=frozenset({"locked-lyr"}))
    assert [a.target for a in plan.refused] == ["locked-lyr"]
    assert [a.target for a in plan.actions] == ["free-lyr"]


# ── Scenario 9：中断恢复（W14 Scenario 9 模式薄封装）──────────────────────


class _AnchorDb:
    """极简 async DB 桩（复用 W14 test_resume_verify_v6._FakeDb 模式）。"""

    def __init__(self) -> None:
        self.rows: Dict[str, Any] = {}

    def add(self, row: Any) -> None:
        if not getattr(row, "id", None):
            row.id = str(uuid.uuid4())
        self.rows[row.id] = row

    async def commit(self) -> None:
        return None

    async def refresh(self, row: Any) -> None:
        return None

    async def get(self, model: Any, pk: str) -> Any:
        return self.rows.get(pk)

    class _Result:
        def __init__(self, row: Any) -> None:
            self._row = row

        def scalar_one_or_none(self) -> Any:
            return self._row

    async def execute(self, stmt: Any) -> Any:
        from app.models.project import WorkflowResumeAnchor

        sid = None
        try:
            for desc in stmt.whereclause.clauses:
                if "session_id" in str(desc.left):
                    sid = desc.right.value
        except Exception:  # noqa: BLE001 — 解析失败则取唯一行
            sid = None
        row = next(
            (r for r in self.rows.values()
             if isinstance(r, WorkflowResumeAnchor)
             and (sid is None or r.session_id == sid)),
            None,
        )
        return self._Result(row)


def _payload(tag: str) -> Dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"tag": tag},
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]}},
        ],
    }


@pytest.mark.asyncio
async def test_scenario9_interrupt_resume_continue_full_green() -> None:
    """三节点 DAG 执行 2/3 → 建锚 → 中断 → resume → fake executor 跑完，全绿。"""
    from app.services.gis_harness.resume_anchor import (
        resume_from_anchor,
        save_anchor,
    )
    from app.services.gis_harness.resume_verify import VERDICT_LIVE
    from app.services.session_data import session_data_manager
    from app.services.session_plan import (
        SessionPlan,
        load_session_plan,
        save_session_plan,
    )

    caps = ["load_data", "interpolate", "compose_map"]
    deps = [("interpolate", "load_data"), ("compose_map", "interpolate")]
    sid = f"v6-w16-s9-{uuid.uuid4().hex[:6]}"
    await session_data_manager.clear_session(sid)
    instance = {
        "instance_id": "inst-w16", "plan_id": "plan-w16", "state_revision": 1,
        "rows_fingerprint": "rows-fp-w16",
        "stages": [
            {"capability": cap, "kind": "analysis", "state": "pending",
             "bound_ref": "", "resolved_algorithm": f"alg-{cap}"}
            for cap in caps
        ],
        "dependencies": [
            {"consumer": c, "producer": p, "state": "satisfied"} for c, p in deps
        ],
    }
    await save_session_plan(SessionPlan(
        envelope_id="sp-w16-s9", session_id=sid,
        user_goal="中断恢复 E2E",
        gis_chapter={"query": "中断恢复", "workflow_instance": instance},
        progress=[],
    ))
    executed = {}
    for cap in caps[:2]:  # 执行 2/3：前两节点 satisfied 并绑定真实产物
        executed[cap] = await session_data_manager.store(sid, _payload(cap))
    plan = await load_session_plan(sid)
    assert plan is not None and isinstance(plan.gis_chapter, dict)
    for s in plan.gis_chapter["workflow_instance"]["stages"]:
        if s["capability"] in executed:
            s["state"] = "satisfied"
            s["bound_ref"] = executed[s["capability"]]
    await save_session_plan(plan)

    db = _AnchorDb()
    saved = await save_anchor(db, session_id=sid, user_id="u-w16")
    assert set(executed.values()) <= set(saved["anchor"]["ref_ids"])

    # 中断：旧 session 全清（进程重启语义），唯一幸存是 DB 锚点。
    await session_data_manager.clear_session(sid)
    assert await load_session_plan(sid) is None

    result = await resume_from_anchor(
        db, anchor_id=saved["anchor_id"], user_id="u-w16")
    new_sid = result["session_id"]
    restored = await load_session_plan(new_sid)
    assert restored is not None and isinstance(restored.gis_chapter, dict)
    assert [s["capability"]
            for s in restored.gis_chapter["workflow_instance"]["stages"]] == caps
    assert result["missing_refs"] and not result["restored_refs"]
    assert result["workflow_verify"]["verdict"] == VERDICT_LIVE
    by_cap = {s["capability"]: s
              for s in restored.gis_chapter["workflow_instance"]["stages"]}
    assert by_cap["load_data"]["state"] == "stale"
    assert by_cap["interpolate"]["state"] == "stale"
    assert set(result["stale_nodes"]) >= {"load_data", "interpolate"}
    assert set(result["recompute_plan"].get("recompute", [])) >= {
        "load_data", "interpolate"}
    assert by_cap["compose_map"]["state"] == "pending"

    # fake executor：按拓扑序重算 stale + 推进 pending（确定性夹具）。
    for cap in caps:
        fresh = await load_session_plan(new_sid)
        assert fresh is not None and isinstance(fresh.gis_chapter, dict)
        stages = fresh.gis_chapter["workflow_instance"]["stages"]
        stage = next(s for s in stages if s["capability"] == cap)
        assert stage["state"] in ("stale", "pending"), (cap, stage["state"])
        stage["state"] = "satisfied"
        stage["bound_ref"] = await session_data_manager.store(
            new_sid, _payload(f"re-{cap}"))
        stage.pop("stale_reason", None)
        await save_session_plan(fresh)

    final = await load_session_plan(new_sid)
    assert final is not None and isinstance(final.gis_chapter, dict)
    final_stages = final.gis_chapter["workflow_instance"]["stages"]
    assert all(s["state"] == "satisfied" for s in final_stages)
    for s in final_stages:
        assert await session_data_manager.get(new_sid, s["bound_ref"]) is not None

    await session_data_manager.clear_session(new_sid)


# ── Scenario 10：重复 repair 无进展自动停止（W11 账本口）──────────────────


def test_scenario10_repeated_repair_without_progress_stops() -> None:
    """同 finding 同 epoch 重复 → no_progress；达上限 → exhausted → 披露停止。"""
    assert MAX_REPAIR_ATTEMPTS_PER_FINDING == 3
    fp = finding_fingerprint(_uf("runtime_node_stale", entity="cap:x"))
    ledger: Dict[str, Any] = {}
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="1:3")
    assert verdicts[fp] == LOOP_OK
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="1:3")
    assert verdicts[fp] == LOOP_NO_PROGRESS  # 状态未动：无进展
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="2:3")
    assert verdicts[fp] == LOOP_OK  # epoch 推进 → 重新 ok
    ledger, verdicts = evaluate_repair_loop(ledger, [fp], state_epoch="3:3")
    assert verdicts[fp] == LOOP_EXHAUSTED  # 尝试耗尽 → 自动停止
    uf = UnifiedFinding(
        domain="workflow_runtime", code="runtime_node_stale",
        severity="warning", source="t", scope="node",
        affected_entity="cap:x", evidence="ev", blocks_completion=True,
    )
    plan = plan_repairs([uf], loop_verdicts={finding_fingerprint(uf):
                                             LOOP_EXHAUSTED})
    assert not plan.actions
    assert plan.exhausted == [finding_fingerprint(uf)]
    assert plan.refused[0].repair_class == "abort_with_disclosure"
    assert plan.refused[0].safety == "not_allowed"
    assert plan.refused[0].executor == "none"
