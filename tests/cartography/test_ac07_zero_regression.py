"""AC-07（ADR-0156 P8）：20 MapSpec × 4 版式三类版面检查归零回归（全量口径）。

P0 基线（docs/dev/ac-07-baseline.json，由 tests/cartography/ac07_corpus_harness.py
复现）：compose 直出（未经 solver）的 20 案例 × 4 版式上，LAYOUT_COLLISION
告警 72/80（90%，根因 top-center exclusive 双组件）；注入越界浮动件后
COMPONENT_OUTSIDE_CANVAS 80/80。本测试对同一全量语料应用修复建议
（plan_layout_repairs 动作链 + resolve_floating_layout 钳制）后逐版式复检
—— 三类版面检查必须归零（P8 验收门禁）。
"""
import pytest

from tests.cartography.ac07_corpus_harness import (
    PROFILES,
    apply_repairs,
    build_raw_components,
    build_spec,
    layout_tally,
    pick_cases,
)

pytestmark = pytest.mark.cartography


@pytest.mark.parametrize("corrupt", [False, True], ids=["native", "corrupt"])
def test_three_layout_checks_zero_after_repairs_full_corpus(corrupt):
    cases = pick_cases(20)
    assert len(cases) == 20
    for case in cases:
        raw = build_raw_components(case)
        for profile in PROFILES:
            spec = build_spec(raw, profile, corrupt)
            before = layout_tally(spec)
            repaired = apply_repairs(spec)
            after = layout_tally(repaired)
            assert not after, (
                f"{case['id']}@{profile}: 修复后仍残留 {after}"
                f"（修复前 {before}）"
            )
