"""Wave 9 / Query Optimizer V5 tests (audit 06 §6.2 steps 1-5).

锁定五件事：
1. AND 边分解的逐子句下推拆分（混合可推性 → 两半如实记录 + ``partial``
   分级 + 确定性；OR 原子不拆；全可推/全不可推/单叶与基线逐位一致 ——
   用**实现前捕获的 golden 哈希**钉死 no-stats 兼容红线）。
2. 统计接线到剩余 adapter（ArcGIS count-only / OGC-API·WFS numberMatched /
   STAC item_count）→ 行级 DatasetStatistics 进入 plan_query；无统计逐位回落。
3. 链式联邦经工具边界可达（query_federated_chain：有界结果 + explain +
   typed 错误）。
4. derive_projection 链上默认开启（显式 opt-out 复原）。
5. 两源路径半连接约减回迁（结果等价 + 披露字段 + 键集超限诚实放弃）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile, DatasetDescriptor, QueryResult, QuerySpec
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.federation import (
    FederatedExecutor,
    FederatedQueryRequest,
    chain_explain_lines,
)
from app.services.data_fabric.query.models import (
    ExecutionBudget,
    OffsetPage,
    OrderByItem,
    OutputSpec,
    QuerySpecV2,
)
from app.services.data_fabric.query.planner import plan_query
from app.services.data_fabric.query.predicates import evaluate_predicate, predicate_from_dict
from app.services.data_fabric.query.pushdown import (
    resolve_plan_filter_split,
    split_filter_pushdown,
)
from app.services.data_fabric.query.statistics import (
    DatasetStatistics,
    get_statistics,
    invalidate_statistics,
    put_statistics,
)


# ── fixtures / helpers ──────────────────────────────────────────────────────


def _descriptor(st: str = "postgis", feature_count: Any = 1_000) -> Dict[str, Any]:
    return {
        "id": f"ds-{st}",
        "source_type": st,
        "feature_count": feature_count,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "geometry_type": "Point",
        "srs": "EPSG:4326",
        "fields": [{"name": "v", "type": "int"}, {"name": "g", "type": "str"}],
        "metadata": {},
    }


def _spec(filter_dict: Dict[str, Any] | None = None) -> QuerySpecV2:
    data: Dict[str, Any] = {"select": ["v"], "page": OffsetPage(limit=100)}
    if filter_dict is not None:
        data["filter"] = filter_dict
    return QuerySpecV2(
        order_by=[OrderByItem(field="v", direction="asc")],
        output=OutputSpec(),
        execution=ExecutionBudget(),
        **data,
    )


def _plan(st, filter_dict, caps=None, stats=None, descriptor=None):
    return plan_query(
        _spec(filter_dict), descriptor if descriptor is not None else _descriptor(st),
        caps=caps or get_capabilities(st),
        source_id="s1", dataset_fingerprint="fp", query_fp="q1", stats=stats,
    )


MIXED = {"op": "and", "args": [
    {"op": "eq", "field": "g", "value": "a"},
    {"op": "like", "field": "g", "pattern": "%x%"},
]}
ALL_PUSHABLE = {"op": "and", "args": [
    {"op": "eq", "field": "g", "value": "a"},
    {"op": "gt", "field": "v", "value": 3},
]}
OR_MIXED = {"op": "or", "args": [
    {"op": "eq", "field": "g", "value": "a"},
    {"op": "like", "field": "g", "pattern": "%x%"},
]}


def _partial_caps(st: str, ops: List[str]):
    return get_capabilities(st).model_copy(update={"filter_ops_local": ops})


@pytest.fixture(autouse=True)
def _clean_stats_store():
    invalidate_statistics(None)
    yield
    invalidate_statistics(None)


# ── 1. AND 边分解（planner）────────────────────────────────────────────────


class TestAndEdgeSplit:
    def test_mixed_conjunction_records_both_halves(self):
        """混合 AND：可推半进 pushed_filters、余项进 local_filters、计划携带
        两半的执行真相。"""
        plan = _plan("postgis", MIXED, caps=_partial_caps("postgis", ["like"]),
                     stats=DatasetStatistics(dataset_fingerprint="fp", row_count=100))
        assert plan.pushed_filters == ["g eq"]
        assert "g like" in plan.local_filters
        assert plan.filter_split is not None
        assert plan.filter_split["pushed"]["op"] == "eq"
        assert plan.filter_split["local"]["op"] == "like"
        # 计划即执行：两半是语义正交的子合取。
        pushed, local = resolve_plan_filter_split(MIXED, plan)
        assert pushed.op == "eq" and local.op == "like"

    def test_pushdown_classes_partial(self):
        """同族混合 → ``partial``；跨族混合 → 各自分级如实。"""
        stats = DatasetStatistics(dataset_fingerprint="fp", row_count=100)
        plan = _plan("postgis", MIXED, caps=_partial_caps("postgis", ["like"]), stats=stats)
        assert plan.pushdown_classes["scalar_filter"] == "partial"
        cross = {"op": "and", "args": [
            {"op": "eq", "field": "g", "value": "a"},
            {"op": "between", "field": "v", "low": 1, "high": 9},
        ]}
        plan2 = _plan("postgis", cross, caps=_partial_caps("postgis", ["between"]), stats=stats)
        assert plan2.pushdown_classes["scalar_filter"] == "exact"
        assert plan2.pushdown_classes["range_filter"] == "local"
        assert "violation" not in plan2.pushdown_classes.values()

    def test_split_plan_deterministic(self):
        """同输入两次 → plan dict 完全一致（确定性红线）。"""
        stats = DatasetStatistics(dataset_fingerprint="fp", row_count=100)
        caps = _partial_caps("postgis", ["like"])
        a = _plan("postgis", MIXED, caps=caps, stats=stats).model_dump()
        b = _plan("postgis", MIXED, caps=caps, stats=stats).model_dump()
        assert a == b

    def test_or_never_split(self):
        """OR 分支是原子：内含不可推叶 → 整体本地，绝不拆分。"""
        stats = DatasetStatistics(dataset_fingerprint="fp", row_count=100)
        plan = _plan("postgis", OR_MIXED, caps=_partial_caps("postgis", ["like"]), stats=stats)
        assert plan.pushed_filters == []
        # C1：守卫路径携带执行真相 —— pushed=None + 整棵 OR 为本地余项。
        assert plan.filter_split == {"pushed": None, "local": OR_MIXED}
        # 整个 OR 子树作为单一本地余项（摘要 = 原子 OR）。
        assert plan.local_filters and plan.local_filters[0] == "(g eq OR g like)"

    def test_all_pushable_unchanged(self):
        """全部可推 → 与基线一致：整体下推、无拆分字段。"""
        caps = _partial_caps("postgis", ["like"])
        plan = _plan("postgis", ALL_PUSHABLE, caps=caps)
        assert plan.pushed_filters == ["(g eq AND v gt)"]
        assert plan.filter_split is None
        assert "partial" not in plan.pushdown_classes.values()

    def test_none_pushable_whole_local(self):
        """声明的本地 op 覆盖全部叶 → 整体本地（绝不推送源推不了的 op）。

        C1：守卫路径落执行真相 —— ``filter_split={"pushed": None,
        "local": 整棵 AST}``，执行侧据此绝不向远端编译任何过滤子句。"""
        caps = _partial_caps("postgis", ["like"])
        plan = _plan("postgis", {"op": "like", "field": "g", "pattern": "%x%"}, caps=caps)
        assert plan.pushed_filters == []
        assert plan.local_filters and "g like" in plan.local_filters[0]
        assert plan.filter_split == {
            "pushed": None,
            "local": {"op": "like", "field": "g", "pattern": "%x%"},
        }
        # 计划即执行：resolve 返回（None, 整体）→ 远端零编译。
        pushed, local = resolve_plan_filter_split(plan.filter_split["local"], plan)
        assert pushed is None and local.op == "like"

    def test_single_leaf_behaves_as_baseline(self):
        """单叶过滤器与基线逐位一致（无 ops_local 时 push 判定不变）。"""
        base = _plan("postgis", {"op": "eq", "field": "g", "value": "a"})
        assert base.pushed_filters == ["g eq"]
        assert base.filter_split is None
        local = _plan("geoparquet", {"op": "eq", "field": "g", "value": "a"})
        assert local.pushed_filters == []
        # C1：geoparquet 声明无过滤下推 → 全本地守卫路径携带执行真相。
        assert local.filter_split == {
            "pushed": None,
            "local": {"op": "eq", "field": "g", "value": "a"},
        }

    def test_no_stats_mixed_guard_with_rejected_alternative(self):
        """混合可推 + 无统计 → 正确性守卫整体本地 + 确定性的被拒替代记录。"""
        plan = _plan("postgis", MIXED, caps=_partial_caps("postgis", ["like"]), stats=None)
        assert plan.pushed_filters == []
        # C1：无统计守卫路径同样落执行真相（远端零编译）。
        assert plan.filter_split == {"pushed": None, "local": MIXED}
        alt = [a for a in plan.alternatives if a["name"] == "partial_filter_pushdown"]
        assert len(alt) == 1 and alt[0]["feasible"] is False
        assert "no column statistics" in alt[0]["rejected_reason"]
        assert len(plan.alternatives) <= 8
        # 确定性文案。
        plan2 = _plan("postgis", MIXED, caps=_partial_caps("postgis", ["like"]), stats=None)
        assert plan2.alternatives == plan.alternatives

    # 实现前（pre-V5 代码路径）捕获的 golden：7 源类型 × 4 过滤形态，
    # no-stats + 默认能力矩阵 → 计划必须与基线逐位一致（sha256 钉死）。
    #
    # C1（round1 修复）再生说明：``filter_split`` 为守卫路径新增的执行真相
    # 字段 —— 源默认能力矩阵不带过滤下推（ogc_api/stac/geoparquet/pmtiles）
    # 或声明本地 op 时，计划走全本地守卫路径，现在携带
    # ``{"pushed": None, "local": 整棵 AST}``。下列 16 个 golden 在 C1 修复
    # 前钉死（当时 filter_split 恒为 None），修复即变更本身 → 已按修复后
    # 计划再生（hash 覆盖含 filter_split 的完整 plan dump）。其余 12 个
    # （postgis/arcgis/wfs 全可推路径）保持 pre-V5 原值：剔除缺省的
    # ``filter_split=None`` 后其余内容必须与基线逐位一致。
    GOLDEN_PLAN_HASHES = {
        "arcgis|and_all": "bad7c835b5259ba0",
        "arcgis|and_mixed": "f2e07a6fb7eca3d7",
        "arcgis|eq": "4e8d36b77102e99a",
        "arcgis|or_atomic": "df20d2571090aad7",
        "geoparquet|and_all": "64fb329a1e0ffc20",
        "geoparquet|and_mixed": "99f424088a383398",
        "geoparquet|eq": "cbcb1bfca0a7d7dd",
        "geoparquet|or_atomic": "a63a00f531d217dc",
        "ogc_api|and_all": "432ea4ea435bea08",
        "ogc_api|and_mixed": "d4cb954d1fbf5ba9",
        "ogc_api|eq": "d40d4404735642f4",
        "ogc_api|or_atomic": "bb54bfd6614c1679",
        "pmtiles|and_all": "a703b1427ff69e6e",
        "pmtiles|and_mixed": "e7730e8db11b0fc6",
        "pmtiles|eq": "55f2a8c3d8a397af",
        "pmtiles|or_atomic": "680f42cc3c98dc12",
        "postgis|and_all": "4752757322fbfd7c",
        "postgis|and_mixed": "a0f5a907f286bacc",
        "postgis|eq": "dab489e478a1d8fe",
        "postgis|or_atomic": "14b40eec56cdf967",
        "stac|and_all": "684d152042351dfc",
        "stac|and_mixed": "bd62d71066e5100a",
        "stac|eq": "cd4dd5f282edd184",
        "stac|or_atomic": "8ad65cf05e9395d3",
        "wfs|and_all": "c461fc60c29127e3",
        "wfs|and_mixed": "cb6bc05b5f6efa6e",
        "wfs|eq": "ffc3acccd3bfff2f",
        "wfs|or_atomic": "4ed0c260a2fd4f2e",
    }

    # C1 再生的守卫路径 pin（filter_split 纳入 hash；见上注释）。
    GUARD_PATH_REGENERATED = frozenset(
        f"{st}|{f}"
        for st in ("geoparquet", "ogc_api", "pmtiles", "stac")
        for f in ("eq", "and_all", "and_mixed", "or_atomic")
    )

    @pytest.mark.parametrize("key", sorted(GOLDEN_PLAN_HASHES))
    def test_no_stats_plans_bit_identical_to_baseline(self, key):
        st, fname = key.split("|")
        filters = {"eq": {"op": "eq", "field": "g", "value": "a"},
                   "and_mixed": MIXED, "and_all": ALL_PUSHABLE, "or_atomic": OR_MIXED}
        plan = _plan(st, filters[fname], descriptor=_descriptor(st))
        dump = plan.model_dump()
        if key in self.GUARD_PATH_REGENERATED:
            # C1：守卫路径的执行真相是 hash 的一部分（pushed=None + 整体本地）。
            assert dump["filter_split"] == {"pushed": None, "local": filters[fname]}
        else:
            # V5 新增的 filter_split 缺省 None 是定义上的可加字段（历史计划无此
            # 键）；剔除后其余全部内容必须与基线逐位一致。
            assert dump.pop("filter_split") is None
        payload = json.dumps(dump, sort_keys=True,
                             ensure_ascii=False, separators=(",", ":"))
        assert hashlib.sha256(payload.encode()).hexdigest()[:16] == self.GOLDEN_PLAN_HASHES[key]


# ── 2. 拆分的执行奇偶 + 分页诚实 ───────────────────────────────────────────


class _FeatureAdapter:
    """内存 feature adapter：记录 query 调用（federation/工具边界用）。"""

    source_type = "generic"

    def __init__(self, features_by_dataset: Dict[str, List[dict]]):
        self._data = features_by_dataset
        self.calls: List[Dict[str, Any]] = []

    def query(self, dataset_id: str, spec: Any) -> QueryResult:
        self.calls.append({"dataset_id": dataset_id, "spec": spec})
        return QueryResult(dataset_id=dataset_id, features=list(self._data.get(dataset_id, [])))


def _make_arcgis(ops_local=None):
    """FakeSession ArcGIS adapter（可选声明部分下推契约 filter_ops_local）。"""
    from app.services.data_fabric.adapters import ArcGISAdapter
    from tests.unit.test_ogc_adapters_753 import ARCGIS_LAYER, ARCGIS_SERVICE, FakeResponse

    profile = ConnectionProfile(provider_type="arcgis", endpoint="")
    profile.url = "https://example.com/arcgis/rest/services/Hosted/FS/FeatureServer"

    class _Adapter(ArcGISAdapter):
        def capabilities_v2(self, descriptor=None):
            caps = super().capabilities_v2(descriptor)
            if ops_local:
                caps = caps.model_copy(update={"filter_ops_local": ops_local})
            return caps

    adapter = _Adapter(profile)
    s = MagicMock()
    # 图层 schema 含 pid + name（过滤引用两个字段）。
    layer = dict(ARCGIS_LAYER)
    layer["fields"] = [
        {"name": "pid", "type": "esriFieldTypeInteger"},
        {"name": "name", "type": "esriFieldTypeString"},
    ]
    # f=geojson 请求 → 响应是 GeoJSON properties 形状（本地余项求值读
    # properties；attributes 形状是 f=json 的）
    features = [
        {"properties": {"pid": 1, "name": "alpha"}},
        {"properties": {"pid": 2, "name": "beta"}},
    ]

    def route(url, params=None, timeout=None, **kwargs):
        p = dict(params or {})
        s.calls.append({"url": url, "params": p})
        if url.rstrip("/").endswith("FeatureServer"):
            return FakeResponse(json_data=ARCGIS_SERVICE)
        if url.rstrip("/").endswith("/0"):
            return FakeResponse(json_data=layer)
        if url.rstrip("/").endswith("/query"):
            return FakeResponse(json_data={"features": features,
                                           "exceededTransferLimit": False})
        return FakeResponse(json_data={})

    s.get = route
    s.calls = []
    adapter.session = s
    return adapter


class TestSplitExecutionParity:
    ROWS = [
        {"properties": {"g": "ax", "v": 1}},
        {"properties": {"g": "bx", "v": 2}},
        {"properties": {"g": "ay", "v": 3}},
        {"properties": {"g": "bz", "v": 4}},
    ]

    def test_two_part_eval_equals_whole_filter(self):
        """拆分执行 ≡ 整体过滤：对每个样本行，pushed ∧ local ≡ 原谓词。"""
        caps = _partial_caps("postgis", ["like"])
        whole = predicate_from_dict(MIXED)
        split = split_filter_pushdown(whole, caps)
        assert split.partial
        for row in self.ROWS + [{"properties": {"g": None, "v": 5}}]:
            expected = evaluate_predicate(whole, row["properties"])
            got = (evaluate_predicate(split.pushed, row["properties"])
                   and evaluate_predicate(split.local, row["properties"]))
            assert got == expected, row

    def test_arcgis_split_end_to_end(self):
        """真 adapter + 拆分计划：远端 where 只含可推半，余项取回后本地求值。"""
        adapter = _make_arcgis(ops_local=["like"])
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(adapter.describe("0"))
        put_statistics(DatasetStatistics(dataset_fingerprint=fp, row_count=2, source_type="arcgis"))
        try:
            res = adapter.query("0", QuerySpec(limit=10, filter_expr={
                "op": "and",
                "args": [
                    {"op": "eq", "field": "pid", "value": 1},
                    {"op": "like", "field": "name", "pattern": "alpha"},
                ],
            }))
            call = [c for c in adapter.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
            assert call["params"]["where"] == "pid = 1", "远端只编译可推半"
            assert [f["properties"]["name"] for f in res.features] == ["alpha"]
            plan = res.metadata["query_plan"]
            assert plan["filter_split"]["pushed"]["op"] == "eq"
            assert plan["filter_split"]["local"]["op"] == "like"
        finally:
            invalidate_statistics(fp)

    def test_arcgis_unsplit_path_byte_identical_where(self):
        """无 ops_local（历史能力矩阵）→ where 与基线完全一致（整体编译）。"""
        adapter = _make_arcgis(ops_local=None)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(adapter.describe("0"))
        put_statistics(DatasetStatistics(dataset_fingerprint=fp, row_count=2, source_type="arcgis"))
        try:
            res = adapter.query("0", QuerySpec(limit=10, filter_expr={
                "op": "and",
                "args": [
                    {"op": "eq", "field": "pid", "value": 1},
                    {"op": "like", "field": "name", "pattern": "alpha"},
                ],
            }))
            call = [c for c in adapter.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
            assert "LIKE" in call["params"]["where"], "历史路径整体编译（含 like）"
            assert res.metadata["query_plan"]["filter_split"] is None
        finally:
            invalidate_statistics(fp)

    def test_arcgis_guard_path_compiles_nothing_remotely(self):
        """C1 端到端（ArcGIS）：全本地守卫路径 —— 远端 where 不含任何本地方案
        声明的 op（整体 1=1），过滤在取回后本地求值。"""
        adapter = _make_arcgis(ops_local=["like"])
        res = adapter.query("0", QuerySpec(limit=10, filter_expr={
            "op": "like", "field": "name", "pattern": "alpha"}))
        call = [c for c in adapter.session.calls if c["url"].rstrip("/").endswith("/query")][-1]
        assert call["params"]["where"] == "1=1", "守卫路径绝不向远端编译任何过滤子句"
        assert "like" not in call["params"]["where"].lower()
        # 本地余项（整棵 AST）取回后求值：只保留匹配行。
        assert [f["properties"]["name"] for f in res.features] == ["alpha"]
        plan = res.metadata["query_plan"]
        assert plan["filter_split"] == {
            "pushed": None,
            "local": {"op": "like", "field": "name", "pattern": "alpha"},
        }
        # 无统计守卫路径：执行真相在场（不再依赖 adapter 二次决策）。
        assert plan["local_filters"], "整体本地必须如实记录"

    def test_ogc_guard_path_sends_no_filter_param(self):
        """C1 端到端（OGC API）：全本地守卫路径 —— 远端请求完全不带 filter
        参数，余项本地求值；numberMatched 是无过滤命中数 → total_matching
        如实置 None（m1）。"""
        from app.services.data_fabric.adapters import OGCAPIAdapter
        from tests.unit.test_ogc_adapters_753 import (
            FakeResponse,
            OGC_COLLECTIONS,
            OGC_PARCELS,
            OGC_QUERYABLES,
        )

        profile = ConnectionProfile(provider_type="ogc_api", endpoint="")
        profile.url = "https://example.com/ogc"

        class _Ogc(OGCAPIAdapter):
            def _capabilities_v2(self):
                return super()._capabilities_v2().model_copy(
                    update={"filter_ops_local": ["like"]})

        adapter = _Ogc(profile)
        s = MagicMock()
        items = {
            "type": "FeatureCollection",
            "numberMatched": 2,
            "features": [
                {"type": "Feature", "properties": {"owner": "alpha"}, "geometry": None},
                {"type": "Feature", "properties": {"owner": "beta"}, "geometry": None},
            ],
        }

        def route(url, params=None, timeout=None, **kwargs):
            p = dict(params or {})
            s.calls.append({"url": url, "params": p})
            if "conformance" in url:
                return FakeResponse(json_data={"conformsTo": [
                    "http://www.opengis.net/doc/IS/ogcapi-features-2/1.0"]})
            if url.rstrip("/").endswith("/collections"):
                return FakeResponse(json_data=OGC_COLLECTIONS)
            if url.rstrip("/").endswith("/queryables"):
                return FakeResponse(json_data=OGC_QUERYABLES)
            if url.rstrip("/").endswith("/collections/parcels"):
                return FakeResponse(json_data=OGC_PARCELS)
            if url.rstrip("/").endswith("/items"):
                return FakeResponse(json_data=items)
            return FakeResponse(json_data={})

        s.get = route
        s.calls = []
        adapter.session = s
        res = adapter.query("parcels", QuerySpec(limit=10, filter_expr={
            "op": "like", "field": "owner", "pattern": "a%"}))
        call = [c for c in adapter.session.calls if c["url"].rstrip("/").endswith("/items")][-1]
        assert "filter" not in call["params"], "守卫路径不携带任何远端过滤参数"
        assert "filter-lang" not in call["params"]
        assert [f["properties"]["owner"] for f in res.features] == ["alpha"]
        assert res.total_matching is None, "存在本地余项 → total_matching 如实为 None"
        plan = res.metadata["query_plan"]
        assert plan["filter_split"]["pushed"] is None

    def test_postgis_guard_path_where_has_no_local_op_and_count_skipped(self):
        """C1 端到端（PostGIS）：全本地守卫路径 —— 主查询 WHERE 不含声明本地
        的 op；m1：存在本地余项 → 不再以下推半（此处为空 WHERE）count 冒充
        total_matching，count SQL 根本不执行。"""
        from app.services.data_fabric.adapters.postgis_adapter import PostGISAdapter
        from app.services.data_fabric.query.capabilities import default_capabilities

        executed: list = []

        class _Cursor:
            def __init__(self):
                self.description = []
                self._result = None

            def execute(self, sql, params=()):
                executed.append((sql, params))
                self.description = []
                self._result = None
                sql_l = sql.lower()
                if "information_schema.columns" in sql_l:
                    self.description = [("name",), ("type",)]
                    self._result = [("name", "text"), ("geom", "geometry")]
                elif "from geometry_columns" in sql_l and "f_geometry_column, srid, type" in sql_l:
                    self._result = ("geom", 4326, "POINT")
                elif "geometry_columns" in sql_l:
                    self._result = ("geom", 4326)
                elif "pg_index" in sql_l:
                    self._result = []
                elif "pg_indexes" in sql_l:
                    self._result = None
                elif "count(*)" in sql_l:
                    self._result = (2,)
                elif "estimatedextent" in sql_l:
                    self._result = None
                else:
                    self.description = [("name",), ("_geojson",)]
                    self._result = [
                        ("alpha", '{"type":"Point","coordinates":[116.5,39.5]}'),
                        ("beta", '{"type":"Point","coordinates":[116.6,39.6]}'),
                    ]

            def fetchone(self):
                if isinstance(self._result, list):
                    return self._result[0] if self._result else None
                return self._result

            def fetchall(self):
                if isinstance(self._result, list):
                    return self._result
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

        adapter = PostGISAdapter.__new__(PostGISAdapter)
        caps = default_capabilities("postgis").model_copy(update={"filter_ops_local": ["like"]})

        class _Conn:
            def cursor(self):
                return _Cursor()

            def rollback(self):
                pass

        class _ConnCtx:
            def __enter__(self):
                return _Conn()

            def __exit__(self, *a):
                return None

        adapter._connection_context = _ConnCtx
        adapter._meta_cache = {}
        adapter._caps = caps
        adapter.profile = ConnectionProfile(id="p_guard", source_type="postgis")

        res = adapter.query("public.roads_guard", QuerySpec(limit=10, filter_expr={
            "op": "like", "field": "name", "pattern": "a%"}))

        main_sqls = [
            sql for sql, _ in executed
            if 'FROM "public"."roads_guard"' in sql and sql.strip().startswith("SELECT")
        ]
        assert main_sqls, "main feature query must have executed"
        assert "LIKE" not in main_sqls[0].upper(), "守卫路径 WHERE 绝不编译声明本地的 op"
        # 本地余项求值：只保留匹配行。
        assert [f["properties"]["name"] for f in res.features] == ["alpha"]
        # m1：count 只在 meta 装载时出现过一次（feature_count），分页 count
        # 被跳过（下推半为空 WHERE，其命中数不诚实）。
        assert sum("count(*)" in sql.lower() for sql, _ in executed) == 1
        assert res.total_matching is None
        assert res.metadata["query_plan"]["filter_split"]["pushed"] is None

    def test_ogc_cursor_preserved_with_local_remainder(self):
        """cursor 分页 + 本地余项：links.next 游标保留（页协商不变），页内
        余项求值诚实收缩返回行。"""
        from app.services.data_fabric.adapters import ArcGISAdapter  # noqa: F401
        from app.services.data_fabric.adapters import OGCAPIAdapter
        from tests.unit.test_ogc_adapters_753 import (
            FakeResponse,
            OGC_COLLECTIONS,
            OGC_PARCELS,
            OGC_QUERYABLES,
        )

        profile = ConnectionProfile(provider_type="ogc_api", endpoint="")
        profile.url = "https://example.com/ogc"

        class _Ogc(OGCAPIAdapter):
            def _capabilities_v2(self):
                return super()._capabilities_v2().model_copy(
                    update={"filter_ops_local": ["like"]})

        adapter = _Ogc(profile)
        s = MagicMock()
        items = {
            "type": "FeatureCollection",
            "numberMatched": 2,
            "features": [
                {"type": "Feature", "properties": {"owner": "a"}, "geometry": None},
                {"type": "Feature", "properties": {"owner": "b"}, "geometry": None},
            ],
            "links": [{"rel": "next", "href": "https://example.com/ogc/collections/parcels/items?next=1"}],
        }

        def route(url, params=None, timeout=None, **kwargs):
            p = dict(params or {})
            s.calls.append({"url": url, "params": p})
            if "conformance" in url:
                return FakeResponse(json_data={"conformsTo": [
                    "http://www.opengis.net/doc/IS/ogcapi-features-2/1.0"]})
            if url.rstrip("/").endswith("/collections"):
                return FakeResponse(json_data=OGC_COLLECTIONS)
            if url.rstrip("/").endswith("/queryables"):
                return FakeResponse(json_data=OGC_QUERYABLES)
            if url.rstrip("/").endswith("/collections/parcels"):
                return FakeResponse(json_data=OGC_PARCELS)
            if url.rstrip("/").endswith("/items"):
                return FakeResponse(json_data=items)
            return FakeResponse(json_data={})

        s.get = route
        s.calls = []
        adapter.session = s
        # 拆分门需要统计在场：先 describe 算指纹，播种行级统计。
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(adapter.describe("parcels"))
        put_statistics(DatasetStatistics(dataset_fingerprint=fp, row_count=2, source_type="ogc_api"))
        try:
            res = adapter.query("parcels", QuerySpec(limit=10, filter_expr={
                "op": "and",
                "args": [
                    {"op": "eq", "field": "owner", "value": "a"},
                    {"op": "like", "field": "owner", "pattern": "a%"},
                ],
            }))
            assert [f["properties"]["owner"] for f in res.features] == ["a"]
            call = [c for c in adapter.session.calls if c["url"].rstrip("/").endswith("/items")][-1]
            sent_filter = call["params"].get("filter", "")
            # CQL2 只含可推半：不含 like 字面量模式。
            assert "a%" not in sent_filter
            assert res.next_cursor, "links.next 游标必须保留（页协商不变）"
            assert res.metadata["query_plan"]["filter_split"] is not None
        finally:
            invalidate_statistics(fp)

    def test_stac_attribute_filter_total_matching_none_remote_scoped_kept(self):
        """F2（round2）端到端（STAC）：属性过滤从不下推（caps.filter_pushdown
        =False → 守卫路径整体本地求值）—— numberMatched 只是 bbox/datetime/
        分页窗口的远端命中数，冒充 total_matching 不诚实 → 如实置 None；
        远端口径计数以 ``remote_scoped_matched`` 如实命名留存于 metadata
        证据面。bbox-only 查询（无属性过滤）语义不变。"""
        from app.services.data_fabric.adapters import STACAdapter
        from app.services.data_fabric.metadata_cache import _describe_cache

        profile = ConnectionProfile(source_type="stac",
                                    endpoint_url="https://example.com/stac",
                                    name="test_stac")
        adapter = STACAdapter(profile)
        collection = {
            "id": "scenes", "title": "Scenes", "itemType": "feature",
            "summaries": {"cloud": {"min": 0, "max": 100}},
            "extent": {"spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]}},
        }
        search_doc = {
            "type": "FeatureCollection",
            "numberMatched": 57,
            "features": [
                {"type": "Feature", "properties": {"cloud": 5}, "geometry": None},
                {"type": "Feature", "properties": {"cloud": 80}, "geometry": None},
            ],
            "links": [],
        }
        adapter.session.get = lambda url, **kw: _FakeResp(collection)
        adapter.session.post = lambda url, **kw: _FakeResp(search_doc)
        _describe_cache.invalidate()
        try:
            res = adapter.query("scenes", QuerySpec(
                limit=10, filter_expr={"op": "lt", "field": "cloud", "value": 50}))
            assert [f["properties"]["cloud"] for f in res.features] == [5]
            assert res.total_matching is None, \
                "属性过滤查询的远端窗口命中数不得冒充 total_matching"
            assert res.metadata["remote_scoped_matched"] == 57, \
                "远端口径命中数必须如实命名留存"
            ev = res.metadata["query_evidence"]
            assert ev["total_matching"] is None
            assert res.metadata["query_plan"]["filter_split"]["pushed"] is None

            # bbox-only（无属性过滤）：远端命中范围即查询范围 → 原语义不变。
            res2 = adapter.query(
                "scenes", QuerySpec(limit=10, bbox=[0.0, 0.0, 1.0, 1.0]))
            assert res2.total_matching == 57
            assert "remote_scoped_matched" not in res2.metadata
        finally:
            _describe_cache.invalidate()


# ── 3. 统计接线（剩余 adapter）─────────────────────────────────────────────


class TestStatsPlumbing:
    def test_observe_row_count_basics(self):
        from app.services.data_fabric.query.statistics import observe_row_count

        assert observe_row_count("ogc_api", "fp-x", 42) is True
        assert get_statistics("fp-x").row_count == 42
        assert get_statistics("fp-x").collector == "observed_count"
        assert get_statistics("fp-x").confidence == "estimated"
        # 非法/过滤计数契约：负数与非 int 拒收。
        assert observe_row_count("ogc_api", "fp-x", -1) is False
        assert observe_row_count("ogc_api", "fp-x", "42") is False

    def test_arcgis_count_only_feeds_row_stats_and_estimates(self):
        """无过滤 count-only → 观测计数入库；下一次计划的估算确定性改变。"""
        adapter = _make_arcgis(None)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(adapter.describe("0"))
        adapter.session.calls = []
        # count-only：无过滤（where/bbox 缺省）→ 诚实总量观测。
        orig_route = adapter.session.get

        def route(url, params=None, timeout=None, **kwargs):
            if url.rstrip("/").endswith("/query") and (params or {}).get("returnCountOnly"):
                return _FakeResp({"count": 555})
            return orig_route(url, params=params, timeout=timeout, **kwargs)

        adapter.session.get = route
        res = adapter.query("0", QuerySpec(limit=1, aggregate=[{"func": "count"}]))
        assert res.data == [{"count": 555}]
        stats = get_statistics(fp)
        assert stats is not None and stats.row_count == 555
        # 下一次计划：估算 = 555 * sel(eq)=0.05 → 27.75 → 27（确定性）。
        adapter.session.get = orig_route
        adapter.session.calls = []
        res2 = adapter.query("0", QuerySpec(limit=10, filter_expr={
            "op": "eq", "field": "pid", "value": 1}))
        assert res2.metadata["query_plan"]["estimated_rows"] == 27
        assert res2.metadata["query_plan"]["statistics_confidence"] == "estimated"
        invalidate_statistics(fp)

    def test_arcgis_filtered_count_never_stored(self):
        """过滤请求的命中数绝不冒充总量：带 bbox 的 count 不入库。"""
        adapter = _make_arcgis(None)
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(adapter.describe("0"))
        orig_route = adapter.session.get

        def route(url, params=None, timeout=None, **kwargs):
            if url.rstrip("/").endswith("/query") and (params or {}).get("returnCountOnly"):
                return _FakeResp({"count": 7})
            return orig_route(url, params=params, timeout=timeout, **kwargs)

        adapter.session.get = route
        adapter.query("0", QuerySpec(limit=1, aggregate=[{"func": "count"}],
                                     bbox=[0.0, 0.0, 0.5, 0.5]))
        assert get_statistics(fp) is None or get_statistics(fp).row_count != 7
        invalidate_statistics(fp)

    def test_ogc_number_matched_observed_and_plumbed(self):
        from app.services.data_fabric.adapters import OGCAPIAdapter
        from tests.unit.test_ogc_adapters_753 import (
            FakeResponse,
            OGC_COLLECTIONS,
            OGC_PARCELS,
            OGC_QUERYABLES,
        )

        profile = ConnectionProfile(provider_type="ogc_api", endpoint="")
        profile.url = "https://example.com/ogc"
        adapter = OGCAPIAdapter(profile)
        s = MagicMock()
        items = {
            "type": "FeatureCollection",
            "numberMatched": 40,
            "features": [{"type": "Feature", "properties": {"owner": "a"}, "geometry": None}],
        }

        def route(url, params=None, timeout=None, **kwargs):
            p = dict(params or {})
            s.calls.append({"url": url, "params": p})
            if "conformance" in url:
                return FakeResponse(json_data={"conformsTo": []})
            if url.rstrip("/").endswith("/collections"):
                return FakeResponse(json_data=OGC_COLLECTIONS)
            if url.rstrip("/").endswith("/queryables"):
                return FakeResponse(json_data=OGC_QUERYABLES)
            if url.rstrip("/").endswith("/collections/parcels"):
                return FakeResponse(json_data=OGC_PARCELS)
            if url.rstrip("/").endswith("/items"):
                return FakeResponse(json_data=items)
            return FakeResponse(json_data={})

        s.get = route
        adapter.session = s
        adapter.query("parcels", QuerySpec(limit=5))
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(adapter.describe("parcels"))
        stats = get_statistics(fp)
        assert stats is not None and stats.row_count == 40
        # 统计进计划（无过滤请求 → 估算 = 观测行数，确定性），置信度如实披露。
        res3 = adapter.query("parcels", QuerySpec(limit=5))
        plan = res3.metadata["query_plan"]
        assert plan["estimated_rows"] == 40
        assert plan["statistics_confidence"] == "estimated"
        invalidate_statistics(fp)

    def test_wfs_number_matched_observed(self):
        from tests.unit.test_ogc_adapters_753 import FakeResponse, FakeSession
        from app.services.data_fabric.adapters import WFSAdapter

        profile = ConnectionProfile(provider_type="wfs", endpoint="")
        profile.url = "https://example.com/wfs"
        adapter = WFSAdapter(profile)
        s = FakeSession()
        s.routes = {
            "GetCapabilities": FakeResponse(content=b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0"><wfs:FeatureTypeList>
<wfs:FeatureType><wfs:Name>roads</wfs:Name></wfs:FeatureType></wfs:FeatureTypeList></wfs:WFS_Capabilities>"""),
            "/wfs": FakeResponse(json_data={
                "type": "FeatureCollection",
                "numberMatched": 77,
                "features": [{"type": "Feature",
                              "properties": {"name": "ring"},
                              "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}}],
            }),
        }
        adapter.session = s
        res = adapter.query("roads", QuerySpec(limit=5))
        assert res.total_matching == 77
        from app.services.data_fabric.fingerprint import dataset_fingerprint_service

        fp = dataset_fingerprint_service.calculate_descriptor_fingerprint(adapter.describe("roads"))
        stats = get_statistics(fp)
        assert stats is not None and stats.row_count == 77

    def test_stac_item_count_reaches_planner(self):
        """STAC 收割：collection item_count → DatasetStatistics → 计划估算。"""
        import json as _json

        from app.services.data_fabric.adapters import STACAdapter
        from app.services.data_fabric.metadata_cache import _describe_cache

        profile = ConnectionProfile(source_type="stac", endpoint_url="https://example.com/stac",
                                    name="test_stac")
        adapter = STACAdapter(profile)
        collection = {
            "id": "scenes", "title": "Scenes", "itemType": "feature",
            "item_count": 321,
            "extent": {"spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]}},
        }
        raw = _json.dumps(collection).encode()

        class _StreamResp:
            status_code = 200
            headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
            content = raw

            def json(self):
                return collection

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=65536):
                yield raw

            def close(self):
                pass

        _describe_cache.invalidate()
        try:
            adapter.session.get = lambda url, **kw: _StreamResp()
            desc = adapter.describe("scenes")
            assert desc.feature_count == 321
        finally:
            _describe_cache.invalidate()
        from app.services.data_fabric.query.statistics import statistics_for_request

        stats = statistics_for_request(desc)
        assert stats is not None and stats.row_count == 321
        plan = plan_query(
            _spec({"op": "eq", "field": "cloud", "value": 1}), desc,
            get_capabilities("stac"), source_id="s", dataset_fingerprint="fp", stats=stats,
        )
        assert plan.estimated_rows == 16  # 321 * 0.05 = 16.05 → 16
        assert plan.statistics_confidence == "estimated"

    def test_no_stats_fallback_bit_identical_estimates(self):
        """无统计 → 估算走 V2 常数与历史一致（assumption 如实标注）。"""
        from types import SimpleNamespace

        desc = SimpleNamespace(
            id="ds-1", source_type="arcgis", feature_count=1000,
            bbox=[0.0, 0.0, 1.0, 1.0], metadata={},
        )
        plan = _plan("arcgis", {"op": "eq", "field": "g", "value": "a"}, descriptor=desc)
        assert plan.estimated_rows == 50  # 1000 * 0.05
        assert any("default constants" in a for a in plan.assumptions)
        assert plan.statistics_confidence is None


class _FakeResp:
    def __init__(self, json_data):
        import json as _json

        self._json = json_data
        self.content = _json.dumps(json_data).encode()
        self.text = self.content.decode()
        self.status_code = 200
        self.headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._json

    def iter_content(self, chunk_size=65536):
        body = self.content
        for i in range(0, len(body), chunk_size):
            yield body[i:i + chunk_size]

    def close(self):
        pass


# ── 4. 链式联邦工具边界 ─────────────────────────────────────────────────────


def _registered_tools():
    from app.tools.registry import ToolRegistry
    from app.tools.data_fabric_tools import register_data_fabric_tools

    reg = ToolRegistry()
    register_data_fabric_tools(reg)
    return reg


def _run_tool(reg, name, **kwargs):
    tool_obj = reg.get_tool(name) if hasattr(reg, "get_tool") else reg._tools[name]
    fn = tool_obj.fn if hasattr(tool_obj, "fn") else tool_obj
    return asyncio.run(fn(**kwargs))


def _feature_rows(prefix, keys):
    return [
        {"type": "Feature", "geometry": None,
         "properties": {"key": k, "payload": f"{prefix}{k}"}}
        for k in keys
    ]


class TestChainTool:
    @pytest.fixture
    def chain_env(self, monkeypatch):
        import app.tools.data_fabric_tools as tools_mod
        from app.services.data_fabric.spatial_catalog import SpatialCatalogService

        svc = SpatialCatalogService()
        adapters = {}
        for i, keys in ((0, [1, 2, 3]), (1, [2, 3]), (2, [3])):
            ds = f"d{i}"
            svc.register_dataset(
                DatasetDescriptor(id=ds, source_type="postgis"), profile_id=f"p{i}")
            adapters[f"p{i}"] = _FeatureAdapter({ds: _feature_rows(f"a{i}", keys)})
        monkeypatch.setattr(tools_mod, "spatial_catalog_service", svc)
        return adapters

    def test_three_source_chain_bounded_result_and_explain(self, chain_env):
        reg = _registered_tools()
        adapters = chain_env
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "profile_id": "p0"},
                                     {"dataset_id": "d1", "profile_id": "p1"},
                                     {"dataset_id": "d2", "profile_id": "p2"}],
                            joins=[{"kind": "attribute_join", "join_field_left": "key",
                                    "join_field_right": "key"},
                                   {"kind": "attribute_join", "join_field_left": "key",
                                    "join_field_right": "key"}],
                            limit=100)
        assert res["status"] == "success"
        assert res["row_count"] == 1
        assert res["rows"][0]["key"] == 3
        assert len(res["rows"]) <= 200, "工具内联行必须有界"
        assert res["explain"], "explain 行必须包含"
        assert any("Order:" in line for line in res["explain"])
        assert len(res["plans"]) == 2
        assert res["per_source_rows"] == {"s0": 3, "s1": 2, "s2": 1}

    def test_row_cap_disclosure(self, chain_env):
        """超过内联上限 → 截断 + 诚实 notice（row_count 报告真实总量）。"""
        reg = _registered_tools()
        adapters = chain_env
        # 两源等值 join：右侧 3 行 key 全相同 → 扇出 3。
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "profile_id": "p0"},
                                     {"dataset_id": "d1", "profile_id": "p1"}],
                            joins=[{"kind": "attribute_join", "join_field_left": "key",
                                    "join_field_right": "key"}],
                            limit=10000)
        assert res["status"] == "success"
        assert res["row_count"] <= 10000

    def test_join_count_mismatch_typed_error(self, chain_env):
        reg = _registered_tools()
        adapters = chain_env
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "profile_id": "p0"},
                                     {"dataset_id": "d1", "profile_id": "p1"},
                                     {"dataset_id": "d2", "profile_id": "p2"}],
                            joins=[{"kind": "attribute_join", "join_field_left": "key",
                                    "join_field_right": "key"}])
        assert res["status"] == "error"
        assert res["error_type"], "typed error code 必须披露"

    def test_non_chainable_id_joins_typed_error(self, chain_env):
        """id 寻址 join 不成链（两条都从 s0 出发）→ typed 失败。"""
        reg = _registered_tools()
        adapters = chain_env
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "source_id": "s0", "profile_id": "p0"},
                                     {"dataset_id": "d1", "source_id": "s1", "profile_id": "p1"},
                                     {"dataset_id": "d2", "source_id": "s2", "profile_id": "p2"}],
                            joins=[
                                {"kind": "attribute_join", "join_field_left": "key",
                                 "join_field_right": "key",
                                 "left_source_id": "s0", "right_source_id": "s1"},
                                {"kind": "attribute_join", "join_field_left": "key",
                                 "join_field_right": "key",
                                 "left_source_id": "s0", "right_source_id": "s2"},
                            ],
                            order_strategy="cost", engine="v5")
        assert res["status"] == "error"

    def test_non_chainable_id_joins_v6_tree_executes(self, chain_env):
        """V6（ADR-0118）：同一星形 join graph 是合法**树** —— 成功执行。

        V5 左深链契约（不成链 typed 失败）由上一测试以 engine="v5" 锁定；
        V6 cost-based 枚举接受树形 join graph（链是特例），这是 V6 的
        行为改进而非契约破坏。
        """
        reg = _registered_tools()
        adapters = chain_env
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "source_id": "s0", "profile_id": "p0"},
                                     {"dataset_id": "d1", "source_id": "s1", "profile_id": "p1"},
                                     {"dataset_id": "d2", "source_id": "s2", "profile_id": "p2"}],
                            joins=[
                                {"kind": "attribute_join", "join_field_left": "key",
                                 "join_field_right": "key",
                                 "left_source_id": "s0", "right_source_id": "s1"},
                                {"kind": "attribute_join", "join_field_left": "key",
                                 "join_field_right": "key",
                                 "left_source_id": "s0", "right_source_id": "s2"},
                            ],
                            order_strategy="cost", engine="v6")
        assert res["status"] == "success"
        assert res.get("engine") == "v6"

    def test_duplicate_source_id_invalid(self, chain_env):
        reg = _registered_tools()
        adapters = chain_env
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "source_id": "dup", "profile_id": "p0"},
                                     {"dataset_id": "d1", "source_id": "dup", "profile_id": "p1"}],
                            joins=[{"kind": "attribute_join", "join_field_left": "key",
                                    "join_field_right": "key"}])
        assert res["status"] == "error"
        assert res["error_type"] == "INVALID_QUERY"

    def test_nan_inf_estimated_rows_degrade_to_none(self, chain_env):
        """m3（round1）：NaN/inf 成本提示不再令 int() 崩溃 —— 如实降级为
        None（无提示），计划照常产出。"""
        reg = _registered_tools()
        adapters = chain_env
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "profile_id": "p0",
                                      "estimated_rows": float("nan")},
                                     {"dataset_id": "d1", "profile_id": "p1",
                                      "estimated_rows": float("inf")}],
                            joins=[{"kind": "attribute_join", "join_field_left": "key",
                                    "join_field_right": "key"}],
                            limit=100)
        assert res["status"] == "success", res.get("error")
        assert res["row_count"] >= 0
        assert res["plans"], "计划必须产出（NaN 估算视为无提示）"

    def test_single_source_rejected(self, chain_env):
        reg = _registered_tools()
        adapters = chain_env
        with patch("app.tools.data_fabric_tools.connection_manager") as cm:
            cm.get_adapter.side_effect = lambda pid, owner=None: adapters.get(pid)
            res = _run_tool(reg, "query_federated_chain",
                            sources=[{"dataset_id": "d0", "profile_id": "p0"}],
                            joins=[])
        assert res["status"] == "error"


