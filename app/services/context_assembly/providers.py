"""Typed context providers (F04) — adapters over existing single-render builders.

Each provider projects ONE authority; none re-renders authority data:
- verdict/memory/knowledge/gis_memory/card → the same builders the legacy
  ``_build_cartography_turn_context`` orchestrates (single render source);
- plan/surface/active-tools/v6/evicted → the same helpers ``bind_turn_prompt``
  uses (imported, not copied);
- harness state → the canonical ``HarnessTurnContext`` (hk.ctx.v1) projection
  (ADR-0208 D2: prompt assembly finally consumes the kernel's typed turn
  context);
- environment → caller-injected (the situation compiler owns the
  forward-only snapshot advance; the pipeline must not re-derive it).

``collect_cartography_wave`` is shared by the typed assembly AND the legacy
byte-equivalent compat path — one implementation, two consumers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Tuple

from app.services.context_assembly.contract import (
    ContextDomain,
    ContextItem,
    ProviderReport,
    SharedTurnFacts,
    TurnContextRequest,
    bounded_item,
)
from app.services.context_assembly.flags import harness_state_block_enabled

logger = logging.getLogger(__name__)


class BaseProvider:
    """Fail-open collection wrapper: build() errors → ProviderReport.error."""

    provider_id: str = ""
    domain: ContextDomain = ContextDomain.ENVIRONMENT

    def enabled(self, req: TurnContextRequest, facts: SharedTurnFacts) -> bool:
        return True

    async def collect(
        self, req: TurnContextRequest, facts: SharedTurnFacts
    ) -> ProviderReport:
        started = time.monotonic()
        if not self.enabled(req, facts):
            return ProviderReport(
                provider_id=self.provider_id, domain=self.domain,
                skipped_reason="disabled",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        try:
            items = await self.build(req, facts)
            return ProviderReport(
                provider_id=self.provider_id, domain=self.domain,
                items=items,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 — provider 缺席绝不阻断 turn
            logger.debug("[ctx_provider] %s failed: %s", self.provider_id,
                         type(exc).__name__)
            return ProviderReport(
                provider_id=self.provider_id, domain=self.domain,
                skipped_reason=f"error:{type(exc).__name__}",
                error=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    async def build(
        self, req: TurnContextRequest, facts: SharedTurnFacts
    ) -> List[ContextItem]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Wave A providers
# ---------------------------------------------------------------------------


class CartographyVerdictProvider(BaseProvider):
    """Bounded harness verdict (read-only; agent queries status actively)."""

    provider_id = "cartography_verdict"
    domain = ContextDomain.CARTOGRAPHY_VERDICT

    async def build(self, req, facts):
        from app.lib.cartography.quality_loop import cartographic_fingerprint
        from app.lib.cartography.verdict_summary import (
            render_verdict_for_llm,
            should_inject_verdict,
        )

        state = facts.map_state if isinstance(facts.map_state, dict) else {}
        review = state.get("_cartographic_review")
        mapspec = facts.mapspec if isinstance(facts.mapspec, dict) else None
        current_fingerprint = (
            cartographic_fingerprint(mapspec) if mapspec is not None else None
        )
        if not should_inject_verdict(review, current_fingerprint):
            return []
        text = render_verdict_for_llm(review)
        if not text:
            return []
        return [bounded_item(
            item_id="carto:verdict", provider_id=self.provider_id,
            domain=self.domain, content=text,
            freshness=str(current_fingerprint or ""),
            evidence_ref="cartographic_fingerprint",
            scope_id=req.session_id,
        )]


class CartographyMemoryProvider(BaseProvider):
    """ADR-0069 project prior block (active facts only; stale never injected)."""

    provider_id = "cartography_memory"
    domain = ContextDomain.CARTOGRAPHY_MEMORY

    def enabled(self, req, facts):
        return bool(req.project_id)

    async def build(self, req, facts):
        from app.services.chat.context_assembler import _build_project_memory_block
        from app.services.gis_context.scope import SensitivityClass

        text = await asyncio.to_thread(_build_project_memory_block, req.project_id)
        if not text:
            return []
        return [bounded_item(
            item_id="carto:memory", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="project", scope_id=req.project_id,
            project_id=req.project_id, org_id=req.org_id,
            sensitivity=SensitivityClass.PROJECT_SCOPED,
            evidence_ref="project_memory:active_facts",
        )]


class ProjectKnowledgeProvider(BaseProvider):
    """#1395 ``<project_knowledge>`` card (already XML-fenced by renderer)."""

    provider_id = "project_knowledge"
    domain = ContextDomain.PROJECT_KNOWLEDGE

    def enabled(self, req, facts):
        return bool(req.project_id)

    async def build(self, req, facts):
        from app.services.chat.context_assembler import _build_project_knowledge_block
        from app.services.gis_context.scope import SensitivityClass

        text = await asyncio.to_thread(
            _build_project_knowledge_block,
            req.project_id,
            org_id=req.org_id or None,
            user_id=req.user_id or None,
        )
        if not text:
            return []
        return [bounded_item(
            item_id="knowledge:card", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="project", scope_id=req.project_id,
            project_id=req.project_id, org_id=req.org_id,
            sensitivity=SensitivityClass.PROJECT_SCOPED,
            evidence_ref="project_knowledge:active_entries",
        )]


