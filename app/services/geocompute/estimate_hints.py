"""Plan-node 数值资源 hint 投影（H06 DoD2 上游：compiler 侧写真实数值）。

agent_swarm/plan compiler 构建节点时只有 ``rows`` 真值（volume 估算），
``memory_mb``/``cpu_seconds`` 此前留空 —— plan 估算止步于 schema（D2 的
worker 侧投影因此常常无线可投）。本模块把 **已注册 descriptor 的声明式
``resource_envelope``**（ADR-0117：estimate-before-allocate 的机器可读
事实源）与 **governor 单一先验表**（``class_prior``，ADR-0213 D1 —— 不
新建第二份表）合成为 plan-node 数值 hint：

- ``memory_mb = rows × bytes_per_feature``（descriptor 声明 × 行真值，
  source 证据链：declared:resource_envelope）；
- ``cpu_seconds``：descriptor ``cpu_cost`` 档位 → ``class_prior`` wall
  期望值（wall 期望作 CPU 预算代理 —— 诚实近似，confidence 相应降档）；
- 未注册操作：回退旧行为（rows-only，confidence 沿 volume 方法）。

身份映射（operation → descriptor id）是**标识**不是成本先验；数值全部
来自 descriptor 声明与既有先验表。本模块纯函数、无 IO、fail-open（注册
表缺席/描述缺失 → 旧行为）。
"""
from __future__ import annotations

import logging
import math
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024

#: agent_swarm/geocompute 操作 → 已注册 descriptor id（标识映射，非先验）。
#: 只映射**身份确凿**的操作；泛化操作（aggregate/filter/query）不猜。
_OPERATION_DESCRIPTOR_IDS: Dict[str, str] = {
    "buffer_analysis": "geometry.buffer",
    "multi_ring_buffer": "geometry.multi_ring_buffer",
    "overlay_analysis": "geometry.overlay",
    "clip": "geometry.clip",
    "dissolve": "geometry.dissolve",
    "convex_hull": "geometry.convex_hull",
    "spatial_join": "geometry.spatial_join",
    "zonal_stats": "remote.zonal_stats",
    "h3_binning": "spatial.grid.h3",
}

#: descriptor cpu_cost（CostLevel）→ class_prior 档位
_COST_TO_CLASS = {"low": "light", "medium": "medium", "high": "heavy"}

__all__ = ["plan_estimate_for_operation", "OPERATION_DESCRIPTOR_IDS"]

#: 供测试/审计只读
OPERATION_DESCRIPTOR_IDS = dict(_OPERATION_DESCRIPTOR_IDS)


def plan_estimate_for_operation(
    operation: str,
    *,
    rows: Optional[int],
    method: str = "unknown",
):
    """operation + rows 真值 → plan-node ``ResourceEstimate``（H06）。

    无注册 descriptor / 无 envelope 声明时逐字段回退旧行为
    （``ResourceEstimate(rows=rows, confidence=...)``）。
    """
    from app.services.geocompute.plan import ResourceEstimate

    confidence = "medium" if method == "cost_hint" else "assumption"
    if rows is not None:
        try:
            from app.lib.gis.algorithm_registry import get_algorithm_registry

            algo_id = _OPERATION_DESCRIPTOR_IDS.get(str(operation))
            descriptor = (
                get_algorithm_registry().get(algo_id) if algo_id else None
            )
            envelope = getattr(descriptor, "resource_envelope", None)
            bpf = getattr(envelope, "bytes_per_feature", None)
            if descriptor is not None and bpf:
                est_bytes = float(rows) * float(bpf)
                memory_mb = math.ceil(est_bytes / _MIB)
                cpu_seconds: Optional[float] = None
                try:
                    from app.services.governor.estimation import class_prior

                    level = _COST_TO_CLASS.get(
                        str(getattr(descriptor, "cpu_cost", "")).lower())
                    if level:
                        # wall 期望作 CPU 预算代理（单一先验表；诚实近似）
                        cpu_seconds = float(class_prior(level)[4])
                except Exception:  # noqa: BLE001 — 先验缺席不阻断规划面
                    logger.debug("class_prior unavailable for %s", algo_id)
                return ResourceEstimate(
                    rows=int(rows),
                    bytes=int(est_bytes),
                    memory_mb=float(memory_mb),
                    cpu_seconds=cpu_seconds,
                    confidence="medium",
                )
        except Exception:  # noqa: BLE001 — 注册面缺席 → 旧行为（fail-open）
            logger.debug("estimate hint projection fell back for %s",
                         operation, exc_info=True)
    return ResourceEstimate(rows=rows, confidence=confidence)
