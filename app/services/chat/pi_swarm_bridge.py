"""Protected primary Swarm writer gateway, re-exported by agent_pi_bridge."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_SWARM_ORCHESTRATOR_ENV = "GIS_SWARM_ORCHESTRATOR"
_SWARM_OFF_VALUES = {"0", "false", "no", "off"}


class SwarmBridge:
    """Master(Pi) → Specialist 集群的受保护委派网关（ADR-0187 §D7）。

    守卫序（任一不满足 → ``{"delegated": False, "reason": ...}`` 诚实拒绝）：
    1. 特性开关（``GIS_SWARM_ORCHESTRATOR``，默认关）；
    2. goal 有界（空 / 超 2000 字符拒绝）；
    3. 主 turn 互斥（该 session 存在在飞 Pi turn 时拒绝，防并发写会话态）。

    通过守卫后：惰性构建 ``WorldStateProjection`` 切片（fail-open 最小面）→
    ``SwarmOrchestrator.run_swarm`` → 返回有界摘要（仅 run_id/终态/计数/
    ref 提货券清单，零 payload）。agent_swarm 全部惰性 import —— 开关关闭
    时不加载任何集群模块。
    """

    def __init__(
        self,
        session_id: str,
        *,
        orchestrator_factory: Optional[Callable[[str], Any]] = None,
        projection_builder: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._session_id = session_id
        self._orchestrator_factory = orchestrator_factory
        self._projection_builder = projection_builder

    @classmethod
    def enabled(cls) -> bool:
        return (
            os.getenv(_SWARM_ORCHESTRATOR_ENV, "0").strip().lower()
            not in _SWARM_OFF_VALUES
        )

    async def delegate_compound_task(
        self, root_goal: str, *, cartography_context: Optional[dict] = None,
        mission_id: str = "", org_id: str = "",
    ) -> dict:
        if not self.enabled():
            return {"delegated": False, "reason": "swarm_disabled"}
        if mission_id and not str(org_id).strip():
            return {"delegated": False, "reason": "org_required"}
        from app.services.chat.pi_writer_gate import acquire_writer
        from app.services.distributed_lock import LockError
        try:
            lease = await acquire_writer(self._session_id, timeout_s=0.05)
        except LockError:
            return {"delegated": False, "reason": "session_writer_busy"}
        try:
            return await self._delegate_locked(
                root_goal, cartography_context=cartography_context,
                mission_id=mission_id, org_id=org_id,
            )
        finally:
            lease.release()

    async def _delegate_locked(
        self,
        root_goal: str,
        *,
        cartography_context: Optional[dict] = None,
        mission_id: str = "",
        org_id: str = "",
    ) -> dict:
        if not self.enabled():
            return {"delegated": False, "reason": "swarm_disabled"}
        goal = (root_goal or "").strip()
        if not goal or len(goal) > 2000:
            return {"delegated": False, "reason": "invalid_goal"}
        from app.agent_pi_bridge import get_active_turn_entry

        if get_active_turn_entry(self._session_id) is not None:
            return {"delegated": False, "reason": "master_turn_active"}
        from app.services.agent_swarm.delegation_contracts import (
            MAX_GOAL_CHARS,
            WorldStateProjection,
        )
        from app.services.agent_swarm.dispatcher import SpecialistDispatcher
        from app.services.agent_swarm.orchestrator import SwarmOrchestrator

        if self._projection_builder is not None:
            projection = self._projection_builder(goal, cartography_context)
        else:
            projection = WorldStateProjection(
                session_id=self._session_id, goal_summary=goal[:MAX_GOAL_CHARS]
            )
        if self._orchestrator_factory is not None:
            orchestrator = self._orchestrator_factory(self._session_id)
        else:
            orchestrator = SwarmOrchestrator(
                self._session_id, dispatcher=SpecialistDispatcher()
            )
        try:
            status = await orchestrator.run_swarm(goal, projection=projection)
        except Exception:  # noqa: BLE001 — details stay in server logs
            logger.exception(
                "[SwarmBridge] swarm run failed session=%s", self._session_id
            )
            return {"delegated": False, "reason": "swarm_error"}
        manifest = status.manifest
        refs = [entry.ref_id for entry in manifest.entries] if manifest else []
        out = {
            "delegated": True,
            "run_id": status.run_id,
            "state": status.state,
            "counts": dict(status.counts),
            "refs": refs[:24],
            "manifest_ref": status.manifest_ref,
        }
        # D04: optional Mission create/reuse when GIS_MISSION_HOTPATH=1 (default off).
        try:
            from app.services.gis_harness.hotpath_convergence import (
                maybe_bind_mission_for_turn,
                set_mission_id,
            )
            _bind = maybe_bind_mission_for_turn(
                session_id=self._session_id,
                org_id=str(org_id or ""),
                root_goal=goal,
                mission_id=mission_id,
            )
            if _bind.mission_id:
                mission_id = _bind.mission_id
                set_mission_id(self._session_id, mission_id)
                out["mission_bind"] = _bind.to_bounded_dict()
        except Exception:  # noqa: BLE001 — bounded disclosure, server-only details
            logger.warning("Swarm mission bind unavailable", exc_info=True)
            out["mission_bind_status"] = "unavailable"
        # ADR-0197 D4: optional durable swarm mirror under a Mission (fail-open).
        if mission_id:
            try:
                from app.services.mission_runtime.service import mission_runtime_enabled
                from app.services.mission_runtime.swarm_bridge import DurableSwarmBridge
                if mission_runtime_enabled():
                    bridge = DurableSwarmBridge()
                    descriptors = []
                    task_map = getattr(status, "tasks", None) or {}
                    if isinstance(task_map, dict) and task_map:
                        for tid, tstate in task_map.items():
                            if isinstance(tstate, dict):
                                side = tstate.get("side_effect", "pure")
                            else:
                                side = getattr(tstate, "side_effect", "pure")
                            descriptors.append({
                                "task_id": str(tid),
                                "side_effect": side,
                            })
                    else:
                        descriptors = [{"task_id": f"swarm:{status.run_id}", "side_effect": "pure"}]
                    run = bridge.begin_run(
                        mission_id,
                        org_id=org_id,
                        goal_slice=goal[:2000],
                        task_descriptors=descriptors,
                    )
                    # Aggregate fallback only when fine-grained map unavailable.
                    # partial/degraded must settle as failed — never SKIPPED→COMPLETED (#1323).
                    agg_status = "succeeded"
                    state_val = getattr(status.state, "value", status.state)
                    state_l = str(state_val).lower()
                    if state_l in ("failed", "failure", "cancelled", "canceled"):
                        agg_status = state_l
                    elif state_l in ("partial", "degraded"):
                        agg_status = "failed"
                    for d in descriptors:
                        tid = d["task_id"]
                        tstate = task_map.get(tid) if isinstance(task_map, dict) else None
                        if isinstance(tstate, dict):
                            task_status = str(tstate.get("status") or agg_status)
                            produced = list(tstate.get("produced_refs") or [])[:12]
                            assignment_id = str(tstate.get("assignment_id") or status.run_id)
                            error_code = str(tstate.get("error_code") or "")
                            summary = str(tstate.get("summary") or "")
                            attempt = int(tstate.get("attempt") or 0)
                            side = tstate.get("side_effect", d.get("side_effect", "pure"))
                        elif tstate is not None:
                            task_status = str(
                                getattr(getattr(tstate, "status", None), "value", None)
                                or getattr(tstate, "status", None)
                                or agg_status
                            )
                            produced = list(getattr(tstate, "produced_refs", None) or [])[:12]
                            assignment_id = str(getattr(tstate, "assignment_id", "") or status.run_id)
                            error_code = str(getattr(tstate, "error_code", "") or "")
                            summary = str(getattr(tstate, "summary", "") or "")
                            attempt = int(getattr(tstate, "attempt", 0) or 0)
                            side = getattr(tstate, "side_effect", d.get("side_effect", "pure"))
                        else:
                            task_status = agg_status
                            produced = []
                            assignment_id = str(status.run_id)
                            error_code = ""
                            summary = ""
                            attempt = 0
                            side = d.get("side_effect", "pure")
                        bridge.settle(
                            run.swarm_run_id,
                            task_id=tid,
                            status=task_status,
                            produced_refs=produced,
                            assignment_id=assignment_id,
                            side_effect=side,
                            error_code=error_code,
                            summary=summary,
                            attempt=attempt,
                        )
                    out["mission_swarm_run_id"] = run.swarm_run_id
            except Exception:  # noqa: BLE001 — durability mirror must not break swarm path
                out["mission_mirror_status"] = "unavailable"
                logger.warning(
                    "[SwarmBridge] mission durable mirror failed session=%s mission=%s",
                    self._session_id, mission_id, exc_info=True,
                )
        return out