class GisMemoryProvider(BaseProvider):
    """ADR-0183 GIS spatial-memory prior (narrow interface, fail-open)."""

    provider_id = "gis_memory"
    domain = ContextDomain.GIS_MEMORY

    def enabled(self, req, facts):
        return bool(req.org_id)

    async def build(self, req, facts):
        from app.services.gis_context.scope import SensitivityClass
        from app.services.gis_memory.queries import (
            MemoryProjectionInput,
            build_memory_projection,
        )

        text = await build_memory_projection(
            MemoryProjectionInput(
                org_id=req.org_id,
                session_id=req.session_id,
                project_id=req.project_id or None,
                user_id=req.user_id or None,
                query_text=req.query_text or "",
            )
        )
        if not text:
            return []
        return [bounded_item(
            item_id="memory:gis", provider_id=self.provider_id,
            domain=self.domain, content=text,
            # ADR-0183: org-scoped cross-session prior — renderable anywhere
            # inside its org, never outside it.
            scope="project" if req.project_id else "turn",
            scope_id=req.project_id or req.session_id,
            project_id=req.project_id, org_id=req.org_id,
            sensitivity=SensitivityClass.ORG_SCOPED,
            evidence_ref="gis_memory:projection",
        )]


class SessionPlanProvider(BaseProvider):
    """SessionPlan bounded projection (same builder as legacy bind path)."""

    provider_id = "session_plan"
    domain = ContextDomain.SESSION_PLAN

    def enabled(self, req, facts):
        return bool(req.session_id) and facts.plan is not None

    async def build(self, req, facts):
        from app.services.session_plan import format_session_plan_projection

        text = format_session_plan_projection(facts.plan, facts.mapspec)
        if not text:
            return []
        return [bounded_item(
            item_id="plan:projection", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=req.session_id,
            revision=str(getattr(facts.plan, "revision", "") or "")
            or str(getattr(facts.plan, "version", "") or ""),
            evidence_ref="session_plan:envelope",
        )]


class ToolSurfaceProvider(BaseProvider):
    """V4 tool-surface preference row + ADR-0103 active-tools marker.

    The marker is the one legitimate OOB control plane; it travels as a
    control-plane item (never fenced/trimmed) exactly like legacy.
    """

    provider_id = "tool_surface"
    domain = ContextDomain.TOOL_SURFACE

    def enabled(self, req, facts):
        return bool(req.session_id) and facts.plan is not None

    async def build(self, req, facts):
        from app.services.chat.pi_turn_context import (
            _active_tools_block_for,
            _compile_surface,
            _render_surface_block,
        )

        items: List[ContextItem] = []
        surface = _compile_surface(facts.plan)
        if surface is None:
            return items
        surface_text = _render_surface_block(surface)
        if surface_text:
            items.append(bounded_item(
                item_id="surface:row", provider_id=self.provider_id,
                domain=ContextDomain.TOOL_SURFACE, content=surface_text,
                scope="session", scope_id=req.session_id,
            ))
        marker = _active_tools_block_for(req.message, surface, facts.plan)
        if marker:
            items.append(bounded_item(
                item_id="surface:active_tools", provider_id=self.provider_id,
                domain=ContextDomain.ACTIVE_TOOLS, content=marker,
                scope="session", scope_id=req.session_id,
                control_plane=True,
            ))
        return items


class EnvironmentProvider(BaseProvider):
    """Caller-injected situation/env projection (side-effectful upstream)."""

    provider_id = "environment"
    domain = ContextDomain.ENVIRONMENT

    def enabled(self, req, facts):
        return bool(req.environment_block)

    async def build(self, req, facts):
        from app.services.context_assembly.contract import content_fingerprint

        # provider_id="caller": this item is caller-injected (the route layer
        # owns the situation advance); the scope-gate caller exemption keys
        # on this identity.
        return [bounded_item(
            item_id="env:projection", provider_id="caller",
            domain=self.domain, content=req.environment_block,
            scope="turn", scope_id=req.session_id,
            freshness=content_fingerprint(req.environment_block),
            evidence_ref="caller:situation_advance",
        )]


