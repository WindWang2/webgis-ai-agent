"""多源探索契约（v3）单元测试：sources/links/层级模型、v2 兼容读取、
per-source 条件编译、传播 DAG、键族语义与传播键集收集。

oracle：期望值独立手算/枚举，不调用生产 evaluator 生成 expected；
键族语义 oracle 与 G38 keys 文档（类型分族/null 不匹配/变换只作用
字符串）逐条对齐。
"""
from __future__ import annotations

import pytest

from app.services.linked_exploration import contract, relations
from app.services.linked_exploration.errors import LinkedExplorationError


def _anchor(ref="ref:a", rev=3, fp="fp-a"):
    return {"ref_id": ref, "content_revision": rev, "content_fingerprint": fp}


def _v2_plan(**overrides):
    raw = {
        "schema_version": 2,
        "anchor": _anchor(),
        "charts": [{"chart_id": "ch1", "kind": "category", "field": "type"}],
        "conditions": [],
    }
    raw.update(overrides)
    return raw


def _v3_plan(sources, links=(), charts=(), conditions=(), anchor=None):
    return {
        "schema_version": 3,
        "anchor": anchor,
        "sources": sources,
        "links": list(links),
        "charts": list(charts),
        "conditions": list(conditions),
    }


def _src(sid, ref, role="context", rev=3, hierarchy=None):
    out = {"source_id": sid, "anchor": _anchor(ref=ref, rev=rev), "role": role}
    if hierarchy is not None:
        out["hierarchy"] = hierarchy
    return out


def _attr_link(lid, left, right, lf, rf, cardinality="one_to_many",
               propagate="none", transform=None):
    key = {"left_field": lf, "right_field": rf}
    if transform:
        key["transform"] = transform
    return {
        "link_id": lid, "kind": "attribute", "left_source_id": left,
        "right_source_id": right, "keys": [key],
        "cardinality": cardinality, "propagate": propagate,
    }


class TestV2Compat:
    def test_v2_plan_still_validates_and_derives_primary(self):
        """v2 计划零迁移可读；派生 primary 绑定访问面统一。"""
        plan = contract.ExplorationPlan.model_validate(_v2_plan())
        bindings = contract.plan_source_bindings(plan)
        assert len(bindings) == 1
        assert bindings[0].source_id == "primary"
        assert bindings[0].role == "primary"
        assert bindings[0].anchor.ref_id == "ref:a"
        assert contract.plan_primary_binding(plan).source_id == "primary"

    def test_v2_digest_matches_legacy_formula(self):
        """v2 digest 与基线（master 59eb3f527, 常量=2 时代）公式手构对拍。

        review A-P3-4：不再用生产函数自证 —— 手构 v2 时代 payload（常量
        2 + anchor/charts/conditions）独立重算 sha256 前 16 位。"""
        import hashlib
        import json as _json
        plan = contract.ExplorationPlan.model_validate(_v2_plan())
        conds = contract.parse_conditions(plan.conditions)
        payload = {
            "schema_version": 2,  # v2 时代常量
            "anchor": plan.anchor.model_dump(),
            "charts": [c.model_dump() for c in plan.charts],
            "conditions": [c.model_dump() for c in conds],
        }
        raw = _json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), default=str)
        expected = "g46-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        assert contract.plan_digest_of_plan(plan) == expected

    def test_v2_rejects_source_id_on_chart(self):
        raw = _v2_plan()
        raw["charts"][0]["source_id"] = "primary"
        with pytest.raises(ValueError, match="source_id"):
            contract.ExplorationPlan.model_validate(raw)

    def test_v2_rejects_source_id_on_condition(self):
        raw = _v2_plan()
        raw["conditions"] = [{
            "kind": "category", "condition_id": "c1", "chart_id": "ch1",
            "field": "type", "values": ["a"], "source_id": "primary",
        }]
        with pytest.raises(ValueError, match="source_id"):
            contract.ExplorationPlan.model_validate(raw)

    def test_v3_requires_sources(self):
        raw = _v2_plan()
        raw["schema_version"] = 3
        with pytest.raises(ValueError, match="sources"):
            contract.ExplorationPlan.model_validate(raw)

    def test_unsupported_schema_version_rejected(self):
        raw = _v2_plan()
        raw["schema_version"] = 4
        with pytest.raises(ValueError, match="schema_version"):
            contract.ExplorationPlan.model_validate(raw)

    def test_legacy_constant_bumped(self):
        """v3 是当前合同版本；v2 仍在可读词表内。"""
        assert contract.PLAN_SCHEMA_VERSION == 3
        assert 2 in contract._PLAN_SCHEMA_VERSIONS


