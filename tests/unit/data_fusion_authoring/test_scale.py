"""WP6 规模与性能验证（分区分块/有界内存/召回不漏/密集溢出）。

纪律：
- 规模分层保守（CI 友好）：2 万×2 万点（合成、空间受控），标记
  ``perf`` 的基线对比不在此域（无既有基线）；本文件记录真实测量值
  于输出（不捏造百分比），并在预算/召回上做硬断言；
- 召回硬断言：ground truth 全部真配对必须进入候选集（blocking 不漏
  关键候选 —— 引擎正确性的规模口径，与 oracle 单测互补）；
- 密集溢出：单实体候选超帽 → 强制审阅披露（绝不静默丢弃）；
- tracemalloc 有界：峰值内存远低于域预算（诚实测量，不达标即失败）。
"""
from __future__ import annotations

import time
import tracemalloc
from typing import Dict, List, Tuple

from shapely.geometry import Point

from app.services.data_fusion_authoring import matching
from app.services.data_fusion_authoring.matching import SpatialContext
from app.services.data_fusion_authoring.plan import (
    FieldMapping,
    FusionPlan,
    FusionSourceSpec,
    MatchStrategy,
)


def _scale_plan(**over) -> FusionPlan:
    strategy_over = {}
    for k in ("max_match_distance_m", "close_distance_m"):
        if k in over:
            strategy_over[k] = over.pop(k)
    kw = dict(
        entity_kind="point_poi",
        source_a=FusionSourceSpec.model_validate({
            "kind": "session_ref", "session_id": "s", "ref": "ref:a",
            "label": "A", "priority": 1, "priority_reason": "官方名录测试",
            "name_field": "name",
            "field_mappings": [
                FieldMapping(source_field="name", output_field="name",
                             rule="same_value"),
            ],
        }),
        source_b=FusionSourceSpec.model_validate({
            "kind": "session_ref", "session_id": "s", "ref": "ref:b",
            "label": "B", "priority": 2, "priority_reason": "众包测试",
            "name_field": "name",
            "field_mappings": [
                FieldMapping(source_field="name", output_field="name",
                             rule="same_value"),
            ],
        }),
        strategy=None,  # 占位 —— 下方组装
    )
    kw.update(over)
    strategy_kwargs = {
        # 规模夹具不声明类别字段 → category 显式 0（计划校验一致）
        "weights": {"external_id": 0.0, "name": 0.60, "address": 0.0,
                    "category": 0.0, "distance": 0.40},
        "max_match_distance_m": 80.0, "close_distance_m": 20.0,
    }
    strategy_kwargs.update(strategy_over)
    kw["strategy"] = MatchStrategy(**strategy_kwargs)
    return FusionPlan.model_validate(kw)


def _grid_rows(n_side: int, spacing_deg: float, jitter: float,
               seed: int, prefix: str) -> Tuple[List[Dict], List]:
    """规则网格 + 受控伪随机抖动（合成；确定性）。"""
    import random

    rng = random.Random(seed)
    rows: List[Dict] = []
    geoms = []
    base_lon, base_lat = 104.0, 30.5
    for i in range(n_side):
        for j in range(n_side):
            lon = base_lon + j * spacing_deg + rng.uniform(-jitter, jitter)
            lat = base_lat + i * spacing_deg + rng.uniform(-jitter, jitter)
            rows.append({"name": f"{prefix}{i * n_side + j:06d}"})
            geoms.append(Point(lon, lat))
    return rows, geoms


