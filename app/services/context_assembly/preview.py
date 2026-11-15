"""Side-effect-free turn-context preview (G43, ADR-0295, WP06).

Answers「下一轮会带哪些资料、为什么」**without** advancing any world
state. The REAL assembly (``assemble_turn_context``) is side-effectful by
design (SessionPlan slot creation, knowledge-liveness demotion commits,
situation snapshot advance, SurfaceReceipt minting) — the preview therefore
never calls it. Instead it runs the same single-render-source builders over
read-only facts:

- SessionPlan: load-only (no ``ensure_session_plan_slot``) — absent plan ⇒
  plan-dependent domains honestly ``not_previewed``.
- Project knowledge: ``read_only=True`` card load (liveness computed, no
  demotion commit, no db.commit()).
- Environment / situation: caller-injected by design ⇒ ``not_previewed``
  (compiling it would advance the snapshot).
- Active-tools marker: control plane, mints receipts ⇒ ``not_previewed``.
- gis_context card: ADR-0206 builder owns invalidation writes ⇒ ``not_previewed``.

The preview is explicitly NOT a send receipt: it reports the projection at
``generated_at`` with the source versions it saw, and the UI labels it as
preview. The real turn re-reads authorities and re-validates scope; any
drift is the honest diff between preview and actual receipt (C06/C14).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.chat.context.history_compression import _estimate_tokens
from app.services.context_assembly.contract import (
    SharedTurnFacts,
    TurnContextRequest,
)

logger = logging.getLogger(__name__)

#: domains intentionally excluded from preview (side-effectful or control
#: plane) — reported honestly instead of being fabricated.
NOT_PREVIEWED_DOMAINS = (
    "environment",
    "tool_surface",
    "active_tools",
    "gis_context_card",
    "session_plan",      # plan may exist; preview reports it only if loadable read-only
)


async def _readonly_facts(req: TurnContextRequest) -> SharedTurnFacts:
    """Read-only variant of ``fetch_shared_facts`` (no slot creation, no
    policy-load failures blocking, no harness-context build side doors)."""
    facts = SharedTurnFacts()
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(req.session_id)
        facts.map_state = state if isinstance(state, dict) else {}
    except Exception as exc:  # noqa: BLE001
        facts.fetch_errors["map_state"] = type(exc).__name__
        facts.map_state = {}
    try:
        from app.services.mapspec.store import mapspec_store_instance

        spec = await mapspec_store_instance.get_mapspec(req.session_id)
        facts.mapspec = spec if isinstance(spec, dict) else None
    except Exception as exc:  # noqa: BLE001
        facts.fetch_errors["mapspec"] = type(exc).__name__
    # plan: load-only (create-slot is a side effect; absent plan stays None)
    if req.session_id:
        try:
            from app.services.session_plan import load_session_plan

            facts.plan = await load_session_plan(req.session_id)
        except Exception as exc:  # noqa: BLE001
            facts.fetch_errors["plan"] = type(exc).__name__
        if facts.plan is not None:
            facts.source_session_id = str(
                getattr(facts.plan, "session_id", "") or ""
            )
        if not facts.source_session_id:
            facts.source_session_id = req.session_id
    return facts


async def preview_turn_context(
    *,
    session_id: str,
    org_id: str = "",
    project_id: str = "",
    user_id: str = "",
    message_draft: str = "",
    query_text: str = "",
    policy: Optional[Any] = None,
) -> Dict[str, Any]:
    """Project the optional-source slice of the next turn (read-only)."""
    from app.services.context_assembly.providers import (
        CartographyMemoryProvider,
        GisMemoryProvider,
        ProjectKnowledgeProvider,
    )

    if policy is None:
        try:
            from app.services.turn_context_store import load_policy

            policy = await load_policy(session_id)
        except Exception:  # noqa: BLE001 — 策略读失败 = auto 预览
            policy = None

    req = TurnContextRequest(
        session_id=session_id or "",
        message=message_draft or "",
        org_id=org_id or "",
        project_id=project_id or "",
        user_id=user_id or "",
        query_text=query_text or message_draft or "",
    )
    facts = await _readonly_facts(req)
    facts.source_policy = policy
    # review P1：只读投影模式 —— provider 写面（知识 demotion commit）被
    # 跳过，预览前后权威行零变化。
    facts.preview_mode = True

    providers = [
        CartographyMemoryProvider(),
        ProjectKnowledgeProvider(),
        GisMemoryProvider(),
    ]
    sources: List[Dict[str, Any]] = []
    blocks: List[Dict[str, Any]] = []
    for provider in providers:
        if not provider.enabled(req, facts):
            blocks.append({
                "domain": str(provider.domain.value),
                "available": False,
                "reason": "disabled_or_no_scope",
                "est_tokens": 0,
            })
            continue
        try:
            report = await provider.collect(req, facts)
        except Exception as exc:  # noqa: BLE001 — 单 provider 失败不炸预览
            blocks.append({
                "domain": str(provider.domain.value),
                "available": False,
                "reason": f"error:{type(exc).__name__}",
                "est_tokens": 0,
            })
            continue
        for ref in report.source_refs:
            sources.append(dict(ref))
        text = "".join(i.content for i in report.items)
        blocks.append({
            "domain": str(provider.domain.value),
            "available": bool(report.items),
            "reason": report.skipped_reason or "",
            "est_tokens": sum(i.est_tokens for i in report.items),
            "chars": len(text),
        })

    included_tokens = sum(b["est_tokens"] for b in blocks)
    message_tokens = _estimate_tokens(message_draft) if message_draft else 0
    return {
        "schema": "ctx.preview.v1",
        "session_id": session_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_preview": True,
        "disclaimer": (
            "预览不是发送回执：实际装配会重新读取权威源并复验权限/预算，"
            "结果可能与预览不同（差异会在真实回执中说明）。"
        ),
        "policy": policy.as_bounded_dict() if policy is not None else None,
        "blocks": blocks,
        "sources": sources[:96],
        "estimate": {
            "optional_priors_tokens": included_tokens,
            "message_draft_tokens": message_tokens,
            "note": "tokens 为同一估算器的渲染估算，不是供应商计费。",
        },
        "not_previewed": list(NOT_PREVIEWED_DOMAINS),
    }


__all__ = ["NOT_PREVIEWED_DOMAINS", "preview_turn_context"]
