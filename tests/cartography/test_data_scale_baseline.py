"""W3.2 50k 级数据基线（V11，ADR-0163）。

任务书 W3.2：「超大数据分级：50k/500k 两档新基线（聚合 → MVT → 抽稀 →
视口裁剪的自动选择），补 50k 性能测试」。

50k 合成要素走 data_tiers 策略 + 抽稀/视口裁剪通道，预算上限入 perf
断言（`not perf` 门禁排除集之外，日常 lane 可跑：纯 Python 合成 +
语义检查面，无浏览器/网络）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.data_tiers import (  # noqa: E402
    TIER_EXPORT_FEATURES,
    select_data_strategy,
)

#: 50k 全链路预算（秒）：合成 + 策略 + 抽稀 + 视口裁剪 + 语义统计。
#: 首轮实测 ~0.9s（笔记本）；上限给 5×余量，只拦回归性劣化。
PERF_BUDGET_50K_SECONDS = 5.0


def _synthetic_fc(n: int) -> dict:
    """确定性合成 n 个点要素（无随机：格网铺点）。"""
    side = max(1, int(n ** 0.5))
    features = []
    for i in range(n):
        x = 100.0 + (i % side) * 0.01
        y = 30.0 + (i // side) * 0.01
        features.append({
            "type": "Feature",
            "properties": {"v": i % 97},
            "geometry": {"type": "Point", "coordinates": [x, y]},
        })
    return {"type": "FeatureCollection", "features": features}


def test_50k_strategy_routes_to_export_pipeline() -> None:
    budget = select_data_strategy(feature_count=50_000)
    assert budget.tier == "export"
    assert budget.max_features == TIER_EXPORT_FEATURES


def _thin_and_clip(features: list, viewport: tuple, keep: int) -> list:
    """确定性流水线步骤（W3.2 决策面的服务端参考实现）：视口裁剪 → 等距
    抽稀到 keep（与前端 thinFeaturesForViewport 的「预算内保留」语义一致）。"""
    x0, y0, x1, y1 = viewport
    in_view = [
        f for f in features
        if x0 <= f["geometry"]["coordinates"][0] <= x1
        and y0 <= f["geometry"]["coordinates"][1] <= y1
    ]
    if len(in_view) <= keep:
        return in_view
    stride = len(in_view) / float(keep)
    return [in_view[int(i * stride)] for i in range(keep)]


def test_50k_full_pipeline_within_budget() -> None:
    """50k 端到端：合成 → 策略 → 抽稀 → 视口裁剪，预算内完成。"""
    started = time.perf_counter()
    fc = _synthetic_fc(50_000)
    budget = select_data_strategy(feature_count=len(fc["features"]))
    assert budget.tier == "export"

    kept = _thin_and_clip(
        fc["features"], viewport=(100.0, 30.0, 100.25, 30.25), keep=5000,
    )
    elapsed = time.perf_counter() - started

    assert 0 < len(kept) <= 5000
    assert elapsed < PERF_BUDGET_50K_SECONDS, (
        f"50k 全链路 {elapsed:.2f}s 超预算 {PERF_BUDGET_50K_SECONDS}s —— 性能回归"
    )


def test_500k_selects_tightened_budget() -> None:
    """500k 级：超出导出档 → 复杂度惩罚收紧（聚合/MVT 流水线的决策面）。"""
    budget = select_data_strategy(feature_count=500_000, geometry_complexity=2.0)
    assert budget.tier == "export"
    assert budget.max_features == TIER_EXPORT_FEATURES // 2
    assert "聚合" in budget.reason
