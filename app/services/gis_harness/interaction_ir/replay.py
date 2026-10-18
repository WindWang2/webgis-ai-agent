"""Interaction IR V1 回放 harness（Goal 02 / ADR-0227 I）。

``events + initial authority revisions -> same meaningful projection``：

- 回放与生产摄入共用 :func:`ingest.apply_event`（同一分类/去重/淘汰路径），
  与 :func:`projection.project_interaction_snapshot`（同一投影路径）——
  **不是**一套平行实现；
- 确定性：不读时钟、不用随机源；``created_at`` 等 advisory 字段可以存在
  但不影响任何判定（fingerprint/排序/投影均由序号驱动）；
- corpus 场景（``tests/unit/gis_harness/interaction_ir/replay_corpus/*.json``）
  声明初始态与期望（verdicts / projection 部分匹配 / 不变量），由
  ``test_replay_corpus.py`` 参数化驱动，另做双跑一致性断言。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.gis_harness.interaction_ir.contracts import LedgerState
from app.services.gis_harness.interaction_ir.ingest import (
    EventVerdict,
    apply_event,
    summarize_verdicts,
)
from app.services.gis_harness.interaction_ir.projection import (
    project_interaction_snapshot,
)


def initial_state_from(initial: Dict[str, Any] | None) -> LedgerState:
    """corpus ``initial`` → 状态机初始态（对齐 load_ledger_state 的对账逻辑）。"""
    initial = initial if isinstance(initial, dict) else {}
    events = initial.get("events")
    clients = initial.get("clients")
    meta = initial.get("meta") if isinstance(initial.get("meta"), dict) else {}
    state = LedgerState(
        events=list(events) if isinstance(events, list) else [],
        clients=dict(clients) if isinstance(clients, dict) else {},
        server_seq=int(meta.get("server_seq") or 0),
        dropped=int(meta.get("dropped") or 0),
    )
    state.recalculate_bytes()
    if state.events:
        tail = state.events[-1].get("server_seq") if isinstance(state.events[-1], dict) else None
        if isinstance(tail, int):
            state.server_seq = max(state.server_seq, tail)
    return state


def replay_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """fold 全部事件 → {verdicts, summary, projection, state}（确定性）。"""
    state = initial_state_from(scenario.get("initial"))
    dedup = (scenario.get("initial") or {}).get("mutation_dedup")
    verdicts: List[EventVerdict] = []
    for event in scenario.get("events") or []:
        raw = dict(event) if isinstance(event, dict) else {}
        verdicts.append(apply_event(state, raw, mutation_dedup=dedup))
    return {
        "verdicts": [v.to_ack() for v in verdicts],
        "summary": summarize_verdicts(verdicts),
        "projection": project_interaction_snapshot(state.events),
        "state": {
            "server_seq": state.server_seq,
            "dropped": state.dropped,
            "clients": {
                instance: int(entry.get("acked_seq", 0))
                for instance, entry in state.clients.items()
            },
            "event_count": len(state.events),
        },
    }
