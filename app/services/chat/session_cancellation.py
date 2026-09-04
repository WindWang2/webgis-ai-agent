"""Unified session cancellation seam (ADR-0100, /goal §11).

Before this module, "cancel what this session is doing" was three
hand-maintained choreographies (task cancel / job cancel / session delete),
each independently wiring tracker-token + durable-cancel + bridge-abort —
and each needing its own memory of the #1066 lesson (the tracker token and
the bridge turn token are independent signals; forget the bridge and the
subprocess keeps running). New cancel paths kept re-learning that.

This module owns the two reusable joints:

- :func:`abort_active_pi_turn` — the single bridge-abort primitive. Resolves
  the OWNING worker via the session-keyed ``_active_turns`` table (V5-B;
  never the legacy singleton view), keeps the session-checked singleton
  fallback for pool-less deployments, and applies the CONC-F7 bound so a
  stuck Pi can never hold a caller hostage. Never raises — cancellation must
  not be blocked by its own cleanup.
- :func:`cancel_agent_task_and_turn` — the agent-task cascade shared by
  ``DELETE /tasks/{task_id}`` and ``DELETE /jobs/{job_id}`` (agent class):
  tracker terminalization + durable-job cancel requests + registry ignition +
  bridge abort. One implementation, two routes, zero drift.

Session deletion keeps its richer tombstone choreography in chat.py but MUST
abort through :func:`abort_active_pi_turn` — the module is the only place
that talks to the bridge about cancellation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: CONC-F7: abort RPC budget when Pi is stuck in a long tool. Same bound the
#: disconnect path uses; a session-level cancel must never hold the caller.
ABORT_TIMEOUT_S = 5.0


async def abort_active_pi_turn(
    session_id: Optional[str],
    *,
    timeout: float = ABORT_TIMEOUT_S,
    reason: str = "",
) -> Dict[str, Any]:
    """Abort the session's active Pi turn through its OWNING worker bridge.

    Resolution: the session-keyed ``_active_turns`` entry's ``bridge``
    (pool-correct owner). No entry → no active turn → no abort (the old
    call sites all guarded on the same entry; a blind singleton abort could
    hit another session's turn on the shared subprocess — V5-B).

    Returns ``{"aborted": bool, "detail": str}``; abort failures are logged
    and reported, never raised (cancellation must not fail because its
    subprocess was already gone).
    """
    if not session_id:
        return {"aborted": False, "detail": "no session id"}
    try:
        from app.agent_pi_bridge import get_active_turn_entry

        entry = get_active_turn_entry(session_id)
        if entry is None:
            return {"aborted": False, "detail": "no active turn"}
        try:
            result = await asyncio.wait_for(
                entry.bridge.abort(session_id), timeout=timeout
            )
            return {"aborted": True, "detail": str(result)[:200]}
        except asyncio.TimeoutError:
            logger.warning(
                "[SessionCancel] abort timed out (%ss) for session %s%s",
                timeout, session_id,
                f" reason={reason}" if reason else "",
            )
            return {"aborted": False, "detail": "abort timeout"}
        except Exception as e:  # noqa: BLE001 — abort 失败不阻断取消
            logger.warning(
                "[SessionCancel] abort failed for session %s: %s", session_id, e
            )
            return {"aborted": False, "detail": f"abort error: {e}"[:200]}
    except Exception as e:  # noqa: BLE001 — 结构性失败也绝不上抛
        logger.warning(
            "[SessionCancel] abort_active_pi_turn(%s) failed: %s", session_id, e
        )
        return {"aborted": False, "detail": f"seam error: {e}"[:200]}


async def cancel_agent_task_and_turn(
    db: Any,
    task_id: str,
    *,
    registry_cancel_reason: str = "parent agent task cancelled",
) -> Dict[str, Any]:
    """Agent-task cancellation cascade (shared by task + job routes).

    1. tracker.cancel — terminalize steps, ignite the task token, collect the
       turn's durable job ids;
    2. durable cancel requests land in the DB (cross-process truth) + local
       registry ignition (same-process immediacy);
    3. abort the session's active Pi turn via :func:`abort_active_pi_turn`
       (#1066: the tracker token does not reach the subprocess).

    The db commit is the caller's responsibility (route-level transaction
    boundaries stay unchanged).
    """
    from app.lib.cancellation import registry as cancellation_registry
    from app.services.jobs.store import DurableJobStore
    from app.api.routes.chat import get_engine

    tracker = get_engine().tracker
    task_info = tracker.get(task_id)
    background_job_ids = (
        list(task_info.background_job_ids) if task_info else []
    )
    cancelled = tracker.cancel(task_id)
    for job_id in background_job_ids:
        await DurableJobStore.request_cancel(db, job_id)
        cancellation_registry.cancel(str(job_id), registry_cancel_reason)

    session_id = task_info.session_id if task_info else None
    abort = await abort_active_pi_turn(
        session_id, reason=f"agent task {task_id} cancelled"
    )
    return {
        "cancelled": cancelled,
        "durable_cancels": background_job_ids,
        "pi_abort": abort,
    }


__all__ = [
    "abort_active_pi_turn",
    "cancel_agent_task_and_turn",
    "ABORT_TIMEOUT_S",
]
