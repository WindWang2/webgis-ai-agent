"""F08：并发计划 simulation / property tests（ADR-0214 验收面）。

方向 DoD 的「可证明」部分：

1. **并行内存峰值**：波内节点并行驻留 —— 模拟器逐波累计 live 内存，
   属性：模拟 live 峰值 ≤ 计划聚合 parallel peak 的 max 语义上界
   （声明序内任意调度不产生超出聚合的驻留组合，同波成员恒同波驻留）；
2. **串行 wall time**：max_concurrency=1 时模拟 wall = 逐节点和 =
   聚合 critical path（SEQUENTIAL 链求和语义）；
3. **retry multiplier**：expected_attempts=k → 聚合 wall 恰好 ×k
   （内存不叠乘 —— 重试串行）；
4. **cache hit**：cache_read_probability 折扣只作用于可缓存维，
   memory 不折扣；
5. **fallback/optional**：不进主聚合（披露池单列）；
6. **槽位背压**：K 个并发 wave 工作共享 S 个槽位 → 模拟器在飞数
   恒 ≤ S（背压上界可证明，不依赖 OOM 后补救）；
7. **确定性**：同输入必同输出（估算/聚合/模拟三面）。
"""
from __future__ import annotations

import copy
import random

import pytest

from app.services.governor.contract import (
    Dimension,
    DimValue,
    ResourceEstimate,
    Subsystem,
)
from app.services.governor.plan_aggregation import (
    PlanNode,
    PlanNodeKind,
    aggregate_plan,
)
from app.services.workflow_runtime.plan_feasibility import (
    dag_plan_nodes,
    dag_waves,
)


def _est(mem_bytes: float, wall_s: float, *,
         tokens: float = 0.0) -> ResourceEstimate:
    dims = {
        Dimension.MEMORY_BYTES: DimValue.known(float(mem_bytes)),
        Dimension.WALL_TIME_S: DimValue.known(float(wall_s)),
    }
    if tokens:
        dims[Dimension.CONTEXT_TOKENS] = DimValue.known(float(tokens))
    return ResourceEstimate(subsystem=Subsystem.WORKFLOW, dims=dims)


def _layered_dag(layers: int, width: int) -> dict:
    """layers 层、每层 width 个节点的分层 DAG（相邻层全连接）。"""
    nodes = []
    edges = []
    prev = None
    for lv in range(layers):
        cur = [f"n{lv}_{i}" for i in range(width)]
        nodes.extend({"node_id": n, "kind": "transform"} for n in cur)
        if prev is not None:
            for p in prev:
                for c in cur:
                    edges.append({"from": p, "to": c})
        prev = cur
    return {"nodes": nodes, "edges": edges}


class _WaveSimulator:
    """波次调度模拟器（确定性事件推进；纯内存，不触碰真实栈）。

    资源模型：每节点携带 (mem, wall)；同一波内节点并行驻留（受
    ``slots`` 截断 —— 截断的成员推迟到下一波，与 driver 波次语义
    同构）；波间串行。记录 live 内存峰值与总 wall。
    """

    def __init__(self, plans, *, slots: int):
        self._plans = {p.key: p for p in plans}
        self._slots = max(1, int(slots))

    def run(self, dag) -> dict:
        live_peak = 0.0
        total_wall = 0.0
        for wave in dag_waves(dag):
            batch = wave[: self._slots]
            # 截断余量并入下一批（保守：同波溢出成员串行追加）
            overflow = wave[self._slots:]
            waves = [batch] + [[n] for n in overflow]
            for w in waves:
                live = sum(
                    (self._plans[n].estimate.dim(
                        Dimension.MEMORY_BYTES).expected or 0.0)
                    for n in w)
                live_peak = max(live_peak, live)
                total_wall += max(
                    (self._plans[n].estimate.dim(
                        Dimension.WALL_TIME_S).expected or 0.0)
                    for n in w) if w else 0.0
        return {"live_peak": live_peak, "wall": total_wall}


def test_parallel_live_peak_bound_property():
    """属性：任意波内组合的模拟 live 峰值 ≤ 聚合 parallel peak 上界。"""
    dag = _layered_dag(4, 3)
    rng = random.Random(1408)
    estimates = {
        n["node_id"]: _est(rng.randrange(50, 500) * 1024**2,
                           rng.uniform(0.5, 5.0))
        for n in dag["nodes"]
    }
    plans = dag_plan_nodes(dag, max_parallel=3, estimates=estimates)
    agg = aggregate_plan(plans)
    sim = _WaveSimulator(plans, slots=3).run(dag)
    peak_cap = agg.estimate.dim(Dimension.MEMORY_BYTES).expected
    assert sim["live_peak"] <= peak_cap + 1e-6
    # 波内并行 → 峰值必然超过单节点最大（并行 sum 语义真实生效）
    max_single = max(v.dim(Dimension.MEMORY_BYTES).expected
                     for v in estimates.values())
    assert sim["live_peak"] > max_single


