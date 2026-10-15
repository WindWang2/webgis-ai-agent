"""Representation golden corpus —— 后端锁定面（方向 5）。

fixture 由 ``generate.py`` 从 python 权威实现生成并冻结（24 case × 12
zoom）。本文件重算并断言相等（实现漂移即红）；前端
``frontend/lib/carto-ir/representation-parity.golden.test.ts`` 用 TS 裁决
器对拍同一 fixture 的 mode_at_samples —— 跨语言 parity 闸。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lib.cartography.representation import (
    representation_at_scale,
    representation_mode_at_zoom,
)
from app.lib.cartography.scale_rules import resolve_scale_tier

CORPUS = Path(__file__).parent / "representation_corpus" / "representation_golden.json"


def _load() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _input_to_kwargs(inp: dict) -> dict:
    kwargs = dict(inp)
    if "artifact_modes" in kwargs:
        kwargs["artifact_modes"] = tuple(kwargs["artifact_modes"])
    return kwargs


class TestCorpusLock:
    def test_corpus_shape(self):
        golden = _load()
        assert golden["contract"] == "representation_corpus.v1"
        # 任务书 DoD：≥20 个代表性 MapSpec 输入 × 多 zoom。
        assert len(golden["cases"]) >= 20
        assert len(golden["zoom_samples"]) >= 8

    @pytest.mark.parametrize("case", _load()["cases"], ids=lambda c: c["name"])
    def test_case_reproduces(self, case: dict):
        decision = representation_at_scale(**_input_to_kwargs(case["input"]))
        assert decision.geometry_family == case["geometry_family"]
        assert decision.feature_count == case["feature_count"]
        assert [
            {"min_zoom": w.min_zoom, "max_zoom": w.max_zoom, "mode": w.mode}
            for w in decision.windows
        ] == case["windows"]
        assert decision.visibility_gate == case["visibility_gate"]
        assert (decision.aggregation is not None) == case["aggregation_present"]
        assert decision.mode_at_reference == case["mode_at_reference"]
        assert decision.tier == case["tier_at_reference"]
        for zoom, mode, tier in zip(
            case["zoom_samples"], case["modes_at_samples"], case["tiers_at_samples"]
        ):
            assert representation_mode_at_zoom(decision.windows, zoom) == mode
            assert resolve_scale_tier(zoom).name == tier

    def test_corpus_covers_required_matrix(self):
        """任务书测试矩阵覆盖审计：几何族 × 密度梯度 × pin × artifact × 媒介。"""
        names = {c["name"] for c in _load()["cases"]}
        for required in (
            "point_small", "point_medium", "point_dense", "point_extreme",
            "line_overload_detail_gate", "polygon_overload", "raster",
            "pin_cluster", "pin_incompatible",
            "point_dense_a4_portrait", "point_dense_a4_landscape",
            "line_generalized_artifact", "unknown_family",
        ):
            assert required in names, required
