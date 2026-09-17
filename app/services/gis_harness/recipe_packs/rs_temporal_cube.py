"""领域包：RS Temporal Cube（时序立方体 / SAR-光学融合 / 防泄漏样本）。

本包是 remote_sensing/sar/temporal 之外的时序立方体产品族：盘点 → 对齐
→ 特征 → 融合 → 样本 → 产品。义务与回退对齐 skill
``rs_temporal_cube_workflow``（网格恒等 typed 拒绝 + 缺口类型化披露）。

Kill-switch：环境变量 ``RS_TEMPORAL_CUBE=0`` 时本包 RECIPES 为空——
禁用后 registry 行为与 master 逐位一致（additive 门禁，
GIS_SKILL_POLICY 风格）。
"""
from __future__ import annotations

import os
from typing import List

from app.services.gis_harness.recipe_packs._kit import (
    MAP_COMPONENTS_CHART,
    MAP_COMPONENTS_CONTINUOUS,
    fb,
    obl,
    role,
    standard_completion,
    wf,
)
from app.services.gis_harness.recipes import CartographyRecipe, RecipeFallback

_GRID_IDENTITY = obl(
    "rs_cube_grid_identity", "precondition",
    precondition="grid_identity_match",
    code="RS_CUBE_GRID_IDENTITY_REQUIRED",
    desc="光学/SAR 跨模态对齐要求网格恒等（crs/width/height/transform）"
         "逐键可核；不一致 typed 拒绝，绝不静默重采样。",
    action="degrade_with_disclosure",
)
_GAP_DISCLOSURE = obl(
    "rs_cube_gap_disclosure", "disclosure",
    code="RS_CUBE_GAP_DISCLOSURE_REQUIRED",
    desc="缺测/云/阴影/layover/低质以类型化缺口计数披露——绝不充当 0 或"
         "有效观测；missing_acquisition 槽位不伪造资产。",
    action="warn",
)
_GRID_RF = RecipeFallback(
    when="网格恒等不一致（跨模态网格漂移）",
    reason_code="RS_CUBE_GRID_IDENTITY_REQUIRED", use="raster_view")
_GRID_FALLBACK = fb(
    "RS_CUBE_GRID_IDENTITY_REQUIRED", frm="rs_cube_alignment", to="raster_view",
    downgrade="degraded",
    disclosure="网格恒等不一致：跳过跨模态对齐，仅呈现单模态原始时序"
               "（覆盖卡披露缺口）。",
)

_RS_CUBE_SUBJECT = role(
    "subject", capability="rs_cube_describe",
    artifacts=("rs_cube_descriptor", "raster_surface"), geometry=("raster",),
    note="时序栅格立方体（光学/SAR 多期影像；refs-only 描述符）",
)

_RECIPES_ALL: List[CartographyRecipe] = [
    CartographyRecipe(
        id="rs_temporal_cube_product",
        name="遥感时序立方体产品",
        description="光学/SAR 时序立方体联合分析产品：对齐计划 + 类型化"
                    "缺口账 + 时序特征 + 融合图层 + 图/表通道。",
        intent_tasks=["temporal_trend", "change_detection",
                      "vegetation_index"],
        intent_cartography=["raster_surface"],
        preferred_analysis=["rs_cube_describe", "rs_cube_alignment",
                            "rs_temporal_feature_pack", "rs_joint_fusion",
                            "raster_source"],
        optional_analysis=["rs_sample_split", "zonal_statistics"],
        primary_cartography="raster_surface",
        secondary_cartography=[],
        default_components=MAP_COMPONENTS_CHART,
        fallbacks=[_GRID_RF],
        export_profile={"formats": ["png", "csv"], "chart": True},
        priority=46,
        schema_version=2,
        workflow=wf(
            "remote_sensing", "rs_temporal_cube",
            zh=["时序立方体", "SAR 光学融合", "物候", "全年时序",
                "多期影像"],
            en=["temporal cube", "sar optical fusion", "multi-temporal"],
            roles=[_RS_CUBE_SUBJECT],
            obligations=[_GRID_IDENTITY, _GAP_DISCLOSURE],
            fallbacks=[_GRID_FALLBACK],
            completion=standard_completion(uncertainty=True),
            recompute=["data", "algorithm", "parameter", "output"],
        ),
    ),
    CartographyRecipe(
        id="rs_cube_coverage_audit",
        name="时序立方体覆盖审计",
        description="时序数据体检产品：缺口账（按类型化码）+ 配对时距 + "
                    "覆盖卡表格（不产分析结论）。",
        intent_tasks=["temporal_trend"],
        intent_cartography=["raster_surface"],
        preferred_analysis=["rs_cube_describe", "rs_cube_alignment"],
        primary_cartography="raster_surface",
        default_components=MAP_COMPONENTS_CONTINUOUS,
        fallbacks=[_GRID_RF],
        export_profile={"formats": ["csv"]},
        priority=45,
        schema_version=2,
        workflow=wf(
            "remote_sensing", "rs_cube_audit",
            zh=["时序体检", "缺口报告", "数据覆盖"],
            en=["cube audit", "gap report", "coverage"],
            roles=[_RS_CUBE_SUBJECT],
            obligations=[_GAP_DISCLOSURE],
            fallbacks=[_GRID_FALLBACK],
            completion=standard_completion(uncertainty=False),
            recompute=["data"],
        ),
    ),
]

#: Kill-switch：``RS_TEMPORAL_CUBE=0`` → 空 pack（master 行为逐位一致）。
RECIPES: List[CartographyRecipe] = (
    [] if os.environ.get("RS_TEMPORAL_CUBE", "").strip().lower() in
    ("0", "off", "no", "false") else _RECIPES_ALL
)
