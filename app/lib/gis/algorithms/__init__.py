"""算法域包聚合（ADR-0099 §34）。

中央 AlgorithmRegistry 经 DOMAIN_PACKS 聚合各域模块 —— 新域 = 新模块
+ 在此登记，algorithm_registry.py 不再单文件膨胀。decision.py 为
workbench 分支（feat/agentic-gis-workbench-vnext）的 MCDA 条目预留合并位。
"""
from __future__ import annotations

from typing import Iterable, List

from app.lib.gis.algorithm_registry import AlgorithmDescriptor
from app.lib.gis.algorithms import aggregation as _aggregation_mod
from app.lib.gis.algorithms import data_access as _data_access_mod
from app.lib.gis.algorithms import decision as _decision_mod
from app.lib.gis.algorithms import density as _density_mod
from app.lib.gis.algorithms import geometry as _geometry_mod
from app.lib.gis.algorithms import interpolation as _interpolation_mod
from app.lib.gis.algorithms import network as _network_mod
from app.lib.gis.algorithms import point_pattern as _point_pattern_mod
from app.lib.gis.algorithms import raster as _raster_mod
from app.lib.gis.algorithms import remote_sensing as _remote_mod
from app.lib.gis.algorithms import statistics as _statistics_mod
from app.lib.gis.algorithms import temporal as _temporal_mod
from app.lib.gis.algorithms import terrain as _terrain_mod

_ALL_MODULES = (
    _data_access_mod, _geometry_mod, _aggregation_mod, _density_mod,
    _statistics_mod, _point_pattern_mod, _interpolation_mod, _network_mod,
    _terrain_mod, _raster_mod, _remote_mod, _temporal_mod, _decision_mod,
)


def iter_domain_packs() -> Iterable[List[AlgorithmDescriptor]]:
    """确定性域序聚合（顺序 = 注册序；同 id 冲突仍由 register() 拒绝）。"""
    for module in _ALL_MODULES:
        yield module.ALGORITHMS


def iter_contract_packs() -> Iterable[list]:
    """各域模块导出的 PARAMETER_CONTRACTS（缺省空表）。

    契约注册表聚合域契约 —— 域模块拥有自己的参数契约，中央
    parameter_contracts.py 不再被多方追加。注意属性在域**模块**上，
    不在 ALGORITHMS 列表上（此前的实现读列表属性恒为 None）。
    """
    for module in _ALL_MODULES:
        contracts = getattr(module, "PARAMETER_CONTRACTS", None)
        if contracts:
            yield contracts
