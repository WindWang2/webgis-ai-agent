"""GeoAI HTTP 面（Platform 11 / ADR-0198；WP-G 后端）。

最小自包含面：模型列举 / 平台状态（encoder + embedding cache）/ 可提示
分割提交（含候选）/ embedding 提交。安全门：``source_uri`` 只允许解析到
``settings.DATA_DIR`` 之下的路径（uploads/sessions/modelops 数据域），
防 HTTP 面变成任意本地文件读取器；越界 = 400。

#1379：全路由强制 ``get_current_user``。#1414：带 ``session_id`` /
``project_id`` 的请求必须过会话/项目所有权校验；preview / artifact-geojson /
推理等读路径端点强制 scope，会话面路径根收缩到该会话目录 + 其 uploads
（不再对任意登录用户开放整棵 DATA_DIR）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    actor_ids,
    get_current_user,
    get_owner_token,
    verify_session_owner,
)
from app.core.config import settings
from app.core.database import SessionLocal, get_async_db

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_URI = 2048
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
_MAX_INLINE_FEATURES = 20000


def _http_allowed_roots() -> "List[Path]":
    """HTTP 面的 artifact 引用根（与 source gate 同口径 + registry 数据域）。

    review fix：artifact 内嵌的 mask_ref.path / reference_layer.uri 此前
    绕过 DATA_DIR 门（任意本地栅格读取 + sha256 oracle）；compile 在
    HTTP 调用路径必须传 allowed_roots。
    """
    from app.services.modelops.service import get_modelops_service

    data_root = Path(settings.DATA_DIR).resolve()
    registry_root = Path(get_modelops_service()._settings.registry_dir).resolve()
    return [data_root, registry_root]


def _gate_source_uri(
    source_uri: str, *, allowed_roots: Optional[List[Path]] = None
) -> str:
    """路径安全门：只放行 ``allowed_roots``（默认 DATA_DIR）内的路径。"""
    uri = str(source_uri)[:_MAX_URI]
    path = Path(uri)
    if path.drive and not path.is_absolute():
        raise HTTPException(status_code=400, detail="source_uri must be absolute")
    try:
        resolved = path.resolve()
    except (ValueError, OSError):
        raise HTTPException(
            status_code=400,
            detail="source_uri must resolve inside the platform data directory",
        ) from None
    roots = allowed_roots or [Path(settings.DATA_DIR).resolve()]
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return uri
        except (ValueError, OSError):
            continue
    raise HTTPException(
        status_code=400,
        detail="source_uri must resolve inside the platform data directory",
    )


async def _bind_owner_scope(
    *,
    session_id: Optional[str],
    project_id: Optional[str],
    user: Dict[str, Any],
    db: AsyncSession,
    owner_token: Optional[str] = None,
    require: bool = False,
) -> Dict[str, str]:
    """#1414：校验 session/project 归属，返回 normalize_scope 可用的 scope dict。"""
    import asyncio

    from app.services.modelops.service import normalize_scope
    from app.services.project_service import ProjectService

    sid = (session_id or "").strip() or None
    pid = (project_id or "").strip() or None
    if sid and pid:
        raise HTTPException(
            status_code=400,
            detail="provide exactly one of session_id / project_id",
        )
    if require and not sid and not pid:
        raise HTTPException(
            status_code=400,
            detail="session_id or project_id is required",
        )
    if not sid and not pid:
        return {}

    if sid:
        await verify_session_owner(
            db,
            sid,
            user_id=user.get("user_id") if isinstance(user, dict) else None,
            owner_token=owner_token,
        )
        return normalize_scope(session_id=sid)

    user_id, org_id = actor_ids(user if isinstance(user, dict) else None)

    def _check_project():
        with SessionLocal() as sdb:
            return ProjectService.get_project_with_auth(
                sdb, pid, user_id=user_id, org_id=org_id
            )

    project = await asyncio.to_thread(_check_project)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return normalize_scope(project_id=pid)


async def _roots_for_scope(
    db: AsyncSession, scope: Dict[str, str]
) -> List[Path]:
    """会话 scope：收缩到会话目录 + 该会话 uploads + modelops registry。

    项目 scope / 无 scope：保持 DATA_DIR + registry（调用方已过项目 auth）。
    """
    from sqlalchemy import select

    from app.models.upload import UploadRecord
    from app.services.modelops.service import get_modelops_service

    data_root = Path(settings.DATA_DIR).resolve()
    registry_root = Path(get_modelops_service()._settings.registry_dir).resolve()
    session_id = scope.get("session_id")
    if not session_id:
        return [data_root, registry_root]

    roots: List[Path] = [data_root / session_id, registry_root]
    result = await db.execute(
        select(UploadRecord.id, UploadRecord.filename).where(
            UploadRecord.session_id == session_id
        )
    )
    for upload_pk, filename in result.all():
        parts = str(filename or "").replace("\\", "/").split("/")
        upload_key = parts[0] if parts and parts[0] else str(upload_pk)
        roots.append(data_root / "uploads" / upload_key)
    return roots


class PromptSegmentBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    source_uri: str = Field(min_length=1, max_length=_MAX_URI)
    artifact: Optional[Dict[str, Any]] = None
    points: Optional[List[List[float]]] = Field(default=None, max_length=64)
    boxes: Optional[List[List[float]]] = Field(default=None, max_length=64)
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
    _user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    # scope 可选；一旦带 session/project 必须过所有权（#1414）。
    from app.services.modelops.service import get_modelops_service

    scope = await _bind_owner_scope(
        session_id=session_id,
        project_id=project_id,
        user=_user,
        db=db,
        owner_token=owner_token,
        require=False,
    )
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
async def geoai_status(_user: Dict[str, Any] = Depends(get_current_user)) -> dict:
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
async def prompt_segment(
    body: PromptSegmentBody,
    _user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    from app.lib.modelops.errors import ModelOpsError
    from app.lib.modelops.promptable import PromptSpec
    from app.services.modelops.engine import InferenceRequest
    from app.services.modelops.service import get_modelops_service

    scope = await _bind_owner_scope(
        session_id=body.session_id,
        project_id=body.project_id,
        user=_user,
        db=db,
        owner_token=owner_token,
        require=True,
    )
    roots = await _roots_for_scope(db, scope)
    uri = _gate_source_uri(body.source_uri, allowed_roots=roots)
    service = get_modelops_service()
    artifact_id = None
    audit = None
    prompt_crs = bool(body.geographic_coords)
    # review fix：compile 也在 try 内（畸形 artifact 是最常见客户端错误，
    # typed 422 而非 500）；HTTP 面的 artifact 引用根 = DATA_DIR + registry。
    try:
        if body.artifact is not None:
            compiled = service.compile_geo_prompt(
                body.artifact, uri, allowed_roots=roots
            )
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
    except ModelOpsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
async def embed(
    body: EmbedBody,
    _user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    from app.lib.modelops.errors import ModelOpsError
    from app.services.modelops.engine import InferenceRequest
    from app.services.modelops.service import get_modelops_service

    scope = await _bind_owner_scope(
        session_id=body.session_id,
        project_id=body.project_id,
        user=_user,
        db=db,
        owner_token=owner_token,
        require=True,
    )
    roots = await _roots_for_scope(db, scope)
    uri = _gate_source_uri(body.source_uri, allowed_roots=roots)
    service = get_modelops_service()
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


class RefineBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=128)
    source_uri: str = Field(min_length=1, max_length=_MAX_URI)
    candidates_path: str = Field(min_length=1, max_length=_MAX_URI)
    candidate: int = Field(ge=0, le=3)
    session_id: Optional[str] = None
    project_id: Optional[str] = None


@router.post(
    "/geoai/prompt-refine",
    tags=["geoai"],
    summary="候选精化（选定候选 → 内容寻址先验 → 重跑）",
)
async def prompt_refine(
    body: RefineBody,
    _user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    import asyncio

    from app.lib.modelops.errors import ModelOpsError
    from app.services.modelops.service import get_modelops_service

    scope = await _bind_owner_scope(
        session_id=body.session_id,
        project_id=body.project_id,
        user=_user,
        db=db,
        owner_token=owner_token,
        require=True,
    )
    roots = await _roots_for_scope(db, scope)
    uri = _gate_source_uri(body.source_uri, allowed_roots=roots)
    candidates = _gate_source_uri(body.candidates_path, allowed_roots=roots)
    service = get_modelops_service()
    try:
        result, meta = await asyncio.to_thread(
            service.run_prompt_refine,
            body.model_id,
            uri,
            candidates,
            body.candidate,
            session_id=scope.get("session_id"),
            project_id=scope.get("project_id"),
        )
    except ModelOpsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "run_id": result.run_id,
        "status": result.status,
        "outputs": result.outputs,
        "performance": result.perf,
        "refined_from": meta,
    }


#: 预览渲染的最大边（px）——HTTP 面的有界性（不物化整幅）。
_PREVIEW_MAX_DIM = 512


def _render_preview(uri: str, max_dim: int) -> Dict[str, Any]:
    """有界降采样预览（2%-98% 分位拉伸 → RGB PNG base64 + 地理元数据）。"""
    import base64

    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile

    with rasterio.open(uri) as src:
        width, height = src.width, src.height
        if width <= 0 or height <= 0:
            raise HTTPException(status_code=422, detail="raster has empty grid")
        scale = min(max_dim / width, max_dim / height, 1.0)
        out_w = max(1, int(width * scale))
        out_h = max(1, int(height * scale))
        count = min(3, src.count)
        data = src.read(indexes=list(range(1, count + 1)), out_shape=(count, out_h, out_w))
        nodata = src.nodata
        transform = list(src.transform)[:6]
        crs = str(src.crs) if src.crs else None
        bounds = list(src.bounds)
    arr = np.asarray(data, dtype=np.float32)
    # review fix：nodata/NaN 掩蔽后再做分位拉伸（NaN 会让 percentile 全
    # NaN → 预览全黑；nodata 填充值会压垮 2%/98% 分位）。
    finite = np.isfinite(arr)
    if nodata is not None:
        finite &= arr != float(nodata)
    if bool(finite.any()):
        lo = float(np.percentile(arr[finite], 2))
        hi = float(np.percentile(arr[finite], 98))
    else:
        lo, hi = 0.0, 1.0
    arr = np.clip((arr - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    if count == 1:
        arr = np.repeat(arr, 3, axis=0)
    elif count == 2:
        # 2 波段：复制末波段补齐 RGB（PNG 契约 3 通道）。
        arr = np.concatenate([arr, arr[-1:]], axis=0)
    arr = np.where(finite, arr, 0.0)  # nodata/NaN → 固定黑
    rgb = (arr * 255.0).astype("uint8")
    with MemoryFile() as mem:
        with mem.open(
            driver="PNG", width=out_w, height=out_h, count=3, dtype="uint8"
        ) as dst:
            dst.write(rgb)
        png_b64 = base64.b64encode(mem.read()).decode("ascii")
    return {
        "png_base64": png_b64,
        "preview_width": out_w,
        "preview_height": out_h,
        "source_width": width,
        "source_height": height,
        "crs": crs,
        "transform": transform,
        "bounds": bounds,
    }


@router.get(
    "/geoai/preview", tags=["geoai"], summary="栅格有界预览（PNG base64 + 地理元数据）"
)
async def geoai_preview(
    source_uri: str,
    max_dim: int = _PREVIEW_MAX_DIM,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    _user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    import asyncio

    scope = await _bind_owner_scope(
        session_id=session_id,
        project_id=project_id,
        user=_user,
        db=db,
        owner_token=owner_token,
        require=True,
    )
    roots = await _roots_for_scope(db, scope)
    uri = _gate_source_uri(source_uri, allowed_roots=roots)
    dim = max(64, min(int(max_dim), _PREVIEW_MAX_DIM))
    try:
        return await asyncio.to_thread(_render_preview, uri, dim)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — 打不开/损坏栅格 → 422（不泄栈）
        raise HTTPException(
            status_code=422, detail=f"raster preview failed: {type(exc).__name__}"
        ) from exc


@router.get(
    "/geoai/artifact-geojson",
    tags=["geoai"],
    summary="读取 DATA_DIR 内的 GeoJSON 产物（只读；面板消费候选/掩膜几何）",
)
async def artifact_geojson(
    path: str,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
    _user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    import asyncio
    import json as _json

    scope = await _bind_owner_scope(
        session_id=session_id,
        project_id=project_id,
        user=_user,
        db=db,
        owner_token=owner_token,
        require=True,
    )
    roots = await _roots_for_scope(db, scope)
    gated = _gate_source_uri(path, allowed_roots=roots)
    p = Path(gated)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    try:
        size = p.stat().st_size
    except OSError:
        raise HTTPException(status_code=404, detail="artifact not found") from None
    if size > _MAX_ARTIFACT_BYTES:
        raise HTTPException(status_code=413, detail="artifact exceeds size limit")

    def _read():
        payload = _json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            feats = payload.get("features")
            if isinstance(feats, list) and len(feats) > _MAX_INLINE_FEATURES:
                raise ValueError("too many features")
        return payload

    try:
        return await asyncio.to_thread(_read)
    except ValueError as exc:
        if "too many features" in str(exc):
            raise HTTPException(
                status_code=413, detail="artifact exceeds feature limit"
            ) from None
        raise HTTPException(status_code=422, detail="artifact is not valid JSON") from None
    except OSError:
        raise HTTPException(status_code=422, detail="artifact is not valid JSON") from None
