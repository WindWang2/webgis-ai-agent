"""Planner Runtime / Plan Graph 性能不变量（Runtime v3, §14）。

确定性 call-count 契约（不是 wall-clock 微基准的替代，而是补充）：
- 共享 PlannerRuntime 的 memo 跨调用真实命中（第二次规划 0 次 resolver
  解析——A2 修复前每调用新建 planner，intent→product 链每步都全量解析）；
- manifest 世代不变时重复规划稳定命中；世代变化全量失效；
- 64+ memo 条目（eviction 路径可达）时 100 次规划仍在毫秒量级中位数；
- build_plan_graph（Phase D/E）在真实 recipe 规模与放大规模（60 节点）
  下评估耗时受 ceiling 约束，无 O(N²) 状态传播爆炸。
"""
import statistics
import time

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.plan_graph import build_plan_graph, project_graph_block
from app.services.gis_harness.planner_runtime import (
    get_planner_runtime,
    reset_planner_runtime,
)


def setup_function(_fn):
    reset_planner_runtime()


def teardown_function(_fn):
    reset_planner_runtime()


def _count_resolver_calls(fn):
    """monkeypatch-free 计数：包一层 resolver.resolve。"""
    from app.lib.gis import algorithm_resolver as ar_module

    resolver = ar_module.get_algorithm_resolver()
    original = resolver.resolve
    counter = {"n": 0}

    def counting(*args, **kwargs):
        counter["n"] += 1
        return original(*args, **kwargs)

    resolver.resolve = counting
    try:
        fn()
    finally:
        resolver.resolve = original
    return counter["n"]


class TestPlannerRuntimePerf:
    def setup_method(self):
        reset_planner_runtime()

    def teardown_method(self):
        reset_planner_runtime()
    def test_second_plan_hits_memo_zero_resolver_calls(self):
        planner = get_planner_runtime()
        intent = resolve_map_request_intent("成都小学分布情况")

        first = _count_resolver_calls(lambda: planner.plan_from_intent(intent))
        assert first > 0, "首次规划必须真实解析能力（sanity）"

        second = _count_resolver_calls(lambda: planner.plan_from_intent(intent))
        assert second == 0, (
            f"memo 命中时不得触发 resolver（实际 {second} 次）—— "
            "A2 修复前 planner 每调用新建，链路永远全量解析"
        )

    def test_intent_then_product_chain_resolves_once(self):
        """intent 工具 + product 工具（显式回放同一 recipe/template，与
        tools.py 的 plan 连续性参数一致）只解析一次。"""
        planner = get_planner_runtime()
        intent = resolve_map_request_intent("成都小学分布情况")

        def chain():
            planner.plan_from_intent(intent)          # webgis_map_intent
            planner.plan_from_intent(                 # webgis_map_product 重放
                intent,
                template_id="education_facility_distribution",
                recipe_id="poi_distribution_overview",
            )

        total = _count_resolver_calls(chain)
        # 第二次调用 memo 命中（裁决结果相同）→ 只剩第一次的解析
        once = _count_resolver_calls(lambda: planner.plan_from_intent(intent, use_memo=False))
        assert total == once, (
            f"intent→product 链应只解析一次（{once}），实际 {total}"
        )

    def test_manifest_unchanged_keeps_hitting(self):
        planner = get_planner_runtime()
        intent = resolve_map_request_intent("成都小学分布情况")
        planner.plan_from_intent(intent)
        for _ in range(20):
            planner.plan_from_intent(intent)
        assert len(planner._plan_memo) == 1, "manifest 不变时同输入不得重复入账"

    def test_manifest_same_content_recompile_keeps_memo(self):
        """同内容重编译指纹稳定（内容敏感指纹不含 compiled_at）→ memo 不失效；
        真正的世代失效（内容变化）由 unit 侧
        test_manifest_generation_change_invalidates 锁定。"""
        from app.lib.gis.runtime_manifest import refresh_runtime_manifest
        planner = get_planner_runtime()
        intent = resolve_map_request_intent("成都小学分布情况")
        planner.plan_from_intent(intent)
        refresh_runtime_manifest()
        planner.plan_from_intent(intent)
        assert len(planner._plan_memo) == 1, "同内容重编译指纹稳定，不得失效"

    def test_eviction_path_100_plans_bounded_latency(self):
        """64+ 唯一 intent：eviction 可达 + 100 次规划中位数受 ceiling。"""
        planner = get_planner_runtime()
        for i in range(80):
            planner.plan_from_intent(
                resolve_map_request_intent(f"城市{i}小学分布情况"))
        assert len(planner._plan_memo) == planner._plan_memo_max

        intents = [
            resolve_map_request_intent(f"城市{i}医院分布密度")
            for i in range(20)
        ]
        samples = []
        for idx in range(100):
            it = intents[idx % len(intents)]
            t0 = time.perf_counter()
            planner.plan_from_intent(it)
            samples.append((time.perf_counter() - t0) * 1000)
        med = statistics.median(samples)
        assert med < 100.0, f"满容量 memo 下规划过慢: {med:.3f}ms"


