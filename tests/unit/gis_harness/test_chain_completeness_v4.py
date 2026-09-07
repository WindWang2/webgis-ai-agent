"""18 阶段证据链完整性（V4 Wave 8 / ADR-0104 决策 9）回归锁。

验收（任务书 Wave 8）：
- 关键 GIS 场景 trace completeness ≥95%（18 阶段全发射 = 1.0；显式
  N/A 阶段可从分母扣除 —— 缺发射绝不伪装成 N/A）；
- 链可持久化（会话 JSONL）→ 离线门可回归；
- 全部发射 best-effort：turn 上下文缺席 → 静默跳过，绝不伪造链。
"""
from __future__ import annotations

import uuid

import pytest


from app.lib.runtime.chain_emitters import emit_chain, emit_chain_once
from app.lib.runtime.context import bind_runtime_context
from app.lib.runtime.gis_trace import Stage, get_gis_trace_registry


def _bind_turn():
    import contextlib

    turn_id = f"w8-{uuid.uuid4().hex[:10]}"
    stack = contextlib.ExitStack()
    stack.enter_context(
        bind_runtime_context(turn_id=turn_id, session_id=f"w8-sess-{turn_id}")
    )
    return turn_id, stack


def _drain_chain(turn_id: str) -> dict:
    chain = get_gis_trace_registry().get(turn_id)
    assert chain is not None
    d = chain.as_dict()
    get_gis_trace_registry().drop(turn_id)
    return d


def test_all_18_stages_reachable_via_emitters():
    """18 个规范阶段全部可通过发射面覆盖（门 1.0 的可达性证明）。"""
    turn_id, stack = _bind_turn()
    try:
        for stage in Stage:
            emit_chain(stage, probe=True)
        d = _drain_chain(turn_id)
        assert d["completeness"] == 1.0, sorted(
            s.get("stage") for s in d["stages"])
    finally:
        stack.__exit__(None, None, None)


def test_emit_without_context_is_honest_noop():
    """无 turn 上下文 → 不发射（不伪造链、不落孤立链）。"""
    ok = emit_chain(Stage.USER_INTENT, query="孤儿发射必须被拒绝")
    assert ok is False


def test_emit_chain_once_dedupes_per_stage():
    turn_id, stack = _bind_turn()
    try:
        assert emit_chain_once(Stage.SELECTED_WORKFLOW, recipe_id="a") is True
        assert emit_chain_once(Stage.SELECTED_WORKFLOW, recipe_id="b") is False
        d = _drain_chain(turn_id)
        sel = [s for s in d["stages"] if s["stage"] == "SELECTED_WORKFLOW"]
        assert len(sel) == 1 and sel[0]["recipe_id"] == "a"
    finally:
        stack.__exit__(None, None, None)


def test_planner_emits_intent_to_workflow_stages():
    """规划路径发射阶段 1-6（USER_INTENT → SELECTED_WORKFLOW）。"""
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    turn_id, stack = _bind_turn()
    try:
        intent = resolve_map_request_intent("成都小学分布密度热力图")
        MapProductPlanner().plan_from_intent(intent, use_memo=False)
        d = _drain_chain(turn_id)
        covered = {s.get("stage") for s in d["stages"]}
        for stage in (
            "USER_INTENT", "PARSED_INTENT", "TASK_ONTOLOGY",
            "CANDIDATE_WORKFLOWS", "SELECTED_WORKFLOW",
        ):
            assert stage in covered, f"planner must emit {stage}"
    finally:
        stack.__exit__(None, None, None)


def test_planner_finalize_emits_data_profile():
    from app.services.gis_harness.intent import resolve_map_request_intent
    from app.services.gis_harness.planner import MapProductPlanner

    turn_id, stack = _bind_turn()
    try:
        intent = resolve_map_request_intent("成都小学分布密度热力图")
        plan = MapProductPlanner().plan_from_intent(intent, use_memo=False)
        MapProductPlanner().finalize_with_profile(
            plan,
            {"geometry": "point", "featureCount": 40, "crs": "EPSG:4326",
             "fields": {"students": {"type": "numeric"}}},
        )
        d = _drain_chain(turn_id)
        covered = {s.get("stage") for s in d["stages"]}
        assert "DATA_PROFILE" in covered
        prof = next(s for s in d["stages"] if s["stage"] == "DATA_PROFILE")
        assert prof["feature_count"] == 40
    finally:
        stack.__exit__(None, None, None)