class TestV3Sources:
    def test_multi_source_plan_with_chart_and_condition_bindings(self):
        raw = _v3_plan(
            sources=[_src("pop", "ref:pop", role="primary"),
                     _src("school", "ref:school")],
            charts=[
                {"chart_id": "ch_pop", "kind": "category",
                 "field": "district", "source_id": "pop"},
                {"chart_id": "ch_school", "kind": "category",
                 "field": "type", "source_id": "school"},
            ],
            conditions=[{
                "kind": "category", "condition_id": "c1",
                "chart_id": "ch_school", "field": "type",
                "values": ["public"],
            }],
        )
        plan = contract.ExplorationPlan.model_validate(raw)
        assert {b.source_id for b in plan.sources} == {"pop", "school"}
        # 条件无显式 source_id → 继承所属图表的源
        conds = contract.parse_conditions(plan.conditions)
        chart_src = {
            c.chart_id: contract.resolve_chart_source_id(plan, c)
            for c in plan.charts
        }
        assert contract.resolve_condition_source_id(
            plan, conds[0], chart_src) == "school"

    def test_condition_source_must_match_chart_source(self):
        """条件跨源 → facet 排除口径歧义，typed 拒绝。"""
        raw = _v3_plan(
            sources=[_src("pop", "ref:pop", role="primary"),
                     _src("school", "ref:school")],
            charts=[{"chart_id": "ch_pop", "kind": "category",
                     "field": "district", "source_id": "pop"}],
            conditions=[{
                "kind": "category", "condition_id": "c1",
                "chart_id": "ch_pop", "field": "type",
                "values": ["public"], "source_id": "school",
            }],
        )
        with pytest.raises(ValueError, match="不一致"):
            contract.ExplorationPlan.model_validate(raw)

    def test_unknown_chart_source_rejected(self):
        raw = _v3_plan(
            sources=[_src("pop", "ref:pop", role="primary")],
            charts=[{"chart_id": "ch1", "kind": "category",
                     "field": "f", "source_id": "ghost"}],
        )
        with pytest.raises(ValueError, match="不在 sources"):
            contract.ExplorationPlan.model_validate(raw)

    def test_duplicate_source_id_rejected(self):
        raw = _v3_plan(sources=[_src("a", "ref:a", role="primary"),
                                _src("a", "ref:b")])
        with pytest.raises(ValueError, match="重复"):
            contract.ExplorationPlan.model_validate(raw)

    def test_two_primaries_rejected(self):
        raw = _v3_plan(sources=[_src("a", "ref:a", role="primary"),
                                _src("b", "ref:b", role="primary")])
        with pytest.raises(ValueError, match="primary"):
            contract.ExplorationPlan.model_validate(raw)

    def test_no_primary_rejected(self):
        raw = _v3_plan(sources=[_src("a", "ref:a"), _src("b", "ref:b")])
        with pytest.raises(ValueError, match="primary"):
            contract.ExplorationPlan.model_validate(raw)

    def test_v3_anchor_must_match_primary(self):
        raw = _v3_plan(
            sources=[_src("pop", "ref:pop", role="primary", rev=5)],
            anchor=_anchor(ref="ref:other", rev=1),
        )
        with pytest.raises(ValueError, match="anchor"):
            contract.ExplorationPlan.model_validate(raw)

    def test_v3_anchor_omitted_ok(self):
        raw = _v3_plan(sources=[_src("pop", "ref:pop", role="primary")])
        plan = contract.ExplorationPlan.model_validate(raw)
        assert plan.anchor is None
        assert contract.plan_primary_binding(plan).anchor.ref_id == "ref:pop"

    def test_max_sources_cap(self):
        sources = [_src(f"s{i}", f"ref:{i}", role="primary" if i == 0 else "context")
                   for i in range(5)]
        raw = _v3_plan(sources=sources)
        with pytest.raises(ValueError, match="最多 4"):
            contract.ExplorationPlan.model_validate(raw)


