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
_cartography_eval_locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()
_CARTOGRAPHY_EVAL_LOCKS_LIMIT = 256
_deleted_cartography_sessions: "OrderedDict[str, None]" = OrderedDict()
_DELETED_SESSION_TOMBSTONE_LIMIT = 1_024
_HARNESS_REGISTRY_LIMIT = 128
_harness_feature_enabled = os.getenv("PI_HARNESS_ENABLED", "").lower() in (
    "true", "1", "yes"
)

# ADR-0158 P1：评审触发从「结果携带 mapspec_fingerprint」扩展为「产生了地图
# 变更」。fingerprint 只覆盖 authoring seam 产物；前端 command 渲染路径
# （模板 symbology、原生热力图、图层样式族）同样改变图面，却从不进入评审。
# 这里是命令族白名单 —— 只有图面内容变更触发评审；相机/注记/导出/底图
# chrome 不在其中（底图走独立 SetBasemapIntent 通道）。
MAP_CHANGE_COMMANDS = frozenset({
    "add_layer",
    "add_native_heatmap",
    "add_heatmap_raster",
    "layer_style_update",
    "layer_visibility_update",
    "apply_layer_filter",
    "reorder_layer",
    "remove_layer",
})


def result_indicates_map_change(raw_result: Any) -> bool:
    """结构化判定一次成功结果是否改变了图面内容（评审触发条件）。

    fingerprint 存在 ⇒ 变更（authoring 路径，语义不变）；否则结果携带的
    ``command``/``commands[]`` 命中白名单 ⇒ 变更。只读形状检查，不猜测。
    """
    if not isinstance(raw_result, dict):
        return False
    if raw_result.get("mapspec_fingerprint"):
        return True
    candidates: list[Any] = []
    commands = raw_result.get("commands")
    if isinstance(commands, list):
        candidates.extend(item for item in commands if isinstance(item, dict))
    if raw_result.get("command"):
        candidates.append(raw_result)
    for command in candidates:
        name = str(command.get("command") or command.get("type") or "")
        if name.strip().lower() in MAP_CHANGE_COMMANDS:
            return True
    return False


