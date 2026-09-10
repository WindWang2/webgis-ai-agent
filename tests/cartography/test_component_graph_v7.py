"""V7（Goal 08 Phase B/C）组件图 + registry 智能契约测试.

覆盖：
1. schema 1.2 additive：layout.component_links 校验/披露/round-trip 保真；
   迁移矩阵（1.0/1.1 → 1.2 identity；1.3 forward 拒绝）。
2. component_graph：derived/explicit 边、悬空披露、duplicate_binding /
   cycle / unknown_component_type / orphan_binding、确定性拓扑序
   （under/requires 语义 + 环回退）、有界摘要。
3. component registry V7：语义搜索（词表/过滤器/弃用排除）、可解释推荐
   （模型兼容/角色命中/在场去重/确定性）、弃用指针与预览元数据契约。
"""
import json

import pytest

from app.lib.cartography.component_graph import (
    build_component_graph,
    graph_summary,
    topological_component_order,
    validate_component_graph,
)
from app.lib.cartography.component_registry import (
    ComponentRecommendationContext,
    get_component_registry,
    reset_component_registry,
)
from app.lib.cartography.mapspec_schema import (
    KNOWN_VERSIONS,
    LATEST_VERSION,
    MapSpecSchemaError,
    canonicalize_mapspec,
    dumps_canonical,
    parse_mapspec,
    require_parseable_mapspec,
)


@pytest.fixture()
def registry():
    reset_component_registry()
    reg = get_component_registry()
    try:
        yield reg
    finally:
        reset_component_registry()


def _spec_with_links(links=None, extra_component=None):
    components = [
        {"id": "title-1", "type": "title"},
        {"id": "subtitle-1", "type": "subtitle"},
        {"id": "legend-main", "type": "legend", "options": {"layerId": "districts"}},
        {"id": "chart-1", "type": "chart_panel", "options": {"layerId": "districts"}},
        {"id": "north-1", "type": "north_arrow"},
    ]
    if extra_component is not None:
        components.append(extra_component)
    layout = {"components": components}
    if links is not None:
        layout["component_links"] = links
    return {
        "version": "1.2",
        "layers": [
            {"id": "districts", "source": "src", "type": "fill"},
            {"id": "schools", "source": "src", "type": "circle"},
        ],
        "layout": layout,
    }


# ── schema 1.2 additive ──────────────────────────────────────────────────


class TestSchemaV12Links:
    def test_known_versions_include_12(self):
        assert LATEST_VERSION == "1.2"
        assert KNOWN_VERSIONS == ("1.0", "1.1", "1.2")

    def test_links_round_trip_fidelity(self):
        spec = _spec_with_links([
            {"src": "chart-1", "dst": "legend-main", "type": "under"},
        ])
        assert dumps_canonical(canonicalize_mapspec(spec)) == json.dumps(
            spec, ensure_ascii=False, separators=(",", ":"))

    def test_legacy_versions_still_parse(self):
        for version in ("1.0", "1.1"):
            spec = _spec_with_links([])
            spec["version"] = version
            result = parse_mapspec(spec)
            assert result.valid, result.invalid_fields
            assert result.effective_version == "1.2"
            assert result.migrated

    def test_forward_version_rejected(self):
        spec = _spec_with_links([])
        spec["version"] = "1.3"
        with pytest.raises(MapSpecSchemaError):
            require_parseable_mapspec(spec)

    def test_unknown_link_type_is_invalid_disclosure(self):
        spec = _spec_with_links([
            {"src": "chart-1", "dst": "legend-main", "type": "magic"},
        ])
        result = parse_mapspec(spec)
        assert not result.valid
        assert any("component_links" in f.path for f in result.invalid_fields)

    def test_links_bounded_at_32(self):
        links = [{"src": "chart-1", "dst": "legend-main", "type": "under"}
                 for _ in range(33)]
        result = parse_mapspec(_spec_with_links(links))
        assert not result.valid
        assert any("component_links" in f.path for f in result.invalid_fields)

    def test_links_absent_stays_valid(self):
        result = parse_mapspec(_spec_with_links())
        assert result.valid, result.invalid_fields


