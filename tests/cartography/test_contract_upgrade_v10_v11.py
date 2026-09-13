"""W9 契约升级测试：C1–C4 的 v10 → v11 兼容路径（V11，ADR-0169）。

每条契约验证「V10 形态的旧工件在新代码里可读且行为等价」——升级路径
不得破坏既有消费方（只加不改的机器化核验）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography


# ── C1: SymbologyDecision v10 dict → v11 模型 ───────────────────────────

def test_c1_v10_decision_dict_reads_in_v11() -> None:
    from app.lib.cartography.symbology import SymbologyDecision

    v10_payload = {
        "method": "quantiles", "k": 5, "palette": "YlOrRd",
        "clip_policy": "none", "context": "screen",
        "reasons": ["v10 shape"], "rejected": [], "confidence": 0.6,
        "source": "distribution", "low_confidence": False,
        "clip_low": None, "clip_high": None, "n_clipped": 0,
    }
    decision = SymbologyDecision.model_validate(v10_payload)
    assert decision.k == 5
    # v11 扩展键缺省 None（V10 形状不因新字段而失效）
    assert decision.bivariate is None and decision.cost_hint is None
    # 再序列化 = 原形状 + 四个 None 键（消费方忽略安全）
    dumped = decision.model_dump()
    assert {k: dumped[k] for k in v10_payload} == v10_payload


def test_c1_legend_spec_v10_without_c3_keys() -> None:
    from app.lib.cartography.label_plan import build_label_spec

    v10_profile = {
        "featureCount": 100,
        "fields": {"name": {"type": "text", "sampleValues": ["A"], "null_count": 0}},
    }
    spec = build_label_spec(v10_profile)
    assert spec is not None
    # v11 新键带缺省（v10 消费方忽略安全；v11 消费方得到 V10 等价语义）
    assert spec["collision"]["strategy"] == "grid"
    assert spec["typography"]["wrapMode"] == "auto"


# ── C2: 版面 IR v1 与 v2 并存 ───────────────────────────────────────────

def test_c2_publication_v1_still_valid() -> None:
    from app.lib.cartography.layout_description import (
        PUBLICATION_LAYOUT_VERSION, LayoutInput, build_publication_layout,
    )

    out = build_publication_layout(LayoutInput(
        paper_size="A4", orientation="landscape", dpi=300,
        frame_width=2480, frame_height=1754, request_title="升级兼容",
    ))
    assert out["version"] == PUBLICATION_LAYOUT_VERSION == 1
    assert out["page"]["widthPx"] == 2480  # v1 语义逐字段不变


def test_c2_ir_v2_fixture_loads_and_validates() -> None:
    import json

    from app.lib.cartography.layout_description import validate_layout_ir

    fixture = json.loads((
        Path(__file__).resolve().parents[2]
        / "tests/cartography/golden_corpus/layout_ir/basic.json"
    ).read_text(encoding="utf-8"))
    assert validate_layout_ir(fixture["expected"]) == []
    assert fixture["expected"]["version"] == 2


# ── C3: v10 标注 spec（无 collision/typography）→ v11 消费 ──────────────

def test_c3_label_strategy_v10_shape_defaults() -> None:
    from app.lib.cartography.label_plan import LabelStrategy

    v10_strategy = LabelStrategy(
        mode="top_n", top_n=50, priority_field="pop",
        priority_source="value_field", size_ratio=1.0,
    )
    assert v10_strategy.collision is None
    assert v10_strategy.typography is None
    # dump 里新键为 None —— v10 消费方按缺省读（TS normalizeLabelStrategy
    # 的同款形态由前端测试锁定）


# ── C4: 无 wave/cost 列的旧观测行 → v11 聚合 ────────────────────────────

def test_c4_legacy_rows_without_wave_still_aggregate() -> None:
    from app.services.cartography_ratchet import (
        Observation,
        aggregate_observations,
        aggregate_observations_by_wave,
    )

    legacy_rows = [
        {"scene_id": "heatmap", "check_id": "carto.x", "value": 1.0},
        {"scene_id": "heatmap", "check_id": "carto.x", "value": 3.0},
    ]
    old_agg = aggregate_observations(legacy_rows)
    assert len(old_agg) == 1 and old_agg[0].wave is None  # v10 聚合面不变
    new_agg = aggregate_observations_by_wave(legacy_rows)
    assert len(new_agg) == 1 and new_agg[0].wave == "unscoped"  # 归 unscoped 组
    # 旧构造（二/三参）兼容
    assert Observation("s", "c", 1.0).wave is None
