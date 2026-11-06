"""Cross-writer and fail-closed regressions for review #1815."""
import asyncio
from collections import OrderedDict
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.services.session_data import MemorySessionStore
from app.services.session_plan import SessionPlan, save_session_plan, load_session_plan


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "redis"])
async def test_envelope_cas_refuses_equal_revision_competing_writer_and_missing_ref(backend):
    if backend == "memory":
        store = MemorySessionStore()
    else:
        import fakeredis.aioredis
        from app.services.session_data_redis import RedisSessionStore
        store = RedisSessionStore("redis://fake-review/0")
        store._r = fakeredis.aioredis.FakeRedis()
        async def connected():
            return store._r
        store._ensure_connected = connected
    plan = SessionPlan(envelope_id="review-envelope", session_id=f"cas-review-{backend}", revision=0)
    assert await save_session_plan(plan, store=store, expected_revision=0)
    left, right = await asyncio.gather(load_session_plan(plan.session_id, store=store), load_session_plan(plan.session_id, store=store))
    assert left.revision == right.revision
    left.user_goal = "first"
    right.user_goal = "stale"
    outcomes = await asyncio.gather(save_session_plan(left, store=store, expected_revision=left.revision), save_session_plan(right, store=store, expected_revision=right.revision))
    assert sum(outcomes) == 1
    saved = await load_session_plan(plan.session_id, store=store)
    assert saved.revision == plan.revision + 1
    ref = await store.resolve_alias(plan.session_id, "session_plan_current")
    # The actual alias constant is used so deleted/expired refs cannot be revived.
    from app.services.session_plan import CURRENT_ALIAS
    ref = await store.resolve_alias(plan.session_id, CURRENT_ALIAS)
    await store.delete_ref(plan.session_id, ref)
    assert not await store.overwrite(plan.session_id, ref, {"revision": 999}, expected_revision=saved.revision)


@pytest.mark.asyncio
async def test_atomic_recovery_counters_preserve_concurrent_loop_updates(monkeypatch):
    from app.services import session_data
    from app.services.gis_harness.durable_context import update_recovery_state, load_recovery_state
    store = MemorySessionStore()
    monkeypatch.setattr(session_data, "session_data_manager", store)
    await asyncio.gather(*(update_recovery_state("atomic-review", loop="repair" if index % 2 else "replan") for index in range(20)))
    state = await load_recovery_state("atomic-review")
    assert state["loops"]["repair"] == state["loops"]["replan"] == 10
    assert len(state["history"]) == 16


@pytest.mark.asyncio
async def test_auto_repair_reservation_is_atomic_and_failure_refunds_only_its_token():
    from app.services.gis_harness.visual_observation.auto_budget import reserve, finish
    store = MemorySessionStore()
    calls = await asyncio.gather(*(reserve(store, "budget-review", "_budget", 5, per_revision=1, per_session=4) for _ in range(8)))
    granted = [token for token, reason in calls if token]
    assert len(granted) == 1
    assert await finish(store, "budget-review", "_budget", granted[0], retain_charge=False)
    assert (await reserve(store, "budget-review", "_budget", 5, per_revision=1, per_session=4))[0]


def test_governor_failure_uses_bounded_local_reservations(monkeypatch):
    from app.services.gis_harness import loop_budget
    monkeypatch.setattr(loop_budget, "_LOCAL_ATTEMPTS", OrderedDict())
    monkeypatch.setattr(loop_budget, "_CHARGE_UNAVAILABLE", False)
    monkeypatch.setattr(loop_budget, "_retry_budget", lambda: None)
    decisions = [loop_budget.loop_retry_admissible("loop-review", "repair")[0] for _ in range(8)]
    assert decisions == [True, True, True, False, False, False, False, False]


def test_recovery_write_failure_returns_unavailable_not_fresh_retry_count(tmp_path, monkeypatch):
    from app.services.gis_harness import recovery_ledger
    from app.services.harness_kernel import metrics
    monkeypatch.setattr(recovery_ledger, "_ledger_path", lambda sid: tmp_path / "ledger.json")
    ledger = recovery_ledger.RecoveryLedger()
    monkeypatch.setattr(ledger, "_save_locked", lambda *args: False)
    before = metrics.snapshot()["counters"].get("recovery_ledger_persist_failed", 0)
    assert ledger.record_failure("review", "read", "timeout") == recovery_ledger.LEDGER_UNAVAILABLE
    assert ledger.record_failure("review", "read", "timeout") == recovery_ledger.LEDGER_UNAVAILABLE
    assert metrics.snapshot()["counters"]["recovery_ledger_persist_failed"] == before + 2