class TestPlanGraphPerf:
    def _plan(self):
        return get_planner_runtime().plan_from_intent(
            resolve_map_request_intent("成都小学分布情况"))

    def test_graph_build_and_evaluate_real_plan(self):
        plan = self._plan()
        t0 = time.perf_counter()
        for _ in range(50):
            graph = build_plan_graph(plan)
        med = (time.perf_counter() - t0) * 1000 / 50
        assert med < 20.0, f"build_plan_graph 过慢: {med:.3f}ms"
        assert graph.nodes

    def test_graph_projection_bounded(self):
        plan = self._plan()
        graph = build_plan_graph(plan)
        t0 = time.perf_counter()
        for _ in range(50):
            text = project_graph_block(graph)
        med = (time.perf_counter() - t0) * 1000 / 50
        assert med < 5.0, f"project_graph_block 过慢: {med:.3f}ms"
        assert len(text.splitlines()) <= 10

    def test_graph_evaluate_scaled_60_nodes(self):
        """放大规模（60 节点链式图）：评估传播无 O(N²) 爆炸。"""
        rows = []
        steps = []
        for i in range(60):
            cap = f"cap_{i:03d}"
            deps = [f"cap_{i - 1:03d}"] if i > 0 else []
            rows.append({"capability": cap, "status": "pending", "depends_on": deps})
            steps.append({"capability": cap, "status": "pending", "depends_on": deps})
        chapter = {
            "plan_id": "perf-scale",
            "data_requirements": rows,
            "analysis_steps": steps,
            "algorithm_selections": [],
        }
        t0 = time.perf_counter()
        graph = build_plan_graph(chapter)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 200.0, f"60 节点建图+评估过慢: {elapsed_ms:.3f}ms"
        # 链式依赖：只有头节点 ready
        assert graph.ready_nodes() == ["cap_000"]
        # 完成头节点 → 逐级解锁（一次完成一个）
        rows[0]["status"] = "available"
        steps[0]["status"] = "done"
        graph2 = build_plan_graph(chapter)
        assert graph2.ready_nodes() == ["cap_001"]

    def test_graph_evaluate_unavailable_cascade_60_chain(self):
        """review-D：级联 unavailable 传播真正走多轮固定点（头节点不可用
        → 全链阻塞），受 ceiling 约束。"""
        rows = []
        steps = []
        for i in range(60):
            cap = f"cc_{i:03d}"
            deps = [f"cc_{i - 1:03d}"] if i > 0 else []
            status = "unavailable" if i == 0 else "pending"
            rows.append({"capability": cap, "status": status, "depends_on": deps})
            steps.append({"capability": cap, "status": status, "depends_on": deps})
        chapter = {
            "plan_id": "perf-cascade",
            "data_requirements": rows,
            "analysis_steps": steps,
            "algorithm_selections": [],
        }
        t0 = time.perf_counter()
        graph = build_plan_graph(chapter)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 400.0, f"60 节点级联传播过慢: {elapsed_ms:.3f}ms"
        # 全链被头节点的不可用阻塞，且 blocked_by 指向直接前驱
        assert graph.node("cc_001").status.value == "unavailable"
        assert graph.node("cc_001").blocked_by == ["cc_000"]
        assert graph.node("cc_059").status.value == "unavailable"
        assert graph.ready_nodes() == []

    def test_graph_evaluate_wide_fan_in_60_nodes(self):
        """review-D：宽扇入（1 汇聚节点 + 59 个不可用上游）——最重的
        `_dep_satisfied` 扫描形态，受 ceiling 约束。"""
        rows = [{"capability": "sink", "status": "pending",
                 "depends_on": [f"fan_{i:03d}" for i in range(59)]}]
        steps = [{"capability": "sink", "status": "pending",
                  "depends_on": [f"fan_{i:03d}" for i in range(59)]}]
        for i in range(59):
            cap = f"fan_{i:03d}"
            rows.append({"capability": cap, "status": "unavailable", "depends_on": []})
            steps.append({"capability": cap, "status": "unavailable", "depends_on": []})
        chapter = {
            "plan_id": "perf-fanin",
            "data_requirements": rows,
            "analysis_steps": steps,
            "algorithm_selections": [],
        }
        t0 = time.perf_counter()
        graph = build_plan_graph(chapter)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 400.0, f"60 节点宽扇入评估过慢: {elapsed_ms:.3f}ms"
        sink = graph.node("sink")
        assert sink.status.value == "unavailable"
        assert len(sink.blocked_by) == 59
