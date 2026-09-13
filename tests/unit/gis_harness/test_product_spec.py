"""MapProductSpec v1（ADR-0183 M1/M2）单元测试。

不变式：versioned / bounded / serializable / fail-closed 校验 /
digest 确定性 / 视图关系图 dangling+cycle 拒绝。
"""
import pytest

from app.services.gis_harness.product_spec import (
    MAX_VIEWS,
    PRODUCT_SPEC_VERSION,
    RELATION_KINDS,
    VIEW_KINDS,
    MapProductSpec,
    ProductDeliveryIntent,
    ProductRelation,
    ProductView,
    ProductViewBinding,
    apply_product_edit,
    spec_digest,
    spec_from_storage,
    storage_payload,
    validate_product_spec,
    view_graph_edges,
)


def _view(vid: str = "v-map", kind: str = "map", **kw) -> ProductView:
    return ProductView(view_id=vid, kind=kind, **kw)


def _spec(**kw) -> MapProductSpec:
    base = dict(
        spec_id="spec-test",
        query="成都小学分布情况",
        product_type="distribution_overview",
        task="poi_distribution",
        scope="成都",
        subject="小学",
    )
    base.update(kw)
    return MapProductSpec(**base)


# ── M1：schema 往返 / 有界 / digest ──────────────────────────────────────


def test_spec_roundtrip_and_version():
    s = _spec(views=[_view()], recipe_id="poi_distribution_overview")
    payload = storage_payload(s)
    assert payload["spec_version"] == PRODUCT_SPEC_VERSION
    restored = spec_from_storage(payload)
    assert restored is not None
    assert restored.model_dump() == s.model_dump()


def test_spec_digest_stable_and_revision_excluded():
    s = _spec(views=[_view()])
    d1 = spec_digest(s)
    assert d1 == spec_digest(_spec(views=[_view()]))
    s2 = s.model_copy(deep=True)
    s2.revision = 99
    assert spec_digest(s2) == d1, "revision 是编辑计数，不参与内容身份"
    s3 = s.model_copy(deep=True)
    s3.views[0].enabled = False
    assert spec_digest(s3) != d1


def test_spec_from_storage_tolerates_garbage():
    assert spec_from_storage(None) is None
    assert spec_from_storage("x") is None
    assert spec_from_storage({"spec": {"views": "not-a-list"}}) is None
    bad_version = _spec()
    raw = bad_version.model_dump()
    raw["spec_version"] = "0.9"
    assert spec_from_storage({"spec": raw}) is None


def test_view_kinds_wordlist():
    assert set(VIEW_KINDS) == {
        "map", "inset", "overview", "chart", "stats_panel",
        "narrative", "comparison", "time_panel",
    }
    assert set(RELATION_KINDS) == {
        "same_dataset", "derived_statistic", "comparison", "overview_detail",
        "chart_linked_to_map", "shared_legend", "shared_extent",
        "source_attribution",
    }


# ── M2：校验（dangling / cycle / 重复 / 未知词）──────────────────────────


def test_validate_ok_for_flagship_shape():
    s = _spec(
        views=[
            _view("v-map", "map", required=True),
            _view("v-chart", "chart", chart_kind="bar", required=True),
            _view("v-stats", "stats_panel", required=False),
        ],
        relations=[
            ProductRelation(src="v-map", dst="v-chart", kind="chart_linked_to_map"),
            ProductRelation(src="v-map", dst="v-stats", kind="derived_statistic"),
        ],
    )
    assert validate_product_spec(s) == []


def test_validate_rejects_dangling_and_unknown_kind():
    s = _spec(
        views=[_view("v-map", "map")],
        relations=[ProductRelation(src="v-map", dst="ghost", kind="same_dataset")],
    )
    errors = validate_product_spec(s)
    assert any("dangling" in e for e in errors)

    # 未知 kind：pydantic Literal 是构造期第一道门；校验器兜底 model_construct
    # 旁路载荷（纵深防御）。
    raw_view = ProductView.model_construct(view_id="v-x", kind="hologram")
    s2 = _spec(views=[raw_view])
    assert any("unknown kind" in e for e in validate_product_spec(s2))


def test_validate_rejects_duplicate_ids_and_self_loop():
    s = _spec(views=[_view("v-map", "map"), _view("v-map", "chart")])
    assert any("duplicate view_id" in e for e in validate_product_spec(s))
    s2 = _spec(
        views=[_view("v-map", "map")],
        relations=[ProductRelation(src="v-map", dst="v-map", kind="same_dataset")],
    )
    assert any("self loop" in e for e in validate_product_spec(s2))


def test_validate_rejects_directional_cycle_but_allows_symmetric_triangles():
    s = _spec(
        views=[_view(f"v{i}") for i in range(3)],
        relations=[
            ProductRelation(src="v0", dst="v1", kind="derived_statistic"),
            ProductRelation(src="v1", dst="v2", kind="derived_statistic"),
            ProductRelation(src="v2", dst="v0", kind="derived_statistic"),
        ],
    )
    assert any("cycle" in e for e in validate_product_spec(s))
    # 对称关系三角不构成有向环（same_dataset 是无向语义）
    s2 = _spec(
        views=[_view(f"v{i}") for i in range(3)],
        relations=[
            ProductRelation(src="v0", dst="v1", kind="same_dataset"),
            ProductRelation(src="v1", dst="v2", kind="same_dataset"),
            ProductRelation(src="v2", dst="v0", kind="same_dataset"),
        ],
    )
    assert validate_product_spec(s2) == []


