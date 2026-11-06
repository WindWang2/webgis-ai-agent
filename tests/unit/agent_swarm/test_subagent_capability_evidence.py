"""Parent descriptor/dispatch evidence for #1862 S3-024; no LLM authority."""
from types import SimpleNamespace

import pytest

from app.tools.registry import ToolRegistry, tool
from app.services.agent_swarm.capability_observation import (
    resolve_subagent_surface, CapabilityRecordingRegistry, CapabilityPolicyError,
)
from app.services.agent_swarm.delegation import DelegationGateway, FairSlotScheduler
from app.services.agent_swarm.delegation_adapters import run_single_delegation


def register(registry, name, capabilities, *, domains=("dataset",), tier=1, side_effect="pure", effects=None):
    async def execute() -> dict:
        if effects is not None:
            effects.append(name)
        return {"success": True}
    tool(registry, name=name, description=name, capabilities=capabilities,
         domains=list(domains), tier=tier, side_effect=side_effect)(execute)


def test_same_role_surface_drives_parent_grant_and_child_extra_tool_domain_ceiling():
    from app.services.subagent_roles import SubagentRole
    registry = ToolRegistry()
    register(registry, "data", ["data.read"], domains=("dataset",), tier=2)
    register(registry, "network", ["network.read"], domains=("network",), tier=2)
    register(registry, "mixed", ["mixed.read"], domains=("dataset", "network"), tier=2)
    role = SubagentRole(name="test", title="test", model_role="execution", allowed_domains=("dataset",), allow_mutation=False, max_rounds=2)
    surface = resolve_subagent_surface(registry, role=role, domains=["dataset", "network"], extra_tools=["network"], max_rounds=10)
    assert {schema["function"]["name"] for schema in surface.schemas} == {"data", "mixed"}
    assert surface.max_rounds == 2
    observer = CapabilityRecordingRegistry(registry, surface)
    assert observer.granted == {"data.read", "mixed.read"}
    # Adhoc extra-tool behavior remains available to an unrestricted parent.
    assert "network" in {s["function"]["name"] for s in resolve_subagent_surface(registry, extra_tools=["network"]).schemas}


def test_parent_descriptor_union_exceeds_old_twelve_but_never_truncates_over_bound():
    registry = ToolRegistry()
    for index in range(13):
        register(registry, f"t{index}", [f"cap.{index}"])
    observer = CapabilityRecordingRegistry(registry, resolve_subagent_surface(registry))
    assert len(observer.granted) == 13
    from app.services.agent_swarm.delegation import MAX_CAPABILITIES_PER_REQUEST
    for index in range(13, MAX_CAPABILITIES_PER_REQUEST + 1):
        register(registry, f"t{index}", [f"cap.{index}"])
    with pytest.raises(CapabilityPolicyError, match="bounded"):
        CapabilityRecordingRegistry(registry, resolve_subagent_surface(registry))


@pytest.mark.asyncio
async def test_default_production_request_and_outcome_use_parent_actual_dispatch(monkeypatch):
    from app.services import subagent
    registry = ToolRegistry()
    effects = []
    register(registry, "read_one", ["cap.one"], effects=effects)
    register(registry, "read_two", ["cap.two"], effects=effects)
    class Dispatcher:
        def __init__(self, registry, session_id):
            self.registry = registry
        async def run(self, **kwargs):
            await self.registry.dispatch("read_one", {})
            return subagent.SubagentResult(success=True, summary="done", lineage={"capabilities_used": ["forged.child.claim"]})
    monkeypatch.setattr(subagent, "SubagentDispatcher", Dispatcher)
    seen = []
    class Gateway(DelegationGateway):
        async def execute(self, request, runner):
            seen.append(request.allowed_capabilities)
            result = await super().execute(request, runner)
            self.outcome = result
            return result
    gateway = Gateway(scheduler=FairSlotScheduler())
    result, _ = await run_single_delegation(registry, "s", task="read", gateway=gateway)
    assert result.success and effects == ["read_one"]
    assert seen == [["cap.one", "cap.two"]]
    assert gateway.outcome.capabilities_used == ["cap.one"]
    assert result.lineage["capabilities_used"] == ["cap.one"]
    assert gateway.outcome.runner_extras["capability_evidence"]["source"] == "parent_registry_dispatch"


@pytest.mark.asyncio
async def test_parent_capability_denial_prevents_effect_and_child_cannot_claim_success(monkeypatch):
    from app.services import subagent
    registry = ToolRegistry()
    effects = []
    register(registry, "read_one", ["cap.one"], effects=effects)
    register(registry, "write_two", ["cap.two"], effects=effects)
    class Dispatcher:
        def __init__(self, registry, session_id):
            self.registry = registry
        async def run(self, **kwargs):
            denied = await self.registry.dispatch("write_two", {})
            assert denied["code"] == "DELEGATION_CAPABILITY_DENIED"
            return subagent.SubagentResult(success=True, summary="pretend success", lineage={"capabilities_used": []})
    monkeypatch.setattr(subagent, "SubagentDispatcher", Dispatcher)
    result, _ = await run_single_delegation(registry, "s", task="read", allowed_capabilities=["cap.one"], gateway=DelegationGateway(scheduler=FairSlotScheduler()))
    assert not result.success and effects == []
    assert "capabilities_not_granted:cap.two" in result.error
    assert result.lineage["capabilities_used"] == []
    assert result.lineage["delegation_verdicts"] == ["capabilities_not_granted:cap.two"]


