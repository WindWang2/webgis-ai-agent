"""ContextAssemblyReceipt (F04).

The machine-readable answer to "what entered the prompt, what didn't, and
why" — into replay/trace and logs. Contract:

- schema-versioned, bounded (item list capped in the dict projection;
  full fidelity goes to trace stages), deterministic digest: same
  identity + same item fingerprints + same decisions ⇒ same digest.
- settle-once (first-wins), mirroring the ``OutcomeRecorder`` discipline.
- digest covers *decisions and fingerprints*, never full payload text —
  receipts are safe to persist; prompts are not.
- reason codes are closed-ish strings ``<verb>:<detail>`` — no free-text
  explanations in the machine-readable face (free text goes to logs).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

RECEIPT_SCHEMA = "ctx.receipt.v1"
_MAX_ITEM_LINES = 64
_MAX_DIGEST_ITEMS = 256


class ItemDecisionLine(BaseModel):
    """One item's lifecycle in this assembly (bounded, serializable)."""

    model_config = ConfigDict(frozen=True)

    item_id: str
    provider_id: str
    domain: str
    decision: str                 # included / omitted / truncated / deduped / scope_denied
    reason_code: str = ""
    fingerprint: str = ""
    est_tokens: int = 0


class ContextAssemblyReceipt(BaseModel):
    """Assembly receipt (identity + decisions + economics + digest)."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = RECEIPT_SCHEMA
    session_id: str = ""
    turn_id: str = ""
    mode: str = "typed"                    # typed / legacy
    decision_lines: List[ItemDecisionLine] = Field(default_factory=list)
    skipped_providers: Dict[str, str] = Field(default_factory=dict)
    planned_tokens: int = 0                # allocator plan (pre-render)
    actual_tokens: int = 0                 # rendered prompt estimate (same estimator)
    pool_usage: Dict[str, int] = Field(default_factory=dict)
    usable_tokens: int = 0
    window_known: bool = True
    over_budget: bool = False
    governor: Dict[str, Any] = Field(default_factory=dict)   # plan/settle reconcile
    digest: str = ""

    def settle(self) -> "ContextAssemblyReceipt":
        """Freeze the digest (idempotent — first computation wins)."""
        if self.digest:
            return self
        return self.model_copy(update={"digest": self._compute_digest()})

    def _compute_digest(self) -> str:
        payload = {
            "schema": self.schema_version,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "mode": self.mode,
            "planned": self.planned_tokens,
            "actual": self.actual_tokens,
            "pools": dict(sorted(self.pool_usage.items())),
            "skipped": dict(sorted(self.skipped_providers.items())),
            "items": [
                [l.item_id, l.domain, l.decision, l.reason_code, l.fingerprint]
                for l in self.decision_lines[:_MAX_DIGEST_ITEMS]
            ],
        }
        canonical = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    def as_bounded_dict(self, max_items: int = _MAX_ITEM_LINES) -> Dict[str, Any]:
        """Bounded projection for logs/trace (fingerprints, never content)."""
        return {
            "schema": self.schema_version,
            "session_id": self.session_id[:48],
            "turn_id": self.turn_id[:48],
            "mode": self.mode,
            "digest": self.digest,
            "planned_tokens": self.planned_tokens,
            "actual_tokens": self.actual_tokens,
            "usable_tokens": self.usable_tokens,
            "window_known": self.window_known,
            "over_budget": self.over_budget,
            "pool_usage": dict(self.pool_usage),
            "skipped_providers": dict(self.skipped_providers),
            "governor": dict(self.governor),
            "included": sum(1 for l in self.decision_lines if l.decision == "included"),
            "omitted": sum(1 for l in self.decision_lines if l.decision.startswith("omitted")),
            "truncated": sum(1 for l in self.decision_lines if l.decision == "truncated"),
            "deduped": sum(1 for l in self.decision_lines if l.decision == "deduped"),
            "scope_denied": sum(1 for l in self.decision_lines if l.decision == "scope_denied"),
            "items": [l.model_dump() for l in self.decision_lines[:max_items]],
            "items_total": len(self.decision_lines),
        }

    def log_bounded(self, logger_: Optional[Any] = None) -> None:
        """One bounded info line per turn (observability; never raises)."""
        try:
            bound = self.as_bounded_dict(max_items=0)
            (logger_ or logger).info(
                "[ctx_assembly] mode=%s session=%s turn=%s digest=%s "
                "planned=%d actual=%d usable=%d over=%s incl=%d omit=%d "
                "trunc=%d dedup=%d denied=%d skipped=%s governor=%s",
                bound["mode"], bound["session_id"], bound["turn_id"],
                bound["digest"], bound["planned_tokens"], bound["actual_tokens"],
                bound["usable_tokens"], bound["over_budget"], bound["included"],
                bound["omitted"], bound["truncated"], bound["deduped"],
                bound["scope_denied"], bound["skipped_providers"] or "-",
                bound["governor"].get("reconcile", "-"),
            )
        except Exception:  # noqa: BLE001 — logging must never break a turn
            pass


def build_item_lines(
    *,
    included: List[Any],
    pre_items: Optional[List[Any]] = None,
    allocation_records: List[Any] = (),
    dedupe_dropped: List[Any] = (),
    scope_denied: List[Any] = (),
) -> List[ItemDecisionLine]:
    """Assemble decision lines from the pipeline stages (single place where
    stage outputs become receipt lines).

    ``pre_items`` is the pre-allocation item list; it lets omissions (items
    dropped by the allocator, hence absent from ``included``) still appear
    in the receipt with their reason codes.
    """
    lines: List[ItemDecisionLine] = []

    def _line(item: Any, decision: str, reason: str, tokens: int) -> ItemDecisionLine:
        return ItemDecisionLine(
            item_id=str(item.item_id)[:96],
            provider_id=str(item.provider_id)[:48],
            domain=str(getattr(item.domain, "value", item.domain)),
            decision=decision,
            reason_code=reason[:96],
            fingerprint=str(getattr(item, "fingerprint", ""))[:16],
            est_tokens=int(tokens),
        )

    alloc_by_item: Dict[str, Any] = {}
    omitted_records: List[Any] = []
    for rec in allocation_records:
        alloc_by_item.setdefault(rec.item_id, rec)
        if rec.decision not in ("included",):
            omitted_records.append(rec)

    included_ids = {str(i.item_id) for i in included}
    for item in included:
        rec = alloc_by_item.get(item.item_id)
        if rec is not None and rec.decision != "included":
            lines.append(_line(item, rec.decision, rec.reason_code, rec.est_tokens_after))
        else:
            lines.append(_line(item, "included", rec.reason_code if rec else "included",
                               getattr(item, "est_tokens", 0)))
    # omitted items never made it into ``included`` — recover them from
    # pre_items via the allocation record (honest omission ledger).
    if pre_items is not None:
        pre_by_id = {str(i.item_id): i for i in pre_items}
        for rec in omitted_records:
            if rec.decision.startswith("omitted") and rec.item_id in pre_by_id:
                if rec.item_id in included_ids:
                    continue
                lines.append(_line(
                    pre_by_id[rec.item_id], "omitted", rec.reason_code, 0
                ))
    for item, reason in dedupe_dropped:
        lines.append(_line(item, "deduped", reason, getattr(item, "est_tokens", 0)))
    for item, reason in scope_denied:
        lines.append(_line(item, "scope_denied", reason, getattr(item, "est_tokens", 0)))
    return lines


__all__ = [
    "RECEIPT_SCHEMA",
    "ContextAssemblyReceipt",
    "ItemDecisionLine",
    "build_item_lines",
]
