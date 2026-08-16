"""地图动作 ACK 闭环（V3）单元测试：session 存储 ACK 日志 + map-action-ack 端点。

覆盖设计 §3/§4：
- 双后端协议对齐（Memory + Redis-via-fakeredis，同 test_session_store_contract 的注入缝）：
  追加/读取回环、action_id 幂等（首达终态获胜）、200 上限淘汰最旧、session 隔离、
  clear_session 清理、Redis TTL、并发写（WATCH 重试循环）。
- 端点：schema 校验边界（422）、所有权拒绝（404，含 owner_token 不匹配）、
  重复 ACK 幂等、乱序 ACK、批量 >50 拒绝、单条 >16KB 拒绝、per-IP 限速（429）。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.routes import chat as _chat_mod
from app.api.routes.chat import MapActionAck, router
from app.services.session_data import MAX_MAP_ACTION_EVENTS, MemorySessionStore
from app.services.session_data_redis import RedisSessionStore


def _redis_store_factory():
    """RedisSessionStore backed by in-process fakeredis（同 test_session_store_contract）。"""
    import fakeredis.aioredis

    return RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )


STORE_FACTORIES = [MemorySessionStore, _redis_store_factory]


def _ack(action_id: str, status: str = "succeeded", **overrides) -> dict:
    event = {
        "action_id": action_id,
        "command": "fly_to",
        "status": status,
        "error": "",
        "started_at": "2026-08-11T00:00:00Z",
        "finished_at": "2026-08-11T00:00:01Z",
        "duration_ms": 12.5,
    }
    event.update(overrides)
    return event


# ─── 存储层契约（双后端参数化，语义必须一致 —— ADR-0025/0035） ─────────────


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_map_action_append_get_roundtrip(store_factory):
    """追加后按到达顺序读回；乱序到达（先 B 后 A）保持到达顺序。"""
    store = store_factory()
    sid = "ack_sess_roundtrip"

    assert await store.append_map_action_event(sid, _ack("ma-b")) is True
    assert await store.append_map_action_event(sid, _ack("ma-a", status="failed", error="timeout")) is True

    events = await store.get_map_action_events(sid)
    assert [e["action_id"] for e in events] == ["ma-b", "ma-a"]
    assert events[1]["status"] == "failed"
    assert events[1]["error"] == "timeout"


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_map_action_duplicate_first_terminal_wins(store_factory):
    """同一 action_id 重复上报：第二次返回 False，已存终态不被覆盖。"""
    store = store_factory()
    sid = "ack_sess_dup"

    assert await store.append_map_action_event(sid, _ack("ma-1")) is True
    assert await store.append_map_action_event(sid, _ack("ma-1", status="failed", error="late")) is False

    events = await store.get_map_action_events(sid)
    assert len(events) == 1
    assert events[0]["status"] == "succeeded"


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_map_action_missing_action_id_rejected(store_factory):
    """缺 action_id 的事件不入库。"""
    store = store_factory()
    assert await store.append_map_action_event("ack_sess_noid", {"status": "succeeded"}) is False
    assert await store.append_map_action_event("ack_sess_noid", {"action_id": ""}) is False
    assert await store.get_map_action_events("ack_sess_noid") == []


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_map_action_session_isolation(store_factory):
    """session A/B 的 ACK 日志互相不可见；同一 action_id 可分别入库。"""
    store = store_factory()
    assert await store.append_map_action_event("ack_sess_a", _ack("ma-1")) is True
    assert await store.append_map_action_event("ack_sess_b", _ack("ma-1", status="cancelled")) is True

    events_a = await store.get_map_action_events("ack_sess_a")
    events_b = await store.get_map_action_events("ack_sess_b")
    assert [e["status"] for e in events_a] == ["succeeded"]
    assert [e["status"] for e in events_b] == ["cancelled"]


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_map_action_cap_evicts_oldest(store_factory):
    """超过 MAX_MAP_ACTION_EVENTS 按插入序淘汰最旧；被淘汰的 action_id 可重新入库。"""
    store = store_factory()
    sid = "ack_sess_cap"
    total = MAX_MAP_ACTION_EVENTS + 5
    for i in range(total):
        assert await store.append_map_action_event(sid, _ack(f"ma-{i:03d}")) is True

    events = await store.get_map_action_events(sid)
    ids = [e["action_id"] for e in events]
    assert len(ids) == MAX_MAP_ACTION_EVENTS
    assert ids == [f"ma-{i:03d}" for i in range(5, total)]

    # 已淘汰的 action_id 不再是"重复"，可重新入库（同时再淘汰当前最旧一条）
    assert await store.append_map_action_event(sid, _ack("ma-000")) is True
    ids_after = [e["action_id"] for e in await store.get_map_action_events(sid)]
    assert len(ids_after) == MAX_MAP_ACTION_EVENTS
    assert ids_after[-1] == "ma-000"
    assert "ma-005" not in ids_after


@pytest.mark.parametrize("store_factory", STORE_FACTORIES)
@pytest.mark.asyncio
async def test_map_action_clear_session(store_factory):
    """clear_session 连同 ACK 日志一起清理。"""
    store = store_factory()
    sid = "ack_sess_clear"
    await store.append_map_action_event(sid, _ack("ma-1"))
    await store.clear_session(sid)
    assert await store.get_map_action_events(sid) == []


@pytest.mark.asyncio
async def test_map_action_redis_ttl_set():
    """Redis 后端：ACK hash/order 键带 TTL（同 STATE_TTL 语义）。"""
    store = _redis_store_factory()
    sid = "ack_sess_ttl"
    await store.append_map_action_event(sid, _ack("ma-1"))
    assert await store._r.ttl(store._map_actions_key(sid)) > 0
    assert await store._r.ttl(store._map_actions_order_key(sid)) > 0


@pytest.mark.asyncio
async def test_map_action_redis_arrival_order_same_tick():
    """同一时钟 tick 内连续写入：zset score 严格递增（单调 tie-break），保持到达序。

    回归测试：若 score 相同，Redis 按 member 字典序排序，会返回 ["ma-a", "ma-b"]
    而破坏与 memory 后端的"按到达顺序读回"协议对齐。固定 time.time() 复现。
    """
    import types

    import app.services.session_data_redis as redis_mod

    store = _redis_store_factory()
    fake_time = types.SimpleNamespace(time=lambda: 100.0)
    sid = "ack_sess_sametick"
    with patch.object(redis_mod, "time", fake_time):
        assert await store.append_map_action_event(sid, _ack("ma-b")) is True
        assert await store.append_map_action_event(sid, _ack("ma-a")) is True
    ids = [e["action_id"] for e in await store.get_map_action_events(sid)]
    assert ids == ["ma-b", "ma-a"]


@pytest.mark.asyncio
async def test_map_action_redis_concurrent_duplicate_writers(monkeypatch):
    """并发写同一 action_id（asyncio.gather）→ 恰好 1 个成功，其余被判重复。

    fakeredis 的 async 命令路径在单事件循环内同步完成（不 yield），gather 下
    事务实际是串行的 —— 为真实命中 WATCH/MULTI 重试循环，这里在 execute 前
    强制让出事件循环，使多个事务在 watch 之后、execute 之前交错：后提交者触发
    WatchError → 重试 → hexists 判重返回 False。回归防护：若 WATCH 失效，
    并发事务会同时通过判重（sum>1）或后写覆盖先写（首达终态被篡改）。
    """
    import redis.asyncio.client as rclient

    store = _redis_store_factory()
    sid = "ack_sess_concurrent"
    n = 8

    orig_execute = rclient.Pipeline.execute

    async def _yielding_execute(self, *args, **kwargs):
        # 让出事件循环：所有事务都停在 execute 前 → watch 之后真实交错
        await asyncio.sleep(0)
        return await orig_execute(self, *args, **kwargs)

    monkeypatch.setattr(rclient.Pipeline, "execute", _yielding_execute)
    event = _ack("ma-concurrent")
    results = await asyncio.gather(
        *[store.append_map_action_event(sid, dict(event)) for _ in range(n)]
    )
    assert sum(results) == 1
    events = await store.get_map_action_events(sid)
    assert len(events) == 1
    assert events[0]["action_id"] == "ma-concurrent"
    assert events[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_map_action_redis_concurrent_distinct_writers(monkeypatch):
    """并发写 N 个不同 action_id → 全部接受，读回完整（WATCH 重试恢复合法写）。

    与重复写测试一样在 execute 前强制让出事件循环制造真实竞争：后提交者触发
    WatchError → 重试 → 重新判重（不同 id 不重复）→ 提交成功。若 WATCH 重试
    失效（WatchError 落到 RedisError→drop），后写者会被丢弃（accepted < N）——
    本测试即重试恢复的判别用例（2 个写者，无 3 次上限耗尽的竞争）。
    """
    import redis.asyncio.client as rclient

    store = _redis_store_factory()
    sid = "ack_sess_concurrent_distinct"
    n = 2

    orig_execute = rclient.Pipeline.execute

    async def _yielding_execute(self, *args, **kwargs):
        await asyncio.sleep(0)
        return await orig_execute(self, *args, **kwargs)

    monkeypatch.setattr(rclient.Pipeline, "execute", _yielding_execute)
    results = await asyncio.gather(*[
        store.append_map_action_event(sid, _ack(f"ma-c{i:02d}")) for i in range(n)
    ])
    assert results == [True] * n
    events = await store.get_map_action_events(sid)
    assert len(events) == n
    assert sorted(e["action_id"] for e in events) == [f"ma-c{i:02d}" for i in range(n)]


# ─── schema 校验（直接 model 层） ──────────────────────────────────────────


def test_map_action_ack_defaults():
    """可选字段默认值：error/started_at/finished_at 空串，其余 None。"""
    ack = MapActionAck(action_id="ma-1", command="fly_to", status="succeeded")
    assert ack.error == ""
    assert ack.started_at == ""
    assert ack.finished_at == ""
    assert ack.duration_ms is None
    assert ack.correlation is None
    assert ack.requested is None
    assert ack.actual is None


def test_map_action_ack_serialized_size_cap():
    """单条 ACK 序列化 >16KB -> ValidationError（requested/actual 无界 dict 的兜底）。"""
    with pytest.raises(ValidationError):
        MapActionAck(
            action_id="ma-big",
            command="fly_to",
            status="succeeded",
            requested={"blob": "x" * 20000},
        )
    # 16KB 以内正常通过
    MapActionAck(
        action_id="ma-ok",
        command="fly_to",
        status="succeeded",
        requested={"blob": "x" * 16000},
    )


# ─── 端点（鉴权 + 幂等 + 422 边界） ────────────────────────────────────────


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _pass_ownership():
    """让 require_owned_session 通过（跨租户 e2e 由 test_cross_tenant_isolation 覆盖）。

    #525: the ownership guard now uses the metadata-only get_session_meta."""
    return patch.object(
        _chat_mod.AsyncHistoryService, "get_session_meta", AsyncMock(return_value=MagicMock())
    )


