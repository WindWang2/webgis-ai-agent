"""Raster / 遥感 / 时序表达模型域包（ADR-0101 §B1）。

native：terrain 解析面、光谱指数面、变化对比面 —— raster/fill +
连续色带/分级投影的既有机制族。
planned：hillshade（前端 union 未含 hillshade 图层）、classified raster
（需 color-relief/step 化栅格）、elevation tint+hillshade 合成、SAR
强度/变化（无 SAR artifact）、趋势/异常/不确定性面（无趋势 artifact）、
before/after swipe（runtime 无 swipe 语义）。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel
from app.lib.cartography.model_packs._base import (
    _MAPLIBRE_SPEC_URL,
    _QGIS_URL,
    m,
)

RASTER_REMOTE_SENSING_PACK: List[MapModel] = [
    m(
        id="terrain_analytical_surface", name_zh="地形解析面",
        purpose_zh="坡度/坡向/曲率等地形衍生量的连续色面（分析视图，非晕渲）",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="none",
        color_scheme_kind="perceptual_uniform", default_palette="Magma",
        aliases=["terrain_derivative"],
        accepted_artifact_types=["terrain_surface", "raster_surface"],
        recommended_components=["continuous_colorbar"],
        export_compatibility=["png"],
        fallback_model_id="raster_surface",
        qgis_renderer="singleband pseudocolor",
        pitfalls_zh=[
            "坡向是环形量 —— 线性色带会在 0°/360° 处制造假断裂，需环形配色（planned）",
            "解析面单位是度/百分比/曲率 —— colorbar 必须带单位",
        ],
        sources=[_MAPLIBRE_SPEC_URL, _QGIS_URL],
    ),
    m(
        id="spectral_index_surface", name_zh="光谱指数面",
        purpose_zh="NDVI/NDBI/NDWI 等遥感指数的连续/发散色面渲染",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="none",
        color_scheme_kind="perceptual_uniform", default_palette="Viridis",
        aliases=["remote_sensing_index_map"],
        accepted_artifact_types=["remote_sensing_index", "raster_surface"],
        recommended_components=["continuous_colorbar"],
        export_compatibility=["png"],
        fallback_model_id="raster_surface",
        qgis_renderer="singleband pseudocolor",
        pitfalls_zh=[
            "指数有定义域（NDVI ∈ [-1,1]）—— colorbar 端点按定义域 clamp，不得按样本 min/max 误导",
            "云/水体掩膜应保持透明而非置最低档色",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="change_comparison_map", name_zh="变化对比图",
        purpose_zh="两时相/两情景的变化量分级面（新增/减少双向发散）",
        geometry_kinds=["polygon"], maplibre_layer_type="fill",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="RdBu",
        recommended_classifiers=["std_dev", "natural_breaks"],
        default_class_count=5,
        aliases=["change_map"],
        accepted_artifact_types=["change_set", "admin_aggregate_table"],
        recommended_components=["legend"],
        export_compatibility=["png", "pdf"],
        fallback_model_id="diverging_choropleth",
        qgis_renderer="graduated（变化量）",
        pitfalls_zh=[
            "变化量为 0 必须是色带中点 —— 图例中点标注『无变化』",
            "两期数据口径/分类体系不一致时变化量撒谎 —— 披露两期来源",
        ],
        sources=[_QGIS_URL],
    ),
    # ── planned：需要新运行时能力（诚实登记，不伪装 native）──────────
    m(
        id="hillshade", name_zh="山体阴影",
        purpose_zh="DEM 光照晕渲（地形表达底图/单独产品）",
        geometry_kinds=["raster"], maplibre_layer_type="hillshade",
        classification="none", color_scheme_kind="none",
        runtime_status="planned",
        accepted_artifact_types=["terrain_surface", "raster_surface"],
        qgis_renderer="hillshade 渲染器",
        pitfalls_zh=[
            "planned：hillshade 图层在前端编译器 union 与 raster-dem source 链路均未接线",
            "光源方位角/高度角必须随图披露，否则同一 DEM 可渲染出不同地貌观感",
        ],
        sources=[_MAPLIBRE_SPEC_URL],
    ),
    m(
        id="classified_raster", name_zh="分级栅格图",
        purpose_zh="连续栅格按断点分级为离散色阶（如高程带/温度带）",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="YlOrRd",
        recommended_classifiers=["natural_breaks", "equal_interval"],
        runtime_status="planned",
        accepted_artifact_types=["raster_surface", "terrain_surface"],
        qgis_renderer="paletted / 调色板分级",
        pitfalls_zh=[
            "planned：栅格 step 化着色需 color-relief/服务端重分类链路，本分支未实现",
        ],
        sources=[_QGIS_URL],
    ),
    m(
        id="elevation_tint_hillshade", name_zh="高程分层设色 + 晕渲",
        purpose_zh="hypsometric tint 与 hillshade 合成的经典地形图表达",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="graduated",
        color_scheme_kind="sequential", default_palette="Oranges",
        runtime_status="planned",
        accepted_artifact_types=["terrain_surface"],
        pitfalls_zh=[
            "planned：依赖 hillshade（未接线）与多层栅格合成顺序契约",
        ],
        sources=[_MAPLIBRE_SPEC_URL],
    ),
    m(
        id="sar_intensity_surface", name_zh="SAR 强度面",
        purpose_zh="SAR 后向散射强度（σ⁰）对数/分贝渲染",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="none",
        color_scheme_kind="perceptual_uniform", default_palette="Inferno",
        runtime_status="planned",
        accepted_artifact_types=["raster_surface"],
        pitfalls_zh=[
            "planned：无 SAR 强度 artifact 类型与 dB 归一契约，本分支未实现",
        ],
        sources=[],
    ),
    m(
        id="sar_change_detection", name_zh="SAR 变化检测图",
        purpose_zh="InSAR 相位/SAR 幅度变化的发散色面（形变/语义变化）",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="RdBu",
        runtime_status="planned",
        accepted_artifact_types=["raster_surface"],
        pitfalls_zh=[
            "planned：无 InSAR/相干性 artifact 契约，本分支未实现",
            "形变量色标必须对称且以 0 为中点，否则毫米级形变被误读",
        ],
        sources=[],
    ),
    m(
        id="temporal_trend_surface", name_zh="时序趋势面",
        purpose_zh="像元/单元级时间序列趋势（斜率/显著性）发散渲染",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="RdBu",
        runtime_status="planned",
        accepted_artifact_types=["raster_surface"],
        pitfalls_zh=[
            "planned：需要趋势拟合 artifact（斜率/p 值字段），本分支未实现",
            "不显著趋势应以低饱和/置灰表达，不与显著趋势争色",
        ],
        sources=[],
    ),
    m(
        id="anomaly_surface", name_zh="异常距平面",
        purpose_zh="相对气候态/背景值的距平（anomaly）发散渲染",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="graduated",
        color_scheme_kind="diverging", default_palette="RdBu",
        runtime_status="planned",
        accepted_artifact_types=["raster_surface"],
        pitfalls_zh=[
            "planned：需要背景态参考 artifact，本分支未实现",
        ],
        sources=[],
    ),
    m(
        id="uncertainty_surface", name_zh="不确定性面",
        purpose_zh="插值/模型预测的方差或区间宽度渲染（诚实披露空间）",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="none",
        color_scheme_kind="sequential", default_palette="Purples",
        runtime_status="planned",
        accepted_artifact_types=["raster_surface", "density_surface"],
        pitfalls_zh=[
            "planned：需要不确定性场 artifact（方差/分位带），本分支未实现",
        ],
        sources=[],
    ),
    m(
        id="before_after_swipe", name_zh="前后对比卷帘",
        purpose_zh="两期栅格/影像的 swipe 对比表达",
        geometry_kinds=["raster"], maplibre_layer_type="raster",
        classification="none", color_scheme_kind="none",
        runtime_status="planned",
        accepted_artifact_types=["raster_surface"],
        pitfalls_zh=[
            "planned：runtime 无 swipe 交互语义与导出双帧契约，本分支未实现",
            "导出侧等价物是双帧并排（before/after 双面板），swipe 仅限交互面",
        ],
        sources=[],
    ),
]
