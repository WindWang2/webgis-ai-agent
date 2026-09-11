"""模型推理算法域包（Harness V8 / ADR-0136，B-10/#1212 链路闭合）。

`model_*` capability（capabilities/modelops.py）的实现者不是统计算法，
而是 ModelOps 的深度学习模型族 —— 本包为每族注册一条**模型推理算法**
描述符（tool_candidates = modelops 工具面），使
capability → algorithm → tool 解析链对模型路径可导航，且
parity 校验（每个 capability 至少一条 algorithm）闭合。

资源/兼容性的精确声明在 ModelOps descriptor（registry 真源）；
此处只投影响链导航的最小面（不要在此复制模型契约 —— 图/引擎
按需从 ModelOps registry 读）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.algorithm_registry import (
    AlgorithmDescriptor,
    ResourceEnvelope,
)

_INFERENCE_TOOLS = ["modelops_run_inference", "modelops_run_promptable"]
_GEOJSON_OUT_TOOLS = list(_INFERENCE_TOOLS)

ALGORITHMS: List[AlgorithmDescriptor] = [
    AlgorithmDescriptor(
        id="model.inference.semantic_segmentation", name="模型语义分割",
        category="model_inference",
        capabilities=["model_image_segmentation"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="raster_surface",
        tool_candidates=_INFERENCE_TOOLS,
        cpu_cost="high", memory_cost="high", io_cost="medium",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=[
            "候选模型按 task_type/bands/分辨率兼容性筛选（ModelOps registry）",
            "概率输出按类聚合，argmax 出类别栅格（置信度同帧披露）",
        ],
        limitations=[
            "模型可用性受 owner scope 与 provider 运行时约束",
            "GPU 资源由 ModelOps VRAM ledger 调度（无 GPU 时降 CPU 模型）",
        ],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=["raster_uncertainty"],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=65536,
            notes="按 tile 计费（chip×context 网格，65536 = 引擎单 run "
                  "tile 硬顶 MAX_TILES_PER_RUN）；精确 envelope 在 "
                  "ModelOps resource estimate（basis=declared）"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
    AlgorithmDescriptor(
        id="model.inference.object_detection", name="模型目标检测",
        category="model_inference",
        capabilities=["model_object_detection"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="polygon_feature_set",
        tool_candidates=_INFERENCE_TOOLS,
        cpu_cost="high", memory_cost="high", io_cost="medium",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=["tile 检测 + 类内 NMS 融合（确定性 tie-break）"],
        limitations=["边缘 tile 检测框按 pad 偏移校正（全局像素坐标）"],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=65536,
            notes="按 tile 计费（MAX_TILES_PER_RUN 硬顶）；精确 envelope "
                  "在 ModelOps estimate"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
    AlgorithmDescriptor(
        id="model.inference.instance_segmentation", name="模型实例分割",
        category="model_inference",
        capabilities=["model_instance_segmentation"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="raster_surface",
        tool_candidates=_GEOJSON_OUT_TOOLS,
        cpu_cost="high", memory_cost="high", io_cost="medium",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=["逐实例 id 融合（跨 tile first-write-wins）"],
        limitations=["矢量化为可选后处理（polygonize_instances）"],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=65536,
            notes="融合缓冲 RAM>256MiB 走 memmap（磁盘有界；tile 顶同上）"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
    AlgorithmDescriptor(
        id="model.inference.change_detection", name="模型变化检测",
        category="model_inference",
        capabilities=["model_change_detection"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="raster_surface",
        tool_candidates=_INFERENCE_TOOLS,
        cpu_cost="high", memory_cost="high", io_cost="medium",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=["双时相对齐（光学/光学、SAR/光学融合族）"],
        limitations=["跨传感器对的辐射归一化由 preprocess 声明承载"],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=["raster_uncertainty"],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=65536,
            notes="双时相读取（IO×2；tile 顶同上）"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
    AlgorithmDescriptor(
        id="model.inference.super_resolution", name="模型超分辨率",
        category="model_inference",
        capabilities=["model_super_resolution"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="raster_surface",
        tool_candidates=_INFERENCE_TOOLS,
        cpu_cost="high", memory_cost="high", io_cost="medium",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=["stride=chip 无重叠平铺（鬼影消除）"],
        limitations=["仅支持无重叠 stride 的模型注册"],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=65536,
            notes="输出 = 输入×scale（显存按输出尺寸预算）"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
    AlgorithmDescriptor(
        id="model.inference.temporal_forecast", name="模型时序预测",
        category="model_inference",
        capabilities=["model_temporal_forecast"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="raster_surface",
        tool_candidates=_INFERENCE_TOOLS,
        cpu_cost="medium", memory_cost="medium", io_cost="medium",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=["时序栅格栈（波段=变量）单窗口推理"],
        limitations=[],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=1, notes="单窗口路径"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
    AlgorithmDescriptor(
        id="model.inference.temporal_classification", name="模型时序分类",
        category="model_inference",
        capabilities=["model_temporal_classification"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="raster_surface",
        tool_candidates=_INFERENCE_TOOLS,
        cpu_cost="medium", memory_cost="medium", io_cost="medium",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=["逐时相类别概率（T,K）→ argmax 时序"],
        limitations=[],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=1, notes="单窗口路径"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
    AlgorithmDescriptor(
        id="model.inference.embedding", name="模型特征嵌入",
        category="model_inference",
        capabilities=["model_embedding"],
        input_artifact_types=["raster_surface"],
        output_artifact_type="feature_collection",
        tool_candidates=_INFERENCE_TOOLS,
        cpu_cost="medium", memory_cost="medium", io_cost="low",
        preferred_execution_policy="ASYNC", priority=30,
        algorithm_family="model_inference",
        assumptions=["chip 级嵌入提取（相似性检索输入）"],
        limitations=[],
        crs_class="GEOGRAPHIC_OK",
        uncertainty_outputs=[],
        scientific_status="VALIDATED",
        resource_envelope=ResourceEnvelope(
            bytes_per_feature=0, hard_max_features=65536,
            notes="嵌入维度 × tile 数计费"),
        cancellation_profile="chunk_boundary",
        conformance_tests=[
            "tests/unit/modelops/test_backends_and_packages.py",
            "tests/unit/modelops/test_compatibility.py",
        ],
    ),
]
