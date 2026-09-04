"""算法域包聚合（ADR-0099 §34）。

中央 AlgorithmRegistry 经 DOMAIN_PACKS 聚合各域模块 —— 新域 = 新模块
+ 在此登记，algorithm_registry.py 不再单文件膨胀。decision.py 为
workbench 分支（feat/agentic-gis-workbench-vnext）的 MCDA 条目预留合并位。
"""
from __future__ import annotations

from typing import Iterable, List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.algorithms.aggregation import ALGORITHMS as _aggregation
from app.lib.gis.algorithms.data_access import ALGORITHMS as _data_access
from app.lib.gis.algorithms.decision import ALGORITHMS as _decision
from app.lib.gis.algorithms.density import ALGORITHMS as _density
from app.lib.gis.algorithms.geometry import ALGORITHMS as _geometry
from app.lib.gis.algorithms.interpolation import ALGORITHMS as _interpolation
from app.lib.gis.algorithms.network import ALGORITHMS as _network
from app.lib.gis.algorithms.point_pattern import ALGORITHMS as _point_pattern
from app.lib.gis.algorithms.raster import ALGORITHMS as _raster
from app.lib.gis.algorithms.remote_sensing import ALGORITHMS as _remote
from app.lib.gis.algorithms.statistics import ALGORITHMS as _statistics
from app.lib.gis.algorithms.terrain import ALGORITHMS as _terrain
from app.lib.gis.algorithms.temporal import ALGORITHMS as _temporal


def iter_domain_packs() -> Iterable[List[AlgorithmDescriptor]]:
    """确定性域序聚合（顺序 = 注册序；同 id 冲突仍由 register() 拒绝）。"""
    yield from (
        _data_access, _geometry, _aggregation, _density, _statistics,
        _point_pattern, _interpolation, _network, _terrain, _raster,
        _remote, _temporal, _decision,
    )
