"""G06 / M5 参数契约域包（ADR-0099 §34 域包惯例）。

纪律：``ALGORITHMS`` 保持**空表** —— 本方向不新增算法描述符，
避免改变既有 AlgorithmRegistry 的 253 条硬断言与 catalog 生成物；
新原语是 ToolRegistry 的真实执行能力，其科学元数据通过
``app/lib/gis/capability_ecosystem_primitives.py`` 的只读投影提供
（见 docs/dev/GIS_TOOL_AUTHORING_CONTRACT.md）。

``PARAMETER_CONTRACTS`` 经既有 ``iter_contract_packs()`` 汇入唯一的
``ParameterContractRegistry``，工具侧用 ``apply_contract`` 严格校验。
"""
from __future__ import annotations

from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: list = []

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
