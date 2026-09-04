"""插值 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="spatial_interpolation", name="空间插值", category="analysis",
            description="IDW / Kriging 等插值。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["terrain_surface"],
            purpose_template="空间插值",
        ),
]
