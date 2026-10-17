"""Interaction IR V1 摄入状态机测试（ADR-0224 D4/G）：幂等 / 水印 / 多标签页 /
gap 披露 / actor 伪造拒绝 / causality 交叉校验 / 有界淘汰与 drop 披露。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.interaction_ir.contracts import (
    MAX_LEDGER_EVENTS,
    MAX_TRACKED_CLIENTS,
    LedgerState,
    interaction_event_fingerprint,
)
from app.services.gis_harness.interaction_ir.ingest import (
    VERDICT_ACCEPTED,
    VERDICT_ACCEPTED_WITH_GAP,
    VERDICT_DUPLICATE,
    VERDICT_REJECTED,
    VERDICT_STALE_SEQ,
    apply_event,
    summarize_verdicts,
)


def _event(seq: int, kind: str = "viewport_settled", instance: str = "tab-aaaa0001",
           event_id: str | None = None, **extra) -> dict:
    # event_id 默认包含实例与序号（真实客户端为 uuid —— 测试助手同样保证
    # 跨实例唯一，避免命中环内 id 兜底查重）。
    return {
        "event_id": event_id or f"evt-{instance[-4:]}-{seq:08d}",
        "client_instance_id": instance,
        "client_seq": seq,
        "kind": kind,
        "payload": extra.pop("payload", {"center": [116.0, 39.9], "zoom": 10}),
        "actor": "user",
        **extra,
    }


def test_in_order_acceptance_assigns_monotonic_server_seq():
    state = LedgerState()
    v1 = apply_event(state, _event(1))
    v2 = apply_event(state, _event(2))
    assert (v1.verdict, v2.verdict) == (VERDICT_ACCEPTED, VERDICT_ACCEPTED)
    assert v2.server_seq == v1.server_seq + 1
    assert state.clients["tab-aaaa0001"]["acked_seq"] == 2


def test_retry_after_ack_loss_is_stale_not_duplicate():
    """重发同一条（ack 丢失）→ stale_seq 幂等收敛，不重复入账。"""
    state = LedgerState()
    apply_event(state, _event(1))
    apply_event(state, _event(2))
    again = apply_event(state, _event(2))
    assert again.verdict == VERDICT_STALE_SEQ
    assert len(state.events) == 2


def test_batch_internal_duplicate_event_id():
    state = LedgerState()
    # 不同 client_seq 复用同一 event_id（畸形客户端）→ 第二条 duplicate。
    apply_event(state, _event(1, event_id="evt-shared-01"))
    dup = apply_event(state, _event(2, event_id="evt-shared-01"))
    assert dup.verdict == VERDICT_DUPLICATE
    assert len(state.events) == 1


def test_gap_disclosed():
    state = LedgerState()
    apply_event(state, _event(1))
    v = apply_event(state, _event(5))
    assert v.verdict == VERDICT_ACCEPTED_WITH_GAP
    assert v.gap_from == 2


def test_actor_forgery_rejected():
    state = LedgerState()
    v = apply_event(state, _event(1, actor="agent"))
    assert v.verdict == VERDICT_REJECTED
    assert v.reason == "actor_not_user"
    assert state.events == []


def test_unknown_kind_and_bad_payload_rejected():
    state = LedgerState()
    assert apply_event(state, _event(1, kind="nope")).verdict == VERDICT_REJECTED
    bad = _event(1, payload={"totally": "unknown"})
    assert apply_event(state, bad).reason == "invalid_payload"


def test_multi_tab_independent_watermarks():
    """tab A/B 各自独立 client_seq —— 互不干扰、不互判 stale。"""
    state = LedgerState()
    a1 = apply_event(state, _event(1, instance="tab-aaaa0001"))
    b1 = apply_event(state, _event(1, instance="tab-bbbb0002"))
    a2 = apply_event(state, _event(2, instance="tab-aaaa0001"))
    assert [v.verdict for v in (a1, b1, a2)] == [VERDICT_ACCEPTED] * 3
    assert state.clients["tab-aaaa0001"]["acked_seq"] == 2
    assert state.clients["tab-bbbb0002"]["acked_seq"] == 1


def test_mutation_bridge_verified_against_dedup_index():
    dedup = {"c:mut-12345678": {"revision": 42}}
    state = LedgerState()
    v = apply_event(
        state,
        _event(
            1, kind="layer_visibility_changed",
            payload={"layer_id": "roads", "visible": False},
            client_mutation_id="mut-12345678",
            mutation_outcome="committed",
            mutation_revision=42,
        ),
        mutation_dedup=dedup,
    )
    assert v.verdict == VERDICT_ACCEPTED
    rec = state.events[-1]
    assert rec["mutation_verified"] is True
    assert rec["verified_revision"] == 42


def test_mutation_bridge_unverified_when_index_miss():
    """申报 committed 但引擎索引无存证（淘汰/未落/CAS 拒）→ verified=False，
    客户端申报保留 —— 投影面按 claimed 呈现，绝不冒充已校验。"""
    state = LedgerState()
    apply_event(
        state,
        _event(
            1, kind="layer_visibility_changed",
            payload={"layer_id": "roads", "visible": False},
            client_mutation_id="mut-87654321",
            mutation_outcome="committed",
        ),
        mutation_dedup={},
    )
    rec = state.events[-1]
    assert rec["mutation_verified"] is False
    assert "verified_revision" not in rec


def test_ring_count_bound_and_drop_disclosure():
    state = LedgerState()
    total = MAX_LEDGER_EVENTS + 40
    for seq in range(1, total + 1):
        apply_event(state, _event(seq))
    assert len(state.events) == MAX_LEDGER_EVENTS
    assert state.dropped == 40
    # 环尾保留最新（server_seq 连续推进；淘汰只影响头部）。
    assert state.events[-1]["client_seq"] == total


def test_ring_byte_budget_eviction():
    """字节预算兜底：常规路径 payload≤512B × 128 条 < 96KB 不会触发 ——
    用超越常规单条体积的构造态直接验证预算淘汰逻辑本身（头部淘汰 +
    dropped 披露 + 计数收敛）。"""
    from app.services.gis_harness.interaction_ir.replay import initial_state_from

    fat_events = [
        {
            "schema_version": "interaction_ir.v1", "server_seq": i + 1,
            "event_id": f"fat-{i:06d}", "kind": "feature_selected", "actor": "user",
            "client_instance_id": "tab-aaaa0001", "client_seq": i + 1,
            "payload": {"feature_id": "x" * 128}, "fingerprint": f"{i:016x}",
            "_pad": "p" * 700,
        }
        for i in range(100)
    ]
    initial = {"events": fat_events, "clients": {"tab-aaaa0001": {"acked_seq": 100, "last_seen": 100}},
               "meta": {"server_seq": 100, "dropped": 0}}
    state = initial_state_from(initial)
    assert state.total_bytes > 96 * 1024 - 5 * 1024  # 构造态接近预算

    # 再叠一条 → 超预算，头部淘汰直至回到预算内。
    apply_event(state, _event(101))
    assert state.dropped > 0
    assert len(state.events) < 101


def test_tracked_clients_bounded_fifo():
    state = LedgerState()
    for i in range(MAX_TRACKED_CLIENTS + 3):
        apply_event(state, _event(1, instance=f"tab-{i:08d}"))
    assert len(state.clients) == MAX_TRACKED_CLIENTS
    # 最久未见实例被淘汰（i=0..2 不在表内）。
    assert "tab-00000000" not in state.clients
    assert f"tab-{MAX_TRACKED_CLIENTS + 2:08d}" in state.clients


def test_fingerprint_stored_excludes_wall_clock():
    state = LedgerState()
    apply_event(state, {**_event(1), "created_at": "2026-10-18T00:00:00Z"})
    rec = state.events[-1]
    expected = interaction_event_fingerprint(
        "viewport_settled", "user", "tab-aaaa0001", 1,
        rec["payload"],
    )
    assert rec["fingerprint"] == expected


def test_summarize_verdicts():
    state = LedgerState()
    verdicts = [
        apply_event(state, _event(1)),
        apply_event(state, _event(1)),  # stale
        apply_event(state, _event(9)),  # gap
    ]
    assert summarize_verdicts(verdicts) == {
        "accepted": 2, "duplicates": 0, "stale": 1, "rejected": 0,
    }


def test_determinism_same_input_same_output():
    """核心确定性：同输入序列 → 逐位相同的状态（含 server_seq/dropped）。"""
    events = [
        _event(1), _event(2, kind="feature_selected",
                          payload={"layer_id": "r", "feature_id": "f"}),
        _event(5), _event(5),
    ]
    s1, s2 = LedgerState(), LedgerState()
    for e in events:
        apply_event(s1, e)
        apply_event(s2, e)
    assert s1.events == s2.events
    assert (s1.server_seq, s1.dropped, s1.clients) == (s2.server_seq, s2.dropped, s2.clients)


@pytest.mark.asyncio
async def test_ingest_batch_roundtrip_with_memory_store():
    """生产接线：load → fold → persist → 重放收敛（水印幂等）。"""
    from app.services.gis_harness.interaction_ir.ingest import (
        ingest_interaction_batch, load_ledger_state,
    )
    from app.services.session_data import MemorySessionStore

    store = MemorySessionStore()
    batch1 = [
        {"event_id": "e-00000001", "client_instance_id": "tab-aaaa0001",
         "client_seq": 1, "kind": "viewport_settled",
         "payload": {"center": [116, 39], "zoom": 10}, "actor": "user"},
        {"event_id": "e-00000002", "client_instance_id": "tab-aaaa0001",
         "client_seq": 2, "kind": "aoi_drawn",
         "payload": {"geometry_type": "Polygon", "vertex_count": 5,
                     "geometry_digest": "abc123def456abc1"}, "actor": "user"},
    ]
    r1 = await ingest_interaction_batch(store, "sess-x", batch1)
    assert r1["accepted"] == 2
    assert r1["watermarks"] == {"tab-aaaa0001": 2}

    # 弱网重发整批（ack 丢失）→ 全部 stale，不双计。
    r2 = await ingest_interaction_batch(store, "sess-x", batch1)
    assert r2["stale"] == 2 and r2["accepted"] == 0
    state = await load_ledger_state(store, "sess-x")
    assert len(state.events) == 2
