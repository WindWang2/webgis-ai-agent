"""交互式只读地图发布 API（04 线）——作者面 + 读者面。

两条入口（REST UI / Pi 工具）共用 `app/services/publication` 唯一领域服务；
读者面是**唯一新增产品中心**：匿名 + share_code + 限流 + 字段投影，
无编辑端 API、无 Agent 私有入口。

安全语义：
- 作者面：会话归属（verify_session_owner）+ 发布行三元组归属校验；
  publish 需显式 confirm；link 签发是显式动作。
- 读者面：share_code 校验（唯一索引 + 常量时间复核）+ 过期/撤销 fail-closed
  （一律 404，不泄露存在性）+ per-IP 限流 + 响应 nosniff/no-referrer。
- 字段边界：where/order/fields 引用的字段必须在层白名单内（防布尔推断
  泄露白名单外字段）。
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user_optional, get_owner_token
from app.core.database import get_async_db
from app.lib.geojson_serializer import serialize_geojson
from app.models.api_response import ApiResponse
from app.models.map_publication import MapPublication
from app.schemas.publication_schema import (
    PublicationCreateRequest,
    PublicationLinkRequest,
    PublicationPreviewRequest,
    PublicationPublishRequest,
)
from app.services.auth_history_bridge import verify_session_owner
from app.services.feature_pages import (
    FeaturePageError,
    count_matching,
    parse_bbox_param,
    parse_fields_param,
    parse_order_param,
    parse_where_param,
)
from app.services.publication import reader as pub_reader
from app.services.publication import store as pub_store
from app.services.publication.access import (
    limit_publication_meta,
    limit_publication_read,
)
from app.services.publication.contract import DataMode
from app.services.publication.service import (
    PUBLICATION_DISPLAY_NAME,
    PUBLICATION_TASK_TYPE,
    PublicationRequestError,
    create_draft,
    mint_link,
    plan_publication_from_session,
    require_owned_publication,
    revoke_link,
    revoke_publication,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/publications", tags=["只读地图发布"])

#: 读者响应安全头（JSON 面：nosniff + 不进 referer + 不缓存授权内容）。
_READER_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, no-store",
}


def _json_body(data: Any) -> str:
    """ApiResponse 信封 → JSON 字符串（Response 面需要自定义安全头时使用）。"""
    import json

    return json.dumps(
        ApiResponse.ok(data=data).model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,  # R1 P3-7：NaN/Inf 不产出非法 JSON（fail-loud）
    )


def _publication_view(publication: MapPublication, *, include_snapshot: bool = False) -> Dict[str, Any]:
    view: Dict[str, Any] = {
        "id": publication.id,
        "product_id": publication.product_id,
        "version": publication.version,
        "session_id": publication.session_id,
        "title": publication.title,
        "status": publication.status,
        "access_policy": publication.access_policy,
        "has_share_link": bool(publication.share_code),
        "share_expires_at": publication.share_expires_at.isoformat() if publication.share_expires_at else None,
        "spec_fingerprint": publication.spec_fingerprint,
        "spec_revision": publication.spec_revision,
        "materialized": bool(publication.materialized),
        "created_at": publication.created_at.isoformat() if publication.created_at else None,
        "published_at": publication.published_at.isoformat() if publication.published_at else None,
        "revoked_at": publication.revoked_at.isoformat() if publication.revoked_at else None,
    }
    snapshot = publication.snapshot or {}
    view["layer_count"] = len(snapshot.get("layers") or [])
    view["blockers"] = snapshot.get("blockers") or []
    view["qa"] = snapshot.get("qa") or {}
    view["snapshot_fingerprint"] = snapshot.get("snapshot_fingerprint")
    if include_snapshot:
        view["snapshot"] = snapshot
    return view


# ── 作者面 ───────────────────────────────────────────────────────────


@router.post("/preview")
async def preview_publication(
    body: PublicationPreviewRequest,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    """发布计划预览（纯规划，无落盘/无行创建）。"""
    await verify_session_owner(db, body.session_id, user_id=user.get("user_id"), owner_token=owner_token)
    planned = await plan_publication_from_session(
        body.session_id,
        selected_layer_ids=body.selected_layer_ids,
        field_whitelists=body.field_whitelists,
    )
    plan = planned["plan"]
    plan["hydrated_source_count"] = len(planned["hydrated"])
    return ApiResponse.ok(data=plan)


@router.post("")
async def create_publication_draft(
    body: PublicationCreateRequest,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    """创建发布草稿（private，无公开副作用）。"""
    await verify_session_owner(db, body.session_id, user_id=user.get("user_id"), owner_token=owner_token)
    try:
        row = await create_draft(
            db,
            session_id=body.session_id,
            owner_token=owner_token,
            creator_id=user.get("user_id"),
            org_id=user.get("org_id"),
            meta_params={
                "title": body.title,
                "purpose": body.purpose,
                "attribution": body.attribution,
                "license": body.license,
                "missing_values_note": body.missing_values_note,
            },
            selected_layer_ids=body.selected_layer_ids,
            field_whitelists=body.field_whitelists,
            alt_text=body.alt_text,
            palette=body.palette,
            base_publication_id=body.base_publication_id,
        )
    except PublicationRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"reason": exc.reason, "message": exc.message})
    return ApiResponse.ok(data=_publication_view(row))


@router.post("/{publication_id}/publish")
async def publish_publication(
    publication_id: str,
    body: PublicationPublishRequest,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    """发布（显式 confirm；durable job 物化 + CAS published）。"""
    try:
        publication = await require_owned_publication(
            db, publication_id, creator_id=user.get("user_id"), owner_token=owner_token
        )
        if not body.confirm:
            raise PublicationRequestError(
                "confirmation_required",
                "发布需要显式确认（confirm=true）——对外可见副作用由作者明确批准",
                status_code=400,
            )
        # 提交期快速门（job 内仍二次校验 —— 框架语义保留）。
        from app.services.publication.contract import PublicationMeta
        from app.services.publication.service import (
            license_blocker_if_missing,
            publish_blockers,
        )

        hard = publish_blockers(publication.snapshot or {})
        lic = license_blocker_if_missing(
            PublicationMeta(**(publication.snapshot or {}).get("meta", {}))
        )
        if lic is not None:
            hard.append(lic)
        if hard:
            raise PublicationRequestError(
                hard[0].reason.value, f"发布被阻断：{hard[0].message}", status_code=422
            )
    except PublicationRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"reason": exc.reason, "message": exc.message})

    from app.services.jobs.lifecycle import JobKind
    from app.services.jobs.submit import submit_durable_job
    from app.services.publication.tasks import run_publication_publish

    submit_result = submit_durable_job(
        celery_task=run_publication_publish,
        task_type=PUBLICATION_TASK_TYPE,
        display_name=f"{PUBLICATION_DISPLAY_NAME} · {publication.title[:24]}",
        params={"publication_id": publication.id},
        kind=JobKind.map_publication,
        session_id=publication.session_id,
        idempotent=True,
    )
    return ApiResponse.ok(data={
        "publication": _publication_view(publication),
        "job": submit_result,
    })


@router.post("/{publication_id}/link")
async def create_share_link(
    publication_id: str,
    body: PublicationLinkRequest,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    """签发可撤销只读链接（显式动作；对外可见副作用）。"""
    try:
        publication = await require_owned_publication(
            db, publication_id, creator_id=user.get("user_id"), owner_token=owner_token
        )
        code = await mint_link(db, publication, ttl_days=body.ttl_days)
    except PublicationRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"reason": exc.reason, "message": exc.message})
    return ApiResponse.ok(data={
        "share_code": code,
        "share_url": f"/p/{code}",
        "api_base": f"/api/v1/publications/shared/{code}",
        "ttl_days": body.ttl_days,
        "expires_at": publication.share_expires_at.isoformat() if publication.share_expires_at else None,
    })


@router.delete("/{publication_id}/link")
async def delete_share_link(
    publication_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    try:
        publication = await require_owned_publication(
            db, publication_id, creator_id=user.get("user_id"), owner_token=owner_token
        )
        await revoke_link(db, publication)
    except PublicationRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"reason": exc.reason, "message": exc.message})
    return ApiResponse.ok(data={"revoked": True, "id": publication.id})


@router.post("/{publication_id}/revoke")
async def revoke_publication_route(
    publication_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    try:
        publication = await require_owned_publication(
            db, publication_id, creator_id=user.get("user_id"), owner_token=owner_token
        )
        moved = await revoke_publication(db, publication)
    except PublicationRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"reason": exc.reason, "message": exc.message})
    return ApiResponse.ok(data={"revoked": bool(moved), "id": publication.id, "status": publication.status})


@router.get("/{publication_id}")
async def get_publication_route(
    publication_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    try:
        publication = await require_owned_publication(
            db, publication_id, creator_id=user.get("user_id"), owner_token=owner_token
        )
        versions = await pub_store.list_product_versions(db, publication.product_id)
    except PublicationRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"reason": exc.reason, "message": exc.message})
    return ApiResponse.ok(data={
        **_publication_view(publication),
        "versions": [
            {"id": v.id, "version": v.version, "status": v.status,
             "spec_fingerprint": v.spec_fingerprint,
             "published_at": v.published_at.isoformat() if v.published_at else None}
            for v in versions
        ],
    })


@router.get("/sessions/{session_id}")
async def list_session_publications(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> Response:
    await verify_session_owner(db, session_id, user_id=user.get("user_id"), owner_token=owner_token)
    rows = await pub_store.list_session_publications(db, session_id)
    return ApiResponse.ok(data={"publications": [_publication_view(r) for r in rows]})


# ── 读者面（匿名 + share_code + 限流） ───────────────────────────────


async def _resolve_reader(db: AsyncSession, share_code: str):
    publication, denial = await pub_reader.resolve_reader_publication(db, share_code)
    if denial is not None or publication is None:
        # 统一 404：不泄露存在性/撤销/过期的差异。
        raise HTTPException(status_code=404, detail="publication not available")
    return publication


@router.get("/shared/{share_code}", dependencies=[Depends(limit_publication_meta)])
async def get_shared_publication(
    share_code: str,
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """读者元数据 + 等价信息投影（WP4）。"""
    publication = await _resolve_reader(db, share_code)
    snapshot = pub_reader.snapshot_document_of(publication)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="publication snapshot unavailable")
    return Response(
        content=_json_body(pub_reader.reader_doc(snapshot)),
        media_type="application/json",
        headers=_READER_HEADERS,
    )


@router.get("/shared/{share_code}/map", dependencies=[Depends(limit_publication_meta)])
async def get_shared_publication_map(
    share_code: str,
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """读者 map spec（授权投影：源重写/阻断层剔除/编辑面剥离）。"""
    publication = await _resolve_reader(db, share_code)
    snapshot = pub_reader.snapshot_document_of(publication)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="publication snapshot unavailable")
    reader_spec, degradations = pub_reader.build_reader_map_spec(snapshot, share_code=share_code)
    payload = {
        "spec": reader_spec,
        "degradations": degradations,
        "snapshot_fingerprint": snapshot.snapshot_fingerprint,
    }
    return Response(
        content=_json_body(payload),
        media_type="application/json",
        headers=_READER_HEADERS,
    )


_LAYER_ID_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,200}$")


def _reader_layer_context(
    snapshot, publication, layer_id: str
):
    """读者层校验：形态 → 快照在册 → materialized → 产物可用。"""
    # R1 P3-12：非法形态与"层不在册"同语义 404（不落到产物读的 503）。
    if not _LAYER_ID_RE.fullmatch(layer_id or ""):
        raise HTTPException(status_code=404, detail="layer not available")
    layer = snapshot.layer_by_id(layer_id)
    if layer is None or layer.data_mode != DataMode.materialized:
        raise HTTPException(status_code=404, detail="layer not available")
    fc = pub_reader.load_layer_fc(publication, layer_id)
    if fc is None:
        raise HTTPException(status_code=503, detail="layer data unavailable")
    return layer, fc


@router.get("/shared/{share_code}/layers/{layer_id}/data", dependencies=[Depends(limit_publication_read)])
async def get_shared_layer_data(
    share_code: str,
    layer_id: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """层物化 GeoJSON（写时投影已保证字段白名单；ETag 内容寻址）。"""
    publication = await _resolve_reader(db, share_code)
    snapshot = pub_reader.snapshot_document_of(publication)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="publication snapshot unavailable")
    layer, fc = _reader_layer_context(snapshot, publication, layer_id)
    body = await serialize_geojson(fc, pretty=False)
    etag = '"%s"' % hashlib.sha256(body).hexdigest()[:16]
    if_none_match = request.headers.get("if-none-match")
    headers = {**_READER_HEADERS, "ETag": etag}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _where_fields(node: Any, out: Set[str]) -> None:
    """where AST 字段收集（白名单校验防布尔推断）。"""
    if isinstance(node, dict):
        field = node.get("field")
        if isinstance(field, str) and field:
            out.add(field)
        for key in ("terms", "term"):
            child = node.get(key)
            if isinstance(child, list):
                for item in child:
                    _where_fields(item, out)
            elif child is not None:
                _where_fields(child, out)


def _validate_reader_filters(
    snapshot,
    layer,
    *,
    fields_param: Optional[List[str]],
    where_ast: Optional[Dict[str, Any]],
    order_spec: Optional[tuple],
) -> None:
    whitelist = set(layer.field_whitelist)
    if fields_param:
        illegal = [f for f in fields_param if f not in whitelist]
        if illegal:
            raise HTTPException(
                status_code=400,
                detail={"reason": "field_not_allowed", "fields": illegal},
            )
    if where_ast:
        used: Set[str] = set()
        _where_fields(where_ast, used)
        illegal = [f for f in used if f not in whitelist]
        if illegal:
            raise HTTPException(
                status_code=400,
                detail={"reason": "field_not_allowed", "fields": illegal},
            )
    if order_spec:
        order_field = order_spec[0]
        if order_field not in whitelist:
            raise HTTPException(
                status_code=400,
                detail={"reason": "field_not_allowed", "fields": [order_field]},
            )


@router.get("/shared/{share_code}/layers/{layer_id}/features", dependencies=[Depends(limit_publication_read)])
async def get_shared_layer_features(
    share_code: str,
    layer_id: str,
    limit: int = Query(200, ge=1, le=1000),
    cursor: Optional[str] = Query(None, max_length=512),
    bbox: Optional[str] = Query(None, max_length=128),
    fields: Optional[str] = Query(None, max_length=1024),
    order: Optional[str] = Query(None, max_length=129),
    where: Optional[str] = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """读者要素分页（白名单内筛选/排序/投影；显式分母）。"""
    publication = await _resolve_reader(db, share_code)
    snapshot = pub_reader.snapshot_document_of(publication)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="publication snapshot unavailable")
    layer, fc = _reader_layer_context(snapshot, publication, layer_id)
    try:
        window_bbox = parse_bbox_param(bbox)
        field_list = parse_fields_param(fields)
        order_spec = parse_order_param(order)
        where_ast = parse_where_param(where)
    except FeaturePageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _validate_reader_filters(
        snapshot, layer, fields_param=field_list, where_ast=where_ast, order_spec=order_spec
    )
    from app.services.feature_pages import page_features

    filtered_fc = pub_reader.filter_layer_fields(
        fc, field_whitelist=list(layer.field_whitelist), fields=field_list
    )
    page = page_features(
        filtered_fc,
        limit=limit,
        cursor=cursor,
        bbox=window_bbox,
        fields=None,  # 字段投影已在 filter_layer_fields 完成（白名单∩请求）
        order=order_spec,
        where=where_ast,
    )
    total = count_matching(
        (fc.get("features") or []) if isinstance(fc, dict) else [],
        where=where_ast,
        bbox=window_bbox,
    )
    payload = {
        **page,
        "denominator": {
            "total_features": layer.feature_count,
            "matching_features": total,
            "bbox": list(window_bbox) if window_bbox else None,
            "where": where_ast,
        },
    }
    return Response(
        content=_json_body(payload),
        media_type="application/json",
        headers=_READER_HEADERS,
    )


@router.get("/shared/{share_code}/summary", dependencies=[Depends(limit_publication_read)])
async def get_shared_summary(
    share_code: str,
    layer_id: str = Query(..., min_length=1, max_length=200),
    field: str = Query(..., min_length=1, max_length=200),
    bbox: Optional[str] = Query(None, max_length=128),
    where: Optional[str] = Query(None, max_length=2048),
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    """可见范围统计（白名单数值字段；显式分母与筛选条件）。"""
    publication = await _resolve_reader(db, share_code)
    snapshot = pub_reader.snapshot_document_of(publication)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="publication snapshot unavailable")
    layer = snapshot.layer_by_id(layer_id)
    if layer is None or layer.data_mode != DataMode.materialized:
        raise HTTPException(status_code=404, detail="layer not available")
    if field not in set(layer.field_whitelist):
        raise HTTPException(status_code=400, detail={"reason": "field_not_allowed", "fields": [field]})
    try:
        window_bbox = parse_bbox_param(bbox)
        where_ast = parse_where_param(where)
    except FeaturePageError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    fc = pub_reader.load_layer_fc(publication, layer_id)
    if fc is None:
        raise HTTPException(status_code=503, detail="layer data unavailable")
    features = fc.get("features") if isinstance(fc, dict) else []

    # 数值字段统计（白名单字段；与 page_features/count_matching 同一筛选语义）。
    from app.services.feature_pages import evaluate_where
    from app.services.publication.projection import bbox_intersects, bbox_of_geometry

    stats: Dict[str, Any] = {
        "count": 0, "sum": None, "min": None, "max": None,
        "null_count": 0, "non_numeric_count": 0,
    }
    values: List[float] = []
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if window_bbox is not None and isinstance(geometry, dict):
            feature_bbox = bbox_of_geometry(geometry)
            if feature_bbox is not None and not bbox_intersects(feature_bbox, window_bbox):
                continue
        if where_ast is not None and not evaluate_where(where_ast, feature):
            continue
        props = feature.get("properties")
        value = (props or {}).get(field) if isinstance(props, dict) else None
        if value is None:
            stats["null_count"] += 1
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            stats["non_numeric_count"] += 1
            continue
        numeric = float(value)
        if not math.isfinite(numeric):
            # R1 P3-7：NaN/Inf 不进数值统计（防 NaN 字面量进 JSON 响应）。
            stats["non_numeric_count"] += 1
            continue
        values.append(numeric)
    if values:
        stats["count"] = len(values)
        stats["sum"] = sum(values)
        stats["min"] = min(values)
        stats["max"] = max(values)
    matching = count_matching(
        features if isinstance(features, list) else [],
        where=where_ast,
        bbox=window_bbox,
    )
    payload = {
        "layer_id": layer_id,
        "field": field,
        "stats": stats,
        "denominator": {
            "total_features": layer.feature_count,
            "matching_features": matching,
            "numeric_count": stats["count"],
            "null_count": stats["null_count"],
            "non_numeric_count": stats["non_numeric_count"],
            "bbox": list(window_bbox) if window_bbox else None,
            "where": where_ast,
            "note": "count/min/max/sum 基于当前筛选（bbox+where）；非数值与缺失值计入 null_or_non_numeric，不进数值统计",
        },
    }
    return Response(
        content=_json_body(payload),
        media_type="application/json",
        headers=_READER_HEADERS,
    )
