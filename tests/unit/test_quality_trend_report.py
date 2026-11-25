"""制图质量趋势报告脚本（scripts/quality_trend_report.py）回归锁。

覆盖：
- 出版就绪只用布尔 ``True`` 计数，非布尔真值（如 1）不虚高分子；
- 缺 ``check_id`` 的 metric 行不进入分组，避免 ``sorted`` 混 None/str 崩溃。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import quality_trend_report as report  # noqa: E402


def _patch_latest_runs(monkeypatch, runs):
    from app.services import cartography_metrics_store as store

    async def _fake_latest_runs(n=30):
        return runs

    monkeypatch.setattr(store, "latest_runs", _fake_latest_runs)


def test_publish_readiness_counts_only_boolean_true(monkeypatch):
    """passed=1 是非布尔真值，不得计入出版就绪分子。"""
    _patch_latest_runs(monkeypatch, [
        {"lane": "publish", "source": "publish", "passed": 1, "summary": {}},
        {"lane": "publish", "source": "publish", "passed": True, "summary": {}},
    ])
    metrics = report.top_level_metrics()
    assert metrics["出版就绪"] == "50.0% (n=2)"


def test_collect_rows_skips_metrics_without_check_id(monkeypatch):
    """无 check_id 的 metric 行不得让 group_trends 的 sorted 崩溃。"""
    _patch_latest_runs(monkeypatch, [
        {
            "ts": None,
            "lane": "runtime",
            "scene_id": "s",
            "metrics": [
                {"check_id": "carto.load.ratio", "value": 0.2, "verdict": "pass"},
                {"check_id": None, "value": 0.3, "verdict": "pass"},
            ],
        },
    ])
    rows = report.collect_rows()
    assert [row["check_id"] for row in rows] == ["carto.load.ratio"]
    grouped = report.group_trends(rows)
    assert list(grouped) == ["carto.load.ratio"]
