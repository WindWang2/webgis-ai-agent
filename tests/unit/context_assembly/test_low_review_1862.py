"""Source evidence, global hard budget and governor-denial regressions."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.lib.runtime.scoped_text import ScopedText
from app.services.context_assembly.contract import (
    ContextAssemblyRejected, ContextDomain, SharedTurnFacts, TurnContextRequest, bounded_item,
)


@pytest.mark.asyncio
async def test_source_scope_is_not_relabelled_from_request(monkeypatch):
    from app.services.context_assembly.providers import ProjectKnowledgeProvider, GisMemoryProvider
    from app.services.context_assembly.assembly import _scope_gate
    import app.services.chat.context_assembler as source
    import app.services.gis_memory.queries as memory
    req = TurnContextRequest(session_id="own", project_id="p-own", org_id="org-own")
    monkeypatch.setattr(source, "_build_project_knowledge_block", lambda *args, **kwargs: ScopedText("private", source_scope={
        "scope": "project", "scope_id": "p-other", "project_id": "p-other", "org_id": "org-other", "sensitivity": "project_scoped"}))
    async def memory_text(inp):
        return ScopedText("private", source_scope={"scope": "project", "scope_id": "org-other", "org_id": "org-other", "sensitivity": "org_scoped"})
    monkeypatch.setattr(memory, "build_memory_projection", memory_text)
    for provider in (ProjectKnowledgeProvider(), GisMemoryProvider()):
        report = await provider.collect(req, SharedTurnFacts())
        assert report.items and report.items[0].org_id == "org-other"
        kept, denied = _scope_gate(report.items, req)
        assert kept == [] and len(denied) == 1


@pytest.mark.asyncio
async def test_unscoped_source_string_is_not_self_attested_by_provider(monkeypatch):
    from app.services.context_assembly.providers import ProjectKnowledgeProvider
    import app.services.chat.context_assembler as source
    monkeypatch.setattr(source, "_build_project_knowledge_block", lambda *args, **kwargs: "unknown owner")
    report = await ProjectKnowledgeProvider().collect(TurnContextRequest(project_id="p"), SharedTurnFacts())
    assert report.items == []


@pytest.mark.asyncio
async def test_project_provider_preserves_existing_authorization_gate(monkeypatch):
    import app.services.chat.context_assembler as source
    from app.services.project_service import ProjectService
    from app.services.project_knowledge import store
    from app.services.context_assembly.providers import ProjectKnowledgeProvider
    from contextlib import nullcontext
    seen = []
    monkeypatch.setenv("GIS_PROJECT_KNOWLEDGE", "1")
    monkeypatch.setattr(source, "_get_session_local", lambda: lambda: nullcontext(object()))
    def denied(db, project_id, *, user_id, org_id):
        seen.append((project_id, user_id, org_id))
        return None
    monkeypatch.setattr(ProjectService, "get_project_with_auth", denied)
    def forbidden(*args, **kwargs):
        raise AssertionError("knowledge read bypassed project authorization")
    monkeypatch.setattr(store, "get_active_entries", forbidden)
    report = await ProjectKnowledgeProvider().collect(TurnContextRequest(project_id="foreign", org_id="12", user_id="caller"), SharedTurnFacts())
    assert report.items == [] and seen == [("foreign", "caller", 12)]


def test_all_owner_dimensions_are_checked_and_unknown_owner_is_not_wildcard():
    req = dict(org_id="org-own", project_id="p", session_id="s")
    item = bounded_item(item_id="x", provider_id="p", domain=ContextDomain.PROJECT_KNOWLEDGE, content="text",
                        scope="project", project_id="p", org_id="org-other", sensitivity="project_scoped")
    assert not item.renderable_in(**req)
    unknown = item.model_copy(update={"org_id": "", "project_id": ""})
    assert not unknown.renderable_in(**req)


def test_preferred_floors_cannot_exceed_global_hard_limit(monkeypatch):
    from app.services.context_assembly import allocator
    monkeypatch.setattr(allocator, "plan_budget", lambda **kwargs: SimpleNamespace(
        usable=8, window_known=True, category_budgets={"MAP_STATE": 1}))
    items = [bounded_item(item_id=f"floor-{i}", provider_id="p", domain=ContextDomain.ENVIRONMENT,
                          content="中文" * 1000) for i in range(3)]
    out = allocator.allocate(items, context_window=32, max_output_tokens=0)
    assert out.planned_tokens <= 8 and not out.over_budget
    assert len({(row.provider_id, row.item_id) for row in out.records}) == len(out.records)
    assert all(row.decision == "omitted" for row in out.records)


@pytest.mark.asyncio
async def test_governor_rejection_aborts_before_settlement_and_cannot_use_legacy(monkeypatch):
    from app.services.context_assembly import assembly, governor_link
    from app.services.chat import pi_turn_context
    monkeypatch.setattr(assembly, "fetch_shared_facts", AsyncMock(return_value=SharedTurnFacts()))
    monkeypatch.setattr(assembly, "build_default_providers", lambda: [])
    monkeypatch.setattr("app.services.context_assembly.providers.GisContextCardProvider.build", AsyncMock(return_value=[]))
    monkeypatch.setattr(governor_link, "plan_turn_context", AsyncMock(return_value=SimpleNamespace(decision="reject")))
    settle = AsyncMock()
    monkeypatch.setattr(governor_link, "settle_turn_context", settle)
    legacy = AsyncMock(return_value="must not run")
    monkeypatch.setattr(pi_turn_context, "legacy_bind_turn_prompt", legacy)
    monkeypatch.setenv("GIS_TYPED_CONTEXT_ASSEMBLY", "1")
    with pytest.raises(ContextAssemblyRejected, match="governor"):
        await pi_turn_context.bind_turn_prompt("hello", "token", "s")
    settle.assert_not_called()
    legacy.assert_not_called()


def test_project_source_scope_comes_from_authorized_database_row(monkeypatch):
    import app.services.chat.context_assembler as source
    from app.services.project_service import ProjectService
    from app.services.project_knowledge import card
    from contextlib import nullcontext
    monkeypatch.setenv("GIS_PROJECT_KNOWLEDGE", "1")
    monkeypatch.setattr(source, "_get_session_local", lambda: lambda: nullcontext(SimpleNamespace(commit=lambda: None)))
    monkeypatch.setattr(ProjectService, "get_project_with_auth", lambda *args, **kwargs: SimpleNamespace(id="actual-project", org_id=22, name="Actual"))
    monkeypatch.setattr(card, "load_project_knowledge_card", lambda *args, **kwargs: SimpleNamespace(text="source projection"))
    text = source._build_project_knowledge_block("requested-project", user_id="caller", org_id="11")
    assert text.source_scope["project_id"] == "actual-project"
    assert text.source_scope["org_id"] == "22"


@pytest.mark.asyncio
async def test_plan_scope_uses_source_envelope_not_requested_session(monkeypatch):
    from app.services.context_assembly.providers import SessionPlanProvider, ToolSurfaceProvider
    from app.services.context_assembly.assembly import _scope_gate
    from app.services import session_plan
    monkeypatch.setattr(session_plan, "format_session_plan_projection", lambda *args: "other plan")
    facts = SharedTurnFacts(plan=SimpleNamespace(session_id="source-other", revision=1))
    req = TurnContextRequest(session_id="requested")
    report = await SessionPlanProvider().collect(req, facts)
    assert report.items[0].scope_id == "source-other"
    assert not _scope_gate(report.items, req)[0]
    assert not ToolSurfaceProvider().enabled(req, facts)
