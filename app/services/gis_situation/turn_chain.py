"""TurnChain —— turn → intent → map op → observed 可重建链投影（ADR-0285 D4）。

把四份既有账本/环**只读 join** 成 per-turn 变更链，并对每个变更做三类
可解释分类（纯规则，不写任何权威状态、不建第二真相）：

- ``user_explicit`` —— 用户显式地图参数变化（IR mutation-linked 事件
  committed / provenance origin=user）；
- ``agent_proposed_pending`` —— Agent 提议尚未落定（回执 preview /
  confirmation_required / clarification_needed）；
- ``executed_observed`` —— 已执行且被**较新**可信观察覆盖（observation.
  mapspec_revision ≥ mutation revision）。迟到旧观察不升格
  （``pending_execution`` / 无观察面 ``unknown_observation`` 诚实披露）。

数据源（全部权威 store 的派生读面）：
- turn_journal（DB 账本；不可达 → fail-open 降级披露，不阻断）；
- ``map_state._map_operation_receipts``（操作回执环，ADR-0278）；
- ``map_state._interaction_ir_ledger``（交互 IR 环，ADR-0227）；
- ``map_state._gis_provenance``（mutation 起源审计尾）；
- ``map_state._cartographic_observation.mapspec_revision``（服务端盖章的
  最新可信观察代）。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "build_turn_chain",
    "classify_change",
    "project_turn_chain",
    "MAX_CHAIN_TURNS",
    "CHANGE_CLASSES",
]

#: 链上 turn 数上限（有界；更早 turn 由 journal compaction/retention 语义覆盖）。
MAX_CHAIN_TURNS = 8

#: 三类可解释分类词表（封闭）。
CHANGE_CLASSES = (
    "user_explicit",
    "agent_proposed_pending",
    "executed_observed",
)

_PENDING_STATUSES = frozenset({
    "preview", "confirmation_required", "clarification_needed",
})
_MUTATION_LINKED_LAYER_KINDS = frozenset({
    "layer_visibility_changed", "layer_opacity_changed", "layer_style_changed",
    "layer_order_changed", "layer_removed", "temporal_window_settled",
})


def classify_change(
    *,
    origin: Optional[str],
    receipt_status: Optional[str],
    outcome: Optional[str],
    mutation_revision: Optional[int],
    observed_revision: Optional[int],
) -> str:
    """纯分类规则（封闭词表，含终态/诚实披露态）。"""
    if receipt_status in _PENDING_STATUSES:
        return "agent_proposed_pending"
    if outcome == "superseded" or receipt_status == "superseded":
        return "superseded"
    if outcome == "failed" or receipt_status == "failed":
        return "failed"
    if origin == "user" and outcome == "committed":
        return "user_explicit"
    if receipt_status not in ("committed", "partial") and outcome != "committed":
        return "not_executed"
    # agent（或 system）commit：是否已被较新可信观察覆盖。
    if mutation_revision is None:
        return "unknown_revision"
    if observed_revision is None:
        return "unknown_observation"
    if observed_revision >= mutation_revision:
        return "executed_observed"
    return "pending_execution"


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(value)


def _receipt_revision(receipt: Dict[str, Any]) -> Optional[int]:
    revisions = receipt.get("mutation_revisions")
    if isinstance(revisions, list) and revisions:
        tail = _as_int(revisions[-1])
        if tail is not None:
            return tail
    return _as_int(receipt.get("final_revision")) or _as_int(receipt.get("mutation_revision"))


def _effect_targets(effect: Dict[str, Any]) -> List[str]:
    targets = effect.get("targets")
    if isinstance(targets, list):
        return [str(t)[:64] for t in targets[:4] if isinstance(t, (str, int))]
    return []


def build_turn_chain(
    journal_events: Iterable[Dict[str, Any]],
    receipts: Iterable[Dict[str, Any]],
    ir_events: Iterable[Dict[str, Any]],
    provenance: Iterable[Dict[str, Any]],
    *,
    observed_revision: Optional[int],
    max_turns: int = MAX_CHAIN_TURNS,
) -> Dict[str, Any]:
    """纯装配：四源 → per-turn 变更链（新 → 旧 turn 输出）。"""
    max_turns = max(1, min(int(max_turns), MAX_CHAIN_TURNS))
    # ── 收集用户显式变更（IR mutation-linked committed + provenance user）──
    user_changes: List[Dict[str, Any]] = []
    user_layer_touch: Dict[str, int] = {}  # layer → 变更序（user-wins 判据）
    order = 0
    for record in ir_events:
        if not isinstance(record, dict):
            continue
        kind = str(record.get("kind") or "")
        if kind not in _MUTATION_LINKED_LAYER_KINDS:
            continue
        if record.get("mutation_outcome") != "committed":
            continue
        payload = record.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        layer_id = str(payload.get("layer_id") or "")[:64]
        order += 1
        # IR payloads remain client claims even when a mutation id exists.
        # Only authoritative user provenance establishes a user-wins layer.
        user_changes.append({
            "source": "interaction_ir",
            "kind": kind,
            "layer_id": layer_id or None,
            "class": "user_claimed",
            "wins": False,
            "link": {
                "client_mutation_id": record.get("client_mutation_id"),
                "mutation_verified": bool(record.get("mutation_verified")),
            },
        })
    for entry in provenance:
        if not isinstance(entry, dict) or entry.get("origin") != "user":
            continue
        order += 1
        target = str(entry.get("target") or "")[:64]
        if target:
            user_layer_touch.setdefault(target, order)
        user_changes.append({
            "source": "gis_provenance",
            "kind": str(entry.get("kind") or "mutation")[:64],
            "layer_id": target or None,
            "wins": True,
            "link": {},
        })

    # ── 回执 → agent 变更（带 turn 归属）────────────────────────────────
    receipt_changes: List[Dict[str, Any]] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        status = str(receipt.get("status") or "")
        origin = str(receipt.get("origin") or "agent")
        revision = _receipt_revision(receipt)
        turn_id = ""
        prov = receipt.get("provenance")
        if isinstance(prov, dict) and prov.get("turn_id"):
            turn_id = str(prov["turn_id"])[:64]
        effects = receipt.get("effects")
        effects = effects if isinstance(effects, list) else []
        effect_views = [
            {
                "kind": str(e.get("kind") or "")[:48],
                "targets": _effect_targets(e) if isinstance(e, dict) else [],
                "state": str(e.get("state") or "")[:24] if isinstance(e, dict) else "",
            }
            for e in effects[:8]
        ]
        change_class = classify_change(
            origin=origin, receipt_status=status,
            outcome=None, mutation_revision=revision,
            observed_revision=observed_revision,
        )
        # user-wins：agent 效果触碰用户显式改过的层 → 披露（不吞并）。
        touched_user_layers = sorted({
            t for view in effect_views for t in view["targets"]
            if t in user_layer_touch
        })
        receipt_changes.append({
            "source": "map_operation_receipt",
            "operation_id": str(receipt.get("operation_id") or "")[:64],
            "turn_id": turn_id or None,
            "origin": origin,
            "status": status,
            "class": change_class,
            "mutation_revision": revision,
            "effects": effect_views,
            "wins": False,
            **({"user_wins_conflict_layers": touched_user_layers}
               if touched_user_layers else {}),
        })

    # ── journal → turn 分组（map_mutated 行给 revision span）────────────
    turn_order: List[str] = []
    turn_mutations: Dict[str, List[int]] = {}
    turn_intent: Dict[str, str] = {}
    for row in journal_events:
        if not isinstance(row, dict):
            continue
        turn_id = str(row.get("turn_id") or "")[:64]
        kind = str(row.get("kind") or "")
        if not turn_id or kind == "turn_compacted":
            continue
        if turn_id not in turn_mutations:
            turn_mutations[turn_id] = []
            turn_order.append(turn_id)
        if kind == "map_mutated":
            rev = _as_int(row.get("mutation_revision"))
            if rev is not None:
                turn_mutations[turn_id].append(rev)
        note = row.get("note")
        if kind == "tool_started" and note and turn_id not in turn_intent:
            turn_intent[turn_id] = str(note)[:96]

    # 回执 turn 归属未知时归入 "unattributed" 组（诚实，不猜 turn）。
    for change in receipt_changes:
        receipt_turn = change.get("turn_id")
        if isinstance(receipt_turn, str) and receipt_turn and receipt_turn not in turn_mutations:
            turn_mutations[receipt_turn] = []
            turn_order.append(receipt_turn)
    if any(not change.get("turn_id") for change in receipt_changes) or (user_changes and not turn_order):
        turn_order.append("unattributed")
        turn_mutations["unattributed"] = []

    turns: List[Dict[str, Any]] = []
    for turn_id in reversed(turn_order[-max_turns:]):
        mutations = sorted(set(turn_mutations.get(turn_id) or []))
        changes: List[Dict[str, Any]] = [
            c for c in receipt_changes if (c.get("turn_id") or None) == turn_id
        ]
        if turn_id == "unattributed":
            changes = [c for c in receipt_changes if not c.get("turn_id")]
        turn_view: Dict[str, Any] = {
            "turn_id": turn_id,
            "intent_digest": turn_intent.get(turn_id),
            "revision_span": [mutations[0], mutations[-1]] if mutations else None,
            "changes": changes,
        }
        # 用户显式变更（IR/provenance 无 turn 归属）→ 挂在最新 turn 的
        # changes（class=user_explicit，wins=true）；无 turn 时进 unattributed。
        if turn_order and turn_id == turn_order[-1]:
            turn_view["changes"] = (
                [{**c, "class": c.get("class", "user_explicit"), "turn_id": turn_id}
                 for c in user_changes[-16:]] + turn_view["changes"]
            )
        turns.append(turn_view)

    return {
        "schema_version": "situation_turn_chain.v1",
        "turns": turns,
        "observed_revision": observed_revision,
        "user_wins_layers": sorted(user_layer_touch)[:16],
        "counts": {
            "user_explicit": sum(1 for c in user_changes if c.get("class", "user_explicit") == "user_explicit"),
            "agent_receipts": len(receipt_changes),
            "agent_pending": sum(
                1 for c in receipt_changes
                if c["class"] == "agent_proposed_pending"
            ),
            "executed_observed": sum(
                1 for c in receipt_changes
                if c["class"] == "executed_observed"
            ),
        },
    }


async def project_turn_chain(
    session_id: str,
    *,
    store: Any = None,
    mapspec_store: Any = None,
    ledger: Any = None,
    max_turns: int = MAX_CHAIN_TURNS,
) -> Dict[str, Any]:
    """IO 壳：权威 store 定向读 → build_turn_chain。journal 不可达 →
    fail-open 降级（``degraded`` 披露），绝不阻断。"""
    if store is None:
        from app.services.session_data import session_data_manager as store

    degraded: List[str] = []
    journal_events: List[Dict[str, Any]] = []
    if ledger is None:
        try:
            from app.services.turn_journal.ledger import TurnEventLedger
            ledger = TurnEventLedger()
        except Exception:  # noqa: BLE001 — 账本缺席 → 降级披露
            degraded.append("turn_journal_module")
            ledger = None
    if ledger is not None:
        try:
            journal_events = await ledger.list_events(
                session_id, latest_first=True, limit=200,
            )
        except Exception:  # noqa: BLE001 — fail-open：账本是取证面非正确性面
            degraded.append("turn_journal")
            journal_events = []

    map_state: Dict[str, Any] = {}
    try:
        state = await store.get_map_state(session_id)
        map_state = state if isinstance(state, dict) else {}
    except Exception:  # noqa: BLE001 — 读失败 → 空链 + 披露
        degraded.append("map_state")
        map_state = {}

    receipts = map_state.get("_map_operation_receipts")
    receipts = [r for r in receipts if isinstance(r, dict)] if isinstance(receipts, list) else []
    ir_ledger = map_state.get("_interaction_ir_ledger")
    ir_events = [e for e in ir_ledger if isinstance(e, dict)] if isinstance(ir_ledger, list) else []
    provenance = map_state.get("_gis_provenance")
    provenance = [p for p in provenance if isinstance(p, dict)] if isinstance(provenance, list) else []
    observation = map_state.get("_cartographic_observation")
    observed_revision = (
        _as_int(observation.get("mapspec_revision"))
        if isinstance(observation, dict) else None
    )

    chain = build_turn_chain(
        journal_events, receipts, ir_events, provenance,
        observed_revision=observed_revision, max_turns=max_turns,
    )
    if degraded:
        chain["degraded"] = degraded
    return chain