def test_statistic_metadata_cannot_positively_prove_itself():
    from app.services.gis_harness.evidence_claim.census import project_artifact_record
    from app.services.gis_harness.evidence_claim.verify import _value_compatible
    from app.services.gis_harness.evidence_claim.contracts import Claim
    record = SimpleNamespace(artifact_id="stat-review", artifact_type="stats_table", status="valid", revision=1, metadata={"value": 4.7})
    evidence = project_artifact_record(record, tenant_id="t", session_id="s")
    claim = Claim(claim_id="claim-review", claim_type="density", subject="district", predicate="density", value=4.7)
    assert _value_compatible(claim, evidence.metadata) == (False, "value_self_attested")


def test_all_recipe_elements_and_fallbacks_have_executable_sources():
    from app.services.gis_harness.recipes import get_recipe_registry, run_eligibility_rules, EligibilityContext
    from app.services.gis_harness.recipe_integrity import declaration_issues
    from app.lib.cartography.model_library import get_map_model_registry
    registry = get_recipe_registry()
    models = get_map_model_registry()
    assert not {rid: issues for rid in registry.all_ids if (issues := declaration_issues(registry.get(rid), models))}
    kriging = registry.get("kriging_interpolation_workflow")
    assert any(not result.ok for result in run_eligibility_rules(kriging, EligibilityContext(n=29)))
    assert all(result.ok for result in run_eligibility_rules(kriging, EligibilityContext(n=30)))
    assert kriging.eligibility[0].element == "raster_surface"


@pytest.mark.asyncio
async def test_settlement_claim_survives_process_receipt_cache_reset(monkeypatch):
    from app.services.harness_kernel import runtime as runtime_module, settle
    store = MemorySessionStore()
    runtime = runtime_module.GISSessionRuntime("settle-claim-review", store=store)
    await runtime.begin_turn("turn-review", host="pi", message="q")
    monkeypatch.setattr(runtime_module, "get_runtime", lambda sid: runtime)
    entered = asyncio.Event()
    proceed = asyncio.Event()
    effects = []
    async def run_effects(*args, **kwargs):
        effects.append(args)
        entered.set()
        await proceed.wait()
        return [], None
    monkeypatch.setattr(settle, "run_settle_effects", run_effects)
    monkeypatch.setattr(settle, "resolve_turn_refusal", AsyncMock(return_value=None))
    inp = settle.HarnessSettleInput(session_id=runtime.session_id, turn_id="turn-review", host="pi", cause="clean")
    first = asyncio.create_task(settle.settle_turn(inp))
    await asyncio.wait_for(entered.wait(), 5)
    monkeypatch.setattr(settle, "_TURN_LOCKS", OrderedDict())
    monkeypatch.setattr(settle, "_RECEIPTS", OrderedDict())
    second = await settle.settle_turn(inp)
    assert second.disposition == "in_progress"
    assert len(effects) == 1
    proceed.set()
    await first


@pytest.mark.asyncio
async def test_concurrent_shared_screenshot_registration_keeps_all_owners(tmp_path, monkeypatch):
    from app.services import session_data, durable_blob_store
    from app.services.gis_harness.visual_observation.store import register_visual_screenshot, load_screenshot_index
    from app.services.gis_harness.visual_observation.blob_refs import blob_has_live_ref, release_blob_ref
    from app.services.durable_blob_store import FilesystemBlobStore
    store = MemorySessionStore()
    blobs = FilesystemBlobStore(tmp_path)
    monkeypatch.setattr(session_data, "session_data_manager", store)
    monkeypatch.setattr(durable_blob_store, "get_filesystem_blob_store", lambda: blobs)
    image = BytesIO()
    Image.new("RGB", (8, 8), (20, 30, 40)).save(image, "PNG")
    entries = await asyncio.gather(*(register_visual_screenshot(f"owner-{i}", image.getvalue()) for i in range(8)))
    assert len({entry.sha256 for entry in entries}) == 1
    for index, entry in enumerate(entries):
        assert blob_has_live_ref(entry.sha256, f"owner-{index}")
        assert len(await load_screenshot_index(f"owner-{index}")) == 1
    assert not await asyncio.to_thread(release_blob_ref, entries[0].sha256, "owner-0")
    assert blob_has_live_ref(entries[0].sha256, "owner-7")


