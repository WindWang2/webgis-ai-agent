"""Reproducible sample selection —— 可复现选样与纳入概率/权重（WP2）。

从冻结设计（:class:`app.lib.survey_sampling.design.SurveyDesign`）按
seed 确定性生成样本序列：

- 每层独立 SRS **不放回**（``rng.permutation(N_h)`` 取前 n_h+backup
  个，顺序即候补次序——替补是**预抽序列的下一项**，属设计声明行为，
  不是无记录换样）；
- 纳入概率 π = n_h/N_h（层内恒等），权重 w = 1/π = N_h/n_h；
- ``sample_id``：uuid5(design_digest‖stratum‖unit_index)——跨端稳定、
  全局唯一、与坐标/批次无关（重复建样同 digest ⇒ 同 id ⇒ 幂等）。

选择机制假设：分析集 = 响应单元 + 预抽替补（同层同 π），非响应为
层内随机缺失（missing-at-random）——**不可验证，必须披露**（估计层
的 limitations 承担）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from app.lib.gis.scientific_errors import DegenerateData

#: uuid5 命名空间（本项目专属，避免与其它域 sample_id 冲突）
SAMPLE_ID_NAMESPACE = uuid.UUID("6f1d2c34-9a5e-4b71-8f3d-2a7c1e9b4d55")


@dataclass(frozen=True)
class SampleUnit:
    """单个抽样单元（抽样事实；参考标签另存）。"""

    sample_id: str
    design_digest: str
    stratum_id: str
    #: 层内总体单元索引（0..N_h-1；框冻结后稳定）
    unit_index: int
    #: 像元中心坐标（frame CRS）
    x: float
    y: float
    #: WGS84 经纬度（制图/核查导航用）
    lon: float
    lat: float
    inclusion_prob: float      # π = n_h/N_h
    weight: float              # w = 1/π
    #: 主样本序位（0..n_h-1）；替补从 n_h 起按序顶替
    draw_rank: int
    is_backup: bool

    def as_dict(self) -> Dict[str, object]:
        return {
            "sample_id": self.sample_id,
            "design_digest": self.design_digest,
            "stratum_id": self.stratum_id,
            "unit_index": self.unit_index,
            "x": self.x,
            "y": self.y,
            "lon": self.lon,
            "lat": self.lat,
            "inclusion_prob": self.inclusion_prob,
            "weight": self.weight,
            "draw_rank": self.draw_rank,
            "is_backup": self.is_backup,
        }


def make_sample_id(design_digest: str, stratum_id: str, unit_index: int) -> str:
    """稳定 sample_id：uuid5(digest‖层‖单元索引)。"""
    name = f"{design_digest}|{stratum_id}|{int(unit_index)}"
    return uuid.uuid5(SAMPLE_ID_NAMESPACE, name).hex


def draw_stratum_units(
    N_h: int, n_draw: int, seed: int,
) -> List[int]:
    """层内 SRS 不放回：rng.permutation(N_h, seed) 前 n_draw 个（确定性）。

    ``seed`` 应由设计 seed 派生（``derive_stratum_seed``），层间独立且
    与层序无关（避免同种子同排列的跨层相关）。
    """
    if n_draw > N_h:
        raise DegenerateData(
            f"draw {n_draw} exceeds stratum population {N_h}",
            correction_hint="reduce n_h/backup or split the frame",
        )
    rng = np.random.default_rng(seed)
    perm = rng.permutation(N_h)
    return [int(u) for u in perm[:n_draw]]


def derive_stratum_seed(design_seed: int, stratum_id: str) -> int:
    """设计 seed + 层名 → 层独立 seed（sha256 前 8 字节，确定性）。"""
    import hashlib

    h = hashlib.sha256(f"{design_seed}|{stratum_id}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "little")


def draw_samples(
    design,   # SurveyDesign（避免循环导入，类型在调用方保证）
    frame,    # SurveyFrame（frame.py；提供 unit 坐标查询）
) -> List[SampleUnit]:
    """按设计从冻结框生成全部样本（含预抽替补；顺序即候补序）。

    幂等：同 design digest + frame ⇒ 逐位同序列、同 sample_id。
    """
    units: List[SampleUnit] = []
    for spec in design.strata:
        n_h = design.allocation[spec.stratum_id]
        if n_h <= 0:
            continue
        n_draw = min(n_h + design.backup_per_stratum, spec.N_units)
        stratum_seed = derive_stratum_seed(design.seed, spec.stratum_id)
        picked = draw_stratum_units(spec.N_units, n_draw, stratum_seed)
        pi = n_h / spec.N_units
        w = 1.0 / pi
        for rank, unit_index in enumerate(picked):
            x, y, lon, lat = frame.unit_coordinates(spec.stratum_id, unit_index)
            units.append(SampleUnit(
                sample_id=make_sample_id(design.digest, spec.stratum_id,
                                         unit_index),
                design_digest=design.digest,
                stratum_id=spec.stratum_id,
                unit_index=unit_index,
                x=x, y=y, lon=lon, lat=lat,
                inclusion_prob=pi, weight=w,
                draw_rank=rank,
                is_backup=(rank >= n_h),
            ))
    return units
