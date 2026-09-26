"""Context budget allocator (F04).

Turns ``plan_budget``'s deterministic planning (single truth:
``app/services/chat/context_budget.py``) into *enforcement* for the Pi turn
path, with honest omission instead of random truncation:

- **Pools** — every domain maps onto an existing ``Category`` pool (no new
  category; F04 adds no second planning truth). Pool cap = the plan's
  category budget (``GIS_SECTION_CAPS`` fractions) or the item's own hard
  limit.
- **Floors** — floor-protected domains (verdict / plan / environment) are
  truncated to their floor under pressure, never silently dropped.
- **Untouchable** — user message and control-plane markers are never
  touched.
- **Yield order** — within a pool, lowest value-density yields first:
  higher ``priority`` number, then bigger cost, then stable ``item_id``.
  Across pools the same rule applies once pools are at cap and the global
  usable budget is still exceeded.
- **window_unknown honesty** — when the context window is unconfigured the
  plan falls back to the conservative 8k window; pool caps still apply,
  but a total-overrun is reported as ``window_unknown`` instead of a
  violation (ADR-0104 #6(h) discipline).

Same inputs → same allocations (no clock, no RNG, no dict ordering).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.services.chat.context_budget import plan_budget
from app.services.chat.context.history_compression import _estimate_tokens
from app.services.context_assembly.contract import (
    UNTOUCHABLE_DOMAINS,
    ContextDomain,
    ContextItem,
    Pool,
    domain_pool,
    domain_rank,
)

#: receipt reason codes
REASON_INCLUDED = "included"
REASON_OMITTED_POOL_CAP = "omitted:pool_cap"
REASON_OMITTED_GLOBAL_CAP = "omitted:global_cap"
REASON_TRUNCATED_FLOOR = "truncated:floor"
REASON_TRUNCATED_MARKER = "truncated:safety_cap"
REASON_WINDOW_UNKNOWN = "window_unknown"


@dataclass
class AllocationRecord:
    """One item's allocation outcome (receipt input)."""

    item_id: str
    provider_id: str
    domain: ContextDomain
    decision: str
    reason_code: str
    est_tokens_before: int
    est_tokens_after: int


@dataclass
class AllocationResult:
    items: List[ContextItem] = field(default_factory=list)
    records: List[AllocationRecord] = field(default_factory=list)
    planned_tokens: int = 0
    pool_usage: Dict[str, int] = field(default_factory=dict)
    usable_tokens: int = 0
    window_known: bool = True
    over_budget: bool = False


def _yield_key(item: ContextItem) -> tuple:
    """Yield-first ordering: least important, most expensive, stable id."""
    return (-item.priority, -item.est_tokens, item.item_id)


def _truncate_content(item: ContextItem, floor_tokens: int) -> ContextItem:
    """Char-bound truncation aimed at ~floor tokens (CJK-aware approximation:
    1 token ≈ 1.5 CJK chars ⇒ chars ≈ tokens × 1.33 conservative)."""
    approx_chars = max(16, int(floor_tokens * 1.33))
    content = item.content
    if len(content) <= approx_chars:
        return item
    head = content[:approx_chars].rstrip()
    return item.trimmed_copy(head + " …[已按预算截断]")


