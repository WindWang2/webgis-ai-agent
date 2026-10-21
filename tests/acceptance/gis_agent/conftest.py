"""G05 task-acceptance 目录级 fixtures（不触碰 tests/conftest.py 共享面）。

registry/sid 生命周期模式复制自 tests/test_harness_kernel_e2e_scenarios.py
（蓝本不 import 私有 fixture；本目录自治）。
"""
from __future__ import annotations


import pytest


from .runner import TaskScenarioRunner, ensure_registry_injected


@pytest.fixture
def g05_registry():
    return ensure_registry_injected()


@pytest.fixture
def g05_runner(g05_registry) -> TaskScenarioRunner:
    # tmp_root 缺省 = ./tmp/g05-rasters（validate_data_path 允许区）
    return TaskScenarioRunner(g05_registry)
