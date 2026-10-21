"""SEC-02（#1674）：/ws/collab 握手后的周期性撤销复核。

缺陷：``collab_websocket`` 只在握手时认证一次（mode == "bearer" →
``authenticate_ws_token``），消息循环内从不重验 —— 已注销 / 停用 /
``token_version`` 被 bump 的 token 可无限期保持协作读写（读全量 workbench
doc、抢/续/放编辑锁），半径是 HTTP 面 30min TTL 的**无限倍**，跨 pod 经
Redis 总线扇出同样成立。

修复口径与姊妹端点 ws.py:L128-135 一致：消息循环内每 ``_REAUTH_INTERVAL_S``
（60s）重跑 ``authenticate_ws_token``（token_version / is_active 复核），
``WsAuthError`` → close 4401。仅 bearer 模式有撤销向量（session 模式是
``Conversation.owner_token``，没有可撤销的 DB 用户）。

本文件用真实 DB bump（不是桩）：复核必须真的再查一次 users 表。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import ws_collab as collab_module
from app.core.auth import create_access_token

_USER_ID = "user-reauth"
_SESSION_ID = "sess-reauth"
_OWNER_TOKEN = "tok-correct"

_TMPDIRS: list[str] = []


@pytest.fixture(autouse=True)
def _restore_patched_module_state():
    """模块级补丁无恢复会污染同进程后续测试（同 test_ws_auth.py 的纪律）。"""
    import app.core.database as core_db
    import app.core.rate_limiter as rl_module
    import app.tools._utils as _utils

    snapshots = [
        (_utils, "async_db_session", getattr(_utils, "async_db_session", None)),
        (core_db, "async_db_session", getattr(core_db, "async_db_session", None)),
        (rl_module, "get_rate_limiter", getattr(rl_module, "get_rate_limiter", None)),
        (collab_module, "_REAUTH_INTERVAL_S", collab_module._REAUTH_INTERVAL_S),
    ]
    yield
    for mod, attr, original in snapshots:
        setattr(mod, attr, original)
    import shutil

    for d in list(_TMPDIRS):
        shutil.rmtree(d, ignore_errors=True)
        _TMPDIRS.remove(d)


def _make_collab_app(user_token_version: int = 0):
    """真实 sqlite 库：user（可 bump token_version）+ 本人绑定的会话。"""
    import app.core.database as core_db
    import app.core.rate_limiter as rl_module
    import app.tools._utils as _utils

    tmpdir = tempfile.mkdtemp()
    _TMPDIRS.append(tmpdir)
    db_path = os.path.join(tmpdir, "collab_reauth.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, org_id INTEGER, username TEXT, email TEXT,
            password_hash TEXT, full_name TEXT, avatar_url TEXT, role TEXT,
            is_active INTEGER, email_verified INTEGER, last_login TEXT,
            login_count INTEGER, token_version INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, user_id TEXT, title TEXT, owner_token TEXT,
            created_at TEXT, updated_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO users (id, username, email, role, is_active, token_version)"
        " VALUES (?, 'u', 'e@x', 'viewer', 1, ?)",
        (_USER_ID, user_token_version),
    )
    conn.execute(
        "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, 't')",
        (_SESSION_ID, _USER_ID),
    )
    conn.commit()
    conn.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def _session():
        async with factory() as s:
            yield s

    # authenticate_ws_token 从 app.core.database 取 seam（auth.py:784）；
    # _utils 是 re-export 面，双写保持两面一致（同 test_ws_auth.py）。
    _utils.async_db_session = _session
    core_db.async_db_session = _session

    class _NoOpLimiter:
        async def is_allowed(self, *a, **kw):
            return True

    async def _stub():
        return _NoOpLimiter()

    rl_module.get_rate_limiter = _stub

    app = FastAPI()
    app.include_router(collab_module.router, prefix="/api/v1")
    return app, db_path


def _bump_token_version(db_path: str, new_version: int) -> None:
    """logout / 全局登出语义：bump users.token_version（旧 token 立即失效）。"""
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(
            "UPDATE users SET token_version = ? WHERE id = ?", (new_version, _USER_ID)
        )
    conn.close()


def _receive_hello(ws) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("event") == "hello":
            return msg
        if msg.get("event") == "presence_full":
            continue
        return msg


def test_bumped_token_version_closes_connection_on_periodic_reauth(monkeypatch):
    """#1674 主用例：bump token_version → 下一次帧触发复核 → close 4401。"""
    app, db_path = _make_collab_app(user_token_version=0)
    client = TestClient(app)
    token = create_access_token({"sub": _USER_ID, "role": "viewer"})
    # 60s 窗口在测试里快进：把间隔压到 0，让**下一帧**即触发复核（确定性的
    # 「到期」语义，不需要真的等 61s）。
    monkeypatch.setattr(collab_module, "_REAUTH_INTERVAL_S", 0.0)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/api/v1/ws/collab/{_SESSION_ID}", subprotocols=["bearer", token]
        ) as ws:
            assert _receive_hello(ws)["data"]["clientId"], "握手必须成功"
            _bump_token_version(db_path, 1)  # 用户在别处 logout
            ws.send_json({"event": "ping"})
            # 复核失败必须**立即**断开：这一帧不得再被当作合法 ping 处理。
            # 握手期的 presence join 回声可能先到，于是一路读到 close 为止
            # （收到 pong 即说明撤销没被拦住 —— 明确失败而不是挂住）。
            while True:
                frame = json.loads(ws.receive_text())
                assert frame.get("event") != "pong", "撤销后的帧不得再被当作合法 ping 应答"
    assert exc_info.value.code == 4401, "撤销后的 token 必须以 4401 断开（ws.py 同口径）"


