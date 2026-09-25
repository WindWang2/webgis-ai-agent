"""F08 / ADR-0214 D3：计划级 feasibility（DAG → aggregate → 裁决）。

验收面：
- dag_waves：拓扑分层确定性、分支并行、环收敛不死循环、
  depends_on 结构依赖与 machine.upstream_of 同源；
- dag_plan_nodes：波内 PARALLEL / 波间 SEQUENTIAL / optional → 披露池；
- aggregate 接通：critical path、parallel live peak、unknown 维地板披露；
- budget_violations：显式上限命中/不命中；limits 空 → 零裁决（行为不变）；
- 模式词表：off/observe/enforce；enforce+违规 → blocked；
- workflow_plan_limits：manifest 缺文件 fail-open 空。
"""
from __future__ import annotations

import copy

import pytest

from app.services.governor.contract import (
    CONSERVATIVE_FLOORS,
    Dimension,
)
from app.services.workflow_runtime.plan_feasibility import (
    PLAN_ADMISSION_ENV,
    dag_plan_nodes,
    dag_waves,
    evaluate_plan,
    plan_admission_mode,
    workflow_plan_limits,
)


def _diamond_dag():
    return {
        "nodes": [
            {"node_id": "a", "kind": "transform"},
            {"node_id": "b", "kind": "transform"},
            {"node_id": "c", "kind": "transform"},
            {"node_id": "d", "kind": "output"},
        ],
        "edges": [
            {"from": "a", "to": "b"},
            {"from": "a", "to": "c"},
            {"from": "b", "to": "d"},
            {"from": "c", "to": "d"},
        ],
    }


def _port_edges_dag():
    return {
        "nodes": [
            {"node_id": "a", "kind": "data_input"},
            {"node_id": "b", "kind": "transform"},
        ],
        "edges": [
            {"from": "a.data", "to": "b.input"},
        ],
    }


def test_dag_waves_diamond():
    waves = dag_waves(_diamond_dag())
    assert waves == [["a"], ["b", "c"], ["d"]]


def test_dag_waves_port_suffix_edges():
    waves = dag_waves(_port_edges_dag())
    assert waves == [["a"], ["b"]]


def test_dag_waves_depends_on_structural_dependency():
    dag = {
        "nodes": [
            {"node_id": "x", "depends_on": ["w"]},
            {"node_id": "w"},
        ],
        "edges": [],
    }
    assert dag_waves(dag) == [["w"], ["x"]]


def test_dag_waves_deterministic():
    dag = _diamond_dag()
    assert dag_waves(dag) == dag_waves(copy.deepcopy(dag))


def test_dag_waves_cycle_converges_without_hang():
    dag = {
        "nodes": [{"node_id": n} for n in ("p", "q", "r")],
        "edges": [
            {"from": "p", "to": "q"},
            {"from": "q", "to": "p"},
            {"from": "p", "to": "r"},
        ],
    }
    waves = dag_waves(dag)  # 必须终止
    flat = [n for w in waves for n in w]
    assert sorted(flat) == ["p", "q", "r"]


def test_plan_nodes_wave_shape():
    plan = dag_plan_nodes(_diamond_dag(), max_parallel=4)
    kinds = {p.key: p.kind.value for p in plan}
    assert kinds["a"] == "sequential"
    assert kinds["b"] == "parallel"
    assert kinds["c"] == "parallel"
    assert kinds["d"] == "sequential"


def test_plan_nodes_sequential_when_single_slot():
    plan = dag_plan_nodes(_diamond_dag(), max_parallel=1)
    assert all(p.kind.value == "sequential" for p in plan)


def test_optional_nodes_excluded_from_main_path():
    dag = _diamond_dag()
    dag["nodes"][2]["optional"] = True  # c
    plan = dag_plan_nodes(dag, max_parallel=4)
    kinds = {p.key: p.kind.value for p in plan}
    assert kinds["c"] == "optional"


def test_aggregate_critical_path_and_peak():
    from app.services.governor.contract import (
        DimValue,
        ResourceEstimate,
        Subsystem,
    )

    def est(mem_bytes, wall_s):
        return ResourceEstimate(
            subsystem=Subsystem.WORKFLOW,
            dims={
                Dimension.MEMORY_BYTES: DimValue.known(float(mem_bytes)),
                Dimension.WALL_TIME_S: DimValue.known(float(wall_s)),
            },
        )

    dag = _diamond_dag()
    estimates = {
        "a": est(100, 10), "b": est(300, 20),
        "c": est(200, 5), "d": est(50, 2),
    }
    plan = dag_plan_nodes(dag, max_parallel=4, estimates=estimates)
    from app.services.governor.plan_aggregation import aggregate_plan

    agg = aggregate_plan(plan)
    # critical path：a → b（b 是并行波内较长支）→ d
    assert agg.critical_path == ["a", "b", "d"]
    # wall expected = 10 + 20 + 2
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    assert wall.expected == pytest.approx(32.0)
    # parallel live peak：波2 = b+c 求和（500），其余单节点 max
    assert agg.parallel_peak_memory_bytes == pytest.approx(500.0)
    mem = agg.estimate.dim(Dimension.MEMORY_BYTES)
    assert mem.expected == pytest.approx(500.0)


