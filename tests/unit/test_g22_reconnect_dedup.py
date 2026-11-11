"""G22-P4 — 断线重连与副作用去重（A19 / A20）。

A19：重连不重复提交 mutation。三条权威链各自钉死：
  1. SSE 重订阅（Last-Event-ID）是纯重放 —— 既有 test_sse_resume/J7 锁定
     （本文件复跑其关键断言源函数；完整旅程在 journeys）。
  2. 客户端放弃重连后的**整报重发** = 新 turn；其 durable 副作用经
     ``build_execution_key(task_type, session, params)`` 幂等键去重 ——
     同参数同会话跨 turn 复用同一 job 行（submit 层语义，重发不重复入队）。
  3. MapSpec 写路径经 expected_revision CAS：重放旧 revision 的 mutation
     得到 superseded 拒绝，不二次应用（mapspec_store 既有测试族）。
A20：迟到 worker 回调被代际拒绝 —— turn 交接后，旧 turn 的回调 token
即使仍在 15min 时钟窗内，也被 live-turn 检查 409 拒绝且零执行。
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest


# ── A19-2：幂等键跨 turn 稳定（重发不重复入队的权威）───────────────────────


def test_a19_execution_key_stable_across_turns_and_orders():
    from app.services.jobs.submit import build_execution_key

    k1 = build_execution_key(
        "buffer_analysis", "sess-x",
        {"layer": "schools", "radius": 500},
    )
    # 同参数不同 dict 顺序（JSON 往返后的自然形态）→ 同键
    k2 = build_execution_key(
        "buffer_analysis", "sess-x",
        {"radius": 500, "layer": "schools"},
    )
    assert k1 == k2
    # turn/toolCallId 不参与键 → 新 turn 重发同参数复用同一行
    assert "turn" not in k1 and "tc-" not in k1
    # 不同会话 → 不同键（A21 隔离的另一面）
    assert k1 != build_execution_key(
        "buffer_analysis", "sess-y", {"layer": "schools", "radius": 500})
    # 不同参数 → 不同键
    assert k1 != build_execution_key(
        "buffer_analysis", "sess-x", {"layer": "schools", "radius": 1000})


def test_a19_execution_key_queue_independent():
    """队列是路由事实不进幂等键（换路由不换节点身份）——重连后经不同队列
    重发仍命中同一行。"""
    from app.services.jobs.submit import build_execution_key

    key = build_execution_key("t", "s", {"a": 1})
    assert "queue" not in key and "celery" not in key


# ── A20：迟到 worker 回调的代际拒绝（409 · 零执行）────────────────────────


@pytest.mark.asyncio
async def test_a20_late_callback_after_handoff_rejected_409(monkeypatch):
    """turn1 交接给 turn2 后，turn1 的回调 token（时钟窗内仍签名有效）必须
    被 live-turn 检查拒绝：409 + 不执行任何工具。"""
    os.environ["WEBGIS_BRIDGE_SECRET"] = "test-secret-a20"
    import app.agent_pi_bridge as bridge_mod
    from app.api.routes.pi_tools import router
    from app.services.chat.pi_turn_context import (
        issue_turn_token,
        pi_turn_registry,
    )

    executed: list[str] = []

    from app.agent_pi_bridge import PiToolResponse

    async def _fake_dispatch(request):
        executed.append(request.name)
        return PiToolResponse(
            toolCallId=request.toolCallId, isError=False,
            content=[{"type": "text", "text": "ok"}],
        )

    import app.api.routes.pi_tools as pi_tools_routes
    monkeypatch.setattr(pi_tools_routes, "dispatch_tool", _fake_dispatch)

    sid = "sess-a20"
    token1 = issue_turn_token("test-secret-a20", sid, "turn-1")

    # 交接：turn1 注销、turn2 注册（本地注册表视角）
    await pi_turn_registry.register_turn(sid, "turn-2")

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/pi-tools/execute",
            json={
                "name": "webgis_execute",
                "toolCallId": "tc-late",
                "arguments": {"toolName": "query_map_features",
                              "arguments": {"location": [116.0, 39.0]}},
                "turnToken": token1,
            },
            headers={"X-Pi-Bridge-Secret": "test-secret-a20"},
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "TURN_CONTEXT_INACTIVE"
    assert executed == [], "迟到回调绝不可执行工具（零副作用）"

    # 对照：turn2 自己的 token 放行（live-turn 语义未被破坏）
    token2 = issue_turn_token("test-secret-a20", sid, "turn-2")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp2 = await client.post(
            "/pi-tools/execute",
            json={
                "name": "webgis_execute",
                "toolCallId": "tc-live",
                "arguments": {"toolName": "query_map_features",
                              "arguments": {"location": [116.0, 39.0]}},
                "turnToken": token2,
            },
            headers={"X-Pi-Bridge-Secret": "test-secret-a20"},
        )
    assert resp2.status_code == 200
    assert executed == ["webgis_execute"]

    await pi_turn_registry.unregister_turn(sid, "turn-2")


@pytest.mark.asyncio
async def test_a20_registry_prefix_values_do_not_false_positive(monkeypatch):
    """实例前缀值（G22-P3 新格式）不破坏 is_active 判定：本/他实例写入的
    「instance:turn」值按 turn 段判定；本实例残留 key 被清扫后不再活跃。"""
    from app.services.chat.pi_turn_context import PiTurnRegistry

    class _FakeRedis:
        def __init__(self):
            self.store: dict[str, str] = {}

        async def set(self, key, value, ex=None):
            self.store[key] = value

        async def get(self, key):
            return self.store.get(key)

        async def delete(self, key):
            self.store.pop(key, None)

        async def eval(self, script, numkeys, key, *argv):
            if self.store.get(key) in argv:
                self.store.pop(key, None)
                return 1
            return 0

        def scan_iter(self, match=None, count=None):
            import fnmatch

            async def _gen():
                for k in list(self.store):
                    if match is None or fnmatch.fnmatch(k, match):
                        yield k
            return _gen()

    reg = PiTurnRegistry()
    fake = _FakeRedis()
    monkeypatch.setattr(reg, "_get_redis_client", lambda: fake)
    monkeypatch.setattr(PiTurnRegistry, "_instance_id",
                        staticmethod(lambda: "pod-me"))

    # 「上个进程」的残留登记（同实例前缀）——重启后必须不再视为活跃
    fake.store["webgis:pi:active_turn:s-stale"] = "pod-me:turn-old"
    assert await reg.is_active("s-stale", "turn-old") is True, (
        "清扫前的残留 key 仍按 turn 段判定（跨 pod 语义兼容）"
    )
    await reg.sweep_own_keys(force=True)
    assert await reg.is_active("s-stale", "turn-old") is False, (
        "启动清扫后，本实例残留 key 不再使迟到的旧 token 通过 live-turn 检查"
    )

    # 他实例 live 登记不受清扫影响（A20 与多 pod 共存）
    fake.store["webgis:pi:active_turn:s-peer"] = "pod-peer:turn-live"
    await reg.sweep_own_keys(force=True)
    assert await reg.is_active("s-peer", "turn-live") is True
