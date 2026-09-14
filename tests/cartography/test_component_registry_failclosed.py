"""Q023（qc-loop round 3）回归锁：组件校验器内部异常必须 fail-closed 出 issue。

历史缺陷：``_validate_inner`` 把整个校验体（含 renderer 支持矩阵对账）
包在 ``except Exception: pass`` 里 —— 校验器自身出错时静默返回
「0 issues」，违反 ``validate()`` 注释明示的 fail-closed 契约
（"校验器自身异常转为 issue，绝不静默返回 0 issues"）。
"""
import pytest

pytestmark = pytest.mark.cartography

from app.lib.cartography.component_registry import get_component_registry


def test_validator_internal_failure_becomes_issue_not_silent_pass(
        monkeypatch):
    def _boom():
        raise RuntimeError("renderer registry unavailable (simulated)")

    monkeypatch.setattr(
        "app.lib.cartography.component_renderers.get_component_renderer_registry",
        _boom,
    )
    issues = get_component_registry().validate()
    assert issues, "fail-closed: internal failure must surface as an issue"
    assert any("validate internals raised" in i for i in issues)


def test_healthy_registry_validates_clean(monkeypatch):
    monkeypatch.undo()
    assert get_component_registry().validate() == []
