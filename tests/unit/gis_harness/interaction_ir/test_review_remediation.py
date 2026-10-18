"""Interaction IR review 清偿回归（round-1 独立 review P1/P2 项）：

- P1-1 `_render_interactions` IR 形态渲染（kind@server_seq + 摘要子集，
  新→旧取头 3 条）；
- P1-2 map-state 恢复面剥离 IR 内部键（含 _updated_at 伴生）；
- P2-1 新实例首 seq 不披露假 gap；
- P2-3 持久化降级不携带 watermarks；
- P2-4 ledger 读失败 → 503（不折叠为空态覆写）；
- P3-4 记录预算常量关系断言。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.gis_harness.interaction_ir.contracts import (
    MAX_PAYLOAD_BYTES,
    LedgerState,
)
from app.services.gis_harness.interaction_ir.ingest import (
    LEDGER_KEY,
    VERDICT_ACCEPTED,
    apply_event,
)
from app.services.gis_situation.projection import _render_interactions


# ── P1-1 渲染双形态 ────────────────────────────────────────────────────


def _fold_ir(events):
    state = LedgerState()
    for e in events:
        apply_event(state, e)
    from app.services.gis_harness.interaction_ir.projection import (
        project_interaction_snapshot,
    )

    return project_interaction_snapshot(state.events)["recent_interactions"]


def test_render_interactions_ir_shape():
    """IR 投影条目（新→旧、无 payload 键）→ kind@server_seq + 引用摘要，
    取**头 3 条**（= 最近 3 条）。"""
    value = _fold_ir([
        {"event_id": "r-00000001", "client_instance_id": "t-aaaa0001", "client_seq": 1,
         "kind": "viewport_settled", "payload": {"center": [116, 39], "zoom": 10}},
        {"event_id": "r-00000002", "client_instance_id": "t-aaaa0001", "client_seq": 2,
         "kind": "feature_selected", "payload": {"layer_id": "roads", "feature_id": "f1"}},
        {"event_id": "r-00000003", "client_instance_id": "t-aaaa0001", "client_seq": 3,
         "kind": "aoi_drawn", "payload": {"geometry_type": "Polygon", "vertex_count": 4,
                                          "geometry_digest": "abc"}},
        {"event_id": "r-00000004", "client_instance_id": "t-aaaa0001", "client_seq": 4,
         "kind": "layer_visibility_changed",
         "payload": {"layer_id": "roads", "visible": False},
         "client_mutation_id": "mut-12345678", "mutation_outcome": "committed"},
    ])
    rendered = _render_interactions(value)
    assert rendered is not None
    bits = rendered.split(";")
    assert len(bits) == 3  # 头 3 条 = 最近
    # 最新在前（新→旧），seq 是 server_seq，摘要有 layer_id 而非 null。
    assert bits[0].startswith("layer_visibility_changed@4=")
    assert "layer_id" in bits[0]
    assert "roads" in bits[0]
    assert "outcome" in bits[0]
    assert bits[1].startswith("aoi_drawn@3=")
    assert bits[2].startswith("feature_selected@2=")
    # 无一条是 `@None=null`（P1-1 断裂形态）。
    assert "@None=null" not in rendered


def test_render_interactions_legacy_shape_unchanged():
    """legacy 环形态（旧→新、有 payload）→ 既有行为逐位保持（取尾 3）。"""
    value = [
        {"sequence": 1, "kind": "viewport_change", "payload": {"zoom": 9}},
        {"sequence": 2, "kind": "selection_change", "payload": {"layer_id": "a"}},
        {"sequence": 3, "kind": "layer_focus", "payload": {"layer_id": "b"}},
        {"sequence": 4, "kind": "layer_visibility", "payload": {"layer_id": "c"}},
    ]
    rendered = _render_interactions(value)
    bits = rendered.split(";")
    assert len(bits) == 3
    # legacy 旧→新：取尾 3 条 = seq 2/3/4，首条是最旧的 seq2。
    assert bits[0].startswith("selection_change@2=")
    assert bits[2].startswith("layer_visibility@4=")


# ── P1-2 恢复面剥离 ────────────────────────────────────────────────────


@pytest.fixture
def chat_client():
    from app.api.routes.chat import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_map_state_restore_strips_interaction_ir_keys(chat_client):
    from app.api.routes import chat as _chat_mod

    mock_sdm = MagicMock()
    mock_sdm.get_map_state = AsyncMock(return_value={
        "base_layer": "dark",
        "_interaction_ir_ledger": [{"event_id": "e1"}] * 128,
        "_interaction_ir_clients": {"tab-1": {"acked_seq": 9}},
        "_interaction_ir_meta": {"server_seq": 9, "dropped": 0},
        "_interaction_ir_ledger_updated_at": "2026-10-18T00:00:00Z",
        "_interaction_ir_meta_updated_at": "2026-10-18T00:00:01Z",
        "_situation_interactions": [{"sequence": 1}],
    })
    mock_conv = MagicMock()
    # route 内部延迟 import session_data_manager —— patch 源模块属性。
    with patch(
        "app.services.session_data.session_data_manager", mock_sdm,
    ), patch.object(
        _chat_mod.AsyncHistoryService, "get_session_meta",
        AsyncMock(return_value=mock_conv),
    ):
        resp = chat_client.get("/api/v1/chat/sessions/sess-ir-strip/map-state")
    assert resp.status_code == 200
    state = resp.json()["map_state"]
    assert state["base_layer"] == "dark"
    for key in (
        "_interaction_ir_ledger", "_interaction_ir_clients", "_interaction_ir_meta",
        "_interaction_ir_ledger_updated_at", "_interaction_ir_meta_updated_at",
        "_situation_interactions",
    ):
        assert key not in state, key


# ── P2-1 新实例首 seq 无假 gap ─────────────────────────────────────────


def test_new_instance_high_first_seq_no_fake_gap():
    state = LedgerState()
    v = apply_event(state, {
        "event_id": "fresh-000001", "client_instance_id": "tab-new00001",
        "client_seq": 57,  # 刷新续号 / 复制 tab 基线
        "kind": "viewport_settled", "payload": {"center": [1, 2], "zoom": 3},
    })
    assert v.verdict == VERDICT_ACCEPTED
    assert v.gap_from is None
    # 已知实例跳号仍披露。
    v2 = apply_event(state, {
        "event_id": "fresh-000002", "client_instance_id": "tab-new00001",
        "client_seq": 60, "kind": "viewport_settled",
        "payload": {"center": [1, 2], "zoom": 3},
    })
    assert v2.gap_from == 58


# ── P2-3 降级响应无 watermarks ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_degraded_response_omits_watermarks():
    from app.services.gis_harness.interaction_ir.ingest import (
        ingest_interaction_batch,
    )
    from app.services.session_data import MemorySessionStore

    class DegradedStore(MemorySessionStore):
        async def set_map_state(self, session_id, key, value):
            return False

    store = DegradedStore()
    resp = await ingest_interaction_batch(store, "sess-degraded", [{
        "event_id": "d-00000001", "client_instance_id": "tab-aaaa0001",
        "client_seq": 1, "kind": "viewport_settled",
        "payload": {"center": [1, 2], "zoom": 3},
    }])
    assert resp["accepted"] == 1
    assert resp["persist_degraded"] is True
    # 降级时水印来自内存态 —— 不得让客户端误信"已记账"。
    assert "watermarks" not in resp


# ── P2-4 读失败 → 503 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_read_failure_maps_to_503():
    from app.api.routes import interaction_events as _ie_mod
    from app.services.session_data import MemorySessionStore

    app = FastAPI()
    app.include_router(_ie_mod.router, prefix="/api/v1")
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()

    class FlakyStore(MemorySessionStore):
        async def get_state_field(self, session_id, field):
            if field.startswith("_interaction_ir"):
                raise RuntimeError("redis read timeout")
            return await MemorySessionStore.get_state_field(self, session_id, field)

    async def _allowed(key, max_requests, window_seconds):
        return True

    flaky = FlakyStore()
    with patch.object(_ie_mod, "get_rate_limiter", AsyncMock(return_value=MagicMock(
        is_allowed=_allowed,
    ))), patch.object(_ie_mod, "session_data_manager", flaky):
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/chat/sessions/sess-flaky/interaction-events",
                json={"events": [{
                    "event_id": "f-00000001", "client_instance_id": "tab-aaaa0001",
                    "client_seq": 1, "kind": "viewport_settled",
                    "payload": {"center": [1, 2], "zoom": 3},
                }]},
            )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "interaction_ledger_unavailable"
    # 共享账面未被空态覆写（三键皆缺 —— 写路径未执行；绕开 Flaky 的
    # 抛错 override，用基类直读内部状态）。
    assert await MemorySessionStore.get_state_field(flaky, "sess-flaky", LEDGER_KEY) is None


# ── P3-4 记录预算常量关系 ───────────────────────────────────────────────


def test_record_budget_constant_relation():
    """_MAX_RECORD_BYTES 必须容纳"全信封字段顶满 + payload 顶满"的最大
    记录 —— 关系破坏（新增信封字段）时此断言先红，而不是线上
    accepted-but-dropped。"""
    from app.services.gis_harness.interaction_ir.ingest import _MAX_RECORD_BYTES

    envelope_overhead = 512  # 全 optional 字段（ids/fingerprint/refs）经验上界
    assert _MAX_RECORD_BYTES >= MAX_PAYLOAD_BYTES + envelope_overhead
    assert _MAX_RECORD_BYTES == 1536
