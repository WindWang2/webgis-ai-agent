"""F04 contract tests — ContextItem / domain table invariants.

Negative cases are first-class: caps bite, scopes deny, fingerprints
distinguish, and the control plane is structurally untouchable.
"""
import pytest
from pydantic import ValidationError

from app.services.context_assembly.contract import (
    CONTEXT_DOMAIN_RANKS,
    DOMAIN_CHAR_CAPS,
    INLINE_JOIN_DOMAINS,
    UNTOUCHABLE_DOMAINS,
    ContextDomain,
    ContextItem,
    SharedTurnFacts,
    TurnContextRequest,
    bounded_item,
    content_fingerprint,
    domain_fence_mode,
    domain_pool,
    domain_rank,
)
from app.services.gis_context.scope import SensitivityClass, ScopeTier


def test_item_estimates_tokens_and_fingerprint_deterministically():
    a = bounded_item(item_id="x", provider_id="p",
                     domain=ContextDomain.SESSION_PLAN, content="hello 世界")
    b = bounded_item(item_id="x", provider_id="p",
                     domain=ContextDomain.SESSION_PLAN, content="hello 世界")
    assert a.est_tokens > 0
    assert a.fingerprint == b.fingerprint == content_fingerprint("hello 世界")
    assert a.fingerprint != content_fingerprint("hello 世界!")


def test_item_char_cap_bites_at_factory():
    cap = DOMAIN_CHAR_CAPS[ContextDomain.GIS_MEMORY]
    item = bounded_item(item_id="x", provider_id="p",
                        domain=ContextDomain.GIS_MEMORY, content="x" * (cap + 500))
    assert len(item.content) == cap
    assert item.content.endswith("…")


def test_item_defaults_derive_from_domain_table():
    item = bounded_item(item_id="x", provider_id="p",
                        domain=ContextDomain.CARTOGRAPHY_VERDICT, content="v")
    assert item.priority == 10  # verdict table priority
    assert item.floor_tokens > 0
    assert domain_pool(item.domain).name == "HARNESS_EVIDENCE"


def test_marker_items_are_control_plane():
    marker = bounded_item(item_id="m", provider_id="a",
                          domain=ContextDomain.TURN_MARKER, content="[x]")
    tools = bounded_item(item_id="t", provider_id="a",
                         domain=ContextDomain.ACTIVE_TOOLS, content="[y]")
    assert marker.control_plane and tools.control_plane
    assert {marker.domain, tools.domain} <= UNTOUCHABLE_DOMAINS


def test_request_is_frozen_and_bounded():
    req = TurnContextRequest(session_id="s", message="hi")
    with pytest.raises(Exception):
        req.session_id = "other"  # type: ignore[misc]
    assert req.schema_version == "ctx.req.v1"


def test_scope_gate_session_local_requires_matching_session():
    item = ContextItem(
        item_id="i", provider_id="p", domain=ContextDomain.GIS_MEMORY,
        content="c", scope=ScopeTier.SESSION, scope_id="sess-A",
        sensitivity=SensitivityClass.SESSION_LOCAL,
    )
    assert item.renderable_in(session_id="sess-A")
    assert not item.renderable_in(session_id="sess-B")


def test_scope_gate_project_and_org():
    proj = ContextItem(
        item_id="i", provider_id="p", domain=ContextDomain.CARTOGRAPHY_MEMORY,
        content="c", scope=ScopeTier.PROJECT, scope_id="p1",
        project_id="p1", sensitivity=SensitivityClass.PROJECT_SCOPED,
    )
    assert proj.renderable_in(project_id="p1")
    assert not proj.renderable_in(project_id="p2")
    org = ContextItem(
        item_id="j", provider_id="p", domain=ContextDomain.GIS_MEMORY,
        content="c", org_id="org9",
        sensitivity=SensitivityClass.ORG_SCOPED,
    )
    assert org.renderable_in(org_id="org9")
    assert not org.renderable_in(org_id="org8")


def test_domain_table_invariants():
    ranks = [domain_rank(d) for d in ContextDomain]
    assert len(set(ranks)) == len(ranks), "render ranks must be unique"
    assert CONTEXT_DOMAIN_RANKS[0] is ContextDomain.USER_MESSAGE
    assert CONTEXT_DOMAIN_RANKS[-1] is ContextDomain.TURN_MARKER
    # legacy byte contract: the five cartography domains join inline
    assert INLINE_JOIN_DOMAINS == {
        ContextDomain.CARTOGRAPHY_VERDICT,
        ContextDomain.CARTOGRAPHY_MEMORY,
        ContextDomain.PROJECT_KNOWLEDGE,
        ContextDomain.GIS_MEMORY,
        ContextDomain.GIS_CONTEXT_CARD,
    }
    # every domain has a fence discipline and a pool
    for domain in ContextDomain:
        assert domain_fence_mode(domain) in ("internal", "wrap", "none")
        assert domain_pool(domain) is not None


def test_trimmed_copy_re_fingerprints():
    item = bounded_item(item_id="x", provider_id="p",
                        domain=ContextDomain.ENVIRONMENT, content="a" * 200)
    trimmed = item.trimmed_copy("short")
    assert trimmed.fingerprint == content_fingerprint("short")
    assert trimmed.content == "short"
    assert trimmed.item_id == item.item_id


def test_shared_facts_carry_errors():
    facts = SharedTurnFacts()
    facts.fetch_errors["map_state"] = "TimeoutError"
    assert facts.map_state is None
    assert facts.fetch_errors["map_state"] == "TimeoutError"


def test_item_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ContextItem(
            item_id="i", provider_id="p", domain=ContextDomain.GIS_MEMORY,
            content="c", nonsense_field="x",
        )