def _url(session_id: str) -> str:
    return f"/api/v1/chat/sessions/{session_id}/map-action-ack"


def _make_limiter(allowed: bool):
    """构造一个按固定结果应答的限速器 stub（同 test_token_refresh 的做法）。"""
    async def _is_allowed(key, max_requests, window_seconds):
        return allowed
    limiter = MagicMock()
    limiter.is_allowed = _is_allowed
    return limiter


@pytest.fixture(autouse=True)
def _stub_ack_rate_limiter(monkeypatch):
    """ack 路由默认限速放行 —— 真实 get_rate_limiter() 首次调用会尝试连 Redis
    （单测禁止网络）。限速本身由 test_ack_endpoint_rate_limited 单独覆盖。"""
    async def _get_stub():
        return _make_limiter(True)
    monkeypatch.setattr(_chat_mod, "get_rate_limiter", _get_stub)


@pytest.mark.asyncio
async def test_ack_endpoint_happy_path(client):
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        resp = await client.post(
            _url("sess-a"),
            json={"acks": [_ack("ma-1"), _ack("ma-2", status="failed", error="timeout")]},
        )
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2, "duplicates": 0}
    events = await store.get_map_action_events("sess-a")
    assert [e["action_id"] for e in events] == ["ma-1", "ma-2"]


