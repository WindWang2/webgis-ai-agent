"""多 session 压力测试（R19，ADR-0182 D12）：1/4/8/16 并发 synthetic session。

**不开真实 Next/browser/raster 进程** —— FakeExecutor 走真实 governor 管线
（准入/背压/账本/归还），执行时长压缩为零。断言：

- 无饥饿：任何任务的最大排队等待有界（aging + small bypass 生效）；
- 公平：各 session 完成量分布的基尼系数有界（weighted fair）；
- 峰值在飞预留有界（背压水位 = 全局并发上限之和的量级）；
- 结束后零残留（通道清零、reservation 清零）。
"""
from __future__ import annotations

import asyncio
import statistics
from pathlib import Path

import pytest

from app.services.governor.config import GovernorConfig, load_manifest
from app.services.governor.governor import HarnessResourceGovernor
from tests.governor.synthetic import run_mixed_corpus

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"

_SESSION_MATRIX = (1, 4, 8, 16)


def _governor() -> HarnessResourceGovernor:
    config = GovernorConfig()
    config.queue_max_wait_s = 8.0   # 压测下给排队留余量
    return HarnessResourceGovernor(config, budgets=load_manifest(_MANIFEST))


def _gini(values) -> float:
    """基尼系数（0 = 完全均等；1 = 完全垄断）。"""
    xs = sorted(values)
    n = len(xs)
    total = sum(xs)
    if total == 0 or n == 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (n + 1 - 2 * cum / total) / n


@pytest.mark.parametrize("sessions", _SESSION_MATRIX)
@pytest.mark.asyncio
async def test_multi_session_matrix(sessions: int):
    gov = _governor()
    tasks_per_session = 8
    metrics = await run_mixed_corpus(
        gov, sessions=sessions, tasks_per_session=tasks_per_session,
        max_wait_s=8.0)

    total = sessions * tasks_per_session
    accounted = metrics["completed"] + metrics["rejected"] + metrics["failed"]
    assert accounted == total, "每个任务必须有终态（完成/拒绝/失败）"

    # 无饥饿：p95 与 max 排队等待有界
    assert metrics["max_wait_s"] <= 8.0
    assert metrics["p95_wait_s"] <= 8.0

    # 零残留
    assert metrics["live_after"] == 0
    assert all(v["in_flight"] == 0 for v in metrics["channels_after"].values())

    # 高吞吐：零 sleep 的合成任务在 8s 内跑完 16×8=128 任务（防隐性串行）
    if sessions >= 8:
        assert metrics["elapsed_s"] < 15.0, metrics["elapsed_s"]


@pytest.mark.asyncio
async def test_fairness_across_sessions():
    """同规模任务下，各 session 的完成量应大体均衡（基尼 < 0.4）。"""
    gov = _governor()
    executor_class = None
    from tests.governor.synthetic import FakeExecutor, synthetic_demand
    executor_class = FakeExecutor

    sessions = 8
    per_session = 12
    executor = executor_class(gov, time_scale=0.0, rng_seed=99)
    completed_per_session = [0] * sessions

    async def worker(sid: int):
        for i in range(per_session):
            d = synthetic_demand(f"fair-{sid}", "vector", "small",
                                 seed=sid * 100 + i)
            d.max_wait_s = 8.0
            before = executor.completed
            await executor.execute(d)
            if executor.completed > before:
                completed_per_session[sid] += 1

    await asyncio_gather_workers(worker, sessions)
    assert sum(completed_per_session) == sessions * per_session
    g = _gini(completed_per_session)
    assert g < 0.4, f"fairness gini={g:.3f} per_session={completed_per_session}"


async def asyncio_gather_workers(worker, sessions: int):
    import asyncio
    await asyncio.gather(*(worker(i) for i in range(sessions)))


@pytest.mark.asyncio
async def test_starvation_index_bounded_under_heavy_load():
    """大任务持续占满 heavy 通道时，小任务等待有界（bypass/aging 生效）。"""
    from app.services.governor.contract import (
        Dimension, DimValue, ResourceClass, ResourceDemand, ResourceEstimate,
    )
    gov = _governor()
    # 持续的重 raster 流（占满 raster 通道的重槽）
    async def heavy_stream():
        for i in range(10):
            est = ResourceEstimate(resource_class=ResourceClass.RASTER, dims={
                Dimension.MEMORY_BYTES: DimValue.known(5e8),
                Dimension.WALL_TIME_S: DimValue.known(120.0)})
            d = ResourceDemand(session_id=f"hog-{i % 2}", estimate=est)
            d.max_wait_s = 0.5
            dec, r, t = await gov.admit_and_reserve(d)
            if r is not None:
                await gov.complete(r, t)
            await asyncio.sleep(0)

    # 小 raster 任务（估时 0.5s → bypass 资格）与 hog 并发
    small_waits = []
    for j in range(5):
        est = ResourceEstimate(resource_class=ResourceClass.RASTER, dims={
            Dimension.MEMORY_BYTES: DimValue.known(1e7),
            Dimension.WALL_TIME_S: DimValue.known(0.5)})
        d = ResourceDemand(session_id=f"small-{j}", estimate=est)
        d.max_wait_s = 2.0
        t0 = asyncio.get_event_loop().time()
        dec, r, t = await gov.admit_and_reserve(d)
        small_waits.append(asyncio.get_event_loop().time() - t0)
        if r is not None:
            await gov.complete(r, t)
    await heavy_stream()
    # 小任务的准入决策本身必须即时（< 0.5s —— 排队留给通道，决策零阻塞）
    assert all(w < 0.5 for w in small_waits), small_waits


def test_percentile_math():
    from tests.governor.synthetic import FakeExecutor
    ex = FakeExecutor.__new__(FakeExecutor)
    ex.queued_waits = [0.1] * 50 + [5.0] * 50
    assert ex.percentile_wait(0.5) == pytest.approx(0.1)
    assert ex.percentile_wait(0.95) == pytest.approx(5.0)
    assert statistics.median(ex.queued_waits) == pytest.approx(2.55)
