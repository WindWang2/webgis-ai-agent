"""GeoAI HTTP 面（Platform 11 / ADR-0198；WP-G 后端）。

最小自包含面：模型列举 / 平台状态（encoder + embedding cache）/ 可提示
分割提交（含候选）/ embedding 提交。安全门：``source_uri`` 只允许解析到
``settings.DATA_DIR`` 之下的路径（uploads/sessions/modelops 数据域），
防 HTTP 面变成任意本地文件读取器；越界 = 400。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_URI = 2048


def _gate_source_uri(source_uri: str) -> str:
    """路径安全门：只放行 DATA_DIR 数据域内的栅格（解析后相对检查）。"""
    uri = str(source_uri)[:_MAX_URI]
    path = Path(uri)
    if path.drive and not path.is_absolute():
        raise HTTPException(status_code=400, detail="source_uri must be absolute")
    try:
        resolved = path.resolve()
        root = Path(settings.DATA_DIR).resolve()
        resolved.relative_to(root)
    except (ValueError, OSError):
        raise HTTPException(
            status_code=400,
            detail="source_uri must resolve inside the platform data directory",
        ) from None
    return uri


class PromptSegmentBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    source_uri: str = Field(min_length=1, max_length=_MAX_URI)
    artifact: Optional[Dict[str, Any]] = None
    points: Optional[List[List[float]]] = None
    boxes: Optional[List[List[float]]] = None
    geographic_coords: bool = False
    return_candidates: bool = False
    candidate_selection: str = "best"
    selected_candidate: Optional[int] = None
    session_id: Optional[str] = None
    project_id: Optional[str] = None


class EmbedBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    source_uri: str = Field(min_length=1, max_length=_MAX_URI)
    session_id: Optional[str] = None
    project_id: Optional[str] = None


@router.get("/geoai/models", tags=["geoai"], summary="GeoAI 模型清单（按任务过滤）")
async def list_geoai_models(
    task_type: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> dict:
    from app.services.modelops.service import get_modelops_service, normalize_scope

    scope = normalize_scope(session_id=session_id, project_id=project_id)
    service = get_modelops_service()
    return {
        "models": service.list_models(
            session_id=scope.get("session_id"),
            project_id=scope.get("project_id"),
            task_type=task_type,
        )
    }


@router.get(
    "/geoai/status", tags=["geoai"], summary="GeoAI 平台状态（encoder/embedding cache）"
)
async def geoai_status() -> dict:
    from app.services.modelops.service import get_modelops_service

    service = get_modelops_service()
    return {
        "semantic_encoder": service.semantic_encoder_caps(),
        "embedding_cache": service.embedding_cache_stats(),
    }


@router.post(
    "/geoai/prompt-segment",
    tags=["geoai"],
    summary="可提示分割（GeoPrompt artifact 或 points/boxes；含多候选）",
)
async def prompt_segment(body: PromptSegmentBody) -> dict:
    from app.lib.modelops.errors import ModelOpsError
    from app.lib.modelops.promptable import PromptSpec
    from app.services.modelops.engine import InferenceRequest
    from app.services.modelops.service import get_modelops_service, normalize_scope

    uri = _gate_source_uri(body.source_uri)
    service = get_modelops_service()
    scope = normalize_scope(session_id=body.session_id, project_id=body.project_id)
    artifact_id = None
    audit = None
    prompt_crs = bool(body.geographic_coords)
    if body.artifact is not None:
        compiled = service.compile_geo_prompt(body.artifact, uri)
        prompt = compiled["prompt"]
        artifact_id = compiled["artifact_id"]
        audit = compiled["audit"]
        prompt_crs = False  # artifact 已在编译期完成坐标变换
    elif body.points or body.boxes:
        prompt = PromptSpec(
            points=tuple(tuple(map(float, p)) for p in (body.points or [])),
            boxes=tuple(tuple(map(float, b)) for b in (body.boxes or [])),
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="artifact or points/boxes required",
        )
    request = InferenceRequest(
        model_id=body.model_id,
        source_uri=uri,
        owner_scope=scope,
        prompt=prompt,
        prompt_crs=prompt_crs,
        prompt_artifact_id=artifact_id,
        prompt_audit=audit,
        return_candidates=body.return_candidates,
        candidate_selection=body.candidate_selection,
        selected_candidate=body.selected_candidate,
    )
    try:
        result = await service.run_inference_async(request)
    except ModelOpsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "run_id": result.run_id,
        "status": result.status,
        "reused": result.reused,
        "task_type": result.task_type,
        "outputs": result.outputs,
        "performance": result.perf,
        "manifest": result.manifest,
    }


@router.post("/geoai/embed", tags=["geoai"], summary="embedding 推理（含 cache 观测）")
async def embed(body: EmbedBody) -> dict:
    from app.lib.modelops.errors import ModelOpsError
    from app.services.modelops.engine import InferenceRequest
    from app.services.modelops.service import get_modelops_service, normalize_scope

    uri = _gate_source_uri(body.source_uri)
    service = get_modelops_service()
    scope = normalize_scope(session_id=body.session_id, project_id=body.project_id)
    try:
        result = await service.run_inference_async(InferenceRequest(
            model_id=body.model_id,
            source_uri=uri,
            owner_scope=scope,
            task_type="embedding",
        ))
    except ModelOpsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "run_id": result.run_id,
        "status": result.status,
        "outputs": result.outputs,
        "performance": result.perf,
        "embedding_cache": service.embedding_cache_stats(),
    }
