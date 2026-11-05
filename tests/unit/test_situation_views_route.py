"""Situation 视图端点测试（ADR-0283 D4/D5）：只读派生 / 鉴权直通 /
journal 降级披露 / 选择史与 claimed scope 面在场。模式同
interaction_ir test_route.py。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import situation_views as _sv_mod
from app.api.routes.situation_views import router
from app.services.gis_harness.interaction_ir.contracts import LedgerState
from app.services.gis_harness.interaction_ir.ingest import apply_event
from app.services.session_data import MemorySessionStore


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


@pytest.fixture(autouse=True)
def _stub_ownership(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


def _seed_ledger(store: MemorySessionStore, session_id: str) -> None:
    state = LedgerState()
    apply_event(state, {
        "event_id": "c-1", "client_instance_id": "t", "client_seq": 1,
        "kind": "feature_selected",
        "payload": {"layer_id": "schools", "dataset_ref": "ref:x",
                    "selected_ids": ["sch-1"]},
    })
    store._map_state.setdefault(session_id, {})["_interaction_ir_ledger"] = state.events


@pytest.mark.asyncio
async def test_get_situation_view_composites(client):
    store = MemorySessionStore()
    _seed_ledger(store, "s1")
    with patch.object(_sv_mod, "session_data_manager", new=store), \
         patch("app.services.mapspec.store.mapspec_store_instance", new=MagicMock()):
        resp = await client.get("/api/v1/chat/sessions/s1/situation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == "situation_view.v1"
    assert "situation" in body and "inconsistencies" in body
    assert isinstance(body["selection_history"], list)
    assert body["claimed_scope"]["kind"] == "feature_ids"
    assert body["claimed_scope"]["feature_ids"] == ["sch-1"]


@pytest.mark.asyncio
async def test_get_turn_chain_readonly_with_degradation(client):
    store = MemorySessionStore()
    store._map_state.setdefault("s1", {})["_map_operation_receipts"] = [{
        "schema_version": "map_operation.v1", "operation_id": "op-1",
        "mode": "commit", "origin": "agent", "status": "preview",
        "base_revision": 1, "provenance": {"turn_id": "t1"},
        "effects": [], "applied_count": 0,
    }]

    class _NoLedger:
        async def list_events(self, *a, **k):
            raise RuntimeError("db down")

    with patch.object(_sv_mod, "session_data_manager", new=store), \
         patch("app.services.mapspec.store.mapspec_store_instance", new=MagicMock()), \
         patch("app.services.turn_journal.ledger.TurnEventLedger",
               side_effect=RuntimeError("no ledger")):
        resp = await client.get("/api/v1/chat/sessions/s1/turn-chain")
    assert resp.status_code == 200
    body = resp.json()
    # journal 不可达 → degraded 披露 + 回执仍构成链（fail-open）。
    assert any("turn_journal" in d for d in body.get("degraded", []))
    assert body["turns"], "receipts must still form a chain"
    changes = body["turns"][0]["changes"]
    assert any(c["class"] == "agent_proposed_pending" for c in changes)


@pytest.mark.asyncio
async def test_get_turn_chain_happy_path_three_classes(client):
    store = MemorySessionStore()
    store._map_state.setdefault("s2", {}).update({
        "_map_operation_receipts": [
            {"schema_version": "map_operation.v1", "operation_id": "op-ok",
             "mode": "commit", "origin": "agent", "status": "committed",
             "mutation_revisions": [4], "provenance": {"turn_id": "t1"},
             "effects": [{"kind": "set_layer_visibility",
                          "targets": ["heatmap"], "state": "applied"}],
             "applied_count": 1},
            {"schema_version": "map_operation.v1", "operation_id": "op-pend",
             "mode": "commit", "origin": "agent",
             "status": "confirmation_required", "base_revision": 4,
             "provenance": {"turn_id": "t1"}, "effects": [], "applied_count": 0},
        ],
        "_interaction_ir_ledger": [
            {"event_id": "c-1", "client_instance_id": "t", "client_seq": 1,
             "server_seq": 1, "kind": "layer_visibility_changed",
             "payload": {"layer_id": "heatmap", "visible": False},
             "client_mutation_id": "m-1", "mutation_outcome": "committed"},
        ],
        "_cartographic_observation": {"mapspec_revision": 4},
    })

    with patch.object(_sv_mod, "session_data_manager", new=store), \
         patch("app.services.mapspec.store.mapspec_store_instance", new=MagicMock()), \
         patch("app.services.turn_journal.ledger.TurnEventLedger",
               side_effect=RuntimeError("use fake")):
        # route 不注入 ledger → project_turn_chain 内部构造 TurnEventLedger；
        # patch 构造器抛错 → degraded 路径（三类断言仍由回执/IR 构成）。
        resp = await client.get("/api/v1/chat/sessions/s2/turn-chain")
    assert resp.status_code == 200
    body = resp.json()
    classes = {
        c["class"]
        for turn in body["turns"] for c in turn["changes"]
    }
    assert "user_explicit" in classes
    assert "agent_proposed_pending" in classes
    assert "executed_observed" in classes
    counts = body["counts"]
    assert counts["executed_observed"] >= 1
    assert counts["agent_pending"] >= 1
