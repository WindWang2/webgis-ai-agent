"""GIS 领域知识库 —— artifact / capability / algorithm 的统一注册层。

职责边界：
- ArtifactTypeRegistry   「计算得到了什么」的语义词汇
- CapabilityRegistry     「需要什么能力」的稳定词汇（recipe/plan 引用）
- AlgorithmRegistry      「如何计算」：capability → 算法 → 工具候选
- AlgorithmResolver      capability → algorithm → tool 的确定性裁决

执行仍在 ToolRegistry / ToolDispatchService；制图表达权威在
app.lib.cartography.model_library；本包不 import app/services。
"""
from app.lib.gis.artifacts import (
    ArtifactDescriptor,
    ArtifactTypeDescriptor,
    ArtifactTypeRegistry,
    artifact_from_profile,
    artifact_from_ref_descriptor,
    get_artifact_type_registry,
    reset_artifact_type_registry,
)
from app.lib.gis.capability_registry import (
    CapabilityDescriptor,
    CapabilityRegistry,
    get_capability_registry,
    reset_capability_registry,
)
from app.lib.gis.algorithm_registry import (
    AlgorithmDescriptor,
    AlgorithmRegistry,
    get_algorithm_registry,
    reset_algorithm_registry,
)
from app.lib.gis.algorithm_resolver import (
    AlgorithmResolution,
    AlgorithmResolver,
    FallbackStep,
    get_algorithm_resolver,
    reset_algorithm_resolver,
)

__all__ = [
    "ArtifactDescriptor",
    "ArtifactTypeDescriptor",
    "ArtifactTypeRegistry",
    "artifact_from_profile",
    "artifact_from_ref_descriptor",
    "get_artifact_type_registry",
    "reset_artifact_type_registry",
    "CapabilityDescriptor",
    "CapabilityRegistry",
    "get_capability_registry",
    "reset_capability_registry",
    "AlgorithmDescriptor",
    "AlgorithmRegistry",
    "get_algorithm_registry",
    "reset_algorithm_registry",
    "AlgorithmResolution",
    "AlgorithmResolver",
    "FallbackStep",
    "get_algorithm_resolver",
    "reset_algorithm_resolver",
]
