"""G40 —— 制图符号资产 REST 面（UI 与 Pi 工具共用 catalog/binding 服务）。

- ``GET    /cartographic-assets``                              目录（builtin 内联
  规范形 + 调用者可见 DB 资产分页；kind 过滤）；
- ``POST   /cartographic-assets``                              创建（受控 SVG 子集导入；匿名拒绝）；
- ``GET    /cartographic-assets/{id}``                         详情+版本列表；
- ``GET    /cartographic-assets/{id}/versions/{rev}/content``  规范形（scope）；
- ``POST   /cartographic-assets/{id}/versions``                新版本；
- ``POST   /cartographic-assets/{id}/versions/{rev}/publish``；
- ``POST   /cartographic-assets/{id}/retire``                  软退役；
- ``POST   /cartographic-assets/{id}/restore/{rev}``；
- ``DELETE /cartographic-assets/{id}/versions/{rev}``          引用检查后硬删；
- ``GET/POST .../sessions/{sid}/layers/{lid}/(inspect|preview|apply|unbind)``
  绑定工作台（require_owned_session）。

纪律照抄 composition_template_assets.py / thematic_authoring.py：不可见 =
404 不泄露存在性；写 = creator/admin（403）；DB 经 asyncio.to_thread
（SessionLocal 同步会话）；提交走 MapSpec 生命周期引擎（CAS+checkpoint+undo）。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import actor_ids, get_current_user, get_current_user_optional
from app.lib.cartography.symbol_asset import (
    SymbolAssetError,
    builtin_asset_index,
)
from app.lib.cartography.svg_import import import_svg_asset
from app.models.db_model import Conversation
from app.schemas.cartographic_assets_schema import (
    CartographicAssetContentResponse,
    CartographicAssetCreateResponse,
    CartographicAssetDeleteVersionResponse,
    CartographicAssetDetailResponse,
    CartographicAssetListResponse,
    CartographicAssetRetireResponse,
    CartographicAssetVersionResponse,
    SymbolBindingInspectResponse,
    SymbolBindingReceipt,
)
from app.services.auth_history_bridge import require_owned_session
from app.services.cartographic_assets.binding import (
    SymbolBindingError,
    apply_binding,
    inspect_layer_binding,
    preview_binding,
    unbind_layer,
)
from app.services.cartographic_assets.catalog import CartographicAssetError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cartographic-assets", tags=["制图符号资产（G40）"])

_STATUS_BY_REASON = {
    "asset_not_found": 404,
    "asset_forbidden": 403,
    "asset_conflict": 409,
    "asset_invalid_visibility": 422,
    "asset_document_error": 422,
    "asset_kind_mismatch": 422,
    "asset_content_mismatch": 409,
    "asset_in_use": 409,
    "asset_draft": 409,
    "asset_retired": 409,
    "asset_revision_not_found": 404,
    "asset_version_not_published": 409,
    # 该 reason 是服务端拒绝（扫描不完整），不是附带披露；映射成 200
    # 会让客户端拿到「成功码 + 错误体」。
    "asset_reference_scan_truncated": 409,
}


def _map_catalog_error(exc: CartographicAssetError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_REASON.get(exc.reason, 400),
        detail={"code": exc.reason, "message": str(exc)[:300]})


def _actor(user: Any) -> tuple[Optional[str], Any, Optional[str]]:
    user_id, org_id = actor_ids(user if isinstance(user, dict) else None)
    role = user.get("role") if isinstance(user, dict) else None
    return user_id, org_id, role


def _run(fn, *args, **kwargs):
    """同步 DB 面在线程池执行（SessionLocal 同步会话，G04 同款）。"""
    return asyncio.to_thread(fn, *args, **kwargs)


# ─── 请求体 ────────────────────────────────────────────────────────────────

class AssetCreateBody(BaseModel):
    kind: str = Field(..., description="point_symbol | area_texture")
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=2000)
    visibility: str = Field(default="private")
    project_id: str = Field(default="")
    publish: bool = Field(default=True)
    #: SVG 导入（base64 字节；受控子集 → 规范形单一清洗口径）
    svg_base64: Optional[str] = Field(
        default=None, max_length=700_000,
        description="受控 SVG 子集字节（base64；~512KB 原始上限）")
    license_note: str = Field(default="", max_length=300)


class NewVersionBody(AssetCreateBody):
    pass


class BindingPlanBody(BaseModel):
    plan: Dict[str, Any]


class BindingCommitBody(BindingPlanBody):
    expected_revision: int = Field(ge=0)
    client_mutation_id: Optional[str] = Field(default=None, max_length=128)


class UnbindBody(BaseModel):
    expected_revision: int = Field(ge=0)
    client_mutation_id: Optional[str] = Field(default=None, max_length=128)


# ─── 目录 ──────────────────────────────────────────────────────────────────

@router.get("", response_model=CartographicAssetListResponse)
async def list_catalog(
    kind: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=50),
    offset: int = Query(0, ge=0),
    include_retired: bool = Query(False),
    user: Any = Depends(get_current_user_optional),
):
    user_id, org_id, role = _actor(user)
    db_assets, total = await _run(
        _list_assets_with_db, user_id=user_id, org_id=org_id, role=role,
        kind=kind, include_retired=include_retired, limit=limit, offset=offset)
    builtin = [dict(item) for item in builtin_asset_index()
               if not kind or item.get("kind") == kind]
    # builtin 内联规范形（前端零额外请求预热 doc 缓存）
    from app.lib.cartography.symbol_asset import BUILTIN_SYMBOL_ASSETS
    for item in builtin:
        doc = BUILTIN_SYMBOL_ASSETS.get(item["asset_id"])
        if doc is not None:
            item["document"] = doc.model_dump()
    return {
        "success": True,
        "assets": builtin + db_assets,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


def _list_assets_with_db(*, user_id, org_id, role, kind, include_retired,
                         limit, offset):
    from app.core.database import SessionLocal
    from app.services.cartographic_assets.catalog import list_assets

    db = SessionLocal()
    try:
        return list_assets(
            db, user_id=user_id, org_id=org_id, role=role, kind=kind,
            include_retired=include_retired, limit=limit, offset=offset)
    finally:
        db.close()


@router.post("", response_model=CartographicAssetCreateResponse)
async def create_asset(body: AssetCreateBody,
                       user: Any = Depends(get_current_user)):
    user_id, org_id, role = _actor(user)
    if user_id is None:
        raise HTTPException(status_code=403, detail={
            "code": "asset_forbidden", "message": "匿名不能创建资产"})
    try:
        if body.svg_base64:
            svg_bytes = base64.b64decode(body.svg_base64, validate=True)
            document = import_svg_asset(
                svg_bytes, kind=body.kind, name=body.name,
                license_note=body.license_note)
        else:
            raise HTTPException(status_code=422, detail={
                "code": "asset_document_error",
                "message": "创建需要 svg_base64（受控 SVG 子集导入）"})
    # 14b：坏 base64 是客户端输入错误 → 422 typed，不得冒泡成 500。
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail={
            "code": "asset_document_error",
            "message": f"svg_base64 非法: {exc}"[:300]}) from exc
    except SymbolAssetError as exc:
        raise HTTPException(status_code=422, detail={
            "code": exc.code, "message": str(exc)[:300]}) from exc
    from app.services.cartographic_assets.catalog import create_asset as _create

    def _do():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            asset, version = _create(
                db, document=document, visibility=body.visibility,
                project_id=body.project_id or None, creator_id=user_id,
                org_id=org_id, description=body.description,
                publish=body.publish)
            db.commit()
            return _asset_view(asset, version)
        except CartographicAssetError:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return {"success": True, "asset": await _run(_do)}
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc


def _asset_view(asset: Any, version: Any = None) -> Dict[str, Any]:
    out = {
        "asset_id": asset.id,
        "name": asset.name,
        "kind": asset.kind,
        "visibility": asset.visibility,
        "project_id": asset.project_id,
        "status": asset.status,
        "latest_revision": asset.latest_revision,
        "current_revision": asset.current_revision,
        "builtin": False,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }
    if version is not None:
        out["revision"] = version.revision
        out["content_digest"] = version.content_digest
        out["version_status"] = version.status
    return out


@router.get("/{asset_id}", response_model=CartographicAssetDetailResponse)
async def asset_detail(asset_id: str,
                       user: Any = Depends(get_current_user_optional)):
    from app.services.cartographic_assets.catalog import list_versions

    user_id, org_id, role = _actor(user)

    def _do():
        from app.core.database import SessionLocal
        from app.services.cartographic_assets.catalog import (
            _get_asset_or_404, _caller_project_ids)
        db = SessionLocal()
        try:
            project_ids = _caller_project_ids(db, user_id, org_id)
            asset = _get_asset_or_404(db, asset_id, user_id, org_id, role,
                                      project_ids)
            versions = list_versions(
                db, asset_id, user_id=user_id, org_id=org_id, role=role)
            return _asset_view(asset), versions
        finally:
            db.close()

    try:
        asset, versions = await _run(_do)
        return {"success": True, "asset": asset, "versions": versions}
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc


@router.get("/{asset_id}/versions/{revision}/content", response_model=CartographicAssetContentResponse)
async def version_content(asset_id: str, revision: int,
                          user: Any = Depends(get_current_user_optional)):

    user_id, org_id, role = _actor(user)
    try:
        blob, meta = await _run(
            _read_content_with_db, asset_id, revision, user_id, org_id, role)
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc
    import json as _json
    try:
        document = _json.loads(blob.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail={
            "code": "asset_document_error",
            "message": f"内容不可解析: {exc}"}) from exc
    # F1（评审）：blob 是 exclude digest 的规范形 —— 注回 meta.content_digest
    # 后**扁平**返回（前端 fetchDocByRef 直接消费文档本体，digest 自校验）。
    if isinstance(document, dict):
        document.setdefault("content_digest", meta.get("content_digest"))
    return {"success": True, **(document if isinstance(document, dict) else {}),
            "meta": meta}


def _read_content_with_db(asset_id, revision, user_id, org_id, role):
    from app.core.database import SessionLocal
    from app.services.cartographic_assets.catalog import read_version_blob

    db = SessionLocal()
    try:
        return read_version_blob(
            db, asset_id, revision, user_id=user_id, org_id=org_id, role=role)
    finally:
        db.close()


@router.post("/{asset_id}/versions", response_model=CartographicAssetVersionResponse)
async def new_version(asset_id: str, body: NewVersionBody,
                      user: Any = Depends(get_current_user)):
    user_id, org_id, role = _actor(user)
    try:
        svg_bytes = base64.b64decode(body.svg_base64 or "", validate=True)
        document = import_svg_asset(
            svg_bytes, kind=body.kind, name=body.name,
            license_note=body.license_note)
    # 14b：坏 base64 是客户端输入错误 → 422 typed，不得冒泡成 500。
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail={
            "code": "asset_document_error",
            "message": f"svg_base64 非法: {exc}"[:300]}) from exc
    except SymbolAssetError as exc:
        raise HTTPException(status_code=422, detail={
            "code": exc.code, "message": str(exc)[:300]}) from exc
    from app.services.cartographic_assets.catalog import new_version as _new

    def _do():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            version = _new(db, asset_id, document=document, user_id=user_id,
                           org_id=org_id, role=role, publish=body.publish)
            db.commit()
            return {"revision": version.revision,
                    "content_digest": version.content_digest,
                    "status": version.status}
        except CartographicAssetError:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return {"success": True, **await _run(_do)}
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc


@router.post("/{asset_id}/versions/{revision}/publish", response_model=CartographicAssetVersionResponse)
async def publish(asset_id: str, revision: int,
                  user: Any = Depends(get_current_user)):
    from app.services.cartographic_assets.catalog import publish_version

    user_id, org_id, role = _actor(user)

    def _do():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            v = publish_version(db, asset_id, revision, user_id=user_id,
                                org_id=org_id, role=role)
            db.commit()
            return {"revision": v.revision, "status": v.status}
        except CartographicAssetError:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return {"success": True, **await _run(_do)}
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc


@router.post("/{asset_id}/retire", response_model=CartographicAssetRetireResponse)
async def retire(asset_id: str,
                 user: Any = Depends(get_current_user),
                 note: str = Query(default="", max_length=500)):
    from app.services.cartographic_assets.catalog import retire_asset

    user_id, org_id, role = _actor(user)

    def _do():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            asset = retire_asset(db, asset_id, user_id=user_id, org_id=org_id,
                                 role=role, note=note)
            db.commit()
            return {"status": asset.status}
        except CartographicAssetError:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return {"success": True, **await _run(_do)}
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc


@router.post("/{asset_id}/restore/{revision}", response_model=CartographicAssetVersionResponse)
async def restore(asset_id: str, revision: int,
                  user: Any = Depends(get_current_user)):
    from app.services.cartographic_assets.catalog import restore_asset

    user_id, org_id, role = _actor(user)

    def _do():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            v = restore_asset(db, asset_id, revision, user_id=user_id,
                              org_id=org_id, role=role)
            db.commit()
            return {"revision": v.revision, "content_digest": v.content_digest}
        except CartographicAssetError:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return {"success": True, **await _run(_do)}
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc


@router.delete("/{asset_id}/versions/{revision}", response_model=CartographicAssetDeleteVersionResponse)
async def delete_version(asset_id: str, revision: int,
                         user: Any = Depends(get_current_user)):
    from app.services.cartographic_assets.catalog import delete_asset_version

    user_id, org_id, role = _actor(user)

    def _do():
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            out = delete_asset_version(db, asset_id, revision,
                                       user_id=user_id, org_id=org_id,
                                       role=role)
            db.commit()
            return out
        except CartographicAssetError:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return await _run(_do)
    except CartographicAssetError as exc:
        raise _map_catalog_error(exc) from exc


# ─── 会话绑定工作台 ────────────────────────────────────────────────────────

def _binding_http(exc: SymbolBindingError) -> HTTPException:
    detail = exc.payload()
    return HTTPException(status_code=exc.status_code, detail=detail)


@router.get("/sessions/{session_id}/layers/{layer_id}/inspect", response_model=SymbolBindingInspectResponse)
async def inspect_binding(
    session_id: str,
    layer_id: str,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await inspect_layer_binding(
            session_id, layer_id, owner_token=owner_token)
    except SymbolBindingError as exc:
        raise _binding_http(exc) from exc


@router.post("/sessions/{session_id}/layers/{layer_id}/preview", response_model=SymbolBindingReceipt)
async def preview(
    session_id: str,
    layer_id: str,
    body: BindingPlanBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    user: Any = Depends(get_current_user_optional),
    conv: Conversation = Depends(require_owned_session),
):
    user_id, org_id, role = _actor(user)
    try:
        return await preview_binding(
            session_id, layer_id, body.plan, owner_token=owner_token,
            user_id=user_id, org_id=org_id, role=role)
    except SymbolBindingError as exc:
        raise _binding_http(exc) from exc


@router.post("/sessions/{session_id}/layers/{layer_id}/apply", response_model=SymbolBindingReceipt)
async def apply(
    session_id: str,
    layer_id: str,
    body: BindingCommitBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    user: Any = Depends(get_current_user_optional),
    conv: Conversation = Depends(require_owned_session),
):
    user_id, org_id, role = _actor(user)
    try:
        return await apply_binding(
            session_id, layer_id, body.plan,
            expected_revision=body.expected_revision,
            origin="user", actor="symbol_asset_panel",
            mutation_id=(f"symbolasset:{body.client_mutation_id}"
                         if body.client_mutation_id else None),
            owner_token=owner_token, user_id=user_id, org_id=org_id, role=role)
    except SymbolBindingError as exc:
        raise _binding_http(exc) from exc


@router.post("/sessions/{session_id}/layers/{layer_id}/unbind", response_model=SymbolBindingReceipt)
async def unbind(
    session_id: str,
    layer_id: str,
    body: UnbindBody,
    owner_token: Optional[str] = Header(None, alias="X-Session-Token"),
    conv: Conversation = Depends(require_owned_session),
):
    try:
        return await unbind_layer(
            session_id, layer_id, expected_revision=body.expected_revision,
            origin="user", actor="symbol_asset_panel",
            mutation_id=(f"symbolasset:{body.client_mutation_id}"
                         if body.client_mutation_id else None),
            owner_token=owner_token)
    except SymbolBindingError as exc:
        raise _binding_http(exc) from exc
