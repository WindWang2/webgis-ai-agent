"""方法实验共享服务层（WP5）—— Pi 工具面与普通 UI REST 路由的**同链**实现。

单一服务语义（UI 与 Agent 不分叉）：

- 状态读写：会话 store 单键别名 + 有界载荷（tools/routes 共用）；
- 生产 dispatch：共享 ToolDispatchService（与 Pi 直调同一缝；不可用 =
  诚实 decline，绝不回落旁路）；
- run 窗口：实验级分布式锁内 reload→执行→持久（与 AWF run 同锁纪律）；
- 计划编译 / 评价 / 采纳回执直接复用 plan/runner/evaluate 纯函数。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import JsonValue

from app.services.gis_harness.method_experiment.plan import (
    MethodExperimentPlan,
)
from app.services.gis_harness.method_experiment.runner import (
    DEFAULT_MAX_TRIALS_PER_CALL,
    DEFAULT_WALL_GUARD_SECONDS,
    ExperimentRunner,
)
from app.services.gis_harness.method_experiment.state import (
    ExperimentRunState,
    new_state_from_plan,
    state_alias,
    state_from_payload,
    state_to_payload,
)
from app.services.session_data import session_data_manager

logger = logging.getLogger(__name__)


async def load_state(session_id: str, experiment_id: str) -> Optional[ExperimentRunState]:
    """读实验运行态（store 别名；Redis 降级按无状态处理）。"""
    alias = state_alias(experiment_id)
    try:
        ref = await session_data_manager.resolve_alias(session_id, alias)
    except Exception:  # noqa: BLE001 — Redis 降级按无状态处理
        ref = alias
    payload = None
    try:
        payload = await session_data_manager.get(session_id, ref)
    except Exception:  # noqa: BLE001
        payload = None
    state = state_from_payload(payload if isinstance(payload, dict) else {})
    return state if state.experiment_id else None


async def persist_state(session_id: str, state: ExperimentRunState) -> None:
    ref = await session_data_manager.store(
        session_id, state_to_payload(state), prefix="mexp-state")
    await session_data_manager.set_alias(
        session_id, ref, state_alias(state.experiment_id))


def production_dispatch(
) -> Optional[Callable[[str, Dict[str, JsonValue], str], Any]]:
    """试验分发 = 共享 ToolDispatchService（内层与 Pi 直调同一生产缝）。

    返回 None = 服务不可用（调用方诚实 decline，不回落旁路）。
    """
    import uuid as _uuid
    from app.agent_pi_bridge import (
        _get_shared_dispatch_service,
        try_get_tool_registry,
    )
    if try_get_tool_registry() is None:
        return None

    async def _dispatch(tool_name: str, args: Dict[str, JsonValue],
                        session_id: str):
        service = _get_shared_dispatch_service()
        tc = {"id": f"mexp-{_uuid.uuid4().hex[:8]}",
              "function": {"name": tool_name, "arguments": args}}
        return await service.dispatch(tc, session_id, set())

    return _dispatch


def plan_snapshot(plan: MethodExperimentPlan) -> Dict[str, Any]:
    return plan.to_bounded_summary()


async def ensure_state_for_plan(
    session_id: str, plan: MethodExperimentPlan,
) -> Tuple[ExperimentRunState, str]:
    """为计划创建（或读取既有）运行态。

    返回 (state, decline_code)。decline_code 非空 = 同 id 不同内容
    （指纹失配，调用方拒绝覆盖）。
    """
    summary_fingerprint = plan.fingerprint()[:16]
    # 创建/对账进同一锁族（review C-P2：锁外 load→create→persist 的
    # TOCTOU——并发同 id plan 可双写；与 run/adopt 同单写者纪律）。
    from app.services.distributed_lock import session_lock_registry

    async with session_lock_registry.lock(
            f"mexp:{plan.experiment_id}", fail_on_degraded=True):
        existing = await load_state(session_id, plan.experiment_id)
        if existing is None:
            state = new_state_from_plan(plan)
            state.log("plan_compiled", trials=len(plan.trials))
            await persist_state(session_id, state)
            return state, ""
        if existing.plan_fingerprint != summary_fingerprint:
            return existing, "MEXP_ID_COLLISION"
        return existing, ""


async def run_window(
    session_id: str,
    experiment_id: str,
    *,
    max_trials_per_call: int = DEFAULT_MAX_TRIALS_PER_CALL,
    wall_guard_seconds: float = DEFAULT_WALL_GUARD_SECONDS,
    only_candidates: Optional[List[str]] = None,
) -> Tuple[Optional[ExperimentRunState], str]:
    """锁内执行一个 run 窗口（与 AWF run 同锁纪律：锁内 reload）。

    返回 (state, decline_code)；state=None = typed 拒绝（不存在/快照损坏/
    分发服务不可用），decline_code 说明原因。
    """
    from app.services.distributed_lock import session_lock_registry

    async with session_lock_registry.lock(
            f"mexp:{experiment_id}", fail_on_degraded=True):
        state = await load_state(session_id, experiment_id)
        if state is None:
            return None, "MEXP_EXPERIMENT_NOT_FOUND"
        snapshot = state.plan_snapshot or {}
        try:
            plan = MethodExperimentPlan.model_validate(snapshot)
        except Exception:  # noqa: BLE001 — 快照损坏 = 诚实失败
            return None, "MEXP_PLAN_SNAPSHOT_CORRUPT"
        if plan.experiment_id != experiment_id:
            return None, "MEXP_PLAN_MISMATCH"
        dispatch = production_dispatch()
        if dispatch is None:
            return None, "MEXP_REGISTRY_UNAVAILABLE"
        if state.running_stale():
            state.log("resume_after_stale")
        runner = ExperimentRunner(
            dispatch=dispatch,
            persist=lambda st: persist_state(session_id, st),
            max_features=int(plan.budget.max_features),
        )
        from app.lib.cancellation import is_cancelled
        state = await runner.run(
            session_id, plan, state,
            only_candidates=only_candidates,
            max_trials_per_call=max(1, min(int(max_trials_per_call),
                                           DEFAULT_MAX_TRIALS_PER_CALL)),
            wall_guard_seconds=wall_guard_seconds,
            cancel_check=is_cancelled,
        )
        return state, ""


async def record_adoption(
    session_id: str, experiment_id: str, trial_id: str, reason: str,
) -> Tuple[Optional[Any], str]:
    """锁内记录采纳回执（build_adoption_receipt 的持久化包装）。"""
    from app.services.distributed_lock import session_lock_registry
    from app.services.gis_harness.method_experiment.evaluate import (
        build_adoption_receipt,
    )
    from app.services.gis_harness.method_experiment.plan import (
        MethodExperimentPlan,
    )

    async with session_lock_registry.lock(
            f"mexp:{experiment_id}", fail_on_degraded=True):
        state = await load_state(session_id, experiment_id)
        if state is None:
            return None, "MEXP_EXPERIMENT_NOT_FOUND"
        try:
            plan = MethodExperimentPlan.model_validate(state.plan_snapshot)
        except Exception:  # noqa: BLE001
            return None, "MEXP_PLAN_SNAPSHOT_CORRUPT"
        receipt, decline = build_adoption_receipt(
            plan, state, trial_id=trial_id, reason=reason)
        if receipt is None:
            return None, decline
        state.adoption = receipt
        state.log("adoption_recorded", trial_id=trial_id,
                  candidate_id=receipt.candidate_id)
        await persist_state(session_id, state)
        return receipt, ""


def rebuild_plan(state: ExperimentRunState) -> Tuple[Optional[MethodExperimentPlan], str]:
    """从状态快照重建计划（compare 等只读路径；损坏 = typed 拒绝）。"""
    try:
        plan = MethodExperimentPlan.model_validate(state.plan_snapshot)
    except Exception:  # noqa: BLE001
        return None, "MEXP_PLAN_SNAPSHOT_CORRUPT"
    if plan.experiment_id != state.experiment_id:
        return None, "MEXP_PLAN_MISMATCH"
    return plan, ""
