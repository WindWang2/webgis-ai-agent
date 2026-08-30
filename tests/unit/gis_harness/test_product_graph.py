"""Goal → Product Graph 投影（ADR-0085）单元测试。

不变式：ProductGraph 是派生只读投影（章节/MapSpec → 结构），绝不持久化、
不成为第二计划真相 —— 节点状态全部回读既有事实。
"""
import pytest

from app.services.gis_harness.product_graph import (
    KIND_CHART,
    KIND_MAP_LAYER,
    KIND_NARRATIVE,
    KIND_STATISTICS,
    S_DONE,
    S_FAILED,
    S_OFF,
    S_PENDING,
    build_product_graph,
)
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    import shutil
    import uuid

    sid = f"pg-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR

    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _chapter():
    """旗舰场景形章节：成都小学分布（POI → 密度 + 行政区对比）。"""
    return {
        "plan_id": "p1",
        "query": "成都小学分布情况",
        "recipe_id": "poi_density_distribution",
        "data_requirements": [
            {"capability": "poi_query", "status": "available", "bound_ref": "ref:geojson-poi"}
        ],
        "analysis_steps": [
            {"capability": "density_surface", "status": "done", "bound_ref": "ref:geojson-poi"},
            {"capability": "admin_aggregation", "status": "done", "bound_ref": "ref:geojson-district"},
        ],
        "map_layers": [
            {"role": "primary", "layer_id": "poi-heatmap", "enabled": True},
            {"role": "secondary", "layer_id": "district-choropleth", "enabled": True},
        ],
        "template_selection": {
            "composition_template_id": "tpl-x",
            "export_profile": {"formats": ["png", "pdf"]},
        },
    }


def _mapspec():
    return {
        "layers": [
            {"id": "poi-heatmap", "source": "s1", "type": "heatmap",
             "provenance": {"result_ref": "ref:geojson-poi"}},
            {"id": "district-choropleth", "source": "s2", "type": "fill",
             "provenance": {"result_ref": "ref:geojson-district"}},
        ],
        "sources": {},
        "layout": {
            "components": [
                {"id": "statistics", "type": "statistics_panel", "enabled": True},
                {"id": "chart-panel", "type": "chart_panel", "enabled": True,
                 "options": {"chartRef": "ref:chart-1"}},
            ]
        },
    }


def test_full_product_facets():
    """旗舰：heatmap + choropleth + statistics + chart + export + narrative。"""
    graph = build_product_graph(_chapter(), _mapspec())
    kinds = {n.kind for n in graph.nodes}
    assert kinds >= {
        "map_layer", "analysis", "statistics", "chart", "export", "narrative",
    }
    # 双地图 facet（primary + secondary）都在场
    layers = graph.by_kind(KIND_MAP_LAYER)
    assert {n.key for n in layers} == {"poi-heatmap", "district-choropleth"}
    assert all(n.status == S_DONE for n in layers)
    # 供给边：density → poi-heatmap（MapSpec provenance 实证）
    density = next(n for n in graph.nodes if n.key == "density_surface")
    assert density.inputs == ["map_layer:poi-heatmap"]
    # chart facet 携带 chartRef artifact
    chart = graph.by_kind(KIND_CHART)[0]
    assert chart.artifact_ref == "ref:chart-1"
    # narrative 待完成块 → pending
    assert graph.by_kind(KIND_NARRATIVE)[0].status == S_PENDING
    # summary 行包含 facet 构成
    line = graph.summary_line()
    assert "[Products]" in line
    assert "map 2/2" in line
    assert "stats 1/1" in line
    assert "chart 1/1" in line


def test_missing_layer_and_failed_rows_project_honestly():
    chapter = _chapter()
    # 行政区图层未挂载 + 聚合行失败
    spec = _mapspec()
    spec["layers"] = [spec["layers"][0]]
    chapter["analysis_steps"][1]["status"] = "failed"
    graph = build_product_graph(chapter, spec)
    district = next(
        n for n in graph.by_kind(KIND_MAP_LAYER) if n.key == "district-choropleth"
    )
    assert district.status == S_PENDING
    agg = next(n for n in graph.nodes if n.key == "admin_aggregation")
    assert agg.status == S_FAILED
    line = graph.summary_line()
    assert "map 1/2" in line
    assert "owed" in line