def _get_cartography_eval_lock(session_id: str) -> asyncio.Lock:
    """Get or create per-session cartography evaluation lock with LRU bounds."""
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    lock = _cartography_eval_locks.get(session_id)
    if lock is not None:
        lock_loop = getattr(lock, "_loop", None)
        if lock_loop is not None and lock_loop is not current_loop:
            lock = asyncio.Lock()
            _cartography_eval_locks[session_id] = lock
        _cartography_eval_locks.move_to_end(session_id)
        return lock

    lock = asyncio.Lock()
    _cartography_eval_locks[session_id] = lock
    _cartography_eval_locks.move_to_end(session_id)
    while len(_cartography_eval_locks) > _CARTOGRAPHY_EVAL_LOCKS_LIMIT:
        _cartography_eval_locks.popitem(last=False)
    return lock


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

        # v2(audit F2): harness context 是共享 Redis 投影 —— 降级锁 fail-closed。
        async with session_lock_registry.lock(session_id, fail_on_degraded=True):
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

        # v2(audit F2): issued-action 投影是共享 Redis 写 —— 降级锁 fail-closed。
        async with session_lock_registry.lock(session_id, fail_on_degraded=True):
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

    # v2(audit P3)：两个字段的定向读（tombstone + harness context）——
    # 旧实现全量解析 mapspec。
    _get_field = getattr(session_data_manager, "get_state_field", None)
    if callable(_get_field):
        if await _get_field(session_id, "_cartographic_deleted") is True:
            return
        context = await _get_field(session_id, "_cartographic_harness_context")
    else:
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
    _defer_selfheal_commit: bool = True,
) -> dict[str, Any]:
    """Serialize and recompute the session gate after meaningful evidence.

    The distributed session lock makes evaluation atomic with deletion across
    API replicas. Route handlers already holding it opt out of reacquisition.
    """
    if not session_lock_held:
        from app.services.distributed_lock import session_lock_registry

        # v2(audit F2): 评估持锁读冷态 —— 降级锁下两 pod 并发评估/写评审，
        # 世代守卫只在写侧兜底；fail-closed 保持读-判-写一致。
        async with session_lock_registry.lock(session_id, fail_on_degraded=True):
            from app.services.session_data import session_data_manager
            session_data_manager.invalidate_local_cache(session_id)
            # NOTE: 调用方快照读取于锁外，跨锁边界可能已被其它写者更新——
            # 锁内必须重新冷读（#1064 的快照复用只发生在锁内路由）。
            result = await evaluate_cartographic_session(
                session_id, session_lock_held=True, state=state,
                _defer_selfheal_commit=False,
            )
        # ADR-0158：呈现提交（换色带/改版面）在会话锁释放后**内联**执行 ——
        # lifecycle apply_mutation 自带会话锁，持锁重入会自死锁；锁外内联
        # 保证确定性（提交完成即本调用返回，新世代评审由下一次观察触发）。
        pending_commit = result.pop("_pending_selfheal_commit", None)
        if pending_commit is not None:
            await _execute_selfheal_commit(session_id, pending_commit)
        return result
    if session_id in _deleted_cartography_sessions:
        return _not_evaluated_no_harness(session_id)
    harness = _get_session_harness(session_id, create=True)
    if harness is None or not await _hydrate_cartographic_harness(
        session_id, harness, state=state
    ):
        return _not_evaluated_no_harness(session_id)
    lock = _get_cartography_eval_lock(session_id)
    async with lock:
        result = await _evaluate_cartographic_session_unlocked(session_id, state=state)
    if _defer_selfheal_commit:
        # 直接持锁调用方（observation/ACK 路由）：本帧不能执行提交
        # （apply_mutation 重入死锁），调度后台任务锁外执行。
        pending_commit = result.pop("_pending_selfheal_commit", None)
        if pending_commit is not None:
            _schedule_selfheal_commit(session_id, pending_commit)
    return result


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
    if outcome.status != "ok" or not result_indicates_map_change(raw):
        return
    has_generation = bool(raw.get("mapspec_fingerprint"))
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
    # ADR-0158 P1：command-only 变更（无 fingerprint / 无 mutation revision）
    # 不能进 durable harness context —— 该投影只接受真实 MapSpec 世代（revision
    # 守卫会拒绝 revision=0 的迟到帧）。它们只记进程本地证据并触发共享评估；
    # 评估本身重读 session 权威状态，诚实回报（无世代标签 ⇒ not_evaluated）。
    if has_generation and not await _persist_cartographic_harness_context(
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


# ── 自愈呈现提交：锁外执行器（ADR-0158 P5/P6） ──────────────────────────
# apply_mutation 自带会话锁；评估路径持锁，直接提交会自死锁。编排器只产出
# ``_pending_selfheal_commit`` 计划，评估入口在锁释放后调度本执行器：
# lifecycle 提交（自带确定性复审 + 锁 guard + 世代推进）→ harness mutation
# 台账登记 → repair_state 尝试状态回填。
_pending_selfheal_commit_tasks: set = set()


def _schedule_selfheal_commit(session_id: str, pending: dict[str, Any]) -> None:
    task = asyncio.create_task(_execute_selfheal_commit(session_id, pending))
    _pending_selfheal_commit_tasks.add(task)
    task.add_done_callback(_pending_selfheal_commit_tasks.discard)


async def _execute_selfheal_commit(
    session_id: str, pending: dict[str, Any]
) -> None:
    from app.services.mapspec_store import mapspec_store
    from app.services.session_data import session_data_manager as sdm

    commit_call_id = str(pending.get("commit_call_id") or "")
    results: list[dict[str, Any]] = []
    failure: Optional[str] = None
    try:
        # 锁纪律：layer_upsert → apply_mutation 自带 per-session 分布式锁，
        # 这里绝不能再包一层会话锁（不可重入 ⇒ 自死锁）。
        for update in pending.get("iterations") or []:
            layer = update.get("layer")
            if not isinstance(layer, dict):
                continue
            commit_result = await mapspec_store.layer_upsert(
                session_id, layer
            )
            if not commit_result.get("success"):
                logger.warning(
                    "[SelfHeal] presentation commit rejected for %s/%s: %s",
                    session_id, update.get("layer_id"),
                    str(commit_result.get("message")
                        or commit_result.get("error_code") or "unknown")[:200],
                )
            results.append({
                "layer_id": str(update.get("layer_id") or ""),
                "operation": "presentation_commit",
                "committed": bool(commit_result.get("success")),
                "error_code": commit_result.get("error_code"),
                "is_compiled": commit_result.get("is_compiled"),
                "mapspec_fingerprint": commit_result.get(
                    "mapspec_fingerprint"
                ),
                "runtime_observation_seq": commit_result.get(
                    "runtime_observation_seq"
                ),
                "mutation_revision": commit_result.get("mutation_revision"),
                "warnings": [
                    str(w)[:200]
                    for w in (commit_result.get("warnings") or [])[:8]
                ],
            })
        # ADR-0158 P6：提交产生的新世代必须进入 harness mutation 台账
        # （结构性分类：结果携带 fingerprint）——否则后续评估对着旧世代
        # reported 指纹永远 superseded，闭环断裂。进程本地登记。
        succeeded = [item for item in results if item["committed"]]
        if succeeded:
            harness = _get_session_harness(session_id, create=True)
            if harness is not None:
                last = succeeded[-1]
                harness.record_tool_call(
                    commit_call_id,
                    "cartographic_selfheal_commit",
                    {"layer": {"id": str(last["layer_id"])}},
                    session_id=session_id,
                )
                harness.record_tool_result(
                    commit_call_id,
                    "cartographic_selfheal_commit",
                    {
                        "success": True,
                        "is_compiled": last.get("is_compiled"),
                        "mapspec_fingerprint": last.get(
                            "mapspec_fingerprint"
                        ),
                        "runtime_observation_seq": last.get(
                            "runtime_observation_seq"
                        ),
                        "mutation_revision": last.get("mutation_revision"),
                    },
                    session_id=session_id,
                )
    except Exception as exc:  # noqa: BLE001 — 提交失败诚实留在尝试台账
        logger.warning(
            "[SelfHeal] presentation commit failed for %s: %s",
            session_id, type(exc).__name__,
        )
        failure = type(exc).__name__
        results.append({
            "layer_id": "",
            "committed": False,
            "error": str(exc)[:200],
        })
    # 尝试状态回填对失败路径同样生效（审计链不断：issued 不得永久悬挂）。
    if results:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(
            session_id, fail_on_degraded=True
        ):
            sdm.invalidate_local_cache(session_id)
            state = await sdm.get_map_state(session_id)
            repair_state = state.get("_cartographic_repair_state")
            updated = False
            if isinstance(repair_state, dict) and isinstance(
                repair_state.get("attempts"), list
            ):
                for attempt in repair_state["attempts"]:
                    if (
                        isinstance(attempt, dict)
                        and str(attempt.get("action_id") or "")
                        == commit_call_id
                    ):
                        attempt["status"] = (
                            "succeeded" if failure is None and results
                            and all(item["committed"] for item in results)
                            else "failed"
                        )
                        attempt["commits"] = results[:8]
                        if failure:
                            attempt["error"] = failure
                        updated = True
                        break
            if updated:
                persisted = await sdm.set_map_state(
                    session_id, "_cartographic_repair_state", repair_state
                )
                if persisted is False:
                    logger.warning(
                        "[SelfHeal] commit state update rejected for %s",
                        session_id,
                    )


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
        repair_patch_fingerprint,
    )
    from app.lib.cartography.selfheal_actions import (
        authorized,
        build_presentation_commit,
        candidates_from_rejected,
        quality_improved,
        quality_snapshot,
        quality_worse,
        select_actions,
        triggers_from_review,
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
    if isinstance(previous, dict) and previous.get("mapspec_fingerprint") == fingerprint:
        repair_state = copy.deepcopy(previous)
    else:
        # ADR-0158 P5：呈现提交会推进世代 → 指纹变化触发重置。为防"换代后
        # 同一动作无限重选/无法回退"，重置时继承上一代的有界尝试历史与
        # tried 集合（防色带轮换循环）。
        prior_attempts: list[dict[str, Any]] = []
        inherited_tried: set[str] = set()
        if isinstance(previous, dict) and isinstance(previous.get("attempts"), list):
            prior_attempts = [
                item for item in previous["attempts"][:6] if isinstance(item, dict)
            ]
            for item in prior_attempts:
                if item.get("action_name"):
                    inherited_tried.add(str(item["action_name"]))
                inherited_tried.update(
                    str(cover) for cover in (item.get("covers") or [])
                )
        repair_state = {
            "mapspec_fingerprint": fingerprint,
            "attempts": [],
            "history": prior_attempts,
            "inherited_tried": sorted(inherited_tried - {""}),
        }
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

    # ── ADR-0158 P5：修复后重评 ──
    # 最近一次已成功（ACK 终态 + 其后新观察已纳入本轮证据）的动作做改善
    # 判定。语义（确定性优先，防止"回退"伤害权威投影）：
    # - 投影恢复 patch：变差（有害）→ 本轮回退；持平/未改善 → 不回退
    #  （回退会把 live 拉离权威 desired），交给既有 repeated→exhausted；
    # - 呈现提交（换代）：未改善即回退（重提交变更前呈现，防色带轮换循环）。
    quality_now = quality_snapshot(cartography)
    pending_rollback = None
    pending_commit_revert = None

    def _judge(entry: dict[str, Any]) -> bool:
        """对一条已成功尝试做一次性改善判定；返回是否本轮新判定。"""
        if entry.get("quality_after") is not None:
            return False
        entry["quality_after"] = copy.deepcopy(quality_now)
        entry["improved"] = quality_improved(
            entry.get("quality_before"), quality_now
        )
        entry["worse"] = quality_worse(
            entry.get("quality_before"), quality_now
        )
        return True

    for attempt in reversed(attempts):
        if attempt.get("status") != "succeeded":
            continue
        if _judge(attempt) and not attempt.get("improved"):
            if attempt.get("kind") in (None, "patch", "rollback"):
                if attempt.get("worse") and attempt.get("patches"):
                    # 有害变更（比修复前更差）→ 必须撤销。
                    pending_rollback = attempt
                    cartography["selfheal_last_verdict"] = "worse_than_before"
            elif attempt.get("kind") == "commit":
                pass  # 提交回退走 before_presentation（见 history 判定）
        break
    # 呈现提交推进世代 → 提交尝试落在 history；对最新一条未判定的提交做
    # 判定，未改善则回退（重提交变更前呈现，防色带轮换循环）。
    history = repair_state.get("history")
    if isinstance(history, list):
        for past in reversed(history):
            if not isinstance(past, dict) or past.get("kind") != "commit":
                continue
            if past.get("rolled_back"):
                break
            if _judge(past) and not past.get("improved"):
                pending_commit_revert = past
                cartography["selfheal_last_verdict"] = "no_improvement"
            break
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

    sequence = int(observation.get("sequence") or 0)
    source_tool_call_id = str(cartography.get("source_tool_call_id") or "")

    async def issue_frontend_patch(
        plan: dict[str, Any],
        *,
        kind: str,
        action_name: str,
        covers: list[str],
    ) -> dict[str, Any]:
        """签发一个同代前端修复动作（既有 cartographic_runtime_repair 通道）。"""
        action_id = f"ma-carto-{uuid.uuid4().hex[:16]}"
        patch_fingerprint = str(plan["patch_fingerprint"])
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
            "kind": kind,
            "action_id": action_id,
            "action_name": action_name,
            "covers": covers,
            "patch_fingerprint": patch_fingerprint,
            "observation_sequence": sequence,
            "status": "issued",
            "repairability": "auto_safe",
            # 有界审计：补丁本体（呈现字段，≤8 条）留档供回退与复盘。
            "patches": copy.deepcopy(plan["patches"][:8]),
            "quality_before": copy.deepcopy(quality_now),
            "rules": sorted({
                rule
                for patch in plan["patches"]
                for rule in patch.get("rules", [])
            }),
        })
        return action

    async def terminate(
        status: str, reason: str, *, extra: Optional[dict] = None
    ) -> dict[str, Any]:
        cartography["status"] = status
        cartography["passed"] = False
        cartography["termination_reason"] = reason
        result["overall_passed"] = False
        repair_state["termination_reason"] = reason
        if extra:
            cartography.update(extra)
        await persist_repair_state(repair_state)
        return result

    # ── ADR-0158 P4/P5：注册表驱动选择 ──
    triggers = triggers_from_review(cartography)
    runtime_action_ids = {
        "restore_visibility", "reapply_opacity",
        "refresh_legend", "restore_style_projection",
    }
    tried_actions: set[str] = {
        str(item) for item in (repair_state.get("inherited_tried") or [])
    }
    if attempts:
        tried_actions |= runtime_action_ids
    for attempt in attempts:
        tried_actions.add(str(attempt.get("action_name") or ""))
        tried_actions.update(attempt.get("covers") or [])
    ranked = select_actions(triggers, tried_actions=sorted(tried_actions))
    unauthorized = [
        {
            "action_id": spec.action_id,
            "risk": spec.risk,
            "description": spec.description,
            "reason": (
                "explicit_only_suggestion_only"
                if spec.risk == "explicit_only"
                else "semantic_risk_requires_authorization"
            ),
        }
        for spec in ranked if not authorized(spec)
    ]
    if unauthorized:
        cartography["selfheal_suggestions"] = unauthorized[:6]

    # 1) 回退优先（a）：未改善的呈现提交 → 重提交变更前呈现（防色带轮换
    #    循环；提交世代已推进，这里在新一代里诚实撤销）。
    if pending_commit_revert is not None:
        before_presentation = pending_commit_revert.get("before_presentation") or {}
        revert_layers = []
        for layer_id, snapshot in list(before_presentation.items())[:8]:
            target = next(
                (
                    layer for layer in (mapspec.get("layers") or [])
                    if isinstance(layer, dict)
                    and str(layer.get("id") or "") == str(layer_id)
                ),
                None,
            )
            if target is None or not isinstance(snapshot, dict):
                continue
            updated = copy.deepcopy(target)
            if isinstance(snapshot.get("legend_spec"), dict):
                updated["legend_spec"] = copy.deepcopy(snapshot["legend_spec"])
            if isinstance(snapshot.get("paint"), dict):
                updated["paint"] = copy.deepcopy(snapshot["paint"])
            revert_layers.append(updated)
        if revert_layers:
            # 锁纪律：apply_mutation 自带会话锁，持评估锁直接提交会自死锁
            # → 这里只产出 pending 提交计划，由入口在锁外调度执行。
            revert_call_id = f"selfheal-revert-{uuid.uuid4().hex[:16]}"
            attempts.append({
                "iteration": len(attempts) + 1,
                "kind": "commit_revert",
                "action_id": revert_call_id,
                "action_name": f"revert:{pending_commit_revert.get('action_name')}",
                "covers": [str(pending_commit_revert.get("action_name") or "")],
                "status": "issued",
                "repairability": "auto_safe",
                "quality_before": copy.deepcopy(quality_now),
            })
            pending_commit_revert["rolled_back"] = True
            repair_state["attempts"] = attempts
            repair_state["termination_reason"] = "selfheal_commit_reverted"
            cartography["repair_attempts"] = copy.deepcopy(attempts)
            cartography["status"] = "not_evaluated"
            cartography["passed"] = False
            cartography["termination_reason"] = "selfheal_presentation_commit_issued"
            result["selfheal_commit_issued"] = {
                "commit_call_id": revert_call_id,
                "action_name": f"revert:{pending_commit_revert.get('action_name')}",
                "layers": [str(updated.get("id") or "") for updated in revert_layers],
            }
            result["_pending_selfheal_commit"] = {
                "commit_call_id": revert_call_id,
                "action_name": f"revert:{pending_commit_revert.get('action_name')}",
                "iterations": [
                    {"layer_id": str(updated.get("id") or ""), "layer": updated}
                    for updated in revert_layers
                ],
            }
            result["overall_passed"] = False
            await persist_repair_state(repair_state)
            return result

    # 1) 回退优先（b）：未改善的已成功 patch → 补偿动作（同代 before/desired 互换）。
    if pending_rollback is not None:
        rollback_patches = []
        for patch in pending_rollback.get("patches") or []:
            before = {
                key: value for key, value in (patch.get("before") or {}).items()
                if key != "_intentGeneration"
            }
            desired = dict(patch.get("desired") or {})
            if not desired or not before:
                continue
            rollback_patches.append({
                "layer_id": patch.get("layer_id"),
                "mapspec_layer_id": patch.get("mapspec_layer_id"),
                "before": {
                    **desired,
                    "_intentGeneration": (
                        (patch.get("before") or {}).get("_intentGeneration")
                    ),
                },
                "desired": before,
                "rules": [f"rollback:{pending_rollback.get('action_name', 'repair')}"],
            })
        if rollback_patches:
            prior_patch_ids = {
                str(attempt.get("patch_fingerprint")) for attempt in attempts
            }
            rollback_plan = {
                "patches": rollback_patches,
                "patch_fingerprint": repair_patch_fingerprint(rollback_patches),
            }
            if rollback_plan["patch_fingerprint"] not in prior_patch_ids:
                if len(attempts) >= MAX_RUNTIME_REPAIR_ITERATIONS:
                    return await terminate(
                        "repair_exhausted", "runtime_repair_iteration_limit"
                    )
                action = await issue_frontend_patch(
                    rollback_plan,
                    kind="rollback",
                    action_name="rollback_repair",
                    covers=[str(pending_rollback.get("action_name") or "")],
                )
                repair_state["attempts"] = attempts
                repair_state["termination_reason"] = "selfheal_rollback_issued"
                cartography["repair_attempts"] = copy.deepcopy(attempts)
                await persist_repair_state(repair_state)
                result["repair_action"] = action
                result["overall_passed"] = False
                return result

    # 2) 首次 runtime patch：既有 union 行为逐字节保留（无尝试历史时）。
    has_patch_attempt = any(
        attempt.get("kind") in (None, "patch") for attempt in attempts
    )
    if not has_patch_attempt:
        plan = plan_runtime_repairs(mapspec, observation, cartography)
        if plan is not None and not plan.get("patches"):
            # W15 锁下沉：全部命中用户锁 → 无可下发修复，诚实终止并披露
            # （不发空修复动作；用户解锁后新观察重进回路）。呈现提交对被锁
            # 图层同样会被 lifecycle 拒绝 —— 不烧尝试槽。
            return await terminate(
                "failed_unrepairable",
                "locked_layer_repair_refused",
                extra={"locked_refused": list(plan.get("locked_refused") or [])},
            )
        if plan is not None:
            patch_fingerprint = str(plan["patch_fingerprint"])
            prior = next(
                (
                    attempt for attempt in attempts
                    if attempt.get("patch_fingerprint") == patch_fingerprint
                ),
                None,
            )
            if prior is None:
                if len(attempts) >= MAX_RUNTIME_REPAIR_ITERATIONS:
                    return await terminate(
                        "repair_exhausted", "runtime_repair_iteration_limit"
                    )
                action = await issue_frontend_patch(
                    plan,
                    kind="patch",
                    action_name="restore_projection",
                    covers=sorted(runtime_action_ids),
                )
                repair_state["attempts"] = attempts
                repair_state["termination_reason"] = "runtime_repair_issued"
                cartography["repair_attempts"] = copy.deepcopy(attempts)
                await persist_repair_state(repair_state)
                result["repair_action"] = action
                result["overall_passed"] = False
                return result

    # 3) desired_state 呈现提交（rotate_palette / clamp_layout / 显式授权的
    #    分类调整）：经 lifecycle layer_upsert 走既有评审 + 锁 guard + 世代
    #    推进；提交后本轮诚实报 not_evaluated（被评审世代已被替换）。
    for spec in ranked:
        if not authorized(spec) or spec.surface != "desired_state":
            continue
        if spec.action_id in tried_actions:
            continue
        rejected_pool = candidates_from_rejected(mapspec)
        layers = [
            layer for layer in (mapspec.get("layers") or [])
            if isinstance(layer, dict)
        ]
        commits: list[dict[str, Any]] = []
        for layer in layers[:8]:
            rejected = next(
                (
                    item for item in rejected_pool
                    if str(item.get("layer_id") or "") == str(layer.get("id") or "")
                ),
                rejected_pool[0] if rejected_pool else None,
            )
            recipe = build_presentation_commit(
                spec, layer=layer, rejected=rejected
            )
            if recipe is not None:
                commits.append(recipe)
        if not commits:
            continue
        commit_fingerprint = repair_patch_fingerprint([
            {"layer_id": str(recipe.get("layer_id") or ""),
             "operation": recipe.get("operation")}
            for recipe in commits
        ])
        if commit_fingerprint in tried_actions:
            continue
        if len(attempts) >= MAX_RUNTIME_REPAIR_ITERATIONS:
            return await terminate(
                "repair_exhausted", "runtime_repair_iteration_limit"
            )
        commit_call_id = f"selfheal-{uuid.uuid4().hex[:16]}"
        commit_updates: list[dict[str, Any]] = []
        before_presentation: dict[str, Any] = {}
        for recipe in commits:
            layer_id = str(recipe.get("layer_id") or "")
            target = next(
                (
                    layer for layer in layers
                    if str(layer.get("id") or "") == layer_id
                ),
                None,
            )
            if target is None:
                continue
            updated = copy.deepcopy(target)
            if isinstance(recipe.get("legend_spec"), dict):
                before_presentation[layer_id] = {
                    "legend_spec": copy.deepcopy(target.get("legend_spec")),
                    "paint": copy.deepcopy(target.get("paint")),
                }
                updated["legend_spec"] = recipe["legend_spec"]
            if isinstance(recipe.get("paint"), dict):
                # 换色带必须连 paint 输出色一起换 —— legend↔paint 漂移会
                # 被 LEGEND_STYLE_EQUIVALENCE 拒绝（提交面 fail-closed）。
                updated["paint"] = {
                    **(updated.get("paint") or {}),
                    **recipe["paint"],
                }
            commit_updates.append({"layer_id": layer_id, "layer": updated})
        if not commit_updates:
            continue
        attempts.append({
            "iteration": len(attempts) + 1,
            "kind": "commit",
            "action_id": commit_call_id,
            "action_name": spec.action_id,
            "covers": [spec.action_id],
            "commit_fingerprint": commit_fingerprint,
            "status": "issued",
            "repairability": spec.risk,
            "quality_before": copy.deepcopy(quality_now),
            "before_presentation": before_presentation,
        })
        repair_state["attempts"] = attempts
        repair_state["termination_reason"] = "selfheal_presentation_commit_issued"
        cartography["repair_attempts"] = copy.deepcopy(attempts)
        cartography["status"] = "not_evaluated"
        cartography["passed"] = False
        cartography["termination_reason"] = "selfheal_presentation_commit_issued"
        result["selfheal_commit_issued"] = {
            "commit_call_id": commit_call_id,
            "action_name": spec.action_id,
            "layers": [item["layer_id"] for item in commit_updates],
        }
        result["_pending_selfheal_commit"] = {
            "commit_call_id": commit_call_id,
            "action_name": spec.action_id,
            "iterations": commit_updates,
        }
        result["overall_passed"] = False
        await persist_repair_state(repair_state)
        return result

    # 4) 动作耗尽：诚实终止，保留全部尝试（可审计）。既有重复补丁语义
    #    保持（同代重复 patch 不得伪装新尝试）；注册表动作从未可用时保持
    #    既有 no_safe_runtime_repair 终止语义。
    plan = plan_runtime_repairs(mapspec, observation, cartography)
    if plan is not None and plan.get("patches"):
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
    registry_tried = any(
        attempt.get("kind") in ("rollback", "commit") for attempt in attempts
    ) or any(
        isinstance(past, dict)
        and past.get("kind") in ("rollback", "commit", "commit_revert")
        for past in (repair_state.get("history") or [])
    )
    if registry_tried:
        return await terminate("repair_exhausted", "selfheal_actions_exhausted")
    if plan is None:
        return await terminate("failed_unrepairable", "no_safe_runtime_repair")
    return await terminate("repair_exhausted", "runtime_repair_iteration_limit")


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