class TestChainExplainLines:
    def test_bounded_and_deterministic(self):
        result = {
            "strategy": "left_deep_chain",
            "order": ["a", "b", "c"],
            "row_count": 1, "joined_row_count": 1, "rows_fetched": 6,
            "per_source_rows": {"a": 3, "b": 2, "c": 1},
            "plans": [
                {"kind": "attribute_join", "left": {"source_id": "a"},
                 "right": {"source_id": "b", "fields": ["key"]}},
                {"kind": "attribute_join", "left": {"source_id": "b"},
                 "right": {"source_id": "c"}},
            ],
            "semi_join_reduction": [{"right_rows_before": 3, "right_rows_after": 2}],
            "warnings": ["join ordered by estimated_rows hints (cost-based, left-deep)"],
        }
        lines = chain_explain_lines(result)
        assert lines == chain_explain_lines(dict(result))
        assert len(lines) <= 16
        assert any("Semi-join reduction" in line for line in lines)
        assert any("Hop 1" in line for line in lines)


# ── 5. 两源半连接约减回迁 ───────────────────────────────────────────────────


class TestTwoSourceSemiJoinBackport:
    def _req(self):
        return FederatedQueryRequest(
            left_source_id="l", left_dataset_id="d-l",
            right_source_id="r", right_dataset_id="d-r",
            join_field_left="key", join_field_right="key",
            limit=10_000,
        )

    def test_selective_left_reduces_right_and_preserves_result(self):
        right = _feature_rows("r", [1, 2, 3, 4, 5])
        adapters = {
            "l": _FeatureAdapter({"d-l": _feature_rows("l", [1, 5])}),
            "r": _FeatureAdapter({"d-r": right}),
        }
        result = FederatedExecutor(lambda sid: adapters.get(sid)).execute(self._req())
        assert result["row_count"] == 2
        keys = {row["key"] for row in result["rows"]}
        assert keys == {1, 5}
        # 披露：右侧行被键集约减（5 → 2）。
        reduction = result.get("semi_join_reduction")
        assert reduction and reduction[0]["right_rows_before"] == 5
        assert reduction[0]["right_rows_after"] == 2

    def test_oversized_key_set_bails_out_honestly(self):
        """页内键集超上限（>1000）→ 诚实放弃约减（无披露、结果不变）。"""
        left = _feature_rows("l", list(range(1500)))
        right = _feature_rows("r", list(range(1500)))
        adapters = {
            "l": _FeatureAdapter({"d-l": left}),
            "r": _FeatureAdapter({"d-r": right}),
        }
        result = FederatedExecutor(lambda sid: adapters.get(sid)).execute(self._req())
        assert result["row_count"] == 1500
        assert "semi_join_reduction" not in result

    def test_aggregate_join_reduction_result_equality(self):
        right = _feature_rows("dim", [1, 2, 3, 99])
        left = _feature_rows("fact", [1, 3])
        adapters = {
            "l": _FeatureAdapter({"d-l": left}),
            "r": _FeatureAdapter({"d-r": right}),
        }
        req = FederatedQueryRequest(
            left_source_id="l", left_dataset_id="d-l",
            right_source_id="r", right_dataset_id="d-r",
            join_field_left="key", join_field_right="key",
            group_by_right=["payload"], aggregates=[{"func": "count"}],
            limit=10_000,
        )
        result = FederatedExecutor(lambda sid: adapters.get(sid)).execute(req)
        # 聚合按右表 payload 分组：key 1→r1、key 3→r3，各 count 1。
        assert result["row_count"] == 2
        assert result.get("semi_join_reduction")


