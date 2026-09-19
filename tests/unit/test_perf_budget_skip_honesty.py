"""TEST-12（deep-review 2026-09-19）：run_budget 的 skip 语义必须诚实。

审计：scripts/perf/run_budget.py 把 SkipMeasurement（如 zarr 缺席）记为
``ok=True, skipped=True`` 并让 gate 整体 PASS —— "测不了" 被当成了 "通过"，
预算线可以在缺失依赖的环境里永久空转。契约改为：

  * skip 默认 RED（无法测量 ≠ 通过）；
  * 只有显式 allowlist（``--allow-skip ID`` 或 ``PERF_ALLOW_SKIPS=id1,id2``）
    的 skip 才允许 PASS，并在报告里可见。

新增预算线/删除 allowlist 时这些断言会失败 —— 那是显式的接线决策点。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.perf.measurements import SkipMeasurement
from scripts.perf import run_budget


def _skip_measurement(**_kwargs):
    raise SkipMeasurement("zarr not installed: simulated")


@pytest.fixture
def budgets_file(tmp_path: Path) -> Path:
    path = tmp_path / "budgets.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "schema": "quality-e2e-v9/ADR-0146 budget gate",
                "tolerance_pct": 10,
                "iterations_ci": 1,
                "budgets": [
                    {
                        "id": "skip_me",
                        "metric": "p95_ms",
                        "target": 100,
                        "tool": "test-fixture",
                        "rationale": "skip-honesty fixture",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def skipping_measurements(monkeypatch):
    monkeypatch.setitem(run_budget.MEASUREMENTS, "skip_me", _skip_measurement)


def test_unallowlisted_skip_fails_the_gate(budgets_file, skipping_measurements):
    results, ok = run_budget.run_gate(budgets_path=budgets_file)
    assert ok is False, "未 allowlist 的 skip 必须让 gate 红"
    assert results[0]["id"] == "skip_me"
    assert results[0]["ok"] is False
    assert results[0]["skipped"] is True
    assert "not allowlisted" in results[0]["error"]


def test_allowlisted_skip_passes_and_is_reported(budgets_file, skipping_measurements):
    results, ok = run_budget.run_gate(
        budgets_path=budgets_file, allow_skips={"skip_me"}
    )
    assert ok is True, "显式 allowlist 的 skip 允许通过"
    assert results[0]["ok"] is True and results[0]["skipped"] is True
    report = run_budget.render_report(results, ok)
    assert "SKIP skip_me" in report


def test_env_allowlist_is_parsed(monkeypatch):
    monkeypatch.setenv("PERF_ALLOW_SKIPS", "cube_window_p95_ms, other_line")
    assert run_budget._allow_skips_from_env() == {
        "cube_window_p95_ms",
        "other_line",
    }
    monkeypatch.setenv("PERF_ALLOW_SKIPS", "")
    assert run_budget._allow_skips_from_env() == set()


def test_cli_allow_skip_flag_turns_skip_green(budgets_file, skipping_measurements):
    assert run_budget.main(["--budgets", str(budgets_file)]) == 1
    assert (
        run_budget.main(
            ["--budgets", str(budgets_file), "--allow-skip", "skip_me"]
        )
        == 0
    )


def test_cli_env_allow_skip_turns_skip_green(
    budgets_file, skipping_measurements, monkeypatch
):
    monkeypatch.setenv("PERF_ALLOW_SKIPS", "skip_me")
    assert run_budget.main(["--budgets", str(budgets_file)]) == 0
