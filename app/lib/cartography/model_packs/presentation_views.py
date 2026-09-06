"""Presentation / 多视图表达模型域包（Design System V4）。

诚实 planned 族：这些模型需要「多画幅组合」运行时语义（多个 MapSpec
画面的并置/联动），超出单画布 MapSpec 的表达能力 —— 在运行时落地前
保持 planned 并登记原因，不伪装 native。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel
from app.lib.cartography.model_packs._base import _QGIS_URL, m

PRESENTATION_VIEWS_PACK: List[MapModel] = [
    m(
        id="small_multiple_map", name_zh="小倍数地图组",
        purpose_zh="同一表达跨多个属性/时期的并置小图（比较形态学）",
        geometry_kinds=["polygon", "point", "raster"],
        maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Blues",
        runtime_status="planned",
        accepted_artifact_types=["admin_aggregate_table", "grid_aggregate"],
        qgis_renderer="atlas / 多版面",
        pitfalls_zh=[
            "planned：需要多画幅组合运行时（多个 MapSpec 画面的并置/联动"
            "布局），当前单画布 MapSpec 无法承载 —— 诚实保留 planned",
            "落地前替代：temporal_comparison_map（双期）或导出侧多次出图",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="cartogram_map", name_zh="统计地图变形（cartogram）",
        purpose_zh="面单元按数值变形面积/形状的 cartogram 表达",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="YlOrRd",
        runtime_status="planned",
        accepted_artifact_types=["admin_aggregate_table"],
        qgis_renderer="cartogram 插件",
        pitfalls_zh=[
            "planned：需要面积保持变形算法（Gastner-Newman 扩散等）与"
            "变形后几何的渲染契约，本分支未实现",
            "变形图必须同时披露原始地理轮廓参照（inset），否则读者失去"
            "地理定位",
        ],
        sources=[_QGIS_URL],
    ),
]
