"""三维制图产品组合模板 pack（G3D05 / ADR-0275）。

三类 3D/2.5D 产品模板，从既有 2D pack 体系（ADR-0238 D1）扩展：

- ``composition.terrain_3d_scene``：三维地形专题（地形面 + 晕渲 + 等高
  线），场景披露必填（夸张系数/垂直基准），比例尺在透视视角下必须带
  可信度披露。
- ``composition.urban_3d_skyline``：建筑挤出/城市天际线 + 行政区统计，
  挤出仅在有高度证据时成立（scene_planning fail-closed，模板不放宽）。
- ``composition.viewshed_scene_3d``：视域分析三维场景（视域面 + 地形）。

诚实边界（与 scene_planning/scene_quality 一致，模板不越权）：

- 输出目标只声明 ``interactive`` 与 ``png``（浏览器截图保留透视）；
  ``pdf``/``svg``/``print`` 由导出管线显式降级为 2D/2.5D 表达并附
  typed 收据（scene_quality ``SCENE_EXPORT_VECTOR_DEGRADE``），模板
  不得直接声明矢量目标支持 3D。
- ``scene_disclosure`` 槽必填：三维产品不带场景披露 = 误导性产品，
  conformance 层按 required slot 拦截。
"""
from __future__ import annotations

from app.lib.cartography.composition_packs._base import (
    attribution_slot,
    charts_slot,
    composition,
    export_slot,
    legend_slot,
    map_border_slot,
    north_arrow_slot,
    scale_bar_slot,
    slot,
    stats_slot,
    subtitle_slot,
    title_slot,
)

SCENE_3D_PACK = [
    composition(
        cid="composition.terrain_3d_scene",
        name="三维地形场景图",
        description=(
            "地形+晕渲+等高线的三维专题产品：透视相机下的地形起伏表达，"
            "场景披露（夸张/垂直基准/比例尺可信度）必填；矢量导出自动"
            "降级 2.5D 并附收据"
        ),
        models=["terrain_analytical_surface", "elevation_tint_hillshade",
                "hillshade", "isoline_contour"],
        outputs=["interactive", "png"],
        profile="academic",
        fallback="composition.remote_sensing_map",
        priority=41,
        tags=["scene3d", "terrain", "hillshade", "g3d05"],
        slots=[
            title_slot(),
            subtitle_slot(),
            legend_slot(["continuous_colorbar", "legend"], zone="bottom-right"),
            north_arrow_slot(),
            # 比例尺在透视视角不可信 —— 必填但由披露卡声明可信度，
            # 不允许模板级静默。
            scale_bar_slot(),
            # 三维产品强制场景披露（mode/exaggeration/垂直基准/比例尺
            # 可信度）：required 槽缺失在 conformance 层拦截。
            slot("scene_disclosure", "disclosure.scene_disclosure",
                 ["scene_disclosure"], cardinality="required", required=True,
                 min_count=1, position_zone="bottom-right",
                 fallback_zones=["bottom-left"]),
            slot("elevation_profile", "analysis.elevation_profile",
                 ["elevation_profile"], cardinality="optional",
                 position_zone="bottom-left",
                 fallback_zones=["bottom-right"]),
            stats_slot(),
            charts_slot(max_count=2),
            attribution_slot(),
            map_border_slot(),
            export_slot(),
        ],
    ),
    composition(
        cid="composition.urban_3d_skyline",
        name="城市三维天际线图",
        description=(
            "建筑挤出 + 行政区统计的三维城市产品：挤出高度仅采信有证据的"
            "高度字段（scene_planning fail-closed），行政区统计图表与三维"
            "场景联动；场景披露必填"
        ),
        models=["extrusion_3d", "administrative_choropleth"],
        outputs=["interactive", "png"],
        profile="presentation",
        fallback="composition.statistical_map",
        priority=40,
        tags=["scene3d", "extrusion", "urban", "skyline", "g3d05"],
        slots=[
            title_slot(),
            subtitle_slot(),
            legend_slot(["legend", "categorical_legend"]),
            north_arrow_slot(),
            scale_bar_slot(),
            slot("scene_disclosure", "disclosure.scene_disclosure",
                 ["scene_disclosure"], cardinality="required", required=True,
                 min_count=1, position_zone="bottom-right",
                 fallback_zones=["bottom-left"]),
            stats_slot(),
            charts_slot(max_count=3),
            attribution_slot(),
            map_border_slot(),
            export_slot(),
        ],
    ),
    composition(
        cid="composition.viewshed_scene_3d",
        name="三维视域场景图",
        description=(
            "视域分析 + 地形的三维场景产品：观察点视域范围在地形起伏上"
            "的呈现，剖面面板可选挂接；场景披露必填"
        ),
        models=["viewshed_map", "terrain_analytical_surface"],
        outputs=["interactive", "png"],
        profile="academic",
        fallback="composition.terrain_analysis",
        priority=40,
        tags=["scene3d", "viewshed", "terrain", "g3d05"],
        slots=[
            title_slot(),
            subtitle_slot(),
            legend_slot(["legend", "categorical_legend"], zone="bottom-right"),
            north_arrow_slot(),
            scale_bar_slot(),
            slot("scene_disclosure", "disclosure.scene_disclosure",
                 ["scene_disclosure"], cardinality="required", required=True,
                 min_count=1, position_zone="bottom-right",
                 fallback_zones=["bottom-left"]),
            slot("elevation_profile", "analysis.elevation_profile",
                 ["elevation_profile"], cardinality="recommended",
                 position_zone="bottom-left",
                 fallback_zones=["bottom-right"]),
            attribution_slot(),
            map_border_slot(),
            export_slot(),
        ],
    ),
]
