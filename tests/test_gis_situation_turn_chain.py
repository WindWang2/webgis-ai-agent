"""S4 — TurnChain 投影：turn → intent → map op → observed 可重建链
（ADR-0283 D4）。

三类变更分类（纯规则）：
- user_explicit —— 用户显式地图参数变化（IR mutation-linked committed /
  provenance origin=user）；
- agent_proposed_pending —— Agent 提议未落定（回执 preview/
  confirmation_required/clarification_needed）；
- executed_observed —— 已执行且被较新可信观察覆盖（observation.
  mapspec_revision ≥ mutation revision）。迟到旧观察不升格。
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.services.gis_situation.turn_chain import (
    build_turn_chain,
    classify_change,
    project_turn_chain,
)
from tests.data.situation_fakes import FakeMapspecStore, FakeSituationStore


# ── 纯分类函数 ────────────────────────────────────────────────────────


def test_classify_user_explicit():
    assert classify_change(
        origin="user", receipt_status=None, outcome="committed",
        mutation_revision=3, observed_revision=5,
    ) == "user_explicit"


def test_classify_agent_pending_states():
    for status in ("preview", "confirmation_required", "clarification_needed"):
        assert classify_change(
            origin="agent", receipt_status=status, outcome=None,
            mutation_revision=None, observed_revision=None,
        ) == "agent_proposed_pending"


def test_classify_executed_observed_needs_newer_observation():
    assert classify_change(
        origin="agent", receipt_status="committed", outcome="committed",
        mutation_revision=4, observed_revision=5,
    ) == "executed_observed"
    # 迟到旧观察（observed < mutation）不升格 —— 披露为 pending_execution。
    assert classify_change(
        origin="agent", receipt_status="committed", outcome="committed",
        mutation_revision=6, observed_revision=5,
    ) == "pending_execution"
    # 无观察面 → 诚实 unknown_observation。
    assert classify_change(
        origin="agent", receipt_status="committed", outcome="committed",
        mutation_revision=6, observed_revision=None,
    ) == "unknown_observation"


def test_classify_terminal_outcomes():
    assert classify_change(
        origin="agent", receipt_status="superseded", outcome="superseded",
        mutation_revision=1, observed_revision=9,
    ) == "superseded"
    assert classify_change(
        origin="agent", receipt_status="failed", outcome="failed",
        mutation_revision=1, observed_revision=9,
    ) == "failed"


# ── 纯装配：journal × receipts × IR × provenance ──────────────────────


def _journal_row(turn: str, kind: str, **extra) -> Dict[str, Any]:
    return {"turn_id": turn, "kind": kind, "session_id": "s1",
            "mutation_revision": extra.pop("mutation_revision", None),
            "detail": extra.pop("detail", {}), **extra}


def _receipt(operation_id: str, status: str, *, origin: str = "agent",
             turn_id: str = "t1", revision: int | None = 3) -> Dict[str, Any]:
    return {
        "schema_version": "map_operation.v1", "operation_id": operation_id,
        "mode": "commit", "actor": "assistant" if origin == "agent" else "user",
        "origin": origin,
        "base_revision": (revision - 1) if revision is not None else None,
        **({"mutation_revisions": [revision]} if revision is not None else {}),
        "status": status,
        "provenance": {"turn_id": turn_id, "user_ack": False},
        "effects": [{"index": 0, "kind": "set_layer_visibility",
                     "targets": ["schools"], "state": "applied"}],
        "applied_count": 1,
    }


def _ir_event(seq: int, kind: str, payload: dict, outcome: str = "committed") -> dict:
    return {
        "event_id": f"c-{seq:08d}", "client_instance_id": "t-aaaa0001",
        "client_seq": seq, "server_seq": seq, "kind": kind, "payload": payload,
        "client_mutation_id": f"mut-{seq}", "mutation_outcome": outcome,
    }


def test_build_turn_chain_three_classes():
    journal = [
        _journal_row("t1", "tool_started", note="webgis_map_operation"),
        _journal_row("t1", "map_mutated", mutation_revision=3),
        _journal_row("t1", "turn_ended"),
    ]
    receipts = [
        _receipt("op-1", "committed", origin="agent", revision=3),
        _receipt("op-2", "confirmation_required", origin="agent", revision=None),
    ]
    ir_events = [
        _ir_event(1, "layer_visibility_changed",
                  {"layer_id": "heatmap", "visible": False}),
    ]
    provenance = [
        {"origin": "user", "kind": "PatchLayerPresentation",
         "target": "heatmap", "detail": {"visible": False}},
    ]
    chain = build_turn_chain(
        journal, receipts, ir_events, provenance, observed_revision=3,
    )
    assert chain["turns"], "chain must produce turns"
    turn = chain["turns"][0]
    classes = {c["class"] for c in turn["changes"]}
    assert "user_explicit" in classes      # IR committed（用户显式）
    assert "agent_proposed_pending" in classes  # op-2 confirmation_required
    assert "executed_observed" in classes   # op-1 committed + observed≥rev
    # user-wins 披露：同层冲突（user 隐藏 heatmap vs agent 效果）给 wins 字段。
    user_changes = [c for c in turn["changes"] if c["class"] == "user_explicit"]
    assert user_changes[0]["wins"] is True


def test_build_turn_chain_stale_observation_not_executed():
    journal = [_journal_row("t1", "map_mutated", mutation_revision=6)]
    receipts = [_receipt("op-3", "committed", origin="agent", revision=6)]
    chain = build_turn_chain(journal, receipts, [], [], observed_revision=5)
    turn = chain["turns"][0]
    assert turn["changes"][0]["class"] == "pending_execution"


def test_build_turn_chain_bounded_turns():
    journal = []
    for i in range(1, 12):
        journal.append(_journal_row(f"t{i}", "map_mutated", mutation_revision=i))
        journal.append(_journal_row(f"t{i}", "turn_ended"))
    chain = build_turn_chain(journal, [], [], [], observed_revision=11, max_turns=4)
    assert len(chain["turns"]) <= 4
    assert chain["turns"][0]["turn_id"] == "t11"  # 新 → 旧


# ── IO 壳：store 注入 + journal 失败降级 ───────────────────────────────


class _NoLedger:
    async def list_events(self, *args, **kwargs):
        raise RuntimeError("db down")


@pytest.mark.asyncio
async def test_project_turn_chain_degrades_without_journal():
    """journal 不可达 → 回执/IR-only 链（fail-open，含 degraded 披露）。"""
    store = FakeSituationStore(map_state={
        "_map_operation_receipts": [_receipt("op-9", "committed", revision=2)],
        "_interaction_ir_ledger": [
            _ir_event(1, "layer_opacity_changed",
                      {"layer_id": "a", "opacity": 0.5}),
        ],
        "_cartographic_observation": {"mapspec_revision": 2},
    })
    chain = await project_turn_chain(
        "s1", store=store, mapspec_store=FakeMapspecStore({}),
        ledger=_NoLedger(),
    )
    assert chain["degraded"] == ["turn_journal"]
    assert chain["turns"]  # 回执/IR 仍构成链（unattributed turn 分组）


@pytest.mark.asyncio
async def test_project_turn_chain_reads_authoritative_state():
    store = FakeSituationStore(map_state={
        "_map_operation_receipts": [_receipt("op-1", "preview")],
        "_interaction_ir_ledger": [],
        "_gis_provenance": [],
        "_cartographic_observation": {"mapspec_revision": 1},
    })
    chain = await project_turn_chain(
        "s1", store=store, mapspec_store=FakeMapspecStore({}),
        ledger=None,
    )
    assert chain["turns"][0]["changes"][0]["class"] == "agent_proposed_pending"


def test_unknown_or_noop_receipts_never_become_executed_by_reusing_base_revision():
    journal = [_journal_row("actual", "turn_started")]
    receipts = [{"operation_id": "unknown", "status": "committed", "base_revision": 9},
                {"operation_id": "noop", "status": "noop", "base_revision": 9}]
    chain = build_turn_chain(journal, receipts, [], [], observed_revision=9)
    assert chain["counts"]["executed_observed"] == 0
    changes = [change for turn in chain["turns"] for change in turn["changes"]]
    assert {row["operation_id"] for row in changes} == {"unknown", "noop"}
    assert {row["class"] for row in changes} == {"unknown_revision", "not_executed"}


def test_client_claimed_committed_event_cannot_establish_authoritative_user_wins():
    chain = build_turn_chain([], [], [_ir_event(1, "layer_visibility_changed", {"layer_id": "victim", "visible": False})], [], observed_revision=9)
    assert chain["user_wins_layers"] == []
    assert chain["counts"]["user_explicit"] == 0
    assert chain["turns"][0]["changes"][0]["class"] == "user_claimed"
    assert chain["turns"][0]["changes"][0]["wins"] is False
