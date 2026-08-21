"""MapProductTemplate —— 完整地图产品组合的描述层。

一个「地图产品」= recipe（制图方法）+ 图层角色 + 组件 + 输出物。模板
描述**期望的制图成果**，不硬编码工具调用序列（数据/分析能力由
Tool Resolver 在执行期解析）—— 工具替换时模板无需重写。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel


class LayerRoleSpec(BaseModel):
    role: str                      # primary / secondary / reference
    layer_type: str                # heatmap / circle / fill / line / raster
    cartography: str               # visual_heatmap / point_overlay / …
    source_capability: str         # 数据来源能力 id
    description: str = ""


class MapProductTemplate(BaseModel):
    id: str
    name: str
    description: str = ""
    recipe_id: str
    layer_roles: List[LayerRoleSpec]
    # #719: subject tokens this template is specialized for (e.g. education
    # facilities); empty = generic. find_for_recipe prefers a subject match
    # over the generic template so plan evidence never mislabels the product.
    subject_categories: List[str] = []
    default_components: List[str] = []
    outputs: List[str] = ["interactive_map"]
    exports: List[str] = ["png"]
    title_pattern: str = ""        # 如 "{scope}{subject}分布"


SEED_PRODUCT_TEMPLATES: List[MapProductTemplate] = [
    MapProductTemplate(
        # #719: generic default for poi_distribution_overview — find_for_recipe
        # prefers the template whose id equals the recipe id, so the education
        # Golden-case product is only selected via explicit template_id.
        id="poi_distribution_overview",
        name="POI 分布概览产品",
        description="通用 POI 分布概览：热力 + 点叠加 + 色条（主体无关）。",
        recipe_id="poi_distribution_overview",
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="heatmap", cartography="visual_heatmap",
                          source_capability="poi_query", description="主体分布热力"),
            LayerRoleSpec(role="secondary", layer_type="circle", cartography="point_overlay",
                          source_capability="poi_query", description="主体点叠加"),
        ],
        default_components=["title", "continuous_colorbar", "legend", "north_arrow", "scale_bar",
                            "attribution", "statistics_panel"],
        outputs=["interactive_map", "statistics", "summary"],
        exports=["png", "pdf"],
        title_pattern="{scope}{subject}分布",
    ),
    MapProductTemplate(
        id="education_facility_distribution",
        name="教育设施分布产品",
        description="Golden case：学校类 POI 的完整分布产品（热力+点+行政区+统计）。",
        recipe_id="poi_distribution_overview",
        subject_categories=["小学", "中学", "大学", "学校"],
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="heatmap", cartography="visual_heatmap",
                          source_capability="poi_query", description="学校分布热力"),
            LayerRoleSpec(role="secondary", layer_type="circle", cartography="point_overlay",
                          source_capability="poi_query", description="学校点叠加"),
            LayerRoleSpec(role="reference", layer_type="fill", cartography="administrative_choropleth",
                          source_capability="admin_aggregation", description="区县边界/分级统计"),
        ],
        default_components=["title", "continuous_colorbar", "legend", "north_arrow", "scale_bar",
                            "attribution", "statistics_panel"],
        outputs=["interactive_map", "statistics", "summary"],
        exports=["png", "pdf", "csv"],
        title_pattern="{scope}{subject}分布",
    ),
    MapProductTemplate(
        id="poi_density_overview",
        name="POI 密度概览产品",
        description="通用 POI 密度概览：热力 + 点 + 色条。",
        recipe_id="poi_distribution_overview",
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="heatmap", cartography="visual_heatmap",
                          source_capability="poi_query"),
            LayerRoleSpec(role="secondary", layer_type="circle", cartography="point_overlay",
                          source_capability="poi_query"),
        ],
        default_components=["title", "continuous_colorbar", "north_arrow", "scale_bar", "attribution"],
        outputs=["interactive_map", "summary"],
        exports=["png"],
        title_pattern="{scope}{subject}分布",
    ),
    MapProductTemplate(
        id="administrative_statistics_map",
        name="行政统计地图产品",
        description="各区数量/密度统计：choropleth + 统计面板 + CSV。",
        recipe_id="administrative_choropleth",
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="fill", cartography="administrative_choropleth",
                          source_capability="admin_aggregation"),
            LayerRoleSpec(role="secondary", layer_type="circle", cartography="point_overlay",
                          source_capability="poi_query"),
        ],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution", "statistics_panel"],
        outputs=["interactive_map", "statistics", "table", "summary"],
        exports=["png", "pdf", "csv"],
        title_pattern="{scope}{subject}统计",
    ),
    MapProductTemplate(
        id="simple_poi_view",
        name="轻量 POI 点图",
        description="『给我看看』：不过度分析，一张点图。",
        recipe_id="poi_distribution_overview",
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="circle", cartography="simple_point_map",
                          source_capability="poi_query"),
        ],
        default_components=["title", "north_arrow", "scale_bar", "attribution"],
        outputs=["interactive_map"],
        exports=[],
        title_pattern="{scope}{subject}",
    ),
    MapProductTemplate(
        id="grid_density_product",
        name="格网聚合密度产品",
        description="H3/渔网格网分级填色 + 点叠加：比热力可量化的密度表达。",
        recipe_id="grid_density_aggregate",
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="fill", cartography="aggregate_grid",
                          source_capability="grid_binning"),
            LayerRoleSpec(role="secondary", layer_type="circle", cartography="point_overlay",
                          source_capability="poi_query"),
        ],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution"],
        outputs=["interactive_map", "statistics", "summary"],
        exports=["png"],
        title_pattern="{scope}{subject}格网分布",
    ),
    MapProductTemplate(
        id="proportional_symbol_product",
        name="比例符号气泡产品",
        description="圆面积 ∝ sqrt(value) 的权重点图：少样本带权场景的热力替代。",
        recipe_id="proportional_symbol_map",
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="circle", cartography="proportional_symbol",
                          source_capability="poi_query"),
        ],
        default_components=["title", "legend", "north_arrow", "scale_bar", "attribution"],
        outputs=["interactive_map"],
        exports=["png"],
        title_pattern="{scope}{subject}规模分布",
    ),
]


class ProductTemplateRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, MapProductTemplate] = {}

    def load_builtins(self) -> None:
        self._by_id.clear()
        for tpl in SEED_PRODUCT_TEMPLATES:
            self.register(tpl)

    def register(self, tpl: MapProductTemplate) -> None:
        if tpl.id not in self._by_id:
            self._by_id[tpl.id] = tpl

    def get(self, template_id: str) -> Optional[MapProductTemplate]:
        return self._by_id.get(template_id)

    def find_for_recipe(
        self, recipe_id: str, subject_category: str = "",
    ) -> Optional[MapProductTemplate]:
        """Deterministic selection: (1) a subject-specialized template
        matching the intent's subject token, (2) the generic template whose
        id equals the recipe id, (3) first registrant.
        #719: the old first-match returned the education Golden-case template
        for EVERY poi_distribution_overview request, mislabeling plan
        evidence (『成都餐厅分布』 claimed an 教育设施分布产品)."""
        matches = [t for t in self._by_id.values() if t.recipe_id == recipe_id]
        if not matches:
            return None
        if subject_category:
            for tpl in matches:
                if subject_category in tpl.subject_categories:
                    return tpl
        for tpl in matches:
            if tpl.id == recipe_id:
                return tpl
        return matches[0]

    @property
    def all_ids(self) -> List[str]:
        return sorted(self._by_id.keys())


_registry: Optional[ProductTemplateRegistry] = None


def get_product_template_registry() -> ProductTemplateRegistry:
    global _registry
    if _registry is None:
        _registry = ProductTemplateRegistry()
        _registry.load_builtins()
    return _registry


__all__ = [
    "MapProductTemplate",
    "LayerRoleSpec",
    "SEED_PRODUCT_TEMPLATES",
    "ProductTemplateRegistry",
    "get_product_template_registry",
]
