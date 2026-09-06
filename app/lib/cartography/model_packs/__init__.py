"""Map Model Domain Packs — 制图模型库 V3 域包聚合（ADR-0101 D1/D2）.

seed 模型（model_library.SEED_MAP_MODELS）保持不变；本包按域组织扩充模型，
``MapModelRegistry.load_builtins()`` 在 seed 之后按确定性顺序载入。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel
from app.lib.cartography.model_packs._base import FRONTEND_RUNTIME_LAYER_TYPES
from app.lib.cartography.model_packs.decision_analysis import DECISION_ANALYSIS_PACK
from app.lib.cartography.model_packs.point_line import POINT_LINE_PACK
from app.lib.cartography.model_packs.polygon_statistical import POLYGON_STATISTICAL_PACK
from app.lib.cartography.model_packs.raster_remote_sensing import RASTER_REMOTE_SENSING_PACK

# 确定性顺序（点线 → 面 → 栅格 → 决策）；新增域包在此追加。
MODEL_PACK_MODELS: List[MapModel] = [
    *POINT_LINE_PACK,
    *POLYGON_STATISTICAL_PACK,
    *RASTER_REMOTE_SENSING_PACK,
    *DECISION_ANALYSIS_PACK,
]

__all__ = [
    "MODEL_PACK_MODELS",
    "FRONTEND_RUNTIME_LAYER_TYPES",
]
