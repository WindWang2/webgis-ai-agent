"""Map Component Taxonomy — 地图组件分类体系.

将原本 ``ComponentType = Literal[...]`` 的扁平枚举升级为
机器可读的层级分类体系，供 registry / composition / QA 复用。
"""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

PlacementDomain = Literal["layer", "overlay", "chrome", "panel", "export", "interaction"]
OutputTarget = Literal["interactive", "png", "pdf", "svg", "print"]


class ComponentCategory(BaseModel):
    id: str
    name: str
    name_zh: str = ""
    description: str = ""
    parent: Optional[str] = None
    placement_domain: PlacementDomain = "overlay"
    output_targets: List[str] = Field(default_factory=list)
    children: List[str] = Field(default_factory=list)


_SEED_CATEGORIES: List[ComponentCategory] = [
    # ── top level ──────────────────────────────────────────────────
    ComponentCategory(
        id="content", name="content", name_zh="地图内容", description="MapContent components; may map to MapSpec sources/layers.",
        placement_domain="layer", output_targets=["interactive", "png", "pdf", "svg"],
    ),
    ComponentCategory(
        id="legend", name="legend", name_zh="图例", description="Legend family",
        placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"],
    ),
    ComponentCategory(
        id="navigation", name="navigation", name_zh="导航辅助", description="North arrow / scale / graticule",
        placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"],
    ),
    ComponentCategory(
        id="annotation", name="annotation", name_zh="注记", description="Titles, text, attribution",
        placement_domain="chrome", output_targets=["interactive", "png", "pdf", "svg"],
    ),
    ComponentCategory(
        id="frame", name="frame", name_zh="图框", description="Border / neatline / background",
        placement_domain="chrome", output_targets=["png", "pdf", "svg"],
    ),
    ComponentCategory(
        id="analysis", name="analysis", name_zh="分析展示", description="Statistics / charts",
        placement_domain="panel", output_targets=["interactive", "png", "pdf"],
    ),
    ComponentCategory(
        id="inset", name="inset", name_zh="插图", description="Overview / location insets",
        placement_domain="overlay", output_targets=["interactive", "png", "pdf"],
    ),
    ComponentCategory(
        id="interaction", name="interaction", name_zh="交互", description="Web interaction",
        placement_domain="interaction", output_targets=["interactive"],
    ),
    ComponentCategory(
        id="export", name="export", name_zh="输出布局", description="Page layout, DPI, margins",
        placement_domain="export", output_targets=["png", "pdf", "svg", "print"],
    ),
    # ── content sub ────────────────────────────────────────────────
    ComponentCategory(id="content.basemap", name="content.basemap", name_zh="底图", parent="content", placement_domain="layer", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="content.thematic_layer", name="content.thematic_layer", name_zh="专题图层", parent="content", placement_domain="layer", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="content.reference_layer", name="content.reference_layer", name_zh="参考图层", parent="content", placement_domain="layer", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="content.overlay_layer", name="content.overlay_layer", name_zh="叠加图层", parent="content", placement_domain="layer", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="content.label_layer", name="content.label_layer", name_zh="标注图层", parent="content", placement_domain="layer", output_targets=["interactive", "png", "pdf", "svg"]),
    # ── legend sub ─────────────────────────────────────────────────
    ComponentCategory(id="legend.graduated", name="legend.graduated", name_zh="分级图例", parent="legend", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="legend.categorical", name="legend.categorical", name_zh="分类图例", parent="legend", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="legend.continuous_colorbar", name="legend.continuous_colorbar", name_zh="连续色条", parent="legend", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="legend.bivariate", name="legend.bivariate", name_zh="双变量图例", parent="legend", placement_domain="overlay", output_targets=["interactive", "png", "pdf"]),
    ComponentCategory(id="legend.symbol", name="legend.symbol", name_zh="符号图例", parent="legend", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="legend.size", name="legend.size", name_zh="大小图例", parent="legend", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    # ── navigation sub ─────────────────────────────────────────────
    ComponentCategory(id="navigation.north_arrow", name="navigation.north_arrow", name_zh="指北针", parent="navigation", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="navigation.scale_bar", name="navigation.scale_bar", name_zh="比例尺", parent="navigation", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="navigation.graticule", name="navigation.graticule", name_zh="经纬网", parent="navigation", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="navigation.coordinate_grid", name="navigation.coordinate_grid", name_zh="坐标格网", parent="navigation", placement_domain="overlay", output_targets=["interactive", "png", "pdf", "svg"]),
    # ── annotation sub ─────────────────────────────────────────────
    ComponentCategory(id="annotation.title", name="annotation.title", name_zh="标题", parent="annotation", placement_domain="chrome", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="annotation.subtitle", name="annotation.subtitle", name_zh="副标题", parent="annotation", placement_domain="chrome", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="annotation.text", name="annotation.text", name_zh="文本注记", parent="annotation", placement_domain="chrome", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="annotation.callout", name="annotation.callout", name_zh="引线注记", parent="annotation", placement_domain="chrome", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="annotation.data_source", name="annotation.data_source", name_zh="数据来源", parent="annotation", placement_domain="chrome", output_targets=["png", "pdf", "svg"]),
    ComponentCategory(id="annotation.attribution", name="annotation.attribution", name_zh="版权信息", parent="annotation", placement_domain="chrome", output_targets=["interactive", "png", "pdf", "svg"]),
    ComponentCategory(id="annotation.metadata", name="annotation.metadata", name_zh="元数据块", parent="annotation", placement_domain="chrome", output_targets=["png", "pdf"]),
    # ── frame sub ──────────────────────────────────────────────────
    ComponentCategory(id="frame.map_border", name="frame.map_border", name_zh="图框边框", parent="frame", placement_domain="chrome", output_targets=["png", "pdf", "svg"]),
    ComponentCategory(id="frame.neatline", name="frame.neatline", name_zh="内图廓", parent="frame", placement_domain="chrome", output_targets=["png", "pdf", "svg"]),
    ComponentCategory(id="frame.background", name="frame.background", name_zh="背景", parent="frame", placement_domain="chrome", output_targets=["png", "pdf", "svg"]),
    ComponentCategory(id="frame.margin", name="frame.margin", name_zh="页边距", parent="frame", placement_domain="export", output_targets=["pdf", "print"]),
    ComponentCategory(id="frame.inset_frame", name="frame.inset_frame", name_zh="插图框", parent="frame", placement_domain="chrome", output_targets=["png", "pdf", "svg"]),
    # ── analysis sub ───────────────────────────────────────────────
    ComponentCategory(id="analysis.statistics_panel", name="analysis.statistics_panel", name_zh="统计面板", parent="analysis", placement_domain="panel", output_targets=["interactive", "png", "pdf"]),
    ComponentCategory(id="analysis.chart_panel", name="analysis.chart_panel", name_zh="图表面板", parent="analysis", placement_domain="panel", output_targets=["interactive", "png", "pdf"]),
    ComponentCategory(id="analysis.summary", name="analysis.summary", name_zh="摘要", parent="analysis", placement_domain="panel", output_targets=["interactive", "png", "pdf"]),
    ComponentCategory(id="analysis.indicator", name="analysis.indicator", name_zh="指标卡", parent="analysis", placement_domain="panel", output_targets=["interactive", "png", "pdf"]),
    # ── inset sub ──────────────────────────────────────────────────
    ComponentCategory(id="inset.map", name="inset.map", name_zh="插图", parent="inset", placement_domain="overlay", output_targets=["interactive", "png", "pdf"]),
    ComponentCategory(id="inset.overview_map", name="inset.overview_map", name_zh="总览图", parent="inset", placement_domain="overlay", output_targets=["interactive", "png", "pdf"]),
    ComponentCategory(id="inset.location_map", name="inset.location_map", name_zh="区位图", parent="inset", placement_domain="overlay", output_targets=["interactive", "png", "pdf"]),
    # ── interaction sub ────────────────────────────────────────────
    ComponentCategory(id="interaction.layer_switcher", name="interaction.layer_switcher", name_zh="图层切换器", parent="interaction", placement_domain="interaction", output_targets=["interactive"]),
    ComponentCategory(id="interaction.zoom_control", name="interaction.zoom_control", name_zh="缩放控件", parent="interaction", placement_domain="interaction", output_targets=["interactive"]),
    ComponentCategory(id="interaction.fullscreen", name="interaction.fullscreen", name_zh="全屏", parent="interaction", placement_domain="interaction", output_targets=["interactive"]),
    ComponentCategory(id="interaction.popup", name="interaction.popup", name_zh="弹窗", parent="interaction", placement_domain="interaction", output_targets=["interactive"]),
    ComponentCategory(id="interaction.search", name="interaction.search", name_zh="搜索", parent="interaction", placement_domain="interaction", output_targets=["interactive"]),
    # ── export sub ─────────────────────────────────────────────────
    ComponentCategory(id="export.page_layout", name="export.page_layout", name_zh="页面版式", parent="export", placement_domain="export", output_targets=["pdf", "print"]),
    ComponentCategory(id="export.page_size", name="export.page_size", name_zh="纸张尺寸", parent="export", placement_domain="export", output_targets=["pdf", "print"]),
    ComponentCategory(id="export.orientation", name="export.orientation", name_zh="方向", parent="export", placement_domain="export", output_targets=["pdf", "print"]),
    ComponentCategory(id="export.dpi", name="export.dpi", name_zh="分辨率", parent="export", placement_domain="export", output_targets=["png", "pdf", "print"]),
    ComponentCategory(id="export.margin", name="export.margin", name_zh="边距", parent="export", placement_domain="export", output_targets=["pdf", "print"]),
    ComponentCategory(id="export.footer", name="export.footer", name_zh="页脚", parent="export", placement_domain="export", output_targets=["pdf", "print"]),
    ComponentCategory(id="export.metadata_block", name="export.metadata_block", name_zh="元数据块", parent="export", placement_domain="export", output_targets=["pdf", "print"]),
]


class ComponentCategoryRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, ComponentCategory] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for cat in _SEED_CATEGORIES:
            self._by_id[cat.id] = cat
        # populate children
        for cat in _SEED_CATEGORIES:
            if cat.parent and cat.parent in self._by_id:
                parent = self._by_id[cat.parent]
                if cat.id not in parent.children:
                    parent.children.append(cat.id)

    def get(self, category_id: str) -> Optional[ComponentCategory]:
        return self._by_id.get(category_id)

    def has(self, category_id: str) -> bool:
        return category_id in self._by_id

    def children_of(self, category_id: str) -> List[ComponentCategory]:
        cat = self._by_id.get(category_id)
        if not cat:
            return []
        return [self._by_id[cid] for cid in cat.children if cid in self._by_id]

    def is_descendant(self, candidate: str, ancestor: str) -> bool:
        cur = self._by_id.get(candidate)
        while cur and cur.parent:
            if cur.parent == ancestor:
                return True
            cur = self._by_id.get(cur.parent)
        return candidate == ancestor

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())

    @property
    def count(self) -> int:
        return len(self._by_id)

    @property
    def top_level_ids(self) -> List[str]:
        return sorted(c.id for c in self._by_id.values() if c.parent is None)

    def validate(self) -> List[str]:
        issues: List[str] = []
        for cat in self._by_id.values():
            if cat.parent and cat.parent not in self._by_id:
                issues.append(f"category {cat.id}: parent {cat.parent} not found")
        return issues


_registry: Optional[ComponentCategoryRegistry] = None


def get_component_category_registry() -> ComponentCategoryRegistry:
    global _registry
    if _registry is None:
        _registry = ComponentCategoryRegistry()
        _registry.load_builtins()
    return _registry


def reset_component_category_registry() -> None:
    global _registry
    _registry = None


__all__ = [
    "ComponentCategory",
    "ComponentCategoryRegistry",
    "get_component_category_registry",
    "reset_component_category_registry",
    "_SEED_CATEGORIES",
]
