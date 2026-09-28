"""Recompute orchestration for the situational memory graph (H09).

When the invalidation walk stales derived findings, this module re-derives
them *under the new basis* — bounded, priority-ordered, and generation-
fenced:

- **Plan**: stale findings ordered by (priority desc, family, finding_id),
  capped per turn (resource bound — recompute is best-effort, never a turn
  blocker).
- **Execute**: a family-keyed executor re-derives the finding from live
  state. Executors are deterministic and read-only on the world; the
  engine owns all writes.
- **Publish (atomic)**: the fresh result replaces the stale row in place —
  new generation (the context revision it was published under), fresh
  anchors, ``stale_reasons`` cleared. A failure of any check leaves the
  finding stale: stale is never treated as current.
- **Fencing** (the race the DoD names: the user keeps editing while a
  recompute is in flight): every result is re-checked against *live*
  anchors at publish time via the ``live_anchor`` resolver — if the world
  advanced past the anchor the derivation used, the result is refused
  (``superseded_by_newer_world``) and never published. The caller's
  ``store.save(expected_revision=…)`` CAS is the second fence: a save that
  loses to a concurrent writer is rebased by the store, never overwrites
  it. Old generations can therefore never trample newer decisions.

Every attempt appends a ``FINDING_RECOMPUTED`` receipt (the ADR-0215
receipt ring, one new closed kind) — why a recompute happened (the stale
attribution), what evidence re-derived the result, or exactly which check
refused it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from app.services.gis_context.working_context import (
    MAX_EDGES,
    MAX_STALE_ATTR,
    DependencyEdge,
    DerivedFinding,
    GISWorkingContext,
    RevalidationReceipt,
)

KIND_RECOMPUTE = "FINDING_RECOMPUTED"

REJECT_NO_EXECUTOR = "no_executor"
REJECT_NOT_STALE = "not_stale"
REJECT_TARGET_GONE = "target_gone"
REJECT_EXECUTOR_ERROR = "executor_error"
REJECT_EXECUTOR_REFUSED = "executor_refused"
REJECT_SUPERSEDED_BY_NEWER_WORLD = "superseded_by_newer_world"

#: Per-turn resource bound — recompute is bounded work, not a batch job.
MAX_TASKS_PER_TURN = 4

#: A publish is only attempted when the executor's anchors still match the
#: live world (resolver contract: ``live_anchor(dim, ref) -> token``; empty
#: = unknown, which never refuses — fail-open on unknown, fail-closed on
#: known mismatch).
LiveAnchorResolver = Callable[[str, str], str]


@dataclass
class RecomputeTask:
    """One stale derived finding to re-derive, with its causal evidence."""

    finding_id: str
    family: str
    ref: str
    priority: int
    reasons: List[str] = field(default_factory=list)


@dataclass
class RecomputeResult:
    """Executor output. ``edges`` carry the *fresh* anchors the derivation
    actually used — the engine re-checks them against live state before
    publishing."""

    ok: bool
    reason: str = ""
    label: str = ""
    detail: str = ""
    digest: str = ""
    priority: Optional[int] = None
    edges: List[DependencyEdge] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    #: True when the recompute legitimately found the subject gone — the
    #: row is retired as ``superseded`` (terminal), not kept stale.
    superseded: bool = False


class RecomputeExecutor(Protocol):
    """Family-keyed deterministic re-derivation (read-only on the world)."""

    family: str

    def __call__(
        self, wc: GISWorkingContext, task: RecomputeTask, *, ctx: Dict[str, Any]
    ) -> RecomputeResult:  # pragma: no cover - protocol
        ...


@dataclass
class RecomputeOutcome:
    receipts: List[RevalidationReceipt] = field(default_factory=list)
    recomputed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)   # "id:reason"
    superseded: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.recomputed or self.superseded)


def plan_recompute(
    wc: GISWorkingContext,
    *,
    max_tasks: int = MAX_TASKS_PER_TURN,
    families: Optional[set] = None,
) -> List[RecomputeTask]:
    """Stale derived findings → bounded, priority-ordered task list.

    ``families`` restricts planning to families an executor exists for —
    a family without a production executor keeps its stale row (surfaced
    by the card as recompute-owed) instead of flooding the receipt ring
    with no-executor rejections every turn.
    """
    stale = [
        f for f in wc.derived_findings
        if f.status == "stale" and (families is None or f.family in families)
    ]
    stale.sort(key=lambda f: (-int(f.priority), f.family, f.finding_id))
    tasks: List[RecomputeTask] = []
    for f in stale[: max(0, int(max_tasks))]:
        tasks.append(RecomputeTask(
            finding_id=f.finding_id, family=f.family, ref=f.ref,
            priority=int(f.priority),
            reasons=[str(r)[:96] for r in f.stale_reasons[:MAX_STALE_ATTR]],
        ))
    return tasks


def execute_recompute(
    wc: GISWorkingContext,
    tasks: List[RecomputeTask],
    *,
    executors: Dict[str, RecomputeExecutor],
    ctx: Optional[Dict[str, Any]] = None,
    turn_id: str = "",
    live_anchor: Optional[LiveAnchorResolver] = None,
) -> RecomputeOutcome:
    """Run the plan and publish atomically, fenced against a concurrently
    advancing world. Only the engine writes; executors only derive."""
    outcome = RecomputeOutcome()
    run_ctx = dict(ctx or {})
    for task in tasks[: max(0, MAX_TASKS_PER_TURN)]:
        finding = wc.derived_finding(task.finding_id)
        if finding is None:
            continue
        if finding.status != "stale":
            outcome.receipts.append(_receipt(
                wc, task, verdict="rejected", reason=REJECT_NOT_STALE,
                turn_id=turn_id, record=False))
            continue
        executor = executors.get(task.family)
        if executor is None:
            outcome.receipts.append(_receipt(
                wc, task, verdict="rejected", reason=REJECT_NO_EXECUTOR,
                turn_id=turn_id))
            outcome.failed.append(f"{task.finding_id}:{REJECT_NO_EXECUTOR}")
            continue
        try:
            result = executor(wc, task, ctx=run_ctx)
        except Exception as exc:  # noqa: BLE001 — executor failure keeps stale
            outcome.receipts.append(_receipt(
                wc, task, verdict="rejected",
                reason=f"{REJECT_EXECUTOR_ERROR}:{type(exc).__name__}"[:48],
                turn_id=turn_id))
            outcome.failed.append(f"{task.finding_id}:{REJECT_EXECUTOR_ERROR}")
            continue
        if not result.ok:
            reason = result.reason or REJECT_EXECUTOR_REFUSED
            outcome.receipts.append(_receipt(
                wc, task, verdict="rejected", reason=reason, turn_id=turn_id,
                evidence=result.evidence))
            outcome.failed.append(f"{task.finding_id}:{reason[:48]}")
            continue

        if result.superseded:
            # Subject legitimately gone — retire the row (terminal), never
            # leave a zombie stale entry pointing at nothing.
            wc.revision = int(wc.revision) + 1
            finding.status = "superseded"
            finding.generation = int(wc.revision)
            finding.basis_revision = int(wc.revision)
            outcome.receipts.append(_receipt(
                wc, task, verdict="rejected", reason=REJECT_TARGET_GONE,
                turn_id=turn_id, evidence=result.evidence))
            outcome.superseded.append(task.finding_id)
            continue

        # Publish fence: the anchors the derivation used must still equal
        # the live world. A user edit (or any advance) between derivation
        # and publish refuses the result — the old generation never wins.
        drifted = _first_drifted_anchor(result.edges, live_anchor)
        if drifted is not None:
            dim, ref = drifted
            outcome.receipts.append(_receipt(
                wc, task, verdict="rejected",
                reason=f"{REJECT_SUPERSEDED_BY_NEWER_WORLD}:{dim}:{ref}"[:64],
                turn_id=turn_id, evidence=result.evidence))
            outcome.failed.append(
                f"{task.finding_id}:{REJECT_SUPERSEDED_BY_NEWER_WORLD}")
            continue

        wc.revision = int(wc.revision) + 1
        finding.status = "current"
        finding.generation = int(wc.revision)
        finding.basis_revision = int(wc.revision)
        finding.depends_on = list(result.edges)[:MAX_EDGES]
        if result.label:
            finding.label = str(result.label)[:120]
        if result.detail:
            finding.detail = str(result.detail)[:160]
        if result.digest:
            finding.digest = str(result.digest)[:96]
        if result.priority is not None:
            finding.priority = max(0, min(3, int(result.priority)))
        finding.stale_reasons = []
        receipt = _receipt(
            wc, task, verdict="restored", turn_id=turn_id,
            evidence=result.evidence)
        receipt.checks = receipt.checks[:2]
        outcome.receipts.append(receipt)
        outcome.recomputed.append(task.finding_id)
    return outcome


def _first_drifted_anchor(
    edges: List[DependencyEdge], live_anchor: Optional[LiveAnchorResolver]
) -> Optional[tuple]:
    """First (dim, ref) whose fresh anchor no longer matches live state."""
    if live_anchor is None:
        return None
    for edge in edges[:MAX_EDGES]:
        if edge.dim not in ("data", "mapspec") or not edge.anchor:
            continue
        try:
            live = str(live_anchor(edge.dim, edge.ref) or "")
        except Exception:  # noqa: BLE001 — resolver failure is not a refusal
            continue
        if not live:
            continue  # unknown never refuses (fail-open on unknown)
        if live != edge.anchor:
            return (edge.dim, edge.ref)
    return None


def _receipt(
    wc: GISWorkingContext,
    task: RecomputeTask,
    *,
    verdict: str,
    reason: str = "",
    turn_id: str = "",
    evidence: Optional[List[str]] = None,
    record: bool = True,
) -> RevalidationReceipt:
    from app.services.gis_context.working_context import CheckResult, EvidenceRef

    receipt = RevalidationReceipt(
        receipt_id="",
        kind=KIND_RECOMPUTE,
        target=str(task.finding_id)[:64],
        basis_revision=int(wc.revision),
        verdict=verdict,
        reject_reason=str(reason)[:48],
        prior_reason=";".join(task.reasons)[:96],
        turn_id=str(turn_id or "")[:64],
        evidence=[
            EvidenceRef(ref="reason", token=str(e)[:96]) for e in (evidence or [])[:4]
        ],
        checks=[CheckResult(check="recompute", verdict=verdict, detail=str(reason)[:96])],
    )
    if record:
        return wc.append_receipt(receipt)
    receipt.receipt_id = "rtv-passive"
    return receipt


__all__ = [
    "KIND_RECOMPUTE",
    "MAX_TASKS_PER_TURN",
    "RecomputeExecutor",
    "RecomputeOutcome",
    "RecomputeResult",
    "RecomputeTask",
    "REJECT_EXECUTOR_ERROR",
    "REJECT_EXECUTOR_REFUSED",
    "REJECT_NOT_STALE",
    "REJECT_NO_EXECUTOR",
    "REJECT_SUPERSEDED_BY_NEWER_WORLD",
    "REJECT_TARGET_GONE",
    "execute_recompute",
    "plan_recompute",
]
