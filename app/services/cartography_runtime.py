"""Cartography session runtime — session-scoped evaluation harnesses.

AH-P1-1（运行时收敛）：本模块从 app/agent_pi_bridge.py 整体迁出。此处是
**共享的制图会话运行时**（与 Pi 传输无关）：session 级 harness 注册表、
cartographic session 评估（desired vs observed 收敛判定）、harness 上下文
持久化/水化、runtime repair 推进、删除墓碑。

legacy ChatEngine（tool_pipeline）与 Pi bridge 两条执行路径都经此模块获得
同一套制图闭环语义——它是 desired-state 权威评估器，不是 Pi 的一部分。

迁移说明（2026-08-27）：符号与行为 1:1 保留；app 内 importer 与测试已随迁
（agent_pi_bridge 保留 re-export 以兼容存量 import）。
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import copy
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import uuid
from typing import Any, Optional

from typing import TYPE_CHECKING

from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.lib.harness.tool_call_event import ToolCallEvent

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import cycle
    from app.services.tool_dispatch_service import ToolDispatchResult

logger = logging.getLogger(__name__)


# ── Session-scoped evaluation harnesses ────────────────────────────────
# General telemetry remains opt-in. Display-producing MapSpec mutations create
# a harness even when telemetry is disabled because cartographic verification
# is part of the product result, not optional observability.
_harness: Optional[PiAgentHarness] = None
_harnesses: "OrderedDict[str, PiAgentHarness]" = OrderedDict()
_cartography_eval_cache: "OrderedDict[tuple[str, str, int, str], dict[str, Any]]" = OrderedDict()
_cartography_eval_locks: dict[str, asyncio.Lock] = {}
_deleted_cartography_sessions: "OrderedDict[str, None]" = OrderedDict()
_DELETED_SESSION_TOMBSTONE_LIMIT = 1_024
_HARNESS_REGISTRY_LIMIT = 128
_harness_feature_enabled = os.getenv("PI_HARNESS_ENABLED", "").lower() in (
    "true", "1", "yes"
)


def _build_session_harness(session_id: str) -> PiAgentHarness:
    """Create one evidence accumulator whose readers are tenant scoped."""
    from app.services.session_data import session_data_manager as sdm
    from app.services.mapspec.coordinator import validate as validate_mapspec
    from app.services.mapspec.store import mapspec_store_instance
    from app.lib.harness.ref_resolver import make_session_store_resolver

    async def read_cartographic_state(requested_session_id: str) -> dict[str, Any]:
        mapspec, map_state = await asyncio.gather(
            mapspec_store_instance.get_mapspec(requested_session_id),
            sdm.get_map_state(requested_session_id),
        )
        return {
            "session_id": requested_session_id,
            "mapspec": mapspec,
            "map_state": map_state,
        }

    return PiAgentHarness(
        session_id=session_id,
        ref_resolver=make_session_store_resolver(sdm),
        mapspec_validator=validate_mapspec,
        cartography_state_reader=read_cartographic_state,
        map_action_reader=sdm.get_map_action_events,
    )


def _get_session_harness(
    session_id: str,
    *,
    create: bool = False,
) -> Optional[PiAgentHarness]:
    """Return a bounded, exact-session harness; never retag a singleton."""
    global _harness
    if not session_id:
        return None
    if session_id in _deleted_cartography_sessions:
        return None
    if _harness is not None and _harness.session_id == session_id:
        return _harness
    existing = _harnesses.get(session_id)
    if existing is not None:
        _harnesses.move_to_end(session_id)
        return existing
    if not create and not _harness_feature_enabled:
        return None
    try:
        created = _build_session_harness(session_id)
    except Exception as harness_err:  # noqa: BLE001 - map interaction must survive telemetry failure
        logger.warning(
            "[PiBridge] session harness wiring failed for %s: %s",
            session_id,
            harness_err,
        )
        return None
    _harnesses[session_id] = created
    _harness = created  # compatibility/telemetry summary: most recently active
    while len(_harnesses) > _HARNESS_REGISTRY_LIMIT:
        evicted_session, _ = _harnesses.popitem(last=False)
        _cartography_eval_locks.pop(evicted_session, None)
        for key in list(_cartography_eval_cache):
            if key[0] == evicted_session:
                del _cartography_eval_cache[key]
    return created


def _discard_session_harness(session_id: str) -> None:
    """Evict one stale process-local accumulator without tombstoning it."""
    global _harness
    removed = _harnesses.pop(session_id, None)
    if removed is not None and _harness is removed:
        _harness = next(reversed(_harnesses.values()), None) if _harnesses else None


def _persistable_cartographic_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project one mutation result to bounded, non-dataset harness evidence."""
    projected: dict[str, Any] = {}
    for key in (
        "status", "success", "is_compiled", "checkpoint_id",
        "mapspec_fingerprint", "runtime_observation_seq",
        "runtime_projection_fingerprint",
        "mutation_revision",
    ):
        value = result.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            projected[key] = value
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        projected["warnings"] = [str(value)[:500] for value in warnings[:32]]
    review = result.get("cartographic_review")
    if isinstance(review, dict) and isinstance(review.get("attempts"), list):
        projected["cartographic_review"] = {
            "attempts": copy.deepcopy(review["attempts"][:2]),
        }
    return projected


