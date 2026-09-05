"""决策分析域算法包（ADR-0099 §34 domain packs）。

MCDA 条目迁自 workbench VNext（PR #1142）对中央 registry 的追加 ——
合并时按域包架构归位到本模块。域分组事实源 + descriptor.category。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor

ALGORITHMS: List[AlgorithmDescriptor] = [
    # ── Semantic V2（ADR-0098）：MCDA 决策评价 ────────────────────────
    # 候选×准则×约束 → WSM/TOPSIS + Pareto + 敏感性/不确定性。引擎是
    # DecisionEngineV3（确定性；蒙特卡洛档位固定种子可复现）。观测证据、
    # 用户权重、假设在结果中必须可区分 —— 不合成证据。
    AlgorithmDescriptor(
        id="decision.mcda.wsm", name="MCDA 决策评价（WSM/TOPSIS）",
        category="decision_analysis",
        capabilities=["mcda_evaluation"],
        input_artifact_types=["poi_feature_set", "point_feature_set",
                              "polygon_feature_set", "admin_aggregate_table"],
        output_artifact_type="stats_table",
        tool_candidates=["spatial_decision_v3"],
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="ASYNC", priority=40,
        # ADR-0099 合并归位：科学元数据补齐（DecisionEngineV3 固定种子
        # 蒙特卡洛 → fixed_seed；库层测试见 test_spatial_decision_v3_core）。
        algorithm_family="mcda",
        method_references=["hwang_yoon1981"],
        crs_class="CRS_AGNOSTIC",
        random_seed_policy="fixed_seed",
        scientific_status="VALIDATED",
        conformance_tests=[
            "tests/unit/test_spatial_decision_v3_core.py",
        ],
        assumptions=[
            "权重/准则方向由声明给定；蒙特卡洛不确定性仅在声明不确定参数时激活",
        ],
        limitations=[
            "不合成证据：无不确定参数时不注入伪噪声分布",
        ],
    ),
]
