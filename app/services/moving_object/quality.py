"""Quality diagnostics and separated cleaning proposals (WP2).

Diagnostics answer "how good is this data" (counts, distributions, flags).
Cleaning proposals answer "what *could* change" — they are bounded previews
with per-operation impact counts; applying a proposal re-runs ingest under
a modified config producing a NEW observation set (new ref/version). The
raw input is never rewritten.

Rules:
- Diagnostics never mutate; proposals never claim improvements they cannot
  compute (each operation reports the exact rows it would drop/flag).
- Row-level accuracy filtering uses the declared ``accuracy_m`` property
  only; absent accuracy is reported, not imputed.
- Sampling-interval statistics are interval facts (median/p90/max gaps),
  so users can pick segmentation thresholds from data, not vibes.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.moving_object.builder import TrajectoryBuild
from app.services.moving_object.contracts import (
    IngestConfig,
    IngestDiagnostics,
    MAX_PREVIEW_FEATURES,
    ObsPoint,
)


class SamplingIntervalStats(BaseModel):
    n_intervals: int
    median_gap_s: float
    p90_gap_s: float
    max_gap_s: float
    min_gap_s: float
    zero_gap_intervals: int  # duplicate-time pairs kept by policy
    degenerate_intervals: int


class EntityQualityRow(BaseModel):
    entity_id: str
    n_observations: int
    n_segments: int
    dropped_runs: int
    degenerate_intervals: int
    sampling: Optional[SamplingIntervalStats] = None


class QualityReport(BaseModel):
    schema_version: str = "moving_object.quality.v1"
    ingest: IngestDiagnostics
    n_entities: int
    n_breaks: int
    break_reason_counts: Dict[str, int]
    entities: List[EntityQualityRow]
    overall_sampling: Optional[SamplingIntervalStats] = None
    notes: List[str] = Field(default_factory=list)


class CleaningOperation(str):
    """Supported proposal operations (one config delta each)."""

    DROP_LOW_ACCURACY = "drop_low_accuracy"      # accuracy_m > threshold
    DEDUP_KEEP_FIRST = "dedup_keep_first"        # duplicate policy switch
    DEDUP_KEEP_LAST = "dedup_keep_last"


class CleaningProposalConfig(BaseModel):
    drop_accuracy_above_m: Optional[float] = Field(None, gt=0)
    duplicate_time_policy: Optional[str] = Field(
        None, description="keep_first | keep_last (switch from current)"
    )


class CleaningProposal(BaseModel):
    """Bounded preview of a cleaning; applying it re-ingests into a new ref."""

    operations: List[str]
    rows_dropped_low_accuracy: int = 0
    rows_dropped_duplicates: int = 0
    rows_reorder_flagged: int = 0
    accepted_after: int
    accepted_before: int
    affected_entity_count: int
    #: Bounded sample of dropped rows (source_row ids) for inspection.
    dropped_row_sample: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


def _interval_stats_for(points: List[ObsPoint], degenerate_n: int) -> Optional[SamplingIntervalStats]:
    if len(points) < 2:
        return None
    gaps = [
        (b.t - a.t).total_seconds()
        for a, b in zip(points, points[1:])
    ]
    if not gaps:
        return None
    ordered = sorted(gaps)
    n = len(ordered)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2.0
    p90 = ordered[min(n - 1, int(round(0.9 * (n - 1))))]
    return SamplingIntervalStats(
        n_intervals=n,
        median_gap_s=median,
        p90_gap_s=p90,
        max_gap_s=ordered[-1],
        min_gap_s=ordered[0],
        zero_gap_intervals=sum(1 for g in gaps if g <= 0),
        degenerate_intervals=degenerate_n,
    )


def diagnose(
    points: List[ObsPoint],
    ingest_diag: IngestDiagnostics,
    build: TrajectoryBuild,
    ingest_cfg: IngestConfig,
) -> QualityReport:
    """Aggregate ingest diagnostics + build facts into one quality report."""
    pts = sorted(points, key=lambda p: (p.t, p.order))
    break_counts: Dict[str, int] = {}
    for br in build.breaks:
        break_counts[br.reason] = break_counts.get(br.reason, 0) + 1

    by_entity: Dict[str, List[ObsPoint]] = {}
    for p in pts:
        by_entity.setdefault(p.entity_id, []).append(p)

    rows: List[EntityQualityRow] = []
    all_gaps: List[float] = []
    total_degenerate = 0
    for entity_id in sorted(by_entity.keys()):
        stream = by_entity[entity_id]
        deg = build.degenerate_intervals.get(entity_id, 0)
        total_degenerate += deg
        st = _interval_stats_for(stream, deg)
        if st is not None:
            all_gaps.extend(
                (b.t - a.t).total_seconds()
                for a, b in zip(stream, stream[1:])
            )
        traj = build.trajectories.get(entity_id)
        rows.append(EntityQualityRow(
            entity_id=entity_id,
            n_observations=len(stream),
            n_segments=traj.n_segments if traj else 0,
            dropped_runs=build.dropped_runs.get(entity_id, 0),
            degenerate_intervals=deg,
            sampling=st,
        ))
    overall = None
    if all_gaps:
        ordered = sorted(all_gaps)
        n = len(ordered)
        median = ordered[n // 2] if n % 2 else (
            ordered[n // 2 - 1] + ordered[n // 2]
        ) / 2.0
        overall = SamplingIntervalStats(
            n_intervals=n,
            median_gap_s=median,
            p90_gap_s=ordered[min(n - 1, int(round(0.9 * (n - 1))))],
            max_gap_s=ordered[-1],
            min_gap_s=ordered[0],
            zero_gap_intervals=sum(1 for g in all_gaps if g <= 0),
            degenerate_intervals=total_degenerate,
        )

    notes: List[str] = list(ingest_diag.notes)
    if break_counts.get("zero_dt_conflict"):
        notes.append(
            f"{break_counts['zero_dt_conflict']} same-time positional "
            "conflicts broke segments (dt=0 with distinct positions is a "
            "data contradiction, not motion)"
        )
    if break_counts.get("speed_jump"):
        notes.append(
            f"{break_counts['speed_jump']} inter-observation moves exceeded "
            "max_speed_kmh and were segmented as jumps (sparse-sampling "
            "aliasing is disclosed, not interpolated)"
        )

    return QualityReport(
        ingest=ingest_diag,
        n_entities=len(by_entity),
        n_breaks=len(build.breaks),
        break_reason_counts=break_counts,
        entities=rows,
        overall_sampling=overall,
        notes=notes,
    )


def propose_cleaning(
    points: List[ObsPoint],
    ingest_diag: IngestDiagnostics,
    current_cfg: IngestConfig,
    proposal_cfg: CleaningProposalConfig,
) -> CleaningProposal:
    """Compute a bounded cleaning preview WITHOUT producing a new version.

    The caller applies by re-running ingest under the merged config (new
    ref); this function only quantifies the impact so the user can decide.
    """
    operations: List[str] = []
    dropped_low_accuracy = 0
    dropped_duplicates = 0
    dropped_rows: List[str] = []
    dropped_keys: set = set()
    affected_entities = set()

    if proposal_cfg.drop_accuracy_above_m is not None:
        operations.append(CleaningOperation.DROP_LOW_ACCURACY)
        for p in points:
            if p.accuracy_m is None:
                continue  # absent accuracy is reported upstream, not assumed
            if p.accuracy_m > proposal_cfg.drop_accuracy_above_m:
                dropped_low_accuracy += 1
                dropped_keys.add(p.order)
                affected_entities.add(p.entity_id)
                if len(dropped_rows) < MAX_PREVIEW_FEATURES:
                    dropped_rows.append(p.source_row_id)

    if proposal_cfg.duplicate_time_policy in ("keep_first", "keep_last"):
        operations.append(
            CleaningOperation.DEDUP_KEEP_FIRST
            if proposal_cfg.duplicate_time_policy == "keep_first"
            else CleaningOperation.DEDUP_KEEP_LAST
        )
        if current_cfg.duplicate_time_policy.value != "keep_all":
            dropped_duplicates += 0  # already deduped upstream; no-op
        else:
            pts = sorted(points, key=lambda p: (p.t, p.order))
            for a, b in zip(pts, pts[1:]):
                if a.entity_id == b.entity_id and a.t == b.t:
                    dropped_duplicates += 1
                    dropped_keys.add(b.order)
                    affected_entities.add(b.entity_id)
                    if len(dropped_rows) < MAX_PREVIEW_FEATURES:
                        dropped_rows.append(b.source_row_id)

    notes: List[str] = []
    if proposal_cfg.drop_accuracy_above_m is not None and (
        ingest_diag.accepted and dropped_low_accuracy == 0
    ):
        notes.append(
            "no carries a usable accuracy_m above the threshold — if the "
            "source lacks accuracy_m, this operation cannot act (absent "
            "accuracy is never imputed)"
        )
    notes.append(
        "proposal only previews; applying re-ingests into a NEW ref "
        "(raw input immutable)"
    )

    return CleaningProposal(
        operations=operations,
        rows_dropped_low_accuracy=dropped_low_accuracy,
        rows_dropped_duplicates=dropped_duplicates,
        rows_reorder_flagged=0,
        # Distinct-row accounting: operations may overlap on the same row.
        accepted_after=ingest_diag.accepted - len(dropped_keys),
        accepted_before=ingest_diag.accepted,
        affected_entity_count=len(affected_entities),
        dropped_row_sample=dropped_rows,
        notes=notes,
    )
