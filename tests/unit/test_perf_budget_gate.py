"""Budget gate 语义（P5：perf/budgets.json 门禁 + 自证有效）。

门禁契约：超线（含容差）→ 红；故意超线的合成用例必须能红 —— 自证有效
（#1230-#1233 预算漂移波次的教训：静默预算形同虚设）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.perf.run_budget import (
    DEFAULT_BUDGETS,
    evaluate,
    load_budgets,
    render_report,
    run_gate,
    self_test,
)


def test_budgets_file_exists_with_four_first_wave_lines() -> None:
    cfg = load_budgets(DEFAULT_BUDGETS)
    ids = [b["id"] for b in cfg["budgets"]]
    assert ids == [
        "sse_concurrent_50_total_ms",
        "mvt_tile_p95_ms",
        "cube_window_p95_ms",
        "chat_first_token_p95_ms",
    ]
    assert cfg["tolerance_pct"] == 10
    for b in cfg["budgets"]:
        assert b["target"] > 0
        assert b["rationale"] and b["tool"]  # 每条预算必须带依据


def test_evaluate_inside_tolerance_passes() -> None:
    entry = {"id": "x", "target": 100}
    verdict = evaluate(entry, measured_ms=105, tolerance_pct=10)
    assert verdict["ok"] is True  # 105 ≤ 110 ceiling


def test_evaluate_beyond_tolerance_fails() -> None:
    entry = {"id": "x", "target": 100}
    verdict = evaluate(entry, measured_ms=111, tolerance_pct=10)
    assert verdict["ok"] is False


def test_synthetic_breach_turns_gate_red(tmp_path: Path) -> None:
    # 自证：把第一条预算注入 10 倍测量值 → 门禁必须红（不可静默绿）。
    code = self_test(DEFAULT_BUDGETS)
    assert code == 0, "注入超线测量后门禁未红 —— 门禁失效"


def test_run_gate_reports_missing_measurement_as_red(tmp_path: Path) -> None:
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({
        "schema": "quality-e2e-v9/ADR-0146 budget gate",
        "tolerance_pct": 10,
        "budgets": [{"id": "no_such_line", "metric": "p95_ms", "target": 10,
                     "tool": "t", "rationale": "r"}],
    }), encoding="utf-8")
    results, ok = run_gate(budgets_path=budgets)
    assert ok is False
    assert results[0]["ok"] is False


def test_render_report_marks_red_visibly() -> None:
    results = [{"id": "x", "ok": False, "target_ms": 10, "tolerance_pct": 10,
                "ceiling_ms": 11, "measured_ms": 99}]
    report = render_report(results, all_ok=False)
    assert "RED" in report and "FAIL" in report


def test_unknown_schema_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "b.json"
    bad.write_text(json.dumps({"schema": "other", "budgets": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        load_budgets(bad)
