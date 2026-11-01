"""多尺度表达档案服务（scale_profile.v1，GOAL 04 M7）。

工具面（app/tools/scale_profile_tools.py）经 ToolRegistry 注册；提交链
复用 apply_gis_mutation_batch 单事务 CAS + provenance（不建第二事务）。
"""
from app.services.gis_harness.scale_profile.service import (
    MAX_RECEIPT_RING,
    RECEIPTS_KEY,
    SCHEMA_VERSION,
    ScaleProfileError,
    apply_scale_representation,
    inspect_map_scale_profile,
    preview_scale_representation,
)

__all__ = [
    "MAX_RECEIPT_RING",
    "RECEIPTS_KEY",
    "SCHEMA_VERSION",
    "ScaleProfileError",
    "apply_scale_representation",
    "inspect_map_scale_profile",
    "preview_scale_representation",
]
