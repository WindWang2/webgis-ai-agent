"""统一执行 DAG：契约/图/执行器/算子/可观测性测试（ADR-0096 D2）。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.lib.cancellation import CancellationToken
from app.services.geocompute import (
    BudgetExceededError,
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    ExecutionRunStatus,
    GeoExecutionEngine,
    NodeCategory,
    NodeReusePolicy,
    ResourceBudget,
    ResourceEstimate,
    RetryPolicy,
    UnsupportedOperationError,
    graph,
    ops,
)


def _node(node_id: str, category: NodeCategory, **kw) -> ExecutionNode:
    return ExecutionNode(node_id=node_id, category=category, **kw)


def _fc(n: int = 3, value_field: str = "v") -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0 + i * 0.01]},
            "properties": {"v": i, "name": f"f{i}", "kind": "a" if i % 2 == 0 else "b"},
        }
        for i in range(n)
    ]


def _filter_node(node_id="f1", n=4, predicate=None, **kw) -> ExecutionNode:
    return _node(
        node_id,
        NodeCategory.FILTER,
        parameters={
            "predicate": predicate or {"op": "eq", "field": "kind", "value": "a"},
            "features": _fc(n),
        },
        **kw,
    )


# ------------------------------------------------------------------ plan


class TestPlanContracts:
    def test_node_fingerprint_deterministic_and_order_free(self):
        a = _node("x", NodeCategory.FILTER, parameters={"p": 1, "q": 2}, inputs=["a", "b"])
        b = _node("x", NodeCategory.FILTER, parameters={"q": 2, "p": 1}, inputs=["b", "a"])
        assert a.semantic_fingerprint() == b.semantic_fingerprint()

    def test_node_fingerprint_sensitive_to_semantics(self):
        base = _node("x", NodeCategory.FILTER, parameters={"p": 1})
        assert base.semantic_fingerprint() != base.model_copy(
            update={"parameters": {"p": 2}}
        ).semantic_fingerprint()
        assert base.semantic_fingerprint() != base.model_copy(
            update={"dataset_fingerprints": {"d": "rev2"}}
        ).semantic_fingerprint()

    def test_node_fingerprint_ignores_policy_and_estimate(self):
        base = _node("x", NodeCategory.FILTER, parameters={"p": 1})
        twin = base.model_copy(
            update={
                "policy": ExecutionPolicyKind.DURABLE_JOB,
                "estimate": ResourceEstimate(rows=10),
                "deadline_s": 5.0,
                "reuse": NodeReusePolicy.DISALLOW,
            }
        )
        assert base.semantic_fingerprint() == twin.semantic_fingerprint()

    def test_plan_fingerprint_order_independent(self):
        n1 = _filter_node("a")
        n2 = _node("b", NodeCategory.AGGREGATE, inputs=["a"],
                   parameters={"aggregates": [{"func": "count", "field": "v"}], "group_by": ["kind"]})
        p1 = ExecutionPlan(plan_id="p", nodes=[n1, n2])
        p2 = ExecutionPlan(plan_id="p", nodes=[n2, n1])
        assert p1.graph_fingerprint() == p2.graph_fingerprint()

    def test_wired_categories_are_real(self):
        wired = set(ops.wired_categories())
        assert {"query", "filter", "aggregate", "spatial_join", "materialize"} <= wired


# ------------------------------------------------------------------ graph


class TestGraph:
    def test_validate_rejects_unknown_input(self):
        plan = ExecutionPlan(plan_id="p", nodes=[_filter_node("a"), _node("b", NodeCategory.QUERY, inputs=["ghost"])])
        with pytest.raises(graph.PlanValidationError, match="unknown input"):
            graph.validate_plan(plan)

    def test_validate_rejects_cycle(self):
        a = _node("a", NodeCategory.FILTER, inputs=["b"], parameters={"predicate": {"op": "eq", "field": "x", "value": 1}})
        b = _node("b", NodeCategory.FILTER, inputs=["a"], parameters={"predicate": {"op": "eq", "field": "x", "value": 1}})
        with pytest.raises(graph.PlanValidationError, match="cycle"):
            graph.validate_plan(ExecutionPlan(plan_id="p", nodes=[a, b]))

    def test_validate_rejects_duplicates_and_empty(self):
        with pytest.raises(graph.PlanValidationError):
            graph.validate_plan(ExecutionPlan(plan_id="p", nodes=[]))
        n = _filter_node("a")
        with pytest.raises(graph.PlanValidationError, match="duplicate"):
            graph.validate_plan(ExecutionPlan(plan_id="p", nodes=[n, n.model_copy()]))

    def test_topo_waves(self):
        scan = _node("src", NodeCategory.SOURCE_SCAN, parameters={"dataset_id": "d1"})
        f1 = _filter_node("f1", inputs=["src"])
        f2 = _filter_node("f2", inputs=["src"])
        agg = _node("agg", NodeCategory.AGGREGATE, inputs=["f1", "f2"],
                    parameters={"aggregates": [{"func": "count", "field": "v"}]})
        plan = ExecutionPlan(plan_id="p", nodes=[agg, f2, f1, scan])
        graph.validate_plan(plan)
        waves = graph.topo_wave_order(plan)
        assert waves[0] == ["src"]
        assert waves[1] == ["f1", "f2"]
        assert waves[2] == ["agg"]

    def test_invalidation_set_covers_descendants(self):
        scan = _node("src", NodeCategory.SOURCE_SCAN, parameters={"dataset_id": "d1"})
        f1 = _filter_node("f1", inputs=["src"])
        agg = _node("agg", NodeCategory.AGGREGATE, inputs=["f1"],
                    parameters={"aggregates": [{"func": "count", "field": "v"}]})
        plan = ExecutionPlan(plan_id="p", nodes=[scan, f1, agg])
        changed = {scan.semantic_fingerprint()}
        assert graph.invalidation_set(plan, changed) == {"src", "f1", "agg"}


# --------------------------------------------------------------- executor


class TestExecutor:
    def _engine(self) -> GeoExecutionEngine:
        return GeoExecutionEngine(max_workers=2)

    def test_happy_path_chain(self):
        agg = _node(
            "agg", NodeCategory.AGGREGATE, inputs=["f1"],
            parameters={"aggregates": [{"func": "count", "field": "v"}], "group_by": ["kind"]},
        )
        plan = ExecutionPlan(plan_id="p", nodes=[_filter_node(), agg])
        run = self._engine().execute_plan(plan)
        assert run.status is ExecutionRunStatus.COMPLETED
        assert run.evidence["f1"].status == "completed"
        assert run.evidence["agg"].status == "completed"
        assert run.summary_lines()[0].startswith("run ")

    def test_reuse_and_invalidation(self):
        eng = self._engine()
        n1 = _filter_node()
        plan_a = ExecutionPlan(plan_id="pa", nodes=[n1])
        run_a = eng.execute_plan(plan_a)
        assert run_a.evidence["f1"].status == "completed"

        plan_b = ExecutionPlan(plan_id="pb", nodes=[_filter_node()])
        run_b = eng.execute_plan(plan_b)
        assert run_b.evidence["f1"].status == "reused"

        changed = ExecutionPlan(
            plan_id="pc",
            nodes=[_filter_node(predicate={"op": "eq", "field": "kind", "value": "b"})],
        )
        run_c = eng.execute_plan(changed)
        assert run_c.evidence["f1"].status == "completed"

    def test_failure_skips_descendants(self):
        def boom(ctx, node, payloads):
            raise ValueError("source exploded")

        bad = _node("bad", NodeCategory.SOURCE_SCAN, parameters={"dataset_id": "x"})
        child = _filter_node("child", inputs=["bad"])
        plan = ExecutionPlan(plan_id="p", nodes=[bad, child])
        eng = self._engine()
        # monkeypatch the registry handler for SOURCE_SCAN to raise
        original = ops.REGISTRY[NodeCategory.SOURCE_SCAN]
        ops.REGISTRY[NodeCategory.SOURCE_SCAN] = boom
        try:
            run = eng.execute_plan(plan)
        finally:
            ops.REGISTRY[NodeCategory.SOURCE_SCAN] = original
        assert run.status is ExecutionRunStatus.FAILED
        assert run.evidence["bad"].status == "failed"
        assert run.evidence["child"].status == "skipped"
        assert run.error_code == "NODE_FAILED"

    def test_transient_retry_then_success(self):
        calls = {"n": 0}

        def flaky(ctx, node, payloads):
            calls["n"] += 1
            if calls["n"] < 2:
                raise ops.NodeExecutionError("transient remote hiccup", retry_safe=True)
            return {"rows": [{"ok": 1}], "metadata": {}}

        node = _node("n", NodeCategory.SOURCE_SCAN, retry=RetryPolicy(max_attempts=3),
                     parameters={"dataset_id": "x"})
        plan = ExecutionPlan(plan_id="p", nodes=[node])
        eng = self._engine()
        original = ops.REGISTRY[NodeCategory.SOURCE_SCAN]
        ops.REGISTRY[NodeCategory.SOURCE_SCAN] = flaky
        try:
            run = eng.execute_plan(plan)
        finally:
            ops.REGISTRY[NodeCategory.SOURCE_SCAN] = original
        assert run.status is ExecutionRunStatus.COMPLETED
        assert run.evidence["n"].attempts == 2

    def test_non_retryable_fails_once(self):
        calls = {"n": 0}

        def permanent(ctx, node, payloads):
            calls["n"] += 1
            raise ops.NodeExecutionError("invalid params", retry_safe=False)

        node = _node("n", NodeCategory.SOURCE_SCAN, retry=RetryPolicy(max_attempts=3),
                     parameters={"dataset_id": "x"})
        original = ops.REGISTRY[NodeCategory.SOURCE_SCAN]
        ops.REGISTRY[NodeCategory.SOURCE_SCAN] = permanent
        try:
            run = self._engine().execute_plan(ExecutionPlan(plan_id="p", nodes=[node]))
        finally:
            ops.REGISTRY[NodeCategory.SOURCE_SCAN] = original
        assert run.status is ExecutionRunStatus.FAILED
        assert calls["n"] == 1

    def test_unsupported_category_fails_honestly(self):
        node = _node("n", NodeCategory.NETWORK_OPERATION, parameters={"x": 1})
        run = self._engine().execute_plan(ExecutionPlan(plan_id="p", nodes=[node]))
        assert run.status is ExecutionRunStatus.FAILED
        assert run.evidence["n"].error_code == "OPERATION_UNSUPPORTED"

    def test_durable_job_policy_honest_until_wired(self):
        node = _node("n", NodeCategory.FILTER, policy=ExecutionPolicyKind.DURABLE_JOB,
                     parameters={"predicate": {"op": "eq", "field": "kind", "value": "a"},
                                 "features": _fc(2)})
        run = self._engine().execute_plan(ExecutionPlan(plan_id="p", nodes=[node]))
        assert run.status is ExecutionRunStatus.FAILED
        assert run.evidence["n"].error_code == "OPERATION_UNSUPPORTED"

    def test_admission_rejects_overbudget_estimates(self):
        heavy = _node("h", NodeCategory.QUERY,
                      estimate=ResourceEstimate(rows=10_000_000, confidence="high"),
                      parameters={"dataset_id": "d"})
        plan = ExecutionPlan(
            plan_id="p", nodes=[heavy],
            budget=ResourceBudget(max_rows=1000, max_bytes=256 * 1024 * 1024,
                                  deadline_s=30, max_nodes=8),
        )
        with pytest.raises(BudgetExceededError) as ei:
            self._engine().execute_plan(plan)
        assert "suggestions" in ei.value.details

    def test_row_budget_enforced_at_execution(self):
        # 10 features in / budget 2 rows → node fails with budget error
        node = _filter_node(n=10)
        plan = ExecutionPlan(
            plan_id="p", nodes=[node],
            budget=ResourceBudget(max_rows=2, max_bytes=256 * 1024 * 1024,
                                  deadline_s=30, max_nodes=8),
        )
        run = self._engine().execute_plan(plan)
        assert run.status is ExecutionRunStatus.FAILED
        assert run.evidence[node.node_id].error_code == "RESOURCE_BUDGET_EXCEEDED"

    def test_cancellation_propagates(self):
        token = CancellationToken()

        def slow(ctx, node, payloads):
            time.sleep(0.3)
            ctx.checkpoint()
            return {"rows": [], "metadata": {}}

        n1 = _node("n1", NodeCategory.SOURCE_SCAN, parameters={"dataset_id": "d"})
        n2 = _node("n2", NodeCategory.SOURCE_SCAN, parameters={"dataset_id": "d"})
        plan = ExecutionPlan(plan_id="p", nodes=[n1, n2])
        original = ops.REGISTRY[NodeCategory.SOURCE_SCAN]
        ops.REGISTRY[NodeCategory.SOURCE_SCAN] = slow
        engine = self._engine()

        def cancel_soon():
            time.sleep(0.05)
            token.cancel("user requested")

        import threading

        t = threading.Thread(target=cancel_soon)
        t.start()
        try:
            run = engine.execute_plan(plan, cancel_token=token)
        finally:
            ops.REGISTRY[NodeCategory.SOURCE_SCAN] = original
            t.join()
        assert run.status is ExecutionRunStatus.CANCELLED

    def test_parallel_wave_both_complete(self):
        n1 = _filter_node("a")
        n2 = _filter_node("b")
        run = self._engine().execute_plan(ExecutionPlan(plan_id="p", nodes=[n1, n2]))
        assert run.status is ExecutionRunStatus.COMPLETED
        assert run.evidence["a"].status in {"completed", "reused"}
        assert run.evidence["b"].status in {"completed", "reused"}

    def test_run_registry_bounded_and_gettable(self):
        eng = self._engine()
        run = eng.execute_plan(ExecutionPlan(plan_id="p", nodes=[_filter_node()]))
        assert eng.get_run(run.run_id) is not None
        assert eng.get_run("missing") is None


# -------------------------------------------------------------- operators


class TestOperators:
    def _ctx(self, **kw):
        return ops.OperatorContext(run_id="r", node_id="n", **kw)

    def test_filter_predicate_semantics(self):
        node = _filter_node(predicate={"op": "in", "field": "v", "values": [0, 2]})
        payload = ops.execute_node(self._ctx(), node, {})
        assert [f["properties"]["v"] for f in payload["features"]] == [0, 2]
        assert payload["metadata"]["filtered_from"] == 4

    def test_aggregate_group_count(self):
        node = _node("agg", NodeCategory.AGGREGATE, inputs=["src"],
                     parameters={"aggregates": [{"func": "count", "field": "v"}],
                                 "group_by": ["kind"]})
        src = _filter_node("src", predicate={"op": "in", "field": "v", "values": [0, 1, 2, 3]})
        payload = ops.execute_node(self._ctx(), node, {"src": ops.execute_node(self._ctx(), src, {})})
        groups = {r["kind"]: r for r in payload["rows"]}
        assert set(groups) == {"a", "b"}

    def test_spatial_join_points_in_polygons(self):
        points = _fc(3)
        polys = [
            {"type": "Feature",
             "geometry": {"type": "Polygon", "coordinates": [[[115.0, 38.0], [117.0, 38.0],
                                                             [117.0, 40.0], [115.0, 40.0],
                                                             [115.0, 38.0]]]},
             "properties": {"zone": "z1"}},
        ]
        left = _node("pts", NodeCategory.FILTER,
                     parameters={"predicate": {"op": "in", "field": "v", "values": [0, 1, 2]},
                                 "features": points})
        right = _node("polys", NodeCategory.FILTER,
                      parameters={"predicate": {"op": "eq", "field": "zone", "value": "z1"},
                                  "features": polys})
        join = _node("j", NodeCategory.SPATIAL_JOIN, inputs=["pts", "polys"],
                     parameters={"spatial_op": "within"})
        pts_out = ops.execute_node(self._ctx(), left, {})
        polys_out = ops.execute_node(self._ctx(), right, {})
        payload = ops.execute_node(self._ctx(), join, {"pts": pts_out, "polys": polys_out})
        assert payload["metadata"]["join_pairs"] == len(points)
        assert all("__right__" in r for r in payload["rows"])

    def test_materialize_stores_ref(self):
        node = _node("m", NodeCategory.MATERIALIZE, inputs=["src"],
                     parameters={"prefix": "geocompute"})
        src_payload = {"features": _fc(2), "metadata": {}}
        ctx = self._ctx(session_id="geocompute-test-session")
        payload = ops.execute_node(ctx, node, {"src": src_payload})
        assert payload["ref_id"].startswith("ref:geocompute-")
        assert payload["metadata"]["materialized_rows"] == 2

    def test_query_uses_injected_catalog_fn(self, monkeypatch):
        def fake_query(db, item_id, spec):
            assert item_id == "cat-1"
            return SimpleNamespace(features=_fc(2), total_matching=2,
                                   metadata={"query_plan": {"steps": []}})
        monkeypatch.setattr(ops, "query_catalog_fn", fake_query)
        node = _node("q", NodeCategory.QUERY,
                     parameters={"dataset_id": "cat-1", "query": {"limit": 10}})
        payload = ops.execute_node(self._ctx(), node, {})
        assert len(payload["features"]) == 2
        assert "query_plan" in payload["metadata"]

    def test_vector_operation_unsupported_op(self):
        node = _node("v", NodeCategory.VECTOR_OPERATION, operation="morph",
                     parameters={"features": _fc(1)})
        with pytest.raises(UnsupportedOperationError):
            ops.execute_node(self._ctx(), node, {})


# ---------------------------------------------------------------- tracing


class TestTracing:
    def test_events_emitted_and_sensitive_keys_dropped(self):
        from app.services.geocompute import tracing

        tracing.emit("node_completed", run_id="r1", node_id="n1", status="completed",
                     rows=3, payload={"secret": "data"}, credentials="x", sql="select 1")
        events = tracing.recent_events(limit=5)
        last = events[-1]
        assert last["event"] == "node_completed" and last["rows"] == 3
        assert "payload" not in last and "credentials" not in last and "sql" not in last

    def test_run_produces_trace_events(self):
        from app.services.geocompute import tracing

        before = len(tracing.recent_events(limit=1024))
        self._engine_min().execute_plan(ExecutionPlan(plan_id="p", nodes=[_filter_node()]))
        after = len(tracing.recent_events(limit=1024))
        assert after > before

    @staticmethod
    def _engine_min() -> GeoExecutionEngine:
        return GeoExecutionEngine(max_workers=1)
