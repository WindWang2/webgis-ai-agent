"""BE-3 evidence tests: webgis_* wrapper evidence forwarding + view_set camera loop.

HARNESS-V3 design §8: the six webgis_* mutation wrappers must forward the
adapter's evidence fields (is_compiled / warnings / checkpoint_id /
correction_hint) and derive ``success`` from the adapter result — a REJECTED
mutation returns ``success:False`` + ``correction_hint`` so the LLM can
self-heal (previously the wrappers hardcoded ``success:True`` and dropped all
evidence). ``webgis_view_set`` must additionally sync ``map_state.viewport``
and emit a ``fly_to`` command (the live camera actually moves).
``spatial_decision_v2`` must read the validator's ``valid`` key —
``validate_runtime`` returns ``{valid: ...}``, not ``{success: ...}``.
"""
import shutil
import uuid

import pytest

from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    from app.tools.cartography_tools import register_mapspec_cartography_tools
    register_mapspec_cartography_tools(r)
    return r


@pytest.fixture
async def clean_session():
    sid = f"be3-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    session_dir = BASE_STORAGE_DIR / sid
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)


def _geojson(features=None):
    return {"type": "FeatureCollection", "features": features or []}


# ─── webgis_* mutation wrappers forward evidence ─────────────────────────


@pytest.mark.asyncio
async def test_project_init_forwards_evidence(registry, clean_session):
    """init_project must not hardcode success:True — it forwards the adapter's
    is_compiled (False for a bare empty project: MISSING_SOURCES) and warnings."""
    res = await registry.dispatch(
        "webgis_project_init",
        {"view": {"center": [120.0, 30.0], "zoom": 10.0}},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["is_compiled"] is False, "an empty project has no sources → not compile-valid"
    assert "mapspec" in res and res["mapspec"]["view"]["center"] == [120.0, 30.0]
    # MISSING_SOURCES surfaces through the engine's warnings list.
    assert any("sources" in w.lower() for w in res.get("warnings", []))


@pytest.mark.asyncio
async def test_layer_upsert_forwards_is_compiled_and_checkpoint_id(registry, clean_session):
    """A successful upsert must carry is_compiled=True (real validate() outcome)
    and the auto-checkpoint id — the evidence the harness MapSpecValidity
    ladder reads."""
    layer = {"id": "eq", "source": "src", "type": "circle", "paint": {"circle-color": "#ff0000"}}
    res = await registry.dispatch(
        "webgis_layer_upsert",
        {
            "layer": layer,
            "source_data": _geojson([
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                 "properties": {"v": 1}},
            ]),
        },
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["is_compiled"] is True, "a valid upsert must compile-validate"
    assert res["checkpoint_id"], "auto-checkpoint id must be forwarded"
    assert res["layer_id"] == "eq"


@pytest.mark.asyncio
async def test_rejected_mutation_returns_success_false_with_correction_hint(registry, clean_session):
    """A mutation introducing a blocking error (NON_INCREASING_STOPS) is rejected
    by the engine → the wrapper must report success:False + correction_hint so the
    LLM can self-heal instead of the old hardcoded success:True."""
    # Establish a valid baseline (last-known-good).
    good_layer = {"id": "good", "source": "src-good", "type": "circle", "paint": {"circle-color": "#ff0000"}}
    ok = await registry.dispatch(
        "webgis_layer_upsert",
        {
            "layer": good_layer,
            "source_data": _geojson([
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                 "properties": {"v": 1}},
            ]),
        },
        session_id=clean_session,
    )
    assert ok["success"] is True

    bad_layer = {
        "id": "bad", "source": "src-bad", "type": "circle",
        "paint": {"circle-color": {"method": "interpolate", "field": "v", "stops": [
            [10, "#fff"], [5, "#000"]  # non-increasing
        ]}},
    }
    res = await registry.dispatch(
        "webgis_layer_upsert",
        {"layer": bad_layer, "source_data": _geojson()},
        session_id=clean_session,
    )
    assert res["success"] is False, "blocking mutation must NOT report success"
    assert res["is_compiled"] is False
    assert res.get("message"), "rejection must carry the error message"
    assert res.get("correction_hint"), "rejection must carry a self-healing hint"
    # Last-known-good preserved: the bad layer never landed, the good one did.
    mapspec = await mapspec_store.get_mapspec(clean_session)
    layer_ids = [lyr["id"] for lyr in mapspec.get("layers", [])]
    assert "good" in layer_ids
    assert "bad" not in layer_ids