@pytest.mark.asyncio
async def test_product_spec_cas_precedes_physical_effects_and_stale_guard_refuses(monkeypatch):
    from app.services import session_plan
    from app.services.gis_harness.product_edit_commit import commit_product_edit, apply_product_component
    from app.services.mapspec import mapspec_lifecycle_engine
    store = MemorySessionStore()
    monkeypatch.setattr(session_plan, "session_data_manager", store)
    plan = SessionPlan(envelope_id="edit-review", session_id="edit-review", revision=0,
                       gis_chapter={"product_spec": {"digest": "base"}})
    assert await save_session_plan(plan, store=store, expected_revision=0)
    assert await commit_product_edit("edit-review", "base", {"digest": "first"})
    assert not await commit_product_edit("edit-review", "base", {"digest": "stale"})
    committed = await load_session_plan("edit-review", store=store)
    assert committed.gis_chapter["product_spec"]["digest"] == "first"
    async def execute(sid, intent, *, pre_commit_check):
        return await pre_commit_check(sid, intent, "agent", {})
    monkeypatch.setattr(mapspec_lifecycle_engine, "apply_mutation", execute)
    result = await apply_product_component("edit-review", "stale", "component")
    assert not result["success"]
    assert "superseded" in result["message"]


def test_delegation_uses_registered_runtime_tool_surface(monkeypatch):
    from app import agent_pi_bridge
    from app.services.gis_harness.delegation import _registry_for
    from app.tools.registry import ToolRegistry, tool
    registry = ToolRegistry()
    @tool(registry, name="review_read", description="Read-only regression fixture", tier=1, domains=["map"])
    async def read() -> dict:
        return {"success": True}
    monkeypatch.setattr(agent_pi_bridge, "_tool_registry", registry)
    assert _registry_for() is registry
    assert "review_read" in _registry_for().list_tools()


@pytest.mark.asyncio
async def test_supersede_preserves_revision_and_implicit_save_cannot_revert_new_goal(monkeypatch):
    from app.services.session_plan import apply_tool_result
    from app.services.workflow_runtime import hooks
    monkeypatch.setattr(hooks, "record_tool_result_safe", AsyncMock())
    monkeypatch.setattr(hooks, "record_intent_changes_safe", AsyncMock())
    store = MemorySessionStore()
    def plan_for(city):
        return {"query": f"{city}学校分布", "recipe_id": "poi_distribution_overview",
                "intent": {"task": "distribution_overview", "scope": {"name": city, "level": "city"},
                           "subject": {"category": "学校"}}, "data_requirements": [], "analysis_steps": []}
    await apply_tool_result("supersede-review", "webgis_map_intent", {"plan": plan_for("成都")}, success=True, store=store)
    stale = await load_session_plan("supersede-review", store=store)
    await apply_tool_result("supersede-review", "webgis_map_intent", {"plan": plan_for("重庆")}, success=True, store=store)
    fresh = await load_session_plan("supersede-review", store=store)
    assert fresh.envelope_id != stale.envelope_id
    assert fresh.revision > stale.revision
    assert not await save_session_plan(stale, store=store)
    assert (await load_session_plan("supersede-review", store=store)).user_goal == fresh.user_goal


@pytest.mark.asyncio
async def test_settlement_claim_rechecks_status_changed_after_snapshot(monkeypatch):
    from app.services.harness_kernel import runtime as runtime_module
    from app.services.harness_kernel.settle_claim import claim_settlement
    runtime = runtime_module.GISSessionRuntime("settle-status-review", store=MemorySessionStore())
    await runtime.begin_turn("turn-review", host="pi", message="q")
    monkeypatch.setattr(runtime_module, "get_runtime", lambda sid: runtime)
    await runtime.end_turn("turn-review", host="pi", status="cancelled")
    assert await claim_settlement(runtime.session_id, "turn-review", expected_status="running") == "stale"
    plan = await load_session_plan(runtime.session_id, store=runtime._store)
    assert not plan.turns[-1].settle_token


