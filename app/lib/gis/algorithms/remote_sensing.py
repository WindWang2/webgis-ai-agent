"""遥感 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 remote_sensing 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：本包为光谱指数族 / 双时相变化检测 / SAR 补齐科学
元数据（出处 / CRS 类 / 前置条件 / 不确定性 / conformance 节点）与参数
契约。实现位于 app/lib/geo_analysis/{spectral,raster_change,sar_temporal}.py，
工具层（app/tools/{remote_sensing,change_detection}.py）只做
validate → 调实现 → 挂证据块。

诚实声明：sar.speckle_filter / sar.radiometric_calibration 是 planned
条目（无实现、无工具候选）——斑点滤波与辐射定标未实现，绝不伪装 native。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="remote.ndvi", name="NDVI 植被指数", category="remote_sensing",
            capabilities=["ndvi"],
            input_artifact_types=["raster_surface", "terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["compute_ndvi", "compute_vegetation_index"],
            cpu_cost="medium", memory_cost="high", io_cost="medium",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=10,
            algorithm_family="spectral_index",
            method_references=["rouse1974"],
            assumptions=[
                "反射率需 0-1 定标；零分母→NaN（nodata 像元不稀释统计）",
                "在线路径按 STAC 波段语义取 B04/B08（显式角色映射，非位置猜测）",
            ],
            limitations=[
                "比值指数对线性缩放不变，但对云影/气溶胶/定标漂移敏感",
                "无大气校正补偿，跨期可比性依赖同一 L2A 产品线",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_spectral_engine.py::test_compute_index_array_ndvi",
                "tests/unit/test_spectral_engine.py::test_compute_ndvi_coverage_excludes_nodata",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_family_hand_cases_exact",
            ],
        ),

        AlgorithmDescriptor(
            id="remote.change.raster", name="双时相栅格变化检测", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_raster_change"],
            cpu_cost="high", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD",
            compatible_map_models=["raster_surface"], priority=10,
            version="1.0",
            algorithm_family="change_detection",
            assumptions=[
                "A（T1）网格为基准，B 经 WarpedVRT 对齐；对齐事实进质量证据",
                "有效像元 = 双方都有效（任一 nodata → nodata）",
            ],
            limitations=[
                "差值法对配准/辐射差异敏感，无语义分类（变化≠地类转移）",
                "normalized_difference 零分母 → nodata（不产 inf）",
            ],
            crs_class="RASTER_GRID",
        ),

        # ── VNext：类型化光谱指数 / CVA / 比值变化 ─────────────────────
        AlgorithmDescriptor(
            id="remote.spectral_index", name="类型化光谱指数（12 公式族）", category="remote_sensing",
            capabilities=["spectral_index"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["compute_spectral_index"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="spectral_index",
            method_references=["rouse1974", "huete1988", "gao1996", "xu2006",
                               "zha_woodcock2003", "key_nottrott2011", "mcfeeters1996"],
            assumptions=[
                "波段按语义角色显式命名（band_map），绝不按波段位置猜测",
                "线性定标先于公式（DN/10000→反射率）；零分母→NaN",
                "超理论值域只报告不钳制（out_of_range_fraction）",
            ],
            limitations=[
                "公式出处逐指数声明（gndvi/msavi/ndmi 无词表出处，诚实留空）",
                "EVI/SAVI 常数项只在反射率单位下成立（#382）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["band_semantics_required"],
            numerical_tolerance="conformance fixture 上与手算精确一致（float64）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_family_hand_cases_exact",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_zero_denominator_and_scale",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_missing_role_typed_error",
                "tests/unit/lib/test_spectral_science_vnext.py::test_spectral_dn_input_out_of_range_reported",
            ],
            parameter_contract_ref="spectral_index_analysis",
        ),

        AlgorithmDescriptor(
            id="remote.cva", name="变化向量分析（CVA）", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_change_cva"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="change_detection",
            method_references=["malila1980"],
            assumptions=[
                "两景波段按语义角色对齐（缺角色拒绝，不按位置猜测）",
                "幅度=全角色欧氏范数；角度=固定角色序前两分量 atan2（弧度）",
                "同一像元任一角色任一期无效 → 输出 NaN",
            ],
            limitations=[
                "CVA 只给幅度/方向，不构成土地覆盖语义变化",
                "方向角依赖角色序约定——跨研究比较需披露所用角色序",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["band_semantics_required"],
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_change_detection_science.py::test_cva_two_band_hand_computed_exact",
                "tests/unit/lib/test_change_detection_science.py::test_cva_identical_scenes_zero_and_nodata",
                "tests/unit/lib/test_change_detection_science.py::test_cva_role_order_documented_and_asserted",
            ],
        ),

        AlgorithmDescriptor(
            id="remote.ratio_change", name="双时相比值变化", category="remote_sensing",
            capabilities=["raster_change_detection"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_ratio_change"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="change_detection",
            assumptions=[
                "比值法适用于 SAR 后向散射/强度（同量纲输入）",
                "ratio：a/b，零分母→NaN；log_ratio：log(a)−log(b)（对数域对称）",
            ],
            limitations=[
                "比值不区分变化原因（物候/几何/定标漂移同权混合）",
                "log_ratio 输入须为正（线性强度或 dB）",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_change_detection_science.py::test_ratio_and_log_ratio_hand_exact",
                "tests/unit/lib/test_change_detection_science.py::test_log_ratio_symmetry_and_zeros",
            ],
            parameter_contract_ref="ratio_change_analysis",
        ),

        # ── VNext：SAR 域（native 三件 + planned 两件，诚实分离）────────
        AlgorithmDescriptor(
            id="sar.temporal_stats", name="SAR 时序栈统计", category="remote_sensing",
            capabilities=["sar_analysis"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_temporal_stats"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="sar_temporal_statistics",
            assumptions=[
                "输入假定已几何校正并对齐；std 为总体标准差（ddof=0）",
                "nodata/NaN 逐切片剔除，剩余有效切片上统计（部分有效像元披露）",
            ],
            limitations=[
                "无斑点滤波、无辐射定标（对应能力为 planned，见 sar.speckle_filter/sar.radiometric_calibration）",
                "栈深 ≤24、H·W ≤4096×4096，超限 ResourceScaleMismatch 先拒绝",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_science_vnext.py::test_sar_stack_statistics_hand_exact",
                "tests/unit/test_temporal_science_vnext.py::test_sar_stack_scale_guard",
            ],
            parameter_contract_ref="sar_temporal_stats_analysis",
        ),

        AlgorithmDescriptor(
            id="sar.vh_ratio", name="SAR VV/VH 极化比", category="remote_sensing",
            capabilities=["sar_analysis"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["sar_vh_ratio"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=15,
            algorithm_family="sar_polarimetry",
            assumptions=[
                "VV/VH：线性域为比值、dB 域为 dB 差（VV−VH）；VH=0 → NaN",
                "同景双极化（如 Sentinel-1 VV+VH）",
            ],
            limitations=[
                "无辐射定标假定下仅作结构对比代理，非物理量",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_temporal_science_vnext.py::test_sar_vh_ratio_and_log_ratio_exact",
            ],
        ),

        AlgorithmDescriptor(
            id="sar.log_ratio_change", name="SAR 双时相对数比值变化", category="remote_sensing",
            capabilities=["sar_analysis"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=["detect_ratio_change"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", priority=20,
            algorithm_family="change_detection",
            assumptions=[
                "log(a)−log(b)：对数域对称（增强=衰减镜像），SAR 双期惯用量",
                "经 detect_ratio_change 工具 method=log_ratio 参数执行",
            ],
            limitations=[
                "比值不区分变化原因；输入须为正（线性强度或 dB）",
            ],
            crs_class="RASTER_GRID",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_change_detection_science.py::test_log_ratio_symmetry_and_zeros",
            ],
        ),

        # ── planned（诚实：无实现、无工具候选，不伪装 native）──────────
        AlgorithmDescriptor(
            id="sar.speckle_filter", name="SAR 斑点噪声滤波", category="remote_sensing",
            capabilities=["sar_speckle_filtering"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=[],
            cpu_cost="high", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", priority=50,
            algorithm_family="sar_speckle_filtering",
            runtime_status="planned",
            assumptions=["斑点为乘性噪声（Lee/Lee-Sigma/Refined-Lee 家族假设）"],
            limitations=["未实现——planned 条目；现网 SAR 统计假定未滤波输入"],
        ),

        AlgorithmDescriptor(
            id="sar.radiometric_calibration", name="SAR 辐射定标", category="remote_sensing",
            capabilities=["sar_radiometric_calibration"],
            input_artifact_types=["raster_surface"],
            output_artifact_type="raster_surface",
            tool_candidates=[],
            cpu_cost="medium", memory_cost="medium", io_cost="medium",
            preferred_execution_policy="THREAD", priority=50,
            algorithm_family="sar_calibration",
            runtime_status="planned",
            assumptions=["DN → σ⁰/γ⁰（需定标常数与参考面）"],
            limitations=["未实现——planned 条目；比值法在同量纲输入下部分免疫"],
        ),
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="spectral_index_analysis", version=1,
        description="类型化光谱指数：指数 id（INDEX_FAMILY 12 成员；波段按角色显式命名）。",
        parameters=[
            ParameterSpec(
                name="index_id", type="enum", required=True,
                enum_values=["ndvi", "gndvi", "savi", "msavi", "ndwi",
                             "mndwi", "ndbi", "ndmi", "nbr", "evi"],
                description="光谱指数 id（公式出处随结果披露）",
            ),
        ],
    ),
    ParameterContract(
        id="ratio_change_analysis", version=1,
        description="双时相比值变化：ratio（a/b）或 log_ratio（SAR 惯用）。",
        parameters=[
            ParameterSpec(
                name="method", type="enum", default="ratio",
                enum_values=["ratio", "log_ratio"],
                description="ratio=a/b（零分母→NaN）；log_ratio=log(a)−log(b)（对数域对称）",
            ),
        ],
    ),
    ParameterContract(
        id="sar_temporal_stats_analysis", version=1,
        description="SAR 时序栈统计量（时间维聚合；规模守卫 T≤24、H·W≤4096²）。",
        parameters=[
            ParameterSpec(
                name="product", type="enum", default="mean",
                enum_values=["mean", "std", "min", "max", "range"],
                description="统计量（std 为总体标准差 ddof=0）",
            ),
        ],
    ),
]
