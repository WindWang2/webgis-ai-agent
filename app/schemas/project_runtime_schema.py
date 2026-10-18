"""Project Runtime Snapshot API schemas（ADR-0226）。

薄入口：list / validation preview / resume / delete。不做大规模 UI
redesign —— 每个响应都携带「恢复了什么 / 没恢复什么 / 要重算什么」的
机器可读披露面。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RuntimeSnapshotSaveRequest(BaseModel):
    """显式冻结请求（freeze 点：explicit_save / named_checkpoint / publish）。"""

    session_id: str = Field(..., max_length=128)
    label: str = Field("", max_length=96)
    freeze_reason: str = Field("explicit_save", max_length=32)
    map_product_version_no: Optional[int] = None
    materialize_budget_bytes: Optional[int] = Field(
        None, ge=0, le=4 * 1024 * 1024 * 1024,
        description="per-freeze durable materialization budget (default 256MB)",
    )


class RuntimeSnapshotSaveResponse(BaseModel):
    project_id: str
    snapshot_id: str
    session_id: str
    workspace_snapshot_id: str
    label: str
    freeze_reason: str
    mapspec_fingerprint: str
    mapspec_mutation_revision: int
    sources_total: int
    sources_durable: int
    sources_needing_recompute: int
    datasets: int
    promotion_summary: Dict[str, Any] = Field(default_factory=dict)
    snapshot: Dict[str, Any] = Field(default_factory=dict)


class RuntimeSnapshotSummary(BaseModel):
    snapshot_id: str
    label: str = ""
    created_at: Optional[float] = None
    freeze_reason: str = ""
    session_id: str = ""
    sources: int = 0
    datasets: int = 0
    schema_version: int = 1
    workspace_snapshot_id: str = ""


class RuntimeSnapshotListResponse(BaseModel):
    project_id: str
    count: int
    items: List[RuntimeSnapshotSummary] = Field(default_factory=list)


class RuntimeSnapshotInspectResponse(BaseModel):
    """validation preview（只读；resume 前即可见 missing/stale/recompute）。"""

    snapshot_id: str
    status: str
    schema_supported: bool
    project_ok: bool
    permission: str
    mapspec_integrity_ok: bool
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    dispositions: List[Dict[str, Any]] = Field(default_factory=list)
    dataset_verdicts: Dict[str, str] = Field(default_factory=dict)
    capability_verdicts: Dict[str, str] = Field(default_factory=dict)
    recovery_plan: Dict[str, Any] = Field(default_factory=dict)


class RuntimeSnapshotResumeRequest(BaseModel):
    """恢复请求（allow_stale=False 要求纯净恢复面）。"""

    allow_stale: bool = True


class RuntimeSnapshotResumeResponse(BaseModel):
    """ResumeReceipt 面（诚实披露）。"""

    snapshot_id: str
    project_id: str
    session_id: str
    source_session_id: str
    status: str
    restored_refs: List[str] = Field(default_factory=list)
    ref_map: Dict[str, str] = Field(default_factory=dict)
    missing_refs: List[str] = Field(default_factory=list)
    mapspec_rebound: bool = False
    mapspec_frozen_fingerprint: str = ""
    mapspec_new_fingerprint: str = ""
    datasets_changed: List[str] = Field(default_factory=list)
    capabilities_unavailable: List[str] = Field(default_factory=list)
    invalidated: List[str] = Field(default_factory=list)
    artifacts_registered: int = 0
    artifacts_expired: int = 0
    degraded: List[Dict[str, str]] = Field(default_factory=list)
    disclosures: List[str] = Field(default_factory=list)


class RuntimeSnapshotDeleteResponse(BaseModel):
    project_id: str
    snapshot_id: str
    deleted: bool
    durable_pointers: int = 0