def test_disabled_facets_project_as_off():
    chapter = _chapter()
    chapter["map_layers"][1]["enabled"] = False  # 用户/计划关闭副层
    spec = _mapspec()
    graph = build_product_graph(chapter, spec)
    district = next(
        n for n in graph.by_kind(KIND_MAP_LAYER) if n.key == "district-choropleth"
    )
    assert district.status == S_OFF


def test_narrative_done_when_map_product_complete():
    chapter = _chapter()
    chapter["map_product"] = {"status": "complete"}
    graph = build_product_graph(chapter, _mapspec())
    assert graph.by_kind(KIND_NARRATIVE)[0].status == S_DONE


def test_no_spec_degrades_without_inventing_facts():
    graph = build_product_graph(_chapter(), None)
    # 无 MapSpec：图层在场不可知 → pending（不虚构 done）
    assert all(n.status == S_PENDING for n in graph.by_kind(KIND_MAP_LAYER))
    # 组件 facets 无从谈起（无 spec）→ 不出现
    assert graph.by_kind(KIND_STATISTICS) == []


def test_empty_chapter_is_safe():
    graph = build_product_graph(None)
    assert graph.nodes == []
    assert graph.summary_line() == ""
    graph2 = build_product_graph({})
    assert graph2.summary_line() == ""


def test_projection_is_derived_not_persisted():
    """同输入两次投影等价；且不写任何共享状态（纯函数）。"""
    a = build_product_graph(_chapter(), _mapspec())
    b = build_product_graph(_chapter(), _mapspec())
    assert [(n.node_id, n.status) for n in a.nodes] == [
        (n.node_id, n.status) for n in b.nodes
    ]
    assert a.summary_line() == b.summary_line()


@pytest.mark.asyncio
async def test_session_plan_projection_includes_products_line(clean_session):
    """[GIS Plan] 投影携带 [Products] facets 行（ADR-0085 集成）。"""
    from app.services.session_plan import (
        SessionPlan,
        _init_progress,
        format_session_plan_projection,
        save_session_plan,
    )
    import uuid

    chapter = _chapter()
    plan = SessionPlan(
        envelope_id=f"env-{uuid.uuid4().hex[:8]}",
        session_id=clean_session,
        user_goal="成都小学分布情况",
        gis_chapter=chapter,
        progress=_init_progress(chapter),
    )
    await save_session_plan(plan)
    text = format_session_plan_projection(plan, _mapspec())
    assert "[Products]" in text
    assert "map 2/2" in text
    # 无 spec 路径也安全（facet 行退化为分析侧事实）
    text2 = format_session_plan_projection(plan, None)
    assert isinstance(text2, str)


# ── P9: per-facet completion（派生投影；render 证据仅 revision 匹配时参与）──


def _observation(revision, layers, *, seq=1, session_id="sid"):
    return {
        "session_id": session_id,
        "sequence": seq,
        "source": "frontend_runtime",
        "mapspec_fingerprint": "fp-aaaaaaaaaaaaaaaa",
        "mapspec_revision": revision,
        "layers": layers,
        "style_loaded": True,
        "reconcile_error": "",
        "components": [],
        "runtime_errors": [],
    }


def test_facet_completion_full_product_all_complete():
    from app.services.gis_harness.product_graph import (
        FS_COMPLETE,
        KIND_CHART,
        KIND_MAP_LAYER,
        KIND_STATISTICS,
        build_facet_completion,
    )

    facets = build_facet_completion(_chapter(), _mapspec())
    by_kind = {}
    for f in facets:
        by_kind.setdefault(f.kind, []).append(f)
    layer_statuses = {f.status for f in by_kind[KIND_MAP_LAYER]}
    assert layer_statuses == {FS_COMPLETE}
    assert {f.status for f in by_kind[KIND_STATISTICS]} == {FS_COMPLETE}
    assert {f.status for f in by_kind[KIND_CHART]} == {FS_COMPLETE}
    # narrative 依赖 map_product 块（缺席 → pending，不虚构）
    narrative = [f for f in facets if f.kind == "narrative"]
    assert narrative and narrative[0].status == "pending"