@pytest.mark.asyncio
async def test_ack_endpoint_duplicate_idempotent(client):
    """重复 ACK（重连/重试重发）：第二次 duplicates=1，已存终态不变。"""
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        r1 = await client.post(_url("sess-a"), json={"acks": [_ack("ma-1")]})
        r2 = await client.post(_url("sess-a"), json={"acks": [_ack("ma-1", status="failed", error="late")]})
    assert r1.status_code == 200 and r1.json() == {"accepted": 1, "duplicates": 0}
    assert r2.status_code == 200 and r2.json() == {"accepted": 0, "duplicates": 1}
    events = await store.get_map_action_events("sess-a")
    assert len(events) == 1
    assert events[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_ack_endpoint_out_of_order(client):
    """乱序到达的 ACK（先发后至）全部接受，按到达顺序存储。"""
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        r1 = await client.post(_url("sess-a"), json={"acks": [_ack("ma-2")]})
        r2 = await client.post(_url("sess-a"), json={"acks": [_ack("ma-1")]})
    assert r1.json() == {"accepted": 1, "duplicates": 0}
    assert r2.json() == {"accepted": 1, "duplicates": 0}
    events = await store.get_map_action_events("sess-a")
    assert [e["action_id"] for e in events] == ["ma-2", "ma-1"]


@pytest.mark.asyncio
async def test_ack_endpoint_session_isolation(client):
    """同一 action_id 在不同 session 各自入库，互不视为重复。"""
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        ra = await client.post(_url("sess-a"), json={"acks": [_ack("ma-1")]})
        rb = await client.post(_url("sess-b"), json={"acks": [_ack("ma-1", status="cancelled")]})
    assert ra.json() == {"accepted": 1, "duplicates": 0}
    assert rb.json() == {"accepted": 1, "duplicates": 0}
    assert (await store.get_map_action_events("sess-a"))[0]["status"] == "succeeded"
    assert (await store.get_map_action_events("sess-b"))[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_ack_endpoint_rejects_foreign_session(client):
    """所有权校验：get_session_meta 返回 None（不存在或非本人/owner_token 不匹配）-> 404。"""
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), patch.object(
        _chat_mod.AsyncHistoryService, "get_session_meta", AsyncMock(return_value=None)
    ):
        resp = await client.post(_url("sess-foreign"), json={"acks": [_ack("ma-1")]})
    assert resp.status_code == 404
    assert await store.get_map_action_events("sess-foreign") == []


@pytest.mark.asyncio
async def test_ack_endpoint_owner_token_mismatch_rejected(client):
    """SEC-08 匿名会话 owner_token 不匹配（会话存在但 token 错误）→ 404 拒绝写入，
    与"会话不存在"同样处理（不泄漏存在性）；正确 token 放行。"""
    store = MemorySessionStore()
    conv = MagicMock()

    async def _get_session_meta(self_or_db, session_id, *, user_id=None, owner_token=None):
        # 模拟 AsyncHistoryService：仅携带匹配的 owner_token 才视为已授权
        return conv if owner_token == "correct-token" else None

    with patch("app.services.session_data.session_data_manager", new=store), patch.object(
        _chat_mod.AsyncHistoryService, "get_session_meta", _get_session_meta
    ):
        wrong = await client.post(
            _url("sess-owner"),
            json={"acks": [_ack("ma-1")]},
            headers={"X-Session-Token": "wrong-token"},
        )
        assert wrong.status_code == 404
        assert await store.get_map_action_events("sess-owner") == []

        right = await client.post(
            _url("sess-owner"),
            json={"acks": [_ack("ma-1")]},
            headers={"X-Session-Token": "correct-token"},
        )
        assert right.status_code == 200
        assert right.json() == {"accepted": 1, "duplicates": 0}
        assert (await store.get_map_action_events("sess-owner"))[0]["action_id"] == "ma-1"


@pytest.mark.asyncio
async def test_ack_endpoint_rate_limited(client, monkeypatch):
    """per-IP 限速：limiter 拒绝 → 429，ACK 不入库。"""
    store = MemorySessionStore()

    async def _get_stub():
        return _make_limiter(False)

    monkeypatch.setattr(_chat_mod, "get_rate_limiter", _get_stub)
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        resp = await client.post(_url("sess-a"), json={"acks": [_ack("ma-1")]})
    assert resp.status_code == 429
    assert await store.get_map_action_events("sess-a") == []


@pytest.mark.asyncio
async def test_ack_endpoint_schema_edges(client):
    """schema 边界：空/超长 action_id、超长 command、非法 status、超长 error、
    负 duration、超长 started_at/finished_at、Infinity duration。"""
    store = MemorySessionStore()
    base = _ack("ma-1")
    bad_payloads = [
        {**base, "action_id": ""},
        {**base, "action_id": "x" * 65},
        {**base, "command": "c" * 65},
        {**base, "status": "exploded"},
        {**base, "error": "e" * 501},
        {**base, "duration_ms": -1},
        {**base, "started_at": "s" * 65},
        {**base, "finished_at": "f" * 65},
    ]
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        for bad in bad_payloads:
            resp = await client.post(_url("sess-a"), json={"acks": [bad]})
            assert resp.status_code == 422, bad
    assert await store.get_map_action_events("sess-a") == []


def test_map_action_ack_rejects_non_finite_duration():
    """duration_ms 拒绝 Infinity/NaN（ge=0, le=1e15）：非有限值不得入库。

    json.loads("1e999")/Infinity 会解析成 inf —— 在模型层拦截（HTTP 层 FastAPI
    的 422 响应序列化无法内嵌 inf 输入值，故端点级 inf 用例不做）。"""
    with pytest.raises(ValidationError):
        MapActionAck(action_id="ma-inf", command="fly_to", status="succeeded", duration_ms=float("inf"))
    with pytest.raises(ValidationError):
        MapActionAck(action_id="ma-nan", command="fly_to", status="succeeded", duration_ms=float("nan"))
    with pytest.raises(ValidationError):
        MapActionAck(action_id="ma-big", command="fly_to", status="succeeded", duration_ms=1e16)
    # 边界内（le=1e15）正常接受
    MapActionAck(action_id="ma-ok", command="fly_to", status="succeeded", duration_ms=1e15)


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "superseded"])
@pytest.mark.asyncio
async def test_ack_endpoint_all_terminal_statuses(client, status):
    """四种终态均为合法 status。"""
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        resp = await client.post(_url("sess-a"), json={"acks": [_ack("ma-1", status=status)]})
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 1, "duplicates": 0}


