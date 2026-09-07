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

        # ── Foundation V3：遥感 V3 批次（波段栈科学算法族，新能力族）──
        CapabilityDescriptor(
            id="mnf_transform", name="MNF 变换", category="raster",
            domain="raster",
            description="最小噪声分数变换（Green 1988：局部差分噪声白化 + 白化空间 PCA，"
                        "分量按 SNR 排序，含逆变换去噪重建）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="MNF 噪声白化降维",
        ),

        CapabilityDescriptor(
            id="ica_transform", name="ICA 独立成分分析", category="raster",
            domain="raster",
            description="FastICA 独立成分分解（Hyvärinen 1999；random_state=42，"
                        "whiten=unit-variance；收敛性显式披露）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="ICA 独立成分分解",
        ),

        CapabilityDescriptor(
            id="spectral_target_detection", name="光谱目标检测", category="raster",
            domain="raster",
            description="已知光谱签名下的逐像元目标检测/相似度：光谱角 SAM（Kruse 1993）、"
                        "光谱信息散度 SID（Chang 2000）、匹配滤波（Boardman 1995）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="光谱目标检测",
        ),

        CapabilityDescriptor(
            id="rx_anomaly_detection", name="RX 异常检测", category="raster",
            domain="raster",
            description="Reed-Xiaoli 全局 RX 异常检测（Mahalanobis 距离 + 尺度不变岭正则；"
                        "单高斯背景假设，阈值启发式披露；局部/核 RX 未实现）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="RX 异常检测",
        ),

        CapabilityDescriptor(
            id="image_segmentation", name="图像分割", category="raster",
            domain="raster",
            description="k-means 分割基座（Lloyd 1982；光谱 z-score + 加权空间坐标特征，"
                        "random_state=42 确定性；非 SLIC——无几何紧致约束/watershed 精化，披露）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="图像分割",
        ),

        CapabilityDescriptor(
            id="endmember_extraction", name="端元提取", category="raster",
            domain="raster",
            description="VCA 顶点成分分析端元提取（Nascimento & Dias 2005 的简化确定性变体；"
                        "纯像元假设；EXPERIMENTAL——结果需人工核验）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="端元提取",
        ),

        CapabilityDescriptor(
            id="band_statistics", name="波段统计", category="raster",
            domain="raster",
            description="波段×波段 Pearson 相关矩阵 + 逐对样本数（公共有效掩膜约定披露；"
                        "stats_table 产物）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["stats_table"],
            purpose_template="波段相关统计",
        ),

        CapabilityDescriptor(
            id="temporal_feature_extraction", name="时序特征提取", category="raster",
            domain="raster",
            description="逐像元时序特征（min/max/mean/std/amplitude/first−last + 单周期谐波；"
                        "无物候模型拟合——线性趋势 + 单谐波边界披露）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="时序特征提取",
        ),

        CapabilityDescriptor(
            id="radiometric_normalization", name="辐射归一化", category="raster",
            domain="raster",
            description="稳健跨波段/跨场景归一化（2-98 分位拉伸或参考场景分位匹配；"
                        "NaN-aware 分位、逐波段分位披露；线性增益/偏移不变）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="辐射归一化",
        ),

        CapabilityDescriptor(
            id="cloud_qc_advisory", name="云 QC 咨询掩膜", category="raster",
            domain="raster",
            description="亮度阈值 + 可选 NDVI 近零的云咨询掩膜（EXPERIMENTAL——非 Fmask/"
                        "云概率：无热红外、卷云、视差/多时相检验，强披露）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="云 QC 咨询掩膜",
        ),

        # ── Foundation V3：SAR 批次新能力族（相干性 / 地形几何校正）────
        # （热噪声/量纲换算/ENL 图/MT-Lee 折入既有 sar_radiometric_calibration
        #  与 sar_speckle_filtering 能力，不另立新族。）
        CapabilityDescriptor(
            id="sar_coherence", name="SAR 相干性估计", category="raster",
            domain="raster",
            description="复数 SLC 双通道相干性 γ 窗口估计（|Σ a·b*|/√(Σ|a|²Σ|b|²)；"
                        "EXPERIMENTAL——无轨道元数据/配准质量披露；强度-only 输入"
                        "类型化拒绝）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="SAR 相干性估计",
        ),

        CapabilityDescriptor(
            id="sar_terrain_geometry_correction", name="SAR 地形几何/辐射校正",
            category="raster",
            domain="raster",
            description="SAR 地形效应校正：RTC gamma 平坦化（Small 2011，"
                        "γ_flat=σ⁰·cosθi/cosθl）与叠掩/阴影几何分类"
                        "（{0=normal,1=layover,2=shadow,3=nodata}；Horn 坡度坡向 + "
                        "本地入射角；range-only 无轨道元数据简化披露）。",
            input_artifact_types=["raster_surface", "terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="SAR 地形几何/辐射校正",
        ),
    ]
