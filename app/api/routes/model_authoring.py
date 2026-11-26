"""模型作者域 REST（additive；/model-authoring 前缀）。

安全语义与 workflow_runtime 路由同一纪律：
- 全部端点强制认证（get_current_user）；项目写路径过
  ``_authorize_project_write``（IDOR 防护）；owner 域经
  ``owner_scope_for`` 派生（他人包/草稿 → 404，不泄漏存在性）；
- 实例化/发布/运行统一 to_thread / asyncio 卸载；
- 运行/取消/重试/事件复用既有 /workflow-runtime/instances/* 端点
  （不重复执行面）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth import get_current_user, get_owner_token
from app.services.model_authoring.bindings import BindingError
from app.services.model_authoring.service import (
    ModelAuthoringError,
    ModelAuthoringService,
    get_service,
)
from app.services.model_authoring.store import ModelStoreError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/model-authoring", tags=["Model Authoring"])


def _svc() -> ModelAuthoringService:
    return get_service()


def _owner(user: Dict[str, Any]) -> str:
    from app.services.workflow_runtime.service import owner_scope_for

    return owner_scope_for(user, None)


def _org(user: Dict[str, Any]) -> str:
    from app.core import tenancy

    return tenancy.effective_org_in_thread(user)


async def _authorize_project_write(project_id: str, user: Dict[str, Any]) -> None:
    if not project_id:
        return

    def allowed() -> bool:
        from app.core.database import SessionLocal
        from app.services.project_service import ProjectService

        with SessionLocal() as db:
            return ProjectService.get_project_for_write(
                db, project_id, user_id=user.get("user_id"),
                org_id=_org(user)) is not None

    if not await asyncio.to_thread(allowed):
        raise HTTPException(status_code=403, detail="PROJECT_FORBIDDEN")


def _http(code: str, status: int = 422, detail: str = "") -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "detail": detail})


# ── 请求/响应契约（bounded）───────────────────────────────────────────


class ModelCapabilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str
    input_artifact_types: List[str] = Field(default_factory=list)
    output_artifact_types: List[str] = Field(default_factory=list)
    status: str = ""


class CapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    transform_ops: List[str]
    data_roles: List[str]
    capabilities: List[ModelCapabilityEntry]


class ModelSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_slug: str
    name: str
    document_fingerprint: str
    revision_no: int
    published: bool
    latest_published_version: str = ""
    updated_at: str = ""


class ModelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    models: List[Dict[str, Any]]


class ModelDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    draft: Dict[str, Any]


class ModelCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_slug: str = Field(min_length=3, max_length=40)
    document: Dict[str, Any]


class ModelUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document: Dict[str, Any]
    expected_revision: int = Field(ge=1)
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)


class ModelValidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    valid: bool
    findings: List[Dict[str, Any]]
    document_fingerprint: str = ""


class ModelPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change: str = Field(default="minor", pattern="^(major|minor|patch)$")


class ModelPublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    package_id: str
    version: str
    fingerprint: str
    # 指针写入诚实回执（R2-1）：stale = 包已发布但草稿指针因并发编辑
    # 未能写到本版本（幂等收尾语义，不构成发布失败）。
    pointer_stale: bool = False
    next_draft_revision: Optional[int] = None
    findings: List[Dict[str, Any]] = Field(default_factory=list)


class ModelInstantiateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(default="", max_length=255)
    version: str = Field(default="", max_length=16)
    param_values: Dict[str, Any] = Field(default_factory=dict)
    input_bindings: Dict[str, str] = Field(default_factory=dict,
                                           max_length=16)


class ModelRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deadline_s: float = Field(default=60.0, gt=0, le=300.0)
    param_values: Optional[Dict[str, Any]] = None


class ModelImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_slug: str = Field(min_length=3, max_length=40)
    payload: Dict[str, Any]


class ModelDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool


class ModelInstantiateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    model_slug: str
    package: Dict[str, Any]
    params_resolved: Dict[str, Any]
    node_params: Dict[str, Dict[str, Any]]
    bindings: Dict[str, Dict[str, Any]]
    instance: Dict[str, Any]


class ModelRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    run: Dict[str, Any]
    node_params: Dict[str, Dict[str, Any]]


class ModelCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    models: List[Dict[str, Any]]


class ModelExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: bool
    format: str
    format_version: str
    document: Dict[str, Any]


# ── 能力目录（作者面只读投影）────────────────────────────────────────


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def list_capabilities(
    user: Dict[str, Any] = Depends(get_current_user),
):
    from app.schemas.model_authoring_schema import EXECUTABLE_TRANSFORM_OPS

    def _load() -> List[ModelCapabilityEntry]:
        from app.lib.gis.capability_registry import get_capability_registry

        out: List[ModelCapabilityEntry] = []
        try:
            reg = get_capability_registry()
            for cap_id in reg.all_ids:
                desc = reg.get(cap_id)
                status = str(getattr(desc, "status", "") or "")
                if status != "native":
                    continue
                out.append(ModelCapabilityEntry(
                    capability=cap_id,
                    input_artifact_types=[str(t) for t in (
                        getattr(desc, "input_artifact_types", []) or [])][:6],
                    output_artifact_types=[str(t) for t in (
                        getattr(desc, "output_artifact_types", []) or [])][:6],
                    status=status))
        except Exception:  # noqa: BLE001 — 目录失败诚实空集
            logger.info("[model-authoring] capability catalog unavailable",
                        exc_info=True)
        return sorted(out, key=lambda e: e.capability)[:64]

    caps = await asyncio.to_thread(_load)
    roles: List[str] = []
    try:
        from app.services.gis_harness.workflow_schema import DATA_ROLES

        roles = list(DATA_ROLES)
    except Exception:  # noqa: BLE001
        roles = []
    return {"success": True, "transform_ops": list(EXECUTABLE_TRANSFORM_OPS),
            "data_roles": roles, "capabilities": caps}


# ── 草稿 CRUD ────────────────────────────────────────────────────────


@router.post("/projects/{project_id}/models", response_model=ModelDraftResponse)
async def create_model(
    project_id: str, body: ModelCreateRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    from app.schemas.model_authoring_schema import ModelDocument

    try:
        doc = ModelDocument.model_validate(body.document)
    except Exception as e:  # noqa: BLE001 — pydantic 摘要有界回显
        raise _http("MODEL_SCHEMA_INVALID", 422, str(e)[:300])
    try:
        draft = await _svc().create_draft(
            project_id=project_id, document=doc,
            model_slug=body.model_slug, created_by=user.get("user_id") or "")
    except ModelStoreError as e:
        status = 409 if e.code == "DRAFT_EXISTS" else 422
        raise _http(e.code, status, e.detail)
    return {"success": True, "draft": draft}


@router.get("/projects/{project_id}/models", response_model=ModelListResponse)
async def list_models(
    project_id: str,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    rows = await _svc().list_drafts(project_id, limit=limit, offset=offset)
    return {"success": True, "models": rows}


@router.get("/projects/{project_id}/models/{model_slug}",
            response_model=ModelDraftResponse)
async def get_model(
    project_id: str, model_slug: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    draft = await _svc().get_draft(project_id, model_slug)
    if draft is None:
        raise _http("DRAFT_NOT_FOUND", 404, model_slug)
    return {"success": True, "draft": draft}


@router.put("/projects/{project_id}/models/{model_slug}",
            response_model=ModelDraftResponse)
async def update_model(
    project_id: str, model_slug: str, body: ModelUpdateRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    from app.schemas.model_authoring_schema import ModelDocument

    try:
        doc = ModelDocument.model_validate(body.document)
    except Exception as e:  # noqa: BLE001
        raise _http("MODEL_SCHEMA_INVALID", 422, str(e)[:300])
    try:
        draft = await _svc().update_document(
            project_id=project_id, model_slug=model_slug, document=doc,
            expected_revision=body.expected_revision, name=body.name,
            description=body.description)
    except ModelStoreError as e:
        raise _http(e.code, 409 if e.code == "REVISION_CONFLICT" else 422,
                    e.detail)
    return {"success": True, "draft": draft}


@router.delete("/projects/{project_id}/models/{model_slug}",
             response_model=ModelDeleteResponse)
async def delete_model(
    project_id: str, model_slug: str, expected_revision: int,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    try:
        await _svc().delete_draft(
            project_id=project_id, model_slug=model_slug,
            expected_revision=expected_revision)
    except ModelStoreError as e:
        raise _http(e.code, 409, e.detail)
    return {"success": True}


# ── 验证 / 发布 / 实例化 ─────────────────────────────────────────────


@router.post("/projects/{project_id}/models/{model_slug}/validate",
             response_model=ModelValidateResponse)
async def validate_model(
    project_id: str, model_slug: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    try:
        out = await _svc().validate_model(project_id, model_slug)
    except ModelAuthoringError as e:
        raise _http(e.code, 404 if e.code == "DRAFT_NOT_FOUND" else 422,
                    e.detail)
    return {"success": True, "valid": out["valid"],
            "findings": out["findings"],
            "document_fingerprint": out["document_fingerprint"]}


@router.post("/projects/{project_id}/models/{model_slug}/publish",
             response_model=ModelPublishResponse)
async def publish_model(
    project_id: str, model_slug: str, body: ModelPublishRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    owner = _owner(user)
    org_id = await asyncio.to_thread(_org, user)
    try:
        out = await _svc().publish_model(
            project_id=project_id, model_slug=model_slug,
            owner_scope=owner, org_id=org_id, change=body.change)
    except ModelAuthoringError as e:
        status = {"DRAFT_NOT_FOUND": 404, "MODEL_INVALID": 422,
                  "PACKAGE_CONFLICT": 409}.get(e.code, 422)
        raise HTTPException(
            status_code=status,
            detail={"code": e.code, "detail": e.detail,
                    **({"findings": e.extra.get("findings", [])}
                       if e.extra.get("findings") else {})})
    return {"success": True, "package_id": out["package_id"],
            "version": out["version"], "fingerprint": out["fingerprint"],
            "pointer_stale": out.get("pointer_stale", False),
            "next_draft_revision": out.get("next_draft_revision"),
            "findings": out["findings"]}


@router.post("/projects/{project_id}/models/{model_slug}/instantiate",
             response_model=ModelInstantiateResponse)
async def instantiate_model(
    project_id: str, model_slug: str, body: ModelInstantiateRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    await _authorize_project_write(project_id, user)
    owner = _owner(user)
    org_id = await asyncio.to_thread(_org, user)
    if body.session_id:
        from app.api.routes.geocompute import _authorize_session_write_sync

        await asyncio.to_thread(
            _authorize_session_write_sync, body.session_id, user, owner_token)
    from app.services import org_quota

    try:
        await org_quota.enforce_for_principal(user)
    except org_quota.QuotaExceededError:
        raise _http("QUOTA_EXCEEDED", 429)
    except Exception:  # noqa: BLE001 — 配额面不可用放行（fail-open 既有纪律）
        pass
    try:
        out = await _svc().instantiate_model(
            project_id=project_id, model_slug=model_slug,
            owner_scope=owner, session_id=body.session_id, org_id=org_id,
            version=body.version, param_values=body.param_values,
            input_bindings=body.input_bindings)
    except ModelAuthoringError as e:
        status = {"DRAFT_NOT_FOUND": 404, "MODEL_NOT_PUBLISHED": 409,
                  "REQUIRED_PARAM_MISSING": 422}.get(e.code, 422)
        raise _http(e.code, status, e.detail)
    except BindingError as e:
        raise _http(e.code, 422, e.detail)
    return {"success": True, **out}


@router.post("/instances/{instance_id}/run",
             response_model=ModelRunResponse)
async def run_instance(
    instance_id: str, body: ModelRunRequest,
    project_id: str = "",
    user: Dict[str, Any] = Depends(get_current_user),
):
    # P1-1：project_id 携带时必须过项目写权限（草稿参数解析只信任实例
    # 归属项目，但调用方转述的项目仍不得绕过鉴权面）。
    await _authorize_project_write(project_id, user)
    owner = _owner(user)
    try:
        out = await _svc().run_model_instance(
            instance_id=instance_id, owner_scope=owner,
            param_values=body.param_values, project_id=project_id,
            deadline_s=body.deadline_s)
    except ModelAuthoringError as e:
        raise _http(e.code, 404 if e.code == "INSTANCE_NOT_FOUND" else 422,
                    e.detail)
    return {"success": True, **out}


# ── 目录 / 版本 ──────────────────────────────────────────────────────


@router.get("/projects/{project_id}/catalog",
             response_model=ModelCatalogResponse)
async def model_catalog(
    project_id: str,
    limit: int = 50,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    owner = _owner(user)
    entries = await _svc().list_model_catalog(
        project_id=project_id, owner_scope=owner, limit=limit)
    return {"success": True, "models": entries}


# ── 导入 / 导出（小型模型文档；导入只建草稿，绝不自动执行）────────────


@router.get("/projects/{project_id}/models/{model_slug}/export",
             response_model=ModelExportResponse)
async def export_model(
    project_id: str, model_slug: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    try:
        doc = await _svc().export_model(project_id, model_slug)
    except ModelAuthoringError as e:
        raise _http(e.code, 404 if e.code == "DRAFT_NOT_FOUND" else 422,
                    e.detail)
    return {"success": True, **doc}


@router.post("/projects/{project_id}/models/import",
             response_model=ModelDraftResponse)
async def import_model(
    project_id: str, body: ModelImportRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    await _authorize_project_write(project_id, user)
    try:
        draft = await _svc().import_model(
            project_id=project_id, payload=body.payload,
            model_slug=body.model_slug, created_by=user.get("user_id") or "")
    except ModelAuthoringError as e:
        raise _http(e.code, 422, e.detail)
    except ModelStoreError as e:
        status = 409 if e.code == "DRAFT_EXISTS" else 422
        raise _http(e.code, status, e.detail)
    except ValueError as e:
        raise _http("IMPORT_INVALID", 422, str(e)[:300])
    return {"success": True, "draft": draft}
