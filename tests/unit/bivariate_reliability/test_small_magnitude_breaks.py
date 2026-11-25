"""#2223 回归：小量级值域的断点不得有损取整塌缩。

审计实证：rate=(i+1)*1e-8 → 旧实现 estimate_breaks 被 round(_, 6) 塌缩成
[0.0, ...]，step paint 全部要素落最高档色，而图例区间仍显示非零值 ——
图例 / paint / 真实分类三者漂移。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.lib.cartography.thematic_spec import spec_to_paint
from app.services.bivariate_reliability_authoring import (
    parse_plan,
    preview_expression,
)
from app.services.bivariate_reliability_authoring.classify import (
    axis_breaks,
    equal_interval,
)
from tests.unit.bivariate_reliability.helpers import (
    bivariate_plan,
    make_fc,
    reliability_plan,
    seed_layer,
)

_TINY_EST = [{"est": float(i + 1) * 1e-8, "se": 1e-9} for i in range(10)]
_TINY_AB = [{"a": float(i + 1) * 1e-8, "b": float(10 - i) * 1e-8}
            for i in range(10)]


def _assert_intervals_match_internal_breaks(intervals, internal):
    """图例区间与内部断点同源一致（lo/hi 即断点邻接）。"""
    assert len(intervals) == len(internal) + 1
    for i, seg in enumerate(intervals):
        lo = internal[i - 1] if i > 0 else None
        hi = internal[i] if i < len(internal) else None
        assert seg["lo"] == lo and seg["hi"] == hi


@pytest.mark.asyncio
async def test_reliability_tiny_breaks_paint_and_legend_agree(session_id):
    await seed_layer(session_id, make_fc(_TINY_EST))
    plan = parse_plan(reliability_plan())
    prev = await preview_expression(session_id, "L1", plan,
                                    trusted_dispatch=True)
    est = prev["legend"]["estimate"]
    breaks = list(est["breaks"])
    internal = breaks[1:-1]
    # 内部断点保持区分度（round(_, 6) 会把 1e-8 量级全部塌缩成 0.0）。
    assert len(internal) >= 1
    assert len(set(internal)) == len(internal)
    assert all(b > 0 for b in breaks)
    _assert_intervals_match_internal_breaks(est["intervals"], internal)

    # paint 投影与图例同源：step stops 使用同一组内部断点，非退化。
    paint, warnings = spec_to_paint(est["legend_spec"])
    assert paint is not None and not warnings
    assert paint["method"] == "step"
    stop_inputs = [s[0] for s in paint["stops"]]
    assert stop_inputs == [float(b) for b in internal]
    assert len(set(stop_inputs)) == len(stop_inputs)


@pytest.mark.asyncio
async def test_bivariate_tiny_breaks_legend_and_paint_agree(session_id):
    await seed_layer(session_id, make_fc(_TINY_AB))
    plan = parse_plan(bivariate_plan(n=3))
    prev = await preview_expression(session_id, "L1", plan,
                                    trusted_dispatch=True)
    for axis_key in ("col", "row"):
        axis = prev["legend"]["axes"][axis_key]
        # bivariate 轴 breaks 即内部断点（axis_breaks 不含 min/max）。
        internal = list(axis["breaks"])
        assert len(internal) >= 1
        assert len(set(internal)) == len(internal)
        assert all(b > 0 for b in internal)
        _assert_intervals_match_internal_breaks(axis["intervals"], internal)


def test_equal_interval_collapsed_breaks_are_dropped_and_disclosed():
    """1e16 附近 ulp=2：等距断点舍入回端点时必须丢弃，不得谎报满级。"""
    values = [1e16, 1e16 + 2.0]
    # 未修复时 n=2 返回 [1e16]（断点塌缩成 min），全部要素被归入最高类。
    assert equal_interval(values, 2) == []
    assert equal_interval(values, 3) == []
    axis = SimpleNamespace(method="equal_interval", field="a", breaks=None)
    breaks, _, disclosures = axis_breaks(values, axis, 2)
    assert breaks == []
    assert disclosures and "实际只有 1 级" in disclosures[0]
