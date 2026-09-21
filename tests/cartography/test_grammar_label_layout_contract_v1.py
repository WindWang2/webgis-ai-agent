"""Grammar × Label × Layout 契约测试（C4/C5，ADR-0204）。

C4：标注委托 label_plan（不建第二引擎）——GrammarDecision.label_spec
携带完整标注契约（字段/策略/zoom 分级/碰撞/排版）。
C5：布局委托 layout_solver.solve_layout_v3——参与者投影、碰撞回退、
user-pinned 槽位 wins、live/export page profile 决策一致性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.cartography.grammar_solver import (  # noqa: E402
    FieldEvidence,
    GrammarRequest,
    solve_grammar,
)
from app.lib.cartography.label_plan import (  # noqa: E402
    DENSE_FEATURE_COUNT,
)
from app.lib.cartography.layout_solver import (  # noqa: E402
    LayoutParticipantV3,
    solve_layout_v3,
)


def _label_profile(feature_count: int = 300) -> dict:
    return {
        "featureCount": feature_count,
        "fields": {
            "name": {"type": "string", "sampleValues": ["站点A", "站点B", "站点C"],
                     "null_count": 0},
            "ridership": {"type": "number", "sampleValues": [120, 340, 80],
                          "null_count": 0},
        },
    }


def _point_decision(feature_count: int = 300, with_label: bool = True):
    return solve_grammar(GrammarRequest(
        geometry="point", feature_count=feature_count, zoom=12.0,
        fields=[FieldEvidence(name="ridership", dtype="float",
                              values=[120.0, 340.0, 80.0])],
        label_profile=_label_profile(feature_count) if with_label else None,
    ))


class TestLabelContractDelegation:
    def test_label_spec_delegates_to_label_plan(self):
        d = _point_decision()
        spec = d.label_spec
        assert spec is not None
        # 契约要素齐全（camelCase，与 mapspec_schema.MapSpecLayerLabel 同形）
        assert spec["field"] == "name"
        assert spec["mode"] in ("all", "top_n", "hover_only")
        assert spec["priorityField"] is not None
        assert len(spec["zoomBands"]) == 4
        for band in spec["zoomBands"]:
            assert {"minZoom", "maxZoom", "topRatio", "sizeRatio"} <= set(band)
        assert spec["collision"]["strategy"] in ("grid", "maplibre")
        assert spec["typography"]["maxChars"] > 0

    def test_dense_profile_downgrades_label_budget(self):
        sparse = _point_decision(feature_count=300)
        dense = _point_decision(feature_count=DENSE_FEATURE_COUNT * 3)
        assert sparse.label_spec["mode"] == "all"
        assert dense.label_spec["mode"] == "top_n"
        assert dense.label_spec["topN"] is not None

    def test_no_label_profile_means_no_label_spec(self):
        d = _point_decision(with_label=False)
        assert d.label_spec is None

    def test_unqualified_field_yields_none_with_advisory(self):
        profile = {
            "featureCount": 50,
            "fields": {
                "objectid": {"type": "number",
                             "sampleValues": ["1", "2", "3"],
                             "null_count": 0, "unique_count": 3},
            },
        }
        d = solve_grammar(GrammarRequest(
            geometry="point", feature_count=50, zoom=12.0,
            fields=[FieldEvidence(name="v", dtype="float", values=[1.0])],
            label_profile=profile))
        # label_plan 契约：无合格字段 → 不标注（禁止用 ID 当标注）
        assert d.label_spec is None


class TestLayoutParticipantProjection:
    def test_participants_cover_baseline_and_legend(self):
        d = solve_grammar(GrammarRequest(
            geometry="polygon", zoom=9.0, output_purpose="a4_landscape",
            fields=[FieldEvidence(name="v", dtype="float",
                                  values=[1.0, 2.0, 3.0, 4.0])],
            content_flags={"has_data_source": True}))
        participants = d.layout_participants()
        types = {p["id"] for p in participants}
        assert {"title", "scale_bar", "north_arrow", "attribution",
                "legend"} <= types
        # 专题层在场 → 图例必配（required，不可被抑制）
        legend = next(p for p in participants if p["id"] == "legend")
        assert legend["optional"] is False
        assert legend["collision_group"] == "legend_family"

    def test_legend_form_none_drops_legend_participant(self):
        d = solve_grammar(GrammarRequest(
            geometry="point", feature_count=10, zoom=15.0,
            fields=[]))
        participants = d.layout_participants()
        assert all(p["id"] != "legend" for p in participants)


class TestLayoutSolving:
    def _participants(self, pinned_zones=None):
        d = solve_grammar(GrammarRequest(
            geometry="polygon", zoom=9.0,
            fields=[FieldEvidence(name="v", dtype="float",
                                  values=[1.0, 2.0, 3.0, 4.0])],
            content_flags={"has_data_source": True}))
        return d.layout_participants(pinned_zones=pinned_zones)

    def test_collision_fallback_keeps_required(self):
        raw = self._participants()
        # 把所有组件都钉到同一槽位 → 求解器必须回退而非丢弃
        pinned = {p["id"]: "top-left" for p in raw}
        participants = [LayoutParticipantV3(
            **{**p, "requested_zone": pinned[p["id"]]}) for p in raw]
        solution = solve_layout_v3(participants)
        placed_types = {p.type for p in solution.placements}
        assert "legend" in placed_types
        assert "title" in placed_types
        moved = [p for p in solution.placements if p.moved]
        assert moved, "过容量槽位必须产生回退移动"
        assert not solution.suppressed, "required 组件不可被抑制"

    def test_default_intent_and_user_pinned_zone_wins(self):
        raw = self._participants()
        # 语法默认意图：legend 先验 top-left（容量允许 → 不移动）
        default_solution = solve_layout_v3(
            [LayoutParticipantV3(**p) for p in raw])
        assert default_solution.zone_for("legend") == "top-left"
        assert not default_solution.suppressed
        # user-pinned：显式槽位以用户为准（user-wins）
        pinned_raw = self._participants(pinned_zones={"legend": "bottom-right"})
        pinned_solution = solve_layout_v3(
            [LayoutParticipantV3(**p) for p in pinned_raw])
        assert pinned_solution.zone_for("legend") == "bottom-right"
        moved_legend = next(p for p in pinned_solution.placements
                            if p.id == "legend")
        assert moved_legend.moved is False

    def test_optional_component_can_be_suppressed_deterministically(self):
        raw = self._participants()
        # north_arrow 是 optional：钉满 top-left 强挤压下允许被抑制，
        # 但结果必须确定（同输入两次求解一致）
        participants = [
            LayoutParticipantV3(**{**p, "requested_zone": "top-left"})
            for p in raw
        ]
        s1 = solve_layout_v3(participants)
        s2 = solve_layout_v3(participants)
        assert s1.model_dump() == s2.model_dump()

    def test_live_export_parity_same_decision_adapts_to_profile(self):
        """live/export grammar parity：同一份参与者决策在 viewport 与
        a4_landscape 两个 page profile 下都得到可行解（required 不丢）。"""
        raw = self._participants()
        participants = [LayoutParticipantV3(**p) for p in raw]
        live = solve_layout_v3(participants, page_profile="viewport")
        export = solve_layout_v3(participants, page_profile="a4_landscape")
        assert live.page_profile == "viewport"
        assert export.page_profile == "a4_landscape"
        for solution in (live, export):
            placed_types = {p.type for p in solution.placements}
            assert {"title", "scale_bar", "north_arrow",
                    "attribution", "legend"} <= placed_types
