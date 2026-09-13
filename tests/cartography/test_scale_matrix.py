"""W8 规模化验证矩阵测试（V11，ADR-0168）。

矩阵形状（408/1632）、成本诚实（无 LLM 记 0 + llmUsed false）、成本预算
告警、波次聚合、**注入劣化 100% 拦截**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.lib.harness.scale_matrix import (  # noqa: E402
    build_matrix_combos,
    matrix_results_to_observations,
    run_matrix,
)


# ── W8.1 矩阵形状 ────────────────────────────────────────────────────────

def test_matrix_shape_core_408_full_1632() -> None:
    core = build_matrix_combos(tier="core")
    full = build_matrix_combos(tier="full")
    assert len(core) == 17 * 12 * 2  # = 408（核心批）
    assert len(full) == 17 * 12 * 2 * 4  # = 1632（全量）
    assert len({c.combo_id for c in core}) == len(core)  # id 唯一（可回放定位）
    # 轴覆盖：17 图型 / 12 数据态 / 2 语言 / 4 形态（full）
    assert len({c.map_type for c in full}) == 17
    assert len({c.data_state for c in full}) == 12
    assert {c.language for c in full} == {"zh", "en"}
    assert {c.output_form for c in full} == {"map", "svg", "pdf", "print"}


def test_matrix_deterministic_order() -> None:
    assert build_matrix_combos(tier="core") == build_matrix_combos(tier="core")


# ── W8.2 核心 408 组实跑 + 成本 ─────────────────────────────────────────

def test_core_matrix_runs_with_cost_fields() -> None:
    out = run_matrix(build_matrix_combos(tier="core"))
    assert out["executed"] == 408
    assert out["llmUsed"] is False          # 无 LLM 通道：诚实标注
    assert out["totalCostTokens"] == 0      # 记 0 而非伪造 token 数
    assert out["totalCostMs"] >= 0
    sample = out["results"][0]
    assert {"qualityMetrics", "costTokens", "costMs", "artifacts"} <= set(sample)
    assert set(sample["qualityMetrics"]) == {
        "carto.symbology.k", "carto.symbology.confidence",
        "carto.symbology.low_confidence", "carto.layout.overall",
        "carto.matrix.values_n",
    }


def test_cost_budget_alerts(monkeypatch) -> None:
    import app.lib.harness.scale_matrix as sm

    monkeypatch.setitem(sm.COST_BUDGET_MS, "heatmap_density", -1)  # 预算 -1ms → 必超
    out = run_matrix(build_matrix_combos(
        tier="core", map_types=["heatmap_density"], data_states=["nominal_point"],
        languages=["zh"]))
    assert out["executed"] == 1
    assert out["budgetAlerts"], "超预算必须告警（不拦截）"
    assert out["budgetAlerts"][0]["budgetMs"] == -1


# ── W8.4 波次聚合 + 劣化 100% 拦截 ──────────────────────────────────────

def test_wave_aggregation_and_degradation_intercept() -> None:
    from app.services.cartography_ratchet import (
        Baseline,
        aggregate_observations_by_wave,
        evaluate_ratchet,
    )

    out = run_matrix(build_matrix_combos(tier="core"))
    rows = matrix_results_to_observations(out, wave="W8")
    assert rows and {r["wave"] for r in rows} == {"W8"}

    observations = aggregate_observations_by_wave(rows)
    assert observations and all(o.wave == "W8" for o in observations)

    # 好基线（provisional → active 模拟：直接构造 active）
    baselines = [
        Baseline(scene_id=o.scene_id, check_id=o.check_id, value=o.value,
                 direction="low_bad", tolerance_pct=5.0, status="active")
        for o in observations
    ]
    # 未劣化 → 无违规
    assert evaluate_ratchet(observations, baselines) == []

    # 注入劣化：全部观测值 ×0.5（low_bad → 100% 拦截）
    degraded = [
        type(o)(scene_id=o.scene_id, check_id=o.check_id,
                value=o.value * 0.5 if o.value else -1.0, wave=o.wave)
        for o in observations
    ]
    violations = evaluate_ratchet(degraded, baselines)
    assert len(violations) == len(observations), "劣化必须 100% 拦截"
    # delta_pct 是「劣化幅度」（正 = 更糟；与 ratchet gate 的打印约定一致）；
    # 0 值基线的相对劣化为 inf —— 断言按语义（观测严格劣于基线）而非固定值
    assert all(v.delta_pct > 0 for v in violations)
    assert all(v.observed_value < v.baseline_value for v in violations)


# ── 核心矩阵 golden 冻结（确定性面；成本计时不入 golden）────────────────

GOLDEN = Path(__file__).resolve().parents[2] / (
    "tests/cartography/golden_corpus/scale_matrix/core_summary.json"
)


def test_core_summary_matches_frozen_golden() -> None:
    import json
    import statistics

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    out = run_matrix(build_matrix_combos(tier="core"))
    assert out["executed"] == golden["executed"]
    per_metric: dict = {}
    for r in out["results"]:
        for k, v in r["qualityMetrics"].items():
            per_metric.setdefault(k, []).append(float(v))
    means = {k: round(statistics.fmean(vs), 6) for k, vs in sorted(per_metric.items())}
    assert means == golden["metricMeans"], "矩阵度量均值漂移 —— 显式重生成 golden"
