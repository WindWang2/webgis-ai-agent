"""Publication Cases v1 —— 六类出版用例共享定义（ADR-0283 D5）.

六族 × zh|en × 16:9|A4|A3 的确定性用例矩阵：
1. dot_density_district —— 城市学校点密度 + 区级统计面板（密集组件画幅）
2. choropleth_graduated —— 分级设色 + 离散图例（长类目）
3. continuous_raster_colorbar —— 连续栅格 + 色条 + 单位
4. long_name_labels —— 长名称行政区标注（zh 长中文 / en 长拉丁）
5. comparison_two_views —— 双图对比（atlas 双帧）
6. research_figure_inset_chart —— 研究图件（inset + 图表 + 注记 + 方法论）

每用例产出：参与者（solver 输入语义）+ 出版版面输入（标题/副标题/署名）+
期望不变量（key 组件不抑制、标题 wrap-not-clip、披露码集）。纯数据模块，
generate.py 与 test_publication_cases_v1.py 共同消费。
"""
from __future__ import annotations

from typing import Any, Dict, List

#: 页面档（16:9 电子屏 / A4 / A3，横版；与 LayoutInput paper_size 对齐）。
PAGE_PROFILES: List[Dict[str, Any]] = [
    {"key": "16x9", "paper_size": "screen", "orientation": "landscape",
     "dpi": 96, "frame": {"width": 1920, "height": 1080}},
    {"key": "a4", "paper_size": "A4", "orientation": "landscape",
     "dpi": 300, "frame": {"width": 3508, "height": 2480}},
    {"key": "a3", "paper_size": "A3", "orientation": "landscape",
     "dpi": 300, "frame": {"width": 4961, "height": 3508}},
]

LOCALES = ("zh", "en")

LONG_TITLE_ZH = (
    "二〇二六年秋季华北平原城市群基础教育资源空间分异研究：基于多源地理开放数据"
    "的区县级统计与择校可达性综合评估报告（覆盖京津冀鲁豫五省市一百零二个区县、"
    "四千三百余所小学与初级中学的分布、师资生均比、择校通勤可达性与学区房价梯度"
    "的联合分析，并给出分省市政策建议附录）"
)
LONG_TITLE_EN = (
    "Spatial Differentiation of Basic Education Resources across the "
    "North China Plain Urban Agglomeration in Autumn 2026: A County-Level "
    "Statistical and Accessibility Assessment Covering Five Provinces"
)


def _p(pid: str, ptype: str, zone: str, *, priority: int = 50,
        optional: bool = True, width_units: int = 1,
        collapsible: bool = False) -> Dict[str, Any]:
    return {
        "id": pid, "type": ptype, "requested_zone": zone,
        "priority": priority, "optional": optional,
        "width_units": width_units, "collapsible": collapsible,
    }


def case_participants(case: str) -> List[Dict[str, Any]]:
    """六族参与者定义（solve_layout_v5 输入语义）。"""
    if case == "dot_density_district":
        return [
            _p("title", "title", "top-center", priority=1, optional=False),
            _p("subtitle", "subtitle", "top-center", priority=2, optional=False),
            _p("stats", "statistics_panel", "top-left", priority=20, width_units=2),
            _p("chart", "chart_panel", "top-left", priority=21, width_units=2),
            _p("legend_schools", "categorical_legend", "bottom-left", priority=11),
            _p("legend_districts", "legend", "bottom-left", priority=12),
            _p("scale", "scale_bar", "bottom-right", priority=5),
            _p("north", "north_arrow", "top-right", priority=6),
            _p("attr", "attribution", "bottom-right", priority=7),
            _p("method", "methodology_note", "bottom-left", priority=8),
        ]
    if case == "choropleth_graduated":
        return [
            _p("title", "title", "top-center", priority=1, optional=False),
            _p("legend_classes", "legend", "bottom-left", priority=11, width_units=2),
            _p("legend_top", "categorical_legend", "bottom-left", priority=12),
            _p("scale", "scale_bar", "bottom-right", priority=5),
            _p("north", "north_arrow", "top-right", priority=6),
            _p("attr", "attribution", "bottom-right", priority=7),
            _p("unc", "uncertainty_panel", "bottom-right", priority=13),
        ]
    if case == "continuous_raster_colorbar":
        return [
            _p("title", "title", "top-center", priority=1, optional=False),
            _p("colorbar", "continuous_colorbar", "bottom-right", priority=10, width_units=2),
            _p("scale", "scale_bar", "bottom-right", priority=5),
            _p("north", "north_arrow", "top-right", priority=6),
            _p("attr", "attribution", "bottom-right", priority=7),
            _p("method", "methodology_note", "bottom-left", priority=8),
        ]
    if case == "long_name_labels":
        return [
            _p("title", "title", "top-center", priority=1, optional=False),
            _p("labels", "label_layer", "none", priority=30),
            _p("legend_admin", "legend", "bottom-left", priority=11),
            _p("scale", "scale_bar", "bottom-right", priority=5),
            _p("north", "north_arrow", "top-right", priority=6),
            _p("attr", "attribution", "bottom-right", priority=7),
        ]
    if case == "comparison_two_views":
        # 双帧：帧 A 摘要组件全集，帧 B 对照精简（同一求解语义分别求）
        return [
            _p("title", "title", "top-center", priority=1, optional=False),
            _p("legend_a", "legend", "bottom-left", priority=11),
            _p("legend_b", "legend", "bottom-left", priority=12),
            _p("colorbar_b", "continuous_colorbar", "bottom-right", priority=10),
            _p("scale", "scale_bar", "bottom-right", priority=5),
            _p("north", "north_arrow", "top-right", priority=6),
            _p("attr", "attribution", "bottom-right", priority=7),
        ]
    if case == "research_figure_inset_chart":
        return [
            _p("title", "title", "top-center", priority=1, optional=False),
            _p("subtitle", "subtitle", "top-center", priority=2, optional=False),
            _p("inset", "inset_map", "top-right", priority=3),
            _p("chart", "chart_panel", "top-left", priority=21, width_units=2),
            _p("stats", "statistics_panel", "top-left", priority=20),
            _p("annotation", "annotation", "top-left", priority=22),
            _p("method", "methodology_note", "bottom-left", priority=8),
            _p("legend", "legend", "bottom-left", priority=11),
            _p("scale", "scale_bar", "bottom-right", priority=5),
            _p("north", "north_arrow", "top-right", priority=6),
            _p("attr", "attribution", "bottom-right", priority=7),
        ]
    raise KeyError(case)


