"""MissionBridge 回归测试：session 解析口径 / 最近 mission 选择。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.lib.runtime.clock import to_db_utc
from app.models.mission import GISMissionRow
from app.services.mission_runtime.service import MissionRuntimeService
from app.services.mission_runtime.store import MissionStore
from app.services.spatial_events import contracts as C
from app.services.spatial_events.mission_bridge import MissionBridge


def _watch(**over) -> C.SpatialWatch:
    kw = dict(
        watch_id="w-mb", org_id="org-a", name="n",
        kinds=["dataset.version_changed"],
        actions=["mission_create"],
        mission_goal_template="审查 {subject_key}",
        cooldown_s=0,
    )
    kw.update(over)
    return C.SpatialWatch(**kw)


class _StepClock:
    """每次调用前进 1s——制造严格递增的 updated_at。"""

    def __init__(self) -> None:
        self._now = to_db_utc(datetime(2026, 1, 1, tzinfo=timezone.utc))

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


def _seed_missions(store: MissionStore, n: int = 150) -> None:
    for i in range(n):
        store.create_mission(
            org_id="org-a", user_id="u", project_id="proj-1",
            root_goal=f"g{i}", session_id="sess-1",
            mission_id=f"m-{i:03d}",
        )


def test_mission_create_uses_watch_session_when_event_has_none(
    mission_runtime,
):
    """回归：mission_create 必须与 _candidate_missions 同口径解析 session。"""
    bridge = MissionBridge(mission_runtime)
    watch = _watch(session_id="watch-sess")
    event = {
        "event_id": "evt-no-session",
        "org_id": "org-a",
        "kind": "artifact.revision_committed",
        "subject_key": "artifact:a1",
        "session_id": None,
    }
    created = bridge.mission_create(watch, event, digest="d1")
    assert created["outcome"] == "mission_created"
    rec = mission_runtime.store.get_mission(
        created["detail"]["mission_id"], org_id="org-a")
    assert rec is not None
    assert rec.refs.active_session_ids == ["watch-sess"]
    # 后续 revise 必须能匹配刚建的 mission（旧口径 active_session_ids=[]
    # 会让 revise 永久 no_active_mission）。
    revised = bridge.mission_revise(watch, event, digest="d2")
    assert revised["outcome"] == "mission_revised"


def test_mission_revise_targets_newest_beyond_old_window(db_factory):
    """回归：>100 未终态 mission 时不得把「第 100 旧」当最近更新。"""
    store = MissionStore(factory=db_factory, clock=_StepClock())
    runtime = MissionRuntimeService(store=store)
    _seed_missions(store, 150)
    bridge = MissionBridge(runtime)
    watch = _watch(
        watch_id="w-rev", actions=["mission_revise"],
        session_id="sess-1", mission_project_id="proj-1",
    )
    event = {
        "event_id": "evt-rev", "org_id": "org-a",
        "project_id": "proj-1", "session_id": "sess-1",
    }
    res = bridge.mission_revise(watch, event, digest="d")
    assert res["outcome"] == "mission_revised"
    assert res["detail"]["mission_id"] == "m-149"
    assert store.get_mission("m-149", org_id="org-a").goal_revision == 2


def test_mission_resume_targets_newest_beyond_old_window(db_factory):
    """回归：resume 与 revise 共用候选选择，同样必须取最近更新。"""
    store = MissionStore(factory=db_factory, clock=_StepClock())
    runtime = MissionRuntimeService(store=store)
    _seed_missions(store, 150)
    with db_factory() as db:
        db.query(GISMissionRow).update({"state": "suspended"})
        db.commit()
    bridge = MissionBridge(runtime)
    watch = _watch(
        watch_id="w-res", actions=["mission_resume"],
        session_id="sess-1", mission_project_id="proj-1",
    )
    event = {
        "event_id": "evt-res", "org_id": "org-a",
        "project_id": "proj-1", "session_id": "sess-1",
    }
    res = bridge.mission_resume(watch, event)
    assert res["outcome"] == "mission_resumed"
    assert res["detail"]["mission_id"] == "m-149"
    assert store.get_mission("m-149", org_id="org-a").state.value == "running"
