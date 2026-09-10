"""Workbench V6 server-side collaboration —— 后端测试。

覆盖（§15 必测矩阵的后端部分）：
- delta 纯函数管线（validate/apply：部分字段、级联删除、引用检查、Set-wins）
- presence（cap 32、TTL 过期清扫、心跳合并、leave）
- lease（acquire/renew/release、TTL 过期、client 上限、非法 key、release_all）
- bus（信封/seq/预算闸/local 扇出/监听器生命周期）
- 引擎：patch_workbench_delta 端到端、base_workbench_revision CAS、
  agent locked-layer 守卫（单发 + batch 路径）、`_rev` 盖章
- 门面挂钩：成功 mutation → bus doc/delta/op 事件（seq = mutation_revision）
- ws_collab 认证矩阵（JWT owned / unowned / 匿名 token / 错 token /
  legacy NULL / 交叉使用拒绝）+ hello/sync/ping 环回
- 跨连接扇出（两连接同会话收到同一事件；单扇出无双计）

全部在 USE_REDIS=false（conftest 钉扎）的进程内降级模式下运行 —— 这同时
就是降级路径的 oracle；Redis 路径语义由同款 Lua/键布局保证（CI 外验证）。
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import uuid as _uuid

_RUN = _uuid.uuid4().hex[:8]


def _sid(name: str) -> str:
    """每次 pytest 进程唯一的会话 id（落盘型内存 store 的跨运行隔离）。"""
    return f"{name}_{_RUN}"

from app.services.collab.bus import CollabBus, bound_payload, build_envelope
from app.services.collab.leases import LeaseRegistry, normalize_lock_key
from app.services.collab.presence import PresenceRegistry
from app.services.collab.delta import DeltaError, apply_delta, validate_delta
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    PatchWorkbenchDeltaIntent,
    RemoveLayerIntent,
    SetViewIntent,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
)


# ─── delta 纯函数 ────────────────────────────────────────────────────────────

def _doc():
    return {
        "version": 5,
        "groups": [
            {"id": "g1", "name": "A", "collapsed": False, "parentId": None},
            {"id": "g2", "name": "B", "collapsed": True, "parentId": "g1"},
        ],
        "membership": {"l1": "g2"},
        "lockedLayerIds": ["l1"],
        "mode": "compose",
    }


def test_delta_partial_field_patch_keeps_other_fields():
    out = apply_delta(_doc(), validate_delta({"setGroups": [{"id": "g2", "collapsed": False}]}))
    g2 = next(g for g in out["groups"] if g["id"] == "g2")
    assert g2["collapsed"] is False
    assert g2["name"] == "B"  # 未触碰字段不动
    assert g2["parentId"] == "g1"


def test_delta_create_requires_name_and_patches_partial():
    out = apply_delta(_doc(), validate_delta({"setGroups": [{"id": "g3", "name": "C", "parentId": "g2"}]}))
    assert [g["id"] for g in out["groups"]] == ["g1", "g2", "g3"]
    with pytest.raises(DeltaError):
        apply_delta(_doc(), validate_delta({"setGroups": [{"id": "g9"}]}))  # create 缺 name


def test_delta_remove_cascades_and_clears_membership():
    out = apply_delta(_doc(), validate_delta({"removeGroupIds": ["g1"]}))
    assert out["groups"] == []
    assert out["membership"] == {}  # 指向被移除组的成员清空


def test_delta_set_remove_conflict_rejected():
    with pytest.raises(DeltaError):
        apply_delta(_doc(), validate_delta({"setGroups": [{"id": "g1", "name": "X"}], "removeGroupIds": ["g1"]}))


def test_delta_membership_target_must_exist_and_set_wins_over_clear():
    with pytest.raises(DeltaError):
        apply_delta(_doc(), validate_delta({"membershipSet": [{"layerId": "l9", "groupId": "gX"}]}))
    out = apply_delta(_doc(), validate_delta({
        "membershipSet": [{"layerId": "l1", "groupId": "g1"}],
        "membershipClear": ["l1"],
    }))
    assert out["membership"] == {"l1": "g1"}  # Set 优先


def test_delta_locks_add_remove():
    out = apply_delta(_doc(), validate_delta({"locksAdd": ["l2"], "locksRemove": ["l1"]}))
    assert out["lockedLayerIds"] == ["l2"]


def test_delta_rejects_unknown_fields_and_empty():
    with pytest.raises(DeltaError):
        validate_delta({"mode": "explore"})  # mode 不在 delta 域
    with pytest.raises(DeltaError):
        validate_delta({})
    with pytest.raises(DeltaError):
        validate_delta([])


def test_delta_absolute_semantics_replay_idempotent():
    d = validate_delta({"setGroups": [{"id": "g2", "collapsed": False}], "membershipSet": [{"layerId": "l1", "groupId": "g1"}]})
    once = apply_delta(_doc(), d)
    twice = apply_delta(once, d)
    assert once == twice  # 重放幂等（revision 门控采纳的安全前提）


# ─── presence（进程内降级模式）───────────────────────────────────────────────

def test_presence_join_snapshot_cap_and_expiry():
    reg = PresenceRegistry(ttl_s=0)  # ttl=0 → 立即过期（确定性测试）
    assert reg._join_local("s1", "c1", {"label": "a"}, time.time()) is not None
    # 过期成员在快照中被惰性清扫
    assert reg.snapshot_local("s1") == []

    reg2 = PresenceRegistry()
    for i in range(32):
        assert reg2._join_local("s2", f"c{i}", {"label": f"u{i}"}, time.time()) is not None
    assert reg2._join_local("s2", "c32", {"label": "over"}, time.time()) is None  # cap 32
    snap = reg2.snapshot_local("s2")
    assert len(snap) == 32
    assert all("exp" not in p for p in snap)  # exp 不外泄


def test_presence_heartbeat_merges_sanitized_fields():
    reg = PresenceRegistry()
    reg._join_local("s1", "c1", {"label": "a"}, time.time())
    import asyncio

    async def _run():
        await reg.heartbeat("s1", "c1", {"editingLayerId": "l1", "evil": "x", "label": "b"})
    asyncio.run(_run())
    snap = reg.snapshot_local("s1")
    assert snap[0]["editingLayerId"] == "l1"
    assert "evil" not in snap[0]  # 白名单裁剪
    assert snap[0]["label"] == "b"


# ─── lease（进程内降级模式）──────────────────────────────────────────────────

def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_lease_acquire_renew_release_lifecycle():
    reg = LeaseRegistry()
    assert _run(reg.acquire("s1", "layer:l1", "c1"))["granted"] is True
    # 他人持有 → 拒绝并给 holder
    other = _run(reg.acquire("s1", "layer:l1", "c2"))
    assert other["granted"] is False and other["holder"]["client"] == "c1"
    # 自己重复 acquire = 幂等授予
    assert _run(reg.acquire("s1", "layer:l1", "c1"))["granted"] is True
    assert _run(reg.renew("s1", "layer:l1", "c1")) is True
    assert _run(reg.renew("s1", "layer:l1", "c2")) is False  # 仅持有人可续
    assert _run(reg.release("s1", "layer:l1", "c2")) is False  # 仅持有人可释
    assert _run(reg.release("s1", "layer:l1", "c1")) is True
    assert _run(reg.acquire("s1", "layer:l1", "c2"))["granted"] is True


def test_lease_ttl_expiry_frees_lock():
    reg = LeaseRegistry(ttl_s=0)
    assert _run(reg.acquire("s1", "group:g1", "c1"))["granted"] is True
    # ttl=0 → 立即过期：他人可获取（无永久孤儿锁）
    assert _run(reg.acquire("s1", "group:g1", "c2"))["granted"] is True


def test_lease_client_limit_and_invalid_key():
    reg = LeaseRegistry()
    for i in range(16):
        assert _run(reg.acquire("s1", f"layer:l{i}", "c1"))["granted"] is True
    assert _run(reg.acquire("s1", "layer:l99", "c1"))["reason"] == "client_limit"
    assert _run(reg.acquire("s1", "bogus", "c1"))["reason"] == "invalid_lock_key"
    assert normalize_lock_key("layer:x") == "layer:x"
    assert normalize_lock_key("group:y") == "group:y"
    assert normalize_lock_key("session:z") is None


def test_lease_release_all_for_client():
    reg = LeaseRegistry()
    _run(reg.acquire("s1", "layer:l1", "c1"))
    _run(reg.acquire("s1", "group:g1", "c1"))
    released = _run(reg.release_all("s1", "c1"))
    assert released == 2
    assert _run(reg.snapshot("s1")) == []


# ─── bus（进程内降级模式）────────────────────────────────────────────────────

def test_bus_envelope_seq_and_payload_bounds():
    env = build_envelope("doc", "s1", {"revision": 3}, seq=3)
    assert env["seq"] == 3 and env["kind"] == "doc" and env["v"] == 1
    transient = build_envelope("presence", "s1", {})
    assert isinstance(transient["seq"], int) and transient["seq"] > 10**15  # time_ns
    with pytest.raises(ValueError):
        build_envelope("bogus", "s1", {})
    huge = build_envelope("doc", "s1", {"revision": 1, "doc": {"blob": "x" * (200 * 1024)}}, seq=1)
    bounded = bound_payload(huge)
    assert bounded["data"]["truncated"] is True
    assert bounded["data"]["revision"] == 1
    assert len(json.dumps(bounded)) < 2048


def test_bus_local_fanout_and_unsubscribe():
    bus = CollabBus()
    got1, got2 = [], []

    async def _scenario():
        rm1 = await bus.add_local_listener("s1", got1.append)
        await bus.add_local_listener("s1", got2.append)
        await bus.publish("s1", "op", {"revision": 1}, seq=1)
        await bus.publish("s2", "op", {"revision": 2}, seq=2)  # 别的会话
        rm1()
        await bus.publish("s1", "op", {"revision": 3}, seq=3)

    import asyncio
    asyncio.run(_scenario())
    assert [e["data"]["revision"] for e in got1] == [1]  # 退订后不再收
    assert [e["data"]["revision"] for e in got2] == [1, 3]  # 仍订阅 s1：3 照收


# ─── 引擎：delta intent / base CAS / lock 守卫 / _rev 盖章 ──────────────────

def _valid_doc():
    return {
        "version": 5,
        "groups": [{"id": "wg-1", "name": "东部", "collapsed": False, "parentId": None}],
        "membership": {"layer-a": "wg-1"},
        "lockedLayerIds": ["layer-b"],
        "mode": "analyze",
    }


@pytest.mark.asyncio
async def test_engine_delta_applies_incrementally():
    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_delta_1")
    await engine.apply_mutation(sid, SetWorkbenchStateIntent(doc=_valid_doc()), origin="user", expected_revision=0)
    res = await engine.apply_mutation(
        sid,
        PatchWorkbenchDeltaIntent(delta={"setGroups": [{"id": "wg-2", "name": "子组", "parentId": "wg-1"}]}),
        origin="user", expected_revision=1,
    )
    assert res.is_error is False
    doc = res.mapspec["workbench"]
    assert [g["id"] for g in doc["groups"]] == ["wg-1", "wg-2"]
    assert doc["membership"] == {"layer-a": "wg-1"}  # delta 未触碰
    assert doc["_rev"] == 2  # `_rev` 盖章 = 本次 mutation_revision
    # 全量校验在结果级复核：造环被拒（400 语义 = is_error）
    bad = await engine.apply_mutation(
        sid,
        PatchWorkbenchDeltaIntent(delta={"setGroups": [{"id": "wg-1", "parentId": "wg-2"}]}),
        origin="user", expected_revision=2,
    )
    assert bad.is_error is True and bad.error_code == "workbench_delta_invalid"


@pytest.mark.asyncio
async def test_engine_delta_rejects_dangling_membership_ref():
    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_delta_2")
    await engine.apply_mutation(sid, SetWorkbenchStateIntent(doc=_valid_doc()), origin="user", expected_revision=0)
    res = await engine.apply_mutation(
        sid,
        PatchWorkbenchDeltaIntent(delta={"membershipSet": [{"layerId": "l9", "groupId": "wg-X"}]}),
        origin="user", expected_revision=1,
    )
    assert res.is_error is True and res.error_code == "workbench_delta_conflict"


@pytest.mark.asyncio
async def test_engine_base_workbench_revision_cas():
    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_base_1")
    first = await engine.apply_mutation(
        sid, SetWorkbenchStateIntent(doc=_valid_doc(), base_workbench_revision=0),
        origin="user", expected_revision=0,
    )
    assert first.is_error is False
    assert first.mapspec["workbench"]["_rev"] == 1
    # 陈旧 base（仍 0）+ 新鲜 session revision → superseded（R1-C2 核心场景）
    other = await engine.apply_mutation(sid, SetViewIntent(center=[104, 30]), origin="user", expected_revision=1)
    assert other.is_error is False  # 无关 mutation 推进 revision 至 2
    stale = await engine.apply_mutation(
        sid, SetWorkbenchStateIntent(doc=_valid_doc(), base_workbench_revision=0),
        origin="user", expected_revision=2,
    )
    assert stale.superseded is True
    # 正确 base → 通过
    good = await engine.apply_mutation(
        sid,
        SetWorkbenchStateIntent(doc={**_valid_doc(), "mode": "explore"}, base_workbench_revision=1),
        origin="user", expected_revision=2,
    )
    assert good.is_error is False


@pytest.mark.asyncio
async def test_engine_agent_lock_guard_single_and_family():
    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_lock_1")
    await engine.apply_mutation(sid, SetWorkbenchStateIntent(doc=_valid_doc()), origin="user", expected_revision=0)
    # agent 删除锁定层 → 拒绝（error_code=layer_locked）
    res = await engine.apply_mutation(sid, RemoveLayerIntent(layer_id="layer-b"), origin="agent")
    assert res.is_error is True and res.error_code == "layer_locked"
    # family 语义：layer-b 的子层族同样拒绝
    res2 = await engine.apply_mutation(sid, RemoveLayerIntent(layer_id="layer-b-child"), origin="agent")
    assert res2.is_error is True and res2.error_code == "layer_locked"
    # agent upsert 锁定层（重建）→ 拒绝
    res3 = await engine.apply_mutation(
        sid, UpsertLayerIntent(layer={"id": "layer-b", "type": "circle", "source": "src-a", "paint": {"circle-color": "#fff"}}, source_data={"type": "FeatureCollection", "features": []}), origin="agent",
    )
    assert res3.is_error is True and res3.error_code == "layer_locked"
    # agent 操作未锁定层 → 放行（锁定集只拦目标层）。先建 layer-a（此时
    # 仅存在于 workbench.membership，spec.layers 无此层）。
    upsert_a = await engine.apply_mutation(
        sid, UpsertLayerIntent(layer={"id": "layer-a", "type": "circle", "source": "src-a", "paint": {"circle-color": "#fff"}}, source_data={"type": "FeatureCollection", "features": []}), origin="agent",
    )
    assert upsert_a.is_error is False
    res4 = await engine.apply_mutation(
        sid, PatchLayerPresentationIntent(layer_id="layer-a", visible=False), origin="agent",
    )
    assert res4.is_error is False
    # 用户路径不受限
    res5 = await engine.apply_mutation(
        sid, RemoveLayerIntent(layer_id="layer-b"), origin="user", expected_revision=res4.mutation_revision,
    )
    assert res5.is_error is False


@pytest.mark.asyncio
async def test_engine_agent_lock_guard_batch_path():
    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_lock_2")
    # 建两层
    await engine.apply_mutation(
        sid, UpsertLayerIntent(layer={"id": "la", "type": "circle", "source": "src-a", "paint": {"circle-color": "#fff"}}, source_data={"type": "FeatureCollection", "features": []}), origin="agent",
    )
    await engine.apply_mutation(
        sid, UpsertLayerIntent(layer={"id": "lb", "type": "circle", "source": "src-a", "paint": {"circle-color": "#fff"}}, source_data={"type": "FeatureCollection", "features": []}), origin="agent",
    )
    await engine.apply_mutation(
        sid,
        SetWorkbenchStateIntent(
            doc={"version": 5, "groups": [], "membership": {}, "lockedLayerIds": ["lb"], "mode": "explore"},
        ),
        origin="user", expected_revision=2,
    )
    batch = await engine.apply_presentation_batch(
        sid,
        [PatchLayerPresentationIntent(layer_id="la", visible=False),
         PatchLayerPresentationIntent(layer_id="lb", visible=False)],
        origin="agent",
    )
    assert batch.committed is True
    statuses = {o.layer_id: o.status for o in batch.outcomes}
    assert statuses["la"] == "applied"
    assert statuses["lb"] == "refused"  # 锁定层在 batch 中被拒（不静默）
    assert "locked" in (next(o for o in batch.outcomes if o.layer_id == "lb").error_msg or "")


# ─── 门面挂钩：mutation → bus 事件 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_facade_publishes_doc_and_op_events():
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.collab.bus import bus

    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_hook_1")
    received = []
    rm = await bus.add_local_listener(sid, received.append)
    try:
        res = await apply_gis_mutation(
            sid, SetWorkbenchStateIntent(doc=_valid_doc()),
            origin="user", actor="test", expected_revision=0, engine=engine,
        )
        assert res.is_error is False
    finally:
        rm()
    kinds = [(e["kind"], e["seq"]) for e in received]
    assert ("doc", 1) in kinds
    assert ("op", 1) in kinds  # seq = mutation_revision
    doc_event = next(e for e in received if e["kind"] == "doc")
    assert doc_event["data"]["doc"]["version"] == 5
    assert doc_event["data"]["actor"] == "test"


@pytest.mark.asyncio
async def test_facade_publishes_delta_and_presentation_events():
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.collab.bus import bus

    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_hook_2")
    await apply_gis_mutation(
        sid, UpsertLayerIntent(layer={"id": "la", "type": "circle", "source": "src-a", "paint": {"circle-color": "#fff"}}, source_data={"type": "FeatureCollection", "features": []}),
        origin="agent", actor="t", engine=engine,
    )
    await apply_gis_mutation(
        sid, SetWorkbenchStateIntent(doc=_valid_doc()),
        origin="user", actor="t", expected_revision=1, engine=engine,
    )
    received = []
    rm = await bus.add_local_listener(sid, received.append)
    try:
        res = await apply_gis_mutation(
            sid,
            PatchWorkbenchDeltaIntent(delta={"setGroups": [{"id": "wg-1", "collapsed": True}]}),
            origin="user", actor="t", expected_revision=2, engine=engine,
        )
        assert res.is_error is False
        pres = await apply_gis_mutation(
            sid, PatchLayerPresentationIntent(layer_id="la", visible=False),
            origin="user", actor="t", expected_revision=3, engine=engine,
        )
        assert pres.is_error is False
    finally:
        rm()
    kinds = [e["kind"] for e in received]
    assert "delta" in kinds and "presentation" in kinds
    delta_event = next(e for e in received if e["kind"] == "delta")
    assert delta_event["data"]["delta"]["setGroups"] == [{"id": "wg-1", "collapsed": True}]


@pytest.mark.asyncio
async def test_facade_superseded_publishes_nothing():
    from app.services.gis_world_state import apply_gis_mutation
    from app.services.collab.bus import bus

    engine = MapSpecLifecycleEngine()
    sid = _sid("v6_hook_3")
    received = []
    rm = await bus.add_local_listener(sid, received.append)
    try:
        res = await apply_gis_mutation(
            sid, SetWorkbenchStateIntent(doc=_valid_doc()),
            origin="user", actor="t", expected_revision=5, engine=engine,  # 错误 CAS
        )
        assert res.superseded is True
    finally:
        rm()
    assert received == []  # superseded ≠ 提交，零事件


# ─── ws_collab：认证矩阵 + hello/sync/ping + 扇出 ────────────────────────────

_TMPDIRS = []


@pytest.fixture(autouse=True)
def _restore_ws_state():
    import app.tools._utils as _utils
    import app.core.rate_limiter as rl_module

    snapshots = [
        (_utils, "async_db_session", getattr(_utils, "async_db_session", None)),
        (rl_module, "get_rate_limiter", getattr(rl_module, "get_rate_limiter", None)),
    ]
    yield
    for mod, attr, original in snapshots:
        setattr(mod, attr, original)
    import shutil
    for d in list(_TMPDIRS):
        shutil.rmtree(d, ignore_errors=True)
        _TMPDIRS.remove(d)


def _make_collab_app(sessions: list[tuple[str, str | None, str | None]]):
    """sessions: [(session_id, user_id, owner_token)]。"""
    import os
    import sqlite3
    import tempfile
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    import app.tools._utils as _utils
    import app.core.rate_limiter as rl_module
    from app.api.routes.ws_collab import router as collab_router

    tmpdir = tempfile.mkdtemp()
    _TMPDIRS.append(tmpdir)
    db_path = os.path.join(tmpdir, "collab_ws_test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, org_id INTEGER, username TEXT, email TEXT,
        password_hash TEXT, full_name TEXT, avatar_url TEXT, role TEXT,
        is_active INTEGER, email_verified INTEGER, last_login TEXT,
        login_count INTEGER, token_version INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY, user_id TEXT, title TEXT, owner_token TEXT,
        created_at TEXT, updated_at TEXT
    )""")
    for sid, uid, otk in sessions:
        if uid is not None:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, username, email, role, is_active, token_version) VALUES (?, 'u', 'e@x', 'viewer', 1, 0)",
                (uid,),
            )
        conn.execute(
            "INSERT INTO conversations (id, user_id, owner_token, title) VALUES (?, ?, ?, 't')",
            (sid, uid, otk),
        )
    conn.commit()
    conn.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def _session():
        async with factory() as s:
            yield s

    _utils.async_db_session = _session

    class _NoOpLimiter:
        async def is_allowed(self, *a, **kw):
            return True

    async def _stub():
        return _NoOpLimiter()

    rl_module.get_rate_limiter = _stub

    app = FastAPI()
    app.include_router(collab_router, prefix="/api/v1")
    return app


