"""Project/Mission Portfolio read model（纯查询投影；零新状态真相）。

纪律（DECISIONS D9）：
- 只读聚合 ``gis_missions`` × ``workflow_instances`` × ``spatial_events`` ×
  ``spatial_watch_fires``——不物化任何 portfolio 表/第二状态。
- 全部 org 域过滤；响应有界（≤200 projects、missions ≤100/project、
  events ≤50/project）。
- 项目分组来自各表既有 ``project_id`` 字符串；无 project 归 ``_unfiled``。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select

from app.models.db_model import WorkflowInstanceRow
from app.models.mission import GISMissionRow
from app.models.spatial_events import SpatialEventRow, SpatialWatchFireRow

MAX_PROJECTS = 200
MAX_MISSIONS_PER_PROJECT = 100
MAX_INSTANCES_PER_PROJECT = 50
MAX_EVENTS_PER_PROJECT = 50

_UNFILED = "_unfiled"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def org_summary(org_id: str, *, factory=None) -> Dict[str, Any]:
    """org 级 portfolio 汇总（mission 状态分布 + 事件/触发计数）。"""
    if factory is None:
        from app.core.database import SessionLocal as factory
    with factory() as db:
        mission_states: Dict[str, int] = {}
        for state, cnt in db.execute(
            select(GISMissionRow.state, func.count(GISMissionRow.mission_id))
            .where(GISMissionRow.org_id == org_id)
            .group_by(GISMissionRow.state)
        ):
            mission_states[str(state)] = int(cnt)
        event_stats: Dict[str, int] = {}
        for status, cnt in db.execute(
            select(SpatialEventRow.status, func.count(SpatialEventRow.id))
            .where(SpatialEventRow.org_id == org_id)
            .group_by(SpatialEventRow.status)
        ):
            event_stats[str(status)] = int(cnt)
        fires_24h = int(
            db.execute(
                select(func.count(SpatialWatchFireRow.id)).where(
                    SpatialWatchFireRow.org_id == org_id,
                    SpatialWatchFireRow.fired_at
                    >= datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(hours=24),
                )
            ).scalar()
            or 0
        )
        running_instances = int(
            db.execute(
                select(func.count(WorkflowInstanceRow.instance_id)).where(
                    WorkflowInstanceRow.org_id == org_id,
                    WorkflowInstanceRow.status == "running",
                )
            ).scalar()
            or 0
        )
    return {
        "org_id": org_id,
        "missions_by_state": mission_states,
        "events_by_status": event_stats,
        "watch_fires_24h": fires_24h,
        "running_workflow_instances": running_instances,
    }


def project_portfolio(org_id: str, *, factory=None) -> List[Dict[str, Any]]:
    """project 级聚合行（按最近活动排序；有界 ≤200）。"""
    if factory is None:
        from app.core.database import SessionLocal as factory
    with factory() as db:
        m_rows = db.execute(
            select(
                GISMissionRow.project_id,
                func.count(GISMissionRow.mission_id),
                func.max(GISMissionRow.updated_at),
            )
            .where(GISMissionRow.org_id == org_id)
            .group_by(GISMissionRow.project_id)
        ).all()
        i_rows = db.execute(
            select(
                WorkflowInstanceRow.project_id,
                func.count(WorkflowInstanceRow.instance_id),
                func.max(WorkflowInstanceRow.updated_at),
            )
            .where(WorkflowInstanceRow.org_id == org_id)
            .group_by(WorkflowInstanceRow.project_id)
        ).all()
        e_rows = db.execute(
            select(
                SpatialEventRow.project_id,
                func.count(SpatialEventRow.id),
                func.max(SpatialEventRow.ingested_at),
            )
            .where(SpatialEventRow.org_id == org_id)
            .group_by(SpatialEventRow.project_id)
        ).all()

    buckets: Dict[str, Dict[str, Any]] = {}

    def _bucket(pid: Optional[str]) -> Dict[str, Any]:
        key = str(pid) if pid else _UNFILED
        if key not in buckets:
            buckets[key] = {
                "project_id": key,
                "mission_count": 0,
                "instance_count": 0,
                "event_count": 0,
                "last_activity_at": None,
            }
        return buckets[key]

    def _touch(b: Dict[str, Any], dt: Optional[datetime]) -> None:
        if dt is None:
            return
        cur = b.get("last_activity_at")
        if cur is None or dt > cur:
            b["last_activity_at"] = dt

    for pid, cnt, last in m_rows:
        b = _bucket(pid)
        b["mission_count"] += int(cnt)
        _touch(b, last)
    for pid, cnt, last in i_rows:
        b = _bucket(pid)
        b["instance_count"] += int(cnt)
        _touch(b, last)
    for pid, cnt, last in e_rows:
        b = _bucket(pid)
        b["event_count"] += int(cnt)
        _touch(b, last)

    ordered = sorted(
        buckets.values(),
        key=lambda b: (b["last_activity_at"] is None, b["last_activity_at"]),
        reverse=False,
    )
    # 有 None 的排最后：先按时间降序取非 None，再补 None
    with_ts = [b for b in ordered if b["last_activity_at"] is not None]
    without = [b for b in ordered if b["last_activity_at"] is None]
    with_ts.sort(key=lambda b: b["last_activity_at"], reverse=True)
    result = (with_ts + without)[:MAX_PROJECTS]
    for b in result:
        b["last_activity_at"] = _iso(b["last_activity_at"])
    return result


def project_detail(
    org_id: str, project_id: str, *, factory=None
) -> Optional[Dict[str, Any]]:
    """单 project 详情（missions/instances/events/fires 有界清单）。

    未知 project（四源全空）返回 None（404-not-403 租户语义）。
    """
    if factory is None:
        from app.core.database import SessionLocal as factory
    pid = None if project_id == _UNFILED else project_id
    with factory() as db:
        m_q = (
            select(GISMissionRow)
            .where(GISMissionRow.org_id == org_id)
            .order_by(GISMissionRow.updated_at.desc())
            .limit(MAX_MISSIONS_PER_PROJECT)
        )
        m_q = (
            m_q.where(GISMissionRow.project_id == pid)
            if pid is not None
            else m_q.where(GISMissionRow.project_id.is_(None))
        )
        missions = [
            {
                "mission_id": r.mission_id,
                "state": r.state,
                "goal_revision": r.goal_revision,
                "root_goal": (r.root_goal or "")[:160],
                "updated_at": _iso(r.updated_at),
            }
            for r in db.execute(m_q).scalars()
        ]
        i_q = (
            select(WorkflowInstanceRow)
            .where(
                WorkflowInstanceRow.org_id == org_id,
                WorkflowInstanceRow.project_id == pid,
            )
            .order_by(WorkflowInstanceRow.updated_at.desc())
            .limit(MAX_INSTANCES_PER_PROJECT)
        )
        instances = [
            {
                "instance_id": r.instance_id,
                "status": r.status,
                "package_id": r.package_id,
                "updated_at": _iso(r.updated_at),
            }
            for r in db.execute(i_q).scalars()
        ]
        e_q = (
            select(SpatialEventRow)
            .where(SpatialEventRow.org_id == org_id)
            .order_by(SpatialEventRow.id.desc())
            .limit(MAX_EVENTS_PER_PROJECT)
        )
        e_q = (
            e_q.where(SpatialEventRow.project_id == pid)
            if pid is not None
            else e_q.where(SpatialEventRow.project_id.is_(None))
        )
        events = [
            {
                "event_id": r.event_id,
                "kind": r.kind,
                "subject_key": r.subject_key,
                "status": r.status,
                "occurred_at": _iso(r.occurred_at),
            }
            for r in db.execute(e_q).scalars()
        ]
        f_q = (
            select(SpatialWatchFireRow)
            .where(SpatialWatchFireRow.org_id == org_id)
            .order_by(SpatialWatchFireRow.id.desc())
            .limit(MAX_EVENTS_PER_PROJECT)
        )
        fires = [
            {
                "watch_id": r.watch_id,
                "event_id": r.event_id,
                "action": r.action,
                "outcome": r.outcome,
                "fired_at": _iso(r.fired_at),
            }
            for r in db.execute(f_q).scalars()
        ]
    if not (missions or instances or events):
        return None
    return {
        "project_id": project_id,
        "missions": missions,
        "workflow_instances": instances,
        "recent_events": events,
        "recent_watch_fires": fires[:MAX_EVENTS_PER_PROJECT],
        "counts": {
            "missions": len(missions),
            "workflow_instances": len(instances),
            "recent_events": len(events),
        },
    }