async def _persist_cartographic_harness_context(
    session_id: str,
    event: ToolCallEvent,
    actions: list[dict[str, Any]],
    *,
    session_lock_held: bool = False,
) -> bool:
    """Persist the latest mutation seam so another API replica can rehydrate.

    Only correlation, the semantic layer id, slim tool outcome, and already
    bounded issued-action requests are retained. Source/feature bodies and raw
    tool arguments never enter this session-state projection.
    """
    if not session_lock_held:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id):
            from app.services.session_data import session_data_manager
            session_data_manager.invalidate_local_cache(session_id)
            return await _persist_cartographic_harness_context(
                session_id,
                event,
                actions,
                session_lock_held=True,
            )
    if session_id in _deleted_cartography_sessions:
        return False

    layer = (
        event.arguments.get("layer")
        if isinstance(event.arguments, dict)
        and isinstance(event.arguments.get("layer"), dict)
        else {}
    )
    context = {
        "version": 2,
        "session_id": session_id,
        "tool_call": {
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_name,
            "arguments": {"layer": {"id": str(layer.get("id") or "")}},
            "duration_ms": max(0, int(event.duration_ms)),
            "is_error": bool(event.is_error),
            "error_msg": str(event.error_msg or "")[:200],
            "result": _persistable_cartographic_result(event.result),
        },
        "map_actions": [
            {
                "action_id": str(action.get("action_id") or ""),
                "command": str(action.get("command") or ""),
                "requested": copy.deepcopy(action.get("requested") or {}),
                "mapspec_fingerprint": action.get("mapspec_fingerprint"),
            }
            for action in actions[:16]
            if isinstance(action, dict) and action.get("action_id")
        ],
    }
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
        if state.get("_cartographic_deleted") is True:
            _discard_session_harness(session_id)
            return False
        try:
            current_revision = int(
                state.get("_cartographic_mutation_revision", 0)
            )
            incoming_revision = int(
                (event.result or {}).get("mutation_revision", 0)
            )
        except (TypeError, ValueError):
            _discard_session_harness(session_id)
            return False
        # Lifecycle commit and this persistence use the same distributed lock,
        # but the completed tool may wait behind a newer mutation. Reject its
        # late context rather than regressing durable provenance.
        if incoming_revision <= 0 or incoming_revision != current_revision:
            # A late tool completion may already have populated this worker's
            # process-local harness before durable serialization. Discard it;
            # the next evaluation rehydrates the current durable generation.
            _discard_session_harness(session_id)
            return False
        stored_context = state.get("_cartographic_harness_context")
        if isinstance(stored_context, dict):
            try:
                stored_revision = int(stored_context.get("mutation_revision", 0))
            except (TypeError, ValueError):
                stored_revision = 0
            if stored_revision > incoming_revision:
                _discard_session_harness(session_id)
                return False
        context["mutation_revision"] = incoming_revision
        persisted = await session_data_manager.set_map_state(
            session_id, "_cartographic_harness_context", context
        )
        if persisted is False:
            raise RuntimeError("cartographic context persistence rejected")
        return True
    except Exception as error:  # noqa: BLE001 - preserve completed GIS work
        logger.warning(
            "[PiBridge] unable to persist cartographic context for %s: %s",
            session_id,
            error,
        )
        _discard_session_harness(session_id)
        return False


