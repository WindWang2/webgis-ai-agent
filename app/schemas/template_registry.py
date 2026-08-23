"""
Template Registry V2 (F-FE-TPL) — centralized, indexed template catalog.

The previous SEED_TEMPLATES list (in `template_schema.py`) and the
PRESET_COMBINATIONS dict (in `mapspec/composite_builder.py`) are the
authoritative source of built-in templates and composite workflow presets.
This module is the V2 wrapper that gives them:

  - O(1) lookup by template id (built on a dict, not list-scan)
  - O(1) lookup by kind (basemap / symbology / layout / thematic / composite)
  - tags + category indexes for fast search
  - A registry validation entry point that asserts every entry is unique,
    well-formed, and has the metadata the gallery UIs need
  - A single helper to translate a registry entry into the V2 summary DTO
    for the gallery

Composite templates are stored separately (they reuse individual template
ids rather than carrying their own payload).

Backward compat: `SEED_TEMPLATES` and `PRESET_COMBINATIONS` are still
importable from their original modules. The V2 registry is built from
them once and lazily re-built when callers add user templates.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from app.schemas.template_schema import SEED_TEMPLATES

logger = logging.getLogger(__name__)


# Built-in composite template definitions. These are recipe-style entries
# that the Agent can apply as one logical unit (e.g. "make a population
# density map" — pulls basemap + symbology + thematic + layout in one shot).
#
# Each composite references template_ids that must exist in SEED_TEMPLATES.
# The `pipeline` field is informational; the actual execution path lives in
# the Agent tool pipeline (combine_map_theme / webgis_map_combine).
#
# `id` values are stable and used as the cache key. The "composite" kind
# is brand new in V2 and the V2 gallery gains a dedicated tab.
COMPOSITE_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": 'composite_population_density_analysis',
        "kind": 'composite',
        "name": '人口密度样式预设',
        "category": 'analysis',
        "keywords": ['population', 'density', 'choropleth', '人口', '密度', '分级'],
        "description": '样式组合预设：浅色底图 + 蓝调面填充 + 分位数 YlOrRd 分级 + 学术版式（需提供数值字段，样式预设不含密度计算）。',
        "recommended_use": '用户说「应用人口密度图」「展示区域人口分布」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'method': 'quantiles', 'palette': 'YlOrRd'},
        "preview_metadata": {'method': 'quantiles', 'palette': 'YlOrRd'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_pop_choro',
            "layout": 'tmpl_ly_academic',
        },
    },
    {
        "id": 'composite_vegetation_health',
        "kind": 'composite',
        "name": '植被指数样式预设',
        "category": 'analysis',
        "keywords": ['ndvi', 'vegetation', 'remote_sensing', '植被', '健康', '遥感'],
        "description": '样式组合预设：卫星底图 + 蓝调面 + 自然断裂 Viridis 分级 + 标准报告版式（需提供数值字段，不含 NDVI 计算）。',
        "recommended_use": '用户说「应用植被覆盖图」「NDVI 分析」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 6, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_satellite',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_terrain_elevation',
        "kind": 'composite',
        "name": '高程分级样式预设',
        "category": 'analysis',
        "keywords": ['elevation', 'terrain', 'dem', '高程', '地形', '分级'],
        "description": '样式组合预设：灰度底图 + 蓝调面 + 等间隔 Blues 分级 + 极简版式（需提供数值字段，不含高程/DEM 计算）。',
        "recommended_use": '用户说「应用高程图」「等高线分级」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 7},
        "preview_metadata": {'method': 'equal_interval', 'palette': 'Blues'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_grayscale',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_equal_interval',
            "layout": 'tmpl_ly_minimal',
        },
    },
    {
        "id": 'composite_urban_analysis',
        "kind": 'composite',
        "name": '用地分类样式预设',
        "category": 'analysis',
        "keywords": ['urban', 'builtup', 'landuse', '城市', '建成区', '用地'],
        "description": '样式组合预设：浅色底图 + 土地利用分类配色 + 自然断裂 Viridis 分级 + 标准报告版式（需提供分类字段，不含建成区提取）。',
        "recommended_use": '用户说「应用城市建成区图」「用地分类可视化」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_landuse_cat',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_hydrology',
        "kind": 'composite',
        "name": '水文制图样式预设',
        "category": 'analysis',
        "keywords": ['hydrology', 'river', 'water', '水文', '流域', '河流'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + 分位数 YlOrRd 分级 + 学术版式（需提供数值字段，不含流域/水文计算）。',
        "recommended_use": '用户说「应用水文图」「流域分析」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5},
        "preview_metadata": {'method': 'quantiles', 'palette': 'YlOrRd'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_pop_choro',
            "layout": 'tmpl_ly_academic',
        },
    },
    {
        "id": 'composite_transport_accessibility',
        "kind": 'composite',
        "name": '路网热力样式预设',
        "category": 'analysis',
        "keywords": ['transport', 'accessibility', 'road', '交通', '可达性', '路网'],
        "description": '样式组合预设：浅色底图 + 橙色虚线 + 热力密度渲染 + 标准报告版式（需提供点数据，不含可达性建模）。',
        "recommended_use": '用户说「应用交通分析」「可达性评估」时。',
        "required_inputs": ['field'],
        "preview_metadata": {'method': 'heatmap'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_road_orange',
            "thematic": 'tmpl_th_heatmap',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_air_quality',
        "kind": 'composite',
        "name": '空气质量分级样式预设',
        "category": 'analysis',
        "keywords": ['air', 'quality', 'aqi', 'pollution', '空气', '污染', 'AQI'],
        "description": '样式组合预设：暗色底图 + 蓝调面 + 自然断裂 Viridis 分级 + 演示版式（需提供数值字段，不含 AQI 计算）。',
        "recommended_use": '用户说「应用空气质量图」「AQI 分布」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 6, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_dark',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_presentation',
        },
    },
    {
        "id": 'composite_flood_risk_screening',
        "kind": 'composite',
        "name": '洪涝风险样式预设',
        "category": 'risk',
        "keywords": ['flood', 'risk', 'water', '洪涝', '风险', '水文'],
        "description": '样式组合预设：卫星底图 + 蓝调面 + LISA 聚类 RdBu + 标准报告版式（需提供风险字段，样式预设不含洪水模拟/风险评分）。',
        "recommended_use": '用户说「应用洪涝风险图」「洪水评估」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'RdBu'},
        "preview_metadata": {'method': 'lisa', 'palette': 'RdBu'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_satellite',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_econ_lisa',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_wildfire_risk',
        "kind": 'composite',
        "name": '火险分级样式预设',
        "category": 'risk',
        "keywords": ['wildfire', 'fire', 'risk', '森林', '火灾', '风险'],
        "description": '样式组合预设：卫星底图 + 蓝调面 + 分位数 YlOrRd 分级 + 标准报告版式（需提供风险字段，不含火险建模）。',
        "recommended_use": '用户说「应用火险图」「火灾风险评估」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'YlOrRd'},
        "preview_metadata": {'method': 'quantiles', 'palette': 'YlOrRd'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_satellite',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_pop_choro',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_ecological_sensitivity',
        "kind": 'composite',
        "name": '生态敏感性样式预设',
        "category": 'risk',
        "keywords": ['ecology', 'sensitivity', 'environment', '生态', '敏感性', '环境'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + 自然断裂 Viridis 分级 + 极简版式（需提供数值字段，不含生态评估建模）。',
        "recommended_use": '用户说「应用生态敏感性图」「环境评估」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_minimal',
        },
    },
    {
        "id": 'composite_site_suitability',
        "kind": 'composite',
        "name": '场地适宜性样式预设',
        "category": 'planning',
        "keywords": ['suitability', 'site', 'multi_factor', '适宜性', '场地', '选址'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + 自然断裂 Viridis + 极简版式（需提供评分字段，不含多因子叠加/AHP）。',
        "recommended_use": '用户说「应用场地选址」「多因子评估」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_minimal',
        },
    },
    {
        "id": 'composite_urban_growth_change',
        "kind": 'composite',
        "name": '城市增长样式预设',
        "category": 'planning',
        "keywords": ['urban', 'growth', 'change', '城市', '增长', '变化'],
        "description": '样式组合预设：卫星底图 + 蓝调面 + 自然断裂 Viridis + 标准报告版式（需提供变化字段，不含变化检测计算）。',
        "recommended_use": '用户说「应用城市扩张分析」「变化检测」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_satellite',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_hospital_accessibility',
        "kind": 'composite',
        "name": '医院分布热力样式预设',
        "category": 'planning',
        "keywords": ['hospital', 'accessibility', 'health', '医院', '可达性', '卫生'],
        "description": '样式组合预设：浅色底图 + 红色点 + 热力密度渲染 + 标准报告版式（需提供点数据，不含可达性/服务区计算）。',
        "recommended_use": '用户说「应用医院可达性」「医疗服务覆盖」时。',
        "required_inputs": ['field'],
        "preview_metadata": {'method': 'heatmap'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_poi_red',
            "thematic": 'tmpl_th_heatmap',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_school_service_area',
        "kind": 'composite',
        "name": '校园点位分级样式预设',
        "category": 'planning',
        "keywords": ['school', 'service', 'education', '学校', '学区', '教育'],
        "description": '样式组合预设：浅色底图 + 红色点 + 分位数 YlOrRd 分级 + 标准报告版式（需提供数值字段，不含学区/服务范围计算）。',
        "recommended_use": '用户说「应用学区图」「学校服务范围」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'YlOrRd', 'method': 'quantiles'},
        "preview_metadata": {'method': 'quantiles', 'palette': 'YlOrRd'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_poi_red',
            "thematic": 'tmpl_th_pop_choro',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_green_space_accessibility',
        "kind": 'composite',
        "name": '绿地覆盖样式预设',
        "category": 'planning',
        "keywords": ['green', 'space', 'park', '绿地', '公园', '可达性'],
        "description": '样式组合预设：浅色底图 + 分类面 + 自然断裂 Viridis 分级 + 极简版式（需提供数值字段，不含可达性建模）。',
        "recommended_use": '用户说「应用绿地分析」「公园可达性」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_landuse_cat',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_minimal',
        },
    },
    {
        "id": 'composite_land_use_change',
        "kind": 'composite',
        "name": '用地变化样式预设',
        "category": 'planning',
        "keywords": ['landuse', 'change', '土地利用', '变化', '检测'],
        "description": '样式组合预设：浅色底图 + 分类面 + LISA 聚类 RdBu + 学术版式（需提供分类字段，不含变化检测）。',
        "recommended_use": '用户说「应用土地利用变化图」时。',
        "required_inputs": ['field'],
        "preview_metadata": {'method': 'lisa', 'palette': 'RdBu'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_landuse_cat',
            "thematic": 'tmpl_th_econ_lisa',
            "layout": 'tmpl_ly_academic',
        },
    },
    {
        "id": 'composite_facility_location_analysis',
        "kind": 'composite',
        "name": '设施选址样式预设',
        "category": 'planning',
        "keywords": ['facility', 'location', 'site_selection', '设施', '选址'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + 自然断裂 Viridis + 极简版式（需提供评分字段，不含选址建模）。',
        "recommended_use": '用户说「应用设施选址」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_minimal',
        },
    },
    {
        "id": 'composite_population_service_gap',
        "kind": 'composite',
        "name": '服务缺口样式预设',
        "category": 'planning',
        "keywords": ['population', 'service', 'gap', '人口', '服务', '缺口'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + LISA 聚类 RdBu + 学术版式（需提供缺口字段，不含缺口分析计算）。',
        "recommended_use": '用户说「应用服务缺口分析」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'RdBu'},
        "preview_metadata": {'method': 'lisa', 'palette': 'RdBu'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_econ_lisa',
            "layout": 'tmpl_ly_academic',
        },
    },
    {
        "id": 'composite_remote_sensing_index_analysis',
        "kind": 'composite',
        "name": '遥感指数样式预设',
        "category": 'analysis',
        "keywords": ['ndvi', 'ndwi', 'nbr', 'remote_sensing', '遥感', '指数'],
        "description": '样式组合预设：卫星底图 + 蓝调面 + 自然断裂 Viridis + 标准报告版式（需提供数值字段，不含指数计算/推断）。',
        "recommended_use": '用户说「应用遥感指数分析」「NDVI 指数」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 6, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_satellite',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_hotspot_investigation',
        "kind": 'composite',
        "name": '热点聚类样式预设',
        "category": 'analysis',
        "keywords": ['hotspot', 'cluster', '热点', '聚类'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + LISA 聚类 RdBu + 学术版式（需提供数值字段并先行热点分析，样式预设不含聚类计算）。',
        "recommended_use": '用户说「应用热点分析」「LISA 聚类」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 4, 'palette': 'RdBu'},
        "preview_metadata": {'method': 'lisa', 'palette': 'RdBu'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_econ_lisa',
            "layout": 'tmpl_ly_academic',
        },
    },
    {
        "id": 'composite_interpolation_to_map',
        "kind": 'composite',
        "name": '插值结果样式预设',
        "category": 'analysis',
        "keywords": ['interpolation', 'kriging', 'idw', '插值', '克里金'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + 自然断裂 Viridis + 标准报告版式（需提供数值字段，不含插值/Kriging 计算）。',
        "recommended_use": '用户说「应用插值图」「Kriging 表面」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 7, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_standard_report',
        },
    },
    {
        "id": 'composite_multi_factor_suitability',
        "kind": 'composite',
        "name": '多因子适宜性样式预设',
        "category": 'planning',
        "keywords": ['multi_factor', 'suitability', 'AHP', '多因子', '综合', '适宜性'],
        "description": '样式组合预设：浅色底图 + 蓝调面 + 自然断裂 Viridis + 极简版式（需提供评分字段，不含加权叠加/AHP）。',
        "recommended_use": '用户说「应用多因子综合评价」「AHP 适宜性」时。',
        "required_inputs": ['field'],
        "optional_parameters": {'k': 5, 'palette': 'Viridis'},
        "preview_metadata": {'method': 'natural_breaks', 'palette': 'Viridis'},
        "source": 'builtin',
        "version": 1,
        "is_builtin": True,
        "pipeline": {
            "basemap": 'tmpl_bm_positron',
            "symbology": 'tmpl_sym_admin_blue',
            "thematic": 'tmpl_th_natural_breaks',
            "layout": 'tmpl_ly_minimal',
        },
    },
]


class TemplateRegistry:
    """V2 registry — O(1) lookup by id/kind, search by tag/category, validation."""

    def __init__(self) -> None:
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._by_kind: Dict[str, List[Dict[str, Any]]] = {}
        self._by_tag: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Bulk load
    # ------------------------------------------------------------------
    def load_builtins(self) -> int:
        """(Re)load the canonical built-in set. Returns the count of entries."""
        with self._lock:
            self._by_id.clear()
            self._by_kind.clear()
            self._by_tag.clear()
            for entry in SEED_TEMPLATES:
                self._insert(entry)
            for entry in COMPOSITE_TEMPLATES:
                self._insert(entry)
            return len(self._by_id)

    def _insert(self, entry: Dict[str, Any]) -> None:
        eid = entry.get("id")
        if not eid:
            raise ValueError("Template entry missing 'id'")
        if eid in self._by_id:
            # Silent dedup — DB-stored entries can shadow seeds of the same id.
            return
        self._by_id[eid] = entry
        kind = entry.get("kind", "other")
        self._by_kind.setdefault(kind, []).append(entry)
        for tag in entry.get("keywords", []) or []:
            self._by_tag.setdefault(str(tag).lower(), []).append(entry)

    def add_user_template(self, entry: Dict[str, Any]) -> None:
        """Register a user-saved template (or composite). Safe to call at runtime."""
        with self._lock:
            self._insert(entry)

    def remove(self, template_id: str) -> bool:
        with self._lock:
            entry = self._by_id.pop(template_id, None)
            if entry is None:
                return False
            for kind_list in self._by_kind.values():
                if entry in kind_list:
                    kind_list.remove(entry)
            for tag, tag_list in self._by_tag.items():
                if entry in tag_list:
                    tag_list.remove(entry)
                    if not tag_list:
                        self._by_tag.pop(tag, None)
            return True

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def get(self, template_id: str) -> Optional[Dict[str, Any]]:
        return self._by_id.get(template_id)

    def __contains__(self, template_id: str) -> bool:
        return template_id in self._by_id

    def by_kind(self, kind: str) -> List[Dict[str, Any]]:
        return list(self._by_kind.get(kind, ()))

    def all_ids(self) -> Set[str]:
        return set(self._by_id.keys())

    def count(self) -> int:
        return len(self._by_id)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(
        self,
        q: Optional[str] = None,
        kind: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Case-insensitive search across name / description / keywords.

        Returns ``(page, total)`` for the registry. Caller is responsible
        for paginating. ``source='builtin'`` / ``'user'`` filters on
        ``is_builtin`` (composites are always built-in).
        """
        with self._lock:
            pool = list(self._by_id.values())

        if kind:
            pool = [e for e in pool if e.get("kind") == kind]
        if category:
            pool = [e for e in pool if e.get("category") == category]
        if source == "builtin":
            pool = [e for e in pool if e.get("is_builtin", True)]
        elif source == "user":
            pool = [e for e in pool if not e.get("is_builtin", True)]

        if q:
            kw = q.lower()
            if kw:
                pool = [
                    e for e in pool
                    if kw in (e.get("id") or "").lower()
                    or kw in (e.get("name") or "").lower()
                    or kw in (e.get("description") or "").lower()
                    or any(kw in str(k).lower() for k in (e.get("keywords") or []))
                ]

        total = len(pool)
        # Stable order: by kind then by name (idempotent across pages).
        pool.sort(key=lambda e: (e.get("kind", ""), e.get("name", ""), e.get("id", "")))
        return pool[offset:offset + limit], total

    # ------------------------------------------------------------------
    # Composite expansion
    # ------------------------------------------------------------------
    def expand_composite(self, composite_id: str) -> Dict[str, Optional[Dict[str, Any]]]:
        """Resolve a composite template's slot references into the actual
        sub-template records (basemap, symbology, thematic, layout).

        Missing references are returned as ``None`` (so the caller can log
        a graceful fallback). The expansion is a pure dict lookup, O(1).
        """
        composite = self.get(composite_id)
        if composite is None or composite.get("kind") != "composite":
            return {}
        return {
            slot: self.get(ref_id) if isinstance(ref_id := ref, str) else None
            for slot, ref in (composite.get("pipeline") or {}).items()
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Run integrity checks over the registry. Returns a list of error
        messages (empty list = valid)."""
        errors: List[str] = []
        seen_ids: Set[str] = set()
        seen_pipeline_refs: Set[str] = set()

        for entry in self._by_id.values():
            eid = entry.get("id", "")
            if eid in seen_ids:
                errors.append(f"duplicate template id: {eid}")
            seen_ids.add(eid)

            if not entry.get("name"):
                errors.append(f"{eid}: missing 'name'")
            # #721: palettes referenced by payloads must exist in the single
            # palette source (COLOR_PALETTES) — unresolvable names used to
            # degrade silently to YlOrRd at every render site.
            _payload = entry.get("payload")
            if isinstance(_payload, dict):
                _pal = _payload.get("palette")
                if isinstance(_pal, str) and _pal:
                    from app.lib.cartography.palettes import COLOR_PALETTES
                    if _pal not in COLOR_PALETTES:
                        errors.append(
                            f"{eid}: palette '{_pal}' does not exist in "
                            "COLOR_PALETTES (renders silently as YlOrRd)"
                        )
            if not entry.get("kind"):
                errors.append(f"{eid}: missing 'kind'")
            if entry.get("kind") not in {"basemap", "symbology", "layout", "thematic", "composite"}:
                errors.append(f"{eid}: unknown kind '{entry.get('kind')}'")
            if entry.get("kind") != "composite" and not entry.get("payload") and "preview_metadata" not in entry:
                # Non-composite templates must carry either a payload (built-in)
                # or preview_metadata (user) — at least one so the detail view
                # is meaningful.
                errors.append(f"{eid}: missing 'payload' or 'preview_metadata'")
            # §Phase F: 显式 compatibility 键必须可解析且引用真实 registry 条目
            # （未携带该键的旧模板/DB 模板不受影响）。
            compat_raw = entry.get("compatibility")
            if compat_raw is not None:
                from app.schemas.template_schema import TemplateCompatibility
                try:
                    compat = TemplateCompatibility.model_validate(compat_raw)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{eid}: invalid 'compatibility' ({exc})")
                else:
                    from app.lib.cartography.model_library import get_map_model_registry
                    from app.lib.gis.artifacts import get_artifact_type_registry
                    models = get_map_model_registry()
                    artifacts = get_artifact_type_registry()
                    for mm in compat.compatible_map_models:
                        if models.resolve(mm) is None:
                            errors.append(
                                f"{eid}: compatibility.map_model '{mm}' not in MapModelRegistry")
                    for at in compat.accepted_artifact_types:
                        if not artifacts.has(at):
                            errors.append(
                                f"{eid}: compatibility.artifact_type '{at}' not registered")

        # Composite-specific: every pipeline reference must resolve.
        for entry in self._by_kind.get("composite", ()):
            pipeline = entry.get("pipeline") or {}
            for slot, ref in pipeline.items():
                if not isinstance(ref, str) or not ref:
                    errors.append(f"{entry.get('id')}: pipeline.{slot} must be a non-empty string")
                    continue
                if ref not in self._by_id:
                    errors.append(f"{entry.get('id')}: pipeline.{slot} references missing template '{ref}'")
                seen_pipeline_refs.add(ref)

        return errors


# Process-wide singleton (lazy). ``load_builtins`` runs once on first access.
_registry: Optional[TemplateRegistry] = None
_registry_lock = threading.Lock()


def get_template_registry() -> TemplateRegistry:
    """Return the process-wide V2 registry, building it on first call."""
    global _registry
    with _registry_lock:
        if _registry is None:
            r = TemplateRegistry()
            r.load_builtins()
            _registry = r
    return _registry


def reset_template_registry() -> None:
    """Test helper — drop the singleton so the next call rebuilds it."""
    global _registry
    with _registry_lock:
        _registry = None