def case_texts(case: str, locale: str) -> Dict[str, str]:
    """标题/副标题/署名（locale 维度；long_name/研究图件为长文本族）。"""
    long_title = LONG_TITLE_ZH if locale == "zh" else LONG_TITLE_EN
    if case in ("long_name_labels", "research_figure_inset_chart"):
        return {"title": long_title,
                "subtitle": "区县级统计口径 · 2026" if locale == "zh"
                else "County-level statistics, 2026",
                "attribution": "© webgis-ai-agent 开放数据" if locale == "zh"
                else "© webgis-ai-agent open data"}
    names = {
        "dot_density_district": ("学校点密度与区级统计", "School Density & District Statistics"),
        "choropleth_graduated": ("分级设色图", "Graduated Choropleth"),
        "continuous_raster_colorbar": ("连续栅格分布", "Continuous Raster Distribution"),
        "comparison_two_views": ("双图对比", "Two-View Comparison"),
    }
    zh, en = names[case]
    return {"title": zh if locale == "zh" else en,
            "subtitle": "2026 年秋季" if locale == "zh" else "Autumn 2026",
            "attribution": "© webgis-ai-agent" }


#: 期望不变量（逐 case；golden 之外的语义断言）。
EXPECTED_KEYS = {
    # 每 case 的 key 组件（缺省派生给出的 must_fit 集）永远不出现在 suppressed
    "dot_density_district": {"title", "attr", "method", "subtitle"},
    "choropleth_graduated": {"title", "attr", "unc"},
    "continuous_raster_colorbar": {"title", "attr", "method"},
    "long_name_labels": {"title", "attr"},
    "comparison_two_views": {"title", "attr"},
    "research_figure_inset_chart": {"title", "attr", "method", "subtitle"},
}

def case_constraint_overrides(case: str) -> Dict[str, Dict[str, object]]:
    """旗舰用例的**显式**约束声明（derive 缺省之上的出版意图覆写）。

    R9 P1-1 语义下缺省派生 = V4 行为（不强制 key 存活）；旗舰研究图件
    的出版意图是注记（annotation）必须保留 —— 显式 must_fit 展示约束
    系统的用法（显式 > 缺省），而非依赖隐式强制。
    """
    if case == "research_figure_inset_chart":
        return {
            "annotation": {"must_fit": True, "no_truncate": True},
            "subtitle": {"must_fit": True},
        }
    return {}


#: 每 case 允许出现的约束发现码（closed 子集；超出即回归）。
ALLOWED_FINDING_CODES = {
    "dot_density_district": {"max_width_clamped", "overflow_suppressed",
                             "constraint_unmatched"},
    "choropleth_graduated": {"max_width_clamped", "overflow_suppressed"},
    "continuous_raster_colorbar": {"max_width_clamped"},
    "long_name_labels": set(),
    "comparison_two_views": {"max_width_clamped"},
    "research_figure_inset_chart": {"max_width_clamped", "overflow_suppressed"},
}

CASES = tuple(EXPECTED_KEYS.keys())
