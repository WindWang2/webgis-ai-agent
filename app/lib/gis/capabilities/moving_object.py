"""移动对象轨迹 能力包（GOAL-20261127-02；ADR-0099 §34 domain packs）。

轨迹构建/驻留/区域事件为真实分析 producer（status=available 语义由
算法注册表 conformance 面佐证）；trajectory_apply 为地图提交面
（planned——工具面真实存在，非分析 producer，resolver 不派发为分析）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="trajectory_build", name="轨迹构建", category="analysis",
        description=(
            "实体观测流→测地分段轨迹与运动量区间估计"
            "（trajectory_build；时隙/跳变/矛盾断点可溯源）。"),
        input_artifact_types=["point_feature_set"],
        output_artifact_types=["line_feature_set", "stats_table"],
        purpose_template="构建移动对象轨迹",
    ),
    CapabilityDescriptor(
        id="trajectory_dwell", name="驻留识别", category="analysis",
        description=(
            "半径+时长+点数阈值的可验证驻留识别，含近似命中披露"
            "（trajectory_build 内联）。"),
        input_artifact_types=["point_feature_set"],
        output_artifact_types=["point_feature_set"],
        purpose_template="识别轨迹驻留",
    ),
    CapabilityDescriptor(
        id="region_events", name="区域时空事件", category="analysis",
        description=(
            "区域 enter/exit/cross/dwell 事件决策表，推断区间诚实标注"
            "（trajectory_build 给定 regions_ref 时内联）。"),
        input_artifact_types=["point_feature_set", "polygon_feature_set"],
        output_artifact_types=["event_table"],
        purpose_template="分析轨迹与区域的时空事件",
    ),
    CapabilityDescriptor(
        id="trajectory_quality", name="轨迹质量诊断", category="statistics",
        description=(
            "摄取异常计数、采样间隔统计与分离式清洗预览"
            "（trajectory_inspect/build）。"),
        input_artifact_types=["point_feature_set"],
        output_artifact_types=["stats_table"],
        purpose_template="诊断轨迹数据质量",
    ),
    CapabilityDescriptor(
        id="trajectory_apply", name="轨迹落图", category="platform",
        status="planned",
        description=(
            "把轨迹产品按一次 CAS 事务落图（trajectory_apply；图层族原子替换，"
            "用户展示态保留）。"),
    ),
]
