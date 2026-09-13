"""领域包：Transport（交通研究）。"""
from __future__ import annotations

from typing import List

from app.services.gis_harness.recipe_packs._kit import (
    MAP_COMPONENTS_BASE,
    MAP_COMPONENTS_CHART,
    boundary_role,
    fb,
    obl,
    role,
    standard_completion,
    subject_role,
    wf,
    auto_fallback,
)
from app.services.gis_harness.recipes import CartographyRecipe

_RECIPES: List[CartographyRecipe] = [
    CartographyRecipe(
        id="transit_service_coverage",
        name="公交服务覆盖",
        description="公交/轨道站点服务覆盖评价（站点缓冲 + 覆盖人口）：分母义务。",
        intent_tasks=["accessibility_analysis", "administrative_statistic"],
        intent_cartography=["proximity_overlay", "administrative_choropleth"],
        preferred_analysis=["poi_query", "service_area", "admin_boundary_query", "admin_aggregation",
                            "rate_aggregation"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["administrative_choropleth"],
        default_components=MAP_COMPONENTS_CHART,
        fallbacks=[],
        fallback_links=auto_fallback(),  # ADR-0151 P7：通用兜底链（auto_generated）
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=46,
        schema_version=2,
        workflow=wf(
            "transport", "transit_coverage",
            zh=["公交覆盖", "轨道服务", "站点服务范围"],
            en=["transit service coverage"],
            roles=[subject_role(), boundary_role(),
                   role("population", acquisition="data_fabric", required=False)],
            obligations=[
                obl("transit_coverage_denominator", "denominator",
                    code="TRANSIT_COVERAGE_DENOMINATOR_REQUIRED",
                    desc="覆盖人口比需要人口分母；缺失只能报覆盖面积。",
                    action="degrade_with_disclosure"),
            ],
            completion=standard_completion(),
            fallbacks=[fb("DATA_ROLE_MISSING_DENOMINATOR", frm="coverage_population_ratio",
                          to="coverage_area_map", downgrade="degraded",
                          disclosure="缺少人口分母：仅报覆盖面积/范围。")],
        ),
    ),
    CartographyRecipe(
        id="road_network_inventory",
        name="路网要素清单",
        description="路网/道路等级清单与里程统计（线要素台账）：里程口径披露。",
        intent_tasks=["distribution_overview", "administrative_statistic"],
        intent_cartography=["categorical_thematic", "point_overlay"],
        preferred_analysis=["poi_query", "admin_boundary_query", "admin_aggregation",
                            "category_breakdown", "point_profile"],
        primary_cartography="categorical_thematic",
        secondary_cartography=["point_overlay"],
        default_components=MAP_COMPONENTS_BASE,
        fallbacks=[],
        fallback_links=auto_fallback(),  # ADR-0151 P7：通用兜底链（auto_generated）
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=47,
        schema_version=2,
        workflow=wf(
            "transport", "network_inventory",
            zh=["路网清单", "道路里程", "道路等级"],
            en=["road network inventory"],
            roles=[subject_role(capability="poi_query",
                                artifacts=("line_feature_set",), geometry=("line",)),
                   boundary_role()],
            completion=standard_completion(),
        ),
    ),
    CartographyRecipe(
        id="commute_flow_corridor",
        name="通勤流走廊分析",
        description="通勤 OD 走廊强度分析（主走廊识别 + 流量分级）：OD 数据口径披露。",
        intent_tasks=["mobility_flow"],
        intent_cartography=["flow_od_arc", "point_overlay"],
        preferred_analysis=["poi_query", "od_matrix", "od_flow_mapping", "point_profile"],
        primary_cartography="flow_od_arc",
        secondary_cartography=["point_overlay"],
        default_components=MAP_COMPONENTS_CHART,
        fallbacks=[],
        fallback_links=auto_fallback(),  # ADR-0151 P7：通用兜底链（auto_generated）
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=46,
        schema_version=2,
        workflow=wf(
            "transport", "commute_corridor",
            zh=["通勤走廊", "主通道", "通勤od"],
            en=["commuting corridor"],
            roles=[subject_role(),
                   role("measure", capability="od_matrix", artifacts=("od_table",),
                        geometry=("table",), required=False,
                        note="通勤流量字段")],
            completion=standard_completion(uncertainty=True),
        ),
    ),
    CartographyRecipe(
        id="station_catchment_profile",
        name="站点集散圈画像",
        description="轨道站点集散圈（步行/骑行接驳圈）覆盖画像：接驳方式口径披露。",
        intent_tasks=["accessibility_analysis", "proximity_analysis"],
        intent_cartography=["proximity_overlay", "point_overlay"],
        preferred_analysis=["poi_query", "service_area", "geometry_buffer", "point_profile",
                            "admin_boundary_query"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["point_overlay"],
        default_components=MAP_COMPONENTS_BASE,
        fallbacks=[],
        fallback_links=auto_fallback(),  # ADR-0151 P7：通用兜底链（auto_generated）
        export_profile={"formats": ["png"]},
        priority=47,
        schema_version=2,
        workflow=wf(
            "transport", "station_catchment",
            zh=["站点集散圈", "tod圈层", "站城覆盖"],
            en=["station catchment", "tod radius"],
            roles=[subject_role(), boundary_role(required=False)],
            obligations=[
                obl("catchment_mode_disclosure", "disclosure",
                    code="CATCHMENT_MODE_SEMANTICS",
                    desc="集散圈的接驳方式与时间阈值必须披露。"),
            ],
            completion=standard_completion(),
        ),
    ),
]

RECIPES: List[CartographyRecipe] = _RECIPES
