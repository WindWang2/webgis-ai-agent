"""V2 cartography axes corpus tests（D10）。

layout 重叠/越界、CVD 可辨性（含 YlOrRd known-unsafe 锚）、required
槽位缺席、visual 轴离线诚实性 —— 全部确定性纯函数。
"""
from __future__ import annotations

import pytest

from app.evaluation.cartography_axes_corpus import (
    build_cartography_axes_corpus,
    run_cartography_axes_case,
)


def test_corpus_covers_all_axes():
    cases = build_cartography_axes_corpus()
    axes = {c.axis for c in cases}
    assert axes == {"layout", "cvd", "template", "honesty"}
    assert len(cases) >= 8


@pytest.mark.parametrize("case", build_cartography_axes_corpus(), ids=lambda c: c.case_id)
def test_axis_case(case):
    result = run_cartography_axes_case(case)
    assert result.passed, f"{case.case_id}: {result.failures[:3]}"


def test_layout_overlap_names_both_components():
    cases = {c.case_id: c for c in build_cartography_axes_corpus()}
    result = run_cartography_axes_case(cases["CARTX-layout-overlap-detected"])
    assert result.passed


def test_cvd_known_unsafe_is_anchored():
    """默认 YlOrRd deuteranopia 缺口必须持续被检出（硬化后翻新此行）。"""
    cases = {c.case_id: c for c in build_cartography_axes_corpus()}
    row = cases["CARTX-cvd-default-ylorrd-deuteranopia"]
    assert row.expected_cvd_safe is False
    assert run_cartography_axes_case(row).passed


def test_visual_axis_honesty():
    cases = {c.case_id: c for c in build_cartography_axes_corpus()}
    result = run_cartography_axes_case(cases["CARTX-honesty-visual-not-evaluated"])
    assert result.passed, result.failures
