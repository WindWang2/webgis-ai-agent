"""Review API 路由测试（router 级 FastAPI + dependency_overrides，仓库同款）。"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import review_proposals as _mod
from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)

_USER = {"user_id": "u1", "role": "editor", "org_id": "org1"}


def _app(session_id: str, user: dict | None = _USER) -> FastAPI:
    application = FastAPI()
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError
    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )

    application.add_exception_handler(
        StarletteHTTPException, unified_http_exception_handler,
    )
    application.add_exception_handler(
        RequestValidationError, unified_validation_exception_handler,
    )
    application.dependency_overrides[require_owned_session] = (
        lambda: Conversation(id=session_id)
    )
    if user is not None:
        application.dependency_overrides[
            _mod.get_current_user_optional
        ] = lambda: user
    application.include_router(_mod.router, prefix="/api/v1")
    return application


@pytest.fixture
async def client():
    sid = f"rv-api-{uuid4().hex[:8]}"
    transport = ASGITransport(app=_app(sid))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["x-test-sid"] = sid
        yield c


async def _seed_layer(sid: str) -> int:
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    r = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "L1", "source": "s", "type": "circle", "paint": {"circle-color": "#123456"}},
            source_data={"type": "FeatureCollection", "features": []},
        ),
    )
    return r.mutation_revision


def _intents(base: int):
    return [{
        "intent": "patch_layer_style", "expected_revision": base,
        "layer_id": "L1", "paint": {"circle-color": "#654321"},
    }]


@pytest.mark.asyncio
async def test_http_full_flow(client):
    sid = client.headers["x-test-sid"]
    base = await _seed_layer(sid)
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals",
        json={"title": "HTTP 流", "mutation_intents": _intents(base)},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["proposal"]["proposal_id"]
    assert r.json()["proposal"]["status"] == "draft"

    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/submit",
        json={"base_revision": base},
    )
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "submitted"

    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/comments",
        json={"body": "看一下这层", "anchor": {"kind": "layer", "id": "L1"}},
    )
    assert r.status_code == 200
    assert r.json()["anchor_states"]

    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/decisions",
        json={"decision": "approve", "reason": "ok"},
    )
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "approved"

    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/merge"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["merge_outcome"]["ok"] is True
    assert body["proposal"]["status"] == "merged"
    assert body["proposal"]["merge_evidence"]["applied"]


@pytest.mark.asyncio
async def test_http_error_mapping(client):
    sid = client.headers["x-test-sid"]
    base = await _seed_layer(sid)
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals",
        json={"title": "t", "mutation_intents": _intents(base)},
    )
    pid = r.json()["proposal"]["proposal_id"]
    # draft 直接 merge → 409
    r = await client.post(f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/merge")
    assert r.status_code == 409
    # 未知 proposal → 404
    r = await client.get(f"/api/v1/chat/sessions/{sid}/review/proposals/ghost")
    assert r.status_code == 404
    # 非作者 submit → 403
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/submit",
        json={"base_revision": base},
    )
    assert r.status_code == 200  # u1 是作者
    # 匿名用户上下文（无 user override 的 app）：高风险 proposal 匿名不可批
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals",
        json={
            "title": "高危探针",
            "mutation_intents": [
                {"intent": "remove_layer", "expected_revision": base, "layer_id": "L1"},
            ],
        },
    )
    pid_high = r.json()["proposal"]["proposal_id"]
    anon_app = _app(sid, user={"user_id": "anonymous", "role": "anonymous"})
    transport = ASGITransport(app=anon_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c2:
        r = await c2.post(
            f"/api/v1/chat/sessions/{sid}/review/proposals/{pid_high}/submit",
            json={"base_revision": base},
        )
        assert r.status_code == 403  # 匿名非作者（u1 才是作者）
        # 以作者身份（匿名 owner）提交后自批 → 高风险自批 403
        author_app = _app(sid, user=_USER)
        transport2 = ASGITransport(app=author_app)
        async with AsyncClient(transport=transport2, base_url="http://test") as c3:
            r = await c3.post(
                f"/api/v1/chat/sessions/{sid}/review/proposals/{pid_high}/submit",
                json={"base_revision": base},
            )
            assert r.status_code == 200
            r = await c3.post(
                f"/api/v1/chat/sessions/{sid}/review/proposals/{pid_high}/decisions",
                json={"decision": "approve"},
            )
            assert r.status_code == 403  # 高风险自批拒绝


@pytest.mark.asyncio
async def test_http_high_risk_self_approve_403(client):
    sid = client.headers["x-test-sid"]
    base = await _seed_layer(sid)
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals",
        json={
            "title": "删层",
            "mutation_intents": [
                {"intent": "remove_layer", "expected_revision": base, "layer_id": "L1"},
            ],
        },
    )
    pid = r.json()["proposal"]["proposal_id"]
    await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/submit",
        json={"base_revision": base},
    )
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/decisions",
        json={"decision": "approve"},
    )
    assert r.status_code == 403  # 自批高风险（u1=作者, editor）


@pytest.mark.asyncio
async def test_http_export_allowlist(client):
    sid = client.headers["x-test-sid"]
    base = await _seed_layer(sid)
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals",
        json={"title": "导出探针", "mutation_intents": _intents(base)},
    )
    pid = r.json()["proposal"]["proposal_id"]
    await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/submit",
        json={"base_revision": base},
    )
    r = await client.get(f"/api/v1/chat/sessions/{sid}/review/export")
    assert r.status_code == 200
    body = r.json()
    assert body["proposal_count"] == 1
    raw = str(body)
    assert "owner_token" not in raw
    assert "authorization" not in raw.lower()
    assert "reasoning" not in raw


@pytest.mark.asyncio
async def test_http_feature_off_404(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "REVIEW_WORKFLOW_ENABLED", False)
    sid = client.headers["x-test-sid"]
    r = await client.get(f"/api/v1/chat/sessions/{sid}/review/proposals")
    assert r.status_code == 404
    assert "disabled" in str(r.json())


@pytest.mark.asyncio
async def test_http_malformed_intent_422(client):
    sid = client.headers["x-test-sid"]
    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals",
        json={"title": "t", "mutation_intents": [{"intent": "nope"}]},
    )
    assert r.status_code in (400, 422)