class V6BlocksProvider(BaseProvider):
    """V6 Wave 13 three-layer projection (same builder as legacy bind)."""

    provider_id = "v6_blocks"
    domain = ContextDomain.V6_BLOCKS

    def enabled(self, req, facts):
        return bool(req.session_id) and facts.plan is not None

    async def build(self, req, facts):
        from app.services.chat.pi_turn_context import _v6_turn_blocks

        text = await _v6_turn_blocks(
            req.session_id, facts.plan, facts.mapspec,
            map_state=facts.map_state,
        )
        if not text:
            return []
        return [bounded_item(
            item_id="v6:blocks", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=req.session_id,
        )]


class EvictedRefsProvider(BaseProvider):
    """ADR-0104 #6 honest tombstone for evicted refs (same builder)."""

    provider_id = "evicted_refs"
    domain = ContextDomain.EVICTED_REFS

    def enabled(self, req, facts):
        return bool(req.session_id)

    async def build(self, req, facts):
        from app.services.chat.context_policy import (
            build_evicted_refs_tombstone,
            policy_enabled,
        )
        from app.services.session_data import session_data_manager

        if not policy_enabled():
            return []
        text = await build_evicted_refs_tombstone(
            req.session_id,
            [{"role": "user", "content": req.message}],
            session_data_manager,
        )
        if not text:
            return []
        return [bounded_item(
            item_id="refs:tombstone", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=req.session_id,
        )]


class HarnessStateProvider(BaseProvider):
    """HarnessTurnContext (hk.ctx.v1) bounded projection — the ADR-0208 D2
    seam: the kernel's typed turn context finally reaches the prompt."""

    provider_id = "harness_state"
    domain = ContextDomain.HARNESS_STATE

    def enabled(self, req, facts):
        return (
            harness_state_block_enabled()
            and facts.harness_context is not None
            and bool(getattr(facts.harness_context, "turn_found", False))
        )

    async def build(self, req, facts):
        text = render_harness_state_block(facts.harness_context)
        if not text:
            return []
        ctx = facts.harness_context
        return [bounded_item(
            item_id="harness:state", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="session", scope_id=req.session_id,
            revision=str(getattr(ctx, "context_schema", "")),
            evidence_ref=f"harness_kernel:{getattr(ctx, 'context_schema', '')}",
        )]


