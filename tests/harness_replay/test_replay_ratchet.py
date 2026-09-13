"""轨迹 ratchet 接流测试（B7）：provisional-first、故意劣化会红、waiver。"""
from __future__ import annotations

import dataclasses

import pytest

from app.lib.harness.replay.ratchet import (
    baseline_rows,
    degrade_rows,
    evaluate_replay_observations,
    rows_from_results,
)
from app.lib.harness.replay.replayer import OfflineReplayer
from app.lib.harness.replay.scenarios import build_corpus
from app.services.cartography_ratchet import (
    build_baseline_entries,
    resolve_direction,
)

pytestmark = pytest.mark.cartography

_GREEN_IDS = ("core-point_distribution-01", "core-choropleth-01",
              "core-heatmap-01")


@pytest.fixture(scope="module")
def rows():
    corpus = {s.scenario_id: s for s in build_corpus()}
    replayer = OfflineReplayer(seed=11)
    results = [replayer.replay_scenario(corpus[sid]) for sid in _GREEN_IDS]
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return rows_from_results([loop.run_until_complete(r) for r in results])
    finally:
        loop.close()


def test_direction_naming_matches_ratchet_rules(rows):
    for row in rows:
        direction = resolve_direction(row["check_id"])
        if row["check_id"].startswith("gate."):
            assert direction == "low_bad"  # 得分：越高越好
        else:
            assert direction == "high_bad"  # 成本/错误：越高越糟


def test_provisional_first_never_blocks(rows):
    """provisional 基线只记录不拦截（ADR-0159 纪律）。"""
    entries = build_baseline_entries(baseline_rows(rows))
    assert entries and all(e.status == "provisional" for e in entries)
    violations = evaluate_replay_observations(rows, entries)
    assert violations == []


def test_intentional_degradation_turns_gate_red(rows):
    """故意劣化必须被 active 基线拦截（否则 ratchet 无意义）。"""
    entries = [
        dataclasses.replace(e, status="active")
        for e in build_baseline_entries(baseline_rows(rows))
    ]
    degraded = degrade_rows(rows)
    violations = evaluate_replay_observations(rows, entries)
    assert violations == []  # 原观测不劣化 → 无违规
    degraded_violations = evaluate_replay_observations(degraded, entries)
    assert degraded_violations  # 劣化观测 → 拦截
    assert all(v.scene_id for v in degraded_violations)


def test_zero_baseline_growth_is_regression():
    """零基线特殊语义（ADR-0178）：0 → 任何增长即回归。"""
    from app.services.cartography_ratchet import Baseline

    rows = [{"scene_id": "s", "check_id": "replay.tool_error_rate",
             "value": 12.5}]
    baselines = [Baseline(scene_id="s", check_id="replay.tool_error_rate",
                          value=0.0, tolerance_pct=5.0, status="active")]
    violations = evaluate_replay_observations(rows, baselines)
    assert len(violations) == 1
    assert violations[0].observed_value == 12.5


def test_waiver_suppresses_and_expires(rows):
    from datetime import datetime, timedelta, timezone

    entries = [
        dataclasses.replace(e, status="active")
        for e in build_baseline_entries(baseline_rows(rows))
    ]
    degraded = degrade_rows(rows)
    first = evaluate_replay_observations(degraded, entries)
    assert first
    victim = first[0]
    waivers = [{
        "check_id": victim.check_id,
        "scene_id": "*",
        "reason": "intentional experiment",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
    }]
    suppressed = evaluate_replay_observations(degraded, entries, waivers)
    assert all(v.waived for v in suppressed
               if v.check_id == victim.check_id)
    expired = [{
        "check_id": victim.check_id, "scene_id": "*",
        "reason": "expired", "expires_at":
            datetime.now(timezone.utc) - timedelta(days=1),
    }]
    revived = evaluate_replay_observations(degraded, entries, expired)
    assert any(not v.waived for v in revived
               if v.check_id == victim.check_id)


@pytest.mark.asyncio
async def test_record_replay_run_never_raises(monkeypatch):
    """落账面 fail-safe：总闸关闭 → None；store 异常 → 不抛。"""
    from app.lib.harness.replay.ratchet import record_replay_run

    monkeypatch.setenv("CARTO_METRICS_STORE_ENABLED", "0")
    assert await record_replay_run(
        [{"scene_id": "s", "check_id": "replay.tool_calls", "value": 1}],
        scene_id="s",
    ) is None

    async def _boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setenv("CARTO_METRICS_STORE_ENABLED", "1")
    import app.services.cartography_metrics_store as store

    monkeypatch.setattr(store, "record_quality_run", _boom)
    assert await record_replay_run(
        [{"scene_id": "s", "check_id": "replay.tool_calls", "value": 1}],
        scene_id="s",
    ) is None
