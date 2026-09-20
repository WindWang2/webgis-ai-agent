"""统一产品完整性 verdict（ADR-0204）契约测试。

覆盖：
- finalizer 消费 product_completeness：语义缺口 → product_* findings 参与
  状态阶梯（不撒谎的 complete）；语义完整的章节零新 findings（零回归）；
- map_product_block 携带 additive product_completeness 摘要键；
- assess_export_parity 豁免单源（component_renderers）行为不变；
- publication 矢量链真值单源（PUBLICATION_COMPONENT_TYPES）与 product
  completeness 的导出覆盖披露一致。
"""
import uuid

import pytest

from app.services.gis_harness.completion.pipeline import (
    _product_completeness_report,
    _validate_all,
    map_product_block,
    run_map_finalization,
)
from app.services.gis_harness.completion.contracts import (
    STATUS_COMPLETE,
    STATUS_NEEDS_REPAIR,
)
from app.services.gis_harness.product_spec import (
    MapProductSpec,
    ProductDeliveryIntent,
    ProductRelation,
    ProductView,
    ProductViewBinding,
    storage_payload,
)
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    import shutil

    sid = f"unified-comp-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR

    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _view(vid, kind, **kw):
    kw.setdefault("binding", ProductViewBinding())
    return ProductView(view_id=vid, kind=kind, **kw)


def _complete_spec() -> MapProductSpec:
    from app.services.gis_harness.product_templates import (
        get_product_template_registry,
    )

    template = get_product_template_registry().find_for_recipe(
        "administrative_choropleth", "")
    return MapProductSpec(
        spec_id="s", product_type="regional_comparison",
        task="administrative_statistic",
        template_id=template.id if template is not None else "",
        composition_template_id=(
            template.composition_template_id if template is not None else ""),
        views=[
            _view("v-map", "map", required=True),
            _view("v-chart", "chart", chart_kind="bar", required=True,
                  binding=ProductViewBinding(dataset_ref="ref:data")),
        ],
        relations=[
            ProductRelation(src="v-map", dst="v-chart", kind="chart_linked_to_map"),
        ],
    )


def _gap_spec() -> MapProductSpec:
    """regional_comparison shape 缺 chart/stats 面 → required_any_missing。"""
    from app.services.gis_harness.product_templates import (
        get_product_template_registry,
    )

    template = get_product_template_registry().find_for_recipe(
        "administrative_choropleth", "")
    return MapProductSpec(
        spec_id="s2", product_type="regional_comparison",
        task="administrative_statistic",
        template_id=template.id if template is not None else "",
        views=[_view("v-map", "map", required=True)],
        relations=[],
    )


def _chapter_with_spec(spec: MapProductSpec) -> dict:
    return {
        "plan_id": "plan-test",
        "query": "成都各区小学数量对比",
        "recipe_id": "administrative_choropleth",
        "data_requirements": [
            {"capability": "poi_query", "purpose": "POI", "status": "available",
             "bound_ref": "ref:geojson-x", "optional": False},
        ],
        "analysis_steps": [
            {"capability": "admin_aggregation", "purpose": "stats",
             "status": "done", "bound_ref": "ref:geojson-x", "optional": False},
        ],
        "map_layers": [{"role": "primary", "layer_id": "poi-main", "enabled": True}],
        "components": [],
        "template_selection": {},
        "product_spec": storage_payload(spec),
    }


def _inputs(chapter: dict) -> dict:
    return {
        "mapspec": {"layers": [], "sources": [],
                    "layout": {"components": []}},
        "descriptors": {},
        "required_slots": [],
    }


# ── finalizer 消费 product_completeness ────────────────────────────────


def test_validate_all_projects_product_gap_as_error_finding():
    chapter = _chapter_with_spec(_gap_spec())
    findings = _validate_all(_inputs(chapter), chapter)
    product_errs = [f for f in findings if f.code == "product_required_any_missing"]
    assert product_errs and product_errs[0].severity == "error"
    assert product_errs[0].repair is None, "修复归组装/执行通道，finalizer 不自造"


def test_validate_all_semantically_complete_chapter_has_zero_product_findings():
    chapter = _chapter_with_spec(_complete_spec())
    findings = _validate_all(_inputs(chapter), chapter)
    assert not [f for f in findings if f.code.startswith("product_")]


