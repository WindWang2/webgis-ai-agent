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
        # G15 native 化的实证组合 = polygon/point（frame_product.v1 全链：
        # 真实数据桶计划 → CAS 绑定 → 逐帧有效文档 → 共享分级 → 同页
        # 出版 PNG/SVG/PDF，见 tests/cartography/test_frame_product_*）。
        # raster 未实证 —— 不入 kinds，诚实留在 pitfalls。
        geometry_kinds=["polygon", "point"],
        maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Blues",
        runtime_status="native",
        accepted_artifact_types=["admin_aggregate_table", "grid_aggregate"],
        qgis_renderer="atlas / 多版面",
        pitfalls_zh=[
            "native 前提：MapSpec frame_product.v1 契约（frame_product_bind"
            "建立画幅身份与数据绑定）—— 无契约时仍是单画布，不自动并置",
            "共享色带是资格契约：字段含义/单位/分母/测量语义不一致（如计数"
            "对率）拒绝共享，回落独立图例并披露",
            "raster 图层组合未实证（帧过滤语义只覆盖内联 geojson）—— 栅格"
            "多画幅仍走 V5 ExportRequest.frames 导出面，诚实降级",
            "客户端 canvas PNG/SVG 网格不承载 layerOverrides/纸型/共享图例带"
            "—— 精确语义走后端 publication 产品页（atlas.driver=product）",
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
            "V5（ADR-0118 D8）：导出请求携带 cartogram 意图时诚实降级"
            "（cartogram_unsupported，按未变形几何渲染）—— 不伪造变形支持",
        ],
        sources=[_QGIS_URL],
    ),
]
