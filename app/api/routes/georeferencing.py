"""G48 Control-Point (GCP) Image Georeferencing & Deliverable GeoRaster HTTP API."""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from app.core.auth import get_current_user_optional
from app.core.owner_tokens import owner_token_digest, transport_owner_token
from app.models.db_model import Conversation
from app.schemas.georeferencing_schema import (
    MAX_UPLOAD_BYTES,
    BindLayerIn,
    CreatePlanIn,
    DraftPlanUpdateIn,
    ExecutePlanIn,
    GeorefBindLayerResponse,
    GeorefDeleteResponse,
    GeorefDomainError,
    GeorefErrorCode,
    GeorefExecuteResponse,
    GeorefJobStatusResponse,
    GeorefPlanListResponse,
    GeorefPlanResponse,
    GeorefPreviewResponse,
    GeorefSourceListResponse,
    GeorefSourceResponse,
    ImportGcpsIn,
    PreviewPlanIn,
    artifact_public_view,
    plan_public_view,
    source_public_view,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.georeferencing import service, store
from app.services.session_data import session_data_manager

router = APIRouter()


def _http_err(exc: GeorefDomainError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.to_payload())


def _owner_info(conv: Conversation, user: Optional[dict], owner_token: Optional[str]) -> dict:
    uid = user.get("user_id") if isinstance(user, dict) else conv.user_id
    oid = user.get("org_id") if isinstance(user, dict) else None
    tok = owner_token or transport_owner_token(getattr(conv, "owner_token", None))
    return {"user_id": uid, "org_id": oid, "owner_token": tok}


async def _sync_owner_verifier(session_id: str, conv: Conversation) -> None:
    """Mirror DB anonymous owner_token digest into session_data_manager so ref reads succeed even before chat."""
    if getattr(conv, "user_id", None) is None:
        raw_tok = getattr(conv, "owner_token", None)
        if raw_tok:
            try:
                await session_data_manager.set_map_state(
                    session_id, "owner_token_digest", owner_token_digest(raw_tok)
                )
            except Exception:  # noqa: BLE001 — defensive sync
                pass


@router.post(
    "/georeferencing/sources/upload",
    response_model=GeorefSourceResponse,
    tags=["控制点影像配准 (G48)"],
)
async def upload_georef_source(
    session_id: str = Query(..., min_length=8, max_length=128),
    ignore_exif: bool = Query(False),
    file: UploadFile = File(...),
    _conv: Conversation = Depends(require_owned_session),
):
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": GeorefErrorCode.SOURCE_TOO_LARGE.value,
                    "message": f"Upload exceeds {MAX_UPLOAD_BYTES} bytes limit.",
                },
            )
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        src = await service.upload_source(
            session_id=session_id,
            filename=file.filename or "upload.png",
            raw_bytes=raw,
            ignore_exif=ignore_exif,
        )
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefSourceResponse(
        source=source_public_view(src),
        image_url=f"/api/v1/georeferencing/sources/{src.source_id}/image?session_id={session_id}&v={src.normalized_sha256[:12]}",
    )


