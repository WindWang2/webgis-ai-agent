"""Deep-review API/DATA batch — review merge status codes + lock error mapping.

API-08：merge 冲突/回滚/终态 → 409；引擎失败 → 422（不再 200 掩盖）。
API-09：LockDegraded/LockLost 分支可达（session_lock_unavailable 503）。
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import mapspec_mutations as mapspec_routes
from app.api.routes import review_proposals as review_routes
from app.core.auth import require_owned_session
from app.models.db_model import Conversation
from app.services.distributed_lock import (
    LockContentionError,
    LockDegradedError,
    LockLostError,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    SetViewIntent,
    UpsertLayerIntent,
)
from app.services.review.merge import MergeOutcome

_USER = {"user_id": "u1", "role": "editor", "org_id": "org1"}


def _review_app(session_id: str) -> FastAPI:
    application = FastAPI()
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

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
    application.dependency_overrides[
        review_routes.get_current_user_optional
    ] = lambda: _USER
    application.include_router(review_routes.router, prefix="/api/v1")
    return application


@pytest.fixture
async def review_client():
    sid = f"rv-dr-{uuid4().hex[:8]}"
    transport = ASGITransport(app=_review_app(sid))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["x-test-sid"] = sid
        yield c


async def _seed_layer(sid: str):
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    r = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "L1", "source": "s", "type": "circle",
                   "paint": {"circle-color": "#123456"}},
            source_data={"type": "FeatureCollection", "features": []},
        ),
    )
    return engine, r.mutation_revision


def test_merge_error_status_mapping():
    assert review_routes._merge_error_status(MergeOutcome(ok=True)) is None
    assert review_routes._merge_error_status(
        MergeOutcome(ok=False, conflict=True, failure="base_revision_drift")
    ) == 409
    assert review_routes._merge_error_status(
        MergeOutcome(ok=False, interleaved=True, failure="interleaved")
    ) == 409
    assert review_routes._merge_error_status(
        MergeOutcome(ok=False, rolled_back=True, failure="rolled_back")
    ) == 409
    assert review_routes._merge_error_status(
        MergeOutcome(ok=False, failure="already_merged")
    ) == 409
    assert review_routes._merge_error_status(
        MergeOutcome(ok=False, failure="checkpoint_failed")
    ) == 422


@pytest.mark.asyncio
async def test_merge_base_drift_returns_409_with_outcome(review_client):
    sid = review_client.headers["x-test-sid"]
    engine, base = await _seed_layer(sid)
    created = await review_client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals",
        json={
            "title": "冲突探针",
            "mutation_intents": [{
                "intent": "patch_layer_style", "expected_revision": base,
                "layer_id": "L1", "paint": {"circle-color": "#654321"},
            }],
        },
    )
    pid = created.json()["proposal"]["proposal_id"]
    await review_client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/submit",
        json={"base_revision": base},
    )
    approved = await review_client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/decisions",
        json={"decision": "approve", "reason": "ok"},
    )
    assert approved.json()["proposal"]["status"] == "approved"

    # 批准后他人推进 revision → merge 必须冲突。
    await engine.apply_mutation(sid, SetViewIntent(zoom=20), origin="agent")

    r = await review_client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/{pid}/merge"
    )
    assert r.status_code == 409, r.text
    body = r.json()
    payload = body.get("data") or body.get("detail") or {}
    assert payload["merge_outcome"]["conflict"] is True
    assert payload["merge_outcome"]["ok"] is False
    assert payload["proposal"]["status"] == "approved"


@pytest.mark.asyncio
async def test_merge_engine_failure_returns_422(review_client, monkeypatch):
    sid = review_client.headers["x-test-sid"]

    class _StubService:
        async def merge(self, session_id, proposal_id, actor):
            return SimpleNamespace(proposal_id=proposal_id), MergeOutcome(
                ok=False, failure="checkpoint_failed",
            )

        async def get_proposal_projection(self, session_id, proposal_id):
            return {"proposal": {"proposal_id": proposal_id, "status": "approved"}}

    monkeypatch.setattr(review_routes, "review_service", _StubService())
    r = await review_client.post(
        f"/api/v1/chat/sessions/{sid}/review/proposals/rp_dr/merge"
    )
    assert r.status_code == 422, r.text
    body = r.json()
    payload = body.get("data") or body.get("detail") or {}
    assert payload["merge_outcome"]["failure"] == "checkpoint_failed"


def _mapspec_app(session_id: str) -> FastAPI:
    application = FastAPI()
    application.dependency_overrides[require_owned_session] = (
        lambda: Conversation(id=session_id)
    )
    application.include_router(mapspec_routes.router, prefix="/api/v1")
    return application


class _Raising:
    def __init__(self, exc: BaseException):
        self._exc = exc

    def __call__(self, *args, **kwargs):
        raise self._exc


def test_lock_error_mapping_branches(monkeypatch):
    from fastapi.testclient import TestClient

    cases = [
        (LockContentionError("busy"), "session_busy"),
        (TimeoutError("slow"), "session_busy"),
        (LockDegradedError("degraded"), "session_lock_unavailable"),
        (LockLostError("lost"), "session_lock_unavailable"),
    ]
    for exc, expected in cases:
        sid = f"map-dr-{uuid4().hex[:8]}"
        monkeypatch.setattr(
            "app.services.gis_world_state.apply_gis_mutation", _Raising(exc),
        )
        client = TestClient(_mapspec_app(sid))
        r = client.post(
            f"/api/v1/chat/sessions/{sid}/mapspec/mutations",
            json={"intent": "set_view", "expected_revision": 1, "zoom": 10},
        )
        assert r.status_code == 503, (type(exc).__name__, r.text)
        assert r.json()["detail"]["error"] == expected, type(exc).__name__
