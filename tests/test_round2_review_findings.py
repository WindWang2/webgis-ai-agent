"""Round-2 second-pass review regression tests.

Each test is keyed to a finding ID (A-/B-/C-/D-/E-/F-/H-) and was written to
FAIL on the BASE_SHA before the corresponding fix (RED -> GREEN).
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db_model import Organization, User
from app.services.project_service import ProjectService, _caller_may_access_project


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(Organization(id=1, name="org1", slug="org1"))
    session.add(Organization(id=2, name="org2", slug="org2"))
    session.add(User(id="alice", org_id=1, username="alice", email="a@x", role="editor"))
    session.add(User(id="bob", org_id=1, username="bob", email="b@x", role="editor"))
    session.add(User(id="carol", org_id=2, username="carol", email="c@x", role="editor"))
    session.commit()
    yield session
    session.close()


# ── A-1: project IDOR — NULL-org private project readable by any org caller ──

def test_A1_null_org_private_project_blocked_for_org_caller(db):
    """A project with org_id=NULL owned by alice must NOT be readable by bob
    (a different user) just because bob carries an org_id. The pre-fix guard
    `and not org_id` skipped the owner check whenever the caller had an org."""
    alice_proj = ProjectService.create_project(db, name="Alice private", owner_id="alice")
    assert alice_proj.org_id is None

    # alice herself can still access her own private project (she has org 1)
    assert ProjectService.get_project_with_auth(
        db, alice_proj.id, user_id="alice", org_id=1
    ) is not None

    # bob (same org 1) must NOT access alice's NULL-org private project
    assert ProjectService.get_project_with_auth(
        db, alice_proj.id, user_id="bob", org_id=1
    ) is None

    # carol (different org 2) must NOT access it either
    assert ProjectService.get_project_with_auth(
        db, alice_proj.id, user_id="carol", org_id=2
    ) is None


def test_A1_org_project_visible_to_same_org_member(db):
    """An org-scoped project is visible to other members of the same org."""
    proj = ProjectService.create_project(
        db, name="Org shared", owner_id="alice", org_id=1
    )
    # bob is in org 1 -> may access even though owner is alice
    assert ProjectService.get_project_with_auth(
        db, proj.id, user_id="bob", org_id=1
    ) is not None
    # carol is in org 2 -> blocked
    assert ProjectService.get_project_with_auth(
        db, proj.id, user_id="carol", org_id=2
    ) is None


def test_A1_public_project_visible_to_everyone(db):
    """A truly public project (no owner, no org) is visible to all callers."""
    proj = ProjectService.create_project(db, name="public", owner_id=None)
    assert _caller_may_access_project(proj, None, None) is True
    assert _caller_may_access_project(proj, "bob", 1) is True


# ── A-4: data-fabric create/probe/sync must require authentication ───────────

def test_A4_data_fabric_mutations_require_auth():
    """Anonymous callers must not create data sources or trigger probe/sync
    (which initiate server-side outbound requests)."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    payload = {
        "name": "evil",
        "source_type": "ogc_api",
        "endpoint_url": "https://example.com/data",
        "options": {},
    }
    # create without token -> 401
    res = client.post("/api/v1/data-fabric/sources", json=payload)
    assert res.status_code == 401, res.text
    # probe / sync on any id without token -> 401 (auth runs before lookup)
    assert client.post("/api/v1/data-fabric/sources/ds_x/probe").status_code == 401
    assert client.post("/api/v1/data-fabric/sources/ds_x/sync").status_code == 401
    # preview / query / materialize also trigger outbound remote fetches on
    # global sources — anonymous callers must not initiate them (R1S-A4).
    assert client.get("/api/v1/data-fabric/catalog/cat_x/preview").status_code == 401
    assert client.post("/api/v1/data-fabric/catalog/cat_x/query", json={"limit": 1}).status_code == 401
    assert client.post(
        "/api/v1/data-fabric/materialize",
        json={"session_id": "s", "catalog_item_id": "cat_x"},
    ).status_code == 401


# ── A-2: upload tools must not read another session's uploads ────────────────

def test_A2_upload_tools_deny_foreign_session_via_runtime_context():
    """The LLM-supplied session_id is untrusted. When the turn is bound to a
    verified session (RuntimeContext), a mismatched id must be refused rather
    than used to query another session's UploadRecord rows."""
    from app.tools.upload_tools import register_upload_tools, _resolve_session_id
    from app.tools.registry import ToolRegistry
    from app.lib.runtime.context import bind_runtime_context

    reg = ToolRegistry()
    register_upload_tools(reg)
    list_uploaded_data = reg._tools["list_uploaded_data"]

    # Turn is bound to "own-session"; the (injected) arg names a victim session.
    with bind_runtime_context(session_id="own-session"):
        assert _resolve_session_id("victim-session") is None  # denied
        assert _resolve_session_id("own-session") == "own-session"
        result = list_uploaded_data(session_id="victim-session")
    assert result["count"] == 0  # did not attempt to read victim uploads

    # Without a runtime context, the provided id passes through (back-compat).
    assert _resolve_session_id("any") == "any"