@router.get(
    "/georeferencing/sources",
    response_model=GeorefSourceListResponse,
    tags=["控制点影像配准 (G48)"],
)
async def list_georef_sources(
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    sources = await asyncio.to_thread(store.list_sources, session_id)
    return GeorefSourceListResponse(
        session_id=session_id,
        sources=[source_public_view(x) for x in sources],
    )


@router.get(
    "/georeferencing/sources/{source_id}",
    response_model=GeorefSourceResponse,
    tags=["控制点影像配准 (G48)"],
)
async def get_georef_source(
    source_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        src = await asyncio.to_thread(service.require_source, session_id, source_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefSourceResponse(
        source=source_public_view(src),
        image_url=f"/api/v1/georeferencing/sources/{src.source_id}/image?session_id={session_id}&v={src.normalized_sha256[:12]}",
    )


@router.get(
    "/georeferencing/sources/{source_id}/image",
    tags=["控制点影像配准 (G48)"],
)
async def stream_georef_source_image(
    source_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        path = await asyncio.to_thread(service.get_source_viewer_image_path, session_id, source_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.delete(
    "/georeferencing/sources/{source_id}",
    response_model=GeorefDeleteResponse,
    tags=["控制点影像配准 (G48)"],
)
async def delete_georef_source(
    source_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        released = await service.delete_source(session_id, source_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefDeleteResponse(deleted=True, source_id=source_id, released_blobs=released)


@router.post(
    "/georeferencing/plans",
    response_model=GeorefPlanResponse,
    tags=["控制点影像配准 (G48)"],
)
async def create_georef_plan(
    body: CreatePlanIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    if body.session_id and body.session_id != session_id:
        raise HTTPException(
            status_code=422,
            detail={"code": GeorefErrorCode.INVALID_GCP_COORDS.value, "message": "Body session_id disagrees with query session_id."},
        )
    try:
        plan = await asyncio.to_thread(
            service.create_plan,
            session_id=session_id,
            source_id=body.source_id,
            name=body.name,
            target_crs=body.target_crs,
            resampling=body.resampling,
            target_resolution_m=body.target_resolution_m,
            generate_cog=body.generate_cog,
        )
        src = await asyncio.to_thread(service.require_source, session_id, plan.source_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefPlanResponse(plan=plan_public_view(plan), source=source_public_view(src))


@router.get(
    "/georeferencing/plans",
    response_model=GeorefPlanListResponse,
    tags=["控制点影像配准 (G48)"],
)
async def list_georef_plans(
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    plans = await asyncio.to_thread(store.list_plans, session_id)
    return GeorefPlanListResponse(
        session_id=session_id,
        plans=[plan_public_view(p) for p in plans],
    )


@router.get(
    "/georeferencing/plans/{plan_id}",
    response_model=GeorefPlanResponse,
    tags=["控制点影像配准 (G48)"],
)
async def get_georef_plan(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        plan = await asyncio.to_thread(service.require_plan, session_id, plan_id)
        src = await asyncio.to_thread(store.get_source, session_id, plan.source_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefPlanResponse(
        plan=plan_public_view(plan),
        source=source_public_view(src) if src is not None else None,
    )


@router.put(
    "/georeferencing/plans/{plan_id}/draft",
    response_model=GeorefPlanResponse,
    tags=["控制点影像配准 (G48)"],
)
async def update_georef_draft(
    plan_id: str,
    body: DraftPlanUpdateIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        plan = await asyncio.to_thread(
            service.revise_draft,
            session_id=session_id,
            plan_id=plan_id,
            expected_revision=body.expected_revision,
            name=body.name,
            target_crs=body.target_crs,
            resampling=body.resampling,
            target_resolution_m=body.target_resolution_m,
            clear_target_resolution=body.clear_target_resolution,
            generate_cog=body.generate_cog,
            gcps=body.gcps,
        )
        src = await asyncio.to_thread(store.get_source, session_id, plan.source_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefPlanResponse(
        plan=plan_public_view(plan),
        source=source_public_view(src) if src is not None else None,
    )


@router.post(
    "/georeferencing/plans/{plan_id}/gcps/import",
    response_model=GeorefPlanResponse,
    tags=["控制点影像配准 (G48)"],
)
async def import_georef_gcps(
    plan_id: str,
    body: ImportGcpsIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        plan = await asyncio.to_thread(
            service.import_gcps,
            session_id=session_id,
            plan_id=plan_id,
            expected_revision=body.expected_revision,
            fmt=body.format,
            content=body.content,
            replace_existing=body.replace_existing,
            coords_are_wgs84=body.coords_are_wgs84,
        )
        src = await asyncio.to_thread(store.get_source, session_id, plan.source_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefPlanResponse(
        plan=plan_public_view(plan),
        source=source_public_view(src) if src is not None else None,
    )


@router.get(
    "/georeferencing/plans/{plan_id}/gcps/export",
    tags=["控制点影像配准 (G48)"],
)
async def export_georef_gcps(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    fmt: Literal["json", "csv"] = Query("json"),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        content, media_type, fname = await asyncio.to_thread(service.export_gcps, session_id, plan_id, fmt=fmt)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post(
    "/georeferencing/plans/{plan_id}/preview",
    response_model=GeorefPreviewResponse,
    tags=["控制点影像配准 (G48)"],
)
async def preview_georef_plan(
    plan_id: str,
    body: Optional[PreviewPlanIn] = None,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        resp = await asyncio.to_thread(
            service.generate_preview,
            session_id=session_id,
            plan_id=plan_id,
            expected_revision=body.expected_revision if body else None,
        )
        return resp.model_copy(update={"plan": plan_public_view(resp.plan)})
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc


@router.get(
    "/georeferencing/plans/{plan_id}/preview/image",
    tags=["控制点影像配准 (G48)"],
)
async def get_georef_preview_image(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        path = await asyncio.to_thread(service.get_preview_image_path, session_id, plan_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return FileResponse(path, media_type="image/png")


@router.post(
    "/georeferencing/plans/{plan_id}/execute",
    response_model=GeorefExecuteResponse,
    tags=["控制点影像配准 (G48)"],
)
async def execute_georef_plan(
    plan_id: str,
    body: ExecutePlanIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    user: Optional[dict] = Depends(get_current_user_optional),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    await _sync_owner_verifier(session_id, conv)
    owner = _owner_info(conv, user, owner_token)
    try:
        res = await service.execute_plan(
            session_id=session_id,
            plan_id=plan_id,
            expected_revision=body.expected_revision,
            background=body.background,
            idempotency_key=body.idempotency_key,
            owner_id=owner["user_id"],
            owner_token=owner["owner_token"],
            org_id=owner["org_id"],
            project_id=body.project_id,
        )
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefExecuteResponse(
        plan_id=res["plan_id"],
        job_id=res["job_id"],
        execution_id=res["execution_id"],
        status=res["status"],
        background=res["background"],
        idempotent_reuse=res["idempotent_reuse"],
        artifact=artifact_public_view(res.get("artifact")),
        poll_url=f"/api/v1/georeferencing/plans/{plan_id}/status?session_id={session_id}",
    )


@router.post(
    "/georeferencing/plans/{plan_id}/cancel",
    response_model=GeorefJobStatusResponse,
    tags=["控制点影像配准 (G48)"],
)
async def cancel_georef_plan(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        res = await service.cancel_job(session_id, plan_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefJobStatusResponse(**{
        **res,
        "plan": plan_public_view(res["plan"]),
        "artifact": artifact_public_view(res.get("artifact")),
    })


@router.get(
    "/georeferencing/plans/{plan_id}/status",
    response_model=GeorefJobStatusResponse,
    tags=["控制点影像配准 (G48)"],
)
async def get_georef_status(
    plan_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        res = await asyncio.to_thread(service.get_status, session_id, plan_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return GeorefJobStatusResponse(**{
        **res,
        "plan": plan_public_view(res["plan"]),
        "artifact": artifact_public_view(res.get("artifact")),
    })


@router.post(
    "/georeferencing/plans/{plan_id}/bind-layer",
    response_model=GeorefBindLayerResponse,
    tags=["控制点影像配准 (G48)"],
)
async def bind_georef_mapspec_layer(
    plan_id: str,
    body: BindLayerIn,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    await _sync_owner_verifier(session_id, conv)
    tok = owner_token or transport_owner_token(getattr(conv, "owner_token", None))
    try:
        res = await service.bind_mapspec_layer(
            session_id=session_id,
            plan_id=plan_id,
            expected_mapspec_revision=body.expected_mapspec_revision,
            layer_id=body.layer_id,
            layer_name=body.layer_name,
            opacity=body.opacity,
            owner_token=tok,
            origin="user",
            actor="georeferencing_workbench",
        )
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return res.model_copy(update={"artifact": artifact_public_view(res.artifact)})


@router.get(
    "/georeferencing/artifacts/{artifact_id}/download",
    tags=["控制点影像配准 (G48)"],
)
async def download_georef_artifact(
    artifact_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    _conv: Conversation = Depends(require_owned_session),
):
    try:
        path, art = await asyncio.to_thread(service.resolve_authorized_artifact_download, session_id, artifact_id)
    except GeorefDomainError as exc:
        raise _http_err(exc) from exc
    return FileResponse(
        path,
        media_type="image/tiff",
        filename=f"{art.plan_id}_{artifact_id}.tif",
        headers={"X-Georef-Sha256": art.sha256},
    )
