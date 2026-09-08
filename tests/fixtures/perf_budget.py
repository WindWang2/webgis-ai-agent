"""性能断言稳定化助手（W11）—— 散落固定墙钟断言的最小迁移面。

仓库性能闸的两种健康模式：
1. ``tests/benchmarks/test_perf_harness.py``：median-of-7 +
   ``max(floor_ms, baseline_ms * 4.0)``（perf 标记车道专用，基线持久化在
   ``tests/benchmarks/baselines.json``）；
2. ``tests/quality/test_structural_perf_gates.py``：结构量闸（字节数/计数
   单调，零计时）。

harness 之外散落的固定墙钟断言（单次采样 + 绝对秒数上限）对 CI 负载敏感，
历史反复抖红（f2124e68 把 cached retrieval 0.05→0.2s；d2232d5f 人工抬结构
基线 53000）。本助手把它们收敛成与 harness 同款的判定语义：

    median-of-N 秒数 <= max(floor_s, baseline.get(name, 0.0) * factor)

BASELINE_HINTS —— 与 ``tests/benchmarks/baselines.json`` 的关系：
- **不读取、不合并、不互相写入**。baselines.json 是 perf 车道 harness 的
  持久基线（PERF_UPDATE_BASELINES=1 再生成）；本助手的 ``baseline`` 字典
  由调用方在测试文件内注入（通常是顶部的小字面量），只服务本文件断言，
  没有跨文件真相，因此不需要持久化。
- 迁移优先序（W11 纪律）：能用 **ratio/结构断言** 表达原意图的（缓存命中
  vs 全量扫描、并发 vs 串行）优先用比值断言 —— 它们对机器绝对速度不敏感；
  helper 的 median+floor 只兜绝对上界，两者可叠加。
- 不放宽语义：迁移后的断言必须仍能捕获原 bug（见各迁移点注释）。

用法：
    from tests.fixtures.perf_budget import (
        assert_within_budget, aassert_within_budget, measure_median,
    )

    # 同步被测物
    med = assert_within_budget(
        "surface.augment.200", lambda: proj.project(req),
        iterations=5, floor_s=0.15)

    # 异步被测物（pytest-asyncio 用例内 await）
    await aassert_within_budget(
        "ws.broadcast.concurrent_round", round_fn, iterations=3, floor_s=0.25)

    # 只测中位数（配比值断言用）
    med = measure_median(fn, iterations=3)
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Awaitable, Callable, Mapping, Optional

__all__ = [
    "measure_median",
    "ameasure_median",
    "assert_within_budget",
    "aassert_within_budget",
    "budget_limit",
]

#: harness 的失败因子（tests/benchmarks/test_perf_harness.py FAIL_FACTOR 同款）。
#: floor 兜「机器快到 baseline 缺失/极小」的场景；factor 兜「baseline 注入
#: 但机器整体变慢」的场景。默认 4.0 与 harness 一致，调用方可覆盖。
DEFAULT_FAIL_FACTOR = 4.0


def budget_limit(
    name: str,
    floor_s: float,
    factor: float = DEFAULT_FAIL_FACTOR,
    baseline: Optional[Mapping[str, float]] = None,
) -> float:
    """单次判定的预算上界：``max(floor_s, baseline.get(name, 0) * factor)``。"""
    base = float((baseline or {}).get(name, 0.0) or 0.0)
    return max(float(floor_s), base * float(factor))


def measure_median(fn: Callable[[], Any], iterations: int = 5) -> float:
    """同步执行 ``fn`` 共 ``iterations`` 次，返回耗时中位数（秒）。

    ``fn`` 通常是被测热路径的零参闭包；有返回值需要断言时用闭包把结果
    捕获到外层 holder（见各迁移点）。median 对调度噪声/CI 邻居负载鲁棒
    （harness 同款 median-of-N 语义）。
    """
    samples: list[float] = []
    for _ in range(max(1, int(iterations))):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples)


async def ameasure_median(
    fn: Callable[[], Awaitable[Any]], iterations: int = 5
) -> float:
    """:func:`measure_median` 的异步版（``await fn()`` 计入计时）。"""
    samples: list[float] = []
    for _ in range(max(1, int(iterations))):
        t0 = time.perf_counter()
        await fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples)


def assert_within_budget(
    name: str,
    fn: Callable[[], Any],
    *,
    iterations: int = 5,
    floor_s: float,
    factor: float = DEFAULT_FAIL_FACTOR,
    baseline: Optional[Mapping[str, float]] = None,
) -> float:
    """median-of-N 耗时 ≤ ``max(floor_s, baseline[name] * factor)``，返回中位数。

    ``baseline`` 未注入（缺省 None）时退化为纯 floor —— 与被迁移的固定
    墙钟断言同强度，只是把单次采样换成 median 抗噪；注入 baseline 后
    获得 harness 同款的「基线 × factor」上界。
    """
    samples: list[float] = []
    for _ in range(max(1, int(iterations))):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    med = statistics.median(samples)
    limit = budget_limit(name, floor_s, factor, baseline)
    assert med <= limit, (
        f"[perf_budget] {name}: median {med:.4f}s > limit {limit:.4f}s "
        f"(floor_s={floor_s}, factor={factor}, "
        f"samples={[round(s, 4) for s in samples]})"
    )
    return med


async def aassert_within_budget(
    name: str,
    fn: Callable[[], Awaitable[Any]],
    *,
    iterations: int = 5,
    floor_s: float,
    factor: float = DEFAULT_FAIL_FACTOR,
    baseline: Optional[Mapping[str, float]] = None,
) -> float:
    """:func:`assert_within_budget` 的异步版（在被测事件循环内 await）。"""
    samples: list[float] = []
    for _ in range(max(1, int(iterations))):
        t0 = time.perf_counter()
        await fn()
        samples.append(time.perf_counter() - t0)
    med = statistics.median(samples)
    limit = budget_limit(name, floor_s, factor, baseline)
    assert med <= limit, (
        f"[perf_budget] {name}: median {med:.4f}s > limit {limit:.4f}s "
        f"(floor_s={floor_s}, factor={factor}, "
        f"samples={[round(s, 4) for s in samples]})"
    )
    return med
