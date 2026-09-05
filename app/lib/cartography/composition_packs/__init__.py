"""Composition Template Domain Packs — 组合模板域包聚合（ADR-0101 D1）.

seed 模板（composition_templates.SEED_COMPOSITION_TEMPLATES）保持不变；
本包按域组织扩充模板，``CompositionTemplateRegistry.load_builtins()``
在 seed 之后按确定性顺序载入。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.composition_packs.decision_deliverables import (
    DECISION_PACK,
    DELIVERABLES_PACK,
)
from app.lib.cartography.composition_packs.domain_reports import (
    PLANNING_EQUITY_PACK,
    RISK_ENVIRONMENT_PACK,
    TEMPORAL_CHANGE_PACK,
)
from app.lib.cartography.composition_packs.network_transport import NETWORK_TRANSPORT_PACK
from app.lib.cartography.composition_packs.remote_sensing import REMOTE_SENSING_PACK
from app.lib.cartography.composition_packs.statistical import STATISTICAL_PACK
from app.lib.cartography.composition_packs.terrain_hydrology import TERRAIN_HYDROLOGY_PACK
from app.lib.cartography.composition_templates import MapCompositionTemplate

# 确定性顺序（统计 → 网络 → 地形水文 → 遥感 → 时序 → 风险环境 →
# 规划公平 → 决策 → 交付物）；新增域包在此追加。
COMPOSITION_PACK_TEMPLATES: List[MapCompositionTemplate] = [
    *STATISTICAL_PACK,
    *NETWORK_TRANSPORT_PACK,
    *TERRAIN_HYDROLOGY_PACK,
    *REMOTE_SENSING_PACK,
    *TEMPORAL_CHANGE_PACK,
    *RISK_ENVIRONMENT_PACK,
    *PLANNING_EQUITY_PACK,
    *DECISION_PACK,
    *DELIVERABLES_PACK,
]

__all__ = ["COMPOSITION_PACK_TEMPLATES"]