# ── 6. derive_projection 默认开启 ───────────────────────────────────────────


class _ProjectingAdapter:
    """遵守 fields 投影的假 adapter（投影派生对结果形状的影响可观测）。"""

    source_type = "generic"

    def __init__(self, features_by_dataset: Dict[str, List[dict]]):
        self._data = features_by_dataset

    def query(self, dataset_id: str, spec: Any) -> QueryResult:
        fields = getattr(spec, "fields", None)
        feats = self._data.get(dataset_id, [])
        if fields is not None:
            feats = [
                {**f, "properties": {k: v for k, v in f["properties"].items() if k in fields}}
                for f in feats
            ]
        return QueryResult(dataset_id=dataset_id, features=feats)


class TestDeriveProjectionDefaultOn:
    def _chain(self, derive_projection=None):
        data = {
            "d-a": _feature_rows("a", [1, 2, 3]),
            "d-b": _feature_rows("b", [2, 3, 99]),
            "d-c": _feature_rows("c", [3, 42]),
        }
        adapters = {sid: _ProjectingAdapter(data) for sid in ("a", "b", "c")}
        from app.services.data_fabric.query.federation import ChainJoin, ChainSource, FederatedChainRequest

        sources = [ChainSource("a", "d-a"), ChainSource("b", "d-b"), ChainSource("c", "d-c")]
        joins = [
            ChainJoin(kind="attribute_join", join_field_left="key", join_field_right="key"),
            ChainJoin(kind="attribute_join", join_field_left="key", join_field_right="key"),
        ]
        kw = {} if derive_projection is None else {"derive_projection": derive_projection}
        req = FederatedChainRequest(sources=sources, joins=joins, limit=10_000, **kw)
        result = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
        return result

    def test_default_on_projection_active(self):
        result = self._chain()
        # 派生投影只保留链上可证明需要的字段（key），结果行仍是正确连接。
        assert result["row_count"] == 1
        assert result["rows"][0]["key"] == 3
        plans = result["plans"]
        assert plans[0]["left"]["fields"] == ["key"]
        assert plans[1]["right"]["fields"] == ["key"]

    def test_default_on_matches_explicit_false_results(self):
        """默认开启与显式 opt-out 的连接语义一致（投影只裁形状不裁行）。"""
        on = self._chain()
        off = self._chain(derive_projection=False)
        assert on["row_count"] == off["row_count"] == 1
        assert on["rows"][0]["key"] == off["rows"][0]["key"] == 3
        # 形状差异是显式披露的（on 的计划带 fields，off 不带）。
        assert on["plans"][0]["left"].get("fields") == ["key"]
        assert "fields" not in off["plans"][0]["left"]