async def _hydrate_cartographic_harness(
    session_id: str, harness: PiAgentHarness,
    state: Optional[dict] = None,
) -> bool:
    """Restore the latest owned mutation evidence into a fresh worker.

    P-6（#879）：``state`` 允许调用方传入锁内已读快照（observation/ACK 处理
    链此前一次请求内 3 次独立 get_map_state，1MiB 级 mapspec 字段反复冷读/
    重解析）；缺省时自行读取（行为不变）。
    """
    has_local_mutation = any(
        mutation.get("session_id") == session_id
        for mutation in harness.mapspec_mutations
    )
    from app.services.session_data import session_data_manager

    if state is None:
        state = await session_data_manager.get_map_state(session_id)
    if state.get("_cartographic_deleted") is True:
        return False
    context = state.get("_cartographic_harness_context")
    if (
        not isinstance(context, dict)
        or context.get("session_id") != session_id
        or context.get("version") not in (1, 2)
    ):
        return has_local_mutation
    if context.get("version") == 2:
        try:
            if int(context.get("mutation_revision", 0)) != int(
                state.get("_cartographic_mutation_revision", 0)
            ):
                return has_local_mutation
        except (TypeError, ValueError):
            return has_local_mutation
    call = context.get("tool_call")
    if (
        not isinstance(call, dict)
        or not isinstance(call.get("result"), dict)
        or not call["result"].get("mapspec_fingerprint")
    ):
        return has_local_mutation
    tool_call_id = str(call.get("tool_call_id") or "")
    if not tool_call_id:
        return has_local_mutation
    if not any(
        mutation.get("tool_call_id") == tool_call_id
        for mutation in harness.mapspec_mutations
    ):
        for action in context.get("map_actions") or []:
            if not isinstance(action, dict) or not action.get("action_id"):
                continue
            if any(
                issued.get("action_id") == action["action_id"]
                for issued in harness.map_actions_issued
            ):
                continue
            harness.record_map_action_issued(
                session_id=session_id,
                tool_call_id=tool_call_id,
                action_id=str(action["action_id"]),
                command=str(action.get("command") or ""),
                requested=action.get("requested") or {},
                mapspec_fingerprint=action.get("mapspec_fingerprint"),
            )
        harness.record_event(ToolCallEvent(
            tool_call_id=tool_call_id,
            tool_name=str(call.get("tool_name") or "webgis_layer_upsert"),
            arguments=call.get("arguments") or {},
            duration_ms=max(0, int(call.get("duration_ms") or 0)),
            is_error=bool(call.get("is_error")),
            error_msg=str(call.get("error_msg") or "")[:200],
            result=call.get("result") or {},
            session_id=session_id,
        ))
    return True


async def _persist_cartographic_issued_action(
    session_id: str,
    action: dict[str, Any],
    *,
    session_lock_held: bool = False,
) -> None:
    """Add a runtime-repair command to the cross-worker harness projection."""
    if not session_lock_held:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id):
            from app.services.session_data import session_data_manager
            session_data_manager.invalidate_local_cache(session_id)
            await _persist_cartographic_issued_action(
                session_id,
                action,
                session_lock_held=True,
            )
        return
    if session_id in _deleted_cartography_sessions:
        return
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(session_id)
    if state.get("_cartographic_deleted") is True:
        return
    context = state.get("_cartographic_harness_context")
    if not isinstance(context, dict) or context.get("session_id") != session_id:
        return
    try:
        if int(context.get("mutation_revision", 0)) != int(
            state.get("_cartographic_mutation_revision", 0)
        ):
            return
    except (TypeError, ValueError):
        return
    actions = [
        item for item in (context.get("map_actions") or [])
        if isinstance(item, dict) and item.get("action_id") != action.get("action_id")
    ]
    actions.append({
        "action_id": str(action.get("action_id") or ""),
        "command": str(action.get("command") or ""),
        "requested": copy.deepcopy(action.get("requested") or {}),
        "mapspec_fingerprint": action.get("mapspec_fingerprint"),
    })
    context = copy.deepcopy(context)
    context["map_actions"] = actions[-16:]
    persisted = await session_data_manager.set_map_state(
        session_id, "_cartographic_harness_context", context
    )
    if persisted is False:
        raise RuntimeError("cartographic issued-action persistence rejected")