def test_validate_all_without_product_spec_unchanged():
    chapter = _chapter_with_spec(_complete_spec())
    del chapter["product_spec"]
    findings = _validate_all(_inputs(chapter), chapter)
    assert not [f for f in findings if f.code.startswith("product_")]


def test_product_completeness_report_helper_none_for_missing_spec():
    assert _product_completeness_report({}) is None
    assert _product_completeness_report({"product_spec": {"digest": "x"}}) is None


# ── map_product_block 的 additive 摘要键 ───────────────────────────────


def test_map_product_block_carries_product_completeness_summary():
    from app.services.gis_harness.completion.contracts import MapCompletionResult

    chapter = _chapter_with_spec(_gap_spec())
    block = map_product_block(MapCompletionResult(), 0, chapter=chapter)
    summary = block.get("product_completeness")
    assert summary is not None
    assert summary["complete"] is False
    assert "required_any_missing" in summary["finding_codes"]


def test_map_product_block_omits_key_without_product_spec():
    from app.services.gis_harness.completion.contracts import MapCompletionResult

    block = map_product_block(MapCompletionResult(), 0, chapter=None)
    assert "product_completeness" not in block


@pytest.mark.asyncio
async def test_finalization_gap_blocks_complete(clean_session):
    """端到端：语义缺口的章节不得 complete（统一 verdict 的牙齿）。"""
    from app.services.gis_harness.completion.pipeline import run_map_finalization

    result = await run_map_finalization(
        clean_session, chapter=_chapter_with_spec(_gap_spec()))
    codes = {f.code for f in result.findings}
    assert "product_required_any_missing" in codes
    assert result.status in (STATUS_NEEDS_REPAIR, "failed")


# ── export parity 豁免单源 + publication 真值对账 ───────────────────────


def test_export_parity_exemptions_single_sourced():
    from app.lib.cartography.component_renderers import (
        EXPORT_PARITY_EXEMPT_TYPES,
    )
    from app.services.gis_harness.completion.validators.viewport_export import (
        assess_export_parity,
    )

    # basemap 豁免（导出管线自身承接）；面板族有 canvas 消费方 → parity
    spec = {"layout": {"components": [
        {"id": "bm", "type": "basemap", "enabled": True},
        {"id": "t", "type": "title", "enabled": True},
        {"id": "c", "type": "chart_panel", "enabled": True},
    ]}}
    assert assess_export_parity(spec) == "parity"
    assert EXPORT_PARITY_EXEMPT_TYPES == ("basemap",)


def test_publication_truth_matches_completeness_disclosure():
    """product_completeness 的导出覆盖披露以 publication 真值单源为准：
    chrome 基础族不披露，面板/披露族如实披露。"""
    from app.services.mapspec_to_svg import PUBLICATION_COMPONENT_TYPES
    from app.services.gis_harness.product_completeness import (
        _publication_omitted_families as _omit,
    )

    assert not _omit({"legend", "title", "north_arrow", "scale_bar"})
    omitted = _omit({"chart_panel", "statistics_panel", "continuous_colorbar",
                     "annotation", "methodology_note"})
    assert omitted == ["annotation", "chart_panel", "continuous_colorbar",
                       "methodology_note", "statistics_panel"]
    # 真值源与 _render_chrome_groups 处理序同模块（漂移防线：常量随处理序走）
    assert "map_border" in PUBLICATION_COMPONENT_TYPES
    assert "graticule" in PUBLICATION_COMPONENT_TYPES


def test_delivery_coverage_warns_only_publication_omitted_families():
    """delivery 含非 interactive 目标 → 面板族缺席如实披露（warning 级）。"""
    from app.services.gis_harness.product_completeness import (
        validate_product_completeness,
    )

    s = _complete_spec()
    s.delivery = ProductDeliveryIntent(targets=["interactive", "pdf"])
    report = validate_product_completeness(s)
    codes = [f.code for f in report.findings if f.code == "export_partial_coverage"]
    assert codes, "面板族在 publication 矢量链缺席 → 如实披露"
    assert report.complete, "warning 不影响 complete"