class TestLinks:
    def _plan_raw(self, links):
        return _v3_plan(
            sources=[_src("a", "ref:a", role="primary"),
                     _src("b", "ref:b")],
            links=links,
        )

    def test_attribute_link_ok(self):
        plan = contract.ExplorationPlan.model_validate(
            self._plan_raw([_attr_link("l1", "a", "b", "district", "district",
                                       propagate="left_to_right")]))
        assert plan.links[0].propagate == "left_to_right"

    def test_spatial_link_requires_predicate(self):
        raw = self._plan_raw([{
            "link_id": "l1", "kind": "spatial_within",
            "left_source_id": "a", "right_source_id": "b",
            "cardinality": "many_to_one", "propagate": "left_to_right",
        }])
        with pytest.raises(ValueError, match="predicate"):
            contract.ExplorationPlan.model_validate(raw)

    def test_attribute_link_requires_keys(self):
        raw = self._plan_raw([{
            "link_id": "l1", "kind": "attribute",
            "left_source_id": "a", "right_source_id": "b",
            "cardinality": "many_to_one",
        }])
        with pytest.raises(ValueError, match="键对"):
            contract.ExplorationPlan.model_validate(raw)

    def test_many_to_many_rejected(self):
        raw = self._plan_raw([_attr_link("l1", "a", "b", "k", "k",
                                         cardinality="many_to_many")])
        with pytest.raises(ValueError, match="many_to_many"):
            contract.ExplorationPlan.model_validate(raw)

    def test_bidirectional_propagate_rejected(self):
        raw = self._plan_raw([_attr_link("l1", "a", "b", "k", "k",
                                         propagate="bidirectional")])
        with pytest.raises(ValueError, match="propagate"):
            contract.ExplorationPlan.model_validate(raw)

    def test_unknown_link_source_rejected(self):
        raw = self._plan_raw([_attr_link("l1", "a", "ghost", "k", "k")])
        with pytest.raises(ValueError, match="不在 sources"):
            contract.ExplorationPlan.model_validate(raw)

    def test_self_link_rejected(self):
        raw = self._plan_raw([_attr_link("l1", "a", "a", "k", "k")])
        with pytest.raises(ValueError, match="不同源"):
            contract.ExplorationPlan.model_validate(raw)

    def test_propagation_cycle_rejected(self):
        """A→B + B→A：循环传播 typed 拒绝（双向用两条单向表达时也拦）。"""
        raw = self._plan_raw([
            _attr_link("l1", "a", "b", "k", "k", propagate="left_to_right"),
            _attr_link("l2", "a", "b", "k", "k", propagate="right_to_left"),
        ])
        with pytest.raises(ValueError, match="环"):
            contract.ExplorationPlan.model_validate(raw)

    def test_three_node_cycle_rejected(self):
        raw = _v3_plan(
            sources=[_src("a", "ref:a", role="primary"),
                     _src("b", "ref:b"), _src("c", "ref:c")],
            links=[
                _attr_link("l1", "a", "b", "k", "k", propagate="left_to_right"),
                _attr_link("l2", "b", "c", "k", "k", propagate="left_to_right"),
                _attr_link("l3", "c", "a", "k", "k", propagate="left_to_right"),
            ],
        )
        with pytest.raises(ValueError, match="环"):
            contract.ExplorationPlan.model_validate(raw)

    def test_dag_ok(self):
        raw = _v3_plan(
            sources=[_src("a", "ref:a", role="primary"),
                     _src("b", "ref:b"), _src("c", "ref:c")],
            links=[
                _attr_link("l1", "a", "b", "k", "k", propagate="left_to_right"),
                _attr_link("l2", "a", "c", "k", "k", propagate="left_to_right"),
                _attr_link("l3", "b", "c", "k", "k", propagate="left_to_right"),
            ],
        )
        plan = contract.ExplorationPlan.model_validate(raw)
        order = relations.propagation_order(plan)
        assert set(order) == {"a", "b", "c"}
        assert order.index("a") < order.index("b") < order.index("c")

    def test_composite_keys_cap(self):
        keys = [{"left_field": f"k{i}", "right_field": f"k{i}"}
                for i in range(9)]
        raw = self._plan_raw([{
            "link_id": "l1", "kind": "attribute",
            "left_source_id": "a", "right_source_id": "b",
            "keys": keys, "cardinality": "one_to_one",
        }])
        with pytest.raises(ValueError, match="最多 8"):
            contract.ExplorationPlan.model_validate(raw)


