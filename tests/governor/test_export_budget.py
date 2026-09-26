"""F08 / ADR-0214 D7：ExportWork 契约与 render/export 窄 seam。

验收面：
- 公式确定性 + 单调性（页/层/像素/图表递增 → 估工不減）；
- 输入有界性（页数/图层数钳制）与防御式（负值钳 0、缺省单页）；
- estimate_export：EXPORT 通道归因（resource_class=EXPORT）；
- export_work_from_summary：宽松键投影（消费方传什么算什么）；
- DPI 面积律上限（dpi_scale ≤ 4）。
"""
from __future__ import annotations

import pytest

from app.services.governor.contract import (
    Dimension,
    ResourceClass,
    Subsystem,
)
from app.services.governor.export_budget import (
    FORMULA_VERSION,
    ExportWork,
    estimate_export,
    export_work_from_summary,
    export_work_units,
)


def test_formula_deterministic():
    w = ExportWork(page_count=3, vector_layers=5, raster_pixels=1000,
                   feature_count=100, chart_count=1,
                   pixel_width=800, pixel_height=600, dpi=150.0)
    assert export_work_units(w) == export_work_units(w)


@pytest.mark.parametrize("mutate", [
    lambda w: setattr(w, "page_count", w.page_count + 1),
    lambda w: setattr(w, "vector_layers", w.vector_layers + 1),
    lambda w: setattr(w, "raster_pixels", w.raster_pixels + 1000),
    lambda w: setattr(w, "feature_count", w.feature_count + 100),
    lambda w: setattr(w, "chart_count", w.chart_count + 1),
    lambda w: setattr(w, "pixel_height", w.pixel_height + 100),
])
def test_formula_monotonic(mutate):
    base = ExportWork(page_count=2, vector_layers=3, raster_pixels=5000,
                      feature_count=200, chart_count=1,
                      pixel_width=800, pixel_height=600)
    bigger = ExportWork(page_count=2, vector_layers=3, raster_pixels=5000,
                        feature_count=200, chart_count=1,
                        pixel_width=800, pixel_height=600)
    mutate(bigger)
    assert export_work_units(bigger) >= export_work_units(base)


def test_bounded_inputs_and_defensive_defaults():
    w = ExportWork(page_count=10**9, vector_layers=10**9)
    assert w.page_count <= 512
    assert w.vector_layers <= 256
    empty = ExportWork()
    assert empty.page_count == 1
    assert export_work_units(ExportWork(page_count=-5)) >= 0.0


def test_estimate_export_routes_export_channel():
    est = estimate_export(ExportWork(page_count=4, pixel_width=1000,
                                     pixel_height=800))
    assert est.subsystem is Subsystem.EXPORT
    assert est.resource_class is ResourceClass.EXPORT
    assert FORMULA_VERSION in est.source
    wall = est.dim(Dimension.WALL_TIME_S)
    mem = est.dim(Dimension.MEMORY_BYTES)
    assert wall.expected > 0 and mem.expected > 0
    # range 语义：min ≤ expected ≤ max
    assert wall.min <= wall.expected <= wall.max
    # DPI 面积律有界（≤4×）
    big_dpi = estimate_export(ExportWork(page_count=1, pixel_width=100,
                                         pixel_height=100, dpi=4800.0))
    small_dpi = estimate_export(ExportWork(page_count=1, pixel_width=100,
                                           pixel_height=100, dpi=None))
    ratio = (big_dpi.dim(Dimension.RENDER_WORK_UNITS).expected
             / small_dpi.dim(Dimension.RENDER_WORK_UNITS).expected)
    assert ratio <= 4.0 + 1e-6


def test_summary_projection_lenient_keys():
    w = export_work_from_summary({
        "pages": 7, "layers": 3, "raster_pixels": 900,
        "features": 42, "charts": 2, "width": 640, "height": 480,
        "dpi": 200,
    })
    assert (w.page_count, w.vector_layers, w.raster_pixels) == (7, 3, 900)
    assert (w.feature_count, w.chart_count) == (42, 2)
    assert (w.pixel_width, w.pixel_height) == (640, 480)
    assert w.dpi == 200.0
    # 非法/缺省输入 → 单页空导出（不抛）
    junk = export_work_from_summary("not-a-dict")
    assert junk.page_count == 1
    assert export_work_from_summary({"dpi": "x"}).dpi is None


def test_workflow_seam_export_node_declares_export_work():
    """workflow 节点 resources.export 申报 → 走同一 governor 通道语义。"""
    from app.services.workflow_runtime.estimate import (
        resource_estimate_for_workflow_node,
    )

    node = {"node_id": "e", "kind": "export",
            "resources": {"export": {"pages": 2, "width": 500,
                                     "height": 400}}}
    est = resource_estimate_for_workflow_node(node)
    assert est.resource_class is ResourceClass.EXPORT
    assert est.subsystem is Subsystem.WORKFLOW  # 归因 workflow，通道 EXPORT
    assert est.dim(Dimension.RENDER_WORK_UNITS).is_meaningful()
