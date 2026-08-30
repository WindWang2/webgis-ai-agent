"""MapProductTemplate —— 完整地图产品组合的描述层。

一个「地图产品」= recipe（制图方法）+ 图层角色 + 组件 + 输出物。模板
描述**期望的制图成果**，不硬编码工具调用序列（数据/分析能力由
Tool Resolver 在执行期解析）—— 工具替换时模板无需重写。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from typing import Dict, List, Optional

from pydantic import BaseModel, model_validator


class LayerRoleSpec(BaseModel):
    """产品模板里的一个图层角色（§16：layer_type 由 MapModel 推导）。

    演进：`map_model` 是制图模型 id（缺省取 `cartography`）；`layer_type`
    变为可选——显式给出时必须与 MapModelRegistry 的
    ``maplibre_layer_type``（或几何多态映射）一致，validation suite 锁定，
    禁止以后手写漂移。`source_artifact` 声明该角色消费的 artifact 语义
    类型；`style_slot` 引用样式模板 id（TemplateCatalog 校验存在性）。
    """
    role: str                      # primary / secondary / reference
    cartography: str               # visual_heatmap / point_overlay / …（MapModel id/别名）
    layer_type: str = ""           # 可选；缺省由 MapModel 推导
    map_model: str = ""            # 缺省 = cartography
    source_capability: str = ""    # 数据来源能力 id
    source_artifact: str = ""      # 消费的 artifact 语义类型
    style_slot: str = ""           # 样式模板 id（可选）
    description: str = ""

    @property
    def resolved_map_model(self) -> str:
        return self.map_model or self.cartography


class MapProductTemplate(BaseModel):
    id: str
    name: str
    description: str = ""
    recipe_id: str
    layer_roles: List[LayerRoleSpec]
    subject_categories: List[str] = []
    task_affinity: List[str] = []
    default_components: List[str] = []
    outputs: List[str] = ["interactive_map"]
    exports: List[str] = ["png"]
    title_pattern: str = ""
    compatible_map_models: List[str] = []
    priority: int = 50
    template_version: str = "1.0"
    deprecated: bool = False
    composition_template_id: str = ""
    component_overrides: Dict[str, object] = {}
    component_requirements: Dict[str, str] = {}
    # v2（P7）：产品原型归属 —— 模板是「原型 + 组合 + 组件」的实例化，
    # 差异应来自 subject/参数/组件构成而非新模板。固定词表：
    #   distribution_overview   点/事件/POI 分布概览（热力+点+统计）
    #   regional_comparison     行政区对比（choropleth+排名图+统计）
    #   density_analysis        密度/格网分析（heatmap/grid+colorbar）
    #   remote_sensing          遥感栅格产品（raster+colorbar+图框）
    #   simple_view             轻量浏览（仅点图+导航件）
    #   proportional_symbol     比例符号权重点图
    archetype: str = ""

    @model_validator(mode="after")
    def _derive_compatible_map_models(self) -> "MapProductTemplate":
        if not self.compatible_map_models:
            models: List[str] = []
            for role in self.layer_roles:
                mm = role.resolved_map_model
                if mm not in models:
                    models.append(mm)
            self.compatible_map_models = models
        return self


# 原型词表（v2 §10）：新模板必须归属其一（regression guard 锁定 ——
# 防垂直模板重新膨胀：school/hospital/earthquake 级差异属于 subject
# 参数，不是新模板）。
PRODUCT_ARCHETYPES = (
    "distribution_overview",
    "regional_comparison",
    "density_analysis",
    "remote_sensing",
    "simple_view",
    "proportional_symbol",
)


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
        composition_template_id="composition.density_map",
        archetype="distribution_overview",
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
        composition_template_id="composition.density_map",
        archetype="distribution_overview",
    ),
    MapProductTemplate(
        # P7（模板/配方整合）：与 poi_distribution_overview 结构完全相同
        # （同 recipe、同 composition、同 layer_roles），仅组件集略少 ——
        # 组件差异属于 composition 模板的职责，不是另一个产品模板。
        # 零外部引用（仅显式 template_id 可达）；标记 deprecated 并保留
        # 注册：旧持久会话的 template_selection.composition_template_id
        # 引用仍可解析（迁移兼容），selector 不再把它选进新计划。
        id="poi_density_overview",
        name="POI 密度概览产品（已并入 poi_distribution_overview）",
        description="deprecated：组件差异走 composition 模板，不再单独成模板。",
        recipe_id="poi_distribution_overview",
        deprecated=True,
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
        composition_template_id="composition.density_map",
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
        composition_template_id="composition.statistical_map",
        archetype="regional_comparison",
    ),
    MapProductTemplate(
        id="simple_poi_view",
        name="轻量 POI 点图",
        description="『给我看看』：不过度分析，一张点图。",
        recipe_id="poi_distribution_overview",
        task_affinity=["simple_view"],
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="circle", cartography="simple_point_map",
                          source_capability="poi_query"),
        ],
        default_components=["title", "north_arrow", "scale_bar", "attribution"],
        outputs=["interactive_map"],
        exports=[],
        title_pattern="{scope}{subject}",
        composition_template_id="composition.minimal_interactive",
        archetype="simple_view",
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
        composition_template_id="composition.statistical_map",
        archetype="density_analysis",
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
        composition_template_id="composition.standard_analysis",
        archetype="proportional_symbol",
    ),
    MapProductTemplate(
        # v2（P7 archetype）：遥感栅格产品 —— raster_distribution 配方的
        # 产品化接线（此前配方无产品模板，composition.remote_sensing_map
        # 不可达）。栅格 + 连续色条 + 图框 + 版式。
        id="remote_sensing_product",
        name="遥感栅格产品",
        description="栅格面/影像 + 连续色条 + 图框 + 导出版式（遥感原型）。",
        recipe_id="raster_distribution",
        archetype="remote_sensing",
        layer_roles=[
            LayerRoleSpec(role="primary", layer_type="raster", cartography="raster_surface",
                          source_capability="raster_source", description="栅格面/影像"),
        ],
        default_components=["title", "continuous_colorbar", "north_arrow", "scale_bar",
                            "attribution", "map_border", "export_layout"],
        outputs=["interactive_map", "summary"],
        exports=["png", "pdf"],
        title_pattern="{scope}{subject}栅格产品",
        composition_template_id="composition.remote_sensing_map",
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
        if tpl.id in self._by_id:
            # #1075(D-2): 静默跳过 → 显式告警。
            logger.warning("product template %r 重复注册：保留既有条目", tpl.id)
            return
        self._by_id[tpl.id] = tpl

    def get(self, template_id: str) -> Optional[MapProductTemplate]:
        return self._by_id.get(template_id)

    def values(self) -> List[MapProductTemplate]:
        """注册序全量视图（TemplateCatalog 等门面消费）。"""
        return list(self._by_id.values())

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
        # P7：兜底序优先非 deprecated（deprecated 仅作旧会话兼容解析，
        # 不进新计划）。
        live = [t for t in matches if not t.deprecated]
        return (live or matches)[0]

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