def _receive_hello(ws) -> dict:
    import json as _json

    while True:
        msg = _json.loads(ws.receive_text())
        if msg.get("event") == "hello":
            return msg
        if msg.get("event") == "presence_full":
            continue
        return msg


def test_collab_ws_auth_matrix():
    from app.core.auth import create_access_token

    app = _make_collab_app([
        ("sess-owned", "user-123", None),
        ("sess-anon", None, "tok-correct"),
        ("sess-legacy", None, None),
    ])
    client = TestClient(app)
    good = create_access_token({"sub": "user-123"})

    # 1) 无 token → 4001
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws/collab/sess-owned"):
            pass
    # 2) 认证用户连自己会话 → hello（带 clientId/revision）
    with client.websocket_connect(
        "/api/v1/ws/collab/sess-owned", subprotocols=["bearer", good]
    ) as ws:
        hello = _receive_hello(ws)
        assert hello["data"]["clientId"]
        assert hello["data"]["doc"] is None or isinstance(hello["data"]["doc"], dict)
    # 3) 认证用户连他人/不存在会话 → 4003
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/ws/collab/sess-anon", subprotocols=["bearer", good]
        ):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/ws/collab/sess-missing", subprotocols=["bearer", good]
        ):
            pass
    # 4) 匿名正确 owner_token → 接受
    with client.websocket_connect(
        "/api/v1/ws/collab/sess-anon", subprotocols=["session", "tok-correct"]
    ) as ws:
        hello = _receive_hello(ws)
        assert hello["data"]["clientId"]
    # 5) 匿名错误 token → 4003
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/ws/collab/sess-anon", subprotocols=["session", "tok-wrong"]
        ):
            pass
    # 6) legacy NULL/NULL → 4003（#1109 fail-closed；任意 token 均拒）
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/ws/collab/sess-legacy", subprotocols=["session", "any-token"]
        ):
            pass
    # 7) 未知 subprotocol → 4001
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/ws/collab/sess-owned", subprotocols=["other", "x"]
        ):
            pass


