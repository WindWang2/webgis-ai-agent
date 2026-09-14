"""GIS Spatial Reasoning Memory（方向 9，ADR-0183）。

可复用的 GIS 世界事实记忆：resolved_place / boundary_ref /
dataset_semantics / field_role / crs_resolution / analysis_artifact /
successful_strategy / provider_failure / product_decision /
user_cartographic_preference。

分层：
- ``contract``   —— 类型/词汇/写入请求/检索结果（唯一类型真相）；
- ``sanitizer``  —— 值/refs 消毒（凭证剥除、路径遮蔽、有界化）；
- ``policy``     —— 写入门（R2：evidence-gated、fail-closed、偏好路由）；
- ``store``      —— 持久化（R3 supersede 链 / R7 预算与失效）；
- ``retrieval``  —— 按情境检索（R4：谓词过滤 + 有界 top-k + 理由）；
- ``projection`` —— 模型面有界投影（R5：``[GIS_MEMORY]`` 块）；
- ``harvest``    —— turn 端收割（生产写入位点，fail-safe）；
- ``queries``    —— narrow interface（situation/planner 消费）。

不重做：cartography 偏好（ADR-0069 账本）、recipe 亲和（V11）、
dataset catalog（ads-v1）、artifact 账本、RecoveryLedger。
"""
from app.services.gis_memory.contract import (  # noqa: F401
    KIND_RESOLVED_PLACE,
    KIND_DATASET_SEMANTICS,
    KIND_PROVIDER_FAILURE,
    SPATIAL_MEMORY_KINDS,
    MemoryEvidence,
    MemoryPolicyError,
    MemoryWriteRequest,
    RetrievedMemory,
    SpatialMemoryRecord,
)

__all__ = [
    "SPATIAL_MEMORY_KINDS",
    "KIND_RESOLVED_PLACE",
    "KIND_DATASET_SEMANTICS",
    "KIND_PROVIDER_FAILURE",
    "MemoryEvidence",
    "MemoryWriteRequest",
    "MemoryPolicyError",
    "SpatialMemoryRecord",
    "RetrievedMemory",
]
