"""Situation 只读视图端点（ADR-0283 D4/D5）。

- ``GET /chat/sessions/{sid}/situation`` —— GISSituation JSON 投影 +
  一致性检查 + 选择史 + claimed scope（刷新/重连的状态面恢复数据源；
  全部只读派生，不建第二真相）。
- ``GET /chat/sessions/{sid}/turn-chain`` —— turn → intent → map op →
  observed 可重建链（三类可解释 diff：user_explicit /
  agent_proposed_pending / executed_observed）。

鉴权与 interaction-events / map-operations 同族（require_owned_session，
不属主 404 不泄存在性）。两端点都是纯读面：不写权威状态、不驱动 turn。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["AI对话"])

#: 状态面恢复只需最近 4 turn（有界）。
_TURN_CHAIN_LIMIT = 4


@router.get("/sessions/{session_id}/situation")
async def get_situation_view(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """GISSituation JSON 投影（只读派生）+ 一致性检查 + 选择史 + scope。"""
    from app.services.gis_harness.interaction_ir.projection import (
        interaction_snapshot_via_store,
    )
    from app.services.gis_situation.analysis_scope import derive_claimed_scope
    from app.services.gis_situation.compiler import compile_situation
    from app.services.gis_situation.consistency import check_consistency
    from app.services.gis_situation.selection_history import (
        project_selection_history,
    )
    from app.services.mapspec.store import mapspec_store_instance

    situation = await compile_situation(
        session_id, store=session_data_manager,
        mapspec_store=mapspec_store_instance,
    )
    snapshot = await interaction_snapshot_via_store(session_data_manager, session_id)
    pre_turn = situation.interaction.selected_feature
    pre_turn_value = (
        pre_turn.value if pre_turn.status == "known" and isinstance(pre_turn.value, dict)
        else None
    )
    # 选择史从 ledger 环重建（投影 snapshot 无环本体 → 定向读一次）。
    try:
        ring = await session_data_manager.get_state_field(
            session_id, "_interaction_ir_ledger")
        ring_events = ring if isinstance(ring, list) else []
    except Exception:  # noqa: BLE001 — 史面是增值披露，缺席为空
        ring_events = []
    affordances = None
    try:
        afford = await session_data_manager.get_state_field(
            session_id, "_situation_canvas_affordances")
        affordances = afford if isinstance(afford, list) else None
    except Exception:  # noqa: BLE001
        affordances = None
    return {
        "schema_version": "situation_view.v1",
        "session_id": session_id,
        "situation": situation.to_dict(),
        "inconsistencies": [i.to_dict() for i in check_consistency(situation)],
        "interaction_snapshot": {
            k: v for k, v in (snapshot or {}).items()
            if k in ("viewport", "selection", "active_aoi", "chart_selection",
                     "temporal_window", "user_overrides", "attempted_not_applied")
        },
        "selection_history": project_selection_history(ring_events),
        "claimed_scope": derive_claimed_scope(
            snapshot, pre_turn_value, affordance_ring=affordances,
        ),
    }


@router.get("/sessions/{session_id}/turn-chain")
async def get_turn_chain(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> dict[str, Any]:
    """turn 链投影（只读；journal 不可达 fail-open 降级披露）。"""
    from app.services.gis_situation.turn_chain import project_turn_chain
    from app.services.mapspec.store import mapspec_store_instance

    chain = await project_turn_chain(
        session_id, store=session_data_manager,
        mapspec_store=mapspec_store_instance, max_turns=_TURN_CHAIN_LIMIT,
    )
    chain["session_id"] = session_id
    return chain
