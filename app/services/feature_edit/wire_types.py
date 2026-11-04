"""Wire contracts for the feature-edit journal and bounded evidence.

These are dictionaries deliberately: persistence, copy-on-write and unknown
extension keys retain their existing runtime semantics. JSON values are open
only at dataset attribute values and finding details, not at record structure.
Raw dataset payloads and unvalidated operations remain boundary dictionaries.
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from pydantic import JsonValue
from app.contracts.geojson import GeoJSONGeometry


class EditFinding(TypedDict):
    code: str
    message: str
    detail: NotRequired[dict[str, JsonValue]]
    wfid: NotRequired[str]


FieldProfileRecord = TypedDict(
    "FieldProfileRecord",
    {"name": str, "types": dict[str, int], "enum": NotRequired[list[str]]},
)


class FieldChange(TypedDict):
    before: JsonValue
    after: JsonValue
    before_absent: NotRequired[bool]
    after_absent: NotRequired[bool]


class FeatureDelta(TypedDict):
    wfid: str | None
    fields: NotRequired[dict[str, FieldChange]]
    # Geometry dictionaries originate at the Shapely / raw GeoJSON boundary.
    geometry_before: NotRequired[GeoJSONGeometry | None]
    geometry_after: NotRequired[GeoJSONGeometry | None]


RenameField = TypedDict(
    "RenameField", {"op": Literal["rename_field"], "from": str | None, "to": str | None}
)


class SnapSummary(TypedDict, total=False):
    snapped: int
    snap_distances_m: dict[str, float]
    tolerance_m: float


OperationSummary = TypedDict(
    "OperationSummary",
    {
        "op": str,
        "fields": list[str],
        "from": str | None,
        "to": str | None,
        "fixes": list[str],
        "dx_m": float | None,
        "dy_m": float | None,
        "snapped": int,
        "tolerance_m": float,
    },
    total=False,
)


class JournalOperationSummary(TypedDict, total=False):
    summary: str
    ops: list[OperationSummary]
    materialized: bool
    plan_id: str
    redo_of_seq: int | None


class JournalEntry(TypedDict, total=False):
    kind: Literal["apply", "undo", "redo"]
    seq: int
    undo_of_seq: int | None
    idempotency_key: str | None
    actor: str | None
    origin: str | None
    ref_id: str | None
    deltas: list[FeatureDelta]
    schema_ops: list[RenameField]
    op_summary: JournalOperationSummary
    fingerprint_before: str | None
    fingerprint_after: str | None
    content_revision_before: int | None
    content_revision_after: int | None
    created_at: float | None


class EditJournal(TypedDict):
    entries: list[JournalEntry]
    head_seq: int
    dropped: int


class GeometryPreview(TypedDict):
    positions_before: int
    positions_after: int
    bbox_after: list[float] | None


class DeltaPreview(TypedDict):
    wfid: str | None
    fields: NotRequired[dict[str, FieldChange]]
    geometry: NotRequired[GeometryPreview]


class EditReceipt(TypedDict, total=False):
    entry_seq: int | None
    kind: Literal["apply", "undo", "redo"] | None
    ref_id: str | None
    changed_wfids: list[str | None]
    schema_ops: list[RenameField]
    content_revision_after: int | None
    fingerprint_after: str | None
    actor: str | None
    origin: str | None
    idempotency_key: str | None
    undo_of_seq: int | None
    stamp_status: str
    mapspec_revision: int | None
    no_changes: bool
    plan_id: str


class JournalHead(TypedDict):
    seq: int | None
    kind: Literal["apply", "undo", "redo"] | None
    actor: str | None
    origin: str | None
    created_at: float | None


class JournalHeadSummary(TypedDict):
    head_seq: int
    head: JournalHead | None


class SelectionView(TypedDict, total=False):
    dataset_ref: str
    feature_id: str
    selected_ids: list[str]


class PlanSummary(TypedDict, total=False):
    plan_id: str | None
    ref_id: str | None
    status: str | None
    committable: bool | None
    safe: bool | None
    n_targets: int
    ops: list[OperationSummary]
    validation: list[EditFinding]
    expected_content_revision: int | None
    changed_features: int
    schema_ops: list[dict[str, str | None]]
    preview: list[DeltaPreview]
    expires_in_s: int
