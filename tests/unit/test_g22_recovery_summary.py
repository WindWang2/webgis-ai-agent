"""G22-P6 — 会话恢复可读性摘要（recovery_summary）单测。

口径（与 G22-P2 权威恢复目录同源）：摘要只折叠权威源现取的事实 ——
MapSpec revision/layers/锁/隐藏/标注/画幅、``session_data_manager.has``
核对的失效 source refs、analysis_tasks 非终态 durable jobs。缺席 →
短语/字段缺席（不虚构）；任何阶段失败 → 该字段为空、绝不抛出；
绝不携带载荷或凭据；id 截断 32 字符。

路由面（GET /chat/sessions/{id}?recovery=1）：默认响应不带 recovery 键
（字节不变）；显式 flag 才附挂（TestClient 装配，接缝与
tests/test_chat_history_routes.py 相同）。
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.services.chat.recovery_summary import build_recovery_summary

SID = "sess-g22-p6"

#: 全要素 mapspec：锁定 + 隐藏（canonical 与 legacy 两种形状）+ 标注覆写
#: （dict 与 list 两种形状）+ frame_product + 混入非 dict 层（形状守卫）。
MAPSPEC = {
    "revision": 9,
    "layers": [
        {
            "id": "l-keep",
            "layout": {"visibility": "visible"},
            "label": {"authoring": {"overrides": {"字号": 1, "颜色": 2}}},
        },
        {"id": "l-hidden", "layout": {"visibility": "none"}},
        {"id": "l-legacy-hidden", "visible": False},  # legacy 顶层 flag
        {"id": "l-raster", "label": {"authoring": {"overrides": ["a", "b", "c"]}}},
        "not-a-dict",  # 形状守卫：非 dict 层不计数
    ],
    "workbench": {"lockedLayerIds": ["l-keep"], "lockedComponentIds": ["comp-1", "comp-2"]},
    "sources": {
        "src-live": {
            "refId": "ref-live",
            # 载荷绝不可进摘要（泄漏面断言用标记值）。
            "inlinePayload": "PAYLOAD-MUST-NOT-APPEAR",
        },
        "src-dead": {"refId": "ref-dead-" + "x" * 48},
        "src-url": {"url": "https://example.com/tiles"},  # url 型指针不判定
        "src-no-ref": {"type": "data_fabric"},
    },
    "frame_product": {"active_frame": "frame-a", "frames": []},
    "scene": {},
}

#: 假 durable job 行（newest-first；parameters 携带敏感标记值，绝不可外泄）。
JOB_ROWS = [
    SimpleNamespace(
        id=424242, status="running", created_at=datetime(2026, 11, 12),
        parameters={"api_key": "SECRET-MUST-NOT-APPEAR"},
    ),
    SimpleNamespace(id=424241, status="cancelling", created_at=datetime(2026, 11, 11), parameters={}),
]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    """SQLAlchemy AsyncSession 替身：捕获语句 + 返回预置行。"""

    def __init__(self, rows):
        self._rows = rows
        self.captured_stmt = None

    async def execute(self, stmt):
        self.captured_stmt = stmt
        return _FakeResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSessionLocal:
    def __init__(self, rows=None, raise_on_execute=False):
        self._rows = rows or []
        self._raise = raise_on_execute

    def __call__(self):
        if self._raise:
            db = _FailingDb()
        else:
            db = _FakeDb(self._rows)
        self.last_db = db
        return db


class _FailingDb:
    async def execute(self, stmt):
        raise RuntimeError("db down")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_mapspec(monkeypatch, mapspec):
    from app.services.mapspec.store import mapspec_store_instance

    async def _fake_get(sid, state_hint=None):
        return mapspec

    monkeypatch.setattr(mapspec_store_instance, "get_mapspec", _fake_get)


def _patch_has(monkeypatch, live_refs):
    """session_data_manager.has 替身（生产后端未必实现 has —— raising=False）。"""
    from app.services.session_data import session_data_manager

    monkeypatch.setattr(
        session_data_manager, "has",
        lambda sid, ref: sid == SID and ref in live_refs,
        raising=False,
    )


# ── 摘要：全要素投影 ──────────────────────────────────────────────────────


async def test_full_summary_projects_all_constraints_and_facts(monkeypatch):
    _patch_mapspec(monkeypatch, MAPSPEC)
    _patch_has(monkeypatch, {"ref-live"})
    monkeypatch.setattr(
        "app.core.database.AsyncSessionLocal", _FakeSessionLocal(JOB_ROWS))

    s = await build_recovery_summary(SID)

    assert s["status"] == "ok"
    assert s["recovered"] is True
    assert s["session_id"] == SID
    assert s["recovered_to_revision"] == 9
    # 非 dict 层不计数：5 项中 1 项非 dict → 4 层。
    assert s["layer_count"] == 4
    # 失效 refs：dead 报、live 不报、url 型/无 ref 型不判定；id 截断 32。
    assert s["invalid_source_refs"] == [
        {"source": "src-dead", "ref": ("ref-dead-" + "x" * 48)[:32]}
    ]
    # durable jobs：非终态行如实（状态原样），最新在前（fake 按此序）。
    assert s["durable_jobs"] == [
        {"id": "424242", "status": "running"},
        {"id": "424241", "status": "cancelling"},
    ]
    uc = s["user_constraints"]
    assert uc["locked_layers"] == ["l-keep"]
    assert uc["locked_components"] == 2
    assert uc["hidden_layers"] == ["l-hidden", "l-legacy-hidden"]
    assert uc["label_overrides"] == 5  # dict 2 条 + list 3 条
    assert uc["active_frame"] == "frame-a"


async def test_durable_jobs_query_contract_active_statuses_desc_limit(monkeypatch):
    """查询契约：非终态过滤 + created_at 倒序 + limit 8（SQL 侧钉死）。"""
    _patch_mapspec(monkeypatch, MAPSPEC)
    _patch_has(monkeypatch, set())
    factory = _FakeSessionLocal(JOB_ROWS)
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", factory)

    await build_recovery_summary(SID)

    stmt_sql = str(factory.last_db.captured_stmt)
    assert "analysis_tasks.session_id" in stmt_sql
    assert "status IN" in stmt_sql
    assert "ORDER BY analysis_tasks.created_at DESC" in stmt_sql
    assert "LIMIT" in stmt_sql


async def test_invalid_refs_capped_at_eight(monkeypatch):
    mapspec = {
        "revision": 1,
        "layers": [],
        "sources": {f"src-{i}": {"refId": f"ref-{i}"} for i in range(12)},
    }
    _patch_mapspec(monkeypatch, mapspec)
    _patch_has(monkeypatch, set())  # 全部失效
    monkeypatch.setattr(
        "app.core.database.AsyncSessionLocal", _FakeSessionLocal([]))

    s = await build_recovery_summary(SID)
    assert len(s["invalid_source_refs"]) == 8


async def test_durable_jobs_capped_at_eight_defensively(monkeypatch):
    """SQL LIMIT 之外的二道防线：stub 返回 12 行也只披露 8。"""
    _patch_mapspec(monkeypatch, MAPSPEC)
    _patch_has(monkeypatch, set())
    rows = [
        SimpleNamespace(id=i, status="queued", created_at=datetime(2026, 1, 1))
        for i in range(12)
    ]
    monkeypatch.setattr(
        "app.core.database.AsyncSessionLocal", _FakeSessionLocal(rows))

    s = await build_recovery_summary(SID)
    assert len(s["durable_jobs"]) == 8


# ── 缺席与失败：不虚构、不抛出 ────────────────────────────────────────────


async def test_missing_mapspec_reports_recovered_false(monkeypatch):
    _patch_mapspec(monkeypatch, None)
    s = await build_recovery_summary(SID)
    assert s == {"status": "ok", "session_id": SID, "recovered": False}


async def test_empty_session_id_is_recovered_false():
    s = await build_recovery_summary("")
    assert s == {"status": "ok", "session_id": "", "recovered": False}


async def test_mapspec_read_failure_degrades_to_recovered_false(monkeypatch):
    from app.services.mapspec.store import mapspec_store_instance

    async def _boom(sid, state_hint=None):
        raise RuntimeError("store down")

    monkeypatch.setattr(mapspec_store_instance, "get_mapspec", _boom)
    s = await build_recovery_summary(SID)
    assert s["recovered"] is False


async def test_has_unavailable_means_no_fabricated_invalid_refs(monkeypatch):
    """后端无 has（生产 Redis 后端即如此）→ 无法核实 → 不虚构失效。"""
    from app.services.session_data import session_data_manager

    _patch_mapspec(monkeypatch, MAPSPEC)
    monkeypatch.delattr(session_data_manager, "has", raising=False)
    monkeypatch.setattr(
        "app.core.database.AsyncSessionLocal", _FakeSessionLocal([]))

    s = await build_recovery_summary(SID)
    assert s["invalid_source_refs"] == []


async def test_db_failure_yields_empty_jobs_and_summary_still_ok(monkeypatch):
    _patch_mapspec(monkeypatch, MAPSPEC)
    _patch_has(monkeypatch, set())
    monkeypatch.setattr(
        "app.core.database.AsyncSessionLocal",
        _FakeSessionLocal(raise_on_execute=True))

    s = await build_recovery_summary(SID)
    assert s["status"] == "ok"
    assert s["recovered"] is True
    assert s["durable_jobs"] == []


# ── 泄漏面：载荷/凭据绝不进摘要 ───────────────────────────────────────────


async def test_no_payload_or_credential_leakage(monkeypatch):
    _patch_mapspec(monkeypatch, MAPSPEC)
    _patch_has(monkeypatch, {"ref-live"})
    monkeypatch.setattr(
        "app.core.database.AsyncSessionLocal", _FakeSessionLocal(JOB_ROWS))

    blob = json.dumps(await build_recovery_summary(SID), ensure_ascii=False)
    for marker in (
        "PAYLOAD-MUST-NOT-APPEAR",      # source 载荷
        "SECRET-MUST-NOT-APPEAR",       # job parameters
        "parameters",                   # 行载荷键
        "inlinePayload",                # 源载荷键
        "owner_token", "api_key",       # 凭据键名
    ):
        assert marker not in blob, f"泄漏面标记进入摘要: {marker}"

    s = json.loads(blob)
    for entry in s["invalid_source_refs"]:
        assert set(entry.keys()) == {"source", "ref"}
    for entry in s["durable_jobs"]:
        assert set(entry.keys()) == {"id", "status"}


# ── 路由面：?recovery=1 才附挂，默认响应形状不变 ──────────────────────────


def _route_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import chat as chat_mod
    from app.core.database import get_async_db

    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api/v1")
    client = TestClient(app)

    conv = MagicMock()
    conv.id = SID
    conv.title = "G22"
    conv.created_at = datetime(2026, 11, 12)
    conv.updated_at = datetime(2026, 11, 12)
    msg = MagicMock()
    msg.id = 1
    msg.role = "user"
    msg.content = "hello"
    msg.created_at = datetime(2026, 11, 12)

    class _Db:
        async def execute(self, stmt):
            if "count(" in str(stmt).lower():
                return _CountResult(1)
            return _FakeResult([msg])

    class _CountResult:
        def __init__(self, n):
            self._n = n

        def scalar_one(self):
            return self._n

    async def _override_db():
        yield _Db()

    client.app.dependency_overrides[get_async_db] = _override_db
    monkeypatch.setattr(
        "app.services.auth_history_bridge.verify_session_owner",
        AsyncMock(return_value=conv))
    return client


async def _fake_summary(sid):
    return {"status": "ok", "session_id": sid, "recovered": True,
            "recovered_to_revision": 9}


def test_route_default_response_has_no_recovery_key(monkeypatch):
    client = _route_client(monkeypatch)
    resp = client.get(f"/api/v1/chat/sessions/{SID}")
    assert resp.status_code == 200
    assert "recovery" not in resp.json()


def test_route_recovery_flag_attaches_summary(monkeypatch):
    monkeypatch.setattr(
        "app.services.chat.recovery_summary.build_recovery_summary",
        _fake_summary)
    client = _route_client(monkeypatch)
    resp = client.get(f"/api/v1/chat/sessions/{SID}?recovery=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["recovery"]["recovered"] is True
    assert data["recovery"]["recovered_to_revision"] == 9
    # 既有 transcript 面不受附挂影响。
    assert data["id"] == SID
    assert len(data["messages"]) == 1


def test_route_recovery_summary_failure_degrades_not_500(monkeypatch):
    async def _boom(sid):
        raise RuntimeError("summary down")

    monkeypatch.setattr(
        "app.services.chat.recovery_summary.build_recovery_summary", _boom)
    client = _route_client(monkeypatch)
    resp = client.get(f"/api/v1/chat/sessions/{SID}?recovery=1")
    assert resp.status_code == 200
    assert resp.json()["recovery"] == {"status": "ok", "recovered": False}
