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
    # G14 结构性语义：presence 声明要素出生/消失（缺省 = 修改，旧 entry 兼容）。
    # created：feature_after 携带完整要素（含 _wfid 的 properties + geometry），
    #   undo = 移除该 wfid；removed：feature_before 携带完整快照，undo = 原样
    #   复活（同 wfid、同字段、同几何）。
    presence: NotRequired[Literal["created", "removed"]]
    feature_before: NotRequired[dict | None]
    feature_after: NotRequired[dict | None]
    # removed 记录要素在操作前列表中的位置；其逆（created）按该位置重插 ——
    # undo 恢复的不仅是内容还有次序（载荷指纹含列表序，位置漂移会让链
    # 完整性断言诚实失败）。created 的原生新要素（split/merge 子）无 index，
    # 按推导顺序追加。
    index: NotRequired[int]


class LineageEvent(TypedDict):
    kind: Literal["split", "merge"]
    parent_wfids: list[str]
    child_wfids: list[str]
    seq: int


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
    # G14：结构性操作的血缘投影（可选；旧 entry 无此键 = 无血缘事实）。
    lineage: list[LineageEvent]


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
    presence: NotRequired[Literal["created", "removed"]]


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
    # G14 结构性投影：本 entry 出生/退役的稳定身份（选择/标注/分析失效面）。
    created_wfids: list[str]
    retired_wfids: list[str]
    lineage_events: list[LineageEvent]


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