def test_collab_ws_ping_sync_roundtrip():
    from app.core.auth import create_access_token

    app = _make_collab_app([("sess-p", "user-123", None)])
    client = TestClient(app)
    good = create_access_token({"sub": "user-123"})
    with client.websocket_connect(
        "/api/v1/ws/collab/sess-p", subprotocols=["bearer", good]
    ) as ws:
        _receive_hello(ws)
        import json as _json

        ws.send_text(_json.dumps({"event": "ping"}))
        got = _json.loads(ws.receive_text())
        while got.get("event") != "pong":
            got = _json.loads(ws.receive_text())
        assert "revision" in got["data"]
        # sync：knownRevision 缺失 → 权威 doc 回放（replay 标记）
        ws.send_text(_json.dumps({"event": "sync", "data": {"knownRevision": 999}}))
        got = _json.loads(ws.receive_text())
        while got.get("event") != "doc":
            got = _json.loads(ws.receive_text())
        assert got["data"]["replay"] is True
        assert "revision" in got["data"]


def test_collab_ws_send_loop_drains_queue_to_socket():
    """扇出末段（_CollabConnection.send_loop）：bus listener 入队 → WS 帧落盘。

    与 test_bus_local_fanout_and_unsubscribe（总线→listener）拼接后即覆盖
    「发布 → 两连接各自收到」的完整扇出链，无需跨线程操纵真实 WS。
    """
    import asyncio
    import json as _json

    from app.api.routes.ws_collab import _CollabConnection, _make_listener

    class _FakeWS:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(text)

    async def _scenario():
        bus = CollabBus()
        fake1, fake2 = _FakeWS(), _FakeWS()
        conn1 = _CollabConnection(fake1, "sess-x", "c1", "u")
        conn2 = _CollabConnection(fake2, "sess-x", "c2", "u")
        conn1.send_task = asyncio.get_running_loop().create_task(conn1.send_loop())
        conn2.send_task = asyncio.get_running_loop().create_task(conn2.send_loop())
        rm1 = await bus.add_local_listener("sess-x", _make_listener(conn1))
        await bus.add_local_listener("sess-x", _make_listener(conn2))
        await bus.publish("sess-x", "op", {"revision": 7}, seq=7)
        await asyncio.sleep(0.05)
        rm1()
        await bus.publish("sess-x", "op", {"revision": 8}, seq=8)
        await asyncio.sleep(0.05)
        for conn in (conn1, conn2):
            conn.enqueue(None)  # 哨兵（enqueue 对 None 放行 closed 门）
            conn.closed = True
            await conn.send_task
        return fake1.sent, fake2.sent

    frames1_raw, frames2_raw = asyncio.run(_scenario())
    frames1 = [_json.loads(t) for t in frames1_raw]
    frames2 = [_json.loads(t) for t in frames2_raw]
    assert [f["data"]["revision"] for f in frames1] == [7]  # 退订后不再收
    assert [f["data"]["revision"] for f in frames2] == [7, 8]
    # 单扇出无双计：每连接各恰一份
    assert len(frames1) == 1 and len(frames2) == 2