@pytest.mark.asyncio
async def test_layer_remove_forwards_evidence(registry, clean_session):
    layer = {"id": "L", "source": "s", "type": "circle", "paint": {"circle-color": "#0f0"}}
    await registry.dispatch(
        "webgis_layer_upsert",
        {"layer": layer, "source_data": _geojson()},
        session_id=clean_session,
    )
    res = await registry.dispatch("webgis_layer_remove", {"layer_id": "L"}, session_id=clean_session)
    assert res["success"] is True
    assert res["is_compiled"] is True
    assert res["removed_id"] == "L"


@pytest.mark.asyncio
async def test_layout_set_forwards_evidence(registry, clean_session):
    await registry.dispatch("webgis_project_init", {}, session_id=clean_session)
    res = await registry.dispatch(
        "webgis_layout_set",
        {"legend": {"title": "T", "position": "top-right", "visible": True}},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert "is_compiled" in res, "layout_set must forward is_compiled evidence"
    assert res["layout"]["legend"]["title"] == "T"


# ─── webgis_view_set: live camera moves (viewport sync + fly_to command) ──


@pytest.mark.asyncio
async def test_view_set_emits_fly_to_and_syncs_viewport(registry, clean_session):
    """The live camera must actually move: result carries a fly_to command and
    map_state.viewport is synced (previously only mapspec.view was written)."""
    await registry.dispatch(
        "webgis_project_init",
        {"view": {"center": [0.0, 0.0], "zoom": 2.0}},
        session_id=clean_session,
    )
    res = await registry.dispatch(
        "webgis_view_set",
        {"center": [116.4, 39.9], "zoom": 12.0},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["command"] == "fly_to"
    assert res["params"]["center"] == [116.4, 39.9]
    assert res["params"]["zoom"] == 12.0
    state = await session_data_manager.get_map_state(clean_session)
    assert state["viewport"]["center"] == [116.4, 39.9]
    assert state["viewport"]["zoom"] == 12.0


@pytest.mark.asyncio
async def test_view_set_no_command_without_view_params(registry, clean_session):
    """Guard: when no view params are provided, no fly_to command is emitted
    and the viewport is left untouched."""
    await registry.dispatch(
        "webgis_project_init",
        {"view": {"center": [0.0, 0.0], "zoom": 2.0}},
        session_id=clean_session,
    )
    res = await registry.dispatch("webgis_view_set", {}, session_id=clean_session)
    assert res["success"] is True
    assert "command" not in res
    assert "params" not in res
    state = await session_data_manager.get_map_state(clean_session)
    assert "viewport" not in state or state["viewport"].get("center") == [0.0, 0.0]


@pytest.mark.asyncio
async def test_view_set_partial_params_merge_viewport(registry, clean_session):
    """Partial view_set (pitch only) must merge into the existing viewport, not
    clobber the previously-set center/zoom."""
    await registry.dispatch(
        "webgis_view_set",
        {"center": [116.4, 39.9], "zoom": 12.0},
        session_id=clean_session,
    )
    res = await registry.dispatch("webgis_view_set", {"pitch": 45.0}, session_id=clean_session)
    assert res["command"] == "fly_to"
    assert res["params"] == {"pitch": 45.0}
    state = await session_data_manager.get_map_state(clean_session)
    assert state["viewport"]["center"] == [116.4, 39.9]
    assert state["viewport"]["zoom"] == 12.0
    assert state["viewport"]["pitch"] == 45.0


# ─── webgis_source_profile: failure path returns self-healing hint ────────


@pytest.mark.asyncio
async def test_source_profile_success(registry, clean_session):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
             "properties": {"v": 1}},
        ],
    }
    res = await registry.dispatch(
        "webgis_source_profile",
        {"source_id": "eq", "geojson_data": geojson},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["source_id"] == "eq"
    assert res["profile"]["featureCount"] == 1


@pytest.mark.asyncio
async def test_source_profile_failure_returns_correction_hint(registry, monkeypatch, clean_session):
    """The source_profile adapter bypasses the engine (no lock/validation), so
    its failure path is a raised exception — the wrapper must convert it into
    success:False + correction_hint (self-healing) instead of letting it throw."""

    async def _boom(session_id, source_id, geojson_data):
        raise ValueError("malformed geojson")

    monkeypatch.setattr(mapspec_store, "source_profile", _boom)
    res = await registry.dispatch(
        "webgis_source_profile",
        {"source_id": "eq", "geojson_data": {"bad": 1}},
        session_id=clean_session,
    )
    assert res["success"] is False
    assert res["message"]
    assert "correction_hint" in res


# ─── spatial_decision_v2: runtime_validated reads the `valid` key ─────────


class _FakeDecisionResult:
    """Minimal stand-in for SpatialDecisionResult (only what the tool touches:
    model_dump() and simulation_ref_id on the Fetch-on-Demand trim path)."""

    simulation_ref_id = "ref:sim-fake"

    def model_dump(self):
        return {
            "type": "spatial_decision_result",
            "decision_id": "dec_fake",
            "simulation_geojson": {"type": "FeatureCollection", "features": []},
        }


@pytest.fixture
def spatial_registry():
    r = ToolRegistry()
    from app.tools.spatial_decision_tools import register_spatial_decision_tools
    register_spatial_decision_tools(r)
    return r


async def _run_spatial_tool(spatial_registry, sid: str) -> dict:
    """Invoke spatial_decision_v2 through the real ``registry.dispatch`` path.

    Regression guard: the tool is ``async def`` and must be registered with
    ``ToolExecutionPolicy.ASYNC`` — under the old THREAD policy the registry
    ran the coroutine function in a thread and returned the un-awaited
    coroutine (a latent dispatch bug that broke JSON serialization downstream).
    Dispatching here proves the ASYNC path actually awaits and yields a plain
    dict result.
    """
    res = await spatial_registry.dispatch(
        "spatial_decision_v2",
        {
            "scenario": "新建地铁站",
            "target_area": "test-area",
            "parameters": {},
            "baseline_data_ref": "",
        },
        session_id=sid,
    )
    assert not hasattr(res, "send"), (
        "dispatch returned an un-awaited coroutine — execution policy mismatch "
        "(async def tool must use ToolExecutionPolicy.ASYNC)"
    )
    return res


@pytest.fixture
def spatial_mocks(monkeypatch):
    from app.services.spatial_decision.engine import DecisionEngine

    async def _fake_evaluate(self, scenario_text, target_area_text, parameters=None,
                             baseline_data_ref="", session_id="", owner_token=None):
        return _FakeDecisionResult()

    monkeypatch.setattr(DecisionEngine, "evaluate_decision", _fake_evaluate)

    async def _fake_apply(session_id, result):
        return {"mapspec_applied": True}

    # The tool module binds these names at import time — patch its namespace.
    monkeypatch.setattr("app.tools.spatial_decision_tools.apply_decision_to_mapspec", _fake_apply)
    monkeypatch.setattr(
        "app.tools.spatial_decision_tools.generate_decision_report_markdown",
        lambda result: "# report",
    )
    return monkeypatch


@pytest.mark.asyncio
async def test_spatial_decision_runtime_validated_true_when_validator_returns_valid(
    spatial_registry, spatial_mocks, clean_session
):
    """validate_runtime returns {valid: ...} on the evaluation path — the tool
    must read `valid` (the old `success`-only read reported a passing
    validation as False)."""
    from app.services.runtime_validator import runtime_validator

    async def _valid(session_id):
        return {"valid": True}

    spatial_mocks.setattr(runtime_validator, "validate_runtime", _valid)
    res = await _run_spatial_tool(spatial_registry, clean_session)
    assert res["runtime_validated"] is True


@pytest.mark.asyncio
async def test_spatial_decision_runtime_validated_fallback_to_success(
    spatial_registry, spatial_mocks, clean_session
):
    """Failure short-circuits return {success: ...} instead — the fallback keeps
    those paths working too."""
    from app.services.runtime_validator import runtime_validator

    async def _success_only(session_id):
        return {"success": True}

    spatial_mocks.setattr(runtime_validator, "validate_runtime", _success_only)
    res = await _run_spatial_tool(spatial_registry, clean_session)
    assert res["runtime_validated"] is True