# ── component graph ──────────────────────────────────────────────────────


class TestComponentGraph:
    def test_derived_layer_bindings(self):
        graph = build_component_graph(_spec_with_links())
        assert graph.components_bound_to_layer("districts") == [
            "chart-1", "legend-main"]
        assert graph.components_bound_to_layer(
            "districts", component_type="legend") == ["legend-main"]
        pairs = {(lk.src, lk.dst, lk.type) for lk in graph.links}
        assert ("subtitle-1", "title-1", "annotates") in pairs

    def test_explicit_links_and_dangling_disclosures(self):
        spec = _spec_with_links([
            {"src": "chart-1", "dst": "legend-main", "type": "under"},
            {"src": "chart-1", "dst": "ghost", "type": "requires"},
        ])
        graph = build_component_graph(spec)
        explicit = [lk for lk in graph.links if lk.origin == "explicit"]
        assert [(lk.src, lk.dst, lk.type) for lk in explicit] == [
            ("chart-1", "legend-main", "under")]
        assert any("ghost" in d for d in graph.disclosures)

    def test_duplicate_binding_issue(self):
        spec = _spec_with_links(extra_component={
            "id": "legend-dup", "type": "legend", "options": {"layerId": "districts"},
        })
        issues = validate_component_graph(build_component_graph(spec))
        dup = [i for i in issues if i.code == "duplicate_binding"]
        assert len(dup) == 1
        assert set(dup[0].ids) == {"legend-main", "legend-dup"}

    def test_per_layer_legends_are_not_duplicates(self):
        """图例族 per-layer 展开是合法构成（v2 binding 语义），不是重复。"""
        spec = _spec_with_links(extra_component={
            "id": "cb-schools", "type": "continuous_colorbar",
            "options": {"layerId": "schools"},
        })
        issues = validate_component_graph(build_component_graph(spec))
        assert not [i for i in issues if i.code == "duplicate_binding"]

    def test_cycle_detection(self):
        spec = _spec_with_links([
            {"src": "legend-main", "dst": "chart-1", "type": "requires"},
            {"src": "chart-1", "dst": "legend-main", "type": "requires"},
        ])
        issues = validate_component_graph(build_component_graph(spec))
        assert any(i.code == "cycle" for i in issues)

    def test_orphan_binding_conflict(self):
        spec = _spec_with_links([
            {"src": "chart-1", "dst": "schools", "type": "binds_to",
             "dst_kind": "layer"},
        ])
        issues = validate_component_graph(build_component_graph(spec))
        orphan = [i for i in issues if i.code == "orphan_binding"]
        assert len(orphan) == 1  # layerId=districts vs 显式 binds_to schools

    def test_unknown_component_type_flagged(self, registry):
        spec = _spec_with_links(extra_component={"id": "x", "type": "woozle"})
        issues = validate_component_graph(build_component_graph(spec))
        unknown = [i for i in issues if i.code == "unknown_component_type"]
        assert len(unknown) == 1 and unknown[0].ids == ["x"]

    def test_topological_order_under_semantics(self):
        spec = _spec_with_links([
            {"src": "chart-1", "dst": "legend-main", "type": "under"},
        ])
        order = topological_component_order(build_component_graph(spec))
        # under：src 在 dst 之下 → 先绘制
        assert order.index("chart-1") < order.index("legend-main")

    def test_topological_order_requires_semantics(self):
        spec = _spec_with_links([
            {"src": "legend-main", "dst": "north-1", "type": "requires"},
        ])
        order = topological_component_order(build_component_graph(spec))
        assert order.index("north-1") < order.index("legend-main")

    def test_topological_order_cycle_falls_back_deterministic(self):
        spec = _spec_with_links([
            {"src": "legend-main", "dst": "chart-1", "type": "under"},
            {"src": "chart-1", "dst": "legend-main", "type": "under"},
        ])
        order = topological_component_order(build_component_graph(spec))
        assert sorted(order) == sorted(
            ["title-1", "subtitle-1", "legend-main", "chart-1", "north-1"])
        # 确定性：同输入两次一致
        assert order == topological_component_order(build_component_graph(spec))

    def test_empty_and_degenerate_specs(self):
        assert build_component_graph(None).nodes == []
        assert build_component_graph({}).nodes == []
        graph = build_component_graph({"layout": {"components": [{"id": ""}, "junk"]}})
        assert graph.nodes == []
        assert graph.disclosures

    def test_bounded_summary(self, registry):
        summary = graph_summary(_spec_with_links())
        assert summary["node_count"] == 5
        assert "legend" in summary["roles"]
        assert any(n == "chart-1" for n in summary["floating"]) is False
        assert summary["disclosure_count"] == 0

    def test_disabled_component_keeps_node(self):
        spec = _spec_with_links(extra_component={
            "id": "ann-1", "type": "annotation", "enabled": False,
        })
        graph = build_component_graph(spec)
        node = graph.node("ann-1")
        assert node is not None and node.enabled is False

    def test_floating_placement_projected(self):
        spec = _spec_with_links(extra_component={
            "id": "chart-f", "type": "chart_panel",
            "placement": {"mode": "floating", "x": 8, "y": 8},
        })
        graph = build_component_graph(spec)
        assert graph.node("chart-f").placement_mode == "floating"