@pytest.mark.asyncio
async def test_per_runner_observers_are_isolated_and_missing_metadata_is_disclosed():
    import asyncio
    registry = ToolRegistry()
    register(registry, "one", ["cap.one"])
    register(registry, "two", ["cap.two"])
    register(registry, "unmapped", [])
    surface = resolve_subagent_surface(registry)
    first, second = CapabilityRecordingRegistry(registry, surface), CapabilityRecordingRegistry(registry, surface)
    await asyncio.gather(first.dispatch("one", {}), second.dispatch("two", {}))
    assert first.capabilities_used() == ["cap.one"] and second.capabilities_used() == ["cap.two"]
    await first.dispatch("unmapped", {})
    assert first.evidence()["status"] == "incomplete"
    assert first.evidence()["tools_without_capabilities"] == ["unmapped"]
    explicit = CapabilityRecordingRegistry(registry, surface, allowed_capabilities=["cap.one"])
    assert (await explicit.dispatch("unmapped", {}))["code"] == "DELEGATION_CAPABILITY_DENIED"


def test_mapper_ignores_self_reported_capabilities_without_parent_observation():
    from app.services.agent_swarm.delegation import DelegationLease
    from app.services.agent_swarm.delegation_adapters import outcome_from_subagent_result
    lease = DelegationLease(delegation_id="dg-test", lease_id="lease", session_id="s", generation=1)
    result = SimpleNamespace(success=True, summary="done", capabilities_used=["forged"], lineage={"capabilities_used": ["forged"]})
    assert outcome_from_subagent_result(lease, result, 0).capabilities_used == []


def test_capability_id_length_is_rejected_before_start_without_truncation():
    registry = ToolRegistry()
    register(registry, "bad", ["x" * 129])
    with pytest.raises(CapabilityPolicyError, match="128"):
        CapabilityRecordingRegistry(registry, resolve_subagent_surface(registry))
    registry = ToolRegistry()
    register(registry, "good", ["valid"])
    with pytest.raises(CapabilityPolicyError, match="128"):
        CapabilityRecordingRegistry(registry, resolve_subagent_surface(registry), allowed_capabilities=["x" * 129])


@pytest.mark.asyncio
async def test_changed_descriptor_is_checked_at_dispatch_before_effect():
    registry = ToolRegistry()
    effects = []
    register(registry, "changing", ["initial"], effects=effects)
    observed = CapabilityRecordingRegistry(registry, resolve_subagent_surface(registry))
    register(registry, "changing", ["x" * 129], effects=effects)
    result = await observed.dispatch("changing", {})
    assert result["code"] == "DELEGATION_CAPABILITY_DENIED" and effects == []
    assert observed.evidence()["denials"] == ["capability_descriptor_invalid:changing"]


def test_all_production_role_descriptor_unions_fit_bounded_parent_policy():
    from app.tools import init_tools
    from app.services.subagent_roles import SUBAGENT_ROLES
    from app.services.agent_swarm.delegation import MAX_CAPABILITIES_PER_REQUEST
    registry = ToolRegistry()
    init_tools(registry)
    maximum = 0
    for role in [None, *SUBAGENT_ROLES]:
        surface = resolve_subagent_surface(registry, role=role)
        observer = CapabilityRecordingRegistry(registry, surface)
        actual = {cap for schema in surface.schemas for cap in registry.descriptor(schema["function"]["name"]).capabilities}
        assert observer.granted == actual
        assert len(actual) <= MAX_CAPABILITIES_PER_REQUEST
        maximum = max(maximum, len(actual))
    assert maximum > 128  # Guards the real inventory which disproved the interim128 cap.


@pytest.mark.asyncio
async def test_failed_parent_policy_cannot_recover_into_an_empty_declared_grant(monkeypatch):
    from app.services import subagent
    from app.services.agent_swarm.delegation import DelegationLease
    from app.services.agent_swarm.delegation_adapters import SubagentDispatcherRunner
    registry = ToolRegistry()
    register(registry, "tool", ["x" * 129])
    runner = SubagentDispatcherRunner(registry, "s", task="read")
    with pytest.raises(CapabilityPolicyError):
        runner.prepare_policy()
    # Registry can change between request creation and its queued runner. The
    # failed request must remain failed, rather than run with allowed_caps=[].
    register(registry, "tool", ["now.valid"])
    called = []
    class Dispatcher:
        def __init__(self, registry, session_id):
            called.append(True)
        async def run(self, **kwargs):
            return subagent.SubagentResult(success=True, summary="must not run")
    monkeypatch.setattr(subagent, "SubagentDispatcher", Dispatcher)
    lease = DelegationLease(delegation_id="dg-failed-policy", lease_id="lease", generation=1, session_id="s")
    result = await runner.run(lease)
    assert result.status == "failed" and called == []