# ── E-2/E-5: MVT projection poles + NaN/Inf vertex robustness ─────────────────

def test_E2_mvt_pole_vertex_does_not_crash():
    """A polygon spanning the pole (lat=-90) must encode without raising
    (previously: math domain error in the pure path; inf in the shapely path)."""
    from app.services.mvt import encode_tile

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": 1},
         "geometry": {"type": "Polygon", "coordinates": [[
             [-180.0, -90.0], [180.0, -90.0], [180.0, -80.0], [-180.0, -80.0], [-180.0, -90.0],
         ]]}}
    ]}
    # z0 whole-world tile — must not raise and must produce non-empty bytes.
    blob = encode_tile(fc, 0, 0, 0)
    assert isinstance(blob, (bytes, bytearray))
    assert len(blob) > 0


def test_E5_mvt_linestring_nan_vertex_does_not_crash():
    """A LineString containing a NaN/Inf vertex must skip it, not raise
    ValueError in the pure path (the points path already guarded this)."""
    from app.services.mvt import encode_tile

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": 1},
         "geometry": {"type": "LineString", "coordinates": [
             [0.0, float("nan")], [10.0, float("inf")], [20.0, 30.0],
         ]}}
    ]}
    blob = encode_tile(fc, 0, 0, 0)  # must not raise
    assert isinstance(blob, (bytes, bytearray))


# ── E-4: isochrone weight must be meters, not a degree-valued attribute ───────

def test_E4_isochrone_weight_is_metric_not_degree_attribute():
    """A geographic (EPSG:4326) network whose features carry a ``length``
    attribute in DEGREES must not have its edge weight taken from that
    attribute — otherwise the meter cutoff reaches the whole network."""
    from app.lib.geo_analysis.network import calculate_isochrones

    # One short road edge near the equator: 0.01 deg lon ≈ 1.11 km.
    def _line(fid, x1, y1, x2, y2):
        return {"type": "Feature", "properties": {"id": fid, "length": 0.001},
                "geometry": {"type": "LineString", "coordinates": [[x1, y1], [x2, y2]]}}
    net = {"type": "FeatureCollection", "features": [_line("e1", 0, 0, 0.01, 0)]}
    fac = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": "f1"},
         "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}}]}
    res = calculate_isochrones(net, fac, travel_time_min=1, mode="walking")
    assert res.success is True, getattr(res, "error", None)
    # 1 min walking ≈ 80 m, so only ~80 m of the ~1.11 km edge is reachable.
    # Extract the isochrone polygon's longitude extent.
    feats = res.data["features"] if isinstance(res.data, dict) else res.data
    assert feats, "expected an isochrone polygon feature"
    lons: list[float] = []

    def _collect(node):
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
            lons.append(float(node[0]))
        elif isinstance(node, (list, tuple)):
            for child in node:
                _collect(child)

    _collect(feats[0]["geometry"]["coordinates"])
    span = max(lons) - min(lons)
    # Post-fix (metric weight): polygon spans ~0.001-0.002 deg (reachable
    # ~80 m + ~30 m road buffer). Pre-fix (degree-valued attribute used as
    # meters): the 0.001 "m" weight makes the WHOLE 1.11 km edge reachable,
    # so the polygon spans ≈ 0.01 deg. 0.005 deg cleanly separates the two.
    assert span < 0.005, f"isochrone swallowed the network: lon span {span:.5f} deg"


# ── D-1: session activity must refresh state/events/ACK key TTLs ─────────────

@pytest.mark.asyncio
async def test_D1_refresh_session_ttl_covers_state_events_and_acks():
    """A read (get) must refresh the state/events/map_actions key TTLs, not
    only the ref registry. Pre-fix an active session lost its viewport/event
    log/ACK history at 4h while payloads stayed alive."""
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore, SESSION_TTL

    raw = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisSessionStore(redis_url="redis://unused", redis=raw, capacity=8)
    sid = "s1"
    ref = await store.store(sid, {"type": "FeatureCollection", "features": []}, prefix="geojson")

    # Seed the sibling state/events/ACK keys with a SHORT ttl (simulate a key
    # that is about to expire after its last write).
    state_key = RedisSessionStore._state_key(sid)
    events_key = RedisSessionStore._events_key(sid)
    actions_key = RedisSessionStore._map_actions_key(sid)
    await raw.hset(state_key, "viewport", b"{}")
    await raw.rpush(events_key, b"{}")
    await raw.hset(actions_key, "act1", b"{}")
    for k in (state_key, events_key, actions_key):
        await raw.expire(k, 5)  # near expiry

    # A read triggers _refresh_session_ttl.
    await store.get(sid, ref)

    # All three sibling keys must now be refreshed to ~SESSION_TTL, not 5s.
    assert await raw.ttl(state_key) > SESSION_TTL - 120
    assert await raw.ttl(events_key) > SESSION_TTL - 120
    assert await raw.ttl(actions_key) > SESSION_TTL - 120


# ── B-1: partially_completed is resumable, not terminal ──────────────────────

