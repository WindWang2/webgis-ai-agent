"""Parent-owned subagent surface policy and actual registry-dispatch evidence."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from app.services.subagent_roles import SubagentRole


@dataclass(frozen=True)
class SubagentSurface:
    role: SubagentRole | None
    domains: list[str] | None
    max_rounds: int
    schemas: list[dict]


def resolve_subagent_surface(
    registry: Any, *, domains: list[str] | None = None,
    extra_tools: list[str] | None = None, max_rounds: int = 10,
    role: str | SubagentRole | None = None,
) -> SubagentSurface:
    """The single role/domain/mutation/blacklist/zero-tools narrowing seam."""
    from app.services.subagent import select_tools_for_subagent
    from app.services.subagent_roles import get_subagent_role
    role_obj = role if isinstance(role, SubagentRole) else get_subagent_role(role) if isinstance(role, str) else None
    if role_obj is not None:
        if role_obj.allowed_domains:
            requested = set(domains or [])
            domains = sorted(requested & set(role_obj.allowed_domains) if requested else role_obj.allowed_domains)
        max_rounds = min(max_rounds, role_obj.max_rounds)
    schemas = select_tools_for_subagent(
        registry, domains=domains, extra_tools=extra_tools, exclude_tier3=True,
        allow_mutation=role_obj.allow_mutation if role_obj else None,
        tool_blacklist=list(role_obj.tool_blacklist) if role_obj else None,
    )
    if role_obj is not None and role_obj.allowed_domains:
        allowed_domains = set(role_obj.allowed_domains)
        schemas = [schema for schema in schemas if allowed_domains & set(registry.descriptor(schema["function"]["name"]).domains)]
    if role_obj is not None and role_obj.max_tool_calls == 0:
        schemas = []
    return SubagentSurface(role_obj, domains, max_rounds, schemas)


class CapabilityPolicyError(ValueError):
    pass


def _descriptor_capabilities(registry: Any, name: str) -> set[str]:
    capabilities = registry.descriptor(name).capabilities
    if any(not isinstance(capability, str) or not capability or len(capability) > 128 for capability in capabilities):
        raise CapabilityPolicyError("descriptor capability IDs must contain1..128 characters")
    return set(capabilities)


class CapabilityRecordingRegistry:
    """A per-run narrowing proxy; shared parent registry is never patched."""
    def __init__(self, registry: Any, surface: SubagentSurface, *, allowed_capabilities: list[str] | None = None) -> None:
        from app.services.agent_swarm.delegation import MAX_CAPABILITIES_PER_REQUEST
        self._inner = registry
        self._allowed_names = frozenset(schema["function"]["name"] for schema in surface.schemas)
        derived = {cap for name in self._allowed_names for cap in _descriptor_capabilities(registry, name)}
        if allowed_capabilities is not None and any(not isinstance(cap, str) or not cap or len(cap) > 128 for cap in allowed_capabilities):
            raise CapabilityPolicyError("parent capability IDs must contain1..128 characters")
        if len(derived) > MAX_CAPABILITIES_PER_REQUEST:
            raise CapabilityPolicyError("parent descriptor capability grant exceeds bounded contract")
        self._explicit_grant = allowed_capabilities is not None
        self.granted = frozenset(derived if allowed_capabilities is None else derived & set(allowed_capabilities))
        self._used: set[str] = set()
        self._called: set[str] = set()
        self._missing: set[str] = set()
        self._denied: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def all_metadata(self) -> dict:
        return {name: metadata for name, metadata in self._inner.all_metadata().items() if name in self._allowed_names}

    def get_schemas(self) -> list[dict]:
        return self._inner.get_schemas_subset(set(self._allowed_names))

    def get_schemas_subset(self, names: set[str]) -> list[dict]:
        return self._inner.get_schemas_subset(set(names) & self._allowed_names)

    def list_tools(self) -> list[str]:
        return sorted(self._allowed_names)

    async def dispatch(self, name: str, *args: Any, **kwargs: Any) -> Any:
        canonical = self._inner.resolve_name(name)
        if canonical not in self._allowed_names:
            return self._deny("tool_not_granted:" + canonical)
        # Read the current descriptor immediately at the actual parent dispatch
        # boundary. Child summary/lineage is never capability evidence.
        try:
            capabilities = _descriptor_capabilities(self._inner, canonical)
        except CapabilityPolicyError:
            return self._deny("capability_descriptor_invalid:" + canonical)
        if not capabilities and self._explicit_grant:
            return self._deny("capability_evidence_missing:" + canonical)
        alien = capabilities - self.granted
        if alien:
            return self._deny("capabilities_not_granted:" + ",".join(sorted(alien)))
        self._called.add(canonical)
        if not capabilities:
            self._missing.add(canonical)
        self._used.update(capabilities)
        result = self._inner.dispatch(name, *args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def _deny(self, reason: str) -> dict:
        if len(self._denied) < 8:
            self._denied.append(reason[:240])
        return {"success": False, "code": "DELEGATION_CAPABILITY_DENIED", "error": reason[:240]}

    def capabilities_used(self) -> list[str]:
        return sorted(self._used)

    def evidence(self) -> dict:
        return {"source": "parent_registry_dispatch", "tools_used": sorted(self._called)[:128],
                "tools_without_capabilities": sorted(self._missing)[:128],
                "status": "incomplete" if self._missing else "complete", "denials": list(self._denied)}
