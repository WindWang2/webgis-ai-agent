"""Facet ↔ Artifact ↔ MapSpec Lineage 单元测试（ADR-0088 P3/P4）。

覆盖：
- 层 → source artifact 血缘（output ref + producer capability）；
- analysis facet 的输入血缘（registry artifact 类型交集推断）；
- chart 欠账的最小重计算输入（存活表/聚合类上游 ref）；
- 死 artifact 检测（expired descriptor / record 状态 → 执行债证据）；
- liveness 三态（alive / dead / unknown —— 不虚构）；
- superseded 记录的状态投影。
"""
from app.services.gis_harness.product_lineage import build_facet_lineage
from app.services.artifact_registry import ArtifactRecord


def _chapter(*, layer_in_spec=True):
    chapter = {
        "plan_id": "plan-test",
        "query": "成都小学统计",
        "data_requirements": [
            {"capability": "poi_query", "status": "available",
             "bound_ref": "ref:geojson-poi"},
        ],
        "analysis_steps": [
            {"capability": "category_breakdown", "status": "done",
             "bound_ref": "ref:stats-1"},
        ],
        "map_layers": [
            {"role": "primary", "layer_id": "poi-main", "enabled": True,
             "source_capability": "poi_query"},
        ],
        "template_selection": {"export_profile": {"chart": True}},
    }
    mapspec = {
        "layers": (
            [{"id": "poi-main", "source": "s-poi", "type": "circle"}]
            if layer_in_spec else []
        ),
        "sources": {"s-poi": {"type": "geojson", "ref_id": "ref:geojson-poi"}},
        "layout": {"components": []},
    }
    return chapter, mapspec


def test_layer_facet_backed_by_source_artifact():
    chapter, mapspec = _chapter()
    lineage = build_facet_lineage(chapter, mapspec)
    entry = lineage.entries["map_layer:poi-main"]
    out = [r for r in entry.artifact_refs if r.role == "output"]
    assert len(out) == 1
    assert out[0].ref == "ref:geojson-poi"
    assert out[0].producer_capability == "poi_query"


def test_analysis_facet_carries_input_lineage():
    chapter, mapspec = _chapter()
    lineage = build_facet_lineage(chapter, mapspec)
    entry = lineage.entries["analysis:category_breakdown"]
    inputs = [r for r in entry.artifact_refs if r.role == "input"]
    assert any(r.ref == "ref:geojson-poi" and r.producer_capability == "poi_query"
               for r in inputs)


def test_chart_owed_reuses_alive_stats_artifact():
    """Scenario D：chart 缺失 + statistics 存活 → produce_chart 可复用输入。"""
    chapter, mapspec = _chapter(layer_in_spec=False)
    # 层未落 spec：map_layer facet 有执行债（source_capability）
    lineage = build_facet_lineage(chapter, mapspec)
    chart_entry = lineage.entries["chart:required"]
    reusable = lineage.reusable_inputs("chart:required")
    assert "ref:stats-1" in reusable
    assert chart_entry.kind == "chart"


def test_expired_stats_artifact_excluded_from_reuse():
    """确认过期的上游 ref 不进 reusable（死 artifact 不虚构活性）。"""
    chapter, mapspec = _chapter()
    descriptors = {"ref:stats-1": None}
    lineage = build_facet_lineage(chapter, mapspec, descriptors=descriptors)
    assert "ref:stats-1" not in lineage.reusable_inputs("chart:required")


def test_unknown_liveness_still_reusable_but_flagged():
    """无 liveness 证据（无 descriptors/records）→ unknown，不排除也不虚构。"""
    chapter, mapspec = _chapter()
    lineage = build_facet_lineage(chapter, mapspec)
    entry = lineage.entries["chart:required"]
    stat_refs = [r for r in entry.artifact_refs if r.ref == "ref:stats-1"]
    assert stat_refs and stat_refs[0].liveness == "unknown"
    assert "ref:stats-1" in lineage.reusable_inputs("chart:required")


def test_dead_source_artifact_is_execution_debt_not_repair():
    """Scenario B：MapSpec source ref 过期 → 执行债证据（不 remount）。"""
    chapter, mapspec = _chapter()
    descriptors = {"ref:geojson-poi": None}
    lineage = build_facet_lineage(chapter, mapspec, descriptors=descriptors)
    entry = lineage.entries["map_layer:poi-main"]
    assert entry.recompute_capabilities == ["poi_query"]
    assert "ref:geojson-poi" in lineage.dead_outputs()


def test_record_status_projects_liveness():
    """ArtifactRecord 状态直接投影 liveness（valid→alive；superseded→排除）。"""
    chapter, mapspec = _chapter()
    records = {
        "ref:geojson-poi": ArtifactRecord(
            artifact_id="ref:geojson-poi", status="superseded",
        ),
        "ref:stats-1": ArtifactRecord(
            artifact_id="ref:stats-1", status="valid",
        ),
    }
    lineage = build_facet_lineage(chapter, mapspec, records=records)
    entry = lineage.entries["map_layer:poi-main"]
    assert entry.artifact_refs[0].liveness == "superseded"
    # superseded 的 ref 不进 reusable
    assert "ref:geojson-poi" not in lineage.reusable_inputs("chart:required")
    assert "ref:stats-1" in lineage.reusable_inputs("chart:required")


def test_lineage_is_pure_and_deterministic():
    chapter, mapspec = _chapter()
    a = build_facet_lineage(chapter, mapspec)
    b = build_facet_lineage(chapter, mapspec)
    assert {k: [r.to_dict() for r in v.artifact_refs]
            for k, v in a.entries.items()} == {
        k: [r.to_dict() for r in v.artifact_refs]
        for k, v in b.entries.items()
    }
