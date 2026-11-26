"""地理锚定统计图符表达模型域包（GOAL-20261126-04）。

与既有家族的边界（planning/DEDUP_MATRIX 实证查重）：
- ``chart_panel`` 是浮动面板（屏幕空间 chrome）—— 不是本域；
- ``proportional_symbol`` 是单字段比例圆 —— 本域的退化特例；
- G47 Dorling 是示意坐标纸面图 —— 坐标空间不同。

三模型统一走 ``layer.diagram_symbol``（MapSpec 2.2 additive）+ 几何
oracle（app/lib/cartography/diagram_symbols）+ live GL custom layer，
live/导出同源。诚实状态：runtime_status 在 live 渲染 + 单幅导出链路
实证落地后保持 native（本线交付物即该链路）。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel
from app.lib.cartography.model_packs._base import _QGIS_URL, m

DIAGRAM_SYMBOLS_PACK: List[MapModel] = [
    m(
        id="composition_glyph_map", name_zh="组成图符地图（饼/环形）",
        purpose_zh="逐要素组成比例图符：扇区角 ∝ 类别占比，可选总量面积编码",
        geometry_kinds=["point", "polygon"], maplibre_layer_type="circle",
        classification="none",
        color_scheme_kind="qualitative", default_palette="Set1",
        aliases=["pie_chart_map", "donut_glyph_map", "composition_pie_symbol_map"],
        accepted_artifact_types=["point_feature_set", "polygon_feature_set",
                                 "admin_aggregate_table"],
        recommended_components=["legend"],
        export_compatibility=["png", "svg", "pdf"],
        data_preconditions_zh=[
            "wide 形状：每类别一个数值字段（categories[i] ↔ fields[i] 一一对应）",
            "组成语义要求非负值且分母明确（Σ 参算类别 > 0）；负值 typed 拒绝",
            "总量面积编码开启时跨要素半径 ∝ √总量（面积 ∝ 总量）",
        ],
        pitfalls_zh=[
            "组成比例 ≠ 多序列比较：各 slice 是同一总量的份额，不可跨量纲混装",
            "要素锚定 ≠ 质心即真实位置：面要素用代表点并在位移时披露"
            "（displacement 政策见 diagram_symbols.qualification）",
            "与 chart_panel 浮动饼图（屏幕空间）正交：本模型渲染在地理坐标上",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="multi_series_glyph_map", name_zh="多序列条形图符地图",
        purpose_zh="逐要素同量纲多序列条形（堆叠/并排）：长度线性 ∝ 值，0 基线正负分流",
        geometry_kinds=["point", "polygon", "line"], maplibre_layer_type="circle",
        classification="none",
        color_scheme_kind="qualitative", default_palette="Set2",
        aliases=["stacked_bar_map", "grouped_bar_map", "bar_glyph_map"],
        accepted_artifact_types=["point_feature_set", "polygon_feature_set",
                                 "admin_aggregate_table", "stats_table"],
        recommended_components=["legend"],
        export_compatibility=["png", "svg", "pdf"],
        data_preconditions_zh=[
            "所有类别字段同一量纲（长度比较语义）；unit 未声明时图例如实披露",
            "允许正负值（0 基线：正向上/堆叠向上，负向下）；全零要素无长度语义",
        ],
        pitfalls_zh=[
            "堆叠序 = 类别序；正负分别沿基线上/下堆叠，不可正负混堆",
            "极端跨度（max/min > 10⁴）typed 拒绝 —— 最小条不可辨，先分档或拆层",
            "条形图符的方位（横/竖）是渲染端投影约定，几何合同只产出"
            "glyph-local 坐标（基线 y=0）",
        ],
        sources=[],
    ),
    m(
        id="segmented_proportional_glyph_map", name_zh="等角分段比例图符地图",
        purpose_zh="逐要素等角分段比例符号（夜莺式极区图）：扇区面积 ∝ 值，跨要素可比",
        geometry_kinds=["point", "polygon"], maplibre_layer_type="circle",
        classification="none",
        color_scheme_kind="qualitative", default_palette="Dark2",
        aliases=["nightingale_rose_map", "polar_area_glyph_map",
                 "segmented_proportional_symbol_map"],
        accepted_artifact_types=["point_feature_set", "polygon_feature_set",
                                 "admin_aggregate_table"],
        recommended_components=["legend"],
        export_compatibility=["png", "svg", "pdf"],
        data_preconditions_zh=[
            "wide 形状非负数值字段；v_ref = 全图 (要素,类别) 最大值（公共尺度）",
            "每扇区面积 ∝ value（等角 + 半径 ∝ √value）；跨要素/跨类别可比",
        ],
        pitfalls_zh=[
            "与风玫瑰（wind rose）无关：角度槽位 = 类别顺序索引，不编码任何"
            "方向统计 —— 禁止把方向数据喂给本模型",
            "负值 typed 拒绝（扇区面积不能为负）；零值扇区半径 0（不可见，"
            "有会计）",
            "扇区数 = 类别数（≤64 有界）；类别多时优先聚合长尾再入图",
        ],
        sources=[],
    ),
]