def test_serial_wall_equals_sum_and_critical_path():
    dag = _layered_dag(3, 4)
    estimates = {n["node_id"]: _est(1024**2, 2.0) for n in dag["nodes"]}
    serial_plans = dag_plan_nodes(dag, max_parallel=1,
                                  estimates=estimates)
    agg = aggregate_plan(serial_plans)
    # max_concurrency=1 → 全 SEQUENTIAL → wall = 逐节点和
    assert agg.estimate.dim(Dimension.WALL_TIME_S).expected == \
        pytest.approx(2.0 * 12)
    assert agg.critical_path == [p.key for p in serial_plans]


def test_retry_multiplier_wall_not_memory():
    node = PlanNode(key="s", estimate=_est(100 * 1024**2, 10.0),
                    expected_attempts=3)
    agg = aggregate_plan([node])
    assert agg.estimate.dim(Dimension.WALL_TIME_S).expected == \
        pytest.approx(30.0)
    # 内存不叠乘（重试串行，峰值不叠乘）
    assert agg.estimate.dim(Dimension.MEMORY_BYTES).expected == \
        pytest.approx(100 * 1024**2)


def test_cache_discount_only_cached_dims():
    """CACHED_DIMS 折扣只作用于可缓存维（wall/网络/渲染工时/成本/外调）；
    CONTEXT_TOKENS 是累计维（只乘 retry，不折扣）；memory 不折扣。"""
    base = PlanNode(key="c", estimate=_est(100, 10.0, tokens=1000),
                    cache_read_probability=0.5)
    agg = aggregate_plan([base])
    wall = agg.estimate.dim(Dimension.WALL_TIME_S)
    tokens = agg.estimate.dim(Dimension.CONTEXT_TOKENS)
    mem = agg.estimate.dim(Dimension.MEMORY_BYTES)
    assert wall.expected == pytest.approx(5.0)      # 可缓存 → 折扣
    assert tokens.expected == pytest.approx(1000.0)  # 非缓存累计维 → 原值
    assert mem.expected == pytest.approx(100.0)      # 内存不折扣


def test_optional_fallback_excluded_from_main_aggregate():
    main = PlanNode(key="m", estimate=_est(100, 10.0))
    opt = PlanNode(key="o", estimate=_est(10_000, 999.0),
                   kind=PlanNodeKind.OPTIONAL)
    fb = PlanNode(key="f", estimate=_est(10_000, 999.0),
                  kind=PlanNodeKind.FALLBACK)
    agg = aggregate_plan([main, opt, fb])
    assert agg.estimate.dim(Dimension.WALL_TIME_S).expected == \
        pytest.approx(10.0)
    assert agg.estimate.dim(Dimension.MEMORY_BYTES).expected == \
        pytest.approx(100.0)
    assert len(agg.optional_pool) == 1
    assert len(agg.fallback_pool) == 1


def test_slot_backpressure_bound_property():
    """K 路并发 × S 槽位：模拟在飞数恒 ≤ S（可证明背压，非 OOM 补救）。"""
    dag = _layered_dag(5, 8)
    estimates = {n["node_id"]: _est(10 * 1024**2, 0.2)
                 for n in dag["nodes"]}
    for slots in (1, 2, 4):
        plans = dag_plan_nodes(dag, max_parallel=slots,
                               estimates=estimates)
        sim = _WaveSimulator(plans, slots=slots).run(dag)
        # 每波在飞 ≤ slots 的结构事实由模拟器内 batch 截断保证；
        # 这里验证其资源投影：live 峰值 ≤ slots × 单节点上界
        assert sim["live_peak"] <= slots * 10 * 1024**2 + 1e-6


def test_end_to_end_determinism_three_faces():
    """估算 → 聚合 → 模拟 三面：同输入必同输出。"""
    dag = _layered_dag(3, 3)
    estimates = {n["node_id"]: _est(64 * 1024**2, 1.5)
                 for n in dag["nodes"]}
    a = dag_plan_nodes(dag, max_parallel=3, estimates=estimates)
    b = dag_plan_nodes(dag, max_parallel=3,
                       estimates=copy.deepcopy(estimates))
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]
    agg_a = aggregate_plan(a)
    agg_b = aggregate_plan(b)
    assert agg_a.model_dump() == agg_b.model_dump()
    sim_a = _WaveSimulator(a, slots=3).run(dag)
    sim_b = _WaveSimulator(b, slots=3).run(dag)
    assert sim_a == sim_b


def test_simulation_matches_aggregate_on_layered_shape():
    """分层数据上的相互印证：模拟 wall == 聚合 critical path wall。"""
    dag = _layered_dag(4, 2)
    estimates = {n["node_id"]: _est(32 * 1024**2, 1.0)
                 for n in dag["nodes"]}
    plans = dag_plan_nodes(dag, max_parallel=2, estimates=estimates)
    agg = aggregate_plan(plans)
    sim = _WaveSimulator(plans, slots=2).run(dag)
    assert sim["wall"] == pytest.approx(
        agg.estimate.dim(Dimension.WALL_TIME_S).expected)
