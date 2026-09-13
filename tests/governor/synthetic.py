"""Synthetic workload + fake executor（R17/R19，ADR-0182 D12）。

校准脚本、chaos 测试与多 session 压测的**公共基座**：

- ``WorkloadShape``：small/medium/large/extreme × 六类域（vector/raster/
  acquisition/cartography/export/long_context）的 descriptor 形状 —— 零真实
  大数据，payload 全部合成；
- ``FakeExecutor``：走真实 governor 管线（admit_and_reserve → 模拟执行 →
  complete），执行时长按 ``simulated_time_scale`` 压缩（默认 0 —— 瞬时，
  chaos/压力只关心排队与账面语义，不烧真实 CPU）；
- ``run_mixed_corpus``：N session × M 混合任务的压测驱动器，产出 p50/p95
  queue delay、fairness、starvation、peak reservation 指标。
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Dict, List, Tuple

from app.services.governor.contract import (
    Dimension,
    DimValue,
    ExecutionPriority,
    ResourceClass,
    ResourceDemand,
    ResourceEstimate,
    ResourceUsage,
    Subsystem,
)

#: 规模词表（spec §22）
SCALES = ("small", "medium", "large", "extreme")

#: 域 → (Subsystem, ResourceClass) 的合成映射
_DOMAIN_SHAPE: Dict[str, Tuple[Subsystem, ResourceClass]] = {
    "vector": (Subsystem.VECTOR_COMPUTE, ResourceClass.MEDIUM),
    "raster": (Subsystem.RASTER_COMPUTE, ResourceClass.RASTER),
    "acquisition": (Subsystem.DATA_FABRIC, ResourceClass.MEDIUM),
    "cartography": (Subsystem.MAP_COMPILE, ResourceClass.HEAVY),
    "export": (Subsystem.EXPORT, ResourceClass.EXPORT),
    "long_context": (Subsystem.LLM_CONTEXT, ResourceClass.LLM),
}

#: 规模 → 估工倍率（memory bytes / wall seconds 的基准放大）
_SCALE_FACTOR: Dict[str, float] = {
    "small": 1.0,
    "medium": 8.0,
    "large": 64.0,
    "extreme": 512.0,
}

_BASE_MEMORY = 32 * 1024**2
_BASE_WALL = 1.0


def synthetic_estimate(domain: str, scale: str, *,
                       rng_seed: int = 0) -> ResourceEstimate:
    """确定性合成估算（同 (domain, scale, seed) 必同输出）。"""
    subsystem, rclass = _DOMAIN_SHAPE[domain]
    factor = _SCALE_FACTOR[scale]
    lo = _BASE_MEMORY * factor
    mem = DimValue.estimated(lo, lo * 1.5, lo * 3.0, confidence=0.6,
                             source="synthetic", reason=f"{domain}/{scale}")
    wall = DimValue.estimated(_BASE_WALL * factor, _BASE_WALL * factor * 1.5,
                              _BASE_WALL * factor * 3.0, confidence=0.6,
                              source="synthetic", reason=f"{domain}/{scale}")
    est = ResourceEstimate(subsystem=subsystem, resource_class=rclass,
                           dims={Dimension.MEMORY_BYTES: mem,
                                 Dimension.WALL_TIME_S: wall},
                           confidence=0.6, source="synthetic",
                           reason=f"{domain}/{scale}/seed={rng_seed}")
    return est


def synthetic_demand(session_id: str, domain: str, scale: str, *,
                     turn_id: str = "", goal_id: str = "",
                     priority: ExecutionPriority = ExecutionPriority.NORMAL,
                     seed: int = 0) -> ResourceDemand:
    return ResourceDemand(
        session_id=session_id, turn_id=turn_id, goal_id=goal_id,
        subsystem=_DOMAIN_SHAPE[domain][0], tool_name=f"synthetic_{domain}",
        estimate=synthetic_estimate(domain, scale, rng_seed=seed),
        priority=priority,
    )


class FakeExecutor:
    """走真实 governor 管线的合成执行器。

    ``time_scale``：估时 wall → 真实 sleep 的压缩系数（0 = 不 sleep，
    只保留排队/账面语义；0.01 = 1% 实时，供时序类 chaos 用）。
    """

    def __init__(self, governor, *, time_scale: float = 0.0,
                 failure_rate: float = 0.0, rng_seed: int = 42):
        self.governor = governor
        self.time_scale = time_scale
        self.failure_rate = failure_rate
        self._rng = random.Random(rng_seed)
        # 观测面（压测指标）
        self.completed = 0
        self.failed = 0
        self.rejected = 0
        self.queued_waits: List[float] = []
        self.peak_live_reservations = 0

    async def execute(self, demand: ResourceDemand, *,
                      max_wait_s: float = 5.0) -> str:
        """单次合成执行。返回 completed/rejected/failed。"""
        demand.max_wait_s = max_wait_s
        t0 = time.monotonic()
        decision, reservation, ticket = await self.governor.admit_and_reserve(demand)
        if not decision.allowed:
            self.rejected += 1
            self.queued_waits.append(0.0)
            return "rejected"
        self.queued_waits.append(time.monotonic() - t0)
        self.peak_live_reservations = max(
            self.peak_live_reservations,
            self.governor.snapshot().get("live_reservations", 0))
        try:
            wall = demand.estimate.adjudged(Dimension.WALL_TIME_S)
            sleep_s = wall * self.time_scale
            if sleep_s > 0:
                await asyncio.sleep(min(sleep_s, 5.0))
            if self._rng.random() < self.failure_rate:
                self.failed += 1
                await self.governor.complete(
                    reservation, ticket,
                    usage=ResourceUsage(
                        session_id=demand.session_id,
                        tool_name=demand.tool_name,
                        subsystem=demand.subsystem,
                        status="failed",
                        wall_time_s=sleep_s),
                    estimate=demand.estimate)
                return "failed"
            self.completed += 1
            await self.governor.complete(
                reservation, ticket,
                usage=ResourceUsage(
                    session_id=demand.session_id,
                    tool_name=demand.tool_name,
                    subsystem=demand.subsystem,
                    status="completed",
                    wall_time_s=sleep_s or wall * 0.01),
                estimate=demand.estimate)
            return "completed"
        except BaseException:
            # chaos 断言：执行方崩溃也必须归还（适配器同款纪律）
            try:
                await self.governor.complete(reservation, ticket)
            except Exception:  # noqa: BLE001
                pass
            raise

    def percentile_wait(self, p: float) -> float:
        if not self.queued_waits:
            return 0.0
        xs = sorted(self.queued_waits)
        # 下侧取整（p50 = 排序后第 50% 个，落在低半区）
        idx = min(len(xs) - 1, max(0, int(len(xs) * p - 1e-9)))
        return xs[idx]


async def run_mixed_corpus(governor, *, sessions: int, tasks_per_session: int,
                           time_scale: float = 0.0, max_wait_s: float = 5.0,
                           seed: int = 42) -> Dict:
    """N session × M 混合任务（域 × 规模轮转）。返回压测指标。"""
    executor = FakeExecutor(governor, time_scale=time_scale, rng_seed=seed)
    domains = list(_DOMAIN_SHAPE.keys())
    scales = list(SCALES)

    async def session_worker(sid: int) -> None:
        for i in range(tasks_per_session):
            domain = domains[(sid + i) % len(domains)]
            scale = scales[(sid * 2 + i) % len(scales)]
            demand = synthetic_demand(
                f"stress-sid-{sid}", domain, scale,
                turn_id=f"t-{sid}-{i}", seed=sid * 100 + i)
            await executor.execute(demand, max_wait_s=max_wait_s)

    t0 = time.monotonic()
    await asyncio.gather(*(session_worker(i) for i in range(sessions)))
    elapsed = time.monotonic() - t0

    total = sessions * tasks_per_session
    state = governor.snapshot()
    waits = executor.queued_waits
    return {
        "sessions": sessions,
        "tasks": total,
        "completed": executor.completed,
        "rejected": executor.rejected,
        "failed": executor.failed,
        "elapsed_s": round(elapsed, 3),
        "p50_wait_s": round(executor.percentile_wait(0.50), 4),
        "p95_wait_s": round(executor.percentile_wait(0.95), 4),
        "max_wait_s": round(max(waits) if waits else 0.0, 4),
        "peak_live_reservations": executor.peak_live_reservations,
        "live_after": state.get("live_reservations"),
        "channels_after": state.get("channels"),
    }


__all__ = [
    "SCALES",
    "FakeExecutor",
    "synthetic_estimate",
    "synthetic_demand",
    "run_mixed_corpus",
]
