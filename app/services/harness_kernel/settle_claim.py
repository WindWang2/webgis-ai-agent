"""Expiring durable settlement ownership; terminal writes are token fenced."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from app.services.distributed_lock import session_lock_registry

# All effects are cancelled before this lease can expire. After process death a
# different worker may retry; effects remain idempotent projections, not a claim
# of exactly-once delivery to arbitrary external systems.
SETTLE_EFFECT_BUDGET_S = 60.0
SETTLE_LEASE_S = 90.0


@dataclass(frozen=True)
class SettlementClaim:
    state: str
    token: str = ""


async def acquire_settlement_claim(session_id: str, turn_id: str, *, expected_status: str) -> SettlementClaim:
    from app.services.harness_kernel.runtime import get_runtime
    from app.services.session_plan import load_session_plan, save_session_plan

    runtime = get_runtime(session_id)
    async with session_lock_registry.lock(session_id, fail_on_degraded=True, fail_on_lost=True):
        envelope = await load_session_plan(session_id, store=runtime._store)
        turn = next((row for row in (envelope.turns if envelope else []) if row.turn_id == turn_id), None)
        if turn is None or envelope is None:
            return SettlementClaim("unknown")
        if str(turn.status) != expected_status:
            return SettlementClaim("stale")
        if turn.settle_finished_at:
            return SettlementClaim("duplicate")
        now = time.time()
        if turn.settle_token and turn.settle_started_at + SETTLE_LEASE_S > now:
            return SettlementClaim("in_progress")
        turn.settle_token = uuid.uuid4().hex
        turn.settle_started_at = now
        if not await save_session_plan(envelope, store=runtime._store, expected_revision=envelope.revision):
            return SettlementClaim("conflict")
        return SettlementClaim("claimed", turn.settle_token)


async def claim_settlement(session_id: str, turn_id: str, *, expected_status: str) -> str:
    """Compatibility status-only view; coordinator uses the fenced claim."""
    return (await acquire_settlement_claim(session_id, turn_id, expected_status=expected_status)).state


async def complete_settlement(session_id: str, turn_id: str, token: str) -> bool:
    from app.services.harness_kernel.runtime import get_runtime
    from app.services.session_plan import load_session_plan, save_session_plan

    runtime = get_runtime(session_id)
    async with session_lock_registry.lock(session_id, fail_on_degraded=True, fail_on_lost=True):
        envelope = await load_session_plan(session_id, store=runtime._store)
        turn = next((row for row in (envelope.turns if envelope else []) if row.turn_id == turn_id), None)
        if turn is None or envelope is None or turn.settle_token != token or turn.status == "running":
            return False
        turn.settle_finished_at = time.time()
        return await save_session_plan(envelope, store=runtime._store, expected_revision=envelope.revision)