class TestHierarchy:
    def test_same_source_hierarchy_ok(self):
        raw = _v3_plan(sources=[
            _src("poi", "ref:poi", role="primary",
                 hierarchy={"parent_field": "district_code",
                            "level_note": "街道点→区县"}),
        ])
        plan = contract.ExplorationPlan.model_validate(raw)
        assert plan.sources[0].hierarchy.parent_field == "district_code"

    def test_cross_source_hierarchy_ok(self):
        raw = _v3_plan(sources=[
            _src("poi", "ref:poi", role="primary",
                 hierarchy={"parent_field": "district",
                            "parent_source_id": "pop"}),
            _src("pop", "ref:pop"),
        ])
        plan = contract.ExplorationPlan.model_validate(raw)
        assert plan.sources[0].hierarchy.parent_source_id == "pop"

    def test_unknown_hierarchy_parent_rejected(self):
        raw = _v3_plan(sources=[
            _src("poi", "ref:poi", role="primary",
                 hierarchy={"parent_field": "district",
                            "parent_source_id": "ghost"}),
        ])
        with pytest.raises(ValueError, match="不在 sources"):
            contract.ExplorationPlan.model_validate(raw)


class TestCompilePlanWheres:
    def test_per_source_grouping_and_inheritance(self):
        raw = _v3_plan(
            sources=[_src("pop", "ref:pop", role="primary"),
                     _src("school", "ref:school")],
            charts=[
                {"chart_id": "ch_pop", "kind": "category",
                 "field": "district", "source_id": "pop"},
                {"chart_id": "ch_school", "kind": "category",
                 "field": "type", "source_id": "school"},
            ],
            conditions=[
                {"kind": "category", "condition_id": "c1",
                 "chart_id": "ch_pop", "field": "district",
                 "values": ["武侯区"]},
                {"kind": "category", "condition_id": "c2",
                 "chart_id": "ch_school", "field": "type",
                 "values": ["public"]},
            ],
        )
        plan = contract.ExplorationPlan.model_validate(raw)
        conds = contract.parse_conditions(plan.conditions)
        wheres = contract.compile_plan_wheres(plan, conds)
        assert set(wheres) == {"pop", "school"}
        # oracle：pop 组 = in ["武侯区"]；school 组 = in ["public"]
        assert wheres["pop"] == {"op": "in", "field": "district",
                                 "values": ["武侯区"]}
        assert wheres["school"] == {"op": "in", "field": "type",
                                    "values": ["public"]}

    def test_facet_exclusion_across_sources(self):
        raw = _v3_plan(
            sources=[_src("pop", "ref:pop", role="primary"),
                     _src("school", "ref:school")],
            charts=[
                {"chart_id": "ch_pop", "kind": "category",
                 "field": "district", "source_id": "pop"},
                {"chart_id": "ch_school", "kind": "category",
                 "field": "type", "source_id": "school"},
            ],
            conditions=[
                {"kind": "category", "condition_id": "c1",
                 "chart_id": "ch_pop", "field": "district",
                 "values": ["武侯区"]},
                {"kind": "category", "condition_id": "c2",
                 "chart_id": "ch_school", "field": "type",
                 "values": ["public"]},
            ],
        )
        plan = contract.ExplorationPlan.model_validate(raw)
        conds = contract.parse_conditions(plan.conditions)
        wheres = contract.compile_plan_wheres(plan, conds,
                                              exclude_chart_id="ch_pop")
        assert wheres["pop"] is None          # 只排除自身条件
        assert wheres["school"] is not None   # 其它源条件保留

    def test_source_with_no_conditions_is_none_not_false(self):
        raw = _v3_plan(sources=[_src("pop", "ref:pop", role="primary"),
                                _src("school", "ref:school")])
        plan = contract.ExplorationPlan.model_validate(raw)
        wheres = contract.compile_plan_wheres(plan, [])
        assert wheres == {"pop": None, "school": None}


