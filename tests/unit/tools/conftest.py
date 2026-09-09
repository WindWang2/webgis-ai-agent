"""tests/unit/tools 共享 fixture —— dispatch 行为化测试的隔离环境。

这些测试经 ``registry.dispatch("tool_name", {...})`` 真实执行工具函数体
（app/lib/quality/behavioral.py 的 AST dispatch 证据来源）。dispatch 每次都会
写 tool_metrics JSONL —— 这里统一重定向到 tmp，避免污染仓库 logs/。
"""
import pytest

from app.services import tool_metrics


@pytest.fixture(autouse=True)
def _isolated_tool_metrics(tmp_path, monkeypatch):
    tool_metrics._wait_idle()
    monkeypatch.setattr(
        tool_metrics, "LOG_PATH", str(tmp_path / "tool_metrics.jsonl")
    )
    tool_metrics._reset_for_tests()
    yield
    tool_metrics._reset_for_tests()
