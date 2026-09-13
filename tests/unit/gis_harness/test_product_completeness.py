"""Product Completeness Validator（ADR-0183 M8）单元测试。

正交性：语义完整性 ≠ binding 完成度（planner）≠ 渲染/视口核验（completion/）。
全部检查确定性、结构化、code 可断言。
"""
import pytest

from app.services.gis_harness.product_completeness import (
    validate_product_completeness,
)
from app.services.gis_harness.product_shapes import shape_for_archetype
from app.services.gis_harness.product_spec import (
    MapProductSpec,
    ProductRelation,
    ProductView,
    ProductViewBinding,
    apply_product_edit,
    validate_product_spec,
)


def _view(vid, kind, **kw):
    kw.setdefault("binding", ProductViewBinding())
    return ProductView(view_id=vid, kind=kind, **kw)


def _spec(**kw):
    base = dict(spec_id="s", product_type="regional_comparison", task="administrative_statistic")
    base.setdefault("views", [
        _view("v-map", "map", required=True),
        _view("v-chart", "chart", chart_kind="bar", required=True,
              binding=ProductViewBinding(dataset_ref="ref:data")),
    ])
    base.setdefault("relations", [
        ProductRelation(src="v-map", dst="v-chart", kind="chart_linked_to_map"),
    ])
    base.update(kw)
    return MapProductSpec(**base)


def test_complete_regional_comparison_passes():
    s = _spec()
    assert validate_product_spec(s) == []
    report = validate_product_completeness(s)
    assert report.complete, report.to_dict()
    assert "shape_required_views" in report.checked


def test_missing_stats_family_fails_required_any():
    s = _spec(views=[_view("v-map", "map", required=True)],
              relations=[])
    report = validate_product_completeness(s)
    assert not report.complete
    codes = {f.code for f in report.findings}
    assert "required_any_missing" in codes  # comparison/chart/stats 全缺


def test_user_required_view_disabled_flags():
    s = _spec()
    s.views[1].enabled = False  # required chart 被禁用（未经显式编辑）
    report = validate_product_completeness(s)
    assert any(f.code == "user_required_view_disabled" for f in report.findings)


def test_explicit_retraction_not_flagged():
    """显式编辑撤回（把统计图去掉）→ 不再判完整性缺失（用户改主意）。"""
    s = _spec()
    ns, errs, _ = apply_product_edit(
        s, "toggle_component", "chart_panel",
        {"component_type": "chart_panel", "enabled": False}, "把右边的统计图去掉")
    assert ns is not None and not errs
    # 撤回后 required 已清 → 禁用态不再报 user_required_view_disabled
    ns2, errs2, _ = apply_product_edit(ns, "toggle_view", "v-chart")
    assert ns2 is not None
    report = validate_product_completeness(ns2)
    assert report.complete, report.to_dict()


def test_chart_map_data_mismatch_detected():
    s = _spec()
    s.views[0].binding = ProductViewBinding(dataset_ref="ref:data")
    s.views[1].binding = ProductViewBinding(dataset_ref="ref:other")
    report = validate_product_completeness(s)
    assert any(f.code == "chart_map_data_mismatch" for f in report.findings)
    assert not report.complete


def test_comparison_claim_without_comparison_view_warns():
    s = _spec(claims=["各行政区小学数量的可对比差异"])
    # regional_comparison shape 的 required_any 已由 chart 满足；但 claims
    # 明说对比而图上无 comparison 面 → warning
    report = validate_product_completeness(s)
    assert any(
        f.code == "comparison_claim_unsupported" and f.severity == "warning"
        for f in report.findings)


def test_attribution_disabled_disclosed():
    s = _spec()
    ns, errs, _ = apply_product_edit(
        s, "toggle_component", "attribution",
        {"component_type": "attribution", "enabled": False}, "去掉署名")
    assert ns is not None
    report = validate_product_completeness(ns)
    assert any(f.code == "source_note_removed" for f in report.findings)


def test_export_live_only_gap_disclosed_as_warning():
    s = _spec()
    from app.services.gis_harness.product_spec import ProductDeliveryIntent

    s.delivery = ProductDeliveryIntent(targets=["interactive", "pdf"])
    report = validate_product_completeness(s)
    export_findings = [f for f in report.findings
                       if f.code == "export_partial_coverage"]
    assert export_findings and export_findings[0].severity == "warning"
    # warning 不影响 complete（errors 才影响）
    assert report.complete


def test_generic_shape_minimal():
    s = _spec(product_type="", views=[_view("v-map", "map", required=True)],
              relations=[])
    report = validate_product_completeness(s)
    assert report.complete  # generic shape 只要求 map
    assert report.product_type == ""
