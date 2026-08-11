"""
Unit tests for PiAgentHarness V2, HarnessEvaluator, and harness_runner.

V2 contract under test:
- MapSpecValidity is derived from REAL evidence (is_compiled), not "didn't error".
- CursorResolutionRate is derived from REAL SessionStore resolution, not ref prefix.
- Missing evidence → 0.0 (honest), never 100.0 (fake success).
- Each evidence record carries run/session/turn correlation (no cross-session pollution).
"""
import pytest

from app.lib.harness.evaluator import HarnessEvaluator
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.tools.harness_runner import run_benchmark_scenario


class _FakeStore:
    """Minimal async session store for ref-resolution tests."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}

    async def get(self, session_id, ref):
        return self._data.get(ref)


# ── Legacy compatibility: evaluator gate on an explicit metric dict ──────


def test_evaluator_quality_gate_checks():
    evaluator = HarnessEvaluator()
    metrics = {
        "ToolChoiceAccuracy": 95.0,
        "MapSpecValidity": 100.0,
        "CursorResolutionRate": 100.0,
        "StepEfficiency": 85.0,
        "ErrorRecoveryRate": 90.0,
    }
    result = evaluator.evaluate_session(metrics)
    assert result["overall_passed"] is True

    report = evaluator.generate_markdown_report("test_session", result)
    assert "# Pi GIS Agent Evaluation Report" in report
    assert "✅ PASSED" in report


# ── V2: MapSpecValidity is evidence-driven, not "didn't error" ───────────


def test_mapspec_validity_requires_real_evidence():
    """V2 core fix: a mutation that 'didn't error' but lacks is_compiled
    evidence is NOT counted as valid. MapSpecValidity must be 0, not 100."""
    harness = PiAgentHarness(session_id="s1")

    # mutation tool that returned success but NO semantic validation evidence
    harness.record_tool_call("c1", "webgis_layer_upsert", {"layer": {"id": "L"}})
    harness.record_tool_result("c1", "webgis_layer_upsert", {"success": True})

    assert harness.compute_mapspec_validity() == 0.0


def test_mapspec_validity_semantic_valid_when_is_compiled():
    """A mutation with is_compiled=True carries real semantic-validity evidence."""
    harness = PiAgentHarness(session_id="s2")
    harness.record_tool_call("c1", "webgis_layer_upsert", {"layer": {"id": "L"}})
    harness.record_tool_result(
        "c1", "webgis_layer_upsert", {"success": True, "is_compiled": True}
    )
    assert harness.compute_mapspec_validity() == 100.0


def test_mapspec_validity_no_mutations_is_zero_not_hundred():
    """No mutations recorded → 0.0 (no evidence), NOT 100.0 (fake success)."""
    harness = PiAgentHarness(session_id="s3")
    assert harness.compute_mapspec_validity() == 0.0


def test_mapspec_validity_mixed_mutations():
    """1 semantic-valid + 1 evidence-less mutation → 50%, reflecting honest split."""
    harness = PiAgentHarness(session_id="s4")
    harness.record_tool_call("c1", "webgis_layer_upsert", {})
    harness.record_tool_result("c1", "webgis_layer_upsert", {"success": True, "is_compiled": True})
    harness.record_tool_call("c2", "webgis_view_set", {})
    harness.record_tool_result("c2", "webgis_view_set", {"success": True})  # no is_compiled
    assert harness.compute_mapspec_validity() == 50.0


# ── V2: CursorResolutionRate from REAL SessionStore resolution ───────────


def test_cursor_resolution_no_resolver_is_zero_not_hundred():
    """Without a real resolver, refs are syntactically-valid only → NOT resolved.
    No resolver + a ref present → 0.0, not 100.0."""
    harness = PiAgentHarness(session_id="s5")  # no ref_resolver wired
    harness.record_tool_call("c1", "st_dbscan", {"geojson": "ref:geojson-12345"})
    harness.record_tool_result("c1", "st_dbscan", {"success": True})
    assert harness.compute_cursor_resolution_rate() == 0.0


def test_cursor_resolution_no_refs_is_zero_not_hundred():
    harness = PiAgentHarness(session_id="s6")
    assert harness.compute_cursor_resolution_rate() == 0.0


@pytest.mark.asyncio
async def test_cursor_resolution_real_resolver_resolved_and_missing():
    """A real resolver resolves an existing ref but NOT a missing one."""
    store = _FakeStore({"ref:geojson-exists": {"type": "FeatureCollection", "features": []}})
    harness = PiAgentHarness(
        session_id="s7",
        ref_resolver=_build_resolver(store),
    )
    harness.record_tool_call("c1", "st_dbscan", {
        "a": "ref:geojson-exists",      # present
        "b": "ref:geojson-missing",     # absent
    })
    harness.record_tool_result("c1", "st_dbscan", {"success": True})

    await harness.evaluate_with_evidence(expected_tools=["st_dbscan"], ideal_step_count=1)
    # 1 of 2 resolved → 50%
    assert harness.compute_cursor_resolution_rate() == 50.0


@pytest.mark.asyncio
async def test_cursor_resolution_type_mismatch_not_resolved():
    """A ref whose payload type contradicts its prefix is TYPE_MISMATCH, not resolved."""
    store = _FakeStore({"ref:raster-x": {"type": "FeatureCollection", "features": []}})
    harness = PiAgentHarness(
        session_id="s8", ref_resolver=_build_resolver(store),
    )
    harness.record_tool_call("c1", "render", {"data": "ref:raster-x"})
    harness.record_tool_result("c1", "render", {"success": True})
    await harness.evaluate_with_evidence(expected_tools=["render"], ideal_step_count=1)
    assert harness.compute_cursor_resolution_rate() == 0.0


# ── V2: session/run/turn correlation scoping ────────────────────────────


def test_evidence_carries_correlation_fields():
    harness = PiAgentHarness(session_id="scope_session")
    harness.set_correlation(run_id="run_42", turn_id="turn_7")
    harness.record_tool_call("c1", "webgis_layer_upsert", {})
    harness.record_tool_result("c1", "webgis_layer_upsert", {"success": True, "is_compiled": True})

    ev = harness.tool_calls[0]
    assert ev["run_id"] == "run_42"
    assert ev["turn_id"] == "turn_7"
    assert ev["session_id"] == "scope_session"


@pytest.mark.asyncio
async def test_two_runs_do_not_cross_contaminate():
    """Concurrent sessions pool into separate correlation scopes; evidence is
    not shared across runs (no pollution)."""
    store = _FakeStore({"ref:geojson-own": {"type": "FeatureCollection", "features": []}})
    h1 = PiAgentHarness(session_id="sess_A", ref_resolver=_build_resolver(store))
    h2 = PiAgentHarness(session_id="sess_B", ref_resolver=_build_resolver(store))

    h1.set_correlation(run_id="run_A")
    h2.set_correlation(run_id="run_B")

    h1.record_tool_call("a1", "st_dbscan", {"data": "ref:geojson-own"})
    h2.record_tool_call("b1", "st_dbscan", {"data": "ref:geojson-missing"})

    assert h1.tool_calls[0]["run_id"] == "run_A"
    assert h2.tool_calls[0]["run_id"] == "run_B"
    assert len(h1.ref_cursors) == 1
    assert len(h2.ref_cursors) == 1


@pytest.mark.asyncio
async def test_evaluate_with_evidence_builds_structured_trail():
    store = _FakeStore({"ref:geojson-ok": {"type": "FeatureCollection", "features": []}})
    harness = PiAgentHarness(session_id="s9", ref_resolver=_build_resolver(store))
    harness.record_tool_call("c1", "webgis_layer_upsert", {"src": "ref:geojson-ok"})
    harness.record_tool_result("c1", "webgis_layer_upsert", {"success": True, "is_compiled": True})

    result = await harness.evaluate_with_evidence(
        expected_tools=["webgis_layer_upsert"], ideal_step_count=1
    )
    assert result["run_id"]
    ev = result["evidence"][0]
    assert ev["tool_name"] == "webgis_layer_upsert"
    assert ev["mapspec_validity"]["tier"] == "SEMANTIC_VALID"
    assert ev["mapspec_validity"]["is_valid"] is True
    assert result["ref_resolutions"]["ref:geojson-ok"]["status"] == "resolved"


# ── V2: evaluate_evidence gate policy — missing evidence is NOT success ──


@pytest.mark.asyncio
async def test_evaluate_evidence_fails_when_cartography_not_evaluated():
    """A run with no mutations/refs must FAIL the gate under default policy
    (require_evaluated=True), not pass via fake 100s."""
    harness = PiAgentHarness(session_id="s10")
    harness.record_tool_call("c1", "st_dbscan", {})
    harness.record_tool_result("c1", "st_dbscan", {"success": True})
    ev_result = await harness.evaluate_with_evidence(expected_tools=["st_dbscan"], ideal_step_count=1)

    evaluator = HarnessEvaluator()
    gated = evaluator.evaluate_evidence(ev_result)
    assert gated["overall_passed"] is False
    assert gated["checks"]["MapSpecValidity"]["reason"] == "not_evaluated_policy_fail"


# ── Error recovery + step efficiency (unchanged mechanics, re-asserted) ──


def test_error_recovery_tracking():
    harness = PiAgentHarness(session_id="s11")
    harness.record_tool_call("c1", "st_dbscan", {})
    harness.record_tool_result("c1", "st_dbscan", {}, is_error=True, error_msg="Invalid CRS")
    harness.record_tool_call("c2", "st_dbscan", {"crs": "EPSG:4326"})
    harness.record_tool_result("c2", "st_dbscan", {"success": True}, is_error=False)

    metrics = harness.evaluate_all(expected_tools=["st_dbscan"], ideal_step_count=1)
    assert metrics["ErrorRecoveryRate"] == 100.0
    assert metrics["StepEfficiency"] == 50.0  # 1 ideal / 2 actual


# ── harness_runner: honest metric reporting ──────────────────────────────


def test_run_benchmark_scenario_reports_honest_metrics():
    """The runner must report honest V2 metrics. A mutation scenario with real
    is_compiled evidence yields MapSpecValidity=100; a non-cartography scenario
    honestly yields 0 where there is no evidence."""
    res = run_benchmark_scenario(
        scenario_id="scenario_cartography_v2",
        expected_tools=["webgis_layer_upsert"],
        ideal_step_count=1,
        simulated_tool_calls=[{
            "id": "c1", "name": "webgis_layer_upsert",
            "arguments": {"layer": {"id": "L"}},
        }],
        simulated_tool_results=[{
            "id": "c1", "name": "webgis_layer_upsert",
            "result": {"success": True, "is_compiled": True},
        }],
    )
    assert res["scenario_id"] == "scenario_cartography_v2"
    assert res["metrics"]["MapSpecValidity"] == 100.0
    # No refs in this scenario → CursorResolutionRate is honestly 0 (no evidence).
    assert res["metrics"]["CursorResolutionRate"] == 0.0
    assert "ToolChoiceAccuracy" in res["metrics"]


def _build_resolver(store: _FakeStore):
    """Build a resolver using the real make_session_store_resolver against a fake store."""
    from app.lib.harness.ref_resolver import make_session_store_resolver
    return make_session_store_resolver(store)
