"""G20 栅格表达工作台 HTTP 面（UI 与 Pi 工具共用 raster_authoring.service）。

新文件优先：``layer.py`` 保持零修改，旧 XYZ tile / 旧 PNG / 外部底图路径
逐字回归。本路由复用 layer.py 的 owner-scoped 路径解析、ETag 响应、
``_layer_data_budget``，以及 mvt 的 singleflight / LRU epoch 失效。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import get_current_user_optional_with_version
from app.models.db_model import Conversation
from app.schemas.raster_spec import RasterExpressionError
from app.services.auth_history_bridge import require_owned_session
from app.services.mvt import single_flight, tile_lru_cache
from app.services.raster_authoring import (
    commit_expression,
    describe_raster,
    export_snapshot,
    probe_raster,
    validate_expression,
)
from app.services.raster_authoring.errors import error_payload, http_status
from app.services.raster_authoring.render import render_tile
from app.services.raster_authoring.service import (
    resolve_committed_expression,
    resolve_source,
)

router = APIRouter(prefix="/raster-authoring", tags=["栅格表达工作台"])


def _fail(err: RasterExpressionError) -> HTTPException:
    return HTTPException(status_code=http_status(err), detail=error_payload(err))


async def _owned(session_id: str, owner_token: Optional[str], conv: Conversation) -> str:
    """会话归属由 require_owned_session 守卫；这里只取用于 ref 读取的 token。"""
    return owner_token or getattr(conv, "owner_token", None)


# ─── 请求体 ────────────────────────────────────────────────────────────────

class ProbeBody(BaseModel):
    ref_id: str = Field(min_length=1, max_length=128)
    points: List[Any] = Field(min_length=1, max_length=16)
    crs: Optional[str] = Field(default="EPSG:4326", max_length=64)
    bands: Optional[List[int]] = None
    data_revision: Optional[str] = Field(default=None, max_length=128)


class ExpressionBody(BaseModel):
    ref_id: str = Field(min_length=1, max_length=128)
    expression: Dict[str, Any]
    layer_id: Optional[str] = Field(default=None, max_length=200)


class CommitBody(ExpressionBody):
    layer_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)
    client_mutation_id: Optional[str] = Field(default=None, max_length=128)


class ExportBody(BaseModel):
    expected_revision: Optional[int] = Field(default=None, ge=0)
    format: str = Field(default="png", pattern=r"^(png|svg|pdf)$")


# ─── 已登记栅格 ref 列表（工作台选择器；有界、owner-scoped）─────────────────

@router.get("/refs")
async def list_raster_refs(
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    from app.services.session_data import session_data_manager

    refs = await session_data_manager.list_refs(session_id)
    token = await _owned(session_id, owner_token, conv)
    out = []
    for ref_id in list(refs)[:32]:
        res = await session_data_manager.get_ref_data(
            session_id, ref_id, owner_token=token
        )
        if not res.success or not isinstance(res.data, dict):
            continue
        payload = res.data
        path = payload.get("file_path") or payload.get("path")
        if not isinstance(path, str) or not path:
            continue
        out.append({
            "ref_id": ref_id,
            "alias": refs.get(ref_id, ""),
            "kind": payload.get("kind"),
            # 未开文件头验证；选定后 descriptor 端点才做真实读取与披露。
            "verified": False,
        })
    return {"success": True, "refs": out, "truncated": len(refs) > 32}


# ─── 元数据 / 直方图 ───────────────────────────────────────────────────────

@router.get("/descriptor/{ref_id}")
async def descriptor(
    ref_id: str,
    session_id: str = Query(..., min_length=8, max_length=128),
    histogram_bands: Optional[str] = Query(None, description="逗号分隔的 1-based 波段"),
    buckets: Optional[int] = Query(None, ge=2, le=64),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        bands = None
        if histogram_bands:
            bands = [int(b.strip()) for b in histogram_bands.split(",") if b.strip()]
        return await describe_raster(
            session_id, ref_id,
            owner_token=await _owned(session_id, owner_token, conv),
            histogram_bands=bands, buckets=buckets,
        )
    except RasterExpressionError as err:
        raise _fail(err) from err
    except (ValueError, TypeError) as err:
        raise HTTPException(status_code=400, detail="histogram_bands 必须是 1-based CSV") from err


# ─── 真实像元检视（只读，不写 MapSpec）──────────────────────────────────────

@router.post("/probe")
async def probe(
    body: ProbeBody,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await probe_raster(
            session_id, body.ref_id, body.points,
            crs=body.crs, bands=body.bands,
            owner_token=await _owned(session_id, owner_token, conv),
            expected_data_revision=body.data_revision,
        )
    except RasterExpressionError as err:
        raise _fail(err) from err


# ─── 预览 / 提交（同一领域服务）────────────────────────────────────────────

@router.post("/expression/preview")
async def preview(
    body: ExpressionBody,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await validate_expression(
            session_id, body.ref_id, body.expression,
            layer_id=body.layer_id,
            owner_token=await _owned(session_id, owner_token, conv),
        )
    except RasterExpressionError as err:
        raise _fail(err) from err


@router.post("/expression/commit")
async def commit(
    body: CommitBody,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await commit_expression(
            session_id, body.ref_id, body.layer_id, body.expression,
            expected_revision=body.expected_revision,
            owner_token=await _owned(session_id, owner_token, conv),
            origin="user", actor="raster_authoring_panel",
            mutation_id=f"raster:{body.client_mutation_id}" if body.client_mutation_id else None,
        )
    except RasterExpressionError as err:
        raise _fail(err) from err


# ─── 真实瓦片（样式来自当前权威 MapSpec）────────────────────────────────────

@router.get("/tiles/{ref_id}/{z}/{x}/{y}.png")
async def styled_tile(
    ref_id: str,
    z: int,
    x: int,
    y: int,
    session_id: str = Query(..., min_length=8, max_length=128),
    layer_id: str = Query(..., min_length=1, max_length=200),
    style_id: str = Query(..., min_length=8, max_length=64),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    if_none_match: Optional[str] = Header(None, alias="If-None-Match"),
    conv: Conversation = Depends(require_owned_session),
):
    """用 layer 的已提交表达渲染 XYZ PNG。

    - 凭证由前端 ``buildTileTransformRequest`` 注入 header（URL 不含 owner token）；
    - ``style_id`` 与当前 MapSpec 比对，过期 URL 直接 409，不出旧像素；
    - 缓存身份含 (style_id, layer_id, data_revision)；复用 singleflight / epoch / ETag。
    """
    from app.api.routes.layer import _layer_data_budget, _png_tile_response

    try:
        await _layer_data_budget(session_id)
        if not ref_id or len(ref_id) > 128 or any(c.isspace() for c in ref_id):
            raise RasterExpressionError(
                "SOURCE_UNAVAILABLE", "invalid raster ref_id", status_code=404,
            )
        if not (0 <= z <= 20) or x < 0 or y < 0 or x >= (1 << z) or y >= (1 << z):
            raise HTTPException(status_code=400, detail="非法瓦片坐标")

        committed = await resolve_committed_expression(
            session_id, layer_id, style_id=style_id
        )
        if committed.ref_id != ref_id:
            raise RasterExpressionError(
                "STALE_STYLE", "tile ref does not match the committed layer source",
                status_code=409,
            )
        source = await resolve_source(
            session_id, ref_id, await _owned(session_id, owner_token, conv)
        )
        if source.data_revision != committed.data_revision:
            raise RasterExpressionError(
                "STALE_BINDING", "source data revision changed after the style commit",
                status_code=409,
            )

        cache_key = (
            session_id, ref_id, z, x, y,
            layer_id, committed.spec.style_id, source.data_revision,
        )
        cached = tile_lru_cache.get(cache_key)
        if cached is not None:
            return _png_tile_response(cached, if_none_match)

        async def _compute() -> bytes:
            epoch = tile_lru_cache.get_epoch(session_id, ref_id)
            png = await asyncio.to_thread(
                render_tile, source.path, z, x, y, committed.spec
            )
            # 失效后完成的旧请求不回灌缓存（P1-1 既有纪律）。
            tile_lru_cache.put_if_current(cache_key, png, epoch)
            return png

        png_bytes = await single_flight.run(cache_key, _compute)
        return _png_tile_response(png_bytes, if_none_match)
    except RasterExpressionError as err:
        raise _fail(err) from err


# ─── 单幅同语义导出（冻结修订 + 受权下载）──────────────────────────────────

@router.post("/export")
async def export_map(
    body: ExportBody,
    session_id: str = Query(..., min_length=8, max_length=128),
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
    user: dict = Depends(get_current_user_optional_with_version),
):
    """冻结一次 MapSpec 修订 → 真实 PNG / 离线可读 SVG → 受权下载引用。"""
    from app.services.raster_authoring.delivery import persist_snapshot_artifact

    try:
        artifact = await export_snapshot(
            session_id,
            expected_revision=body.expected_revision,
            fmt=body.format,
            owner_token=await _owned(session_id, owner_token, conv),
        )
        uid = user.get("user_id") if isinstance(user, dict) else None
        principal = str(uid) if uid and uid != "anonymous" else f"session:{session_id}"
        return await persist_snapshot_artifact(
            session_id, artifact, principal=principal
        )
    except RasterExpressionError as err:
        raise _fail(err) from err
    except OSError as err:
        raise HTTPException(status_code=503, detail="导出文件持久化失败") from err
