"""领域包：SAR（合成孔径雷达）。

红线：speckle 滤波 / 辐射定标两个前置 capability 为 planned（诚实不可执行）；
依赖它们的定量比较工作流必须表达「定标证据义务」，未满足时只允许描述性
相对比较或阻断 —— 绝不伪造定量结论。
"""
from __future__ import annotations

from typing import List

from app.services.gis_harness.recipe_packs._kit import (
    MAP_COMPONENTS_CONTINUOUS,
    fb,
    obl,
    role,
    standard_completion,
    wf,
)
from app.services.gis_harness.recipes import CartographyRecipe, RecipeFallback

_SAR_SUBJECT = role(
    "subject", capability="raster_source",
    artifacts=("raster_surface",), geometry=("raster",),
    note="SAR 影像（单极化/双极化/极化组合）",
)
_CALIBRATION_OBLIGATION = obl(
    "sar_calibration_evidence", "transformation",
    code="SAR_CALIBRATION_EVIDENCE_REQUIRED",
    desc="定量后向散射比较需要辐射定标证据（σ⁰/γ⁰）；未定标只能做描述性相对比较。",
    action="degrade_with_disclosure",
)

_RECIPES: List[CartographyRecipe] = [
    CartographyRecipe(
        id="sar_backscatter_overview",
        name="SAR 后向散射概览",
        description="SAR 影像后向散射强度概览（描述性）：极化语义披露，不做定量跨景比较。",
        intent_tasks=["sar_analysis", "raster_distribution"],
        intent_cartography=["raster_surface"],
        preferred_analysis=["raster_source", "sar_analysis"],
        optional_analysis=["sar_speckle_filtering"],
        primary_cartography="raster_surface",
        default_components=MAP_COMPONENTS_CONTINUOUS,
        fallbacks=[],
        export_profile={"formats": ["png"]},
        priority=44,
        schema_version=2,
        workflow=wf(
            "sar", "backscatter_overview",
            zh=["sar", "后向散射", "雷达影像"],
            en=["sar backscatter", "sar overview"],
            roles=[_SAR_SUBJECT],
            obligations=[
                obl("sar_polarization_disclosure", "disclosure",
                    code="SAR_POLARIZATION_SEMANTICS",
                    desc="极化方式（VV/VH/HH/HV）决定散射解释，必须披露。"),
            ],
            completion=standard_completion(),
            recompute=["data", "algorithm", "output"],
        ),
    ),
    CartographyRecipe(
        id="sar_calibrated_comparison",
        name="SAR 定标对比",
        description="跨景 SAR 定量比较：辐射定标证据义务（planned 前置诚实呈现）；未定标降级描述比较。",
        intent_tasks=["sar_analysis", "change_detection"],
        intent_cartography=["raster_surface"],
        preferred_analysis=["raster_source", "sar_analysis"],
        optional_analysis=["sar_radiometric_calibration", "sar_speckle_filtering"],
        primary_cartography="raster_surface",
        default_components=MAP_COMPONENTS_CONTINUOUS,
        fallbacks=[RecipeFallback(when="calibration unavailable", reason_code="SAR_CALIBRATION_EVIDENCE_REQUIRED",
                                  use="descriptive_comparison")],
        export_profile={"formats": ["png"]},
        priority=45,
        schema_version=2,
        workflow=wf(
            "sar", "calibrated_comparison",
            zh=["sar对比", "定标", "绝对后向散射", "辐射定标"],
            en=["sar calibration", "calibrated comparison"],
            roles=[_SAR_SUBJECT],
            obligations=[
                _CALIBRATION_OBLIGATION,
                obl("sar_speckle_preprocess", "transformation",
                    code="SAR_SPECKLE_FILTER_PLANNED",
                    desc="斑点噪声抑制（planned）：不可用时定量比较需披露噪声风险。"),
            ],
            completion=standard_completion(uncertainty=True),
            evidence=["calibration_evidence"],
            fallbacks=[fb("SAR_CALIBRATION_EVIDENCE_REQUIRED", frm="absolute_backscatter_comparison",
                          to="descriptive_relative_comparison", downgrade="proxy",
                          disclosure="无定标证据：仅做相对明暗对比，不做绝对量级结论。",
                          blocks=False)],
        ),
    ),
    CartographyRecipe(
        id="insar_deformation_screening",
        name="InSAR 形变筛查",
        description="InSAR 地表形变筛查：干涉处理链（planned 能力诚实呈现），不可用时阻断或转述外部产品。",
        intent_tasks=["sar_analysis", "risk_exposure"],
        intent_cartography=["raster_surface", "point_overlay"],
        preferred_analysis=["raster_source", "sar_analysis"],
        optional_analysis=["sar_radiometric_calibration"],
        primary_cartography="raster_surface",
        secondary_cartography=["point_overlay"],
        default_components=MAP_COMPONENTS_CONTINUOUS,
        fallbacks=[],
        export_profile={"formats": ["png"]},
        priority=46,
        schema_version=2,
        workflow=wf(
            "sar", "insar_deformation",
            zh=["insar", "形变", "沉降监测", "地面沉降", "干涉"],
            en=["insar", "deformation mapping", "subsidence"],
            roles=[_SAR_SUBJECT],
            obligations=[
                # R1-A8：干涉测量的门槛是「景数」（时间栈基数），不是单景
                # 波段数 —— 双极化单景（VV+VH = 2 band）不构成干涉对。用
                # min_temporal_observations:2 表达 ≥2 景（同一 precondition
                # 引擎，语义 = 场景级时间观测数）。
                obl("insar_processing_chain_planned", "temporal",
                    precondition="min_temporal_observations:2",
                    code="INSAR_STACK_INSUFFICIENT",
                    desc="干涉测量需要双景及以上 SLC 栈（≥2 景时间观测）；"
                         "双极化单景不构成干涉对，禁止形变结论。",
                    action="block_method"),
                obl("insar_temporal_baseline", "temporal",
                    code="INSAR_TEMPORAL_BASELINE_REQUIRED",
                    desc="时序 InSAR 需要长时序栈与基线信息；不足时只做描述性筛查。",
                    action="degrade_with_disclosure"),
            ],
            completion=standard_completion(uncertainty=True),
            fallbacks=[fb("INSAR_STACK_INSUFFICIENT", frm="deformation_map", to="summary",
                          downgrade="not_allowed", blocks=True,
                          disclosure="SLC 栈不足：无法做形变测量，禁止伪形变图。")],
        ),
    ),
    CartographyRecipe(
        id="sar_change_detection_workflow",
        name="SAR 变化检测",
        description="双时相 SAR 变化检测（强度差/比值）：定标义务 + 相干性义务披露。",
        intent_tasks=["sar_analysis", "change_detection"],
        intent_cartography=["raster_surface"],
        preferred_analysis=["raster_source", "sar_analysis", "raster_change_detection"],
        optional_analysis=["sar_speckle_filtering", "sar_radiometric_calibration"],
        primary_cartography="raster_surface",
        default_components=MAP_COMPONENTS_CONTINUOUS,
        fallbacks=[],
        export_profile={"formats": ["png"]},
        priority=45,
        schema_version=2,
        workflow=wf(
            "sar", "change_detection",
            zh=["sar变化", "雷达变化检测"],
            en=["sar change detection"],
            roles=[
                _SAR_SUBJECT,
                role("comparison_time", capability="raster_source",
                     artifacts=("raster_surface",), geometry=("raster",),
                     policy="degrade",
                     disclosure="缺少对比期影像：仅呈现单期，不做变化结论。"),
            ],
            obligations=[
                _CALIBRATION_OBLIGATION,
                obl("sar_change_dual_date", "temporal",
                    code="SAR_CHANGE_DUAL_DATE_REQUIRED",
                    desc="变化检测需要两期影像；缺失时阻断变化结论。",
                    action="degrade_with_disclosure"),
            ],
            completion=standard_completion(uncertainty=True),
            fallbacks=[fb("SAR_CHANGE_DUAL_DATE_REQUIRED", frm="sar_change_map", to="single_date_view",
                          downgrade="degraded",
                          disclosure="缺少对比期：仅呈现单期后向散射，不做变化结论。")],
        ),
    ),
    CartographyRecipe(
        id="sar_flood_mapping",
        name="SAR 洪水制图",
        description="SAR 洪水范围制图（水体镜面反射低后向散射）：阈值敏感性与定标义务。",
        intent_tasks=["sar_analysis", "risk_exposure", "watershed_analysis"],
        intent_cartography=["raster_surface", "proximity_overlay"],
        preferred_analysis=["raster_source", "sar_analysis", "raster_reclassify", "zonal_statistics"],
        optional_analysis=["sar_radiometric_calibration"],
        primary_cartography="raster_surface",
        secondary_cartography=["proximity_overlay"],
        default_components=MAP_COMPONENTS_CONTINUOUS,
        fallbacks=[],
        export_profile={"formats": ["png"]},
        priority=46,
        schema_version=2,
        workflow=wf(
            "sar", "flood_mapping",
            zh=["sar洪水", "洪水范围", "淹没提取"],
            en=["sar flood mapping"],
            roles=[_SAR_SUBJECT],
            obligations=[
                _CALIBRATION_OBLIGATION,
                obl("sar_flood_threshold", "disclosure",
                    code="SAR_FLOOD_THRESHOLD_SENSITIVITY",
                    desc="水体分割阈值影响淹没面积，需披露阈值依据。"),
            ],
            completion=standard_completion(uncertainty=True),
        ),
    ),
]

RECIPES: List[CartographyRecipe] = _RECIPES
