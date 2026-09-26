"""workflow_runtime 测试进程隔离（review P2-7）。

默认关闭 workflow 面 governor（GIS_WORKFLOW_GOVERNOR=0）：f08 之外的
既有测试行为与 F08 之前完全一致，且真实 governor 单例（manifest/背压/
RetryBudget）不会跨测试残留。需要 governor link 的测试在各自文件里用
monkeypatch.setenv("GIS_WORKFLOW_GOVERNOR", "1") 显式打开。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _governor_surface_off(monkeypatch):
    monkeypatch.setenv("GIS_WORKFLOW_GOVERNOR", "0")
    yield