@pytest.mark.asyncio
async def test_ack_endpoint_batch_over_50_rejected(client):
    """单批 >50 条 -> 422。"""
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        resp = await client.post(
            _url("sess-a"), json={"acks": [_ack(f"ma-{i:02d}") for i in range(51)]}
        )
        assert resp.status_code == 422
        ok = await client.post(
            _url("sess-a"), json={"acks": [_ack(f"ma-{i:02d}") for i in range(50)]}
        )
        assert ok.status_code == 200
        assert ok.json() == {"accepted": 50, "duplicates": 0}


@pytest.mark.asyncio
async def test_ack_endpoint_over_16kb_rejected(client):
    """单条 ACK 序列化 >16KB -> 422；以内正常接受。"""
    store = MemorySessionStore()
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        big = await client.post(
            _url("sess-a"), json={"acks": [_ack("ma-big", requested={"blob": "x" * 20000})]}
        )
        assert big.status_code == 422
        ok = await client.post(
            _url("sess-a"), json={"acks": [_ack("ma-ok", requested={"blob": "x" * 16000})]}
        )
        assert ok.status_code == 200
        assert ok.json() == {"accepted": 1, "duplicates": 0}


@pytest.mark.asyncio
async def test_ack_endpoint_optional_fields(client):
    """duration_ms=null / 0、correlation/requested/actual 缺省均合法；duration_ms=0 接受。"""
    store = MemorySessionStore()
    ack = _ack("ma-1", duration_ms=None)
    ack_zero = _ack("ma-2", duration_ms=0)
    with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
        resp = await client.post(_url("sess-a"), json={"acks": [ack, ack_zero]})
    assert resp.status_code == 200
    assert resp.json() == {"accepted": 2, "duplicates": 0}
    events = await store.get_map_action_events("sess-a")
    assert "correlation" not in events[0]  # None 字段不落库（exclude_none）
    assert events[1]["duration_ms"] == 0


@pytest.mark.asyncio
async def test_late_ack_cannot_resurrect_deleted_session(client):
    from app.agent_pi_bridge import (
        clear_cartographic_session_state,
        restore_cartographic_session_state,
    )

    store = MemorySessionStore()
    sid = "sess-deleted-carto"
    clear_cartographic_session_state(sid)
    try:
        with patch("app.services.session_data.session_data_manager", new=store), _pass_ownership():
            resp = await client.post(_url(sid), json={"acks": [_ack("ma-late")]})
        assert resp.status_code == 410
        assert await store.get_map_action_events(sid) == []
    finally:
        restore_cartographic_session_state(sid)
