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
from app.services.data_fabric.query.predicates import (
    evaluate_predicate,
    predicate_from_dict,
    temporal_from_dict,
)
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

    # ---------------------------------------- 链式正确性（对抗评审回归）--------

    def test_chain_hop2_join_key_from_prior_right_props(self):
        """F1：hop2 连接键只存在于 hop1 右属性（嵌在 __right__）→ 必须非空。

        修复前：左键仅顶层取值 → hop2 全部 miss → 静默 0 行。
        """
        sources = [
            ChainSource("s-a", "d-a", estimated_rows=100),
            ChainSource("s-b", "d-b", estimated_rows=200),
            ChainSource("s-c", "d-c", estimated_rows=300),
        ]
        joins = [
            ChainJoin(kind="attribute_join", join_field_left="k",
                      join_field_right="k"),
            ChainJoin(kind="attribute_join", join_field_left="e",
                      join_field_right="e"),
        ]
        rows = {
            "d-a": [{"properties": {"k": "a1"}, "geometry": None}],
            "d-b": [{"properties": {"k": "a1", "e": "E9"}, "geometry": None}],
            "d-c": [{"properties": {"e": "E9", "c": 7}, "geometry": None}],
        }

        def factory(source_id):
            return SimpleNamespace(query=lambda ds, spec: SimpleNamespace(
                features=rows.get(ds, [])))

        result = FederatedExecutor(factory).execute_chain(
            FederatedChainRequest(sources=sources, joins=joins, limit=100)
        )
        assert result["row_count"] == 1
        row = result["rows"][0]
        assert row["k"] == "a1"
        assert row["e"] == "E9"               # hop1 右属性的键已提升到顶层
        assert row["__right__"]["c"] == 7     # __right__ 是最后一跳右属性

    def test_chain_aggregate_hop_joins_right_source(self):
        """F2：聚合跳先连接右源、再按右源字段分组 —— 分组与聚合值正确。

        修复前：right_rows 被丢弃、分组键全部落空 → 单一全 None 组。
        """
        sources = [
            ChainSource("s-fact", "d-fact", estimated_rows=10),
            ChainSource("s-dim", "d-dim", estimated_rows=50),
        ]
        joins = [ChainJoin(
            kind="aggregate_join",
            join_field_left="zone", join_field_right="zone",
            group_by_right=["zone_name"],
            aggregates=[{"func": "count"}, {"func": "sum", "field": "v"}],
        )]
        rows = {
            "d-fact": [
                {"properties": {"zone": "z1", "v": 1}, "geometry": None},
                {"properties": {"zone": "z1", "v": 2}, "geometry": None},
                {"properties": {"zone": "z2", "v": 3}, "geometry": None},
            ],
            "d-dim": [
                {"properties": {"zone": "z1", "zone_name": "north"}, "geometry": None},
                {"properties": {"zone": "z2", "zone_name": "south"}, "geometry": None},
            ],
        }

        def factory(source_id):
            return SimpleNamespace(query=lambda ds, spec: SimpleNamespace(
                features=rows.get(ds, [])))

        result = FederatedExecutor(factory).execute_chain(
            FederatedChainRequest(sources=sources, joins=joins, limit=100)
        )
        assert result["row_count"] == 2
        by_name = {r["zone_name"]: r for r in result["rows"]}
        assert by_name["north"]["count"] == 2
        assert by_name["north"]["sum_v"] == 3
        assert by_name["south"]["count"] == 1
        assert by_name["south"]["sum_v"] == 3

    def test_chain_spatial_then_spatial(self):
        """F3：点在面在面 —— 左几何经 __left_geometry__ 跨跳携带，hop2 可入
        STRtree；修复前 hop1 累积行是扁平 dict（几何被丢弃）→ 静默 0 行。"""
        sources = [
            ChainSource("s-pts", "d-pts", estimated_rows=10),
            ChainSource("s-parcel", "d-parcel", estimated_rows=20),
            ChainSource("s-district", "d-district", estimated_rows=30),
        ]
        joins = [
            ChainJoin(kind="spatial_join", spatial_op="within"),
            ChainJoin(kind="spatial_join", spatial_op="within"),
        ]
        rows = {
            "d-pts": [{"properties": {"pid": 1},
                       "geometry": {"type": "Point", "coordinates": [0.5, 0.5]}}],
            "d-parcel": [{"properties": {"parcel": "P1"},
                          "geometry": {"type": "Polygon", "coordinates": [
                              [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}],
            "d-district": [{"properties": {"district": "D1"},
                            "geometry": {"type": "Polygon", "coordinates": [
                                [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}}],
        }

        def factory(source_id):
            return SimpleNamespace(query=lambda ds, spec: SimpleNamespace(
                features=rows.get(ds, [])))

        result = FederatedExecutor(factory).execute_chain(
            FederatedChainRequest(sources=sources, joins=joins, limit=100)
        )
        assert result["row_count"] == 1
        row = result["rows"][0]
        assert row["pid"] == 1
        assert row["__right__"]["district"] == "D1"

    def test_chain_spatial_hop_without_carried_geometry_is_typed_error(self):
        """F3：前置跳未携带几何的空间跳 → typed 失败，绝不静默 0 行。"""
        sources = [
            ChainSource("s-a", "d-a", estimated_rows=10),
            ChainSource("s-b", "d-b", estimated_rows=20),
            ChainSource("s-poly", "d-poly", estimated_rows=30),
        ]
        joins = [
            ChainJoin(kind="attribute_join", join_field_left="k",
                      join_field_right="k"),
            ChainJoin(kind="spatial_join", spatial_op="within"),
        ]
        rows = {
            "d-a": [{"properties": {"k": "a"}, "geometry": None}],
            "d-b": [{"properties": {"k": "a"}, "geometry": None}],
            "d-poly": [{"properties": {"p": 1},
                        "geometry": {"type": "Polygon", "coordinates": [
                            [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}],
        }

        def factory(source_id):
            return SimpleNamespace(query=lambda ds, spec: SimpleNamespace(
                features=rows.get(ds, [])))

        with pytest.raises(FederatedQueryError, match="no left geometry"):
            FederatedExecutor(factory).execute_chain(
                FederatedChainRequest(sources=sources, joins=joins, limit=100)
            )

    def test_mixed_crs_chain_fails_at_plan_time(self):
        """F4：≥2 个互不相同的非空 srs → 计划期 typed fail-fast；一致/缺省不报错。"""
        req = self._req()
        req.sources[0].srs = "EPSG:4326"
        req.sources[1].srs = "EPSG:3857"
        with pytest.raises(FederatedQueryError, match="mix CRS"):
            plan_federated_chain(req)
        same = self._req()
        same.sources[0].srs = same.sources[1].srs = "EPSG:4326"
        assert plan_federated_chain(same)

    def test_explicit_none_budget_falls_back_to_default(self):
        """F7：budget 显式 None（击穿 default_factory）→ 回落联邦默认，
        计划/执行路径都不抛 AttributeError。"""
        req = self._req()
        req.budget = None
        plans = plan_federated_chain(req)
        assert plans and plans[0].warnings
        rows: dict = {"d-small": [], "d-mid": [], "d-big": []}

        def factory(source_id):
            return SimpleNamespace(query=lambda ds, spec: SimpleNamespace(
                features=rows.get(ds, [])))

        result = FederatedExecutor(factory).execute_chain(req)
        assert result["status"] == "success"
        assert result["row_count"] == 0

    def test_plan_does_not_mutate_request_warnings(self):
        """F7：plan_federated_chain 是纯函数 —— warnings 随 plans[0] 返回，
        不追加到 req.warnings。"""
        req = self._req()
        plans = plan_federated_chain(req)
        assert req.warnings == []
        assert any("cost-based" in w for w in plans[0].warnings)
        assert plans[1].warnings == []


# ------------------------------------------------- 谓词 NULL 三值逻辑回归 ----


class TestPredicateNullSemantics:
    def test_not_in_with_null_member_never_true(self):
        """F5：SQL 三值逻辑 —— x NOT IN (1, NULL) 永不为 TRUE（unknown → 排除）。"""
        pred = predicate_from_dict({"op": "not_in", "field": "x", "values": [1, None]})
        assert evaluate_predicate(pred, {"x": 1}) is False     # 命中 → FALSE
        assert evaluate_predicate(pred, {"x": 2}) is False     # NULL 成员 → unknown
        assert evaluate_predicate(pred, {"x": None}) is False  # 左 NULL → unknown
        strict = predicate_from_dict({"op": "not_in", "field": "x", "values": [1, 2]})
        assert evaluate_predicate(strict, {"x": 3}) is True    # 无 NULL 成员保持 TRUE
        assert evaluate_predicate(strict, {"x": 1}) is False


# -------------------------------------------- 选择率时间谓词诚实标注回归 ----


class TestSelectivityTemporalBasis:
    def test_temporal_constant_fallback_is_assumption(self):
        """F6：before/after/during 落常数 0.25 时标 assumption，不冒充 statistics。"""
        stats = DatasetStatistics(
            dataset_fingerprint="fp",
            columns=[ColumnStatistics(name="t", min_value=0.0, max_value=100.0,
                                      confidence="measured")],
        )
        after = temporal_from_dict({"op": "after", "field": "t", "value": "2024-01-01"})
        est = selectivity.estimate_predicate_selectivity(after, stats)
        assert est.value == pytest.approx(0.25)
        assert est.basis == "assumption"
        during = temporal_from_dict(
            {"op": "during", "field": "t",
             "start": "2024-01-01", "end": "2024-02-01"})
        est2 = selectivity.estimate_predicate_selectivity(during, stats)
        assert est2.value == pytest.approx(0.25)
        assert est2.basis == "assumption"
        # 数值 range 谓词仍由统计推导，标 statistics 不变
        gt = predicate_from_dict({"op": "gt", "field": "t", "value": 25.0})
        num = selectivity.estimate_predicate_selectivity(gt, stats)
        assert num.value == pytest.approx(0.75)
        assert num.basis == "statistics"
