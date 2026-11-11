"""G06/M5 能力生态域包（ADR-0099 §34 域包惯例）+ G13 算法绑定。

G06 阶段 ``ALGORITHMS`` 曾保持空表（避免扰动既有 253 条计数断言），
代价是两个真实原语在 planner 面只能拿到 ``binding_unbacked`` warning、
可用性快照永远落 ``available_unverified``。G13 补上**真实算法绑定**：
两条 AlgorithmDescriptor 把原语挂回 ``zonal_statistics`` /
``service_area`` 既有能力的算法链（tool_candidates 反查自动回填工具
描述符的 ``algorithms`` 字段，不新建第二真相）。

科学元数据的运行时承载仍是 ``app/tools/capability_ecosystem_tools.py``
结果封套的 ``science`` 字段；本描述符是**声明面**（规划/预检/目录消费）。

``PARAMETER_CONTRACTS`` 经既有 ``iter_contract_packs()`` 汇入唯一的
``ParameterContractRegistry``，工具侧用 ``apply_contract`` 严格校验。
"""
from __future__ import annotations

from app.lib.gis.algorithm_registry import AlgorithmDescriptor, ResourceEnvelope
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: list = [
    AlgorithmDescriptor(
        # G13 / WP06：条件多分位数分区统计的算法声明（原语自 G06 起真实计算）。
        # 刻意**不声明 fallback_algorithms**：均值类 zonal_stats 不是分位数
        # 分布的科学等价替代（C12：缺原语时披露差异/返回缺口，不静默降级）。
        id="raster.zonal.quantile_conditional",
        name="条件多分位数分区统计",
        category="raster_analysis",
        capabilities=["zonal_statistics"],
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=192.0,
            notes=(
                "每分区分位数行 + 像元掩膜；整幅读取前有 MAX_RASTER_CELLS + "
                "RasterResourceGuard 双重硬闸（先判界再读取）"
            ),
        ),
        input_artifact_types=["raster_surface", "polygon_feature_set"],
        output_artifact_type="stats_table",
        tool_candidates=["conditional_quantile_zonal_stats"],
        parameter_contract_ref="conditional_quantile_zonal_stats",
        cpu_cost="medium", memory_cost="medium", io_cost="medium",
        preferred_execution_policy="THREAD", priority=18,
        algorithm_family="raster_zonal",
        deterministic=True,
        complexity="O(zones × pixels_in_zone) 掩膜 + O(n log n) 分位数排序",
        assumptions=[
            "分位数 = 经验分位数（numpy.quantile 线性插值）；固定输入 + 固定"
            "分位数列表 ⇒ 输出逐位一致",
            "有效样本分母 = zone 内像元 ∩ finite ∩ 非 nodata ∩ where 条件"
            "（不是 zone 总像元，也不是分区面积）",
            "where 仅允许受限 value <op> <number>（and 连接），编译为谓词，"
            "绝不 eval 任意代码",
            "像元归属 = 像元中心落在分区内（rasterio geometry_mask "
            "all_touched=False 惯例，与 remote.zonal_stats 同口径）",
        ],
        limitations=[
            "栅格与分区 CRS 不一致时不做隐式重采样对齐（上层须先变换；"
            "仅改 CRS 标签不等于变换成功）",
            "小样本分区（有效像元 < 30）不提供置信区间，显式标注 not_evaluated",
            "经纬度栅格仍可计算像元值分布，但其面积/长度不可按米解释"
            "（geographic_crs_warning 披露）",
        ],
        crs_class="RASTER_GRID",
        scientific_preconditions=["raster_band_required:1"],
        random_seed_policy="deterministic",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/tools/test_capability_ecosystem_primitives.py::"
            "test_cap_o08_conditional_quantile_zonal_stats",
        ],
        version="1.0",
    ),
    AlgorithmDescriptor(
        # G13 / WP06：多方案路网服务覆盖一致性的算法声明。
        # 刻意**不声明 fallback_algorithms**：单方案 service_area 生成不是
        # 一致性对拍的科学等价替代（比较对象是覆盖边集，非服务区面）。
        id="network.service_coverage.consistency",
        name="多方案路网服务覆盖一致性",
        category="network_analysis",
        capabilities=["service_area"],
        input_artifact_types=["line_feature_set", "point_feature_set"],
        output_artifact_type="stats_table",
        tool_candidates=["network_service_coverage_consistency"],
        parameter_contract_ref="network_service_coverage_consistency",
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="THREAD", priority=15,
        algorithm_family="service_coverage_consistency",
        deterministic=True,
        complexity="O(schemes × facilities × Dijkstra(E log V)) + O(k²) 两两 Jaccard",
        assumptions=[
            "比较对象 = 各方案 Dijkstra 可达的**覆盖边集**（Jaccard），"
            "不是地表覆盖面积比；边集相等 ⇔ consistency=1",
            "边权量纲由 cost_unit 声明：米制 = 平面几何长度；时间制 = 显式"
            " travel_time_s/duration_s/cost_s（要素级按段长占比分摊，多段线"
            "不重复计总时间）或 speed_mps 换算",
            "时间模式缺真实时间成本 → typed TIME_COST_NOT_EVALUATED，"
            "绝不以几何长度冒充秒（秒 ≠ 米）",
            "输入为投影/米制平面坐标（时间制由边属性提供秒数，不用几何长度换算）",
        ],
        limitations=[
            "设施点按最近节点欧氏吸附；不可达设施与吸附失败分别计数披露，"
            "不把无覆盖悄悄当零差异",
            "不建模转向惩罚/拥堵；需要转向成本应改用 service_area 族并披露",
            "方案两两比较 O(k²)：方案数受 MAX_SCHEMES 上界约束，超限 typed 拒绝",
        ],
        crs_class="PROJECTED_REQUIRED",
        scientific_preconditions=["projected_crs_required"],
        random_seed_policy="deterministic",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/tools/test_capability_ecosystem_primitives.py::"
            "test_cap_o09_network_service_coverage_consistency",
        ],
        version="1.0",
    ),
]