class TestMultiSourceDigest:
    def test_plan_conditions_digest_changes_with_links_and_wheres(self):
        sources = [_src("a", "ref:a", role="primary"), _src("b", "ref:b")]
        charts = [{"chart_id": "ch1", "kind": "category", "field": "f"}]
        conditions = [{"kind": "category", "condition_id": "c1",
                       "chart_id": "ch1", "field": "f", "values": ["x"]}]

        def _build(links):
            raw = _v3_plan(sources=list(sources), links=links,
                           charts=list(charts), conditions=list(conditions))
            plan = contract.ExplorationPlan.model_validate(raw)
            return plan, contract.parse_conditions(plan.conditions)

        plan1, conds1 = _build([])
        plan2, conds2 = _build([
            _attr_link("l1", "a", "b", "k", "k", propagate="left_to_right")])
        d1 = contract.plan_conditions_digest(plan1, conds1)
        d2 = contract.plan_conditions_digest(plan2, conds2)
        assert d1 != d2          # 传播声明改变下游口径 → 必须进 digest
        assert d1.startswith("g46m-")
        # 稳定性：同一计划同条件 digest 一致
        assert contract.plan_conditions_digest(plan1, conds1) == d1

    def test_v3_plan_digest_stable_and_v2_distinct(self):
        raw = _v3_plan(
            sources=[_src("pop", "ref:pop", role="primary"),
                     _src("school", "ref:school")],
            links=[_attr_link("l1", "pop", "school", "district", "district")],
        )
        plan = contract.ExplorationPlan.model_validate(raw)
        d1 = contract.plan_digest_of_plan(plan)
        assert d1 == contract.plan_digest_of_plan(plan)
        assert d1.startswith("g46-")
        v2 = contract.ExplorationPlan.model_validate(_v2_plan())
        assert contract.plan_digest_of_plan(v2) != d1


class TestRelationKeys:
    """键族语义 oracle（与 G38 keys 文档逐条对齐）。"""

    _SPEC = dict(left_field="k", right_field="k")

    def _spec(self, transform=None, zfill=0):
        pair = contract.LinkKeyPair.model_validate(
            {**self._SPEC, "transform": transform, "zfill_width": zfill})
        link = contract.LinkSpec.model_validate(
            {**_attr_link("l", "a", "b", "k", "k"),
             "keys": [pair.model_dump()]})
        return relations.key_spec_for(link, pair, "left")

    def test_numeric_family_int_equals_float(self):
        key1, inv1 = relations.normalize_link_key_safe(1, self._spec())
        key2, inv2 = relations.normalize_link_key_safe(1.0, self._spec())
        assert not inv1 and not inv2
        assert key1 == key2          # ("n", 1.0) 数值族
        assert key1[0] == "n"

    def test_string_family_strict(self):
        key_s, _ = relations.normalize_link_key_safe("1", self._spec())
        key_n, _ = relations.normalize_link_key_safe(1, self._spec())
        assert key_s != key_n        # "1" != 1
        assert key_s[0] == "s"

    def test_bool_own_family(self):
        key_b, _ = relations.normalize_link_key_safe(True, self._spec())
        key_n, _ = relations.normalize_link_key_safe(1, self._spec())
        assert key_b != key_n        # True 独立族（isinstance(bool, int) 陷阱）
        assert key_b[0] == "b"

    def test_none_is_missing_not_key(self):
        key, invalid = relations.normalize_link_key_safe(None, self._spec())
        assert key is None and not invalid

    def test_non_comparable_type_flagged_invalid(self):
        key, invalid = relations.normalize_link_key_safe(["a"], self._spec())
        assert invalid and key == relations.KEY_INVALID

    def test_transform_only_affects_strings(self):
        trim = self._spec("trim")
        key, _ = relations.normalize_link_key_safe("  A  ", trim)
        assert key == ("s", "A")
        key_n, _ = relations.normalize_link_key_safe(7, trim)
        assert key_n == ("n", 7.0)   # 数值不受字符串变换影响

    def test_casefold_and_zfill(self):
        cf = self._spec("casefold")
        key, _ = relations.normalize_link_key_safe("Wuhou", cf)
        assert key == ("s", "wuhou")
        zf = self._spec("zfill", zfill=4)
        key, _ = relations.normalize_link_key_safe("12", zf)
        assert key == ("s", "0012")


def _feat(props):
    return {"type": "Feature", "properties": props, "geometry": None}


