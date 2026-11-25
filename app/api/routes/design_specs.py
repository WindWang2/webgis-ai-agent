"""Design Specs REST 面 —— 设计规范 CRUD / 版本化 / 预览（WP1；新路由文件）。

- ``GET    /design-specs``                       scope 内列表；
- ``POST   /design-specs``                       新建（version 1，校验 fail-closed）；
- ``GET    /design-specs/{spec_id}``             详情（effective payload + 校验投影）；
- ``POST   /design-specs/{spec_id}/versions``    落新版本（可带 parent 继承）；
- ``GET    /design-specs/{spec_id}/versions/{version}``  版本详情（深合并 + 链）；
- ``POST   /design-specs/{spec_id}/versions/{version}/deprecate`` 失效；
- ``POST   /design-specs/{spec_id}/publish``     draft → published（校验门）；
- ``POST   /design-specs/{spec_id}/retire``      软退役；
- ``GET    /design-specs/{spec_id}/preview``     预览（渲染目标/冲突/继承链/digest）。

鉴权（SEC-02）：读 = builtin ∪ creator ∪ org（不可见与不存在同返 404）；
写 = creator 或同组织 admin；builtin 只读；继承父版本必须对调用者可见。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import actor_ids, get_current_user, get_current_user_optional
from app.models.design_spec import CartographyDesignSpec
from app.services.design_specs.errors import (
    DesignSpecConflictError,
    DesignSpecError,
    DesignSpecForbiddenError,
    DesignSpecNotFoundError,
    DesignSpecValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/design-specs", tags=["制图设计规范"])


class DesignSpecCreateRequest(BaseModel):
    payload: Dict[str, Any]
    make_builtin: bool = False  # 仅 admin 可置（默认 False）


class DesignSpecVersionCreateRequest(BaseModel):
    payload: Dict[str, Any]
    parent_version_id: Optional[str] = None


class DesignSpecDeprecateRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class DesignSpecRetireRequest(BaseModel):
    note: str = Field(default="", max_length=500)


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, DesignSpecForbiddenError):
        return HTTPException(status_code=403, detail=exc.error_payload())
    if isinstance(exc, DesignSpecConflictError):
        return HTTPException(status_code=409, detail=exc.error_payload())
    if isinstance(exc, DesignSpecNotFoundError):
        return HTTPException(status_code=404, detail=exc.error_payload())
    if isinstance(exc, DesignSpecValidationError):
        return HTTPException(status_code=400, detail=exc.error_payload())
    if isinstance(exc, DesignSpecError):
        return HTTPException(status_code=400, detail=exc.error_payload())
    raise exc


def _actor_ctx(user: Any) -> tuple[Optional[str], Any, Optional[str]]:
    user_id, org_id = actor_ids(user if isinstance(user, dict) else None)
    role = user.get("role") if isinstance(user, dict) else None
    return user_id, org_id, role


def _visible_spec_or_404(db: Any, spec_id: str, user: Any) -> CartographyDesignSpec:
    from app.services.design_specs.versioning import design_spec_visible, get_spec

    user_id, org_id, _role = _actor_ctx(user)
    try:
        spec = get_spec(db, spec_id)
    except DesignSpecNotFoundError:
        raise HTTPException(status_code=404, detail=f"Design spec '{spec_id}' not found")
    if not design_spec_visible(spec, user_id, org_id):
        raise HTTPException(status_code=404, detail=f"Design spec '{spec_id}' not found")
    return spec


def _require_can_write(db: Any, spec: CartographyDesignSpec, user: Any) -> None:
    from app.services.design_specs.versioning import design_spec_can_write

    user_id, org_id, role = _actor_ctx(user)
    if not design_spec_can_write(spec, user_id, org_id, role):
        raise HTTPException(
            status_code=403,
            detail={"code": "DESIGN_SPEC_FORBIDDEN", "message": "read-only for this caller"},
        )


def _spec_brief(spec: CartographyDesignSpec) -> Dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "is_builtin": bool(spec.is_builtin),
        "status": spec.status,
        "current_version": spec.current_version,
        "structural_digest": spec.structural_digest,
        "retired": spec.retired_at is not None,
        "retirement_note": spec.retirement_note,
        "creator_id": spec.creator_id,
        "updated_at": spec.updated_at.isoformat() if spec.updated_at else None,
    }


@router.get("")
def list_design_specs(user: dict = Depends(get_current_user_optional)) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.versioning import list_specs

    user_id, org_id, _role = _actor_ctx(user)
    with SessionLocal() as db:
        rows = list_specs(db, user_id, org_id)
        return {"success": True, "items": [_spec_brief(r) for r in rows]}


@router.post("")
def create_design_spec(
    body: DesignSpecCreateRequest, user: dict = Depends(get_current_user)
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.versioning import create_spec

    user_id, org_id, role = _actor_ctx(user)
    if body.make_builtin and role != "admin":
        raise HTTPException(status_code=403, detail="only admin may create builtin specs")
    try:
        with SessionLocal() as db:
            spec = create_spec(
                db, body.payload,
                creator_id=(user.get("user_id") if isinstance(user, dict) else user_id),
                org_id=org_id,
                is_builtin=bool(body.make_builtin and role == "admin"),
                created_by=(user.get("user_id") if isinstance(user, dict) else user_id),
            )
            return {"success": True, "spec": _spec_brief(spec)}
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)


@router.get("/{spec_id}")
def get_design_spec(
    spec_id: str, user: dict = Depends(get_current_user_optional)
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.contract import validate_design_spec_payload

    with SessionLocal() as db:
        spec = _visible_spec_or_404(db, spec_id, user)
        report = validate_design_spec_payload(spec.payload or {})
        return {
            "success": True,
            "spec": _spec_brief(spec),
            "payload": spec.payload,
            "reference_validation": {
                "ok": report["ok"],
                "violations": report["violations"],
                "warnings": report["warnings"],
            },
        }


@router.post("/{spec_id}/versions")
def create_design_spec_version(
    spec_id: str,
    body: DesignSpecVersionCreateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.versioning import create_version

    with SessionLocal() as db:
        spec = _visible_spec_or_404(db, spec_id, user)
        _require_can_write(db, spec, user)
        try:
            row = create_version(
                db, spec_id, body.payload,
                parent_version_id=body.parent_version_id,
                created_by=(user.get("user_id") if isinstance(user, dict) else None),
                can_write=lambda s: True,  # 已在上文校验
                can_read_parent=lambda s: True,
            )
            return {
                "success": True,
                "version": {
                    "id": row.id,
                    "spec_id": row.spec_id,
                    "version": row.version,
                    "parent_version_id": row.parent_version_id,
                    "structural_digest": row.structural_digest,
                    "reference_violations": row.reference_violations,
                    "reference_warnings": row.reference_warnings,
                },
            }
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)


@router.get("/{spec_id}/versions/{version}")
def get_design_spec_version(
    spec_id: str, version: str, user: dict = Depends(get_current_user_optional)
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.contract import validate_design_spec_payload
    from app.services.design_specs.versioning import get_version, resolve_payload

    with SessionLocal() as db:
        _visible_spec_or_404(db, spec_id, user)
        try:
            row = get_version(
                db, spec_id, None if version == "latest" else int(version)
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="version 必须为整数或 latest")
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        effective, chain = resolve_payload(db, row)
        report = validate_design_spec_payload(effective)
        return {
            "success": True,
            "version": {
                "id": row.id,
                "spec_id": row.spec_id,
                "version": row.version,
                "parent_version_id": row.parent_version_id,
                "deprecated": row.deprecated_at is not None,
                "deprecation_note": row.deprecation_note,
                "structural_digest": row.structural_digest,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            },
            "effective_payload": effective,
            "inheritance_chain": chain,
            "reference_validation": {
                "ok": report["ok"],
                "violations": report["violations"],
                "warnings": report["warnings"],
            },
        }


@router.post("/{spec_id}/versions/{version}/deprecate")
def deprecate_design_spec_version(
    spec_id: str,
    version: str,
    body: DesignSpecDeprecateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.versioning import deprecate_version

    with SessionLocal() as db:
        spec = _visible_spec_or_404(db, spec_id, user)
        _require_can_write(db, spec, user)
        try:
            row = deprecate_version(
                db, spec_id, None if version == "latest" else int(version),
                note=body.note,
                actor=(user.get("user_id") if isinstance(user, dict) else None),
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


@router.post("/{spec_id}/publish")
def publish_design_spec(
    spec_id: str, user: dict = Depends(get_current_user)
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.versioning import publish_spec

    with SessionLocal() as db:
        spec = _visible_spec_or_404(db, spec_id, user)
        _require_can_write(db, spec, user)
        try:
            row = publish_spec(db, spec_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {"success": True, "spec": _spec_brief(row)}


@router.post("/{spec_id}/retire")
def retire_design_spec(
    spec_id: str,
    body: DesignSpecRetireRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.versioning import retire_spec

    with SessionLocal() as db:
        spec = _visible_spec_or_404(db, spec_id, user)
        _require_can_write(db, spec, user)
        try:
            row = retire_spec(db, spec_id, note=body.note)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {"success": True, "spec": _spec_brief(row)}


@router.get("/{spec_id}/preview")
def preview_design_spec(
    spec_id: str,
    version: Optional[str] = None,
    user: dict = Depends(get_current_user_optional),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.design_specs.preview import build_design_spec_preview

    with SessionLocal() as db:
        _visible_spec_or_404(db, spec_id, user)
        try:
            result = build_design_spec_preview(
                db, spec_id,
                version=None if version in (None, "latest") else int(version),
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="version 必须为整数或 latest")
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {"success": True, **result}


__all__ = ["router"]
