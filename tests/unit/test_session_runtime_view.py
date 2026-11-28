"""WP4（GOAL-20261127-01）unit：GET /chat/sessions/{id}/runtime 聚合投影。

覆盖：账本 turn/budget 投影、健康表白名单投影、vendor session_stats 对账块
（可达 / 不可达诚实降级）、owner 归属走既有依赖（不重复实现）。
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import Base
from app.services.chat.model_runtime.usage_ledger import (
    TurnUsageCollector,
    persist_turn_records,
)


@pytest_asyncio.fixture
async def db_factory(tmp_path, monkeypatch):
    from app.core import database as db_mod

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=eng, expire_on_commit=False)

    import contextlib

    @contextlib.asynccontextmanager
    async def _patched():
        async with fac() as db:
            yield db

    monkeypatch.setattr(db_mod, "async_db_session", _patched)
    yield fac
    await eng.dispose()


def _message_end_event(model="m-base", usage=None):
    return {
        "type": "message_end",
        "message": {
            "role": "assistant", "provider": "webgis", "model": model,
            "usage": usage or {"input": 12, "output": 8},
            "stopReason": "stop",
        },
    }


class _StubRpc:
    def __init__(self, fail=False, payload=None):
        self.fail = fail
        self.payload = payload or {"turns": 3, "totalTokens": {"input": 999}}

    async def request(self, cmd, data=None):
        if self.fail:
            raise RuntimeError("rpc down")
        return self.payload


@pytest.mark.asyncio
async def test_runtime_projection_full(db_factory, monkeypatch, clean_health):
    collector = TurnUsageCollector("s-rt", "t-rt", "run")
    collector.observe(_message_end_event())
    await persist_turn_records(
        collector,
        provider_retry_attempts=2,
        route_reason_codes="primary_cooldown:30s",
        last_provider_failure_kind="rate_limit",
        finalize=True,
    )

    from app.api.routes.session_model_views import get_session_runtime

    class _StubBridge:
        # R1-P1-1：worker 绑定/活动会话字段（对账块仅在本会话绑定时可用）
        _bound_gis_session_id = "s-rt"
        _active_turn_sid = None

        class rpc:  # noqa: N801
            @staticmethod
            async def request(cmd, data=None):
                assert cmd == "get_session_stats"
                return {"totalMessages": 3, "toolCalls": 1,
                        "tokens": {"input": 999, "output": 5},
                        "sessionFile": "/srv/x.jsonl", "sessionId": "v-1",
                        "unknown_vendor_key": {"deep": {"nested": 1}}}

    async def _fake_bridge(session_id=None, extension_paths=None):
        return _StubBridge()

    import app.agent_pi_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "get_pi_bridge", _fake_bridge)
    clean_health.record_failure("webgis", "m-base", kind="rate_limit")

    async with db_factory() as db:
        resp = await get_session_runtime("s-rt", _conv=None, db=db)
    payload = json.loads(resp.model_dump_json())
    assert payload["session_id"] == "s-rt"
    turn = payload["turns"][-1]
    assert turn["provider_retry_attempts"] == 2
    assert turn["route_reason_codes"] == ["primary_cooldown:30s"]
    assert turn["last_provider_failure_kind"] == "rate_limit"
    assert payload["vendor_session_stats"]["available"] is True
    stats = payload["vendor_session_stats"]["stats"]
    assert stats["totalMessages"] == 3
    # R1-P2-2：路径/内部 id/白名单外字段都不出 API 面
    assert "sessionFile" not in stats and "sessionId" not in stats
    assert "unknown_vendor_key" not in stats
    assert "webgis/m-base" in payload["provider_health"]
    health_entry = payload["provider_health"]["webgis/m-base"]
    assert set(health_entry) <= {
        "available", "cooldown_remaining_s", "consecutive_failures",
        "last_failure_kind", "rate_limited_recently", "capability_mismatch",
        "last_latency_bucket", "total_success", "total_failure",
    }
    assert payload["budget"]["cap"] is None  # 无预算控制如实 unlimited


@pytest.mark.asyncio
async def test_runtime_projection_vendor_unavailable_degrades_honestly(
    db_factory, monkeypatch, clean_health,
):
    collector = TurnUsageCollector("s-rt2", "t-rt2", "run")
    collector.observe(_message_end_event())
    await persist_turn_records(collector, finalize=True)

    from app.api.routes.session_model_views import get_session_runtime

    async def _fake_bridge(session_id=None, extension_paths=None):
        raise RuntimeError("bridge pool down")

    import app.agent_pi_bridge as bridge_mod

    monkeypatch.setattr(bridge_mod, "get_pi_bridge", _fake_bridge)
    async with db_factory() as db:
        resp = await get_session_runtime("s-rt2", _conv=None, db=db)
    payload = resp.model_dump()
    assert payload["vendor_session_stats"]["available"] is False
    assert payload["vendor_session_stats"]["reason"]  # 诚实原因码
    assert payload["turns"][-1]["turn_id"] == "t-rt2"


@pytest.mark.asyncio
async def test_runtime_vendor_stats_rejects_unbound_or_foreign_session(
    db_factory, monkeypatch, clean_health,
):
    """R1-P1-1：vendor stats 是 worker 当前绑定会话的统计 —— 绑定不匹配/
    他人会话在飞时必须拒答，绝不把别的 owner 会话用量当对账块返回。"""
    from types import SimpleNamespace

    from app.api.routes.session_model_views import get_session_runtime

    class _StubBridge:
        def __init__(self, bound, active):
            self._bound_gis_session_id = bound
            self._active_turn_sid = active

        class rpc:  # noqa: N801
            called = False

            @staticmethod
            async def request(cmd, data=None):
                type(self).called = True  # noqa: F841 — 不应被调到
                return {"totalMessages": 99}

    import app.agent_pi_bridge as bridge_mod

    cases = [
        ("foreign_bound", _StubBridge("someone-else", None), "session_not_bound"),
        ("worker_busy", _StubBridge("s-rt3", "other-active"), "worker_busy"),
    ]
    for name, bridge, reason in cases:
        async def _fake(session_id=None, extension_paths=None, _b=bridge):
            return _b

        monkeypatch.setattr(bridge_mod, "get_pi_bridge", _fake)
        async with db_factory() as db:
            resp = await get_session_runtime("s-rt3", _conv=None, db=db)
        payload = resp.model_dump()
        assert payload["vendor_session_stats"] == {
            "available": False, "reason": reason,
        }, (name, payload["vendor_session_stats"])


@pytest.mark.asyncio
async def test_runtime_vendor_stats_whitelisted_projection(
    db_factory, monkeypatch, clean_health,
):
    """R1-P2-2：sessionFile/sessionId 不出 API 面；数值字段保留。"""
    from app.api.routes.session_model_views import get_session_runtime

    class _StubBridge:
        _bound_gis_session_id = "s-rt4"
        _active_turn_sid = None

        class rpc:  # noqa: N801
            @staticmethod
            async def request(cmd, data=None):
                return {
                    "sessionFile": "/srv/secret/path/session.jsonl",
                    "sessionId": "vendor-internal-id",
                    "totalMessages": 7,
                    "tokens": {"input": 10, "output": 5},
                    "toolCalls": 2,
                }

    import app.agent_pi_bridge as bridge_mod

    async def _fake(session_id=None, extension_paths=None):
        return _StubBridge()

    monkeypatch.setattr(bridge_mod, "get_pi_bridge", _fake)
    async with db_factory() as db:
        resp = await get_session_runtime("s-rt4", _conv=None, db=db)
    stats = resp.model_dump()["vendor_session_stats"]["stats"]
    assert "sessionFile" not in stats and "sessionId" not in stats
    assert stats["totalMessages"] == 7
    assert stats["tokens"] == {"input": 10, "output": 5}


@pytest.fixture
def clean_health():
    from app.services.chat.model_runtime.health import get_llm_provider_health

    health = get_llm_provider_health()
    health.reset()
    yield health
    health.reset()
