"""模型推理能力包（Harness V8 / ADR-0136，B-10 拆分）。

历史缺口：capability registry 的 ``image_segmentation`` 描述绑定 k-means
统计语义，而模型推理（GeoAI）只是 modelops 工具的 ToolRegistry 标签 ——
同名异义 + 模型功能对 planner 隐身。本包注册 ``model_*`` 词汇族：模型
推理能力的稳定锚点（Unified Capability Graph 的 model→capability
implements 边指向这里；capability_descriptors/tool_surface 的检索语料
同样受益）。统计法与学习法的裁决由此有了可区分的词汇。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="model_image_segmentation", name="模型影像分割", category="analysis",
        domain="raster",
        description=(
            "以深度学习模型对遥感影像做语义/可提示分割（GeoAI 推理路径，"
            "区别于 k-means 统计分割）：候选模型按 task_type/bands/分辨率"
            "兼容性筛选，输出类别/置信度栅格。"),
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 影像模型分割",
    ),
    CapabilityDescriptor(
        id="model_object_detection", name="模型目标检测", category="analysis",
        domain="raster",
        description=(
            "深度学习目标检测（建筑物/车辆等）：tile 推理 + NMS 融合，"
            "输出检测框 GeoJSON（全局像素坐标 → 地理参考）。"),
        input_artifact_types=["raster_surface"],
        output_artifact_types=["polygon_feature_set"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 目标检测",
    ),
    CapabilityDescriptor(
        id="model_instance_segmentation", name="模型实例分割", category="analysis",
        domain="raster",
        description=(
            "实例级分割：逐实例 id 栅格 + 可选矢量化 GeoJSON（多边形地理"
            "参考）。"),
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface", "polygon_feature_set"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 实例分割",
    ),
    CapabilityDescriptor(
        id="model_change_detection", name="模型变化检测", category="analysis",
        domain="raster",
        description=(
            "双时相（光学/光学、SAR/光学融合）模型变化检测：输出变化"
            "栅格/概率面。"),
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 模型变化检测",
    ),
    CapabilityDescriptor(
        id="model_super_resolution", name="模型超分辨率", category="analysis",
        domain="raster",
        description="逐 chip 上采样重建（stride=chip 无重叠），输出放大栅格。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 超分辨率重建",
    ),
    CapabilityDescriptor(
        id="model_temporal_forecast", name="模型时序预测", category="analysis",
        domain="temporal",
        description="时序栅格栈的模型化预测（波段=变量的预测栈输出）。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 时序预测",
    ),
    CapabilityDescriptor(
        id="model_temporal_classification", name="模型时序分类", category="analysis",
        domain="temporal",
        description="逐时相类别概率（T,K）→ 时序类别序列。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["raster_surface"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 时序分类",
    ),
    CapabilityDescriptor(
        id="model_embedding", name="模型特征嵌入", category="analysis",
        domain="raster",
        description="影像嵌入提取（相似性检索/下游统计的输入）。",
        input_artifact_types=["raster_surface"],
        output_artifact_types=["feature_collection"],
        preferred_execution="celery",
        deterministic=True,
        version="1.0",
        purpose_template="{subject} 特征嵌入",
    ),
]
