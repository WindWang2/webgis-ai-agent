"""G04 —— 组合模板资产 REST 面（新路由文件）。

- ``GET    /composition-template-assets``                     列表
  （builtin 4 族 + 调用者可见 DB 资产；匿名只见 builtin）；
- ``POST   /composition-template-assets``                     创建
  （显式 payload 或 ``session_id`` 派生 save-as-my-template）；
- ``GET    /composition-template-assets/{asset_id}``          详情+版本；
- ``POST   /composition-template-assets/{asset_id}/versions`` 追加新版本；
- ``POST   /composition-template-assets/{asset_id}/versions/{revision}/publish``
- ``POST   /composition-template-assets/{asset_id}/retire``   软退役；
- ``POST   /composition-template-assets/{asset_id}/restore/{revision}``。

鉴权：``get_current_user``（读可选 builtin-only；写必登录）。纪律照抄
``template_versions.py``：不可见 = 404 不泄露存在性；写 = creator/admin
（403）；``session_id`` 派生先过 ``verify_session_owner`` 会话属主守卫。
DB 操作经 ``asyncio.to_thread``（SessionLocal 同步会话，与工具面同款）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import actor_ids, get_current_user, get_current_user_optional
from app.core.database import get_async_db
from app.lib.cartography.template_assets import (
    BUILTIN_TEMPLATE_ASSETS,
    TemplateAssetPayload,
    asset_reference_of,
    materialize_template,
)
from app.services.templates.composition_assets import (
    ASSET_REASON_CODES,
    CompositionAssetError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/composition-template-assets", tags=["组合模板资产（G04）"])


def _map_asset_error(exc: CompositionAssetError) -> HTTPException:
    status_by_reason = {
        "asset_not_found": 404,
        "asset_forbidden": 403,
        "asset_conflict": 409,
        "asset_invalid_visibility": 422,
        "asset_payload_error": 422,
        "asset_publish_gate_failed": 422,
        "asset_draft": 409,
        "asset_retired": 409,
        "asset_revision_not_found": 404,
        "asset_version_not_published": 409,
    }
    code = status_by_reason.get(exc.reason, 400)
    return HTTPException(status_code=code, detail=str(exc)[:300])


def _actor(user: Any) -> tuple[Optional[str], Any, Optional[str]]:
    user_id, org_id = actor_ids(user if isinstance(user, dict) else None)
    role = user.get("role") if isinstance(user, dict) else None
    return user_id, org_id, role


class AssetCreateRequest(BaseModel):
    session_id: str = Field(
        default="", description="从该会话派生（save-as-my-template）")
    payload: Optional[Dict[str, Any]] = Field(
        default=None, description="显式 TemplateAssetPayload（与 session_id "
                                  "至少其一；同时给出时显式 payload 优先）")
    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=400)
    visibility: str = Field(default="private")
    project_id: str = Field(default="")
    publish: bool = Field(default=True)


class AssetNewVersionRequest(BaseModel):
    session_id: str = Field(
        default="", description="从该会话派生新内容（需会话属主；与 payload 二选一）")
    payload: Optional[Dict[str, Any]] = Field(default=None)
    publish: bool = Field(default=True)


class RetireRequest(BaseModel):
    note: str = Field(default="", max_length=500)


def _builtin_refs() -> list[Dict[str, Any]]:
    out = []
    for rec in BUILTIN_TEMPLATE_ASSETS:
        try:
            tpl = materialize_template(rec)
            out.append(asset_reference_of(rec, tpl).to_bounded_dict())
        except Exception:  # noqa: BLE001 — 单个内置资产损坏不阻塞列表
            continue
    return out


def _sync_list(user_id, org_id, role, include_retired: bool):
    from app.core.database import SessionLocal
    from app.services.templates.composition_assets import list_assets

    db = SessionLocal()
    try:
        return list_assets(
            db, user_id=user_id, org_id=org_id, role=role,
            include_retired=include_retired)
    finally:
        db.close()


@router.get("")
async def list_composition_template_assets(
    user: Optional[dict] = Depends(get_current_user_optional),
    include_retired: bool = False,
    include_builtin: bool = True,
) -> dict:
    user_id, org_id, role = _actor(user)
    builtin = _builtin_refs() if include_builtin else []
    if user_id is None and role != "admin":
        return {"success": True, "assets": builtin,
                "asset_count": len(builtin), "reason_codes": []}
    rows = await asyncio.to_thread(
        _sync_list, user_id, org_id, role, include_retired)
    db_rows = [{**r, "builtin": False} for r in rows]
    return {"success": True, "assets": builtin + db_rows,
            "asset_count": len(builtin) + len(db_rows), "reason_codes": []}


async def _resolve_payload_or_session(
    body: AssetCreateRequest, user: dict, db: AsyncSession,
) -> TemplateAssetPayload:
    """显式 payload 优先；否则从会话派生（先过会话属主守卫）。"""
    if body.payload is not None:
        try:
            return TemplateAssetPayload.model_validate(body.payload)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"invalid asset payload: {exc}") from exc
    if not body.session_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="payload 或 session_id 至少提供其一")
    from app.services.auth_history_bridge import verify_session_owner

    user_id, _org_id, _role = _actor(user)
    await verify_session_owner(db, body.session_id, user_id=user_id)
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(body.session_id) or {}
    layout = spec.get("layout") if isinstance(spec, dict) else None
    components = (
        [c for c in (layout.get("components") or []) if isinstance(c, dict)]
        if isinstance(layout, dict) else [])
    if not components:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="会话无组件版面可保存")
    from app.services.templates.composition_assets import (
        derive_payload_from_spec,
    )

    payload = derive_payload_from_spec(spec)
    if body.name:
        payload.name = body.name[:80]
    if body.description:
        payload.description = body.description[:400]
    return payload


def _sync_create(payload, visibility, project_id, user_id, org_id, publish):
    from app.core.database import SessionLocal
    from app.services.templates.composition_assets import create_asset

    db = SessionLocal()
    try:
        asset, version = create_asset(
            db, payload=payload, visibility=visibility,
            project_id=project_id or None, creator_id=user_id, org_id=org_id,
            publish=publish)
        return {
            "asset_id": asset.id, "name": asset.name,
            "visibility": asset.visibility, "status": version.status,
            "revision": version.revision,
            "validation": (version.validation or [])[:8],
            "fingerprint": (version.fingerprint or "")[:80],
        }
    finally:
        db.close()


@router.post("")
async def create_composition_template_asset(
    body: AssetCreateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    user_id, org_id, _role = _actor(user)
    payload = await _resolve_payload_or_session(body, user, db)
    try:
        result = await asyncio.to_thread(
            _sync_create, payload, body.visibility, body.project_id,
            user_id, org_id, body.publish)
    except CompositionAssetError as exc:
        raise _map_asset_error(exc) from exc
    return {"success": True, **result}


def _sync_versions(asset_id, user_id, org_id, role):
    from app.core.database import SessionLocal
    from app.services.templates.composition_assets import list_versions

    db = SessionLocal()
    try:
        return list_versions(
            db, asset_id, user_id=user_id, org_id=org_id, role=role)
    finally:
        db.close()


@router.get("/{asset_id}")
async def get_composition_template_asset(
    asset_id: str,
    user: Optional[dict] = Depends(get_current_user_optional),
) -> dict:
    user_id, org_id, role = _actor(user)
    try:
        versions = await asyncio.to_thread(
            _sync_versions, asset_id, user_id, org_id, role)
    except CompositionAssetError as exc:
        raise _map_asset_error(exc) from exc
    return {"success": True, "asset_id": asset_id, "versions": versions}


@router.post("/{asset_id}/versions")
async def new_asset_version(
    asset_id: str,
    body: AssetNewVersionRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    user_id, org_id, role = _actor(user)
    create_body = AssetCreateRequest(
        session_id=body.session_id, payload=body.payload)
    payload = None
    if body.payload is not None or body.session_id:
        payload = await _resolve_payload_or_session(create_body, user, db)

    def _sync():
        from app.core.database import SessionLocal
        from app.services.templates.composition_assets import new_version

        db = SessionLocal()
        try:
            if payload is None:
                # 缺省保留原 payload：恢复语义之外的「重派生」必须显式给源
                raise CompositionAssetError(
                    "asset_payload_error",
                    "payload 或 session_id 至少提供其一")
            version = new_version(
                db, asset_id, payload, user_id=user_id, org_id=org_id,
                role=role, publish=body.publish)
            return {
                "revision": version.revision, "status": version.status,
                "validation": (version.validation or [])[:8],
                "fingerprint": (version.fingerprint or "")[:80],
            }
        finally:
            db.close()

    try:
        result = await asyncio.to_thread(_sync)
    except CompositionAssetError as exc:
        raise _map_asset_error(exc) from exc
    return {"success": True, "asset_id": asset_id, **result}


@router.post("/{asset_id}/versions/{revision}/publish")
async def publish_asset_version(
    asset_id: str,
    revision: int,
    user: dict = Depends(get_current_user),
) -> dict:
    user_id, org_id, role = _actor(user)

    def _sync():
        from app.core.database import SessionLocal
        from app.services.templates.composition_assets import publish_version

        db = SessionLocal()
        try:
            version = publish_version(
                db, asset_id, revision, user_id=user_id, org_id=org_id,
                role=role)
            return {"revision": version.revision, "status": version.status}
        finally:
            db.close()

    try:
        result = await asyncio.to_thread(_sync)
    except CompositionAssetError as exc:
        raise _map_asset_error(exc) from exc
    return {"success": True, "asset_id": asset_id, **result}


@router.post("/{asset_id}/retire")
async def retire_asset_route(
    asset_id: str,
    body: RetireRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    user_id, org_id, role = _actor(user)

    def _sync():
        from app.core.database import SessionLocal
        from app.services.templates.composition_assets import retire_asset

        db = SessionLocal()
        try:
            asset = retire_asset(
                db, asset_id, user_id=user_id, org_id=org_id, role=role,
                note=body.note)
            return {"status": asset.status}
        finally:
            db.close()

    try:
        result = await asyncio.to_thread(_sync)
    except CompositionAssetError as exc:
        raise _map_asset_error(exc) from exc
    return {"success": True, "asset_id": asset_id, **result}


@router.post("/{asset_id}/restore/{revision}")
async def restore_asset_revision(
    asset_id: str,
    revision: int,
    user: dict = Depends(get_current_user),
) -> dict:
    user_id, org_id, role = _actor(user)

    def _sync():
        from app.core.database import SessionLocal
        from app.services.templates.composition_assets import restore_revision

        db = SessionLocal()
        try:
            version = restore_revision(
                db, asset_id, revision, user_id=user_id, org_id=org_id,
                role=role)
            return {
                "restored_from": revision, "revision": version.revision,
                "status": version.status,
                "fingerprint": (version.fingerprint or "")[:80],
            }
        finally:
            db.close()

    try:
        result = await asyncio.to_thread(_sync)
    except CompositionAssetError as exc:
        raise _map_asset_error(exc) from exc
    return {"success": True, "asset_id": asset_id, **result}


__all__ = ["router", "ASSET_REASON_CODES"]
