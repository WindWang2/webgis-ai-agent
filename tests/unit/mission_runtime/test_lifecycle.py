"""Mission lifecycle: create → start → suspend → resume → cancel/complete."""
from __future__ import annotations

from app.services.mission_runtime.contracts import MissionState


def test_create_start_complete(runtime):
    m = runtime.create(org_id="1", user_id="u1", root_goal="分析成都市小学分布")
    assert m.state == MissionState.CREATED
    m = runtime.start(m.mission_id, worker_id="w1")
    assert m.state == MissionState.RUNNING
    m = runtime.complete(m.mission_id, worker_id="w1")
    assert m.state == MissionState.COMPLETE
    diag = runtime.diagnostics(m.mission_id)
    assert diag.last_checkpoint


def test_suspend_resume_cancel(runtime):
    m = runtime.create(org_id="1", user_id="u1", root_goal="goal")
    runtime.start(m.mission_id, worker_id="w1")
    m = runtime.suspend(m.mission_id, worker_id="w1")
    assert m.state == MissionState.SUSPENDED
    result = runtime.resume(m.mission_id, worker_id="w2")
    assert result["ok"] is True
    m = runtime.cancel(m.mission_id, worker_id="w2")
    assert m.state == MissionState.CANCELLED


def test_org_isolation(runtime):
    m = runtime.create(org_id="org-a", user_id="u1", root_goal="a")
    assert runtime.store.get_mission(m.mission_id, org_id="org-b") is None
    assert runtime.store.get_mission(m.mission_id, org_id="org-a") is not None


def test_lifecycle_helpers_reject_vanished_mission(runtime, monkeypatch):
    """A mission deleted after lease acquire must raise typed FencingError."""
    import pytest

    from app.services.mission_runtime.store import FencingError

    m = runtime.create(org_id="1", user_id="u1", root_goal="goal")
    runtime.start(m.mission_id, worker_id="w1")
    monkeypatch.setattr(runtime.store, "get_mission", lambda *a, **k: None)
    with pytest.raises(FencingError):
        runtime.start(m.mission_id, worker_id="w1")
    with pytest.raises(FencingError):
        runtime.revise_goal(m.mission_id, worker_id="w1", new_goal="new")
    with pytest.raises(FencingError):
        runtime.attach_session(
            m.mission_id, worker_id="w1", session_id="s1", org_id="1")
