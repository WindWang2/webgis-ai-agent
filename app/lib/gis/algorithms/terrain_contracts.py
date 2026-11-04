"""Terrain tool parameter contracts, consumed by descriptor and tool parity checks."""

from typing import List

from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="terrain_derivative",
        version=1,
        description="DEM 邻域衍生指标：指标类型、窗口、垂直比例与 nodata 覆盖。",
        parameters=[
            ParameterSpec(
                name="derivative",
                type="enum",
                required=True,
                enum_values=[
                    "tpi",
                    "tri",
                    "roughness",
                    "plan_curvature",
                    "profile_curvature",
                ],
                description="衍生指标：地形位置指数/崎岖度/粗糙度/平面曲率/剖面曲率",
            ),
            ParameterSpec(
                name="window",
                type="integer",
                default=3,
                minimum=3,
                maximum=101,
                unit="pixels",
                description="TPI/粗糙度窗口边长（奇数；TRI/曲率固定邻域不受影响）",
            ),
            ParameterSpec(
                name="z_factor",
                type="number",
                default=1.0,
                minimum=0.0001,
                unit="ratio",
                description="垂直单位比例（z 米/值；英尺 DEM ≈ 0.3048）",
            ),
            ParameterSpec(
                name="nodata",
                type="number",
                description="nodata 覆盖值（缺省用文件声明或 NaN 语义）",
            ),
        ],
    ),
    ParameterContract(
        id="viewshed_analysis",
        version=2,
        description=(
            "视域分析：观察点位置、高度、最大视线距离与可视几何输出开关"
            "（v2：additive include_geometry——#1829 stats-only 的补全，"
            "几何与统计可分离消费，统计字段两模式逐位一致）。"
        ),
        parameters=[
            ParameterSpec(
                name="observer_x",
                type="number",
                required=True,
                description="观察点世界 x（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="observer_y",
                type="number",
                required=True,
                description="观察点世界 y（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="observer_height",
                type="number",
                default=2.0,
                unit="m",
                minimum=0,
                description="观察者离地高度（默认 2 m）",
            ),
            ParameterSpec(
                name="target_height",
                type="number",
                default=0.0,
                unit="m",
                minimum=0,
                description="目标离地高度（默认 0 = 地表）",
            ),
            ParameterSpec(
                name="max_distance",
                type="number",
                default=5000.0,
                unit="m",
                minimum=10,
                maximum=50000,
                description="最大视线距离（米；窗口内生效）",
            ),
            ParameterSpec(
                name="include_geometry",
                type="boolean",
                default=True,
                description=(
                    "输出可视区 GeoJSON 面（cell-edge 对齐、有界多边形化、"
                    "含 nodata 排除与覆盖披露）；False 回到纯 stats"
                ),
            ),
        ],
    ),
    ParameterContract(
        id="flow_analysis",
        version=2,
        description="D8 水文分析产品选择与平地路由模式（v2：additive flat_routing）。",
        parameters=[
            ParameterSpec(
                name="product",
                type="enum",
                default="flow_accumulation",
                enum_values=["flow_direction", "flow_accumulation"],
                description="输出产品：D8 流向编码或汇流累积（默认累积）",
            ),
            ParameterSpec(
                name="flat_routing",
                type="enum",
                default="none",
                enum_values=["none", "epsilon"],
                description=(
                    "平地路由：none = 平地/洼地即汇（code 0）；epsilon = 先经 "
                    "Barnes 2014 epsilon 填洼（terrain.sink_fill 机制）再路由，"
                    "平地排向溢流出口"
                ),
            ),
        ],
    ),
    ParameterContract(
        id="extract_contours",
        version=1,
        description="等值线水平控制：显式列表 > 间隔 > 等间隔数。",
        parameters=[
            ParameterSpec(
                name="n_levels",
                type="integer",
                default=10,
                minimum=2,
                maximum=30,
                unit="count",
                description="等间隔水平数（levels/interval 缺席时生效）",
            ),
            ParameterSpec(
                name="interval",
                type="number",
                minimum=1e-06,
                description="等值线间隔（自 vmin 起；必须 > 0）",
            ),
            ParameterSpec(
                name="levels",
                type="string",
                description="显式等值线水平（JSON 数组或逗号分隔文本；优先于 interval/n_levels）",
            ),
        ],
    ),
    # ── Foundation V2（A5）：水文与地貌量测扩展 ────────────────────────
    ParameterContract(
        id="sink_fill",
        version=2,
        description="Priority-Flood 填洼：epsilon 变体、nodata 覆盖与填充面持久化。",
        parameters=[
            ParameterSpec(
                name="epsilon",
                type="number",
                default=0.0,
                minimum=0.0,
                unit="m",
                description="逐像元抬升量（>0 → 填后表面严格单调可排；0 = 纯填洼）",
            ),
            ParameterSpec(
                name="nodata",
                type="number",
                description="nodata 覆盖值（缺省用文件声明或 NaN 语义）",
            ),
            ParameterSpec(
                name="persist_filled",
                type="boolean",
                default=False,
                description="持久化填充后 DEM 为 GeoTIFF（data_dir 内 *_filled.tif），"
                "返回 filled_raster_path 供下游水文工具（D∞/河网/TWI）直接消费",
            ),
        ],
    ),
    ParameterContract(
        id="dinf_analysis",
        version=1,
        description="D∞ 多向流产品选择。",
        parameters=[
            ParameterSpec(
                name="product",
                type="enum",
                default="flow_accumulation",
                enum_values=["flow_direction", "flow_accumulation"],
                description="输出产品：D∞ 方向角（弧度）或比例分流汇流累积（默认累积）",
            ),
        ],
    ),
    ParameterContract(
        id="flow_length_analysis",
        version=1,
        description="流程长度方向口径。",
        parameters=[
            ParameterSpec(
                name="mode",
                type="enum",
                default="downstream",
                enum_values=["downstream", "upstream"],
                description="downstream = 到出口距离；upstream = 距最远山脊源（MAX 口径）",
            ),
        ],
    ),
    ParameterContract(
        id="stream_network",
        version=1,
        description="河网提取阈值与产品。",
        parameters=[
            ParameterSpec(
                name="threshold",
                type="number",
                required=True,
                minimum=1,
                unit="count",
                description="河网阈值（上游贡献像元数；accum ≥ threshold 即河网）",
            ),
            ParameterSpec(
                name="product",
                type="enum",
                default="stream_order",
                enum_values=["stream_mask", "stream_order"],
                description="输出产品：河网掩膜或 Strahler 分级图（默认分级）",
            ),
        ],
    ),
    ParameterContract(
        id="morphometry_analysis",
        version=1,
        description="流域形态量测：pour point 与可选河网阈值。",
        parameters=[
            ParameterSpec(
                name="pour_x",
                type="number",
                required=True,
                description="pour point 世界 x（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="pour_y",
                type="number",
                required=True,
                description="pour point 世界 y（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="stream_threshold",
                type="number",
                minimum=1,
                unit="count",
                description="河网阈值（上游像元数；给定时报告排水密度）",
            ),
        ],
    ),
    ParameterContract(
        id="wetness_index",
        version=1,
        description="湿润/水力指数产品选择。",
        parameters=[
            ParameterSpec(
                name="product",
                type="enum",
                default="twi",
                enum_values=["twi", "spi"],
                description="输出产品：TWI = ln(SCA/tanβ) 或 SPI = SCA·tanβ",
            ),
        ],
    ),
    ParameterContract(
        id="ls_factor_analysis",
        version=1,
        description="USLE LS 因子方法与坡长。",
        parameters=[
            ParameterSpec(
                name="method",
                type="enum",
                default="mccool",
                enum_values=["mccool", "desmet_govers"],
                description="mccool = 坡长经验式（Wischmeier-Smith）；desmet_govers = 比集水面积式",
            ),
            ParameterSpec(
                name="flow_length",
                type="number",
                default=100.0,
                minimum=0.1,
                unit="m",
                description="mccool 坡长 λ（米；desmet_govers 用内部 SCA 替代）",
            ),
        ],
    ),
    ParameterContract(
        id="openness_analysis",
        version=1,
        description="地形开放度射线参数。",
        parameters=[
            ParameterSpec(
                name="radius_cells",
                type="integer",
                default=8,
                minimum=1,
                maximum=100,
                unit="pixels",
                description="开放度搜索半径（像元；≤100 护栏）",
            ),
            ParameterSpec(
                name="azimuth_count",
                type="integer",
                default=16,
                minimum=4,
                maximum=64,
                unit="count",
                description="方位数（等角距，自北顺时针）",
            ),
        ],
    ),
    ParameterContract(
        id="geomorphon_analysis",
        version=1,
        description="Geomorphons 视线参数。",
        parameters=[
            ParameterSpec(
                name="lookup_radius_cells",
                type="integer",
                default=8,
                minimum=1,
                maximum=128,
                unit="pixels",
                description="视线查找半径（像元；≤128 护栏）",
            ),
            ParameterSpec(
                name="flatten",
                type="number",
                default=0.0,
                minimum=0.0,
                unit="degrees",
                description="平地容差（度；zenith/nadir 超过它才记 +1/-1）",
            ),
            ParameterSpec(
                name="far",
                type="number",
                default=0.0,
                minimum=0.0,
                unit="pixels",
                description="近场跳过半径（像元；只扫 > far 的距离）",
            ),
        ],
    ),
    ParameterContract(
        id="landform_analysis",
        version=1,
        description="Weiss 双尺度 TPI 地类分级参数。",
        parameters=[
            ParameterSpec(
                name="tpi_window_small",
                type="integer",
                default=3,
                minimum=3,
                maximum=101,
                unit="pixels",
                description="小尺度 TPI 窗口（奇数）",
            ),
            ParameterSpec(
                name="tpi_window_large",
                type="integer",
                default=25,
                minimum=3,
                maximum=101,
                unit="pixels",
                description="大尺度 TPI 窗口（奇数；≥ 小窗口）",
            ),
            ParameterSpec(
                name="elevation_tolerance",
                type="number",
                default=0.1,
                minimum=0.0,
                maximum=0.5,
                unit="ratio",
                description="平地带高程百分位容差（0-0.5）",
            ),
        ],
    ),
    ParameterContract(
        id="hillshade_multiazimuth",
        version=1,
        description="多方位山体阴影：太阳高度、方位集合与合成方式。",
        parameters=[
            ParameterSpec(
                name="altitude",
                type="number",
                default=45.0,
                minimum=0.0001,
                maximum=90.0,
                unit="degrees",
                description="太阳高度角（度；0 < alt ≤ 90）",
            ),
            ParameterSpec(
                name="azimuths",
                type="string",
                description="太阳方位列表（罗盘度；逗号分隔，如 '315,135'；缺省 315,135）",
            ),
            ParameterSpec(
                name="combine",
                type="enum",
                default="mean",
                enum_values=["mean", "min"],
                description="合成方式：多方位均值（去阴影）或逐像元最小（制图）",
            ),
        ],
    ),
    # ── Terrain V3：地平线角与天空可视因子 ────────────────────────────
    ParameterContract(
        id="terrain_horizon_analysis",
        version=1,
        description="地平线角射线参数：方位集合与搜索半径。",
        parameters=[
            ParameterSpec(
                name="azimuths",
                type="string",
                description=(
                    "地平线方位列表（罗盘度；逗号分隔；缺省 "
                    "'0,45,90,135,180,225,270,315'）"
                ),
            ),
            ParameterSpec(
                name="max_search_radius",
                type="integer",
                default=100,
                minimum=1,
                maximum=100,
                unit="pixels",
                description="射线搜索半径（像元；≤100 护栏）",
            ),
        ],
    ),
    ParameterContract(
        id="terrain_svf_analysis",
        version=1,
        description="天空可视因子（Steyn 1980）：方位数与搜索半径。",
        parameters=[
            ParameterSpec(
                name="n_azimuths",
                type="integer",
                default=16,
                minimum=4,
                maximum=64,
                unit="count",
                description="方位数（等角距，自北顺时针）",
            ),
            ParameterSpec(
                name="max_search_radius",
                type="integer",
                default=100,
                minimum=1,
                maximum=100,
                unit="pixels",
                description="地平线射线搜索半径（像元；≤100 护栏）",
            ),
        ],
    ),
    ParameterContract(
        id="cost_distance_analysis",
        version=1,
        description="累积成本面：摩擦栅格 / 源点 GeoJSON / nodata。",
        parameters=[
            ParameterSpec(
                name="raster_path",
                type="string",
                required=True,
                description="摩擦（成本）面 GeoTIFF 路径（data_dir 内）",
            ),
            ParameterSpec(
                name="sources_geojson",
                type="string",
                required=True,
                description="源点要素 GeoJSON（Point FeatureCollection）或数据引用(ref:xxx)",
            ),
            ParameterSpec(
                name="nodata",
                type="number",
                default=0,
                description="nodata 覆盖值（0=用栅格自带 nodata）；NaN 视为无效",
            ),
        ],
    ),
    ParameterContract(
        id="least_cost_path_analysis",
        version=1,
        description="最小成本路径：累积成本面 / 目标点。",
        parameters=[
            ParameterSpec(
                name="accumulated_raster_path",
                type="string",
                required=True,
                description="cost_distance 产出的累积成本面 GeoTIFF 路径",
            ),
            ParameterSpec(
                name="target_x",
                type="number",
                required=True,
                unit="meters",
                description="目标点 X（累积面栅格 CRS 原生坐标，与 "
                "cost_distance 产物同一坐标框架）",
            ),
            ParameterSpec(
                name="target_y",
                type="number",
                required=True,
                unit="meters",
                description="目标点 Y（同上）",
            ),
        ],
    ),
    # ── G3D03（ADR-0274）：三维量测族参数契约 ────────────────────────
    ParameterContract(
        id="terrain_profile",
        version=1,
        description="地形剖面：采样步长、nodata 政策与垂直基准声明（顶点为数据输入，工具层校验）。",
        parameters=[
            ParameterSpec(
                name="sampling_step_m",
                type="number",
                unit="m",
                minimum=0.01,
                description="采样步长（米；缺省 ~1 像元 = 0.5·(cy+cx)）",
            ),
            ParameterSpec(
                name="nodata_policy",
                type="enum",
                default="error",
                enum_values=["error", "skip"],
                description="nodata 采样点策略：error = 类型化失败；skip = 剔除并披露",
            ),
            ParameterSpec(
                name="vertical_datum",
                type="enum",
                default="unknown",
                enum_values=[
                    "ellipsoid_height",
                    "geoid_height_egm96",
                    "geoid_height_egm2008",
                    "orthometric_local",
                    "unknown",
                ],
                description="DEM 高程垂直基准声明（GeoTIFF 无 z 元数据；缺省 unknown 披露）",
            ),
            ParameterSpec(
                name="z_units",
                type="enum",
                default="m",
                enum_values=["m", "ft"],
                description="DEM 高程存储单位（ft 按 0.3048 精确折算米并披露）",
            ),
        ],
    ),
    ParameterContract(
        id="line_of_sight_analysis",
        version=1,
        description="通视线：观察/目标点与高度、曲率模型、绝对高程覆盖与垂直基准声明。",
        parameters=[
            ParameterSpec(
                name="observer_x",
                type="number",
                required=True,
                description="观察点世界 x（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="observer_y",
                type="number",
                required=True,
                description="观察点世界 y（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="target_x",
                type="number",
                required=True,
                description="目标点世界 x（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="target_y",
                type="number",
                required=True,
                description="目标点世界 y（栅格 CRS 单位）",
            ),
            ParameterSpec(
                name="observer_height",
                type="number",
                default=2.0,
                unit="m",
                minimum=0,
                description="观察者离地高度（米；offset 与 datum 无关）",
            ),
            ParameterSpec(
                name="target_height",
                type="number",
                default=0.0,
                unit="m",
                minimum=0,
                description="目标离地高度（米）",
            ),
            ParameterSpec(
                name="observer_z",
                type="number",
                unit="m",
                description="观察点绝对高程覆盖（DEM z_units；须与 DEM 同 datum，声明不一致类型化拒绝）",
            ),
            ParameterSpec(
                name="target_z",
                type="number",
                unit="m",
                description="目标点绝对高程覆盖（同上）",
            ),
            ParameterSpec(
                name="override_vertical_datum",
                type="enum",
                enum_values=[
                    "ellipsoid_height",
                    "geoid_height_egm96",
                    "geoid_height_egm2008",
                    "orthometric_local",
                    "unknown",
                ],
                description="绝对高程覆盖值的垂直基准声明（缺省继承 DEM 声明；不同即拒绝）",
            ),
            ParameterSpec(
                name="curvature",
                type="enum",
                default="none",
                enum_values=["none", "earth", "earth_refracted"],
                description="地球曲率/大气折射模型（none 默认；earth R=6371008.8m；earth_refracted k=0.1307）",
            ),
            ParameterSpec(
                name="sampling_step_m",
                type="number",
                unit="m",
                minimum=0.01,
                description="视线采样步长（米；缺省 ~1 像元）",
            ),
            ParameterSpec(
                name="nodata_policy",
                type="enum",
                default="error",
                enum_values=["error", "skip"],
                description="视线沿途 nodata 策略（error = 类型化失败；skip = 剔除并披露）",
            ),
            ParameterSpec(
                name="vertical_datum",
                type="enum",
                default="unknown",
                enum_values=[
                    "ellipsoid_height",
                    "geoid_height_egm96",
                    "geoid_height_egm2008",
                    "orthometric_local",
                    "unknown",
                ],
                description="DEM 高程垂直基准声明",
            ),
            ParameterSpec(
                name="z_units",
                type="enum",
                default="m",
                enum_values=["m", "ft"],
                description="DEM 高程存储单位",
            ),
        ],
    ),
    ParameterContract(
        id="surface_distance_analysis",
        version=1,
        description="地表距离：采样步长、nodata 政策与垂直基准声明（顶点为数据输入，工具层校验）。",
        parameters=[
            ParameterSpec(
                name="sampling_step_m",
                type="number",
                unit="m",
                minimum=0.01,
                description="采样步长（米；缺省 ~1 像元；附半步收敛指示）",
            ),
            ParameterSpec(
                name="nodata_policy",
                type="enum",
                default="error",
                enum_values=["error", "skip"],
                description="nodata 采样点策略",
            ),
            ParameterSpec(
                name="vertical_datum",
                type="enum",
                default="unknown",
                enum_values=[
                    "ellipsoid_height",
                    "geoid_height_egm96",
                    "geoid_height_egm2008",
                    "orthometric_local",
                    "unknown",
                ],
                description="DEM 高程垂直基准声明",
            ),
            ParameterSpec(
                name="z_units",
                type="enum",
                default="m",
                enum_values=["m", "ft"],
                description="DEM 高程存储单位",
            ),
        ],
    ),
    ParameterContract(
        id="surface_area_analysis",
        version=1,
        description="地表面积：nodata 阈值与垂直基准声明（多边形为数据输入，工具层校验）。",
        parameters=[
            ParameterSpec(
                name="max_invalid_fraction",
                type="number",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                unit="ratio",
                description="quad 无效比例上限（超过类型化失败）",
            ),
            ParameterSpec(
                name="vertical_datum",
                type="enum",
                default="unknown",
                enum_values=[
                    "ellipsoid_height",
                    "geoid_height_egm96",
                    "geoid_height_egm2008",
                    "orthometric_local",
                    "unknown",
                ],
                description="DEM 高程垂直基准声明",
            ),
            ParameterSpec(
                name="z_units",
                type="enum",
                default="m",
                enum_values=["m", "ft"],
                description="DEM 高程存储单位",
            ),
        ],
    ),
    ParameterContract(
        id="cut_fill_volume_analysis",
        version=1,
        description=(
            "基准面土方量：基准面模式/绝对高程、nodata 阈值与垂直基准声明"
            "（多边形为数据输入，工具层校验）。"
        ),
        parameters=[
            ParameterSpec(
                name="reference",
                type="enum",
                default="constant",
                enum_values=["constant", "mean"],
                description=(
                    "基准面模式：constant（base_z 绝对高程）| mean（区域"
                    "有效像元均值——平衡挖填/平整检验）"
                ),
            ),
            ParameterSpec(
                name="base_z",
                type="number",
                description=(
                    "constant 模式的基准面绝对高程（DEM z_units；datum 声明"
                    "须与 DEM 一致，混算类型化拒绝）"
                ),
            ),
            ParameterSpec(
                name="max_invalid_fraction",
                type="number",
                default=0.5,
                minimum=0.0,
                maximum=1.0,
                unit="ratio",
                description="区域内 nodata 像元比例上限（超过类型化失败）",
            ),
            ParameterSpec(
                name="vertical_datum",
                type="enum",
                default="unknown",
                enum_values=[
                    "ellipsoid_height",
                    "geoid_height_egm96",
                    "geoid_height_egm2008",
                    "orthometric_local",
                    "unknown",
                ],
                description="DEM 高程垂直基准声明",
            ),
            ParameterSpec(
                name="override_vertical_datum",
                type="enum",
                enum_values=[
                    "ellipsoid_height",
                    "geoid_height_egm96",
                    "geoid_height_egm2008",
                    "orthometric_local",
                    "unknown",
                ],
                description="基准面高程的垂直基准声明（缺省继承 DEM 声明；不同即拒绝）",
            ),
            ParameterSpec(
                name="z_units",
                type="enum",
                default="m",
                enum_values=["m", "ft"],
                description="DEM 高程存储单位",
            ),
            ParameterSpec(
                name="include_geometry",
                type="boolean",
                default=True,
                description="输出挖方/填方分区 GeoJSON 面（cell-edge 对齐，有界）",
            ),
        ],
    ),
]