class TestPropagatedKeySet:
    def test_collect_respects_where_and_counts_honestly(self):
        feats = [
            _feat({"district": "武侯区", "kind": "school"}),
            _feat({"district": "锦江区", "kind": "school"}),
            _feat({"district": None, "kind": "school"}),
            _feat({"kind": "school"}),                    # 缺字段
            _feat({"district": ["x"], "kind": "school"}),  # 类型不可比
            _feat({"district": "青羊区", "kind": "park"}),  # 不匹配 where
        ]
        where = {"op": "eq", "field": "kind", "value": "school"}
        spec = relations.key_spec_for(
            contract.LinkSpec.model_validate(_attr_link(
                "l", "a", "b", "district", "district")),
            contract.LinkKeyPair.model_validate(
                {"left_field": "district", "right_field": "district"}),
            "left")
        keyset = relations.collect_propagated_keys(
            feats, where, field="district", spec=spec)
        # oracle 手算：school 行 5 行（含 None/缺失/非法），匹配键 2 个
        assert keyset.keys == {("s", "武侯区"), ("s", "锦江区")}
        assert keyset.scanned == 6
        assert keyset.matched == 5
        assert keyset.skipped_missing == 2     # None + 缺字段
        assert keyset.skipped_invalid == 1
        assert keyset.invalid_samples == [["x"]]

    def test_cap_rejected_not_truncated(self):
        n = contract.MAX_PROPAGATED_KEYS + 1
        feats = [_feat({"district": f"d{i:04d}"}) for i in range(n)]
        spec = relations.key_spec_for(
            contract.LinkSpec.model_validate(_attr_link(
                "l", "a", "b", "district", "district")),
            contract.LinkKeyPair.model_validate(
                {"left_field": "district", "right_field": "district"}),
            "left")
        keyset = relations.collect_propagated_keys(
            feats, None, field="district", spec=spec)
        with pytest.raises(LinkedExplorationError) as ei:
            relations.require_key_set_within_cap(keyset)
        assert ei.value.code == "PROPAGATION_KEYSET_OVER_LIMIT"
        assert ei.value.detail["key_count"] == n

    def test_key_set_predicate_typed(self):
        spec = relations.key_spec_for(
            contract.LinkSpec.model_validate(_attr_link(
                "l", "a", "b", "district", "district")),
            contract.LinkKeyPair.model_validate(
                {"left_field": "district", "right_field": "district"}),
            "right")
        pred = relations.key_set_predicate("district", {("s", "武侯区")}, spec)
        assert pred(_feat({"district": "武侯区"})) is True
        assert pred(_feat({"district": "锦江区"})) is False
        assert pred(_feat({"district": None})) is False
        assert pred(_feat({"district": ["x"]})) is False  # 脏值不命中
        assert pred(_feat({})) is False

    def test_key_set_to_maplibre_sorted_typed(self):
        expr = relations.key_set_to_maplibre(
            "district", {("s", "武侯区"), ("n", 2.0), ("b", True)})
        # oracle：族序 s < n < b（按 (family, str(value)) 排序）
        assert expr[0] == "in"
        assert expr[1] == ["get", "district"]
        assert expr[2] == ["literal", ["武侯区", 2, True]]


class TestSpatialPropagationGuard:
    def test_spatial_link_propagation_rejected(self):
        """spatial 关系 + propagate → typed 拒绝（空键谓词口径错配防御）。"""
        raw = _v3_plan(
            sources=[_src("a", "ref:a", role="primary"), _src("b", "ref:b")],
            links=[{
                "link_id": "l1", "kind": "spatial_within",
                "left_source_id": "a", "right_source_id": "b",
                "predicate": "within", "cardinality": "one_to_many",
                "propagate": "left_to_right",
            }],
        )
        with pytest.raises(ValueError, match="不支持 propagate"):
            contract.ExplorationPlan.model_validate(raw)

    def test_spatial_link_without_propagation_ok(self):
        raw = _v3_plan(
            sources=[_src("a", "ref:a", role="primary"), _src("b", "ref:b")],
            links=[{
                "link_id": "l1", "kind": "spatial_within",
                "left_source_id": "a", "right_source_id": "b",
                "predicate": "within", "cardinality": "one_to_many",
            }],
        )
        plan = contract.ExplorationPlan.model_validate(raw)
        assert plan.links[0].propagate == "none"