def test_finalize_emits_verification_repair_verdict_stages():
    """终验发射阶段 15/16/17（VERIFICATION / REPAIR / FINAL_VERDICT）。"""
    turn_id, stack = _bind_turn()
    try:
        # 直接以完整章节驱动 run_map_finalization 的发射面不依赖 session
        # 存储 —— 用最小 MapCompletionResult 走 emit 面等价：这里走管线级
        # 单元（pipeline 的 emit 块在 status 定格后执行）。
        from app.services.gis_harness.completion.contracts import (
            MapCompletionResult,
        )
        from app.services.gis_harness.completion.pipeline import _emit_finalization_chain

        result = MapCompletionResult(
            status="complete", summary="ok", render_status="verified",
            final_map_status="verified", product_verdict="READY",
            repairs_applied=["add_component:legend"],
        )
        _emit_finalization_chain(result, passes=1)
        d = _drain_chain(turn_id)
        covered = {s.get("stage") for s in d["stages"]}
        for stage in ("VERIFICATION", "REPAIR", "FINAL_VERDICT"):
            assert stage in covered, f"finalization must emit {stage}"
        fv = next(s for s in d["stages"] if s["stage"] == "FINAL_VERDICT")
        assert fv["verdict"] == "READY"
    finally:
        stack.__exit__(None, None, None)


# ── 持久化 + 离线门 ──────────────────────────────────────────────────────

def test_persist_and_gate_roundtrip(tmp_path, monkeypatch):
    from app.services.gis_harness import trace_store

    sid = f"w8-gate-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    monkeypatch.delenv("GIS_TRACE_PERSIST", raising=False)
    turn_id, stack = _bind_turn()
    try:
        for stage in Stage:
            emit_chain(stage, probe=True)
        d = _drain_chain(turn_id)
        d["session_id"] = sid
        assert trace_store.persist_chain(d, session_id=sid) is True
    finally:
        stack.__exit__(None, None, None)
    chains = trace_store.read_chains(sid)
    assert len(chains) == 1
    from app.evaluation.chain_gate import run_chain_gate_for_session

    report = run_chain_gate_for_session(sid)
    assert report["passed"] is True
    assert report["per_chain"][0]["completeness"] == 1.0


def test_gate_explicit_na_excluded_and_disclosed(tmp_path, monkeypatch):
    """显式 N/A 从分母扣除（无 LLM 的脚本场景 → MODEL_ROUTING 不适用）；
    缺发射不算 N/A —— 门如实失败。"""
    sid = f"w8-na-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    from app.services.gis_harness import trace_store

    chain = {
        "turn_id": "t1", "session_id": sid,
        "stages": [
            {"stage": Stage.USER_INTENT.name, "stage_id": 1, "ts": 0},
            {"stage": Stage.SELECTED_WORKFLOW.name, "stage_id": 6, "ts": 0},
        ],
    }
    trace_store.persist_chain(chain, session_id=sid)
    from app.evaluation.chain_gate import run_chain_gate_for_session

    report = run_chain_gate_for_session(sid, na_stages={"MODEL_ROUTING"})
    assert report["passed"] is False  # 2/17 ≪ 0.95：如实失败
    assert report["na_stages"] == ["MODEL_ROUTING"]
    assert report["per_chain"][0]["effective_total"] == 17


def test_persist_rotation_bounded(tmp_path, monkeypatch):
    from app.services.gis_harness import trace_store

    sid = f"w8-rot-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    for i in range(trace_store.MAX_RECORDS_PER_SESSION + 10):
        trace_store.persist_chain(
            {"turn_id": f"t{i}", "session_id": sid, "stages": []},
            session_id=sid,
        )
    chains = trace_store.read_chains(sid)
    assert len(chains) <= trace_store.MAX_RECORDS_PER_SESSION
    assert chains[-1]["turn_id"] == f"t{trace_store.MAX_RECORDS_PER_SESSION + 9}"


def test_kill_switch_disables_persistence(tmp_path, monkeypatch):
    from app.services.gis_harness import trace_store

    sid = f"w8-off-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("GIS_TRACE_PERSIST", "0")
    assert trace_store.persist_chain(
        {"turn_id": "t", "session_id": sid, "stages": []}, session_id=sid
    ) is False
    assert trace_store.read_chains(sid) == []


def test_replay_exposes_chain_completeness_report(tmp_path, monkeypatch):
    """review Round-1 MAJOR：门在 replay 评测面可达（ADR 决策 9 的接线承诺）。"""
    import contextlib
    import uuid

    from app.evaluation import replay
    from app.services.gis_harness import trace_store

    sid = f"w8-replay-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    turn_id = f"w8r-{uuid.uuid4().hex[:8]}"
    with contextlib.ExitStack() as stack:
        stack.enter_context(bind_runtime_context(
            turn_id=turn_id, session_id=sid))
        for stage in Stage:
            emit_chain(stage, probe=True)
        d = get_gis_trace_registry().get(turn_id).as_dict()
        get_gis_trace_registry().drop(turn_id)
    d["session_id"] = sid
    assert trace_store.persist_chain(d, session_id=sid)

    report = replay.chain_completeness_report(sid)
    assert report["passed"] is True
    assert report["per_chain"][0]["completeness"] == 1.0
    # 无链会话：空报告，不伪造通过
    assert replay.chain_completeness_report(f"{sid}-empty")["passed"] is False