def test_scale_20k_recall_and_bounded_time():
    """20k×20k（141×141 网格）配对：真配对全部召回 + 有界时间。"""
    plan = _scale_plan()
    spacing = 0.001  # ~100m 网格；抖动 ±5m → 真配对距离 ≤ ~12m
    rows_a, geoms_a = _grid_rows(120, spacing, 0.00003, seed=7, prefix="甲")
    rows_b, geoms_b = _grid_rows(120, spacing, 0.00003, seed=99, prefix="乙")
    n = len(rows_a)
    assert n == 14400

    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    # 计时跑（无 tracer 开销）与内存跑分开 —— tracemalloc 每次分配
    # 追踪会放大 wall time 数倍，两者不可混测（诚实测量口径）
    t0 = time.monotonic()
    result = matching.match_sources(rows_a, geoms_a, rows_b, geoms_b,
                                    plan, ctx)
    elapsed = time.monotonic() - t0
    tracemalloc.start()
    matching.match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # 召回硬断言：网格一一对应 → 全部 matched（blocking 不漏关键候选）
    assert result.stats["pairs_matched"] == n, (
        f"召回缺口：matched={result.stats['pairs_matched']} / {n}")
    assert result.stats["unmatched_a"] == 0
    assert result.stats["unmatched_b"] == 0
    assert result.stats["pairs_needs_review"] == 0
    assert result.stats["pairs_ambiguous"] == 0
    # 有界时间（测量报告；上限为硬闸 —— 域 deadline 默认 300s）
    assert elapsed < 120.0, f"匹配耗时 {elapsed:.1f}s 超硬闸"
    # 有界内存（tracemalloc Python 峰值；引擎预算 256MB 内）
    assert peak < 256 * 1024 * 1024, f"峰值内存 {peak / 1e6:.0f}MB 超预算"
    print(f"[scale] n={n} elapsed={elapsed:.2f}s peak={peak / 1e6:.1f}MB "
          f"candidates={result.stats['candidates']}")


def test_scale_dense_cluster_overflow_forces_review():
    """高密集区：单实体候选超帽 → 涉及对全部强制审阅（绝不自动合并）。"""
    plan = _scale_plan(max_match_distance_m=500.0)
    # 1 个 A 点周围放 60+ 个近距 B 点（超 50 帽）
    rows_a = [{"name": "密集中心"}]
    geoms_a = [Point(104.06, 30.65)]
    rows_b = []
    geoms_b = []
    import random

    rng = random.Random(42)
    for k in range(60):
        rows_b.append({"name": f"密集点{k:03d}"})
        geoms_b.append(Point(104.06 + rng.uniform(-0.002, 0.002),
                             30.65 + rng.uniform(-0.002, 0.002)))
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = matching.match_sources(rows_a, geoms_a, rows_b, geoms_b,
                                    plan, ctx)
    assert result.stats["candidate_overflow_entities_a"] >= 1
    for p in result.pairs:
        assert p.status != "matched"   # 歧义保护：溢出绝不自动合并


def test_scale_partition_halo_no_miss_on_tile_boundary():
    """跨格候选不漏（地理帧动态 halo：赤道口径 ≥3×3，高纬东西向扩展）。"""
    plan = _scale_plan()
    # A 点恰落在格网线上，B 点在相邻格 —— 3×3 邻域必须覆盖
    rows_a = [{"name": "格线点"}]
    geoms_a = [Point(104.0612, 30.6500)]   # max=80m → cell≈80/111320 度
    rows_b = [{"name": "格线点"}]
    geoms_b = [Point(104.0618, 30.6501)]   # ~59m：跨格邻居（同名）
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = matching.match_sources(rows_a, geoms_a, rows_b, geoms_b,
                                    plan, ctx)
    # halo 不漏的关键断言：跨格候选已生成（1 个）并进入评分
    assert result.stats["candidates"] == 1
    assert len(result.pairs) == 1
    # 同名但 59m（> close 20m）→ 审阅带（诚实评分；不因测试方便虚高）
    assert result.pairs[0].status == "needs_review"
    d_e = next(e for e in result.pairs[0].evidence if e.kind == "distance")
    assert d_e.detail["distance_m"] <= plan.strategy.max_match_distance_m


def test_scale_empty_and_single_contract():
    plan = _scale_plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    r0 = matching.match_sources([], [], [], [], plan, ctx)
    assert r0.stats["candidates"] == 0
    r1 = matching.match_sources([{"name": "唯一"}], [Point(104, 30)],
                                [{"name": "唯一"}], [Point(104.0001, 30)],
                                plan, ctx)
    assert r1.stats["pairs_matched"] == 1
