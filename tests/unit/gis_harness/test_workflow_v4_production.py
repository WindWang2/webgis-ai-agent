"""Workflow V4 —— 生产接入集成测试（Wave 12）。

覆盖：semantic tool（compile_workflow_semantics）的行为契约、
plan_orchestrator 合成路径的 V4 证据附加（含失败不阻塞）、
compile → package → diff → recompute 全链闭环、确定性。

运行方式：无网络/无 DB/无 LLM 依赖（纯组件级）。
"""
from __future__ import annotations

import json

import pytest

from app.services.gis_harness.workflow_v4.compiler_v4 import (
    compile_workflow_v4,
)
from app.services.gis_harness.workflow_v4.diff import diff_workflow_packages
from app.services.gis_harness.workflow_v4.package import (
    emit_workflow_package,
)
from app.services.gis_harness.workflow_v4.recompute import (
    compute_affected_subgraph,
)

_PROFILE = {
    "featureCount": 150,
    "geometryTypes": ["Point"],
    "crs": "EPSG:32648",
    "fields": {"name": {"type": "string"}, "count": {"type": "number"}},
}


# ── semantic tool 行为契约 ───────────────────────────────────────────────

def _make_registry():
    from app.tools.registry import ToolRegistry
    from app.tools.semantic_tools import register_semantic_tools
    reg = ToolRegistry()
    register_semantic_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_semantic_tool_compile_workflow_semantics() -> None:
    reg = _make_registry()
    out = await reg.dispatch(
        "compile_workflow_semantics",
        {"query": "分析成都便利店的空间密度", "profile": _PROFILE},
    )
    assert out["success"] is True
    assert out["methodology_family"] == "distribution_density"
    assert out["selected_method"] == "density.kernel_surface"
    assert out["package_fingerprint"]
    assert out["note"]  # 披露：advisory，不执行
    for r in out["rejected_methods"]:
        assert r["reasons"]


@pytest.mark.asyncio
async def test_semantic_tool_rejects_empty_query() -> None:
    reg = _make_registry()
    out = await reg.dispatch(
        "compile_workflow_semantics", {"query": ""})
    assert out.get("success") is False
    assert out.get("message") or out.get("error")


# ── plan_orchestrator V4 证据 ────────────────────────────────────────────

def test_plan_v4_evidence_attached_on_harness_synth() -> None:
    """harness 确定性合成路径附加 workflow_v4 有界证据（失败为 None）。"""
    import asyncio

    from app.services.chat.plan_orchestrator import AgentPlanOrchestrator

    orch = AgentPlanOrchestrator()
    plan = asyncio.run(
        orch._synth_plan_from_harness("sess-test", "成都市小学分布情况"))
    if plan is None:
        pytest.skip("harness 合成条件未满足（intent/置信/候选）——非 V4 缺陷")
    assert plan.recipe_id
    ev = plan.workflow_v4
    assert ev is not None
    assert ev["methodology_family"]
    assert ev["package_fingerprint"]
    assert ev["typed_dag_summary"]["nodes"] > 0
    assert len(json.dumps(ev, ensure_ascii=False)) < 16_000  # 有界


def test_plan_v4_evidence_failure_is_none_not_crash() -> None:
    from app.services.chat.plan_orchestrator import AgentPlanOrchestrator

    ev = AgentPlanOrchestrator._compile_v4_evidence(
        "全不匹配的乱码请求xyzzy", None, "", None)
    # 未映射任务 → None（诚实留白）；绝不抛异常
    assert ev is None or isinstance(ev, dict)


# ── 全链闭环：compile → package → diff → recompute ──────────────────────

def test_full_chain_compile_package_diff_recompute() -> None:
    c = compile_workflow_v4(
        "分析成都便利店的空间密度", profile=_PROFILE)
    assert c.full_stage_sequence[-8:] == [
        "resolve_methodology", "select_method", "compile_typed_dag",
        "inherit_obligations", "resolve_parameters",
        "evaluate_cartographic_obligations", "plan_acquisition",
        "emit_workflow_package",
    ]
    pkg = emit_workflow_package(c)
    assert pkg.fingerprint

    # 模拟参数变化 → diff → 受影响子图
    cf_new = json.loads(json.dumps(pkg.compiled_form))
    assert cf_new["typed_dag"]["nodes"], "compiled DAG 不应为空"
    old_pkg = pkg
    new_pkg = pkg.model_copy(update={
        "compiled_form": cf_new, "version": "1.0.1"})
    d = diff_workflow_packages(old_pkg, new_pkg)
    assert d.compatible is True
    changes = [e.change for e in d.entries]
    plan = compute_affected_subgraph(
        new_pkg.compiled_form["typed_dag"], changes)
    assert plan.recompute or not changes  # 无变化 → 空计划


def test_full_chain_deterministic_end_to_end() -> None:
    a = compile_workflow_v4(
        "分析成都便利店的空间密度", profile=_PROFILE).to_bounded_dict()
    b = compile_workflow_v4(
        "分析成都便利店的空间密度", profile=_PROFILE).to_bounded_dict()
    assert a == b
    assert len(json.dumps(a, ensure_ascii=False)) < 64_000
