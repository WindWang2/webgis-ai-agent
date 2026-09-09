"""Harness V6 Wave 17 性能结构契约（§58 synthetic structural benchmark）。

非 wall-clock 契约（照抄 test_runtime_v2_perf_contracts.py 纪律）：断言对象
只有调用计数 / 条目数 / 字节数 / 长度，零计时。合成结构数据，不做真实
万级渲染 / 真实超大图。

- PCL-1：500/1k/10k layers 下 derive_runtime_block 调用计数恒 1、块节点数
  与 N 无关、artifact_index 有界（≤96，首 64 层相同时跨 N 全等）。
- PCL-2：lineage 查询输出有界（per-entry 列表 ≤8、键白名单、缺席 None）。
- PCL-3：200 节点大 DAG 下 compute_affected_subgraph 闭包精确（保守正确）
  + to_bounded_dict 截断有界（recompute ≤32、explanations ≤6、dims ≤5）。
- PCL-4：10 层产消链 deep lineage，20 次查询全中有界且生产/消费精确。
- PCL-5：long chat 级超大输入下 v6 三块字节 cap 恒生效（输出 ≤ cap）。
- PCL-6：大 registry（真实 registry，init_tools 全量）下语义索引只建一次
  （embed 批量调用 ==1，二次 query 零重建）。
- PCL-7：50 findings 下 collect 截断生效（默认预算 24，error 优先）。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest


# ── 合成编译产物（compile_fn 注入形状；调用可计数）────────────────────────

class _FakeCompilation:
    def __init__(self, dag: Dict[str, Any]) -> None:
        self.methodology_family = "distribution_mapping"
        self.method_qualification = {"selected_id": "m1"}
        self.typed_dag = dag
        self.package_fingerprint = "pkg-perf-1"
        self.compiler_version = "4.0.0"


def _dag3() -> Dict[str, Any]:
    return {
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


class _CountingCompile:
    """compile_fn 注入桩：调用计数 + 固定 DAG（零 I/O）。"""

    def __init__(self, dag: Dict[str, Any]) -> None:
        self.calls = 0
        self._dag = dag

    def __call__(self, query: str, *, recipe_id: str = "", **_: Any) -> _FakeCompilation:
        self.calls += 1
        return _FakeCompilation(self._dag)


def _chapter() -> Dict[str, Any]:
    return {
        "plan_id": "plan-perf",
        "recipe_id": "",
        "query": "性能契约合成查询",
        "status": "finalized",
        "data_requirements": [
            {"capability": "schools_data", "purpose": "schools",
             "status": "available", "bound_ref": "ref:perf-d-1",
             "resolved_algorithm": "", "resolved_tool": "",
             "depends_on": [], "params": {}},
        ],
        "analysis_steps": [
            {"capability": "density_mapping", "purpose": "密度图",
             "status": "done", "bound_ref": "ref:perf-a-1",
             "resolved_algorithm": "kernel_density", "resolved_tool": "",
             "depends_on": ["schools_data"], "optional": False, "params": {}},
        ],
        "map_layers": [],
        "components": [],
    }


def _synthetic_mapspec(n_layers: int) -> Dict[str, Any]:
    """合成 mapspec：N 个图层 + sources 字典（首 64 层跨 N 全等）。"""
    layers = [
        {"id": f"perf-lyr-{i}", "type": "circle", "source": f"perf-src-{i}",
         "paint": {"circle-color": "#00aa00"}}
        for i in range(n_layers)
    ]
    sources = {
        f"perf-src-{i}": {"id": f"perf-src-{i}", "ref": f"ref:perf-lyr-{i}"}
        for i in range(n_layers)
    }
    return {"layers": layers, "sources": sources, "version": 5}


# ── PCL-1：层规模无关的 finalize/索引契约 ────────────────────────────────

@pytest.mark.perf
def test_pcl1_layer_scale_finalize_counts_independent_of_n():
    """N=500/1k/10k：compile 恰调 1 次；块节点数跨 N 全等；索引 ≤96 且跨 N 全等。"""
    from app.services.gis_harness.runtime_bridge import derive_runtime_block

    dag = _dag3()
    per_n: Dict[int, Dict[str, Any]] = {}
    for n in (500, 1000, 10000):
        compile_fn = _CountingCompile(dag)
        block = derive_runtime_block(
            _chapter(), mapspec=_synthetic_mapspec(n), compile_fn=compile_fn)
        assert block is not None, f"n={n} 合成输入应产出运行态块"
        assert compile_fn.calls == 1, f"n={n} 派生只做一次编译（实测 {compile_fn.calls}）"
        assert len(block["nodes"]) == 3, f"n={n} 块节点数只跟 DAG 走（实测 {len(block['nodes'])}）"
        assert len(block["artifact_index"]) <= 96, (
            f"n={n} artifact_index 必须有界（实测 {len(block['artifact_index'])}）")
        per_n[n] = block
    node_ids = {n: [x["node_id"] for x in b["nodes"]] for n, b in per_n.items()}
    assert node_ids[500] == node_ids[1000] == node_ids[10000], "块节点与 N 无关"
    index_keys = {n: sorted(b["artifact_index"]) for n, b in per_n.items()}
    assert index_keys[500] == index_keys[1000] == index_keys[10000], (
        "首 64 层相同时索引键跨 N 全等（层处理只取 [:64] 有界窗口）")


# ── PCL-2：lineage 查询有界 ─────────────────────────────────────────────

@pytest.mark.perf
def test_pcl2_lineage_query_output_bounded():
    """1k 层块上 40+3 次 lineage 查询：输出键白名单、列表 ≤8、缺席 None、零异常。"""
    from app.services.gis_harness.runtime_bridge import (
        artifact_lineage,
        derive_runtime_block,
        node_lineage,
    )

    block = derive_runtime_block(
        _chapter(), mapspec=_synthetic_mapspec(1000),
        compile_fn=_CountingCompile(_dag3()))
    assert block is not None
    queries = 0
    for ref in sorted(block["artifact_index"])[:40]:
        hit = artifact_lineage(block, ref)
        queries += 1
        assert hit is not None
        assert set(hit) == {"ref", "producer_node", "consumer_nodes",
                            "layer_ids", "component_ids"}
        assert len(hit["consumer_nodes"]) <= 8
        assert len(hit["layer_ids"]) <= 8
        assert len(hit["component_ids"]) <= 8
    for node in block["nodes"]:
        hit = node_lineage(block, node["node_id"])
        queries += 1
        assert hit is not None
        assert set(hit) == {"node_id", "state", "stale_reason", "inputs",
                            "output_ref", "layer_ids", "component_ids",
                            "consumer_nodes"}
        assert len(hit["inputs"]) <= 8
    assert queries == 43, "查询次数固定（40 索引 + 3 节点），与层数无关"
    assert artifact_lineage(block, "ref:perf-no-such-ref") is None
    assert node_lineage(block, "cap:no-such-node") is None
    assert artifact_lineage(None, "ref:x") is None
    assert node_lineage(None, "cap:x") is None


# ── PCL-3：200 节点大 DAG 受影响子图 ────────────────────────────────────

def _chain_dag(n: int) -> Dict[str, Any]:
    nodes = [{"node_id": f"n{i:03d}", "parameters": [{"name": f"p{i:03d}"}]}
             for i in range(n)]
    edges = [{"from": f"n{i:03d}.output", "to": f"n{i + 1:03d}.input"}
             for i in range(n - 1)]
    return {"nodes": nodes, "edges": edges}


@pytest.mark.perf
def test_pcl3_large_dag_affected_subgraph_exact_and_bounded():
    """200 节点链：头部变更全图重算、中部变更精确分区、未知目标 no-op、输出截断有界。"""
    from app.services.gis_harness.workflow_v4.recompute import (
        WorkflowChange,
        compute_affected_subgraph,
    )

    dag = _chain_dag(200)
    all_nodes = sorted(f"n{i:03d}" for i in range(200))

    head = compute_affected_subgraph(
        dag, [WorkflowChange(dimension="data", target_kind="node", target="n000")])
    assert head.recompute == all_nodes, "头部变更保守全图重算"
    assert head.reuse == []

    mid = compute_affected_subgraph(
        dag, [WorkflowChange(dimension="parameter", target_kind="parameter",
                             target="p100")])
    assert mid.recompute == sorted(f"n{i:03d}" for i in range(100, 200)), (
        "参数变更只污染拥有者下游")
    assert mid.reuse == sorted(f"n{i:03d}" for i in range(100)), "上游精确复用"
    assert len(mid.recompute) + len(mid.reuse) == 200, "重算/复用恰为全图划分"

    noop = compute_affected_subgraph(
        dag, [WorkflowChange(dimension="output", target_kind="node",
                             target="n999")])
    assert noop.recompute == [] and len(noop.reuse) == 200
    assert noop.explanations == ["node:n999 → 图中无对应节点（no-op）"]

    multi = compute_affected_subgraph(dag, [
        WorkflowChange(dimension="data", target_kind="node", target="n010"),
        WorkflowChange(dimension="algorithm", target_kind="algorithm", target="n020"),
        WorkflowChange(dimension="style", target_kind="style", target="n030"),
    ])
    assert len(multi.explanations) == 3, "每变更恰一条解释（无放大）"
    bounded = multi.to_bounded_dict()
    assert len(bounded["recompute"]) <= 32
    assert len(bounded["reuse"]) <= 32
    assert len(bounded["reuse_artifacts"]) <= 8
    assert len(bounded["changed_dimensions"]) <= 5
    assert len(bounded["explanations"]) <= 6
    again = compute_affected_subgraph(dag, [
        WorkflowChange(dimension="data", target_kind="node", target="n010"),
        WorkflowChange(dimension="algorithm", target_kind="algorithm", target="n020"),
        WorkflowChange(dimension="style", target_kind="style", target="n030"),
    ])
    assert again.to_bounded_dict() == bounded, "纯函数确定性"


# ── PCL-4：10 层产消链 deep lineage ─────────────────────────────────────

def _chain_block(depth: int = 10) -> Dict[str, Any]:
    nodes = []
    index = {}
    for i in range(depth):
        ref = f"ref:perf-chain-{i}"
        inputs = [f"ref:perf-chain-{i - 1}"] if i else []
        nodes.append({"node_id": f"cap:chain-{i}", "state": "satisfied",
                      "stale_reason": "", "bound_ref": ref, "inputs": inputs})
        index[ref] = {
            "producer_node": f"cap:chain-{i}",
            "consumer_nodes": [f"cap:chain-{i + 1}"] if i + 1 < depth else [],
            "layer_ids": [f"perf-chain-lyr-{i}"],
            "component_ids": [],
        }
    return {"nodes": nodes, "artifact_index": index}


@pytest.mark.perf
def test_pcl4_deep_lineage_chain_queries_bounded():
    """10 层链上 20 次查询：生产/消费精确、输出有界、查询次数固定。"""
    from app.services.gis_harness.runtime_bridge import (
        artifact_lineage,
        node_lineage,
    )

    block = _chain_block(10)
    for i in range(10):
        node = node_lineage(block, f"cap:chain-{i}")
        assert node is not None
        assert node["output_ref"] == f"ref:perf-chain-{i}"
        assert node["inputs"] == ([f"ref:perf-chain-{i - 1}"] if i else [])
        assert node["consumer_nodes"] == ([f"cap:chain-{i + 1}"] if i < 9 else [])
        assert node["layer_ids"] == [f"perf-chain-lyr-{i}"]
        art = artifact_lineage(block, f"ref:perf-chain-{i}")
        assert art is not None
        assert art["producer_node"] == f"cap:chain-{i}"
        assert art["consumer_nodes"] == ([f"cap:chain-{i + 1}"] if i < 9 else [])
    # 链首无输入、链尾无消费（边界精确，无越界编造）
    assert node_lineage(block, "cap:chain-0")["inputs"] == []
    assert artifact_lineage(block, "ref:perf-chain-9")["consumer_nodes"] == []


# ── PCL-5：long chat 级输入下三块字节 cap ───────────────────────────────

@pytest.mark.perf
def test_pcl5_long_chat_block_byte_caps_hold():
    """超大输入（400 参数/300 进度行/200 诊断/百 KB 文本）下三块输出恒 ≤ cap。"""
    from app.services.chat.v6_context_blocks import (
        MAP_SITUATION_BLOCK_MAX_BYTES,
        NODE_LOCAL_BLOCK_MAX_BYTES,
        WORKFLOW_GLOBAL_BLOCK_MAX_BYTES,
        blocks_metric,
        build_map_situation_block,
        build_node_local_block,
        build_workflow_global_block,
    )
    from app.services.session_plan import CapabilityProgress, SessionPlan

    assert NODE_LOCAL_BLOCK_MAX_BYTES == 2048
    assert WORKFLOW_GLOBAL_BLOCK_MAX_BYTES == 4096
    assert MAP_SITUATION_BLOCK_MAX_BYTES == 2048

    giant_params = {f"p{i:03d}": "v" * 60 for i in range(400)}
    node = build_node_local_block({
        "node_id": "cap:perf-node", "capability": "perf", "state": "running",
        "ports_in": ["input"], "ports_out": ["output"], "params": giant_params,
        "obligations": [f"obligation-{i}-{'x' * 40}" for i in range(50)],
        "bound_ref": "ref:perf-a-1", "input_refs": ["ref:perf-d-1"],
        "tool": "perf_tool", "algorithm": "perf_alg", "failure": "",
    })
    assert node is not None
    assert node.byte_len <= node.byte_cap == NODE_LOCAL_BLOCK_MAX_BYTES
    assert node.truncated is True, "24KB+ 参数输入必须触发截断留痕"

    long_text = "长会话背景文本。" * 8000  # ~100KB：模拟 long chat 上下文压力
    plan = SessionPlan(
        envelope_id="sp-perf", session_id="perf-sid", user_goal=long_text,
        gis_chapter={
            "plan_id": "plan-perf", "query": long_text,
            "data_requirements": [
                {"capability": f"perf-cap-{i}", "purpose": "y" * 200,
                 "status": "pending", "bound_ref": "", "depends_on": [],
                 "params": {}} for i in range(200)],
            "analysis_steps": [],
        },
        progress=[CapabilityProgress(capability=f"perf-cap-{i}-{'z' * 120}",
                                     status="pending") for i in range(300)],
    )
    workflow = build_workflow_global_block(plan)
    assert workflow is not None
    assert workflow.byte_len <= workflow.byte_cap == WORKFLOW_GLOBAL_BLOCK_MAX_BYTES

    diagnostics = [{"code": f"perf_code_{i}", "severity": "warning",
                    "detail": "d" * 500, "layer_id": f"perf-lyr-{i}"}
                   for i in range(200)]
    situation = build_map_situation_block(
        map_state={"_cartographic_mutation_revision": 7},
        review={"cartography": {"status": "passed"}},
        current_fingerprint="fp-perf",
        chapter={"map_product": {"render_status": "r" * 200}},
        render_diagnostics=diagnostics,
    )
    assert situation.byte_len <= situation.byte_cap == MAP_SITUATION_BLOCK_MAX_BYTES

    metric = blocks_metric([node, workflow, situation])
    assert metric["total_byte_cost"] == node.byte_len + workflow.byte_len + situation.byte_len
    assert set(metric) >= {"node_local_byte_cost", "workflow_global_byte_cost",
                           "map_situation_byte_cost", "total_byte_cost"}


# ── PCL-6：大 registry 语义索引只建一次 ─────────────────────────────────

class _CountingEmbed:
    """确定性假编码器：常量向量（全命中）+ 调用/文本计数。"""

    def __init__(self) -> None:
        self.calls = 0
        self.texts = 0

    def __call__(self, texts) -> List[List[float]]:
        self.calls += 1
        bucket = list(texts)
        self.texts += len(bucket)
        return [[1.0, 0.0, 0.0, 0.0] for _ in bucket]


@pytest.mark.perf
def test_pcl6_large_registry_semantic_index_builds_once():
    """真实全量 registry：一次批量建索引（embed 调用 ==1），二次 query 零重建。"""
    from app.services.chat.tool_semantic_retrieval import ToolSemanticIndex
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    init_tools(registry)
    names = registry.list_tools()
    assert len(names) >= 200, f"大 registry 基数（实测 {len(names)}）"
    visible = [n for n in names if registry.descriptor(n).model_visible]
    assert len(visible) >= 200

    embed = _CountingEmbed()
    index = ToolSemanticIndex(embed_fn=embed)
    index.build_if_stale(registry)
    assert embed.calls == 1, f"建索引恰一次批量 embed（实测 {embed.calls}）"
    assert embed.texts == len(visible), "批量文本数 == 模型可见工具数"
    assert len(index._names) <= 512, "索引工具数硬顶 512"
    assert set(index._names) == set(visible), "索引恰为模型可见投影（不多不少）"
    key = index._key
    assert key, "建成后指纹键非空"

    first = index.query(registry, "perf query", 10000)
    assert embed.calls == 2, "首次 query 只多一次 query 向量编码"
    assert {h.name for h in first} == set(visible), "常量编码器下全可见工具命中"
    second = index.query(registry, "perf query", 10000)
    assert embed.calls == 3, "二次 query 零重建（只多 query 向量编码）"
    assert index._key == key, "registry 未变指纹键稳定"
    assert [h.name for h in second] == [h.name for h in first], "同输入同输出"


# ── PCL-7：50 findings 收集截断 ─────────────────────────────────────────

def _finding(code: str, severity: str, i: int) -> SimpleNamespace:
    return SimpleNamespace(code=code, severity=severity,
                           target=f"perf-entity-{i}", repair="",
                           detail=f"perf-detail-{i}")


@pytest.mark.perf
def test_pcl7_multiple_findings_collect_capped():
    """50 findings：默认预算截断到 24 且 error 优先；自定义预算同样有界；确定性。"""
    from app.services.gis_harness.completion.unified_findings import (
        collect_unified_findings,
    )

    findings = ([_finding("layer_missing", "error", i) for i in range(10)]
                + [_finding("stale_overlay", "warning", i) for i in range(40)])
    result = SimpleNamespace(findings=findings)
    out = collect_unified_findings(result=result)
    assert len(out) == 24, f"默认预算 24 截断（实测 {len(out)}）"
    assert all(f.severity == "error" for f in out[:10]), "error 优先排前"
    assert [f.code for f in out] == [f.code for f in collect_unified_findings(
        result=result)], "同输入同输出"
    small = collect_unified_findings(result=result, budget=10)
    assert len(small) == 10, "自定义预算同样截断"

    runtime_block = {"nodes": [
        {"node_id": f"perf-n{i}", "state": "stale", "stale_reason": "perf"}
        for i in range(70)]}
    diagnostics = [{"code": "perf_diag", "severity": "warning",
                    "detail": "perf"} for _ in range(30)]
    mixed = collect_unified_findings(
        runtime_block=runtime_block, render_diagnostics=diagnostics)
    assert len(mixed) == 24, "运行态 64 + 诊断 16 上游切片后仍受总预算截断"