def test_validate_bounds_and_delivery_words():
    bypass = [ProductView.model_construct(view_id=f"v{i}", kind="map")
              for i in range(MAX_VIEWS + 1)]
    s = MapProductSpec.model_construct(
        spec_id="s", views=bypass, relations=[], overrides=[],
        claims=[], delivery=ProductDeliveryIntent())
    assert any("MAX_VIEWS" in e for e in validate_product_spec(s))
    s2 = _spec()
    s2.delivery = ProductDeliveryIntent(targets=["hologram"])
    assert any("unknown target" in e for e in validate_product_spec(s2))


def test_view_graph_edges_normalization():
    s = _spec(
        views=[_view("b"), _view("a"), _view("c")],
        relations=[
            ProductRelation(src="b", dst="a", kind="shared_legend"),
            ProductRelation(src="a", dst="c", kind="overview_detail"),
        ],
    )
    edges = view_graph_edges(s)
    assert ("a", "b", "shared_legend") in edges  # 对称边归一化
    assert ("a", "c", "overview_detail") in edges  # 方向边保序


# ── M6 入口：apply_product_edit（先验证后提交 / 未受影响视图保持）────────


def test_edit_remove_view_cascades_relations_only():
    s = _spec(
        views=[_view("v-map", required=True), _view("v-chart", required=True),
               _view("v-stats")],
        relations=[
            ProductRelation(src="v-map", dst="v-chart", kind="chart_linked_to_map"),
            ProductRelation(src="v-map", dst="v-stats", kind="derived_statistic"),
        ],
    )
    before = s.model_dump()
    # 显式编辑撤回必需性：required 视图可删（用户改主意是最高优先信号，
    # override 账留痕 —— M8 不再把已撤回构成判缺失）
    ns, errs, aff = apply_product_edit(s, "remove_view", "v-map", reason="不要地图")
    assert ns is not None and not errs, errs
    assert ns.view("v-map") is None
    assert ns.relations == []  # 级联清除 v-map 的关系边
    assert ns.overrides[-1].op == "remove_view" and ns.overrides[-1].reason == "不要地图"
    # 非必需视图删除：其余视图原样
    ns2, errs2, aff2 = apply_product_edit(s, "remove_view", "v-stats")
    assert ns2 is not None and not errs2
    assert set(aff2) == {"v-stats", "v-map"}
    assert ns2.view("v-chart").model_dump() == s.view("v-chart").model_dump()
    assert ns2.revision == s.revision + 1
    assert s.model_dump() == before, "输入 spec 不被原地修改"


def test_edit_chart_kind_and_filter_and_errors():
    s = _spec(views=[
        _view("v-map", required=True),
        _view("v-chart", "chart", chart_kind="pie", required=True),
    ])
    ns, errs, aff = apply_product_edit(
        s, "replace_component", "v-chart", {"chart_kind": "bar"}, "换成柱状图")
    assert ns is not None and not errs and aff == ["v-chart"]
    assert ns.view("v-chart").chart_kind == "bar"
    assert validate_product_spec(ns) == []
    # 未知 chart kind → 结构校验拦截（chart_kinds 词表）
    ns2, errs2, _ = apply_product_edit(
        s, "replace_component", "v-chart", {"chart_kind": "hologram"})
    assert ns2 is None and any("chart_kind" in e for e in errs2)
    # filter 编辑
    ns3, errs3, aff3 = apply_product_edit(
        s, "set_view_filter", "v-map", {"filter": {"category": "小学"}})
    assert ns3 is not None and aff3 == ["v-map"]
    assert ns3.view("v-map").binding.filter == {"category": "小学"}
    # 目标不存在 → fail-closed
    _, errs4, aff4 = apply_product_edit(s, "set_view_filter", "ghost", {"filter": {}})
    assert errs4 and not aff4


def test_edit_unknown_op_and_payload_validation():
    ns, errs, _ = apply_product_edit(_spec(), "teleport", "x")
    assert ns is None and errs and any("teleport" in e for e in errs)
    # payload 白名单：未声明键被消毒拒绝（fail-closed，不落账）
    ns2, errs2, _ = apply_product_edit(
        _spec(views=[_view("v-map")]), "set_caption", "v-map",
        {"text": "ok", "evil": {"deep": [1] * 500}})
    assert ns2 is None and any("evil" in e for e in errs2)
    # 值消毒：超长字符串截断后合法落账
    ns3, errs3, _ = apply_product_edit(
        _spec(views=[_view("v-map")]), "set_caption", "v-map",
        {"text": "长" * 999})
    assert ns3 is not None and not errs3
    assert len(ns3.view("v-map").title) <= 160