PARAMETER_CONTRACTS = [
    ParameterContract(
        id="conditional_quantile_zonal_stats",
        version=1,
        description=(
            "条件/多分位数分区栅格统计：分位数 ∈ (0,1)、最多 16 项；"
            "where 仅允许受限 value <op> <number> 表达式；nodata 可选。"),
        parameters=[
            ParameterSpec(
                name="quantiles", type="string", required=False,
                default="0.25,0.5,0.75",
                description="逗号分隔分位数；由工具侧解析为浮点列表并逐项校验"),
            ParameterSpec(
                name="where", type="string", required=False, default="",
                description="受限条件表达式（绝不 eval 任意代码）"),
            ParameterSpec(
                name="nodata", type="number", required=False,
                description="覆盖栅格自带 nodata 的数值"),
        ],
    ),
    ParameterContract(
        id="network_service_coverage_consistency",
        version=1,
        description=(
            "线网服务覆盖一致性校验：cutoff > 0 且有界；cost_unit 必须声明"
            "时间或长度量纲；秒与米绝不混合。"),
        parameters=[
            ParameterSpec(
                name="cutoff", type="number", required=True,
                minimum=0.000001, maximum=1_000_000.0,
                description="服务阈值（单位由 cost_unit 声明）"),
            ParameterSpec(
                name="cost_unit", type="enum", default="meters",
                enum_values=["meters", "m", "km", "seconds", "s", "min"],
                description="成本量纲：长度或时间，二者不可混用"),
            ParameterSpec(
                name="metric", type="string", required=False, default="",
                description="请求输出量纲（诊断用，用于量纲冲突预检）"),
            ParameterSpec(
                name="output_unit", type="string", required=False, default="",
                description="请求输出单位（诊断用）"),
            ParameterSpec(
                name="request_metric", type="string", required=False, default="",
                description="请求指标（诊断用）"),
        ],
    ),
]
