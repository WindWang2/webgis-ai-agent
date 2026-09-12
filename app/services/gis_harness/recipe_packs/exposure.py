"""领域包：Exposure（暴露评价）。"""
from __future__ import annotations

from typing import List

from app.services.gis_harness.recipe_packs._kit import (
    MAP_COMPONENTS_CHART,
    boundary_role,
    denominator_role,
    fb,
    obl,
    role,
    standard_completion,
    subject_role,
    wf,
    auto_fallback,
)
from app.services.gis_harness.recipes import CartographyRecipe, RecipeFallback

_RECIPES: List[CartographyRecipe] = [
    CartographyRecipe(
        id="population_exposure_estimate",
        name="人口暴露估算",
        description="影响区 × 人口暴露估算（影响区内人口规模）：人口分母义务。",
        intent_tasks=["risk_exposure", "spatial_equity"],
        intent_cartography=["administrative_choropleth", "proximity_overlay"],
        preferred_analysis=["geometry_buffer", "geometry_overlay", "admin_boundary_query",
                            "admin_aggregation", "rate_aggregation"],
        primary_cartography="administrative_choropleth",
        secondary_cartography=["proximity_overlay"],
        default_components=MAP_COMPONENTS_CHART,
        fallbacks=[RecipeFallback(when="population missing", reason_code="DATA_ROLE_MISSING_POPULATION",
                                  use="exposure_area_map")],
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=45,
        schema_version=2,
        workflow=wf(
            "exposure", "population_exposure",
            zh=["人口暴露", "影响人口", "受影响人数"],
            en=["population exposure", "affected population"],
            roles=[
                role("hazard", capability="geometry_buffer", artifacts=("proximity_zone",),
                     geometry=("polygon",)),
                denominator_role(),
                boundary_role(),
            ],
            obligations=[
                obl("exposure_population_required", "denominator",
                    code="EXPOSURE_POPULATION_MISSING",
                    desc="人口暴露需要人口数据；缺失只能报影响面积。",
                    action="degrade_with_disclosure"),
            ],
            completion=standard_completion(),
            fallbacks=[fb("DATA_ROLE_MISSING_DENOMINATOR", frm="population_exposure", to="exposure_area_map",
                          downgrade="degraded",
                          disclosure="缺少人口数据：仅统计影响面积，不报受影响人口。")],
        ),
    ),
    CartographyRecipe(
        id="facility_exposure_inventory",
        name="设施暴露清单",
        description="影响区内设施/承灾体清单（数量与类别构成）：清单语义，非损失评估。",
        intent_tasks=["risk_exposure", "distribution_overview"],
        intent_cartography=["proximity_overlay", "categorical_thematic"],
        preferred_analysis=["geometry_buffer", "poi_query", "geometry_overlay", "spatial_join",
                            "category_breakdown"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["categorical_thematic"],
        default_components=MAP_COMPONENTS_CHART,
        fallbacks=[],
        fallback_links=auto_fallback(),  # ADR-0151 P7：通用兜底链（auto_generated）
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=46,
        schema_version=2,
        workflow=wf(
            "exposure", "facility_inventory",
            zh=["影响区内设施", "暴露设施", "受影响设施"],
            en=["exposed facilities"],
            roles=[
                role("hazard", capability="geometry_buffer", artifacts=("proximity_zone",),
                     geometry=("polygon",)),
                subject_role(),
            ],
            obligations=[
                obl("exposure_not_loss", "disclosure",
                    code="EXPOSURE_NOT_LOSS_ESTIMATE",
                    desc="设施暴露清单不是损失评估，不得推断经济损失额。"),
            ],
            completion=standard_completion(),
        ),
    ),
    CartographyRecipe(
        id="vulnerability_profile_overlay",
        name="易损性画像叠加",
        description="敏感群体/易损属性与影响区叠加（脆弱性画像）：代理指标语义披露。",
        intent_tasks=["risk_exposure", "spatial_equity"],
        intent_cartography=["administrative_choropleth", "proximity_overlay"],
        preferred_analysis=["geometry_buffer", "geometry_overlay", "admin_boundary_query",
                            "admin_aggregation", "rate_aggregation"],
        primary_cartography="administrative_choropleth",
        secondary_cartography=["proximity_overlay"],
        default_components=MAP_COMPONENTS_CHART,
        fallbacks=[],
        fallback_links=auto_fallback(),  # ADR-0151 P7：通用兜底链（auto_generated）
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=47,
        schema_version=2,
        workflow=wf(
            "exposure", "vulnerability_profile",
            zh=["易损性", "脆弱群体", "敏感区域画像"],
            en=["vulnerability profile"],
            roles=[
                role("hazard", capability="geometry_buffer", artifacts=("proximity_zone",),
                     geometry=("polygon",)),
                role("receptor", acquisition="data_fabric", required=False,
                     note="敏感群体分布（学校/养老等代理）"),
                boundary_role(),
                denominator_role(),
            ],
            obligations=[
                obl("vulnerability_proxy_disclosure", "disclosure",
                    code="VULNERABILITY_PROXY_SEMANTICS",
                    desc="以设施代理脆弱群体是近似，必须披露代理语义。"),
            ],
            completion=standard_completion(uncertainty=True),
        ),
    ),
    CartographyRecipe(
        id="hazard_buffer_receptor_screen",
        name="危险源缓冲受体筛查",
        description="危险源周边缓冲带内受体筛查（多环缓冲 + 空间连接）：缓冲距离口径披露。",
        intent_tasks=["risk_exposure", "proximity_analysis"],
        intent_cartography=["proximity_overlay", "point_overlay"],
        preferred_analysis=["multi_ring_buffer", "geometry_buffer", "poi_query", "spatial_join",
                            "category_breakdown"],
        primary_cartography="proximity_overlay",
        secondary_cartography=["point_overlay"],
        default_components=MAP_COMPONENTS_CHART,
        fallbacks=[],
        fallback_links=auto_fallback(),  # ADR-0151 P7：通用兜底链（auto_generated）
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=45,
        schema_version=2,
        workflow=wf(
            "exposure", "buffer_receptor_screen",
            zh=["安全距离", "卫生防护距离", "周边筛查", "缓冲区受体"],
            en=["buffer receptor screening", "safety distance"],
            roles=[
                role("hazard", capability="multi_ring_buffer", artifacts=("proximity_zone",),
                     geometry=("polygon",)),
                subject_role(),
            ],
            obligations=[
                obl("buffer_distance_semantics", "disclosure",
                    code="BUFFER_DISTANCE_SEMANTICS",
                    desc="缓冲距离的依据（规范值/用户指定）必须披露。"),
            ],
            completion=standard_completion(),
        ),
    ),
]

RECIPES: List[CartographyRecipe] = _RECIPES
