"""M4 新语义检查测试：图-图口径 / 视口覆盖 / 密度语义诚实。

每个码覆盖 pass / fail 或 warning / not_evaluated 三态；经唯一入口
``evaluate_cartography_semantics`` 驱动（契约行为面，非内部函数直调）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

pytestmark = pytest.mark.cartography


def _spec(layers, components=None, view=None, sources=None):
    return {
        "version": "1.7",
        "view": view,
        "sources": sources or {},
        "layers": layers,
        "layout": {"components": components or []},
    }


def _checks(report, code):
    return [c for c in report.checks if c.rule == code]


def test_chart_map_coherence_same_family_passes() -> None:
    spec = _spec(
        layers=[{"id": "ly1", "type": "fill", "source": "s1", "visible": True,
                 "name": "各区小学数量",
                 "legend_spec": {"field": "count", "title": "小学数量（所）"}}],
        components=[{"id": "chart1", "type": "chart_panel",
                     "options": {"chart": {
                         "type": "bar", "title": "各区小学数量对比（所）",
                         "data": [{"name": "锦江区", "value": 12}]}}}],
    )
    report = evaluate_cartography_semantics(spec)
    checks = _checks(report, "CHART_MAP_FIELD_COHERENCE")
    assert checks and checks[0].status == "pass"


def test_chart_map_coherence_contradiction_fails() -> None:
    spec = _spec(
        layers=[{"id": "ly1", "type": "fill", "source": "s1", "visible": True,
                 "name": "各区小学数量",
                 "legend_spec": {"field": "count", "title": "小学数量（所）"}}],
        components=[{"id": "chart1", "type": "chart_panel",
                     "options": {"chart": {
                         "type": "bar", "title": "各区每万人小学密度对比",
                         "data": [{"name": "锦江区", "value": 1.2}]}}}],
    )
    report = evaluate_cartography_semantics(spec)
    checks = _checks(report, "CHART_MAP_FIELD_COHERENCE")
    assert checks and checks[0].status == "fail"


def test_chart_map_coherence_disclosed_contradiction_warns() -> None:
    spec = _spec(
        layers=[{"id": "ly1", "type": "fill", "source": "s1", "visible": True,
                 "name": "各区小学数量",
                 "legend_spec": {"field": "count", "title": "小学数量（所）"}}],
        components=[{"id": "chart1", "type": "chart_panel",
                     "options": {"chart": {
                         "type": "bar",
                         "title": "各区每万人小学密度（估计，口径不同）",
                         "data": [{"name": "锦江区", "value": 1.2}]}}}],
    )
    report = evaluate_cartography_semantics(spec)
    checks = _checks(report, "CHART_MAP_FIELD_COHERENCE")
    assert checks and checks[0].status == "warning"


def test_chart_map_coherence_one_side_only_not_evaluated() -> None:
    spec = _spec(
        layers=[{"id": "ly1", "type": "fill", "source": "s1", "visible": True,
                 "name": "各区",
                 "legend_spec": {"field": "count", "title": "小学数量（所）"}}],
        components=[{"id": "chart1", "type": "chart_panel",
                     "options": {"chart": {
                         "type": "bar", "title": "汇总",
                         "data": [{"name": "a", "value": 1}]}}}],
    )
    report = evaluate_cartography_semantics(spec)
    checks = _checks(report, "CHART_MAP_FIELD_COHERENCE")
    assert checks and checks[0].status == "not_evaluated"


def test_viewport_covers_pass_and_fail() -> None:
    sources = {"s1": {"type": "geojson",
                      "profile": {"bbox": [104.0, 30.5, 104.2, 30.7]}}}
    layer = {"id": "ly1", "type": "circle", "source": "s1", "visible": True}
    ok = evaluate_cartography_semantics(_spec(
        [layer], view={"center": [104.1, 30.6], "zoom": 10},
        sources=sources))
    assert _checks(ok, "VIEWPORT_COVERS_RESULTS")[0].status == "pass"
    off = evaluate_cartography_semantics(_spec(
        [layer], view={"center": [116.4, 39.9], "zoom": 10},  # 北京视口
        sources=sources))
    check = _checks(off, "VIEWPORT_COVERS_RESULTS")[0]
    assert check.status == "fail"


def test_viewport_covers_not_evaluated_without_evidence() -> None:
    layer = {"id": "ly1", "type": "circle", "source": "s1", "visible": True}
    no_view = evaluate_cartography_semantics(_spec(
        [layer], view=None, sources={"s1": {"profile": {"bbox": [0, 0, 1, 1]}}}))
    assert _checks(no_view, "VIEWPORT_COVERS_RESULTS")[0].status == "not_evaluated"
    no_bbox = evaluate_cartography_semantics(_spec(
        [layer], view={"center": [104.1, 30.6], "zoom": 10},
        sources={"s1": {"profile": {}}}))
    assert _checks(no_bbox, "VIEWPORT_COVERS_RESULTS")[0].status == "not_evaluated"


def test_density_kde_impersonating_population_density_fails() -> None:
    layer = {"id": "heat1", "type": "heatmap", "source": "s1", "visible": True,
             "name": "成都人口密度热力图"}
    report = evaluate_cartography_semantics(_spec([layer]))
    checks = _checks(report, "DENSITY_SEMANTICS_HONESTY")
    assert checks and checks[0].status == "fail"


def test_density_kde_with_disclosure_passes() -> None:
    layer = {"id": "heat1", "type": "heatmap", "source": "s1", "visible": True,
             "name": "成都小学密度概览（核密度估计）"}
    report = evaluate_cartography_semantics(_spec([layer]))
    checks = _checks(report, "DENSITY_SEMANTICS_HONESTY")
    assert checks and checks[0].status == "pass"


def test_density_kde_generic_claim_without_disclosure_warns() -> None:
    layer = {"id": "heat1", "type": "heatmap", "source": "s1", "visible": True,
             "name": "成都小学密度热力图"}
    report = evaluate_cartography_semantics(_spec([layer]))
    checks = _checks(report, "DENSITY_SEMANTICS_HONESTY")
    assert checks and checks[0].status == "warning"


def test_density_raw_count_mislabeled_as_rate_warns() -> None:
    layer = {"id": "fill1", "type": "fill", "source": "s1", "visible": True,
             "name": "各区每万人小学数量",
             "data_binding": {"field": "school_count"},
             "legend_spec": {"field": "school_count"}}
    report = evaluate_cartography_semantics(_spec([layer]))
    checks = _checks(report, "DENSITY_SEMANTICS_HONESTY")
    assert checks and checks[0].status == "warning"


def test_density_no_claim_no_check() -> None:
    layer = {"id": "fill1", "type": "fill", "source": "s1", "visible": True,
             "name": "各区小学数量",
             "data_binding": {"field": "school_count"},
             "legend_spec": {"field": "school_count", "title": "小学数量（所）"}}
    report = evaluate_cartography_semantics(_spec([layer]))
    assert not _checks(report, "DENSITY_SEMANTICS_HONESTY")
