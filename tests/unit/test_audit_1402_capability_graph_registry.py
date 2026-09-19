"""#1402: capability graph uses lifespan registry; descriptor security enforced."""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture()
def ext_registry(monkeypatch):
    """Inject a registry that includes an extension-style tool."""
    from app.agent_pi_bridge import set_tool_registry
    from app.services.gis_harness.capability_graph import reset_capability_graph
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)

    async def _ext(**_kwargs):
        return {"success": True, "summary": "ext-ok"}

    reg.register(
        "ext_audit_1402_tool",
        "extension tool for audit 1402",
        _ext,
        parameters={"type": "object", "properties": {}},
        capabilities=["audit_1402_cap"],
        requires_credentials=["ext_api_key"],
        required_permission="ext_admin",
        provider_dependencies=["ext_provider"],
        latency_class="fast",
        cost="light",
        side_effect="pure",
    )
    set_tool_registry(reg)
    reset_capability_graph()
    yield reg
    # leave injected; other tests may reset via their fixtures
    reset_capability_graph()


def test_extension_tool_visible_in_capability_graph(ext_registry):
    from app.services.gis_harness.capability_graph import (
        REL_INVOKES,
        get_capability_graph,
        reset_capability_graph,
    )

    reset_capability_graph()
    g = get_capability_graph()
    tools = g.tools_for_capability("audit_1402_cap")
    assert "ext_audit_1402_tool" in tools
    # invokes edge emitted
    src = "tool:ext_audit_1402_tool"
    invoked = [e.dst for e in g._edges if e.src == src and e.relation == REL_INVOKES]
    assert "provider:ext_provider" in invoked


def test_descriptor_mutation_rebuilds_graph(ext_registry):
    from app.services.gis_harness.capability_graph import (
        get_capability_graph,
        graph_build_count_for_tests,
        reset_capability_graph,
        source_fingerprints,
    )

    reset_capability_graph()
    g1 = get_capability_graph()
    before = graph_build_count_for_tests()
    fp1 = source_fingerprints()["tool_descriptors"]
    # mutate a consumed field
    meta = ext_registry.metadata("ext_audit_1402_tool")
    meta["cost"] = "heavy"
    reset_capability_graph()  # clear instance cache; fingerprint drives rebuild
    g2 = get_capability_graph()
    fp2 = source_fingerprints()["tool_descriptors"]
    assert fp1 != fp2
    assert graph_build_count_for_tests() > before
    assert g2 is not g1


def test_dispatch_denies_missing_credentials(ext_registry):
    from app.tools.registry import present_tool_credentials

    async def _run():
        denied = await ext_registry.dispatch(
            "ext_audit_1402_tool", {}, session_id="s-1402")
        assert denied.get("success") is False
        assert denied.get("code") == "CREDENTIALS_REQUIRED"

        with present_tool_credentials("ext_api_key"):
            # still missing permission
            denied2 = await ext_registry.dispatch(
                "ext_audit_1402_tool", {}, session_id="s-1402")
        assert denied2.get("code") == "PERMISSION_DENIED"

    asyncio.run(_run())


def test_dispatch_allows_when_credential_and_permission_granted(ext_registry):
    from app.tools.registry import (
        grant_tool_permissions,
        present_tool_credentials,
    )

    async def _run():
        with present_tool_credentials("ext_api_key"), grant_tool_permissions("ext_admin"):
            ok = await ext_registry.dispatch(
                "ext_audit_1402_tool", {}, session_id="s-1402")
        assert ok.get("success") is True

    asyncio.run(_run())


def test_qualify_node_credentials_hard_fail():
    from app.services.gis_harness.capability_graph import GraphNode, KIND_TOOL
    from app.services.gis_harness.qualification_v8 import (
        QualificationContext,
        QualificationStatus,
        qualify_node,
    )

    node = GraphNode(
        "t", KIND_TOOL, "test",
        extras={"requires_credentials": ["api_key"], "tier": 1},
    )
    ctx = QualificationContext(credentials_present={"api_key": False})
    result = qualify_node(node, ctx)
    assert result.status == QualificationStatus.INELIGIBLE
    assert any(r.check == "credentials" for r in result.reasons)
