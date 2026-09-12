"""Workflow V6 — RetryPolicy 退避几何与默认策略（P4 补强：retry.py policy 面）。"""
from __future__ import annotations

import random

import pytest

from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import retry as RT


def test_delay_is_exponential_and_bounded() -> None:
    policy = RT.RetryPolicy(base_delay_s=0.5, factor=2.0, max_delay_s=8.0, jitter_ratio=0.0)
    rng = random.Random(2026)
    delays = [policy.delay_for(n, rng=rng) for n in range(1, 8)]
    assert delays[0] == pytest_approx(0.5)
    assert delays[1] == pytest_approx(1.0)
    assert delays[2] == pytest_approx(2.0)
    # 有界上界：后续全部钳到 max_delay_s。
    assert all(d == pytest_approx(8.0) for d in delays[4:])


def pytest_approx(v: float) -> float:
    return v


def test_jitter_stays_within_ratio_band() -> None:
    policy = RT.RetryPolicy(base_delay_s=1.0, factor=1.0, max_delay_s=300.0, jitter_ratio=0.2)
    rng = random.Random(7)
    for _ in range(200):
        d = policy.delay_for(3, rng=rng)
        assert 0.8 <= d <= 1.2


def test_negative_jitter_never_below_zero() -> None:
    policy = RT.RetryPolicy(base_delay_s=0.01, factor=1.0, max_delay_s=0.02, jitter_ratio=1.0)
    rng = random.Random(3)
    assert all(policy.delay_for(1, rng=rng) >= 0.0 for _ in range(100))


def test_attempts_exhausted_semantics() -> None:
    policy = RT.RetryPolicy(max_attempts=2)
    assert not policy.attempts_exhausted(1)
    assert policy.attempts_exhausted(2)
    assert policy.attempts_exhausted(3)


def test_policy_bounded_by_contract_max_attempts() -> None:
    with pytest.raises(Exception):
        RT.RetryPolicy(max_attempts=C.MAX_NODE_ATTEMPTS + 1)


def test_default_policy_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("GIS_WORKFLOW_RETRY_BASE_DELAY_S", "0.25")
    monkeypatch.setenv("GIS_WORKFLOW_RETRY_MAX_DELAY_S", "4")
    policy = RT.default_policy()
    assert policy.max_attempts == 3
    assert policy.base_delay_s == 0.25
    assert policy.max_delay_s == 4.0


def test_default_policy_garbage_env_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIS_WORKFLOW_RETRY_MAX_ATTEMPTS", "not-a-number")
    monkeypatch.setenv("GIS_WORKFLOW_RETRY_BASE_DELAY_S", "")
    policy = RT.default_policy()
    assert policy.max_attempts >= 1
    assert policy.base_delay_s > 0
