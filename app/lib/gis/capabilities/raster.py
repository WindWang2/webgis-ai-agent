"""栅格/遥感/地形 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="raster_source", name="栅格数据源", category="raster",
            domain="raster",
            description="DEM/遥感栅格获取。",
            output_artifact_types=["terrain_surface", "raster_surface"],
            geometry_requirements=["raster"],
            compatible_map_models=["raster_surface"],
            purpose_template="栅格数据源",
        ),

        CapabilityDescriptor(
            id="terrain_slope", name="坡度分析", category="raster",
            domain="raster", description="DEM 坡度。",
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["terrain_surface"],
            purpose_template="坡度分析",
        ),

        CapabilityDescriptor(
            id="terrain_aspect", name="坡向分析", category="raster",
            domain="raster", description="DEM 坡向。",
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["terrain_surface"],
            purpose_template="坡向分析",
        ),

        CapabilityDescriptor(
            id="terrain_hillshade", name="山体阴影", category="raster",
            domain="raster", description="DEM 山体阴影。",
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["terrain_surface"],
            purpose_template="山体阴影",
        ),

        CapabilityDescriptor(
            id="ndvi", name="NDVI 植被指数", category="raster",
            domain="raster", description="遥感 NDVI 计算。",
            input_artifact_types=["terrain_surface", "raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="NDVI 指数",
        ),

        CapabilityDescriptor(
            id="band_math", name="波段/栅格代数", category="raster",
            domain="raster",
            description="逐像元栅格代数（A/B 表达式、常数运算；A 为基准网格，B 自动对齐）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="波段/栅格代数",
        ),

        CapabilityDescriptor(
            id="raster_change_detection", name="双时相栅格变化检测", category="raster",
            domain="raster",
            description="两个栅格工件的对齐像元级变化检测（差值/绝对差/归一化差 + 阈值分类）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface", "change_set"],
            compatible_map_models=["raster_surface"],
            purpose_template="双时相栅格变化检测",
        ),

        CapabilityDescriptor(
            id="zonal_statistics", name="分区统计", category="raster",
            domain="raster", description="面内栅格 min/max/mean/sum 统计。",
            input_artifact_types=["raster_surface", "polygon_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="分区统计",
        ),

        CapabilityDescriptor(
            id="raster_reclassify", name="栅格重分类", category="raster",
            domain="raster", description="连续栅格值按方案映射为离散类别。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="栅格重分类",
        ),

        CapabilityDescriptor(
            id="raster_resample", name="栅格重采样", category="raster",
            domain="raster", description="改变像元大小和/或 CRS（对齐预处理）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="栅格重采样",
        ),

        # ── VNext（ADR-0099）：类型化光谱指数 + SAR 域能力 ─────────────

        CapabilityDescriptor(
            id="spectral_index", name="类型化光谱指数", category="raster",
            domain="raster",
            description="按语义角色（red/nir/swir1/...）显式命名的 12 公式族光谱指数（含出处与值域诚实报告）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="光谱指数计算",
        ),

        CapabilityDescriptor(
            id="sar_analysis", name="SAR 时序/极化分析", category="raster",
            domain="raster",
            description="SAR 时序栈统计（含 CV/鲁棒分位数）、时序合成、VV/VH 极化比与双时相对数比值"
                        "（滤波/定标为独立能力：sar_speckle_filtering / sar_radiometric_calibration）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="SAR 时序/极化分析",
        ),

        # Foundation V2（A6）：斑点滤波/辐射定标 planned→native（实现 +
        # 工具候选落地；原 planned 非可执行披露替换为实现级披露）。
        CapabilityDescriptor(
            id="sar_speckle_filtering", name="SAR 斑点滤波", category="raster",
            domain="raster",
            description="SAR 相干斑点噪声抑制（Lee 1980 / Refined-Lee 边缘方向 MMSE / Frost 1982；"
                        "ENL 显式优先、缺省矩估计披露；refined_lee 为 7 子窗近似实现）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="SAR 斑点滤波",
        ),

        CapabilityDescriptor(
            id="sar_radiometric_calibration", name="SAR 辐射定标", category="raster",
            domain="raster",
            description="DN → β⁰/σ⁰/γ⁰ 常数辐射定标（定标常数显式必需；逐像元 LUT 与热噪声去除未实现——披露）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="SAR 辐射定标",
        ),

        CapabilityDescriptor(
            id="sar_texture", name="GLCM 纹理特征", category="raster",
            domain="raster",
            description="窗口化 GLCM 纹理属性（Haralick 1973：contrast/homogeneity/entropy 等 9 项；"
                        "纯 numpy 手工实现，量化 2-98 分位、P+Pᵀ 对称、多方向均值）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="GLCM 纹理分析",
        ),

        CapabilityDescriptor(
            id="raster_dimensionality_reduction", name="波段降维（PCA）", category="raster",
            domain="raster",
            description="多波段栅格 SVD 主成分分析（协方差/相关 PCA、explained variance、载荷与前 k 分量栅格）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="波段 PCA 降维",
        ),

        CapabilityDescriptor(
            id="tasseled_cap_transformation", name="Tasseled Cap 冠层变换", category="raster",
            domain="raster",
            description="传感器系数注册表驱动的亮度/绿度/湿度三轴变换"
                        "（landsat5_tm=crist_cicone1984、landsat8_oli=baig2014、sentinel2=shi_xu2019；六语义角色显式映射）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="Tasseled Cap 变换",
        ),
]