@pytest.mark.asyncio
async def test_B1_partially_completed_can_converge_to_completed():
    """A plan that paused at partially_completed must be able to advance to
    completed on resume. Pre-fix partially_completed was in _TERMINAL_STATUSES,
    so the first-terminal-wins guard dropped the partial->completed write and
    the stored status stayed partially_completed forever."""
    from app.services import plan_mode as svc

    sid = "test-b1-converge"
    plan = svc.PlanProposal(
        title="g",
        steps=[svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "x"})],
    )
    plan_id = await svc.store_plan(sid, plan)

    # Simulate a mid-way pause then a successful resume.
    await svc.update_plan_status(sid, plan_id, __status__="partially_completed")
    await svc.update_plan_status(sid, plan_id, __status__="completed")

    data = await svc.load_plan(sid, plan_id)
    assert data is not None
    assert data["__status__"] == "completed"

    # And a true terminal still refuses to be revived (first-terminal-wins).
    await svc.update_plan_status(sid, plan_id, __status__="running")
    data2 = await svc.load_plan(sid, plan_id)
    assert data2["__status__"] == "completed"


# ── H-1: dispatch must detect the unavailable-ref store sentinel ──────────────

@pytest.mark.asyncio
async def test_H1_dispatch_detects_unavailable_ref_sentinel(monkeypatch):
    """When Redis is down, store() returns the ``ref:redis-unavailable-`` sentinel.
    dispatch must not author a MapSpec layer at that phantom ref and mark the
    call completed; it must return status=error so the LLM can retry."""
    from app.tools.registry import ToolRegistry
    from app.services.tool_dispatch_service import ToolDispatchService
    from app.services.session_data_protocol import UNAVAILABLE_REF_PREFIX

    reg = ToolRegistry()

    @reg.tool(name="make_features", description="returns a feature collection")
    def make_features() -> dict:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
        ]}

    svc = ToolDispatchService(registry=reg)

    async def _unavailable_store(session_id, data, prefix="geojson"):
        return f"{UNAVAILABLE_REF_PREFIX}deadbeef"

    monkeypatch.setattr(
        "app.services.tool_dispatch_service.session_data_manager.store",
        _unavailable_store,
    )

    tc = {"id": "call_1", "function": {"name": "make_features", "arguments": "{}"}}
    result = await svc.dispatch(tc, session_id="s-h1", executed_tools=set())
    assert result.status == "error"
    assert result.geojson_ref is None
    assert "unavailable" in (result.error_msg or "")


# ── B-1 (R1C-3): a transient 'failed' plan must be resumable and converge ────

@pytest.mark.asyncio
async def test_B1_failed_plan_can_converge_on_resume():
    """A plan that failed (no stored step results) and is legitimately resumed
    must be able to advance to completed. ``failed`` is resumable (transient
    failures are retried); only completed/cancelled are truly terminal."""
    from app.services import plan_mode as svc

    sid = "test-b1-failed-resume"
    plan = svc.PlanProposal(
        title="g",
        steps=[svc.PlanStep(id="s1", tool="fake_get_bbox", args={"area": "x"})],
    )
    plan_id = await svc.store_plan(sid, plan)
    await svc.update_plan_status(sid, plan_id, __status__="failed")
    # Resume: failed -> running -> completed must all be writable.
    await svc.update_plan_status(sid, plan_id, __status__="running")
    await svc.update_plan_status(sid, plan_id, __status__="completed")
    data = await svc.load_plan(sid, plan_id)
    assert data is not None
    assert data["__status__"] == "completed"


# ── R2-1: data-fabric DELETE must require authentication ─────────────────────

def test_R2_1_data_fabric_delete_requires_auth():
    """The destructive DELETE route must not accept anonymous callers (the
    anonymous branch of _require_tenant_owned matched legacy GLOBAL sources)."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    assert client.delete("/api/v1/data-fabric/sources/ds_x").status_code == 401


# ── R2-2: tool-supplied result_ref sentinel must fail dispatch ───────────────

@pytest.mark.asyncio
async def test_R2_2_dispatch_detects_tool_supplied_result_ref_sentinel(monkeypatch):
    """A tool that stores data itself can hand back the unavailable-ref sentinel
    as result_ref (e.g. webgis_layer_upsert's inline branch). The sentinel must
    not be promoted to geojson_ref with status=ok."""
    from app.tools.registry import ToolRegistry
    from app.services.tool_dispatch_service import ToolDispatchService
    from app.services.session_data_protocol import UNAVAILABLE_REF_PREFIX

    reg = ToolRegistry()

    @reg.tool(name="self_storing_tool", description="returns its own result_ref")
    def self_storing_tool() -> dict:
        return {
            "success": True,
            "result_ref": f"{UNAVAILABLE_REF_PREFIX}cafebabe",
            "bbox": [0, 0, 1, 1],
        }

    svc = ToolDispatchService(registry=reg)
    tc = {"id": "call_r22", "function": {"name": "self_storing_tool", "arguments": "{}"}}
    result = await svc.dispatch(tc, session_id="s-r22", executed_tools=set())
    assert result.status == "error"
    assert result.geojson_ref is None
