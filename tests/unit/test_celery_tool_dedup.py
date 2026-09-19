"""#1438 回归：run_sync_tool_isolated 的 acks_late 重投守卫。

语义：同 task_id 的重复投递（broker 在 worker 死亡后重发，UUID 不变）
必须放弃执行；新 task_id（合法的重复调用）不受影响；Redis 不可用时
fail-open 执行。
"""
from unittest import mock

import pytest

from app.services.spatial_tasks import _claim_tool_delivery, run_sync_tool_isolated


class _FakeRedis:
    """最小 SET NX 语义桩（不引入 fakeredis 依赖）。"""

    def __init__(self):
        self.store: dict = {}

    def set(self, key, value, nx=False, ex=None):  # noqa: ARG002
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


def test_claim_allows_first_delivery_and_blocks_redelivery(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(
        "redis.from_url", mock.Mock(return_value=fake), raising=False,
    )
    assert _claim_tool_delivery("task-a") is True
    assert _claim_tool_delivery("task-a") is False  # 同 UUID 重投 → 拒绝
    assert _claim_tool_delivery("task-b") is True  # 新任务（合法重复调用）→ 放行


def test_claim_fails_open_without_redis(monkeypatch):
    def _boom(*_a, **_k):
        raise ConnectionError("redis down")

    monkeypatch.setattr("redis.from_url", _boom, raising=False)
    assert _claim_tool_delivery("task-a") is True


def test_claim_empty_task_id_passes():
    assert _claim_tool_delivery("") is True


def test_task_body_raises_on_duplicate_delivery(monkeypatch):
    monkeypatch.setattr(
        "app.services.spatial_tasks._claim_tool_delivery",
        mock.Mock(return_value=False),
    )
    # task.run 已绑定任务实例（bind=True）；guard 已 mock，request.id 不参与
    with pytest.raises(RuntimeError, match="duplicate delivery suppressed"):
        run_sync_tool_isolated.run("buffer_analysis", {})


def test_task_body_executes_on_first_delivery(monkeypatch):
    monkeypatch.setattr(
        "app.services.spatial_tasks._claim_tool_delivery",
        mock.Mock(return_value=True),
    )
    monkeypatch.setattr(
        "app.services.spatial_tasks._worker_sync_tool",
        mock.Mock(return_value=lambda **kw: {"ok": True, "args": kw}),
    )
    out = run_sync_tool_isolated.run("buffer_analysis", {"distance": 100})
    assert out == {"ok": True, "args": {"distance": 100}}