def _not_evaluated_no_harness(session_id: str) -> dict[str, Any]:
    """#1069(A-7): not_evaluated/no_session_harness 门（此前 4 处逐字粘贴）。"""
    return {
        "session_id": session_id,
        "cartography": {
            "status": "not_evaluated",
            "trusted": False,
            "evaluated": False,
            "passed": False,
            "termination_reason": "no_session_harness",
        },
        "gate": {
            "score": 0.0,
            "target": 100.0,
            "passed": False,
            "evaluated": False,
            "reason": "not_evaluated_policy_fail",
            "status": "not_evaluated",
            "trusted": False,
        },
        "overall_passed": False,
    }


async def evaluate_cartographic_session(
    session_id: str, *, session_lock_held: bool = False,
    state: Optional[dict] = None,
) -> dict[str, Any]:
    """Serialize and recompute the session gate after meaningful evidence.

    The distributed session lock makes evaluation atomic with deletion across
    API replicas. Route handlers already holding it opt out of reacquisition.
    """
    if not session_lock_held:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id):
            from app.services.session_data import session_data_manager
            session_data_manager.invalidate_local_cache(session_id)
            # NOTE: 调用方快照读取于锁外，跨锁边界可能已被其它写者更新——
            # 锁内必须重新冷读（#1064 的快照复用只发生在锁内路由）。
            return await evaluate_cartographic_session(
                session_id, session_lock_held=True
            )
    if session_id in _deleted_cartography_sessions:
        return _not_evaluated_no_harness(session_id)
    harness = _get_session_harness(session_id, create=True)
    if harness is None or not await _hydrate_cartographic_harness(
        session_id, harness, state=state
    ):
        return _not_evaluated_no_harness(session_id)
    lock = _cartography_eval_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        return await _evaluate_cartographic_session_unlocked(session_id, state=state)


async def record_cartographic_dispatch_evidence(
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: "ToolDispatchResult",
    duration_ms: int,
) -> None:
    """Shared legacy-agent seam for one display-producing dispatch.

    Pi records all telemetry in ``dispatch_tool``. The legacy chat pipeline
    calls this narrow additive seam so both production agents feed the same
    session-scoped harness and runtime observation/ACK evaluator.
    """
    raw = outcome.raw_result if isinstance(outcome.raw_result, dict) else {}
    if outcome.status != "ok" or not raw.get("mapspec_fingerprint"):
        return
    result_evidence: dict[str, Any] = {
        "status": outcome.status,
        "llm_payload_len": len(outcome.llm_payload),
    }
    from app.lib.harness.evidence import CARTOGRAPHIC_RESULT_EVIDENCE_KEYS
    for key in CARTOGRAPHIC_RESULT_EVIDENCE_KEYS:
        if key in raw:
            result_evidence[key] = raw[key]
    event = ToolCallEvent(
        tool_call_id=tool_call_id,
        # #789: real tool name (no "webgis_layer_upsert" relabel) — the harness
        # classifies mutations structurally from the mapspec_fingerprint.
        tool_name=tool_name,
        arguments=arguments,
        duration_ms=duration_ms,
        is_error=False,
        result=result_evidence,
        session_id=session_id,
    )
    if not await _persist_cartographic_harness_context(
        session_id, event, outcome.map_actions
    ):
        return
    harness = _get_session_harness(session_id, create=True)
    if harness is None:
        return
    for action in outcome.map_actions:
        harness.record_map_action_issued(
            session_id=session_id,
            tool_call_id=tool_call_id,
            action_id=action["action_id"],
            command=action["command"],
            requested=action["requested"],
            mapspec_fingerprint=action.get("mapspec_fingerprint"),
        )
    harness.record_event(event)
    await evaluate_cartographic_session(session_id)