@pytest.mark.asyncio
async def test_redis_deleted_and_expired_current_alias_can_be_recreated():
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore
    from app.services.session_plan import CURRENT_ALIAS, ensure_session_plan_slot
    store = RedisSessionStore("redis://fake-review/0")
    store._r = fakeredis.aioredis.FakeRedis()
    async def connected():
        return store._r
    store._ensure_connected = connected
    first = await ensure_session_plan_slot("alias-review", store=store)
    ref = await store.resolve_alias("alias-review", CURRENT_ALIAS)
    assert await store._r.hget(store._refs_key("alias-review"), ref) == CURRENT_ALIAS.encode()
    assert await store.delete_ref("alias-review", ref)
    assert await store.resolve_alias("alias-review", CURRENT_ALIAS) == CURRENT_ALIAS
    second = await ensure_session_plan_slot("alias-review", store=store)
    assert second.envelope_id != first.envelope_id
    ref = await store.resolve_alias("alias-review", CURRENT_ALIAS)
    await store._r.delete(store._data_key("alias-review", ref))  # payload TTL expires first
    assert await store.resolve_alias("alias-review", CURRENT_ALIAS) == CURRENT_ALIAS
    third = await ensure_session_plan_slot("alias-review", store=store)
    assert third.envelope_id != second.envelope_id
    live = await store.resolve_alias("alias-review", CURRENT_ALIAS)
    await store.set_alias("alias-review", live, "shared")
    other = await store.store("alias-review", {"value": 1})
    await store.set_alias("alias-review", other, "shared")
    await store.delete_ref("alias-review", live)
    assert await store.resolve_alias("alias-review", "shared") == other


@pytest.mark.asyncio
async def test_archive_publication_failure_aborts_supersede_and_removes_new_ref(monkeypatch):
    from app.services.session_plan import _archive_envelope, CURRENT_ALIAS
    store = MemorySessionStore()
    plan = SessionPlan(envelope_id="archive-review", session_id="archive-review", revision=0)
    assert await save_session_plan(plan, store=store, expected_revision=0)
    before = await store.list_refs(plan.session_id)
    live = await store.resolve_alias(plan.session_id, CURRENT_ALIAS)
    monkeypatch.setattr(store, "set_alias_if_absent", AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match="archive publication"):
        await _archive_envelope(plan, store=store)
    assert await store.list_refs(plan.session_id) == before
    assert await store.resolve_alias(plan.session_id, CURRENT_ALIAS) == live


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "redis"])
async def test_plan_history_and_data_have_independent_capacity(backend):
    if backend == "memory":
        store = MemorySessionStore(capacity=1)
    else:
        import fakeredis.aioredis
        from app.services.session_data_redis import RedisSessionStore
        store = RedisSessionStore("redis://fake-review/0", capacity=1)
        store._r = fakeredis.aioredis.FakeRedis()
        async def connected():
            return store._r
        store._ensure_connected = connected
    from app.services.session_plan import _archive_envelope, CURRENT_ALIAS
    from app.services.session_control_refs import MAX_PLAN_HISTORY
    data = await store.store("capacity-review", {"user": "data"})
    current = SessionPlan(envelope_id="current", session_id="capacity-review", revision=0)
    assert await save_session_plan(current, store=store, expected_revision=0)
    for index in range(MAX_PLAN_HISTORY + 2):
        await _archive_envelope(SessionPlan(envelope_id=f"old-{index}", session_id="capacity-review"), store=store)
    assert await store.get("capacity-review", data) == {"user": "data"}
    refs = await store.list_refs("capacity-review")
    assert sum(str(alias or "").startswith("session-plan-id:") for alias in refs.values()) == MAX_PLAN_HISTORY
    newer_data = await store.store("capacity-review", {"user": "new"})
    assert await store.get("capacity-review", newer_data) == {"user": "new"}
    assert await load_session_plan("capacity-review", store=store) is not None
    assert await store.resolve_alias("capacity-review", CURRENT_ALIAS) != CURRENT_ALIAS
