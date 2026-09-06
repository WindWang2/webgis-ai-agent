"""ADR-0101 D7：有界序枚举、id 寻址 join、半连接约减、首跳服务端、
最小投影派生、分级下推模型与 planner↔capability parity 门。"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.federation import (
    MAX_FEDERATED_SOURCES,
    ChainJoin,
    ChainSource,
    ChainSourceStats,
    FederatedChainRequest,
    FederatedExecutor,
    FederatedQueryError,
    _chain_order_cost,
    _semi_join_reduce_right,
    plan_federated_chain,
)
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.pushdown import (
    PushdownClass,
    classify_plan_pushdowns,
    pushdown_profile,
)
from app.schemas.data_fabric_schema import QueryResult


class _RecordingAdapter:
    """记录每次 query 的 fields/where/limit 的假 adapter。"""

    source_type = "generic"

    def __init__(self, features_by_dataset: Dict[str, List[dict]]):
        self._data = features_by_dataset
        self.calls: List[Dict[str, Any]] = []
        self.query_plan_attached = {"estimated_rows": None}

    def query(self, dataset_id: str, spec: Any) -> QueryResult:
        self.calls.append({
            "dataset_id": dataset_id,
            "fields": getattr(spec, "fields", None),
            "where": getattr(spec, "where", None),
            "limit": getattr(spec, "limit", None),
        })
        feats = self._data.get(dataset_id, [])
        return QueryResult(dataset_id=dataset_id, features=list(feats))


def _executor(adapters: Dict[str, Any]) -> FederatedExecutor:
    return FederatedExecutor(lambda sid: adapters.get(sid))


def _rows(prefix: str, keys: List[int]) -> List[dict]:
    return [
        {"type": "Feature", "geometry": None,
         "properties": {"key": k, "payload": f"{prefix}{k}"}}
        for k in keys
    ]


# ------------------------------------------------------------- 序枚举 / 排序


class TestBoundedOrderEnumeration:
    def _req(self, strategy: str, with_ids: bool = True) -> FederatedChainRequest:
        sources = [
            ChainSource(source_id="big", dataset_id="d-big", estimated_rows=1_000_000),
            ChainSource(source_id="tiny", dataset_id="d-tiny", estimated_rows=100),
            ChainSource(source_id="mid", dataset_id="d-mid", estimated_rows=10_000),
        ]
        id_joins = [
            ChainJoin(kind="attribute_join", join_field_left="key",
                      join_field_right="key", left_source_id="tiny", right_source_id="big"),
            ChainJoin(kind="attribute_join", join_field_left="key",
                      join_field_right="key", left_source_id="big", right_source_id="mid"),
        ]
        positional = [
            ChainJoin(kind="attribute_join", join_field_left="key", join_field_right="key"),
            ChainJoin(kind="attribute_join", join_field_left="key", join_field_right="key"),
        ]
        return FederatedChainRequest(
            sources=sources,
            joins=id_joins if with_ids else positional,
            order_strategy=strategy,
            stats_hints={
                "tiny": ChainSourceStats(estimated_rows=100, column_ndv={"key": 100}),
                "big": ChainSourceStats(estimated_rows=1_000_000, column_ndv={"key": 1_000_000}),
                "mid": ChainSourceStats(estimated_rows=10_000, column_ndv={"key": 10_000}),
            },
        )

    def test_cost_stats_picks_minimal_cost_order(self):
        req = self._req("cost_stats")
        plans = plan_federated_chain(req)
        order = ["tiny", "big", "mid"]  # 首跳 100×1e6/1e6=100；次跳 100×10k/10k=100
        assert plans[0].left["source_id"] == order[0]
        assert [p.right["source_id"] for p in plans] == order[1:]

    def test_rejected_orders_disclosed(self):
        req = self._req("cost_stats")
        plans = plan_federated_chain(req)
        assert 1 <= len(plans[0].rejected_orders) <= 3
        assert all("order" in r and "cost" in r for r in plans[0].rejected_orders)
        assert any("bounded enumeration" in w for w in plans[0].warnings)

    def test_cost_model_prefers_high_ndv_first_pair(self):
        """显式验证成本排序方向：tiny→big 首跳基数远小于 big→mid。"""
        req = self._req("cost_stats")
        id_joins = {
            "tiny>big": 0, "big>mid": 1, "mid>tiny": None,
        }
        id_joins = {k: v for k, v in id_joins.items() if v is not None}
        cost_tb = _chain_order_cost([1, 0, 2], req, id_joins)  # tiny,big,mid
        cost_bm = _chain_order_cost([0, 2, 1], req, id_joins)  # big,mid,tiny
        assert cost_tb < cost_bm

    def test_no_stats_falls_back_to_v3_order(self):
        # id 寻址 + 无统计提示 → 回落 V3 排序；该序下 id join 不成链 → typed 失败。
        req = self._req("cost_stats")
        req.stats_hints = None
        with pytest.raises(FederatedQueryError, match="do not form a chain"):
            plan_federated_chain(req)
        # 位置寻址 + 无提示 → V3 序（estimated_rows 升序）。
        req2 = self._req("cost_stats", with_ids=False)
        req2.stats_hints = None
        plans2 = plan_federated_chain(req2)
        assert plans2[0].left["source_id"] == "tiny"
        assert [p.right["source_id"] for p in plans2] == ["mid", "big"]

    def test_positional_joins_with_stats_warn_fallback(self):
        req = self._req("cost_stats", with_ids=False)
        plans = plan_federated_chain(req)
        assert any("positional" in w for w in plans[0].warnings)

    def test_id_joins_not_forming_chain_fail_typed(self):
        req = self._req("cost_stats")
        req.joins[1] = ChainJoin(
            kind="attribute_join", join_field_left="key", join_field_right="key",
            left_source_id="tiny", right_source_id="mid")  # 两条都从 tiny 出发：无链
        with pytest.raises(FederatedQueryError, match="do not form a chain"):
            plan_federated_chain(req)

    def test_source_cap_unchanged(self):
        req = FederatedChainRequest(
            sources=[ChainSource(source_id=f"s{i}", dataset_id=f"d{i}")
                     for i in range(MAX_FEDERATED_SOURCES + 1)],
            joins=[ChainJoin(kind="attribute_join", join_field_left="k",
                             join_field_right="k") for _ in range(MAX_FEDERATED_SOURCES)],
        )
        with pytest.raises(FederatedQueryError, match="at most"):
            plan_federated_chain(req)


# ------------------------------------------------------------- 半连接约减


class TestSemiJoinReduction:
    def test_right_rows_reduced_to_left_keys(self):
        left = [{"properties": {"key": 1}}, {"properties": {"key": 2}}]
        right = _rows("r", [1, 2, 3, 4, 5])
        join = ChainJoin(kind="attribute_join", join_field_left="key", join_field_right="key")
        reduced, original = _semi_join_reduce_right(left, right, join)
        assert original == 5
        assert [r["properties"]["key"] for r in reduced] == [1, 2]

    def test_float_int_key_semantics_preserved(self):
        left = [{"properties": {"key": 1}}]
        right = _rows("r", [1, 2])
        right[0]["properties"]["key"] = 1.0
        join = ChainJoin(kind="attribute_join", join_field_left="key", join_field_right="key")
        reduced, _ = _semi_join_reduce_right(left, right, join)
        assert len(reduced) == 1

    def test_no_keys_honestly_skips(self):
        left = [{"properties": {"key": None}}]
        right = _rows("r", [1])
        join = ChainJoin(kind="attribute_join", join_field_left="key", join_field_right="key")
        reduced, original = _semi_join_reduce_right(left, right, join)
        assert reduced is right and original == 1

    def test_reduction_is_semantics_preserving_end_to_end(self):
        """约减后的链结果与不约减完全一致（3 源两跳属性连接）。"""
        a = _rows("a", [1, 2, 3])
        b = _rows("b", [2, 3, 99])     # 99 不在 A 中
        c = _rows("c", [3, 42])        # 42 不在 A⋈B 中
        adapters = {"sa": _RecordingAdapter({"d-a": a}),
                    "sb": _RecordingAdapter({"d-b": b}),
                    "sc": _RecordingAdapter({"d-c": c})}
        req = FederatedChainRequest(
            sources=[ChainSource("sa", "d-a", estimated_rows=3),
                     ChainSource("sb", "d-b", estimated_rows=3),
                     ChainSource("sc", "d-c", estimated_rows=2)],
            joins=[ChainJoin(kind="attribute_join", join_field_left="key",
                             join_field_right="key"),
                   ChainJoin(kind="attribute_join", join_field_left="key",
                             join_field_right="key")],
            limit=10_000,
        )
        result = _executor(adapters).execute_chain(req)
        assert result["row_count"] == 1  # 只有 key=3 全链匹配
        assert result["rows"][0]["key"] == 3


# ------------------------------------------------------- 首跳服务端快路径


class TestServerSideFirstHop:
    def test_same_source_spatial_hop_runs_server_side(self):
        class ServerAdapter(_RecordingAdapter):
            def server_spatial_join(self, left_ds, right_ds, *, join_op,
                                    group_by_polygon_field, limit):
                return [{"properties": {"grp": "g", "n": 1}}]

        adapter = ServerAdapter({"d-x": _rows("x", [1]),
                                 "d-y": [{"type": "Feature", "geometry": None,
                                          "properties": {"grp": "g", "other": 1}}]})
        req = FederatedChainRequest(
            sources=[ChainSource("sx", "d-x", estimated_rows=10),
                     ChainSource("sx", "d-x-poly", estimated_rows=5),
                     ChainSource("sy", "d-y", estimated_rows=1)],
            joins=[ChainJoin(kind="spatial_join", spatial_op="within"),
                   ChainJoin(kind="attribute_join", join_field_left="grp",
                             join_field_right="grp")],
            order_strategy="given",  # 保持 sx, sx, sy 顺序（同源首跳才可服务端）
        )
        result = _executor({
            "sx": adapter,
            "sy": _RecordingAdapter({"d-y": [{"type": "Feature", "geometry": None,
                                              "properties": {"grp": "g", "other": 1}}]}),
        }).execute_chain(req)
        assert result["strategy"] == "server_side_first_hop"
        assert result["row_count"] == 1
        assert result["rows"][0]["grp"] == "g"

    def test_server_failure_falls_back_to_local(self):
        class BrokenServerAdapter(_RecordingAdapter):
            def server_spatial_join(self, *a, **kw):
                raise RuntimeError("boom")

        adapter = BrokenServerAdapter({
            "d-x": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                     "properties": {"key": 1}}],
            "d-x-poly": [{"type": "Feature",
                          "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                          "properties": {"key": 1}}],
        })
        req = FederatedChainRequest(
            sources=[ChainSource("sx", "d-x"),
                     ChainSource("sx", "d-x-poly")],
            joins=[ChainJoin(kind="spatial_join", spatial_op="within")],
        )
        result = _executor({"sx": adapter}).execute_chain(req)
        assert result["strategy"] == "left_deep_chain"
        assert result["row_count"] == 1
        assert any("server-side first hop failed" in w for w in result["warnings"])


# ------------------------------------------------------------- 最小投影派生


class TestDerivedProjection:
    def test_derived_fields_cover_join_and_aggregate_needs(self):
        sources = [
            ChainSource("a", "d-a"), ChainSource("b", "d-b"), ChainSource("c", "d-c"),
        ]
        joins = [
            ChainJoin(kind="attribute_join", join_field_left="k1",
                      join_field_right="kb", left_source_id="a", right_source_id="b"),
            ChainJoin(kind="aggregate_join", join_field_left="kb",
                      join_field_right="kc", group_by_right=["grp"],
                      aggregates=[{"func": "sum", "field": "amt"}],
                      left_source_id="b", right_source_id="c"),
        ]
        req = FederatedChainRequest(sources=sources, joins=joins, derive_projection=True)
        plans = plan_federated_chain(req)
        # id 寻址 + 无排序提示 → given-ish V3 序（a,b,c）
        assert plans[0].left["fields"] == ["k1"]
        assert plans[0].right["fields"] == ["kb"]
        assert plans[1].right["fields"] == ["amt", "grp", "kc"]
        assert any("minimal projection derived" in w for w in plans[0].warnings)

    def test_projection_off_by_default(self):
        req = FederatedChainRequest(
            sources=[ChainSource("a", "d-a"), ChainSource("b", "d-b")],
            joins=[ChainJoin(kind="attribute_join", join_field_left="k",
                             join_field_right="k")],
        )
        plans = plan_federated_chain(req)
        assert "fields" not in plans[0].left
        assert "fields" not in plans[0].right

    def test_execution_passes_derived_fields_to_adapter(self):
        adapters = {
            "a": _RecordingAdapter({"d-a": _rows("a", [1])}),
            "b": _RecordingAdapter({"d-b": _rows("b", [1])}),
        }
        req = FederatedChainRequest(
            sources=[ChainSource("a", "d-a"), ChainSource("b", "d-b")],
            joins=[ChainJoin(kind="attribute_join", join_field_left="key",
                             join_field_right="key")],
            derive_projection=True,
        )
        result = _executor(adapters).execute_chain(req)
        assert result["row_count"] == 1
        assert adapters["a"].calls[0]["fields"] == ["key"]
        assert adapters["b"].calls[0]["fields"] == ["key"]


# ----------------------------------------------------------- 分级下推模型


class TestPushdownClasses:
    def test_profiles_by_source_type(self):
        postgis = pushdown_profile(get_capabilities("postgis"))
        assert postgis["scalar_filter"] is PushdownClass.EXACT
        assert postgis["spatial_predicate"] is PushdownClass.EXACT
        assert postgis["cql"] is PushdownClass.UNSUPPORTED
        assert postgis["join"] is PushdownClass.UNSUPPORTED
        assert postgis["spatial_join"] is PushdownClass.EXACT

        ogc = pushdown_profile(get_capabilities("ogc_api"))
        # ogc_api 默认不声明 filter_pushdown（CQL2 由 conformance 探针动态升级）
        assert ogc["scalar_filter"] is PushdownClass.UNSUPPORTED
        assert ogc["cql"] is PushdownClass.UNSUPPORTED
        assert ogc["temporal"] is PushdownClass.EQUIVALENT  # datetime 区间重映射

        gp = pushdown_profile(get_capabilities("geoparquet"))
        assert gp["bbox"] is PushdownClass.EXACT
        assert gp["spatial_predicate"] is PushdownClass.COARSE

    def test_violation_detected_for_unsupported_push(self):
        caps = get_capabilities("geoparquet")  # filter_pushdown=False
        report = classify_plan_pushdowns(
            {"has_filter": True, "pushed_filters": True}, caps)
        assert report["scalar_filter"] == "violation"

    def test_local_and_note_disclosure(self):
        caps = get_capabilities("geoparquet")
        report = classify_plan_pushdowns(
            {"has_filter": True, "pushed_filters": False,
             "has_spatial": True, "pushed_spatial": False},
            caps, spatial_op="intersects")
        assert report["scalar_filter"] == "local"
        assert report["spatial_predicate"] == "local"
        assert "coarse bbox prefilter available" in report["spatial_predicate_note"]


class TestPlannerCapabilityParity:
    """自动 parity 门（审计发现的缺口）：每个默认源类型 × 每个谓词族，
    planner 的推送决策必须与能力矩阵一致（violation = 契约缺陷）。"""

    SOURCE_TYPES = [
        "postgis", "ogc_api", "wfs", "arcgis", "geoparquet", "flatgeobuf",
        "stac", "pmtiles", "wms",
    ]

    @pytest.mark.parametrize("source_type", SOURCE_TYPES)
    def test_no_violations_and_flag_parity(self, source_type):
        from app.services.data_fabric.query.models import (
            ExecutionBudget,
            OffsetPage,
            OrderByItem,
            OutputSpec,
            QuerySpecV2,
        )

        caps = get_capabilities(source_type)
        descriptor = {
            "id": f"ds-{source_type}",
            "source_type": source_type,
            "feature_count": 1_000,
            "bbox": [0.0, 0.0, 1.0, 1.0],
            "geometry_type": "Point",
            "srs": "EPSG:4326",
            "fields": [{"name": "v", "type": "int"}, {"name": "g", "type": "str"}],
            "metadata": {},
        }
        spec = QuerySpecV2(
            select=["v"],
            filter={"op": "eq", "field": "g", "value": "a"},
            order_by=[OrderByItem(field="v", direction="asc")],
            page=OffsetPage(limit=100),
            output=OutputSpec(),
            execution=ExecutionBudget(),
        )
        plan = plan_query(spec, descriptor, caps=caps,
                          source_id="s1", dataset_fingerprint="fp",
                          query_fp="q1")
        assert "violation" not in plan.pushdown_classes.values(), plan.pushdown_classes
        # 布尔奇偶：过滤器推/不推必须与 caps 一致。
        assert bool(plan.pushed_filters) == (
            caps.filter_pushdown and spec.filter is not None)
        assert plan.pushed_sort == caps.sort_pushdown
        assert plan.pushed_projection == caps.projection_pushdown