def test_facet_render_evidence_requires_revision_match():
    """observation revision 不匹配 → render_status 留空（不虚构 verified）。"""
    from app.services.gis_harness.product_graph import (
        FS_COMPLETE,
        KIND_MAP_LAYER,
        build_facet_completion,
    )

    obs = _observation(9, [{"id": "poi-heatmap", "runtime_layer_count": 2, "visible": True}])
    facets = build_facet_completion(
        _chapter(), _mapspec(), observation=obs, current_revision=10
    )
    layer_facets = [f for f in facets if f.kind == KIND_MAP_LAYER]
    assert all(f.render_status == "" for f in layer_facets)
    assert all(f.status == FS_COMPLETE for f in layer_facets)

    # 匹配 revision → verified
    obs2 = _observation(10, [{"id": "poi-heatmap", "runtime_layer_count": 2, "visible": True}])
    facets2 = build_facet_completion(
        _chapter(), _mapspec(), observation=obs2, current_revision=10
    )
    primary = next(f for f in facets2 if f.kind == KIND_MAP_LAYER and f.key == "poi-heatmap")
    assert primary.render_status == "verified"
    assert primary.status == FS_COMPLETE


def test_facet_render_missing_layer_flips_to_needs_repair():
    from app.services.gis_harness.product_graph import (
        FS_NEEDS_REPAIR,
        KIND_MAP_LAYER,
        build_facet_completion,
    )

    obs = _observation(10, [])  # runtime 无任何层挂载
    facets = build_facet_completion(
        _chapter(), _mapspec(), observation=obs, current_revision=10
    )
    primary = next(f for f in facets if f.kind == KIND_MAP_LAYER and f.key == "poi-heatmap")
    assert primary.render_status == "issues"
    assert primary.status == FS_NEEDS_REPAIR


def test_facet_bbox_from_descriptor_else_null():
    from app.services.gis_harness.product_graph import build_facet_completion

    descriptors = {"ref:geojson-poi": {"bbox": [103.9, 30.5, 104.2, 30.8]}}
    facets = build_facet_completion(
        _chapter(), _mapspec(), descriptors=descriptors
    )
    analysis = next(f for f in facets if f.kind == "analysis" and f.key == "poi_query")
    assert analysis.bbox == [103.9, 30.5, 104.2, 30.8]
    layer = next(f for f in facets if f.kind == "map_layer" and f.key == "poi-heatmap")
    # 层 bbox：source 无 ref/bounds → None（不虚构）
    assert layer.bbox is None
    # 无 descriptors → 全部 None
    facets_none = build_facet_completion(_chapter(), _mapspec())
    assert all(f.bbox is None for f in facets_none)


def test_chart_required_owed_facet_synthesized():
    """Scenario M 投影面：export_profile.chart=True 且无 chart 组件 → owed。"""
    from app.services.gis_harness.product_graph import (
        FS_PENDING,
        KIND_CHART,
        build_facet_completion,
    )

    chapter = _chapter()
    chapter["template_selection"]["export_profile"] = {"formats": ["png"], "chart": True}
    spec = _mapspec()
    spec["layout"]["components"] = [
        c for c in spec["layout"]["components"] if c["type"] != "chart_panel"
    ]
    facets = build_facet_completion(chapter, spec)
    chart = [f for f in facets if f.kind == KIND_CHART]
    assert len(chart) == 1
    assert chart[0].facet_id == "chart:required"
    assert chart[0].status == FS_PENDING
    assert chart[0].required is True
    # 图表组件在场 → 不合成
    facets2 = build_facet_completion(chapter, _mapspec())
    chart2 = [f for f in facets2 if f.kind == KIND_CHART]
    assert all(f.facet_id != "chart:required" for f in chart2)


def test_facet_projection_never_persists():
    """派生只读：两次构建是独立对象，输入不变输出相等（无隐藏状态）。"""
    from app.services.gis_harness.product_graph import build_facet_completion

    a = build_facet_completion(_chapter(), _mapspec())
    b = build_facet_completion(_chapter(), _mapspec())
    assert [f.to_dict() for f in a] == [f.to_dict() for f in b]
    assert a is not b
