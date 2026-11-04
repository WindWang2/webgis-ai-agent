"""Typed wire records produced by bounded federation planning/execution.

These are plain dictionaries at runtime. Dataset rows and legacy predicates stay
open at their existing adapter boundaries; the planner and execution evidence
have fixed field meanings and should not discard that information as ``Any``.
"""
from __future__ import annotations

from typing import Any, Literal
from typing_extensions import NotRequired, TypedDict


class AggregateSpecRecord(TypedDict):
    func: Literal["count", "sum", "avg", "min", "max", "stddev", "distinct_count"]
    field: NotRequired[str | None]


class SourceSideRecord(TypedDict, total=False):
    source_id: str
    dataset_id: str
    chain_position: int
    fields: list[str]
    fetch_limit: int | None


class RejectedOrderRecord(TypedDict):
    order: list[str]
    cost: int


class FederatedPlanRecord(TypedDict):
    kind: str
    left: SourceSideRecord
    right: SourceSideRecord
    join_field_left: str | None
    join_field_right: str | None
    spatial_op: str | None
    group_by_right: list[str] | None
    aggregates: list[AggregateSpecRecord] | None
    strategy: str
    estimated_left_rows: int | None
    estimated_right_rows: int | None
    warnings: list[str]
    rejected_orders: NotRequired[list[RejectedOrderRecord]]


class SourceQueryOptions(TypedDict, total=False):
    limit: int
    offset: int
    deadline_s: float
    max_rows: int
    fields: list[str]
    # The legacy adapter boundary accepts an expression string or predicate AST.
    where: str | dict[str, Any] | None
    bbox: list[float]
    output_crs: str


class FeedbackDriftRecord(TypedDict):
    factor: float
    samples: int


class EstimateBasisRecord(TypedDict):
    governed: bool
    source_type: NotRequired[str | None]
    caps_basis: NotRequired[str]
    rows_basis: NotRequired[str]
    hint_feedback_drift: NotRequired[FeedbackDriftRecord]


class BloomBuildRecord(TypedDict):
    keys_added: int
    saturated: bool


class BloomTraceRecord(BloomBuildRecord):
    hop_kind: str
    reason: str


class KeysetReductionRecord(TypedDict):
    right_rows_before: int
    right_rows_after: int


class SemiJoinReductionRecord(KeysetReductionRecord, total=False):
    left_row_offset: int
    hop: int


class SemiJoinTraceRecord(TypedDict, total=False):
    bloom_filtered: int
    keyset_reduction: KeysetReductionRecord


class HopTraceRecord(SemiJoinTraceRecord):
    hop: int
    kind: str
    output_rows: int
    aggregate_pushdown: NotRequired[bool]
    joined_rows: NotRequired[int]


class CrsFallbackRecord(TypedDict):
    source_id: str
    requested: str
    delivered: str | None
    fallback: str


class CrsTransformRecord(TypedDict):
    from_crs: str
    to_crs: str
    rows: int
    placement: str


class ReprojectSideRecord(TypedDict):
    reproject: str


class JoinSideRecord(TypedDict):
    subtree: str


class UnknownSideRecord(TypedDict):
    node: str


class V6PlanRecord(TypedDict):
    kind: str
    left: SourceSideRecord | ReprojectSideRecord | JoinSideRecord | UnknownSideRecord
    right: SourceSideRecord | ReprojectSideRecord | JoinSideRecord | UnknownSideRecord
    join_field_left: str | None
    join_field_right: str | None
    spatial_op: str | None
    group_by_right: list[str] | None
    aggregates: list[AggregateSpecRecord] | None
    chain_position: int


class AggregateCell(TypedDict):
    count: int
    sum: float
    sumsq: float
    # min/max preserve arbitrary source scalar values (including Decimal/date).
    min: Any
    max: Any
    distinct: set[str]


class AggregateGroup(TypedDict):
    n: int
    cells: dict[str, AggregateCell]
