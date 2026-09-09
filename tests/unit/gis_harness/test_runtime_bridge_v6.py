"""Compiler V4 → Runtime bridge（V6 Wave 1–2）回归锁。

不变式（对应 02-runtime-unification.md 验收）：
1. typed DAG node_id 是运行态共享命名空间：cap:<capability> / data:<role>
   节点在运行态块中携带与 plan 行一致的状态（StageState 词汇，与
   WorkflowInstance 同一映射源）；
2. 证据漂移（satisfied 行被重绑/换参）只把受影响 typed 子图标 stale，
   无关分支零触碰；
3. 同输入同块（确定性：revision 不涨、指纹稳定）；
4. 方法族未映射 / 无计划 → 诚实缺席（None），不虚构运行态；
5. kill switch（GIS_WORKFLOW_RUNTIME_V6=0）整体关停；
6. 服务入口持久化单键 + 门去重 + 行变化重派生。

纯函数测试经 compile_fn 注入（零 IO）；服务入口测试用真 in-process
store（无 Redis/DB）。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.gis_harness.runtime_bridge import (
    WORKFLOW_RUNTIME_KEY,
    derive_runtime_block,
    maybe_update_runtime_projection,
)


# ── 编译产物构造器（compile_fn 注入形状）────────────────────────────────

class _FakeCompilation:
    """compile_workflow_v4 的最小形状（typed_dag 为 bounded dict 契约）。"""

    def __init__(self, dag: Dict[str, Any], *, family: str = "distribution_mapping",
                 package_fp: str = "pkg-fp-1", selected: str = "m1") -> None:
        self.methodology_family = family
        self.method_qualification = {"selected_id": selected}
        self.typed_dag = dag
        self.package_fingerprint = package_fp
        self.compiler_version = "4.0.0"


def _dag(*, with_second_branch: bool = False) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = [
        {"node_id": "data:schools", "kind": "data_input", "role": "schools",
         "capability": "", "depends_on": []},
        {"node_id": "cap:density_mapping", "kind": "analysis",
         "capability": "density_mapping", "role": "",
         "depends_on": []},
        {"node_id": "output:density_surface", "kind": "output",
         "capability": "", "role": "", "depends_on": []},
    ]
    edges = [
        {"from": "data:schools.data", "to": "cap:density_mapping.input"},
        {"from": "cap:density_mapping.output",
         "to": "output:density_surface.product"},
    ]
    if with_second_branch:
        nodes.append({"node_id": "cap:chart_render", "kind": "analysis",
                      "capability": "chart_render", "role": "",
                      "depends_on": []})
        edges.append({"from": "cap:density_mapping.output",
                      "to": "cap:chart_render.input"})
    return {"nodes": nodes, "edges": edges,
            "primary_output": "output:density_surface",
            "validation_violations": []}


def _chapter(
    *,
    plan_id: str = "plan-v6",
    query: str = "成都小学的分布情况",
    req_status: str = "available",
    req_ref: str = "ref:schools-1",
    step_status: str = "done",
    step_ref: str = "ref:density-1",
    step_params: Any = None,
    extra_rows: bool = False,
) -> Dict[str, Any]:
    # 生产形状（planner + _mark_progress 语义）：数据能力与分析能力是两行
    # 独立 capability；分析步骤经 depends_on 消费数据能力的产出。
    chapter: Dict[str, Any] = {
        "plan_id": plan_id,
        "recipe_id": "",
        "query": query,
        "status": "finalized",
        "data_requirements": [
            {"capability": "schools_data", "purpose": "schools",
             "status": req_status, "bound_ref": req_ref,
             "resolved_algorithm": "", "resolved_tool": "",
             "depends_on": [], "params": {}},
        ],
        "analysis_steps": [
            {"capability": "density_mapping", "purpose": "密度图",
             "status": step_status, "bound_ref": step_ref,
             "resolved_algorithm": "kernel_density", "resolved_tool": "",
             "depends_on": ["schools_data"], "optional": False,
             "params": step_params if step_params is not None else {}},
        ],
        "map_layers": [],
        "components": [],
    }
    if extra_rows:
        chapter["analysis_steps"].append(
            {"capability": "chart_render", "purpose": "图表",
             "status": "done", "bound_ref": "ref:chart-1",
             "resolved_algorithm": "bar_chart", "resolved_tool": "",
             "depends_on": ["density_mapping"], "optional": False,
             "params": {}})
    return chapter


def _compile_for(dag: Dict[str, Any], **kw: Any):
    def _compile(query: str, *, recipe_id: str = "", **_: Any) -> _FakeCompilation:
        return _FakeCompilation(dag, **kw)
    return _compile


def _node(block: Dict[str, Any], node_id: str) -> Dict[str, Any]:
    return next(n for n in block["nodes"] if n["node_id"] == node_id)


# ── 1. 共享命名空间 + 状态投影 ──────────────────────────────────────────

def test_analysis_node_state_from_plan_rows() -> None:
    block = derive_runtime_block(_chapter(), compile_fn=_compile_for(_dag()))
    assert block is not None
    cap = _node(block, "cap:density_mapping")
    assert cap["state"] == "satisfied"
    # 该 capability 唯一行的绑定 ref（分析产出）。
    assert cap["bound_ref"] == "ref:density-1"
    # 输入血缘：depends_on 上游（schools_data）的绑定 ref。
    assert cap["inputs"] == ["ref:schools-1"]
    assert cap["evidence"]
    # output 节点跟随产出者状态（artifact 存在性 = 产出者 bound_ref 事实）
    out = _node(block, "output:density_surface")
    assert out["state"] == "satisfied"
    assert out["bound_ref"] == "ref:density-1"
    assert block["schema"] == "workflow_runtime.v1"
    assert block["runtime_revision"] == 1


def test_pending_rows_project_pending_state() -> None:
    ch = _chapter(req_status="pending", req_ref="",
                  step_status="pending", step_ref="")
    block = derive_runtime_block(ch, compile_fn=_compile_for(_dag()))
    assert block is not None
    # 上游 schools_data 未满足 → 依赖它的 density_mapping 停留 pending
    # （plan_graph ready 派生同一语义：deps satisfied 才 ready）。
    assert _node(block, "cap:density_mapping")["state"] == "pending"
    # output 跟随产出者（非 satisfied）
    assert _node(block, "output:density_surface")["state"] == "pending"


def test_unmapped_role_disclosed_honestly() -> None:
    # 无 recipe → 无 role→capability 映射 → data:schools 进 unmapped
    block = derive_runtime_block(_chapter(), compile_fn=_compile_for(_dag()))
    assert block is not None
    assert "data:schools" in block["unmapped"]
    assert _node(block, "data:schools")["state"] == "pending"


# ── 2. 证据漂移 → 只污染受影响子图 ──────────────────────────────────────

def test_evidence_drift_marks_stale_and_downstream_only() -> None:
    ch = _chapter(extra_rows=True)
    dag = _dag(with_second_branch=True)
    first = derive_runtime_block(ch, compile_fn=_compile_for(dag))
    assert first is not None
    assert _node(first, "cap:density_mapping")["state"] == "satisfied"
    assert _node(first, "cap:chart_render")["state"] == "satisfied"

    # 重绑 satisfied 行（换 ref/换参 → 行签名漂移）
    drifted = _chapter(step_ref="ref:density-2",
                       step_params={"radius": 500}, extra_rows=True)
    block = derive_runtime_block(
        drifted, stored=first, compile_fn=_compile_for(dag))
    assert block is not None
    cap = _node(block, "cap:density_mapping")
    assert cap["state"] == "stale"
    assert cap["stale_reason"] == "evidence_drift"
    # typed 边下游（output 与 chart 支路）被污染
    out = _node(block, "output:density_surface")
    assert out["state"] == "stale"
    assert out["stale_reason"] == "upstream_stale"
    chart = _node(block, "cap:chart_render")
    assert chart["state"] == "stale"
    # revision 推进（内容变化）
    assert block["runtime_revision"] == first["runtime_revision"] + 1


def test_unrelated_change_does_not_stale() -> None:
    ch = _chapter(extra_rows=True)
    dag = _dag(with_second_branch=True)
    first = derive_runtime_block(ch, compile_fn=_compile_for(dag))
    assert first is not None
    # chart 支路重绑（density 不变）→ density 分支零触碰
    drifted = _chapter(extra_rows=True)
    drifted["analysis_steps"][1]["bound_ref"] = "ref:chart-2"
    block = derive_runtime_block(
        drifted, stored=first, compile_fn=_compile_for(dag))
    assert block is not None
    assert _node(block, "cap:chart_render")["state"] == "stale"
    assert _node(block, "cap:density_mapping")["state"] == "satisfied"


# ── 2b. W3 双向 lineage ──────────────────────────────────────────────────

def _mapspec() -> Dict[str, Any]:
    return {
        "sources": {
            "src-density": {"ref": "ref:density-1"},
            "src-chart": {"ref": "ref:chart-1"},
        },
        "layers": [
            {"id": "layer-density", "source": "src-density"},
            {"id": "layer-base", "source": "src-basemap"},
        ],
        "layout": {
            "components": [
                {"id": "chart-1", "type": "chart_panel",
                 "options": {"chartRef": "ref:chart-1"}},
                {"id": "legend-1", "type": "legend", "options": {}},
            ],
        },
    }


def test_artifact_index_forward_and_reverse() -> None:
    ch = _chapter(extra_rows=True)
    dag = _dag(with_second_branch=True)
    block = derive_runtime_block(
        ch, mapspec=_mapspec(), compile_fn=_compile_for(dag))
    assert block is not None
    idx = block["artifact_index"]
    # 正向：chart 节点消费 density 产物（depends_on 行的 bound_ref）
    chart = _node(block, "cap:chart_render")
    assert chart["inputs"] == ["ref:density-1"]
    # 反向：ref:density-1 的生产节点 / 消费节点 / 依赖图层
    entry = idx["ref:density-1"]
    assert entry["producer_node"] == "cap:density_mapping"
    assert "cap:chart_render" in entry["consumer_nodes"]
    assert entry["layer_ids"] == ["layer-density"]
    # chartRef → 组件反查
    assert idx["ref:chart-1"]["component_ids"] == ["chart-1"]
    # 无 spec 引用（basemap）不进索引
    assert "layer-base" not in str(idx.get("src-basemap", ""))


def test_lineage_query_helpers() -> None:
    from app.services.gis_harness.runtime_bridge import (
        artifact_lineage,
        node_lineage,
    )

    ch = _chapter(extra_rows=True)
    dag = _dag(with_second_branch=True)
    block = derive_runtime_block(
        ch, mapspec=_mapspec(), compile_fn=_compile_for(dag))
    assert block is not None
    # 节点正查：消费/产出/依赖图层
    lin = node_lineage(block, "cap:density_mapping")
    assert lin is not None
    assert lin["output_ref"] == "ref:density-1"
    assert lin["layer_ids"] == ["layer-density"]
    assert "cap:chart_render" in lin["consumer_nodes"]
    # artifact 反查
    back = artifact_lineage(block, "ref:chart-1")
    assert back is not None
    assert back["producer_node"] == "cap:chart_render"
    assert back["component_ids"] == ["chart-1"]
    # 缺席 → None（不虚构）
    assert artifact_lineage(block, "ref:missing") is None
    assert node_lineage(block, "cap:missing") is None


def test_no_mapspec_lineage_degrades_honestly() -> None:
    block = derive_runtime_block(
        _chapter(extra_rows=True),
        compile_fn=_compile_for(_dag(with_second_branch=True)))
    assert block is not None
    # 无 spec：节点血缘仍在（行 bound_ref），图层/组件映射为空
    entry = block["artifact_index"]["ref:density-1"]
    assert entry["producer_node"] == "cap:density_mapping"
    assert entry["layer_ids"] == []
    assert entry["component_ids"] == []


# ── 2c. W4 变更分类 + RecomputePlan / W5 reuse validation ────────────────

def _drift_block(ch: Dict[str, Any], dag: Dict[str, Any], **kw: Any) -> Dict[str, Any]:
    first = derive_runtime_block(ch, compile_fn=_compile_for(dag))
    assert first is not None
    block = derive_runtime_block(
        kw.get("drifted", ch), stored=first,
        records=kw.get("records"),
        compile_fn=_compile_for(dag))
    assert block is not None
    return block


def test_change_classification_algorithm() -> None:
    ch = _chapter()
    drifted = _chapter()
    drifted["analysis_steps"][0]["resolved_algorithm"] = "natural_breaks"
    block = _drift_block(ch, _dag(), drifted=drifted)
    changes = block["changes"]
    assert len(changes) == 1
    assert changes[0]["dimension"] == "algorithm"
    assert changes[0]["target"] == "cap:density_mapping"
    plan = block["recompute_plan"]
    assert "cap:density_mapping" in plan["recompute"]
    assert "output:density_surface" in plan["recompute"]
    assert "algorithm" in plan["changed_dimensions"]


def test_change_classification_parameter() -> None:
    ch = _chapter()
    drifted = _chapter(step_params={"radius": 500})
    block = _drift_block(ch, _dag(), drifted=drifted)
    assert block["changes"][0]["dimension"] == "parameter"
    assert _node(block, "cap:density_mapping")["state"] == "stale"


def test_change_classification_data_revision() -> None:
    ch = _chapter()
    # 绑定 ref 修订（换 ref、算法/参数不变）→ data 维 + 节点翻 stale。
    drifted_block = _drift_block(ch, _dag(), drifted=_chapter(step_ref="ref:density-2"))
    assert drifted_block["changes"][0]["dimension"] == "data"
    assert drifted_block["changes"][0]["target"] == "cap:density_mapping"
    assert _node(drifted_block, "cap:density_mapping")["state"] == "stale"


def test_style_inert_revision_bump_no_recompute() -> None:
    # style-only 变化（mapspec revision 推进、行零变化）不得触发科学重算。
    first = derive_runtime_block(_chapter(), compile_fn=_compile_for(_dag()))
    assert first is not None
    block = derive_runtime_block(
        _chapter(), stored=first, mapspec_revision=7, render_seq=3,
        compile_fn=_compile_for(_dag()))
    assert block is not None
    assert block["changes"] == []
    assert block["recompute_plan"] == {}
    assert all(n["state"] == "satisfied" for n in block["nodes"]
               if n["kind"] != "data_input")


def test_reuse_validation_safe_unknown_unsafe() -> None:
    class _Rec:
        def __init__(self, status: str) -> None:
            self.status = status

    ch = _chapter()
    # 无 records 快照 → unknown（证据不足不假设）
    block = derive_runtime_block(ch, compile_fn=_compile_for(_dag()))
    assert block is not None
    rv = block["reuse_validation"]["cap:density_mapping"]
    assert rv["verdict"] == "unknown"
    assert rv["checks"]["artifact_health"] == "unknown"
    # 健康记录 → safe（package 首代 unknown 兜底不影响——unknown 非 unsafe）
    records = {"ref:density-1": _Rec("valid")}
    block2 = derive_runtime_block(
        ch, stored=block, records=records, compile_fn=_compile_for(_dag()))
    assert block2 is not None
    rv2 = block2["reuse_validation"]["cap:density_mapping"]
    assert rv2["checks"]["artifact_health"] == "healthy"
    assert rv2["checks"]["workflow_package"] == "stable"
    assert rv2["verdict"] == "safe"
    # 终态坏记录 → unsafe：节点翻 stale + 强制进 recompute
    bad = {"ref:density-1": _Rec("superseded")}
    block3 = derive_runtime_block(
        ch, stored=block2, records=bad, compile_fn=_compile_for(_dag()))
    assert block3 is not None
    node3 = _node(block3, "cap:density_mapping")
    assert node3["state"] == "stale"
    assert node3["stale_reason"] == "reuse_unsafe:artifact_health"
    assert "cap:density_mapping" in block3["recompute_plan"]["recompute"]


def test_format_recompute_line() -> None:
    from app.services.gis_harness.runtime_bridge import format_recompute_line

    ch = _chapter()
    block = derive_runtime_block(ch, compile_fn=_compile_for(_dag()))
    assert block is not None
    ch[WORKFLOW_RUNTIME_KEY] = block
    assert format_recompute_line(ch) == ""  # 无债零噪声
    drifted = derive_runtime_block(
        _chapter(step_ref="ref:density-2"), stored=block,
        compile_fn=_compile_for(_dag()))
    assert drifted is not None
    ch[WORKFLOW_RUNTIME_KEY] = drifted
    line = format_recompute_line(ch)
    assert "[GIS Recompute]" in line
    assert "cap:density_mapping" in line
    assert "dims=data" in line


# ── 3. 确定性 ────────────────────────────────────────────────────────────

def test_deterministic_same_input_same_block() -> None:
    a = derive_runtime_block(_chapter(), compile_fn=_compile_for(_dag()))
    b = derive_runtime_block(_chapter(), compile_fn=_compile_for(_dag()))
    assert a == b
    again = derive_runtime_block(
        _chapter(), stored=a, compile_fn=_compile_for(_dag()))
    assert again is not None
    # 二代块多一条 package 稳定裁决（stored 在场才可知）→ 内容指纹真实
    # 变化，一次性 revision 推进；此后同输入必须稳定（不再涨）。
    third = derive_runtime_block(
        _chapter(), stored=again, compile_fn=_compile_for(_dag()))
    assert third is not None
    assert third["runtime_revision"] == again["runtime_revision"]
    assert third["state_fingerprint"] == again["state_fingerprint"]


# ── 4. 诚实缺席 ──────────────────────────────────────────────────────────

def test_no_plan_id_returns_none() -> None:
    ch = _chapter()
    ch.pop("plan_id")
    assert derive_runtime_block(ch, compile_fn=_compile_for(_dag())) is None


def test_unmapped_family_returns_none() -> None:
    def _compile_no_family(query: str, *, recipe_id: str = "", **_: Any):
        return _FakeCompilation(_dag(), family="")
    assert derive_runtime_block(
        _chapter(), compile_fn=_compile_no_family) is None


def test_compile_failure_returns_none() -> None:
    def _boom(query: str, *, recipe_id: str = "", **_: Any):
        raise RuntimeError("compiler exploded")
    assert derive_runtime_block(_chapter(), compile_fn=_boom) is None


# ── 5/6. 服务入口（真 in-process store）─────────────────────────────────

@pytest.fixture
async def clean_session():
    import shutil
    import uuid

    sid = f"rtb-svc-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    try:
        from app.lib.data.large_data import BASE_STORAGE_DIR
        d = BASE_STORAGE_DIR / sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001 — 清理失败不影响测试结果
        pass


async def _save_plan(sid: str, chapter: Dict[str, Any]) -> None:
    from app.services.session_plan import SessionPlan, save_session_plan

    await save_session_plan(SessionPlan(
        envelope_id=f"env-{chapter['plan_id']}",
        session_id=sid,
        user_goal=str(chapter.get("query") or "goal"),
        gis_chapter=chapter,
    ))


@pytest.mark.asyncio
async def test_service_persists_block_and_gate_skips(clean_session, monkeypatch):
    import app.services.gis_harness.runtime_bridge as bridge

    monkeypatch.setattr(bridge, "derive_runtime_block", None)  # 防误用
    monkeypatch.setattr(
        bridge, "derive_runtime_block",
        lambda chapter, **kw: derive_runtime_block(
            chapter, compile_fn=_compile_for(_dag()),
            mapspec_revision=kw.get("mapspec_revision", 0),
            render_seq=kw.get("render_seq", 0),
            stored=kw.get("stored")),
    )
    from app.services.session_plan import load_session_plan

    await _save_plan(clean_session, _chapter())
    block = await maybe_update_runtime_projection(clean_session, reason="test")
    assert block is not None
    assert block["schema"] == "workflow_runtime.v1"
    fresh = await load_session_plan(clean_session)
    assert fresh.gis_chapter[WORKFLOW_RUNTIME_KEY]["state_fingerprint"] == \
        block["state_fingerprint"]
    # 门：无变化 → 跳过
    again = await maybe_update_runtime_projection(clean_session, reason="test")
    assert again is None


@pytest.mark.asyncio
async def test_service_real_compile_end_to_end(clean_session):
    """真实 compile_workflow_v4（memo 内确定性）+ 真实章节形状（取自
    编译器自身 plan 投影 —— 与生产 planner 同源）。"""
    from app.services.gis_harness.workflow_v4.compiler_v4 import (
        compile_workflow_v4,
    )
    from app.services.session_plan import load_session_plan

    profile = {"featureCount": 120, "geometryTypes": ["Point"],
               "fields": {"school_name": {"type": "string"}}}
    c = compile_workflow_v4("成都小学的分布情况", profile=profile)
    assert c.methodology_family
    chapter: Dict[str, Any] = {
        "plan_id": "plan-real",
        "recipe_id": c.base.recipe_id,
        "query": "成都小学的分布情况",
        "status": "finalized",
        "data_requirements": c.base.plan.get("data_requirements") or [],
        "analysis_steps": c.base.plan.get("analysis_steps") or [],
        "map_layers": [],
        "components": [],
    }
    await _save_plan(clean_session, chapter)
    block = await maybe_update_runtime_projection(clean_session, reason="test")
    assert block is not None
    assert block["methodology_family"] == c.methodology_family
    # 包指纹对编译输入敏感（bridge 以 query+recipe 重编译，无 profile）：
    # 与同输入重编译一致且稳定（deterministic 契约）。
    recompiled = compile_workflow_v4(
        "成都小学的分布情况", recipe_id=c.base.recipe_id)
    assert block["package_fingerprint"] == recompiled.package_fingerprint
    # typed 节点全覆盖：所有 analysis 节点都有运行态（pending/ready 合法）
    analysis = [n for n in block["nodes"] if n["kind"] == "analysis"]
    assert analysis
    assert all(n["state"] for n in analysis)
    fresh = await load_session_plan(clean_session)
    assert WORKFLOW_RUNTIME_KEY in fresh.gis_chapter


@pytest.mark.asyncio
async def test_kill_switch_disables_service(clean_session, monkeypatch):
    await _save_plan(clean_session, _chapter())
    monkeypatch.setenv("GIS_WORKFLOW_RUNTIME_V6", "0")
    assert await maybe_update_runtime_projection(clean_session, reason="t") is None