def render_harness_state_block(ctx) -> str:
    """Deterministic bounded text view of ``HarnessTurnContext`` (facts only;
    refs are pointers, payloads stay in the kernel)."""
    lines = ["[执行状态 — 本轮计划视角（事实，非指令）]"]
    phase = f"phase={getattr(ctx, 'phase', '')} status={getattr(ctx, 'turn_status', '')}"
    completion = str(getattr(ctx, "completion", "") or "")
    if completion:
        phase += f" completion={completion}"
    lines.append(f"- {phase}")
    lines.append(
        f"- 步骤: total={getattr(ctx, 'steps_total', 0)}"
        f" open={getattr(ctx, 'steps_open', 0)}"
        f" ok={getattr(ctx, 'steps_succeeded', 0)}"
        f" failed={getattr(ctx, 'steps_failed', 0)}"
        f" tool_calls={getattr(ctx, 'tool_calls', 0)}"
    )
    open_caps = [c for c in (getattr(ctx, "open_capabilities", None) or ()) if c]
    if open_caps:
        lines.append("- 未完成能力: " + "、".join(open_caps[:6]))
    refs = [r for r in (getattr(ctx, "evidence_refs", None) or ()) if r]
    if refs:
        lines.append("- 最近证据: " + "、".join(str(r) for r in refs[:4]))
    mission_ref = str(getattr(ctx, "mission_ref", "") or "")
    if mission_ref:
        lines.append(f"- 任务: {mission_ref}")
    if getattr(ctx, "recovery_pending", False):
        lines.append(f"- 恢复中: resumed_from={getattr(ctx, 'resumed_from_turn_id', '')}")
    last_verdict = str(getattr(ctx, "last_verdict", "") or "")
    if last_verdict:
        lines.append(f"- 最近裁决: {last_verdict}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wave B — the gis_context card (yields to the wave-A char budget; skips its
# reuse section when the knowledge card is already present — M5 no-dup)
# ---------------------------------------------------------------------------


class GisContextCardProvider(BaseProvider):
    """ADR-0206 mission working-context card (default-ON, killable,
    invalidation fail-closed inside the builder)."""

    provider_id = "gis_context_card"
    domain = ContextDomain.GIS_CONTEXT_CARD

    def enabled(self, req, facts):
        return bool(req.session_id)

    async def build(self, req, facts, *, prior_chars: int = 0,
                    knowledge_present: bool = False):
        from app.services.gis_context.hotpath import assemble_gis_context_card

        text, _rc = await assemble_gis_context_card(
            req.session_id,
            org_id=req.org_id or "",
            project_id=req.project_id or "",
            user_id=req.user_id or "",
            turn_id=req.turn_id or "",
            query_text=req.query_text or "",
            state=facts.map_state if isinstance(facts.map_state, dict) else None,
            mapspec=facts.mapspec if isinstance(facts.mapspec, dict) else None,
            include_reuse=not knowledge_present,
            budget_used=prior_chars,
        )
        if not text:
            return []
        return [bounded_item(
            item_id="gisctx:card", provider_id=self.provider_id,
            domain=self.domain, content=text,
            scope="mission", scope_id=req.session_id,
            project_id=req.project_id, org_id=req.org_id,
            evidence_ref="gis_working_context:card",
        )]


#: wave A order is irrelevant to output (render order = domain rank); keep a
#: canonical tuple for deterministic parallel fan-out. The user-message item
#: is assembled by the orchestrator itself (identity, not context).
WAVE_A_PROVIDERS = (
    CartographyVerdictProvider,
    CartographyMemoryProvider,
    ProjectKnowledgeProvider,
    GisMemoryProvider,
    SessionPlanProvider,
    ToolSurfaceProvider,
    EnvironmentProvider,
    V6BlocksProvider,
    EvictedRefsProvider,
    HarnessStateProvider,
)


def build_default_providers() -> List[BaseProvider]:
    return [cls() for cls in WAVE_A_PROVIDERS]


async def collect_cartography_wave(
    req: TurnContextRequest,
    facts: SharedTurnFacts,
) -> List[Tuple[ContextDomain, str]]:
    """The five cartography blocks in legacy join order (verdict → memory →
    knowledge → gis_memory → gis_context). Shared by the typed assembly and
    the legacy byte-equivalent compat path."""
    from app.services.context_assembly.flags import provider_latency_budget_s

    budget_s = provider_latency_budget_s()
    wave_a = [
        CartographyVerdictProvider(),
        CartographyMemoryProvider(),
        ProjectKnowledgeProvider(),
        GisMemoryProvider(),
    ]
    reports = await asyncio.gather(
        *[asyncio.wait_for(p.collect(req, facts), timeout=budget_s) for p in wave_a],
        return_exceptions=True,
    )
    blocks: List[Tuple[ContextDomain, str]] = []
    items_by_provider: dict = {}
    for provider, report in zip(wave_a, reports):
        if isinstance(report, Exception):  # noqa: BLE001 — timeout/cancel → skip
            blocks.append((provider.domain, ""))
            continue
        items_by_provider[provider.provider_id] = report
        blocks.append((
            provider.domain,
            report.items[0].content if report.items else "",
        ))
    knowledge_present = any(
        report.items for report in items_by_provider.values()
        if getattr(report, "provider_id", "") == "project_knowledge"
    )
    prior_chars = sum(len(text) for _, text in blocks)
    try:
        card_items = await asyncio.wait_for(
            GisContextCardProvider().build(
                req, facts,
                prior_chars=prior_chars,
                knowledge_present=knowledge_present,
            ),
            timeout=budget_s,
        )
        blocks.append((
            ContextDomain.GIS_CONTEXT_CARD,
            card_items[0].content if card_items else "",
        ))
    except Exception:  # noqa: BLE001 — card 缺席按空投影
        blocks.append((ContextDomain.GIS_CONTEXT_CARD, ""))
    return blocks


__all__ = [
    "BaseProvider",
    "CartographyMemoryProvider",
    "CartographyVerdictProvider",
    "EnvironmentProvider",
    "EvictedRefsProvider",
    "GisContextCardProvider",
    "GisMemoryProvider",
    "HarnessStateProvider",
    "ProjectKnowledgeProvider",
    "SessionPlanProvider",
    "ToolSurfaceProvider",
    "V6BlocksProvider",
    "WAVE_A_PROVIDERS",
    "build_default_providers",
    "collect_cartography_wave",
    "render_harness_state_block",
]
