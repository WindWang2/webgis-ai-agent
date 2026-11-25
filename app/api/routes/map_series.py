"""Map Series REST 面 —— 系列计划 CRUD / 成员 / 一致性 / 传播 / 冻结计划（WP3）。

- ``GET    /map-series``                            scope 内列表；
- ``POST   /map-series``                            新建（校验 fail-closed + pin 解析）；
- ``GET    /map-series/{series_id}``                详情；
- ``POST   /map-series/{series_id}/versions``       落版本快照（可带 parent 继承）；
- ``POST   /map-series/{series_id}/members``        增/删/改单个成员；
- ``GET    /map-series/{series_id}/consistency``    一致性检查（只读）；
- ``POST   /map-series/{series_id}/propagation/preview`` 传播预览（只读）；
- ``POST   /map-series/{series_id}/propagation/apply``   逐图 CAS 传播（绝不宣称原子批量）；
- ``POST   /map-series/{series_id}/freeze-plan``    冻结计划 JSON（不执行生产，WP5 才渲染）。

鉴权（SEC-02，同 design_specs.py）：读 = builtin ∪ creator ∪ org（不可见与
不存在同返 404）；写 = creator 或同组织 admin；builtin 只读。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import actor_ids, get_current_user, get_current_user_optional
from app.models.map_series import CartographyMapSeries
from app.services.map_series.errors import (
    MapSeriesConflictError,
    MapSeriesError,
    MapSeriesForbiddenError,
    MapSeriesNotFoundError,
    MapSeriesValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/map-series", tags=["系列地图计划"])


class MapSeriesCreateRequest(BaseModel):
    payload: Dict[str, Any]
    make_builtin: bool = False  # 仅 admin 可置（默认 False）


class MapSeriesVersionCreateRequest(BaseModel):
    payload: Dict[str, Any]
    parent_version_id: Optional[str] = None


class SeriesMemberOpRequest(BaseModel):
    op: str = Field(default="upsert")  # upsert | remove
    member: Optional[Dict[str, Any]] = None
    member_id: Optional[str] = None


class PropagationPreviewRequest(BaseModel):
    intents: Dict[str, Any]
    spec_version: Optional[int] = None


class PropagationApplyRequest(PropagationPreviewRequest):
    expected_revisions: Optional[Dict[str, int]] = None


class FreezePlanRequest(BaseModel):
    spec_version: Optional[int] = None


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, MapSeriesForbiddenError):
        return HTTPException(status_code=403, detail=exc.error_payload())
    if isinstance(exc, MapSeriesConflictError):
        return HTTPException(status_code=409, detail=exc.error_payload())
    if isinstance(exc, MapSeriesNotFoundError):
        return HTTPException(status_code=404, detail=exc.error_payload())
    if isinstance(exc, MapSeriesValidationError):
        return HTTPException(status_code=400, detail=exc.error_payload())
    if isinstance(exc, MapSeriesError):
        return HTTPException(status_code=400, detail=exc.error_payload())
    raise exc


def _actor_ctx(user: Any) -> tuple[Optional[str], Any, Optional[str]]:
    user_id, org_id = actor_ids(user if isinstance(user, dict) else None)
    role = user.get("role") if isinstance(user, dict) else None
    return user_id, org_id, role


def _visible_series_or_404(db: Any, series_id: str, user: Any) -> CartographyMapSeries:
    from app.services.map_series.store import get_series, map_series_visible

    user_id, org_id, _role = _actor_ctx(user)
    try:
        series = get_series(db, series_id)
    except MapSeriesNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Map series '{series_id}' not found"
        )
    if not map_series_visible(series, user_id, org_id):
        raise HTTPException(
            status_code=404, detail=f"Map series '{series_id}' not found"
        )
    return series


def _require_can_write(db: Any, series: CartographyMapSeries, user: Any) -> None:
    from app.services.map_series.store import map_series_can_write

    user_id, org_id, role = _actor_ctx(user)
    if not map_series_can_write(series, user_id, org_id, role):
        raise HTTPException(
            status_code=403,
            detail={"code": "MAP_SERIES_FORBIDDEN", "message": "read-only for this caller"},
        )


def _series_brief(series: CartographyMapSeries) -> Dict[str, Any]:
    return {
        "id": series.id,
        "name": series.name,
        "description": series.description,
        "is_builtin": bool(series.is_builtin),
        "status": series.status,
        "design_spec_id": series.design_spec_id,
        "spec_version": series.spec_version,
        "current_version": series.current_version,
        "member_count": len((series.payload or {}).get("members") or []),
        "structural_digest": series.structural_digest,
        "retired": series.retired_at is not None,
        "retirement_note": series.retirement_note,
        "creator_id": series.creator_id,
        "updated_at": series.updated_at.isoformat() if series.updated_at else None,
    }


@router.get("")
def list_map_series(user: dict = Depends(get_current_user_optional)) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.store import list_series

    user_id, org_id, _role = _actor_ctx(user)
    with SessionLocal() as db:
        rows = list_series(db, user_id, org_id)
        return {"success": True, "items": [_series_brief(r) for r in rows]}


@router.post("")
def create_map_series(
    body: MapSeriesCreateRequest, user: dict = Depends(get_current_user)
) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.store import create_series

    user_id, org_id, role = _actor_ctx(user)
    if body.make_builtin and role != "admin":
        raise HTTPException(status_code=403, detail="only admin may create builtin series")
    try:
        with SessionLocal() as db:
            series = create_series(
                db,
                body.payload,
                creator_id=(user.get("user_id") if isinstance(user, dict) else user_id),
                org_id=org_id,
                is_builtin=bool(body.make_builtin and role == "admin"),
                created_by=(user.get("user_id") if isinstance(user, dict) else user_id),
            )
            return {"success": True, "series": _series_brief(series)}
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc)


@router.get("/{series_id}")
def get_map_series(
    series_id: str, user: dict = Depends(get_current_user_optional)
) -> dict:
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        series = _visible_series_or_404(db, series_id, user)
        return {"success": True, "series": _series_brief(series), "payload": series.payload}


@router.post("/{series_id}/versions")
def create_map_series_version(
    series_id: str,
    body: MapSeriesVersionCreateRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.store import create_version

    with SessionLocal() as db:
        series = _visible_series_or_404(db, series_id, user)
        _require_can_write(db, series, user)
        try:
            row = create_version(
                db, series_id, body.payload,
                parent_version_id=body.parent_version_id,
                created_by=(user.get("user_id") if isinstance(user, dict) else None),
                can_write=lambda s: True,  # 已在上文校验
                can_read_parent=lambda s: True,
            )
            return {
                "success": True,
                "version": {
                    "id": row.id,
                    "series_id": row.series_id,
                    "version": row.version,
                    "parent_version_id": row.parent_version_id,
                    "structural_digest": row.structural_digest,
                },
            }
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)


@router.post("/{series_id}/members")
def series_member_op(
    series_id: str,
    body: SeriesMemberOpRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.store import remove_member, upsert_member

    with SessionLocal() as db:
        series = _visible_series_or_404(db, series_id, user)
        _require_can_write(db, series, user)
        try:
            if body.op == "remove":
                if not body.member_id:
                    raise HTTPException(status_code=400, detail="op=remove 需要 member_id")
                series = remove_member(
                    db, series_id, body.member_id, can_write=lambda s: True
                )
                return {"success": True, "op": "remove", "member_id": body.member_id}
            if body.member is None:
                raise HTTPException(status_code=400, detail="op=upsert 需要 member")
            series = upsert_member(db, series_id, body.member, can_write=lambda s: True)
            return {
                "success": True,
                "op": "upsert",
                "member_count": len((series.payload or {}).get("members") or []),
            }
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)


@router.get("/{series_id}/consistency")
async def series_consistency(
    series_id: str, user: dict = Depends(get_current_user_optional)
) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.consistency import run_series_consistency

    with SessionLocal() as db:
        _visible_series_or_404(db, series_id, user)
        try:
            result = await run_series_consistency(db, series_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {"success": True, **result}


@router.post("/{series_id}/propagation/preview")
async def series_propagation_preview(
    series_id: str,
    body: PropagationPreviewRequest,
    user: dict = Depends(get_current_user_optional),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.propagation import build_propagation_preview

    with SessionLocal() as db:
        _visible_series_or_404(db, series_id, user)
        try:
            result = await build_propagation_preview(
                db, series_id, spec_version=body.spec_version, intents=body.intents
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {"success": True, **result}


@router.post("/{series_id}/propagation/apply")
async def series_propagation_apply(
    series_id: str,
    body: PropagationApplyRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.propagation import apply_propagation

    with SessionLocal() as db:
        series = _visible_series_or_404(db, series_id, user)
        _require_can_write(db, series, user)
        actor = str(user.get("user_id") or user.get("username") or "user") if isinstance(user, dict) else "user"
        try:
            result = await apply_propagation(
                db, series_id,
                spec_version=body.spec_version,
                intents=body.intents,
                actor=actor,
                expected_revisions=body.expected_revisions,
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {"success": True, **result}


@router.post("/{series_id}/freeze-plan")
async def series_freeze_plan(
    series_id: str,
    body: FreezePlanRequest,
    user: dict = Depends(get_current_user_optional),
) -> dict:
    from app.core.database import SessionLocal
    from app.services.map_series.freeze import build_freeze_plan

    with SessionLocal() as db:
        _visible_series_or_404(db, series_id, user)
        try:
            plan = await build_freeze_plan(db, series_id, spec_version=body.spec_version)
        except Exception as exc:  # noqa: BLE001
            raise _map_error(exc)
        return {"success": True, "freeze_plan": plan}


__all__ = ["router"]
