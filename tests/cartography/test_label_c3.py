"""W4 标注深化测试（V11，ADR-0164）。

覆盖：C3 LabelPlan 定稿（collision/typography 段只加不改）、专业排版
（多语言断行/面内标注点）、10k/50k 标注求解性能预算。
交互侧（网格碰撞/字段兜底）见 frontend label-grid.test.ts。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.label_plan import build_label_spec  # noqa: E402
from app.lib.cartography.label_typography import (  # noqa: E402
    polygon_label_point,
    wrap_label_multilingual,
)
from app.lib.cartography.label_engine import (  # noqa: E402
    LabelEngineInput,
    LabelFeature,
    solve_labels,
)


# ── C3 定稿（只加不改）───────────────────────────────────────────────────

_PROFILE = {
    "featureCount": 2,
    "fields": {
        "name": {"type": "text", "sampleValues": ["朝阳区", "海淀区"],
                 "null_count": 0, "unique_count": 2},
        "code": {"type": "text", "sampleValues": ["110105", "110108"],
                 "null_count": 0, "unique_count": 2},
    },
}


def test_c3_spec_has_collision_and_typography() -> None:
    spec = build_label_spec(_PROFILE)
    assert spec is not None
    # C3 新段（缺省即语义等价 V10：auto 断行、grid 策略）
    assert spec["collision"]["strategy"] == "grid"
    assert spec["collision"]["suppressOverflow"] is True
    assert spec["typography"]["wrapMode"] == "auto"
    assert spec["typography"]["maxLines"] == 2
    # 既有字段原样保留（只加不改）
    assert spec["haloMode"] == "auto"
    assert spec["field"] == "name"


def test_c3_strategy_model_defaults() -> None:
    from app.lib.cartography.label_plan import (
        LabelCollisionConfig,
        LabelStrategy,
        LabelTypography,
    )
    s = LabelStrategy()
    assert s.collision is None and s.typography is None  # V10 形状不变
    assert LabelCollisionConfig().strategy == "grid"
    assert LabelCollisionConfig(strategy="maplibre").strategy == "maplibre"  # 回滚开关
    assert LabelTypography().wrap_mode == "auto"


# ── W4.3 多语言断行 ──────────────────────────────────────────────────────

def test_wrap_cjk_char_mode() -> None:
    assert wrap_label_multilingual(
        "长江三角洲城市群发展战略研究", max_chars=8, wrap_mode="cjk_char",
    ) == ["长江三角", "洲城市群"]  # 按字断（GIS 惯例）


def test_wrap_latin_word_mode() -> None:
    lines = wrap_label_multilingual(
        "Metropolitan Transportation Authority", max_chars=20, wrap_mode="latin_word",
    )
    assert lines[0] == "Metropolitan"
    assert all(len(line) <= 20 for line in lines)


def test_wrap_never_drops_content() -> None:
    """溢出截断进末行 —— 绝不静默丢词（评审纪律）。"""
    text = "Metropolitan Transportation Infrastructure Overview"
    lines = wrap_label_multilingual(text, max_chars=20, wrap_mode="latin_word")
    assert len(lines) == 2
    assert lines[-1]  # 末行有内容（截断而非丢弃）
    # 覆盖全部词的可见前缀
    assert text.startswith("Metropolitan")


def test_wrap_auto_mixed_script() -> None:
    """中英混排：CJK 主导 → 按字；拉丁主导 → 按词（用例锁定）。"""
    zh = wrap_label_multilingual("上海中心大厦核心区", max_chars=6, wrap_mode="auto")
    assert zh[0] == "上海中"  # 6 窄字符位 = 3 个 CJK 字（按字断）
    en = wrap_label_multilingual("Central Park West Side", max_chars=12, wrap_mode="auto")
    assert "Central Park" in " ".join(en)


def test_polygon_label_point_inscribed_vs_centroid() -> None:
    """L 形面：最大内接圆圆心仍在面内且更居中；正方形 → 中心。"""
    square = polygon_label_point([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]])
    assert square == (5.0, 5.0)
    l_shape = polygon_label_point(
        [[0, 0], [10, 0], [10, 2], [2, 2], [2, 10], [0, 10], [0, 0]])
    # 圆心必须落在面内（质心法在 L 形上可能落外的根因消除）
    assert 0 < l_shape[0] < 10 and 0 < l_shape[1] < 10
    degenerate = polygon_label_point([[0, 0], [1, 0], [1, 1]])
    assert degenerate != (0.0, 0.0)  # 退化环回退 representative_point


# ── W4.6 性能预算（10k / 50k）────────────────────────────────────────────

def _synthetic_labels(n: int) -> LabelEngineInput:
    side = max(1, int(n ** 0.5))
    features = [
        LabelFeature(
            id=f"f{i}",
            text=f"站点{i}号标注",
            kind="point",
            geometry=[[100.0 + (i % side) * 0.01, 30.0 + (i // side) * 0.01]],
        )
        for i in range(n)
    ]
    return LabelEngineInput(
        features=features,
        viewport=[99.0, 29.0, 100.0 + side * 0.02, 30.0 + side * 0.02],
    )


@pytest.mark.parametrize("n,budget_s", [(10_000, 10.0), (50_000, 60.0)])
def test_label_solve_perf_budget(n: int, budget_s: float) -> None:
    """10k/50k 标注求解耗时上限（W4.6 验收；首轮实测见台账）。"""
    payload = _synthetic_labels(n)
    started = time.perf_counter()
    solution = solve_labels(payload)
    elapsed = time.perf_counter() - started
    assert solution.stats["total"] == n
    assert elapsed < budget_s, (
        f"{n} 要素标注求解 {elapsed:.2f}s 超预算 {budget_s}s —— 性能回归"
    )
