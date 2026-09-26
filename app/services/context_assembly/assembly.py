"""Turn prompt assembly orchestrator (F04).

Pipeline (all stages deterministic; every drop has a receipt reason):

1. **facts** — one coordinated fetch per turn (plan, mapspec, map_state,
   HarnessTurnContext) — the N+1 discipline: providers never re-fetch.
2. **wave A** — bounded-parallel provider collection (per-provider latency
   budget; timeout → honest skip).
3. **scope gate** — sensitivity gating via the ADR-0206 semantics; failing
   items are dropped with ``scope_denied`` (tenant isolation).
4. **fence + scrub** — marker neutralization / wrap fences / double-pass
   secret scrub per domain discipline.
5. **dedupe** — deterministic exact-content + authority-stale selection.
6. **allocate** — pool caps/floors/yield with per-item records.
7. **render** — legacy ``attach_turn_context`` order (byte-compatible);
   marker last, footer after it.
8. **receipt + governor** — digest settle-once; planned vs actual on the
   same estimator; session-ledger actuals.

Kill-switch: ``GIS_TYPED_CONTEXT_ASSEMBLY=0`` → the caller falls back to
``legacy_bind_turn_prompt`` (byte-equivalent pre-F04 path, see
``pi_turn_context``).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from app.services.context_assembly.allocator import (
    AllocationRecord,
    allocate,
    estimate_message_tokens,
)
from app.services.context_assembly.contract import (
    INLINE_JOIN_DOMAINS,
    UNTOUCHABLE_DOMAINS,
    ContextDomain,
    ContextItem,
    SharedTurnFacts,
    TurnContextRequest,
    bounded_item,
    domain_rank,
)
from app.services.context_assembly.dedupe import dedupe_items
from app.services.context_assembly.fence import apply_domain_fence, scrub_item
from app.services.context_assembly.flags import (
    max_total_context_chars,
    provider_latency_budget_s,
    provider_parallelism,
)
from app.services.context_assembly.providers import build_default_providers
from app.services.context_assembly.receipt import (
    ContextAssemblyReceipt,
    build_item_lines,
)

logger = logging.getLogger(__name__)


async def fetch_shared_facts(req: TurnContextRequest) -> SharedTurnFacts:
    """One coordinated read per turn (single I/O identity for all providers).

    plan / mapspec / map_state / HarnessTurnContext — failures land in
    ``fetch_errors`` (providers see them as absent facts, fail-open). The
    HarnessTurnContext projection reuses the already-loaded plan via the
    canonical ``build_turn_context`` (no second plan read).
    """
    from app.services.session_data import session_data_manager

    facts = SharedTurnFacts()

    async def _map_state():
        try:
            state = await session_data_manager.get_map_state(req.session_id)
            return state if isinstance(state, dict) else {}
        except Exception as exc:  # noqa: BLE001 — 缺席按空投影
            facts.fetch_errors["map_state"] = type(exc).__name__
            return {}

    async def _mapspec():
        try:
            from app.services.mapspec.store import mapspec_store_instance

            return await mapspec_store_instance.get_mapspec(req.session_id)
        except Exception as exc:  # noqa: BLE001
            facts.fetch_errors["mapspec"] = type(exc).__name__
            return None

    state, mapspec = await asyncio.gather(_map_state(), _mapspec())
    facts.map_state = state if isinstance(state, dict) else {}
    facts.mapspec = mapspec if isinstance(mapspec, dict) else None

    if req.session_id:
        try:
            from app.services.session_plan import (
                ensure_session_plan_slot,
                load_session_plan,
            )

            await ensure_session_plan_slot(req.session_id)
            facts.plan = await load_session_plan(req.session_id)
        except Exception as exc:  # noqa: BLE001
            facts.fetch_errors["plan"] = type(exc).__name__
        if facts.plan is not None:
            try:
                from app.services.harness_kernel.context import build_turn_context

                facts.harness_context = build_turn_context(
                    facts.plan, req.turn_id or ""
                )
            except Exception as exc:  # noqa: BLE001
                facts.fetch_errors["harness_context"] = type(exc).__name__
    return facts


def _scope_gate(
    items: List[ContextItem], req: TurnContextRequest
) -> Tuple[List[ContextItem], List[Tuple[ContextItem, str]]]:
    kept: List[ContextItem] = []
    denied: List[Tuple[ContextItem, str]] = []
    for item in items:
        if item.domain in UNTOUCHABLE_DOMAINS or item.control_plane:
            kept.append(item)
            continue
        if item.evidence_ref.startswith("caller:") and item.provider_id == "caller":
            # Caller-injected blocks (situation env / legacy cartography) were
            # tenant-authorized at the route layer; the item-level scope gate
            # governs provider-DERIVED items. Guarded on provider_id so a
            # future provider cannot borrow the bypass.
            kept.append(item)
            continue
        if item.renderable_in(
            org_id=req.org_id, project_id=req.project_id,
            session_id=req.session_id,
        ):
            kept.append(item)
        else:
            denied.append((
                item,
                f"scope:{item.sensitivity.value}:scope_id={item.scope_id[:32]}",
            ))
    return kept, denied


async def _collect_wave(
    req, facts, *, skip_cartography: bool = False
) -> Tuple[List[ContextItem], Dict[str, str]]:
    """Bounded-parallel wave-A collection.

    ``skip_cartography`` (F04 compat): when a caller explicitly injected a
    prebuilt cartography block, the derived cartography providers yield so
    the explicit input wins exactly once (no double injection).
    """
    sem = asyncio.Semaphore(provider_parallelism())
    budget_s = provider_latency_budget_s()
    providers = build_default_providers()
    if skip_cartography:
        providers = [
            p for p in providers
            if p.domain not in INLINE_JOIN_DOMAINS
        ]

    async def _one(provider):
        async with sem:
            return await asyncio.wait_for(
                provider.collect(req, facts), timeout=budget_s
            )

    reports = await asyncio.gather(
        *[_one(p) for p in providers], return_exceptions=True
    )
    items: List[ContextItem] = []
    skipped: Dict[str, str] = {}
    if skip_cartography:
        skipped["cartography_derived"] = "caller_injected_block"
    for provider, report in zip(providers, reports):
        if isinstance(report, Exception):  # noqa: BLE001 — timeout/cancel
            skipped[provider.provider_id] = f"timeout_or_cancel:{type(report).__name__}"
            continue
        if report.skipped_reason:
            skipped[provider.provider_id] = report.skipped_reason
        items.extend(report.items)
    return items, skipped


async def assemble_turn_context(
    req: TurnContextRequest,
    *,
    facts: Optional[SharedTurnFacts] = None,
    context_window: Optional[int] = None,
    max_output_tokens: int = 0,
) -> Tuple[str, ContextAssemblyReceipt]:
    """Assemble the final Pi turn prompt (typed path).

    Returns ``(message, receipt)``; ``message`` starts with the neutralized
    user text and ends with the turn marker + footer — byte-identical to the
    legacy path whenever no provider skips, no dedupe triggers, no pool
    pressure exists and the harness-state block flag is off.
    """

    if facts is None:
        facts = await fetch_shared_facts(req)

    # user message — identity, not context: built by the orchestrator itself,
    # neutralized against control-marker smuggling (legacy semantics) and
    # never altered further (untouchable in the allocator).
    items: List[ContextItem] = []
    if req.message:
        from app.services.context_assembly.fence import neutralize_control_markers

        items.append(bounded_item(
            item_id="user:message", provider_id="assembly",
            domain=ContextDomain.USER_MESSAGE,
            content=neutralize_control_markers(req.message),
            scope="turn", scope_id=req.session_id,
        ))

    # waves A + B (card yields to wave-A budget, same as legacy accounting);
    # explicit caller-injected cartography block suppresses the derived ones
    legacy_carto = (req.legacy_cartography_block or "").strip()
    wave_items, skipped = await _collect_wave(
        req, facts, skip_cartography=bool(legacy_carto)
    )
    items.extend(wave_items)
    if legacy_carto:
        items.append(_legacy_cartography_item(req))
    if not legacy_carto:
        # Card budget accounting mirrors pre-F04 semantics: only the sibling
        # cartography blocks count against COMBINED_BUDGET_CHARS.
        prior_chars = sum(
            len(i.content) for i in items if i.domain in INLINE_JOIN_DOMAINS
        )
        knowledge_present = any(
            i.domain is ContextDomain.PROJECT_KNOWLEDGE for i in items
        )
        try:
            from app.services.context_assembly.providers import (
                GisContextCardProvider,
            )

            card_items = await asyncio.wait_for(
                GisContextCardProvider().build(
                    req, facts,
                    prior_chars=prior_chars,
                    knowledge_present=knowledge_present,
                ),
                timeout=provider_latency_budget_s(),
            )
            items.extend(card_items)
        except Exception:  # noqa: BLE001 — card 缺席按空投影
            skipped.setdefault("gis_context_card", "error:timeout_or_missing")

    # scope gate (tenant isolation — before any content mutation)
    kept, denied = _scope_gate(items, req)

    # fence + scrub (per-domain discipline; scrub skips control plane)
    fenced = []
    for item in kept:
        item = apply_domain_fence(item)
        item = scrub_item(item)
        fenced.append(item)

    # deterministic dedupe
    deduped = dedupe_items(fenced)
    survivors = deduped.kept

    # turn marker item (control plane, always last but one)
    marker_item = _marker_item(req)

    # allocate (pool caps / floors / honest omission)
    allocation = allocate(
        survivors + [marker_item],
        context_window=(
            context_window if context_window is not None else _context_window()
        ),
        max_output_tokens=max_output_tokens or _max_output_tokens(),
        enforce=_enforce_budget(),
    )
    final_items = allocation.items
    if not any(i.item_id == marker_item.item_id for i in final_items):
        # control plane can never be dropped (defence in depth)
        final_items.append(marker_item)

    final_items, ceiling_records = _apply_char_ceiling(final_items)
    allocation.records.extend(ceiling_records)

    # render in legacy attach order (inline cartography family keeps "" join)
    message = _render_parts(req, final_items)

    # receipt + governor (planned vs actual, same estimator)
    planned = allocation.planned_tokens
    governor_plan = None
    reconcile: Dict = {}
    try:
        from app.services.context_assembly.governor_link import (
            plan_turn_context,
            settle_turn_context,
        )

        governor_plan = await plan_turn_context(
            session_id=req.session_id, turn_id=req.turn_id,
            planned_tokens=planned,
        )
        reconcile = await settle_turn_context(
            governor_plan, session_id=req.session_id,
            turn_id=req.turn_id, actual_prompt=message,
        )
    except Exception as exc:  # noqa: BLE001 — governor 缺席不阻断
        reconcile = {"ledger_recorded": False, "settle_error": type(exc).__name__}

    lines = build_item_lines(
        included=final_items,
        pre_items=survivors + [marker_item],
        allocation_records=allocation.records,
        dedupe_dropped=deduped.dropped,
        scope_denied=denied,
    )
    receipt = ContextAssemblyReceipt(
        session_id=req.session_id, turn_id=req.turn_id, mode="typed",
        decision_lines=lines, skipped_providers=skipped,
        planned_tokens=planned,
        actual_tokens=estimate_message_tokens(message),
        pool_usage=allocation.pool_usage,
        usable_tokens=allocation.usable_tokens,
        window_known=allocation.window_known,
        over_budget=allocation.over_budget,
        governor=(governor_plan.to_receipt_dict() if governor_plan else {})
        | {"reconcile": reconcile},
    ).settle()
    receipt.log_bounded()
    _record_trace(req, receipt)
    return message, receipt


def _legacy_cartography_item(req: TurnContextRequest) -> ContextItem:
    """Explicit caller-injected cartography block (compat input) as a typed
    item. Bypasses the per-domain char cap: the caller owns this text
    verbatim (pre-F04 it entered the prompt whole)."""
    from app.services.context_assembly.contract import (
        content_fingerprint,
    )

    return ContextItem(
        item_id="carto:legacy", provider_id="caller",
        domain=ContextDomain.CARTOGRAPHY_VERDICT,
        content=req.legacy_cartography_block,
        scope="turn", scope_id=req.session_id,
        freshness=content_fingerprint(req.legacy_cartography_block),
        evidence_ref="caller:legacy_cartography_block",
    )


def _apply_char_ceiling(
    items: List[ContextItem],
) -> Tuple[List[ContextItem], List[AllocationRecord]]:
    """Absolute char ceiling on context blocks (safety net; ``GIS_CONTEXT_MAX_CHARS``).

    Deterministic yield order; user message and control plane exempt. No-op
    unless the operator configured the ceiling.
    """
    ceiling = max_total_context_chars()
    if ceiling is None or not items:
        return items, []
    total_chars = sum(len(i.content) for i in items)
    if total_chars <= ceiling:
        return items, []
    records: List[AllocationRecord] = []
    kept = {id(i) for i in items}
    droppable = sorted(
        [i for i in items if i.domain not in UNTOUCHABLE_DOMAINS],
        key=lambda i: (-i.priority, -len(i.content), i.item_id),
    )
    for item in droppable:
        if total_chars <= ceiling:
            break
        total_chars -= len(item.content)
        kept.discard(id(item))
        records.append(AllocationRecord(
            item_id=item.item_id, provider_id=item.provider_id,
            domain=item.domain, decision="omitted",
            reason_code=f"omitted:total_char_cap:{total_chars + len(item.content)}>{ceiling}",
            est_tokens_before=item.est_tokens, est_tokens_after=0,
        ))
    return [i for i in items if id(i) in kept], records


def _marker_item(req: TurnContextRequest) -> ContextItem:
    from app.services.chat.pi_turn_context import TURN_CONTEXT_MARKER

    content = (
        f"[{TURN_CONTEXT_MARKER}:{req.token}]\n\n"
        "(Internal routing context; do not quote or modify this marker.)"
    )
    from app.services.context_assembly.contract import bounded_item

    return bounded_item(
        item_id="ctrl:turn_marker", provider_id="assembly",
        domain=ContextDomain.TURN_MARKER, content=content,
        scope="turn", scope_id=req.session_id, control_plane=True,
    )


def _render_parts(req: TurnContextRequest, items: List[ContextItem]) -> str:
    """Legacy ``attach_turn_context`` order with byte-identical joins:
    neutralized message → blocks in rank order (cartography family joined
    inline) → active tools → marker → footer."""

    def sort_key(item: ContextItem) -> tuple:
        return (domain_rank(item.domain), item.priority, item.item_id)

    ordered = sorted(items, key=sort_key)
    parts: List[str] = []
    inline_buffer: List[str] = []
    for item in ordered:
        if item.domain in INLINE_JOIN_DOMAINS:
            inline_buffer.append(item.content)
            continue
        if inline_buffer:
            parts.append("".join(inline_buffer))
            inline_buffer = []
        parts.append(item.content)
    if inline_buffer:
        parts.append("".join(inline_buffer))
    return "\n\n".join(p for p in parts if p)


def _record_trace(req: TurnContextRequest, receipt: ContextAssemblyReceipt) -> None:
    """Replay/trace integration: a content-addressed ``decision_record``
    (ADR-0212) per assembly, logged as one bounded stable line. The
    ``gis_trace`` Stage vocabulary (1-18) is closed and F09-owned, so the
    receipt travels by digest + decision id until a stage row is added
    upstream."""
    try:
        from app.lib.runtime.decision_record import decision_record

        bounded = receipt.as_bounded_dict(max_items=8)
        reasons = [
            f"{line.decision}:{line.reason_code}"
            for line in receipt.decision_lines
            if line.decision != "included"
        ][:8]
        record = decision_record(
            "context_assembly",
            selected=receipt.digest,
            reason_codes=reasons,
            inputs=bounded,
            evidence_refs=[f"ctx_receipt:{receipt.digest}"],
            policy_version="ctx.assembly.v1",
        )
        logger.info(
            "[ctx_assembly] decision=%s kind=%s digest=%s session=%s turn=%s",
            record.get("decision_id", ""),
            record.get("kind", "context_assembly"),
            receipt.digest[:16],
            req.session_id[:24], req.turn_id[:24],
        )
    except Exception:  # noqa: BLE001 — trace 缺席不影响 turn
        logger.debug("[ctx_assembly] decision record unavailable")


def _context_window() -> Optional[int]:
    try:
        from app.core.config import settings

        return getattr(settings, "LLM_CONTEXT_WINDOW", None)
    except Exception:  # noqa: BLE001 — 配置缺席按未知窗口
        return None


def _max_output_tokens() -> int:
    try:
        from app.core.config import settings

        return int(getattr(settings, "LLM_MAX_TOKENS", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _enforce_budget() -> bool:
    from app.services.context_assembly.flags import enforce_mode

    return enforce_mode()


__all__ = [
    "assemble_turn_context",
    "fetch_shared_facts",
]
