"""Project Exchange REST 契约（05 线 · 工程包交换）。

与 ``app/services/project_exchange/contracts.py`` 同源词汇表：
分类/分级/错误码是有界封闭词表；响应模型全部有界投影（不透传大载荷）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

_Bounded = ConfigDict(extra="forbid")


class ExchangeSelection(BaseModel):
    """打包范围（kind + 可选引用集；条目数有界）。"""

    model_config = _Bounded

    kind: Literal["project", "map", "analysis"] = "project"
    map_product_ids: List[str] = Field(default_factory=list, max_length=100)
    workflow_ids: List[str] = Field(default_factory=list, max_length=100)


class ExchangeInspectRequest(BaseModel):
    model_config = _Bounded

    selection: ExchangeSelection = Field(default_factory=ExchangeSelection)


class ExchangeLimits(BaseModel):
    model_config = _Bounded

    max_package_bytes: int
    max_entries: int
    max_entry_bytes: int


class ExchangeInspectionResponse(BaseModel):
    model_config = _Bounded

    project_id: str
    project_name: str
    selection: Dict[str, Any]
    reproduction_level: str
    classification_counts: Dict[str, int]
    totals: Dict[str, int]
    runnable_workflows: int
    estimated_payload_bytes: int
    estimated_unknown_bytes_count: int
    disclosure: List[Dict[str, Any]]
    truncated: Dict[str, bool]
    exportable: bool


class ExchangePlanResponse(BaseModel):
    model_config = _Bounded

    project_id: str
    project_name: str
    selection: Dict[str, Any]
    reproduction_level: str
    estimated_payload_bytes: int
    estimated_unknown_bytes_count: int
    datasets: List[Dict[str, Any]]
    workflows: List[Dict[str, Any]]
    artifacts: List[Dict[str, Any]]
    maps: List[Dict[str, Any]]
    standard_files: Dict[str, Any]
    disclosure: List[Dict[str, Any]]
    truncated: Dict[str, bool]
    limits: ExchangeLimits


class ExportSubmitRequest(BaseModel):
    model_config = _Bounded

    selection: ExchangeSelection = Field(default_factory=ExchangeSelection)
    package_name: str = Field(default="", max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=255)


class ExportSubmitResponse(BaseModel):
    model_config = _Bounded

    job_id: str
    status: str
    idempotent_reuse: bool = False
    message: str = ""


class ExportStatusResponse(BaseModel):
    model_config = _Bounded

    package_id: str
    filename: str
    sha256: str
    bytes: int
    project_id: str
    reproduction: Dict[str, Any]
    counts: Dict[str, int]
    created_at: str
    download_path: str


class PackageUploadResponse(BaseModel):
    model_config = _Bounded

    package_upload_id: str
    filename: str
    bytes: int
    sha256: str


class ImportInspectRequest(BaseModel):
    model_config = _Bounded

    package_upload_id: str = Field(min_length=6, max_length=32)


class ImportInspectResponse(BaseModel):
    model_config = _Bounded

    package_id: str
    format: str
    format_version: int
    created_at: str
    origin: Dict[str, Any]
    project_name: str
    reproduction: Dict[str, Any]
    classification_counts: Dict[str, int]
    totals: Dict[str, int]
    declared_total_bytes: int
    licenses: List[Dict[str, Any]]
    disclosure: Dict[str, Any]
    conflicts: Dict[str, Any]
    limits: ExchangeLimits


class ImportPlanRequest(BaseModel):
    model_config = _Bounded

    package_upload_id: str = Field(min_length=6, max_length=32)
    target_name: str = Field(default="", max_length=120)


class ImportPlanResponse(ImportInspectResponse):
    """预检 = inspect + 默认目标策略（新隔离项目；无覆盖路径）。"""

    default_target: Dict[str, Any] = Field(default_factory=lambda: {
        "mode": "new_isolated_project",
        "overwrites_existing": False,
        "auto_recompute": False,
    })


class ImportSubmitRequest(BaseModel):
    model_config = _Bounded

    package_upload_id: str = Field(min_length=6, max_length=32)
    target_name: str = Field(default="", max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=255)
    acknowledged_conflicts: List[str] = Field(default_factory=list,
                                              max_length=200)


class ImportSubmitResponse(BaseModel):
    model_config = _Bounded

    job_id: str
    status: str
    idempotent_reuse: bool = False
    message: str = ""


class ImportStatusResponse(BaseModel):
    model_config = _Bounded

    import_id: str
    job_id: str = ""   # receipt 含 job_id（R1 P1-4：extra=forbid 下必须显式声明）
    package_id: str
    project_id: str
    target_name: str
    mapping: Dict[str, Any]
    session_writebacks: List[Dict[str, Any]]
    skipped: List[Dict[str, Any]]
    verification: Dict[str, Any]
    created_at: str
