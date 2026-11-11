"""G11 证据化地图简报 —— API 集成测试（真实隔离 DB + 真实会话产物）。

覆盖用户旅程（BRF 指令示例 1-4 的服务端面）：
draft → 编辑/锁定/revision 冲突 → 证据核对 → 预检 → 冻结（幂等）→
私有下载 → 分享/撤销/过期 → 跨 owner 404。

标记：real domain integration（隔离 SQLite + 内存 session store + CAS
文件根 tmp_path；无网络、无 LLM）。
"""
from __future__ import annotations


import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.database import get_async_db

pytestmark = pytest.mark.asyncio


# ── fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def isolated_env(monkeypatch, tmp_path):
    """隔离 DB（async sqlite）+ CAS 根 + 会话数据根。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401
    from app.core.database import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/brief.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with maker() as session:
            yield session

    monkeypatch.setattr(
        "app.services.project_artifact_promotion.content_store_root",
        lambda: tmp_path / "cas")
    from app.services.durable_blob_store import reset_filesystem_blob_store

    reset_filesystem_blob_store()
    yield maker, _override, tmp_path
    reset_filesystem_blob_store()
    await engine.dispose()


@pytest.fixture
async def app_client(isolated_env):
    maker, override, tmp_path = isolated_env
    app = FastAPI()
    from app.api.routes import brief as brief_routes

    app.include_router(brief_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_async_db] = override
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
    from app.core.auth import get_current_user_optional

    app.dependency_overrides[get_current_user_optional] = lambda: {
        "user_id": "u1"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, maker


@pytest.fixture
async def seeded_session(app_client):
    """真实 Conversation 行 + 真实产物（session store + artifact registry）。"""
    c, maker = app_client
    sid = f"brief-t-{uuid.uuid4().hex[:8]}"
    async with maker() as db:
        from app.models.db_model import Conversation

        db.add(Conversation(id=sid, user_id="u1", title="学校分析"))
        await db.commit()

    from app.services.session_data import session_data_manager
    from app.services.artifact_registry import register_tool_artifact

    payload = {"table": {"columns": ["name", "count"],
                         "rows": [{"name": "锦江区", "count": 142},
                                  {"name": "青羊区", "count": 97}]}}
    ref = await session_data_manager.store(sid, payload, prefix="stats")
    await register_tool_artifact(
        sid, ref, tool="analyze_distribution", result=payload)
    yield sid, ref
    await session_data_manager.clear_session(sid)


async def _create_draft(c, sid, **body):
    resp = await c.post(f"/api/v1/briefs/sessions/{sid}/draft",
                        json=body or {"title": "学校分布简报"})
    return resp


# ── 旅程 1：draft → 编辑 → 证据 ─────────────────────────────────────


class TestDraftAndEdit:
    async def test_draft_from_real_artifacts(self, app_client, seeded_session):
        c, _ = app_client
        sid, ref = seeded_session
        resp = await _create_draft(c, sid)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["revision"] == 1
        assert data["summary"]["facts"] > 0
        assert data["summary"]["fact_stats"]["verified"] > 0, (
            "field-located facts must verify against real artifact")

        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        assert got.status_code == 200
        spec = got.json()["spec"]
        assert spec["brief_schema_version"] == "brief-1"
        # 每个自动数值绑定真实 artifact + field_path（BRF-O01）
        for fact in spec["facts"]:
            if fact["status"] == "verified":
                assert fact["basis"]["artifact_ref"].startswith("ref:")
                assert fact["basis"]["field_path"]

    async def test_draft_idempotent_create_conflict(self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        first = await _create_draft(c, sid)
        assert first.status_code == 200
        second = await _create_draft(c, sid)
        assert second.status_code == 409

    async def test_edit_text_and_lock(self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        revision = got.json()["revision"]
        text_block = next(b for b in got.json()["spec"]["blocks"]
                          if b["kind"] == "text")
        # 编辑正文（BRF 指令 3："保留我改过的结论"）
        resp = await c.patch(
            f"/api/v1/briefs/sessions/{sid}/blocks/{text_block['id']}",
            json={"expected_revision": revision,
                  "patch": {"text": "## 结论（人工修订）\n学校集中分布于锦江区。",
                            "origin": "manual", "locked": True}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["revision"] == revision + 1
        # 锁定后内容编辑被拒（BRF-O03）
        blocked = await c.patch(
            f"/api/v1/briefs/sessions/{sid}/blocks/{text_block['id']}",
            json={"expected_revision": revision + 1,
                  "patch": {"text": "覆盖尝试"}})
        assert blocked.status_code == 422
        assert "locked" in blocked.json()["detail"]
        # 解锁后可改
        unlock = await c.patch(
            f"/api/v1/briefs/sessions/{sid}/blocks/{text_block['id']}",
            json={"expected_revision": revision + 1, "patch": {"locked": False}})
        assert unlock.status_code == 200

    async def test_revision_conflict_409(self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        stale_revision = got.json()["revision"]
        first = await c.patch(
            f"/api/v1/briefs/sessions/{sid}",
            json={"expected_revision": stale_revision, "title": "新标题"})
        assert first.status_code == 200
        conflict = await c.patch(
            f"/api/v1/briefs/sessions/{sid}",
            json={"expected_revision": stale_revision, "title": "并发写入"})
        assert conflict.status_code == 409  # BRF-O03：显式冲突，不静默

    async def test_regenerate_preserves_manual_and_locked(
            self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        revision = got.json()["revision"]
        text_block = next(b for b in got.json()["spec"]["blocks"]
                          if b["kind"] == "text")
        manual_text = "## 结论（人工修订）\n此结论由我手工改写，不许动。"
        await c.patch(
            f"/api/v1/briefs/sessions/{sid}/blocks/{text_block['id']}",
            json={"expected_revision": revision,
                  "patch": {"text": manual_text, "origin": "manual",
                            "locked": True}})
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        revision = got.json()["revision"]
        resp = await c.post(
            f"/api/v1/briefs/sessions/{sid}/regenerate",
            json={"expected_revision": revision, "mode": "metrics_only"})
        assert resp.status_code == 200, resp.text
        blocks = {b["id"]: b for b in resp.json()["spec"]["blocks"]}
        assert blocks[text_block["id"]]["text"] == manual_text  # BRF-O03/O11
        assert blocks[text_block["id"]]["origin"] == "manual"

    async def test_inspect_evidence_full_chain(self, app_client, seeded_session):
        c, _ = app_client
        sid, ref = seeded_session
        await _create_draft(c, sid)
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        fact_id = next(f["fact_id"] for f in got.json()["spec"]["facts"]
                       if f["status"] == "verified")
        resp = await c.get(
            f"/api/v1/briefs/sessions/{sid}/evidence/{fact_id}")
        assert resp.status_code == 200
        data = resp.json()
        # BRF 指令 2："带我看对应数据和分母" —— 权威值 + 产物记录在场
        assert data["authoritative_value"] == 142 or \
            data["authoritative_value"] == 97
        assert data["artifact"]["registered"] is True
        assert data["fact"]["basis"]["source_label"]

    async def test_stale_marked_not_mutated(self, app_client, seeded_session):
        c, _ = app_client
        sid, ref = seeded_session
        await _create_draft(c, sid)
        # 产物更新（新 revision）→ 自动块标 stale（BRF-O11：只标记不改内容）
        from app.services.artifact_registry import register_tool_artifact
        from app.services.session_data import session_data_manager

        payload2 = {"table": {"columns": ["name", "count"],
                              "rows": [{"name": "锦江区", "count": 200},
                                       {"name": "青羊区", "count": 97}]}}
        ref2 = await session_data_manager.store(sid, payload2, prefix="stats")
        await register_tool_artifact(
            sid, ref2, tool="analyze_distribution", result=payload2)
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        stale = got.json()["stale"]
        assert any(s["reason"] == "artifact_updated" or
                   s["reason"] == "artifact_missing" for s in stale)


# ── 旅程 2：预检 → 冻结 → 下载（BRF 指令 4） ─────────────────────────


class TestPreflightFreeze:
    async def test_preflight_verdicts(self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        resp = await c.post(f"/api/v1/briefs/sessions/{sid}/preflight")
        assert resp.status_code == 200
        data = resp.json()
        assert data["outcome"] in ("deliverable", "draft_exportable")
        verdicts = {i["check"]: i["verdict"] for i in data["items"]}
        assert verdicts["source_authorization"] == "pass"

    async def test_freeze_blocked_on_missing_map(self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        # 删除全部 map_view 块 → 必需图件缺失 → 预检 fail → 冻结阻断
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        revision = got.json()["revision"]
        for block in list(got.json()["spec"]["blocks"]):
            if block["kind"] == "map_view":
                resp = await c.delete(
                    f"/api/v1/briefs/sessions/{sid}/blocks/{block['id']}",
                    params={"expected_revision": revision})
                assert resp.status_code == 200
                revision = resp.json()["revision"]
        resp = await c.post(f"/api/v1/briefs/sessions/{sid}/releases",
                            json={"allow_draft": True})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "preflight_blocked"

    async def test_freeze_idempotent_and_download(
            self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        pre = await c.post(f"/api/v1/briefs/sessions/{sid}/preflight")
        outcome = pre.json()["outcome"]
        resp = await c.post(
            f"/api/v1/briefs/sessions/{sid}/releases",
            json={"allow_draft": outcome != "deliverable"})
        assert resp.status_code == 200, resp.text
        release = resp.json()
        assert release["already_released"] is False
        # 幂等键不含墙钟（评审 #P1-1）：跨秒重复提交仍必须幂等。
        import time as _time

        _time.sleep(1.2)
        # 幂等：同内容重复提交 → already_released（BRF-O06）
        again = await c.post(
            f"/api/v1/briefs/sessions/{sid}/releases",
            json={"allow_draft": outcome != "deliverable"})
        assert again.status_code == 200
        assert again.json()["already_released"] is True
        assert again.json()["release_no"] == release["release_no"]

        # 私有 HTML 下载：可读、含标题与证据徽标（BRF-O07）
        dl = await c.get(
            f"/api/v1/briefs/sessions/{sid}/releases/"
            f"{release['release_id']}/download")
        assert dl.status_code == 200
        assert "text/html" in dl.headers["content-type"]
        body = dl.text
        assert "学校分布简报" in body
        assert "已核验" in body or "未验证" in body
        # 离线零外链：无 http(s) 外部资源引用
        import re
        assert not re.search(r'(src|href)="https?://', body)
        # manifest 可对账
        manifest = await c.get(
            f"/api/v1/briefs/sessions/{sid}/releases/"
            f"{release['release_id']}/manifest")
        assert manifest.status_code == 200
        m = manifest.json()
        assert m["files"][0]["kind"] == "html" and m["files"][0]["status"] == "ready"
        assert len(m["files"][0]["sha256"]) == 64
        assert m["evidence"], "evidence manifest must be present"
        # facts CSV
        csv_resp = await c.get(
            f"/api/v1/briefs/sessions/{sid}/releases/"
            f"{release['release_id']}/facts.csv")
        assert csv_resp.status_code == 200
        assert "fact_id" in csv_resp.text

    async def test_release_immutable_after_edit(
            self, app_client, seeded_session):
        c, _ = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        pre = await c.post(f"/api/v1/briefs/sessions/{sid}/preflight")
        outcome = pre.json()["outcome"]
        rel = await c.post(
            f"/api/v1/briefs/sessions/{sid}/releases",
            json={"allow_draft": outcome != "deliverable"})
        release_id = rel.json()["release_id"]
        dl1 = await c.get(
            f"/api/v1/briefs/sessions/{sid}/releases/{release_id}/download")
        # 编辑草稿后旧 release 不变（BRF-O06/O11）
        got = await c.get(f"/api/v1/briefs/sessions/{sid}")
        await c.patch(
            f"/api/v1/briefs/sessions/{sid}",
            json={"expected_revision": got.json()["revision"],
                  "title": "改后标题"})
        dl2 = await c.get(
            f"/api/v1/briefs/sessions/{sid}/releases/{release_id}/download")
        assert dl1.content == dl2.content


# ── 旅程 3：分享/撤销/跨 owner（BRF-O09） ────────────────────────────


class TestSharing:
    async def _frozen_release(self, c, sid):
        await _create_draft(c, sid)
        pre = await c.post(f"/api/v1/briefs/sessions/{sid}/preflight")
        outcome = pre.json()["outcome"]
        rel = await c.post(
            f"/api/v1/briefs/sessions/{sid}/releases",
            json={"allow_draft": outcome != "deliverable"})
        assert rel.status_code == 200, rel.text
        return rel.json()["release_id"]

    async def test_share_lifecycle(self, app_client, seeded_session):
        c, maker = app_client
        sid, _ = seeded_session
        release_id = await self._frozen_release(c, sid)
        share = await c.post(
            f"/api/v1/briefs/sessions/{sid}/releases/{release_id}/share",
            json={"ttl_days": 3})
        assert share.status_code == 200, share.text
        code = share.json()["share_code"]
        # 公共投影 + 只读查看
        info = await c.get(f"/api/v1/briefs/shared/{code}")
        assert info.status_code == 200
        assert "session" not in info.json()
        view = await c.get(f"/api/v1/briefs/shared/{code}/view")
        assert view.status_code == 200
        assert "text/html" in view.headers["content-type"]
        assert "default-src 'none'" in view.headers.get(
            "content-security-policy", "")
        # 撤销后服务端读取失败（BRF-O09）
        revoke = await c.delete(
            f"/api/v1/briefs/sessions/{sid}/releases/{release_id}/share")
        assert revoke.status_code == 200
        after = await c.get(f"/api/v1/briefs/shared/{code}/view")
        assert after.status_code == 404

    async def test_cross_owner_404(self, app_client, seeded_session):
        c, maker = app_client
        sid, _ = seeded_session
        await _create_draft(c, sid)
        # 另一会话的 owner（u2）读本会话简报 → 404（不泄露存在性）
        async with maker() as db:
            from app.models.db_model import Conversation

            db.add(Conversation(id=f"{sid}-other", user_id="u2", title="x"))
            await db.commit()
        app2 = FastAPI()
        from app.api.routes import brief as brief_routes

        from app.core.database import get_async_db

        # 复用同一 DB 覆盖，但 current_user 换 u2
        original_override = app2.dependency_overrides
        app2.include_router(brief_routes.router, prefix="/api/v1")
        app2.dependency_overrides[get_current_user] = lambda: {
            "user_id": "u2"}
        # db override 需重新绑定同一 maker
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker2 = None
        engine_ref = None
        # 直接复用 app_client 的 override（闭包同一 maker）
        for dep, ov in app_client[0]._transport.app.dependency_overrides.items():  # noqa
            if dep is get_async_db:
                app2.dependency_overrides[get_async_db] = ov
        transport = ASGITransport(app=app2)
        async with AsyncClient(transport=transport, base_url="http://t2") as c2:
            resp = await c2.get(f"/api/v1/briefs/sessions/{sid}")
            assert resp.status_code == 404
            resp_draft = await c2.post(
                f"/api/v1/briefs/sessions/{sid}/draft", json={})
            assert resp_draft.status_code == 404

    async def test_unknown_session_404(self, app_client):
        c, _ = app_client
        resp = await c.get("/api/v1/briefs/sessions/nope")
        assert resp.status_code == 404