def test_periodic_reauth_keeps_valid_token_connected(monkeypatch):
    """对照：未撤销的 token 在周期复核下不得被误断开（无假阳性）。"""
    app, _db_path = _make_collab_app(user_token_version=0)
    client = TestClient(app)
    token = create_access_token({"sub": _USER_ID, "role": "viewer"})
    monkeypatch.setattr(collab_module, "_REAUTH_INTERVAL_S", 0.0)

    with client.websocket_connect(
        f"/api/v1/ws/collab/{_SESSION_ID}", subprotocols=["bearer", token]
    ) as ws:
        assert _receive_hello(ws)["data"]["clientId"]
        for _ in range(3):  # 每帧都到期 → 每次都重跑复核
            ws.send_json({"event": "ping"})
            got = json.loads(ws.receive_text())
            while got.get("event") != "pong":
                got = json.loads(ws.receive_text())
            assert "revision" in got["data"]


def test_disabled_account_closes_on_periodic_reauth(monkeypatch):
    """停用（is_active=0）同样必须在复核窗口内断开 —— 与 bump 同族撤销向量。"""
    app, db_path = _make_collab_app(user_token_version=0)
    client = TestClient(app)
    token = create_access_token({"sub": _USER_ID, "role": "viewer"})
    monkeypatch.setattr(collab_module, "_REAUTH_INTERVAL_S", 0.0)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            f"/api/v1/ws/collab/{_SESSION_ID}", subprotocols=["bearer", token]
        ) as ws:
            assert _receive_hello(ws)["data"]["clientId"], "握手时账户仍是 active"
            # 连接**之后**被停用（握手一次性认证的漏洞正是这个窗口）。
            conn = sqlite3.connect(db_path)
            with conn:
                conn.execute(
                    "UPDATE users SET is_active = 0 WHERE id = ?", (_USER_ID,)
                )
            conn.close()
            ws.send_json({"event": "ping"})
            while True:
                frame = json.loads(ws.receive_text())
                assert frame.get("event") != "pong", "停用后的帧不得再被当作合法 ping 应答"
    assert exc_info.value.code == 4401


def test_session_mode_owner_token_survives_periodic_reauth(monkeypatch):
    """匿名会话（owner_token 匹配）在「已到期」的复核窗口下仍保持连接。

    同时钉死范围决策：周期复核只挂在 bearer 模式上 —— session 模式没有
    可撤销的 DB 用户（Conversation.owner_token），给它也跑 JWT 复核要么是
    无意义噪声，要么会误断匿名协作。
    """
    import app.core.database as core_db
    import app.core.rate_limiter as rl_module
    import app.tools._utils as _utils
    from contextlib import asynccontextmanager as _acm

    tmpdir = tempfile.mkdtemp()
    _TMPDIRS.append(tmpdir)
    db_path = os.path.join(tmpdir, "collab_reauth_anon.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, org_id INTEGER, username TEXT, email TEXT,
            password_hash TEXT, full_name TEXT, avatar_url TEXT, role TEXT,
            is_active INTEGER, email_verified INTEGER, last_login TEXT,
            login_count INTEGER, token_version INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, user_id TEXT, title TEXT, owner_token TEXT,
            created_at TEXT, updated_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO conversations (id, user_id, title, owner_token)"
        " VALUES (?, NULL, 't', ?)",
        (_SESSION_ID, _OWNER_TOKEN),
    )
    conn.commit()
    conn.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    @_acm
    async def _session():
        async with factory() as s:
            yield s

    _utils.async_db_session = _session
    core_db.async_db_session = _session

    class _NoOpLimiter:
        async def is_allowed(self, *a, **kw):
            return True

    async def _stub():
        return _NoOpLimiter()

    rl_module.get_rate_limiter = _stub

    app = FastAPI()
    app.include_router(collab_module.router, prefix="/api/v1")
    client = TestClient(app)
    monkeypatch.setattr(collab_module, "_REAUTH_INTERVAL_S", 0.0)

    with client.websocket_connect(
        f"/api/v1/ws/collab/{_SESSION_ID}", subprotocols=["session", _OWNER_TOKEN]
    ) as ws:
        assert _receive_hello(ws)["data"]["clientId"], "匿名握手必须成功"
        for _ in range(3):
            ws.send_json({"event": "ping"})
            got = json.loads(ws.receive_text())
            while got.get("event") != "pong":
                got = json.loads(ws.receive_text())
            assert "revision" in got["data"]
