"""Tool capability derivation from registry metadata (design-v3 §capability).

No LLM and no new registration annotations: capabilities are derived
deterministically from what ``ToolRegistry`` already exposes — ``tier``,
``domains``, ``execution_policy`` (registry.py:223-227) and the OpenAI-format
parameter schema (``get_schemas_subset``) — plus one small, documented
heuristic for ``requires_ref``. ``validate_plan_capabilities`` returns a
deterministic issue list; an empty list means the plan is viable.

The keyword-based domain detector in tool_catalog.py stays the runtime
fallback — untouched by this slice.
"""
from dataclasses import dataclass
from typing import Optional

from app.tools.registry import ToolRegistry

from .models import CanonicalPlan

# Domains whose tools produce a session-data ref (cursor) as their primary
# output. Default for ``produces_ref``; override explicitly per tool when the
# domain default is wrong (a ref-less tool tagged with one of these domains).
#
# NOTE (#720 audit review): this is a CAPABILITY taxonomy, deliberately NOT
# identical to ToolCatalog.DOMAIN_KEYWORDS (activation keywords for schema
# subsetting). The two sets answer different questions — "does this domain
# emit refs" vs "when should its schemas be shown". When adding a catalog
# domain whose tools emit refs, add it here too.
PRODUCES_REF_DOMAINS: frozenset[str] = frozenset(
    {
        "osm",
        "data_fabric",
        "dataset",
        "spatial_catalog",
        "network",
        "raster",
        "statistics",
        "spatial",
        "temporal",
        "remote_sensing",
        "chinese",
        "geocoding",
    }
)

# Param-name tokens that suggest the argument receives an existing data ref
# (ref_id / layer_ref / input_ref / source / target / asset_id ...).
_REF_NAME_TOKENS = ("ref", "layer", "geojson", "source", "target", "asset")


@dataclass
class ToolCapability:
    """Static capability digest of one registered tool.

    ``destructive`` defaults to ``tier >= 3`` (the repo's existing derivation,
    e.g. plan_mode.py:65,80); pass an explicit bool to override. ``produces_ref``
    defaults to membership of any ``domains`` in ``PRODUCES_REF_DOMAINS``; pass
    an explicit bool to override. ``requires_ref`` is set by ``capability_of``
    from the documented param-name heuristic.
    """

    name: str
    tier: int
    domains: list[str]
    execution_policy: str
    destructive: Optional[bool] = None
    requires_ref: bool = False
    produces_ref: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.destructive is None:
            self.destructive = self.tier >= 3
        if self.produces_ref is None:
            self.produces_ref = bool(set(self.domains) & PRODUCES_REF_DOMAINS)


def _requires_ref_heuristic(tool_name: str, registry: ToolRegistry) -> bool:
    """``requires_ref`` heuristic over the tool's JSON-schema properties.

    True when any parameter is named like a ref (contains one of
    ``_REF_NAME_TOKENS``), or when any property's string content contains
    ``ref:`` (e.g. a description documenting the ref syntax). Simple and
    documented; ~90% accurate on the measured registry (swarm report B §8).
    """
    schemas = registry.get_schemas_subset({tool_name})
    if not schemas:
        return False
    properties = schemas[0]["function"]["parameters"].get("properties", {})
    for p_name, prop in properties.items():
        name_l = p_name.lower()
        if any(tok in name_l for tok in _REF_NAME_TOKENS):
            return True
        if "ref:" in str(prop):
            return True
    return False


def capability_of(tool_name: str, registry: ToolRegistry) -> Optional[ToolCapability]:
    """Derive a ToolCapability from registry metadata; None when unregistered."""
    if tool_name not in registry.list_tools():
        return None
    meta = registry.metadata(tool_name)
    policy = meta.get("execution_policy")
    policy_str = policy.value if hasattr(policy, "value") else str(policy or "thread")
    return ToolCapability(
        name=tool_name,
        tier=int(meta.get("tier", 1)),
        domains=list(meta.get("domains", [])),
        execution_policy=policy_str,
        requires_ref=_requires_ref_heuristic(tool_name, registry),
    )


def validate_plan_capabilities(plan: CanonicalPlan, registry: ToolRegistry) -> list[str]:
    """Deterministic capability validation; empty list = viable plan.

    Issues (``error:``) and warnings (``warning:``) are emitted in plan step
    order for:

    - ``step.tool`` set but not registered                  → error
    - ``step.tool_family`` set but no registered tool declares that domain
                                                             → error
      (``core`` is the universal fallback family — always accepted)
    - ``step.tool`` registered but its domains do not cover
      ``step.tool_family``                                  → warning

    Steps with neither tool nor tool_family have nothing to check.
    """
    registered = set(registry.list_tools())
    family_domains: set[str] = set()
    for name in registered:
        family_domains.update(registry.metadata(name).get("domains", []))

    issues: list[str] = []
    for step in plan.steps:
        if step.tool:
            if step.tool not in registered:
                issues.append(
                    f"error: step '{step.id}' references unregistered tool '{step.tool}'"
                )
            elif step.tool_family and step.tool_family != "core":
                cap = capability_of(step.tool, registry)
                # cap is not None: step.tool is registered (checked above).
                tool_domains = set(cap.domains) if cap is not None else set()
                if step.tool_family not in tool_domains:
                    issues.append(
                        f"warning: step '{step.id}' tool '{step.tool}' domains "
                        f"{sorted(tool_domains)} do not cover tool_family "
                        f"'{step.tool_family}'"
                    )
        if (
            step.tool_family
            and step.tool_family != "core"
            and step.tool_family not in family_domains
        ):
            issues.append(
                f"error: step '{step.id}' tool_family '{step.tool_family}' "
                "has no registered tool in that domain"
            )
    return issues
