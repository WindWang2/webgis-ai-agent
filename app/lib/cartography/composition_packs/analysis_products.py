"""三维分析产品组合模板 pack（G3D-V2-C / ADR-0276）。

三个**分析产品**模板（区别于 scene_3d.py 的三个**场景**模板）：把真实
三维分析结果（LOS/视域/剖面/土方）作为制图主体，从既有 pack 体系扩展：

- ``composition.mountain_los_obstruction``：山地通视/视域与遮挡图
  （视域面 + LOS 视线 + 地形）；场景披露必填（三维语义产品）。
- ``composition.route_profile_joint``：沿线高程剖面联合制图
  （线路 + elevation_profile 剖面面板 + 统计）；**纯 2D 产品**——矢量
  导出合法（不声明 3D），剖面面板是该产品的必填主体。
- ``composition.earthwork_cut_fill``：土方工程地形图（挖方/填方分区 +
  土方统计 + 方法注记）；场景披露必填（垂直基准是土方量的一部分）。

诚实边界（与 scene_3d.py 同源）：

- 声明 interactive/png 的产品在透视 3D 下比例尺不可信 → scene_disclosure
  required 槽 + scale_bar 由披露卡声明可信度；
- ``route_profile_joint`` 声明 pdf/svg（2D 产品矢量导出合法）——但剖面
  数据的垂直基准仍经 methodology/profile 组件披露；
- 模板只编排既有组件/模型语义（viewshed_map/diverging_choropleth/
  graduated_line/terrain_analytical_surface），不放宽 scene_planning 的
  fail-closed 资格。
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

ANALYSIS_PRODUCTS_PACK = [
    composition(
        cid="composition.mountain_los_obstruction",
        name="山地通视与遮挡图",
        description=(
            "观察点视域面 + 两点通视线 + 首遮挡点的山地三维分析产品："
            "视域/LOS 结果在地形起伏上的联合呈现，通视统计与遮挡诊断"
            "面板联动；场景披露（垂直基准/比例尺可信度）必填"
        ),
        models=["viewshed_map", "terrain_analytical_surface", "graduated_line"],
        outputs=["interactive", "png"],
        profile="academic",
        fallback="composition.viewshed_scene_3d",
        priority=39,
        tags=["scene3d", "los", "viewshed", "obstruction", "g3dv2c"],
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
            stats_slot(cardinality="required"),
            charts_slot(max_count=2),
            attribution_slot(),
            map_border_slot(),
            export_slot(),
        ],
    ),
    composition(
        cid="composition.route_profile_joint",
        name="沿线高程剖面联合制图",
        description=(
            "线路/管线/道路纵断面的 2D 联合制图产品：线路走向图 + "
            "elevation_profile 剖面面板（ref:profile-* 工件绑定）+ 爬升/"
            "坡度统计；纯 2D 表达，矢量导出合法；剖面垂直基准随面板披露"
        ),
        # 主语义模型只留 graduated_line（线路）：terrain_analytical_surface
        # × pdf/svg 的键选模板归既有报告模板族——本模板不以地形面为钥匙
        # 抢占通用报告产品的选择位（selection 回归见 golden corpus）。
        models=["graduated_line"],
        outputs=["interactive", "png", "pdf", "svg"],
        profile="report",
        fallback="composition.terrain_analysis",
        priority=38,
        tags=["profile", "route", "elevation", "g3dv2c"],
        slots=[
            title_slot(),
            subtitle_slot(),
            legend_slot(["legend"], zone="bottom-right"),
            north_arrow_slot(),
            scale_bar_slot(),
            # 剖面面板是本产品的主体（required）——缺剖面只剩一条线，
            # 不是「沿线剖面联合制图」
            slot("elevation_profile", "analysis.elevation_profile",
                 ["elevation_profile"], cardinality="required", required=True,
                 min_count=1, position_zone="bottom-left",
                 fallback_zones=["bottom-right"]),
            stats_slot(cardinality="required"),
            slot("methodology_note", "disclosure.methodology_note",
                 ["methodology_note"], cardinality="recommended",
                 position_zone="bottom-right",
                 fallback_zones=["bottom-left"]),
            attribution_slot(),
            map_border_slot(),
            export_slot(),
        ],
    ),
    composition(
        cid="composition.earthwork_cut_fill",
        name="土方工程地形图",
        description=(
            "基准面土方量的工程制图产品：挖方/填方分区（发散设色）+ "
            "场平范围 + 土方统计（cut/fill/net m³）+ 方法注记（中点求积/"
            "分辨率/nodata 披露）；场景披露必填（垂直基准是土方量语义"
            "的一部分）"
        ),
        models=["diverging_choropleth", "terrain_analytical_surface"],
        outputs=["interactive", "png"],
        profile="report",
        fallback="composition.terrain_analysis",
        priority=38,
        tags=["earthwork", "cut", "fill", "volume", "g3dv2c"],
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
            # 土方量必须带方法注记（求积口径/分辨率敏感性/nodata）——
            # 无方法披露的体积是伪精确
            slot("methodology_note", "disclosure.methodology_note",
                 ["methodology_note"], cardinality="required", required=True,
                 min_count=1, position_zone="bottom-right",
                 fallback_zones=["bottom-left"]),
            stats_slot(cardinality="required"),
            charts_slot(max_count=1),
            attribution_slot(),
            map_border_slot(),
            export_slot(),
        ],
    ),
]
