"""地形分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 terrain 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：为既有 slope/hillshade/aspect 补科学元数据
（Horn 1981 家族 + compass 回归测试锚点），并登记地形科学新算法族
（TPI/TRI/粗糙度/曲率/视域/D8 流向与汇流/流域/等值线）。实现层：
app/lib/geo_analysis/terrain.py；工具层：app/tools/terrain_analysis.py。
全部 crs_class=RASTER_GRID（网格语义，不承诺矢量 CRS 类）。

Foundation V2（A5）：追加水文与地貌量测算法族 —— Priority-Flood 填洼
（barnes2014）、D∞ 多向流（tarboton1997）、流程长度、Strahler 河流分级
与流域形态量测（strahler1957）、TWI/SPI（beven_kirkby1979）、USLE LS
（wischmeier_smith1978 + desmet_govers1996）、开放度（yokoyama2002）、
geomorphons（jasiewicz_stepinski2013）、Weiss 地类分级（weiss2001）、
多方位山体阴影（horn1981），及配套参数契约。

Terrain V3：地平线角与天空可视因子（steyn1980，openness 家族射线
行走），及 terrain.flow 的 flat_routing='epsilon' 平地路由 additive
参数（契约 v2，Barnes 2014 填洼机制）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import (
    AlgorithmDescriptor,
    BackendVariant,
    NumericalTolerance,
    ResourceEnvelope,
)
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=40, notes="float64 主数组+梯度副本系数；无实现层硬上限（输入侧栅格守卫）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.slope", name="坡度", category="terrain_analysis",
            backend_variants=[
                BackendVariant(
                    id="numpy_horn_gradient", backend="numpy", deterministic=True,
                    min_features=1, max_features=25000000,
                    notes="Horn 梯度数组运算；实现无显式闸，按 float32 多数组 ~1GiB 预算保守取 2500 万像元"),
            ],
            capabilities=["terrain_slope"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="terrain_surface", tool_candidates=["compute_terrain"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=10,
            algorithm_family="terrain_gradient",
            method_references=["horn1981"],
            assumptions=[
                "3×3 Horn 梯度；度栅格需 z_factor/纬度修正",
                "坡度 = arctan|∇z|（度）；cell_size_x 承接地理栅格 cos(lat) 东西向修正",
            ],
            limitations=[
                "边界像元 edge 复制延拓（单侧差分）",
                "地理 DEM 未做 cos(lat) 修正时东西向坡度低估 ~cos(lat)",
                "垂直单位非米（英尺 DEM）时需显式 z_factor",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_terrain_compass.py::test_slope_plane_recovers_angle",
                "tests/unit/test_terrain_compass.py::test_cell_size_x_doubles_east_west_gradient",
            ],
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=40, notes="float64 主数组+法向量副本；无实现层硬上限"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.hillshade", name="山体阴影", category="terrain_analysis",
            backend_variants=[
                BackendVariant(
                    id="numpy_hillshade", backend="numpy", deterministic=True,
                    min_features=1, max_features=25000000,
                    notes="曝光角数组运算（多方位 hillshade 窗口 ≤101）；保守窗口 2500 万像元（内存推导，无显式闸）"),
            ],
            capabilities=["terrain_hillshade"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="terrain_surface", tool_candidates=["compute_terrain"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=20,
            algorithm_family="terrain_gradient",
            method_references=["horn1981"],
            assumptions=[
                "3×3 Horn 梯度；度栅格需 z_factor/纬度修正",
                "罗盘方位光照模型：照度 = sin(alt)cos(θ) + cos(alt)sin(θ)cos(az − aspect)",
            ],
            limitations=[
                "无次级散射/大气效应（朗伯面近似）",
                "边界像元 edge 复制延拓（单侧差分）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_terrain_compass.py::test_hillshade_illumination_hemispheres",
                "tests/unit/test_terrain_compass.py::test_hillshade_matches_closed_form_compass_model",
            ],
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=40, notes="float64 主数组+梯度副本；无实现层硬上限"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.aspect", name="坡向", category="terrain_analysis",
            backend_variants=[
                BackendVariant(
                    id="numpy_horn_gradient", backend="numpy", deterministic=True,
                    min_features=1, max_features=25000000,
                    notes="Horn 梯度数组运算；实现无显式闸，按 float32 多数组 ~1GiB 预算保守取 2500 万像元"),
            ],
            capabilities=["terrain_aspect"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="terrain_surface", tool_candidates=["compute_terrain"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=30,
            algorithm_family="terrain_gradient",
            method_references=["horn1981"],
            assumptions=[
                "3×3 Horn 梯度；度栅格需 z_factor/纬度修正",
                "坡向 = 下坡方位（顺时针自北 0-360°）；平地 → NaN",
            ],
            limitations=[
                "平地/近平地坡向数值不稳定（梯度趋于 0）",
                "边界像元 edge 复制延拓（单侧差分）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/test_terrain_compass.py::test_aspect_is_compass_clockwise_from_north",
                "tests/unit/test_terrain_compass.py::test_aspect_flat_is_nan",
            ],
        ),

        # ── VNext 地形科学新算法（实现：app/lib/geo_analysis/terrain.py）──

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=32, notes="圆形窗口均值副本（MAX_WINDOW=101）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.tpi", name="地形位置指数 TPI", category="terrain_analysis",
            capabilities=["terrain_derivatives"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["terrain_derivatives"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=40,
            algorithm_family="terrain_neighborhood",
            method_references=["weiss2001"],
            assumptions=[
                "TPI = z − 窗口均值（含中心像元）；线性坡面上 ≡ 0",
                "窗口为 3-101 奇数；边界收缩为可得像元（不发明填充值）",
                "与像元尺寸无关（高程同量纲输出）",
            ],
            limitations=[
                "Weiss 地类分级需双尺度（如 3/25 格）对照，单一窗口不构成分类",
                "积分图均值-平方差在窗口均值远大于离散度时有浮点精度损失",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_tpi_linear_ramp_zero_and_center_peak_positive",
                "tests/unit/lib/test_terrain_science_vnext.py::test_nodata_excluded_and_all_nodata_raises",
                "tests/unit/lib/test_terrain_science_vnext.py::test_window_guard_rejects_out_of_range",
            ],
            parameter_contract_ref="terrain_derivative",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=32, notes="3×3 邻域极值副本"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.tri", name="地形崎岖度指数 TRI", category="terrain_analysis",
            capabilities=["terrain_derivatives"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["terrain_derivatives"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=41,
            algorithm_family="terrain_neighborhood",
            method_references=["wilson2007"],
            assumptions=[
                "TRI = sqrt(Σ(z − z_nb)²)，8 个直接邻域（Riley 1999 原式）",
                "边界收缩为可得邻域；平坦面 ≡ 0",
            ],
            limitations=[
                "只反映 1 像元尺度起伏，不表征多尺度崎岖度",
                "各向异性像元不做距离加权（与 Riley 原式一致的纯差分）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_tri_flat_zero_and_ramp_hand_computed",
            ],
            parameter_contract_ref="terrain_derivative",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=32, notes="3×3 邻域极值副本"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.roughness", name="地形粗糙度", category="terrain_analysis",
            capabilities=["terrain_derivatives"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["terrain_derivatives"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=42,
            algorithm_family="terrain_neighborhood",
            method_references=["wilson2007"],
            assumptions=[
                "粗糙度 = 窗口内高程总体标准差（ddof=0）——注意：Wilson (2007) 原文粗糙度"
                "为 max−min 口径，本实现采用窗口 std 惯用口径（与引用差异如实披露）",
                "窗口为 3-101 奇数；边界收缩为可得像元",
            ],
            limitations=[
                "对离群高程敏感（无稳健尺度）",
                "积分图方差在窗口均值远大于离散度时有浮点精度损失",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_roughness_flat_zero_and_hand_fixture_exact",
            ],
            parameter_contract_ref="terrain_derivative",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=48, notes="Zevenbergen-Thorne 二阶导数多副本"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.curvature", name="平面/剖面曲率", category="terrain_analysis",
            capabilities=["terrain_derivatives"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["terrain_derivatives"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=43,
            algorithm_family="terrain_curvature",
            method_references=["zevenbergen_thorne1987"],
            assumptions=[
                "Zevenbergen-Thorne 二阶差分：profile 沿最陡下降方向、plan 沿等高线方向",
                "单位 z_units·cell⁻²（惯例 ×100 报告；元数据披露）",
                "符号约定：profile>0 凸（水流减速）/ plan>0 分散；z=x² 检验 profile=+2、plan=0",
                "平地（梯度 0）→ NaN；模板邻域含 nodata → NaN",
            ],
            limitations=[
                "3×3 模板对噪声敏感（无预平滑）",
                "边界像元 edge 复制延拓退化为单侧差分",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="z=x²（cell=1）fixture 的 profile=2、plan=0 为浮点精确值",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_curvature_quadratic_convention_exact",
            ],
            parameter_contract_ref="terrain_derivative",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=16, notes="R3 扇区化（chunk 256）+ _guard_cells 闸"),
            cancellation_profile="chunk_boundary",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.viewshed", name="视域分析", category="terrain_analysis",
            capabilities=["terrain_viewshed"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["viewshed_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=44,
            algorithm_family="viewshed",
            approximate=True,
            method_references=["wang_robinson_white2000"],
            assumptions=[
                "无地球曲率/大气折射；目标高度默认 0",
                "扇区视线角判据：目标仰角 ≥ 沿途地形运行最大仰角即可见（切切记可见）",
                "观察点高程 = 观察点地形 + observer_height；射线 ~1 像元 bilinear 采样",
            ],
            limitations=[
                "扇区角离散 ≈ 最大距离处 1 像元弧长（远距目标近似误差 ≤ 半扇区宽）",
                "观察点邻接 nodata 时高程退化为最近有效像元",
                "地理栅格按 cos(lat) 换算米制像元（带向不修正）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="平坦 DEM 的可见比例 ≈ 圆盘面积/窗口面积（离散化 ±2%）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_viewshed_flat_disk_fraction_and_cone_occlusion",
            ],
            parameter_contract_ref="viewshed_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=48, notes="D8 拓扑排序保持多个 N 数组存活"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            id="terrain.flow", name="D8 流向与汇流累积", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["flow_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=45,
            algorithm_family="terrain_hydrology_d8",
            method_references=["ocallaghan_mark1984"],
            assumptions=[
                "D8 单向流（ESRI 2 的幂编码 1=E…128=NE；0=sink/outlet）",
                "最陡下降按米制像元距离（地理栅格 x 向 cos(lat)）；并列最陡取最低索引邻域",
                "汇流累积 = 上游贡献像元数（不含自身；全流域出口 = N−1）",
                "拓扑序（高程降序）累积，O(N log N)；边界 = 出口",
                "flat_routing='epsilon'：先经 Barnes 2014 Priority-Flood 填洼"
                "（terrain.sink_fill 机制）注入逐像元 epsilon 梯度，再在填充面上路由",
            ],
            limitations=[
                "D8 单向流限制：格网平行流向偏差；多向流为独立算法 terrain.dinf_flow"
                "（Tarboton 1997，本包内已实现，不在本算法内混叠）",
                "默认 flat_routing='none'：平地/洼地即汇（code 0）；可选 "
                "flat_routing='epsilon' 经 terrain.sink_fill 的 epsilon 填洼获得"
                "平地路由（meta 披露填充像元数与抬升量），默认路径保持不变",
                "流出网格边界的流路终止（boundary = outlet，不外推）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="5×5 碗形 z=r² fixture：中心累积 = 24（N−1）精确",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_d8_bowl_flow_toward_center_accumulation_24",
                "tests/unit/lib/test_terrain_v3.py::test_epsilon_flat_routing_bowl_and_default_unchanged",
            ],
            parameter_contract_ref="flow_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=32, notes="逆拓扑划拨；label 数组整型"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            id="terrain.watershed", name="流域圈定", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["watershed_delineation"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=46,
            algorithm_family="terrain_hydrology_d8",
            method_references=["ocallaghan_mark1984"],
            assumptions=[
                "逆 D8 BFS：汇入 pour point 的全部上游像元（含 pour point 自身）",
                "依赖 D8 单向流语义（编码与平局裁决同 terrain.flow）",
            ],
            limitations=[
                "pour point 不做河道 snap（未对齐河道时流域偏小，由调用方负责）",
                "D8 格网流向偏差会传递到流域边界",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="5×5 碗形 fixture：中心 pour point 圈出全部 25 像元",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_watershed_bowl_all_cells",
            ],
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=16, notes="marching-squares 行扫描；折线输出"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.contours", name="等值线提取", category="terrain_analysis",
            capabilities=["terrain_contours"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="line_feature_set", tool_candidates=["extract_contours"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=["raster_surface"], priority=47,
            algorithm_family="contour_extraction",
            assumptions=[
                "marching squares 等值线（matplotlib Agg，无显示环境）",
                "水平选取优先级：显式 levels > interval（自 vmin 等间隔）> n_levels（vmin..vmax 等间隔）",
                "nodata/非有限像元 → NaN 断线；顶点经栅格仿射变换映射到世界坐标",
            ],
            limitations=[
                "level == 数据极值的退化等值线可能为空（不产要素，meta 披露 levels_drawn）",
                "顶点密度受像元网格限制（无样条平滑/加密）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_science_vnext.py::test_contours_ramp_interval_levels_and_world_coords",
            ],
            parameter_contract_ref="extract_contours",
        ),

        # ── Foundation V2（A5）：水文与地貌量测扩展（实现同 geo_analysis/terrain.py）──

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=32, notes="heapq O(N log N)（MAX_HYDRO_CELLS 闸）"),
            cancellation_profile="chunk_boundary",
            backend_variants=[
                BackendVariant(id="full_heap", backend="numpy", deterministic=True,
                               min_features=1, max_features=50000000,
                               approximation_class="exact",
                               notes="全量 heapq Priority-Flood（Barnes 2014）——reference 变体"),
                BackendVariant(id="chunked_band", backend="numpy", deterministic=True,
                               approximation_class="approximate",
                               notes="列带分块 + 邻带裁决（heap 峰值 O(带宽×H)）；seam 可欠/过填（以参考最大填深为界，parity conformance 钉死）；lib API opt-in（fill_depressions_chunked），非 runtime dispatch"),
            ],
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.sink_fill", name="Priority-Flood 填洼", category="terrain_analysis",
            capabilities=["terrain_hydrology_advanced"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["depression_fill"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=48,
            algorithm_family="terrain_hydrology_d8", complexity="O(N log N)",
            method_references=["barnes2014"],
            assumptions=[
                "Priority-Flood（Barnes 2014）heapq 漫水；种子 = 网格边界 + nodata 邻接有效像元",
                "nodata/网格外视作排水出口；epsilon>0 时逐像元抬升 → 表面严格单调可排",
                "meta 报告 filled_volume（z_units·m²）/filled_cell_count/max_fill_depth",
            ],
            limitations=[
                "epsilon=0 时填后平地仍为汇（与 d8 不发明路由语义衔接）",
                "纯 Python 堆循环，>10M 像元耗时显著（护栏 50M 像元先拒绝）",
                "无嵌套洼地深度分层报告（单层溢流面）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="5×7 单洼地 fixture：洼底恰填至溢流高程（浮点精确）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v4.py::test_chunked_pf_never_underfills_and_bounded_overfill",
                "tests/unit/lib/test_hydrology_v4.py::test_chunked_pf_deterministic",
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_fill_depressions_single_pit_spill_elevation_exact",
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_fill_depressions_volume_and_epsilon_monotone",
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_hydrology_nodata_adversarial_and_guards",
            ],
            parameter_contract_ref="sink_fill",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=40, notes="D∞ 角度分配多副本（_guard_cells 闸）"),
            cancellation_profile="chunk_boundary",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.dinf_flow", name="D∞ 多向流", category="terrain_analysis",
            capabilities=["terrain_hydrology_advanced"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["dinf_flow_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=49,
            algorithm_family="terrain_hydrology_dinf", complexity="O(N log N)",
            method_references=["ocallaghan_mark1984"],
            assumptions=[
                "8 三角面平面梯度最陡下降（Tarboton 1997）；角度弧度 ∈ [0,2π)，x=东 y=北",
                "汇流按面内角度比例分流到两下游邻域；拓扑序（高程降序）累积",
                "平地/洼地 → 角度 -1 哨兵；nodata → NaN；函数内不填洼",
            ],
            limitations=[
                "D∞ 不消解格网平行流向偏差的极端情形（面离散 45°）",
                "推荐组合 fill_depressions(epsilon>0) 先行获得单调可排面",
                "缺角邻域的面跳过（边缘只用可得邻域）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="线性坡面 z=-x：中心角 = 0（正东）浮点精确；碗形 Σacc 与 D8 相等",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_dinf_ramp_direction_angle_exact",
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_dinf_pit_sentinel_and_accumulation_sum_matches_d8",
            ],
            parameter_contract_ref="dinf_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=32, notes="顺/逆拓扑累计（_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.flow_length", name="流程长度", category="terrain_analysis",
            capabilities=["terrain_hydrology_advanced"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["flow_length_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=50,
            algorithm_family="terrain_hydrology_d8", complexity="O(N log N)",
            method_references=["ocallaghan_mark1984", "strahler1957"],
            assumptions=[
                "downstream = 沿 D8 接收者到出口的米制步长和（汇/出口 = 0）",
                "upstream = 距最远山脊源的最大路径长（MAX 口径，文档化）",
                "步长 = hypot(Δcol·cx, Δrow·cy)；地理栅格由调用方传 cos(lat) 修正 cx",
            ],
            limitations=[
                "继承 D8 格网流向偏差（路径沿 8 邻域折线）",
                "平地不路由（d8 code 0）→ 平地内长度为 0",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="单通道直线流路：downstream = (n-1)·cell 精确",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_flow_length_straight_channel_golden",
            ],
            parameter_contract_ref="flow_length_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=16, notes="累积阈值掩膜；无独立 cells 闸"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.streams", name="河网提取", category="terrain_analysis",
            capabilities=["terrain_hydrology_advanced"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["stream_network"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=["raster_surface"], priority=51,
            algorithm_family="terrain_hydrology_d8",
            method_references=["strahler1957"],
            assumptions=[
                "河网像元 = 汇流累积 ≥ threshold（上游贡献像元数口径）",
                "阈值由调用方按流域尺度率定（无普适默认）",
            ],
            limitations=[
                "阈值敏感：过低生成伪河网、过高断头（无自动率定）",
                "继承 D8 单向流的河网走向偏差",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_strahler_confluence_orders_exact",
            ],
            parameter_contract_ref="stream_network",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=32, notes="河网级序拓扑（_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            id="terrain.strahler", name="Strahler 河流分级", category="terrain_analysis",
            capabilities=["terrain_hydrology_advanced"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["stream_network"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=52,
            algorithm_family="terrain_hydrology_d8", complexity="O(N log N)",
            method_references=["strahler1957"],
            assumptions=[
                "Strahler 1957：源头 = 1 级；最高上游级唯一 → 同级，并列 → +1",
                "拓扑序 = 高程降序（接收者严格更低；同高程 (row,col) 兜底）",
                "meta 报告 order_distribution 与 max_order",
            ],
            limitations=[
                "河网输入依赖 accumulation 阈值（见 terrain.streams 局限）",
                "格网平行汇流会高估并列（+1 升级）频率",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="二元树汇流 fixture：1+1 → 2 级精确",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_strahler_confluence_orders_exact",
            ],
            parameter_contract_ref="stream_network",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=40, notes="流域形态多指标副本（_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.morphometry", name="流域形态量测", category="terrain_analysis",
            capabilities=["terrain_hydrology_advanced"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["watershed_morphometry_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=53,
            algorithm_family="terrain_hydrology_d8",
            method_references=["strahler1957"],
            assumptions=[
                "面积/周长来自逆 D8 上流域掩膜；周长 = 边界边缘长度和（网格外视作流域外）",
                "basin length = 流域内 MAX upstream 流程长度（最长山脊→出口路径）",
                "form factor = A/L²；elongation = 2√(A/π)/L（Strahler 1957）",
                "给 stream_threshold 时报告河网长度与排水密度（km/km²）",
            ],
            limitations=[
                "basin length 的 MAX 口径对狭长流域外的形状敏感（非主轴拟合）",
                "排水密度继承河网阈值敏感性",
                "pour point 不做河道 snap",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="圆形流域 fixture：form factor/elongation 相对误差 ≤1e-6",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_watershed_morphometry_circular_basin_golden",
            ],
            parameter_contract_ref="morphometry_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=24, notes="ln(a/tanβ)；比汇面积副本"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.twi", name="地形湿润指数 TWI", category="terrain_analysis",
            capabilities=["terrain_wetness_indices"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["topographic_index"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=["raster_surface"], priority=54,
            algorithm_family="terrain_wetness_index",
            method_references=["beven_kirkby1979"],
            assumptions=[
                "TWI = ln(SCA/tanβ)；SCA = (accum+1)·cell_area/contour_width",
                "等流宽度 = cell_size（y 向）；κ=1 flat 口径（单流向近似，meta 披露）",
                "tanβ 下限 1e-6：平地处 TWI 为截断上界（非物理解）",
            ],
            limitations=[
                "D8/D∞ 单向累积低估发散坡的 SCA（无多向 κ 分解）",
                "slope 与 accum 网格必须同形对齐（无重采样）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="均匀坡+均匀累积 fixture：TWI 恒定（浮点精确）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_twi_spi_uniform_golden_and_floor",
            ],
            parameter_contract_ref="wetness_index",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=24, notes="a·tanβ；比汇面积副本"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.spi", name="水流功率指数 SPI", category="terrain_analysis",
            capabilities=["terrain_wetness_indices"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["topographic_index"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=["raster_surface"], priority=55,
            algorithm_family="terrain_wetness_index",
            method_references=["beven_kirkby1979"],
            assumptions=[
                "SPI = SCA·tanβ（侵蚀/输沙潜势代理）；SCA 口径同 terrain.twi",
                "tanβ 无下限（平地 → SPI 0）",
            ],
            limitations=[
                "静态地形代理，无降雨/土壤参数（非过程模型）",
                "SCA 单流向近似偏差同 terrain.twi",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="均匀坡+均匀累积 fixture：SPI 恒定（浮点精确）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_twi_spi_uniform_golden_and_floor",
            ],
            parameter_contract_ref="wetness_index",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(bytes_per_cell=24, notes="USLE LS 坡长-坡度积分副本"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.ls_factor", name="USLE LS 因子", category="terrain_analysis",
            capabilities=["terrain_wetness_indices"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["ls_factor_analysis"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            preferred_execution_policy="INLINE", compatible_map_models=["raster_surface"], priority=56,
            algorithm_family="erosion_index",
            method_references=["wischmeier_smith1978", "desmet_govers1996"],
            assumptions=[
                "mccool：LS=(λ/22.13)^m·(65.41sin²θ+4.56sinθ+0.065)；m 表 McCool 1987："
                "<1%→0.2、1-3%→0.3、3-5%→0.4、≥5%→0.5",
                "desmet_govers：LS=(m+1)·(SCA/22.13)^m·(sinβ/0.0896)^1.3；SCA 口径同 TWI",
                "λ 建议传 upstream 流程长度（缺省固定 100 m，meta 披露）",
            ],
            limitations=[
                "标准径流小区经验式的栅格外推（无降雨/植被因子）",
                "n=1.3 固定（Desmet-Govers 1996 实现惯例），不暴露调参",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="m 分档边界与 desmet_govers 均匀坡手算式 1e-10 一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_hydrology_v2.py::test_ls_factor_mccool_table_and_desmet_govers_hand",
            ],
            parameter_contract_ref="ls_factor_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=32, notes="多方位射线扫描（半径≤100 cells，_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.openness", name="地形开放度", category="terrain_analysis",
            capabilities=["terrain_geomorphometry"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["terrain_openness_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=57,
            algorithm_family="terrain_geomorphometry",
            method_references=["yokoyama2002"],
            assumptions=[
                "正开放度 = mean_φ max_d arctan((z₀−z(d))/d)；负开放度同式取反向差（度）",
                "16 方位（4-64 可调）× 半径 1..R 像元；偏移圆整后的实际米制距离",
                "平地 ≡ 0；山脊高正开放度、谷地高负开放度幅值",
            ],
            limitations=[
                "方位离散 ≤ 360/azimuth_count（默认 22.5°）角分辨率",
                "无有效采样的方位从均值剔除（栅格角隅诚实退化）",
                "半径 ≤ 100 像元护栏（射线行走内存/时间包络）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="平地 fixture 正/负开放度 ≡ 0.0（浮点精确）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_geomorphometry_v2.py::test_openness_flat_zero_and_ridge_crest_high",
                "tests/unit/lib/test_terrain_geomorphometry_v2.py::test_geomorphometry_guards_radius_caps_and_nodata",
            ],
            parameter_contract_ref="openness_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=24, notes="LTP 全方位比较（半径≤128 cells，_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            id="terrain.geomorphons", name="Geomorphons 地貌分类", category="terrain_analysis",
            capabilities=["terrain_geomorphometry"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["geomorphon_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=58,
            algorithm_family="terrain_geomorphometry",
            method_references=["jasiewicz_stepinski2013"],
            assumptions=[
                "8 方位视线三元码（zenith/nadir 角 vs flatten 容差）→ 10 类决策表",
                "决策表（优先级级联）：全-1 summit；全+1 depression；≥6 环 ridge/valley；"
                "双 3-5 环 slope；单 3-5 环 shoulder/hollow；1-2 环 spur/footslope；否则 flat",
                "far>0 跳过近场采样（skip 半径）；无采样腿按 0（平）计并披露",
            ],
            limitations=[
                "相对高程形态学：无绝对坡度语义（缓坡大尺度可判 flat）",
                "lookup ≤ 128 像元护栏；flatten=0 时 DEM 噪声直通分类",
                "角隅像元方位被网格截断（无采样腿按平计）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="合成峰/洼/坡 fixture：中心类 = summit/depression/slope 精确",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_geomorphometry_v2.py::test_geomorphons_peak_pit_ramp_classes_and_distribution",
            ],
            parameter_contract_ref="geomorphon_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=32, notes="双尺度 TPI 分类（_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            id="terrain.landform", name="双尺度 TPI 地类分级", category="terrain_analysis",
            capabilities=["terrain_geomorphometry"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["landform_classify"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=59,
            algorithm_family="terrain_geomorphometry",
            method_references=["weiss2001"],
            assumptions=[
                "Weiss 2001 双尺度标准化 TPI（TPI/SD）+ 高程百分位 10 类决策表",
                "中性带 |TPI/SD|<1；平地带按 elevation_tolerance 截 percentile 分档",
                "TPI 窗口含中心像元（与 terrain.tpi 同口径）；边缘收缩",
            ],
            limitations=[
                "窗口与容差需按景观尺度率定（缺省 3/25 格、0.1 为海报惯例起点）",
                "常量面（TPI SD=0）→ DegenerateData（分类阈值无定义）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="线性坡 fixture 中心 = open_slopes(5)；山脊 fixture 中心 = mountaintops(9)",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_geomorphometry_v2.py::test_landform_ramp_open_slope_and_ridge_class",
            ],
            parameter_contract_ref="landform_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=56, notes="逐方位角山体阴影栈（_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.hillshade_multi", name="多方位山体阴影", category="terrain_analysis",
            capabilities=["terrain_geomorphometry"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["multiazimuth_hillshade"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=60,
            algorithm_family="terrain_gradient",
            method_references=["horn1981"],
            assumptions=[
                "单方位公式与 band_math.compute_hillshade 逐位一致（#379 罗盘语义）",
                "combine=mean 多方位均值（去阴影）/ min 逐像元最小（制图）",
                "NaN 像元传播为 NaN（与渲染掩膜一致）",
            ],
            limitations=[
                "朗伯面近似：无次级散射/大气效应",
                "3×3 Horn 梯度 edge 复制延拓（单侧差分）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="单方位 (315,) 与 band_math.compute_hillshade 逐位相等",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_geomorphometry_v2.py::test_hillshade_multiazimuth_single_equals_band_math",
                "tests/unit/lib/test_terrain_geomorphometry_v2.py::test_hillshade_multiazimuth_mean_min_and_validation",
            ],
            parameter_contract_ref="hillshade_multiazimuth",
        ),

        # ── Terrain V3：地平线角与天空可视因子（Steyn 1980）────────────

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=24, notes="逐方位角地平线扫描（方位角≤64/半径≤100，_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.horizon_angle", name="地平线角", category="terrain_analysis",
            capabilities=["terrain_sky_view"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["horizon_angle_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=61,
            algorithm_family="terrain_geomorphometry",
            method_references=["steyn1980", "yokoyama2002"],
            assumptions=[
                "每方位（罗盘度，自北顺时针）取射线行走 max arctan((z(d)−z₀)/d) 的正仰角（度）",
                "1 像元步长圆整偏移 + 实际米制距离（与 openness 同口径；各向异性感知）",
                "射线遇 nodata/非有限即停；截断（数据外）视作无遮挡（=0，披露）",
                "全下行射线钳为 0：地平线角不为负；平地 ≡ 0（浮点精确）",
            ],
            limitations=[
                "方位离散 ≤ 360/方位数 的角分辨率（缺省 8 方位 45°）",
                "半径 ≤ 100 像元护栏；半径外地形不参与（遮挡被低估）",
                "数据缝后的地形被视作无遮挡 —— 诚实低估而非发明遮挡",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="平地 fixture 全方位 ≡ 0.0；墙 fixture 仰角 = arctan(H/d) 浮点精确",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_v3.py::test_horizon_flat_all_zeros",
                "tests/unit/lib/test_terrain_v3.py::test_horizon_ridge_blocks_specific_azimuths",
                "tests/unit/lib/test_terrain_v3.py::test_horizon_nodata_stops_ray",
            ],
            parameter_contract_ref="terrain_horizon_analysis",
        ),

        AlgorithmDescriptor(
            # ── Science V4（契约 ratchet W1）：声明式资源/取消/容差 ──
            resource_envelope=ResourceEnvelope(hard_max_cells=50000000, bytes_per_cell=24, notes="全方位地平线积分（_guard_cells 闸）"),
            cancellation_profile="none",
            tolerance=NumericalTolerance(rtol=1e-6, atol=1e-9, policy="conformance"),
            id="terrain.sky_view_factor", name="天空可视因子 SVF", category="terrain_analysis",
            capabilities=["terrain_sky_view"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["sky_view_factor_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=62,
            algorithm_family="terrain_geomorphometry",
            method_references=["steyn1980"],
            assumptions=[
                "SVF = (1/N) Σ cos²(ψ_i)（Steyn 1980）；ψ_i = 等角距方位的地平线角（度）",
                "ψ_i 与 terrain.horizon_angle 共用同一射线行走实现（不重复逻辑）",
                "平地 ψ ≡ 0 → SVF ≡ 1.0（浮点精确）；深洼/封闭谷地 SVF → 0",
                "截断射线的剩余段按无遮挡计（cos²=1）—— 数据缝附近 SVF 被高估（披露）",
            ],
            limitations=[
                "方位离散：N 方位等角距采样对崎岖天际线的欠采样",
                "半径 ≤ 100 像元护栏；半径外地形不参与天际线",
                "无地球曲率/大气折射修正（局部地形口径）",
            ],
            crs_class="RASTER_GRID",
            scientific_preconditions=["raster_band_required:1"],
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="平地 fixture SVF ≡ 1.0（1e-12 内）；单 cell 深洼 SVF < 0.05",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_terrain_v3.py::test_svf_flat_exact_one",
                "tests/unit/lib/test_terrain_v3.py::test_svf_deep_pit_small_and_azimuth_invariance",
                "tests/unit/lib/test_terrain_v3.py::test_svf_matches_steyn_formula_from_horizon",
                "tests/unit/lib/test_terrain_v3.py::test_horizon_and_svf_guards",
            ],
            parameter_contract_ref="terrain_svf_analysis",
        ),

        # ── Science V4（W8/W9）：水文与地形 V4 ─────────────────────

        AlgorithmDescriptor(
            id="terrain.breach", name="洼地切沟（Breaching）", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            complexity="O(N log N)（priority-flood ×2 + 逐洼地路径切沟）",
            approximation_class="approximate", approximate=True,
            algorithm_family="terrain_hydrology_d8",
            method_references=["lindsay2016"],
            assumptions=[
                "Lindsay 2016 选择性切沟简化：填洼识别洼地 → epsilon 填面 D8 "
                "接收者链定位 pit→出口路径 → 沿路径下切（min 语义 = 最小开挖）",
                "切沟线 = pit 高程 − k·epsilon（pit→出口方向严格下降）",
                "只降不升：非洼地像元永不改高",
            ],
            limitations=[
                "路径为填面最陡下降链（非全局最小代价路径 LCP）",
                "超深回退填洼（max_breach_depth 限制；计数披露）",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐位一致（确定性堆 + 确定性路径）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v4.py::test_breach_removes_internal_sinks_with_less_work_than_fill",
                "tests/unit/lib/test_hydrology_v4.py::test_breach_max_depth_fallback_to_fill",
            ],
            resource_envelope=ResourceEnvelope(hard_max_cells=50_000_000, bytes_per_cell=32, notes="3×float64 全网格（MAX_HYDRO_CELLS 闸）"),
            backend_variants=[
                BackendVariant(id="numpy_priority_flood", backend="numpy", deterministic=True,
                               min_features=1, max_features=50_000_000,
                               approximation_class="approximate",
                               notes="3×float64 全网格；MAX_HYDRO_CELLS=5000 万像元闸"),
            ],
            cancellation_profile="chunk_boundary",
            tolerance=NumericalTolerance(rtol=1e-9, atol=0.0, policy="conformance"),
            ),

        AlgorithmDescriptor(
            id="terrain.hand", name="最近排水高程（HAND）", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="medium", memory_cost="high", io_cost="low",
            complexity="O(N log N)（fill + d8 + accum + 单遍逆拓扑）",
            approximation_class="exact",
            algorithm_family="terrain_hydrology_d8",
            method_references=["renno2008"],
            assumptions=[
                "HAND = z(cell) − z(D8 下游链第一个河网像元)；望远镜求和单遍",
                "河网 = 填后 D8 汇流累积 ≥ threshold",
                "边界排出且未遇河网 → NaN（诚实缺省，计数披露）",
            ],
            limitations=[
                "河网阈值敏感性：阈值决定『最近排水』的定义",
                "洪泛区语义为地形近似（非水动力淹没模型）",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v4.py::test_hand_zero_on_streams_and_finite_offstream",
            ],
            resource_envelope=ResourceEnvelope(hard_max_cells=50_000_000, bytes_per_cell=40, notes="填面+D8+累积+HAND 多数组（MAX_HYDRO_CELLS 闸）"),
            backend_variants=[
                BackendVariant(id="numpy_d8_accum", backend="numpy", deterministic=True,
                               min_features=1, max_features=50_000_000,
                               approximation_class="approximate",
                               notes="填面+D8+累积+HAND 多数组；MAX_HYDRO_CELLS=5000 万像元闸"),
            ],
            cancellation_profile="chunk_boundary",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            ),

        AlgorithmDescriptor(
            id="terrain.shreve", name="Shreve 河流量级", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            complexity="O(N log N)（与 Strahler 同拓扑机器）",
            approximation_class="exact",
            algorithm_family="terrain_hydrology_d8",
            method_references=["shreve1966"],
            assumptions=[
                "量级 = 上游量级之和（源头 = 1）；拓扑序 = 降序高程",
                "河网 = 汇流累积 ≥ threshold（与 streams/strahler 同口径）",
            ],
            limitations=[
                "单线程长河量级线性增长（对排水强度敏感、对形态不敏感——与 Strahler 互补）",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v4.py::test_shreve_sums_upstream_magnitudes",
            ],
            resource_envelope=ResourceEnvelope(hard_max_cells=50_000_000, bytes_per_cell=24, notes="MAX_HYDRO_CELLS 闸"),
            cancellation_profile="chunk_boundary",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            ),

        AlgorithmDescriptor(
            id="terrain.pfafstetter", name="Pfafstetter 编码（单级）", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            complexity="O(S log S)（干流上溯 + 支流归属 BFS，S=河网像元）",
            approximation_class="exact",
            algorithm_family="terrain_hydrology_d8",
            method_references=["pfafstetter1989"],
            assumptions=[
                "干流 = 出口上溯每步取汇流最大的上游河网像元",
                "偶数码 2,4,… 沿干流等分；4 大支流（junction 汇流降序）取奇数 1,3,5,7",
                "支流子流域 = junction 上游河网像元（下游-first 归属）",
            ],
            limitations=[
                "单级层级（levels=1 语义——多级用 terrain.pfafstetter_multilevel）",
                "出口必须在河网上（否则类型化拒绝）",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v4.py::test_pfafstetter_odd_even_convention",
            ],
            resource_envelope=ResourceEnvelope(hard_max_cells=50_000_000, bytes_per_cell=24, notes="MAX_HYDRO_CELLS 闸"),
            cancellation_profile="chunk_boundary",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            ),

        AlgorithmDescriptor(
            id="terrain.pfafstetter_multilevel", name="Pfafstetter 编码（多级）", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            complexity="O(L·S log S)（L=层级 ≤4；每段主干走法 + 支流归属 BFS）",
            approximation_class="exact",
            algorithm_family="terrain_hydrology_d8",
            method_references=["pfafstetter1989"],
            assumptions=[
                "level k≥2：偶数码段（inter-basin）以其下游端为出口、在父码掩膜内"
                "重跑同一主干走法，子码 = 父码×10 + 位码（经典逐位拼接）",
                "奇数码（支流盆地）为叶子不细分（经典约定）",
                "主干走法与单级共享同一 helper（零第二事实源）",
            ],
            limitations=[
                "子段河网像元 < 8 → 停止细分（计数披露，skipped_segments）",
                "levels ≤ 4（码位预算）；出口必须在河网上",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐位一致；levels=1 与单级函数编码逐位一致（differential 锚）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v5.py::test_multilevel_l1_matches_single_level",
                "tests/unit/lib/test_hydrology_v5.py::test_multilevel_two_digit_codes",
                "tests/unit/lib/test_hydrology_v5.py::test_multilevel_levels_validation",
            ],
            resource_envelope=ResourceEnvelope(hard_max_cells=50_000_000, bytes_per_cell=24, notes="MAX_HYDRO_CELLS 闸；codes int32 工作数组"),
            cancellation_profile="chunk_boundary",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            ),

        AlgorithmDescriptor(
            id="terrain.flow_topology_validate", name="流网拓扑校验", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="stats_table", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            complexity="O(N)（receiver 链染色法环检测，步进有界 2N）",
            approximation_class="exact",
            algorithm_family="terrain_hydrology_d8",
            method_references=["o_callaghan_mark1984"],
            assumptions=[
                "D8 receiver 图应无环；汇流沿 receiver 严格单调增（正 acc）",
                "校验是结构事实报告——不 raise（is_consistent 汇总位）",
            ],
            limitations=[
                "equal-acc 平台单列计数（epsilon 填洼残留线索），不与违例混计",
                "多出口是披露事实（边界 DEM 常态），非错误",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐位一致（计数型输出）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v5.py::test_topology_consistent_basin",
                "tests/unit/lib/test_hydrology_v5.py::test_topology_cycle_detection",
                "tests/unit/lib/test_hydrology_v5.py::test_topology_dangling_and_monotonicity",
            ],
            resource_envelope=ResourceEnvelope(hard_max_cells=50_000_000, bytes_per_cell=24, notes="MAX_HYDRO_CELLS 闸；color uint8 工作数组"),
            cancellation_profile="coarse",
            tolerance=NumericalTolerance(rtol=1e-12, atol=0.0, policy="conformance"),
            ),

        AlgorithmDescriptor(
            id="terrain.hypsometry", name="高程面积分析", category="terrain_analysis",
            capabilities=["terrain_geomorphometry"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="stats_table", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="low", memory_cost="low", io_cost="low",
            complexity="O(N)（确定性直方）",
            approximation_class="approximate", approximate=True,
            algorithm_family="terrain_morphometry",
            method_references=["strahler1952"],
            assumptions=[
                "曲线 a(e) = 高于归一化高程 e 的面积占比（n_levels 级直方）",
                "HI = ∫a de（矩形 = 1；Strahler 1952 侵蚀循环代理）",
            ],
            limitations=[
                "直方分级离散化（连续曲线的级别近似）",
                "常数面退化 HI=0（诚实披露，不伪造曲线）",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="同输入逐位一致（直方确定性）",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v4.py::test_hypsometry_rectangle_and_monotone",
            ],
            resource_envelope=ResourceEnvelope(bytes_per_cell=8, notes="单 float64 主数组 + 直方"),
            cancellation_profile="coarse",
            tolerance=NumericalTolerance(rtol=1e-9, atol=0.0, policy="conformance"),
            ),

        AlgorithmDescriptor(
            id="terrain.solar_radiation", name="晴空太阳辐射", category="terrain_analysis",
            capabilities=["terrain_derivatives"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", runtime_status="native",
            parameter_contract_ref="hydrology_v4_analysis",
            tool_candidates=["hydrology_v4_analysis"],
            cpu_cost="low", memory_cost="medium", io_cost="low",
            complexity="O(N)（解析 Ra + gradient 坡面因子）",
            approximation_class="heuristic", approximate=True,
            algorithm_family="terrain_morphometry",
            method_references=["fao56"],
            assumptions=[
                "FAO-56 大气顶日辐射 Ra（dr/δ/ωs 解析）× 晴空透射（0.75）",
                "地形入射因子 f = cos β + sin β·cos(az_sun − aspect)，钳 ≥0.15（散射底）",
                "日积分代表方位 az_sun = π + δ（单方位近似）",
            ],
            limitations=[
                "heuristic：无地平线遮蔽积分/多时步太阳轨迹（horizon_angle 工具可做后处理）",
                "海拔-大气修正未含（Rso 常数透射）",
            ],
            crs_class="RASTER_GRID",
            uncertainty_outputs=[],
            random_seed_policy="deterministic",
            numerical_tolerance="Ra 解析锚（赤道春秋分 37.6 ±2%）；同输入逐位一致",
            scientific_status="VALIDATED",
            conformance_tests=[
                "tests/unit/lib/test_hydrology_v4.py::test_solar_radiation_analytic_anchor_and_bounds",
            ],
            resource_envelope=ResourceEnvelope(bytes_per_cell=48, notes="z+梯度+坡度+坡向+辐照 5×float64"),
            cancellation_profile="coarse",
            tolerance=NumericalTolerance(rtol=0.02, atol=0.5, policy="analytic_anchor"),
            ),
]

# ── 参数契约（§12；工具签名与契约参数名一致 —— parity 门校验）────────

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="terrain_derivative", version=1,
        description="DEM 邻域衍生指标：指标类型、窗口、垂直比例与 nodata 覆盖。",
        parameters=[
            ParameterSpec(
                name="derivative", type="enum", required=True,
                enum_values=["tpi", "tri", "roughness", "plan_curvature", "profile_curvature"],
                description="衍生指标：地形位置指数/崎岖度/粗糙度/平面曲率/剖面曲率",
            ),
            ParameterSpec(
                name="window", type="integer", default=3, minimum=3, maximum=101,
                unit="pixels",
                description="TPI/粗糙度窗口边长（奇数；TRI/曲率固定邻域不受影响）",
            ),
            ParameterSpec(
                name="z_factor", type="number", default=1.0, minimum=0.0001,
                unit="ratio",
                description="垂直单位比例（z 米/值；英尺 DEM ≈ 0.3048）",
            ),
            ParameterSpec(
                name="nodata", type="number",
                description="nodata 覆盖值（缺省用文件声明或 NaN 语义）",
            ),
        ],
    ),
    ParameterContract(
        id="viewshed_analysis", version=1,
        description="视域分析：观察点位置、高度与最大视线距离。",
        parameters=[
            ParameterSpec(
                name="observer_x", type="number", required=True,
                description="观察点世界 x（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="observer_y", type="number", required=True,
                description="观察点世界 y（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="observer_height", type="number", default=2.0,
                unit="m", minimum=0,
                description="观察者离地高度（默认 2 m）",
            ),
            ParameterSpec(
                name="target_height", type="number", default=0.0,
                unit="m", minimum=0,
                description="目标离地高度（默认 0 = 地表）",
            ),
            ParameterSpec(
                name="max_distance", type="number", default=5000.0,
                unit="m", minimum=10, maximum=50000,
                description="最大视线距离（米；窗口内生效）",
            ),
        ],
    ),
    ParameterContract(
        id="flow_analysis", version=2,
        description="D8 水文分析产品选择与平地路由模式（v2：additive flat_routing）。",
        parameters=[
            ParameterSpec(
                name="product", type="enum", default="flow_accumulation",
                enum_values=["flow_direction", "flow_accumulation"],
                description="输出产品：D8 流向编码或汇流累积（默认累积）",
            ),
            ParameterSpec(
                name="flat_routing", type="enum", default="none",
                enum_values=["none", "epsilon"],
                description=(
                    "平地路由：none = 平地/洼地即汇（code 0）；epsilon = 先经 "
                    "Barnes 2014 epsilon 填洼（terrain.sink_fill 机制）再路由，"
                    "平地排向溢流出口"),
            ),
        ],
    ),
    ParameterContract(
        id="extract_contours", version=1,
        description="等值线水平控制：显式列表 > 间隔 > 等间隔数。",
        parameters=[
            ParameterSpec(
                name="n_levels", type="integer", default=10, minimum=2, maximum=30,
                unit="count",
                description="等间隔水平数（levels/interval 缺席时生效）",
            ),
            ParameterSpec(
                name="interval", type="number", minimum=1e-06,
                description="等值线间隔（自 vmin 起；必须 > 0）",
            ),
            ParameterSpec(
                name="levels", type="string",
                description="显式等值线水平（JSON 数组或逗号分隔文本；优先于 interval/n_levels）",
            ),
        ],
    ),

    # ── Foundation V2（A5）：水文与地貌量测扩展 ────────────────────────

    ParameterContract(
        id="sink_fill", version=2,
        description="Priority-Flood 填洼：epsilon 变体、nodata 覆盖与填充面持久化。",
        parameters=[
            ParameterSpec(
                name="epsilon", type="number", default=0.0, minimum=0.0,
                unit="m",
                description="逐像元抬升量（>0 → 填后表面严格单调可排；0 = 纯填洼）",
            ),
            ParameterSpec(
                name="nodata", type="number",
                description="nodata 覆盖值（缺省用文件声明或 NaN 语义）",
            ),
            ParameterSpec(
                name="persist_filled", type="boolean", default=False,
                description="持久化填充后 DEM 为 GeoTIFF（data_dir 内 *_filled.tif），"
                            "返回 filled_raster_path 供下游水文工具（D∞/河网/TWI）直接消费",
            ),
        ],
    ),
    ParameterContract(
        id="dinf_analysis", version=1,
        description="D∞ 多向流产品选择。",
        parameters=[
            ParameterSpec(
                name="product", type="enum", default="flow_accumulation",
                enum_values=["flow_direction", "flow_accumulation"],
                description="输出产品：D∞ 方向角（弧度）或比例分流汇流累积（默认累积）",
            ),
        ],
    ),
    ParameterContract(
        id="flow_length_analysis", version=1,
        description="流程长度方向口径。",
        parameters=[
            ParameterSpec(
                name="mode", type="enum", default="downstream",
                enum_values=["downstream", "upstream"],
                description="downstream = 到出口距离；upstream = 距最远山脊源（MAX 口径）",
            ),
        ],
    ),
    ParameterContract(
        id="stream_network", version=1,
        description="河网提取阈值与产品。",
        parameters=[
            ParameterSpec(
                name="threshold", type="number", required=True, minimum=1,
                unit="count",
                description="河网阈值（上游贡献像元数；accum ≥ threshold 即河网）",
            ),
            ParameterSpec(
                name="product", type="enum", default="stream_order",
                enum_values=["stream_mask", "stream_order"],
                description="输出产品：河网掩膜或 Strahler 分级图（默认分级）",
            ),
        ],
    ),
    ParameterContract(
        id="morphometry_analysis", version=1,
        description="流域形态量测：pour point 与可选河网阈值。",
        parameters=[
            ParameterSpec(
                name="pour_x", type="number", required=True,
                description="pour point 世界 x（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="pour_y", type="number", required=True,
                description="pour point 世界 y（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="stream_threshold", type="number", minimum=1,
                unit="count",
                description="河网阈值（上游像元数；给定时报告排水密度）",
            ),
        ],
    ),
    ParameterContract(
        id="wetness_index", version=1,
        description="湿润/水力指数产品选择。",
        parameters=[
            ParameterSpec(
                name="product", type="enum", default="twi",
                enum_values=["twi", "spi"],
                description="输出产品：TWI = ln(SCA/tanβ) 或 SPI = SCA·tanβ",
            ),
        ],
    ),
    ParameterContract(
        id="ls_factor_analysis", version=1,
        description="USLE LS 因子方法与坡长。",
        parameters=[
            ParameterSpec(
                name="method", type="enum", default="mccool",
                enum_values=["mccool", "desmet_govers"],
                description="mccool = 坡长经验式（Wischmeier-Smith）；desmet_govers = 比集水面积式",
            ),
            ParameterSpec(
                name="flow_length", type="number", default=100.0, minimum=0.1,
                unit="m",
                description="mccool 坡长 λ（米；desmet_govers 用内部 SCA 替代）",
            ),
        ],
    ),
    ParameterContract(
        id="openness_analysis", version=1,
        description="地形开放度射线参数。",
        parameters=[
            ParameterSpec(
                name="radius_cells", type="integer", default=8, minimum=1, maximum=100,
                unit="pixels",
                description="开放度搜索半径（像元；≤100 护栏）",
            ),
            ParameterSpec(
                name="azimuth_count", type="integer", default=16, minimum=4, maximum=64,
                unit="count",
                description="方位数（等角距，自北顺时针）",
            ),
        ],
    ),
    ParameterContract(
        id="geomorphon_analysis", version=1,
        description="Geomorphons 视线参数。",
        parameters=[
            ParameterSpec(
                name="lookup_radius_cells", type="integer", default=8, minimum=1, maximum=128,
                unit="pixels",
                description="视线查找半径（像元；≤128 护栏）",
            ),
            ParameterSpec(
                name="flatten", type="number", default=0.0, minimum=0.0,
                unit="degrees",
                description="平地容差（度；zenith/nadir 超过它才记 +1/-1）",
            ),
            ParameterSpec(
                name="far", type="number", default=0.0, minimum=0.0,
                unit="pixels",
                description="近场跳过半径（像元；只扫 > far 的距离）",
            ),
        ],
    ),
    ParameterContract(
        id="landform_analysis", version=1,
        description="Weiss 双尺度 TPI 地类分级参数。",
        parameters=[
            ParameterSpec(
                name="tpi_window_small", type="integer", default=3, minimum=3, maximum=101,
                unit="pixels",
                description="小尺度 TPI 窗口（奇数）",
            ),
            ParameterSpec(
                name="tpi_window_large", type="integer", default=25, minimum=3, maximum=101,
                unit="pixels",
                description="大尺度 TPI 窗口（奇数；≥ 小窗口）",
            ),
            ParameterSpec(
                name="elevation_tolerance", type="number", default=0.1, minimum=0.0, maximum=0.5,
                unit="ratio",
                description="平地带高程百分位容差（0-0.5）",
            ),
        ],
    ),
    ParameterContract(
        id="hillshade_multiazimuth", version=1,
        description="多方位山体阴影：太阳高度、方位集合与合成方式。",
        parameters=[
            ParameterSpec(
                name="altitude", type="number", default=45.0, minimum=0.0001, maximum=90.0,
                unit="degrees",
                description="太阳高度角（度；0 < alt ≤ 90）",
            ),
            ParameterSpec(
                name="azimuths", type="string",
                description="太阳方位列表（罗盘度；逗号分隔，如 '315,135'；缺省 315,135）",
            ),
            ParameterSpec(
                name="combine", type="enum", default="mean",
                enum_values=["mean", "min"],
                description="合成方式：多方位均值（去阴影）或逐像元最小（制图）",
            ),
        ],
    ),

    # ── Terrain V3：地平线角与天空可视因子 ────────────────────────────

    ParameterContract(
        id="terrain_horizon_analysis", version=1,
        description="地平线角射线参数：方位集合与搜索半径。",
        parameters=[
            ParameterSpec(
                name="azimuths", type="string",
                description=(
                    "地平线方位列表（罗盘度；逗号分隔；缺省 "
                    "'0,45,90,135,180,225,270,315'）"),
            ),
            ParameterSpec(
                name="max_search_radius", type="integer", default=100, minimum=1, maximum=100,
                unit="pixels",
                description="射线搜索半径（像元；≤100 护栏）",
            ),
        ],
    ),
    ParameterContract(
        id="terrain_svf_analysis", version=1,
        description="天空可视因子（Steyn 1980）：方位数与搜索半径。",
        parameters=[
            ParameterSpec(
                name="n_azimuths", type="integer", default=16, minimum=4, maximum=64,
                unit="count",
                description="方位数（等角距，自北顺时针）",
            ),
            ParameterSpec(
                name="max_search_radius", type="integer", default=100, minimum=1, maximum=100,
                unit="pixels",
                description="地平线射线搜索半径（像元；≤100 护栏）",
            ),
        ],
    ),

]