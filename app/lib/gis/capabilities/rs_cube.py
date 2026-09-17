"""RS Temporal Cube 能力包（本方向 ownership：temporal cube + SAR-optical fusion）。

ADR-0099 §34 domain packs：新能力在各自域模块注册。本包是
remote_sensing/raster 之外的独立扩展面——时序立方体描述符/对齐/融合/
样本 split 的能力声明（id 小写无点；被 SkillPolicy / CapabilityGraph
经 registry 投影自动消费——本包不改 capability_graph.py）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="rs_cube_describe", name="时序立方体描述", category="raster",
            domain="raster",
            description="refs-only 时序立方体描述符：资产/时间/波段/极化/"
                        "网格/质量/缺口账的机器可读清单与有界上下文摘要。",
            input_artifact_types=["rs_cube_descriptor"],
            output_artifact_types=["rs_cube_descriptor"],
            geometry_requirements=["raster"],
            compatible_map_models=["raster_surface"],
            purpose_template="时序立方体盘点",
        ),

        CapabilityDescriptor(
            id="rs_cube_alignment", name="光学/SAR 获取对齐", category="raster",
            domain="raster",
            description="跨模态获取配对计划（容差内最近邻、一景至多服务"
                        "一期）+ 类型化缺口账；网格恒等不一致 typed 拒绝，"
                        "绝不静默重采样。",
            input_artifact_types=["rs_cube_descriptor"],
            output_artifact_types=["rs_cube_descriptor", "stats_table"],
            geometry_requirements=["raster"],
            purpose_template="光学/SAR 时间轴对齐",
        ),

        CapabilityDescriptor(
            id="rs_temporal_feature_pack", name="时序特征包",
            category="raster", domain="raster",
            description="cube 级年度/季节合成 + 分位数 + Sen 稳健斜率 + "
                        "CUSUM 变点 + 物候代理（复用 phenology）。",
            input_artifact_types=["rs_cube_descriptor", "raster_surface"],
            output_artifact_types=["raster_surface", "stats_table"],
            geometry_requirements=["raster"],
            purpose_template="时序特征提取",
        ),

        CapabilityDescriptor(
            id="rs_joint_fusion", name="SAR×光学联合特征融合",
            category="raster", domain="raster",
            description="特征级联合栈（逐像元类型化覆盖码）+ 晚期证据融合"
                        "（描述性加权与符号一致性；非概率模型）。",
            input_artifact_types=["raster_surface", "rs_cube_descriptor"],
            output_artifact_types=["raster_surface", "stats_table"],
            geometry_requirements=["raster"],
            purpose_template="SAR 与光学联合分析",
        ),

        CapabilityDescriptor(
            id="rs_sample_split", name="样本挂接与防泄漏分割",
            category="raster", domain="raster",
            description="多边形样本挂接（nan-aware 聚合）+ 地理分块折/"
                        "时间前向链 split（同 block 必同 fold、严格前向链）。",
            input_artifact_types=["raster_surface", "polygon_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="样本构建与防泄漏分割",
        ),
]
