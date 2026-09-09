"""GIS Task Taxonomy V2 —— 20 类任务分类学（Epic 11 §5.A）。

回答「用户说的问题是哪一类 GIS 问题」：把 Epic 定义的 20 个任务类目
（稳定词表，纯加法演进）与既有 canonical 词表对齐：

    category → ontology tasks（gis_ontology，对齐而非复制）
             → methodology families（workflow_v4，透镜引用）
             → invalid / alternative methods（method 词表引用，校验）
             → recommended visualizations（MapModel 引用，校验）
             → required / optional components（ComponentRegistry 引用）

红线（架构 01-architecture §0/§3.1）：

- **不是第二任务注册表**：类的数据需求（角色/几何/产物）在载入期从
  ontology 成员任务**投影派生**（单一下放：ontology 是数据需求唯一
  事实源），本模块不手写；
- **不新建关键词表**（R1-F6）：类的 lexical 证据 = 成员 ontology 任务
  keywords ∪ 成员 methodology family keywords 的投影；V4 路由裁决
  优先，本分类的匹配仅作 category 级证据（tie-break、abstention 判断）；
- invalid_method_ids 只收「类别级经典错误替换」（教科书级混淆，逐条
  经 case corpus 负例锚定）；数据条件性拒绝（无点数据选 KDE 等）
  归 qualification 引擎（按事实裁决），不进本表；
- 全部引用经 ``validate()`` 对账（注入谓词，悬空 fatal）；
  零 LLM、零 I/O、确定性。

消费方：ranking（taxonomy match 分量）、graph（serves_category 边）、
KnowledgeService（classify/explain）、template planner（组件期望）。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, field_validator

#: 分类学 schema 版本（进指纹）。
TAXONOMY_SCHEMA_VERSION = 2

#: 20 个 GIS 任务类目（稳定词表，纯加法演进；Epic §5.A 全覆盖）。
GIS_TASK_CATEGORIES = (
    "spatial_distribution",
    "density",
    "administrative_aggregation",
    "proximity",
    "accessibility_network",
    "hotspot",
    "clustering",
    "interpolation",
    "suitability",
    "overlay",
    "terrain",
    "hydrology",
    "change_detection",
    "spatiotemporal_pattern",
    "remote_sensing_extraction",
    "uncertainty",
    "comparison",
    "multi_criteria",
    "thematic_cartography",
    "atlas_reporting",
)


class TaskCategoryDescriptor(BaseModel):
    """一个 GIS 任务类目的分类学描述（对齐视图 + 类别级审定知识）。

    手写字段只有：对齐 id、intent 语义、invalid/alternative 方法、
    可视化/组件期望、provenance——数据需求全部载入期投影。
    """
    category_id: str                  # ⊆ GIS_TASK_CATEGORIES
    label_zh: str
    label_en: str = ""
    #: 该类问题的语义（一句话；进解释面）
    intent_semantics: str = ""
    # ── 对齐（全部经注册期校验）────────────────────────────────────────
    ontology_task_ids: Tuple[str, ...] = ()
    methodology_family_ids: Tuple[str, ...] = ()
    #: 类别级经典错误替换（选中即方法学错误；⊆ method 词表）
    invalid_method_ids: Tuple[str, ...] = ()
    #: 合法替代/降级路径（⊆ method 词表）
    alternative_method_ids: Tuple[str, ...] = ()
    #: 推荐可视化（MapModel id/别名，校验）
    recommended_visualizations: Tuple[str, ...] = ()
    required_components: Tuple[str, ...] = ()
    optional_components: Tuple[str, ...] = ()
    provenance_id: str = ""
    # ── 载入期投影（ontology 成员任务并集；不手写）────────────────────
    data_role_demands: Tuple[str, ...] = ()
    geometry_requirements: Tuple[str, ...] = ()
    output_artifact_types: Tuple[str, ...] = ()
    #: lexical 投影池（成员任务 keywords ∪ 成员族 keywords；R1-F6）
    lexical_terms_zh: Tuple[str, ...] = ()
    lexical_terms_en: Tuple[str, ...] = ()

    @field_validator("intent_semantics")
    @classmethod
    def _bounded_semantics(cls, v: str) -> str:
        return v[:200]

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "category_id": self.category_id[:40],
            "label_zh": self.label_zh[:40],
            "ontology_task_ids": list(self.ontology_task_ids[:8]),
            "methodology_family_ids": list(self.methodology_family_ids[:4]),
            "invalid_methods": list(self.invalid_method_ids[:6]),
            "data_role_demands": list(self.data_role_demands[:6]),
            "geometry": list(self.geometry_requirements[:4]),
        }


def _cat(**kw: Any) -> TaskCategoryDescriptor:
    kw.setdefault("provenance_id", "prov.taxonomy.alignment")
    if kw.get("invalid_method_ids"):
        kw["provenance_id"] = "prov.taxonomy.invalid_methods"
    return TaskCategoryDescriptor(**kw)


#: 审定分类表（category→词表对齐 + 类别级方法学知识；纯加法演进）。
#: 数据需求/lexical 池不在此表——载入期从 ontology/families 投影。
_CURATED_CATEGORIES: Tuple[TaskCategoryDescriptor, ...] = (
    _cat(category_id="spatial_distribution", label_zh="空间分布",
         label_en="Spatial distribution",
         intent_semantics="主体在哪里、如何散布：位置表达与构成，不做推断。",
         ontology_task_ids=("distribution.point_distribution",
                            "distribution.category_breakdown"),
         methodology_family_ids=("descriptive_mapping",),
         invalid_method_ids=("interp.ordinary_kriging",),
         alternative_method_ids=("density.grid_binning",),
         recommended_visualizations=("simple_point_map", "categorical_thematic",
                                     "aggregate_grid"),
         required_components=("legend", "title"),
         optional_components=("scale_bar", "attribution", "north_arrow",
                              "chart_panel")),
    _cat(category_id="density", label_zh="密度分析",
         label_en="Density analysis",
         intent_semantics="单位面积的数量强度：KDE 面/行政区率/格网计数。",
         ontology_task_ids=("distribution.density_quantitative",),
         methodology_family_ids=("distribution_density",),
         invalid_method_ids=("interp.ordinary_kriging", "descriptive.simple_display"),
         alternative_method_ids=("density.grid_binning", "density.admin_rate"),
         recommended_visualizations=("normalized_choropleth",
                                     "kernel_density_surface", "aggregate_grid"),
         required_components=("legend", "title"),
         optional_components=("continuous_colorbar", "chart_panel")),
    _cat(category_id="administrative_aggregation", label_zh="行政聚合",
         label_en="Administrative aggregation",
         intent_semantics="按行政单元聚合计数/占比并制表填色。",
         ontology_task_ids=("distribution.regional_aggregation",),
         methodology_family_ids=("zonal_statistics", "descriptive_mapping"),
         invalid_method_ids=("density.kernel_surface",),
         alternative_method_ids=("density.grid_binning",),
         recommended_visualizations=("administrative_choropleth",
                                     "aggregate_grid"),
         required_components=("legend", "title", "chart_panel"),
         optional_components=("table_panel",)),
    _cat(category_id="proximity", label_zh="邻近与缓冲",
         label_en="Proximity & buffer",
         intent_semantics="围绕主体的欧氏邻近域（缓冲/多环）；欧氏≠路网可达。",
         ontology_task_ids=("network.proximity_buffer",),
         methodology_family_ids=("proximity",),
         invalid_method_ids=(),
         alternative_method_ids=("network.service_area",),
         recommended_visualizations=("proximity_overlay", "multi_ring_buffer_map"),
         required_components=("legend", "title", "scale_bar"),
         optional_components=("north_arrow", "attribution")),
    _cat(category_id="accessibility_network", label_zh="可达性与网络",
         label_en="Accessibility & network",
         intent_semantics="沿路网的可达/服务区/路径：需要网络数据，"
                          "欧氏缓冲不能替代。",
         ontology_task_ids=("network.accessibility", "network.service_area"),
         methodology_family_ids=("network",),
         invalid_method_ids=("proximity.euclidean_buffer",),
         alternative_method_ids=("proximity.multi_ring_buffer",),
         recommended_visualizations=("service_area_overlay",
                                     "accessibility_network"),
         required_components=("legend", "title"),
         optional_components=("scale_bar", "chart_panel")),
    _cat(category_id="hotspot", label_zh="热点显著性",
         label_en="Hotspot significance",
         intent_semantics="统计显著性热点（Gi*/LISA）；视觉热力不是显著性结论。",
         ontology_task_ids=("spatial_statistics.hotspot_significance",),
         methodology_family_ids=("spatial_statistics",),
         invalid_method_ids=("interp.ordinary_kriging",),
         alternative_method_ids=("density.visual_heatmap", "stats.local_cluster"),
         recommended_visualizations=("hotspot_overlay",),
         required_components=("legend", "title"),
         optional_components=("chart_panel", "methodology_note")),
    _cat(category_id="clustering", label_zh="聚类分析",
         label_en="Clustering",
         intent_semantics="点聚类分簇（DBSCAN/NNI）与局部聚集定位；"
                          "与显著性热点正交。",
         ontology_task_ids=("spatial_statistics.point_clustering",
                            "spatial_statistics.local_cluster"),
         methodology_family_ids=("spatial_statistics",),
         invalid_method_ids=("interp.ordinary_kriging",),
         alternative_method_ids=("density.kernel_surface",),
         recommended_visualizations=("dbscan_cluster_map", "point_cluster",
                                     "hotspot_overlay"),
         required_components=("legend", "title", "chart_panel"),
         optional_components=("statistics_panel",)),
    _cat(category_id="interpolation", label_zh="空间插值",
         label_en="Spatial interpolation",
         intent_semantics="点观测 → 连续预测面：克里金/IDW/趋势面 + 不确定性。",
         ontology_task_ids=("interpolation.deterministic_surface",
                            "interpolation.geostatistical_kriging",
                            "interpolation.regression_kriging",
                            "interpolation.trend_surface"),
         methodology_family_ids=("interpolation",),
         invalid_method_ids=("density.kernel_surface", "descriptive.simple_display"),
         alternative_method_ids=("interp.idw", "interp.trend_surface"),
         recommended_visualizations=("raster_surface", "interpolation_result_map",
                                     "isoline_contour"),
         required_components=("continuous_colorbar", "title"),
         optional_components=("uncertainty_panel", "legend")),
    _cat(category_id="suitability", label_zh="适宜性评价",
         label_en="Suitability analysis",
         intent_semantics="约束过滤 + 因子叠加的选址/适宜性评价。",
         ontology_task_ids=("decision.suitability", "decision.site_selection"),
         methodology_family_ids=("suitability", "multi_criteria"),
         invalid_method_ids=("descriptive.simple_display",),
         alternative_method_ids=("suit.constraint_filter",),
         recommended_visualizations=("suitability_classes", "mcda_score_map",
                                     "site_selection_result"),
         required_components=("legend", "title"),
         optional_components=("decision_panel", "chart_panel")),
    _cat(category_id="overlay", label_zh="叠加合成",
         label_en="Overlay composite",
         intent_semantics="多图层几何叠加/相交/裁剪合成；因子加权归 MCDA。",
         ontology_task_ids=("decision.overlay_composite",),
         methodology_family_ids=("suitability", "proximity"),
         invalid_method_ids=(),
         alternative_method_ids=("suit.overlay_composite",),
         recommended_visualizations=("suitability_constraint_overlay",
                                     "classified_raster"),
         required_components=("legend", "title"),
         optional_components=("scale_bar",)),
    _cat(category_id="terrain", label_zh="地形分析",
         label_en="Terrain analysis",
         intent_semantics="DEM 衍生：坡度/坡向/山体阴影/视域/地形指数"
                          "（需局部度量 CRS）。",
         ontology_task_ids=("terrain_hydrology.slope_aspect",
                            "terrain_hydrology.hillshade_viewshed",
                            "terrain_hydrology.composite_analysis",
                            "terrain_hydrology.indices"),
         methodology_family_ids=("terrain_hydrology",),
         invalid_method_ids=("density.kernel_surface",),
         alternative_method_ids=(),
         recommended_visualizations=("terrain_analytical_surface",
                                     "elevation_tint_hillshade",
                                     "aspect_direction_map"),
         required_components=("continuous_colorbar", "title"),
         optional_components=("legend",)),
    _cat(category_id="hydrology", label_zh="水文分析",
         label_en="Hydrology",
         intent_semantics="流域划分/河网提取等 DEM 水文衍生。",
         ontology_task_ids=("terrain_hydrology.watershed",
                            "terrain_hydrology.stream_network"),
         methodology_family_ids=("terrain_hydrology",),
         invalid_method_ids=(),
         alternative_method_ids=(),
         recommended_visualizations=("watershed_boundary_map",
                                     "stream_order_map"),
         required_components=("legend", "title"),
         optional_components=("scale_bar",)),
    _cat(category_id="change_detection", label_zh="变化检测",
         label_en="Change detection",
         intent_semantics="双时相/时序变化：栅格差值/分类后比较/时序趋势。",
         ontology_task_ids=("remote_sensing.change_detection",
                            "remote_sensing.temporal_analysis"),
         methodology_family_ids=("change_detection",),
         invalid_method_ids=(),
         alternative_method_ids=("change.temporal_trend",
                                 "change.post_classification"),
         recommended_visualizations=("change_comparison_map",
                                     "surface_difference_map",
                                     "before_after_swipe"),
         required_components=("legend", "title"),
         optional_components=("chart_panel",)),
    _cat(category_id="spatiotemporal_pattern", label_zh="时空格局",
         label_en="Spatiotemporal pattern",
         intent_semantics="时空交互/时空 K 函数/时空聚类：时间字段必需。",
         ontology_task_ids=("spatial_statistics.spatiotemporal_pattern",),
         methodology_family_ids=("spatial_statistics", "change_detection"),
         invalid_method_ids=("density.kernel_surface",),
         alternative_method_ids=("stats.point_cluster_dbscan",),
         recommended_visualizations=("hotspot_overlay",
                                     "temporal_comparison_map"),
         required_components=("legend", "title", "chart_panel"),
         optional_components=("statistics_panel",)),
    _cat(category_id="remote_sensing_extraction", label_zh="遥感信息提取",
         label_en="Remote sensing extraction",
         intent_semantics="光谱指数/影像分类/异常检测/SAR 预处理链。",
         ontology_task_ids=("remote_sensing.spectral_index",
                            "remote_sensing.classification",
                            "remote_sensing.anomaly_detection",
                            "sar.radiometric_calibration",
                            "sar.speckle_filtering", "sar.interpretation"),
         methodology_family_ids=("remote_sensing",),
         invalid_method_ids=("density.kernel_surface",),
         alternative_method_ids=(),
         recommended_visualizations=("spectral_index_surface",
                                     "classified_raster",
                                     "sar_intensity_surface"),
         required_components=("categorical_legend", "title"),
         optional_components=("continuous_colorbar", "legend")),
    _cat(category_id="uncertainty", label_zh="不确定性制图",
         label_en="Uncertainty mapping",
         intent_semantics="预测方差/置信区间/可靠性的显式地图表达。",
         ontology_task_ids=("interpolation.uncertainty_surface",),
         methodology_family_ids=("interpolation",),
         invalid_method_ids=(),
         alternative_method_ids=("interp.ordinary_kriging",),
         recommended_visualizations=("uncertainty_surface",
                                     "uncertainty_choropleth",
                                     "uncertainty_point_symbol"),
         required_components=("uncertainty_panel", "continuous_colorbar"),
         optional_components=("legend",)),
    _cat(category_id="comparison", label_zh="对比表达",
         label_en="Comparison",
         intent_semantics="双期/双方案并排/卷帘对比的产品化表达。",
         ontology_task_ids=("cartographic.comparison_map",),
         methodology_family_ids=("compositional_mapping", "change_detection"),
         invalid_method_ids=(),
         alternative_method_ids=("compose.comparison_map",
                                 "change.bi_temporal_raster"),
         recommended_visualizations=("before_after_swipe",
                                     "change_comparison_map",
                                     "temporal_comparison_map"),
         required_components=("legend", "title"),
         optional_components=("chart_panel",)),
    _cat(category_id="multi_criteria", label_zh="多准则决策",
         label_en="Multi-criteria decision",
         intent_semantics="多因子加权/风险暴露/脆弱性/公平性综合评价；"
                          "权重必须显式声明。",
         ontology_task_ids=("decision.multi_criteria", "decision.risk_exposure",
                            "decision.vulnerability", "decision.spatial_equity"),
         methodology_family_ids=("multi_criteria",),
         invalid_method_ids=("descriptive.choropleth_display",),
         alternative_method_ids=("mcda.risk_exposure", "suit.weighted_overlay"),
         recommended_visualizations=("mcda_score_map", "risk_exposure_classes",
                                     "vulnerability_index", "equity_assessment"),
         required_components=("legend", "title", "decision_panel"),
         optional_components=("chart_panel", "methodology_note")),
    _cat(category_id="thematic_cartography", label_zh="专题制图",
         label_en="Thematic cartography",
         intent_semantics="以统计量为主题的分级/双变量/比例符号专题表达。",
         ontology_task_ids=("cartographic.statistical_map",
                            "distribution.ranking_comparison"),
         methodology_family_ids=("descriptive_mapping", "compositional_mapping",
                                 "zonal_statistics"),
         invalid_method_ids=(),
         alternative_method_ids=("compose.statistical_map",),
         recommended_visualizations=("diverging_choropleth",
                                     "bivariate_choropleth",
                                     "normalized_choropleth",
                                     "proportional_symbol"),
         required_components=("legend", "title", "chart_panel"),
         optional_components=("statistics_panel",)),
    _cat(category_id="atlas_reporting", label_zh="图集与报告",
         label_en="Atlas & reporting",
         intent_semantics="多幅专题图 + 统计页的产品化图集/报告集。",
         ontology_task_ids=("cartographic.report_map",
                            "cartographic.atlas_reporting"),
         methodology_family_ids=("compositional_mapping",),
         invalid_method_ids=(),
         alternative_method_ids=("compose.report_map",),
         recommended_visualizations=("simple_point_map",
                                     "administrative_choropleth",
                                     "raster_surface"),
         required_components=("title", "legend", "north_arrow", "scale_bar",
                              "attribution"),
         optional_components=("chart_panel", "inset_map", "map_border")),
)


class TaskTaxonomy:
    """分类学登记表：O(1) by category + 投影派生 + 校验 + 指纹。"""

    def __init__(
        self,
        categories: Tuple[TaskCategoryDescriptor, ...] = _CURATED_CATEGORIES,
        *,
        task_lookup: Optional[Callable[[str], Any]] = None,
        family_lookup: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """``task_lookup``/``family_lookup`` 注入（返回带 keywords_zh/
        keywords_en 的 descriptor），避免 lib→services 顶层依赖；
        缺省用延迟 import 对接 canonical 单例（与 methodology.load_curated
        同先例）。"""
        if task_lookup is None or family_lookup is None:
            task_lookup, family_lookup = _default_lookups()
        self._by_id: Dict[str, TaskCategoryDescriptor] = {}
        self._task_keywords: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {}
        self._family_keywords: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {}
        for cat in categories:
            if cat.category_id in self._by_id:
                raise ValueError(f"duplicate taxonomy category id: {cat.category_id}")
            self._by_id[cat.category_id] = cat
            self._project_demands(cat, task_lookup)
            self._project_lexical(cat, task_lookup, family_lookup)

    # ── 投影派生（ontology/families 是唯一事实源）──────────────────────
    def _project_demands(
        self, cat: TaskCategoryDescriptor, task_lookup: Callable[[str], Any],
    ) -> None:
        roles: List[str] = []
        geoms: List[str] = []
        artifacts: List[str] = []
        for tid in cat.ontology_task_ids:
            task = task_lookup(tid)
            if task is None:
                continue
            for role in task.required_data_roles:
                if role not in roles:
                    roles.append(role)
            for g in task.geometry_expectations:
                if g not in geoms:
                    geoms.append(g)
            for a in task.output_artifacts:
                if a not in artifacts:
                    artifacts.append(a)
        self._by_id[cat.category_id] = cat.model_copy(update={
            "data_role_demands": tuple(roles),
            "geometry_requirements": tuple(geoms),
            "output_artifact_types": tuple(artifacts),
        })

    def _project_lexical(
        self,
        cat: TaskCategoryDescriptor,
        task_lookup: Callable[[str], Any],
        family_lookup: Callable[[str], Any],
    ) -> None:
        zh: List[str] = []
        en: List[str] = []
        for tid in cat.ontology_task_ids:
            task = task_lookup(tid)
            if task is None:
                continue
            for kw in task.keywords_zh:
                if kw and kw not in zh:
                    zh.append(kw)
            for kw in task.keywords_en:
                if kw and kw not in en:
                    en.append(kw)
            self._task_keywords[tid] = (tuple(task.keywords_zh),
                                        tuple(task.keywords_en))
        for fid in cat.methodology_family_ids:
            fam = family_lookup(fid)
            if fam is None:
                continue
            for kw in fam.keywords_zh:
                if kw and kw not in zh:
                    zh.append(kw)
            for kw in fam.keywords_en:
                if kw and kw not in en:
                    en.append(kw)
            self._family_keywords[fid] = (tuple(fam.keywords_zh),
                                          tuple(fam.keywords_en))
        self._by_id[cat.category_id] = self._by_id[cat.category_id].model_copy(
            update={"lexical_terms_zh": tuple(zh), "lexical_terms_en": tuple(en)})

    # ── 查询 ─────────────────────────────────────────────────────────
    def get(self, category_id: str) -> Optional[TaskCategoryDescriptor]:
        return self._by_id.get(category_id)

    def has(self, category_id: str) -> bool:
        return category_id in self._by_id

    @property
    def all_ids(self) -> List[str]:
        """按 GIS_TASK_CATEGORIES 词表序（稳定）。"""
        return [c for c in GIS_TASK_CATEGORIES if c in self._by_id]

    def categories_for_task(self, task_id: str) -> List[TaskCategoryDescriptor]:
        return [self._by_id[c] for c in self.all_ids
                if task_id in self._by_id[c].ontology_task_ids]

    def categories_for_family(self, family_id: str) -> List[TaskCategoryDescriptor]:
        return [self._by_id[c] for c in self.all_ids
                if family_id in self._by_id[c].methodology_family_ids]

    def tasks_for_category(self, category_id: str) -> Tuple[str, ...]:
        cat = self._by_id.get(category_id)
        return cat.ontology_task_ids if cat else ()

    # ── category 级匹配（lexical 投影；V4 路由优先，此处仅证据）────────
    def match_query(self, query: str, *, limit: int = 3) -> List[Tuple[str, float]]:
        """query → 有序 (category_id, score)。

        分值 = 投影关键词命中长度和的归一（长短语权重高，与 V4
        ``_family_keyword_score`` 同哲学）；纯确定性，平局按词表序。
        上限 score ≤ 1.0；零命中返回空表（诚实无证据 → abstention 用）。
        """
        lowered = (query or "").lower()
        scored: List[Tuple[str, float]] = []
        for cid in self.all_ids:
            cat = self._by_id[cid]
            hits = 0.0
            for kw in cat.lexical_terms_zh:
                if kw and kw in lowered:
                    hits += len(kw)
            for kw in cat.lexical_terms_en:
                k = kw.lower()
                if not k:
                    continue
                if k.isascii() and k.replace(" ", "").isalnum() and " " in k:
                    # 多词英文短语：子串命中（与 V4 keywords_en 同策略）
                    if k in lowered:
                        hits += len(k)
                elif k.isascii() and k.isalnum():
                    if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", lowered):
                        hits += len(k)
                elif k in lowered:
                    hits += len(k)
            if hits > 0:
                scored.append((cid, min(1.0, hits / 40.0)))
        scored.sort(key=lambda t: (-t[1], self.all_ids.index(t[0])))
        return scored[:limit]

    # ── 校验与指纹 ───────────────────────────────────────────────────
    def validate(
        self,
        *,
        task_exists: Optional[Callable[[str], bool]] = None,
        family_exists: Optional[Callable[[str], bool]] = None,
        method_exists: Optional[Callable[[str], bool]] = None,
        artifact_type_exists: Optional[Callable[[str], bool]] = None,
        map_model_exists: Optional[Callable[[str], bool]] = None,
        component_exists: Optional[Callable[[str], bool]] = None,
        provenance_exists: Optional[Callable[[str], bool]] = None,
        data_role_vocabulary: Tuple[str, ...] = (),
    ) -> List[str]:
        violations: List[str] = []
        for cid in self.all_ids:
            cat = self._by_id[cid]
            tag = f"taxonomy[{cid}]"
            if cat.category_id not in GIS_TASK_CATEGORIES:
                violations.append(f"{tag}: unknown category_id")
            if not cat.ontology_task_ids:
                violations.append(f"{tag}: 无 ontology 任务（空类无意义）")
            if not cat.methodology_family_ids:
                violations.append(f"{tag}: 无 methodology family 覆盖")
            for tid in cat.ontology_task_ids:
                if task_exists and not task_exists(tid):
                    violations.append(f"{tag}: ontology task {tid} 不存在")
            for fid in cat.methodology_family_ids:
                if family_exists and not family_exists(fid):
                    violations.append(f"{tag}: methodology family {fid} 不存在")
            for mid in cat.invalid_method_ids + cat.alternative_method_ids:
                if method_exists and not method_exists(mid):
                    violations.append(f"{tag}: method {mid} 不存在")
            overlap = (set(cat.invalid_method_ids)
                       & set(cat.alternative_method_ids))
            if overlap:
                violations.append(
                    f"{tag}: 方法同时 invalid 且 alternative: {sorted(overlap)}")
            for role in cat.data_role_demands:
                if data_role_vocabulary and role not in data_role_vocabulary:
                    violations.append(f"{tag}: unknown data role {role}")
            for at in cat.output_artifact_types:
                if artifact_type_exists and not artifact_type_exists(at):
                    violations.append(f"{tag}: artifact type {at} 未注册")
            for mm in cat.recommended_visualizations:
                if map_model_exists and not map_model_exists(mm):
                    violations.append(f"{tag}: map model {mm} 未注册")
            for comp in cat.required_components + cat.optional_components:
                if component_exists and not component_exists(comp):
                    violations.append(f"{tag}: component {comp} 未注册")
            if provenance_exists and cat.provenance_id and \
                    not provenance_exists(cat.provenance_id):
                violations.append(f"{tag}: provenance {cat.provenance_id} 未登记")
        return violations

    def fingerprint(self) -> str:
        payload = {
            "version": TAXONOMY_SCHEMA_VERSION,
            "categories": [
                self._by_id[c].model_dump(mode="json")
                for c in sorted(self._by_id)
            ],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _default_lookups() -> Tuple[Callable[[str], Any], Callable[[str], Any]]:
    """canonical 单例延迟对接（函数体内 import，避免 lib→services 顶层环）。"""
    from app.services.gis_harness.gis_ontology import get_task_ontology
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )

    ontology = get_task_ontology()
    methods = get_methodology_registry()
    return ontology.get, methods.family


_singleton: Optional[TaskTaxonomy] = None


def get_task_taxonomy() -> TaskTaxonomy:
    global _singleton
    if _singleton is None:
        _singleton = TaskTaxonomy()
    return _singleton


def reset_task_taxonomy() -> None:
    global _singleton
    _singleton = None


__all__ = [
    "TAXONOMY_SCHEMA_VERSION",
    "GIS_TASK_CATEGORIES",
    "TaskCategoryDescriptor",
    "TaskTaxonomy",
    "get_task_taxonomy",
    "reset_task_taxonomy",
]
