"""多尺度对账契约测试（F10 M9，design D8）.

锁定：grammar scale_rules 与 label_plan 分界同界（import 期断言的测试面
复核）、scene_lod 是**有意不同**的第二 zoom 心智模型（不合并、不重抄），
以及 grammar ScaleDecision.visibility_hints 的消费面契约。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.label_plan import DEFAULT_ZOOM_BANDS  # noqa: E402
from app.lib.cartography.scale_rules import (  # noqa: E402
    SCALE_TIERS,
    scale_actions,
)


class TestBandReconciliation:
    def test_scale_tiers_match_label_plan_bounds(self):
        # 单一 zoom 心智模型覆盖标注与表达两层（契约断言的测试面复核）。
        label_bounds = [(b.min_zoom, b.max_zoom) for b in DEFAULT_ZOOM_BANDS]
        scale_bounds = [(t.min_zoom, t.max_zoom) for t in SCALE_TIERS]
        assert scale_bounds == label_bounds

    def test_scene_lod_is_a_distinct_mental_model(self):
        # scene_lod（ADR-0199 渲染 LOD 3/6/10/14）与 label/grammar 带
        # （0/8/11/14）**有意不同**——分母语义不同（渲染抽稀 vs 标注/表达
        # 动作）。如实披露差异，不合并、不互抄系数。
        from app.lib.cartography.scene_lod import LOD_BANDS
        lod_bounds = [(b["min_zoom"], b["max_zoom"]) for b in LOD_BANDS]
        label_bounds = [(b.min_zoom, b.max_zoom) for b in DEFAULT_ZOOM_BANDS]
        assert lod_bounds != label_bounds
        # 边界序列诚实存档（变化需同时审阅两套语义面）。
        assert lod_bounds == [(3.0, 6.0), (6.0, 10.0), (10.0, 14.0), (14.0, 19.0)]

    def test_visibility_hints_present_per_tier(self):
        for tier in SCALE_TIERS:
            assert tier.visibility_hints, tier.name
            assert "street_detail_minzoom" in tier.visibility_hints

    def test_scale_decision_carries_visibility_hints(self):
        sd = scale_actions(zoom=4.0, feature_count=100, geometry="polygon")
        assert sd.visibility_hints == SCALE_TIERS[0].visibility_hints

    def test_advisory_only_contract(self):
        # visibility_hints 是 advisory（渲染端按现状落地）——ScaleDecision
        # 的动作字段（聚合/候选）不得因 hints 缺席而改变。
        a = scale_actions(zoom=12.0, feature_count=9000, geometry="point")
        assert a.is_dense_points is True
        assert a.point_candidates, "dense point contract independent of hints"
