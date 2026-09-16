"""event → Mission bridge：投影事实 → Mission create/revise/resume。

红线与幂等（DECISIONS D3/D7）：
- 只在 ``GIS_SPATIAL_EVENT_MISSION_BRIDGE`` 开启时执行；watch fire 记录
  (watch_id, event_id) UQ 是触发幂等边界。
- mission_create 用**确定性 mission_id**（sha256(watch_id|event_id) 截断）
  ——重放/重投递时 get_mission 命中即幂等跳过，绝不双建。
- 全部走 ``MissionRuntimeService`` 公开 API（复用 lease_epoch/revision
  CAS/fencing；本模块不自建任何 Mission 机制）。
- 租户红线：org_id 断言 event.org == watch.org == mission.org。
- root_goal 由模板**替换生成**（非 str.format——防花括号注入），携带
  投影事实 digest（可审计：goal 与触发事实可对账）。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, Optional

from app.services.spatial_events.contracts import SpatialWatch

logger = logging.getLogger(__name__)

_TOKENS = ("{subject_key}", "{kind}", "{event_id}", "{facts_digest}")


class MissionBridgeError(RuntimeError):
    pass


def render_goal(template: str, *, event: Dict[str, Any], digest: str) -> str:
    """有界模板替换（白名单 token；多余花括号原样保留）。"""
    goal = str(template or "")
    goal = goal.replace("{subject_key}", str(event.get("subject_key", ""))[:128])
    goal = goal.replace("{kind}", str(event.get("kind", ""))[:64])
    goal = goal.replace("{event_id}", str(event.get("event_id", ""))[:64])
    goal = goal.replace("{facts_digest}", digest)
    return goal[:512]


def derived_mission_id(watch_id: str, event_id: str) -> str:
    basis = hashlib.sha256(
        f"{watch_id}|{event_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"evt-{basis}"


class MissionBridge:
    """触发动作执行器（同步；由 worker 在线程内调用）。"""

    def __init__(self, runtime: Any) -> None:
        # runtime: MissionRuntimeService（ duck-typed，便于 hermetic 测试）
        self.runtime = runtime

    # ── 目标 mission 解析 ────────────────────────────────────────────

    def _candidate_missions(
        self, watch: SpatialWatch, event: Dict[str, Any]
    ) -> list:
        org = watch.org_id
        unfinished = self.runtime.store.list_unfinished(org_id=org, limit=100)
        scope_project = watch.mission_project_id or event.get("project_id")
        out = []
        for m in unfinished:
            if scope_project and m.project_id and m.project_id != scope_project:
                continue
            if scope_project and not m.project_id:
                continue
            out.append(m)
        return out

    # ── 动作 ─────────────────────────────────────────────────────────

    def mission_create(
        self, watch: SpatialWatch, event: Dict[str, Any], *, digest: str
    ) -> Dict[str, Any]:
        if watch.org_id != event.get("org_id"):
            return {"outcome": "rejected", "detail": {"reason": "org_mismatch"}}
        goal = render_goal(
            watch.mission_goal_template or "", event=event, digest=digest
        )
        mission_id = derived_mission_id(watch.watch_id, event["event_id"])
        existing = self.runtime.store.get_mission(
            mission_id, org_id=watch.org_id
        )
        if existing is not None:
            return {
                "outcome": "duplicate",
                "detail": {"mission_id": mission_id},
            }
        # 确定性 mission_id 落 store 公开 API（幂等键在 UQ 冲突层再兜底：
        # 并发同事件创建 → 主键冲突按重复处理）
        try:
            rec = self.runtime.store.create_mission(
                org_id=watch.org_id,
                user_id="spatial-events",
                project_id=watch.mission_project_id or event.get("project_id"),
                root_goal=goal,
                session_id=str(event.get("session_id") or ""),
                mission_id=mission_id,
            )
        except Exception as e:  # noqa: BLE001 — 主键冲突 = 对方已创建
            if "UNIQUE" in str(e).upper() or "PRIMARY" in str(e).upper():
                return {
                    "outcome": "duplicate",
                    "detail": {"mission_id": mission_id},
                }
            raise
        return {
            "outcome": "mission_created",
            "detail": {"mission_id": rec.mission_id, "goal": goal[:200]},
        }

    def mission_revise(
        self, watch: SpatialWatch, event: Dict[str, Any], *, digest: str
    ) -> Dict[str, Any]:
        if watch.org_id != event.get("org_id"):
            return {"outcome": "rejected", "detail": {"reason": "org_mismatch"}}
        goal = render_goal(
            watch.mission_goal_template or "", event=event, digest=digest
        )
        cands = self._candidate_missions(watch, event)
        if not cands:
            return {
                "outcome": "rejected",
                "detail": {"reason": "no_active_mission"},
            }
        target = cands[-1]  # 最近更新的
        try:
            rec = self.runtime.revise_goal(
                target.mission_id,
                worker_id="spatial-event-bridge",
                new_goal=goal,
                org_id=watch.org_id,
            )
        except Exception as e:  # noqa: BLE001 — FencingError 等 → 可重试
            return {
                "outcome": "deferred",
                "detail": {"mission_id": target.mission_id, "error": str(e)[:120]},
            }
        return {
            "outcome": "mission_revised",
            "detail": {
                "mission_id": rec.mission_id,
                "goal_revision": rec.goal_revision,
            },
        }

    def mission_resume(
        self, watch: SpatialWatch, event: Dict[str, Any]
    ) -> Dict[str, Any]:
        if watch.org_id != event.get("org_id"):
            return {"outcome": "rejected", "detail": {"reason": "org_mismatch"}}
        cands = [
            m for m in self._candidate_missions(watch, event)
            if m.state.value in ("suspended", "recovering")
        ]
        if not cands:
            return {
                "outcome": "rejected",
                "detail": {"reason": "no_suspended_mission"},
            }
        target = cands[-1]
        result = self.runtime.resume(
            target.mission_id,
            worker_id="spatial-event-bridge",
            org_id=watch.org_id,
        )
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        return {
            "outcome": "mission_resumed" if ok else "deferred",
            "detail": {"mission_id": target.mission_id},
        }

    def execute(
        self,
        action: str,
        watch: SpatialWatch,
        event: Dict[str, Any],
        *,
        digest: str = "",
    ) -> Dict[str, Any]:
        """动作分派（worker 调用；结果进 fire 记录 detail）。"""
        try:
            if action == "mission_create":
                return self.mission_create(watch, event, digest=digest)
            if action == "mission_revise":
                return self.mission_revise(watch, event, digest=digest)
            if action == "mission_resume":
                return self.mission_resume(watch, event)
            return {"outcome": "rejected", "detail": {"reason": "unknown_action"}}
        except Exception as e:  # noqa: BLE001 — 未预期异常 → 可重试
            logger.warning(
                "[spatial_events] mission bridge %s failed: %s", action, e
            )
            return {
                "outcome": "deferred",
                "detail": {"error": str(e)[:120]},
            }
