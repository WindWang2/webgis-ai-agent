"""Shared perf-baseline gate (#618-31).

A new workload with no committed baseline used to ``pytest.skip`` — CI
reported "N skipped" and never compared anything. Recording a baseline is
allowed only when the operator opts in.
"""
from __future__ import annotations

import os

import pytest

ALLOW_MISSING_ENV = "ALLOW_MISSING_PERF_BASELINE"
UPDATE_ENV = "PERF_UPDATE_BASELINES"


def allow_missing_baseline() -> bool:
    return os.environ.get(ALLOW_MISSING_ENV) == "1"


def update_baselines() -> bool:
    return os.environ.get(UPDATE_ENV) == "1"


def decide_baseline_action(
    name: str,
    baselines: dict,
    *,
    update: bool | None = None,
    allow_missing: bool | None = None,
) -> str:
    """Return ``"record"`` or ``"compare"``.

    ``record`` — write a new/updated baseline and skip the comparison.
    ``compare`` — the workload already has a committed baseline.

    A missing baseline with neither opt-in fails so a new workload cannot
    silently drop out of the regression gate.
    """
    if update is None:
        update = update_baselines()
    if allow_missing is None:
        allow_missing = allow_missing_baseline()
    missing = name not in baselines
    if update or (missing and allow_missing):
        return "record"
    if missing:
        pytest.fail(
            f"No perf baseline for workload {name!r}. Commit a median in the "
            f"baselines JSON, or set {ALLOW_MISSING_ENV}=1 "
            f"(or {UPDATE_ENV}=1) to record one."
        )
    return "compare"
