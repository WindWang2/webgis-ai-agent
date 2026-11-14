"""视觉上下文证据的受权入口（G36 / ADR-0291）。

端点（全部 ``require_owned_session`` owner 门）：
- ``POST /chat/sessions/{sid}/visual-context/evidence`` —— multipart intake
  （参考图 / 地图视口 / 框选局部）；服务端真实解码 + 派生 + 登记证据。
- ``GET  /chat/sessions/{sid}/visual-context/evidence`` —— 列表（元数据）。
- ``GET  /chat/sessions/{sid}/visual-context/evidence/{eid}/image`` ——
  受权字节读取（derived 或缩略图；sha 校验）。
- ``DELETE /chat/sessions/{sid}/visual-context/evidence/{eid}`` —— 撤销。

红线：回执只带 ref/元数据（绝不动 base64）；日志只留 ids/hashes；
``layout_preview`` 未验证组合页链路前返回 typed unsupported（V08）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.auth_history_bridge import Conversation, require_owned_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["AI对话"])


class VisualEvidenceResponse(BaseModel):
    """intake/列表条目（ref-only；无字节）。"""

    session_id: str
    evidence_id: str
    source_type: str
    mime: str
    origin_sha256: str
    derived_sha256: str
    derived_size: int = 0
    width: int
    height: int
    origin_width: int = 0
    origin_height: int = 0
    mapspec_revision: Optional[int] = None
    mapspec_fingerprint: str = ""
    viewport_hash: str = ""
    crop: Optional[Dict[str, int]] = None
    label: str = ""
    state: str
    observations: List[str] = Field(default_factory=list)


class VisualEvidenceListResponse(BaseModel):
    session_id: str
    evidence: List[VisualEvidenceResponse]


def _record_to_response(session_id: str, rec) -> VisualEvidenceResponse:
    return VisualEvidenceResponse(
        session_id=session_id,
        evidence_id=rec.evidence_id,
        source_type=rec.source_type,
        mime=rec.mime,
        origin_sha256=rec.origin_sha256,
        derived_sha256=rec.derived_sha256,
        derived_size=rec.derived_size,
        width=rec.width,
        height=rec.height,
        origin_width=rec.origin_width,
        origin_height=rec.origin_height,
        mapspec_revision=rec.mapspec_revision,
        mapspec_fingerprint=rec.mapspec_fingerprint,
        viewport_hash=rec.viewport_hash,
        crop=rec.crop,
        label=rec.label,
        state=rec.state,
        observations=list(rec.observations),
    )


async def _parse_json_field(raw: str, field: str, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_json_field",
                    "message": f"{field} 不是合法 JSON。"},
        ) from None


@router.post(
    "/sessions/{session_id}/visual-context/evidence",
    response_model=VisualEvidenceResponse,
)
async def intake_visual_evidence(
    session_id: str,
    image: UploadFile = File(...),
    source_type: str = Form(...),
    label: str = Form(""),
    mapspec_revision: Optional[int] = Form(None),
    mapspec_fingerprint: str = Form(""),
    viewport: str = Form(""),
    dpr: float = Form(1.0, ge=0.25, le=8.0),
    crop: str = Form(""),
    origin_width: int = Form(0, ge=0, le=16384),
    origin_height: int = Form(0, ge=0, le=16384),
    _conv: Conversation = Depends(require_owned_session),
) -> VisualEvidenceResponse:
    """受权 intake：真实解码 → 送模派生 → 证据登记（draft）。"""
    from app.services.durable_blob_store import sha256_of_bytes
    from app.services.visual_context.decoder import (
        MAX_INTAKE_BYTES,
        decode_image_async,
        derive_model_image_async,
    )
    from app.services.visual_context.evidence import (
        INTAKE_SOURCE_TYPES,
        EvidenceRejected,
        VisualEvidenceRecord,
        mint_evidence_id,
    )
    from app.services.visual_context.store import register_evidence, viewport_hash_of

    if source_type == "layout_preview":
        # V08：组合版面链路未验证前 typed unsupported（视口不冒充纸面）。
        raise HTTPException(
            status_code=422,
            detail={"error": "layout_preview_unsupported",
                    "message": "出版版面附图尚未支持；请附当前地图视口。"},
        )
    if source_type not in INTAKE_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"error": "unsupported_source_type",
                    "message": "来源类型不支持。"},
        )

    data = await image.read(MAX_INTAKE_BYTES + 1)
    if len(data) > MAX_INTAKE_BYTES:
        raise HTTPException(
            status_code=400,
            detail={"error": "intake_oversized",
                    "message": "图片超过 8MiB 上限。"},
        )
    viewport_dict = await _parse_json_field(viewport, "viewport", {})
    crop_dict = await _parse_json_field(crop, "crop", None)

    try:
        decoded = await decode_image_async(data)
        derived_bytes, mime, w, h = await derive_model_image_async(
            decoded, crop=(crop_dict if source_type == "map_region" else None))
    except EvidenceRejected as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": exc.reason, "message": exc.message},
        ) from None

    record = VisualEvidenceRecord(
        evidence_id=mint_evidence_id(),
        session_id=session_id,
        source_type=source_type,
        mime=mime,
        origin_sha256=sha256_of_bytes(data),
        derived_sha256=sha256_of_bytes(derived_bytes),
        origin_size=len(data),
        derived_size=len(derived_bytes),
        width=w,
        height=h,
        origin_width=decoded.width,
        origin_height=decoded.height,
        mapspec_revision=mapspec_revision if source_type in ("map_viewport", "map_region") else None,
        mapspec_fingerprint=(mapspec_fingerprint[:96]
                             if source_type in ("map_viewport", "map_region") else ""),
        viewport=viewport_dict if isinstance(viewport_dict, dict) else {},
        viewport_hash=viewport_hash_of(viewport_dict) if source_type in ("map_viewport", "map_region") else "",
        dpr=float(dpr),
        crop=crop_dict if isinstance(crop_dict, dict) else None,
        label=label[:120],
        state="draft",
        observations=list(decoded.observations),
    )
    try:
        saved = await register_evidence(session_id, derived_bytes, record)
    except EvidenceRejected as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": exc.reason, "message": exc.message},
        ) from None
    return _record_to_response(session_id, saved)


@router.get(
    "/sessions/{session_id}/visual-context/evidence",
    response_model=VisualEvidenceListResponse,
)
async def list_visual_evidence(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> VisualEvidenceListResponse:
    from app.services.visual_context.store import list_evidence

    records = await list_evidence(session_id)
    return VisualEvidenceListResponse(
        session_id=session_id,
        evidence=[_record_to_response(session_id, r) for r in records],
    )


@router.get("/sessions/{session_id}/visual-context/evidence/{evidence_id}/image")
async def read_visual_evidence_image(
    session_id: str,
    evidence_id: str,
    variant: str = Query("derived", pattern="^(derived|thumbnail)$"),
    _conv: Conversation = Depends(require_owned_session),
) -> Response:
    """受权字节读取（sha 校验；缩略图按需生成，内存有界）。"""
    from app.services.visual_context.decoder import make_thumbnail
    from app.services.visual_context.store import resolve_evidence

    resolved = await resolve_evidence(
        session_id, evidence_id, max_bytes=8 * 1024 * 1024)
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "evidence_unavailable",
                    "message": "证据不存在、已撤销或会话无权读取。"},
        )
    rec, data = resolved
    if variant == "thumbnail":
        import asyncio

        thumb = await asyncio.to_thread(make_thumbnail, data)
        return Response(
            content=thumb, media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600",
                     "ETag": f'"{rec.derived_sha256[:16]}-thumb"'})
    return Response(
        content=data, media_type=rec.mime or "image/png",
        headers={"Cache-Control": "private, max-age=3600",
                 "ETag": f'"{rec.derived_sha256[:16]}"'})


@router.delete(
    "/sessions/{session_id}/visual-context/evidence/{evidence_id}",
    response_model=VisualEvidenceResponse,
)
async def revoke_visual_evidence(
    session_id: str,
    evidence_id: str,
    _conv: Conversation = Depends(require_owned_session),
) -> VisualEvidenceResponse:
    """撤销（tombstone + blob 引用释放；V33 由 bridge 台账在下一 turn 复验）。"""
    from app.services.visual_context.evidence import EvidenceRejected
    from app.services.visual_context.store import revoke_evidence

    try:
        rec = await revoke_evidence(session_id, evidence_id)
    except EvidenceRejected as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": exc.reason, "message": exc.message},
        ) from None
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "evidence_not_found",
                    "message": "证据不存在或属于其他会话。"},
        )
    return _record_to_response(session_id, rec)
