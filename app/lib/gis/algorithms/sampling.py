"""空间抽样算法域包（Goal 07 Science V6 新域）。

三个抽样设计共享 ``spatial_sampling`` 能力：简单随机 / 系统网格 /
分层。数值实现在 ``app/lib/geo_analysis/spatial_sampling.py``（投影后
度量空间均匀撒点 + prepared 覆盖判定；numpy default_rng 确定性）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import (
    AlgorithmDescriptor,
    ResourceEnvelope,
)
from app.lib.gis.parameter_contracts import ParameterContract, ParameterSpec

ALGORITHMS: List[AlgorithmDescriptor] = [
    AlgorithmDescriptor(
        id="sampling.random_points", name="简单随机空间抽样",
        category="spatial_sampling",
        capabilities=["spatial_sampling"],
        input_artifact_types=["polygon_feature_set", "admin_boundary_set",
                              "admin_aggregate_table"],
        output_artifact_type="point_feature_set",
        tool_candidates=["sample_random_points"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="THREAD", priority=20,
        algorithm_family="spatial_sampling",
        method_references=["cochran1977"],
        assumptions=[
            "逐多边形简单随机设计（SRS）：每面独立 n 个均匀随机点",
            "均匀性在投影后度量空间定义（地理输入自动投影局部 UTM）",
            "拒绝采样上限 200×需求（凹型框覆盖率过低 → 类型化报错）",
            "numpy default_rng(seed)：同 (输入, seed) 逐位可复现",
        ],
        limitations=[
            "零面积多边形跳过（计数披露）",
            "不约束点间最小距离（需要最小间距时用 systematic_grid）",
        ],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        random_seed_policy="caller_seeded",
        numerical_tolerance="同 (输入, seed) 载荷逐位一致（确定性锚）",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=64,
            hard_max_features=1000000,
            notes="样本点坐标+属性；100 万样本硬顶（先拒绝后分配）"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/lib/test_spatial_sampling_v6.py::"
            "test_random_points_containment_and_determinism",
            "tests/unit/lib/test_spatial_sampling_v6.py::"
            "test_random_points_seed_sensitivity",
            "tests/unit/lib/test_spatial_sampling_v6.py::"
            "test_sampling_adversarial_inputs",
        ],
        parameter_contract_ref="random_sampling_analysis",
    ),

    AlgorithmDescriptor(
        id="sampling.systematic_grid", name="系统网格空间抽样",
        category="spatial_sampling",
        capabilities=["spatial_sampling"],
        input_artifact_types=["polygon_feature_set", "admin_boundary_set",
                              "admin_aggregate_table"],
        output_artifact_type="point_feature_set",
        tool_candidates=["sample_systematic_grid"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="THREAD", priority=20,
        algorithm_family="spatial_sampling",
        method_references=["cochran1977"],
        assumptions=[
            "规则格网（spacing 米）+ 随机起点偏移（seed 决定，避免与坐标轴"
            "对齐的周期性偏差；Cochran 1977 系统抽样惯例）",
            "仅保留落入抽样框的格点（prepared 覆盖判定）",
            "格点数上限 4,000,000（间距过小时先拒绝后分配）",
        ],
        limitations=[
            "系统抽样无设计无偏方差（需近似方差时用 random/stratified）",
            "周期性地物（如规整田块）与固定间距可能混叠",
        ],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        random_seed_policy="caller_seeded",
        numerical_tolerance="同 (输入, seed) 载荷逐位一致（确定性锚）",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=64,
            hard_max_features=4000000,
            notes="格点裁剪后输出；格网规模护栏 4M 单元"),
        cancellation_profile="none",
        conformance_tests=[
            "tests/unit/lib/test_spatial_sampling_v6.py::"
            "test_systematic_grid_spacing_and_alignment",
            "tests/unit/lib/test_spatial_sampling_v6.py::"
            "test_sampling_adversarial_inputs",
        ],
        parameter_contract_ref="systematic_sampling_analysis",
    ),

    AlgorithmDescriptor(
        id="sampling.stratified_points", name="分层空间抽样",
        category="spatial_sampling",
        capabilities=["spatial_sampling"],
        input_artifact_types=["polygon_feature_set", "admin_boundary_set",
                              "admin_aggregate_table"],
        output_artifact_type="point_feature_set",
        tool_candidates=["sample_stratified_points"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="THREAD", priority=20,
        algorithm_family="spatial_sampling",
        method_references=["cochran1977"],
        assumptions=[
            "按 stratum_field 分层；equal=每层 n，proportional=按层面积"
            "权重分配（floor + 大盘尼余数，总量恰为层样本数）",
            "层内按多边形面积再分摊（比例分配），面级均匀随机",
            "分配表进 meta（逐层样本数披露）",
        ],
        limitations=[
            "层名取 str 的字符串化（数值层名按字典序排列）",
            "比例分配下面积占比过小的多边形可能 0 样本（按需提高层预算）",
        ],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        random_seed_policy="caller_seeded",
        numerical_tolerance="同 (输入, seed) 载荷逐位一致（确定性锚）",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=64,
            hard_max_features=1000000,
            notes="样本点坐标+属性；100 万样本硬顶"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/lib/test_spatial_sampling_v6.py::"
            "test_stratified_allocation_tables",
            "tests/unit/lib/test_spatial_sampling_v6.py::"
            "test_sampling_adversarial_inputs",
        ],
        parameter_contract_ref="stratified_sampling_analysis",
    ),
]

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="random_sampling_analysis", version=1,
        description="简单随机抽样：每面样本数 / 种子。",
        parameters=[
            ParameterSpec(
                name="n_per_polygon", type="integer", default=10,
                minimum=1, maximum=100000, unit="count",
                description="每个多边形内生成的随机样本数",
            ),
            ParameterSpec(
                name="seed", type="integer", default=42, minimum=0,
                unit="count",
                description="随机种子（numpy PCG64；同种子可复现）",
            ),
        ],
    ),
    ParameterContract(
        id="systematic_sampling_analysis", version=1,
        description="系统网格抽样：间距 / 种子。",
        parameters=[
            ParameterSpec(
                name="spacing", type="number", default=1000, minimum=1,
                unit="meters",
                description="网格间距（米，投影后度量空间）",
            ),
            ParameterSpec(
                name="seed", type="integer", default=42, minimum=0,
                unit="count",
                description="随机起点偏移种子",
            ),
        ],
    ),
    ParameterContract(
        id="stratified_sampling_analysis", version=1,
        description="分层抽样：层字段 / 层样本数 / 分配方式 / 种子。",
        parameters=[
            ParameterSpec(
                name="stratum_field", type="string", required=True,
                description="层别字段名（每要素一个层别值）",
            ),
            ParameterSpec(
                name="n_per_stratum", type="integer", default=30,
                minimum=1, maximum=100000, unit="count",
                description="每层样本数（proportional 时为层预算）",
            ),
            ParameterSpec(
                name="allocation", type="enum", default="equal",
                enum_values=["equal", "proportional"],
                description="层间分配：equal 等额 / proportional 按面积权重",
            ),
            ParameterSpec(
                name="seed", type="integer", default=42, minimum=0,
                unit="count",
                description="随机种子",
            ),
        ],
    ),
]
