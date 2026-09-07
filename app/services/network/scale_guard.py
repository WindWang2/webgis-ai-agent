"""网络族 n×m OD 代价矩阵统一规模闸（science-v3 审计 04 域 R3/F4）。

背景：location_allocation 启发式 / accessibility / gravity / huff /
closest_facility 的工具面会把**全部** 需求×设施 OD 代价对物化成 Python
list-of-lists / dict（每对 ≥ 50B）：50k 需求 × 10k 候选 ≈ GB 级 —— 此前
这些面无任何闸，请求会先 OOM 而不是被诚实拒绝（F4）；而精确 MILP 路径
（allocation._milp_scale_guard，25000/500）与 OD 工具（network_tools.
MAX_OD_MATRIX_PAIRS=10_000）各自有闸 —— 域内「规模闸先拒绝不 OOM」的
标准被启发式面绕过。

语义：
- 与求解路径无关（exact/heuristic 都要物化同一代价矩阵），属统一资源
  包络；超闸抛 ResourceScaleMismatch（typed，scientific_code=
  RESOURCE_SCALE_MISMATCH），correction_hint 指向拆批/子网 —— 绝不
  静默回退、绝不静默截断；
- 缺省闸值与工具面 MAX_OD_MATRIX_PAIRS 同刻度（10_000 对）；monkeypatch
  本模块常量即可收缩闸值（测试同款）；
- 精确 MILP 服务路径在物化代价矩阵**之前**先过本闸（先拒绝后分配），
  但**用 MILP 自身刻度**（``max_pairs=_MILP_MAX_PRODUCT``=25_000）——
  review R2-1：若 MILP 路径也用 10_000 缺省刻度，其文档承诺的 25_000
  包络在 10k–25k 区间会被本闸静默截断；模型级 500 候选闸仍在求解器
  入口兜底。
"""
from __future__ import annotations

from typing import Optional

from app.lib.gis.scientific_errors import ResourceScaleMismatch

# 与 app/tools/network_tools.py::MAX_OD_MATRIX_PAIRS 同刻度的服务层闸
# （工具层闸防 LLM 上下文膨胀；本闸防 n×m 代价矩阵物化 OOM —— 两层
# 各守一面，数值一致）。
MAX_OD_MATRIX_PAIRS = 10_000


def od_matrix_scale_guard(
    n_demand: int,
    n_facility: int,
    context: str = "",
    max_pairs: Optional[int] = None,
) -> None:
    """n×m OD 代价矩阵规模闸：在任何矩阵/OD 树物化之前拒绝。

    ``max_pairs``：闸值；None（缺省）= 运行时读取模块常量
    ``MAX_OD_MATRIX_PAIRS``（10_000，monkeypatch 收缩测试同款语义）；
    精确 MILP 服务路径传其自身模型刻度 25_000（review R2-1），见模块
    docstring。

    Raises:
        ResourceScaleMismatch: 需求×设施乘积超出闸值。
    """
    limit = MAX_OD_MATRIX_PAIRS if max_pairs is None else max_pairs
    product = n_demand * n_facility
    if product > limit:
        prefix = f"{context}的 " if context else ""
        raise ResourceScaleMismatch(
            f"{prefix}需求×设施 OD 代价矩阵规模 {n_demand}×{n_facility}={product} "
            f"超出上限 {limit}（先拒绝，不物化、不 OOM）",
            estimated=f"n_demand*n_facility={product}",
            limit=f"<={limit}",
            correction_hint=(
                "split the request into smaller batches (拆批需求/设施点) "
                "or subnet the road network (子网提取) and merge results — "
                "the full n×m OD matrix is never materialized beyond the guard"
            ),
        )
