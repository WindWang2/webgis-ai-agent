"""G43（ADR-0295）route 合同测试：owner 守卫 + 策略 CAS + 回执读取。

覆盖场景矩阵（API 层）：
- C25 跨用户/跨租户会话访问 → 404（不泄漏存在性）
- C12 PUT CAS 冲突 → 409 + 当前值
- C09 未授权源 → 400 统一拒绝
- C04/C29 历史 turn 回执读取；缺失 → 404 + coverage 说明
- C07 预览无副作用（不推进状态）
"""
from __future__ import annotations

import contextlib

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.db_model import Base, Conversation


@pytest_asyncio.fixture
async def app_env(tmp_path, monkeypatch):
    from app.core import database as db_mod
    from app.main import app  # noqa: F401 — full app for route deps

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'g43_routes.db'}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fac = async_sessionmaker(bind=eng, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def _patched_session():
        async with fac() as db:
            yield db

    monkeypatch.setattr(db_mod, "async_db_session", _patched_session)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", fac)

    # 种子：u1 的会话 + u2 的会话（存在性不泄漏验证）
    async with fac() as db:
        db.add(Conversation(id="s-mine", user_id="u1", title="t"))
        db.add(Conversation(id="s-other", user_id="u2", title="t"))
        await db.commit()

    from app.core.auth import get_current_user_optional
    from app.main import app as fastapp

    # 认证覆写：测试用户 u1（owner 守卫仍生效 —— u1 只能读自己的会话；
    # 覆写挂在 FastAPI app 上，turn_context_views 与 auth_history_bridge
    # 共享同一函数对象）
    async def _u1():
        return {"user_id": "u1", "org_id": 1}

    fastapp.dependency_overrides[get_current_user_optional] = _u1
    transport = ASGITransport(app=fastapp)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        yield client, fac
    fastapp.dependency_overrides.pop(get_current_user_optional, None)
    await eng.dispose()


class TestOwnerGuard:
    async def test_cross_user_session_404_no_existence_leak(self, app_env):
        client, _ = app_env
        r = await client.get("/api/v1/chat/sessions/s-other/context-policy")
        assert r.status_code == 404
        assert "context" not in r.text.lower() or "not" in r.text.lower()
        r = await client.get("/api/v1/chat/sessions/missing/context-policy")
        assert r.status_code == 404

    async def test_own_session_policy_defaults_auto(self, app_env):
        client, _ = app_env
        r = await client.get("/api/v1/chat/sessions/s-mine/context-policy")
        assert r.status_code == 200
        body = r.json()
        assert body["policy"]["mode"] == "auto"
        assert "下一轮" in body["scope_note"]


class TestPolicyCASFlow:
    async def test_put_then_conflict(self, app_env, monkeypatch):
        client, _ = app_env
        # 目录授权：目录来自 list_authorized_sources（此处无项目 → 空目录，
        # explicit + 选择必然拒绝；先测 auto 写入与 CAS 主体）
        r = await client.put(
            "/api/v1/chat/sessions/s-mine/context-policy",
            json={
                "mode": "auto", "preferred": [], "excluded": [],
                "expected_version": 0, "project_id": "p1",
            },
        )
        assert r.status_code == 200
        version = r.json()["policy"]["policy_version"]
        assert version >= 1

        # 过期版本 → 409 + 当前值（C12）
        r = await client.put(
            "/api/v1/chat/sessions/s-mine/context-policy",
            json={
                "mode": "auto", "preferred": [], "excluded": [],
                "expected_version": 0, "project_id": "p1",
            },
        )
        assert r.status_code == 409
        # 统一错误信封：结构化载荷在 data（dict detail 不再是旧体 detail）
        assert r.json()["data"]["code"] == "POLICY_VERSION_CONFLICT"
        assert r.json()["data"]["current"]["policy_version"] == version

    async def test_unauthorized_source_unified_reject(self, app_env, monkeypatch):
        client, _ = app_env
        import app.services.turn_context_store as store

        async def _fake_sources(**kw):
            return [{"source_id": "pk:1", "kind": "project_knowledge_entry",
                     "label": "x", "version_token": "t", "selectable": True}]

        monkeypatch.setattr(store, "list_authorized_sources", _fake_sources)
        r = await client.put(
            "/api/v1/chat/sessions/s-mine/context-policy",
            json={
                "mode": "explicit",
                "preferred": ["pk:1", "pk:other-tenant-entry"],
                "excluded": [],
                "expected_version": 0,
                "project_id": "p1",
            },
        )
        assert r.status_code == 400
        assert "source" in r.text


class TestReceiptReads:
    async def test_receipt_roundtrip_and_missing(self, app_env):
        client, fac = app_env
        from app.services.turn_context_store import record_assembly

        await record_assembly(
            session_id="s-mine", turn_id="turn-1", assembly_mode="typed",
            policy_version=2, digest="d" * 32,
            payload={"sources": [{"source_id": "pk:1", "decision": "included"}]},
        )
        r = await client.get(
            "/api/v1/chat/sessions/s-mine/context-receipts/turn-1")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "assembled"
        assert body["payload"]["sources"][0]["source_id"] == "pk:1"

        # 缺失 turn → 404 + 明确说明（C29 不补造）
        r = await client.get(
            "/api/v1/chat/sessions/s-mine/context-receipts/turn-none")
        assert r.status_code == 404
        assert "legacy" in r.json()["data"]["message"]

        # 他人的 turn id 也 404（C25）
        await record_assembly(
            session_id="s-other", turn_id="turn-x", assembly_mode="typed",
            policy_version=0, digest="", payload={},
        )
        r = await client.get(
            "/api/v1/chat/sessions/s-mine/context-receipts/turn-x")
        assert r.status_code == 404

    async def test_receipts_list_bounded(self, app_env):
        client, _ = app_env
        from app.services.turn_context_store import record_assembly

        for i in range(3):
            await record_assembly(
                session_id="s-mine", turn_id=f"t{i}", assembly_mode="typed",
                policy_version=0, digest="", payload={},
            )
        r = await client.get(
            "/api/v1/chat/sessions/s-mine/context-receipts?limit=2")
        assert r.status_code == 200
        assert len(r.json()["receipts"]) == 2


class TestPreviewEndpoint:
    async def test_preview_is_readonly_and_labeled(self, app_env, monkeypatch):
        client, _ = app_env
        ensure_calls = {"n": 0}

        async def _no_ensure(sid):
            ensure_calls["n"] += 1

        import app.services.session_plan as sp
        monkeypatch.setattr(sp, "ensure_session_plan_slot", _no_ensure)

        r = await client.post(
            "/api/v1/chat/sessions/s-mine/context-preview",
            json={"project_id": "", "message_draft": "hi"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["is_preview"] is True
        assert ensure_calls["n"] == 0  # C07：预览不建 plan slot
        assert "not_previewed" in body

    async def test_preview_policy_draft_validated_against_catalog(
            self, app_env, monkeypatch):
        client, _ = app_env
        import app.services.turn_context_store as store

        async def _fake_sources(**kw):
            return []

        monkeypatch.setattr(store, "list_authorized_sources", _fake_sources)
        r = await client.post(
            "/api/v1/chat/sessions/s-mine/context-preview",
            json={
                "project_id": "p1",
                "policy_draft": {
                    "mode": "explicit", "preferred": ["pk:not-in-catalog"],
                    "excluded": [], "expected_version": 0, "project_id": "p1",
                },
            },
        )
        assert r.status_code == 400
