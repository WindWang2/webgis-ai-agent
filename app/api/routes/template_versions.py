"""Templates V9 REST 面 —— 版本化/继承/失效/组件校验（P4；新路由文件）。

既有 ``templates.py``（CRUD + apply）零改动；版本面挂子路径：

- ``GET    /templates/{template_id}/versions``             版本列表；
- ``POST   /templates/{template_id}/versions``             落新版本（可带
  ``parent_version_id`` 继承）；
- ``GET    /templates/{template_id}/versions/{version}``   版本详情
  （effective payload = 继承链深合并 + 组件校验 + deprecated 标记）；
- ``POST   /templates/{template_id}/versions/{version}/deprecate`` 失效。

鉴权（SEC-02）：读与写入一律先过 ``templates.py`` 的可见性谓词
（built-in/own/org；不可见 = 404 不泄露存在性）；写额外要求
creator 或 admin（403）；继承的父版本所属模板也必须对调用者可见。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.routes.templates import _template_visible
from app.core.auth import actor_ids, get_current_user, get_current_user_optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["地图制图模板 V9（版本化）"])


class VersionCreateRequest(BaseModel):
    payload: Dict[str, Any]
    parent_version_id: Optional[str] = None


class DeprecateRequest(BaseModel):
    note: str = Field(default="", max_length=500)


def _map_error(exc: Exception) -> HTTPException:
    from app.services.templates.versioning import (
        TemplateVersionError,
        TemplateVersionForbiddenError,
    )

    if isinstance(exc, TemplateVersionForbiddenError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, TemplateVersionError):
        text = str(exc)
        code = 404 if "not found" in text else 400
        return HTTPException(status_code=code, detail=text)
    raise exc


def _actor_ctx(user: Any) -> tuple[Optional[str], Any, Optional[str]]:
    user_id, org_id = actor_ids(user if isinstance(user, dict) else None)
    role = user.get("role") if isinstance(user, dict) else None
    return user_id, org_id, role


def _visible_template_or_404(db: Any, template_id: str, user: Any) -> Any:
    """目标模板必须存在且对调用者可见（不可见与不存在同返 404）。"""
    from app.models.db_model import CartographyTemplate

    user_id, org_id, role = _actor_ctx(user)
    tmpl = db.get(CartographyTemplate, template_id)
    if tmpl is None or not _template_visible(tmpl, user_id, org_id, role):
        raise HTTPException(
            status_code=404, detail=f"Template '{template_id}' not found"
        )
    return tmpl


def _can_write(tmpl: Any, user: Any) -> bool:
    """写谓词：admin 或模板 creator（匿名永不通过）。"""
    user_id, _org_id, role = _actor_ctx(user)
    if role == "admin":
        return True
    return user_id is not None and str(tmpl.creator_id) == str(user_id)


def _visible(tmpl: Any, user: Any) -> bool:
    user_id, org_id, role = _actor_ctx(user)
    return _template_visible(tmpl, user_id, org_id, role)


@router.get("/{template_id}/versions")
def list_versions(
    template_id: str,
    user: dict = Depends(get_current_user_optional),
) -> dict:
    from app.core.database import SessionLocal
    from app.models.template_version import TemplateVersion

    with SessionLocal() as db:
        _visible_template_or_404(db, template_id, user)
        rows = (
            db.query(TemplateVersion)
            .filter_by(template_id=template_id)
            .order_by(TemplateVersion.version.desc())
            .limit(200)
            .all()
        )
        return {
            "success": True,
            "items": [
                {
                    "id": r.id,
                    "template_id": r.template_id,
                    "version": r.version,
                    "parent_version_id": r.parent_version_id,
                    "component_refs": r.component_refs,
                    "component_violations": r.component_violations,
                    "deprecated": r.deprecated_at is not None,
                    "deprecated_at": r.deprecated_at.isoformat()
                    if r.deprecated_at else None,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }


@router.post("/{template_id}/versions")
def create_version_endpoint(
    template_id: str,
    body: VersionCreateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.templates.versioning import (
        create_version,
        validate_component_refs,
        extract_component_refs,
    )

    try:
        with SessionLocal() as db:
            tmpl = _visible_template_or_404(db, template_id, user)
            if not _can_write(tmpl, user):
                raise HTTPException(
                    status_code=403,
                    detail=f"Template '{template_id}' is read-only for this caller",
                )

            def _parent_visible(parent_template: Any) -> bool:
                return _visible(parent_template, user)

            row = create_version(
                db, template_id, body.payload,
                parent_version_id=body.parent_version_id,
                created_by=(user.get("user_id") if isinstance(user, dict) else None),
                can_write=lambda t: _can_write(t, user),
                can_read_parent=_parent_visible,
            )
            validation = validate_component_refs(extract_component_refs(body.payload))
            return {
                "success": True,
                "version": {
                    "id": row.id,
                    "template_id": row.template_id,
                    "version": row.version,
                    "parent_version_id": row.parent_version_id,
                    "component_validation": validation,
                },
            }
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)


@router.get("/{template_id}/versions/{version}")
def get_version_endpoint(
    template_id: str,
    version: str,
    user: dict = Depends(get_current_user_optional),
) -> dict:
    from app.core.database import SessionLocal
    from app.models.db_model import CartographyTemplate
    from app.services.templates.versioning import (
        get_version,
        resolve_payload,
        validate_component_refs,
        extract_component_refs,
    )

    with SessionLocal() as db:
        _visible_template_or_404(db, template_id, user)
        try:
            row = get_version(
                db, template_id, None if version == "latest" else int(version)
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="version 必须为整数或 latest")
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        effective, chain = resolve_payload(db, row)
        # SEC-02：跨模板继承链上的每个祖先模板也必须对调用者可见，否则
        # effective_payload / chain 会泄露他人模板内容。
        for chain_template_id in chain:
            if chain_template_id == template_id:
                continue
            parent_tmpl = db.get(CartographyTemplate, chain_template_id)
            if parent_tmpl is None or not _visible(parent_tmpl, user):
                raise HTTPException(
                    status_code=404, detail=f"Template '{template_id}' not found"
                )
        return {
            "success": True,
            "version": {
                "id": row.id,
                "template_id": row.template_id,
                "version": row.version,
                "parent_version_id": row.parent_version_id,
                "deprecated": row.deprecated_at is not None,
                "deprecation_note": row.deprecation_note,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            },
            "effective_payload": effective,
            "inheritance_chain": chain,
            "component_validation": validate_component_refs(
                extract_component_refs(effective)
            ),
        }


@router.post("/{template_id}/versions/{version}/deprecate")
def deprecate_version_endpoint(
    template_id: str,
    version: str,
    body: DeprecateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.templates.versioning import deprecate_version

    with SessionLocal() as db:
        tmpl = _visible_template_or_404(db, template_id, user)
        if not _can_write(tmpl, user):
            raise HTTPException(
                status_code=403,
                detail=f"Template '{template_id}' is read-only for this caller",
            )
        try:
            row = deprecate_version(
                db, template_id, None if version == "latest" else int(version),
                note=body.note,
                actor=(user.get("user_id") if isinstance(user, dict) else None),
                can_write=lambda t: _can_write(t, user),
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="version 必须为整数或 latest")
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {
            "success": True,
            "version": row.version,
            "deprecated": row.deprecated_at is not None,
            "note": row.deprecation_note,
        }


__all__ = ["router"]