def allocate(
    items: List[ContextItem],
    *,
    context_window: Optional[int],
    max_output_tokens: int,
    enforce: bool = False,
) -> AllocationResult:
    """Allocate items into pools under the deterministic plan.

    ``enforce`` semantics (observe-first discipline):
    - caps bite when the caller forces enforcement OR the context window is
      actually configured (``window_known``);
    - with an unknown window and no explicit enforcement the allocator runs
      observe-only: pool overruns are recorded as ``pool_over`` reasons but
      nothing is omitted — the pre-F04 default path must not start dropping
      blocks based on a conservative 8k imaginary window.
    """
    plan = plan_budget(
        context_window=context_window or 0,
        max_output_tokens=max_output_tokens,
    )
    effective_enforce = bool(enforce) or plan.window_known
    result = AllocationResult(
        usable_tokens=plan.usable,
        window_known=plan.window_known,
    )

    touchable = [i for i in items if i.domain not in UNTOUCHABLE_DOMAINS]
    untouchable = [i for i in items if i.domain in UNTOUCHABLE_DOMAINS]

    pool_used: Dict[Pool, int] = {}
    accepted: Dict[int, ContextItem] = {}
    records: List[AllocationRecord] = []

    # Phase 1 — untouchable items always pass (their cost still counts).
    for item in untouchable:
        accepted[id(item)] = item
        pool_used[domain_pool(item.domain)] = (
            pool_used.get(domain_pool(item.domain), 0) + item.est_tokens
        )

    if not effective_enforce:
        # Observe pass: everything flows (pre-F04 parity), overruns recorded.
        for item in sorted(touchable, key=lambda i: (domain_rank(i.domain), i.item_id)):
            pool = domain_pool(item.domain)
            cap = plan.category_budgets.get(pool.name)
            used = pool_used.get(pool, 0)
            pool_used[pool] = used + item.est_tokens
            reason = REASON_INCLUDED
            if cap is not None and used + item.est_tokens > cap:
                reason = f"pool_over:{pool.name}:{used + item.est_tokens}>{cap}"
            accepted[id(item)] = item
            records.append(AllocationRecord(
                item_id=item.item_id, provider_id=item.provider_id,
                domain=item.domain, decision="included",
                reason_code=reason,
                est_tokens_before=item.est_tokens,
                est_tokens_after=item.est_tokens,
            ))
        final_items = [i for i in items]
        result.items = final_items
        result.records = records
        result.planned_tokens = sum(i.est_tokens for i in final_items)
        result.pool_usage = {p.name: int(v) for p, v in pool_used.items()}
        result.over_budget = False
        if not plan.window_known:
            result.records.append(AllocationRecord(
                item_id="(plan)", provider_id="allocator",
                domain=ContextDomain.USER_MESSAGE, decision="observed",
                reason_code=REASON_WINDOW_UNKNOWN,
                est_tokens_before=0, est_tokens_after=0,
            ))
        return result

    # Phase 2 — pool caps, yield order within pool (most important consumed
    # first; ``_yield_key`` sorts yield-first, so consume in reverse).
    for item in sorted(touchable, key=_yield_key, reverse=True):
        pool = domain_pool(item.domain)
        cap = plan.category_budgets.get(pool.name)
        used = pool_used.get(pool, 0)
        est = item.est_tokens
        room = (cap - used) if cap is not None else None
        if room is None or est <= room:
            accepted[id(item)] = item
            pool_used[pool] = used + est
            continue
        floor = item.floor_tokens
        if floor > 0 and est > floor:
            # Floor-protected contract: keep a bounded core even under pool
            # pressure (honest truncation, receipt-recorded) — never dropped.
            trimmed = _truncate_content(item, floor)
            accepted[id(item)] = trimmed
            pool_used[pool] = used + trimmed.est_tokens
            records.append(AllocationRecord(
                item_id=item.item_id, provider_id=item.provider_id,
                domain=item.domain, decision="truncated",
                reason_code=REASON_TRUNCATED_FLOOR,
                est_tokens_before=est, est_tokens_after=trimmed.est_tokens,
            ))
            continue
        records.append(AllocationRecord(
            item_id=item.item_id, provider_id=item.provider_id,
            domain=item.domain, decision="omitted",
            reason_code=f"{REASON_OMITTED_POOL_CAP}:{pool.name}:"
                        f"{used + est}>{cap}",
            est_tokens_before=est, est_tokens_after=0,
        ))

    # Phase 3 — global usable cap (only when the window is known; honest
    # omission in deterministic yield order, floors still win first because
    # floor-trimmed items are already small).
    total = sum(i.est_tokens for i in accepted.values())
    if plan.window_known:
        for item in sorted(
            [i for i in accepted.values() if i.domain not in UNTOUCHABLE_DOMAINS],
            key=_yield_key,
        ):
            if total <= plan.usable:
                break
            pool = domain_pool(item.domain)
            floor = item.floor_tokens
            if floor > 0:
                trimmed = _truncate_content(item, floor)
                total -= item.est_tokens - trimmed.est_tokens
                accepted[id(item)] = trimmed
                pool_used[domain_pool(item.domain)] = (
                    pool_used.get(domain_pool(item.domain), 0)
                    - item.est_tokens + trimmed.est_tokens
                )
                records.append(AllocationRecord(
                    item_id=item.item_id, provider_id=item.provider_id,
                    domain=item.domain, decision="truncated",
                    reason_code=REASON_TRUNCATED_FLOOR,
                    est_tokens_before=item.est_tokens,
                    est_tokens_after=trimmed.est_tokens,
                ))
                if total <= plan.usable:
                    break
                continue
            over_at_decision = total
            total -= item.est_tokens
            accepted.pop(id(item), None)
            pool_used[domain_pool(item.domain)] = max(
                0, pool_used.get(domain_pool(item.domain), 0) - item.est_tokens
            )
            records.append(AllocationRecord(
                item_id=item.item_id, provider_id=item.provider_id,
                domain=item.domain, decision="omitted",
                reason_code=f"{REASON_OMITTED_GLOBAL_CAP}:"
                            f"{over_at_decision}>{plan.usable}",
                est_tokens_before=item.est_tokens, est_tokens_after=0,
            ))
        result.over_budget = total > plan.usable
    else:
        # ADR-0104 #6(h): unknown window → report, never assert violation.
        result.over_budget = False

    # Records for untouched items (stable, deterministic).
    for item in items:
        final = accepted.get(id(item))
        if final is None:
            continue
        if not any(r.item_id == item.item_id for r in records):
            records.append(AllocationRecord(
                item_id=item.item_id, provider_id=item.provider_id,
                domain=item.domain, decision="included",
                reason_code=REASON_INCLUDED,
                est_tokens_before=item.est_tokens,
                est_tokens_after=final.est_tokens,
            ))

    # Preserve the caller's original order (render order is rank-driven and
    # must not depend on allocation order).
    final_items = [accepted[id(i)] for i in items if id(i) in accepted]

    result.items = final_items
    result.records = records
    result.planned_tokens = sum(i.est_tokens for i in final_items)
    result.pool_usage = {p.name: int(v) for p, v in pool_used.items()}
    if not plan.window_known:
        result.records.append(AllocationRecord(
            item_id="(plan)", provider_id="allocator",
            domain=ContextDomain.USER_MESSAGE, decision="observed",
            reason_code=REASON_WINDOW_UNKNOWN,
            est_tokens_before=0, est_tokens_after=0,
        ))
    return result


def estimate_message_tokens(message: str) -> int:
    """Single estimator exposed for planned/actual symmetry."""
    return _estimate_tokens(message) if message else 0


__all__ = [
    "AllocationRecord",
    "AllocationResult",
    "allocate",
    "estimate_message_tokens",
]
