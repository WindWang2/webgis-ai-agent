"""Interaction IR V1 回放语料测试（ADR-0227 I）。

每个 corpus 场景：``events + initial authority revisions -> same meaningful
projection`` —— 与生产摄入共用 apply_event / project_interaction_snapshot
（非平行实现）。统一断言：双跑逐位一致（确定性，无 wall-clock）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.gis_harness.interaction_ir.contracts import MAX_LEDGER_EVENTS
from app.services.gis_harness.interaction_ir.replay import replay_scenario

CORPUS_DIR = Path(__file__).resolve().parent / "replay_corpus"
SCENARIOS = sorted(CORPUS_DIR.glob("*.json"))


def _partial_match(actual, expected, path=""):
    """期望部分递归匹配（未声明的键不约束；list 长度必须相等）。"""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: expect dict, got {type(actual).__name__}"
        for key, value in expected.items():
            assert key in actual, f"{path}: missing key {key!r}"
            _partial_match(actual[key], value, f"{path}.{key}")
        return
    if isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: expect list, got {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path}: length {len(actual)} != {len(expected)}"
        )
        for i, (a, e) in enumerate(zip(actual, expected)):
            _partial_match(a, e, f"{path}[{i}]")
        return
    assert actual == expected, f"{path}: {actual!r} != {expected!r}"


def _check_invariant(name: str, result: dict, scenario: dict) -> None:
    if name == "ring_bounded_128":
        assert result["state"]["event_count"] <= MAX_LEDGER_EVENTS
    elif name == "dropped_372":
        assert result["state"]["dropped"] == 372
    elif name == "single_viewport_recent":
        kinds = [r["kind"] for r in result["projection"]["recent_interactions"]]
        assert kinds.count("viewport_settled") == 1
    else:
        raise AssertionError(f"unknown invariant {name!r}")


@pytest.mark.parametrize("scenario_path", SCENARIOS, ids=lambda p: p.stem)
def test_replay_scenario(scenario_path: Path):
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    result = replay_scenario(scenario)

    expected = scenario.get("expect", {})

    if "verdicts" in expected:
        if expected["verdicts"] == "all_accepted":
            for ack in result["verdicts"]:
                assert ack["verdict"] in ("accepted", "accepted_with_gap"), ack
        else:
            for ack, want in zip(result["verdicts"], expected["verdicts"]):
                for key, value in want.items():
                    actual_key = "verdict" if key == "event_verdict" else key
                    assert ack.get(actual_key) == value, (ack, want)

    if "summary" in expected:
        _partial_match(result["summary"], expected["summary"], "summary")

    if "state" in expected:
        _partial_match(result["state"], expected["state"], "state")

    if "projection" in expected:
        _partial_match(result["projection"], expected["projection"], "projection")

    for invariant in expected.get("invariants", []):
        _check_invariant(invariant, result, scenario)


@pytest.mark.parametrize("scenario_path", SCENARIOS, ids=lambda p: p.stem)
def test_replay_deterministic_double_run(scenario_path: Path):
    """双跑逐位一致 —— 确定性不依赖进程内随机/hash 种子/wall-clock。"""
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    assert replay_scenario(scenario) == replay_scenario(scenario)


def test_corpus_present():
    assert len(SCENARIOS) >= 8, "回放语料至少覆盖任务书 I 节场景集"
