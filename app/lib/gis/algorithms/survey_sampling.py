"""概率抽样调查域包（GOAL-20261127-03）。

调查抽样设计共享 ``survey_sampling`` 能力：冻结抽样框 / 样本量预览 /
可复现选样 / 设计一致估计。数值实现在
``app/lib/survey_sampling/``（Olofsson et al. 2014 分层 SRS 不放回
估计量；strata == 地图类 ⇒ 行和已知 ⇒ UA/PA 特化精确公式）。

与既有 ``sampling.*``（布点工具，Goal 07）的边界：那边是**几何布点**
（在面内撒点），本域是**概率调查**（纳入概率/权重/参考观测/总体推断）。
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
        id="survey.frame_build", name="概率抽样框构建",
        category="survey_sampling",
        capabilities=["survey_sampling"],
        input_artifact_types=["raster_object"],
        output_artifact_type="stats_table",
        tool_candidates=["survey_frame_preview"],
        cpu_cost="medium", memory_cost="medium", io_cost="medium",
        preferred_execution_policy="THREAD", priority=20,
        algorithm_family="survey_sampling",
        method_references=["olofsson2014"],
        assumptions=[
            "分类栅格值域=类别字典（整数类别编码）",
            "抽样单元=有效像元（NoData/掩膜外排除并披露）",
            "像元测地面积跨框相对变化≤容差（默认 1%）才允许面积权",
            "层内单元索引按行主序枚举（确定性；同源同 digest 同坐标）",
        ],
        limitations=[
            "经纬度/Web Mercator 网格面积随纬度变化 → typed 拒绝，"
            "要求等面积 CRS 重投影（禁止像元数直接当面积）",
            "框单元总量预算 2×10⁷（uint32 索引内存保护）",
            "空类别（零有效像元）拒绝建框——空层无法支撑总体推断",
        ],
        crs_class="PROJECTED_REQUIRED",
        uncertainty_outputs=[],
        random_seed_policy="deterministic",
        numerical_tolerance="计数精确；面积=计数×平均测地面积（容差内）",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=4,
            hard_max_features=20000000,
            notes="每有效像元 4 字节 uint32 索引；分块读峰值另计"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/survey_sampling/test_frame.py::test_frame_counts_and_digest",
            "tests/unit/survey_sampling/test_frame.py::test_frame_rejects_unequal_area",
        ],
    ),
    AlgorithmDescriptor(
        id="survey.design_preview", name="抽样设计预览与样本量",
        category="survey_sampling",
        capabilities=["survey_sampling"],
        input_artifact_types=["stats_table"],
        output_artifact_type="stats_table",
        tool_candidates=["survey_design_preview"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="INLINE", priority=20,
        algorithm_family="survey_sampling",
        method_references=["olofsson2014"],
        assumptions=[
            "分层简单随机（不放回）；层=地图类",
            "目标 SE 反推总量（Eq.13 口径）：n=(Σ W_h S_h / S)²，"
            "S_h=√(v̄_h(1−v̄_h))，v̄_h 缺省 0.5 保守",
            "每层硬下限 2（方差可估必要条件）；建议 30（正态近似）",
        ],
        limitations=[
            "预览无副作用；预算/可达性由用户提供或显式默认",
            "稀有类不跳过（等额保底；goal 硬约束）",
        ],
        crs_class="CRS_AGNOSTIC",
        uncertainty_outputs=[],
        random_seed_policy="deterministic",
        numerical_tolerance="整数分配精确（大盘尼余数）",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=8, hard_max_features=64,
            notes="层数上限 64"),
        cancellation_profile="none",
        conformance_tests=[
            "tests/unit/survey_sampling/test_design.py::test_preview_target_se",
            "tests/unit/survey_sampling/test_design.py::test_preview_no_rare_class_skip",
        ],
        parameter_contract_ref="survey_design",
    ),
    AlgorithmDescriptor(
        id="survey.stratified_estimate", name="设计一致精度与面积估计",
        category="survey_sampling",
        capabilities=["survey_sampling"],
        input_artifact_types=["stats_table"],
        output_artifact_type="stats_table",
        tool_candidates=["survey_estimate"],
        cpu_cost="low", memory_cost="low", io_cost="low",
        preferred_execution_policy="INLINE", priority=20,
        algorithm_family="survey_sampling",
        method_references=["olofsson2014"],
        assumptions=[
            "层内 SRS 不放回；参考标签独立于预测（盲标默认）",
            "加权误差矩阵 p̂_hj = W_h·n_hj/n_h；层=地图类 ⇒ 行和已知",
            "UA_i=q_i 无分母抽样误差；PA_j/面积用 δ 方法线性化",
            "非响应由预抽替补顶替（原因披露）；替补率超设计上限 ⇒ "
            "拒绝总体推断",
        ],
        limitations=[
            "n_h<2 层方差不可估（evaluable=false 披露，不伪造 CI）",
            "任一层零响应 ⇒ 总体级指标全部不可估（not_evaluated）",
            "CI 允许越界 [0,1]：原始区间+裁剪副本同时披露",
            "非响应 missing-at-random 假设不可验证——只披露不宣称",
        ],
        crs_class="CRS_AGNOSTIC",
        uncertainty_outputs=["se", "ci95"],
        random_seed_policy="deterministic",
        numerical_tolerance="估计量与手算 oracle 一致；CI 覆盖模拟 95%±MC",
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=8, hard_max_features=4096,
            notes="矩阵单元数 = 层×参考类（≤64×64）"),
        cancellation_profile="none",
        conformance_tests=[
            "tests/unit/survey_sampling/test_estimation_oracle.py::test_hand_computed_oracle",
            "tests/unit/survey_sampling/test_estimation_oracle.py::test_full_census_zero_variance",
            "tests/unit/survey_sampling/test_estimation_oracle.py::test_ci_coverage_simulation",
        ],
    ),
]

PARAMETER_CONTRACTS: List[ParameterContract] = [
    ParameterContract(
        id="survey_design",
        description="概率抽样调查设计参数（冻结前预览合同）",
        parameters=[
            ParameterSpec(
                name="strategy",
                type="enum",
                required=False,
                default="equal",
                enum_values=["equal", "proportional", "neyman"],
                description="分配策略：等额（稀有类保底）/比例（等概率）/"
                            "Neyman（固定总量最小化 OA 方差）",
            ),
            ParameterSpec(
                name="n_total",
                type="integer",
                required=False,
                description="总样本量（与 target_se_oa 二选一）",
            ),
            ParameterSpec(
                name="target_se_oa",
                type="number",
                required=False,
                description="总体精度目标标准误（Eq.13 反推总量）",
            ),
            ParameterSpec(
                name="seed",
                type="integer",
                required=False,
                default=42,
                description="随机种子（选样可复现）",
            ),
            ParameterSpec(
                name="backup_per_stratum",
                type="integer",
                required=False,
                default=0,
                description="每层预抽替补数（非响应顶替，原因披露）",
            ),
            ParameterSpec(
                name="max_nonresponse_rate",
                type="number",
                required=False,
                default=0.2,
                description="非响应/顶替率上限（超出拒绝总体推断）",
            ),
        ],
    ),
]
