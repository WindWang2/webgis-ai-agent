"""能力域包聚合（ADR-0099 §34）。decision.py 为 workbench 分支预留。"""
from __future__ import annotations

from typing import Iterable, List

from app.lib.gis.capability_registry import CapabilityDescriptor
from app.lib.gis.capabilities.aggregation import CAPABILITIES as _aggregation
from app.lib.gis.capabilities.data_access import CAPABILITIES as _data_access
from app.lib.gis.capabilities.decision import CAPABILITIES as _decision
from app.lib.gis.capabilities.density import CAPABILITIES as _density
from app.lib.gis.capabilities.geometry import CAPABILITIES as _geometry
from app.lib.gis.capabilities.interpolation import CAPABILITIES as _interpolation
from app.lib.gis.capabilities.network import CAPABILITIES as _network
from app.lib.gis.capabilities.raster import CAPABILITIES as _raster
from app.lib.gis.capabilities.statistics import CAPABILITIES as _statistics
from app.lib.gis.capabilities.temporal import CAPABILITIES as _temporal
from app.lib.gis.capabilities.terrain import CAPABILITIES as _terrain


def iter_capability_packs() -> Iterable[List[CapabilityDescriptor]]:
    yield from (
        _data_access, _geometry, _aggregation, _density, _statistics,
        _interpolation, _network, _terrain, _raster, _temporal, _decision,
    )