async def _evaluate_cartographic_session_unlocked(
    session_id: str,
    state: Optional[dict] = None,
) -> dict[str, Any]:
    harness = _get_session_harness(session_id)
    if harness is None:
        return _not_evaluated_no_harness(session_id)
    from app.lib.harness.evaluator import HarnessEvaluator
    from app.services.session_data import session_data_manager

    if state is None:
        state = await session_data_manager.get_map_state(session_id)
    observation = state.get("_cartographic_observation")
    sequence = int(observation.get("sequence", 0)) if isinstance(observation, dict) else 0
    fingerprint = str(observation.get("mapspec_fingerprint") or "") if isinstance(observation, dict) else ""
    actions = await session_data_manager.get_map_action_events(session_id)
    evidence_revision = hashlib.sha256(json.dumps(
        {
            # Session stores cap ACKs at 200 and harness tool evidence at 1000.
            # Hash the complete bounded windows: a late ACK for an older repair
            # must invalidate a prior evaluation even after 16 newer actions.
            "actions": [
                {
                    "id": action.get("action_id"),
                    "status": action.get("status"),
                    "finished_at": action.get("finished_at"),
                }
                for action in actions
                if isinstance(action, dict)
            ],
            "tools": [
                {
                    "id": call.get("tool_call_id"),
                    "name": call.get("name"),
                    "error": call.get("is_error"),
                }
                for call in harness.tool_results
                if isinstance(call, dict)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    cache_key = (
        session_id,
        fingerprint,
        sequence,
        evidence_revision,
    )
    cached = _cartography_eval_cache.get(cache_key)
    if cached is not None:
        _cartography_eval_cache.move_to_end(cache_key)
        return copy.deepcopy(cached)

    evidence = await harness.evaluate_with_evidence()
    gate = HarnessEvaluator().evaluate_evidence(
        evidence,
        require_evaluated=False,
        require_cartography=True,
    )
    cartography_gate = gate.get("checks", {}).get("CartographicQuality") or {}
    result = {
        "session_id": session_id,
        "cartography": evidence.get("cartography") or {},
        "gate": cartography_gate,
        "overall_passed": bool(cartography_gate.get("passed")),
    }
    result = await _advance_runtime_cartographic_repair(
        session_id=session_id,
        harness=harness,
        result=result,
        map_state=state,
        actions=actions,
    )
    terminal_cacheable = (
        result.get("repair_action") is None
        and (result.get("cartography") or {}).get("status") in {
            "passed", "passed_with_warnings", "failed_unrepairable",
            "repair_exhausted", "superseded",
        }
    )
    if terminal_cacheable:
        _cartography_eval_cache[cache_key] = copy.deepcopy(result)
    while len(_cartography_eval_cache) > _HARNESS_REGISTRY_LIMIT * 4:
        _cartography_eval_cache.popitem(last=False)
    if session_id in _deleted_cartography_sessions:
        return {
            "session_id": session_id,
            "cartography": {
                "status": "superseded",
                "trusted": False,
                "evaluated": False,
                "passed": False,
                "termination_reason": "session_deleted",
            },
            "overall_passed": False,
        }
    persisted = await session_data_manager.set_map_state(
        session_id, "_cartographic_review", result
    )
    if persisted is False:
        return {
            "session_id": session_id,
            "cartography": {
                "status": "not_evaluated",
                "trusted": False,
                "evaluated": False,
                "passed": False,
                "termination_reason": "evidence_persistence_unavailable",
            },
            "overall_passed": False,
        }
    return result


async def _advance_runtime_cartographic_repair(
    *,
    session_id: str,
    harness: PiAgentHarness,
    result: dict[str, Any],
    map_state: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Issue at most one event-driven AUTO_SAFE repair for current evidence."""
    if session_id in _deleted_cartography_sessions:
        return result
    from app.lib.cartography.runtime_repair import (
        MAX_RUNTIME_REPAIR_ITERATIONS,
        plan_runtime_repairs,
    )
    from app.services.mapspec.store import mapspec_store_instance
    from app.services.session_data import session_data_manager

    async def persist_repair_state(value: dict[str, Any]) -> None:
        if session_id not in _deleted_cartography_sessions:
            persisted = await session_data_manager.set_map_state(
                session_id, "_cartographic_repair_state", value
            )
            if persisted is False:
                raise RuntimeError("cartographic repair-state persistence rejected")

    cartography = result.get("cartography")
    if not isinstance(cartography, dict):
        return result
    fingerprint = str(cartography.get("mapspec_fingerprint") or "")
    if not fingerprint:
        return result
    observation = map_state.get("_cartographic_observation")
    if not isinstance(observation, dict):
        return result
    mapspec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(mapspec, dict):
        return result

    previous = map_state.get("_cartographic_repair_state")
    repair_state = (
        copy.deepcopy(previous)
        if isinstance(previous, dict) and previous.get("mapspec_fingerprint") == fingerprint
        else {"mapspec_fingerprint": fingerprint, "attempts": []}
    )
    attempts = repair_state.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempts = [attempt for attempt in attempts[:MAX_RUNTIME_REPAIR_ITERATIONS] if isinstance(attempt, dict)]
    acks_by_id = {
        str(action.get("action_id")): action
        for action in actions
        if isinstance(action, dict) and action.get("action_id")
    }
    for attempt in attempts:
        ack = acks_by_id.get(str(attempt.get("action_id") or ""))
        attempt["status"] = str(ack.get("status")) if ack else "issued"
        if ack and ack.get("error"):
            attempt["error"] = str(ack["error"])[:200]
    repair_state["attempts"] = attempts
    cartography["repair_attempts"] = copy.deepcopy(attempts)

    if cartography.get("status") in ("passed", "passed_with_warnings"):
        repair_state["termination_reason"] = "quality_converged"
        await persist_repair_state(repair_state)
        return result
    if cartography.get("status") != "failed_repairable":
        if attempts:
            repair_state["termination_reason"] = str(
                cartography.get("termination_reason") or "repair_stopped"
            )
            await persist_repair_state(repair_state)
        return result

    plan = plan_runtime_repairs(mapspec, observation, cartography)
    if plan is None:
        cartography["status"] = "failed_unrepairable"
        cartography["passed"] = False
        cartography["termination_reason"] = "no_safe_runtime_repair"
        result["overall_passed"] = False
        repair_state["termination_reason"] = "no_safe_runtime_repair"
        await persist_repair_state(repair_state)
        return result

    patch_fingerprint = str(plan["patch_fingerprint"])
    prior = next(
        (
            attempt for attempt in attempts
            if attempt.get("patch_fingerprint") == patch_fingerprint
        ),
        None,
    )
    if prior is not None:
        if prior.get("status") == "issued":
            cartography["status"] = "not_evaluated"
            cartography["passed"] = False
            cartography["termination_reason"] = "runtime_repair_ack_pending"
        elif prior.get("status") in ("cancelled", "superseded"):
            cartography["status"] = "superseded"
            cartography["passed"] = False
            cartography["termination_reason"] = "user_or_newer_intent"
        else:
            cartography["status"] = "repair_exhausted"
            cartography["passed"] = False
            cartography["termination_reason"] = "repeated_runtime_repair"
        result["overall_passed"] = False
        repair_state["termination_reason"] = cartography["termination_reason"]
        await persist_repair_state(repair_state)
        return result
    if len(attempts) >= MAX_RUNTIME_REPAIR_ITERATIONS:
        cartography["status"] = "repair_exhausted"
        cartography["passed"] = False
        cartography["termination_reason"] = "runtime_repair_iteration_limit"
        result["overall_passed"] = False
        repair_state["termination_reason"] = "runtime_repair_iteration_limit"
        await persist_repair_state(repair_state)
        return result

    sequence = int(observation.get("sequence") or 0)
    action_id = f"ma-carto-{uuid.uuid4().hex[:16]}"
    source_tool_call_id = str(cartography.get("source_tool_call_id") or "")
    action = {
        "action_id": action_id,
        "command": "cartographic_runtime_repair",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "correlation": {
            "session_id": session_id,
            "step_id": source_tool_call_id,
        },
        "params": {
            "mapspec_fingerprint": fingerprint,
            "observation_sequence": sequence,
            "patch_fingerprint": patch_fingerprint,
            "repair_patches": plan["patches"],
        },
    }
    harness.record_map_action_issued(
        session_id=session_id,
        tool_call_id=source_tool_call_id,
        action_id=action_id,
        command="cartographic_runtime_repair",
        requested={
            "mapspec_fingerprint": fingerprint,
            "observation_sequence": sequence,
            "patch_fingerprint": patch_fingerprint,
        },
        mapspec_fingerprint=fingerprint,
    )
    await _persist_cartographic_issued_action(
        session_id,
        {
            "action_id": action_id,
            "command": "cartographic_runtime_repair",
            "requested": {
                "mapspec_fingerprint": fingerprint,
                "observation_sequence": sequence,
                "patch_fingerprint": patch_fingerprint,
            },
            "mapspec_fingerprint": fingerprint,
        },
        session_lock_held=True,
    )
    attempts.append({
        "iteration": len(attempts) + 1,
        "action_id": action_id,
        "patch_fingerprint": patch_fingerprint,
        "observation_sequence": sequence,
        "status": "issued",
        "repairability": "auto_safe",
        "rules": sorted({
            rule
            for patch in plan["patches"]
            for rule in patch.get("rules", [])
        }),
    })
    repair_state["attempts"] = attempts
    repair_state["termination_reason"] = "runtime_repair_issued"
    cartography["repair_attempts"] = copy.deepcopy(attempts)
    await persist_repair_state(repair_state)
    result["repair_action"] = action
    result["overall_passed"] = False
    return result


def get_harness(session_id: Optional[str] = None) -> Optional[PiAgentHarness]:
    """Return the active evaluation harness, or None if disabled."""
    if session_id:
        return _get_session_harness(session_id)
    return _harness


def get_harness_telemetry_summary() -> Optional[dict[str, Any]]:
    """#792 (F-A-4): service-level harness telemetry aggregated across the
    per-session harness registry.

    ``/metrics/digest`` used to present the LAST-TOUCHED session's harness as
    service-level telemetry (``get_harness()`` with no session returns the
    module-global ``_harness`` that ``_get_session_harness`` re-points on every
    touch), so with concurrent sessions the digest flip-flopped between one
    session's partial windows. Aggregate instead: mean of non-null per-session
    rates (null when NO session has evidence for that rate), ``evaluated`` is
    true when any session has evidence, counts sum, and ``harness_sessions``
    names how many per-session harnesses contributed.
    """
    harnesses = list(_harnesses.values())
    if not harnesses:
        return None
    summaries = [h.get_telemetry_summary() for h in harnesses]
    rate_keys = sorted({
        key
        for summary in summaries
        for key in (summary.get("rates") or {})
    })
    rates: dict[str, Any] = {}
    evaluated: dict[str, bool] = {}
    for key in rate_keys:
        values = [
            summary["rates"][key]
            for summary in summaries
            if (summary.get("rates") or {}).get(key) is not None
        ]
        rates[key] = round(sum(values) / len(values), 2) if values else None
        evaluated[key] = any(
            (summary.get("evaluated") or {}).get(key) for summary in summaries
        )
    count_keys = sorted({
        key
        for summary in summaries
        for key in (summary.get("counts") or {})
    })
    counts = {
        key: float(sum(
            (summary.get("counts") or {}).get(key, 0.0) for summary in summaries
        ))
        for key in count_keys
    }
    counts["HarnessSessions"] = float(len(harnesses))
    return {
        "rates": rates,
        "evaluated": evaluated,
        "counts": counts,
        "harness_sessions": len(harnesses),
    }


def clear_cartographic_session_state(session_id: str) -> None:
    """Drop process-local evidence when the owned session is deleted."""
    global _harness
    _deleted_cartography_sessions[session_id] = None
    _deleted_cartography_sessions.move_to_end(session_id)
    while len(_deleted_cartography_sessions) > _DELETED_SESSION_TOMBSTONE_LIMIT:
        _deleted_cartography_sessions.popitem(last=False)
    removed = _harnesses.pop(session_id, None)
    _cartography_eval_locks.pop(session_id, None)
    for key in list(_cartography_eval_cache):
        if key[0] == session_id:
            del _cartography_eval_cache[key]
    if removed is not None and _harness is removed:
        _harness = next(reversed(_harnesses.values()), None) if _harnesses else None


def restore_cartographic_session_state(session_id: str) -> None:
    """Undo a pre-delete tombstone when the authoritative delete failed."""
    _deleted_cartography_sessions.pop(session_id, None)


def is_cartographic_session_deleted(session_id: str) -> bool:
    """Deletion tombstone consulted by late observation/ACK writers."""
    return session_id in _deleted_cartography_sessions