# ── registry V7：search / recommend / 弃用 / preview ─────────────────────


class TestRegistrySearch:
    def test_chinese_keyword_hits(self, registry):
        hits = registry.search("区县统计")
        assert hits, "区县统计 keyword should hit chart_panel"
        assert hits[0].descriptor.id == "chart_panel"
        assert "keyword" in hits[0].matched_fields

    def test_exact_id_and_role(self, registry):
        assert registry.search("north_arrow")[0].descriptor.id == "north_arrow"
        hits = registry.search("legend", semantic_role="legend")
        assert {h.descriptor.id for h in hits} >= {
            "legend", "continuous_colorbar", "categorical_legend"}

    def test_english_synonym(self, registry):
        hits = registry.search("colorbar")
        assert any(h.descriptor.id == "continuous_colorbar" for h in hits)

    def test_output_filter(self, registry):
        hits = registry.search("表格", output_target="interactive")
        assert hits
        assert all("interactive" in h.descriptor.supported_outputs for h in hits)

    def test_category_prefix_filter(self, registry):
        hits = registry.search("图例", category="legend")
        assert hits
        assert all(h.descriptor.category.startswith("legend") for h in hits)

    def test_empty_query_returns_empty(self, registry):
        assert registry.search("") == []
        assert registry.search("   ") == []

    def test_limit_bounded(self, registry):
        assert len(registry.search("图", limit=3)) <= 3

    def test_deprecated_excluded_by_default(self, registry):
        desc = registry.get("title")
        registry._by_id["title"] = desc.model_copy(
            update={"deprecated": True, "deprecated_by": "subtitle"})
        assert "title" not in [h.descriptor.id for h in registry.search("标题")]
        assert "title" in [
            h.descriptor.id
            for h in registry.search("标题", include_deprecated=True)]