def test_unknown_floor_disclosed_in_aggregate():
    from app.services.governor.contract import (
        DimValue,
        ResourceEstimate,
        Subsystem,
    )

    est = ResourceEstimate(
        subsystem=Subsystem.WORKFLOW,
        dims={Dimension.GPU_MEMORY_BYTES: DimValue.unknown("no vram evidence")},
    )
    dag = {"nodes": [{"node_id": "g"}], "edges": []}
    plan = dag_plan_nodes(dag, estimates={"g": est})
    from app.services.governor.plan_aggregation import aggregate_plan

    agg = aggregate_plan(plan)
    assert "gpu_memory_bytes" in agg.floor_charged_dims
    # unknown≠0：聚合数值含保守地板
    vram = agg.estimate.dim(Dimension.GPU_MEMORY_BYTES)
    assert vram.expected == CONSERVATIVE_FLOORS[Dimension.GPU_MEMORY_BYTES]


def test_evaluate_violations_and_blocked_semantics(monkeypatch):
    dag = _diamond_dag()
    # 3 波 SEQUENTIAL/PARALLEL 混合：wall exp = 0.5×3 = 1.5 > 1.0 → 违规
    limits = {Dimension.WALL_TIME_S: 1.0}
    # observe（默认）：违规披露但不拦截
    monkeypatch.delenv(PLAN_ADMISSION_ENV, raising=False)
    feas = evaluate_plan(dag, max_parallel=4, expected_attempts=1,
                         limits=limits)
    assert feas.violations
    assert feas.blocked is False
    assert feas.mode == "observe"
    # enforce：同样违规 → blocked
    monkeypatch.setenv(PLAN_ADMISSION_ENV, "enforce")
    feas = evaluate_plan(dag, max_parallel=4, expected_attempts=1,
                         limits=limits)
    assert feas.blocked is True
    assert any(v.startswith("wall_time_s:") for v in feas.violations)
    # off：完全跳过
    monkeypatch.setenv(PLAN_ADMISSION_ENV, "off")
    feas = evaluate_plan(dag, max_parallel=4, expected_attempts=1,
                         limits=limits)
    assert feas.violations == []
    assert feas.blocked is False
    assert feas.mode == "off"


def test_evaluate_empty_limits_is_noop():
    dag = _diamond_dag()
    feas = evaluate_plan(dag, max_parallel=4, limits=None)
    assert feas.violations == []
    assert feas.blocked is False


def test_evaluate_within_limits_passes(monkeypatch):
    monkeypatch.setenv(PLAN_ADMISSION_ENV, "enforce")
    dag = _diamond_dag()
    feas = evaluate_plan(dag, max_parallel=4, expected_attempts=1,
                         limits={Dimension.WALL_TIME_S: 10_000.0,
                                 Dimension.MEMORY_BYTES: 10 * 1024**3})
    assert feas.violations == []
    assert feas.blocked is False


def test_plan_admission_mode_vocabulary(monkeypatch):
    monkeypatch.delenv(PLAN_ADMISSION_ENV, raising=False)
    assert plan_admission_mode() == "observe"
    for raw, expect in (("off", "off"), ("observe", "observe"),
                        ("enforce", "enforce"), ("bogus", "observe")):
        monkeypatch.setenv(PLAN_ADMISSION_ENV, raw)
        assert plan_admission_mode() == expect


def test_retry_multiplier_in_violations(monkeypatch):
    """expected_attempts=2 → 累计 wall ×2（retry 乘数进可行性裁决）。"""
    monkeypatch.setenv(PLAN_ADMISSION_ENV, "enforce")
    dag = {"nodes": [{"node_id": "s"}], "edges": []}
    # 单节点 light 先验 wall exp=0.5s：limit 0.6 下 1 次 attempt 通过
    single = evaluate_plan(dag, max_parallel=1, expected_attempts=1,
                           limits={Dimension.WALL_TIME_S: 0.6})
    assert single.violations == []
    # ×2 = 1.0 > 0.6 → 违规（乘数真实生效）
    double = evaluate_plan(dag, max_parallel=1, expected_attempts=2,
                           limits={Dimension.WALL_TIME_S: 0.6})
    assert any(v.startswith("wall_time_s:") for v in double.violations)


def test_workflow_plan_limits_missing_manifest_is_empty(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # 仓库 manifest 不可达 → fail-open
    assert workflow_plan_limits() == {}


def test_feasibility_to_bounded_dict():
    dag = _diamond_dag()
    feas = evaluate_plan(dag, max_parallel=4, expected_attempts=1,
                         limits={Dimension.WALL_TIME_S: 0.01})
    d = feas.to_bounded_dict()
    assert d["mode"] in ("observe", "enforce")
    assert isinstance(d["violations"], list)
    assert d["node_count"] == 4
    assert "critical_path" in d
