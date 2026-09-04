"""Data Fabric V3 成本优化器测试：统计/选择性/成本/备选/N源链（ADR-0096 D3）。

关键回归红线：无统计路径的选择率与行数估算必须与 V2 常数逐位一致。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.data_fabric.query import optimizer, selectivity
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
    FederatedQueryError,
    MAX_FEDERATED_SOURCES,
    plan_federated_chain,
)
from app.services.data_fabric.query.models import QuerySpecV2
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.predicates import predicate_from_dict
from app.services.data_fabric.query.statistics import (
    DatasetStatistics,
    ColumnStatistics,
    StatisticsStore,
    collect_postgis_statistics,
    statistics_from_descriptor,
)
from app.services.data_fabric.errors import QueryBudgetExceededError


def _descriptor(feature_count=100_000, bbox=None, source_type="postgis", meta=None):
    return SimpleNamespace(
        id="ds-1",
        source_type=source_type,
        feature_count=feature_count,
        bbox=bbox or [0.0, 0.0, 1.0, 1.0],
        metadata=meta or {},
    )


def _spec(filter_dict=None, spatial_bbox=None, aggregate=None, group_by=None):
    data: dict = {}
    if filter_dict:
        data["filter"] = filter_dict
    if spatial_bbox:
        data["spatial"] = {"op": "bbox", "bbox": spatial_bbox}
    if aggregate:
        data["aggregate"] = aggregate
        data["group_by"] = group_by or []
    return QuerySpecV2(**data)


# -------------------------------------------------------------- selectivity


class TestSelectivity:
    def test_no_stats_matches_v2_constants_exactly(self):
        eq = predicate_from_dict({"op": "eq", "field": "a", "value": 1})
        rng = predicate_from_dict({"op": "gt", "field": "a", "value": 1})
        inv = predicate_from_dict({"op": "in", "field": "a", "values": [1, 2, 3]})
        assert selectivity.estimate_predicate_selectivity(eq).value == 0.05
        assert selectivity.estimate_predicate_selectivity(rng).value == 0.25
        assert selectivity.estimate_predicate_selectivity(inv).value == pytest.approx(0.9)

    def test_stats_driven_eq_uses_ndv(self):
        stats = DatasetStatistics(
            dataset_fingerprint="fp",
            columns=[ColumnStatistics(name="zone", ndv=50, confidence="measured")],
        )
        eq = predicate_from_dict({"op": "eq", "field": "zone", "value": "x"})
        est = selectivity.estimate_predicate_selectivity(eq, stats)
        assert est.value == pytest.approx(1.0 / 50)
        assert est.basis == "statistics"
        other = predicate_from_dict({"op": "eq", "field": "untracked", "value": "x"})
        assert selectivity.estimate_predicate_selectivity(other, stats).basis == "default"

    def test_stats_driven_range_uses_minmax(self):
        stats = DatasetStatistics(
            dataset_fingerprint="fp",
            columns=[ColumnStatistics(name="v", min_value=0.0, max_value=100.0,
                                      confidence="measured")],
        )
        gt = predicate_from_dict({"op": "gt", "field": "v", "value": 25.0})
        est = selectivity.estimate_predicate_selectivity(gt, stats)
        assert est.value == pytest.approx(0.75)
        assert est.basis == "statistics"

    def test_null_fraction_semantics(self):
        stats = DatasetStatistics(
            dataset_fingerprint="fp",
            columns=[ColumnStatistics(name="a", null_fraction=0.4, confidence="measured")],
        )
        is_null = predicate_from_dict({"op": "is_null", "field": "a"})
        assert selectivity.estimate_predicate_selectivity(is_null, stats).value == pytest.approx(0.4)

    def test_group_cardinality_bounds_and_fallback(self):
        stats = DatasetStatistics(
            dataset_fingerprint="fp",
            columns=[ColumnStatistics(name="a", ndv=100), ColumnStatistics(name="b", ndv=20)],
        )
        assert selectivity.estimate_group_cardinality(["a", "b"], stats, None) == 2000
        assert selectivity.estimate_group_cardinality(["a", "zzz"], stats, None) == 5000
        assert selectivity.estimate_group_cardinality(["a"], stats, 50) == 50


# --------------------------------------------------------------- statistics


class TestStatistics:
    def test_harvest_from_descriptor_meta(self):
        d = _descriptor(meta={"row_count": 1234, "has_geometry_index": True,
                              "column_statistics": [{"name": "z", "ndv": 9,
                                                     "confidence": "estimated"}]})
        stats = statistics_from_descriptor(d)
        assert stats is not None
        assert stats.row_count == 1234
        assert stats.has_spatial_index is True
        assert stats.column("z").ndv == 9
        assert stats.confidence == "estimated"

    def test_honest_none_when_nothing_known(self):
        assert statistics_from_descriptor(_descriptor(feature_count=None, meta={})) is None

    def test_store_ttl_and_invalidate(self):
        store = StatisticsStore(ttl_s=60.0)
        stats = DatasetStatistics(dataset_fingerprint="fp1", row_count=10)
        store.put(stats)
        assert store.get("fp1") is not None
        store.invalidate("fp1")
        assert store.get("fp1") is None
        store.put(DatasetStatistics(dataset_fingerprint="fp2", row_count=1))
        store.invalidate()
        assert store.get("fp2") is None

    def test_postgis_probe_parses_rows_and_survives_errors(self):
        rows = [("zone", 25.0, 0.1), ("v", -1, None)]
        out = collect_postgis_statistics(lambda sql, params: rows, "public", "t")
        assert out["zone"] == {"ndv": 25, "null_fraction": 0.1, "confidence": "estimated"}
        assert out["v"]["ndv"] is None  # 负 n_distinct（pg 估计语义）→ None

        def boom(sql, params):
            raise RuntimeError("no pg_stats access")

        assert collect_postgis_statistics(boom, "public", "t") == {}


# ----------------------------------------------------------------- optimizer


class TestOptimizer:
    def test_alternatives_bounded_and_typed(self):
        alts = optimizer.generate_alternatives(
            source_type="http_csv", estimated_rows=1_000_000, page_window=10_000,
            budget_max_rows=50_000, budget_max_bytes=256 * 1024 * 1024,
            filter_pushed=False, spatial_pushed=False, aggregation_pushed=False,
            aggregate_requested=True, projection_pushed=True, has_select=False,
            order_by=False, sort_pushed=False, vector_tiles=False,
            result_mode="features",
        )
        assert len(alts) <= optimizer.MAX_ALTERNATIVES
        assert all(isinstance(a, optimizer.PlanAlternative) for a in alts)
        rejected = {a.name for a in alts if not a.feasible}
        assert "pushdown_attribute_filter" in rejected
        feasible = {a.name for a in alts if a.feasible}
        assert "sample_result_mode" in feasible

    def test_vector_tile_alternative_for_large_feature_queries(self):
        alts = optimizer.generate_alternatives(
            source_type="postgis", estimated_rows=500_000, page_window=10_000,
            budget_max_rows=50_000, budget_max_bytes=256 * 1024 * 1024,
            filter_pushed=True, spatial_pushed=True, aggregation_pushed=True,
            aggregate_requested=False, projection_pushed=True, has_select=False,
            order_by=False, sort_pushed=False, vector_tiles=True,
            result_mode="features",
        )
        tile = [a for a in alts if a.name == "vector_tile_path"]
        assert tile and tile[0].feasible
        assert tile[0].estimated_cost.bytes_transferred > 0

    def test_cost_score_prefers_pushdown(self):
        pushed = optimizer.cost_of_chosen(estimated_rows=10_000, estimated_bytes=18_000_000,
                                          pushed_any=True, local_rows=0)
        local = optimizer.cost_of_chosen(estimated_rows=10_000, estimated_bytes=18_000_000,
                                         pushed_any=False, local_rows=10_000)
        assert pushed.score() < local.score()

    def test_budget_suggestions_include_feasible_alternatives(self):
        alt = optimizer.PlanAlternative(name="sample_result_mode", description="s",
                                        feasible=True)
        suggestions = optimizer.budget_failure_suggestions([alt])
        assert any("sample_result_mode" in s for s in suggestions)


# ------------------------------------------------------------------- planner


class TestPlannerV3:
    def test_no_stats_plan_is_behavior_preserving(self):
        d = _descriptor()
        plan = plan_query(_spec(filter_dict={"op": "eq", "field": "a", "value": 1}), d)
        # V2: 100_000 * 1.0(bbox) * 0.05(eq) = 5000
        assert plan.estimated_rows == 5000
        assert plan.cost is not None
        assert any("default constants" in a for a in plan.assumptions)
        assert plan.statistics_confidence is None

    def test_stats_change_estimates_and_confidence(self):
        d = _descriptor()
        stats = DatasetStatistics(
            dataset_fingerprint="ds-1",
            row_count=100_000,
            columns=[ColumnStatistics(name="a", ndv=10_000, confidence="measured")],
        )
        plan = plan_query(
            _spec(filter_dict={"op": "eq", "field": "a", "value": 1}), d, stats=stats,
        )
        assert plan.estimated_rows == 10  # 100_000 * (1/10_000)
        assert plan.statistics_confidence == "measured"
        assert plan.assumptions == []

    def test_plan_alternatives_attached_and_summary_has_cost(self):
        d = _descriptor(source_type="postgis", meta={"has_geometry_index": False})
        plan = plan_query(_spec(spatial_bbox=[0.0, 0.0, 0.5, 0.5]), d)
        assert isinstance(plan.alternatives, list)
        assert len(plan.alternatives) <= optimizer.MAX_ALTERNATIVES
        lines = plan.summary_lines()
        assert any(line.startswith("Cost:") for line in lines)
        assert any(line.startswith("Alternative[") for line in lines)
        assert any(line.startswith("Assumption:") for line in lines)

    def test_model_dump_is_json_safe(self):
        d = _descriptor()
        plan = plan_query(_spec(), d)
        dumped = plan.model_dump()
        assert dumped["cost"]["remote_requests"] >= 1


# ---------------------------------------------------------------- federation


class TestChainFederation:
    def _req(self, **kw):
        sources = [
            ChainSource("s-big", "d-big", estimated_rows=900_000),
            ChainSource("s-small", "d-small", estimated_rows=500),
            ChainSource("s-mid", "d-mid", estimated_rows=40_000),
        ]
        joins = [
            ChainJoin(kind="attribute_join", join_field_left="k",
                      join_field_right="k"),
            ChainJoin(kind="attribute_join", join_field_left="k",
                      join_field_right="k"),
        ]
        return FederatedChainRequest(sources=sources, joins=joins, **{"limit": 1000, **kw})

    def test_plan_validation(self):
        with pytest.raises(FederatedQueryError, match="at least 2"):
            plan_federated_chain(FederatedChainRequest(sources=[ChainSource("a", "b")], joins=[]))
        too_many = FederatedChainRequest(
            sources=[ChainSource(f"s{i}", f"d{i}") for i in range(MAX_FEDERATED_SOURCES + 1)],
            joins=[ChainJoin(kind="attribute_join", join_field_left="k",
                             join_field_right="k")] * MAX_FEDERATED_SOURCES,
        )
        with pytest.raises(FederatedQueryError, match="at most"):
            plan_federated_chain(too_many)
        wrong_count = self._req()
        wrong_count.joins = wrong_count.joins[:1]
        with pytest.raises(FederatedQueryError, match="exactly"):
            plan_federated_chain(wrong_count)

    def test_cost_based_left_deep_ordering(self):
        plans = plan_federated_chain(self._req())
        assert plans[0].left["source_id"] == "s-small"
        assert plans[0].right["source_id"] == "s-mid"
        assert plans[1].right["source_id"] == "s-big"
        assert any("cost-based" in w for w in plans[0].warnings)

    def test_given_order_is_stable_and_labeled_assumption(self):
        req = self._req(order_strategy="given")
        plans = plan_federated_chain(req)
        assert plans[0].left["source_id"] == "s-big"
        assert any("assumption" in w for w in plans[0].warnings)

    def test_limit_over_budget_fails_fast(self):
        req = self._req(limit=10_000_000)
        with pytest.raises(QueryBudgetExceededError):
            plan_federated_chain(req)

    def test_execute_chain_folds_left_deep(self):
        rows = {
            "d-small": [{"properties": {"k": "a"}, "geometry": None}],
            "d-mid": [{"properties": {"k": "a", "mid": 1}, "geometry": None}],
            "d-big": [{"properties": {"k": "a", "big": 2}, "geometry": None}],
        }

        def factory(source_id):
            return SimpleNamespace(query=lambda ds, spec: SimpleNamespace(
                features=rows.get(ds, [])))

        result = FederatedExecutor(factory).execute_chain(self._req())
        assert result["status"] == "success"
        assert result["order"] == ["s-small", "s-mid", "s-big"]
        assert result["row_count"] == 1
        assert result["per_source_rows"] == {"s-small": 1, "s-mid": 1, "s-big": 1}
        row = result["rows"][0]
        assert row["k"] == "a"
        assert row["__right__"]["big"] == 2
        assert result["plans"][0]["kind"] == "attribute_join"

    def test_execute_chain_fail_fast_on_join_explosion(self):
        # 每源 50 行（< 预算 60），但键全部相同 → 首个 join 爆到 2500 行 → fail-fast
        big = [{"properties": {"k": "a"}, "geometry": None} for _ in range(50)]
        wide = [{"properties": {"k": "a"}, "geometry": None} for _ in range(50)]
        rows = {"d-small": big, "d-mid": wide, "d-big": []}

        def factory(source_id):
            return SimpleNamespace(query=lambda ds, spec: SimpleNamespace(
                features=rows.get(ds, [])))

        req = self._req(limit=10)
        req.budget.max_rows = 60
        # join 原语的 StreamingBudget 守卫或链级 fail-fast 都会以类型化预算
        # 错误终止链条 —— 不变量是「绝不静默截断」。
        with pytest.raises(QueryBudgetExceededError):
            FederatedExecutor(factory).execute_chain(req)

    def test_two_source_api_still_works(self):
        from app.services.data_fabric.query.federation import (
            FederatedQueryRequest,
            plan_federated,
        )

        req = FederatedQueryRequest(
            left_source_id="l", left_dataset_id="dl",
            right_source_id="r", right_dataset_id="dr",
            join_field_left="k", join_field_right="k",
        )
        plan = plan_federated(req)
        assert plan.kind == "attribute_join"
