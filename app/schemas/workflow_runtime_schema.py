"""Workflow Runtime 子系统契约模型（V9 契约基石，ADR-0138）。

请求模型从 `app/api/routes/workflow_runtime.py` verbatim 迁出；
响应由服务层投影（PR.instance_projection 等）+ ``success`` 布尔锚构成，
响应模型用 extra="allow" 开放对象声明 —— 投影形状由服务层契约测试覆盖。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

_MAX_QUERY_CHARS = 200
_MAX_CHANGES = 16


class PackageRegisterRequest(BaseModel):
    """POST /workflow-runtime/packages/register 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "query": "缓冲区分析并出图",
                    "recipe_id": "",
                    "project_id": "",
                    "profile": None,
                }
            ]
        }
    )

    query: str = Field(min_length=1, max_length=_MAX_QUERY_CHARS)
    recipe_id: str = Field(default="", max_length=64)
    project_id: str = Field(default="", max_length=255)
    profile: Optional[Dict[str, Any]] = None


class PublishRequest(BaseModel):
    """POST /workflow-runtime/packages/{package_id}/publish 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"version": "1.0.0"}]}
    )

    version: str = Field(min_length=1, max_length=16)


class InstantiateRequest(BaseModel):
    """POST /workflow-runtime/instances 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "package_id": "pkg-1",
                    "version": "1.0.0",
                    "session_id": "sess-123",
                    "project_id": "",
                }
            ]
        }
    )

    package_id: str = Field(min_length=1, max_length=64)
    version: str = Field(default="", max_length=16)
    session_id: str = Field(default="", max_length=255)
    project_id: str = Field(default="", max_length=255)


class ChangeIn(BaseModel):
    """单个变更输入（dimension × target_kind）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"dimension": "style", "target_kind": "layer", "target": "lyr-1", "detail": ""}]
        }
    )

    dimension: str = Field(min_length=1, max_length=24)
    target_kind: str = Field(min_length=1, max_length=24)
    target: str = Field(default="", max_length=64)
    detail: str = Field(default="", max_length=200)


class ChangesRequest(BaseModel):
    """POST /workflow-runtime/instances/{instance_id}/changes 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"changes": [{"dimension": "style", "target_kind": "layer"}], "dry_run": False}]
        }
    )

    changes: List[ChangeIn] = Field(min_length=1, max_length=_MAX_CHANGES)
    dry_run: bool = False


class RunRequest(BaseModel):
    """POST /workflow-runtime/instances/{instance_id}/run 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"deadline_s": 60.0}]}
    )

    deadline_s: float = Field(default=60.0, gt=0, le=300.0)


class NodeCancelRequest(BaseModel):
    """POST /workflow-runtime/instances/{instance_id}/nodes/cancel 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"node_ids": ["node-1"], "include_descendants": True}]
        }
    )

    node_ids: List[str] = Field(min_length=1, max_length=16)
    include_descendants: bool = True


class CloneRequest(BaseModel):
    """POST /workflow-runtime/instances/{instance_id}/clone 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "session_id": "sess-new",
                    "only_nodes": None,
                    "skip_nodes": ["output:zone"],
                }
            ]
        }
    )

    session_id: Optional[str] = Field(default=None, max_length=255)
    only_nodes: Optional[List[str]] = Field(default=None, max_length=16)
    skip_nodes: Optional[List[str]] = Field(default=None, max_length=16)


# ── 响应模型（开放对象：锚定 success + 已知键，服务层投影透传）────────


class _OpenResponse(BaseModel):
    """workflow-runtime 开放响应基类。"""

    model_config = ConfigDict(extra="allow")

    success: Optional[bool] = None


class PackageRegisterResponse(_OpenResponse):
    """POST /packages/register 响应（含 package 投影）。"""


class PackagePublishResponse(_OpenResponse):
    """POST /packages/{package_id}/publish 响应。"""


class PackageListResponse(_OpenResponse):
    """GET /packages 响应。"""

    packages: List[Dict[str, Any]] = []


class PackageVersionsResponse(_OpenResponse):
    """GET /packages/{package_id}/versions 响应。"""

    versions: List[Dict[str, Any]] = []


class InstanceCreateResponse(_OpenResponse):
    """POST /instances 响应。"""

    instance: Optional[Dict[str, Any]] = None


class InstanceRunResponse(_OpenResponse):
    """POST /instances/{instance_id}/run 响应（run 报告，形态由服务层定）。"""


class InstanceCancelResponse(_OpenResponse):
    """POST /instances/{instance_id}/cancel 响应。"""


class InstanceChangesResponse(_OpenResponse):
    """POST /instances/{instance_id}/changes 响应（dry_run 时为 plan）。"""


class InstanceDetailResponse(_OpenResponse):
    """GET /instances/{instance_id} 响应（含 explain 投影）。"""

    instance: Optional[Dict[str, Any]] = None


class RecomputePlanResponse(_OpenResponse):
    """GET /instances/{instance_id}/recompute-plan 响应。"""

    instance_id: Optional[str] = None
    stale: Optional[int] = None
    counts: Dict[str, Any] = {}
    decisions: List[Any] = []


class InstanceListResponse(_OpenResponse):
    """GET /instances 响应。"""

    instances: List[Dict[str, Any]] = []


class InstanceEventsResponse(_OpenResponse):
    """GET /instances/{instance_id}/events 响应。"""

    instance_id: Optional[str] = None
    events: List[Dict[str, Any]] = []


class NodeDetailResponse(_OpenResponse):
    """GET /instances/{instance_id}/nodes/{node_id} 响应。"""


class NodeRetryResponse(_OpenResponse):
    """POST /instances/{instance_id}/nodes/{node_id}/retry 响应。"""


class NodesCancelResponse(_OpenResponse):
    """POST /instances/{instance_id}/nodes/cancel 响应。"""


class InstanceCloneResponse(_OpenResponse):
    """POST /instances/{instance_id}/clone 响应。"""


class InstanceDebugResponse(_OpenResponse):
    """GET /instances/{instance_id}/debug 响应。"""