# ─── artifact 感知（W8）：投影端点 + 失效事件 ────────────────────────────────

@pytest.mark.asyncio
async def test_artifact_status_endpoint_projects_registry():
    """投影端点：登记 stale+valid 产物 → stale 优先、有界、零新真相。"""
    from fastapi import FastAPI as _FA
    from fastapi.testclient import TestClient as _TC
    from app.api.routes import mapspec_mutations as mm

    from app.services.artifact_registry import (
        mark_status,
        register_artifact,
    )

    sid = _sid("v6_art_1")

    await register_artifact(
        sid, artifact_id="ref:stale-1",
        producer_capability="buffer_analyst", producer_node="buffer_1",
        inputs=["ref:up-1"],
    )
    await mark_status(sid, "ref:stale-1", "stale")
    await register_artifact(sid, artifact_id="ref:valid-1")

    # ownership 依赖打桩（端点薄投影，所有权语义由既有依赖测试覆盖）
    class _Conv:
        id = sid
    app = _FA()
    app.include_router(mm.router, prefix="/api/v1")
    app.dependency_overrides[mm.require_owned_session] = lambda: _Conv()
    client = _TC(app)
    try:
        resp = client.get(f"/api/v1/chat/sessions/{sid}/workbench/artifact-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["staleCount"] == 1
        assert body["artifacts"][0]["artifactId"] == "ref:stale-1"  # stale 优先
        a = body["artifacts"][0]
        assert a["producerCapability"] == "buffer_analyst"
        assert a["inputs"] == ["ref:up-1"]
        assert "metadata" not in a  # 载荷有界
    finally:
        from app.services.session_data import session_data_manager as _sdm

        await _sdm.clear_session(sid)
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ref_invalidation_publishes_artifact_event():
    """ref_lifecycle 失效 → bus artifact 事件（跨浏览器 stale 徽标）。"""
    from app.services.collab.bus import bus
    from app.services.ref_lifecycle import RefInvalidationReason, invalidate_ref_caches

    sid = _sid("v6_art_2")
    received = []
    rm = await bus.add_local_listener(sid, received.append)
    try:
        invalidate_ref_caches(sid, ["ref:zz-1"], RefInvalidationReason.OVERWRITE)
        # fire-and-forget task：让出事件循环一次
        import asyncio as _asyncio
        await _asyncio.sleep(0.05)
    finally:
        rm()
    artifact_events = [e for e in received if e["kind"] == "artifact"]
    assert len(artifact_events) == 1
    assert artifact_events[0]["data"]["refId"] == "ref:zz-1"
    assert artifact_events[0]["data"]["reason"] == "OVERWRITE"
