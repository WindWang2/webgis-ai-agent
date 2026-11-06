"""Regression tests for reviewed storage authority and failure semantics."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_redis_overwrite_never_resurrects_an_evicted_ref():
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore

    redis = fakeredis.aioredis.FakeRedis()
    store = RedisSessionStore(redis_url="redis://unused", redis=redis)
    assert not await store.overwrite("s", "ref:missing", {"x": 1})
    assert not await redis.exists(store._data_key("s", "ref:missing"))
    assert not await redis.zcard(store._refs_order_key("s"))


@pytest.mark.asyncio
async def test_memory_payload_budget_preserves_control_plane(monkeypatch):
    from app.services.session_data import MemorySessionStore

    monkeypatch.setenv("SESSION_STORE_MAX_BYTES", "1000")
    store = MemorySessionStore(capacity=1)
    plan_ref = await store.store("s", {"plan": "keep"}, prefix="sessionplan")
    await store.set_alias("s", plan_ref, "session-plan")
    await store.store("s", {"data": 1})
    await store.store("s", {"data": 2})
    assert await store.resolve_alias("s", "session-plan") == plan_ref
    assert await store.get("s", plan_ref) == {"plan": "keep"}
    with pytest.raises(ValueError, match="byte budget"):
        await store.store("s", {"big": "x" * 2000})
    assert await store.get("s", plan_ref) == {"plan": "keep"}


@pytest.mark.asyncio
async def test_state_and_descriptor_reads_do_not_escape_nested_aliases():
    from app.services.session_data import MemorySessionStore

    store = MemorySessionStore()
    await store.set_map_state("s", "value", {"nested": [1]})
    field = await store.get_state_field("s", "value")
    field["nested"].append(2)
    assert await store.get_state_field("s", "value") == {"nested": [1]}
    ref = await store.store("s", {"type": "FeatureCollection", "features": []})
    descriptor = await store.get_ref_descriptor("s", ref)
    descriptor["content_revision"] = 999
    assert (await store.get_ref_descriptor("s", ref))["content_revision"] == 1
    with pytest.raises(ValueError, match="missing"):
        await store.set_alias("s", "ref:gone", "bad")


@pytest.mark.asyncio
async def test_plan_store_unavailability_never_publishes_an_alias():
    from app.services.session_plan import SessionPlan, save_session_plan

    backend = SimpleNamespace(resolve_alias=AsyncMock(return_value="session-plan"),
                              store=AsyncMock(return_value="ref:redis-unavailable-x"), set_alias=AsyncMock())
    with pytest.raises(RuntimeError, match="unavailable"):
        await save_session_plan(SessionPlan(envelope_id="e", session_id="s"), store=backend)
    backend.set_alias.assert_not_called()


def test_failed_repeat_does_not_erase_completed_capability():
    from app.services.session_plan import SessionPlan, CapabilityProgress, _mark_progress

    plan = SessionPlan(envelope_id="e", session_id="s", progress=[CapabilityProgress(capability="buffer", status="complete", bound_ref="ref:good")])
    assert _mark_progress(plan, ["buffer"], status="failed") == []
    assert plan.progress[0].status == "complete"
    assert plan.progress[0].bound_ref == "ref:good"


def test_skill_creator_defaults_to_quarantine_and_rejects_unsafe_code(tmp_path):
    from app.services.skill_creator import SkillCreator

    creator = SkillCreator(str(tmp_path))
    creator.create_skill("safe", "def run():\n    return 1\n", "safe")
    assert (tmp_path / "quarantine" / "safe.py").exists()
    assert not (tmp_path / "safe.py").exists()
    with pytest.raises(ValueError, match="rejected"):
        creator.create_skill("bad", "import subprocess\nsubprocess.run(['id'])\n", "bad")


def test_mvt_exteriors_have_positive_signed_area_in_tile_coordinates():
    from app.services.mvt import _orient_ring, _shoelace

    ring = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]
    assert _shoelace(_orient_ring(ring, exterior=True)) > 0
    assert _shoelace(_orient_ring(ring, exterior=False)) < 0


def test_subagent_in_flight_usage_counts_towards_token_budget():
    from app.services.subagent_roles import SubagentBudget, BudgetExceeded

    budget = SubagentBudget(max_tool_calls=10, max_heavy_tool_calls=1, max_wall_time_s=60, max_total_tokens=10)
    with pytest.raises(BudgetExceeded):
        budget.check_tokens(in_flight_tokens=11)


@pytest.mark.asyncio
async def test_quota_policy_lookup_failure_never_grants_writes():
    from app.services.org_quota import effective_limits
    from app.core.errors import PlatformError

    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("database down")), rollback=AsyncMock())
    with pytest.raises(PlatformError) as caught:
        await effective_limits(db, "org-a")
    assert caught.value.http_status == 503
