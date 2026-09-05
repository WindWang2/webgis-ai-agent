"""地形分析 域算法包（ADR-0099 §34 domain packs）。

描述符逐字迁自 algorithm_registry._SEED_ALGORITHMS（2026-09 split）；
中央 registry 只聚合与校验 —— 本模块是 terrain 域的唯一事实源，
新算法在各自的域模块注册，勿回填中央文件。

VNext（ADR-0099）：为既有 slope/hillshade/aspect 补科学元数据
（Horn 1981 家族 + compass 回归测试锚点），并登记地形科学新算法族
（TPI/TRI/粗糙度/曲率/视域/D8 流向与汇流/流域/等值线）。实现层：
app/lib/geo_analysis/terrain.py；工具层：app/tools/terrain_analysis.py。
全部 crs_class=RASTER_GRID（网格语义，不承诺矢量 CRS 类）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [

        AlgorithmDescriptor(
            id="terrain.slope", name="坡度", category="terrain_analysis",
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
            id="terrain.hillshade", name="山体阴影", category="terrain_analysis",
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
            id="terrain.aspect", name="坡向", category="terrain_analysis",
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
            id="terrain.roughness", name="地形粗糙度", category="terrain_analysis",
            capabilities=["terrain_derivatives"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["terrain_derivatives"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=42,
            algorithm_family="terrain_neighborhood",
            method_references=["wilson2007"],
            assumptions=[
                "粗糙度 = 窗口内高程总体标准差（ddof=0，Wilson 2007 口径）",
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
            id="terrain.viewshed", name="视域分析", category="terrain_analysis",
            capabilities=["terrain_viewshed"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["viewshed_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=44,
            algorithm_family="viewshed",
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
            id="terrain.flow", name="D8 流向与汇流累积", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["flow_analysis"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=45,
            algorithm_family="terrain_hydrology_d8",
            method_references=["tarboton1997"],
            assumptions=[
                "D8 单向流（ESRI 2 的幂编码 1=E…128=NE；0=sink/outlet）",
                "最陡下降按米制像元距离（地理栅格 x 向 cos(lat)）；并列最陡取最低索引邻域",
                "汇流累积 = 上游贡献像元数（不含自身；全流域出口 = N−1）",
                "拓扑序（高程降序）累积，O(N log N)；边界 = 出口",
            ],
            limitations=[
                "D8 单向流限制：格网平行流向偏差，D∞（Tarboton 1997）未实现",
                "平地/洼地即汇（code 0），无 epsilon 梯度平地路由/填洼",
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
            ],
            parameter_contract_ref="flow_analysis",
        ),

        AlgorithmDescriptor(
            id="terrain.watershed", name="流域圈定", category="terrain_analysis",
            capabilities=["terrain_hydrology"],
            input_artifact_types=["terrain_surface"],
            output_artifact_type="raster_surface", tool_candidates=["watershed_delineation"],
            cpu_cost="medium", memory_cost="medium", io_cost="low",
            preferred_execution_policy="THREAD", compatible_map_models=["raster_surface"], priority=46,
            algorithm_family="terrain_hydrology_d8",
            method_references=["tarboton1997"],
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
        id="flow_analysis", version=1,
        description="D8 水文分析产品选择。",
        parameters=[
            ParameterSpec(
                name="product", type="enum", default="flow_accumulation",
                enum_values=["flow_direction", "flow_accumulation"],
                description="输出产品：D8 流向编码或汇流累积（默认累积）",
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
]