class TestRegistryRecommend:
    def test_model_compatible_and_role_hits(self, registry):
        recs = registry.recommend(ComponentRecommendationContext(
            map_model="visual_heatmap",
            output_target="interactive",
            task_categories=("density",),
            semantic_roles=("legend", "statistics"),
        ))
        assert recs, "expected non-empty recommendation"
        assert recs[0].component_id == "continuous_colorbar"
        assert recs[0].reasons

    def test_existing_single_component_excluded(self, registry):
        def _north(present_ids):
            recs = registry.recommend(ComponentRecommendationContext(
                output_target="interactive",
                semantic_roles=("orientation",),
                existing_components=present_ids,
            ), limit=16)
            return next((r for r in recs if r.component_id == "north_arrow"), None)

        baseline = _north(())
        assert baseline is not None and baseline.score >= 5
        # composer 惯例实例 id（横线）与类型 id 都能命中去重；
        # single 组件已在场 → 排除出推荐（不是"新增候选"）
        for present_id in ("north_arrow", "north-arrow"):
            assert _north((present_id,)) is None

    def test_existing_multiple_component_stays(self, registry):
        recs = registry.recommend(ComponentRecommendationContext(
            output_target="interactive",
            existing_components=("legend",),
        ), limit=16)
        legend = next(r for r in recs if r.component_id == "legend")
        assert legend is not None
        assert any("已在场" in reason for reason in legend.reasons)

    def test_deterministic(self, registry):
        ctx = ComponentRecommendationContext(
            map_model="administrative_choropleth",
            output_target="pdf",
            task_categories=("thematic_cartography",),
        )
        first = registry.recommend(ctx)
        second = registry.recommend(ctx)
        assert [(r.component_id, r.score) for r in first] == \
            [(r.component_id, r.score) for r in second]

    def test_output_filter_hard(self, registry):
        recs = registry.recommend(ComponentRecommendationContext(
            output_target="print_nonexistent"))
        assert all(
            registry.get(r.component_id) is None
            or "print_nonexistent" in registry.get(r.component_id).supported_outputs
            for r in recs
        )


class TestRegistryV7Contract:
    def test_seed_projections_applied(self, registry):
        desc = registry.get("chart_panel")
        assert desc.search_keywords_zh, "keyword projection should fill"
        assert desc.preview.glyph, "preview projection should fill"

    def test_all_seeds_have_search_keywords(self, registry):
        missing = [
            d.id for d in registry.native_descriptors()
            if not d.search_keywords_zh
        ]
        assert missing == []

    def test_validate_catches_dangling_deprecated_by(self, registry):
        desc = registry.get("title")
        registry._by_id["title"] = desc.model_copy(
            update={"deprecated": True, "deprecated_by": "ghost_comp"})
        issues = registry.validate()
        assert any("deprecated_by" in i for i in issues)

    def test_validate_catches_bad_accent(self, registry):
        desc = registry.get("title")
        registry._by_id["title"] = desc.model_copy(
            update={"preview": desc.preview.model_copy(update={"accent": "red"})})
        issues = registry.validate()
        assert any("preview.accent" in i for i in issues)

    def test_clean_registry_still_validates(self, registry):
        assert registry.validate() == []


class TestRegistryEdgePaths:
    """review P3 回归：弃用兜底分支与 deprecated×filter 组合。"""

    def test_recommend_deprecated_fallback_when_all_excluded(self, registry):
        for cid in registry.all_ids:
            desc = registry.get(cid)
            registry._by_id[cid] = desc.model_copy(
                update={"deprecated": True,
                        "deprecated_by": (registry.all_ids[1]
                                          if cid == registry.all_ids[0]
                                          else "")})
        recs = registry.recommend(ComponentRecommendationContext(
            output_target="interactive"), limit=4)
        assert recs
        assert all(r.reasons == ["deprecated fallback"] for r in recs)

    def test_search_include_deprecated_with_filters(self, registry):
        desc = registry.get("continuous_colorbar")
        registry._by_id["continuous_colorbar"] = desc.model_copy(
            update={"deprecated": True, "deprecated_by": "legend"})
        # 弃用 + 类目过滤：缺省排除，显式 include 命中且仍受 category 约束
        assert registry.search("色条", category="legend") == []
        hits = registry.search("色条", category="legend",
                               include_deprecated=True)
        assert [h.descriptor.id for h in hits] == ["continuous_colorbar"]

    def test_explicit_source_link_namespace(self):
        """review P3：layer/source 命名空间不得混指（source id 冒充 layer
        必须披露悬空）。"""
        spec = _spec_with_links([
            {"src": "chart-1", "dst": "districts", "type": "binds_to",
             "dst_kind": "source"},
        ])
        graph = build_component_graph(spec)
        assert any("source" in d for d in graph.disclosures)