# ── 真实 seam 场景（review R3 MAJOR：门必须吃真实链，不是自证环）─────────

@pytest.mark.asyncio
async def test_real_seam_scenario_gate(tmp_path, monkeypatch):
    """真实工具路径 turn：ToolDispatchService 真派发 webgis_map_intent /
    webgis_map_product + 真终验 + 真持久化 → 链含核心阶段（planner/调度/
    终验/输出族），并经 replay.chain_completeness_report 消费。

    无 LLM 路由/提示面/观察回执的 headless 场景：MODEL_ROUTING /
    TOOL_SURFACE / MAP_OBSERVATION / USER_OUTPUT 显式 N/A（分母扣除）。
    """
    import contextlib
    import shutil
    import uuid

    from app.evaluation import replay
    from app.services.gis_harness import trace_store
    from app.services.gis_harness.workflow_instance import (
        maybe_update_workflow_instance,
    )
    from app.services.gis_harness.map_completion import maybe_finalize_map_product
    from app.services.tool_dispatch_service import ToolDispatchService
    from app.services.session_data import session_data_manager

    sid = f"w8-real-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))
    turn_id = f"w8real-{uuid.uuid4().hex[:8]}"
    try:
        await session_data_manager.clear_session(sid)
        from app.tools import init_tools
        from app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        init_tools(registry)
        service = ToolDispatchService(registry=registry)
        executed: set = set()
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6 + (i % 5) * 0.01]},
             "properties": {"name": f"s{i}", "students": 100 + i}}
            for i in range(30)
        ]}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")

        with contextlib.ExitStack() as stack:
            stack.enter_context(bind_runtime_context(
                turn_id=turn_id, session_id=sid))
            r1 = await service.dispatch(
                {"id": "c1", "function": {"name": "webgis_map_intent",
                 "arguments": {"query": "成都小学分布密度热力图"}}},
                sid, executed,
            )
            assert r1.status == "ok", r1.error_msg
            # 生产路径：bridge 在 dispatch 后落账 SessionPlan（同一入口）
            from app.services.session_plan import apply_tool_result

            await apply_tool_result(
                sid, "webgis_map_intent", r1.raw_result, success=True)
            r2 = await service.dispatch(
                {"id": "c2", "function": {"name": "webgis_map_product",
                 "arguments": {"query": "成都小学分布密度热力图",
                               "primary_ref": ref}}},
                sid, executed,
            )
            assert r2.status == "ok", r2.error_msg
            await apply_tool_result(
                sid, "webgis_map_product", r2.raw_result, success=True,
                geojson_ref=r2.geojson_ref)
            # 把全部能力行带到生产终态（与 finalization 场景种子同型）——
            # DAG 未终态时 finalizer 毫秒级返回 pending（不发链）。
            from app.services.session_plan import load_session_plan, save_session_plan

            plan2 = await load_session_plan(sid)
            for row in plan2.gis_chapter.get("data_requirements") or []:
                row["status"] = "available"
                row.setdefault("bound_ref", ref)
            for row in plan2.gis_chapter.get("analysis_steps") or []:
                row["status"] = "done"
                row.setdefault("bound_ref", ref)
            await save_session_plan(plan2)
            # 生产路径的真实终验触发（bridge/observation 同一入口）
            completion = await maybe_finalize_map_product(
                sid, reason="real_seam_test", force=True)
            assert completion is not None and completion.status in (
                "complete", "needs_repair", "failed")
            await maybe_update_workflow_instance(sid, reason="real_seam_test")
            # 链持久化（bridge settle 同一入口）
            from app.services.gis_harness.trace_store import persist_turn_chain

            persist_turn_chain(turn_id, session_id=sid)
        report = replay.chain_completeness_report(
            sid,
            expected_stages={
                "USER_INTENT", "PARSED_INTENT", "TASK_ONTOLOGY",
                "CANDIDATE_WORKFLOWS", "SELECTED_WORKFLOW",
                "TOOL_CALLS", "ARGUMENTS", "TOOL_RESULTS",
                "VERIFICATION", "FINAL_VERDICT",
            },
            na_stages={
                "MODEL_ROUTING", "TOOL_SURFACE",           # 无 LLM/提示面
                "MAP_OBSERVATION", "USER_OUTPUT",          # 无前端回执/桥收尾
                "REPAIR",                                   # 干净场景无修复
            },
        )
        assert report["passed"], report["per_chain"][0]
    finally:
        await session_data_manager.clear_session(sid)
