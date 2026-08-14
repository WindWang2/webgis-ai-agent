"""API integration for the new provenance endpoints (spec §32):
revisions list, run detail, replay, resume, compare (new shape).

The tool registry is monkeypatched so we don't depend on the real tool catalog.
"""
import pytest
from fastapi.testclient import TestClient

from app.core.database import Base, Engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=Engine)
    yield


class _FakeRegistry:
    def tool_version(self, name):
        return "1.0#cv1"

    async def dispatch(self, name, args, session_id=None):
        from app.services.session_data import session_data_manager
        payload = {"produced_by": name, "feature_count": 2}
        ref = f"ref:{name}"
        if session_id:
            ref = await session_data_manager.store(session_id, payload, prefix=name)
        return {"success": True, "ref_id": ref, "feature_count": 2}


@pytest.fixture
def fake_registry(monkeypatch):
    reg = _FakeRegistry()
    import app.api.routes.project as route_mod
    monkeypatch.setattr(route_mod, "get_tool_registry", lambda: reg)
    return reg


def _unique(prefix):
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _auth_headers():
    """run/replay/resume require authentication (SEC-F1: executing registered
    tools synchronously is not an anonymous surface)."""
    from app.core.auth import create_access_token
    return {"Authorization": "Bearer " + create_access_token(
        {"sub": "wf-runner", "username": "wf-runner", "role": "viewer"})}


def _make_project(name):
    res = client.post("/api/v1/projects", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _make_workflow(proj_id, wf_name, steps):
    res = client.post(f"/api/v1/projects/{proj_id}/workflows", json={
        "name": wf_name, "graph_spec": {"steps": steps},
    })
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_revisions_list_and_detail(fake_registry):
    proj_id = _make_project(_unique("proj"))
    wf_id = _make_workflow(proj_id, _unique("wf"), [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []}])

    # revision 1 is published at save time.
    rev = client.get(f"/api/v1/projects/{proj_id}/workflows/{wf_id}/revisions")
    assert rev.status_code == 200
    items = rev.json()["items"]
    assert len(items) == 1
    rev_id = items[0]["id"]

    detail = client.get(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/revisions/{rev_id}")
    assert detail.status_code == 200
    assert detail.json()["revision_no"] == 1


def test_run_detail_replay_compare(fake_registry):
    proj_id = _make_project(_unique("proj"))
    wf_id = _make_workflow(proj_id, _unique("wf"), [
        {"step_id": "s1", "tool_name": "t_a", "dependencies": []}])

    run_res = client.post(f"/api/v1/projects/{proj_id}/workflows/{wf_id}/run",
                          json={"input_bindings": {"aoi": "Haidian"}, "start_from_step": None},
                          headers=_auth_headers())
    assert run_res.status_code == 200, run_res.text
    run = run_res.json()
    run_id = run["id"]
    assert run["status"] == "completed"
    assert run["run_fingerprint"]
    assert run["run_manifest"]["graph_fingerprint"]

    # Run detail endpoint.
    detail = client.get(f"/api/v1/projects/{proj_id}/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run_fingerprint"] == run["run_fingerprint"]

    # Replay (exact) via API.
    replay = client.post(f"/api/v1/projects/{proj_id}/runs/{run_id}/replay",
                         json={"mode": "exact"}, headers=_auth_headers())
    assert replay.status_code == 200, replay.text
    replay_run = replay.json()
    assert replay_run["run_fingerprint"] == run["run_fingerprint"]

    # Compare surfaces the new shape.
    cmp = client.post(
        f"/api/v1/projects/{proj_id}/runs/compare",
        params={"run_a_id": run_id, "run_b_id": replay_run["id"]})
    assert cmp.status_code == 200, cmp.text
    body = cmp.json()
    assert body["run_a_id"] == run_id
    assert "revision" in body and "run_fingerprint" in body
    assert body["run_fingerprint"]["same"] is True


def test_resume_endpoint(fake_registry):
    proj_id = _make_project(_unique("proj"))

    # A workflow where s2 fails on first run.
    class _FailOnce(_FakeRegistry):
        def __init__(self):
            super().__init__()
            self._first = True

        async def dispatch(self, name, args, session_id=None):
            if name == "t_b" and self._first:
                self._first = False
                raise RuntimeError("boom")
            return await super().dispatch(name, args, session_id)

    import app.api.routes.project as route_mod
    fail_reg = _FailOnce()
    orig = route_mod.get_tool_registry
    route_mod.get_tool_registry = lambda: fail_reg
    try:
        wf_id = _make_workflow(proj_id, _unique("wf"), [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []},
            {"step_id": "s2", "tool_name": "t_b", "dependencies": ["s1"]},
        ])
        run1 = client.post(f"/api/v1/projects/{proj_id}/workflows/{wf_id}/run",
                           json={"input_bindings": {}, "start_from_step": None},
                           headers=_auth_headers()).json()
        assert run1["status"] == "failed"
        assert run1["completed_steps"] == ["s1"]

        # Switch to a succeeding registry and resume.
        ok_reg = _FakeRegistry()
        route_mod.get_tool_registry = lambda: ok_reg
        resumed = client.post(f"/api/v1/projects/{proj_id}/runs/{run1['id']}/resume",
                              json={"allow_rerun": False}, headers=_auth_headers())
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "completed"
        assert resumed.json()["completed_steps"] == ["s1", "s2"]
    finally:
        route_mod.get_tool_registry = orig


def test_resume_rejects_with_409_when_unresumable(fake_registry):
    proj_id = _make_project(_unique("proj"))

    class _Fail(_FakeRegistry):
        async def dispatch(self, name, args, session_id=None):
            raise RuntimeError("always fails")

    import app.api.routes.project as route_mod
    fail_reg = _Fail()
    orig = route_mod.get_tool_registry
    route_mod.get_tool_registry = lambda: fail_reg
    try:
        wf_id = _make_workflow(proj_id, _unique("wf"), [
            {"step_id": "s1", "tool_name": "t_a", "dependencies": []}])
        run1 = client.post(f"/api/v1/projects/{proj_id}/workflows/{wf_id}/run",
                           json={"input_bindings": {}, "start_from_step": None},
                           headers=_auth_headers()).json()
        assert run1["status"] == "failed"
        # No completed steps → resume is unresumable → 409.
        resumed = client.post(f"/api/v1/projects/{proj_id}/runs/{run1['id']}/resume",
                              json={"allow_rerun": False}, headers=_auth_headers())
        assert resumed.status_code == 409
    finally:
        route_mod.get_tool_registry = orig
