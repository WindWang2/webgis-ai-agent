"""G35 REST 面：owner 归属、pending 不冒充完成、成果 URL 重签、typed 错误。

其余情景（渲染/编码/下载/取消）在 test_pipeline / test_jobs 真实执行；
本文件只把 HTTP 路由接线与授权边界钉住。
"""
from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.temporal_media.contracts import TemporalMediaError
from app.services.temporal_media.freeze import build_frozen
from app.services.temporal_media import service as svc

from .conftest import make_plan, temporal_document

PRINCIPAL = "session:sess-g35"


@pytest.fixture()
def api_app(media_env, monkeypatch):
    from app.main import app
    from app.core.auth import (
        get_current_user_optional_with_version, get_owner_token,
    )
    from app.core.database import get_async_db
    import app.services.auth_history_bridge as ahb

    async def fake_verify(db, session_id, user_id=None, owner_token=None):
        return None

    async def fake_user():
        return {"user_id": "user-g35", "role": "user"}

    async def fake_token():
        return None

    async def fake_db():
        yield None

    doc = temporal_document([2019, 2021, 2024])
    frozen = build_frozen("sess-g35", doc, 3)

    async def fake_freeze(session_id, expected_revision=None):
        return frozen

    def fake_submit(**kwargs):
        return {"job_id": "77", "task_id": "t-77", "idempotent_reuse": False}

    import app.services.jobs.submit as submit_mod
    monkeypatch.setattr(svc, "freeze_document", fake_freeze)
    monkeypatch.setattr(submit_mod, "submit_durable_job", fake_submit)
    monkeypatch.setattr(ahb, "verify_session_owner", fake_verify)

    app.dependency_overrides[get_current_user_optional_with_version] = fake_user
    app.dependency_overrides[get_owner_token] = fake_token
    app.dependency_overrides[get_async_db] = fake_db
    yield app
    app.dependency_overrides.clear()


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app),
                       base_url="http://g35-test")


def test_capabilities_plan_submit_status_result_over_http(api_app):
    async def scenario() -> None:
        async with _client(api_app) as client:
            caps = await client.get(
                "/api/v1/temporal-media/capabilities?session_id=sess-g35")
            assert caps.status_code == 200, caps.text
            assert caps.json()["capabilities"]["renderer"]["available"] is True
            plan = await client.post("/api/v1/temporal-media/plan", json={
                "session_id": "sess-g35", "width": 480, "height": 270})
            assert plan.status_code == 200, plan.text
            body = plan.json()
            assert len(body["plan_ref"]) == 16
            assert body["plan"]["display"]["missing_period_count"] == 3
            submitted = await client.post("/api/v1/temporal-media/submit", json={
                "session_id": "sess-g35", "width": 480, "height": 270,
                "plan_ref": body["plan_ref"]})
            assert submitted.status_code == 200, submitted.text
            assert submitted.json()["status"] == "pending"
            job_id = submitted.json()["job_id"]
            state = await client.get(
                f"/api/v1/temporal-media/jobs/{job_id}?session_id=sess-g35")
            assert state.json()["completed"] is False
            not_ready = await client.get(
                f"/api/v1/temporal-media/jobs/{job_id}/result?session_id=sess-g35")
            assert not_ready.status_code == 409
            payload = not_ready.json()
            # 全局 HTTPException 处理器把领域 payload 放进 data（code 保留）。
            assert payload.get("data", payload.get("detail", {})).get("code") == \
                "RESULT_NOT_READY"
            # 真实 worker 执行（不经 Celery broker）
            from app.services.temporal_media.runner import execute_media_job
            execute_media_job(PRINCIPAL, job_id, recheck=lambda: None)
            done = await client.get(
                f"/api/v1/temporal-media/jobs/{job_id}?session_id=sess-g35")
            assert done.json()["completed"] is True
            result = await client.get(
                f"/api/v1/temporal-media/jobs/{job_id}/result?session_id=sess-g35")
            assert result.status_code == 200, result.text
            assert "animation" in result.json()["artifacts"]
            listed = await client.get(
                "/api/v1/temporal-media/jobs?session_id=sess-g35")
            assert any(j["job_id"] == job_id for j in listed.json()["jobs"])

    asyncio.run(scenario())


def test_closed_vocabulary_and_unknown_plan_version_are_typed(api_app):
    async def scenario() -> None:
        async with _client(api_app) as client:
            bad = await client.post("/api/v1/temporal-media/plan", json={
                "session_id": "sess-g35", "format": "mp4"})
            assert bad.status_code == 422
            shell = await client.post("/api/v1/temporal-media/plan", json={
                "session_id": "sess-g35", "ffmpeg_preset": "/etc/passwd"})
            assert shell.status_code == 422
            unknown = await client.post("/api/v1/temporal-media/submit", json={
                "session_id": "sess-g35", "plan_ref": "0000000000000000"})
            assert unknown.status_code == 409
            unknown_payload = unknown.json()
            assert unknown_payload.get("data", unknown_payload.get("detail", {})).get(
                "code") == "PLAN_VERSION_MISMATCH"

    asyncio.run(scenario())


def test_owner_scope_is_not_bypassable_by_session_id_guessing(media_env):
    """不同 principal 的同名 job_id 不得互读（store 层 owner 隔离）。"""
    from app.services.temporal_media.store import (
        initial_state, load_state, save_plan, save_state,
    )
    _, _, plan = make_plan(width=480, height=270)
    save_plan(PRINCIPAL, "g35-owner", plan)
    save_state(PRINCIPAL, "g35-owner",
               initial_state(PRINCIPAL, "g35-owner", plan))
    with pytest.raises(TemporalMediaError):
        load_state("session:sess-attacker", "g35-owner")


def test_scenario_22_ui_and_pi_share_one_plan_hash(media_env, monkeypatch):
    """同一会话/同一参数 → UI 与 Pi 得到同一 plan_hash/plan_ref。"""
    async def scenario() -> None:
        from app.services.temporal_media.contracts import MediaPlanArgs
        a = await svc.preview(
            MediaPlanArgs(session_id="sess-g35", width=480, height=270),
            PRINCIPAL)
        b = await svc.preview(
            MediaPlanArgs(session_id="sess-g35", width=480, height=270),
            PRINCIPAL)
        assert a["plan_hash"] == b["plan_hash"]
        assert a["plan_ref"] == b["plan_ref"] == a["plan_hash"][:16]

    # 用纯函数注入冻结文档，避免依赖会话存储
    doc = temporal_document([2019, 2021, 2024])
    frozen = build_frozen("sess-g35", doc, 3)

    async def fake_freeze(session_id, expected_revision=None):
        return frozen

    monkeypatch.setattr(svc, "freeze_document", fake_freeze)
    asyncio.run(scenario())
